"use client"

import { useState, useMemo, useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import { Loader2, ChevronDown, ChevronUp, Eye, EyeOff, AlertTriangle, CheckCircle2, XCircle } from "lucide-react"
import { getJson } from "@/lib/format"
import type { BvaConfig } from "@/lib/config"
import {
  ValidationSidebar,
  validateActuals,
  validateBudget,
  validateCC,
  validateLedger,
} from "@/components/setup/column-validation-sidebar"

type Nav = { next: () => void; back: () => void }

// A "column role" is what a raw column in the customer's table MEANS for BvA
// purposes — e.g. "this is the date column", "this is the dollar amount",
// "this is the ledger account code". buildFromConfig() (lib/config.ts) only
// understands roles, not the customer's actual column names, so every column
// the user cares about must be assigned exactly one role before the query can
// be built. See ROLE_OPTIONS below for the full role vocabulary.
type ColumnInfo = {
  name: string
  dataType: string
  isNullable: boolean
  cardinality: number
  cardinalityPct: number | null
  samples: string[]
  suggestedRole: string | null
}

type ProfileResult = {
  rowCount: number
  columns: ColumnInfo[]
  grain: string
  fiscalInfo: {
    fyCol: string | null
    fqCol: string | null
    fyStartMonth: number | null
    rollupValues: string[]
  }
  signInfo: Record<string, { positivePct: number; mean: number }>
  versionInfo: Record<string, string[]>
}

// The full set of roles a column can be assigned to, shown in each table's
// role dropdown. Not every role is relevant to every table type — see
// AUTO_SUGGEST_ALLOWED below for which roles are even auto-suggested for a
// given table.
const ROLE_OPTIONS = [
  { value: "",               label: "— skip —" },
  { value: "period",         label: "Date/period" },
  { value: "fiscal_year",    label: "Fiscal year" },
  { value: "fiscal_quarter", label: "Fiscal quarter" },
  { value: "amount",         label: "Amount (spending)" },
  { value: "account",        label: "Ledger account" },
  { value: "cost_center",    label: "Cost center ID" },
  { value: "category",       label: "Expense category" },
  { value: "version",        label: "Planning version" },
  { value: "department",     label: "Department/org" },
  { value: "cc_name",        label: "Cost center name" },
  { value: "cc_dept",        label: "Cost center department" },
  { value: "cc_updated_at",  label: "Cost center updated date" },
  { value: "ledger_key",     label: "Account code (join key)" },
  { value: "ledger_category",label: "Mapped category (ledger)" },
]

// ── Per-table-type auto-suggest whitelist ─────────────────────────────────────
// Only these roles are ever auto-suggested from the profile — keeps the display
// clean and avoids mapping columns the user doesn't actually need for BvA.
// `category` is intentionally excluded from actuals: it's optional and only
// needed when categorySource === "column", which is decided in Step 3.
const AUTO_SUGGEST_ALLOWED: Record<string, Set<string>> = {
  actuals: new Set(["period", "amount", "cost_center", "account", "fiscal_year", "fiscal_quarter"]),
  budget:  new Set(["period", "amount", "cost_center", "category", "version", "fiscal_year", "fiscal_quarter"]),
  cc:      new Set(["cost_center", "cc_name", "department", "cc_updated_at"]),
  ledger:  new Set(["ledger_key", "ledger_category"]),
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Reconstruct role map from saved config — enables non-linear navigation. */
function initRoles(config: BvaConfig, tableType: "actuals" | "budget" | "cc" | "ledger"): Record<string, string> {
  const r: Record<string, string> = {}
  if (tableType === "actuals") {
    if (config.actualsDateCol)        r[config.actualsDateCol]        = "period"
    if (config.actualsAmountCol)      r[config.actualsAmountCol]      = "amount"
    if (config.actualsAccountCol)     r[config.actualsAccountCol]     = "account"
    if (config.actualsCostCenterCol)  r[config.actualsCostCenterCol]  = "cost_center"
    if (config.actualsCategoryCol)    r[config.actualsCategoryCol]    = "category"
    if (config.fiscalYearColActuals)  r[config.fiscalYearColActuals]  = "fiscal_year"
    if (config.fiscalQuarterColActuals) r[config.fiscalQuarterColActuals] = "fiscal_quarter"
  }
  if (tableType === "budget") {
    if (config.budgetPeriodCol)        r[config.budgetPeriodCol]       = "period"
    if (config.budgetAmountCol)        r[config.budgetAmountCol]       = "amount"
    if (config.budgetCostCenterCol)    r[config.budgetCostCenterCol]   = "cost_center"
    if (config.budgetCategoryCol)      r[config.budgetCategoryCol]     = "category"
    if (config.budgetVersionCol)       r[config.budgetVersionCol]      = "version"
    if (config.fiscalYearColBudget)    r[config.fiscalYearColBudget]   = "fiscal_year"
    if (config.fiscalQuarterColBudget) r[config.fiscalQuarterColBudget] = "fiscal_quarter"
  }
  if (tableType === "cc") {
    if (config.ccIdCol)        r[config.ccIdCol]        = "cost_center"
    if (config.ccNameCol)      r[config.ccNameCol]      = "cc_name"
    if (config.ccDeptCol)      r[config.ccDeptCol]      = "department"
    if (config.ccUpdatedAtCol) r[config.ccUpdatedAtCol] = "cc_updated_at"
  }
  if (tableType === "ledger") {
    if (config.ledgerAccountKeyCol) r[config.ledgerAccountKeyCol] = "ledger_key"
    if (config.ledgerCategoryCol)   r[config.ledgerCategoryCol]   = "ledger_category"
  }
  return r
}

/** Merge explicit overrides with profile suggestions to get effective roles.
 *
 * Rules:
 * 1. Explicit user picks always win (even "" / skip).
 * 2. Auto-suggestions are filtered to the per-table allowed set — roles not
 *    relevant for this table type are never auto-populated.
 * 3. Only one auto-suggestion per role (first column match wins, in ordinal
 *    order). Prevents duplicate-role warnings when multiple columns look like
 *    the same concept.
 * 4. If the user already has an explicit assignment for a role, suppress any
 *    remaining auto-suggestion for that same role on other columns.
 */
function effectiveRoles(
  overrides: Record<string, string>,
  profile: ProfileResult | undefined,
  tableKey: string,
): Record<string, string> {
  if (!profile) return overrides
  const allowed    = AUTO_SUGGEST_ALLOWED[tableKey] ?? new Set<string>()
  const claimed    = new Set(Object.values(overrides).filter(Boolean))
  const autoUsed   = new Set<string>() // roles already consumed by an auto-suggestion
  const result: Record<string, string> = {}
  for (const col of profile.columns) {
    const explicit = overrides[col.name]
    if (explicit !== undefined) {
      // Always honour the explicit value (even if it's "" / skipped)
      result[col.name] = explicit
    } else {
      const raw       = col.suggestedRole ?? ""
      // Filter to allowed roles for this table type
      const suggested = (raw && allowed.has(raw)) ? raw : ""
      // Suppress if already claimed explicitly or already auto-suggested once
      if (suggested && (claimed.has(suggested) || autoUsed.has(suggested))) {
        result[col.name] = ""
      } else {
        result[col.name] = suggested
        if (suggested) autoUsed.add(suggested)
      }
    }
  }
  return result
}

function profileUrl(fqn: string) {
  return `/api/setup/profile?table=${encodeURIComponent(fqn)}`
}

type PreviewResult = {
  rows: Record<string, unknown>[]
  columns: string[]
}

// ── Column mapping table ───────────────────────────────────────────────────────
// Renders one source table's columns as a list of (column name, detected type,
// distinct-value count, role dropdown) rows. This is the actual "confirm or
// correct a suggested mapping" UI: the server profiles the table ahead of time
// and guesses a role per column from its name/shape (col.suggestedRole); the
// user can accept that guess as-is or override it via the dropdown. Columns
// with no role (skipped) are hidden by default to keep the list focused on
// what actually matters for BvA — "Show all" reveals the rest.

function TableColumnMapper({
  tableLabel, tableKey, fqn, roleMap, profile, isFetching, onChange,
}: {
  tableLabel: string
  tableKey: string
  fqn: string
  roleMap: Record<string, string>
  profile: ProfileResult | undefined
  isFetching: boolean
  onChange: (col: string, role: string) => void
}) {
  const [expanded,    setExpanded]    = useState(true)
  const [showAll,     setShowAll]     = useState(false)
  const [showPreview, setShowPreview] = useState(false)
  const effective = effectiveRoles(roleMap, profile, tableKey)

  // Lazy preview fetch — only triggers once the panel is opened
  const { data: previewData, isFetching: loadingPreview } = useQuery({
    queryKey: ["preview", fqn],
    queryFn:  () => getJson<PreviewResult>(`/api/setup/discover?action=preview&table=${encodeURIComponent(fqn)}`),
    enabled:  showPreview,
    staleTime: Infinity,
  })

  // Default: show only columns that have a role (auto-suggested or user-mapped)
  const visibleCols = !profile ? [] : showAll
    ? profile.columns
    : profile.columns.filter((c) => (effective[c.name] ?? "") !== "")
  const hiddenCount = profile ? profile.columns.length - visibleCols.length : 0

  return (
    <div id={`table-${tableKey}`} data-collapsed={String(!expanded)} className="border rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center bg-muted/30">
        <button
          type="button"
          data-expand-btn="1"
          onClick={() => setExpanded((e) => !e)}
          className="flex-1 flex items-center justify-between px-4 py-3 hover:bg-muted/50 transition-colors text-left"
        >
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-semibold text-sm shrink-0">{tableLabel}</span>
            <span className="text-xs text-muted-foreground font-mono truncate">{fqn}</span>
            {profile && (
              <span className="text-xs text-muted-foreground shrink-0">
                {profile.rowCount.toLocaleString()} rows · {profile.columns.length} cols
              </span>
            )}
          </div>
          {isFetching ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground shrink-0" />
          ) : expanded ? (
            <ChevronUp className="h-4 w-4 text-muted-foreground shrink-0" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" />
          )}
        </button>
        {/* Preview toggle — separate from expand/collapse */}
        {profile && (
          <button
            type="button"
            onClick={() => setShowPreview((p) => !p)}
            className="px-3 py-3 text-muted-foreground hover:text-foreground transition-colors shrink-0"
            title={showPreview ? "Hide preview" : "Preview table data"}
          >
            {showPreview ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        )}
      </div>

      {/* Inline data preview panel */}
      {showPreview && expanded && (
        <div className="border-b bg-muted/5" style={{ maxHeight: 156, overflowY: "auto", overflowX: "auto" }}>
          {loadingPreview ? (
            <div className="flex items-center gap-2 px-4 py-3 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading preview…
            </div>
          ) : previewData && previewData.rows.length > 0 ? (
            <table className="text-[10px] whitespace-nowrap">
              <thead className="bg-muted/30 sticky top-0">
                <tr>
                  {previewData.columns.map((c: string) => (
                    <th key={c} className="px-2 py-1 text-left font-mono font-medium border-r last:border-r-0">{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {previewData.rows.map((row: Record<string, unknown>, i: number) => (
                  <tr key={i} className="border-t">
                    {previewData.columns.map((c: string) => (
                      <td key={c} className="px-2 py-1 text-muted-foreground max-w-[140px] truncate border-r last:border-r-0">
                        {String(row[c] ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="px-4 py-3 text-xs text-muted-foreground">No rows returned</p>
          )}
        </div>
      )}

      {expanded && profile && (
        <table className="w-full text-xs">
          <thead className="bg-muted/20">
            <tr>
              <th className="text-left px-4 py-2 font-medium w-[40%]">Column</th>
              <th className="text-left px-3 py-2 font-medium w-[10%]">Type</th>
              <th className="text-right px-3 py-2 font-medium w-[15%]">Distinct</th>
              <th className="text-left px-3 py-2 font-medium w-[35%]">Role</th>
            </tr>
          </thead>
          <tbody>
            {visibleCols.map((col) => {
              const role = effective[col.name] ?? ""
              const isSkipped = !role
              return (
                <tr
                  key={col.name}
                  id={`col-${tableKey}-${col.name}`}
                  className={`border-t transition-colors duration-700 ${isSkipped ? "opacity-40" : ""}`}
                >
                  <td className="px-4 py-2">
                    <div className="font-mono text-xs">{col.name}</div>
                    {col.samples.length > 0 && (
                      <div className="text-[10px] text-muted-foreground mt-0.5 truncate max-w-[220px]">
                        {col.samples.slice(0, 3).join(", ")}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{col.dataType.toLowerCase()}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                    {col.cardinality > 0 ? col.cardinality.toLocaleString() : "—"}
                    {col.cardinalityPct != null && col.cardinalityPct > 0 && (
                      <span className="text-[10px] ml-1">({col.cardinalityPct}%)</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1">
                      <select
                        className="flex-1 border rounded px-2 py-1 text-xs bg-background"
                        value={role}
                        onChange={(e) => onChange(col.name, e.target.value)}
                      >
                        {ROLE_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                      </select>
                      {!isSkipped && (
                        <button
                          type="button"
                          onClick={() => onChange(col.name, "")}
                          className="shrink-0 text-muted-foreground hover:text-foreground leading-none"
                          title="Skip this column"
                        >
                          ×
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
            {/* Footer: reveal hidden columns */}
            {(hiddenCount > 0 || showAll) && (
              <tr>
                <td colSpan={4} className="px-4 py-2.5 text-center border-t">
                  {showAll ? (
                    <button
                      type="button"
                      onClick={() => setShowAll(false)}
                      className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                    >
                      Show less
                    </button>
                  ) : (
                    <button
                      type="button"
                      data-showall-btn="1"
                      onClick={() => setShowAll(true)}
                      className="text-xs text-primary hover:underline transition-colors"
                    >
                      + {hiddenCount} more columns not auto-detected — Show all
                    </button>
                  )}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {expanded && !profile && !isFetching && (
        <p className="px-4 py-3 text-sm text-muted-foreground">Enter a valid table name to load columns.</p>
      )}
    </div>
  )
}

// ── Step 2: Column mapping ────────────────────────────────────────────────────
//
// This is where the user confirms (or corrects) which raw column in each
// source table plays which BvA role — date, amount, account code, cost
// center, etc. (see the ColumnInfo comment above for what a "role" is). It
// mirrors Phase 2 of the setup agent's discovery flow (ARCHITECTURE.md): the
// server profiles each table and suggests roles from column names/shape, and
// this component renders that as an editable confirmation UI instead of a
// chat transcript. Once the user is happy with the mapping, applyAndNext()
// translates the per-table role maps back into the named BvaConfig fields
// (actualsDateCol, budgetAmountCol, etc.) that buildFromConfig() actually
// reads.
export function StepColumns({
  config, update, nav,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
  nav: Nav
}) {
  // Role maps — initialized from saved config so editing is non-destructive
  const [actualsRoles, setActualsRoles] = useState<Record<string, string>>(
    () => initRoles(config, "actuals")
  )
  const [budgetRoles, setBudgetRoles] = useState<Record<string, string>>(
    () => initRoles(config, "budget")
  )
  const [ccRoles, setCcRoles] = useState<Record<string, string>>(
    () => initRoles(config, "cc")
  )
  const [ledgerRoles, setLedgerRoles] = useState<Record<string, string>>(
    () => initRoles(config, "ledger")
  )

  // Re-sync roles if the saved config version changes (handles the race where React Query
  // cached data makes steps navigable before the useEffect in the wizard fires setConfig).
  // Only fires when updatedAt changes — won't reset manual overrides during a wizard session.
  useEffect(() => {
    setActualsRoles(initRoles(config, "actuals"))
    setBudgetRoles(initRoles(config, "budget"))
    setCcRoles(initRoles(config, "cc"))
    setLedgerRoles(initRoles(config, "ledger"))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.updatedAt])

  // Profile queries — lift to this level so the sidebar can read column lists
  const { data: actualsProfile, isFetching: loadingActuals } = useQuery({
    queryKey: ["profile", config.actualsTable],
    queryFn: () => getJson<ProfileResult>(profileUrl(config.actualsTable!)),
    enabled: !!config.actualsTable,
  })
  const { data: budgetProfile, isFetching: loadingBudget } = useQuery({
    queryKey: ["profile", config.budgetTable],
    queryFn: () => getJson<ProfileResult>(profileUrl(config.budgetTable!)),
    enabled: !!config.budgetTable,
  })
  const { data: ccProfile, isFetching: loadingCC } = useQuery({
    queryKey: ["profile", config.costCenterTable],
    queryFn: () => getJson<ProfileResult>(profileUrl(config.costCenterTable!)),
    enabled: !!config.costCenterTable,
  })
  const { data: ledgerProfile, isFetching: loadingLedger } = useQuery({
    queryKey: ["profile", config.ledgerMappingTable],
    queryFn: () => getJson<ProfileResult>(profileUrl(config.ledgerMappingTable!)),
    enabled: !!config.ledgerMappingTable,
  })

  // Ledger mapping validation: runs when all four join/category columns are assigned.
  // Checks both the JOIN KEY (actuals account → ledger account key) and
  // the CATEGORY values (ledger category → budget category).
  // Always returns sample values so the user can eyeball the match.
  const selectedLedgerAccountKeyCol = useMemo(
    () => Object.entries(ledgerRoles).find(([, r]) => r === "ledger_key")?.[0],
    [ledgerRoles],
  )
  const selectedLedgerCategoryCol = useMemo(
    () => Object.entries(ledgerRoles).find(([, r]) => r === "ledger_category")?.[0],
    [ledgerRoles],
  )
  const selectedActualsAccountCol = useMemo(
    () => Object.entries(actualsRoles).find(([, r]) => r === "account")?.[0],
    [actualsRoles],
  )
  const selectedBudgetCategoryCol = useMemo(
    () => Object.entries(budgetRoles).find(([, r]) => r === "category")?.[0],
    [budgetRoles],
  )
  const overlapEnabled = !!(
    config.ledgerMappingTable && selectedLedgerAccountKeyCol && selectedLedgerCategoryCol &&
    config.actualsTable && selectedActualsAccountCol &&
    config.budgetTable && selectedBudgetCategoryCol
  )
  type LedgerMappingCheckResult = {
    actKeyCount: number; lmKeyCount: number; keyOverlapCount: number; keyOverlapPct: number
    actKeySample: string; lmKeySample: string
    lmCatCount: number; budCatCount: number; catOverlapCount: number; catOverlapPct: number
    lmCatSample: string; budCatSample: string
  }
  const ledgerKeyStripPrefixes  = config.keyStripPrefixes?.ledgerAccountKeyCol ?? []
  const actualsKeyStripPrefixes = config.keyStripPrefixes?.actualsAccountCol ?? []
  const { data: overlapData, isFetching: overlapLoading } = useQuery({
    queryKey: ["ledger-check", config.ledgerMappingTable, selectedLedgerAccountKeyCol, selectedLedgerCategoryCol, config.actualsTable, selectedActualsAccountCol, config.budgetTable, selectedBudgetCategoryCol, ledgerKeyStripPrefixes, actualsKeyStripPrefixes],
    queryFn: () => getJson<LedgerMappingCheckResult>(
      `/api/setup/discover?action=category-overlap` +
      `&ledgerTable=${encodeURIComponent(config.ledgerMappingTable!)}` +
      `&ledgerAccountKeyCol=${encodeURIComponent(selectedLedgerAccountKeyCol!)}` +
      `&ledgerCategoryCol=${encodeURIComponent(selectedLedgerCategoryCol!)}` +
      `&actualsTable=${encodeURIComponent(config.actualsTable!)}` +
      `&actualsAccountCol=${encodeURIComponent(selectedActualsAccountCol!)}` +
      `&budgetTable=${encodeURIComponent(config.budgetTable!)}` +
      `&budgetCategoryCol=${encodeURIComponent(selectedBudgetCategoryCol!)}` +
      (ledgerKeyStripPrefixes.length  ? `&ledgerKeyStripPrefixes=${encodeURIComponent(ledgerKeyStripPrefixes.join(","))}`  : "") +
      (actualsKeyStripPrefixes.length ? `&actualsKeyStripPrefixes=${encodeURIComponent(actualsKeyStripPrefixes.join(","))}` : ""),
    ),
    enabled: overlapEnabled,
    staleTime: 60_000,
  })

  // Sidebar validation sections — recompute when roles or profiles change
  const sidebarSections = useMemo(() => {
    const sections: Array<{ key: string; label: string; checks: ReturnType<typeof validateActuals> }> = []
    const categoryRequired = config.categorySource !== "mapping_table"

    if (config.actualsTable && actualsProfile) {
      const eff = effectiveRoles(actualsRoles, actualsProfile, "actuals")
      const cols = actualsProfile.columns.map((c) => c.name)
      sections.push({ key: "actuals", label: "Actuals / GL", checks: validateActuals(eff, cols, { categoryRequired }) })
    }
    if (config.budgetTable && budgetProfile) {
      const eff = effectiveRoles(budgetRoles, budgetProfile, "budget")
      const cols = budgetProfile.columns.map((c) => c.name)
      sections.push({ key: "budget", label: "Budget / Forecast", checks: validateBudget(eff, cols) })
    }
    if (config.costCenterTable && ccProfile) {
      const eff = effectiveRoles(ccRoles, ccProfile, "cc")
      const cols = ccProfile.columns.map((c) => c.name)
      sections.push({ key: "cc", label: "Cost Centers", checks: validateCC(eff, cols) })
    }
    if (config.ledgerMappingTable && ledgerProfile) {
      const eff = effectiveRoles(ledgerRoles, ledgerProfile, "ledger")
      const cols = ledgerProfile.columns.map((c) => c.name)
      sections.push({ key: "ledger", label: "Ledger Account Mapping", checks: validateLedger(eff, cols) })
    }
    return sections
  }, [actualsRoles, budgetRoles, ccRoles, ledgerRoles, actualsProfile, budgetProfile, ccProfile, ledgerProfile, config.categorySource, config.actualsTable, config.budgetTable, config.costCenterTable, config.ledgerMappingTable])

  const handleRoleChange = (tableKey: string, col: string, role: string) => {
    if (tableKey === "actuals") setActualsRoles((prev) => ({ ...prev, [col]: role }))
    else if (tableKey === "budget") setBudgetRoles((prev) => ({ ...prev, [col]: role }))
    else if (tableKey === "cc") setCcRoles((prev) => ({ ...prev, [col]: role }))
    else if (tableKey === "ledger") setLedgerRoles((prev) => ({ ...prev, [col]: role }))
  }

  const isSingle = config.sourceType === "single_table"

  // Converts the per-table role maps (column name -> role) back into the
  // named BvaConfig fields buildFromConfig() actually reads (actualsDateCol,
  // budgetAmountCol, etc.), then advances to the next step. findRole() looks
  // up whichever column currently holds a given role, after applying the
  // same effectiveRoles() merge (explicit picks + auto-suggestions) used for
  // display, so what gets saved matches exactly what's shown on screen.
  const applyAndNext = () => {
    const patch: Partial<BvaConfig> = {}
    const findRole = (roles: Record<string, string>, profile: ProfileResult | undefined, role: string, tableKey: string) => {
      const eff = effectiveRoles(roles, profile, tableKey)
      return Object.entries(eff).find(([, r]) => r === role)?.[0]
    }

    if (config.actualsTable) {
      patch.actualsDateCol       = findRole(actualsRoles, actualsProfile, "period",         "actuals")
      patch.actualsAmountCol     = findRole(actualsRoles, actualsProfile, "amount",         "actuals")
      patch.actualsAccountCol    = findRole(actualsRoles, actualsProfile, "account",        "actuals")
      patch.actualsCostCenterCol = findRole(actualsRoles, actualsProfile, "cost_center",    "actuals")
      patch.actualsCategoryCol   = findRole(actualsRoles, actualsProfile, "category",       "actuals")
      const hasFyCols            = !!findRole(actualsRoles, actualsProfile, "fiscal_year",   "actuals")
      patch.fiscalPath           = hasFyCols ? "A" : "B"
      if (hasFyCols) {
        patch.fiscalYearColActuals    = findRole(actualsRoles, actualsProfile, "fiscal_year",   "actuals")
        patch.fiscalQuarterColActuals = findRole(actualsRoles, actualsProfile, "fiscal_quarter", "actuals")
      }
    }
    if (config.budgetTable) {
      patch.budgetPeriodCol     = findRole(budgetRoles, budgetProfile, "period",         "budget")
      patch.budgetAmountCol     = findRole(budgetRoles, budgetProfile, "amount",         "budget")
      patch.budgetCostCenterCol = findRole(budgetRoles, budgetProfile, "cost_center",    "budget")
      patch.budgetCategoryCol   = findRole(budgetRoles, budgetProfile, "category",       "budget")
      patch.budgetVersionCol    = findRole(budgetRoles, budgetProfile, "version",        "budget") ?? null
      patch.fiscalYearColBudget    = findRole(budgetRoles, budgetProfile, "fiscal_year",    "budget")
      patch.fiscalQuarterColBudget = findRole(budgetRoles, budgetProfile, "fiscal_quarter", "budget")
      const hasPeriod           = !!patch.budgetPeriodCol
      const hasFy               = !!patch.fiscalYearColBudget
      const hasFq               = !!patch.fiscalQuarterColBudget
      if (hasFy && !hasPeriod)  patch.budgetPeriodFormat = "annual"
      else if (hasFq)           patch.budgetPeriodFormat = "fiscal_quarter_string"
      else                      patch.budgetPeriodFormat = "date"
    }
    if (config.costCenterTable) {
      patch.ccIdCol        = findRole(ccRoles, ccProfile, "cost_center",    "cc")
      patch.ccNameCol      = findRole(ccRoles, ccProfile, "cc_name",        "cc")
      patch.ccDeptCol      = findRole(ccRoles, ccProfile, "department",     "cc") ?? findRole(ccRoles, ccProfile, "cc_dept", "cc")
      patch.ccUpdatedAtCol = findRole(ccRoles, ccProfile, "cc_updated_at",  "cc")
    }
    if (config.ledgerMappingTable) {
      patch.ledgerAccountKeyCol = findRole(ledgerRoles, ledgerProfile, "ledger_key",      "ledger")
      patch.ledgerCategoryCol   = findRole(ledgerRoles, ledgerProfile, "ledger_category", "ledger")
    }

    update(patch)
    nav.next()
  }

  // Single-table: skip column mapping
  if (isSingle) {
    return (
      <div className="space-y-6 max-w-xl">
        <div>
          <h2 className="text-lg font-semibold">Step 2 — Column mapping</h2>
          <p className="text-muted-foreground text-sm mt-1">
            Using a single pre-joined table — column mapping is not needed.
          </p>
        </div>
        <div className="rounded-lg border bg-green-50 dark:bg-green-950/20 border-green-200 dark:border-green-800 p-4 text-sm">
          <p className="font-medium text-green-800 dark:text-green-200">Single-table path — skipping column mapping</p>
          <p className="text-xs text-green-700 dark:text-green-300 mt-1">
            Columns are standard (fiscal_year, fiscal_quarter, expense_category, etc.). Proceed to fiscal config.
          </p>
        </div>
        <div className="flex gap-3">
          <button onClick={nav.back} className="px-4 py-2 rounded-lg border text-sm hover:bg-muted transition-colors">Back</button>
          <button onClick={nav.next} className="px-5 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90">
            Next: Fiscal config
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Step 2 — Column mapping</h2>
        <p className="text-muted-foreground text-sm mt-1">
          Assign each column to its BvA role. Auto-suggestions are shown — override any that look wrong.
          The sidebar tracks required roles and blocks Next until all errors are resolved.
        </p>
      </div>

      {/* Two-column layout: tables (left) + validation sidebar (right) */}
      <div className="flex gap-5 items-start">
        {/* Column tables */}
        <div className="flex-1 min-w-0 space-y-4">
          <div className="flex gap-3 items-center">
            <button onClick={nav.back} className="px-4 py-2 rounded-lg border text-sm hover:bg-muted transition-colors">
              Back
            </button>
          </div>

          {config.actualsTable && (
            <TableColumnMapper
              tableLabel="Actuals / GL"
              tableKey="actuals"
              fqn={config.actualsTable}
              roleMap={actualsRoles}
              profile={actualsProfile}
              isFetching={loadingActuals}
              onChange={(col, role) => setActualsRoles((prev) => ({ ...prev, [col]: role }))}
            />
          )}
          {config.budgetTable && (
            <TableColumnMapper
              tableLabel="Budget / Forecast"
              tableKey="budget"
              fqn={config.budgetTable}
              roleMap={budgetRoles}
              profile={budgetProfile}
              isFetching={loadingBudget}
              onChange={(col, role) => setBudgetRoles((prev) => ({ ...prev, [col]: role }))}
            />
          )}
          {config.costCenterTable && (
            <TableColumnMapper
              tableLabel="Cost Center Dimension"
              tableKey="cc"
              fqn={config.costCenterTable}
              roleMap={ccRoles}
              profile={ccProfile}
              isFetching={loadingCC}
              onChange={(col, role) => setCcRoles((prev) => ({ ...prev, [col]: role }))}
            />
          )}
          {config.ledgerMappingTable && (
            <>
              <TableColumnMapper
                tableLabel="Ledger Account Mapping"
                tableKey="ledger"
                fqn={config.ledgerMappingTable}
                roleMap={ledgerRoles}
                profile={ledgerProfile}
                isFetching={loadingLedger}
                onChange={(col, role) => setLedgerRoles((prev) => ({ ...prev, [col]: role }))}
              />
              {/* Ledger mapping validation banner — shown when all four columns are assigned */}
              {overlapEnabled && (
                <div className="space-y-2">
                  {overlapLoading && (
                    <div className="flex items-center gap-2 rounded-lg border border-muted bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Checking join key and category overlap…
                    </div>
                  )}
                  {!overlapLoading && overlapData && (() => {
                    const keyOk  = overlapData.keyOverlapPct >= 50
                    const catOk  = overlapData.catOverlapCount > 0
                    const catGood = overlapData.catOverlapPct >= 30
                    return (
                      <>
                        {/* Join key check */}
                        <div className={`rounded-lg border px-4 py-3 text-sm ${
                          keyOk
                            ? "border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/20"
                            : "border-destructive/40 bg-destructive/5"
                        }`}>
                          <div className="flex items-start gap-2">
                            {keyOk
                              ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
                              : <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                            }
                            <div className="min-w-0">
                              <p className={`font-medium ${keyOk ? "text-green-800 dark:text-green-200" : "text-destructive"}`}>
                                Join key: {overlapData.keyOverlapPct}% of actuals account codes found in ledger mapping
                                {!keyOk && " — possible format mismatch"}
                              </p>
                              <div className="mt-1.5 grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                                <div>
                                  <span className="font-medium block mb-0.5">Actuals ({selectedActualsAccountCol}) examples:</span>
                                  <span className="font-mono">{overlapData.actKeySample || "(none)"}</span>
                                </div>
                                <div>
                                  <span className="font-medium block mb-0.5">Ledger key ({selectedLedgerAccountKeyCol}) examples:</span>
                                  <span className="font-mono">{overlapData.lmKeySample || "(none)"}</span>
                                </div>
                              </div>
                              {!keyOk && (
                                <p className="mt-1 text-xs text-muted-foreground">
                                  If formats differ (e.g. leading zeros, prefixes), check if the account column needs trimming or casting.
                                </p>
                              )}
                            </div>
                          </div>
                        </div>

                        {/* Category check */}
                        <div className={`rounded-lg border px-4 py-3 text-sm ${
                          !catOk
                            ? "border-destructive/40 bg-destructive/5"
                            : !catGood
                              ? "border-yellow-300 dark:border-yellow-700 bg-yellow-50 dark:bg-yellow-950/20"
                              : "border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/20"
                        }`}>
                          <div className="flex items-start gap-2">
                            {!catOk && <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />}
                            {catOk && !catGood && <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-yellow-600" />}
                            {catOk && catGood && <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />}
                            <div className="min-w-0">
                              <p className={`font-medium ${
                                !catOk ? "text-destructive"
                                : !catGood ? "text-yellow-800 dark:text-yellow-200"
                                : "text-green-800 dark:text-green-200"
                              }`}>
                                {!catOk
                                  ? `Expense category: no overlap — ${selectedLedgerCategoryCol} values don't match budget categories`
                                  : `Expense category: ${overlapData.catOverlapCount} of ${overlapData.lmCatCount} categories match budget (${overlapData.catOverlapPct}%)`
                                }
                              </p>
                              <div className="mt-1.5 grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                                <div>
                                  <span className="font-medium block mb-0.5">Ledger ({selectedLedgerCategoryCol}) examples:</span>
                                  <span>{overlapData.lmCatSample || "(none)"}</span>
                                </div>
                                <div>
                                  <span className="font-medium block mb-0.5">Budget ({selectedBudgetCategoryCol}) expects:</span>
                                  <span>{overlapData.budCatSample || "(none)"}</span>
                                </div>
                              </div>
                              {!catOk && (
                                <p className="mt-1 text-xs text-muted-foreground">
                                  Select a different column whose values match the budget categories shown above.
                                </p>
                              )}
                            </div>
                          </div>
                        </div>
                      </>
                    )
                  })()}
                </div>
              )}
            </>
          )}
        </div>

        {/* Validation sidebar — sticky with internal scroll so it never clips */}
        {sidebarSections.length > 0 && (
          <div className="w-72 shrink-0 sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto">
            <ValidationSidebar
              tables={sidebarSections}
              onSetRole={handleRoleChange}
              onNext={applyAndNext}
              nextLabel="Next: Fiscal config"
            />
          </div>
        )}
      </div>
    </div>
  )
}
