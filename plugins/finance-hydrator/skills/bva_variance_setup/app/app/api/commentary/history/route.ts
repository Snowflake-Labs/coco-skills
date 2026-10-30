import { getConfigTable, getCommentaryTable } from "@/lib/config-server"
import { getCommentaryHistory } from "@/lib/queries/commentary"

export const dynamic = "force-dynamic"

// ── GET /api/commentary/history?configName=...&category=...&fiscalYear=...&period=...&source=SKILL ──
// Looks up the latest row for one commentary cell, optionally restricted to a
// specific source — used by the "restore AI version" affordance in
// CommentaryCell: a manual edit becomes the new overall latest, but the prior
// AI-generated note is still in this append-only table and can be looked up
// here even though GET /api/commentary (unfiltered) would no longer surface it.

export async function GET(req: Request) {
  try {
    const url        = new URL(req.url)
    const configName = url.searchParams.get("configName") ?? "default"
    const category    = url.searchParams.get("category") ?? ""
    const fiscalYear  = url.searchParams.get("fiscalYear") ?? ""
    const period      = url.searchParams.get("period") ?? ""
    const source      = url.searchParams.get("source") ?? ""

    if (!category || !fiscalYear || !period) {
      return Response.json({ error: "category, fiscalYear, and period are required" }, { status: 400 })
    }

    const configTable    = await getConfigTable()
    const commentaryTable = getCommentaryTable(configTable)

    const rows = await getCommentaryHistory(commentaryTable, { configName, category, fiscalYear, period, source })

    if (rows.length === 0) return Response.json({ entry: null })
    const r = rows[0]
    return Response.json({
      entry: {
        text: r.COMMENTARY_TEXT ?? "",
        source: r.SOURCE ?? "MANUAL",
        updatedBy: r.UPDATED_BY ?? "",
        updatedAt: r.UPDATED_AT ?? null,
      },
    })
  } catch (e) {
    console.error("[api/commentary/history]", e)
    return Response.json({ error: "Failed to load commentary history" }, { status: 500 })
  }
}
