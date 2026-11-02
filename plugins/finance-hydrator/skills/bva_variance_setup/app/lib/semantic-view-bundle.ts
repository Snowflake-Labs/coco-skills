/**
 * Server-only: discovers Snowflake semantic views to raise confidence in
 * table/column mapping and to give the custom-logic agent business vocabulary
 * (synonyms, named dimensions/facts) it can reuse instead of inventing new
 * expressions from scratch. Mirrors lib/discover-bundle.ts's pattern:
 * precompute deterministically in TypeScript, hand the agent a compact
 * markdown bundle instead of making it issue raw SQL turn-by-turn.
 *
 * Two paths, in priority order:
 *   1. Manual — the customer already named a semantic view (BvaConfig.semanticViewRefs,
 *      set in Step 1). Skips the account scan entirely and describes exactly
 *      those views — an explicit customer choice always wins over auto-discovery.
 *   2. Auto — SHOW SEMANTIC VIEWS [IN ACCOUNT], filtered to finance-relevant
 *      names/comments, then DESCRIBE SEMANTIC VIEW on each candidate.
 *
 * Both SHOW SEMANTIC VIEWS and DESCRIBE SEMANTIC VIEW are pure metadata reads
 * (any privilege on the view is enough to describe it; SELECT is only needed
 * to query it) — fully compatible with this app's read-only snowflake_sql_execute
 * restriction (see lib/agent-backend.ts).
 */
import { querySnowflake } from "@/lib/snowflake"
import { WAREHOUSE } from "@/lib/bva"

// Kept in sync with lib/discover-bundle.ts's DB_KEYWORDS / FINANCE_KEYWORDS —
// same vocabulary, applied to semantic view names/comments instead of tables.
const FINANCE_KEYWORDS = [
  "FINANCE", "FIN", "CORP", "ACCOUNT", "ACCT", "FPA", "ERP", "GL",
  "GENERAL_LEDGER", "LEDGER", "JOURNAL", "GAAP", "BUDGET", "FORECAST",
  "PLANNING", "PLAN", "ACTUAL", "ADAPTIVE", "ANAPLAN", "HYPERION", "NETSUITE",
  "SAP", "WORKDAY", "COST_CENTER", "COSTCENTER", "INCOME_STATEMENT", "VARIANCE", "BVA",
]

const MAX_AUTO_CANDIDATES = 8
// Manual refs are explicit customer intent — no hard cap (a customer with an
// agent backed by 19+ semantic views should be able to point at all of
// them). We only warn when the list is large, since describing many views
// in parallel and stuffing all of them into the agent's prompt gets slower
// and more expensive the more there are — the customer should know that
// trade-off, not have views silently dropped.
const WARN_MANUAL_VIEWS_THRESHOLD = 12
// Per view, per kind (dimension/fact/metric) — guards against a single huge
// semantic view (common in real Cortex Analyst deployments: 50+ columns
// across many tables) blowing up the prompt even when the view count itself
// is small.
const MAX_PROPERTIES_PER_KIND = 30

export type SemanticProperty = {
  kind: "dimension" | "fact" | "metric"
  name: string
  table: string
  expression?: string
  dataType?: string
  synonyms: string[]
  comment?: string
}

export type SemanticTable = {
  name: string
  baseTable: string | null // fully-qualified DB.SCHEMA.TABLE, or null if defined via SQL query
  synonyms: string[]
  comment?: string
}

export type SemanticViewInfo = {
  view: string // fully-qualified name as given/discovered
  comment?: string
  tables: SemanticTable[]
  properties: SemanticProperty[] // dimensions + facts + metrics, flattened
}

export type SemanticViewBundle = {
  views: SemanticViewInfo[]
  source: "manual" | "auto" | "none"
  candidatesConsidered: string[] // for "auto": every view name SHOW returned before filtering
  elapsedMs: number
  // Set when the manual ref list exceeded WARN_MANUAL_VIEWS_THRESHOLD — every
  // ref is still described, this is informational only (surfaced to the
  // agent so it can mention the latency/cost trade-off if asked).
  warnManyViews?: boolean
}

// Cheap pre-filter so we only run the (comparatively expensive) DESCRIBE on
// views that plausibly matter to this app, rather than every semantic view
// in the account. Checks both the name and the view's own comment against
// the finance keyword list — a view named generically but commented as
// finance-related should still be picked up.
function looksFinanceRelated(name: string, comment: string | null): boolean {
  const hay = `${name} ${comment ?? ""}`.toUpperCase()
  return FINANCE_KEYWORDS.some((k) => hay.includes(k))
}

/** Best-effort parse of a DESCRIBE SEMANTIC VIEW SYNONYMS/PRIMARY_KEY value —
 *  the driver may already hand back an array, or a JSON-array string. */
function parseArrayValue(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String)
  if (typeof v === "string") {
    try {
      const parsed = JSON.parse(v)
      if (Array.isArray(parsed)) return parsed.map(String)
    } catch {
      // not JSON — fall through
    }
  }
  return []
}

/**
 * Parses one DESCRIBE SEMANTIC VIEW result set into a structured SemanticViewInfo.
 *
 * DESCRIBE SEMANTIC VIEW doesn't return one row per table/dimension/fact —
 * it returns one row PER PROPERTY (e.g. one row for a table's SYNONYMS, a
 * separate row for its COMMENT, etc.), all flattened into a generic
 * OBJECT_KIND / OBJECT_NAME / PARENT_ENTITY / PROPERTY / PROPERTY_VALUE shape.
 * This function re-assembles that flat row stream back into grouped
 * SemanticTable / SemanticProperty objects, since a table or property's
 * fields can arrive in any order and split across many rows.
 */
export function parseDescribeSemanticView(viewFqn: string, rows: Record<string, unknown>[]): SemanticViewInfo {
  const tablesByName = new Map<string, SemanticTable>()
  // Accumulates BASE_TABLE_DATABASE_NAME/SCHEMA_NAME/NAME independently of
  // SemanticTable.baseTable (which starts null) — properties for the same
  // table can arrive in any order across separate rows.
  const baseTableParts = new Map<string, [string?, string?, string?]>()
  const properties: SemanticProperty[] = []
  let viewComment: string | undefined

  for (const row of rows) {
    const kind = String(row.OBJECT_KIND ?? "").toUpperCase()
    const name = row.OBJECT_NAME != null ? String(row.OBJECT_NAME) : null
    const parent = row.PARENT_ENTITY != null ? String(row.PARENT_ENTITY) : null
    const property = String(row.PROPERTY ?? "").toUpperCase()
    const value = row.PROPERTY_VALUE

    if (!kind || kind === "NULL") {
      if (property === "COMMENT" && typeof value === "string") viewComment = value
      continue
    }

    if (kind === "TABLE" && name) {
      const t = tablesByName.get(name) ?? { name, baseTable: null, synonyms: [] }
      if (property === "BASE_TABLE_DATABASE_NAME" || property === "BASE_TABLE_SCHEMA_NAME" || property === "BASE_TABLE_NAME") {
        const parts = baseTableParts.get(name) ?? [undefined, undefined, undefined]
        const idx = property === "BASE_TABLE_DATABASE_NAME" ? 0 : property === "BASE_TABLE_SCHEMA_NAME" ? 1 : 2
        parts[idx] = String(value)
        baseTableParts.set(name, parts)
        t.baseTable = parts.every(Boolean) ? parts.join(".") : null
      } else if (property === "SYNONYMS") {
        t.synonyms = parseArrayValue(value)
      } else if (property === "COMMENT") {
        t.comment = typeof value === "string" ? value : undefined
      }
      tablesByName.set(name, t)
      continue
    }

    if ((kind === "DIMENSION" || kind === "FACT" || kind === "METRIC") && name) {
      const key = `${kind}:${parent ?? ""}:${name}`
      let prop = properties.find((p) => `${p.kind.toUpperCase()}:${p.table}:${p.name}` === key)
      if (!prop) {
        prop = { kind: kind.toLowerCase() as SemanticProperty["kind"], name, table: parent ?? "", synonyms: [] }
        properties.push(prop)
      }
      if (property === "EXPRESSION" && typeof value === "string") prop.expression = value
      else if (property === "DATA_TYPE" && typeof value === "string") prop.dataType = value
      else if (property === "SYNONYMS") prop.synonyms = parseArrayValue(value)
      else if (property === "COMMENT" && typeof value === "string") prop.comment = value
    }
  }

  return {
    view: viewFqn,
    comment: viewComment,
    tables: [...tablesByName.values()],
    properties,
  }
}

async function describeOne(viewFqn: string): Promise<SemanticViewInfo | null> {
  try {
    // DESCRIBE SEMANTIC VIEW is a metadata-only read (any privilege on the
    // view is enough — SELECT isn't required), so this is safe to run for
    // every candidate without worrying about accidentally querying real data.
    const rows = await querySnowflake(`DESCRIBE SEMANTIC VIEW ${viewFqn}`, {
      warehouse: WAREHOUSE,
      tag: "bva:semantic-view-bundle",
    })
    return parseDescribeSemanticView(viewFqn, rows)
  } catch {
    return null // not found, no privilege, or transient error — caller decides how to surface this
  }
}

/**
 * Builds a semantic view bundle. `manualRefs` (from BvaConfig.semanticViewRefs,
 * set explicitly by the customer in Step 1) always takes priority — when
 * given, this function describes exactly those views and never runs the
 * account-wide SHOW SEMANTIC VIEWS scan.
 */
export async function buildSemanticViewBundle(manualRefs?: string[]): Promise<SemanticViewBundle> {
  const t0 = Date.now()

  if (manualRefs?.length) {
    // Customer-provided refs skip discovery entirely — just describe each one
    // directly (in parallel) rather than scanning the account for candidates.
    const described = await Promise.all(manualRefs.map((ref) => describeOne(ref.trim())))
    return {
      views: described.filter((v): v is SemanticViewInfo => v !== null),
      source: "manual",
      candidatesConsidered: manualRefs,
      elapsedMs: Date.now() - t0,
      warnManyViews: manualRefs.length > WARN_MANUAL_VIEWS_THRESHOLD,
    }
  }

  let showRows: Record<string, unknown>[]
  try {
    // Account-wide inventory of semantic views — the starting point for
    // auto-discovery. Also metadata-only (see describeOne above), so this is
    // cheap and safe to run even when the customer hasn't set anything up yet.
    showRows = await querySnowflake("SHOW SEMANTIC VIEWS IN ACCOUNT", {
      warehouse: WAREHOUSE,
      tag: "bva:semantic-view-bundle",
    })
  } catch {
    return { views: [], source: "none", candidatesConsidered: [], elapsedMs: Date.now() - t0 }
  }

  const allNames = showRows.map((r) => {
    const db = String(r.database_name ?? r.DATABASE_NAME ?? "")
    const schema = String(r.schema_name ?? r.SCHEMA_NAME ?? "")
    const name = String(r.name ?? r.NAME ?? "")
    return { fqn: `${db}.${schema}.${name}`, comment: (r.comment ?? r.COMMENT) as string | null }
  }).filter((v) => v.fqn && !v.fqn.startsWith("."))

  const candidates = allNames
    .filter((v) => looksFinanceRelated(v.fqn, v.comment))
    .slice(0, MAX_AUTO_CANDIDATES)

  if (candidates.length === 0) {
    return { views: [], source: "none", candidatesConsidered: allNames.map((v) => v.fqn), elapsedMs: Date.now() - t0 }
  }

  // Describe every finance-relevant candidate in parallel — this is the
  // expensive step (one round-trip per view), which is why we filtered down
  // to MAX_AUTO_CANDIDATES first instead of describing every view SHOW returned.
  const described = await Promise.all(candidates.map((c) => describeOne(c.fqn)))

  return {
    views: described.filter((v): v is SemanticViewInfo => v !== null),
    source: "auto",
    candidatesConsidered: allNames.map((v) => v.fqn),
    elapsedMs: Date.now() - t0,
  }
}

/**
 * Renders a semantic view bundle as compact markdown for injection into an
 * agent's system prompt (setup discovery, or the custom-logic agent). Empty
 * string when there's nothing to show — callers should skip the section
 * entirely rather than print an empty header.
 */
export function formatSemanticViewMarkdown(bundle: SemanticViewBundle): string {
  if (bundle.views.length === 0) return ""

  const lines: string[] = [
    bundle.source === "manual"
      ? `Semantic view(s) the customer pointed at directly (use this as strong evidence, don't second-guess it):`
      : `Semantic view(s) auto-discovered as finance-relevant (in ${(bundle.elapsedMs / 1000).toFixed(1)}s):`,
  ]

  if (bundle.warnManyViews) {
    lines.push(``, `Note: the customer pointed at ${bundle.views.length} semantic views — a lot of context. Keep responses focused on the views/dimensions actually relevant to the question rather than restating all of them.`)
  }

  for (const v of bundle.views) {
    lines.push(``, `### ${v.view}${v.comment ? ` — ${v.comment}` : ""}`)
    if (v.tables.length) {
      lines.push(`Logical tables:`)
      for (const t of v.tables) {
        const syn = t.synonyms.length ? ` (synonyms: ${t.synonyms.join(", ")})` : ""
        lines.push(`- ${t.name}${syn}${t.baseTable ? ` → backed by ${t.baseTable}` : ""}${t.comment ? ` — ${t.comment}` : ""}`)
      }
    }
    const byKind = (k: SemanticProperty["kind"]) => v.properties.filter((p) => p.kind === k)
    for (const kind of ["dimension", "fact", "metric"] as const) {
      const items = byKind(kind)
      if (!items.length) continue
      lines.push(`${kind[0].toUpperCase()}${kind.slice(1)}s:`)
      const shown = items.slice(0, MAX_PROPERTIES_PER_KIND)
      for (const p of shown) {
        const syn = p.synonyms.length ? ` (synonyms: ${p.synonyms.join(", ")})` : ""
        lines.push(`- ${p.table}.${p.name} = ${p.expression ?? "?"}${syn}${p.comment ? ` — ${p.comment}` : ""}`)
      }
      if (items.length > shown.length) {
        lines.push(`- ...and ${items.length - shown.length} more ${kind}(s) not shown`)
      }
    }
  }

  return lines.join("\n")
}
