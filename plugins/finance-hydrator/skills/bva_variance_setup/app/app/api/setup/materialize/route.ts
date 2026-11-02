import { loadConfig, materializeBva } from "@/lib/config-server"
import { checkIsAdmin } from "@/lib/agent-access-mode"

export const dynamic = "force-dynamic"
// Rebuilding the materialized BvA table is a heavy aggregation — allow time.
export const maxDuration = 300

/**
 * POST /api/setup/materialize
 * "Materialize" here means: run the BvA query once and save its output into a
 * real physical table, instead of re-running the (often multi-CTE, multi-join)
 * query live on every dashboard page load. Called by the setup wizard/dashboard
 * (see components/bva-dashboard.tsx) whenever the saved config changes, so the
 * dashboard reads from that pre-computed table rather than recomputing the
 * whole join every time a viewer opens a page. See lib/config-server.ts's
 * materializeBva (the CREATE-TABLE-AS-SELECT logic) and dropMaterializedBva
 * for the implementation. No-op (materialized:false) for single_table configs
 * or when no config is saved, since those cases have nothing to materialize.
 */
export async function POST() {
  try {
    // Admin-gated: this triggers a real CREATE-TABLE-AS-SELECT write against
    // Snowflake using the app's own privileges, so it shouldn't be triggerable
    // by an arbitrary viewer just loading the dashboard.
    const admin = await checkIsAdmin()
    if (!admin.isAdmin) {
      return Response.json({ error: "Admin only" }, { status: 403 })
    }
    const cfg = await loadConfig()
    if (!cfg) return Response.json({ ok: false, materialized: false, error: "No saved config" }, { status: 400 })
    const mat = await materializeBva(cfg)
    return Response.json({ ok: true, materialized: mat !== null, table: mat })
  } catch (e) {
    console.error("[api/setup/materialize]", e)
    return Response.json({ error: "Refresh failed" }, { status: 500 })
  }
}
