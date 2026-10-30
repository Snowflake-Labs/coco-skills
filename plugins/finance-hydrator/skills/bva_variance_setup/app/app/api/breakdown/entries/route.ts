import { sqlStr } from "@/lib/bva"
import { loadConfig } from "@/lib/config-server"
import { getJournalEntryRows } from "@/lib/queries/bva-data"

export const dynamic = "force-dynamic"

function q(s: string) { return `"${s.replace(/"/g, '""')}"` }

/**
 * Derive the calendar date range for a given fiscal year string (e.g. "FY2027")
 * given the month the fiscal year starts (1 = Jan, 2 = Feb, …).
 *
 * Example: FY2027, fyStartMonth=2 → 2026-02-01 to 2027-01-31
 */
function fiscalYearDateRange(fy: string, fyStartMonth: number): { start: string; end: string } {
  const match = fy.match(/FY(\d{4})/)
  if (!match) return { start: "1900-01-01", end: "2100-12-31" }
  const calYear = parseInt(match[1], 10)

  if (fyStartMonth === 1) {
    // Jan-based fiscal year: FY2027 = 2027-01-01 to 2027-12-31
    return { start: `${calYear}-01-01`, end: `${calYear}-12-31` }
  }

  // Otherwise: FY starts mid-year.
  // The year label in our app means the fiscal year *ending* in that calendar year.
  // E.g. fyStartMonth=2 → FY2027 = Feb 2026 – Jan 2027
  const startCalYear = calYear - 1
  const startMonth   = String(fyStartMonth).padStart(2, "0")
  const endMonth     = String(fyStartMonth - 1).padStart(2, "0")
  // Last day of the end month (use the day before fyStartMonth of calYear)
  const endDay       = new Date(calYear, fyStartMonth - 1, 0).getDate()
  return {
    start: `${startCalYear}-${startMonth}-01`,
    end:   `${calYear}-${endMonth}-${String(endDay).padStart(2, "0")}`,
  }
}

// ── GET /api/breakdown/entries ────────────────────────────────────────────────
// Returns raw journal / transaction rows from the configured actualsTable,
// filtered to the selected category + cost center for the current period.
//
// Query params:
//   category       — selected expense category
//   costCenterId   — the raw cost_center_id value
//   fiscalYear     — e.g. "FY2027"
//   quarter        — e.g. "Q2" or "Full Year"
//
// Response: { columns: string[], rows: Record<string,unknown>[], total: number }

export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url)
    const category      = searchParams.get("category")      ?? ""
    const costCenterId  = searchParams.get("costCenterId")  ?? ""
    const fiscalYear    = searchParams.get("fiscalYear")    ?? ""
    const quarter       = searchParams.get("quarter")       ?? "Full Year"

    if (!fiscalYear) {
      return Response.json({ error: "fiscalYear is required" }, { status: 400 })
    }

    const cfg = await loadConfig()

    // Journal entries only available for multi-table configs with a raw actualsTable
    if (!cfg || cfg.sourceType === "single_table" || !cfg.actualsTable) {
      return Response.json({
        columns: [],
        rows: [],
        total: 0,
        unavailable: "Journal entry drill-down is not available for single-table configurations.",
      })
    }

    const {
      actualsTable,
      actualsDateCol      = "accounting_date",
      actualsCostCenterCol = "cost_center_id",
      actualsAccountCol,
      actualsCategoryCol,
      categorySource,
      ledgerMappingTable,
      ledgerAccountKeyCol,
      ledgerCategoryCol,
      fyStartMonth: fyStartMonthRaw,
      accountFilterPrefixes,
      negateAmount,
    } = cfg
    const fyStartMonth = fyStartMonthRaw ?? 1

    // Date range filter
    const { start, end } = fiscalYearDateRange(fiscalYear, fyStartMonth)
    const dateFilter = `a.${q(actualsDateCol)} BETWEEN '${start}' AND '${end}'`

    // Optional quarter sub-filter — map fiscal quarter to calendar months
    let monthFilter = ""
    if (quarter !== "Full Year" && quarter.match(/^Q[1-4]$/)) {
      const qNum = parseInt(quarter[1], 10)
      const months: number[] = []
      for (let i = 0; i < 3; i++) {
        months.push(((fyStartMonth - 1 + (qNum - 1) * 3 + i) % 12) + 1)
      }
      monthFilter = `AND MONTH(a.${q(actualsDateCol)}) IN (${months.join(",")})`
    }

    // Cost center filter
    const ccFilter = costCenterId
      ? `AND a.${q(actualsCostCenterCol)} = ${sqlStr(costCenterId)}`
      : ""

    // Account prefix filter (same as buildFromConfig)
    let prefixFilter = ""
    if (accountFilterPrefixes?.length && actualsAccountCol) {
      const preds = accountFilterPrefixes.map(
        (p) => `CAST(a.${q(actualsAccountCol)} AS VARCHAR) LIKE '${p.replace(/'/g, "''")}%'`
      )
      prefixFilter = `AND (${preds.join(" OR ")})`
    }

    const rows = await getJournalEntryRows({
      actualsTable,
      actualsDateCol,
      category,
      categorySource,
      ledgerMappingTable,
      ledgerAccountKeyCol,
      ledgerCategoryCol,
      actualsAccountCol,
      actualsCategoryCol,
      dateFilter,
      monthFilter,
      ccFilter,
      prefixFilter,
    })

    if (rows.length === 0) {
      return Response.json({ columns: [], rows: [], total: 0 })
    }

    // Dynamic columns from whatever the raw table has
    const columns = Object.keys(rows[0])

    // Negate amounts if configured (debit-positive source tables)
    const amountCols = new Set<string>()
    if (negateAmount && cfg.actualsAmountCol) {
      // Identify amount-like columns by their names (heuristic)
      for (const col of columns) {
        const lower = col.toLowerCase()
        if (lower.includes("amount") || lower.includes("expense") || lower.includes("cost") || lower.includes("spend")) {
          amountCols.add(col)
        }
      }
    }

    const serializedRows = rows.map((r) => {
      const out: Record<string, unknown> = {}
      for (const col of columns) {
        const v = r[col]
        if (v instanceof Date) {
          out[col] = v.toISOString().split("T")[0]
        } else if (amountCols.has(col) && typeof v === "number") {
          out[col] = -v
        } else {
          out[col] = v ?? null
        }
      }
      return out
    })

    return Response.json({
      columns,
      rows: serializedRows,
      total: rows.length,  // we cap at 1000; real total not counted (expensive)
    })
  } catch (e) {
    console.error(new Date().toISOString(), "[api/breakdown/entries]", e)
    return Response.json(
      { error: "Failed to load journal entries" },
      { status: 500 },
    )
  }
}
