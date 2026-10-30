import { getConfigTable, invalidateConfigCache, materializeBva } from "@/lib/config-server"
import type { BvaConfig } from "@/lib/config"
import { checkIsAdmin } from "@/lib/agent-access-mode"
import { getNamedConfigJson, upsertConfigRow } from "@/lib/queries/config-crud"

export const dynamic = "force-dynamic"
// Materializing the BvA pipeline can be a heavy aggregation — allow time.
export const maxDuration = 300

/**
 * POST /api/setup/config/activate
 * Body: { name: string }
 *
 * Copies a named config profile to the 'default' slot and re-materializes the
 * BvA pipeline so the dashboard immediately reflects the loaded config.
 * The named profile row itself is NOT modified.
 */
export async function POST(req: Request) {
  try {
    const admin = await checkIsAdmin()
    if (!admin.isAdmin) {
      return Response.json({ error: "Admin only" }, { status: 403 })
    }
    const { name } = (await req.json()) as { name: string }
    if (!name?.trim()) {
      return Response.json({ error: "name is required" }, { status: 400 })
    }

    const CONFIG_TABLE = await getConfigTable()

    // 1. Read the named config
    const rows = await getNamedConfigJson(CONFIG_TABLE, name)
    if (rows.length === 0) {
      return Response.json({ error: `Profile "${name}" not found` }, { status: 404 })
    }

    const cfg = rows[0].CONFIG_JSON as BvaConfig

    // 2. Write it to the 'default' slot (this is what the dashboard reads)
    cfg.updatedAt = new Date().toISOString()
    cfg.activatedFrom = name
    await upsertConfigRow(CONFIG_TABLE, "default", cfg)

    invalidateConfigCache("default")

    // Re-materialize in the background — don't block the response.
    materializeBva(cfg).catch((e) =>
      console.error("[api/setup/config/activate POST] materialize failed", e)
    )

    return Response.json({ ok: true, materialized: false, materializeError: null })
  } catch (e) {
    console.error("[api/setup/config/activate POST]", e)
    return Response.json(
      { error: "Failed to activate config" },
      { status: 500 },
    )
  }
}
