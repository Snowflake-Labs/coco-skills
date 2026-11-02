import { buildDiscoveryBundle } from "@/lib/discover-bundle"
import { checkIsAdmin } from "@/lib/agent-access-mode"

export const dynamic = "force-dynamic"
export const maxDuration = 120

/**
 * GET /api/setup/discover-bundle
 * Deterministic, parallel discovery of BvA candidate tables (see lib/discover-bundle).
 * Returns ranked gl/budget/cc/ledger candidate buckets. Called by the setup
 * agent (app/api/agent/setup/route.ts) as a tool so it can skip its own serial
 * SHOW DATABASES / INFORMATION_SCHEMA scan and jump straight to ranked
 * candidates. Admin-gated for the same reason app/api/setup/discover/route.ts
 * is: this scans database/schema/table names account-wide, which is both
 * expensive (don't let any viewer trigger it on a whim) and runs under the
 * app's own privileges — so an unrestricted caller could see database/schema/
 * table names via the app's owner role that their own role wouldn't otherwise
 * be able to see.
 */
export async function GET() {
  const admin = await checkIsAdmin()
  if (!admin.isAdmin) {
    return Response.json({ error: "Admin only" }, { status: 403 })
  }
  try {
    const bundle = await buildDiscoveryBundle()
    return Response.json(bundle)
  } catch (e) {
    console.error("[api/setup/discover-bundle]", e)
    return Response.json({ error: "Discovery failed" }, { status: 500 })
  }
}
