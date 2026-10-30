import { checkIsAdmin } from "@/lib/agent-access-mode"
import {
  showDatabases,
  listSchemasInDatabase,
  scanTablesForBrowser,
  listSemanticViewsInAccount,
  listTableColumns,
  computeColumnCardinalities,
  countTableRows,
  sampleColumnValues,
  columnPrefixCounts,
  checkCostCenterOverlap,
  previewTableRows,
  checkCategoryOverlap,
  listCategoryValues,
  listAllCostCenterValues,
  listInactiveCostCenterValues,
  checkTableExists,
  listAccountCategories,
} from "../../../../lib/queries/discover"

export const dynamic = "force-dynamic"

/**
 * GET /api/setup/discover
 *
 * Params:
 *   action=databases                          → list all accessible databases
 *   action=schemas&db=DB                      → list schemas in a database
 *   action=tables&db=DB&schema=S&keyword=K    → list tables (with scoring)
 *   action=columns&table=DB.SCHEMA.TABLE      → list columns with types + cardinality
 *   action=sample&table=DB.SCHEMA.TABLE&col=C   → top-10 distinct values for a column
 *   action=prefixes&table=DB.SCHEMA.TABLE&col=C    → first-char prefix counts (for account filter)
 *   action=join-check&actualsTable=T1&budgetTable=T2&actualsCC=C1&budgetCC=C2 → CC overlap %
 *   action=preview&table=DB.SCHEMA.TABLE             → 5 actual rows (for inline preview)
 *   action=validate&table=DB.SCHEMA.TABLE            → validate FQN resolves
 *   action=semantic-views&keyword=K                  → list semantic views account-wide (for multi-select browse)
 */
export async function GET(req: Request) {
  const admin = await checkIsAdmin()
  if (!admin.isAdmin) {
    return Response.json({ error: "Admin only" }, { status: 403 })
  }
  const { searchParams } = new URL(req.url)
  const action = searchParams.get("action") ?? "databases"

  try {
    switch (action) {
      case "databases": {
        const rows = await showDatabases()
        const dbs = rows
          .map((r) => String(r.name))
          .filter((name) => !SYSTEM_DBS.has(name.toUpperCase()))
          .sort()
        return Response.json({ databases: dbs })
      }

      case "schemas": {
        const db = searchParams.get("db")
        if (!db) return Response.json({ error: "db required" }, { status: 400 })
        const rows = await listSchemasInDatabase(db)
        return Response.json({ schemas: rows.map((r) => String(r.SCHEMA_NAME)) })
      }

      case "tables": {
        const db = searchParams.get("db")
        const schema = searchParams.get("schema") ?? ""
        const keyword = searchParams.get("keyword") ?? ""
        if (!db) return Response.json({ error: "db required" }, { status: 400 })

        const schemaFilter = schema ? `and t.table_schema = upper(${lit(schema)})` : ""
        const keywordFilter = keyword
          ? `and (upper(t.table_name) like upper(${lit("%" + keyword + "%")})
              or upper(t.table_schema) like upper(${lit("%" + keyword + "%")}))`
          : ""

        const sql = `
          select
              t.table_catalog
              , t.table_schema
              , t.table_name
              , t.table_type
              , t.row_count
              , t.last_altered
              , score_table(t.table_name, t.table_schema) as heuristic_score
          from ${q(db)}.information_schema.tables as t
          where t.table_type in ('BASE TABLE', 'VIEW')
            and t.row_count < 2000000000
            ${schemaFilter}
            ${keywordFilter}
          order by heuristic_score desc, t.row_count desc nulls last
          limit 50`

        // INFORMATION_SCHEMA doesn't support UDFs — compute scoring in TS
        const raw = await scanTablesForBrowser(db, schema, keyword)

        const scored = raw.map((r) => ({
          database: String(r.TABLE_CATALOG),
          schema: String(r.TABLE_SCHEMA),
          table: String(r.TABLE_NAME),
          type: String(r.TABLE_TYPE),
          rowCount: r.ROW_COUNT != null ? Number(r.ROW_COUNT) : null,
          lastAltered: r.LAST_ALTERED ? String(r.LAST_ALTERED) : null,
          score: scoreTable(String(r.TABLE_NAME), String(r.TABLE_SCHEMA)),
          fqn: `${r.TABLE_CATALOG}.${r.TABLE_SCHEMA}.${r.TABLE_NAME}`,
        })).sort((a, b) => b.score - a.score || (b.rowCount ?? 0) - (a.rowCount ?? 0))

        return Response.json({ tables: scored.slice(0, 50) })
      }

      case "semantic-views": {
        // Account-wide list, for the semantic-view multi-select browser
        // (SemanticViewSection / SemanticViewBrowser in step-tables.tsx).
        // No db/schema cascade — semantic views are comparatively rare per
        // account, so a single flat list with client-side keyword filtering
        // is simpler than replicating the table browser's cascade.
        const keyword = (searchParams.get("keyword") ?? "").trim()
        const rows = await listSemanticViewsInAccount()
        const views = rows
          .map((r) => ({
            fqn: `${r.database_name ?? r.DATABASE_NAME}.${r.schema_name ?? r.SCHEMA_NAME}.${r.name ?? r.NAME}`,
            database: String(r.database_name ?? r.DATABASE_NAME ?? ""),
            schema: String(r.schema_name ?? r.SCHEMA_NAME ?? ""),
            name: String(r.name ?? r.NAME ?? ""),
            comment: r.comment ?? r.COMMENT ? String(r.comment ?? r.COMMENT) : null,
          }))
          .filter((v) => v.database && v.schema && v.name)
          .filter((v) =>
            !keyword ||
            `${v.fqn} ${v.comment ?? ""}`.toUpperCase().includes(keyword.toUpperCase()),
          )
          .sort((a, b) => a.fqn.localeCompare(b.fqn))

        return Response.json({ views: views.slice(0, 200) })
      }

      case "columns": {
        const table = searchParams.get("table")
        if (!table) return Response.json({ error: "table required" }, { status: 400 })
        if (!validateTableParam(table)) return Response.json({ error: "Invalid table name" }, { status: 400 })
        const [db, schema, tbl] = table.split(".")
        if (!db || !schema || !tbl) return Response.json({ error: "table must be DB.SCHEMA.TABLE" }, { status: 400 })

        const colRows = await listTableColumns(db, schema, tbl)

        // Fetch cardinality for each column (batch via APPROX_COUNT_DISTINCT)
        const colNames = colRows.map((r) => String(r.COLUMN_NAME))
        let cardinalities: Record<string, number> = {}
        if (colNames.length > 0) {
          try {
            const cardRows = await computeColumnCardinalities(table, colNames)
            if (cardRows.length > 0) {
              for (const col of colNames) {
                const key = ("card_" + col).toUpperCase()
                cardinalities[col] = Number(cardRows[0][key] ?? 0)
              }
            }
          } catch {
            // Non-fatal: skip cardinality if query fails
          }
        }

        // Fetch row count
        let rowCount = 0
        try {
          const rcRows = await countTableRows(table)
          rowCount = Number(rcRows[0]?.RC ?? 0)
        } catch {
          // Non-fatal
        }

        const columns = colRows.map((r) => {
          const name = String(r.COLUMN_NAME)
          const dtype = String(r.DATA_TYPE)
          const card = cardinalities[name] ?? 0
          return {
            name,
            dataType: dtype,
            isNullable: r.IS_NULLABLE === "YES",
            cardinality: card,
            cardinalityPct: rowCount > 0 ? Math.round((card / rowCount) * 1000) / 10 : null,
            suggestedRole: suggestRole(name, dtype, card, rowCount),
          }
        })

        return Response.json({ columns, rowCount })
      }

      case "sample": {
        const table = searchParams.get("table")
        const col = searchParams.get("col")
        if (!table || !col) return Response.json({ error: "table and col required" }, { status: 400 })
        if (!validateTableParam(table)) return Response.json({ error: "Invalid table name" }, { status: 400 })
        if (!validateColParam(col)) return Response.json({ error: "Invalid column name" }, { status: 400 })
        const rows = await sampleColumnValues(table, col)
        return Response.json({ samples: rows.map((r) => ({ value: r.VAL, count: Number(r.CNT) })) })
      }

      case "prefixes": {
        // Returns first-character prefix groups of an account/ledger column.
        const table = searchParams.get("table")
        const col   = searchParams.get("col")
        if (!table || !col) return Response.json({ error: "table and col required" }, { status: 400 })
        if (!validateTableParam(table)) return Response.json({ error: "Invalid table name" }, { status: 400 })
        if (!validateColParam(col)) return Response.json({ error: "Invalid column name" }, { status: 400 })
        const rows = await columnPrefixCounts(table, col)
        return Response.json({
          prefixes: rows.map((r) => ({ prefix: String(r.PREFIX), count: Number(r.CNT) })),
        })
      }

      case "join-check": {
        // Returns cost-center overlap % between actuals and budget tables.
        const aTable = searchParams.get("actualsTable")
        const bTable = searchParams.get("budgetTable")
        const aCC    = searchParams.get("actualsCC")
        const bCC    = searchParams.get("budgetCC")
        if (!aTable || !bTable || !aCC || !bCC)
          return Response.json({ error: "actualsTable, budgetTable, actualsCC, budgetCC required" }, { status: 400 })
        if (!validateTableParam(aTable) || !validateTableParam(bTable))
          return Response.json({ error: "Invalid table name" }, { status: 400 })
        const rows = await checkCostCenterOverlap(aTable, bTable, aCC, bCC)
        return Response.json({ overlapPct: Number(rows[0]?.OVERLAP_PCT ?? 0) })
      }

      case "preview": {
        const table = searchParams.get("table")
        if (!table) return Response.json({ error: "table required" }, { status: 400 })
        if (!validateTableParam(table)) return Response.json({ error: "Invalid table name" }, { status: 400 })
        const parts = table.split(".")
        if (parts.length !== 3) return Response.json({ error: "table must be DB.SCHEMA.TABLE" }, { status: 400 })
        const fqn = parts.map(q).join(".")
        const rows = await previewTableRows(fqn)
        const columns = rows.length > 0 ? Object.keys(rows[0]) : []
        return Response.json({ rows, columns })
      }

      case "category-overlap": {
        // Checks two things about the ledger mapping configuration:
        //   1. Join key overlap: what % of actualsAccountCol values appear in ledgerAccountKeyCol
        //      (format mismatch here means all actuals miss the JOIN and fall into 'Other Expenses')
        //   2. Category overlap: what % of distinct ledgerCategoryCol values appear in budgetCategoryCol
        //      (wrong column here means categories never match the budget)
        //
        // Returns sample values from both sides for every check so the user can eyeball them.
        //
        // Params (all required when ledger mapping is configured):
        //   ledgerTable       — fully-qualified ledger mapping table
        //   ledgerAccountKeyCol — join key column in ledger table (e.g. CODE)
        //   ledgerCategoryCol — category column in ledger table (e.g. CONTROLLABLE_EXPENSE_TYPE)
        //   actualsTable      — fully-qualified actuals/GL table
        //   actualsAccountCol — join key column in actuals (e.g. LEDGER_ACCOUNT_NUMBER)
        //   budgetTable       — fully-qualified budget table
        //   budgetCategoryCol — category column in budget table (e.g. ADAPTIVE_EXPENSE_TYPE)
        // Optional params (comma-separated) — live preview of BvaConfig.keyStripPrefixes
        // (see lib/config.ts, components/setup/step-adjustments.tsx's KeyPrefixPanel):
        //   ledgerKeyStripPrefixes  — prefixes to strip from ledgerAccountKeyCol before comparing
        //   actualsKeyStripPrefixes — prefixes to strip from actualsAccountCol before comparing
        const ledgerTable        = searchParams.get("ledgerTable")
        const ledgerAccountKeyCol = searchParams.get("ledgerAccountKeyCol")
        const ledgerCategoryCol  = searchParams.get("ledgerCategoryCol")
        const actualsTable       = searchParams.get("actualsTable")
        const actualsAccountCol  = searchParams.get("actualsAccountCol")
        const budgetTable        = searchParams.get("budgetTable")
        const budgetCategoryCol  = searchParams.get("budgetCategoryCol")
        if (!ledgerTable || !ledgerAccountKeyCol || !ledgerCategoryCol || !actualsTable || !actualsAccountCol || !budgetTable || !budgetCategoryCol)
          return Response.json({ error: "ledgerTable, ledgerAccountKeyCol, ledgerCategoryCol, actualsTable, actualsAccountCol, budgetTable, budgetCategoryCol required" }, { status: 400 })
        if (!validateTableParam(ledgerTable) || !validateTableParam(actualsTable) || !validateTableParam(budgetTable))
          return Response.json({ error: "Invalid table name" }, { status: 400 })

        const parsePrefixes = (param: string | null): string[] =>
          param ? param.split(",").map((s) => s.trim()).filter(Boolean) : []
        const ledgerKeyPrefixes  = parsePrefixes(searchParams.get("ledgerKeyStripPrefixes"))
        const actualsKeyPrefixes = parsePrefixes(searchParams.get("actualsKeyStripPrefixes"))

        const rows = await checkCategoryOverlap({
          ledgerTable, ledgerAccountKeyCol, ledgerCategoryCol,
          actualsTable, actualsAccountCol,
          budgetTable, budgetCategoryCol,
          ledgerKeyPrefixes, actualsKeyPrefixes,
        })
        const r = rows[0] ?? {}
        const actKeyCount    = Number(r.ACT_KEY_COUNT     ?? 0)
        const keyOverlapCount = Number(r.KEY_OVERLAP_COUNT ?? 0)
        const lmCatCount     = Number(r.LM_CAT_COUNT      ?? 0)
        const catOverlapCount = Number(r.CAT_OVERLAP_COUNT ?? 0)
        return Response.json({
          // Join key check
          actKeyCount,
          lmKeyCount:       Number(r.LM_KEY_COUNT    ?? 0),
          keyOverlapCount,
          keyOverlapPct:    actKeyCount > 0 ? Math.round(keyOverlapCount / actKeyCount * 100) : 0,
          actKeySample:     String(r.ACT_KEY_SAMPLE  ?? ""),
          lmKeySample:      String(r.LM_KEY_SAMPLE   ?? ""),
          // Category check
          lmCatCount,
          budCatCount:      Number(r.BUD_CAT_COUNT   ?? 0),
          catOverlapCount,
          catOverlapPct:    lmCatCount > 0 ? Math.round(catOverlapCount / lmCatCount * 100) : 0,
          lmCatSample:      String(r.LM_CAT_SAMPLE   ?? ""),
          budCatSample:     String(r.BUD_CAT_SAMPLE  ?? ""),
        })
      }

      case "categories": {
        // Distinct category values + counts from a mapped category column.
        // Used by CategoryManagementPanel to populate the rename/merge editor.
        const table = searchParams.get("table")
        const col   = searchParams.get("col")
        if (!table || !col) return Response.json({ error: "table and col required" }, { status: 400 })
        if (!validateTableParam(table)) return Response.json({ error: "Invalid table name" }, { status: 400 })
        if (!validateColParam(col)) return Response.json({ error: "Invalid column name" }, { status: 400 })
        const rows = await listCategoryValues(table, col)
        return Response.json({
          categories: rows.map((r) => ({ value: String(r.VALUE ?? ""), count: Number(r.CNT ?? 0) })),
        })
      }

      case "all-cc": {
        // Returns ALL distinct CC values (up to 500) with an optional pattern badge for flagged ones.
        // Flagged CCs (DNU_, zz, INACTIVE_, etc.) float to the top.
        // Used by CostCenterExclusionsPanel to show the full list, not just inactive patterns.
        const table = searchParams.get("table")
        const col   = searchParams.get("col")
        if (!table || !col) return Response.json({ error: "table and col required" }, { status: 400 })
        if (!validateTableParam(table)) return Response.json({ error: "Invalid table name" }, { status: 400 })
        if (!validateColParam(col)) return Response.json({ error: "Invalid column name" }, { status: 400 })
        const rows = await listAllCostCenterValues(table, col)
        return Response.json({
          costCenters: rows.map((r) => ({
            value:   String(r.VALUE ?? ""),
            count:   Number(r.CNT ?? 0),
            pattern: r.PATTERN != null ? String(r.PATTERN) : null,
          })),
        })
      }

      case "inactive-cc": {
        // Detect CC values with inactive markers (DNU_, zz_, INACTIVE_, CLOSED_, TEST_).
        // Default is to include all — user explicitly opts into excluding specific values.
        const table = searchParams.get("table")
        const col   = searchParams.get("col")
        if (!table || !col) return Response.json({ error: "table and col required" }, { status: 400 })
        if (!validateTableParam(table)) return Response.json({ error: "Invalid table name" }, { status: 400 })
        if (!validateColParam(col)) return Response.json({ error: "Invalid column name" }, { status: 400 })
        const rows = await listInactiveCostCenterValues(table, col)
        return Response.json({
          flagged: rows.map((r) => ({ value: String(r.VALUE ?? ""), count: Number(r.CNT ?? 0), pattern: String(r.PATTERN ?? "") })),
        })
      }

      case "validate": {
        const table = searchParams.get("table")
        if (!table) return Response.json({ error: "table required" }, { status: 400 })
        if (!validateTableParam(table)) return Response.json({ error: "Invalid table name" }, { status: 400 })
        try {
          await checkTableExists(table)
          return Response.json({ valid: true })
        } catch (e) {
          console.error("[api/setup/discover validate]", e)
          return Response.json({ valid: false, error: "Validation failed — verify the table exists and your role can read it." })
        }
      }

      case "account-categories": {
        // Returns every account key from the ledger mapping table with its resolved
        // (max) category — mirrors what buildFromConfig's ledger_map CTE does.
        // Used by AccountCategoryOverridesPanel to let users reclassify specific codes.
        const table      = searchParams.get("table")
        const keyCol     = searchParams.get("keyCol")
        const categoryCol = searchParams.get("categoryCol")
        if (!table || !keyCol || !categoryCol) {
          return Response.json({ error: "table, keyCol, and categoryCol are required" }, { status: 400 })
        }
        if (!validateTableParam(table)) return Response.json({ error: "Invalid table name" }, { status: 400 })
        if (!validateColParam(keyCol) || !validateColParam(categoryCol)) return Response.json({ error: "Invalid column name" }, { status: 400 })
        const rows = await listAccountCategories(table, keyCol, categoryCol)
        return Response.json({
          accounts: rows.map((r) => ({
            code:     String(r.CODE ?? ""),
            category: String(r.CATEGORY ?? ""),
            count:    Number(r.CNT ?? 0),
          })),
        })
      }

      default:
        return Response.json({ error: `Unknown action: ${action}` }, { status: 400 })
    }
  } catch (e) {
    console.error(new Date().toISOString(), "[api/setup/discover]", e)
    return Response.json(
      { error: "Discovery failed" },
      { status: 500 },
    )
  }
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

// Guards every action that takes a user-supplied table name before it's
// interpolated into raw SQL — same shape check as lib/bva.ts's exported
// validateTableParam (this is the original implementation; lib/bva.ts's
// version is a since-duplicated copy used by other routes).
function validateTableParam(table: string | null): table is string {
  return typeof table === "string" && /^[A-Za-z0-9_$"]+\.[A-Za-z0-9_$"]+\.[A-Za-z0-9_$"]+$/.test(table)
}

// Defense-in-depth for the `col` query param. The query helpers in
// lib/queries/discover.ts already wrap col in q() (double-quoted identifier),
// so this is a second gate, not the only one — reject anything that isn't a
// bare column identifier before it reaches SQL. Not re-quoted here (the helper
// quotes); validation only.
function validateColParam(col: string | null): col is string {
  return typeof col === "string" && /^[A-Za-z0-9_$]+$/.test(col)
}
export { validateColParam }

/** Snowflake double-quote identifier */
function q(s: string): string {
  return `"${s.replace(/"/g, '""')}"`
}

/** SQL string literal */
function lit(s: string): string {
  return "'" + s.replace(/'/g, "''") + "'"
}

/** System databases to exclude from the browser */
const SYSTEM_DBS = new Set([
  "SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA", "SNOWFLAKE_MARKETPLACE",
  "ORGADMIN", "SNOWFLAKE_LEARNING", "SNOWFLAKE_OPERATIONS",
])

/** Heuristic score: higher = more likely to be a BvA-relevant table */
function scoreTable(tableName: string, schemaName: string): number {
  const name = (tableName + " " + schemaName).toUpperCase()
  let score = 0
  // Strong GL/actuals signals
  if (/GL|JOURNAL|LEDGER|GENERAL_LEDGER|ACTUALS?/.test(name)) score += 10
  // Budget/forecast signals
  if (/BUDGET|FORECAST|PLAN|VARIANCE|BVA/.test(name)) score += 9
  // Cost center signals
  if (/COST.CENTER|CC_|DEPARTMENT|ORG|DIV/.test(name)) score += 7
  // Finance schema signals
  if (/FINANCE|ACCOUNTING|FP.?A|CONTROLLER/.test(name)) score += 3
  // Penalize staging/temp/raw
  if (/STG|STAGING|RAW|TMP|TEMP|_HIST$|_LOG$/.test(name)) score -= 5
  return score
}

/**
 * Suggest a BvA role for a column based on name, type, and cardinality.
 * These are hints the UI shows as default values — users can always override.
 */
function suggestRole(name: string, dtype: string, cardinality: number, rowCount: number): string | null {
  const n = name.toUpperCase()
  const isDate   = /DATE|TIMESTAMP/.test(dtype)
  const isNumber = /NUMBER|FLOAT|DECIMAL|INT/.test(dtype)
  const isText   = /VARCHAR|TEXT|STRING|CHAR/.test(dtype)
  const cardPct  = rowCount > 0 ? cardinality / rowCount : 0

  // Always skip row-number / rank columns
  if (/^_?RN$|^ROW_?NUM$|^ROWNUM$|^RANK$|^IDX$|^INDEX$/.test(n)) return null

  // Date columns — skip pipeline metadata
  if (isDate) {
    if (/^INGEST|_INGEST_|^LAST_UPDATED|^CREATED_AT$|^UPDATED_AT$|^MODIFIED_AT|^LOAD_DATE|^ETL_DATE/.test(n)) return null
    return "period"
  }

  // Fiscal year / quarter
  if (/FISCAL_?YEAR|^FY[_0-9]|^FISCAL_YR/.test(n)) return "fiscal_year"
  if (/FISCAL_?QUARTER|FISCAL_?QTR|^FQ[_0-9]/.test(n)) return "fiscal_quarter"

  // Number columns
  if (isNumber) {
    if (/RATE$|EXCHANGE|_RATE_|FX_|RATIO|COUNT$|_COUNT$|^ID$|_ID$|_KEY$|^KEY$|CODE$/.test(n)) return null
    if (/AMOUNT|AMT|EXPENSE|SPEND|COST|VALUE|BALANCE|DEBIT|CREDIT|NET_|_NET$|^NET$/.test(n)) return "amount"
    // Name gave no hint — fall back to shape: a dollar-amount column has many
    // distinct values relative to row count (each transaction differs), unlike
    // a flag/code/rate column with a handful of repeated values. 0.5% is a
    // low bar deliberately, since this is just a suggested default the user can change.
    if (cardinality > 0 && cardinality > rowCount * 0.005) return "amount"
    return null
  }

  // Text columns
  if (isText) {
    // Category values are typically a small, curated list (a few to a few
    // hundred distinct labels) — too many distinct values and it's more
    // likely a free-text/description field than a category.
    if (/CATEGOR|SPEND_CAT|EXPENSE_TYPE|EXPENSE_CLASS|ACCOUNT_TYPE|LEDGER_ACCOUNT|ADAPTIVE.*SPEND.*CAT|ADAPTIVE.*EXPENSE|ADAPTIVE.*CAT/.test(n)) {
      if (cardinality === 0 || (cardinality >= 3 && cardinality <= 300)) return "category"
    }
    if (/COST_?CENTER|^CC_?ID$|^CC_?CODE$|^COST_CTR$/.test(n)) return "cost_center"
    if (/^DEPARTMENT$|^DEPT$|^ORG$|^DIVISION$/.test(n)) return "department"
    // Same reasoning as version detection in the profile route: a version/scenario
    // column has a small, bounded set of distinct values (<= 30 planning vintages).
    if (/VERSION|PLAN_NAME|FORECAST_NAME|SCENARIO/.test(n) && (cardinality === 0 || cardinality <= 30)) return "version"
    if (/COST_?CENTER_?NAME|^CC_?NAME$/.test(n)) return "cc_name"
    // High-cardinality text with no other name match is more likely a
    // free-text/ID/comment column than any BvA dimension — don't suggest a role.
    if (cardinality > 0 && cardPct > 0.3) return null
  }

  return null
}
