/**
 * Admin-only control for the setup wizard's discovery-scan scope override.
 * Lets an admin pre-restrict the account-wide table discovery scan (normally
 * a slow SHOW DATABASES -> INFORMATION_SCHEMA scan across ~20 databases, see
 * lib/discover-bundle.ts) down to a single database/schema — e.g. for demos
 * where sitting through a multi-minute full scan live isn't practical.
 * See lib/discovery-scope-mode.ts for the full rationale and why this lives
 * in its own dedicated table/route rather than as a BvaConfig field.
 *
 * GET  — { enabled, database, schema, isAdmin }. isAdmin here is informational
 *        for the UI only (the real check happens again on every POST).
 * POST — { enabled, database, schema }. Re-checks checkIsAdmin() server-side.
 */
import { checkIsAdmin } from "@/lib/agent-access-mode"
import { getDiscoveryScopeSetting, setDiscoveryScopeOverride } from "@/lib/discovery-scope-mode"

export const dynamic = "force-dynamic"

export async function GET() {
  const [setting, admin] = await Promise.all([getDiscoveryScopeSetting(), checkIsAdmin()])
  return Response.json({ ...setting, isAdmin: admin.isAdmin })
}

export async function POST(req: Request) {
  const admin = await checkIsAdmin()
  if (!admin.isAdmin) {
    return Response.json({ error: "Admin only" }, { status: 403 })
  }

  let body: { enabled?: unknown; database?: unknown; schema?: unknown }
  try {
    body = await req.json()
  } catch {
    return Response.json({ error: "Invalid JSON body" }, { status: 400 })
  }

  const enabled = body.enabled === true
  const database = typeof body.database === "string" ? body.database.trim() : ""
  const schema = typeof body.schema === "string" ? body.schema.trim() : ""
  if (enabled && !database) {
    return Response.json({ error: "database is required when enabling" }, { status: 400 })
  }

  await setDiscoveryScopeOverride(enabled, database, schema, admin.viewerRole ?? "unknown")
  return Response.json({ enabled, database, schema })
}
