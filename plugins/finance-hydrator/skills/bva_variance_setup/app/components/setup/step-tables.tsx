"use client"

import { useState, useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import { CheckCircle2, AlertCircle, Search, Loader2, Copy, Check } from "lucide-react"
import { getJson } from "@/lib/format"
import type { BvaConfig } from "@/lib/config"

type Nav = { next: () => void; back: () => void }
type TableRow = {
  fqn: string; database: string; schema: string; table: string
  type: string; rowCount: number | null; lastAltered: string | null; score: number
}

// ── Config storage section ───────────────────────────────────────────────────
// Lets the user pick/create the BVA_APP_CONFIG table that will hold this app's
// saved settings (see lib/config.ts's file header for the full source-priority
// chain). The wizard can't do anything useful without a place to persist to,
// so this section surfaces the current location and offers to create the
// table in a database/schema the user's role can write to.

type LocationData = { configTable: string; tableExists: boolean }

function ConfigStorageSection({ suggestDb }: { suggestDb?: string }) {
  const [db, setDb]           = useState("")
  const [schema, setSchema]   = useState("")
  const [creating, setCreating] = useState(false)
  const [error, setError]     = useState("")
  const [grantSql, setGrantSql] = useState<string | null>(null)
  const [copied, setCopied]   = useState(false)
  const [showForm, setShowForm] = useState(false)

  const { data, refetch, isLoading } = useQuery({
    queryKey: ["config-location"],
    queryFn:  () => getJson<LocationData>("/api/setup/config-location"),
    staleTime: 30_000,
  })

  useEffect(() => {
    if (!db && !schema) {
      if (data?.configTable) {
        const parts = data.configTable.split(".")
        setDb(parts[0] ?? "")
        setSchema(parts[1] ?? "")
      } else if (suggestDb) {
        setDb(suggestDb)
      }
    }
  }, [data, suggestDb]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleCreate = async () => {
    setCreating(true)
    setError("")
    setGrantSql(null)
    try {
      const res  = await fetch("/api/setup/config-location", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ db: db.trim(), schema: schema.trim() }),
      })
      const json = await res.json()
      if (!res.ok || json.error) {
        setError(json.error ?? "Failed")
        setGrantSql(json.grantSql ?? null)
        return
      }
      setShowForm(false)
      refetch()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error")
    } finally {
      setCreating(false)
    }
  }

  const copyGrant = () =>
    navigator.clipboard.writeText(grantSql!).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })

  if (isLoading) return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />

  if (data?.tableExists && !showForm) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <CheckCircle2 className="h-3.5 w-3.5 text-green-600 shrink-0" />
        <code className="font-mono text-foreground">{data.configTable}</code>
        <button
          type="button"
          onClick={() => setShowForm(true)}
          className="ml-1 text-muted-foreground hover:text-foreground underline"
        >
          Change
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {data?.tableExists && (
        <div className="text-xs text-muted-foreground">
          Current: <code className="font-mono">{data.configTable}</code>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Choose a database and schema where your role can create tables.
        The wizard will create <code className="font-mono">BVA_APP_CONFIG</code> there.
      </p>
      <div className="flex items-center gap-1.5 flex-wrap">
        <input
          className="border rounded px-2 py-1 text-xs font-mono bg-background w-36"
          placeholder="DATABASE"
          value={db}
          onChange={(e) => setDb(e.target.value)}
        />
        <span className="text-muted-foreground text-xs">.</span>
        <input
          className="border rounded px-2 py-1 text-xs font-mono bg-background w-36"
          placeholder="SCHEMA"
          value={schema}
          onChange={(e) => setSchema(e.target.value)}
        />
        <button
          type="button"
          onClick={handleCreate}
          disabled={creating || !db.trim() || !schema.trim()}
          className="flex items-center gap-1.5 px-3 py-1 rounded bg-primary text-primary-foreground
                     text-xs font-medium disabled:opacity-50 hover:opacity-90"
        >
          {creating && <Loader2 className="h-3 w-3 animate-spin" />}
          Create config table
        </button>
        {showForm && (
          <button
            type="button"
            onClick={() => setShowForm(false)}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Cancel
          </button>
        )}
      </div>
      {error && (
        <div className="rounded border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive space-y-1.5">
          <p>{error}</p>
          {grantSql && (
            <div className="flex items-center gap-2">
              <code className="flex-1 rounded bg-destructive/10 px-2 py-1 font-mono break-all">
                {grantSql}
              </code>
              <button type="button" onClick={copyGrant} title="Copy SQL">
                {copied
                  ? <Check className="h-3 w-3 text-green-600" />
                  : <Copy className="h-3 w-3" />}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Schema browser ────────────────────────────────────────────────────────────
// A cascading database → schema → table picker for customers who don't know
// (or don't want to type) the fully-qualified name of their GL/budget/cost
// center table off the top of their head. Tables are pre-scored server-side
// (see ARCHITECTURE.md's Phase 1 discovery heuristics) so a "suggested" badge
// can point at the most finance-sounding, curated candidate first.

function SchemaBrowser({ onSelect }: { onSelect: (fqn: string) => void }) {
  const [db, setDb] = useState("")
  const [schema, setSchema] = useState("")
  const [keyword, setKeyword] = useState("")
  const [debouncedKw, setDebouncedKw] = useState("")

  const { data: dbData } = useQuery({
    queryKey: ["discover-dbs"],
    queryFn: () => getJson<{ databases: string[] }>("/api/setup/discover?action=databases"),
  })

  const { data: schemaData } = useQuery({
    queryKey: ["discover-schemas", db],
    queryFn: () => getJson<{ schemas: string[] }>(`/api/setup/discover?action=schemas&db=${encodeURIComponent(db)}`),
    enabled: !!db,
  })

  const { data: tableData, isFetching: loadingTables } = useQuery({
    queryKey: ["discover-tables", db, schema, debouncedKw],
    queryFn: () =>
      getJson<{ tables: TableRow[] }>(
        `/api/setup/discover?action=tables&db=${encodeURIComponent(db)}&schema=${encodeURIComponent(schema)}&keyword=${encodeURIComponent(debouncedKw)}`,
      ),
    enabled: !!db,
  })

  return (
    <div className="border rounded-lg p-4 space-y-3 bg-muted/20">
      <p className="text-sm font-medium text-muted-foreground">Browse tables</p>
      <div className="flex gap-2 flex-wrap">
        <select
          className="border rounded px-2 py-1.5 text-sm bg-background min-w-[160px]"
          value={db}
          onChange={(e) => { setDb(e.target.value); setSchema("") }}
        >
          <option value="">Select database…</option>
          {dbData?.databases.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        {db && (
          <select
            className="border rounded px-2 py-1.5 text-sm bg-background min-w-[160px]"
            value={schema}
            onChange={(e) => setSchema(e.target.value)}
          >
            <option value="">All schemas</option>
            {schemaData?.schemas.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
        {db && (
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <input
              className="border rounded pl-7 pr-3 py-1.5 text-sm bg-background min-w-[180px]"
              placeholder="Search tables…"
              value={keyword}
              onChange={(e) => {
                setKeyword(e.target.value)
                clearTimeout((window as any)._kwTimer)
                ;(window as any)._kwTimer = setTimeout(() => setDebouncedKw(e.target.value), 400)
              }}
            />
          </div>
        )}
        {loadingTables && <Loader2 className="h-4 w-4 animate-spin self-center text-muted-foreground" />}
      </div>

      {tableData?.tables && (
        <div className="max-h-64 overflow-auto border rounded bg-background">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-muted/80">
              <tr>
                <th className="text-left px-3 py-2">Table</th>
                <th className="text-right px-3 py-2">Rows</th>
                <th className="text-right px-3 py-2">Last updated</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {tableData.tables.map((t) => (
                <tr key={t.fqn} className="border-t hover:bg-muted/50">
                  <td className="px-3 py-2 font-mono">
                    <span className="text-muted-foreground">{t.database}.{t.schema}.</span>{t.table}
                    {t.score >= 7 && <span className="ml-2 text-[10px] text-green-600 font-sans">suggested</span>}
                  </td>
                  <td className="text-right px-3 py-2 tabular-nums text-muted-foreground">
                    {t.rowCount != null
                      ? t.rowCount.toLocaleString()
                      : t.type === "VIEW"
                      ? <span className="text-[11px] italic">view</span>
                      : "—"}
                  </td>
                  <td className="text-right px-3 py-2 text-muted-foreground">{t.lastAltered ?? "—"}</td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => onSelect(t.fqn)}
                      className="text-primary text-xs underline hover:no-underline"
                    >
                      Use
                    </button>
                  </td>
                </tr>
              ))}
              {tableData.tables.length === 0 && (
                <tr><td colSpan={4} className="px-3 py-4 text-center text-muted-foreground">No tables found</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Table input with inline validation ───────────────────────────────────────
// A single "DATABASE.SCHEMA.TABLE" text field used for each of the source
// tables below (actuals/GL, budget, cost center, ledger mapping). Typing a
// name and blurring the field fires a lightweight existence/permission check
// against Snowflake so the user gets immediate feedback (green check / red X)
// instead of only discovering a typo once the wizard tries to query it later.
// The "Browse" button opens SchemaBrowser as an alternative to typing.

function TableInput({
  label, description, value, onChange,
}: {
  label: string; description: string; value: string; onChange: (v: string) => void
}) {
  const [showBrowser, setShowBrowser] = useState(false)
  const [validating, setValidating] = useState(false)
  const [valid, setValid] = useState<boolean | null>(null)
  const [error, setError] = useState("")

  const validate = async (fqn: string) => {
    if (!fqn.trim()) { setValid(null); return }
    setValidating(true)
    try {
      const res = await getJson<{ valid: boolean; error?: string }>(
        `/api/setup/discover?action=validate&table=${encodeURIComponent(fqn.trim())}`
      )
      setValid(res.valid)
      setError(res.error ?? "")
    } finally {
      setValidating(false)
    }
  }

  return (
    <div className="space-y-2">
      <label className="block text-sm font-medium">{label}</label>
      <p className="text-xs text-muted-foreground">{description}</p>
      <div className="flex gap-2 items-start">
        <div className="relative flex-1">
          <input
            className="w-full border rounded px-3 py-2 text-sm font-mono bg-background pr-8"
            placeholder="DATABASE.SCHEMA.TABLE"
            value={value}
            onChange={(e) => { onChange(e.target.value); setValid(null) }}
            onBlur={(e) => validate(e.target.value)}
          />
          <div className="absolute right-2 top-1/2 -translate-y-1/2">
            {validating && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            {!validating && valid === true && <CheckCircle2 className="h-4 w-4 text-green-500" />}
            {!validating && valid === false && <AlertCircle className="h-4 w-4 text-destructive" />}
          </div>
        </div>
        <button
          type="button"
          onClick={() => setShowBrowser((b) => !b)}
          className="border rounded px-3 py-2 text-xs hover:bg-muted transition-colors shrink-0"
        >
          Browse
        </button>
      </div>
      {valid === false && error && (
        <p className="text-xs text-destructive">{error}</p>
      )}
      {showBrowser && (
        <SchemaBrowser
          onSelect={(fqn) => {
            onChange(fqn)
            setShowBrowser(false)
            validate(fqn)
          }}
        />
      )}
    </div>
  )
}

// ── Semantic view browser (multi-select) ─────────────────────────────────────
//
// Account-wide flat list (no db/schema cascade — see the "semantic-views"
// discover action) with keyword search and a chip-toggle multi-select, same
// pattern as the category picker in step-groupings.tsx's SummaryRowsPanel.
// Any number of views can be selected — a customer whose agent is backed by
// 19+ semantic views should be able to point at all the relevant ones, not
// just a capped handful.
type SemanticViewRow = { fqn: string; database: string; schema: string; name: string; comment: string | null }

function SemanticViewBrowser({
  selected, onToggle,
}: {
  selected: string[]
  onToggle: (fqn: string) => void
}) {
  const [keyword, setKeyword] = useState("")
  const [debouncedKw, setDebouncedKw] = useState("")

  const { data, isFetching } = useQuery({
    queryKey: ["discover-semantic-views", debouncedKw],
    queryFn: () =>
      getJson<{ views: SemanticViewRow[] }>(
        `/api/setup/discover?action=semantic-views&keyword=${encodeURIComponent(debouncedKw)}`,
      ),
  })

  return (
    <div className="border rounded-lg p-4 space-y-3 bg-muted/20">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-muted-foreground">Browse semantic views</p>
        {selected.length > 0 && (
          <span className="text-xs text-muted-foreground">{selected.length} selected</span>
        )}
      </div>
      <div className="relative">
        <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
        <input
          className="w-full border rounded pl-7 pr-3 py-1.5 text-sm bg-background"
          placeholder="Search semantic views…"
          value={keyword}
          onChange={(e) => {
            setKeyword(e.target.value)
            clearTimeout((window as any)._svKwTimer)
            ;(window as any)._svKwTimer = setTimeout(() => setDebouncedKw(e.target.value), 400)
          }}
        />
        {isFetching && <Loader2 className="absolute right-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 animate-spin text-muted-foreground" />}
      </div>

      <div className="max-h-64 overflow-auto border rounded bg-background">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-muted/80">
            <tr>
              <th className="px-3 py-2"></th>
              <th className="text-left px-3 py-2">Semantic view</th>
              <th className="text-left px-3 py-2">Comment</th>
            </tr>
          </thead>
          <tbody>
            {data?.views.map((v) => {
              const isSelected = selected.includes(v.fqn)
              return (
                <tr
                  key={v.fqn}
                  className="border-t hover:bg-muted/50 cursor-pointer"
                  onClick={() => onToggle(v.fqn)}
                >
                  <td className="px-3 py-2">
                    <input type="checkbox" checked={isSelected} onChange={() => onToggle(v.fqn)} onClick={(e) => e.stopPropagation()} />
                  </td>
                  <td className="px-3 py-2 font-mono">
                    <span className="text-muted-foreground">{v.database}.{v.schema}.</span>{v.name}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{v.comment ?? "—"}</td>
                </tr>
              )
            })}
            {data && data.views.length === 0 && (
              <tr><td colSpan={3} className="px-3 py-4 text-center text-muted-foreground">No semantic views found</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Semantic view reference (optional, manual) ───────────────────────────────
//
// Customers can point at an existing Snowflake semantic view themselves,
// instead of relying on auto-discovery (SHOW SEMANTIC VIEWS). When set, the
// setup agent skips the broad account scan and describes exactly these views
// — an explicit choice always wins over an inferred one. Any number of views
// can be referenced (see lib/semantic-view-bundle.ts's WARN_MANUAL_VIEWS_THRESHOLD
// — a soft warning, not a cap, for customers with many semantic views).
function SemanticViewSection({
  value, onChange,
}: {
  value: string[]
  onChange: (refs: string[]) => void
}) {
  const [open, setOpen] = useState(value.length > 0)
  const [text, setText] = useState(value.join(", "))
  const [showBrowser, setShowBrowser] = useState(false)

  useEffect(() => { setText(value.join(", ")) }, [value])

  const commit = () => {
    const refs = text.split(",").map((s) => s.trim()).filter(Boolean)
    onChange(refs)
  }

  const toggle = (fqn: string) => {
    const next = value.includes(fqn) ? value.filter((v) => v !== fqn) : [...value, fqn]
    onChange(next)
  }

  return (
    <div className="max-w-2xl space-y-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-sm text-muted-foreground hover:text-foreground"
      >
        {open ? "▾" : "▸"} Point to existing semantic view(s) (optional)
      </button>
      {open && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            If you already have Snowflake semantic view(s) covering this data, enter their fully-qualified names
            (e.g. <code className="font-mono">MY_DB.MY_SCHEMA.MY_SEMANTIC_VIEW</code>) — separate multiple names with
            commas, or use Browse to select several at once. There&apos;s no limit on how many you can point at. The
            setup assistant will use them to raise confidence in table/column mapping instead of searching your
            account for one. Leave blank to skip this — it&apos;s never required.
          </p>
          <div className="flex gap-2 items-start">
            <input
              className="flex-1 border rounded px-3 py-2 text-sm font-mono bg-background"
              placeholder="MY_DB.MY_SCHEMA.MY_SEMANTIC_VIEW"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onBlur={commit}
            />
            <button
              type="button"
              onClick={() => setShowBrowser((b) => !b)}
              className="border rounded px-3 py-2 text-xs hover:bg-muted transition-colors shrink-0"
            >
              Browse
            </button>
          </div>
          {showBrowser && <SemanticViewBrowser selected={value} onToggle={toggle} />}
        </div>
      )}
    </div>
  )
}

// ── Step 1: Tables ────────────────────────────────────────────────────────────
//
// This is the wizard's entry point: it decides WHERE the BvA data comes from,
// before anything downstream (column mapping, fiscal config, etc.) can happen.
// Two paths are offered:
//   - Single table/view: the customer already has one pre-joined table with
//     the standard 9-column BvA shape (see lib/config.ts's BVA_BASELINE_COLUMNS).
//     Nothing further to connect — the rest of the wizard mostly gets skipped.
//   - Multi-table: the customer points the wizard at their raw GL/actuals
//     table, budget/forecast table, and (optionally) a cost center dimension
//     table and a ledger account → category mapping table. buildFromConfig()
//     in lib/config.ts joins these together at query time. This mirrors
//     Phase 1 of the setup agent's discovery flow (see ARCHITECTURE.md) but
//     lets the user pick tables by hand instead of chatting with the agent.
// The category-source radio here also determines whether the ledger mapping
// table input (and its downstream column-mapping UI in step-columns.tsx) is
// shown at all.
export function StepTables({
  config, update, nav,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
  nav: Nav
}) {
  const isMulti = config.sourceType === "multi_table"

  return (
    <div className="space-y-8">
      {/* Source type selector */}
      <div className="space-y-4">
        <h2 className="text-lg font-semibold">Step 1 — Connect your data</h2>
        <p className="text-sm text-muted-foreground">
          Choose how your BvA data is structured. Most teams start with a single pre-joined table
          (often an existing analytics view or SQL pipeline output). The multi-table path connects
          your raw GL, budget, and cost center tables directly.
        </p>

        <div className="grid grid-cols-2 gap-3 max-w-2xl">
          <button
            type="button"
            onClick={() => update({ sourceType: "single_table" })}
            className={`rounded-lg border-2 p-4 text-left transition-colors ${
              !isMulti ? "border-primary bg-primary/5" : "border-border hover:border-muted-foreground"
            }`}
          >
            <p className="font-semibold text-sm">Single table or view</p>
            <p className="text-xs text-muted-foreground mt-1">
              I already have a BvA table, view, or the skill output.
            </p>
          </button>
          <button
            type="button"
            onClick={() => update({ sourceType: "multi_table" })}
            className={`rounded-lg border-2 p-4 text-left transition-colors ${
              isMulti ? "border-primary bg-primary/5" : "border-border hover:border-muted-foreground"
            }`}
          >
            <p className="font-semibold text-sm">Connect source tables</p>
            <p className="text-xs text-muted-foreground mt-1">
              Connect my GL actuals, budget, and cost center tables directly.
            </p>
          </button>
        </div>
      </div>

      {/* Single-table path */}
      {!isMulti && (
        <div className="max-w-2xl space-y-4">
          <TableInput
            label="BvA table or view"
            description="Enter the fully-qualified name of your BvA variance table or view. Must include fiscal_year, fiscal_quarter, expense_category, planning_version, actual_expense, and budget_expense columns."
            value={config.singleTable ?? ""}
            onChange={(v) => update({ singleTable: v })}
          />
          <div className="rounded-lg border bg-muted/30 p-4 text-sm space-y-1">
            <p className="font-medium">Required columns</p>
            <p className="text-xs text-muted-foreground font-mono">
              fiscal_year · fiscal_quarter · cost_center_id · cost_center_name · department · expense_category · planning_version · actual_expense · budget_expense
            </p>
          </div>
        </div>
      )}

      {/* Multi-table path */}
      {isMulti && (
        <div className="max-w-2xl space-y-6">
          <TableInput
            label="Actuals / GL table"
            description="Your general ledger or journal lines table. Should have one row per transaction with a date, amount, and cost center."
            value={config.actualsTable ?? ""}
            onChange={(v) => update({ actualsTable: v })}
          />
          <TableInput
            label="Budget / forecast table"
            description="Your budget or forecast table. Should have cost center, expense category, amount, and a planning version column (if multiple versions exist)."
            value={config.budgetTable ?? ""}
            onChange={(v) => update({ budgetTable: v })}
          />
          <TableInput
            label="Cost center dimension"
            description="Optional. Maps cost center IDs to names and departments. If you don't have one, cost center IDs will be used directly."
            value={config.costCenterTable ?? ""}
            onChange={(v) => update({ costCenterTable: v })}
          />

          {/* Category source */}
          <div className="space-y-2">
            <label className="text-sm font-medium">How are expense categories defined?</label>
            <div className="space-y-2">
              {[
                { value: "column", label: "Category column on my GL", desc: "My GL already has an expense_type or category column." },
                { value: "mapping_table", label: "Account codes + mapping table", desc: "My GL has account codes; a separate table maps them to categories." },
                { value: "cluster_analysis", label: "Account codes only", desc: "My GL has account codes but no mapping table. Requires AI Setup — there's no manual editor for cluster patterns, so this option can't be completed by hand." },
                { value: "auto", label: "Not sure", desc: "Map whatever category-like column you have in Step 2 — we won't try to auto-detect anything further." },
              ].map((opt) => (
                <label key={opt.value} className="flex items-start gap-3 cursor-pointer">
                  <input
                    type="radio"
                    name="categorySource"
                    value={opt.value}
                    // "auto" isn't a real BvaConfig.categorySource value — it behaves
                    // identically to "column" in buildFromConfig() (see lib/config.ts),
                    // so it's stored as "column" directly. That means after a reload
                    // this radio can't distinguish "picked auto" from "picked column"
                    // (both are just categorySource === "column") — an acceptable
                    // trade-off for not persisting a value the type doesn't support.
                    checked={config.categorySource === opt.value || (opt.value === "column" && !config.categorySource)}
                    onChange={() => {
                      const nextSource = (opt.value === "auto" ? "column" : opt.value) as BvaConfig["categorySource"]
                      // Clear the ledger mapping table + its column mappings when
                      // switching AWAY from "mapping_table" — otherwise
                      // step-columns.tsx's Ledger Account Mapping section (gated
                      // purely on ledgerMappingTable truthiness, not categorySource)
                      // keeps rendering and the user can spend time validating a
                      // table buildFromConfig() no longer reads at all.
                      const clearLedgerMap = config.categorySource === "mapping_table" && nextSource !== "mapping_table"
                      update({
                        categorySource: nextSource,
                        ...(clearLedgerMap ? { ledgerMappingTable: undefined, ledgerAccountKeyCol: undefined, ledgerCategoryCol: undefined } : {}),
                      })
                    }}
                    className="mt-0.5"
                  />
                  <div>
                    <p className="text-sm font-medium">{opt.label}</p>
                    <p className="text-xs text-muted-foreground">{opt.desc}</p>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {/* Ledger mapping table (if mapping_table path) */}
          {(config.categorySource === "mapping_table") && (
            <TableInput
              label="Ledger account mapping table"
              description="Maps GL account codes to expense category labels."
              value={config.ledgerMappingTable ?? ""}
              onChange={(v) => update({ ledgerMappingTable: v })}
            />
          )}
        </div>
      )}

      {/* Semantic view reference (optional, manual) */}
      <SemanticViewSection
        value={config.semanticViewRefs ?? []}
        onChange={(refs) => update({ semanticViewRefs: refs.length ? refs : undefined })}
      />

      {/* Config storage location */}
      <div className="space-y-2 max-w-2xl pt-2">
        <p className="text-sm font-medium">Config table location</p>
        <ConfigStorageSection
          suggestDb={config.actualsTable?.split(".")[0]}
        />
      </div>

      {/* Navigation */}
      <div className="flex justify-between items-center pt-4 border-t max-w-2xl">
        <span />
        <button
          onClick={nav.next}
          disabled={!isMulti && !config.singleTable?.trim()}
          className="px-5 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium
                     disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 transition-opacity"
        >
          {isMulti ? "Next: Column mapping" : "Next: Fiscal config"}
        </button>
      </div>
    </div>
  )
}
