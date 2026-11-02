import fs from "fs"
import path from "path"
import { buildFinalSql } from "@/lib/config"
import { loadConfig, getConfigTable, getMaterializedTable, materializedReady } from "@/lib/config-server"
import { ensureDemoDataSeeded } from "@/lib/demo-seed"
import { querySnowflake } from "@/lib/snowflake"
import { WAREHOUSE } from "@/lib/bva"

/**
 * The BvA dataset source for the dashboard.
 *
 * Source priority (highest to lowest):
 *   1. BVA_APP_CONFIG 'default' config — shared, persistent, editable via Settings page
 *   2. BVA_APP_CONFIG 'Demo' config    — a fully synthetic dataset. Auto-seeded
 *      (see lib/demo-seed.ts) the first time this app is opened with neither a
 *      'default' nor a 'Demo' config saved yet, so a brand-new deployment
 *      always has something real to show — never a hardcoded reference to any
 *      pre-existing table, which would either not exist in a customer's own
 *      account (broken first run) or, worse, resolve to real data if this
 *      exact instance were ever used for that purpose.
 *   3. bva_query.sql file              — skill output drop-in (backward compatible)
 *
 * The source MUST yield these columns:
 *   fiscal_year, fiscal_quarter, cost_center_id, cost_center_name, department,
 *   expense_category, planning_version, actual_expense, budget_expense
 */
function loadFileSource(): string | null {
  try {
    const p = path.join(process.cwd(), "bva_query.sql")
    if (fs.existsSync(p)) {
      const sql = fs.readFileSync(p, "utf8").trim().replace(/;+\s*$/, "")
      if (sql) return sql
    }
  } catch {
    // fall through
  }
  return null
}

/** Static file-based source (backward-compat). Null when no bva_query.sql file exists. */
export const BVA_SOURCE = loadFileSource()

/** Static file-based FROM clause (backward-compat for any static consumers). */
export const BVA_FROM = BVA_SOURCE ? `(${BVA_SOURCE}) as bva` : null

/**
 * Async source resolver — always prefer this in API routes.
 * Checks BVA_APP_CONFIG in Snowflake first; falls back to Demo, then file.
 */
export async function getBvaFrom(): Promise<string> {
  try {
    const cfg = await loadConfig()
    if (cfg) {
      // Prefer the precomputed materialized table for the multi-table pipeline
      // (source not explicitly single_table AND actuals table set) — it holds the
      // exact same grouped output as buildFinalSql() but as a small scannable
      // table, avoiding a full pipeline recompute on every query.
      if (cfg.sourceType !== "single_table" && cfg.actualsTable) {
        const matTable = getMaterializedTable(await getConfigTable())
        if (await materializedReady(matTable)) return `${matTable} as bva`
      }
      const sql = buildFinalSql(cfg)
      return `(${sql}) as bva`
    }
    // No 'default' config saved yet — use the "Demo" named profile, seeding it
    // (and its fully synthetic backing tables) on first-ever access if needed.
    await ensureDemoDataSeeded()
    const demoCfg = await loadConfig("Demo")
    if (demoCfg) return `(${buildFinalSql(demoCfg)}) as bva`
    if (BVA_FROM) return BVA_FROM
  } catch {
    // Config table unreachable — fall through
  }
  throw new Error(
    "No BvA data source configured and demo data seeding failed. " +
    "Go to Settings to connect a data source, or check Snowflake connectivity."
  )
}

/**
 * Returns the actuals anchor planning_version string from config,
 * falling back to auto-detecting a real planning_version from the resolved
 * data (whichever value has the most matching rows) when no config value is
 * set — matching the setup wizard's documented "leave blank to auto-select"
 * behavior. Falls back further to the legacy hardcoded constant only if that
 * live-data probe itself fails (e.g. Snowflake unreachable).
 *
 * A blank/stale actualsAnchorVersion that doesn't match any real
 * planning_version silently zeroes out every Actuals row (rowContribution()
 * only counts a row when rowVersion === anchor) — so this auto-detect exists
 * specifically to avoid that failure mode for configs where the field was
 * never set.
 */
export async function getActualsAnchor(): Promise<string> {
  try {
    const cfg = await loadConfig()
    if (cfg?.actualsAnchorVersion) return cfg.actualsAnchorVersion
    if (!cfg) {
      const demoCfg = await loadConfig("Demo")
      if (demoCfg?.actualsAnchorVersion) return demoCfg.actualsAnchorVersion
    }
  } catch {
    // ignore — fall through to auto-detect
  }
  try {
    const bvaFrom = await getBvaFrom()
    // Detect-the-anchor-version-by-frequency query: actuals rows are
    // duplicated across every planning_version except the true anchor, so
    // whichever version has the most rows in the resolved data is very
    // likely the real anchor. Used only when no anchor is explicitly
    // configured (see the function doc comment above for why this matters).
    const rows = await querySnowflake(
      `select planning_version, count(*) as n
       from ${bvaFrom}
       where planning_version is not null
       group by 1
       order by n desc
       limit 1`,
      { warehouse: WAREHOUSE },
    )
    const detected = rows[0]?.PLANNING_VERSION
    if (detected) return String(detected)
  } catch {
    // ignore — fall through to legacy constant
  }
  const { ACTUALS_ANCHOR } = await import("@/lib/bva")
  return ACTUALS_ANCHOR
}
