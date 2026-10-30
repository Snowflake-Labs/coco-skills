/**
 * Server-only: loadConfig + getConfigTable + invalidateConfigCache.
 * Only import this from API routes and server components — NOT from client components.
 * Client-safe types and buildFromConfig live in lib/config.ts.
 */
import { querySnowflake, querySnowflakeLongRunning } from "@/lib/snowflake"
import { WAREHOUSE } from "@/lib/bva"
import { readConfigPointer } from "@/lib/config-pointer"
import { buildFinalSql } from "@/lib/config"
import type { BvaConfig } from "@/lib/config"

/**
 * Resolve the fully-qualified config table name.
 *
 * Priority:
 *   1. BVA_CONFIG_TABLE env var (deployment-time override — set in app.yml)
 *   2. @~/bva_app_pointer.json on the user stage (set via the location picker in Step 1)
 *
 * Throws a clear error if neither is set, rather than silently guessing a
 * database/schema — there's no safe generic default location to create a
 * table in on a fresh deployment; the deploying operator must set one.
 *
 * The location-picker path (#2) has always self-provisioned its tables via
 * CREATE TABLE IF NOT EXISTS. The env-var path (#1) previously did not —
 * a deployment that set BVA_CONFIG_TABLE (as this skill's deploy flow does)
 * had no code path that ever created that table, so config/history/commentary
 * writes failed with "does not exist" on a genuinely fresh deployment. This
 * self-heals that: the first call per process bootstraps the env-var-named
 * tables too, using the same schema as the location picker.
 */
interface TableCache { table: string; fetchedAt: number }
let _tableCache: TableCache | null = null
let _envTablesBootstrapped = false

/**
 * Creates the three tables a BvA app deployment needs (config, config
 * history, commentary) — idempotent, via CREATE TABLE IF NOT EXISTS, so it's
 * safe to call on every request. Shared by the two places a config location
 * gets provisioned: the env-var path below (bootstraps once per process the
 * first time getConfigTable() resolves BVA_CONFIG_TABLE), and the location
 * picker in Settings → Step 1, which calls this directly with the db/schema
 * the user just chose (see app/api/setup/config-location/route.ts). History
 * and commentary table names default to the usual derived names, but can be
 * overridden — the location picker passes its own names explicitly so its
 * behavior doesn't shift if BVA_HISTORY_TABLE/BVA_COMMENTARY_TABLE happen to
 * be set as env vars for a different (env-var-configured) deployment.
 */
export async function ensureBvaAppTables(
  configTable: string,
  historyTable: string = getHistoryTable(configTable),
  commentaryTable: string = getCommentaryTable(configTable),
): Promise<void> {
  // CREATE_CONFIG_TABLE: the table that holds every saved BvA config as a
  // row of JSON (config_json), one row per named config ("default" plus any
  // saved profiles). Idempotent — safe to call on every request.
  await querySnowflake(
    `CREATE TABLE IF NOT EXISTS ${configTable} (
      config_name  VARCHAR(255) NOT NULL,
      config_json  VARIANT      NOT NULL,
      updated_at   TIMESTAMP_TZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
      CONSTRAINT bva_app_config_pk UNIQUE (config_name)
    )`,
    { warehouse: WAREHOUSE },
  )
  // CREATE_HISTORY_TABLE: an append-only log of every config_json that was
  // ever saved for a given config_name, written alongside (not instead of)
  // the config table so past versions can be recovered/audited later.
  await querySnowflake(
    `CREATE TABLE IF NOT EXISTS ${historyTable} (
      saved_at    TIMESTAMP_LTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
      config_name VARCHAR(255)  NOT NULL,
      config_json VARIANT       NOT NULL
    )`,
    { warehouse: WAREHOUSE },
  )
  // CREATE_COMMENTARY_TABLE: stores free-text commentary a user (or the AI
  // commentary feature) attaches to one category/fiscal_year/period cell in
  // the BvA table — the unique constraint means there's at most one
  // commentary row per cell per config, so saving again updates it in place.
  await querySnowflake(
    `CREATE TABLE IF NOT EXISTS ${commentaryTable} (
      config_name     VARCHAR(255)  NOT NULL,
      category        VARCHAR(500)  NOT NULL,
      fiscal_year     VARCHAR(50)   NOT NULL,
      period          VARCHAR(50)   NOT NULL,
      commentary_text VARCHAR(4000),
      source          VARCHAR(20)   DEFAULT 'MANUAL',
      updated_by      VARCHAR(255),
      updated_at      TIMESTAMP_TZ  NOT NULL DEFAULT CURRENT_TIMESTAMP(),
      CONSTRAINT bva_commentary_pk UNIQUE (config_name, category, fiscal_year, period)
    )`,
    { warehouse: WAREHOUSE },
  )
  // Self-heal a commentary table that already existed before the `source`
  // column was added (e.g. an earlier deployment) — CREATE TABLE IF NOT
  // EXISTS above is a no-op against it, so add the column separately.
  await querySnowflake(
    `ALTER TABLE ${commentaryTable} ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'MANUAL'`,
    { warehouse: WAREHOUSE },
  )
}

export async function getConfigTable(): Promise<string> {
  if (process.env.BVA_CONFIG_TABLE) {
    const table = process.env.BVA_CONFIG_TABLE
    if (!_envTablesBootstrapped) {
      _envTablesBootstrapped = true // set before awaiting: one attempt per process, even under concurrent requests
      try {
        await ensureBvaAppTables(table)
      } catch {
        _envTablesBootstrapped = false // let a later call retry (e.g. privilege was just granted)
      }
    }
    return table
  }
  if (_tableCache && Date.now() - _tableCache.fetchedAt < 60_000) return _tableCache.table
  const pointer = await readConfigPointer()
  if (!pointer?.configTable) {
    throw new Error(
      "No config table location set. Set the BVA_CONFIG_TABLE environment variable " +
      "in app.yml, or use the location picker in Settings → Step 1 → Config storage."
    )
  }
  const table = pointer.configTable
  _tableCache = { table, fetchedAt: Date.now() }
  return table
}

/** Invalidate the table-location cache (call after writing a new pointer). */
export function invalidateTableCache(): void {
  _tableCache = null
}

// ── Config data cache (1 minute TTL, keyed by config_name) ─────────────────
//
// SECURITY INVARIANT: every query this file caches (here and in
// materializedReady()/getConfigTable() below) must be run WITHOUT
// { callersRights: true } — i.e. only ever under the app's owner role,
// which is the same for every viewer. If a future change ever needs to
// cache the result of a caller's-rights query, it must be kept in a
// structure keyed by caller identity (see lib/snowflake.ts's callersPool,
// or lib/job-store.ts's per-job Map, for the correct pattern) — a single
// shared slot filled by whichever viewer's request runs first would leak
// that viewer's data to every other viewer.

interface ConfigCache { config: BvaConfig | null; fetchedAt: number }
const _configCacheMap = new Map<string, ConfigCache>()
const CACHE_TTL_MS = 60_000

/**
 * Load a config by name (defaults to 'default' — the active config).
 * Results are cached per name for 60 seconds.
 */
export async function loadConfig(name = "default"): Promise<BvaConfig | null> {
  const cached = _configCacheMap.get(name)
  if (cached && Date.now() - cached.fetchedAt < CACHE_TTL_MS) return cached.config
  try {
    const table = await getConfigTable()
    // READ_CONFIG_BY_NAME: fetches the saved JSON blob for one named config
    // (usually "default", the config actively driving the dashboard). This
    // result is what gets cached in _configCacheMap below for 60 seconds.
    const rows  = await querySnowflake(
      `select config_json from ${table} where config_name = '${name.replace(/'/g, "''")}'  limit 1`,
      { warehouse: WAREHOUSE },
    )
    const config = rows.length > 0 ? (rows[0].CONFIG_JSON as BvaConfig) : null
    _configCacheMap.set(name, { config, fetchedAt: Date.now() })
    return config
  } catch {
    _configCacheMap.set(name, { config: null, fetchedAt: Date.now() })
    return null
  }
}

/**
 * Invalidate the config cache.
 * Pass a specific name to clear only that entry, or call with no args to clear all.
 */
export function invalidateConfigCache(name?: string): void {
  if (name) _configCacheMap.delete(name)
  else      _configCacheMap.clear()
}

// ── Materialized BvA table ────────────────────────────────────────────────────
//
// The dashboard's runtime queries (/api/bva, /api/breakdown, /api/filters) wrap
// buildFromConfig() as a subquery and filter on top — so every filter change
// recomputes the full multi-million-row GL aggregation + join. Materializing the
// pipeline output ONCE (on config save) into a small table turns each dashboard
// query into a scan of a few thousand grouped rows. Same SQL, same numbers —
// only tradeoff is staleness until the next refresh.

/**
 * Resolve the materialized table name.
 *   1. BVA_MATERIALIZED_TABLE env var (deployment-time override — required when
 *      the role can't CREATE TABLE in the config table's schema).
 *   2. Default: BVA_MATERIALIZED in the config table's schema.
 */
export function getMaterializedTable(configTable: string): string {
  if (process.env.BVA_MATERIALIZED_TABLE) return process.env.BVA_MATERIALIZED_TABLE
  return configTable.replace(/BVA_APP_CONFIG$/i, "BVA_MATERIALIZED")
}

/** Resolve the commentary table (same schema as config table). */
export function getCommentaryTable(configTable: string): string {
  if (process.env.BVA_COMMENTARY_TABLE) return process.env.BVA_COMMENTARY_TABLE
  return configTable.replace(/BVA_APP_CONFIG$/i, "BVA_COMMENTARY")
}

/**
 * Resolve the config history table.
 *   1. BVA_HISTORY_TABLE env var (deployment-time override — required whenever
 *      the config table isn't literally named "..._APP_CONFIG"/"BVA_APP_CONFIG",
 *      since the fallback below only matches that exact suffix).
 *   2. Default: derived from the config table name by appending "_HISTORY".
 */
export function getHistoryTable(configTable: string): string {
  if (process.env.BVA_HISTORY_TABLE) return process.env.BVA_HISTORY_TABLE
  return `${configTable}_HISTORY`
}

// In-process cache of whether the materialized table exists (avoids a metadata
// round-trip on every getBvaFrom call). Invalidated on save/delete.
interface MatCache { table: string; ready: boolean; fetchedAt: number }
let _matCache: MatCache | null = null
const MAT_CACHE_TTL_MS = 60_000

export function invalidateMaterializedCache(): void {
  _matCache = null
}

/** True when the materialized table exists and is queryable (cached 60s). */
export async function materializedReady(matTable: string): Promise<boolean> {
  if (_matCache && _matCache.table === matTable && Date.now() - _matCache.fetchedAt < MAT_CACHE_TTL_MS) {
    return _matCache.ready
  }
  let ready = false
  try {
    // PROBE_TABLE_EXISTS: `where 1=0` means this never scans real data — it's
    // purely a cheap way to ask Snowflake "does this table exist and can I
    // query it right now" and let a thrown error mean "no".
    await querySnowflake(`select 1 from ${matTable} where 1=0`, { warehouse: WAREHOUSE })
    ready = true
  } catch {
    ready = false
  }
  _matCache = { table: matTable, ready, fetchedAt: Date.now() }
  return ready
}

/**
 * Build (or rebuild) the materialized BvA table from a multi_table config.
 * No-op for single_table configs (they already point at a table/view — nothing
 * to precompute). Uses the long-running query path since the source aggregation
 * can be heavy. Throws on failure so callers can decide whether to surface it;
 * getBvaFrom falls back to the inline pipeline if the table is missing.
 */
export async function materializeBva(cfg: BvaConfig): Promise<string | null> {
  // Mirror buildFromConfig's branch: the multi-table pipeline runs whenever the
  // source is not explicitly single_table AND an actuals table is set (sourceType
  // is often left undefined on saved configs).
  if (cfg.sourceType === "single_table" || !cfg.actualsTable) return null
  const configTable = await getConfigTable()
  const matTable = getMaterializedTable(configTable)
  // buildFinalSql() layers any enabled CustomLogicRule entries on top of the
  // baseline buildFromConfig() output — see lib/config.ts. When there are no
  // enabled rules it returns the baseline verbatim, so this is a strict
  // superset of the old behavior.
  const sql = buildFinalSql(cfg)
  // MATERIALIZE_BVA_TABLE: runs the full multi-table BvA aggregation once and
  // persists the result as a small table, so dashboard reads scan thousands
  // of grouped rows instead of re-running the whole GL pipeline on every
  // filter change (see the "Materialized BvA table" note above). Uses the
  // long-running query path because this aggregation can take a while.
  await querySnowflakeLongRunning(
    `create or replace table ${matTable} as ${sql}`,
    { warehouse: WAREHOUSE, maxWaitMs: 280_000, tag: "bva:materialize" },
  )
  invalidateMaterializedCache()
  await updateVersionPeriods(cfg, configTable, matTable)
  return matTable
}

/**
 * Compute each planning_version's actual fiscal-year/quarter coverage from the
 * freshly materialized table, and merge any NEW versions into the config's
 * versionPeriods map. Existing entries — including user overrides made in the
 * Fiscal Config step — are never overwritten; this is intentionally a
 * fill-the-gaps merge, not a replace.
 *
 * Why this exists: version NAMES are often forecast-vintage labels ("Q1
 * Forecast" = the forecast produced at the Q1 checkpoint), not period-scope
 * labels — e.g. in the BvA demo data, "Q1 Forecast" spans 9 quarters. Deriving
 * a filter reset from the name (regex) would silently hide most of that
 * version's data. Real coverage has to come from the actual rows.
 *
 * Best-effort: failures here must never fail the save/materialize flow.
 */
async function updateVersionPeriods(cfg: BvaConfig, configTable: string, matTable: string): Promise<void> {
  try {
    // READ_VERSION_PERIOD_COVERAGE: for each planning_version present in the
    // freshly materialized table, collects the distinct fiscal years and
    // quarters that version actually has rows for. This is the real,
    // data-derived answer to "what period does this version cover" — see the
    // function doc above for why that beats parsing the version's name.
    const rows = await querySnowflake(
      `select planning_version,
              array_agg(distinct fiscal_year)    within group (order by fiscal_year)    as years,
              array_agg(distinct fiscal_quarter) within group (order by fiscal_quarter) as quarters
       from ${matTable}
       where planning_version is not null
       group by planning_version`,
      { warehouse: WAREHOUSE },
    )

    const existing = { ...(cfg.versionPeriods ?? {}) }
    let changed = false
    for (const r of rows) {
      const version = String(r.PLANNING_VERSION)
      if (existing[version]) continue // never clobber an existing (auto or user-edited) entry
      existing[version] = {
        years: toStringArray(r.YEARS),
        quarters: toStringArray(r.QUARTERS),
      }
      changed = true
    }
    if (!changed) return

    const configName = cfg.configName ?? "default"
    const jsonLiteral = JSON.stringify(existing).replace(/'/g, "''")
    // WRITE_VERSION_PERIODS_PATCH: merges the newly-computed versionPeriods
    // map into the saved config's JSON in place, using object_insert so only
    // that one key changes — the rest of config_json (including any fields
    // this process doesn't know about) passes through untouched.
    await querySnowflake(
      `update ${configTable}
       set config_json = object_insert(config_json, 'versionPeriods', parse_json('${jsonLiteral}'), true)
       where config_name = '${configName.replace(/'/g, "''")}'`,
      { warehouse: WAREHOUSE },
    )
    invalidateConfigCache(configName)
  } catch (e) {
    console.error("[config-server] updateVersionPeriods failed", e)
    // best-effort — don't fail the save/materialize flow over this
  }
}

/** ARRAY_AGG results come back as native JS arrays via the Snowflake driver;
 *  defensively handle a JSON-string form too in case that ever changes. */
function toStringArray(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String)
  if (typeof v === "string") {
    try {
      const parsed = JSON.parse(v)
      if (Array.isArray(parsed)) return parsed.map(String)
    } catch {
      // fall through
    }
  }
  return []
}

/** Best-effort drop of the materialized table (called on config reset). */
export async function dropMaterializedBva(): Promise<void> {
  try {
    const configTable = await getConfigTable()
    const matTable = getMaterializedTable(configTable)
    // DROP_MATERIALIZED_TABLE: removes the precomputed BvA table on config
    // reset, so a stale materialization can't be served against a config
    // that no longer matches it. `if exists` makes this safe even if the
    // table was never built.
    await querySnowflake(`drop table if exists ${matTable}`, { warehouse: WAREHOUSE })
  } catch {
    // best-effort
  }
  invalidateMaterializedCache()
}
