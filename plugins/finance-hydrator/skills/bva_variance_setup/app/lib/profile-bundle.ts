/**
 * Server-only: deterministic, parallel profiling of the tables the user selected
 * in Phase 1. Replaces the agent's slow serial Phase-2 work (a DESCRIBE + one or
 * more sample queries PER table, issued one LLM turn at a time) with a single
 * parallel fan-out: for each selected table we run, concurrently, a column
 * metadata read, one batched APPROX_COUNT_DISTINCT (cardinality for every column
 * in a single pass), and a small sample. Results are injected into the agent's
 * turn so it can emit the column-confirm mapping without touching the tables.
 *
 * Accuracy is preserved: this is the same metadata + sample evidence the agent
 * would have gathered — the user still confirms every column mapping.
 */
import { querySnowflake } from "@/lib/snowflake"
import { WAREHOUSE, validateTableParam } from "@/lib/bva"
import { suggestRole } from "@/lib/queries/profile"

export type SelectedTables = {
  gl?: string
  budget?: string
  cc?: string
  ledger?: string
}

export type ProfiledColumn = {
  name: string
  dataType: string
  cardinality: number
  suggestedRole: string | null
}

export type ProfiledTable = {
  role: keyof SelectedTables
  table: string
  rowCount: number | null
  columns: ProfiledColumn[]
  sample: Record<string, unknown>[]
  error?: string
}

export type ProfileBundle = {
  tables: ProfiledTable[]
  elapsedMs: number
}

const SENTINEL = /I'?d like to use these tables/i

/** Parse the CandidatePicker confirmation message into selected tables by role. */
export function parseSelectedTables(message: string): SelectedTables | null {
  if (!SENTINEL.test(message)) return null
  const sel: SelectedTables = {}
  const lineRe = /^-\s*(.+?):\s*([A-Za-z0-9_$]+\.[A-Za-z0-9_$]+\.[A-Za-z0-9_$]+)\s*$/gm
  let m: RegExpExecArray | null
  while ((m = lineRe.exec(message)) !== null) {
    const label = m[1].toUpperCase()
    const fqn = m[2]
    if (/GL|ACTUAL/.test(label)) sel.gl = fqn
    else if (/BUDGET|FORECAST/.test(label)) sel.budget = fqn
    else if (/COST.?CENTER|DEPARTMENT/.test(label)) sel.cc = fqn
    else if (/LEDGER|ACCOUNT/.test(label)) sel.ledger = fqn
  }
  if (!sel.gl && !sel.budget && !sel.cc && !sel.ledger) return null
  return sel
}

function q(s: string): string { return `"${s.replace(/"/g, '""')}"` }

async function profileOne(role: keyof SelectedTables, table: string): Promise<ProfiledTable> {
  if (!validateTableParam(table)) return { role, table, rowCount: null, columns: [], sample: [], error: "not a DB.SCHEMA.TABLE name" }
  const [db, schema, tbl] = table.split(".")
  try {
    // Column metadata (instant) + sample (small) in parallel.
    const [colRows, sample] = await Promise.all([
      // READ_COLUMN_METADATA: lists the table's columns and their SQL types
      // from INFORMATION_SCHEMA, in declared column order — this is what lets
      // profileOne() know which columns exist before it can compute anything
      // about them (cardinality, samples).
      querySnowflake(
        `SELECT column_name, data_type FROM ${q(db)}.INFORMATION_SCHEMA.COLUMNS
         WHERE table_schema = upper('${schema.replace(/'/g, "''")}') AND table_name = upper('${tbl.replace(/'/g, "''")}')
         ORDER BY ordinal_position`,
        { warehouse: WAREHOUSE, tag: "bva:profile-bundle" },
      ),
      // READ_SAMPLE_ROWS: grabs a small preview of real data (via Snowflake's
      // block SAMPLE, then capped further with LIMIT) so the agent can see
      // actual values, not just column names/types, when confirming mappings.
      querySnowflake(`SELECT * FROM ${table} SAMPLE (50 ROWS) LIMIT 5`, { warehouse: WAREHOUSE, tag: "bva:profile-bundle" })
        .catch(() => [] as Record<string, unknown>[]),
    ])

    const colNames = colRows.map((r) => String(r.COLUMN_NAME))
    // One batched cardinality pass over all columns + row count.
    let card: Record<string, number> = {}
    let rowCount: number | null = null
    if (colNames.length) {
      const exprs = colNames.map((c) => `approx_count_distinct(${q(c)}) as ${q("c_" + c)}`).join(", ")
      try {
        // READ_CARDINALITY_AND_ROWCOUNT: one query, one full table scan, that
        // gets both the total row count and an approximate distinct-value
        // count for every column at once (rather than one COUNT(DISTINCT)
        // query per column) — approx_count_distinct trades exactness for
        // speed, which is fine here since this is just a hint for the agent,
        // not a value shown as ground truth.
        const rows = await querySnowflake(
          `SELECT count(*) as rc, ${exprs} FROM ${table}`,
          { warehouse: WAREHOUSE, tag: "bva:profile-bundle" },
        )
        if (rows[0]) {
          rowCount = Number(rows[0].RC ?? 0)
          for (const c of colNames) card[c] = Number(rows[0][("c_" + c).toUpperCase()] ?? 0)
        }
      } catch { /* cardinality is best-effort */ }
    }

    const columns: ProfiledColumn[] = colRows.map((r) => {
      const name = String(r.COLUMN_NAME)
      const dtype = String(r.DATA_TYPE)
      return { name, dataType: dtype, cardinality: card[name] ?? 0, suggestedRole: suggestRole(name, dtype) }
    })
    return { role, table, rowCount, columns, sample }
  } catch (e) {
    return { role, table, rowCount: null, columns: [], sample: [], error: e instanceof Error ? e.message : String(e) }
  }
}

/** Profile all selected tables in parallel. */
export async function buildProfileBundle(sel: SelectedTables): Promise<ProfileBundle> {
  const t0 = Date.now()
  const jobs: Promise<ProfiledTable>[] = []
  ;(["gl", "budget", "cc", "ledger"] as const).forEach((role) => {
    const t = sel[role]
    if (t) jobs.push(profileOne(role, t))
  })
  const tables = await Promise.all(jobs)
  return { tables, elapsedMs: Date.now() - t0 }
}

/** Render the profile bundle as markdown to inject into the agent's turn. */
export function formatProfileMarkdown(bundle: ProfileBundle): string {
  const roleLabel: Record<string, string> = {
    gl: "GL / Actuals", budget: "Budget / Forecast", cc: "Cost Center mapping", ledger: "Ledger Account mapping",
  }
  const parts: string[] = [
    `Parallel column profiling complete (${(bundle.elapsedMs / 1000).toFixed(1)}s). Use this to map columns — do NOT run DESCRIBE or sample queries for these tables.`,
  ]
  for (const t of bundle.tables) {
    parts.push("")
    parts.push(`### ${roleLabel[t.role] ?? t.role}: ${t.table}${t.rowCount != null ? ` (${t.rowCount.toLocaleString()} rows)` : ""}`)
    if (t.error) { parts.push(`_could not profile: ${t.error}_`); continue }
    parts.push(`| column | type | distinct | hint |`)
    parts.push(`|---|---|---|---|`)
    for (const c of t.columns) {
      parts.push(`| ${c.name} | ${c.dataType} | ${c.cardinality} | ${c.suggestedRole ?? ""} |`)
    }
    if (t.sample.length) {
      const cols = Object.keys(t.sample[0]).slice(0, 8)
      parts.push(``)
      parts.push(`Sample (first ${t.sample.length} rows, ${cols.length} cols shown):`)
      parts.push("```")
      for (const row of t.sample) {
        parts.push(cols.map((c) => `${c}=${JSON.stringify(row[c])}`).join("  "))
      }
      parts.push("```")
    }
  }
  return parts.join("\n")
}
