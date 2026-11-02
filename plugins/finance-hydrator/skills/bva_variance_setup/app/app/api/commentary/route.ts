import { getConfigTable, getCommentaryTable } from "@/lib/config-server"
import { getLatestCommentary, insertCommentary } from "@/lib/queries/commentary"
import { headers } from "next/headers"
import { querySnowflake } from "@/lib/snowflake"

export const dynamic = "force-dynamic"

const GENERIC_ERROR = "An internal error occurred."
const MAX_TEXT_LEN = 4000
const MAX_CATEGORY_LEN = 256

// Strip non-printable control characters (keeping tab, newline, carriage
// return) so agent- or user-authored commentary can't smuggle terminal/escape
// control sequences into text persisted to and rendered from the shared table.
function sanitizeText(s: string): string {
  // eslint-disable-next-line no-control-regex
  return s.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
}

/**
 * Resolves the signed-in viewer's Snowflake identity server-side so a write can
 * be attributed to the real caller rather than a client-supplied string.
 * Returns null when there is no caller token (unauthenticated — e.g. local dev
 * or a deployment without GRANT CALLER), in which case the write is rejected.
 * Uses a caller's-rights query (no warehouse override — metadata-only, same
 * rationale as checkIsAdmin in lib/agent-access-mode.ts).
 */
async function resolveViewer(): Promise<string | null> {
  const token = (await headers()).get("sf-context-current-user-token") ?? ""
  if (!token) return null
  const rows = await querySnowflake("SELECT CURRENT_USER() AS U", { callersRights: true })
  const u = rows[0]?.U
  return typeof u === "string" && u.length > 0 ? u : null
}

// ── GET /api/commentary?configName=...&fiscalYear=... ─────────────────────────
// Returns the latest commentary entry per (category, fiscal_year, period) for
// a given config. The underlying table is append-only (see PUT below) — every
// edit inserts a new row rather than overwriting the previous one, so this
// picks the most recent row per key rather than assuming one row per key.
// Response: { commentary: Record<string, { text, source, updatedBy }> }
// Key format: "${category}|${fiscalYear}|${period}"

export async function GET(req: Request) {
  try {
    const url        = new URL(req.url)
    const configName = url.searchParams.get("configName") ?? "default"
    const fiscalYear = url.searchParams.get("fiscalYear") ?? ""

    const configTable    = await getConfigTable()
    const commentaryTable = getCommentaryTable(configTable)

    const rows = await getLatestCommentary(commentaryTable, configName, fiscalYear)

    const commentary: Record<string, { text: string; source: string; updatedBy: string }> = {}
    for (const r of rows) {
      const key = `${r.CATEGORY}|${r.FISCAL_YEAR}|${r.PERIOD}`
      commentary[key] = {
        text: r.COMMENTARY_TEXT ?? "",
        source: r.SOURCE ?? "MANUAL",
        updatedBy: r.UPDATED_BY ?? "",
      }
    }

    return Response.json({ commentary })
  } catch (e) {
    console.error("[api/commentary GET]", e)
    return Response.json({ error: GENERIC_ERROR }, { status: 500 })
  }
}

// ── PUT /api/commentary ───────────────────────────────────────────────────────
// Appends a new commentary entry — never updates or deletes an existing one.
// The table has no primary key; every edit (human-typed or agent-suggested via
// suggest_commentary) is a new row with its own updated_at, and GET always
// reads back the latest row per (category, fiscal_year, period). This trades
// unbounded row growth for a guarantee that a commentary write can never
// silently clobber a prior entry — including one that hasn't loaded into the
// client yet. See README.md §11 for why this matters for the
// agent-triggered path specifically (this action fires with no human
// confirmation step).
// Body: { configName, category, fiscalYear, period, text, updatedBy?, source? }
// source defaults to "MANUAL" (a person editing the cell); the agent's
// suggest_commentary path passes "SKILL" explicitly.

export async function PUT(req: Request) {
  try {
    // Authenticated-viewer gate: a valid caller token is required, and the
    // writer identity is resolved server-side from CURRENT_USER() rather than
    // trusted from the request body (which any viewer could spoof).
    const viewer = await resolveViewer()
    if (!viewer) {
      return Response.json({ error: "Sign-in required to write commentary." }, { status: 401 })
    }

    const body = (await req.json()) as {
      configName?: string
      category?: string
      fiscalYear?: string
      period?: string
      text?: string
      source?: string
    }

    const configName  = (body.configName ?? "default").trim()
    const category    = sanitizeText((body.category ?? "").trim()).slice(0, MAX_CATEGORY_LEN)
    const fiscalYear  = (body.fiscalYear ?? "").trim()
    const period      = (body.period ?? "").trim()
    const text        = sanitizeText((body.text ?? "").trim()).slice(0, MAX_TEXT_LEN)
    // Identity is server-resolved, never taken from the client.
    const updatedBy   = viewer
    // `source` is advisory provenance only (accountability lives in updatedBy).
    const source      = (body.source ?? "MANUAL").trim().toUpperCase() === "SKILL" ? "SKILL" : "MANUAL"

    if (!category || !fiscalYear || !period) {
      return Response.json({ error: "category, fiscalYear, and period are required" }, { status: 400 })
    }

    const configTable    = await getConfigTable()
    const commentaryTable = getCommentaryTable(configTable)

    await insertCommentary(commentaryTable, { configName, category, fiscalYear, period, text, updatedBy, source })

    return Response.json({ ok: true })
  } catch (e) {
    console.error("[api/commentary PUT]", e)
    return Response.json({ error: GENERIC_ERROR }, { status: 500 })
  }
}
