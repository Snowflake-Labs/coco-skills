/**
 * Admin-gated switch between caller's rights (per-viewer scoping) and
 * owner's rights (app-role scoping) for the embedded Cortex Agent.
 *
 * Deliberately NOT a field on BvaConfig and NOT a row in the BVA_APP_CONFIG
 * table used by app/api/setup/config/route.ts:
 *   1. That route's writes (POST/DELETE) are admin-gated, but its GET is
 *      open to any signed-in viewer by design — the dashboard needs to read
 *      the active config to render at all. Storing the mode there would let
 *      anyone read the current mode via that same open GET, and — if the
 *      write gate were ever loosened or bypassed — bypass the admin check
 *      in app/api/agent-access-mode by just POSTing directly to
 *      /api/setup/config with the right name.
 *   2. BvaConfig round-trips through the setup agent's own JSON proposal
 *      (onConfigAccepted) — an indirect prompt injection (e.g. adversarial
 *      text in a scanned table/column comment) could in principle get the
 *      agent to slip a mode change into a large proposed config blob that a
 *      human skims and approves without noticing one field.
 * This module owns its own single-row table instead, touched only by
 * getAgentAccessMode/setAgentAccessMode and the dedicated API route — no
 * shared code path with the general config mechanism.
 *
 * Default is "caller" whenever the table/row doesn't exist yet, matching the
 * app's behavior before this toggle existed: caller's rights is the safe,
 * secure-by-default posture; owner's rights requires an explicit admin
 * action to opt into.
 */

import { headers } from "next/headers"
import { querySnowflake } from "@/lib/snowflake"
import { WAREHOUSE } from "@/lib/bva"
import { getConfigTable } from "@/lib/config-server"

export type AgentAccessMode = "caller" | "owner"

function q(s: string): string {
  return "'" + s.replace(/'/g, "''") + "'"
}

/**
 * Resolve the mode table (same schema as the config table).
 *
 * SAFETY: this MUST resolve to a table distinct from the config table — a
 * regex that fails to match (e.g. because a deployment's actual config table
 * doesn't end in the exact literal this was written against) silently
 * returns the config table unchanged, and setAgentAccessMode()'s
 * `DELETE FROM <table>` then wipes every saved config with no error — e.g. a
 * config table ending in "..._CONFIG" would not match an overly specific
 * literal like `/BVA_APP_CONFIG$/i`. Guarded by matching the generic "_CONFIG"
 * suffix instead of a literal, AND by throwing here (before any query touches Snowflake) if the
 * result would ever equal configTable again, rather than silently aliasing.
 */
function getModeTable(configTable: string): string {
  const table = process.env.BVA_AGENT_ACCESS_MODE_TABLE
    ?? configTable.replace(/_CONFIG$/i, "_AGENT_ACCESS_MODE")
  if (table === configTable) {
    throw new Error(
      `Refusing to use the agent-access-mode table: it resolved to the same table as the config ` +
      `table ("${configTable}"). Set BVA_AGENT_ACCESS_MODE_TABLE explicitly in app.yml.`
    )
  }
  return table
}

async function ensureModeTable(table: string): Promise<void> {
  // CREATE_MODE_TABLE: idempotent bootstrap for the single-row table that
  // stores this app's caller-vs-owner toggle. Safe to call on every read/write
  // since IF NOT EXISTS makes it a no-op once the table is already there.
  await querySnowflake(
    `CREATE TABLE IF NOT EXISTS ${table} (
      mode        VARCHAR(10)  NOT NULL DEFAULT 'caller',
      updated_at  TIMESTAMP_TZ,
      updated_by  VARCHAR(255)
    )`,
    { warehouse: WAREHOUSE },
  )
}

// ── Mode read/write (short-TTL cache, same shape as config-server.ts's loadConfig) ──

interface ModeCache { mode: AgentAccessMode; fetchedAt: number }
let _modeCache: ModeCache | null = null
const MODE_CACHE_TTL_MS = 15_000

/** Read the current mode. Defaults to "caller" if the table/row doesn't exist yet. */
export async function getAgentAccessMode(): Promise<AgentAccessMode> {
  if (_modeCache && Date.now() - _modeCache.fetchedAt < MODE_CACHE_TTL_MS) return _modeCache.mode
  let mode: AgentAccessMode = "caller"
  try {
    const configTable = await getConfigTable()
    const table = getModeTable(configTable)
    await ensureModeTable(table)
    // READ_CURRENT_MODE: the table only ever holds one row (see
    // setAgentAccessMode's MERGE below), so LIMIT 1 just guards against any
    // unexpected extra rows rather than being a meaningful filter.
    const rows = await querySnowflake(`SELECT mode FROM ${table} LIMIT 1`, { warehouse: WAREHOUSE })
    if (rows.length > 0 && rows[0].MODE === "owner") mode = "owner"
  } catch {
    // No config table location set yet, or a transient failure — fall back
    // to the safe default rather than throwing and breaking every agent turn.
    mode = "caller"
  }
  _modeCache = { mode, fetchedAt: Date.now() }
  return mode
}

/** Set the mode. Callers MUST have already verified admin via checkIsAdmin(). */
export async function setAgentAccessMode(mode: AgentAccessMode, updatedBy: string): Promise<void> {
  const configTable = await getConfigTable()
  const table = getModeTable(configTable)
  await ensureModeTable(table)
  // UPSERT_MODE: the table is meant to hold exactly one row for the whole
  // app, so this MERGE ON TRUE unconditionally matches (and updates) that
  // single existing row, or inserts it the first time the mode is ever set.
  await querySnowflake(
    `MERGE INTO ${table} AS tgt
   USING (SELECT ${q(mode)} AS mode, CURRENT_TIMESTAMP() AS updated_at, ${q(updatedBy)} AS updated_by) AS src
   ON TRUE
   WHEN MATCHED THEN
     UPDATE SET tgt.mode = src.mode, tgt.updated_at = src.updated_at, tgt.updated_by = src.updated_by
   WHEN NOT MATCHED THEN
     INSERT (mode, updated_at, updated_by)
     VALUES (src.mode, src.updated_at, src.updated_by)`,
    { warehouse: WAREHOUSE },
  )
  _modeCache = { mode, fetchedAt: Date.now() }
}

// ── Admin check ──────────────────────────────────────────────────────────────
//
// Admin = the viewer's own role IS the app's owner role, or is a member of it
// (Snowflake role hierarchy) — no separate allowlist to configure. Uses the
// same querySnowflake({ callersRights: true }) mechanism as every other
// caller's-rights query in this app (see lib/snowflake.ts).

let _ownerRoleCache: string | null = null

/** The role the SPCS service itself runs as (service token, no caller). */
async function getOwnerRoleName(): Promise<string> {
  if (_ownerRoleCache) return _ownerRoleCache
  // READ_OWNER_ROLE: runs under the app's own service identity (no caller
  // context, no `callersRights` flag), so CURRENT_ROLE() here always
  // returns the fixed role the app was deployed to run as — this is the
  // "owner" role that checkIsAdmin() below compares each viewer against.
  const rows = await querySnowflake(`SELECT CURRENT_ROLE() AS ROLE`, { warehouse: WAREHOUSE })
  const role = rows[0]?.ROLE
  if (typeof role !== "string" || !role) throw new Error("Could not resolve the app's owner role")
  _ownerRoleCache = role
  return role
}

/**
 * True when the signed-in viewer's own role is (or is a member of) the app's
 * owner role. Fails closed: any error (no caller header present, local dev,
 * a transient query failure) resolves to isAdmin: false, never true.
 */
export async function checkIsAdmin(): Promise<{ isAdmin: boolean; viewerRole?: string }> {
  try {
    const ownerRole = await getOwnerRoleName()
    const callerToken = (await headers()).get("sf-context-current-user-token") ?? ""
    if (!callerToken) return { isAdmin: false }
    // CHECK_VIEWER_IS_OWNER_ROLE: asks Snowflake, as the viewer, whether their
    // session role is (or inherits from) the app's owner role — that's what
    // "admin" means here. Deliberately no `warehouse:` override here (unlike every other query in
    // this file) — this is a caller's-rights query, and QUERY_WAREHOUSE_NAME
    // forces Snowflake to validate/resume that warehouse under the VIEWER's
    // combined-token session, not the app's owner role. IS_ROLE_IN_SESSION()/
    // CURRENT_ROLE() is metadata-only and needs no compute; forcing the app's
    // own warehouse here would require every viewer's own role to separately
    // have USAGE on it (unlikely — warehouse grants aren't part of what
    // GRANT CALLER typically re-grants), causing a silent submission failure
    // for any viewer whose role lacks that grant — omit `warehouse` on any
    // caller's-rights query unless the query itself needs compute.
    const rows = await querySnowflake(
      `SELECT IS_ROLE_IN_SESSION(${q(ownerRole)}) AS IS_ADMIN, CURRENT_ROLE() AS VIEWER_ROLE`,
      { callersRights: true },
    )
    const row = rows[0]
    return { isAdmin: row?.IS_ADMIN === true, viewerRole: row?.VIEWER_ROLE }
  } catch {
    return { isAdmin: false }
  }
}
