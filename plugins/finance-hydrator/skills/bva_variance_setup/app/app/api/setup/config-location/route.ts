import { querySnowflake } from "@/lib/snowflake"
import { WAREHOUSE } from "@/lib/bva"
import { readConfigPointer, writeConfigPointer } from "@/lib/config-pointer"
import { getConfigTable, invalidateTableCache, invalidateConfigCache, ensureBvaAppTables } from "@/lib/config-server"
import { checkIsAdmin } from "@/lib/agent-access-mode"

export const dynamic = "force-dynamic"

const GENERIC_ERROR = "An internal error occurred."
// Standard unquoted Snowflake identifier. db/schema here name a deployment
// location the admin is creating tables in; constraining to this both prevents
// injection via the raw fqTable pointer / grant hint and matches normal usage.
const IDENT = /^[A-Za-z_][A-Za-z0-9_$]*$/

function q(s: string) { return `"${s.replace(/"/g, '""')}"` }

// Recognizes Snowflake's various phrasings for "your role can't do this" so the
// POST handler can offer a targeted GRANT statement instead of a raw error dump.
function isPrivilegeError(e: unknown): boolean {
  const msg = e instanceof Error ? e.message : String(e)
  return (
    msg.includes("Insufficient privileges") ||
    msg.includes("ACCESS_CONTROL") ||
    msg.includes("insufficient privileges") ||
    msg.includes("does not have privilege")
  )
}

// Snowflake's grant error names the role that was missing the privilege
// (e.g. "role MY_ROLE must have ..."). Pull it out so the generated GRANT
// statement targets the right role instead of a placeholder.
function extractRoleFromError(msg: string): string {
  const m = msg.match(/role\s+([\w.$"]+)\s+must\s+have/i)
  return m?.[1] ?? "<your-role>"
}

// ── GET: return current config table location ─────────────────────────────────

export async function GET() {
  try {
    const pointer     = await readConfigPointer()
    const configTable = await getConfigTable()

    // Probe whether the table actually exists at that location
    let tableExists = false
    try {
      await querySnowflake(`SELECT 1 FROM ${configTable} WHERE 1=0`, { warehouse: WAREHOUSE })
      tableExists = true
    } catch {
      tableExists = false
    }

    return Response.json({ configTable, pointer, tableExists })
  } catch (e) {
    console.error("[api/setup/config-location GET]", e)
    return Response.json({ error: GENERIC_ERROR }, { status: 500 })
  }
}

// ── POST: create config tables at user-specified DB.SCHEMA ────────────────────

export async function POST(req: Request) {
  const admin = await checkIsAdmin()
  if (!admin.isAdmin) {
    return Response.json({ error: "Admin only" }, { status: 403 })
  }
  // Declared outside the try block so the catch handler below can still
  // reference the target db/schema when building the GRANT hint.
  let db = ""
  let schema = ""
  try {
    const body = (await req.json()) as { db?: string; schema?: string }
    db     = (body.db     ?? "").trim()
    schema = (body.schema ?? "").trim()

    if (!db || !schema) {
      return Response.json({ error: "db and schema are required" }, { status: 400 })
    }
    if (!IDENT.test(db) || !IDENT.test(schema)) {
      return Response.json({ error: "db and schema must be valid identifiers (letters, digits, underscore, $; cannot start with a digit)." }, { status: 400 })
    }

    const configTable      = `${q(db)}.${q(schema)}.BVA_APP_CONFIG`
    const historyTable     = `${q(db)}.${q(schema)}.BVA_APP_CONFIG_HISTORY`
    const commentaryTable  = `${q(db)}.${q(schema)}.BVA_COMMENTARY`

    await ensureBvaAppTables(configTable, historyTable, commentaryTable)

    // Write pointer to @~ so all future reads use this location
    const fqTable = `${db}.${schema}.BVA_APP_CONFIG`
    await writeConfigPointer({ configTable: fqTable })
    invalidateTableCache()
    invalidateConfigCache()

    return Response.json({ ok: true, configTable: fqTable })
  } catch (e) {
    const msg    = e instanceof Error ? e.message : String(e)
    const isPriv = isPrivilegeError(e)
    const role   = extractRoleFromError(msg)
    // When the failure was a missing privilege, hand the admin a copy-pasteable
    // fix instead of just surfacing the raw Snowflake error.
    const grantSql = isPriv
      ? `GRANT CREATE TABLE ON SCHEMA ${db}.${schema} TO ROLE ${role};`
      : null
    console.error("[api/setup/config-location POST]", e)
    const clientError = isPriv
      ? "Insufficient privileges to create the config tables at that location."
      : GENERIC_ERROR
    return Response.json({ error: clientError, privilegeError: isPriv, grantSql }, { status: 500 })
  }
}
