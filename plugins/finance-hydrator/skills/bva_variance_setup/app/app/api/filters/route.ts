import { ACTUALS_OPTION } from "@/lib/bva"
import { getBvaFrom, getActualsAnchor } from "@/lib/bva-source"
import { loadConfig, getConfigTable, getMaterializedTable, materializedReady } from "@/lib/config-server"
import {
  probeColumnExists,
  getDistinctExtraFilterValues,
  getDistinctVersions,
  getDistinctFiscalYears,
  getDistinctFiscalQuarters,
  getDistinctDepartments,
  getDistinctCostCenters,
  getDistinctCategories,
  getDistinctMonthValues,
} from "../../../lib/queries/filters"

export const dynamic = "force-dynamic"

// Fallback Version 2 selection the dashboard pre-selects before the user picks
// one explicitly — a reasonable "current forecast" default, not derived from data.
const DEFAULT_VERSION = "Latest Forecast"

// If the source exposes a month column, monthly grain is available.
const MONTH_COLS = ["fiscal_month", "month", "period_month"]

async function sourceHasColumn(col: string, from: string): Promise<boolean> {
  try {
    await probeColumnExists(col, from)
    return true
  } catch {
    return false
  }
}

export type ExtraFilter = { column: string; label: string; values: string[] }

/**
 * Any enabled `derived_column` custom logic rule (see lib/config.ts's
 * CustomLogicRule / buildFinalSql()) becomes an ad-hoc dashboard filter here —
 * no per-column code needed. Empty for the common case (no custom logic
 * rules), so this never changes behavior for existing customers.
 */
async function loadExtraFilters(bvaFrom: string, cfg: Awaited<ReturnType<typeof loadConfig>>): Promise<ExtraFilter[]> {
  const rules = (cfg?.customLogicRules ?? []).filter((r) => r.enabled && r.kind === "derived_column" && r.alias)
  if (!rules.length) return []
  const results = await Promise.all(
    rules.map(async (r) => {
      try {
        const rows = await getDistinctExtraFilterValues(r.alias as string, bvaFrom)
        return { column: r.alias as string, label: r.name, values: rows.map((row) => String(row.V)) }
      } catch {
        return null // e.g. not materialized yet — skip rather than fail the whole filters response
      }
    }),
  )
  return results.filter((r): r is ExtraFilter => r !== null && r.values.length > 0)
}

export async function GET() {
  try {
    const [bvaFrom, actualsAnchor, cfg] = await Promise.all([getBvaFrom(), getActualsAnchor(), loadConfig()])

    // Check matTableReady in parallel with the filter queries — don't block on it.
    // Materialization is a no-op for demo mode (no "default" config saved —
    // cfg is null) and for single_table configs (they already point straight
    // at a table/view, see materializeBva()'s early return). Both cases have
    // nothing to "rebuild", so they're reported as ready rather than falling
    // through to a permanent false that would show the rebuild banner forever.
    const matReadyPromise = (async () => {
      if (!cfg || cfg.sourceType === "single_table" || !cfg.actualsTable) return true
      // Only multi-table configs actually get materialized (see materializeBva()'s
      // early return for the other cases) — look up whether that background job
      // finished so the dashboard can show a "rebuilding" banner until it has.
      const matTable = getMaterializedTable(await getConfigTable())
      return materializedReady(matTable)
    })()

    const [versions, years, quarters, departments, costCenters, categories, matTableReady, extraFilters] = await Promise.all([
      getDistinctVersions(bvaFrom),
      getDistinctFiscalYears(bvaFrom),
      getDistinctFiscalQuarters(bvaFrom),
      getDistinctDepartments(bvaFrom),
      getDistinctCostCenters(bvaFrom),
      getDistinctCategories(bvaFrom),
      matReadyPromise,
      loadExtraFilters(bvaFrom, cfg),
    ])

    // Grain detection: probe the source for a month column (source-agnostic).
    let monthColumn: string | null = null
    for (const c of MONTH_COLS) {
      if (await sourceHasColumn(c, bvaFrom)) { monthColumn = c; break }
    }
    const grain = monthColumn ? "month" : "quarter"
    let months: string[] = []
    if (monthColumn) {
      const rows = await getDistinctMonthValues(monthColumn, bvaFrom)
      months = rows.map((r) => String(r.V))
    }

    const versionList = versions.map((r) => r.V as string)
    const orgList = departments.map((r) => r.V as string).filter((v) => v !== "(Unmapped)")
    const catList = categories.map((r) => r.V as string)
    const ccList = costCenters.map((r) => r.V as string).filter(Boolean)
    const yearList = years.map((r) => r.V as string)

    // Capabilities derived from filter arrays (no extra queries needed)
    const capabilities = {
      hasOrganization: orgList.length > 0,
      hasMonthlyGrain: grain === "month",
      hasMultipleVersions: versionList.length > 1,
      hasCategories: catList.length >= 3,
      hasCostCenterNames: ccList.length > 0,
      hasMultipleYears: yearList.length > 1,
      hasExpenseBreakdown: catList.length >= 3 && ccList.length > 0,
    }

    return Response.json({
      fyStartMonth: cfg?.fyStartMonth ?? 1,
      seriesOptions: [ACTUALS_OPTION, ...versionList],
      versions: versionList,
      years: yearList,
      quarters: quarters.map((r) => r.V as string),
      months,
      grain,
      organizations: orgList,
      costCenters: ccList,
      categories: catList,
      defaultVersion: DEFAULT_VERSION,
      actualsAnchor,
      capabilities,
      matTableReady,
      // Ad-hoc filters from enabled derived_column custom logic rules — see
      // loadExtraFilters(). Empty array in the common case (no custom logic).
      extraFilters,
    })
  } catch (e) {
    console.error(new Date().toISOString(), "[api/filters]", e)
    return Response.json(
      { error: "Failed to load filters" },
      { status: 500 },
    )
  }
}
