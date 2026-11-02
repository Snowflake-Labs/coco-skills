"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { ChevronUp, ChevronDown, ChevronsUpDown, ChevronRight } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { ChartTooltip, formatTick } from "@/components/chart-utils"
import { resolveVersionPeriod, quarterMonthLabel } from "@/lib/bva"
import { fmtPct, deltaColor, getJson } from "@/lib/format"
import { Select, MultiSelect, SavedPresetsBar } from "@/components/bva-controls"
import { downloadExcel } from "@/lib/export"
import { useChatContext } from "@/lib/chat-context"
import type { BvaConfig, FilterPreset } from "@/lib/config"
import { isConfigured } from "@/lib/config"

const A_COLOR = "#29B5E8"  // Snowflake blue — V1 actuals
const B_COLOR = "#0F5FA8"  // Snowflake dark blue — V2 forecast/budget
const ACTUALS = "Actuals"

// Full-dollar accounting format (not thousands)
function fmtDollar(v: number): string {
  if (v < 0) return `($${Math.abs(Math.round(v)).toLocaleString()})`
  return `$${Math.round(v).toLocaleString()}`
}

function formatColName(col: string): string {
  return col.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(" ")
}

function ExportButton({ onClick, label = "Export to Excel" }: { onClick: () => void; label?: string }) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md border border-border hover:bg-muted transition-colors shrink-0"
    >
      ↓ {label}
    </button>
  )
}

type Filters = {
  seriesOptions: string[]
  years: string[]
  quarters: string[]
  months: string[]
  grain: "quarter" | "month"
  organizations: string[]
  costCenters: string[]
  categories: string[]
  defaultVersion: string
  fyStartMonth?: number
  actualsAnchor?: string
  extraFilters?: { column: string; label: string; values: string[] }[]
  capabilities?: {
    hasOrganization: boolean
    hasMonthlyGrain: boolean
    hasCategories: boolean
    hasMultipleYears: boolean
  }
}
type BreakdownRow = { v1: number; v2: number; delta: number; deltaPct: number | null }
type CcRow = BreakdownRow & { costCenterId: string; costCenterName: string; department: string }
type CatRow = BreakdownRow & { category: string }
type Breakdown = {
  version1: string
  version2: string
  category: string
  currentLabel: string
  categorySummary: CatRow[]
  costCenters: CcRow[]
  chart: { costCenter: string; v1: number; v2: number }[]
}
type EntriesResult = {
  columns: string[]
  rows: Record<string, unknown>[]
  total: number
  unavailable?: string
}

// ── Sort helpers ───────────────────────────────────────────────────────────────

type SortDir = "asc" | "desc"
type SortState = { key: string; dir: SortDir }

// Generic client-side table sort — works on any row shape since the sort
// key is looked up dynamically. Nulls always sort to the bottom regardless
// of direction (rather than flipping to the top on "desc"), since a missing
// value isn't meaningfully "low" or "high".
function sortRows<T extends Record<string, unknown>>(rows: T[], sort: SortState): T[] {
  return [...rows].sort((a, b) => {
    const av = a[sort.key]; const bv = b[sort.key]
    if (av == null && bv == null) return 0
    if (av == null) return 1; if (bv == null) return -1
    let cmp = 0
    if (typeof av === "number" && typeof bv === "number") cmp = av - bv
    else cmp = String(av).localeCompare(String(bv))
    return sort.dir === "asc" ? cmp : -cmp
  })
}

function SortHeader({ label, sortKey, sort, onSort, className }: {
  label: string; sortKey: string; sort: SortState
  onSort: (key: string) => void; className?: string
}) {
  const active = sort.key === sortKey
  const Icon = active ? (sort.dir === "asc" ? ChevronUp : ChevronDown) : ChevronsUpDown
  return (
    <TableHead
      className={`cursor-pointer select-none hover:text-foreground ${active ? "text-foreground" : "text-muted-foreground"} ${className ?? ""}`}
      onClick={() => onSort(sortKey)}
    >
      <span className="inline-flex items-center gap-1">{label}<Icon className="h-3 w-3 shrink-0" /></span>
    </TableHead>
  )
}

// ── Level 3: Journal entries ───────────────────────────────────────────────────

/**
 * Deepest drill-down level: the raw journal-entry rows behind a single
 * (category, cost center, fiscal year, quarter) combination. Columns are
 * whatever the underlying source table returns — this component doesn't
 * know the schema ahead of time, it just renders `data.columns` generically.
 */
function JournalEntriesTable({
  configName,
  category,
  costCenterId,
  costCenterName,
  fiscalYear,
  quarter,
}: {
  configName: string
  category: string
  costCenterId: string
  costCenterName: string
  fiscalYear: string
  quarter: string
}) {
  const params = new URLSearchParams({ category, costCenterId, fiscalYear, quarter }).toString()
  const { data, isFetching, error } = useQuery({
    queryKey: ["entries", configName, category, costCenterId, fiscalYear, quarter],
    queryFn:  () => getJson<EntriesResult>(`/api/breakdown/entries?${params}`),
    staleTime: 60_000,
  })

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span className="font-semibold text-foreground text-base">Drill into Cost Center</span>
          <ChevronRight className="h-4 w-4" />
          <span>{costCenterName}</span>
        </div>
      </CardHeader>
      <CardContent>
        {isFetching && <Skeleton className="h-64" />}
        {error && <p className="text-sm text-destructive">Error: {(error as Error).message}</p>}
        {data?.unavailable && (
          <p className="text-sm text-muted-foreground italic">{data.unavailable}</p>
        )}
        {data && !data.unavailable && data.rows.length === 0 && (
          <p className="text-sm text-muted-foreground">No journal entries found for this selection.</p>
        )}
        {data && !data.unavailable && data.rows.length > 0 && (
          <>
            <p className="text-xs text-muted-foreground mb-3">
              Showing {data.rows.length}{data.total >= 1000 ? "+" : ""} rows
            </p>
            <div className="max-h-[450px] overflow-y-auto overflow-x-auto rounded-md border">
              <Table className="text-xs whitespace-nowrap [&_td]:py-1 [&_th]:py-2">
                <TableHeader className="sticky top-0 bg-white z-10 shadow-[0_1px_0_0_hsl(var(--border))]">
                  <TableRow className="bg-slate-50">
                    {data.columns.map((col) => (
                      <TableHead key={col} className="text-xs font-semibold">
                        {formatColName(col)}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.rows.map((row, i) => (
                    <TableRow key={i} className={i % 2 === 0 ? "bg-white" : "bg-slate-50"}>
                      {data.columns.map((col) => (
                        <TableCell key={col} className="py-1.5 text-xs">
                          {row[col] == null ? (
                            <span className="text-muted-foreground">—</span>
                          ) : (
                            String(row[col])
                          )}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

/**
 * Expense Breakdown page: a three-level drill-down from category totals →
 * cost centers within a category → raw journal entries for one cost
 * center, alongside the same top-level version/fiscal filters used on the
 * main BvA Working dashboard (see components/bva-dashboard.tsx). Unlike
 * that page, this one doesn't sync its filters into the chat agent context
 * (lib/chat-context.tsx) — it's presentation-only.
 */
export function ExpenseBreakdown() {
  const { data: presetsData, refetch: refetchPresets } = useQuery({
    queryKey: ["presets"],
    queryFn:  () => getJson<{ presets: FilterPreset[] }>("/api/setup/presets"),
    staleTime: 5 * 60 * 1000,
  })
  const presets = presetsData?.presets ?? []

  const { data: filters } = useQuery({
    queryKey: ["filters"],
    queryFn: () => getJson<Filters>("/api/filters"),
    staleTime: 60_000,
  })
  const { data: configData } = useQuery({
    queryKey: ["setup-config"],
    queryFn: () => getJson<{ config: BvaConfig | null }>("/api/setup/config"),
    staleTime: 30_000,
  })
  const { dashboardResetSignal } = useChatContext()

  const configLoading = configData === undefined
  const configured = isConfigured(configData?.config)
  const configName = configData?.config?.configName ?? "default"

  // Filter bar state
  const [version1, setVersion1] = useState("")
  const [version2, setVersion2] = useState("")
  const [fiscalYear, setFiscalYear] = useState("")
  const [quarter, setQuarter] = useState("")
  const [organizations, setOrganizations] = useState<string[]>([])
  const [costCenters, setCostCenters] = useState<string[]>([])
  const [categories, setCategories] = useState<string[]>([])
  // Selected values for any ad-hoc filters contributed by enabled custom
  // logic derived columns (see lib/bva.ts's parseExtraFilters) — column
  // alias -> selected values.
  const [extraFilterState, setExtraFilterState] = useState<Record<string, string[]>>({})

  // Drill-down state (separate from filter bar): which category row and,
  // within it, which cost center row the user has clicked into.
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null)
  const [selectedCostCenter, setSelectedCostCenter] = useState<{ id: string; name: string } | null>(null)

  // Sort state
  const [ccSort, setCcSort] = useState<SortState>({ key: "delta", dir: "desc" })
  const [catSort, setCatSort] = useState<SortState>({ key: "v1", dir: "desc" })

  // Scroll refs for progressive drill-down
  const level2Ref = useRef<HTMLDivElement>(null)
  const level3Ref = useRef<HTMLDivElement>(null)

  // Reset all filter/drill-down/sort state to blank when the dashboard reset
  // signal bumps (see lib/chat-context.tsx's resetDashboard). didMountReset
  // skips the very first render so the effect only fires on later resets,
  // not on initial mount.
  const didMountReset = useRef(false)
  useEffect(() => {
    if (!didMountReset.current) { didMountReset.current = true; return }
    setVersion1(""); setVersion2(""); setFiscalYear(""); setQuarter("")
    setOrganizations([]); setCostCenters([]); setCategories([]); setExtraFilterState({})
    setSelectedCategory(null); setSelectedCostCenter(null)
    setCcSort({ key: "delta", dir: "desc" }); setCatSort({ key: "v1", dir: "desc" })
  }, [dashboardResetSignal])

  // Prune any extraFilterState selection whose column is no longer one of
  // the currently-enabled custom logic rules' extraFilters — see the
  // identical effect in components/bva-dashboard.tsx for why this matters
  // (a stale alias otherwise reaches Snowflake and 500s the whole query).
  useEffect(() => {
    if (!filters) return
    const validCols = new Set((filters.extraFilters ?? []).map((f) => f.column))
    setExtraFilterState((prev) => {
      const next = Object.fromEntries(Object.entries(prev).filter(([k]) => validCols.has(k)))
      return Object.keys(next).length === Object.keys(prev).length ? prev : next
    })
  }, [filters])

  // Populate default filter values once /api/filters resolves, and only
  // for whichever of version1/fiscalYear/quarter are still unset — lets a
  // user's own selection (or a loaded preset) stand instead of being
  // overwritten every time this effect re-runs. Version 2 defaults to
  // filters.defaultVersion (server-side pick) and, when the version's
  // period is known, narrows fiscal year/quarter to match it rather than
  // leaving the "last year in the list" fallback used otherwise.
  useEffect(() => {
    if (!filters) return
    const def = filters.seriesOptions.includes(filters.defaultVersion) ? filters.defaultVersion : filters.seriesOptions[1]
    if (!version1) setVersion1(ACTUALS)
    if (!version2) {
      setVersion2(def)
      const p = resolveVersionPeriod(def, configData?.config?.versionPeriods)
      if (!fiscalYear) setFiscalYear(p?.year ?? (filters.years.includes("FY2027") ? "FY2027" : filters.years[filters.years.length - 1]))
      if (!quarter) setQuarter(p?.quarter ?? "Full Year")
    } else {
      if (!fiscalYear) setFiscalYear(filters.years.includes("FY2027") ? "FY2027" : filters.years[filters.years.length - 1])
      if (!quarter) setQuarter("Full Year")
    }
  }, [filters, version1, version2, fiscalYear, quarter])

  // Auto-scroll so the newly revealed cost-center table is visible without
  // the user having to scroll manually after clicking a category row.
  useEffect(() => {
    if (selectedCategory && level2Ref.current) {
      setTimeout(() => level2Ref.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100)
    }
  }, [selectedCategory])

  // Same auto-scroll, one level deeper, when a cost center row is clicked.
  useEffect(() => {
    if (selectedCostCenter && level3Ref.current) {
      setTimeout(() => level3Ref.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100)
    }
  }, [selectedCostCenter])

  // Selecting a version also snaps fiscal year/quarter to that version's
  // known period (same "narrow the filters to match the version" behavior
  // used on the main BvA dashboard — see resolveVersionPeriod in lib/bva.ts).
  const onVersion = (setter: (v: string) => void) => (v: string) => {
    setter(v)
    const p = resolveVersionPeriod(v, configData?.config?.versionPeriods)
    if (p) { if (p.year) setFiscalYear(p.year); setQuarter(p.quarter ?? "Full Year") }
  }

  // Toggles sort direction when the same column header is clicked twice;
  // switching to a different column always starts at "desc".
  const onSort = (setSort: React.Dispatch<React.SetStateAction<SortState>>) => (key: string) => {
    setSort((prev: SortState) => ({ key, dir: prev.key === key ? (prev.dir === "asc" ? "desc" : "asc") : "desc" }))
  }

  // The category filter sent to the API: use drill selection if active, else the multi-select filter
  const activeCategoryFilter = selectedCategory ?? categories.join(",")

  // Builds the /api/breakdown query string from current filter state.
  // Memoized so the useQuery below only re-fetches when one of these
  // actually changes, not on every render.
  const params = useMemo(
    () => new URLSearchParams({
      version1, version2, fiscalYear, quarter,
      organization: organizations.join(","),
      costCenter:   costCenters.join(","),
      category:     activeCategoryFilter,
      ...(Object.keys(extraFilterState).length ? { extraFilters: JSON.stringify(extraFilterState) } : {}),
    }).toString(),
    [version1, version2, fiscalYear, quarter, organizations, costCenters, activeCategoryFilter, extraFilterState],
  )

  const { data, error, isFetching } = useQuery({
    queryKey: ["breakdown", params],
    queryFn: () => getJson<Breakdown>(`/api/breakdown?${params}`),
    enabled: configured && !!version1 && !!version2 && !!fiscalYear,
  })

  // Sorting is applied client-side to whatever the API already returned —
  // re-sorting doesn't require a new request, just a different order.
  const sortedCc  = useMemo(() => data ? sortRows(data.costCenters,    ccSort)  : [], [data, ccSort])
  const sortedCat = useMemo(() => data ? sortRows(data.categorySummary, catSort) : [], [data, catSort])

  const handleExport = () => {
    if (!data) return
    const label = data.currentLabel
    const catRows = sortedCat.map((r) => ({
      "Expense Category": r.category,
      "Actuals ($)":  Math.round(r.v1),
      "Forecast ($)": Math.round(r.v2),
      "Variance ($)": Math.round(r.delta),
      "Variance %":   r.deltaPct,
    }))
    const ccRows = sortedCc.map((r) => ({
      "Cost Center ID":   r.costCenterId,
      "Cost Center":      r.costCenterName,
      "Department":       r.department,
      "Actuals ($)":  Math.round(r.v1),
      "Forecast ($)": Math.round(r.v2),
      "Variance ($)": Math.round(r.delta),
      "Variance %":   r.deltaPct,
    }))
    downloadExcel(`Expense_Breakdown_${label.replace(/\s/g, "_")}`, [
      { name: "Category Summary", rows: catRows },
      { name: "Cost Center Detail", rows: ccRows },
    ])
  }

  // Adaptive filter visibility — only show a filter control when the
  // underlying data actually has that dimension. showCategoryFilter's >= 3
  // threshold mirrors BvaCapabilities.hasCategories in lib/config.ts: fewer
  // than 3 categories isn't worth a dedicated multi-select filter.
  const showOrgFilter      = (filters?.organizations?.length ?? 0) > 0
  const showMonthFilter    = filters?.grain === "month"
  const showCategoryFilter = (filters?.categories?.length ?? 0) >= 3
  const showYearFilter     = (filters?.years?.length ?? 0) > 1
  const showCCFilter       = (filters?.costCenters?.length ?? 0) > 0

  const orgLabel = organizations.length === 0 ? "All Organizations"
    : organizations.length === 1 ? organizations[0]
    : `${organizations.length} Departments`

  // configData loaded but no config saved → demo mode (server uses fallback table).

  return (
    <main className="w-full max-w-[1400px] mx-auto py-8 px-4 space-y-5">
      <div>
        <h1 className="text-2xl font-bold">Expense Breakdown</h1>
        <p className="text-muted-foreground text-sm mt-1">Drill from category → cost center → journal entries.</p>
      </div>

      {/* Demo mode banner */}
      {!configData?.config && !configLoading && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-800">
          Viewing demo data. Go to <strong>Settings</strong> to connect your own budget and actuals tables.
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-start">
        <Select label="Version 1 (rows)" value={version1} options={filters?.seriesOptions ?? []} onChange={onVersion(setVersion1)} />
        <Select label="Version 2 (comparison)" value={version2} options={filters?.seriesOptions ?? []} onChange={onVersion(setVersion2)} />
        {showYearFilter && <Select label="Fiscal year" value={fiscalYear} options={filters?.years ?? []} onChange={setFiscalYear} />}
        <Select label="Quarter" value={quarter} options={["Full Year", ...(filters?.quarters ?? [])]} onChange={setQuarter} />
        {showMonthFilter && (
          <Select label="Month(s)" value="All" options={["All", ...(filters?.months ?? [])]} />
        )}
        {!showMonthFilter && (() => {
          const label = quarterMonthLabel(quarter, filters?.fyStartMonth ?? 1)
          return (
            <Select label="Month(s)" value={label || "N/A"} options={[label || "N/A"]} disabled
              hint={label ? `${label} (quarterly grain)` : "Quarterly data — monthly grain not available"} />
          )
        })()}
        {showOrgFilter && (
          <MultiSelect label="Organization" selected={organizations} options={filters?.organizations ?? []} onChange={setOrganizations} />
        )}
        {showCategoryFilter && (
          <MultiSelect label="Category" selected={categories} options={filters?.categories ?? []} onChange={(v) => { setCategories(v); setSelectedCategory(null); setSelectedCostCenter(null) }} />
        )}
        {showCCFilter && (
          <MultiSelect label="Cost center" selected={costCenters} options={filters?.costCenters ?? []} onChange={setCostCenters} minWidth={200} />
        )}
        {filters?.extraFilters?.map((ef) => (
          <MultiSelect
            key={ef.column}
            label={ef.label}
            selected={extraFilterState[ef.column] ?? []}
            options={ef.values}
            onChange={(vals) =>
              setExtraFilterState((prev) => {
                const next = { ...prev }
                if (vals.length) next[ef.column] = vals
                else delete next[ef.column]
                return next
              })
            }
          />
        ))}
        {(organizations.length > 0 || categories.length > 0 || costCenters.length > 0 || Object.keys(extraFilterState).length > 0) && (
          <button
            type="button"
            onClick={() => { setOrganizations([]); setCategories([]); setCostCenters([]); setExtraFilterState({}) }}
            className="text-xs text-muted-foreground underline hover:text-foreground self-end pb-2"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Saved filter views */}
      <SavedPresetsBar
        presets={presets}
        currentFilters={{ organizations, categories, costCenters, version1, version2, fiscalYear, quarter }}
        onLoad={(p) => {
          setOrganizations(p.filters.organizations); setCategories(p.filters.categories)
          setCostCenters(p.filters.costCenters)
          if (p.filters.version1) setVersion1(p.filters.version1)
          if (p.filters.version2) setVersion2(p.filters.version2)
          if (p.filters.fiscalYear) setFiscalYear(p.filters.fiscalYear)
          if (p.filters.quarter) setQuarter(p.filters.quarter)
        }}
        onSave={async (name, filters) => {
          const preset: FilterPreset = { id: Date.now().toString(), name, createdAt: new Date().toISOString(), filters }
          await fetch("/api/setup/presets", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(preset) })
          refetchPresets()
        }}
        onDelete={async (id) => {
          await fetch(`/api/setup/presets?id=${id}`, { method: "DELETE" })
          refetchPresets()
        }}
      />

      <div className="border-t pt-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">{orgLabel} — {data?.currentLabel ?? "…"}</h2>
          <p className="text-xs text-blue-600 mt-1 font-medium">$ All figures in dollars (not thousands)</p>
        </div>
        {data && <ExportButton onClick={handleExport} />}
      </div>

      {error && <p className="text-sm text-destructive">Error: {(error as Error).message}</p>}

      {/* ── Level 1: Category Summary ─────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle>
            Category Summary — {orgLabel}, {data?.currentLabel ?? "…"}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {error ? (
            <div className="h-64 flex items-center justify-center text-sm text-muted-foreground border rounded-md border-dashed">
              Couldn&apos;t load category data — see error above.
            </div>
          ) : data ? (
            <div className="max-h-[400px] overflow-y-auto rounded-md border">
            <Table className="[&_td]:py-1.5 [&_td]:text-xs [&_th]:py-2 [&_th]:text-xs">
              <TableHeader className="sticky top-0 bg-white z-10 shadow-[0_1px_0_0_hsl(var(--border))]">
                <TableRow>
                  <SortHeader label="Expense Category" sortKey="category" sort={catSort} onSort={onSort(setCatSort)} />
                  <SortHeader label="Actuals" sortKey="v1" sort={catSort} onSort={onSort(setCatSort)} className="text-right" />
                  <SortHeader label="Forecast" sortKey="v2" sort={catSort} onSort={onSort(setCatSort)} className="text-right" />
                  <SortHeader label="Variance" sortKey="delta" sort={catSort} onSort={onSort(setCatSort)} className="text-right" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedCat.map((r) => (
                  <TableRow
                    key={r.category}
                    className={`cursor-pointer transition-colors ${
                      selectedCategory === r.category
                        ? "bg-blue-50 hover:bg-blue-50"
                        : "hover:bg-slate-50"
                    }`}
                    onClick={() => {
                      if (selectedCategory === r.category) {
                        setSelectedCategory(null); setSelectedCostCenter(null)
                      } else {
                        setSelectedCategory(r.category); setSelectedCostCenter(null)
                      }
                    }}
                  >
                    <TableCell className="font-medium">
                      <span className="inline-flex items-center gap-1.5">
                        {selectedCategory === r.category && <ChevronRight className="h-3 w-3 text-blue-600" />}
                        {r.category}
                      </span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{fmtDollar(r.v1)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmtDollar(r.v2)}</TableCell>
                    <TableCell className="text-right tabular-nums" style={{ color: deltaColor(r.delta) }}>{fmtDollar(r.delta)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            </div>
          ) : <Skeleton className="h-64" />}
          {data && !selectedCategory && (
            <p className="text-xs text-muted-foreground mt-3">Click a row to drill into its cost centers.</p>
          )}
        </CardContent>
      </Card>

      {/* ── Level 2: Drill into Category ─────────────────────────────── */}
      {selectedCategory && (
        <div ref={level2Ref} className="space-y-4">
          {/* Bar chart */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <span className="font-semibold text-foreground text-base">Drill into Category</span>
                  <ChevronRight className="h-4 w-4" />
                  <span>{selectedCategory}</span>
                </div>
                <button
                  type="button"
                  onClick={() => { setSelectedCategory(null); setSelectedCostCenter(null) }}
                  className="text-xs text-muted-foreground hover:text-foreground underline"
                >
                  ← Back to all categories
                </button>
              </div>
            </CardHeader>
            <CardContent>
              {error ? (
                <div className="h-[280px] flex items-center justify-center text-sm text-muted-foreground border rounded-md border-dashed">
                  Couldn&apos;t load chart data — see error above.
                </div>
              ) : data ? (
                <ResponsiveContainer width="100%" height={Math.max(280, Math.min(data.chart.length * 38, 480))}>
                  <BarChart data={data.chart} layout="vertical" margin={{ top: 8, right: 80, bottom: 8, left: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                    <YAxis
                      type="category" dataKey="costCenter" width={200}
                      tickFormatter={(v: string) => (v.length > 28 ? `${v.slice(0, 27)}…` : v)}
                      fontSize={11} tick={{ fill: "hsl(var(--muted-foreground))" }}
                    />
                    <XAxis type="number" tickFormatter={formatTick} fontSize={11} />
                    <Tooltip content={<ChartTooltip />} />
                    <Legend verticalAlign="top" height={28} />
                    <Bar dataKey="v1" name={version1} fill={A_COLOR} />
                    <Bar dataKey="v2" name={version2} fill={B_COLOR} />
                  </BarChart>
                </ResponsiveContainer>
              ) : <Skeleton className="h-[280px]" />}
            </CardContent>
          </Card>

          {/* Cost center detail table */}
          <Card>
            <CardHeader><CardTitle>Cost Centers — {selectedCategory}</CardTitle></CardHeader>
            <CardContent>
              {error ? (
                <div className="h-64 flex items-center justify-center text-sm text-muted-foreground border rounded-md border-dashed">
                  Couldn&apos;t load cost center data — see error above.
                </div>
              ) : data ? (
                <div className="max-h-[360px] overflow-y-auto rounded-md border">
                <Table className="[&_td]:py-1.5 [&_td]:text-xs [&_th]:py-2 [&_th]:text-xs">
                  <TableHeader className="sticky top-0 bg-white z-10 shadow-[0_1px_0_0_hsl(var(--border))]">
                    <TableRow>
                      <TableHead className="text-muted-foreground text-xs font-medium w-24">Cost Center #</TableHead>
                      <SortHeader label="Cost Center" sortKey="costCenterName" sort={ccSort} onSort={onSort(setCcSort)} />
                      <SortHeader label="Department" sortKey="department" sort={ccSort} onSort={onSort(setCcSort)} />
                      <SortHeader label="Actuals" sortKey="v1" sort={ccSort} onSort={onSort(setCcSort)} className="text-right" />
                      <SortHeader label="Forecast" sortKey="v2" sort={ccSort} onSort={onSort(setCcSort)} className="text-right" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sortedCc.map((r) => (
                      <TableRow
                        key={`${r.costCenterId}-${r.department}`}
                        className={`cursor-pointer transition-colors ${
                          selectedCostCenter?.id === r.costCenterId
                            ? "bg-blue-50 hover:bg-blue-50"
                            : "hover:bg-slate-50"
                        }`}
                        onClick={() => {
                          if (selectedCostCenter?.id === r.costCenterId) {
                            setSelectedCostCenter(null)
                          } else {
                            setSelectedCostCenter({ id: r.costCenterId, name: r.costCenterName })
                          }
                        }}
                      >
                        <TableCell className="text-xs text-muted-foreground font-mono">{r.costCenterId}</TableCell>
                        <TableCell className="font-medium whitespace-nowrap">
                          <span className="inline-flex items-center gap-1.5">
                            {selectedCostCenter?.id === r.costCenterId && <ChevronRight className="h-3 w-3 text-blue-600" />}
                            {r.costCenterName}
                          </span>
                        </TableCell>
                        <TableCell className="text-muted-foreground whitespace-nowrap">{r.department}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtDollar(r.v1)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtDollar(r.v2)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                </div>
              ) : <Skeleton className="h-64" />}
              {data && !selectedCostCenter && (
                <p className="text-xs text-muted-foreground mt-3">Click a row to view journal entries.</p>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* ── Level 3: Drill into Cost Center (journal entries) ─────────── */}
      {selectedCategory && selectedCostCenter && (
        <div ref={level3Ref}>
          <JournalEntriesTable
            configName={configName}
            category={selectedCategory}
            costCenterId={selectedCostCenter.id}
            costCenterName={selectedCostCenter.name}
            fiscalYear={fiscalYear}
            quarter={quarter}
          />
        </div>
      )}

      {isFetching && <p className="text-xs text-muted-foreground">Refreshing…</p>}
    </main>
  )
}
