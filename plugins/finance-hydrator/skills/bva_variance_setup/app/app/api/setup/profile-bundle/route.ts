import { buildProfileBundle, type SelectedTables } from "@/lib/profile-bundle"
import { checkIsAdmin } from "@/lib/agent-access-mode"

export const dynamic = "force-dynamic"
export const maxDuration = 120

/**
 * POST /api/setup/profile-bundle  { gl?, budget?, cc?, ledger? }
 * Parallel per-table profiling (column metadata + batched cardinality + sample)
 * for the tables the user selected. Called by the setup agent
 * (app/api/agent/setup/route.ts) as a tool to skip its own serial DESCRIBE +
 * sample queries in Phase 2. Admin-gated for the same reason discover-bundle
 * is: profiling runs actual queries (DESCRIBE, sampling, cardinality counts)
 * against arbitrary account tables under the app's own privileges, so an
 * unrestricted viewer could both trigger unwanted load and see column-level
 * data via the app's owner role that their own role wouldn't otherwise be
 * able to see.
 */
export async function POST(req: Request) {
  const admin = await checkIsAdmin()
  if (!admin.isAdmin) {
    return Response.json({ error: "Admin only" }, { status: 403 })
  }
  try {
    const sel = (await req.json()) as SelectedTables
    if (!sel || (!sel.gl && !sel.budget && !sel.cc && !sel.ledger)) {
      return Response.json({ error: "at least one of gl/budget/cc/ledger required" }, { status: 400 })
    }
    const bundle = await buildProfileBundle(sel)
    return Response.json(bundle)
  } catch (e) {
    console.error("[api/setup/profile-bundle]", e)
    return Response.json({ error: "Profiling failed" }, { status: 500 })
  }
}
