import { validateTableParam } from "@/lib/bva"
import { checkIsAdmin } from "@/lib/agent-access-mode"
import {
  getProfileColumns,
  getColumnCardinalities,
  getTableRowCount,
  sampleDistinctColumnValues,
  getDateGrainStats,
  getSignStats,
  suggestRole,
} from "@/lib/queries/profile"

export const dynamic = "force-dynamic"

/**
 * GET /api/setup/profile?table=DB.SCHEMA.TABLE
 *
 * Returns:
 *   - columns: column list with roles, cardinality, and 3 sample values
 *   - grain: detected data grain (daily/monthly/quarterly)
 *   - fiscalInfo: detected fiscal columns, FY start month
 *   - signInfo: amount sign analysis (positive vs negative)
 *   - versionInfo: distinct version values (budget table)
 *   - rollupInfo: non-standard quarter values to exclude
 */
export async function GET(req: Request) {
  const admin = await checkIsAdmin()
  if (!admin.isAdmin) {
    return Response.json({ error: "Admin only" }, { status: 403 })
  }
  const { searchParams } = new URL(req.url)
  const table = searchParams.get("table")
  if (!table) return Response.json({ error: "table required" }, { status: 400 })
  if (!validateTableParam(table)) return Response.json({ error: "Invalid table name" }, { status: 400 })

  try {
    // ── Column list + cardinality ─────────────────────────────────────────────
    const [db, schema, tbl] = table.split(".")
    if (!db || !schema || !tbl) {
      return Response.json({ error: "table must be DB.SCHEMA.TABLE" }, { status: 400 })
    }

    const colRows = await getProfileColumns(db, schema, tbl)

    const colNames = colRows.map((r) => String(r.COLUMN_NAME))
    const colTypes = Object.fromEntries(colRows.map((r) => [String(r.COLUMN_NAME), String(r.DATA_TYPE)]))

    // Row count + cardinality — use index-based unquoted aliases (c0, c1, ...)
    // so Snowflake uppercases them predictably (C0, C1) and the SDK lookup is reliable.
    // Double-quoted aliases preserve exact case, which caused the original 0-card bug.
    let rowCount = 0
    const cardinalities: Record<string, number> = {}
    try {
      const limited = colNames.slice(0, 100)
      const cardRows = await getColumnCardinalities(table, colNames)
      if (cardRows.length) {
        rowCount = Number(cardRows[0]["RC"] ?? 0)
        for (let i = 0; i < limited.length; i++) {
          cardinalities[limited[i]] = Number(cardRows[0][`C${i}`] ?? 0)
        }
      }
    } catch (e) {
      console.warn("[profile] cardinality probe failed:", e instanceof Error ? e.message : e)
      try {
        const rcRows = await getTableRowCount(table)
        rowCount = Number(rcRows[0]?.RC ?? 0)
      } catch { /* non-fatal */ }
    }

    // Sample values — include DATE/TIMESTAMP + VARCHAR cols with <= 300 distinct values.
    // Raise limit and run in parallel so results arrive quickly.
    const sampleCols = colNames.filter((c) => {
      const dt = colTypes[c] ?? ""
      const card = cardinalities[c] ?? 0
      if (/DATE|TIMESTAMP/.test(dt)) return true
      if (/VARCHAR|TEXT|STRING|CHAR/.test(dt) && (card === 0 || card <= 300)) return true
      return false
    }).slice(0, 30) // up from 12 so deeper columns like SPEND_CATEGORY are reached

    const samples: Record<string, string[]> = {}
    if (sampleCols.length > 0) {
      const results = await Promise.all(
        sampleCols.map(async (col) => {
          try {
            const sr = await sampleDistinctColumnValues(table, col)
            return [col, sr.map((r) => String(r.V))] as const
          } catch {
            return [col, [] as string[]] as const
          }
        }),
      )
      for (const [col, vals] of results) samples[col] = vals
    }

    // ── Grain detection ───────────────────────────────────────────────────────
    let grain: "daily" | "monthly" | "quarterly" | "annual" | "unknown" = "unknown"
    const dateCols = colNames.filter((c) => /DATE|TIMESTAMP/.test(colTypes[c] ?? ""))

    if (dateCols.length > 0) {
      const dateCol = dateCols[0]
      try {
        const gr = await getDateGrainStats(table, dateCol)
        if (gr.length) {
          const dp = Number(gr[0].DISTINCT_PERIODS ?? 0)
          const sm = Number(gr[0].SPAN_MONTHS ?? 1)
          if (dp > sm * 3) grain = "daily"
          else if (dp > sm * 0.7) grain = "monthly"
          else if (dp > sm * 0.2) grain = "quarterly"
          else grain = "annual"
        }
      } catch { /* non-fatal */ }
    }

    // ── Fiscal column detection ───────────────────────────────────────────────
    const fyCol = colNames.find((c) => /FISCAL.?YEAR|^FY$/.test(c.toUpperCase()))
    const fqCol = colNames.find((c) => /FISCAL.?QUARTER|^FQ$/.test(c.toUpperCase()))

    // FY start month detection (from sample date values if available)
    let fyStartMonth: number | null = null
    if (dateCols.length > 0 && samples[dateCols[0]]?.length) {
      const firstDate = samples[dateCols[0]][0]
      const month = new Date(firstDate).getMonth() + 1 // 1-12
      // Heuristic: if first data month isn't January, likely fiscal year start
      fyStartMonth = month === 1 ? 1 : month
    }

    // Rollup row detection (non-standard quarter values)
    let rollupValues: string[] = []
    if (fqCol) {
      const vals = samples[fqCol] ?? []
      rollupValues = vals.filter((v) => !/^Q[1-4]$/.test(v))
    }

    // ── Sign analysis (for amount columns) ───────────────────────────────────
    const numberCols = colNames.filter((c) => /NUMBER|FLOAT|DECIMAL|INT/.test(colTypes[c] ?? ""))
    const signInfo: Record<string, { positivePct: number; mean: number }> = {}
    for (const col of numberCols.slice(0, 5)) {
      try {
        const sr = await getSignStats(table, col)
        if (sr.length) {
          signInfo[col] = {
            positivePct: Math.round(Number(sr[0].PCT_POSITIVE ?? 50)),
            mean: Math.round(Number(sr[0].MEAN_VAL ?? 0)),
          }
        }
      } catch { /* non-fatal */ }
    }

    // ── Version detection ─────────────────────────────────────────────────────
    // A "version" column (Plan, Forecast, Actuals, ...) is text with a small,
    // bounded set of distinct values (2-30 — enough for a handful of planning
    // scenarios, but not so many that it's actually a free-text field) and a
    // name that hints at planning/scenario semantics.
    const potentialVersionCols = colNames.filter((c) => {
      const dt = colTypes[c] ?? ""
      const card = cardinalities[c] ?? 0
      return /VARCHAR|TEXT|STRING/.test(dt) && card >= 2 && card <= 30
        && /VERSION|PLAN|FORECAST|SCENARIO|BUDGET/.test(c.toUpperCase())
    })
    const versionInfo: Record<string, string[]> = {}
    for (const col of potentialVersionCols.slice(0, 3)) {
      versionInfo[col] = samples[col] ?? []
    }

    // ── Assemble column suggestions ───────────────────────────────────────────
    const columns = colRows.map((r) => {
      const name = String(r.COLUMN_NAME)
      const dtype = String(r.DATA_TYPE)
      const card = cardinalities[name] ?? 0
      return {
        name,
        dataType: dtype,
        isNullable: r.IS_NULLABLE === "YES",
        cardinality: card,
        cardinalityPct: rowCount > 0 ? Math.round((card / rowCount) * 1000) / 10 : null,
        samples: samples[name] ?? [],
        suggestedRole: suggestRole(name, dtype, card, rowCount),
      }
    })

    return Response.json({
      rowCount,
      columns,
      grain,
      fiscalInfo: {
        fyCol: fyCol ?? null,
        fqCol: fqCol ?? null,
        fyStartMonth,
        rollupValues,
      },
      signInfo,
      versionInfo,
    })
  } catch (e) {
    console.error(new Date().toISOString(), "[api/setup/profile]", e)
    return Response.json(
      { error: "Profiling failed" },
      { status: 500 },
    )
  }
}
