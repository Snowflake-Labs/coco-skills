/**
 * Server-only: deterministic, parallel discovery of BvA candidate tables.
 *
 * The setup agent (app/api/agent/setup/route.ts) is a single LLM loop that can
 * only issue SQL serially — so account discovery via the LLM took many minutes.
 * This module does ONLY the mechanical part — the account scan — deterministically
 * and IN PARALLEL (Promise.all over databases, backed by the snowflake-sdk pool),
 * then hands the agent a COMPLETE, tier-annotated candidate inventory.
 *
 * It deliberately does NOT rank by row count or pick a "recommended" table — that
 * is the accuracy-critical judgment the LLM does well (tier table, RAW→curated
 * dedup, YTD-vs-full-history, opex-vs-revenue, CC rows_per_cc). We only provide
 * the same evidence the old flow gathered (schema, tier, row count, type), just
 * gathered in seconds instead of minutes. The LLM ranks; the user confirms.
 */
import { querySnowflake } from "@/lib/snowflake"
import { WAREHOUSE } from "@/lib/bva"

const SYSTEM_DBS = new Set([
  "SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA", "SNOWFLAKE_MARKETPLACE",
  "ORGADMIN", "SNOWFLAKE_LEARNING", "SNOWFLAKE_OPERATIONS",
])

// Database-name keywords worth scanning (kept in sync with the prompt list).
const DB_KEYWORDS = [
  "FINANCE", "FIN", "CORP", "ACCOUNTING", "ACCT", "FPA", "ERP", "GL",
  "GENERAL_LEDGER", "LEDGER", "JOURNAL", "GAAP", "BUDGET", "FORECAST",
  "PLANNING", "ACTUALS", "ADAPTIVE", "ANAPLAN", "HYPERION", "NETSUITE",
  "SAP", "WORKDAY", "COST_CENTER", "INCOME_STATEMENT", "ANALYTICS",
  "DWH", "EDW", "WAREHOUSE",
]

// Parallelism is cheap (pool max 10), so scan generously rather than cap hard.
const MAX_DBS = 20
// Per-DB scan cap — a generous safety valve only. The scan must be COMPLETE
// (some finance DBs have ~10k matching tables) so the tier+relevance ranking
// below can see every candidate; an arbitrary low LIMIT with no ORDER BY would
// silently drop canonical tables. Reduction happens in ranking, not here.
const PER_DB_LIMIT = 20000
// Ranked candidates per bucket handed to the LLM (tier + relevance ranked).
// Small on purpose — keeps prompt token usage bounded (~4 buckets × 15).
const MAX_PER_BUCKET = 15

type RawTable = {
  database: string
  schema: string
  table: string
  type: string
  rowCount: number | null
}

export type InventoryItem = {
  table: string          // fully-qualified DB.SCHEMA.TABLE
  schema: string
  tier: number           // 1 (best) … 4 (worst) — schema priority tier
  rows: string           // humanized row count ("56.5M", "view")
  rowCount: number | null
  type: string           // "BASE TABLE" | "VIEW"
}

export type DiscoveryScope = { database: string; schema?: string }

export type DiscoveryBundle = {
  buckets: {
    gl: InventoryItem[]
    budget: InventoryItem[]
    cc: InventoryItem[]
    ledger: InventoryItem[]
  }
  databasesScanned: string[]
  tableCount: number
  elapsedMs: number
  pass: 1 | 2
  scope: DiscoveryScope | null
}

function q(s: string): string { return `"${s.replace(/"/g, '""')}"` }

// Matches a DB.SCHEMA or DB.SCHEMA.TABLE identifier the user typed explicitly
// (e.g. "check YOUR_DB.YOUR_SCHEMA"). Segments require 3+ chars so casual
// prose like "e.g." or "i.e." never matches (those segments are 1 char).
const SCOPE_RE = /\b([A-Za-z][A-Za-z0-9_$]{2,})\.([A-Za-z][A-Za-z0-9_$]{2,})(?:\.[A-Za-z][A-Za-z0-9_$]{2,})?\b/

/**
 * Detect an explicit database/schema scope in the user's message (e.g. "please
 * check YOUR_DB.YOUR_SCHEMA"). When present, discovery must be restricted to
 * exactly that scope instead of scanning the whole account — see Phase 1 in the
 * system prompt for the corresponding LLM-side instruction (covers turns where
 * this precompute doesn't run).
 */
export function parseDiscoveryScope(message: string): DiscoveryScope | null {
  const m = SCOPE_RE.exec(message)
  if (!m) return null
  return { database: m[1], schema: m[2] }
}

function dbIsDev(db: string): boolean {
  return /_DEV$|^DEV_|_DEV_|_TEST$|^TEST_|SANDBOX|SCRATCH|_QA$|_STAGE$|_STG$/.test(db.toUpperCase())
}

/**
 * Schema priority tier (1 best … 4 worst) — mirrors the prompt's tier table.
 * A dev/test/personal *database* floors the tier at 3 so prod tables outrank
 * dev copies of the same table regardless of the schema name.
 */
function tierOf(database: string, schema: string): number {
  const s = schema.toUpperCase()
  let tier: number
  if (/DEV|TEST|SANDBOX|TEMP|BACKUP|CLONE|SCRATCH|QA|DEMO/.test(s)) tier = 4
  else if (/^ACCOUNTING$|^FINANCE$|^FPA$|GENERAL_LEDGER|^BUDGET$|^PLANNING$|^ERP$|^GL$|_GL$|^GL_/.test(s)) tier = 1
  else if (/^STG|^PREP|^MART|^DWH|^GOLD|^SILVER|ANALYTICS|CURATED|ENRICHED|REPORTING|WAREHOUSE/.test(s)) tier = 2
  else if (/^RAW|^BRONZE|^INGEST|^LANDING|EXTERNAL|RAWDATA|^SAP_|^NETSUITE_|^ORACLE_|^WORKDAY_|ADAPTIVE/.test(s)) tier = 3
  else if (/_FPA$|_FIN$|_FINANCE$/.test(s)) tier = 3 // personal/user-prefixed finance schemas (e.g. JDOE_FPA)
  else tier = 2
  if (dbIsDev(database)) tier = Math.max(tier, 3)
  return tier
}

// Converts a raw row count into a short human-readable label (e.g. "56.5M")
// for display in the candidate inventory. Views don't report a row count, so
// they're always labeled "view" rather than "?", which is reserved for a
// base table whose row count genuinely couldn't be determined.
function humanizeRows(n: number | null, type: string): string {
  if (n == null) return type === "VIEW" ? "view" : "?"
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return String(n)
}

// ── Bucket classification (name-based; a table may match multiple buckets) ──
// Over-inclusion is safe — the LLM ranks and the user confirms; a miss is worse.

type Bucket = "gl" | "budget" | "cc" | "ledger"

// General ledger / actuals: journal entries, GAAP ledgers, or anything with
// "actual" in the name — but "LEDGER" alone is excluded when it's really a
// *mapping* table (that belongs in the "ledger" bucket below instead).
function matchesGl(name: string): boolean {
  return /(^|_)(GL|JOURNAL|GAAP|ACTUAL|ACTUALS)(_|$)/.test(name) ||
    (/LEDGER/.test(name) && !/MAP|MAPPING/.test(name))
}
// Budget/forecast/plan tables — the other side of the budget-vs-actuals comparison.
function matchesBudget(name: string): boolean {
  return /(BUDGET|FORECAST|PLAN|INCOME_STATEMENT|VARIANCE|BVA)/.test(name) && !/MAP|MAPPING/.test(name)
}
// Cost center / department dimension tables, used to attach a human-readable
// name and department to the numeric cost center codes on the fact tables.
function matchesCc(name: string): boolean {
  return /(COST_?CENTER|COSTCENTER|DEPARTMENT|COST_CTR)/.test(name)
}
// Ledger account mapping tables — translate raw GL account codes into
// business-friendly expense categories (distinct from the "gl" bucket above,
// which is the raw transaction data itself).
function matchesLedger(name: string): boolean {
  return /(LEDGER_?ACCOUNT|ACCOUNT_?MAP|CHART_OF_ACCOUNT)/.test(name) ||
    (/LEDGER/.test(name) && /MAP|MAPPING/.test(name))
}

/**
 * Name-relevance for a bucket (higher = the NAME more clearly denotes the concept)
 * — the accuracy signal, mirroring the old evidence-based ranking. Row count is
 * NEVER used to rank: mapping tables are tiny views; GL tables are huge — size
 * says nothing about correctness.
 */
function relevanceFor(name: string, bucket: Bucket): number {
  const n = name.toUpperCase()
  const junk = /SNAPSHOT|_BKP|BACKUP|UNNAMED|_OLD|_COPY|DEPRECAT|_TMP|TEMP|_TEST|SCRATCH|DEMO/.test(n)
  let r = 0
  switch (bucket) {
    case "gl":
      if (/GAAP_JOURNAL|JOURNAL_LINE|JOURNAL_ENTRY|GENERAL_LEDGER/.test(n)) r = 3
      else if (/JOURNAL|GAAP|(^|_)GL(_|$)|ACTUAL/.test(n)) r = 2
      else if (/LEDGER/.test(n)) r = 1
      if (/_YTD|_QTD|_MTD/.test(n)) r -= 1 // period-to-date variants — prefer full history
      break
    case "budget":
      if (/INCOME_STATEMENT|(^|_)BUDGET(_|$)|OPEX|EXPENSE_FORECAST|EXPENSE_PLAN/.test(n)) r = 3
      else if (/(^|_)BVA(_|$)|VARIANCE|(^|_)PLAN(_|$)/.test(n)) r = 2
      else if (/FORECAST|PLAN/.test(n)) r = 1
      if (/REVENUE|RPO|BOOKINGS|PIPELINE|PRODUCT|GEO|SEGMENT/.test(n) && !/INCOME_STATEMENT|LESS_REVENUE/.test(n)) r -= 1
      break
    case "cc":
      if (/COST_?CENTER.*(MAP|MAPPING)|(MAP|MAPPING).*COST_?CENTER|DIM_COST_CENTER|COST_CENTER_MASTER/.test(n)) r = 3
      else if (/DEPARTMENT.*(MAP|MAPPING|MASTER|DIM)|(DIM|MAPPING).*DEPARTMENT/.test(n)) r = 2
      else if (/COST_?CENTER|COSTCENTER|DEPARTMENT/.test(n)) r = 1
      if (/(^|_)BVA(_|$)/.test(n)) r += 1
      break
    case "ledger":
      if (/LEDGER_?ACCOUNT.*(MAP|MAPPING)|(MAP|MAPPING).*LEDGER_?ACCOUNT|CHART_OF_ACCOUNT/.test(n)) r = 3
      else if (/ACCOUNT_?MAP|ACCOUNT_MAPPING/.test(n)) r = 2
      else if (/LEDGER/.test(n)) r = 1
      if (/(^|_)BVA(_|$)/.test(n)) r += 1
      break
  }
  if (junk) r -= 2
  return r
}

/**
 * Build a bucket: rank matches by (schema tier, name relevance) — NOT row count —
 * then keep the top MAX_PER_BUCKET. Same evidence-based ranking the old LLM flow
 * used, applied deterministically over the parallel scan results.
 */
function toBucket(rows: RawTable[], bucket: Bucket, matches: (n: string) => boolean): InventoryItem[] {
  return rows
    .filter((r) => matches(r.table.toUpperCase()))
    .map((r) => ({
      table: `${r.database}.${r.schema}.${r.table}`,
      schema: r.schema,
      tier: tierOf(r.database, r.schema),
      relevance: relevanceFor(r.table.toUpperCase(), bucket),
      rows: humanizeRows(r.rowCount, r.type),
      rowCount: r.rowCount,
      type: r.type,
    }))
    .sort((a, b) => a.tier - b.tier || b.relevance - a.relevance || a.table.localeCompare(b.table))
    .slice(0, MAX_PER_BUCKET)
    .map(({ relevance: _r, ...item }) => item)
}

/**
 * Run the parallel discovery scan and return tier-annotated candidate buckets.
 * Pass 1 = keyword-filtered. Pass 2 (unfiltered) runs only if Pass 1 yields
 * neither a GL nor a budget candidate.
 *
 * If `scope` is given (the user named a specific database/schema — see
 * parseDiscoveryScope), discovery is restricted to exactly that scope: no
 * SHOW DATABASES-driven keyword filtering of which databases to scan, and no
 * FINANCE_KEYWORDS filter on schema/table names — the user already told us
 * where to look, so a single unfiltered pass over that scope is both correct
 * and simpler than the two-pass heuristic below.
 */
export async function buildDiscoveryBundle(scope?: DiscoveryScope | null): Promise<DiscoveryBundle> {
  const t0 = Date.now()

  // First list every database in the account, so we know what's available to
  // scan before deciding which ones are actually worth scanning (below).
  // Filter out Snowflake's own system/sample databases up front — they can
  // never contain a customer's finance tables, so there's no point scanning them.
  const dbRows = await querySnowflake("show databases", { warehouse: WAREHOUSE, tag: "bva:discover-bundle" })
  const allDbs = dbRows.map((r) => String(r.name)).filter((n) => !SYSTEM_DBS.has(n.toUpperCase()))

  let dbs: string[]
  if (scope) {
    // Resolve to the actual stored casing if we can find it (SHOW DATABASES
    // returns exact casing; the user may have typed different case) — falls
    // back to as-typed so the scan still attempts it (and fails visibly via
    // the .catch(() => []) below) rather than silently dropping the request.
    const matched = allDbs.find((n) => n.toUpperCase() === scope.database.toUpperCase())
    dbs = [matched ?? scope.database]
  } else {
    dbs = allDbs.filter((n) => DB_KEYWORDS.some((k) => n.toUpperCase().includes(k))).slice(0, MAX_DBS)
    if (dbs.length === 0) dbs = allDbs.slice(0, MAX_DBS)
  }

  // Scans every candidate database's INFORMATION_SCHEMA.TABLES in parallel
  // (one query per database, fired concurrently) and returns every table/view
  // found. `filtered` toggles a keyword WHERE clause: pass 1 (filtered=true)
  // narrows to schema/table names that look finance-related, to keep the scan
  // fast; pass 2 (filtered=false) drops that filter and scans everything, used
  // as a fallback below when pass 1 comes up empty (a real finance table with
  // an unrecognized naming convention shouldn't be invisible to discovery).
  const scan = async (filtered: boolean): Promise<RawTable[]> => {
    const schemaClause = scope?.schema
      ? `AND TABLE_SCHEMA = '${scope.schema.replace(/'/g, "''").toUpperCase()}'`
      : ""
    const keywordClause = filtered
      ? `AND (
            TABLE_SCHEMA ILIKE ANY ('%FINANCE%','%FIN%','%CORP%','%ACCOUNT%','%FPA%','%ERP%','%GL%',
                                    '%LEDGER%','%JOURNAL%','%BUDGET%','%FORECAST%','%PLAN%','%ACTUAL%',
                                    '%ADAPTIVE%','%ANAPLAN%','%HYPERION%','%NETSUITE%','%SAP%','%WORKDAY%',
                                    '%ANALYTIC%','%DWH%','%EDW%','%WAREHOUSE%','%STG%','%PREP%')
         OR TABLE_NAME   ILIKE ANY ('%FINANCE%','%LEDGER%','%JOURNAL%','%GAAP%','%GL_%','%GENERAL_LEDGER%',
                                    '%BUDGET%','%FORECAST%','%PLAN%','%ACTUAL%','%INCOME_STATEMENT%',
                                    '%COST_CENTER%','%COSTCENTER%','%DEPARTMENT%','%ACCOUNT_MAP%',
                                    '%CHART_OF_ACCOUNT%','%EXPENSE%')
         )`
      : ""
    // Fires one INFORMATION_SCHEMA.TABLES query per database, all in parallel
    // (this is the "seconds instead of minutes" speedup described at the top
    // of this file). Restricted to tables with rows and all views, since an
    // empty base table can't be a real candidate; a per-database failure
    // (e.g. no access) is swallowed via .catch so one bad database can't sink
    // the whole scan.
    const perDb = dbs.map((db) =>
      querySnowflake(
        `SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, ROW_COUNT
         FROM ${q(db)}.INFORMATION_SCHEMA.TABLES
         WHERE TABLE_SCHEMA <> 'INFORMATION_SCHEMA'
           AND ((TABLE_TYPE = 'BASE TABLE' AND ROW_COUNT > 0) OR TABLE_TYPE = 'VIEW')
           ${schemaClause}
           ${keywordClause}
         LIMIT ${PER_DB_LIMIT}`,
        { warehouse: WAREHOUSE, tag: "bva:discover-bundle" },
      ).catch(() => [] as Record<string, unknown>[]),
    )
    const results = await Promise.all(perDb)
    return results.flat().map((r) => ({
      database: String(r.TABLE_CATALOG),
      schema: String(r.TABLE_SCHEMA),
      table: String(r.TABLE_NAME),
      type: String(r.TABLE_TYPE),
      rowCount: r.ROW_COUNT != null ? Number(r.ROW_COUNT) : null,
    }))
  }

  let pass: 1 | 2 = 1
  let tables: RawTable[]
  if (scope) {
    // User-scoped: one unfiltered pass over exactly the named database/schema.
    // No Pass 2 widening — widening beyond what the user asked for is exactly
    // the bug this scope exists to prevent.
    tables = await scan(false)
  } else {
    tables = await scan(true)
    const hasGl = () => tables.some((t) => matchesGl(t.table.toUpperCase()))
    const hasBudget = () => tables.some((t) => matchesBudget(t.table.toUpperCase()))
    if (!hasGl() || !hasBudget()) {
      pass = 2
      tables = await scan(false)
    }
  }

  return {
    buckets: {
      gl: toBucket(tables, "gl", matchesGl),
      budget: toBucket(tables, "budget", matchesBudget),
      cc: toBucket(tables, "cc", matchesCc),
      ledger: toBucket(tables, "ledger", matchesLedger),
    },
    databasesScanned: dbs,
    tableCount: tables.length,
    elapsedMs: Date.now() - t0,
    pass,
    scope: scope ?? null,
  }
}

/**
 * Render the bundle as a compact markdown inventory to inject into the agent's
 * system prompt. Grouped by bucket, tier-annotated, so the LLM can apply the
 * Phase-1 tier/dedup rules without re-scanning the account.
 */
export function formatBundleMarkdown(bundle: DiscoveryBundle): string {
  const section = (label: string, items: InventoryItem[]): string => {
    if (!items.length) return `**${label}:** (none found)`
    const lines = items.map((i) => `- ${i.table} — tier ${i.tier}, ${i.rows}, ${i.type}`)
    return `**${label} candidates (${items.length}):**\n${lines.join("\n")}`
  }
  const scopeLabel = bundle.scope
    ? `${bundle.scope.database}${bundle.scope.schema ? "." + bundle.scope.schema : ""}`
    : null
  const header = scopeLabel
    ? `Scoped scan complete (user-requested — do NOT suggest tables from other databases/schemas): ${bundle.tableCount} table(s) found in ${scopeLabel} in ${(bundle.elapsedMs / 1000).toFixed(1)}s.`
    : `Parallel account scan complete: ${bundle.tableCount} finance-relevant tables across ${bundle.databasesScanned.length} database(s) [${bundle.databasesScanned.join(", ")}] in ${(bundle.elapsedMs / 1000).toFixed(1)}s (pass ${bundle.pass}).`
  return [
    header,
    ``,
    section("GL / Actuals", bundle.buckets.gl),
    ``,
    section("Budget / Forecast", bundle.buckets.budget),
    ``,
    section("Cost Center mapping", bundle.buckets.cc),
    ``,
    section("Ledger Account mapping", bundle.buckets.ledger),
  ].join("\n")
}
