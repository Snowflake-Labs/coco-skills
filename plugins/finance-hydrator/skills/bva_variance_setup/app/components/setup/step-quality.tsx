"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Loader2, AlertTriangle, CheckCircle2, ChevronUp, ChevronDown } from "lucide-react"
import { getJson } from "@/lib/format"
import type { BvaConfig } from "@/lib/config"
import { TablePeek } from "@/components/setup/table-peek"

type Nav = { next: () => void; back: () => void }


// ── Account filter panel ──────────────────────────────────────────────────────
// A raw GL table often mixes true operating expenses with revenue,
// balance-sheet, or other non-expense transaction types under the same
// account-code numbering scheme. This panel groups the actuals table's
// account codes by their leading digits (a "prefix") and lets the customer
// tick which prefixes represent real expenses — those become
// BvaConfig.accountFilterPrefixes, which buildFromConfig() turns into a
// `WHERE account like 'prefix%'` clause so non-expense rows never enter BvA.

function AccountFilterPanel({
  config, update,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
}) {
  const table = config.actualsTable
  const acctCol = config.actualsAccountCol
  const [sortBy,  setSortBy]  = useState<"prefix" | "count">("count")
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc")

  const { data, isFetching } = useQuery({
    queryKey: ["acct-filter", table, acctCol],
    queryFn: () =>
      getJson<{ prefixes: Array<{ prefix: string; count: number }> }>(
        `/api/setup/discover?action=prefixes&table=${encodeURIComponent(table!)}&col=${encodeURIComponent(acctCol!)}`,
      ),
    enabled: !!table && !!acctCol,
  })

  const selected = new Set(config.accountFilterPrefixes ?? [])

  // Ticking a prefix adds it to the include-list; unticking removes it.
  // An empty list means "no filter" (all account codes pass through).
  const toggle = (prefix: string) => {
    const next = new Set(selected)
    if (next.has(prefix)) next.delete(prefix)
    else next.add(prefix)
    update({ accountFilterPrefixes: [...next] })
  }

  const handleSort = (col: "prefix" | "count") => {
    if (sortBy === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    else { setSortBy(col); setSortDir(col === "prefix" ? "asc" : "desc") }
  }

  const sorted = [...(data?.prefixes ?? [])].sort((a, b) => {
    const mul = sortDir === "asc" ? 1 : -1
    if (sortBy === "prefix") return mul * a.prefix.localeCompare(b.prefix)
    return mul * (a.count - b.count)
  })

  const SortIcon = ({ col }: { col: "prefix" | "count" }) => {
    if (sortBy !== col) return <span className="inline-block w-3" />
    return sortDir === "asc"
      ? <ChevronUp className="inline h-3 w-3 mb-0.5" />
      : <ChevronDown className="inline h-3 w-3 mb-0.5" />
  }

  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-medium">Account filter</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Your GL may contain mixed transaction types (expenses, revenue, balance-sheet).
          Select the account prefixes that represent your controllable operating expenses.
          {!acctCol && <span className="text-yellow-600 ml-1">No account column mapped in Step 2.</span>}
        </p>
      </div>

      <TablePeek
        fqn={table}
        label="GL / Actuals"
        mappings={[
          { label: "Account", col: acctCol ?? "" },
        ]}
      />

      {isFetching && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}

      {!isFetching && data && (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-muted/20">
              <tr>
                <th className="px-3 py-2 text-left">Include</th>
                <th
                  className="px-3 py-2 text-left cursor-pointer select-none hover:text-foreground"
                  onClick={() => handleSort("prefix")}
                >
                  Account prefix <SortIcon col="prefix" />
                </th>
                <th
                  className="px-3 py-2 text-right cursor-pointer select-none hover:text-foreground"
                  onClick={() => handleSort("count")}
                >
                  Rows <SortIcon col="count" />
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p) => (
                <tr key={p.prefix} className="border-t">
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={selected.has(p.prefix)}
                      onChange={() => toggle(p.prefix)}
                    />
                  </td>
                  <td className="px-3 py-2 font-mono">{p.prefix}xxx</td>
                  <td className="px-3 py-2 text-right tabular-nums">{(p.count ?? 0).toLocaleString()}</td>
                </tr>
              ))}
              {!data.prefixes?.length && (
                <tr><td colSpan={3} className="px-3 py-3 text-center text-muted-foreground">No account data found</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <label className="flex items-center gap-2 text-xs cursor-pointer text-muted-foreground">
        <input
          type="checkbox"
          checked={!config.accountFilterPrefixes?.length}
          // Checking this clears any selected prefixes ("no filter"). There's
          // nothing meaningful to do on uncheck — the actual filter is set by
          // picking specific prefixes in the table above, not by this box —
          // so leave the selection as-is rather than the previous code's
          // always-clear-either-way no-op.
          onChange={(e) => { if (e.target.checked) update({ accountFilterPrefixes: [] }) }}
        />
        No filter needed (table contains only expense rows)
      </label>
    </div>
  )
}

// ── Join alignment panel ──────────────────────────────────────────────────────
// Actuals and Budget are matched on cost_center_id (see buildFromConfig()'s
// bva CTE join in lib/config.ts). If the two tables use different code
// formats for the same cost centers (e.g. one has leading zeros, or a prefix
// the other lacks), that join silently fails to match rows and both sides
// show as if there's no matching actual/budget — with no error. This panel
// runs a quick server-side check (/api/setup/discover?action=join-check) of
// how many distinct cost-center codes appear on BOTH sides, and renders that
// as a percentage bar so a mismatch is visible before Step 7's validation.

function JoinAlignmentPanel({ config }: { config: BvaConfig }) {
  const actualsTable = config.actualsTable
  const budgetTable = config.budgetTable
  const actualsCC = config.actualsCostCenterCol
  const budgetCC = config.budgetCostCenterCol

  const { data, isFetching } = useQuery({
    queryKey: ["join-align", actualsTable, budgetTable, actualsCC, budgetCC],
    queryFn: async () => {
      if (!actualsTable || !budgetTable || !actualsCC || !budgetCC) return null
      const res = await fetch(`/api/setup/discover?action=join-check&actualsTable=${encodeURIComponent(actualsTable)}&budgetTable=${encodeURIComponent(budgetTable)}&actualsCC=${encodeURIComponent(actualsCC)}&budgetCC=${encodeURIComponent(budgetCC)}`)
      const json = await res.json()
      return { overlapPct: Number(json.overlapPct ?? NaN) }
    },
    enabled: !!actualsTable && !!budgetTable && !!actualsCC && !!budgetCC,
  })

  const pct = Number(data?.overlapPct ?? NaN)
  const color = isNaN(pct) ? "text-muted-foreground" : pct >= 80 ? "text-green-600" : pct >= 20 ? "text-yellow-600" : "text-destructive"

  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-medium">Join alignment check</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Checks how well your actuals and budget cost center columns overlap.
          Low overlap means many rows will produce null variance (actuals with no matching budget, or vice versa).
        </p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <TablePeek
          fqn={actualsTable}
          label="GL / Actuals"
          mappings={[
            { label: "Cost center", col: actualsCC ?? "" },
          ]}
        />
        <TablePeek
          fqn={budgetTable}
          label="Budget"
          mappings={[
            { label: "Cost center", col: budgetCC ?? "" },
          ]}
        />
      </div>
      {isFetching && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
      {!isFetching && !isNaN(pct) && (
        <div className="flex items-center gap-3">
          <div className="flex-1 bg-muted rounded-full h-2">
            <div
              className="h-2 rounded-full bg-primary transition-all"
              style={{ width: `${Math.min(pct, 100)}%` }}
            />
          </div>
          <span className={`text-sm font-semibold tabular-nums ${color}`}>{Math.round(pct)}%</span>
          <span className="text-xs text-muted-foreground">
            {pct >= 80 ? "Clean" : pct >= 20 ? "Some misalignment" : "Poor alignment"}
          </span>
        </div>
      )}
      {!actualsCC || !budgetCC ? (
        <p className="text-xs text-yellow-600">Map cost center columns in Step 2 to enable this check.</p>
      ) : null}
    </div>
  )
}

// ── Step 4: Data quality ──────────────────────────────────────────────────────
//
// This step surfaces a handful of "will my query actually produce sensible
// numbers" signals before the customer reaches Step 7's real validation
// checks: which GL rows count as expenses (account filter), whether amounts
// are stored as positive or negative numbers, and whether actuals/budget
// cost-center codes actually line up well enough to join. None of these are
// hard requirements — every panel here is optional and can be skipped — but
// skipping them is how "0 rows" or wildly inflated totals show up later.

export function StepQuality({
  config, update, nav,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
  nav: Nav
}) {
  // Single-table path: skip Step 4 entirely
  if (config.sourceType === "single_table") {
    return (
      <div className="space-y-6 max-w-xl">
        <div>
          <h2 className="text-lg font-semibold">Step 4 — Data quality</h2>
          <p className="text-muted-foreground text-sm mt-1">
            Using a single pre-joined table — data quality checks are skipped.
            Proceed to validate the query output.
          </p>
        </div>
        <div className="rounded-lg border bg-green-50 dark:bg-green-950/20 border-green-200 dark:border-green-800 p-4 flex items-start gap-3">
          <CheckCircle2 className="h-4 w-4 text-green-600 shrink-0 mt-0.5" />
          <p className="text-sm text-green-800 dark:text-green-200">
            Single-table path — skipping account filter and join alignment checks.
          </p>
        </div>
        <div className="flex gap-3">
          <button onClick={nav.back} className="px-4 py-2 rounded-lg border text-sm hover:bg-muted transition-colors">Back</button>
          <button onClick={nav.next} className="px-5 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90">
            Next: Adjustments
          </button>
        </div>
      </div>
    )
  }

  // Show the account filter panel whenever the user mapped a ledger account column in step 2.
  // Also show it for category sources that always need it (mapping_table, cluster_analysis).
  const hasAccountCol = !!config.actualsAccountCol
  const needsAccountFilter = hasAccountCol
    || config.categorySource === "mapping_table"
    || config.categorySource === "cluster_analysis"

  return (
    <div className="space-y-8 max-w-2xl">
      <div>
        <h2 className="text-lg font-semibold">Step 4 — Data quality</h2>
        <p className="text-muted-foreground text-sm mt-1">
          Review data quality signals. All panels are optional — skip any that don&#39;t apply to your data.
        </p>
      </div>

      {/* Account filter — shown when a ledger account column was mapped */}
      {needsAccountFilter ? (
        <AccountFilterPanel config={config} update={update} />
      ) : (
        <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
          <p className="font-medium">Account filter — not configured</p>
          <p className="text-xs mt-1">
            Map a <span className="font-mono">Ledger account</span> column in Step 2 to enable account-level filtering.
          </p>
        </div>
      )}

      {/* Amount sign */}
      <div className="space-y-3">
        <p className="text-sm font-medium">Amount sign</p>
        <p className="text-xs text-muted-foreground">
          How are expense amounts stored in your actuals table?
        </p>
        <TablePeek
          fqn={config.actualsTable}
          label="GL / Actuals"
          mappings={[
            { label: "Amount", col: config.actualsAmountCol ?? "" },
          ]}
        />
        <div className="flex gap-4">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="radio"
              name="amtSign"
              checked={!config.negateAmount}
              onChange={() => update({ negateAmount: false })}
            />
            Positive (50,000 = $50K expense)
          </label>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="radio"
              name="amtSign"
              checked={!!config.negateAmount}
              onChange={() => update({ negateAmount: true })}
            />
            Negative (−50,000 = $50K expense)
          </label>
        </div>
      </div>

      {/* Join alignment */}
      <JoinAlignmentPanel config={config} />

      {/* Callout pointing to Step 5 */}
      <div className="rounded-lg border border-dashed p-4 space-y-1">
        <p className="text-sm font-medium">Optional: Adjustments</p>
        <p className="text-xs text-muted-foreground">
          Rename or merge expense categories, remap GAAP account labels, and exclude specific cost centers in the next step.
        </p>
      </div>

      {/* Warning banner if no account filter set for mapping_table path */}
      {needsAccountFilter && !config.accountFilterPrefixes?.length && (
        <div className="flex items-start gap-3 rounded-lg border border-yellow-200 dark:border-yellow-800 bg-yellow-50 dark:bg-yellow-950/20 p-4">
          <AlertTriangle className="h-4 w-4 text-yellow-600 shrink-0 mt-0.5" />
          <div className="text-sm">
            <p className="font-medium text-yellow-800 dark:text-yellow-200">Account filter recommended</p>
            <p className="text-xs text-yellow-700 dark:text-yellow-300 mt-1">
              Without an account filter, non-expense GL entries (revenue, balance sheet) may inflate spend totals.
              Either select account prefixes above or confirm the table contains only expense rows.
            </p>
          </div>
        </div>
      )}

      <div className="flex gap-3 pt-4 border-t">
        <button onClick={nav.back} className="px-4 py-2 rounded-lg border text-sm hover:bg-muted transition-colors">Back</button>
        <button
          onClick={nav.next}
          className="px-5 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
        >
          Next: Validate
        </button>
      </div>
    </div>
  )
}
