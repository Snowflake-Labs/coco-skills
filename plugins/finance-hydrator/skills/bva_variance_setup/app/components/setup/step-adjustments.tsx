"use client"

import { useState, useCallback } from "react"
import { useQuery } from "@tanstack/react-query"
import { Loader2, AlertTriangle } from "lucide-react"
import { getJson } from "@/lib/format"
import type { BvaConfig } from "@/lib/config"
import { TablePeek } from "@/components/setup/table-peek"

type Nav = { next: () => void; back: () => void }

// ── Category management panel ────────────────────────────────────────────────
// Lets the customer rename/merge how expense category VALUES appear in the
// dashboard, without touching the source data. Renames are display-time only:
// they're stored in BvaConfig.renameMap and applied by lib/config.ts's
// applyRenameMap()/buildCategoryRemapExpr() as a CASE expression layered on
// top of the raw category column. Entering the same display name for two
// different source values merges them into one dashboard row.

type CategorySource = "budget" | "actuals" | "ledger"

function CategoryManagementPanel({
  config, update,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
}) {
  const source: CategorySource = config.categoryRenameSource ?? "budget"

  const sources: { id: CategorySource; label: string; table?: string; col?: string }[] = [
    { id: "budget",  label: "Budget",  table: config.budgetTable,        col: config.budgetCategoryCol },
    { id: "actuals", label: "Actuals", table: config.actualsTable,       col: config.actualsCategoryCol },
    { id: "ledger",  label: "Ledger",  table: config.ledgerMappingTable, col: config.ledgerCategoryCol },
  ]

  const active = sources.find((s) => s.id === source) ?? sources[0]
  const table  = active.table
  const col    = active.col

  const { data, isFetching } = useQuery({
    queryKey: ["categories", source, table, col],
    queryFn:  () => getJson<{ categories: Array<{ value: string; count: number }> }>(
      `/api/setup/discover?action=categories&table=${encodeURIComponent(table!)}&col=${encodeURIComponent(col!)}`,
    ),
    enabled: !!table && !!col,
    staleTime: Infinity,
  })

  const renameMap = config.renameMap ?? {}

  // Setting a display name equal to (or blank vs.) the original value is
  // treated as "no rename" and removed from the map entirely, rather than
  // stored as a no-op entry — keeps renameMap free of clutter that would
  // otherwise show up as a "1 rename configured" the customer didn't intend.
  const setRename = useCallback((value: string, displayName: string) => {
    const next = { ...renameMap }
    if (displayName.trim() === "" || displayName.trim() === value) {
      delete next[value]
    } else {
      next[value] = displayName.trim()
    }
    update({ renameMap: next })
  }, [renameMap, update])

  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-medium">Category names</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Rename or merge categories as they appear in the dashboard.
          Leave the display name blank to keep the original. Enter the same display name on two rows to merge them.
        </p>
      </div>

      {/* Source selector */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">Source:</span>
        <div className="flex rounded-md border overflow-hidden text-xs">
          {sources.map((s) => {
            const available = !!s.table && !!s.col
            return (
              <button
                key={s.id}
                type="button"
                disabled={!available}
                onClick={() => update({ categoryRenameSource: s.id })}
                className={[
                  "px-3 py-1 transition-colors border-r last:border-r-0",
                  source === s.id
                    ? "bg-primary text-primary-foreground"
                    : available
                    ? "hover:bg-muted"
                    : "opacity-40 cursor-not-allowed",
                ].join(" ")}
              >
                {s.label}
              </button>
            )
          })}
        </div>
      </div>

      {table && col && (
        <TablePeek fqn={table} label={active.label} mappings={[{ label: "Category", col }]} />
      )}

      {!table || !col ? (
        <p className="text-xs text-muted-foreground">
          {source === "budget"  && "Map a category column on the Budget table in Step 2 to enable this."}
          {source === "actuals" && "Map a category column on the Actuals table in Step 2 to enable this."}
          {source === "ledger"  && "Configure a ledger mapping table in Step 3 to enable this."}
        </p>
      ) : (
        <>
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}

          {!isFetching && data && (
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-muted/20">
                  <tr>
                    <th className="px-3 py-2 text-left">Category (from data)</th>
                    <th className="px-3 py-2 text-right tabular-nums text-muted-foreground">Rows</th>
                    <th className="px-3 py-2 text-left">Display name</th>
                  </tr>
                </thead>
                <tbody>
                  {data.categories.map((cat) => (
                    <tr key={cat.value} className="border-t">
                      <td className="px-3 py-2 font-mono">{cat.value}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">{cat.count.toLocaleString()}</td>
                      <td className="px-3 py-2">
                        <input
                          type="text"
                          placeholder={cat.value}
                          value={renameMap[cat.value] ?? ""}
                          onChange={(e) => setRename(cat.value, e.target.value)}
                          className="w-full px-2 py-1 text-xs border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary"
                        />
                      </td>
                    </tr>
                  ))}
                  {!data.categories.length && (
                    <tr><td colSpan={3} className="px-3 py-3 text-center text-muted-foreground">No category data found</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {Object.keys(renameMap).length > 0 && (
        <p className="text-xs text-muted-foreground">
          {Object.keys(renameMap).length} rename{Object.keys(renameMap).length !== 1 ? "s" : ""} configured.
          {new Set(Object.values(renameMap)).size < Object.keys(renameMap).length && " Categories with the same display name will be merged."}
        </p>
      )}
    </div>
  )
}

// ── Account rebucket panel ────────────────────────────────────────────────────
// Same rename/merge idea as CategoryManagementPanel above, but specifically
// for the "ledger mapping table" category path (categorySource ===
// "mapping_table"): there, categories come from a lookup table rather than a
// direct column, so this panel writes to BvaConfig.accountRenameMap instead
// of renameMap. lib/config.ts's buildFromConfig() applies these renames in a
// bva_display CTE AFTER the actuals↔budget join, so raw ledger category
// values are still used for matching — only the customer-facing label changes,
// and rows sharing a merged label get summed together.

function AccountRebucketPanel({
  config, update,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
}) {
  const table = config.ledgerMappingTable
  const col   = config.ledgerCategoryCol

  // Only shown when ledger mapping table is configured
  if (config.categorySource !== "mapping_table" || !table || !col) return null

  return <AccountRebucketPanelInner config={config} update={update} table={table} col={col} />
}

function AccountRebucketPanelInner({
  config, update, table, col,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
  table: string
  col: string
}) {
  type RebucketSource = "budget" | "actuals" | "ledger"

  const sources: { id: RebucketSource; label: string; table?: string; col?: string }[] = [
    { id: "budget",  label: "Budget",  table: config.budgetTable,        col: config.budgetCategoryCol },
    { id: "actuals", label: "Actuals", table: config.actualsTable,       col: config.actualsCategoryCol },
    { id: "ledger",  label: "Ledger",  table,                            col },
  ]
  const [source, setSource] = useState<RebucketSource>("ledger")
  const active = sources.find((s) => s.id === source) ?? sources[2]
  const activeTable = active.table
  const activeCol   = active.col

  const { data, isFetching } = useQuery({
    queryKey: ["account-categories", source, activeTable, activeCol],
    queryFn:  () => getJson<{ categories: Array<{ value: string; count: number }> }>(
      `/api/setup/discover?action=categories&table=${encodeURIComponent(activeTable!)}&col=${encodeURIComponent(activeCol!)}`,
    ),
    enabled: !!activeTable && !!activeCol,
    staleTime: Infinity,
  })

  const accountRenameMap = config.accountRenameMap ?? {}

  const setRename = useCallback((value: string, displayName: string) => {
    const next = { ...accountRenameMap }
    if (displayName.trim() === "" || displayName.trim() === value) {
      delete next[value]
    } else {
      next[value] = displayName.trim()
    }
    update({ accountRenameMap: next })
  }, [accountRenameMap, update])

  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-medium">Category names</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Rename or merge expense categories for the dashboard.
          Leave the display name blank to keep the original. Enter the same display name on two rows to merge them.
        </p>
      </div>

      {/* Source selector */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">Source:</span>
        <div className="flex rounded-md border overflow-hidden text-xs">
          {sources.map((s) => {
            const available = !!s.table && !!s.col
            return (
              <button
                key={s.id}
                type="button"
                disabled={!available}
                onClick={() => setSource(s.id)}
                className={[
                  "px-3 py-1 transition-colors border-r last:border-r-0",
                  source === s.id
                    ? "bg-primary text-primary-foreground"
                    : available
                    ? "hover:bg-muted"
                    : "opacity-40 cursor-not-allowed",
                ].join(" ")}
              >
                {s.label}
              </button>
            )
          })}
        </div>
      </div>

      {activeTable && activeCol && (
        <TablePeek fqn={activeTable} label={active.label} mappings={[{ label: "Category", col: activeCol }]} />
      )}

      {!activeTable || !activeCol ? (
        <p className="text-xs text-muted-foreground">
          {source === "budget"  && "Map a category column on the Budget table in Step 2 to enable this."}
          {source === "actuals" && "Map a category column on the Actuals table in Step 2 to enable this."}
          {source === "ledger"  && "Configure a ledger mapping table in Step 3 to enable this."}
        </p>
      ) : (
        <>
          {isFetching && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}

          {!isFetching && data && (
          <div className="border rounded-lg overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-muted/20">
                <tr>
                  <th className="px-3 py-2 text-left">Category (from data)</th>
                  <th className="px-3 py-2 text-right tabular-nums text-muted-foreground">Rows</th>
                  <th className="px-3 py-2 text-left">Display name</th>
                </tr>
              </thead>
              <tbody>
                {data.categories.map((cat) => (
                  <tr key={cat.value} className="border-t">
                    <td className="px-3 py-2 font-mono">{cat.value}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">{cat.count.toLocaleString()}</td>
                    <td className="px-3 py-2">
                      <input
                        type="text"
                        placeholder={cat.value}
                        value={accountRenameMap[cat.value] ?? ""}
                        onChange={(e) => setRename(cat.value, e.target.value)}
                        className="w-full px-2 py-1 text-xs border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                    </td>
                  </tr>
                ))}
                {!data.categories.length && (
                  <tr><td colSpan={3} className="px-3 py-3 text-center text-muted-foreground">No category data found</td></tr>
                )}
              </tbody>
            </table>
          </div>
          )}
        </>
      )}

      {Object.keys(accountRenameMap).length > 0 && (
        <p className="text-xs text-muted-foreground">
          {Object.keys(accountRenameMap).length} rename{Object.keys(accountRenameMap).length !== 1 ? "s" : ""} configured.
          {new Set(Object.values(accountRenameMap)).size < Object.keys(accountRenameMap).length && " Categories with the same display name will be merged and summed."}
        </p>
      )}
    </div>
  )
}

// ── Cost center exclusions panel ──────────────────────────────────────────────
// Lets the customer opt specific cost centers OUT of BvA entirely (e.g. a
// decommissioned/"DNU_" cost center that still has stray transactions).
// Excluded IDs go into BvaConfig.excludedCostCenters, which buildFromConfig()
// turns into a `WHERE cost_center not in (...)` clause on the actuals side.
// Cost centers flagged with an "inactive" naming pattern (surfaced by the
// discover API, e.g. names starting with a common deprecation marker) are
// grouped and highlighted first since they're the most likely candidates.

function CostCenterExclusionsPanel({
  config, update,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
}) {
  const table = config.costCenterTable
  const col   = config.ccIdCol
  const [search, setSearch]       = useState("")
  const [showAll, setShowAll]     = useState(false)

  const { data, isFetching } = useQuery({
    queryKey: ["all-cc", table, col],
    queryFn:  () => getJson<{ costCenters: Array<{ value: string; count: number; pattern: string | null }> }>(
      `/api/setup/discover?action=all-cc&table=${encodeURIComponent(table!)}&col=${encodeURIComponent(col!)}`,
    ),
    enabled: !!table && !!col,
    staleTime: Infinity,
  })

  const excluded = new Set(config.excludedCostCenters ?? [])

  const toggleOne = useCallback((value: string) => {
    const next = new Set(excluded)
    if (next.has(value)) next.delete(value)
    else next.add(value)
    update({ excludedCostCenters: [...next] })
  }, [excluded, update])

  const toggleGroup = useCallback((values: string[], allExcluded: boolean) => {
    const next = new Set(excluded)
    if (allExcluded) values.forEach((v) => next.delete(v))
    else             values.forEach((v) => next.add(v))
    update({ excludedCostCenters: [...next] })
  }, [excluded, update])

  if (!table || !col) {
    return (
      <div className="space-y-3">
        <p className="text-sm font-medium">Cost center exclusions</p>
        <p className="text-xs text-muted-foreground">
          Map a cost center dimension table in Step 1 to enable cost center exclusions.
        </p>
      </div>
    )
  }

  const allCCs    = data?.costCenters ?? []
  const flagged   = allCCs.filter((cc) => cc.pattern !== null)
  const unflagged = allCCs.filter((cc) => cc.pattern === null)

  // Group flagged by pattern type
  const flaggedGroups = new Map<string, Array<{ value: string; count: number }>>()
  for (const cc of flagged) {
    const p = cc.pattern!
    if (!flaggedGroups.has(p)) flaggedGroups.set(p, [])
    flaggedGroups.get(p)!.push({ value: cc.value, count: cc.count })
  }

  const q = search.trim().toLowerCase()

  const filterItems = <T extends { value: string }>(items: T[]) =>
    q ? items.filter((i) => i.value.toLowerCase().includes(q)) : items

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2">
        <AlertTriangle className="h-4 w-4 text-yellow-500 shrink-0 mt-0.5" />
        <div>
          <p className="text-sm font-medium">Cost center exclusions</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            BvA includes all cost centers by default. Select any you want to exclude from analysis.
            {flagged.length > 0 && ` ${flagged.length} cost center${flagged.length !== 1 ? "s" : ""} with inactive markers are highlighted below.`}
          </p>
        </div>
      </div>

      {isFetching && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}

      {!isFetching && allCCs.length > 0 && (
        <>
          {/* Search */}
          <input
            type="text"
            placeholder="Search cost centers…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full px-3 py-1.5 text-xs border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary"
          />

          <div className="border rounded-lg overflow-hidden divide-y">
            {/* Flagged groups */}
            {Array.from(flaggedGroups.entries()).map(([marker, items]) => {
              const filtered = filterItems(items)
              if (!filtered.length) return null
              const allExcluded  = filtered.every((i) => excluded.has(i.value))
              const someExcluded = filtered.some((i) => excluded.has(i.value))
              return (
                <div key={marker}>
                  <div className="flex items-center gap-3 px-3 py-2 bg-yellow-50 dark:bg-yellow-950/20 text-xs font-medium">
                    <input
                      type="checkbox"
                      checked={allExcluded}
                      ref={(el) => { if (el) el.indeterminate = someExcluded && !allExcluded }}
                      onChange={() => toggleGroup(filtered.map((i) => i.value), allExcluded)}
                    />
                    <span className="inline-flex items-center gap-1">
                      <span className="px-1.5 py-0.5 rounded text-[10px] bg-yellow-200 dark:bg-yellow-800 text-yellow-800 dark:text-yellow-200">{marker}</span>
                      <span>{filtered.length} cost center{filtered.length !== 1 ? "s" : ""}</span>
                    </span>
                    {someExcluded && (
                      <span className="ml-auto text-muted-foreground font-normal">
                        {filtered.filter((i) => excluded.has(i.value)).length} excluded
                      </span>
                    )}
                  </div>
                  <table className="w-full text-xs">
                    <tbody>
                      {filtered.map((cc) => (
                        <tr key={cc.value} className="border-t hover:bg-muted/10">
                          <td className="px-3 py-1.5 w-8">
                            <input type="checkbox" checked={excluded.has(cc.value)} onChange={() => toggleOne(cc.value)} />
                          </td>
                          <td className="px-3 py-1.5 font-mono">{cc.value}</td>
                          <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">{cc.count.toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            })}

            {/* Non-flagged (shown via search or "Show all" toggle) */}
            {unflagged.length > 0 && (() => {
              const filteredUnflagged = filterItems(unflagged)
              const visibleUnflagged  = q ? filteredUnflagged : (showAll ? filteredUnflagged : [])

              return (
                <div>
                  {!q && (
                    <div className="flex items-center gap-3 px-3 py-2 bg-muted/10 text-xs">
                      <button
                        type="button"
                        onClick={() => setShowAll((v) => !v)}
                        className="text-muted-foreground underline hover:no-underline"
                      >
                        {showAll ? "Hide" : `Show all ${unflagged.length} cost center${unflagged.length !== 1 ? "s" : ""}`}
                      </button>
                    </div>
                  )}
                  {visibleUnflagged.length > 0 && (
                    <table className="w-full text-xs">
                      <tbody>
                        {visibleUnflagged.map((cc) => (
                          <tr key={cc.value} className="border-t hover:bg-muted/10">
                            <td className="px-3 py-1.5 w-8">
                              <input type="checkbox" checked={excluded.has(cc.value)} onChange={() => toggleOne(cc.value)} />
                            </td>
                            <td className="px-3 py-1.5 font-mono">{cc.value}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">{cc.count.toLocaleString()}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  {q && filteredUnflagged.length === 0 && flaggedGroups.size === 0 && (
                    <p className="px-3 py-3 text-xs text-center text-muted-foreground">No results for &ldquo;{search}&rdquo;</p>
                  )}
                </div>
              )
            })()}
          </div>
        </>
      )}

      {excluded.size > 0 && (
        <p className="text-xs text-muted-foreground">
          {excluded.size} cost center{excluded.size !== 1 ? "s" : ""} will be excluded from BvA.
          <button
            type="button"
            onClick={() => update({ excludedCostCenters: [] })}
            className="ml-2 underline hover:no-underline"
          >
            Clear all
          </button>
        </p>
      )}
    </div>
  )
}

// ── Account category overrides panel ────────────────────────────────────────
// Also only relevant to the ledger mapping table path: sometimes a handful of
// individual account codes are mapped to the wrong category in the ledger
// table itself, and the customer doesn't control that source table. This
// panel lets them override the category for specific account codes without
// editing the ledger table — entries go into BvaConfig.accountCategoryOverrides
// (account code -> target category), which lib/config.ts's ledger_map CTE
// applies with COALESCE so an override always wins over the ledger table's
// own value for that code.

function AccountCategoryOverridesPanel({
  config, update,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
}) {
  const table       = config.ledgerMappingTable
  const keyCol      = config.ledgerAccountKeyCol
  const categoryCol = config.ledgerCategoryCol

  // Only show when ledger mapping path is configured
  if (config.categorySource !== "mapping_table" || !table || !keyCol || !categoryCol) return null

  return <AccountCategoryOverridesPanelInner config={config} update={update} table={table} keyCol={keyCol} categoryCol={categoryCol} />
}

function AccountCategoryOverridesPanelInner({
  config, update, table, keyCol, categoryCol,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
  table: string
  keyCol: string
  categoryCol: string
}) {
  const [search, setSearch] = useState("")

  const { data: accountData, isFetching: fetchingAccounts } = useQuery({
    queryKey: ["account-categories", table, keyCol, categoryCol],
    queryFn: () => getJson<{ accounts: Array<{ code: string; category: string; count: number }> }>(
      `/api/setup/discover?action=account-categories&table=${encodeURIComponent(table)}&keyCol=${encodeURIComponent(keyCol)}&categoryCol=${encodeURIComponent(categoryCol)}`,
    ),
    staleTime: Infinity,
  })

  const { data: categoryData } = useQuery({
    queryKey: ["categories", "ledger", table, categoryCol],
    queryFn: () => getJson<{ categories: Array<{ value: string; count: number }> }>(
      `/api/setup/discover?action=categories&table=${encodeURIComponent(table)}&col=${encodeURIComponent(categoryCol)}`,
    ),
    staleTime: Infinity,
  })

  const overrides = config.accountCategoryOverrides ?? {}
  const knownCategories = (categoryData?.categories ?? []).map((c) => c.value)

  const setOverride = useCallback((code: string, target: string) => {
    const next = { ...overrides }
    if (!target.trim() || target.trim() === (accountData?.accounts.find((a) => a.code === code)?.category ?? "")) {
      delete next[code]
    } else {
      next[code] = target.trim()
    }
    update({ accountCategoryOverrides: Object.keys(next).length > 0 ? next : undefined })
  }, [overrides, accountData, update])

  const allAccounts = accountData?.accounts ?? []
  const q = search.trim().toLowerCase()

  // Always show overridden codes; also show search matches (min 2 chars to avoid loading thousands)
  const visibleAccounts = allAccounts.filter((a) =>
    a.code in overrides || (q.length >= 2 && (a.code.toLowerCase().includes(q) || a.category.toLowerCase().includes(q)))
  )

  const overrideCount = Object.keys(overrides).length

  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-medium">Account category overrides</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          Override the category assigned to specific account codes from the ledger mapping table.
          Overridden codes take precedence over the mapping table at pipeline build time.
        </p>
      </div>

      <input
        type="text"
        placeholder="Search by account code or current category…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="w-full px-3 py-1.5 text-xs border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary"
      />

      {fetchingAccounts && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}

      {!fetchingAccounts && visibleAccounts.length === 0 && (
        <p className="text-xs text-muted-foreground">
          {overrideCount === 0
            ? "Type at least 2 characters to search account codes."
            : "No additional codes match your search."}
        </p>
      )}

      {!fetchingAccounts && visibleAccounts.length > 0 && (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-muted/20">
              <tr>
                <th className="px-3 py-2 text-left">Account code</th>
                <th className="px-3 py-2 text-left text-muted-foreground">Default category</th>
                <th className="px-3 py-2 text-left">Override category</th>
              </tr>
            </thead>
            <tbody>
              {visibleAccounts.map((acct) => (
                <tr key={acct.code} className={`border-t ${acct.code in overrides ? "bg-amber-500/5" : ""}`}>
                  <td className="px-3 py-1.5 font-mono">{acct.code}</td>
                  <td className="px-3 py-1.5 text-muted-foreground">{acct.category}</td>
                  <td className="px-3 py-1.5">
                    {knownCategories.length > 0 ? (
                      <select
                        value={overrides[acct.code] ?? ""}
                        onChange={(e) => setOverride(acct.code, e.target.value)}
                        className="w-full px-2 py-0.5 text-xs border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary"
                      >
                        <option value="">(keep default)</option>
                        {knownCategories.map((cat) => (
                          <option key={cat} value={cat}>{cat}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="text"
                        placeholder="(keep default)"
                        value={overrides[acct.code] ?? ""}
                        onChange={(e) => setOverride(acct.code, e.target.value)}
                        className="w-full px-2 py-0.5 text-xs border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {overrideCount > 0 && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {overrideCount} override{overrideCount !== 1 ? "s" : ""} configured.
            These will be applied when the config is saved and the pipeline re-runs.
          </span>
          <button
            type="button"
            onClick={() => update({ accountCategoryOverrides: undefined })}
            className="underline hover:no-underline hover:text-destructive"
          >
            Clear all
          </button>
        </div>
      )}
    </div>
  )
}

// ── Key prefix normalization panel ────────────────────────────────────────────
//
// Manual control for BvaConfig.keyStripPrefixes (see lib/config.ts). Always
// editable — not gated behind any automatic mismatch detection — so a user
// can fix this class of join-key problem (a code in one table carries a
// prefix the matching code in another table doesn't) as soon as they notice
// it, for any of the cross-table join keys the pipeline relies on. Never
// touches the source tables — only affects the generated SQL's join keys.

type KeyRole = "actualsAccountCol" | "ledgerAccountKeyCol" | "actualsCostCenterCol" | "budgetCostCenterCol" | "ccIdCol"

function KeyPrefixPanel({
  config, update,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
}) {
  const rows: Array<{ role: KeyRole; label: string; source?: string }> = []

  if (config.categorySource === "mapping_table" && config.ledgerMappingTable && config.ledgerAccountKeyCol && config.actualsAccountCol) {
    rows.push({ role: "ledgerAccountKeyCol", label: "Ledger mapping code", source: `${config.ledgerMappingTable}.${config.ledgerAccountKeyCol}` })
    rows.push({ role: "actualsAccountCol",   label: "Actuals account code", source: config.actualsTable ? `${config.actualsTable}.${config.actualsAccountCol}` : undefined })
  }
  if (config.actualsCostCenterCol && config.budgetCostCenterCol) {
    rows.push({ role: "actualsCostCenterCol", label: "Actuals cost center code", source: config.actualsTable ? `${config.actualsTable}.${config.actualsCostCenterCol}` : undefined })
    rows.push({ role: "budgetCostCenterCol",  label: "Budget cost center code",  source: config.budgetTable  ? `${config.budgetTable}.${config.budgetCostCenterCol}`  : undefined })
  }
  if (config.costCenterTable && config.ccIdCol) {
    rows.push({ role: "ccIdCol", label: "Cost center dimension code", source: `${config.costCenterTable}.${config.ccIdCol}` })
  }

  if (rows.length === 0) return null

  const prefixes = config.keyStripPrefixes ?? {}

  const setPrefixes = (role: KeyRole, value: string) => {
    const list = value.split(",").map((s) => s.trim()).filter(Boolean)
    const next = { ...prefixes }
    if (list.length === 0) delete next[role]
    else next[role] = list
    update({ keyStripPrefixes: Object.keys(next).length > 0 ? next : undefined })
  }

  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-medium">Code prefix normalization</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          When two tables use the same code but one carries an extra prefix (e.g. ledger codes stored as
          &quot;FIN_500000&quot; while actuals use &quot;500000&quot;), an exact-match join silently drops those rows.
          Enter prefix(es) to strip (comma-separated) — this only changes how the query matches rows;
          your source tables are never modified.
        </p>
      </div>
      <div className="space-y-2">
        {rows.map((r) => (
          <div key={r.role} className="flex items-center gap-2">
            <label className="text-xs text-muted-foreground w-56 shrink-0 truncate" title={r.source}>
              {r.label}
            </label>
            <input
              type="text"
              placeholder="e.g. FIN_"
              value={(prefixes[r.role] ?? []).join(", ")}
              onChange={(e) => setPrefixes(r.role, e.target.value)}
              className="flex-1 max-w-xs px-2 py-1 text-xs font-mono border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Step 5: Adjustments ───────────────────────────────────────────────────────
//
// Everything in this step is optional, display/filter-level fine-tuning
// layered on top of the core table/column mapping done in earlier steps —
// none of it is required to get a working BvA. It covers four independent
// concerns, each rendered by its own panel above: fixing join-key format
// mismatches (KeyPrefixPanel), renaming/merging how category values are
// labeled (CategoryManagementPanel / AccountRebucketPanel), correcting
// individual account-to-category assignments (AccountCategoryOverridesPanel),
// and excluding specific cost centers from the analysis entirely
// (CostCenterExclusionsPanel). Single-table configs skip this step outright
// since there's no multi-table join or ledger mapping to adjust.

export function StepAdjustments({
  config, update, nav,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
  nav: Nav
}) {
  // Single-table path: skip Step 5 entirely
  if (config.sourceType === "single_table") {
    return (
      <div className="space-y-6 max-w-xl">
        <div>
          <h2 className="text-lg font-semibold">Step 5 — Adjustments</h2>
          <p className="text-muted-foreground text-sm mt-1">
            Using a single pre-joined table — adjustments are skipped. Proceed to validate the query output.
          </p>
        </div>
        <div className="flex gap-3">
          <button onClick={nav.back} className="px-4 py-2 rounded-lg border text-sm hover:bg-muted transition-colors">Back</button>
          <button onClick={nav.next} className="px-5 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90">
            Next: Groupings
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-8 max-w-2xl">
      <div>
        <h2 className="text-lg font-semibold">Step 5 — Adjustments</h2>
        <p className="text-muted-foreground text-sm mt-1">
          Optional configuration for renaming categories, remapping GAAP account labels, and excluding cost centers.
          Everything here is optional — click <strong>Next: Validate</strong> to skip.
        </p>
      </div>

      {/* Code prefix normalization — fixes join-key mismatches before anything
          downstream (categories, overrides) can be expected to work correctly. */}
      <KeyPrefixPanel config={config} update={update} />

      {/* Category renames — only shown when NOT using the ledger mapping path.
          When categorySource=mapping_table, renameMap isn't used in the SQL;
          AccountRebucketPanel (accountRenameMap) handles renames for that path.
          For every other categorySource (column/cluster_analysis/category_remap),
          renameMap IS applied — see lib/config.ts's applyRenameMap()/
          buildCategoryRemapExpr(). */}
      {config.categorySource !== "mapping_table" && (
        <CategoryManagementPanel config={config} update={update} />
      )}

      {/* Account rebucket — only shown when ledger mapping is configured */}
      <AccountRebucketPanel config={config} update={update} />

      {/* Account category overrides — reclassify specific GL codes */}
      <AccountCategoryOverridesPanel config={config} update={update} />

      {/* Cost center exclusions */}
      <CostCenterExclusionsPanel config={config} update={update} />

      <div className="flex gap-3 pt-4 border-t">
        <button onClick={nav.back} className="px-4 py-2 rounded-lg border text-sm hover:bg-muted transition-colors">Back</button>
        <button
          onClick={nav.next}
          className="px-5 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
        >
          Next: Groupings
        </button>
      </div>
    </div>
  )
}
