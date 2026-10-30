/**
 * Raw Snowflake queries backing app/api/setup/discover/route.ts.
 *
 * This is the app's data-discovery route — the UI calls it while a user is
 * picking which tables/columns feed the BvA config (browsing databases,
 * inspecting columns, sampling values, checking whether two tables' keys
 * line up, previewing rows, etc). Each function here is a single query with
 * no interpretation attached — scoring, tiering, and response shaping for
 * the results stay in the route so this file is just "what SQL do we run".
 */
import { querySnowflake } from "../snowflake"
import { WAREHOUSE } from "../bva"

/** Snowflake double-quote identifier */
function q(s: string): string {
  return `"${s.replace(/"/g, '""')}"`
}

/** SQL string literal */
function lit(s: string): string {
  return "'" + s.replace(/'/g, "''") + "'"
}

/** List every database the current role can see (the route filters out Snowflake's own system databases). */
export async function showDatabases(): Promise<Record<string, any>[]> {
  return querySnowflake("show databases", { warehouse: WAREHOUSE })
}

/** List user-created schemas in a database (hides INFORMATION_SCHEMA/PUBLIC, which are never BvA data). */
export async function listSchemasInDatabase(db: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select schema_name from ${q(db)}.information_schema.schemata
           where schema_name not in ('INFORMATION_SCHEMA','PUBLIC')
           order by schema_name`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Scan a database's tables/views for the table browser, optionally narrowed to one
 * schema and/or a keyword typed by the user. INFORMATION_SCHEMA can't call UDFs, so
 * this only fetches the raw metadata — the route scores/sorts results in TS afterward.
 */
export async function scanTablesForBrowser(
  db: string,
  schema: string,
  keyword: string,
): Promise<Record<string, any>[]> {
  const schemaFilter = schema ? `and t.table_schema = upper(${lit(schema)})` : ""
  const keywordFilter = keyword
    ? `and (upper(t.table_name) like upper(${lit("%" + keyword + "%")})
              or upper(t.table_schema) like upper(${lit("%" + keyword + "%")}))`
    : ""

  return querySnowflake(
    `select
              t.table_catalog, t.table_schema, t.table_name, t.table_type,
              t.row_count, to_char(t.last_altered, 'YYYY-MM-DD') as last_altered
           from ${q(db)}.information_schema.tables as t
           where t.table_type in ('BASE TABLE', 'VIEW')
             and coalesce(t.row_count, 0) < 2000000000
             ${schemaFilter}
             ${keywordFilter}
           order by t.row_count desc nulls last
           limit 100`,
    { warehouse: WAREHOUSE },
  )
}

/** List semantic views account-wide, for the semantic-view multi-select browser. */
export async function listSemanticViewsInAccount(): Promise<Record<string, any>[]> {
  return querySnowflake("show semantic views in account", { warehouse: WAREHOUSE })
}

/** List a table's columns with their SQL type, nullability, and position — the basis for the column-role picker. */
export async function listTableColumns(db: string, schema: string, tbl: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select
              column_name, data_type, is_nullable, character_maximum_length,
              numeric_precision, numeric_scale, ordinal_position
           from ${q(db)}.information_schema.columns
           where table_schema = upper(${lit(schema)})
             and table_name   = upper(${lit(tbl)})
           order by ordinal_position`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * Approximate distinct-value counts for a set of columns in one pass (APPROX_COUNT_DISTINCT
 * is a single-scan HLL estimate, far cheaper than a COUNT(DISTINCT ...) per column). Used to
 * guess whether a column looks like a category, an ID, or a free-text field.
 */
export async function computeColumnCardinalities(
  table: string,
  colNames: string[],
): Promise<Record<string, any>[]> {
  const approxExprs = colNames
    .map((c) => `approx_count_distinct(${q(c)}) as ${q("card_" + c)}`)
    .join(", ")
  return querySnowflake(
    `select ${approxExprs} from ${table} limit 1`,
    { warehouse: WAREHOUSE },
  )
}

/** Total row count for a table — used to turn raw cardinality into a percentage. */
export async function countTableRows(table: string): Promise<Record<string, any>[]> {
  return querySnowflake(`select count(*) as rc from ${table}`, { warehouse: WAREHOUSE })
}

/** Top-20 most frequent distinct values of a column, for the inline value-sample preview. */
export async function sampleColumnValues(table: string, col: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select ${q(col)} as val, count(*) as cnt
           from ${table}
           where ${q(col)} is not null
           group by 1 order by 2 desc limit 20`,
    { warehouse: WAREHOUSE },
  )
}

/** First-character prefix counts of a column — helps spot account/ledger-key numbering conventions. */
export async function columnPrefixCounts(table: string, col: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select left(${q(col)}, 1) as prefix, count(*) as cnt
           from ${table}
           where ${q(col)} is not null
           group by 1
           order by 2 desc`,
    { warehouse: WAREHOUSE },
  )
}

/**
 * What % of distinct cost-center values in the actuals table also appear in the budget
 * table's cost-center column. Low overlap is a strong signal the two tables won't join
 * cleanly for a BvA comparison.
 */
export async function checkCostCenterOverlap(
  aTable: string,
  bTable: string,
  aCC: string,
  bCC: string,
): Promise<Record<string, any>[]> {
  return querySnowflake(
    `select
             (select count(distinct ${q(aCC)}) from ${aTable}
              where ${q(aCC)} in (select ${q(bCC)} from ${bTable})) * 100.0
             / nullif((select count(distinct ${q(aCC)}) from ${aTable}), 0) as overlap_pct`,
    { warehouse: WAREHOUSE },
  )
}

/** Grab a handful of actual rows from a table for the inline row preview in the UI. */
export async function previewTableRows(fqn: string): Promise<Record<string, any>[]> {
  return querySnowflake(`SELECT * FROM ${fqn} LIMIT 5`, { warehouse: WAREHOUSE })
}

export type CategoryOverlapParams = {
  ledgerTable: string
  ledgerAccountKeyCol: string
  ledgerCategoryCol: string
  actualsTable: string
  actualsAccountCol: string
  budgetTable: string
  budgetCategoryCol: string
  ledgerKeyPrefixes: string[]
  actualsKeyPrefixes: string[]
}

/**
 * Sanity-checks the ledger mapping configuration two ways: (1) whether the actuals
 * table's account key actually matches the ledger mapping table's key (a format
 * mismatch here means every actuals row silently falls into "Other Expenses"), and
 * (2) whether the ledger mapping's category values actually match the budget table's
 * category values (a wrong column here means categories never line up with budget).
 * Also returns a handful of sample values from each side so a human can eyeball them.
 */
export async function checkCategoryOverlap(params: CategoryOverlapParams): Promise<Record<string, any>[]> {
  const {
    ledgerTable, ledgerAccountKeyCol, ledgerCategoryCol,
    actualsTable, actualsAccountCol,
    budgetTable, budgetCategoryCol,
    ledgerKeyPrefixes, actualsKeyPrefixes,
  } = params

  // Same normalization as lib/config.ts's stripPrefixExpr() — kept in sync manually
  // since this query can't import from the client-safe lib/config.ts's private helper.
  const stripExpr = (col: string, prefixes: string[]): string => {
    if (!prefixes.length) return col
    const alt = prefixes.map((p) => p.replace(/[\\^$.*+?()[\]{}|]/g, "\\$&")).join("|")
    return `regexp_replace(${col}, '^(${alt})', '')`
  }

  return querySnowflake(
    `with
             -- Distinct join keys from each side (cast to string for safe comparison across types)
             act_keys as (
               select distinct ${stripExpr(`${q(actualsAccountCol)}::varchar`, actualsKeyPrefixes)} as k
               from ${actualsTable}
               where ${q(actualsAccountCol)} is not null
               limit 50000
             ),
             lm_keys as (
               select distinct ${stripExpr(`${q(ledgerAccountKeyCol)}::varchar`, ledgerKeyPrefixes)} as k
               from ${ledgerTable}
               where ${q(ledgerAccountKeyCol)} is not null
             ),
             -- Category values from each side
             lm_cats as (
               select distinct ${q(ledgerCategoryCol)} as cat
               from ${ledgerTable}
               where ${q(ledgerCategoryCol)} is not null
             ),
             bud_cats as (
               select distinct ${q(budgetCategoryCol)} as cat
               from ${budgetTable}
               where ${q(budgetCategoryCol)} is not null
             )
           select
             -- Join key stats
             (select count(*) from act_keys)                                  as act_key_count,
             (select count(*) from lm_keys)                                   as lm_key_count,
             (select count(*) from act_keys where k in (select k from lm_keys)) as key_overlap_count,
             (select listagg(k, ', ') within group (order by k)
              from (select k from act_keys limit 5))                          as act_key_sample,
             (select listagg(k, ', ') within group (order by k)
              from (select k from lm_keys limit 5))                           as lm_key_sample,
             -- Category stats
             (select count(*) from lm_cats)                                   as lm_cat_count,
             (select count(*) from bud_cats)                                  as bud_cat_count,
             (select count(*) from lm_cats where cat in (select cat from bud_cats)) as cat_overlap_count,
             (select listagg(cat, ', ') within group (order by cat)
              from (select cat from lm_cats limit 8))                         as lm_cat_sample,
             (select listagg(cat, ', ') within group (order by cat)
              from (select cat from bud_cats limit 8))                        as bud_cat_sample
           from (select 1)`,
    { warehouse: WAREHOUSE },
  )
}

/** Distinct values + counts from a mapped category column, for the rename/merge category editor. */
export async function listCategoryValues(table: string, col: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `SELECT ${q(col)} AS value, COUNT(*) AS cnt
           FROM ${table}
           WHERE ${q(col)} IS NOT NULL
           GROUP BY 1 ORDER BY 2 DESC LIMIT 300`,
    { warehouse: WAREHOUSE, tag: "bva:discover-categories" },
  )
}

/**
 * Every distinct cost-center value (up to 500), tagged with which "looks inactive" naming
 * pattern it matches (DNU_, zz-prefix, INACTIVE_, etc), if any. Flagged values sort first
 * so the full-list exclusions panel can show them prominently.
 */
export async function listAllCostCenterValues(table: string, col: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `SELECT ${q(col)} AS value, COUNT(*) AS cnt,
             CASE
               WHEN ${q(col)} ILIKE 'DNU\\_%'      THEN 'DNU'
               WHEN ${q(col)} ILIKE 'zz%'           THEN 'zz-prefix'
               WHEN ${q(col)} ILIKE 'INACTIVE\\_%'  THEN 'INACTIVE'
               WHEN ${q(col)} ILIKE 'CLOSED\\_%'    THEN 'CLOSED'
               WHEN ${q(col)} ILIKE 'TEST\\_%'      THEN 'TEST'
               WHEN ${q(col)} ILIKE 'DEMO\\_%'      THEN 'DEMO'
               ELSE NULL
             END AS pattern
           FROM ${table}
           WHERE ${q(col)} IS NOT NULL
           GROUP BY 1, 3
           ORDER BY CASE WHEN pattern IS NOT NULL THEN 0 ELSE 1 END, cnt DESC
           LIMIT 500`,
    { warehouse: WAREHOUSE, tag: "bva:discover-all-cc" },
  )
}

/**
 * Only the cost-center values that already match a known "looks inactive" naming pattern
 * (DNU_, zz_, INACTIVE_, CLOSED_, TEST_, DEMO_) — the default-off exclusion candidates a
 * user can opt into removing from the report.
 */
export async function listInactiveCostCenterValues(table: string, col: string): Promise<Record<string, any>[]> {
  return querySnowflake(
    `SELECT ${q(col)} AS value, COUNT(*) AS cnt,
             CASE
               WHEN ${q(col)} ILIKE 'DNU\\_%'      THEN 'DNU'
               WHEN ${q(col)} ILIKE 'zz%'           THEN 'zz-prefix'
               WHEN ${q(col)} ILIKE 'INACTIVE\\_%'  THEN 'INACTIVE'
               WHEN ${q(col)} ILIKE 'CLOSED\\_%'    THEN 'CLOSED'
               WHEN ${q(col)} ILIKE 'TEST\\_%'      THEN 'TEST'
               WHEN ${q(col)} ILIKE 'DEMO\\_%'      THEN 'DEMO'
               ELSE 'other'
             END AS pattern
           FROM ${table}
           WHERE ${q(col)} ILIKE ANY ('DNU\\_%', 'zz%', 'INACTIVE\\_%', 'CLOSED\\_%', 'TEST\\_%', 'DEMO\\_%')
           GROUP BY 1, 3 ORDER BY 2 DESC LIMIT 200`,
    { warehouse: WAREHOUSE, tag: "bva:discover-inactive-cc" },
  )
}

/** Probe whether a fully-qualified table name actually resolves (an empty, zero-row select — cheap and side-effect-free). */
export async function checkTableExists(table: string): Promise<Record<string, any>[]> {
  return querySnowflake(`select * from ${table} where 1=0`, { warehouse: WAREHOUSE })
}

/**
 * Every account key from the ledger mapping table with its resolved (max) category —
 * mirrors what buildFromConfig's ledger_map CTE does — so a user can spot and override
 * specific codes that landed in the wrong category.
 */
export async function listAccountCategories(
  table: string,
  keyCol: string,
  categoryCol: string,
): Promise<Record<string, any>[]> {
  return querySnowflake(
    `SELECT ${q(keyCol)} AS code
                , MAX(${q(categoryCol)}) AS category
                , COUNT(*) AS cnt
           FROM ${table}
           WHERE ${q(keyCol)} IS NOT NULL
             AND ${q(categoryCol)} IS NOT NULL
           GROUP BY ${q(keyCol)}
           ORDER BY category, code`,
    { warehouse: WAREHOUSE, tag: "bva:discover-account-categories" },
  )
}
