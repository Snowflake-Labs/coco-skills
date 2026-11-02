/** Raw Snowflake queries backing the /api/setup/validate data-quality checks. */

import { querySnowflake } from "../snowflake"
import { WAREHOUSE } from "../bva"

/**
 * Counts rows in the baseline query (the BvA mapping before any custom logic
 * rules are layered on). Used to isolate whether a downstream failure comes
 * from the base table/column mapping itself, or from a custom logic rule
 * added on top of it.
 */
export async function getBaselineRowCount(baselineSql: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select count(*) as rc from (${baselineSql}) as bva_baseline limit 1`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Counts rows in the fully assembled BvA query (baseline + any enabled
 * custom logic). An empty result here means the table/column mapping in the
 * config doesn't actually match any real data.
 */
export async function getRowCount(from: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select count(*) as rc from ${from} limit 1`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Counts rows with a missing fiscal_year, fiscal_quarter, or expense_category.
 * These three columns are load-bearing for the dashboard's grouping/filtering,
 * so nulls here indicate a broken column mapping upstream.
 */
export async function getNullCounts(from: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select
        sum(case when fiscal_year is null then 1 else 0 end) as null_fy,
        sum(case when fiscal_quarter is null then 1 else 0 end) as null_fq,
        sum(case when expense_category is null then 1 else 0 end) as null_cat
     from ${from}`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Measures how much actuals and budget spend is present, and — separately —
 * how much of that actuals spend is actually joined to a planning_version
 * (and therefore visible in the dashboard, which filters on that column).
 * A row can have real actual_expense but still be invisible if it never
 * matched a budget row on the join keys (fiscal period, cost center, category).
 */
export async function getCoverageStats(from: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select
        sum(case when actual_expense != 0 then 1 else 0 end)                                as has_actuals_total,
        sum(case when actual_expense != 0 and planning_version is not null then 1 else 0 end) as has_actuals_visible,
        sum(case when budget_expense  != 0 then 1 else 0 end)                                as has_budget,
        round(sum(actual_expense) / 1e6, 1)                                                  as total_actual_m,
        round(sum(case when planning_version is not null then actual_expense else 0 end) / 1e6, 1) as visible_actual_m,
        round(sum(budget_expense)  / 1e6, 1)                                                 as total_budget_m
     from ${from}`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Counts the distinct planning_version values present (e.g. "Budget",
 * "Latest Forecast"). At least one is required for the
 * dashboard's version selector to have anything to show.
 */
export async function getPlanningVersionCount(from: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select count(distinct planning_version) as vc from ${from}`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Computes the percentage of rows that have a non-null cost_center_name.
 * A low percentage isn't fatal — the cost center filter just falls back to
 * showing raw IDs instead of friendly names — so this backs a warning-only
 * check rather than a hard failure.
 */
export async function getCostCenterNameCoverage(from: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select
        sum(case when cost_center_name is not null then 1 else 0 end) * 100.0 / nullif(count(*), 0) as pct_named
     from ${from}`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Compares the set of expense_category values seen on actuals rows against
 * the set seen on budget rows, and counts how many categories appear on
 * both sides. The BvA join matches actuals to budget partly on category, so
 * zero overlap means that join can never succeed and actuals will always
 * show as $0 against budget in the dashboard.
 */
export async function getCategoryOverlap(from: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select
        count(distinct case when actual_expense != 0 then expense_category end) as act_cat_count,
        count(distinct case when budget_expense  != 0 then expense_category end) as bud_cat_count,
        count(distinct case when actual_expense != 0 and budget_expense != 0 then expense_category end) as overlap_count,
        listagg(distinct case when actual_expense != 0 then expense_category end, ', ')
            within group (order by 1) as act_cats,
        listagg(distinct case when budget_expense  != 0 then expense_category end, ', ')
            within group (order by 1) as bud_cats
     from ${from}`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Pulls a handful of sample rows from the assembled BvA query so the setup
 * UI can show the user a preview of what the config actually produces.
 */
export async function getSampleRows(from: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select * from ${from} limit 5`,
    { warehouse: WAREHOUSE },
  )
}
