/**
 * Raw Snowflake queries backing app/api/setup/profile/route.ts — the manual,
 * single-table column profiler used when a user picks one table at a time
 * during setup (as opposed to lib/profile-bundle.ts, which profiles several
 * tables in parallel for the automated discovery flow). Each function below
 * is one query with no interpretation attached; the route decides what the
 * numbers mean (grain, fiscal columns, sign, etc). This file also holds
 * suggestRole(), the shared column-role heuristic used by both flows.
 */
import { querySnowflake } from "../snowflake"
import { WAREHOUSE } from "../bva"

/** Snowflake double-quote identifier, escaping embedded quotes. */
function q(s: string): string {
  return `"${s.replace(/"/g, '""')}"`
}

/**
 * Column metadata (name, SQL type, nullability, position) for one table —
 * the starting point every other profiling step in this file builds on.
 */
export async function getProfileColumns(
  db: string,
  schema: string,
  tbl: string,
): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select column_name, data_type, is_nullable, ordinal_position
     from "${db}".information_schema.columns
     where table_schema = upper('${schema.replace(/'/g, "''")}')
       and table_name   = upper('${tbl.replace(/'/g, "''")}')
     order by ordinal_position`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Row count plus an approximate distinct-value count (APPROX_COUNT_DISTINCT)
 * for every column, all in one scan of the table. This is the expensive part
 * of profiling, so it's batched into a single query instead of one query per
 * column. Index-based aliases (c0, c1, ...) are used instead of the column's
 * own name so Snowflake's automatic uppercasing stays predictable and the
 * result lookup by alias is reliable.
 */
export async function getColumnCardinalities(
  table: string,
  colNames: string[],
): Promise<Record<string, any>[]> {
  const limited = colNames.slice(0, 100)
  const exprs = [`count(*) as rc`, ...limited.map((c, i) => `approx_count_distinct(${q(c)}) as c${i}`)]
  return querySnowflake(`select ${exprs.join(", ")} from ${table}`, { warehouse: WAREHOUSE })
}

/**
 * Plain row count for a table, with no per-column cardinality work. Used as
 * a cheaper fallback when the batched cardinality query above fails (e.g.
 * too many columns, or a transient warehouse issue) — profiling can still
 * report a row count even if it can't estimate cardinality.
 */
export async function getTableRowCount(table: string): Promise<Record<string, any>[]> {
  return querySnowflake(`select count(*) as rc from ${table}`, { warehouse: WAREHOUSE })
}

/**
 * Up to 5 distinct, non-null sample values for one column — enough for the
 * setup UI to show the user a taste of what's actually in a column (e.g.
 * "Q1, Q2, Q3, Q4" for a quarter column) without pulling the whole table.
 */
export async function sampleDistinctColumnValues(
  table: string,
  col: string,
): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select distinct ${q(col)} as v from ${table} where ${q(col)} is not null order by 1 limit 5`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * How many distinct dates a table has, and how many months its data spans —
 * the ratio between the two tells us whether the table is at daily, monthly,
 * quarterly, or annual grain (e.g. roughly 30 distinct dates per month of
 * span means daily; roughly 1 per month means monthly).
 */
export async function getDateGrainStats(table: string, dateCol: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select
        approx_count_distinct("${dateCol.replace(/"/g, '""')}") as distinct_periods
        , datediff('month', min("${dateCol.replace(/"/g, '""')}"), max("${dateCol.replace(/"/g, '""')}")) + 1 as span_months
     from ${table}`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * For one numeric column: its average value and what percentage of its
 * non-null rows are positive. Used to guess whether a dollar-amount column
 * stores expenses as positive numbers, negative numbers, or a mix, so the
 * dashboard can normalize signs consistently.
 */
export async function getSignStats(table: string, col: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select
        avg("${col.replace(/"/g, '""')}") as mean_val
        , sum(case when "${col.replace(/"/g, '""')}" > 0 then 1 else 0 end) * 100.0 / nullif(count(*), 0) as pct_positive
     from ${table}
     where "${col.replace(/"/g, '""')}" is not null`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Guesses a column's business role (date, dollar amount, cost center, etc.)
 * purely from its name and SQL type — no need to sample the data or ask a
 * person to eyeball every column. This lets the setup UI pre-fill sensible
 * defaults for a table that can have hundreds of columns; the user still
 * confirms (or overrides) every mapping, so a wrong guess just costs a click,
 * not correctness.
 *
 * `card` (an approximate distinct-value count) and `rowCount` are optional
 * hints that sharpen a few of the guesses (e.g. telling a real category
 * column apart from a free-text description column of similar cardinality).
 * They default to 0, which is what a caller that doesn't yet know the
 * cardinality (or doesn't need that level of precision) should pass.
 *
 * This function is shared by both the manual per-table profiling API
 * (app/api/setup/profile/route.ts) and the automated multi-table discovery
 * bundle (lib/profile-bundle.ts) — the two previously kept separate copies
 * of this heuristic that had drifted slightly apart; this is the merged,
 * canonical version covering every rule either copy had.
 */
export function suggestRole(name: string, dtype: string, card = 0, rowCount = 0): string | null {
  const n = name.toUpperCase()
  const isDate = /DATE|TIMESTAMP/.test(dtype)
  const isNumber = /NUMBER|FLOAT|DECIMAL|INT/.test(dtype)
  const isText = /VARCHAR|TEXT|STRING|CHAR/.test(dtype)
  const cardPct = rowCount > 0 ? card / rowCount : 0

  // Row-number / rank / index columns are never a meaningful business role.
  if (/^_?RN$|ROW_?NUM|^RANK$|^IDX$|^INDEX$/.test(n)) return null

  // ── Date columns ──────────────────────────────────────────────────────────
  if (isDate) {
    // Pipeline/ETL bookkeeping timestamps (load time, last-modified, etc.)
    // aren't fiscal periods even though they're dates.
    if (/INGEST|LOAD_|ETL_|^CREATED_AT$|^UPDATED_AT$|MODIFIED|^LAST_UPDATED/.test(n)) return null
    return "period"
  }

  // ── Fiscal year / quarter (checked before number — some schemas store these as varchar) ──
  if (/FISCAL_?YEAR|^FY[_0-9]|^FISCAL_YR/.test(n)) return "fiscal_year"
  if (/FISCAL_?QUARTER|FISCAL_?QTR|^FQ[_0-9]/.test(n)) return "fiscal_quarter"

  // ── Number columns ───────────────────────────────────────────────────────
  if (isNumber) {
    // Explicit exclusions — join keys, surrogate IDs, rates, ratios, and
    // counts are never dollar amounts even if the name is otherwise vague.
    if (/RATE$|EXCHANGE|_RATE_|FX_|RATIO|COUNT$|_COUNT$|^ID$|_ID$|_KEY$|^KEY$|CODE$/.test(n)) return null
    // Strong amount signal — suggest even when cardinality is unavailable.
    if (/AMOUNT|AMT|EXPENSE|SPEND|COST|VALUE|BALANCE|DEBIT|CREDIT|NET/.test(n)) return "amount"
    // Weaker signal — only suggest when the cardinality looks like a continuous measure.
    if (card > 0 && card > rowCount * 0.005) return "amount"
    // Otherwise fall through to the shared catch-all below (e.g. a numeric FISCAL_PERIOD).
  } else if (isText) {
    // Category — includes Adaptive Insights naming conventions.
    if (/CATEGOR|SPEND_CAT|EXPENSE_TYPE|EXPENSE_CLASS|ACCOUNT_TYPE|LEDGER_ACCOUNT|ADAPTIVE|CONTROLLABLE/.test(n)) {
      if (card === 0 || (card >= 3 && card <= 300)) return "category"
    }
    // CC name — check BEFORE the generic cost_center pattern so COST_CENTER_NAME
    // maps to the name role, not the ID role.
    if (/COST_?CENTER_?NAME|^CC_?NAME$/.test(n)) return "cc_name"
    // Cost center (ID / code / number join key).
    if (/COST_?CENTER|^CC_?ID$|^CC_?CODE$|COST_CTR/.test(n)) return "cost_center"
    // Department / rollup — a higher-level grouping above cost centers.
    if (/^DEPARTMENT$|DEPARTMENT_?NAME|^DEPT$|^ORG$|^DIVISION$|BVA_DEPARTMENT/.test(n)) return "department"
    // Version / plan.
    if (/VERSION|PLAN_NAME|FORECAST_NAME|SCENARIO/.test(n) && (card === 0 || card <= 30)) return "version"
    // High-cardinality free text reads like a description, not a structured field — skip.
    if (card > 0 && cardPct > 0.3) return null
  }

  // ── Shared catch-all — patterns that apply no matter the column's SQL type ──
  if (/LEDGER_ACCOUNT|ACCOUNT_NUMBER|ACCOUNT_CODE|^ACCOUNT$|^CODE$/.test(n)) return "account"
  if (/PERIOD|^MONTH$|FISCAL_PERIOD/.test(n)) return "period"

  return null
}
