/**
 * Guardrail for user- and agent-authored SQL fragments used in
 * CustomLogicRule.sql (see lib/config.ts's buildFinalSql()). These fragments
 * get spliced into a query that ultimately powers `CREATE OR REPLACE TABLE
 * ... AS` (lib/config-server.ts's materializeBva()), so this is the single
 * place that policy is enforced — do not duplicate these checks elsewhere,
 * and never trust a caller's own self-check (e.g. the agent's) as sufficient.
 *
 * In plain terms: a customer (or an AI agent acting on their behalf) can
 * type a raw SQL snippet into the UI to define a custom filter or derived
 * column. That snippet is not sandboxed SQL — it gets pasted directly into a
 * real query that runs with real privileges. Without a gate like this, a
 * crafted snippet could smuggle in a second SQL statement (SQL injection),
 * call a function/UDF the app never intended to expose, or reference a
 * column that doesn't exist in the safe result set — any of which could
 * leak data, corrupt the materialized table, or blow up query execution.
 * This file's job is to reject anything that isn't a narrow, well-behaved
 * *expression* before it ever reaches real SQL.
 *
 * This is a single SQL *expression* validator, not a full SQL parser. It
 * rejects anything that looks like a full statement, DDL/DML, comments, or a
 * reference to a column/identifier that isn't on the allow-list — not a
 * guarantee of semantic correctness (that's what /api/setup/validate is for).
 */

export type CustomLogicKind = "derived_column" | "filter"

export type GuardResult = { ok: true } | { ok: false; reason: string }

const MAX_SQL_LENGTH = 2000

// Whole-word, case-insensitive. Any of these anywhere in the expression is an
// automatic reject — an expression should never need to issue DDL/DML.
const FORBIDDEN_KEYWORDS = [
  "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "DROP", "TRUNCATE",
  "COPY", "GRANT", "REVOKE", "CALL", "EXECUTE", "EXEC", "USE", "UNLOAD",
]

// Common SQL keywords/operators/functions this app's own generated SQL
// already relies on (see lib/config.ts's buildCategoryExpr, stripPrefixExpr,
// fyExpr, etc.) — allow-listed so a legitimate CASE/COALESCE/date expression
// doesn't get flagged as an "unknown column." Not exhaustive; the agent's
// prompt should steer toward this vocabulary rather than exotic functions.
const ALLOWED_KEYWORDS = new Set([
  "case", "when", "then", "else", "end", "and", "or", "not", "is", "null",
  "true", "false", "in", "like", "ilike", "between", "exists", "distinct",
  "as", "on", "all", "asc", "desc", "over", "partition", "by", "order",
  "coalesce", "cast", "nullif", "ifnull", "iff", "equal_null", "div0",
  "left", "right", "upper", "lower", "trim", "ltrim", "rtrim",
  "regexp_replace", "regexp_substr", "split_part", "concat",
  "sum", "count", "avg", "max", "min", "year", "month", "day", "mod",
  "current_date", "current_timestamp", "dateadd", "datediff", "to_date",
  "to_varchar", "to_number", "row_number", "rank", "dense_rank", "abs",
  "round", "floor", "ceil", "greatest", "least", "zeroifnull", "try_cast",
])

const SAFE_IDENTIFIER = /^[a-zA-Z_][a-zA-Z0-9_]*$/
const RESERVED_ALIASES = new Set([
  ...ALLOWED_KEYWORDS, "select", "from", "where", "group", "having",
  "union", "join", "with", "limit", "table", "into", "values", "using", "qualify",
])

/** Strips single-quoted string literals (handling '' escapes) so their
 *  contents never trigger keyword/identifier checks.
 *  Why this matters: without this step, a harmless string literal like
 *  'DROP THE MIC' would trip the FORBIDDEN_KEYWORDS check below purely
 *  because the word "DROP" appears inside quotes as ordinary text, not as
 *  SQL syntax. Blanking out literal contents (replacing them with a fixed
 *  placeholder) lets the rest of this file reason only about actual SQL
 *  keywords/identifiers, not incidental text sitting inside quotes. */
function stripStringLiterals(sql: string): string {
  return sql.replace(/'(?:[^']|'')*'/g, "''")
}

/** Case-insensitive whole-word search for any keyword in FORBIDDEN_KEYWORDS
 *  (INSERT, DROP, CALL, etc.) — the actual SQL-injection tripwire: an
 *  expression that contains one of these almost certainly isn't a read-only
 *  expression anymore. Returns the first forbidden keyword found, or null. */
function containsForbiddenKeyword(sql: string): string | null {
  for (const kw of FORBIDDEN_KEYWORDS) {
    const re = new RegExp(`\\b${kw}\\b`, "i")
    if (re.test(sql)) return kw
  }
  return null
}

/** Extracts every bare identifier in the expression, INCLUDING ones
 *  immediately followed by `(` (function/UDF calls). Function names must be
 *  checked against the exact same allow-list as column names — skipping
 *  them here would let any function or UDF the role can reach (e.g.
 *  GET_DDL, an external function) be invoked freely, since none of
 *  FORBIDDEN_KEYWORDS' DDL/DML list would catch a read-only-looking call
 *  like that. ALLOWED_KEYWORDS already includes every function this app's
 *  own generated SQL relies on (coalesce, cast, sum, ...), so legitimate
 *  expressions are unaffected — only functions NOT on that list are newly
 *  rejected. */
function extractBareIdentifiers(sql: string): string[] {
  const out: string[] = []
  const re = /[a-zA-Z_][a-zA-Z0-9_]*/g
  let m: RegExpExecArray | null
  while ((m = re.exec(sql))) out.push(m[0])
  return out
}

/**
 * Validates a single custom-logic SQL expression against the shared
 * allow-list policy. `knownColumns` should be the 9 baseline BvA columns
 * plus any already-approved CustomLogicRule aliases (so rules can build on
 * each other) — see lib/config.ts's BVA_BASELINE_COLUMNS.
 *
 * Runs a series of checks, each rejecting a different category of unsafe
 * input, roughly from cheapest/most-obvious to most-thorough:
 *   1. size limit and non-empty
 *   2. no statement separator (";") — must be one expression, not multiple
 *      statements chained together
 *   3. no SQL comments — a comment could otherwise be used to hide/comment
 *      out the rest of a query this expression gets embedded into
 *   4. no forbidden DDL/DML keywords (INSERT, DROP, CALL, ...)
 *   5. every bare identifier (column reference or function/UDF call) must
 *      be on the allow-list — this is what stops an expression from
 *      reading a column it has no business touching, or invoking some
 *      arbitrary function/UDF the role happens to have access to
 *   6. (filters only) the expression must look like a boolean comparison,
 *      not just an arbitrary value
 */
export function validateCustomLogicSql(
  sql: string,
  kind: CustomLogicKind,
  knownColumns: readonly string[],
): GuardResult {
  const trimmed = sql.trim()
  if (!trimmed) return { ok: false, reason: "Expression is empty." }
  if (trimmed.length > MAX_SQL_LENGTH) {
    return { ok: false, reason: `Expression is too long (max ${MAX_SQL_LENGTH} characters).` }
  }

  const withoutStrings = stripStringLiterals(trimmed)

  if (withoutStrings.includes(";")) {
    return { ok: false, reason: "Expression must be a single SQL expression, not a statement — remove the ';'." }
  }
  if (withoutStrings.includes("--") || withoutStrings.includes("/*")) {
    return { ok: false, reason: "SQL comments are not allowed in a custom logic expression." }
  }

  const forbidden = containsForbiddenKeyword(withoutStrings)
  if (forbidden) {
    return { ok: false, reason: `'${forbidden}' is not allowed — custom logic must be a read-only expression.` }
  }

  const allowSet = new Set([...ALLOWED_KEYWORDS, ...knownColumns.map((c) => c.toLowerCase())])
  const identifiers = extractBareIdentifiers(withoutStrings)
  for (const id of identifiers) {
    if (!allowSet.has(id.toLowerCase())) {
      return {
        ok: false,
        reason: `Unknown column or identifier '${id}' — expressions may only reference the baseline BvA columns ` +
          `(${knownColumns.join(", ")}) or other approved custom logic aliases.`,
      }
    }
  }

  if (kind === "filter") {
    // A filter rule should read like a boolean predicate, not assign anything.
    // Heuristic (not a parser): a filter is only accepted if it contains some
    // recognizable comparison operator/construct (=, <, >, IN, LIKE, IS NULL,
    // BETWEEN, ...). This mainly catches an obvious authoring mistake — e.g.
    // pasting a plain column name or value with no comparison at all — before
    // it gets wired into a WHERE clause where it wouldn't behave as intended.
    if (/=(?!=)/.test(withoutStrings) === false && !/[<>]|is\s+null|is\s+not\s+null|in\s*\(|like|ilike|between/i.test(withoutStrings)) {
      return { ok: false, reason: "A filter expression should be a boolean comparison (e.g. col = 'x', col > 0)." }
    }
  }

  return { ok: true }
}

/** Validates a derived_column rule's alias: must be a safe identifier, not a
 *  reserved keyword, and not one of the 9 baseline column names.
 *  This matters because the alias becomes a real SQL identifier spliced
 *  into a generated SELECT list (see buildFinalSql() in lib/config.ts) — an
 *  unsafe alias could break that query's syntax, and a colliding name could
 *  silently shadow one of the baseline BvA columns downstream. */
export function validateCustomLogicAlias(alias: string, baselineColumns: readonly string[]): GuardResult {
  const trimmed = alias.trim()
  if (!SAFE_IDENTIFIER.test(trimmed)) {
    return { ok: false, reason: "Alias must be a valid identifier (letters, numbers, underscores; can't start with a number)." }
  }
  const lower = trimmed.toLowerCase()
  if (RESERVED_ALIASES.has(lower)) {
    return { ok: false, reason: `'${alias}' is a reserved SQL keyword — choose a different name.` }
  }
  if (baselineColumns.some((c) => c.toLowerCase() === lower)) {
    return { ok: false, reason: `'${alias}' collides with an existing baseline column — choose a different name.` }
  }
  return { ok: true }
}
