/**
 * BvA app configuration — persists to a BVA_APP_CONFIG Snowflake table.
 *
 * Source priority (highest to lowest):
 *   1. BVA_APP_CONFIG table 'default' config — shared, persistent, editable via Settings page
 *   2. BVA_APP_CONFIG table 'Demo' config    — fully synthetic, auto-seeded on first run (lib/demo-seed.ts)
 *   3. bva_query.sql file                    — skill output drop-in (backward compatible)
 *
 * Set BVA_CONFIG_TABLE env var to point at the config table location for
 * this deployment (see lib/config-server.ts's getConfigTable()).
 */
/**
 * Client-safe: types + pure SQL assembler only.
 * Server-only functions (loadConfig, invalidateConfigCache) live in lib/config-server.ts.
 */

// ─── Types ────────────────────────────────────────────────────────────────────

export type FilterPreset = {
  id: string
  name: string
  createdAt: string
  filters: {
    organizations: string[]
    categories: string[]
    costCenters: string[]
    version1: string
    version2: string
    fiscalYear: string
    quarter: string
  }
}

export type BvaCapabilities = {
  hasOrganization: boolean      // department column has non-null values
  hasMonthlyGrain: boolean      // grain = "month"
  hasMultipleVersions: boolean  // 2+ planning versions
  hasCategories: boolean        // 3+ distinct expense categories
  hasCostCenterNames: boolean   // cost_center_name is populated
  hasMultipleYears: boolean     // 2+ distinct fiscal years
  hasExpenseBreakdown: boolean  // categories + cost centers both present
}

export type ClusterPattern = {
  label: string
  patternType: "prefix" | "ilike" | "like" | "in" | "catchall"
  patternValue?: string | string[]
  include: boolean
}

// Sub-category breakout: defines an indented row under a parent category.
// The row is computed by filtering the source data to the specified values.
export type BreakoutGroup = {
  id: string
  parentCategory: string              // which category row this appears under
  label: string                       // display label for the sub-row
  filterType: "cost_center" | "account_code"
  filterValues: string[]
}

// User-defined summary row (a named total shown at the bottom of the table).
export type SummaryRowDef = {
  id: string
  name: string
  includeCategories?: string[]        // sum only these; if omitted = all categories
  excludeCategories?: string[]        // exclude these from the sum
  style?: "bold" | "subtotal"
}

// Custom logic layer — user- or agent-authored rules composed on top of the
// baseline query from buildFromConfig(), never mutating it. See buildFinalSql()
// below. Each rule can be toggled or deleted independently, which reverts the
// final query to baseline behavior with no residual effect — that's the whole
// point of keeping this a separate layer instead of a config field that
// mutates buildFromConfig() itself.
export type CustomLogicRule = {
  id: string
  name: string
  description?: string                // the natural-language ask that produced it (for editing/re-prompting)
  kind: "derived_column" | "filter"
  sql: string                         // a single SQL expression, never a full statement — see lib/custom-logic-guard.ts
  alias?: string                      // required for kind === "derived_column"; validated as a safe identifier
  enabled: boolean
  source: "agent" | "manual"
}

export type BvaConfig = {
  configName: string
  sourceType: "single_table" | "multi_table"

  // Single-table path (fully-qualified table name or a SELECT query)
  singleTable?: string

  // Multi-table path ────────────────────────────────────────────────
  actualsTable?: string
  budgetTable?: string
  costCenterTable?: string
  ledgerMappingTable?: string

  // Actuals column mapping
  actualsDateCol?: string
  actualsAmountCol?: string
  actualsCostCenterCol?: string
  actualsCategoryCol?: string
  actualsAccountCol?: string
  accountFilterPrefixes?: string[]
  negateAmount?: boolean

  // Budget column mapping
  budgetPeriodCol?: string
  budgetPeriodFormat?: "date" | "fiscal_quarter_string" | "annual"
  budgetAmountCol?: string
  budgetCostCenterCol?: string
  budgetCategoryCol?: string
  budgetVersionCol?: string | null

  // Ledger account mapping table (optional — maps account codes → expense categories)
  ledgerAccountKeyCol?: string  // account code column (joins to actualsAccountCol)
  ledgerCategoryCol?: string    // expense category column in the mapping table

  // Cost center dimension
  ccIdCol?: string
  ccNameCol?: string
  ccDeptCol?: string
  ccUpdatedAtCol?: string

  // Fiscal config
  fiscalPath?: "A" | "B"       // A = pre-computed year/quarter cols, B = derive from date
  fyStartMonth?: number | null  // 1-12
  fiscalYearColActuals?: string
  fiscalQuarterColActuals?: string
  fiscalYearColBudget?: string
  fiscalQuarterColBudget?: string
  rollupExclusions?: string[]   // non-Q values to exclude (e.g. "FY", "TOTAL")

  // Category handling
  categorySource?: "column" | "mapping_table" | "cluster_analysis" | "category_remap"
  clusterPatterns?: ClusterPattern[]
  renameMap?: Record<string, string>
  // Which table to browse for rename values in the Adjustments UI ("budget" | "actuals" | "ledger")
  categoryRenameSource?: "budget" | "actuals" | "ledger"

  // Account/GAAP rebucket — display-time rename of ledger category values (applied after the join)
  accountRenameMap?: Record<string, string>

  // Account category overrides — account-code-level category reassignment.
  // Takes precedence over the ledger mapping table: { "account_code": "Target Category" }
  accountCategoryOverrides?: Record<string, string>

  // Cost center exclusions — specific CC values to exclude from BvA (e.g. DNU_ ones the user opts out of)
  excludedCostCenters?: string[]

  // Manual, non-destructive normalization applied to cross-table join-key
  // columns before matching (e.g. one table's account/cost-center codes carry
  // a prefix the other table's codes don't, so a straight equality join
  // silently drops matches). Key = which mapped column role to normalize;
  // value = list of literal prefixes stripped from the START of that
  // column's value at query time. Applied only inside the generated SQL in
  // buildFromConfig() — the source tables are never modified. See
  // stripPrefixExpr() below for the exact SQL.
  keyStripPrefixes?: Partial<Record<
    "actualsAccountCol" | "ledgerAccountKeyCol" |
    "actualsCostCenterCol" | "budgetCostCenterCol" | "ccIdCol",
    string[]
  >>

  // Agent config editing — when true, the dashboard chat agent may suggest accountCategoryOverrides patches.
  // Each accepted suggestion triggers rematerialization. All suggestions still require human confirmation.
  agentCanEditConfig?: boolean

  // Saved filter presets — shared across dashboard pages, persisted via /api/setup/presets
  savedFilterPresets?: FilterPreset[]

  // Which planning_version row holds canonical actuals (for dedup)
  actualsAnchorVersion?: string

  // Cached per-version period coverage — computed once from real data after
  // materialization (see lib/config-server.ts's updateVersionPeriods), used to
  // auto-narrow Fiscal Year / Quarter when a version is selected as V1/V2,
  // without re-querying per filter change or guessing from the version NAME
  // (many version names are forecast-vintage labels, not period-scope labels —
  // e.g. "Q1 Forecast" can span many quarters). User-editable in the Fiscal
  // Config step (step-fiscal.tsx). Once a version has an entry — whether
  // auto-detected or user-edited — later materializations never overwrite it;
  // clearing the entry lets it be auto-re-detected on the next save.
  versionPeriods?: Record<string, { years: string[]; quarters: string[] }>

  // Detected capabilities (saved at setup; re-checked live from /api/filters)
  capabilities?: BvaCapabilities

  // Sub-category breakout rules — user-defined indented rows under parent categories.
  categoryBreakouts?: BreakoutGroup[]

  // User-defined summary/total rows shown at the bottom of the BvA table.
  // If empty, a single "Total Expenses" row is used as the default.
  summaryRows?: SummaryRowDef[]

  // Brand color palette (optional — defaults to SNOWFLAKE_COLORS).
  brandColors?: BrandColors

  // Custom logic rules layered on top of the baseline query — see
  // CustomLogicRule and buildFinalSql(). Never consumed by buildFromConfig().
  customLogicRules?: CustomLogicRule[]

  // Semantic view(s) the customer pointed at manually (Step 1) or that the
  // setup agent auto-discovered and the customer confirmed. Fully-qualified
  // names, e.g. "MY_DB.MY_SCHEMA.MY_SEMANTIC_VIEW". See lib/semantic-view-bundle.ts.
  // Purely a discovery/mapping-confidence aid — never queried directly by
  // buildFromConfig()/buildFinalSql().
  semanticViewRefs?: string[]

  updatedAt?: string

  // Set on the 'default' config only, by /api/setup/config/activate, to the
  // name of the saved profile that was last copied into 'default'. Lets the
  // Settings sidebar show which saved profile is currently active — never
  // set on a named profile's own row, only on 'default'.
  activatedFrom?: string
}

// Brand color palette — applied to table headers, accent colors, and charts.
// If omitted, the app uses SNOWFLAKE_COLORS as the default.
export type BrandColors = {
  headerBg: string       // dark header row background
  headerMid: string      // sub-header row background
  accent: string         // V1 / actuals highlight color
  accentAlt: string      // V2 / forecast / budget color
}

export const SNOWFLAKE_COLORS: BrandColors = {
  headerBg:  "#11264F",
  headerMid: "#1A3A6E",
  accent:    "#29B5E8",
  accentAlt: "#0F5FA8",
}

/**
 * True when a config has a usable data source. Used by the dashboard pages to
 * decide whether to load/show data: no config (or a config with no source
 * table) → render an empty "complete setup" state instead of data.
 */
export function isConfigured(cfg: BvaConfig | null | undefined): boolean {
  if (cfg === undefined) return false  // still loading — don't show data yet
  if (cfg === null) return true        // no config → demo mode (server uses fallback table)
  if (cfg.sourceType === "single_table") return !!cfg.singleTable?.trim()
  return !!cfg.actualsTable?.trim()
}

// ─── Config validation ────────────────────────────────────────────────────────

/**
 * Validates that the config has all required fields for the chosen options.
 * Returns an array of human-readable error strings.
 * Empty array = no errors.
 *
 * Called by buildFromConfig() (errors injected as SQL comments) and by the
 * validate API route as a pre-flight check before running any SQL.
 */
export function validateBuildConfig(cfg: BvaConfig): string[] {
  if (cfg.sourceType === "single_table") return []

  const errors: string[] = []

  if (!cfg.actualsTable)
    errors.push("actualsTable is not set — configure the GL/actuals table in Step 1")
  if (!cfg.budgetTable)
    errors.push("budgetTable is not set — configure the budget/forecast table in Step 1")

  if (cfg.categorySource === "mapping_table") {
    if (!cfg.ledgerMappingTable)
      errors.push("categorySource is 'mapping_table' but ledgerMappingTable is not set — configure in Step 1")
    if (!cfg.ledgerAccountKeyCol)
      errors.push("categorySource is 'mapping_table' but ledgerAccountKeyCol is not set — configure in Step 2")
    if (!cfg.ledgerCategoryCol)
      errors.push("categorySource is 'mapping_table' but ledgerCategoryCol is not set — configure in Step 2")
    if (!cfg.actualsAccountCol)
      errors.push("categorySource is 'mapping_table' but actualsAccountCol is not set — configure in Step 2")
  }

  // clusterPatterns has no manual editor anywhere in the wizard — it's only
  // ever populated by the AI Setup assistant. A user who manually picks
  // "Account codes only" in Step 1 without also using AI Setup has no way
  // to satisfy this, so the message says so rather than pointing at a step
  // that has no relevant control.
  if (cfg.categorySource === "cluster_analysis" && (!cfg.clusterPatterns || cfg.clusterPatterns.length === 0))
    errors.push("categorySource is 'cluster_analysis' but no cluster patterns are defined — this option requires running AI Setup (there's no manual editor for cluster patterns); switch to a different category source if setting up manually")

  // Same as clusterPatterns above — category_remap isn't a manually
  // selectable radio option; it's only ever set by the AI Setup assistant.
  if (cfg.categorySource === "category_remap" && (!cfg.renameMap || Object.keys(cfg.renameMap).length === 0))
    errors.push("categorySource is 'category_remap' but no renameMap entries are defined — this option requires running AI Setup")

  if (cfg.fiscalPath === "A" && !cfg.fiscalYearColActuals)
    errors.push("fiscalPath is 'A' (pre-computed columns) but fiscalYearColActuals is not set — configure in Step 3")

  return errors
}

// ─── SQL assembler ───────────────────────────────────────────────────────────

function q(s: string): string {
  return "'" + s.replace(/'/g, "''") + "'"
}

/**
 * Wraps a column reference in a query-time prefix strip (regexp_replace),
 * or returns it unchanged when no prefixes are configured. Never touches the
 * underlying table — this only affects the generated SELECT. See
 * BvaConfig.keyStripPrefixes for where this is configured.
 */
function stripPrefixExpr(col: string, prefixes: string[] | undefined): string {
  if (!prefixes?.length) return col
  const alt = prefixes
    .map((p) => p.replace(/[\\^$.*+?()[\]{}|]/g, "\\$&").replace(/'/g, "''"))
    .join("|")
  return `regexp_replace(${col}, '^(${alt})', '')`
}

function fyExpr(col: string | undefined, alias: string, dateCol: string, start: number): string {
  if (col) return `${alias}.${col}`
  if (start === 1) return `'FY' || year(${alias}.${dateCol})`
  return `'FY' || iff(month(${alias}.${dateCol}) >= ${start}, year(${alias}.${dateCol}) + 1, year(${alias}.${dateCol}))`
}

function fqExpr(col: string | undefined, alias: string, dateCol: string, start: number): string {
  if (col) {
    // Normalize to Q1-Q4 format: handles 'Q1', 'FY2026-Q1', '2026-Q1', 'Q1 FY2026', etc.
    return `regexp_substr(${alias}.${col}, 'Q[1-4]')`
  }
  return `case
      when mod(month(${alias}.${dateCol}) - ${start} + 12, 12) < 3 then 'Q1'
      when mod(month(${alias}.${dateCol}) - ${start} + 12, 12) < 6 then 'Q2'
      when mod(month(${alias}.${dateCol}) - ${start} + 12, 12) < 9 then 'Q3'
      else 'Q4'
    end`
}

function buildCategoryExpr(col: string | undefined, clusters: ClusterPattern[]): string {
  const included = clusters.filter((p) => p.include !== false)
  if (!col) return "'Other Expenses'"
  if (!included.length) return `coalesce(${col}, 'Other Expenses')`

  // A "catchall" pattern always becomes the final ELSE, regardless of where
  // it appears in the configured list — SQL's CASE allows exactly one ELSE
  // and it must be the last clause. Handling it separately (rather than
  // inline in the .map() below) avoids ever emitting two ELSE clauses
  // (invalid SQL) and doesn't depend on the caller having placed it last.
  const catchall = included.find((p) => p.patternType === "catchall")
  const cases = included
    .filter((p) => p.patternType !== "catchall")
    .map((p) => {
      switch (p.patternType) {
        case "prefix": return `when left(${col}, ${String(p.patternValue).length}) = ${q(String(p.patternValue))} then ${q(p.label)}`
        case "ilike":  return `when ${col} ilike ${q(String(p.patternValue))} then ${q(p.label)}`
        case "like":   return `when ${col} like ${q(String(p.patternValue))} then ${q(p.label)}`
        case "in": {
          const vals = Array.isArray(p.patternValue) ? p.patternValue.map(q).join(", ") : q(String(p.patternValue))
          return `when ${col} in (${vals}) then ${q(p.label)}`
        }
        default: return ""
      }
    }).filter(Boolean)

  const elseClause = catchall ? q(catchall.label) : "'Other Expenses'"
  return `coalesce(${col}, case\n        ${cases.join("\n        ")}\n        else ${elseClause}\n      end)`
}

function buildCategoryRemapExpr(col: string | undefined, renameMap: Record<string, string>): string {
  if (!col) return "'Other Expenses'"
  const entries = Object.entries(renameMap)
  if (!entries.length) return `coalesce(${col}, 'Other Expenses')`
  const cases = entries.map(([from, to]) => `when ${col} = ${q(from)} then ${q(to)}`).join("\n        ")
  return `case\n        ${cases}\n        else coalesce(${col}, 'Other Expenses')\n      end`
}

/**
 * Applies renameMap (Step 5's CategoryManagementPanel) as a display-time
 * relabel on top of an ALREADY-COMPUTED category expression, rather than a
 * raw column. Used for the "column"/"cluster_analysis" categorySource
 * branches below, where the panel is shown in the wizard but there was
 * previously no code path that consumed renameMap at all — entries looked
 * "configured" but had zero effect on the generated SQL. (mapping_table has
 * its own rename path via accountRenameMap; category_remap's
 * buildCategoryRemapExpr above already consumes renameMap directly against
 * the raw column, so this wrapper is never applied there.)
 */
function applyRenameMap(expr: string, renameMap: Record<string, string>): string {
  const entries = Object.entries(renameMap)
  if (!entries.length) return expr
  const cases = entries.map(([from, to]) => `when ${expr} = ${q(from)} then ${q(to)}`).join("\n        ")
  return `case\n        ${cases}\n        else ${expr}\n      end`
}

/**
 * Build a BvA SQL query from a multi_table config.
 * Returns a SELECT that yields the standard 9-column BvA schema:
 *   fiscal_year, fiscal_quarter, cost_center_id, cost_center_name, department,
 *   expense_category, planning_version, actual_expense, budget_expense
 *
 * Config errors are injected as SQL comments so they appear in the Step 6 preview.
 * The validate API pre-flight (validateBuildConfig) will block SQL execution on errors.
 */
export function buildFromConfig(cfg: BvaConfig): string {
  if (cfg.sourceType === "single_table" || !cfg.actualsTable) {
    return `select * from ${cfg.singleTable ?? "/* no source configured — open Settings */"}`
  }

  // Inject any config errors as SQL comments so they're visible in the preview panel.
  // Blocking errors (missing required table/column) mean the multi-CTE builder below
  // would otherwise emit syntactically broken SQL (e.g. an empty `from` clause) — bail
  // out to a safe, valid placeholder instead of letting that reach materializeBva's
  // `CREATE TABLE AS SELECT`.
  const configErrors = validateBuildConfig(cfg)
  const errorComment = configErrors.length
    ? `-- CONFIG ERRORS (fix before running validation):\n${configErrors.map((e) => `--   ${e}`).join("\n")}\n\n`
    : ""
  if (configErrors.length > 0) {
    return `${errorComment}select * from ${cfg.actualsTable ?? "/* no source configured — open Settings */"} limit 0`
  }

  const {
    actualsTable, budgetTable = "",
    costCenterTable,
    ledgerMappingTable,
    ledgerAccountKeyCol,
    ledgerCategoryCol,
    actualsDateCol = "transaction_date",
    actualsAmountCol = "amount",
    actualsCostCenterCol,
    actualsCategoryCol,
    actualsAccountCol,
    accountFilterPrefixes,
    negateAmount,
    budgetPeriodCol = "period_date",
    budgetPeriodFormat = "date",
    budgetAmountCol = "budget_amount",
    budgetCostCenterCol,
    budgetCategoryCol = "expense_category",
    budgetVersionCol,
    ccIdCol = "cost_center_id",
    ccNameCol,
    ccDeptCol,
    ccUpdatedAtCol,
    fiscalPath,
    fyStartMonth: fyStartMonthRaw,
    fiscalYearColActuals,
    fiscalQuarterColActuals,
    fiscalYearColBudget,
    fiscalQuarterColBudget,
    rollupExclusions,
    clusterPatterns = [],
    categorySource,
    renameMap = {},
    accountRenameMap = {},
    accountCategoryOverrides = {},
    excludedCostCenters,
    keyStripPrefixes = {},
  } = cfg

  // Bound helper: strip() applies the configured prefixes for a given
  // join-key role (see BvaConfig.keyStripPrefixes) to a column reference.
  const strip = (role: keyof NonNullable<BvaConfig["keyStripPrefixes"]>, colExpr: string) =>
    stripPrefixExpr(colExpr, keyStripPrefixes[role])

  const usePathA = fiscalPath === "A"
  // Nullish-coalesce (not a destructuring default) so an explicit `null` — a
  // real runtime state for this field (see step-fiscal.tsx, setup/profile
  // route) — doesn't slip past the default and reach the SQL below as NULL.
  const fyStartMonth = fyStartMonthRaw ?? 1

  // Fiscal expressions
  const aFy = fyExpr(usePathA ? fiscalYearColActuals   : undefined, "a", actualsDateCol,  fyStartMonth)
  const aFq = fqExpr(usePathA ? fiscalQuarterColActuals: undefined, "a", actualsDateCol,  fyStartMonth)

  let bFy: string, bFq: string
  if (budgetPeriodFormat === "fiscal_quarter_string") {
    bFy = `split_part(b.${budgetPeriodCol}, ' ', 1)`
    bFq = `split_part(b.${budgetPeriodCol}, ' ', 2)`
  } else {
    bFy = fyExpr(usePathA ? fiscalYearColBudget    : undefined, "b", budgetPeriodCol, fyStartMonth)
    bFq = fqExpr(usePathA ? fiscalQuarterColBudget : undefined, "b", budgetPeriodCol, fyStartMonth)
  }

  const includeQuarterJoin = budgetPeriodFormat !== "annual"

  // Account filter — LIKE-based so it works for prefixes of any length, not
  // just single characters. Must stay in sync with the identical filter in
  // app/api/breakdown/entries/route.ts (drill-through uses the same field).
  const acctClause = accountFilterPrefixes?.length && actualsAccountCol
    ? `\n      and (${accountFilterPrefixes.map((p) => `a.${actualsAccountCol} like ${q(p)} || '%'`).join(" or ")})`
    : ""

  // Excluded cost centers
  const ccExcludeClause = excludedCostCenters?.length && actualsCostCenterCol
    ? `\n      and a.${actualsCostCenterCol} not in (${excludedCostCenters.map(q).join(", ")})`
    : ""

  // Rollup exclusion
  const rollupClause = rollupExclusions?.length && (usePathA ? fiscalQuarterColActuals : true)
    ? `\n      and ${usePathA ? `a.${fiscalQuarterColActuals}` : aFq} not in (${rollupExclusions.map(q).join(", ")})`
    : ""

  const amtExpr = negateAmount ? `-a.${actualsAmountCol}` : `a.${actualsAmountCol}`

  // ── Category expression + optional ledger_map CTE ─────────────────
  // mapping_table: join ledger mapping table to translate account codes → categories
  const useLedgerMap = categorySource === "mapping_table"
    && !!ledgerMappingTable && !!ledgerAccountKeyCol && !!ledgerCategoryCol && !!actualsAccountCol

  // Dedup by account key (max() picks one category when duplicates exist).
  // When accountCategoryOverrides has entries, wrap with a COALESCE so specific
  // account codes resolve to the override category instead of the mapping table value.
  const overrideEntries = Object.entries(accountCategoryOverrides)
  const hasOverrides = overrideEntries.length > 0

  const ledgerMapCTE = useLedgerMap ? `ledger_map as (
    select
        acct_key
        , ${hasOverrides ? `coalesce(
            case acct_key
              ${overrideEntries.map(([k, v]) => `when ${q(k)} then ${q(v)}`).join("\n              ")}
              else null
            end,
            expense_category
          )` : "expense_category"} as expense_category
    from (
        select
            ${strip("ledgerAccountKeyCol", `lm.${ledgerAccountKeyCol}`)}          as acct_key
            , max(lm.${ledgerCategoryCol})     as expense_category
        from ${ledgerMappingTable} as lm
        where lm.${ledgerAccountKeyCol} is not null
          and lm.${ledgerCategoryCol}  is not null
        group by 1
    )
),

` : ""

  let catExpr: string
  let actualsLedgerJoin = ""

  if (useLedgerMap) {
    // category comes from mapping table, not a direct column on actuals
    catExpr = "coalesce(lm.expense_category, 'Other Expenses')"
    actualsLedgerJoin = `\n    left join ledger_map as lm\n        on ${strip("actualsAccountCol", `a.${actualsAccountCol}`)} = lm.acct_key`
  } else if (categorySource === "category_remap") {
    catExpr = buildCategoryRemapExpr(actualsCategoryCol ? `a.${actualsCategoryCol}` : undefined, renameMap)
  } else {
    // "column", "cluster_analysis", or undefined
    catExpr = buildCategoryExpr(actualsCategoryCol ? `a.${actualsCategoryCol}` : undefined, clusterPatterns)
    if (Object.keys(renameMap).length > 0) {
      catExpr = applyRenameMap(catExpr, renameMap)
    }
  }

  const versionExpr = budgetVersionCol ? `b.${budgetVersionCol}` : `'Budget'`

  // ── cost_centers CTE ──────────────────────────────────────────────
  // Always deduplicate by ccIdCol so duplicate CC rows can't fan out the bva join.
  // When ccUpdatedAtCol is set, pick the latest row; otherwise order is arbitrary but stable.
  const ccIdExpr = strip("ccIdCol", `cc.${ccIdCol}`)
  const ccDedup = ccUpdatedAtCol
    ? `qualify row_number() over (partition by ${ccIdExpr} order by cc.${ccUpdatedAtCol} desc) = 1`
    : `qualify row_number() over (partition by ${ccIdExpr} order by ${ccIdExpr})    = 1`
  const ccCTE = costCenterTable && ccIdCol ? `cost_centers as (
    select
        ${ccIdExpr} as cost_center_id
        , ${ccNameCol ? `cc.${ccNameCol}` : "null"} as cost_center_name
        ${ccDeptCol ? `, cc.${ccDeptCol} as department` : ""}
    from ${costCenterTable} as cc
    ${ccDedup}
),

` : ""

  // Cost center ID on actuals — optional. Falls back to NULL (not an empty
  // string) so the null-safe EQUAL_NULL() join below still matches rows on
  // both sides when neither table tracks cost centers at all.
  const actualsCcExpr = actualsCostCenterCol
    ? strip("actualsCostCenterCol", `a.${actualsCostCenterCol}`)
    : "cast(null as varchar)"

  // ── actuals CTE ───────────────────────────────────────────────────
  const actualsCTE = `actuals as (
    select
        ${aFy} as fiscal_year
        , ${aFq} as fiscal_quarter
        , ${actualsCcExpr} as cost_center_id
        , ${catExpr} as expense_category
        , sum(${amtExpr}) as expense
    from ${actualsTable} as a${actualsLedgerJoin}
    where 1=1${acctClause}${ccExcludeClause}${rollupClause}
    group by 1, 2, 3, 4
)`

  // ── budget CTE ────────────────────────────────────────────────────
  // Cost center ID on budget — optional, same NULL fallback as actuals above.
  const budgetCcExpr = budgetCostCenterCol
    ? strip("budgetCostCenterCol", `b.${budgetCostCenterCol}`)
    : "cast(null as varchar)"
  const budgetSelectCols = budgetPeriodFormat === "annual"
    ? `${bFy} as fiscal_year
        , ${versionExpr} as planning_version
        , ${budgetCcExpr} as cost_center_id
        , b.${budgetCategoryCol} as expense_category
        , sum(b.${budgetAmountCol}) as expense`
    : `${bFy} as fiscal_year
        , ${bFq} as fiscal_quarter
        , ${versionExpr} as planning_version
        , ${budgetCcExpr} as cost_center_id
        , b.${budgetCategoryCol} as expense_category
        , sum(b.${budgetAmountCol}) as expense`

  const budgetCTE = `budget as (
    select
        ${budgetSelectCols}
    from ${budgetTable} as b
    where b.${budgetCategoryCol} is not null
    group by all
)`

  // ── bva CTE ───────────────────────────────────────────────────────
  // Actuals carry no planning_version of their own — the same real spend
  // must be compared against EVERY version's budget, not just whichever
  // version happens to have a budget row for that exact
  // (fiscal_year, fiscal_quarter, cost_center, category) combo. Without this
  // broadcast, a version with incomplete cost-center/category budget coverage
  // silently drops real actuals from its rollup (they'd only surface under
  // whichever OTHER version's budget happened to cover that combo, or under
  // a version-less orphan row if no version covered it at all) — making that
  // version look artificially favorable. Cross-joining actuals against every
  // distinct version present in budget fixes this: each actual row is
  // guaranteed to appear under every version, with budget_expense = 0 for any
  // version that simply didn't budget that combo.
  const qJoin    = includeQuarterJoin ? "\n        and a.fiscal_quarter = b.fiscal_quarter" : ""
  const versionsCTE = `versions as (
    select distinct planning_version
    from budget
)`
  const actualsVCTE = `actuals_v as (
    select
        a.fiscal_year
        , a.fiscal_quarter
        , a.cost_center_id
        , a.expense_category
        , v.planning_version
        , a.expense
    from actuals as a
    cross join versions as v
)`
  // Only build the cost-center dimension join when at least one fact table
  // actually has a cost-center column mapped — otherwise coalesce(a.cost_center_id,
  // b.cost_center_id) is always NULL and this join can never match any row,
  // silently producing an always-empty cost_center_name/department with no error.
  const ccJoin   = costCenterTable && ccIdCol && (actualsCostCenterCol || budgetCostCenterCol)
    ? `\n    left join cost_centers as cc\n        on coalesce(a.cost_center_id, b.cost_center_id) = cc.cost_center_id`
    : ""
  const ccNameSel = costCenterTable && ccNameCol && ccJoin ? "cc.cost_center_name" : "null as cost_center_name"
  const deptSel   = costCenterTable && ccDeptCol && ccJoin ? "cc.department"       : "null as department"

  const bvaCTE = `bva as (
    select
        coalesce(a.fiscal_year, b.fiscal_year)           as fiscal_year
        -- When budgetPeriodFormat is "annual", the budget CTE above never
        -- projects a fiscal_quarter column (there's no quarter to derive
        -- from an annual figure) — referencing b.fiscal_quarter here would
        -- be an invalid identifier. Fall back to the actuals side only,
        -- consistent with includeQuarterJoin already dropping the quarter
        -- join predicate for this same case.
        , ${includeQuarterJoin ? "coalesce(a.fiscal_quarter, b.fiscal_quarter)" : "a.fiscal_quarter"} as fiscal_quarter
        , coalesce(a.cost_center_id, b.cost_center_id)   as cost_center_id
        , ${ccNameSel}
        , ${deptSel}
        , coalesce(a.expense_category, b.expense_category) as expense_category
        , coalesce(a.planning_version, b.planning_version) as planning_version
        , coalesce(a.expense, 0)                         as actual_expense
        , coalesce(b.expense, 0)                         as budget_expense
    from actuals_v as a
    full outer join budget as b
        on a.fiscal_year = b.fiscal_year${qJoin}
        and equal_null(a.cost_center_id, b.cost_center_id)
        and coalesce(a.expense_category, '') = coalesce(b.expense_category, '')
        and a.planning_version = b.planning_version${ccJoin}
)`

  // ── Account rename CTE (optional display-time rebucket) ──────────────────
  // Applied AFTER the join so raw values are used for actuals↔budget matching.
  // When accountRenameMap has entries, we wrap bva in a bva_display CTE that
  // renames / merges ledger categories and re-aggregates.
  const accountRemapEntries = Object.entries(accountRenameMap)
  let finalFrom = "bva"
  let accountRemapCTE = ""
  if (accountRemapEntries.length > 0) {
    const cases = accountRemapEntries
      .map(([from, to]) => `when expense_category = ${q(from)} then ${q(to)}`)
      .join("\n        ")
    accountRemapCTE = `,\n\nbva_display as (\n    select\n        fiscal_year\n        , fiscal_quarter\n        , cost_center_id\n        , cost_center_name\n        , department\n        , case\n        ${cases}\n        else expense_category\n        end as expense_category\n        , planning_version\n        , sum(actual_expense)  as actual_expense\n        , sum(budget_expense)  as budget_expense\n    from bva\n    group by 1, 2, 3, 4, 5, 6, 7\n)`
    finalFrom = "bva_display"
  }

  return `${errorComment}with\n${ledgerMapCTE}${ccCTE}${actualsCTE},\n\n${budgetCTE},\n\n${versionsCTE},\n\n${actualsVCTE},\n\n${bvaCTE}${accountRemapCTE}\n\nselect\n    fiscal_year\n    , fiscal_quarter\n    , cost_center_id\n    , cost_center_name\n    , department\n    , expense_category\n    , planning_version\n    , actual_expense\n    , budget_expense\nfrom ${finalFrom}`
}

// The fixed 9-column schema buildFromConfig() always produces. Exported so
// lib/custom-logic-guard.ts can allow-list these as safe identifiers for
// custom logic expressions, without duplicating the list.
export const BVA_BASELINE_COLUMNS = [
  "fiscal_year",
  "fiscal_quarter",
  "cost_center_id",
  "cost_center_name",
  "department",
  "expense_category",
  "planning_version",
  "actual_expense",
  "budget_expense",
] as const

/**
 * Layers enabled CustomLogicRule entries on top of buildFromConfig()'s
 * baseline output, without ever modifying that baseline. When there are no
 * enabled rules, returns buildFromConfig(cfg) verbatim — deleting or
 * disabling every rule reverts the query to exact baseline behavior, with no
 * residual artifact. This is the ONLY function that should be used to
 * materialize/query the dashboard's real data; buildFromConfig() remains the
 * baseline-only preview and the thing this function wraps.
 *
 * Shape when rules are enabled:
 *   select base_bva.*, <derived_column.sql> as <alias>, ...
 *   from (<baseline>) as base_bva
 *   where 1=1 and (<filter.sql>) and ...
 */
export function buildFinalSql(cfg: BvaConfig): string {
  const baseline = buildFromConfig(cfg)
  const rules = (cfg.customLogicRules ?? []).filter((r) => r.enabled)
  if (rules.length === 0) return baseline

  const derivedCols = rules.filter((r) => r.kind === "derived_column" && r.alias)
  const filters = rules.filter((r) => r.kind === "filter")

  const selectExtra = derivedCols.map((r) => `    , ${r.sql} as ${r.alias}`).join("\n")
  const whereExtra = filters.map((r) => `\n  and (${r.sql})`).join("")

  const withDerived = `select\n    base_bva.*${selectExtra ? "\n" + selectExtra : ""}\nfrom (\n${baseline}\n) as base_bva`

  if (!filters.length) return withDerived

  // Filters run in an OUTER query, one level above wherever any derived
  // columns are projected. SQL evaluates WHERE before the SELECT list in
  // the same query block (FROM -> WHERE -> SELECT logical order), so a
  // filter referencing a derived-column alias defined in that SAME select
  // could never actually see it — the guard (lib/custom-logic-guard.ts)
  // explicitly allows a filter to reference an earlier derived_column
  // rule's alias, so this nesting is required for that to actually work,
  // not just pass validation. By the time this outer WHERE runs,
  // `with_derived` is a fully materialized result set where every derived
  // column is a real, filterable column.
  // Note: this does NOT let one derived_column rule reference another
  // derived_column rule's alias — those remain sibling SELECT-list items
  // at the same query level, which SQL never allows to see each other.
  if (!derivedCols.length) {
    return `select\n    base_bva.*\nfrom (\n${baseline}\n) as base_bva\nwhere 1=1${whereExtra}`
  }
  return `select\n    with_derived.*\nfrom (\n${withDerived}\n) as with_derived\nwhere 1=1${whereExtra}`
}
