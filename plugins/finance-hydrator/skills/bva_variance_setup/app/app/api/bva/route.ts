import { sqlStr, csvParam, num, pct, periodLabel, seriesVersion, rowContribution, parseExtraFilters, extraFilterWhereClauses } from "@/lib/bva"
import { getBvaFrom, getActualsAnchor } from "@/lib/bva-source"
import { loadConfig } from "@/lib/config-server"
import type { SummaryRowDef, BreakoutGroup } from "@/lib/config"
import { getBvaWorkingRows, getBvaBreakoutRows } from "@/lib/queries/bva-data"

export const dynamic = "force-dynamic"

const MAX_TRAILING = 5

type Cell = { v1: number; v2: number }
type ApiRow = {
  category: string
  trailing: Record<string, number>
  currentV1: number
  currentV2: number
  deltaDollar: number
  deltaPct: number | null
  subRows?: ApiRow[]
}

/**
 * BvA "Working"-style matrix.
 * Rows = expense categories; columns = trailing quarters (Version 1 value each)
 * then a current-period pair [Version 1 (Actuals) | Version 2 (Forecast)] + delta.
 *
 * quarter = "Full Year": trailing = the 4 quarters of the fiscal year, current = FY total.
 * quarter = Q1-Q4: trailing = up to 5 quarters before it, current = that quarter.
 */
export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url)
    const version1 = searchParams.get("version1") ?? ""
    const version2 = searchParams.get("version2") ?? ""
    const fiscalYear = searchParams.get("fiscalYear") ?? ""
    const quarter = searchParams.get("quarter") ?? "Full Year"
    const month    = searchParams.get("month") ?? ""       // non-empty when grain=month
    const organization = searchParams.get("organization") ?? ""
    const costCenter   = searchParams.get("costCenter")   ?? ""
    const category     = searchParams.get("category")     ?? ""
    const extraFilters = parseExtraFilters(searchParams.get("extraFilters"))

    if (!version1 || !version2 || !fiscalYear) {
      return Response.json({ error: "version1, version2, fiscalYear are required" }, { status: 400 })
    }

    const [bvaFrom, actualsAnchor] = await Promise.all([getBvaFrom(), getActualsAnchor()])
    const neededVersions = [...new Set([seriesVersion(version1, actualsAnchor), seriesVersion(version2, actualsAnchor)])]
    const orgs = csvParam(organization)
    const ccs  = csvParam(costCenter)
    const cats = csvParam(category)
    const where = [`planning_version in (${neededVersions.map(sqlStr).join(",")})`]
    if (orgs.length) where.push(`coalesce(department, '(Unmapped)') in (${orgs.map(sqlStr).join(",")})`)
    if (ccs.length)  where.push(`coalesce(cost_center_name, to_varchar(cost_center_id)) in (${ccs.map(sqlStr).join(",")})`)
    if (cats.length) where.push(`expense_category in (${cats.map(sqlStr).join(",")})`)
    where.push(...extraFilterWhereClauses(extraFilters))

    // Monthly grain: when month is provided we assume fiscal_month exists (filters/route.ts
    // verified this before letting the dropdown populate). Filter + group by month.
    const isMonthly = !!month
    if (isMonthly) where.push(`fiscal_month = ${sqlStr(month)}`)

    const monthSelect = isMonthly ? ", fiscal_month" : ""
    const groupBy     = isMonthly ? "fiscal_month" : "fiscal_quarter"

    const rows = await getBvaWorkingRows(bvaFrom, groupBy, monthSelect, where.join(" and "))

    // (period, category) -> {v1, v2}
    const cells = new Map<string, Cell>()
    const periodSet = new Set<string>()
    const categorySet = new Set<string>()

    for (const r of rows) {
      const ver = String(r.PLANNING_VERSION)
      const fy = String(r.FISCAL_YEAR)
      const periodKey = String(r.PERIOD_KEY)
      const cat = String(r.EXPENSE_CATEGORY)
      const actual = num(r.ACTUAL_EXPENSE)
      const budget = num(r.BUDGET_EXPENSE)
      const v1 = rowContribution(version1, ver, actual, budget, actualsAnchor)
      const v2 = rowContribution(version2, ver, actual, budget, actualsAnchor)
      if (v1 === 0 && v2 === 0) continue

      const pk = `${fy}|${periodKey}`
      periodSet.add(pk)
      categorySet.add(cat)
      const ck = `${pk}||${cat}`
      const cur = cells.get(ck) ?? { v1: 0, v2: 0 }
      cur.v1 += v1
      cur.v2 += v2
      cells.set(ck, cur)
    }

    const sortedPeriods = [...periodSet].sort((a, b) => a.localeCompare(b)) // FY2026|Q1 ... FY2027|Q4

    // Resolve the display window.
    let trailingKeys: string[]
    let currentKeys: string[]
    let currentLabel: string
    if (isMonthly) {
      // Monthly: current = selected month; trailing = up to MAX_TRAILING preceding months
      const curKey = `${fiscalYear}|${month}`
      const idx = sortedPeriods.indexOf(curKey)
      trailingKeys = idx >= 0 ? sortedPeriods.slice(Math.max(0, idx - MAX_TRAILING), idx) : []
      currentKeys = idx >= 0 ? [curKey] : []
      currentLabel = `${month} ${fiscalYear.replace("FY20", "FY")}`
    } else if (quarter === "Full Year") {
      const fyQuarters = sortedPeriods.filter((k) => k.startsWith(`${fiscalYear}|`))
      trailingKeys = fyQuarters
      currentKeys = fyQuarters
      currentLabel = `${fiscalYear.replace("FY20", "FY")} Total`
    } else {
      const curKey = `${fiscalYear}|${quarter}`
      const idx = sortedPeriods.indexOf(curKey)
      trailingKeys = idx >= 0 ? sortedPeriods.slice(Math.max(0, idx - MAX_TRAILING), idx) : []
      currentKeys = idx >= 0 ? [curKey] : []
      currentLabel = `${quarter} ${fiscalYear.replace("FY20", "FY")}`
    }

    const cellOf = (pk: string, cat: string): Cell => cells.get(`${pk}||${cat}`) ?? { v1: 0, v2: 0 }
    const labelFor = (pk: string) => {
      const [fy, periodKey] = pk.split("|")
      // Monthly grain: period_key is the raw month value from the source (e.g. "January", "01")
      // Quarterly grain: period_key is Q1/Q2/Q3/Q4
      if (isMonthly) return `${periodKey} ${fy.replace("FY20", "FY")}`
      return periodLabel(fy, periodKey)
    }

    const trailingQuarters = trailingKeys.map((k) => ({ key: k, label: labelFor(k) }))

    const buildRow = (cat: string): ApiRow => {
      const trailing: Record<string, number> = {}
      for (const k of trailingKeys) trailing[k] = cellOf(k, cat).v1
      const currentV1 = currentKeys.reduce((s, k) => s + cellOf(k, cat).v1, 0)
      const currentV2 = currentKeys.reduce((s, k) => s + cellOf(k, cat).v2, 0)
      const deltaDollar = currentV1 - currentV2
      return { category: cat, trailing, currentV1, currentV2, deltaDollar, deltaPct: pct(deltaDollar, currentV2) }
    }

    const categories = [...categorySet].map(buildRow).sort((a, b) => b.currentV1 - a.currentV1)

    // Total row.
    const totalTrailing: Record<string, number> = {}
    for (const k of trailingKeys) totalTrailing[k] = categories.reduce((s, c) => s + (c.trailing[k] ?? 0), 0)
    const totalV1 = categories.reduce((s, c) => s + c.currentV1, 0)
    const totalV2 = categories.reduce((s, c) => s + c.currentV2, 0)
    const total = {
      trailing: totalTrailing,
      currentV1: totalV1,
      currentV2: totalV2,
      deltaDollar: totalV1 - totalV2,
      deltaPct: pct(totalV1 - totalV2, totalV2),
    }

    // Chart: every window quarter, V1 vs V2 summed across categories.
    const chartKeys = quarter === "Full Year" ? currentKeys : [...trailingKeys, ...currentKeys]
    const chart = chartKeys.map((k) => {
      let v1 = 0
      let v2 = 0
      for (const cat of categorySet) {
        const c = cellOf(k, cat)
        v1 += c.v1
        v2 += c.v2
      }
      return { period: labelFor(k), v1, v2 }
    })

    // Load config for user-defined summary rows + breakouts (best-effort — if missing, use defaults)
    const cfg = await loadConfig().catch(() => null)

    // User-defined summary rows — each is a named subset of category totals
    const summaryRowDefs: SummaryRowDef[] = cfg?.summaryRows ?? []
    const summaryRows = summaryRowDefs.map((def) => {
      const filtered = def.includeCategories?.length
        ? categories.filter((c) => def.includeCategories!.includes(c.category))
        : categories.filter((c) => !(def.excludeCategories ?? []).includes(c.category))
      const v1 = filtered.reduce((s, c) => s + c.currentV1, 0)
      const v2 = filtered.reduce((s, c) => s + c.currentV2, 0)
      const trailing: Record<string, number> = {}
      for (const k of trailingKeys) trailing[k] = filtered.reduce((s, c) => s + (c.trailing[k] ?? 0), 0)
      return {
        name: def.name,
        style: def.style ?? "bold",
        trailing,
        currentV1: v1,
        currentV2: v2,
        deltaDollar: v1 - v2,
        deltaPct: pct(v1 - v2, v2),
      }
    })

    // Sub-category breakouts — attach as subRows on the relevant parent category
    const breakoutDefs: BreakoutGroup[] = cfg?.categoryBreakouts ?? []
    if (breakoutDefs.length > 0) {
      // Group breakouts by parent category
      const byParent = new Map<string, BreakoutGroup[]>()
      for (const b of breakoutDefs) {
        if (!byParent.has(b.parentCategory)) byParent.set(b.parentCategory, [])
        byParent.get(b.parentCategory)!.push(b)
      }

      for (const [parentCat, bgroups] of byParent) {
        const parentRow = categories.find((c) => c.category === parentCat)
        if (!parentRow) continue

        // Run one aggregation query per breakout group for this parent
        const subRows = await Promise.all(bgroups.map(async (b) => {
          const subRowsRaw = await getBvaBreakoutRows(bvaFrom, groupBy, where.join(" and "), b.filterType, b.filterValues)

          const subCells = new Map<string, Cell>()
          for (const r of subRowsRaw) {
            const ver = String(r.PLANNING_VERSION)
            const fy  = String(r.FISCAL_YEAR)
            const pk  = `${fy}|${String(r.PERIOD_KEY)}`
            const v1b = rowContribution(version1, ver, num(r.ACTUAL_EXPENSE), num(r.BUDGET_EXPENSE), actualsAnchor)
            const v2b = rowContribution(version2, ver, num(r.ACTUAL_EXPENSE), num(r.BUDGET_EXPENSE), actualsAnchor)
            const cur = subCells.get(pk) ?? { v1: 0, v2: 0 }
            cur.v1 += v1b; cur.v2 += v2b
            subCells.set(pk, cur)
          }

          const subTrailing: Record<string, number> = {}
          for (const k of trailingKeys) subTrailing[k] = (subCells.get(k) ?? { v1: 0, v2: 0 }).v1
          const sv1 = currentKeys.reduce((s, k) => s + (subCells.get(k) ?? { v1: 0, v2: 0 }).v1, 0)
          const sv2 = currentKeys.reduce((s, k) => s + (subCells.get(k) ?? { v1: 0, v2: 0 }).v2, 0)

          return {
            category: b.label,
            trailing: subTrailing,
            currentV1: sv1,
            currentV2: sv2,
            deltaDollar: sv1 - sv2,
            deltaPct: pct(sv1 - sv2, sv2),
          }
        }))

        // Attach subRows to the parent category row
        const idx = categories.indexOf(parentRow)
        if (idx >= 0) categories[idx] = { ...parentRow, subRows }
      }
    }

    return Response.json({
      version1,
      version2,
      organization,
      costCenter,
      category,
      fiscalYear,
      quarter,
      // Lets the dashboard label the two series correctly (e.g. "Actuals" vs a
      // forecast name) without re-deriving it client-side from version1 again.
      isActualsV1: version1 === "Actuals",
      currentLabel,
      trailingQuarters,
      categories,
      total,
      summaryRows: summaryRows.length > 0 ? summaryRows : undefined,
      chart,
    })
  } catch (e) {
    console.error(new Date().toISOString(), "[api/bva]", e)
    return Response.json(
      { error: "Failed to load BvA data" },
      { status: 500 },
    )
  }
}
