"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, Loader2, History, RotateCcw, Download, ShieldAlert, Copy, Check, Pencil, Zap, AlertTriangle } from "lucide-react"
import type { BvaConfig } from "@/lib/config"

// ── label helpers ─────────────────────────────────────────────────────────────

const MONTH_NAMES = [
  "January","February","March","April","May","June",
  "July","August","September","October","November","December",
]

const FISCAL_LABELS: Record<string, string> = {
  A: "Pre-computed fiscal columns",
  B: "Derived from date column",
}

const CAT_LABELS: Record<string, string> = {
  column:           "Direct column in GL table",
  mapping_table:    "Ledger mapping table",
  cluster_analysis: "Account prefix pattern clustering",
  category_remap:   "Manual category remap",
}

function getJson<T>(url: string): Promise<T> {
  return fetch(url).then((r) => r.json())
}

type HistoryEntry = { savedAt: string; config: BvaConfig }
type ProfileEntry = { name: string; updatedAt: string; sourceType: string | null; actualsTable: string | null }

function configSummary(c: BvaConfig): string {
  if (c.sourceType === "single_table") return `Single table: ${c.singleTable?.split(".").pop() ?? "—"}`
  const parts = [c.actualsTable?.split(".").pop(), c.budgetTable?.split(".").pop()].filter(Boolean)
  return `Multi-table: ${parts.join(", ")}`
}

function TableFQN({ value }: { value: string | undefined }) {
  if (!value) return <span className="text-muted-foreground italic text-xs">not set</span>
  return (
    <span className="font-mono text-xs break-all" title={value}>
      {value}
    </span>
  )
}

// ── component ─────────────────────────────────────────────────────────────────

/**
 * Step 7 — Save configuration. This is the final step of the wizard: shows a
 * plain-English summary of everything configured in the previous steps, lets
 * the customer name the config, and persists it to the BVA_APP_CONFIG table
 * in Snowflake (see lib/config.ts's header comment for the config source
 * priority). It also surfaces two secondary flows that don't fit anywhere
 * else in the wizard:
 *   - Named profiles: save under a name other than "default" to keep a
 *     reusable, editable copy (e.g. "West Region") that must be explicitly
 *     "activated" (copied into "default") before it drives the live dashboard.
 *   - Save history: every save is snapshotted, so a bad save can be undone
 *     by restoring an earlier one.
 */
export function StepSave({
  config,
  update: _update,
  setConfig,
  onClearDraft,
  onDone,
  onReset,
  nav,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
  setConfig: (c: BvaConfig) => void
  onClearDraft: () => void
  onDone: () => void
  onReset?: () => void
  nav?: { goTo: (n: number) => void }
}) {
  const [saving, setSaving]             = useState(false)
  const [saved, setSaved]               = useState(false)
  const [error, setError]               = useState("")
  const [grantSql, setGrantSql]         = useState<string | null>(null)
  const [copied, setCopied]             = useState(false)
  const [restoredFrom, setRestoredFrom] = useState<string | null>(null)
  const [activating, setActivating]     = useState<string | null>(null)  // name being activated
  const queryClient = useQueryClient()

  const { data: historyData, refetch: refetchHistory } = useQuery({
    queryKey: ["config-history"],
    queryFn:  () => getJson<{ history: HistoryEntry[] }>("/api/setup/config?history=1"),
    staleTime: 30_000,
  })
  const history = historyData?.history ?? []

  const { data: profilesData } = useQuery({
    queryKey: ["config-profiles"],
    queryFn:  () => getJson<{ configs: ProfileEntry[] }>("/api/setup/config?list=true"),
    staleTime: 30_000,
  })
  // Exclude 'default' from the profiles list — show only named profiles
  const profiles = (profilesData?.configs ?? []).filter((p) => p.name !== "default")

  const handleSave = async () => {
    setSaving(true)
    setError("")
    try {
      const res  = await fetch("/api/setup/config", {
        method:  "POST",
        headers: { "content-type": "application/json" },
        body:    JSON.stringify(config),
      })
      const data = await res.json()
      if (!res.ok || data.error) {
        // The server can reject a save because the current role lacks a
        // Snowflake privilege it needs (e.g. to materialize the BvA table).
        // When that happens it returns the exact GRANT statement an admin
        // needs to run, which the error banner below surfaces with a copy
        // button — the customer's draft is preserved locally either way.
        setGrantSql(data.grantSql ?? null)
        throw new Error(data.error ?? "Save failed")
      }

      // When editing a named profile, also activate it so the dashboard stays in sync.
      const isNamedProfile = config.configName && config.configName !== "default"
      if (isNamedProfile) {
        try {
          await fetch("/api/setup/config/activate", {
            method:  "POST",
            headers: { "content-type": "application/json" },
            body:    JSON.stringify({ name: config.configName }),
          })
        } catch { /* activation failure is non-fatal */ }
      }

      setSaved(true)
      onClearDraft()
      setRestoredFrom(null)
      refetchHistory()
      // Every query key any page reads the config/dashboard data through
      // must be invalidated so the rest of the app picks up the new config
      // and re-materialized data instead of serving stale cached results.
      await Promise.all(
        ["setup-config", "filters", "bva", "breakdown", "time", "query", "config-profiles"].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] })
        )
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save configuration")
    } finally {
      setSaving(false)
    }
  }

  // Loads a previous save snapshot back into the form for review; the
  // customer still has to click Save again to actually persist it (this
  // only mutates the in-memory draft, matching handleLoadProfile below).
  const handleRestore = (entry: HistoryEntry) => {
    setConfig(entry.config)
    setRestoredFrom(entry.savedAt)
    setSaved(false)
  }

  // Load a named profile into the wizard for editing
  const handleLoadProfile = async (name: string) => {
    try {
      const data = await getJson<{ config: BvaConfig | null }>(`/api/setup/config?name=${encodeURIComponent(name)}`)
      if (data.config) {
        setConfig(data.config)
        setSaved(false)
        setRestoredFrom(null)
        nav?.goTo(1)
      }
    } catch { /* non-fatal */ }
  }

  // Activate a named profile (copies to 'default', re-materializes)
  const handleActivateProfile = async (name: string) => {
    setActivating(name)
    try {
      await fetch("/api/setup/config/activate", {
        method:  "POST",
        headers: { "content-type": "application/json" },
        body:    JSON.stringify({ name }),
      })
      await Promise.all(
        ["setup-config", "filters", "bva", "breakdown", "time", "query"].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] })
        )
      )
    } catch { /* non-fatal */ } finally {
      setActivating(null)
    }
  }

  // ── Export config as JSON ───────────────────────────────────────────────
  const handleExport = () => {
    const blob = new Blob([JSON.stringify(config, null, 2)], { type: "application/json" })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement("a")
    a.href     = url
    a.download = `bva-config-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleCopyGrant = () => {
    if (!grantSql) return
    navigator.clipboard.writeText(grantSql).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  // -- saved state --
  // Once handleSave() succeeds, the whole step swaps to this simple
  // confirmation screen (rather than staying on the review form) — there's
  // nothing more to configure, so the only actions left are "go look at the
  // dashboard" or come back later via nav.

  if (saved) {
    return (
      <div className="space-y-6 max-w-xl">
        <div className="flex items-start gap-3 rounded-lg border border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/30 p-4">
          <CheckCircle2 className="h-5 w-5 text-green-600 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold text-sm">Configuration saved</p>
            <p className="text-xs text-muted-foreground mt-1">
              Your BvA data source is configured. The dashboard adapts automatically
              to what your data provides — filters and features adjust based on what&apos;s available.
            </p>
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-sm font-medium">What happens next</p>
          <ul className="text-sm text-muted-foreground space-y-1 list-disc list-inside">
            <li>Filters appear only when the underlying data supports them</li>
            <li>Version comparisons use the versions found in your data</li>
            <li>Return here anytime to edit a single field without redoing the full setup</li>
          </ul>
        </div>

        <button
          onClick={onDone}
          className="px-5 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition-opacity"
        >
          Open BvA Working dashboard
        </button>
      </div>
    )
  }

  // ── review + save state ──────────────────────────────────────────────────

  const isMulti = config.sourceType === "multi_table"

  // Gate for whether to render the "Key columns" summary subsection at all —
  // avoids showing an empty header when none of these were actually mapped.
  const hasKeyColumns =
    config.actualsDateCol ||
    config.actualsAmountCol ||
    config.actualsCostCenterCol ||
    config.actualsAccountCol ||
    config.budgetCostCenterCol ||
    config.ledgerAccountKeyCol

  return (
    <div className="space-y-6 max-w-xl">
      <div>
        <h2 className="text-lg font-semibold">Step 7 — Save configuration</h2>
        <p className="text-muted-foreground text-sm mt-1">
          Review your setup and save. Configuration is stored in your Snowflake account
          and persists across sessions.
        </p>
      </div>

      {/* Config name input — "default" is the reserved name that always
          drives the live dashboard; anything else creates/updates a named,
          reusable profile that must be separately activated. */}
      <div className="space-y-1">
        <label className="text-sm font-medium">Configuration name</label>
        <input
          type="text"
          value={config.configName ?? "default"}
          onChange={(e) => setConfig({ ...config, configName: e.target.value || "default" })}
          className="w-full px-3 py-2 text-sm border rounded-lg bg-background focus:outline-none focus:ring-1 focus:ring-primary"
          placeholder="default"
        />
        <p className="text-xs text-muted-foreground">
          Save under a custom name to create a reusable profile. Leave as &ldquo;default&rdquo; to update the active configuration directly.
        </p>
      </div>
      <div className="rounded-lg border p-4 space-y-3 text-sm">
        <p className="font-medium">Configuration summary</p>
        <dl className="space-y-2">

          {/* Source type */}
          <div className="flex gap-2">
            <dt className="text-muted-foreground w-36 shrink-0">Source type</dt>
            <dd>{isMulti ? "Multi-table (GL + Budget + CC)" : "Single table / view"}</dd>
          </div>

          {/* Single-table */}
          {!isMulti && config.singleTable && (
            <div className="flex gap-2">
              <dt className="text-muted-foreground w-36 shrink-0">Table</dt>
              <dd><TableFQN value={config.singleTable} /></dd>
            </div>
          )}

          {/* Multi-table sections */}
          {isMulti && (
            <>
              {/* Tables */}
              <hr className="border-t border-border/40 !mt-3" />
              <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider !mt-2">Tables</p>

              {config.actualsTable && (
                <div className="flex gap-2">
                  <dt className="text-muted-foreground w-36 shrink-0">GL / Actuals</dt>
                  <dd><TableFQN value={config.actualsTable} /></dd>
                </div>
              )}
              {config.budgetTable && (
                <div className="flex gap-2">
                  <dt className="text-muted-foreground w-36 shrink-0">Budget</dt>
                  <dd><TableFQN value={config.budgetTable} /></dd>
                </div>
              )}
              {config.costCenterTable && (
                <div className="flex gap-2">
                  <dt className="text-muted-foreground w-36 shrink-0">Cost centers</dt>
                  <dd><TableFQN value={config.costCenterTable} /></dd>
                </div>
              )}
              {config.ledgerMappingTable && (
                <div className="flex gap-2">
                  <dt className="text-muted-foreground w-36 shrink-0">Ledger mapping</dt>
                  <dd><TableFQN value={config.ledgerMappingTable} /></dd>
                </div>
              )}

              {/* Key columns */}
              {hasKeyColumns && (
                <>
                  <hr className="border-t border-border/40 !mt-3" />
                  <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider !mt-2">Key columns</p>

                  {config.actualsDateCol && (
                    <div className="flex gap-2">
                      <dt className="text-muted-foreground w-36 shrink-0">GL date</dt>
                      <dd className="font-mono text-xs">{config.actualsDateCol}</dd>
                    </div>
                  )}
                  {config.actualsAmountCol && (
                    <div className="flex gap-2">
                      <dt className="text-muted-foreground w-36 shrink-0">GL amount</dt>
                      <dd className="font-mono text-xs">{config.actualsAmountCol}</dd>
                    </div>
                  )}
                  {config.actualsCostCenterCol && (
                    <div className="flex gap-2">
                      <dt className="text-muted-foreground w-36 shrink-0">GL cost center</dt>
                      <dd className="font-mono text-xs">{config.actualsCostCenterCol}</dd>
                    </div>
                  )}
                  {config.actualsAccountCol && (
                    <div className="flex gap-2">
                      <dt className="text-muted-foreground w-36 shrink-0">GL account</dt>
                      <dd className="font-mono text-xs">{config.actualsAccountCol}</dd>
                    </div>
                  )}
                  {config.budgetCostCenterCol && (
                    <div className="flex gap-2">
                      <dt className="text-muted-foreground w-36 shrink-0">Budget cost center</dt>
                      <dd className="font-mono text-xs">{config.budgetCostCenterCol}</dd>
                    </div>
                  )}
                  {config.ledgerAccountKeyCol && (
                    <div className="flex gap-2">
                      <dt className="text-muted-foreground w-36 shrink-0">Ledger join key</dt>
                      <dd className="font-mono text-xs">{config.ledgerAccountKeyCol}</dd>
                    </div>
                  )}
                </>
              )}

              {/* Fiscal */}
              {config.fiscalPath && (
                <>
                  <hr className="border-t border-border/40 !mt-3" />
                  <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider !mt-2">Fiscal</p>

                  <div className="flex gap-2">
                    <dt className="text-muted-foreground w-36 shrink-0">Fiscal year source</dt>
                    <dd>{FISCAL_LABELS[config.fiscalPath] ?? config.fiscalPath}</dd>
                  </div>
                  {config.fyStartMonth && config.fyStartMonth !== 1 && (
                    <div className="flex gap-2">
                      <dt className="text-muted-foreground w-36 shrink-0">Fiscal year starts</dt>
                      <dd>{MONTH_NAMES[config.fyStartMonth - 1]}</dd>
                    </div>
                  )}
                </>
              )}

              {/* Categories */}
              {config.categorySource && (
                <>
                  <hr className="border-t border-border/40 !mt-3" />
                  <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider !mt-2">Categories</p>

                  <div className="flex gap-2">
                    <dt className="text-muted-foreground w-36 shrink-0">Category source</dt>
                    <dd>{CAT_LABELS[config.categorySource] ?? config.categorySource}</dd>
                  </div>
                </>
              )}
            </>
          )}
        </dl>
      </div>

      {/* Restored-from banner */}
      {restoredFrom && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 dark:bg-amber-900/20 px-4 py-2.5 text-sm text-amber-800 dark:text-amber-200 flex items-center justify-between gap-3">
          <span>
            Restored from {new Date(restoredFrom).toLocaleString()} — review the summary above and save to apply.
          </span>
          <button
            type="button"
            onClick={() => setRestoredFrom(null)}
            className="text-xs opacity-60 hover:opacity-100 shrink-0"
          >
            ✕
          </button>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 space-y-2">
          <p className="text-sm text-destructive">{error}</p>
          {grantSql && (
            <div className="space-y-1.5">
              <p className="text-xs text-destructive/80 flex items-center gap-1">
                <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
                Run this SQL as an admin to grant the required privilege, then save again:
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 rounded bg-destructive/10 px-3 py-1.5 text-xs font-mono text-destructive break-all">
                  {grantSql}
                </code>
                <button
                  type="button"
                  onClick={handleCopyGrant}
                  className="shrink-0 flex items-center gap-1 px-2 py-1.5 rounded border text-xs hover:bg-muted"
                  title="Copy SQL"
                >
                  {copied ? <Check className="h-3 w-3 text-green-600" /> : <Copy className="h-3 w-3" />}
                </button>
              </div>
              <p className="text-[11px] text-muted-foreground">
                Your configuration draft is auto-saved locally — your progress is safe. Fix the privilege and click Save again.
              </p>
            </div>
          )}
        </div>
      )}

      {/* Agent configuration editing toggle — only relevant when using the
          ledger mapping table category source, since accountCategoryOverrides
          (the only thing the agent is allowed to touch) is specific to that
          path. See BvaConfig.agentCanEditConfig / README §14 for the guardrails
          around what this lets the dashboard's Cortex Chat agent do. */}
      {config.sourceType === "multi_table" && config.categorySource === "mapping_table" && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-4 space-y-3">
          <div className="flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-amber-500 shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="font-medium text-sm">Allow agent to edit configuration</p>
              <p className="text-xs text-muted-foreground mt-1">
                When enabled, the Cortex Chat agent on the dashboard can suggest account category
                override changes. Each accepted suggestion triggers rematerialization, which may
                take several minutes. The agent can <strong>only</strong> modify account-level category overrides —
                source tables, column mappings, and all structural settings are always read-only.
              </p>
            </div>
            <input
              type="checkbox"
              checked={config.agentCanEditConfig ?? false}
              onChange={(e) => setConfig({ ...config, agentCanEditConfig: e.target.checked })}
              className="mt-1 h-4 w-4 shrink-0"
            />
          </div>
        </div>
      )}

      {/* Brand color palette */}
      <div className="rounded-md border p-4 space-y-3">
        <div>
          <p className="font-medium text-sm">Brand colors</p>
          <p className="text-xs text-muted-foreground mt-1">
            Customize the table header, accent, and chart colors. Defaults to the Snowflake brand palette.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {(["headerBg", "headerMid", "accent", "accentAlt"] as const).map((key) => {
            const labels: Record<string, string> = {
              headerBg:  "Header (dark)",
              headerMid: "Header (mid)",
              accent:    "Actuals color",
              accentAlt: "Forecast color",
            }
            const defaults: Record<string, string> = {
              headerBg:  "#11264F",
              headerMid: "#1A3A6E",
              accent:    "#29B5E8",
              accentAlt: "#0F5FA8",
            }
            const current = (config.brandColors?.[key] ?? defaults[key]) as string
            return (
              <div key={key} className="space-y-1">
                <label className="text-xs font-medium text-muted-foreground">{labels[key]}</label>
                <div className="flex items-center gap-2">
                  <input
                    type="color"
                    value={current}
                    onChange={(e) =>
                      setConfig({
                        ...config,
                        brandColors: { ...(config.brandColors ?? { headerBg: "#11264F", headerMid: "#1A3A6E", accent: "#29B5E8", accentAlt: "#0F5FA8" }), [key]: e.target.value },
                      })
                    }
                    className="h-8 w-10 cursor-pointer rounded border p-0.5 shrink-0"
                  />
                  <input
                    type="text"
                    value={current}
                    maxLength={7}
                    placeholder="#000000"
                    onChange={(e) => {
                      const v = e.target.value
                      // Apply immediately if valid hex; otherwise just update display
                      const base = config.brandColors ?? { headerBg: "#11264F", headerMid: "#1A3A6E", accent: "#29B5E8", accentAlt: "#0F5FA8" }
                      setConfig({ ...config, brandColors: { ...base, [key]: v } })
                    }}
                    onBlur={(e) => {
                      // On blur, normalise: ensure it starts with # and is 7 chars
                      let v = e.target.value.trim()
                      if (!v.startsWith("#")) v = `#${v}`
                      if (!/^#[0-9A-Fa-f]{6}$/.test(v)) v = defaults[key]
                      const base = config.brandColors ?? { headerBg: "#11264F", headerMid: "#1A3A6E", accent: "#29B5E8", accentAlt: "#0F5FA8" }
                      setConfig({ ...config, brandColors: { ...base, [key]: v } })
                    }}
                    className="w-20 text-xs font-mono border rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
              </div>
            )
          })}
        </div>
        {config.brandColors && (
          <button
            type="button"
            onClick={() => setConfig({ ...config, brandColors: undefined })}
            className="text-xs text-muted-foreground underline hover:text-foreground"
          >
            Reset to Snowflake defaults
          </button>
        )}
      </div>

      {/* Actions */}
      <div className="flex flex-wrap gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-2 px-5 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium
                     disabled:opacity-60 hover:opacity-90 transition-opacity"
        >
          {saving && <Loader2 className="h-4 w-4 animate-spin" />}
          {saving ? "Saving…" : "Save configuration"}
        </button>
        <button
          type="button"
          onClick={handleExport}
          className="flex items-center gap-2 px-4 py-2 rounded-lg border text-sm font-medium hover:bg-muted transition-colors"
          title="Download config as JSON backup"
        >
          <Download className="h-4 w-4" />
          Export JSON
        </button>
      </div>

      {/* Previous saves (collapsible) — every successful save is appended to
          a history log server-side; "Restore" loads an older snapshot back
          into the form (still requires clicking Save again to take effect). */}
      {history.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer select-none text-muted-foreground hover:text-foreground flex items-center gap-1.5 w-fit list-none">
            <History className="h-3.5 w-3.5" />
            Previous saves ({history.length})
          </summary>
          <div className="mt-2 space-y-1.5 pl-1">
            {history.slice(0, 5).map((entry, i) => (
              <div
                key={i}
                className="flex items-center justify-between gap-3 py-1.5 px-3 rounded border text-xs"
              >
                <div className="min-w-0 space-y-0.5">
                  <div className="text-muted-foreground">
                    {new Date(entry.savedAt).toLocaleString()}
                  </div>
                  <div className="text-muted-foreground/60 truncate">
                    {configSummary(entry.config)}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => handleRestore(entry)}
                  className="flex items-center gap-1 text-xs text-blue-600 hover:underline shrink-0"
                >
                  <RotateCcw className="h-3 w-3" />
                  Restore
                </button>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Saved configurations — named profiles (excludes "default"); same
          Edit/Activate actions as the persistent SavedConfigsSidebar. */}
      <div className="space-y-2">
        <p className="text-sm font-medium">Saved configurations</p>
        {profiles.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No saved configurations yet. Enter a custom name (not &ldquo;default&rdquo;) above and save to create one.
          </p>
        ) : (
          <div className="border rounded-lg overflow-hidden divide-y text-xs">
            {profiles.map((p) => (
              <div key={p.name} className="flex items-center gap-2 px-3 py-2.5 hover:bg-muted/20">
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">{p.name}</p>
                  <p className="text-muted-foreground truncate">
                    {p.actualsTable?.split(".").pop() ?? p.sourceType ?? "—"} &middot;{" "}
                    {new Date(p.updatedAt).toLocaleDateString()}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    type="button"
                    onClick={() => handleLoadProfile(p.name)}
                    className="flex items-center gap-1 px-2 py-1 rounded border text-xs hover:bg-muted transition-colors"
                    title="Load into wizard for editing"
                  >
                    <Pencil className="h-3 w-3" />
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => handleActivateProfile(p.name)}
                    disabled={activating === p.name}
                    className="flex items-center gap-1 px-2 py-1 rounded border text-xs hover:bg-muted transition-colors disabled:opacity-50"
                    title="Make this the active configuration"
                  >
                    {activating === p.name
                      ? <Loader2 className="h-3 w-3 animate-spin" />
                      : <Zap className="h-3 w-3" />
                    }
                    Activate
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Start new — non-destructive, just blanks the in-memory form */}
      <div className="pt-2 border-t border-border/40">
        <button
          type="button"
          onClick={() => { onClearDraft(); onReset?.() }}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-muted-foreground/70 transition-colors"
        >
          <RotateCcw className="h-3 w-3" />
          Start new configuration
        </button>
      </div>
    </div>
  )
}
