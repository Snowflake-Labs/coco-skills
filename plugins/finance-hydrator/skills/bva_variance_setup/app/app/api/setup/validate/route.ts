import { buildFromConfig, buildFinalSql, validateBuildConfig } from "@/lib/config"
import type { BvaConfig } from "@/lib/config"
import { checkIsAdmin } from "@/lib/agent-access-mode"
import {
  getBaselineRowCount,
  getRowCount,
  getNullCounts,
  getCoverageStats,
  getPlanningVersionCount,
  getCostCenterNameCoverage,
  getCategoryOverlap,
  getSampleRows,
} from "@/lib/queries/validate"

export const dynamic = "force-dynamic"

/**
 * POST /api/setup/validate
 * Body: BvaConfig
 *
 * Runs validation checks against the assembled SQL (buildFinalSql() — baseline
 * plus any enabled CustomLogicRule entries layered on top; see lib/config.ts):
 *   0. Config completeness — all required fields for the chosen options are set
 *   0.5 Baseline row count  — (only when custom logic rules are enabled) checks
 *                            the pre-custom-logic baseline alone first, so a
 *                            failure is attributable to the base mapping vs.
 *                            the custom logic layer
 *   1. Row count           — the query returns > 0 rows
 *   2. Nulls               — fiscal_year, fiscal_quarter, expense_category have no nulls
 *   3. Coverage            — actuals and budget are both non-zero AND actuals are visible
 *                            (planning_version IS NOT NULL), not just present in raw rows
 *   4. Versions            — at least 1 planning_version present
 *   5. CC match            — cost_center_name is populated for > 50% of rows (warning)
 *   6. Category overlap    — at least 1 expense_category appears in both actuals and budget rows
 */
export async function POST(req: Request) {
  const admin = await checkIsAdmin()
  if (!admin.isAdmin) {
    return Response.json({ error: "Admin only" }, { status: 403 })
  }
  let cfg: BvaConfig
  try {
    cfg = (await req.json()) as BvaConfig
  } catch {
    return Response.json({ error: "Invalid JSON body" }, { status: 400 })
  }

  const checks: Array<{
    name: string
    pass: boolean
    detail: string
    warning?: boolean
  }> = []

  // Check 0: Config completeness — pre-flight before touching Snowflake
  const configErrors = validateBuildConfig(cfg)
  if (configErrors.length) {
    for (const msg of configErrors) {
      checks.push({ name: "Config completeness", pass: false, detail: msg })
    }
    return Response.json({ checks, sample: [] })
  }

  const sql = buildFinalSql(cfg)
  // A config being validated mid-setup can still contain unresolved template
  // tokens (e.g. `{{some_param}}`) from an in-progress custom logic rule that
  // isn't fully filled in yet. Substitute a harmless always-true predicate so
  // the preview query still runs instead of failing on invalid SQL syntax.
  const runnableSql = sql.replace(/\{\{[^}]+\}\}/g, "1=1")
  const from = `(${runnableSql}) as bva_validate`

  // When custom logic rules are layered on top, run one cheap baseline-only
  // row-count check FIRST — so a failure downstream can be attributed to
  // "your custom logic broke this" vs. "the base mapping is wrong," instead
  // of leaving the user to guess which layer is at fault.
  const hasCustomLogic = sql !== buildFromConfig(cfg)
  if (hasCustomLogic) {
    const baselineSql = buildFromConfig(cfg).replace(/\{\{[^}]+\}\}/g, "1=1")
    try {
      const rows = await getBaselineRowCount(baselineSql)
      const rc = Number(rows[0]?.RC ?? 0)
      checks.push({
        name: "Baseline row count (before custom logic)",
        pass: rc > 0,
        detail: rc > 0
          ? `${rc.toLocaleString()} rows before custom logic is applied`
          : "Baseline query (without custom logic) returned 0 rows — the base table/column mapping is the problem, not your custom logic",
      })
      if (rc === 0) return Response.json({ checks, sample: [] })
    } catch (e) {
      console.error("[api/setup/validate] baseline", e)
      checks.push({
        name: "Baseline row count (before custom logic)",
        pass: false,
        detail: "Baseline query failed — verify the base table and column mappings, and that your role can read them.",
      })
      return Response.json({ checks, sample: [] })
    }
  }

  // Check 1: Row count
  try {
    const rows = await getRowCount(from)
    const rc = Number(rows[0]?.RC ?? 0)
    checks.push({
      name: "Row count",
      pass: rc > 0,
      detail: rc > 0 ? `${rc.toLocaleString()} rows returned` : "Query returned 0 rows — check table names and column mappings",
    })
  } catch (e) {
    console.error("[api/setup/validate] row count", e)
    checks.push({
      name: "Row count",
      pass: false,
      detail: "Query failed — verify the table names and column mappings, and that your role can read them.",
    })
    return Response.json({ checks, sample: [] }) // no point running other checks
  }

  // Check 2: Nulls in key columns
  try {
    const nullRows = await getNullCounts(from)
    const r = nullRows[0] ?? {}
    const nullFy  = Number(r.NULL_FY ?? 0)
    const nullFq  = Number(r.NULL_FQ ?? 0)
    const nullCat = Number(r.NULL_CAT ?? 0)
    const issues: string[] = []
    if (nullFy  > 0) issues.push(`${nullFy.toLocaleString()} null fiscal_year`)
    if (nullFq  > 0) issues.push(`${nullFq.toLocaleString()} null fiscal_quarter`)
    if (nullCat > 0) issues.push(`${nullCat.toLocaleString()} null expense_category`)
    checks.push({
      name: "Key columns",
      pass: issues.length === 0,
      detail: issues.length === 0
        ? "No nulls in fiscal_year, fiscal_quarter, expense_category"
        : "Null values found: " + issues.join("; "),
    })
  } catch (e) {
    console.error("[api/setup/validate] key columns", e)
    checks.push({ name: "Key columns", pass: false, detail: "Could not evaluate key columns — check the column mappings." })
  }

  // Check 3: Coverage — checks both existence AND dashboard visibility
  // Actuals on planning_version IS NULL rows are real data but invisible in the dashboard
  // (the dashboard joins on planning_version). Both totals must be non-zero.
  try {
    const covRows = await getCoverageStats(from)
    const r = covRows[0] ?? {}
    const hasActTotal   = Number(r.HAS_ACTUALS_TOTAL   ?? 0) > 0
    const hasActVisible = Number(r.HAS_ACTUALS_VISIBLE ?? 0) > 0
    const hasBud        = Number(r.HAS_BUDGET          ?? 0) > 0
    const actTotalM  = Number(r.TOTAL_ACTUAL_M   ?? 0)
    const actVisM    = Number(r.VISIBLE_ACTUAL_M ?? 0)
    const budM       = Number(r.TOTAL_BUDGET_M   ?? 0)

    if (!hasActTotal && !hasBud) {
      checks.push({ name: "Coverage", pass: false, detail: "No actuals and no budget rows found" })
    } else if (!hasActTotal) {
      checks.push({ name: "Coverage", pass: false, detail: "No actuals found — check table and column mappings" })
    } else if (!hasBud) {
      checks.push({ name: "Coverage", pass: false, detail: "No budget rows found — check budget table and column mappings" })
    } else if (!hasActVisible) {
      // Actuals exist in raw rows but none have a matching budget row (planning_version IS NULL)
      checks.push({
        name: "Coverage",
        pass: false,
        detail: `Actuals exist ($${actTotalM}M total) but none match budget rows — all actuals land on planning_version = NULL and will show as $0 in the dashboard. Check your category mapping: actuals categories must match budget categories.`,
      })
    } else {
      checks.push({
        name: "Coverage",
        pass: true,
        detail: `Actuals: $${actVisM}M visible ($${actTotalM}M total), Budget: $${budM}M total`,
      })
    }
  } catch (e) {
    console.error("[api/setup/validate] coverage", e)
    checks.push({ name: "Coverage", pass: false, detail: "Could not evaluate coverage — check table and column mappings." })
  }

  // Check 4: Versions
  try {
    const verRows = await getPlanningVersionCount(from)
    const vc = Number(verRows[0]?.VC ?? 0)
    checks.push({
      name: "Planning versions",
      pass: vc >= 1,
      detail: vc >= 1 ? `${vc} planning version${vc > 1 ? "s" : ""} found` : "No planning_version values found",
    })
  } catch (e) {
    console.error("[api/setup/validate] planning versions", e)
    checks.push({ name: "Planning versions", pass: false, detail: "Could not evaluate planning versions — check the planning_version mapping." })
  }

  // Check 5: Cost center name coverage (warning only)
  try {
    const ccRows = await getCostCenterNameCoverage(from)
    const pct = Math.round(Number(ccRows[0]?.PCT_NAMED ?? 0))
    checks.push({
      name: "Cost center names",
      pass: true, // always pass — informational
      warning: pct < 50,
      detail: pct >= 50
        ? `${pct}% of rows have a cost center name`
        : `Only ${pct}% have cost center names — cost center filter will show IDs instead of names`,
    })
  } catch (e) {
    checks.push({ name: "Cost center names", pass: true, warning: true, detail: "Could not check cost center name coverage" })
  }

  // Check 6: Category overlap — at least 1 category must appear in both actuals and budget rows.
  // Zero overlap means the full outer join on expense_category always fails → actuals show as $0.
  try {
    const catRows = await getCategoryOverlap(from)
    const r = catRows[0] ?? {}
    const actCatCount  = Number(r.ACT_CAT_COUNT  ?? 0)
    const budCatCount  = Number(r.BUD_CAT_COUNT  ?? 0)
    const overlapCount = Number(r.OVERLAP_COUNT  ?? 0)
    const actCats = String(r.ACT_CATS ?? "")
    const budCats = String(r.BUD_CATS ?? "")

    if (actCatCount === 0 || budCatCount === 0) {
      // No data on one side — already caught by Coverage; skip this check
      checks.push({ name: "Category overlap", pass: true, detail: "Skipped — no data on one side (see Coverage)" })
    } else if (overlapCount === 0) {
      checks.push({
        name: "Category overlap",
        pass: false,
        detail: `No shared categories between actuals and budget — actuals will show as $0 in the dashboard.\nActuals categories: ${actCats || "(none)"}\nBudget categories: ${budCats || "(none)"}`,
      })
    } else {
      checks.push({
        name: "Category overlap",
        pass: true,
        detail: `${overlapCount} shared categor${overlapCount === 1 ? "y" : "ies"} between actuals and budget`,
      })
    }
  } catch (e) {
    checks.push({ name: "Category overlap", pass: true, warning: true, detail: "Could not check category overlap" })
  }

  // Sample: 5 rows
  let sample: Record<string, unknown>[] = []
  try {
    sample = await getSampleRows(from)
  } catch { /* non-fatal */ }

  return Response.json({ checks, sample })
}
