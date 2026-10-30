/** Shared query helpers for the BvA variance API routes. */

export const WAREHOUSE = process.env.BVA_WAREHOUSE ?? "MY_WAREHOUSE"

// FY2027 Q3/Q4 are future periods (budget only, zero actuals).
export const FUTURE_PERIODS = new Set(["FY2027|Q3", "FY2027|Q4"])

// A "series" is either Actuals or a specific planning version.
export const ACTUALS_OPTION = "Actuals"
// Actuals are duplicated across every planning version; read them from a single
// anchor version to avoid triple-counting. Values are identical across versions.
export const ACTUALS_ANCHOR = "Board Plan"

export function isActuals(series: string): boolean {
  return series === ACTUALS_OPTION
}

/** The planning_version whose rows are needed to evaluate a series selection. */
export function seriesVersion(series: string, anchor = ACTUALS_ANCHOR): string {
  return isActuals(series) ? anchor : series
}

/**
 * Contribution of a single row to a series value.
 * Actuals: actual_expense from the anchor version only. Forecast: budget_expense
 * from the matching version. Returns 0 when the row does not belong to the series.
 */
export function rowContribution(
  series: string,
  rowVersion: string,
  actual: number,
  budget: number,
  anchor = ACTUALS_ANCHOR,
): number {
  if (isActuals(series)) return rowVersion === anchor ? actual : 0
  return rowVersion === series ? budget : 0
}

/** Escape a value for safe interpolation as a SQL string literal. */
export function sqlStr(s: string): string {
  return "'" + s.replace(/'/g, "''") + "'"
}

/**
 * Validates a user-supplied "table" query/body param before it's ever
 * interpolated into a FROM/SELECT clause. Requires exactly three
 * dot-separated segments (DATABASE.SCHEMA.TABLE), each made only of
 * identifier-safe characters. This is a shape check, not a full parser —
 * it exists specifically to reject a crafted value like
 * `A.B.C UNION SELECT ...` (which would otherwise still "look like" three
 * dot-separated parts if checked only by `.split(".").length === 3`)
 * before it reaches raw SQL. Every route that takes a table name from a
 * request must call this — see app/api/setup/discover/route.ts (the
 * original, since-duplicated implementation), app/api/setup/profile/route.ts,
 * lib/profile-bundle.ts.
 */
export function validateTableParam(table: string | null | undefined): table is string {
  return typeof table === "string" && /^[A-Za-z0-9_$"]+\.[A-Za-z0-9_$"]+\.[A-Za-z0-9_$"]+$/.test(table)
}

/** NUMBER(38,x) columns can arrive as strings; coerce safely. */
export function num(v: unknown): number {
  const n = typeof v === "number" ? v : parseFloat(String(v ?? "0"))
  return Number.isFinite(n) ? n : 0
}

export function pct(variance: number, budget: number): number | null {
  if (!budget) return null
  return Math.round((variance / Math.abs(budget)) * 1000) / 10
}

export function periodLabel(fy: string, fq: string): string {
  return `${fy.replace("FY20", "FY")} ${fq}`
}

/**
 * Parse the lead fiscal period from a planning-version name.
 * "FY25 Q4 Forecast"                  -> { year: "FY2025", quarter: "Q4" }
 * "FY25 Plan" / "FY24 Revised Plan"    -> { year: "FY2025", quarter: null }
 * "Actuals" / "Annual Plan"            -> null
 */
export function parseVersionPeriod(version: string): { year: string; quarter: string | null } | null {
  // Full match: FY year + specific quarter (e.g. "FY25 Q2 Forecast")
  const full = /^FY(\d{2})\s+(Q[1-4])/.exec(version)
  if (full) return { year: `FY20${full[1]}`, quarter: full[2] }
  // Year-only match: FY year without a Q (e.g. "FY25 Plan", "FY24 Revised Plan")
  const yearOnly = /^FY(\d{2})\s/.exec(version)
  if (yearOnly) return { year: `FY20${yearOnly[1]}`, quarter: null }
  return null
}

/** Per-version period coverage, cached in BvaConfig.versionPeriods (see lib/config.ts). */
export type VersionPeriods = Record<string, { years: string[]; quarters: string[] }>

/**
 * Resolve what the Fiscal Year / Quarter filters should become when `version`
 * is selected as V1/V2. Prefers `versionPeriods` (real coverage computed from
 * the data — see lib/config-server.ts's updateVersionPeriods) over parsing the
 * version NAME, since names are frequently forecast-vintage labels rather than
 * period-scope labels (e.g. "Q1 Forecast" can span many quarters — narrowing
 * to Q1 from the name alone would hide the rest of that version's data).
 *
 * - A single known year -> that year; otherwise year is left `null` (ambiguous
 *   or multi-year — don't guess, leave the current fiscal year untouched).
 * - A single known quarter (only meaningful once year is resolved) -> that
 *   quarter; otherwise `"Full Year"` if year resolved, else `null`.
 * - No cached entry for this version at all -> fall back to the legacy
 *   name-based `parseVersionPeriod()` heuristic (covers single_table configs,
 *   which don't get versionPeriods computed, and configs saved before this
 *   existed).
 */
export function resolveVersionPeriod(
  version: string,
  versionPeriods?: VersionPeriods,
): { year: string | null; quarter: string | null } | null {
  const cached = versionPeriods?.[version]
  if (cached) {
    const year = cached.years.length === 1 ? cached.years[0] : null
    if (!year) return null // spans multiple years (or none) — no signal, leave filters alone
    const quarter = cached.quarters.length === 1 ? cached.quarters[0] : "Full Year"
    return { year, quarter }
  }
  return parseVersionPeriod(version)
}

/** Chronological sort key for a fiscal_year + fiscal_quarter pair. */
export function periodKey(fy: string, fq: string): string {
  return `${fy}|${fq}`
}

const MONTH_ABBR = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

/**
 * Returns a human-readable month range label for a fiscal quarter.
 * e.g. quarterMonthLabel("Q1", 1) → "Jan – Mar"
 *      quarterMonthLabel("Q3", 2) → "Aug – Oct"  (FY starts Feb)
 * Returns "" for "Full Year" or unrecognised values.
 */
export function quarterMonthLabel(quarter: string, fyStartMonth: number): string {
  const qNum = { Q1: 0, Q2: 1, Q3: 2, Q4: 3 }[quarter]
  if (qNum === undefined) return ""
  const startIdx = ((fyStartMonth - 1) + qNum * 3) % 12
  const endIdx   = ((fyStartMonth - 1) + qNum * 3 + 2) % 12
  return `${MONTH_ABBR[startIdx]} – ${MONTH_ABBR[endIdx]}`
}

/** Parse a comma-separated multi-value filter param into a clean array. */
export function csvParam(value: string | null): string[] {
  return value ? value.split(",").map((s) => s.trim()).filter(Boolean) : []
}

// Matches lib/custom-logic-guard.ts's alias validation — an extra-filter
// column name only ever comes from a CustomLogicRule's own alias, but this
// request-level param is user-controllable, so it's re-validated here too
// rather than trusted just because it round-tripped through the filters API.
const SAFE_COLUMN_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/

/**
 * Parses the `extraFilters` query param — a JSON object mapping a
 * CustomLogicRule derived_column alias to an array of selected values, e.g.
 * `{"allocation_status":["Unallocated"]}` — as sent by the dashboard's
 * dynamic filter controls (see components/bva-controls.tsx's generic
 * MultiSelect, wired up in bva-dashboard.tsx / expense-breakdown.tsx).
 * Malformed JSON or an unsafe column name is dropped silently rather than
 * erroring the whole request — an ad-hoc filter is optional by nature.
 */
export function parseExtraFilters(raw: string | null): Record<string, string[]> {
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>
    const out: Record<string, string[]> = {}
    for (const [col, vals] of Object.entries(parsed)) {
      if (!SAFE_COLUMN_RE.test(col)) continue
      if (!Array.isArray(vals)) continue
      const clean = vals.filter((v): v is string => typeof v === "string" && v.length > 0)
      if (clean.length) out[col] = clean
    }
    return out
  } catch {
    return {}
  }
}

/** Builds `col in (...)` WHERE predicates from a parsed extraFilters map. */
export function extraFilterWhereClauses(extra: Record<string, string[]>): string[] {
  return Object.entries(extra).map(([col, vals]) => `${col} in (${vals.map(sqlStr).join(",")})`)
}

/**
 * Build the shared WHERE predicates (year, quarter, departments, categories).
 * Version predicates are added by each route separately.
 */
export function commonPredicates(params: URLSearchParams): string[] {
  const where: string[] = []
  const year = params.get("year") ?? "All"
  const quarter = params.get("quarter") ?? "All"
  const departments = csvParam(params.get("departments"))
  const categories = csvParam(params.get("categories"))

  if (year === "FY2026" || year === "FY2027") where.push(`fiscal_year = ${sqlStr(year)}`)
  if (["Q1", "Q2", "Q3", "Q4"].includes(quarter)) where.push(`fiscal_quarter = ${sqlStr(quarter)}`)
  if (departments.length) {
    where.push(`coalesce(department, '(Unmapped)') in (${departments.map(sqlStr).join(",")})`)
  }
  if (categories.length) where.push(`expense_category in (${categories.map(sqlStr).join(",")})`)
  return where
}
