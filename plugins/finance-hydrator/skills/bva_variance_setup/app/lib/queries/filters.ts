/** Raw Snowflake queries backing the /api/filters route's dropdown/filter data. */

import { querySnowflake } from "../snowflake"
import { WAREHOUSE } from "../bva"

/**
 * Checks whether a column exists on a source table/view by running a
 * zero-row probe query. If the column doesn't exist, Snowflake throws and
 * the caller treats that as "no" — used to auto-detect whether the data
 * source has a month-grain column (some sources only go down to quarter).
 */
export async function probeColumnExists(col: string, from: string) {
  return querySnowflake(`select ${col} from ${from} where 1=0`, { warehouse: WAREHOUSE })
}

/**
 * Returns the distinct non-null values of a custom-logic derived column —
 * an ad-hoc filter that the setup wizard's "custom logic" rules layer on
 * top of the standard BvA fields. Used to populate that column's filter
 * dropdown in the dashboard controls.
 */
export async function getDistinctExtraFilterValues(column: string, from: string) {
  return querySnowflake(
    `select distinct ${column} as v from ${from} where ${column} is not null order by 1`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Returns every planning version (a budget/forecast vintage, e.g. a specific
 * quarterly re-forecast) present in the data, newest first, so the
 * dashboard's version picker has something to show.
 */
export async function getDistinctVersions(from: string) {
  return querySnowflake(`select distinct planning_version as v from ${from} order by 1 desc`, { warehouse: WAREHOUSE })
}

/**
 * Returns every fiscal year present in the data (e.g. "FY2026"), used to
 * populate the Fiscal Year filter.
 */
export async function getDistinctFiscalYears(from: string) {
  return querySnowflake(`select distinct fiscal_year as v from ${from} order by 1`, { warehouse: WAREHOUSE })
}

/**
 * Returns every fiscal quarter present in the data (e.g. "Q1"), used to
 * populate the Quarter filter.
 */
export async function getDistinctFiscalQuarters(from: string) {
  return querySnowflake(`select distinct fiscal_quarter as v from ${from} order by 1`, { warehouse: WAREHOUSE })
}

/**
 * Returns every department present in the data, substituting "(Unmapped)"
 * for rows with no department set, so the Organization filter always has a
 * value to group under rather than a blank row.
 */
export async function getDistinctDepartments(from: string) {
  return querySnowflake(
    `select distinct coalesce(department, '(Unmapped)') as v from ${from} order by 1`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Returns every cost center for the Cost Center filter, preferring the
 * human-readable name and falling back to the numeric ID when no name is
 * mapped for that cost center.
 */
export async function getDistinctCostCenters(from: string) {
  return querySnowflake(
    `select distinct coalesce(cost_center_name, to_varchar(cost_center_id)) as v from ${from} order by 1`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Returns every expense category present in the data, used to populate the
 * Category filter and (by the caller) to decide whether the source has
 * enough categories to support a category breakdown view.
 */
export async function getDistinctCategories(from: string) {
  return querySnowflake(`select distinct expense_category as v from ${from} order by 1`, { warehouse: WAREHOUSE })
}

/**
 * Returns every value of the detected month column, so the dashboard can
 * offer month-level filtering when the source data supports it.
 */
export async function getDistinctMonthValues(monthColumn: string, from: string) {
  return querySnowflake(`select distinct ${monthColumn} as v from ${from} order by 1`, { warehouse: WAREHOUSE })
}
