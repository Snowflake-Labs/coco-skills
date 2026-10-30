/** Raw Snowflake queries backing the /api/bva, /api/breakdown, and /api/breakdown/entries routes. */

import { querySnowflake } from "../snowflake"
import { WAREHOUSE, sqlStr } from "../bva"

/** Quotes a column name for safe use as a double-quoted SQL identifier. */
function q(s: string) {
  return `"${s.replace(/"/g, '""')}"`
}

/**
 * Fetches the rows behind the main BvA "Working" matrix on the /api/bva page:
 * one row per (planning version, period, expense category), with the raw
 * actual and budget dollar amounts for that combination. The route pivots
 * these rows into the trailing-periods-plus-current-period table shown to
 * the user. `groupBy` picks whether "period" means a fiscal quarter or a
 * fiscal month, and `monthSelect` adds the extra month column to the
 * SELECT list only when grouping by month.
 */
export async function getBvaWorkingRows(bvaFrom: string, groupBy: string, monthSelect: string, whereClause: string) {
  const sql = `
      select planning_version, fiscal_year, ${groupBy} as period_key, expense_category${monthSelect},
             actual_expense, budget_expense
      from ${bvaFrom}
      where ${whereClause}`

  return querySnowflake(sql, { warehouse: WAREHOUSE })
}

/**
 * Fetches the rows for one user-defined "breakout" sub-row on the /api/bva
 * page — a drill-in row nested under a parent expense category that narrows
 * the same period/version data down to a specific set of cost centers or
 * account codes (chosen by `filterType`/`filterValues` in the app's Settings
 * UI). Shares the same base filters (`whereClause`) as the main matrix query
 * above, plus this extra narrowing condition.
 */
export async function getBvaBreakoutRows(
  bvaFrom: string,
  groupBy: string,
  whereClause: string,
  filterType: "cost_center" | "account_code",
  filterValues: string[],
) {
  const filterCol = filterType === "cost_center" ? "coalesce(cost_center_name, to_varchar(cost_center_id))" : "cost_center_id"
  const valList = filterValues.map(sqlStr).join(",")
  const subSql = `
        select planning_version, fiscal_year, ${groupBy} as period_key, expense_category,
               actual_expense, budget_expense
        from ${bvaFrom}
        where ${whereClause}
          AND ${filterCol} in (${valList})`

  return querySnowflake(subSql, { warehouse: WAREHOUSE, tag: "bva:breakout" })
}

/**
 * Fetches the rows behind the /api/breakdown page, which slices the current
 * period's spend two ways at once: totals per expense category, and totals
 * per cost center (with its parent department attached). The route
 * aggregates these rows client-side into the category and cost-center
 * summaries shown on that page.
 */
export async function getBreakdownRows(bvaFrom: string, whereClause: string) {
  const sql = `
      select planning_version, expense_category,
             cost_center_id,
             cost_center_name,
             coalesce(cost_center_name, to_varchar(cost_center_id)) as cost_center_display,
             coalesce(department, '(Unmapped)') as department,
             actual_expense, budget_expense
      from ${bvaFrom}
      where ${whereClause}`

  return querySnowflake(sql, { warehouse: WAREHOUSE })
}

/** Parameters needed to build the journal-entry drill-down query for /api/breakdown/entries. */
export type JournalEntryQueryParams = {
  actualsTable: string
  actualsDateCol: string
  category: string
  categorySource?: string
  ledgerMappingTable?: string
  ledgerAccountKeyCol?: string
  ledgerCategoryCol?: string
  actualsAccountCol?: string
  actualsCategoryCol?: string
  dateFilter: string
  monthFilter: string
  ccFilter: string
  prefixFilter: string
}

/**
 * Fetches the raw underlying transaction rows for the "view journal entries"
 * drill-down on the /api/breakdown/entries route — the detailed rows that
 * roll up into a single category + cost-center total elsewhere in the app.
 *
 * How to find the expense category for a row depends on how the customer's
 * source data is set up (configured in the setup wizard), so this branches
 * three ways:
 *   1. "mapping_table" — account codes are joined against a separate ledger
 *      table that maps each code to a category.
 *   2. "column" — the category is already a plain column on the actuals table.
 *   3. neither — no reliable way to filter by category, so entries are
 *      returned unfiltered by category (still filtered by date/cost center).
 * All three branches otherwise share the same date, month, cost-center, and
 * account-prefix filters (passed in already built by the caller).
 */
export async function getJournalEntryRows(params: JournalEntryQueryParams) {
  const {
    actualsTable,
    actualsDateCol,
    category,
    categorySource,
    ledgerMappingTable,
    ledgerAccountKeyCol,
    ledgerCategoryCol,
    actualsAccountCol,
    actualsCategoryCol,
    dateFilter,
    monthFilter,
    ccFilter,
    prefixFilter,
  } = params

  let sql: string

  if (categorySource === "mapping_table" && ledgerMappingTable && ledgerAccountKeyCol && ledgerCategoryCol && actualsAccountCol) {
    // Join through the ledger mapping table to filter by category
    const catFilter = category
      ? `AND lm.${q(ledgerCategoryCol)} = ${sqlStr(category)}`
      : ""
    sql = `
      SELECT a.*
      FROM ${actualsTable} a
      INNER JOIN ${ledgerMappingTable} lm
              ON CAST(a.${q(actualsAccountCol)} AS VARCHAR) = CAST(lm.${q(ledgerAccountKeyCol)} AS VARCHAR)
      WHERE ${dateFilter}
        ${monthFilter}
        ${ccFilter}
        ${catFilter}
        ${prefixFilter}
      ORDER BY a.${q(actualsDateCol)} DESC
      LIMIT 1000`
  } else if (categorySource === "column" && actualsCategoryCol) {
    const catFilter = category
      ? `AND a.${q(actualsCategoryCol)} = ${sqlStr(category)}`
      : ""
    sql = `
      SELECT a.*
      FROM ${actualsTable} a
      WHERE ${dateFilter}
        ${monthFilter}
        ${ccFilter}
        ${catFilter}
        ${prefixFilter}
      ORDER BY a.${q(actualsDateCol)} DESC
      LIMIT 1000`
  } else {
    // Fallback: no category filter — just filter by date + CC
    sql = `
      SELECT a.*
      FROM ${actualsTable} a
      WHERE ${dateFilter}
        ${monthFilter}
        ${ccFilter}
        ${prefixFilter}
      ORDER BY a.${q(actualsDateCol)} DESC
      LIMIT 1000`
  }

  return querySnowflake(sql, { warehouse: WAREHOUSE, tag: "bva:entries" })
}
