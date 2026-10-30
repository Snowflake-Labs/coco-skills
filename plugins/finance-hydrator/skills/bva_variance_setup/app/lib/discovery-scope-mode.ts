/**
 * Admin-gated override that restricts the setup wizard's account discovery
 * scan (buildDiscoveryBundle in lib/discover-bundle.ts) to one database/schema
 * instead of the full account-wide scan.
 *
 * Why this exists: the unscoped scan is deliberately thorough (SHOW DATABASES
 * -> ~20 keyword-matched databases -> parallel INFORMATION_SCHEMA.TABLES scan)
 * and can take several minutes on a large real account — fine for an actual
 * customer onboarding, but not something you can sit through live in front of
 * an audience. buildDiscoveryBundle() already supports a scoped, single-pass
 * scan (see DiscoveryScope) — today it's only reached when a user types an
 * explicit "check DB.SCHEMA" in chat (parseDiscoveryScope). This module lets
 * an admin pre-set that same scope so it applies automatically, without
 * requiring anyone to type it, and without any "demo mode" language surfacing
 * anywhere a viewer/audience would see it — the resulting scan message is the
 * exact same neutral "Scoped scan complete" copy as a user-typed scope.
 *
 * Deliberately a separate table from BvaConfig, same reasoning as
 * agent-access-mode.ts: admin-only state that must never be settable through
 * the ungated /api/setup/config route or slip in via an agent-proposed config
 * blob.
 */
import { querySnowflake } from "@/lib/snowflake"
import { WAREHOUSE } from "@/lib/bva"
import { getConfigTable } from "@/lib/config-server"
import type { DiscoveryScope } from "@/lib/discover-bundle"

export type { DiscoveryScope }

function q(s: string): string {
  return "'" + s.replace(/'/g, "''") + "'"
}

/**
 * Resolve the mode table (same schema/safety-guard reasoning as
 * lib/agent-access-mode.ts's getModeTable). Guards against an overly specific
 * regex that fails to match: it would silently alias this table to the config
 * table, and the DELETE below would then remove saved config rows.
 */
function getModeTable(configTable: string): string {
  const table = process.env.BVA_DISCOVERY_SCOPE_TABLE
    ?? configTable.replace(/_CONFIG$/i, "_DISCOVERY_SCOPE")
  if (table === configTable) {
    throw new Error(
      `Refusing to use the discovery-scope table: it resolved to the same table as the config ` +
      `table ("${configTable}"). Set BVA_DISCOVERY_SCOPE_TABLE explicitly in app.yml.`
    )
  }
  return table
}

async function ensureModeTable(table: string): Promise<void> {
  // Create-table-if-missing query: lets this module create its own settings
  // table lazily on first use, instead of requiring a separate migration step.
  // Safe to call on every access since CREATE TABLE IF NOT EXISTS is a no-op
  // once the table is already there.
  await querySnowflake(
    `CREATE TABLE IF NOT EXISTS ${table} (
      enabled     BOOLEAN      NOT NULL DEFAULT FALSE,
      database    VARCHAR(255),
      schema      VARCHAR(255),
      updated_at  TIMESTAMP_TZ,
      updated_by  VARCHAR(255)
    )`,
    { warehouse: WAREHOUSE },
  )
}

interface ScopeCache { scope: DiscoveryScope | null; fetchedAt: number }
let _scopeCache: ScopeCache | null = null
const SCOPE_CACHE_TTL_MS = 15_000

/**
 * Resolve the settings table and read back its one row. Shared by both
 * public functions below so there's a single place that knows how to find
 * and read this table, instead of two copies of the same three steps
 * (resolve table name -> make sure it exists -> SELECT it).
 */
async function fetchScopeRow(): Promise<{ ENABLED?: boolean; DATABASE?: string; SCHEMA?: string } | undefined> {
  const configTable = await getConfigTable()
  const table = getModeTable(configTable)
  await ensureModeTable(table)
  // Read-the-one-settings-row query: this table is designed to hold at most a
  // single row, so LIMIT 1 is just a safety net rather than a meaningful filter.
  const rows = await querySnowflake(`SELECT enabled, database, schema FROM ${table} LIMIT 1`, { warehouse: WAREHOUSE })
  return rows[0]
}

/** Active override scope, or null if disabled/unset. Fails safe to null (full scan). */
export async function getDiscoveryScopeOverride(): Promise<DiscoveryScope | null> {
  if (_scopeCache && Date.now() - _scopeCache.fetchedAt < SCOPE_CACHE_TTL_MS) return _scopeCache.scope
  let scope: DiscoveryScope | null = null
  try {
    const row = await fetchScopeRow()
    if (row?.ENABLED === true && row?.DATABASE) {
      scope = { database: String(row.DATABASE), schema: row.SCHEMA ? String(row.SCHEMA) : undefined }
    }
  } catch {
    scope = null
  }
  _scopeCache = { scope, fetchedAt: Date.now() }
  return scope
}

/** Read the raw row for the Settings UI (admin already verified by the caller). */
export async function getDiscoveryScopeSetting(): Promise<{ enabled: boolean; database: string; schema: string }> {
  try {
    const row = await fetchScopeRow()
    return {
      enabled: row?.ENABLED === true,
      database: row?.DATABASE ? String(row.DATABASE) : "",
      schema: row?.SCHEMA ? String(row.SCHEMA) : "",
    }
  } catch {
    return { enabled: false, database: "", schema: "" }
  }
}

/** Set the override. Caller MUST have already verified admin via checkIsAdmin(). */
export async function setDiscoveryScopeOverride(
  enabled: boolean,
  database: string,
  schema: string,
  updatedBy: string,
): Promise<void> {
  const configTable = await getConfigTable()
  const table = getModeTable(configTable)
  await ensureModeTable(table)
  // Upsert-the-one-settings-row query: MERGE ... ON TRUE always matches
  // whatever single row already exists in this table (updating it in place),
  // and falls through to the INSERT branch only on the very first save when
  // the table is still empty.
  await querySnowflake(
    `MERGE INTO ${table} AS tgt
   USING (SELECT ${enabled} AS enabled, ${database ? q(database) : "NULL"} AS database, ${schema ? q(schema) : "NULL"} AS schema, CURRENT_TIMESTAMP() AS updated_at, ${q(updatedBy)} AS updated_by) AS src
   ON TRUE
   WHEN MATCHED THEN
     UPDATE SET tgt.enabled = src.enabled, tgt.database = src.database, tgt.schema = src.schema, tgt.updated_at = src.updated_at, tgt.updated_by = src.updated_by
   WHEN NOT MATCHED THEN
     INSERT (enabled, database, schema, updated_at, updated_by)
     VALUES (src.enabled, src.database, src.schema, src.updated_at, src.updated_by)`,
    { warehouse: WAREHOUSE },
  )
  _scopeCache = null
}
