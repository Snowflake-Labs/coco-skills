import { getConfigTable, invalidateConfigCache } from "@/lib/config-server"
import type { BvaConfig, FilterPreset } from "@/lib/config"
import { getPresetsJson, getDefaultConfigJson, upsertConfigRow } from "@/lib/queries/config-crud"

export const dynamic = "force-dynamic"

/**
 * Lightweight filter-preset CRUD.
 * Reads/writes only the `savedFilterPresets` field of the 'default' config row.
 * Does NOT call materializeBva() — presets are UI metadata, not data.
 *
 * GET  /api/setup/presets          → { presets: FilterPreset[] }
 * POST /api/setup/presets          body: FilterPreset → upsert by id, return { ok }
 * DELETE /api/setup/presets?id=xxx → remove by id, return { ok }
 */

async function readPresets(table: string): Promise<FilterPreset[]> {
  try {
    const rows = await getPresetsJson(table)
    if (rows.length === 0 || rows[0].PRESETS == null) return []
    const raw = rows[0].PRESETS
    return Array.isArray(raw) ? (raw as FilterPreset[]) : []
  } catch {
    return []
  }
}

async function writePresets(table: string, presets: FilterPreset[]): Promise<void> {
  // Read the current full config so we can patch just the presets field
  const rows = await getDefaultConfigJson(table)
  const existing: BvaConfig = rows.length > 0 ? (rows[0].CONFIG_JSON as BvaConfig) : { configName: "default", sourceType: "single_table" }
  const updated = { ...existing, savedFilterPresets: presets }
  await upsertConfigRow(table, "default", updated)
  invalidateConfigCache("default")
}

export async function GET() {
  try {
    const table   = await getConfigTable()
    const presets = await readPresets(table)
    return Response.json({ presets })
  } catch (e) {
    console.error("[api/setup/presets GET]", e)
    return Response.json({ presets: [] })
  }
}

export async function POST(req: Request) {
  try {
    const preset = (await req.json()) as FilterPreset
    if (!preset?.id || !preset?.name) {
      return Response.json({ error: "id and name are required" }, { status: 400 })
    }
    // Defend against a malformed agent-emitted save_preset block (missing one of
    // the required filter arrays) persisting a preset that would crash the
    // dashboard later when anyone loads it — see lib/chat-context.tsx.
    preset.filters = {
      ...preset.filters,
      organizations: Array.isArray(preset.filters?.organizations) ? preset.filters.organizations : [],
      categories:    Array.isArray(preset.filters?.categories)    ? preset.filters.categories    : [],
      costCenters:   Array.isArray(preset.filters?.costCenters)   ? preset.filters.costCenters   : [],
    }
    const table   = await getConfigTable()
    const current = await readPresets(table)

    // Never silently overwrite a different preset that happens to share a name —
    // only an edit-by-id (same id) is a legitimate update. See README.md §11:
    // this action fires from CoCo's chat with no human confirmation, so this
    // check is the only thing stopping it from clobbering an existing preset.
    const nameCollision = current.some((p) => p.name === preset.name && p.id !== preset.id)
    if (nameCollision) {
      return Response.json(
        { error: `A preset named "${preset.name}" already exists`, nameCollision: true },
        { status: 409 },
      )
    }

    // Upsert by id (replace existing if same id, otherwise append)
    const updated = current.some((p) => p.id === preset.id)
      ? current.map((p) => p.id === preset.id ? preset : p)
      : [...current, preset]
    await writePresets(table, updated)
    return Response.json({ ok: true })
  } catch (e) {
    console.error("[api/setup/presets POST]", e)
    return Response.json({ error: "Failed to save preset" }, { status: 500 })
  }
}

export async function DELETE(req: Request) {
  try {
    const id = new URL(req.url).searchParams.get("id")
    if (!id) return Response.json({ error: "id is required" }, { status: 400 })
    const table   = await getConfigTable()
    const current = await readPresets(table)
    await writePresets(table, current.filter((p) => p.id !== id))
    return Response.json({ ok: true })
  } catch (e) {
    console.error("[api/setup/presets DELETE]", e)
    return Response.json({ error: "Failed to delete preset" }, { status: 500 })
  }
}
