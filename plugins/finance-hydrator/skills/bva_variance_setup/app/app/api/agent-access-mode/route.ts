/**
 * Admin-only control for whether the embedded Cortex Agent runs under
 * caller's rights (per-viewer scoping, the default) or the app's owner
 * role. See lib/agent-access-mode.ts for why this is a dedicated table/route
 * rather than a BvaConfig field.
 *
 * GET  — { mode, isAdmin }. Safe for any signed-in viewer; the mode value
 *        itself isn't sensitive, and isAdmin here is informational for the
 *        UI only (the real security check happens again on every POST).
 * POST — { mode }. Re-checks checkIsAdmin() itself server-side — never
 *        trusts a client-claimed admin status. 403s non-admins.
 */

import { checkIsAdmin, getAgentAccessMode, setAgentAccessMode } from "@/lib/agent-access-mode"

export const dynamic = "force-dynamic"

export async function GET() {
  const [mode, admin] = await Promise.all([getAgentAccessMode(), checkIsAdmin()])
  return Response.json({ mode, isAdmin: admin.isAdmin })
}

export async function POST(req: Request) {
  const admin = await checkIsAdmin()
  if (!admin.isAdmin) {
    return Response.json({ error: "Admin only" }, { status: 403 })
  }

  let body: { mode?: unknown }
  try {
    body = await req.json()
  } catch {
    return Response.json({ error: "Invalid JSON body" }, { status: 400 })
  }

  if (body.mode !== "caller" && body.mode !== "owner") {
    return Response.json({ error: "mode must be \"caller\" or \"owner\"" }, { status: 400 })
  }

  await setAgentAccessMode(body.mode, admin.viewerRole ?? "unknown")
  return Response.json({ mode: body.mode })
}
