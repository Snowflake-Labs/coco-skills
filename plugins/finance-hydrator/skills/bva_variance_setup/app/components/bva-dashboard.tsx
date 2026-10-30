"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { ChartTooltip, formatTick, getYAxisWidth } from "@/components/chart-utils"
import { resolveVersionPeriod, quarterMonthLabel } from "@/lib/bva"
import { fmtK, fmtPct, deltaColor, shortSeries, getJson } from "@/lib/format"
import { Select, MultiSelect, SavedPresetsBar } from "@/components/bva-controls"
import type { BvaConfig, FilterPreset } from "@/lib/config"
import { isConfigured } from "@/lib/config"
import type { BrandColors } from "@/lib/config"
import { SNOWFLAKE_COLORS } from "@/lib/config"
import { downloadExcel, toK } from "@/lib/export"
import { useChatContext, type FilterUpdate } from "@/lib/chat-context"

const A_COLOR = "#29B5E8"  // Snowflake blue — V1 actuals
const B_COLOR = "#0F5FA8"  // Snowflake dark blue — V2 forecast/budget
const ACTUALS = "Actuals"

// Accounting-style number format — parentheses for negatives, $Xk
// Snowflake brand palette used throughout the table and charts
const SF_NAVY     = "#11264F"  // header background
const SF_NAVY_MID = "#1A3A6E"  // sub-header
const SF_BORDER   = "#2B4EA0"  // header divider
const SF_SUBTEXT  = "#B3D4F0"  // sub-header text
function fmtAcct(v: number): string {
  const k = Math.round(Math.abs(v) / 1000).toLocaleString()
  return v < 0 ? `($${k}K)` : `$${k}K`
}
function fmtPctAcct(v: number | null): string {
  if (v == null) return "—"
  const p = Math.abs(v).toFixed(1)
  return v < 0 ? `(${p}%)` : `${p}%`
}

// Inline commentary cell — click to edit, blur to save. Shows a small "AI"
// badge when the current note came from the agent (source "SKILL"), a
// "Manual" badge otherwise so the two are never ambiguous, a "Generate AI
// commentary" trigger that sends a sample chat prompt (the agent already
// has SQL access + the suggest_commentary mechanism — see AGENT_PLAYBOOK.md
// / README.md §11), and a "Restore AI version" link since editing manually
// makes the manual note the new latest but never deletes the prior AI note
// from the append-only table underneath. Restore is confirm-gated since it
// replaces whatever manual note is currently showing.
interface CommentaryEntry { text: string; source: string; updatedBy: string }
function CommentaryCell({
  entry,
  onSave,
  onGenerate,
  onRestore,
}: {
  entry: CommentaryEntry | undefined
  onSave: (t: string) => void
  onGenerate: () => void
  onRestore: () => void
}) {
  const value = entry?.text ?? ""
  const isAi = entry?.source === "SKILL"
  const [editing, setEditing] = useState(false)
  const [draft, setDraft]   = useState(value)
  // Keep the draft textarea in sync if `entry` changes from outside
  // (e.g. another user's edit, or an agent-suggested commentary landing).
  useEffect(() => { setDraft(value) }, [value])
  if (editing) {
    return (
      <textarea
        autoFocus
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onBlur={() => { onSave(draft); setEditing(false) }}
        onKeyDown={e => { if (e.key === "Escape") { setDraft(value); setEditing(false) } }}
        className="w-full text-xs border rounded p-1 min-w-[160px] resize-none bg-white"
        rows={2}
      />
    )
  }
  return (
    <div className="min-w-[140px]">
      <span
        onClick={() => { setDraft(value); setEditing(true) }}
        className="cursor-text text-xs text-muted-foreground hover:text-foreground block"
      >
        {value || <span className="italic opacity-50">Add comment…</span>}
      </span>
      <div className="flex items-center gap-2 mt-0.5">
        {isAi && <span className="text-[10px] uppercase tracking-wide text-blue-600 font-medium">AI</span>}
        {!isAi && value && <span className="text-[10px] uppercase tracking-wide text-muted-foreground font-medium">Manual</span>}
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onGenerate() }}
          className="text-[10px] text-muted-foreground hover:text-foreground underline"
        >
          Generate AI commentary
        </button>
        {!isAi && value && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              if (window.confirm("Replace this manual comment with the earlier AI-generated version? Your manual comment won't be permanently lost (history is preserved) but it will stop being the version shown here.")) {
                onRestore()
              }
            }}
            className="text-[10px] text-muted-foreground hover:text-foreground underline"
          >
            Restore AI version
          </button>
        )}
      </div>
    </div>
  )
}

type ExtraFilter = { column: string; label: string; values: string[] }
// Shape of the /api/filters response: all the selectable filter values plus
// server-computed metadata (which optional dimensions exist, whether the
// pre-built dashboard dataset is ready) used to decide which filter
// controls to render at all.
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
  matTableReady?: boolean
  // Ad-hoc filters from enabled derived_column custom logic rules (see
  // app/api/filters/route.ts's loadExtraFilters) — empty unless the customer
  // added custom logic in Settings Step 7.
  extraFilters?: ExtraFilter[]
  capabilities?: {
    hasOrganization: boolean
    hasMonthlyGrain: boolean
    hasMultipleVersions: boolean
    hasCategories: boolean
    hasCostCenterNames: boolean
    hasMultipleYears: boolean
    hasExpenseBreakdown: boolean
  }
}
// A single expense-category row of the mirror table: current-period V1/V2
// values, the delta between them, and up to a few trailing prior periods
// for trend context. subRows holds category breakout rows (see
// BreakoutGroup in lib/config.ts) rendered indented beneath this one.
type Row = {
  category: string
  trailing: Record<string, number>
  currentV1: number
  currentV2: number
  deltaDollar: number
  deltaPct: number | null
  subRows?: Row[]
}
// A user-defined summary/total row (see SummaryRowDef in lib/config.ts) —
// same numeric shape as Row but with a display name instead of a category.
type SummaryRowResult = Omit<Row, "category" | "subRows"> & { name: string; style?: "bold" | "subtotal" }
// Full /api/bva response for the current filter selection: the resolved
// version/period labels, one Row per expense category, the grand total,
// any custom summary rows, and the chart series.
type Bva = {
  version1: string
  version2: string
  organization: string
  fiscalYear: string
  quarter: string
  isActualsV1: boolean
  currentLabel: string
  trailingQuarters: { key: string; label: string }[]
  categories: Row[]
  total: Omit<Row, "category">
  summaryRows?: SummaryRowResult[]
  chart: { period: string; v1: number; v2: number }[]
}

// $'000 formatting, accounting parentheses for negatives.

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

type ProfileEntry = { name: string; updatedAt: string; sourceType: string | null; actualsTable: string | null }

/**
 * The main "BvA Working" dashboard: a chart + mirror table comparing two
 * planning "versions" (Version 1 is usually Actuals, Version 2 a
 * budget/forecast) across fiscal periods, broken out by expense category
 * with drill-in sub-rows and per-cell commentary.
 *
 * This component is one half of the page<->agent sync described in
 * lib/chat-context.tsx: it calls setPageContext() (see the effect below)
 * with `mode: "analysis"`, its current filter selections, and a set of
 * on* callbacks. The embedded chat agent uses that snapshot to answer
 * questions about what's currently on screen, and can drive the dashboard
 * back by emitting fenced blocks (```filter, ```save_preset,
 * ```switch_config, ```suggest_overrides, ```suggest_commentary) that
 * chat-context.tsx parses and routes to the matching callback here —
 * that's how "change the filter to Q2" or "add commentary for Payroll"
 * typed in chat actually updates this page's own React state.
 */
export function BvaDashboard() {
  const { data: presetsData, refetch: refetchPresets } = useQuery({
    queryKey: ["presets"],
    queryFn:  () => getJson<{ presets: FilterPreset[] }>("/api/setup/presets"),
    staleTime: 5 * 60 * 1000,
  })
  const presets = presetsData?.presets ?? []

  const { data: profilesData } = useQuery({
    queryKey: ["config-profiles"],
    queryFn:  () => getJson<{ configs: ProfileEntry[] }>("/api/setup/config?list=true"),
    staleTime: 60_000,
  })
  const configProfiles = (profilesData?.configs ?? []).filter((p) => p.name !== "default")

  const { data: filters } = useQuery({
    queryKey: ["filters"],
    queryFn: () => getJson<Filters>("/api/filters"),
    staleTime: 60_000, // 1 min — don't refetch on every tab focus
  })
  const { data: configData } = useQuery({
    queryKey: ["setup-config"],
    queryFn: () => getJson<{ config: BvaConfig | null }>("/api/setup/config"),
    staleTime: 30_000,
  })

  // Gate all data on config presence: no valid config → empty state, no data
  // loaded. Once setup completes (config saved) the dashboard populates.
  // configData === undefined while the config query is in flight.
  const configLoading = configData === undefined
  const configured = isConfigured(configData?.config)

  const { setPageContext, dashboardResetSignal, sendMessage, setIsOpen: setChatOpen } = useChatContext()
  const queryClient = useQueryClient()
  // Disables the "Refresh data" button and swaps its label while a
  // materialize job (rebuild of the underlying dashboard dataset) is running.
  const [refreshing, setRefreshing] = useState(false)
  // Set when the chat agent proposes switching to a different saved config
  // (```switch_config``` block) — non-null shows the confirmation banner;
  // switching configs is destructive enough (reloads all dashboard data)
  // that it always requires this explicit human confirmation step.
  const [pendingConfigSwitch, setPendingConfigSwitch] = useState<string | null>(null)
  const [activating, setActivating] = useState(false)
  // Set when the chat agent proposes account-category override edits
  // (```suggest_overrides``` block, only possible when
  // configData.config.agentCanEditConfig is true) — non-null shows the
  // confirmation banner before the patch is actually written to config.
  const [pendingOverridePatch, setPendingOverridePatch] = useState<{ add?: Record<string,string>; remove?: string[] } | null>(null)
  const [applyingOverrides, setApplyingOverrides] = useState(false)

  // Agent-suggested commentary (```suggest_commentary```) pends a human
  // confirmation banner rather than auto-writing — a prompt-injected or
  // hallucinated agent turn must not persist commentary without review.
  const [pendingCommentary, setPendingCommentary] = useState<Array<{ category: string; text: string }> | null>(null)
  const [applyingCommentary, setApplyingCommentary] = useState(false)
  // Set when a save-preset request (from the UI or the chat agent's
  // ```save_preset``` block) collides with an existing preset name — shows a
  // confirmation banner instead of silently overwriting the existing preset.
  const [pendingPresetSave, setPendingPresetSave] = useState<{ name: string; filters: FilterPreset["filters"] } | null>(null)
  const [savingPreset, setSavingPreset] = useState(false)

  // Commentary map state — populated after fiscalYear state is declared below
  const [commentaryMap, setCommentaryMap] = useState<Record<string, CommentaryEntry>>({})

  const handleConfirmSwitch = useCallback(async () => {
    if (!pendingConfigSwitch) return
    setActivating(true)
    try {
      // Retry once on a genuine network failure ("Failed to fetch" — the request
      // never reached the server at all, e.g. a transient SPCS ingress hiccup or
      // the backend's OAuth token rotating mid-request). A real HTTP error
      // response (4xx/5xx) is NOT retried — that's a real failure, not a blip.
      let res: Response
      try {
        res = await fetch("/api/setup/config/activate", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name: pendingConfigSwitch }),
        })
      } catch {
        res = await fetch("/api/setup/config/activate", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name: pendingConfigSwitch }),
        })
      }
      if (!res.ok) throw new Error(await res.text())
      await Promise.all(
        ["setup-config", "filters", "bva", "breakdown", "time", "presets", "config-profiles"]
          .map((k) => queryClient.invalidateQueries({ queryKey: [k] }))
      )
    } catch (e) {
      alert(`Failed to switch config: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setActivating(false)
      setPendingConfigSwitch(null)
    }
  }, [pendingConfigSwitch, queryClient])

  const handleConfirmOverrides = useCallback(async () => {
    if (!pendingOverridePatch) return
    setApplyingOverrides(true)
    try {
      const configRes = await fetch("/api/setup/config")
      const { config: current } = await configRes.json() as { config: BvaConfig | null }
      if (!current) throw new Error("Config not found")
      const existing = current.accountCategoryOverrides ?? {}
      const next = { ...existing, ...(pendingOverridePatch.add ?? {}) }
      for (const code of pendingOverridePatch.remove ?? []) delete next[code]
      const patched: BvaConfig = { ...current, accountCategoryOverrides: Object.keys(next).length > 0 ? next : undefined }
      const res = await fetch("/api/setup/config", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(patched),
      })
      if (!res.ok) throw new Error(await res.text())
      await Promise.all(
        ["setup-config", "bva", "breakdown", "filters"]
          .map((k) => queryClient.invalidateQueries({ queryKey: [k] }))
      )
    } catch (e) {
      alert(`Failed to apply overrides: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setApplyingOverrides(false)
      setPendingOverridePatch(null)
    }
  }, [pendingOverridePatch, queryClient])
  const handleRefreshData = async () => {
    setRefreshing(true)
    try {
      const res = await fetch("/api/setup/materialize", { method: "POST" })
      if (!res.ok) throw new Error(await res.text())
      await Promise.all(
        ["bva", "breakdown", "filters"].map((k) => queryClient.invalidateQueries({ queryKey: [k] })),
      )
    } catch (err) {
      alert(`Refresh failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setRefreshing(false)
    }
  }
  // Core filter-bar state — what's being compared (version1 = Version 1
  // "rows" series, usually Actuals; version2 = the comparison/budget
  // series) and over what period/scope (fiscalYear + quarter, optionally
  // narrowed further by month when the source data has monthly grain, plus
  // organization/cost-center/category multi-selects). These are also what
  // gets published to the chat agent below (see the setPageContext effect).
  const [version1, setVersion1] = useState("")
  const [version2, setVersion2] = useState("")
  const [fiscalYear, setFiscalYear] = useState("")
  const [quarter, setQuarter] = useState("")
  const [month, setMonth] = useState("")
  const [organizations, setOrganizations] = useState<string[]>([])
  const [costCenters, setCostCenters] = useState<string[]>([])
  const [categories, setCategories] = useState<string[]>([])
  // Ad-hoc filters from custom logic derived columns — column alias -> selected values.
  const [extraFilterState, setExtraFilterState] = useState<Record<string, string[]>>({})

  // Commentary query — depends on fiscalYear state (declared above). Loads
  // the free-text notes shown in the table's rightmost column, keyed by
  // (category, fiscal year, quarter) — see handleCommentarySave below for
  // how edits are written back.
  const configName = configData?.config?.configName ?? "default"
  const { data: commentaryData } = useQuery({
    queryKey: ["commentary", configName, fiscalYear],
    queryFn: () => getJson<{ commentary: Record<string, CommentaryEntry> }>(`/api/commentary?configName=${encodeURIComponent(configName)}&fiscalYear=${encodeURIComponent(fiscalYear)}`),
    staleTime: 5 * 60_000,   // 5 min — don't refetch on every nav
    enabled: configured && !!fiscalYear && !!configData?.config, // only when real config (not demo mode)
  })
  useEffect(() => {
    if (commentaryData?.commentary) setCommentaryMap(commentaryData.commentary)
  }, [commentaryData])

  // Reset all filters to blank when the user triggers a config/dashboard reset.
  // Clearing to empty lets the init effect below repopulate sensible defaults and
  // the chart re-queries. Skip the initial mount (signal starts at 0).
  const didMountReset = useRef(false)
  useEffect(() => {
    if (!didMountReset.current) { didMountReset.current = true; return }
    setVersion1("")
    setVersion2("")
    setFiscalYear("")
    setQuarter("")
    setMonth("")
    setOrganizations([])
    setCostCenters([])
    setCategories([])
    setExtraFilterState({})
  }, [dashboardResetSignal])

  // Prune any extraFilterState selection whose column is no longer one of
  // the currently-enabled custom logic rules' extraFilters. Without this, a
  // rule disabled/deleted in Settings leaves its previously-selected value
  // sitting in state; the /api/bva query below still serializes it into
  // extraFilters, and since parseExtraFilters() only checks identifier
  // SHAPE (not existence), a WHERE clause referencing a column that no
  // longer exists in bvaFrom reaches Snowflake and 500s the whole
  // dashboard query — with the control that caused it already gone from
  // view, since it's no longer in filters.extraFilters either.
  useEffect(() => {
    if (!filters) return
    const validCols = new Set((filters.extraFilters ?? []).map((f) => f.column))
    setExtraFilterState((prev) => {
      const next = Object.fromEntries(Object.entries(prev).filter(([k]) => validCols.has(k)))
      return Object.keys(next).length === Object.keys(prev).length ? prev : next
    })
  }, [filters])

  useEffect(() => {
    if (!filters) return
    const def = filters.seriesOptions.includes(filters.defaultVersion) ? filters.defaultVersion : filters.seriesOptions[1]
    if (!version1) setVersion1(ACTUALS)
    if (!version2) {
      setVersion2(def)
      // Initialize fiscal year + quarter from the default version so the initial
      // view reflects that version's period rather than a hardcoded Q2 default.
      const p = resolveVersionPeriod(def, configData?.config?.versionPeriods)
      if (!fiscalYear) {
        setFiscalYear(p?.year ?? (filters.years.includes("FY2027") ? "FY2027" : filters.years[filters.years.length - 1]))
      }
      if (!quarter) setQuarter(p?.quarter ?? "Full Year")
    } else {
      if (!fiscalYear) setFiscalYear(filters.years.includes("FY2027") ? "FY2027" : filters.years[filters.years.length - 1])
      if (!quarter) setQuarter("Full Year")
    }
  }, [filters, version1, version2, fiscalYear, quarter])

  // Applies a filter change requested by the chat agent (parsed from its
  // ```filter``` block in lib/chat-context.tsx) — this is registered below
  // as the page's onFilterUpdate callback, so "the agent changed the
  // filters" ultimately calls straight into this function. Only touches
  // the fields actually present in `update`; changing a version also
  // re-narrows fiscal year/quarter to that version's known period, same as
  // the manual Select onChange handlers (onVersion1/onVersion2) further down.
  const applyFilterUpdate = useCallback((update: FilterUpdate) => {
    const versionPeriods = configData?.config?.versionPeriods
    if (update.version1) {
      setVersion1(update.version1)
      const p = resolveVersionPeriod(update.version1, versionPeriods)
      if (p) { if (p.year) setFiscalYear(p.year); setQuarter(p.quarter ?? "Full Year") }
    }
    if (update.version2) {
      setVersion2(update.version2)
      const p = resolveVersionPeriod(update.version2, versionPeriods)
      if (p) { if (p.year) setFiscalYear(p.year); setQuarter(p.quarter ?? "Full Year") }
    }
    if (update.fiscalYear) setFiscalYear(update.fiscalYear)
    if (update.quarter) setQuarter(update.quarter)
    if (update.organizations !== undefined) setOrganizations(update.organizations)
    if (update.costCenters !== undefined) setCostCenters(update.costCenters)
    if (update.categories !== undefined) setCategories(update.categories)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configData?.config?.versionPeriods])

  const handleSavePreset = useCallback(async (name: string, filterState: FilterPreset["filters"]) => {
    // Guard against a malformed agent-emitted save_preset block (e.g. missing
    // one of the filter arrays) persisting a preset that crashes the dashboard
    // later when someone loads it — see lib/chat-context.tsx's save_preset parsing.
    const safeFilters: FilterPreset["filters"] = {
      ...filterState,
      organizations: Array.isArray(filterState?.organizations) ? filterState.organizations : [],
      categories:    Array.isArray(filterState?.categories)    ? filterState.categories    : [],
      costCenters:   Array.isArray(filterState?.costCenters)   ? filterState.costCenters   : [],
    }
    try {
      // Never silently overwrite an existing preset with the same name — this
      // action fires from CoCo's chat with no other confirmation step. See
      // README.md §11.
      if (presets.some((p) => p.name === name)) {
        setPendingPresetSave({ name, filters: safeFilters })
        return
      }
      const preset: FilterPreset = { id: Date.now().toString(), name, createdAt: new Date().toISOString(), filters: safeFilters }
      const res = await fetch("/api/setup/presets", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(preset) })
      if (!res.ok) {
        // Race: something else created a same-named preset between the check above and this call.
        setPendingPresetSave({ name, filters: safeFilters })
        return
      }
      refetchPresets()
    } catch (e) {
      alert(`Failed to save preset from chat: ${e instanceof Error ? e.message : String(e)}`)
    }
  }, [presets, refetchPresets])

  const handleConfirmPresetSave = useCallback(async () => {
    if (!pendingPresetSave) return
    setSavingPreset(true)
    try {
      // Auto-suffix rather than overwrite — "(2)", "(3)", ... until the name is free.
      let renamed = pendingPresetSave.name
      let n = 2
      while (presets.some((p) => p.name === renamed)) {
        renamed = `${pendingPresetSave.name} (${n})`
        n += 1
      }
      const preset: FilterPreset = { id: Date.now().toString(), name: renamed, createdAt: new Date().toISOString(), filters: pendingPresetSave.filters }
      const res = await fetch("/api/setup/presets", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(preset) })
      if (!res.ok) throw new Error(await res.text())
      refetchPresets()
    } catch (e) {
      alert(`Failed to save preset: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSavingPreset(false)
      setPendingPresetSave(null)
    }
  }, [pendingPresetSave, presets, refetchPresets])

  // Manual commentary edit from the table's inline textarea. Optimistically
  // updates commentaryMap immediately (so the UI feels instant), then
  // persists to the backend; on failure, rolls the map back to whatever it
  // held before this edit rather than leaving a note the server never saved.
  const handleCommentarySave = useCallback(async (category: string, text: string) => {
    const key = `${category}|${fiscalYear}|${quarter}`
    const prevEntry = commentaryMap[key]
    setCommentaryMap(prev => ({ ...prev, [key]: { text, source: "MANUAL", updatedBy: "user" } }))
    try {
      const res = await fetch("/api/commentary", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ configName, category, fiscalYear, period: quarter, text, updatedBy: "user", source: "MANUAL" }),
      })
      if (!res.ok) throw new Error(await res.text())
    } catch (e) {
      setCommentaryMap(prev => ({ ...prev, [key]: prevEntry ?? { text: "", source: "MANUAL", updatedBy: "" } }))
      alert(`Failed to save commentary: ${e instanceof Error ? e.message : String(e)}`)
    }
  }, [configName, fiscalYear, quarter, commentaryMap])

  // Applies the chat agent's ```suggest_commentary``` block AFTER explicit human
  // confirmation (see the banner below). The agent callback only stages rows
  // into pendingCommentary; nothing is written until the user clicks Apply — a
  // prompt-injected or hallucinated turn must not persist commentary on its own.
  // Writes each (category, text) tagged source "SKILL" (advisory; the writer is
  // resolved server-side from CURRENT_USER()), then merges into commentaryMap.
  const handleConfirmCommentary = useCallback(async () => {
    if (!pendingCommentary) return
    const valid = pendingCommentary.filter((r) => r && typeof r.category === "string" && typeof r.text === "string")
    if (valid.length === 0) { setPendingCommentary(null); return }
    setApplyingCommentary(true)
    try {
      await Promise.all(valid.map(r =>
        fetch("/api/commentary", {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ configName, category: r.category, fiscalYear, period: quarter, text: r.text, source: "SKILL" }),
        }).then((res) => {
          if (!res.ok) throw new Error(`Failed to save commentary for "${r.category}"`)
        })
      ))
      setCommentaryMap(prev => {
        const next = { ...prev }
        for (const r of valid) next[`${r.category}|${fiscalYear}|${quarter}`] = { text: r.text, source: "SKILL", updatedBy: "" }
        return next
      })
      setPendingCommentary(null)
    } catch (e) {
      console.error("[handleConfirmCommentary]", e)
      alert("Failed to save the suggested commentary. Please try again.")
    } finally {
      setApplyingCommentary(false)
    }
  }, [pendingCommentary, configName, fiscalYear, quarter])

  // "Generate AI commentary" is a sample-prompt trigger, not a bespoke
  // generation pipeline — it just sends the same request a person could type
  // themselves, opens the chat so they can see it happen, and lets the
  // agent's existing SQL access + suggest_commentary mechanism do the rest
  // (see AGENT_PLAYBOOK.md / the COMMENTARY section of the system prompt).
  const handleGenerateCommentary = useCallback((category: string) => {
    setChatOpen(true)
    sendMessage(`Generate AI commentary for ${category}, ${fiscalYear} ${quarter}.`)
  }, [fiscalYear, quarter, sendMessage, setChatOpen])

  const handleCommentarySaveWithSource = useCallback(async (category: string, text: string, source: string, updatedBy: string) => {
    const key = `${category}|${fiscalYear}|${quarter}`
    setCommentaryMap(prev => ({ ...prev, [key]: { text, source, updatedBy } }))
    const res = await fetch("/api/commentary", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ configName, category, fiscalYear, period: quarter, text, updatedBy, source }),
    })
    if (!res.ok) throw new Error(await res.text())
  }, [configName, fiscalYear, quarter])

  // Commentary is append-only — a manual edit becomes the new latest, but
  // the prior AI-generated note (if any) is still in the table. This looks
  // it up and re-saves it as the new latest, rather than losing it forever
  // behind a manual edit.
  const handleRestoreAiCommentary = useCallback(async (category: string) => {
    try {
      const res = await fetch(`/api/commentary/history?configName=${encodeURIComponent(configName)}&category=${encodeURIComponent(category)}&fiscalYear=${encodeURIComponent(fiscalYear)}&period=${encodeURIComponent(quarter)}&source=SKILL`)
      const { entry } = await res.json() as { entry: { text: string; source: string; updatedBy: string } | null }
      if (!entry) { alert("No earlier AI-generated commentary found for this cell."); return }
      await handleCommentarySaveWithSource(category, entry.text, "SKILL", entry.updatedBy || "agent")
    } catch (e) {
      alert(`Failed to restore AI commentary: ${e instanceof Error ? e.message : String(e)}`)
    }
  }, [configName, fiscalYear, quarter, handleCommentarySaveWithSource])

  // The "page -> agent" half of the sync described at the top of this file
  // (see lib/chat-context.tsx for the full contract). Re-runs whenever any
  // filter/config/preset/profile value changes, publishing a fresh
  // PageContext snapshot — current filters, what values are selectable
  // (`available`), and the on* callbacks the agent can invoke — so the chat
  // panel is never working off stale state. onSuggestOverrides is only
  // wired up when the config explicitly opts in (agentCanEditConfig), since
  // letting the agent edit account category mappings is an elevated action.
  useEffect(() => {
    setPageContext({
      mode: "analysis",
      config: configData?.config ?? null,
      view: {
        version1, version2, fiscalYear, quarter, organizations, costCenters, categories,
        fyStartMonth: filters?.fyStartMonth,
        presets: presets.map((p) => ({ id: p.id, name: p.name })),
        configs: configProfiles.map((p) => ({ name: p.name, actualsTable: p.actualsTable })),
        available: filters ? {
          versions: filters.seriesOptions,
          years: filters.years,
          organizations: filters.organizations,
          costCenters: filters.costCenters,
          categories: filters.categories,
          quarters: ["Full Year", ...(filters.quarters ?? [])],
        } : undefined,
      },
      onFilterUpdate: applyFilterUpdate,
      onSavePreset: handleSavePreset,
      onSwitchConfig: (name) => setPendingConfigSwitch(name),
      onSuggestOverrides: configData?.config?.agentCanEditConfig
        ? (patch) => setPendingOverridePatch(patch)
        : undefined,
      onSuggestCommentary: (rows) => {
        const valid = Array.isArray(rows) ? rows.filter((r) => r && typeof r.category === "string" && typeof r.text === "string") : []
        setPendingCommentary(valid.length ? valid : null)
      },
    })
  }, [configData?.config, version1, version2, fiscalYear, quarter, organizations, costCenters, categories, filters, presets, configProfiles, setPageContext, applyFilterUpdate, handleSavePreset])

  // Selecting a version updates the fiscal period to match.
  // Year-only versions ("FY25 Plan", "FY24 Revised Plan") set year + "Full Year".
  // Versions with no FY prefix ("Actuals", "Annual Plan") leave both unchanged.
  // Prefers cached versionPeriods (real data coverage) over the version NAME —
  // see resolveVersionPeriod in lib/bva.ts for why (vintage labels like "Q1
  // Forecast" can span many quarters).
  const applyVersionPeriod = (v: string) => {
    const p = resolveVersionPeriod(v, configData?.config?.versionPeriods)
    if (p) {
      if (p.year) setFiscalYear(p.year)
      setQuarter(p.quarter ?? "Full Year")
    }
  }
  const onVersion1 = (v: string) => { setVersion1(v); applyVersionPeriod(v) }
  const onVersion2 = (v: string) => { setVersion2(v); applyVersionPeriod(v) }

  const params = useMemo(
    () =>
      new URLSearchParams({
        version1, version2, fiscalYear, quarter,
        ...(month ? { month } : {}),
        organization: organizations.join(","),
        costCenter:   costCenters.join(","),
        category:     categories.join(","),
        ...(Object.keys(extraFilterState).length ? { extraFilters: JSON.stringify(extraFilterState) } : {}),
      }).toString(),
    [version1, version2, fiscalYear, quarter, month, organizations, costCenters, categories, extraFilterState],
  )

  const { data, error, isFetching } = useQuery({
    queryKey: ["bva", params],
    queryFn: () => getJson<Bva>(`/api/bva?${params}`),
    enabled: configured && !!version1 && !!version2 && !!fiscalYear,
  })

  const handleExport = () => {
    if (!data) return
    const rows = data.categories.map((c) => {
      const row: Record<string, unknown> = { "Expense Category": c.category }
      for (const t of data.trailingQuarters) row[t.label] = toK(c.trailing[t.key] ?? 0)
      row[`${data.currentLabel} ${shortSeries(data.version1)} (V1)`] = toK(c.currentV1)
      row[`${data.currentLabel} ${shortSeries(data.version2)} (V2)`] = toK(c.currentV2)
      row["\u0394 $ ('000)"] = toK(c.deltaDollar)
      row["\u0394 %"] = c.deltaPct
      return row
    })
    const total = data.total
    const totalRow: Record<string, unknown> = { "Expense Category": "Total Expenses" }
    for (const t of data.trailingQuarters) totalRow[t.label] = toK(total.trailing[t.key] ?? 0)
    totalRow[`${data.currentLabel} ${shortSeries(data.version1)} (V1)`] = toK(total.currentV1)
    totalRow[`${data.currentLabel} ${shortSeries(data.version2)} (V2)`] = toK(total.currentV2)
    totalRow["\u0394 $ ('000)"] = toK(total.deltaDollar)
    totalRow["\u0394 %"] = total.deltaPct
    rows.push(totalRow)
    downloadExcel(`BvA_Working_${data.fiscalYear}_${data.quarter}`, [{ name: "BvA Working", rows }])
  }

  // Adaptive visibility flags derived from live filter data. showCategoryFilter's
  // >= 3 threshold mirrors BvaCapabilities.hasCategories in lib/config.ts — fewer
  // than 3 categories isn't worth a dedicated multi-select filter.
  const showOrgFilter     = (filters?.organizations?.length ?? 0) > 0
  const showMonthFilter   = filters?.grain === "month"
  const showCategoryFilter = (filters?.categories?.length ?? 0) >= 3
  const showYearFilter    = (filters?.years?.length ?? 0) > 1
  const showCCFilter      = (filters?.costCenters?.length ?? 0) > 0

  const monthDisabled = !showMonthFilter
  const orgLabel =
    organizations.length === 0 ? "Total Cost"
    : organizations.length === 1 ? organizations[0]
    : `${organizations.length} Departments`
  const title = `${orgLabel} — ${quarter} ${fiscalYear.replace("FY20", "FY")}`

  // configData loaded but no config saved → demo mode (server uses fallback table).
  // Show a soft banner instead of blocking the entire dashboard.

  return (
    <div className="max-w-[1400px] mx-auto py-8 px-4 space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">BvA Working</h1>
          <p className="text-muted-foreground text-sm mt-1">Compare actuals against a planning version.</p>
        </div>
        <button
          onClick={handleRefreshData}
          disabled={refreshing}
          title="Rebuild the dashboard dataset from the latest source data. After saving a new configuration, click here once to load the freshest numbers."
          className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md border border-border hover:bg-muted transition-colors shrink-0 disabled:opacity-50"
        >
          {refreshing ? "Rebuilding data…" : "↻ Refresh data"}
        </button>
      </div>

      {/* Demo mode banner — shown when no config is saved yet */}
      {!configData?.config && !configLoading && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-800">
          Viewing demo data. Go to <strong>Settings</strong> to connect your own budget and actuals tables.
        </div>
      )}

      {/* Data rebuild notice — shown while background materialization is running */}
      {filters && filters.matTableReady === false && configured && (
        <div className="rounded-md border border-blue-200 bg-blue-50 px-4 py-3 flex items-center justify-between gap-4 text-sm text-blue-900">
          <div>
            <span className="font-medium">Dashboard data is being rebuilt.</span>
            <span className="ml-1.5 text-blue-700 text-xs">
              Initial queries may be slow while the dataset compiles. Click &#8203;<strong>Refresh data</strong> once ready to load the fast pre-built version.
            </span>
          </div>
          <button
            onClick={handleRefreshData}
            disabled={refreshing}
            className="text-xs font-medium px-3 py-1.5 rounded-md border border-blue-300 bg-white hover:bg-blue-50 disabled:opacity-50 shrink-0"
          >
            {refreshing ? "Rebuilding…" : "↻ Refresh data"}
          </button>
        </div>
      )}

      {/* Config switch confirmation banner */}
      {pendingConfigSwitch && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 flex items-start justify-between gap-4 text-sm">
          <div>
            <p className="font-medium">Switch to configuration &quot;{pendingConfigSwitch}&quot;?</p>
            <p className="text-muted-foreground text-xs mt-0.5">
              This will reload all dashboard data from the new source configuration. Rematerialization may take a minute.
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={() => setPendingConfigSwitch(null)}
              disabled={activating}
              className="text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmSwitch}
              disabled={activating}
              className="text-xs px-2.5 py-1.5 rounded bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50 transition-colors"
            >
              {activating ? "Switching…" : "Confirm switch"}
            </button>
          </div>
        </div>
      )}

      {/* Override suggestion banner — shown when agent suggests account category overrides */}
      {pendingOverridePatch && (() => {
        const addCount    = Object.keys(pendingOverridePatch.add    ?? {}).length
        const removeCount = (pendingOverridePatch.remove ?? []).length
        const total       = addCount + removeCount
        return (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 flex items-start justify-between gap-4 text-sm">
            <div>
              <p className="font-medium">Apply account category override{total !== 1 ? "s" : ""}?</p>
              <p className="text-muted-foreground text-xs mt-0.5">
                {addCount > 0 && `${addCount} code${addCount !== 1 ? "s" : ""} will be reclassified. `}
                {removeCount > 0 && `${removeCount} existing override${removeCount !== 1 ? "s" : ""} will be removed. `}
                This triggers rematerialization and may take a minute.
              </p>
              {pendingOverridePatch.add && Object.entries(pendingOverridePatch.add).slice(0, 3).map(([code, cat]) => (
                <p key={code} className="text-xs font-mono mt-1 text-muted-foreground">{code} → {cat}</p>
              ))}
              {addCount > 3 && <p className="text-xs text-muted-foreground">+ {addCount - 3} more</p>}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                type="button"
                onClick={() => setPendingOverridePatch(null)}
                disabled={applyingOverrides}
                className="text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmOverrides}
                disabled={applyingOverrides}
                className="text-xs px-2.5 py-1.5 rounded bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50 transition-colors"
              >
                {applyingOverrides ? "Applying…" : "Apply overrides"}
              </button>
            </div>
          </div>
        )
      })()}

      {/* Agent commentary suggestion banner — never auto-persists AI commentary */}
      {pendingCommentary && pendingCommentary.length > 0 && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 flex items-start justify-between gap-4 text-sm">
          <div>
            <p className="font-medium">Apply AI-suggested commentary?</p>
            <p className="text-muted-foreground text-xs mt-0.5">
              {pendingCommentary.length} suggested note{pendingCommentary.length !== 1 ? "s" : ""} for {fiscalYear} {quarter}. Review before applying — this writes to the shared commentary table.
            </p>
            {pendingCommentary.map((r) => (
              <div key={r.category} className="text-xs mt-1.5 text-muted-foreground">
                <span className="font-mono font-medium">{r.category}</span>
                <p className="whitespace-pre-wrap break-words mt-0.5">{r.text}</p>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={() => setPendingCommentary(null)}
              disabled={applyingCommentary}
              className="text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
            >
              Dismiss
            </button>
            <button
              type="button"
              onClick={handleConfirmCommentary}
              disabled={applyingCommentary}
              className="text-xs px-2.5 py-1.5 rounded bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50 transition-colors"
            >
              {applyingCommentary ? "Applying…" : "Apply commentary"}
            </button>
          </div>
        </div>
      )}

      {/* Preset name-collision banner — never silently overwrites an existing preset */}
      {pendingPresetSave && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 flex items-start justify-between gap-4 text-sm">
          <div>
            <p className="font-medium">A preset named &quot;{pendingPresetSave.name}&quot; already exists.</p>
            <p className="text-muted-foreground text-xs mt-0.5">
              It won&apos;t be overwritten. Save this as a new preset with a different name instead?
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={() => setPendingPresetSave(null)}
              disabled={savingPreset}
              className="text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmPresetSave}
              disabled={savingPreset}
              className="text-xs px-2.5 py-1.5 rounded bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50 transition-colors"
            >
              {savingPreset ? "Saving…" : "Save as new preset"}
            </button>
          </div>
        </div>
      )}

      {/* Filters — only render controls that have real data behind them */}
      <div className="flex flex-wrap gap-3 items-start">
        <Select label="Version 1 (rows)" value={version1} options={filters?.seriesOptions ?? []} onChange={onVersion1} />
        <Select label="Version 2 (comparison)" value={version2} options={filters?.seriesOptions ?? []} onChange={onVersion2} />
        {showYearFilter && <Select label="Fiscal year" value={fiscalYear} options={filters?.years ?? []} onChange={setFiscalYear} />}
        <Select label="Quarter" value={quarter} options={["Full Year", ...(filters?.quarters ?? [])]} onChange={setQuarter} />
        {showMonthFilter && (
          <Select
            label="Month"
            value={month || "All"}
            options={["All", ...(filters?.months ?? [])]}
            onChange={(v) => setMonth(v === "All" ? "" : v)}
          />
        )}
        {!showMonthFilter && (() => {
          const label = quarterMonthLabel(quarter, filters?.fyStartMonth ?? 1)
          return (
            <Select
              label="Month(s)"
              value={label || "N/A"}
              options={[label || "N/A"]}
              disabled
              hint={label ? `${label} (quarterly grain)` : "Quarterly data — monthly grain not available"}
            />
          )
        })()}
        {showOrgFilter && (
          <MultiSelect label="Organization" selected={organizations}
            options={filters?.organizations ?? []} onChange={setOrganizations} />
        )}
        {showCategoryFilter && (
          <MultiSelect label="Category" selected={categories}
            options={filters?.categories ?? []} onChange={setCategories} />
        )}
        {showCCFilter && (
          <MultiSelect label="Cost center" selected={costCenters}
            options={filters?.costCenters ?? []} onChange={setCostCenters} minWidth={200} />
        )}
        {/* Ad-hoc filters from custom logic derived columns — same generic MultiSelect, no per-column code */}
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
        {/* Clear filters — shown when any multi-select or month has values */}
        {(organizations.length > 0 || categories.length > 0 || costCenters.length > 0 || !!month || Object.keys(extraFilterState).length > 0) && (
          <button
            type="button"
            onClick={() => { setOrganizations([]); setCategories([]); setCostCenters([]); setMonth(""); setExtraFilterState({}) }}
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
          setOrganizations(p.filters.organizations)
          setCategories(p.filters.categories)
          setCostCenters(p.filters.costCenters)
          if (p.filters.version1) setVersion1(p.filters.version1)
          if (p.filters.version2) setVersion2(p.filters.version2)
          if (p.filters.fiscalYear) setFiscalYear(p.filters.fiscalYear)
          if (p.filters.quarter) setQuarter(p.filters.quarter)
        }}
        onSave={async (name, filters) => {
          const preset: FilterPreset = { id: Date.now().toString(), name, createdAt: new Date().toISOString(), filters }
          const res = await fetch("/api/setup/presets", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(preset) })
          if (!res.ok) {
            const body = await res.json().catch(() => ({}))
            alert(body.error ?? "Failed to save preset — try a different name.")
            return
          }
          refetchPresets()
        }}
        onDelete={async (id) => {
          await fetch(`/api/setup/presets?id=${id}`, { method: "DELETE" })
          refetchPresets()
        }}
      />

      <div className="border-t pt-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">{title}</h2>
          <p className="text-xs text-muted-foreground mt-1">All figures in $&apos;000. Δ = Version 1 − Version 2 (green = favorable / under budget).</p>
        </div>
        {data && <ExportButton onClick={handleExport} />}
      </div>

      {error && <p className="text-sm text-destructive">Error: {(error as Error).message}</p>}

      {/* Chart */}
      <Card>
        <CardHeader><CardTitle>{version1} vs. {version2} by Quarter</CardTitle></CardHeader>
        <CardContent>
          {error ? (
            <div className="h-[300px] flex items-center justify-center text-sm text-muted-foreground border rounded-md border-dashed">
              Couldn&apos;t load chart data — see error above.
            </div>
          ) : data ? (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={data.chart} margin={{ top: 8, right: 8, bottom: 4, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="period" fontSize={12} />
                <YAxis width={getYAxisWidth(data.chart, "v1")} tickFormatter={formatTick} fontSize={12} />
                <Tooltip content={<ChartTooltip />} />
                <Legend />
                <Bar dataKey="v1" name={version1} fill={A_COLOR} />
                <Bar dataKey="v2" name={version2} fill={B_COLOR} />
              </BarChart>
            </ResponsiveContainer>
          ) : <Skeleton className="h-[300px]" />}
        </CardContent>
      </Card>

      {/* Mirror table */}
      <Card>
        <CardContent className="pt-6">
          {error ? (
            <div className="h-96 flex items-center justify-center text-sm text-muted-foreground border rounded-md border-dashed">
              Couldn&apos;t load table data — see error above.
            </div>
          ) : data ? (
            <div className="max-h-[520px] overflow-y-auto overflow-x-auto rounded-md border">
            <MatrixTable
              data={data}
              commentaryMap={commentaryMap}
              onCommentarySave={handleCommentarySave}
              onGenerateCommentary={handleGenerateCommentary}
              onRestoreCommentary={handleRestoreAiCommentary}
              fiscalYear={fiscalYear}
              period={month || quarter}
              brand={configData?.config?.brandColors ?? SNOWFLAKE_COLORS}
            />
            </div>
          ) : <Skeleton className="h-96" />}
        </CardContent>
      </Card>

      {isFetching && <p className="text-xs text-muted-foreground">Refreshing…</p>}
    </div>
  )
}

/**
 * The "mirror table" beneath the chart: one row per expense category
 * (rendered via MatrixDataRow below) showing trailing-period trend columns,
 * the current-period V1/V2 comparison, the delta, and an editable
 * commentary cell — followed by either user-defined summary rows or a
 * single fallback "Total Expenses" row.
 */
function MatrixTable({
  data,
  commentaryMap,
  onCommentarySave,
  onGenerateCommentary,
  onRestoreCommentary,
  fiscalYear,
  period,
  brand,
}: {
  data: Bva
  commentaryMap: Record<string, CommentaryEntry>
  onCommentarySave: (category: string, text: string) => void
  onGenerateCommentary: (category: string) => void
  onRestoreCommentary: (category: string) => void
  fiscalYear: string
  period: string
  brand: BrandColors
}) {
  const trailing = data.trailingQuarters
  const v1Short = shortSeries(data.version1)
  const v2Short = shortSeries(data.version2)
  const BORDER_COLOR = brand.headerMid
  const SUBTEXT_COLOR = "#B3D4F0"

  // Render a summary / total row (no commentary, accounting format)
  const renderSummaryRow = (label: string, r: Omit<Row, "category">, style: "bold" | "subtotal" = "bold") => {
    const rowCls = style === "subtotal"
      ? "text-sm border-t"
      : "border-t-2 font-semibold"
    const cellCls = style === "subtotal" ? "font-medium text-muted-foreground" : "font-semibold"
    const rowStyle = style === "subtotal"
      ? { backgroundColor: "#EBF5FB" }
      : { backgroundColor: "#EBF5FB", borderTopColor: brand.headerBg }
    return (
      <TableRow key={label} className={rowCls} style={rowStyle}>
        <TableCell className={cellCls}>{label}</TableCell>
        {trailing.map((t) => (
          <TableCell key={t.key} className="text-right tabular-nums">{fmtAcct(r.trailing[t.key] ?? 0)}</TableCell>
        ))}
        <TableCell className="text-right tabular-nums border-l" style={{ color: brand.accent }}>{fmtAcct(r.currentV1)}</TableCell>
        <TableCell className="text-right tabular-nums">{fmtAcct(r.currentV2)}</TableCell>
        <TableCell className="text-right tabular-nums border-l" style={{ color: deltaColor(r.deltaDollar) }}>{fmtAcct(r.deltaDollar)}</TableCell>
        <TableCell className="text-right tabular-nums" style={{ color: deltaColor(r.deltaDollar) }}>{fmtPctAcct(r.deltaPct)}</TableCell>
        <TableCell />
      </TableRow>
    )
  }

  return (
    <Table className="[&_td]:py-1.5 [&_td]:text-xs [&_th]:py-2">
      <TableHeader className="sticky top-0 z-10">
        <TableRow style={{ backgroundColor: brand.headerBg }} className="hover:opacity-95 border-none">
          <TableHead rowSpan={2} className="align-bottom text-white font-semibold border-none">Expense Category</TableHead>
          {trailing.map((t) => (
            <TableHead key={t.key} rowSpan={2} className="text-right align-bottom whitespace-nowrap text-white font-semibold border-none">{t.label}</TableHead>
          ))}
          <TableHead colSpan={2} className="text-center text-white font-semibold border-l" style={{ borderColor: BORDER_COLOR }}>{data.currentLabel}</TableHead>
          <TableHead colSpan={2} className="text-center text-white font-semibold border-l" style={{ borderColor: BORDER_COLOR }}>&#916; vs {v2Short}</TableHead>
          <TableHead rowSpan={2} className="align-bottom text-white font-semibold border-l" style={{ borderColor: BORDER_COLOR }}>Commentary</TableHead>
        </TableRow>
        <TableRow style={{ backgroundColor: brand.headerMid }} className="hover:opacity-95 border-none">
          <TableHead className="text-right border-l font-medium" style={{ borderColor: BORDER_COLOR, color: SUBTEXT_COLOR }}>{v1Short}</TableHead>
          <TableHead className="text-right font-medium" style={{ color: SUBTEXT_COLOR }}>{v2Short}</TableHead>
          <TableHead className="text-right border-l font-medium" style={{ borderColor: BORDER_COLOR, color: SUBTEXT_COLOR }}>$</TableHead>
          <TableHead className="text-right font-medium" style={{ color: SUBTEXT_COLOR }}>%</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.categories.map((c, i) => (
          <MatrixDataRow
            key={c.category}
            row={c}
            trailing={trailing}
            commentaryMap={commentaryMap}
            onCommentarySave={onCommentarySave}
            onGenerateCommentary={onGenerateCommentary}
            onRestoreCommentary={onRestoreCommentary}
            fiscalYear={fiscalYear}
            period={period}
            index={i}
          />
        ))}
        {/* Summary rows (user-defined) or fallback Total Expenses */}
        {(data.summaryRows && data.summaryRows.length > 0)
          ? data.summaryRows.map(r => renderSummaryRow(r.name, r, r.style ?? "bold"))
          : renderSummaryRow("Total Expenses", data.total, "bold")
        }
      </TableBody>
    </Table>
  )
}

/** Renders one category row plus any of its indented breakout sub-rows
 *  (see BreakoutGroup in lib/config.ts) directly beneath it. */
function MatrixDataRow({
  row,
  trailing,
  commentaryMap,
  onCommentarySave,
  onGenerateCommentary,
  onRestoreCommentary,
  fiscalYear,
  period,
  index,
}: {
  row: Row
  trailing: { key: string; label: string }[]
  commentaryMap: Record<string, CommentaryEntry>
  onCommentarySave: (category: string, text: string) => void
  onGenerateCommentary: (category: string) => void
  onRestoreCommentary: (category: string) => void
  fiscalYear: string
  period: string
  index: number
}) {
  const rowBg = index % 2 === 0 ? "bg-white" : "bg-slate-50"
  const commentaryKey = `${row.category}|${fiscalYear}|${period}`
  return (
    <>
      <TableRow className={rowBg}>
        <TableCell className="font-medium whitespace-nowrap">{row.category}</TableCell>
        {trailing.map((t) => (
          <TableCell key={t.key} className="text-right tabular-nums">{fmtAcct(row.trailing[t.key] ?? 0)}</TableCell>
        ))}
        <TableCell className="text-right tabular-nums border-l" style={{ color: A_COLOR }}>{fmtAcct(row.currentV1)}</TableCell>
        <TableCell className="text-right tabular-nums">{fmtAcct(row.currentV2)}</TableCell>
        <TableCell className="text-right tabular-nums border-l" style={{ color: deltaColor(row.deltaDollar) }}>{fmtAcct(row.deltaDollar)}</TableCell>
        <TableCell className="text-right tabular-nums" style={{ color: deltaColor(row.deltaDollar) }}>{fmtPctAcct(row.deltaPct)}</TableCell>
        <TableCell className="border-l min-w-[140px]">
          <CommentaryCell
            entry={commentaryMap[commentaryKey]}
            onSave={(t) => onCommentarySave(row.category, t)}
            onGenerate={() => onGenerateCommentary(row.category)}
            onRestore={() => onRestoreCommentary(row.category)}
          />
        </TableCell>
      </TableRow>
      {row.subRows?.map((sub) => {
        const subKey = `${sub.category}|${fiscalYear}|${period}`
        return (
          <TableRow key={sub.category} className="bg-slate-50 text-sm">
            <TableCell className="pl-8 text-muted-foreground whitespace-nowrap">{sub.category}</TableCell>
            {trailing.map((t) => (
              <TableCell key={t.key} className="text-right tabular-nums text-xs text-muted-foreground">
                {fmtAcct(sub.trailing[t.key] ?? 0)}
              </TableCell>
            ))}
            <TableCell className="text-right tabular-nums border-l text-xs" style={{ color: A_COLOR }}>{fmtAcct(sub.currentV1)}</TableCell>
            <TableCell className="text-right tabular-nums text-xs">{fmtAcct(sub.currentV2)}</TableCell>
            <TableCell className="text-right tabular-nums border-l text-xs" style={{ color: deltaColor(sub.deltaDollar) }}>{fmtAcct(sub.deltaDollar)}</TableCell>
            <TableCell className="text-right tabular-nums text-xs" style={{ color: deltaColor(sub.deltaDollar) }}>{fmtPctAcct(sub.deltaPct)}</TableCell>
            <TableCell className="border-l">
              <CommentaryCell
                entry={commentaryMap[subKey]}
                onSave={(t) => onCommentarySave(sub.category, t)}
                onGenerate={() => onGenerateCommentary(sub.category)}
                onRestore={() => onRestoreCommentary(sub.category)}
              />
            </TableCell>
          </TableRow>
        )
      })}
    </>
  )
}
