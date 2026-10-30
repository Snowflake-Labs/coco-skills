import { getConfigTable, getHistoryTable, invalidateConfigCache, materializeBva, dropMaterializedBva } from "@/lib/config-server"
import { BVA_BASELINE_COLUMNS, type BvaConfig } from "@/lib/config"
import { checkIsAdmin } from "@/lib/agent-access-mode"
import { validateTableParam } from "@/lib/bva"
import { validateCustomLogicSql, validateCustomLogicAlias } from "@/lib/custom-logic-guard"
import {
  getConfigList,
  getActivatedFrom,
  getConfigHistoryRows,
  getConfigAndUpdatedAt,
  getConfigJsonForBackup,
  insertConfigHistory,
  getConfigJsonBeforeDelete,
  insertConfigHistoryOnDelete,
  deleteConfigRow,
  upsertConfigRow,
} from "@/lib/queries/config-crud"

/**
 * The actual, authoritative enforcement point for lib/custom-logic-guard.ts's
 * policy — the client-side checks in components/setup/step-validate.tsx are
 * a UX pre-check only, not a security boundary; a direct POST to this route
 * bypasses the wizard UI entirely. Re-validates every rule's sql/alias
 * against the same allow-list the guard's own doc comment claims is
 * enforced "the single place," against the OTHER enabled rules' aliases
 * (so a rule can reference an earlier one, same as the client-side check),
 * and rejects the whole save if any rule fails — never silently drops one
 * rule and saves the rest, which could change behavior in a way the admin
 * making the save didn't intend.
 */
export function validateCustomLogicRules(rules: BvaConfig["customLogicRules"]): string | null {
  if (!rules?.length) return null
  const enabledAliases = rules.filter((r) => r.enabled && r.alias).map((r) => r.alias!)
  const known = [...BVA_BASELINE_COLUMNS, ...enabledAliases]
  for (const rule of rules) {
    const sqlCheck = validateCustomLogicSql(rule.sql, rule.kind, known)
    if (!sqlCheck.ok) return `Custom logic rule "${rule.name}": ${sqlCheck.reason}`
    if (rule.kind === "derived_column") {
      if (!rule.alias) return `Custom logic rule "${rule.name}": a derived_column rule requires an alias.`
      const aliasCheck = validateCustomLogicAlias(rule.alias, BVA_BASELINE_COLUMNS)
      if (!aliasCheck.ok) return `Custom logic rule "${rule.name}": ${aliasCheck.reason}`
    }
  }
  // Reject duplicate aliases across enabled rules — buildFinalSql() would
  // otherwise emit two SELECT columns with the same name.
  const seen = new Set<string>()
  for (const alias of enabledAliases) {
    const lower = alias.toLowerCase()
    if (seen.has(lower)) return `Two enabled custom logic rules use the same alias "${alias}" — rename one.`
    seen.add(lower)
  }
  return null
}

/**
 * Validates every table-name field that will be interpolated into the
 * materialization SQL (buildFinalSql → CREATE OR REPLACE TABLE AS SELECT).
 * These come straight from the request body (incl. agent-proposed configs), so
 * an unvalidated value could redirect the pipeline to an arbitrary table.
 * Validates only non-empty fields — all five are optional in BvaConfig.
 */
export function validateConfigTables(cfg: BvaConfig): string | null {
  const tableFields: Array<[string, string | undefined]> = [
    ["actualsTable", cfg.actualsTable],
    ["budgetTable", cfg.budgetTable],
    ["costCenterTable", cfg.costCenterTable],
    ["ledgerMappingTable", cfg.ledgerMappingTable],
    ["singleTable", cfg.singleTable],
  ]
  for (const [name, val] of tableFields) {
    const t = val?.trim()
    if (t && !validateTableParam(t)) {
      return `Invalid ${name}: must be a DATABASE.SCHEMA.TABLE identifier.`
    }
  }
  return null
}

export const dynamic = "force-dynamic"
// Materializing the BvA pipeline on save can be a heavy aggregation — allow time.
export const maxDuration = 300

// ── GET: load current config, a named config, a list, or history ─────────────

export async function GET(req: Request) {
  const url         = new URL(req.url)
  const wantHistory = url.searchParams.get("history") === "1"
  const wantList    = url.searchParams.get("list") === "true"
  const namedConfig = url.searchParams.get("name")   // e.g. "Q4 Reforecast"
  const CONFIG_TABLE  = await getConfigTable()
  const HISTORY_TABLE = getHistoryTable(CONFIG_TABLE)

  // ── List all saved configs ──────────────────────────────────────────────────
  if (wantList) {
    try {
      const rows = await getConfigList(CONFIG_TABLE)
      // Which saved profile (if any) is currently loaded into 'default' — set
      // by /api/setup/config/activate, cleared on a direct edit-and-save of
      // 'default' itself. Read separately so the list query above stays a
      // single flat SELECT regardless of row count.
      const activeRows = await getActivatedFrom(CONFIG_TABLE)
      const activeProfile = activeRows[0]?.ACTIVATED_FROM ? String(activeRows[0].ACTIVATED_FROM) : null
      return Response.json({
        activeProfile,
        configs: rows.map((r) => ({
          name:         String(r.CONFIG_NAME),
          updatedAt:    String(r.UPDATED_AT),
          sourceType:   r.SOURCE_TYPE  ? String(r.SOURCE_TYPE)  : null,
          actualsTable: r.ACTUALS_TABLE ? String(r.ACTUALS_TABLE) : null,
        })),
      })
    } catch {
      return Response.json({ activeProfile: null, configs: [] })
    }
  }

  // ── History (default or named) ──────────────────────────────────────────────
  if (wantHistory) {
    const target = namedConfig ?? "default"
    try {
      const rows = await getConfigHistoryRows(HISTORY_TABLE, target)
      return Response.json({
        history: rows.map((r) => ({
          savedAt: String(r.SAVED_AT),
          config:  r.CONFIG_JSON as BvaConfig,
        })),
      })
    } catch {
      return Response.json({ history: [] })
    }
  }

  // ── Single named or default config ─────────────────────────────────────────
  const target = namedConfig ?? "default"
  try {
    const rows = await getConfigAndUpdatedAt(CONFIG_TABLE, target)
    if (rows.length === 0) {
      return Response.json({ config: null, tableExists: true })
    }
    return Response.json({ config: rows[0].CONFIG_JSON as BvaConfig, tableExists: true })
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    if (msg.includes("does not exist") || msg.includes("not found") || msg.includes("002003")) {
      return Response.json({ config: null, tableExists: false })
    }
    console.error("[api/setup/config GET]", e)
    return Response.json({ error: "An internal error occurred." }, { status: 500 })
  }
}

// ── POST: backup + upsert config (no CREATE TABLE — tables must already exist) ─

export async function POST(req: Request) {
  try {
    const admin = await checkIsAdmin()
    if (!admin.isAdmin) {
      return Response.json({ error: "Admin only" }, { status: 403 })
    }
    const body = (await req.json()) as BvaConfig
    body.updatedAt  = new Date().toISOString()
    body.configName = body.configName ?? "default"
    // A save through this route is always a fresh edit, not an activation —
    // clear any activatedFrom carried over from a loaded config object so a
    // directly-edited 'default' doesn't keep pointing at a now-stale profile
    // name (the /activate route sets this explicitly when it actually is one).
    delete body.activatedFrom

    // Validate table-name fields before anything touches SQL (see validateConfigTables).
    const tableError = validateConfigTables(body)
    if (tableError) {
      return Response.json({ error: tableError }, { status: 400 })
    }

    const customLogicError = validateCustomLogicRules(body.customLogicRules)
    if (customLogicError) {
      return Response.json({ error: customLogicError }, { status: 400 })
    }

    const CONFIG_TABLE  = await getConfigTable()
    const HISTORY_TABLE = getHistoryTable(CONFIG_TABLE)

    // Back up the current config before overwriting (best-effort)
    try {
      const existing = await getConfigJsonForBackup(CONFIG_TABLE, body.configName)
      if (existing.length > 0) {
        const existingJson = JSON.stringify(existing[0].CONFIG_JSON).replace(/'/g, "''")
        await insertConfigHistory(HISTORY_TABLE, body.configName, existingJson)
      }
    } catch {
      // History backup is best-effort — don't block the save
    }

    // Upsert current config
    await upsertConfigRow(CONFIG_TABLE, body.configName, body)

    invalidateConfigCache()

    // Kick off materialization in the background — don't await it.
    // The dashboard falls back to the inline pipeline while the mat table is building,
    // and the user can navigate immediately. A "↻ Refresh data" button on the dashboard
    // lets them trigger a re-query once it's ready.
    materializeBva(body).catch((e) =>
      console.error("[api/setup/config POST] materialize failed", e)
    )

    return Response.json({ ok: true, materialized: false, materializeError: null })
  } catch (e) {
    console.error("[api/setup/config POST]", e)
    const msg      = e instanceof Error ? e.message : "Failed to save config"
    const isPriv   = msg.includes("Insufficient privileges") || msg.includes("ACCESS_CONTROL")
    const hint = isPriv
      ? "The config table may not exist yet. Go to Step 1 → Config storage to create it."
      : null
    return Response.json({ error: hint ?? "An internal error occurred.", privilegeError: isPriv, hint }, { status: 500 })
  }
}

// ── DELETE: remove a named config or reset 'default' to clean slate ───────────

export async function DELETE(req: Request) {
  const url    = new URL(req.url)
  const target = url.searchParams.get("name") ?? "default"

  try {
    const admin = await checkIsAdmin()
    if (!admin.isAdmin) {
      return Response.json({ error: "Admin only" }, { status: 403 })
    }
    const CONFIG_TABLE  = await getConfigTable()
    const HISTORY_TABLE = getHistoryTable(CONFIG_TABLE)

    // Back up to history before deleting (best-effort)
    try {
      const existing = await getConfigJsonBeforeDelete(CONFIG_TABLE, target)
      if (existing.length > 0) {
        const existingJson = JSON.stringify(existing[0].CONFIG_JSON).replace(/'/g, "''")
        await insertConfigHistoryOnDelete(HISTORY_TABLE, target, existingJson)
      }
    } catch { /* history backup is best-effort */ }

    await deleteConfigRow(CONFIG_TABLE, target)

    invalidateConfigCache()

    // Only drop the materialized table when resetting 'default'
    if (target === "default") {
      await dropMaterializedBva()
    }

    return Response.json({ ok: true })
  } catch (e) {
    console.error("[api/setup/config DELETE]", e)
    return Response.json({ error: "Failed to delete config" }, { status: 500 })
  }
}
