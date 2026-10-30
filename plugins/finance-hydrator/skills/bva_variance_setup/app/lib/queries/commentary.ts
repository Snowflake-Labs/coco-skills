/** Raw Snowflake queries backing the /api/commentary and /api/commentary/history routes. */

import { querySnowflake } from "../snowflake"
import { WAREHOUSE } from "../bva"

function esc(s: string) { return s.replace(/'/g, "''") }

/**
 * Returns the latest commentary row per (category, fiscal_year, period) for
 * a given config. The underlying table is append-only (see insertCommentary
 * below) — every edit inserts a new row rather than overwriting the previous
 * one, so this picks the most recent row per key rather than assuming one
 * row per key.
 */
export async function getLatestCommentary(commentaryTable: string, configName: string, fiscalYear: string) {
  const whereYear = fiscalYear ? `AND fiscal_year = '${esc(fiscalYear)}'` : ""
  return querySnowflake(
    `SELECT category, fiscal_year, period, commentary_text, source, updated_by, updated_at
     FROM ${commentaryTable}
     WHERE config_name = '${esc(configName)}' ${whereYear}
     QUALIFY ROW_NUMBER() OVER (
       PARTITION BY category, fiscal_year, period ORDER BY updated_at DESC
     ) = 1
     ORDER BY fiscal_year, period, category`,
    { warehouse: WAREHOUSE, tag: "bva:commentary-get" },
  )
}

/**
 * Appends a new commentary entry — never updates or deletes an existing one.
 * The table has no primary key; every edit (human-typed or agent-suggested via
 * suggest_commentary) is a new row with its own updated_at, and the GET side
 * always reads back the latest row per (category, fiscal_year, period). This
 * trades unbounded row growth for a guarantee that a commentary write can
 * never silently clobber a prior entry — including one that hasn't loaded
 * into the client yet. See README.md §11 for why this matters for the
 * agent-triggered path specifically (this action fires with no human
 * confirmation step).
 */
export async function insertCommentary(
  commentaryTable: string,
  params: {
    configName: string
    category: string
    fiscalYear: string
    period: string
    text: string
    updatedBy: string
    source: string
  },
) {
  const { configName, category, fiscalYear, period, text, updatedBy, source } = params
  return querySnowflake(
    `INSERT INTO ${commentaryTable}
       (config_name, category, fiscal_year, period, commentary_text, source, updated_by, updated_at)
     VALUES
       ('${esc(configName)}', '${esc(category)}', '${esc(fiscalYear)}', '${esc(period)}',
        '${esc(text)}', '${esc(source)}', '${esc(updatedBy)}', CURRENT_TIMESTAMP())`,
    { warehouse: WAREHOUSE, tag: "bva:commentary-put" },
  )
}

/**
 * Looks up the latest row for one commentary cell, optionally restricted to a
 * specific source — used by the "restore AI version" affordance in
 * CommentaryCell: a manual edit becomes the new overall latest, but the prior
 * AI-generated note is still in this append-only table and can be looked up
 * here even though getLatestCommentary (unfiltered) would no longer surface it.
 */
export async function getCommentaryHistory(
  commentaryTable: string,
  params: {
    configName: string
    category: string
    fiscalYear: string
    period: string
    source: string
  },
) {
  const { configName, category, fiscalYear, period, source } = params
  const whereSource = source ? `AND source = '${esc(source.toUpperCase())}'` : ""
  return querySnowflake(
    `SELECT commentary_text, source, updated_by, updated_at
     FROM ${commentaryTable}
     WHERE config_name = '${esc(configName)}'
       AND category = '${esc(category)}'
       AND fiscal_year = '${esc(fiscalYear)}'
       AND period = '${esc(period)}'
       ${whereSource}
     ORDER BY updated_at DESC
     LIMIT 1`,
    { warehouse: WAREHOUSE, tag: "bva:commentary-history" },
  )
}
