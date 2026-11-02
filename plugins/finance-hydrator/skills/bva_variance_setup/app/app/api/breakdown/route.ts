import { sqlStr, csvParam, num, pct, rowContribution, seriesVersion, parseExtraFilters, extraFilterWhereClauses } from "@/lib/bva"
import { getBvaFrom, getActualsAnchor } from "@/lib/bva-source"
import { getBreakdownRows } from "@/lib/queries/bva-data"

export const dynamic = "force-dynamic"

/**
 * Expense Breakdown: aggregates the current period by cost center and by category,
 * using the same two-series (V1 vs V2) model as /api/bva.
 * "Current period" = the selected quarter (Q1-Q4) or the full fiscal year.
 */
export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url)
    const version1 = searchParams.get("version1") ?? ""
    const version2 = searchParams.get("version2") ?? ""
    const fiscalYear = searchParams.get("fiscalYear") ?? ""
    const quarter = searchParams.get("quarter") ?? "Full Year"
    const organization = searchParams.get("organization") ?? ""
    const category     = searchParams.get("category")     ?? ""
    const costCenter   = searchParams.get("costCenter")   ?? ""
    const extraFilters = parseExtraFilters(searchParams.get("extraFilters"))

    if (!version1 || !version2 || !fiscalYear) {
      return Response.json({ error: "version1, version2, fiscalYear are required" }, { status: 400 })
    }

    const [bvaFrom, actualsAnchor] = await Promise.all([getBvaFrom(), getActualsAnchor()])
    const sv = (s: string) => seriesVersion(s, actualsAnchor)
    const neededVersions = [...new Set([sv(version1), sv(version2)])]
    const orgs = csvParam(organization)
    const cats = csvParam(category)
    const ccs  = csvParam(costCenter)
    const where = [`planning_version in (${neededVersions.map(sqlStr).join(",")})`, `fiscal_year = ${sqlStr(fiscalYear)}`]
    if (quarter !== "Full Year") where.push(`fiscal_quarter = ${sqlStr(quarter)}`)
    if (orgs.length) where.push(`coalesce(department, '(Unmapped)') in (${orgs.map(sqlStr).join(",")})`)
    if (cats.length) where.push(`expense_category in (${cats.map(sqlStr).join(",")})`)
    if (ccs.length)  where.push(`coalesce(cost_center_name, to_varchar(cost_center_id)) in (${ccs.map(sqlStr).join(",")})`)
    where.push(...extraFilterWhereClauses(extraFilters))

    const rows = await getBreakdownRows(bvaFrom, where.join(" and "))

    const catMap = new Map<string, { v1: number; v2: number }>()
    const ccMap = new Map<string, { costCenterId: string; costCenterName: string; department: string; v1: number; v2: number }>()

    for (const r of rows) {
      const ver = String(r.PLANNING_VERSION)
      const cat = String(r.EXPENSE_CATEGORY)
      const ccId   = String(r.COST_CENTER_ID ?? "")
      const ccName = String(r.COST_CENTER_NAME ?? r.COST_CENTER_ID ?? "")
      const ccDisplay = String(r.COST_CENTER_DISPLAY)
      const dept = String(r.DEPARTMENT)
      const actual = num(r.ACTUAL_EXPENSE)
      const budget = num(r.BUDGET_EXPENSE)
      const v1 = rowContribution(version1, ver, actual, budget, actualsAnchor)
      const v2 = rowContribution(version2, ver, actual, budget, actualsAnchor)

      if (v1 !== 0 || v2 !== 0) {
        const cv = catMap.get(cat) ?? { v1: 0, v2: 0 }
        cv.v1 += v1; cv.v2 += v2; catMap.set(cat, cv)

        // Key on cost center + department (not just cost center id) since a
        // missing/blank cost_center_id is valid data (rows with no CC mapped)
        // and would otherwise collapse every such row into a single bucket
        // regardless of which department they actually belong to.
        const ck = `${ccId}||${dept}`
        const cur = ccMap.get(ck) ?? { costCenterId: ccId, costCenterName: ccDisplay, department: dept, v1: 0, v2: 0 }
        cur.v1 += v1; cur.v2 += v2; ccMap.set(ck, cur)
      }
    }

    const shape = (v1: number, v2: number) => ({ v1, v2, delta: v1 - v2, deltaPct: pct(v1 - v2, v2) })

    const categorySummary = [...catMap.entries()]
      .map(([cat, v]) => ({ category: cat, ...shape(v.v1, v.v2) }))
      .sort((a, b) => b.v1 - a.v1)

    const costCenters = [...ccMap.entries()]
      .map(([, v]) => ({ costCenterId: v.costCenterId, costCenterName: v.costCenterName, department: v.department, ...shape(v.v1, v.v2) }))
      .sort((a, b) => b.delta - a.delta)

    const chart = [...ccMap.entries()]
      .map(([, v]) => ({ costCenter: v.costCenterName, v1: v.v1, v2: v.v2 }))
      .sort((a, b) => b.v1 - a.v1)
      .slice(0, 15) // cap so the chart stays readable when there are many cost centers

    const currentLabel = quarter === "Full Year"
      ? `${fiscalYear.replace("FY20", "FY")} Total`
      : `${quarter} ${fiscalYear.replace("FY20", "FY")}`

    return Response.json({ version1, version2, category, currentLabel, categorySummary, costCenters, chart })
  } catch (e) {
    console.error(new Date().toISOString(), "[api/breakdown]", e)
    return Response.json({ error: "Failed to load breakdown data" }, { status: 500 })
  }
}
