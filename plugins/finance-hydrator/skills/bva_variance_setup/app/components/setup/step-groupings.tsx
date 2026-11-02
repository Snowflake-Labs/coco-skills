"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Plus, Trash2 } from "lucide-react"
import { getJson } from "@/lib/format"
import type { BvaConfig, BreakoutGroup, SummaryRowDef } from "@/lib/config"

type Nav = { back: () => void; next: () => void }

// ── Shared helpers ────────────────────────────────────────────────────────────

function uid() { return Math.random().toString(36).slice(2, 10) }

// ── Sub-category breakout panel ───────────────────────────────────────────────
// Lets the user define extra indented rows in the BvA table that break a
// parent expense category down into a more specific slice — e.g. splitting
// "Salaries & Wages" into a "Corporate Accounts" sub-row filtered to specific
// cost centers or account codes. This doesn't change the totals, only adds a
// drill-down row underneath the existing category total. See lib/config.ts's
// BreakoutGroup type for the shape saved into config, and
// app/api/bva/route.ts for where categoryBreakouts is read back out to build
// the dashboard's rows.

function SubCategoryBreakoutsPanel({
  config,
  update,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
}) {
  const breakouts = config.categoryBreakouts ?? []

  const { data: catData } = useQuery({
    queryKey: ["discover", "categories", config.configName],
    queryFn:  () => getJson<{ categories: { value: string }[] }>(
      "/api/setup/discover?action=categories"
    ),
    staleTime: 60_000,
  })
  const categoryOptions = (catData?.categories ?? []).map((c) => c.value)

  const { data: ccData } = useQuery({
    queryKey: ["discover", "cost-centers", config.configName],
    queryFn:  () => getJson<{ costCenters: string[] }>("/api/setup/discover?action=cost-centers"),
    staleTime: 60_000,
    enabled:  false, // only fetched when user picks filterType=cost_center in the form
  })

  const [editing, setEditing] = useState<Partial<BreakoutGroup> | null>(null)

  const save = () => {
    if (!editing?.parentCategory || !editing?.label || !editing?.filterType || !editing.filterValues?.length) return
    const entry: BreakoutGroup = {
      id: editing.id ?? uid(),
      parentCategory: editing.parentCategory,
      label: editing.label,
      filterType: editing.filterType as BreakoutGroup["filterType"],
      filterValues: editing.filterValues,
    }
    const next = editing.id
      ? breakouts.map((b) => (b.id === editing.id ? entry : b))
      : [...breakouts, entry]
    update({ categoryBreakouts: next })
    setEditing(null)
  }

  const remove = (id: string) => update({ categoryBreakouts: breakouts.filter((b) => b.id !== id) })

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold">Sub-category breakouts</h3>
        <p className="text-xs text-muted-foreground mt-0.5">
          Define indented sub-rows that appear under a parent category in the BvA table.
          For example, show &quot;Corporate Accounts&quot; under &quot;Salaries &amp; Wages&quot; filtered by specific cost centers.
        </p>
      </div>

      {breakouts.length === 0 && !editing && (
        <p className="text-xs text-muted-foreground italic">No breakouts defined — rows will show category totals only.</p>
      )}

      {breakouts.map((b) => (
        <div key={b.id} className="flex items-center justify-between text-sm bg-slate-50 border rounded px-3 py-2">
          <div>
            <span className="font-medium">{b.parentCategory}</span>
            <span className="text-muted-foreground mx-1">›</span>
            <span>{b.label}</span>
            <span className="text-xs text-muted-foreground ml-2">
              ({b.filterType === "cost_center" ? "CC" : "Account"}: {b.filterValues.slice(0, 3).join(", ")}{b.filterValues.length > 3 ? ` +${b.filterValues.length - 3}` : ""})
            </span>
          </div>
          <div className="flex gap-1">
            <button
              type="button"
              onClick={() => setEditing({ ...b })}
              className="text-xs px-2 py-1 rounded border hover:bg-muted"
            >Edit</button>
            <button
              type="button"
              onClick={() => remove(b.id)}
              className="text-destructive hover:opacity-70 p-1"
            ><Trash2 size={14} /></button>
          </div>
        </div>
      ))}

      {editing ? (
        <div className="border rounded p-4 space-y-3 bg-white text-sm">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-xs font-medium">Parent category</label>
              {categoryOptions.length > 0 ? (
                <select
                  className="w-full border rounded px-2 py-1.5 text-sm"
                  value={editing.parentCategory ?? ""}
                  onChange={(e) => setEditing({ ...editing, parentCategory: e.target.value })}
                >
                  <option value="">Select category…</option>
                  {categoryOptions.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              ) : (
                <input
                  className="w-full border rounded px-2 py-1.5 text-sm"
                  placeholder="e.g. Salaries & Wages"
                  value={editing.parentCategory ?? ""}
                  onChange={(e) => setEditing({ ...editing, parentCategory: e.target.value })}
                />
              )}
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium">Sub-row label</label>
              <input
                className="w-full border rounded px-2 py-1.5 text-sm"
                placeholder="e.g. Corporate Accounts"
                value={editing.label ?? ""}
                onChange={(e) => setEditing({ ...editing, label: e.target.value })}
              />
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium">Filter by</label>
            <div className="flex gap-2">
              {(["cost_center", "account_code"] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setEditing({ ...editing, filterType: t, filterValues: [] })}
                  className={`text-xs px-3 py-1.5 rounded border ${editing.filterType === t ? "bg-primary text-primary-foreground border-primary" : "hover:bg-muted"}`}
                >
                  {t === "cost_center" ? "Cost Centers" : "Account Codes"}
                </button>
              ))}
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium">
              {editing.filterType === "account_code" ? "Account codes" : "Cost center IDs"} (comma-separated)
            </label>
            <input
              className="w-full border rounded px-2 py-1.5 text-sm font-mono"
              placeholder="CC-1000, CC-1001, CC-1002"
              value={(editing.filterValues ?? []).join(", ")}
              onChange={(e) =>
                setEditing({
                  ...editing,
                  filterValues: e.target.value.split(",").map((v) => v.trim()).filter(Boolean),
                })
              }
            />
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={save}
              disabled={!editing.parentCategory || !editing.label || !editing.filterValues?.length}
              className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-xs font-medium disabled:opacity-50"
            >
              Save breakout
            </button>
            <button type="button" onClick={() => setEditing(null)} className="px-3 py-1.5 rounded border text-xs hover:bg-muted">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setEditing({ filterType: "cost_center", filterValues: [] })}
          className="flex items-center gap-1.5 text-xs text-primary hover:underline"
        >
          <Plus size={13} /> Add breakout
        </button>
      )}
    </div>
  )
}

// ── Summary rows panel ────────────────────────────────────────────────────────
// Lets the user define named "total" rows shown at the bottom of the BvA
// table — e.g. a "Total Controllable Expenses" row that sums only a chosen
// subset of categories, as an alternative/addition to the default single
// "Total Expenses" row (which sums everything). Each row can either include
// a specific set of categories or exclude a specific set (summing the rest).
// See lib/config.ts's SummaryRowDef type for the shape persisted into config.

function SummaryRowsPanel({
  config,
  update,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
}) {
  const summaryRows = config.summaryRows ?? []

  const { data: catData } = useQuery({
    queryKey: ["discover", "categories", config.configName],
    queryFn:  () => getJson<{ categories: { value: string }[] }>(
      "/api/setup/discover?action=categories"
    ),
    staleTime: 60_000,
  })
  const allCategories = (catData?.categories ?? []).map((c) => c.value)

  type Draft = Partial<SummaryRowDef> & { mode?: "include" | "exclude" }
  const [editing, setEditing] = useState<Draft | null>(null)

  const save = () => {
    if (!editing?.name?.trim()) return
    const entry: SummaryRowDef = {
      id:    editing.id ?? uid(),
      name:  editing.name.trim(),
      style: editing.style ?? "bold",
      ...(editing.mode === "include" && editing.includeCategories?.length
        ? { includeCategories: editing.includeCategories }
        : {}),
      ...(editing.mode === "exclude" && editing.excludeCategories?.length
        ? { excludeCategories: editing.excludeCategories }
        : {}),
    }
    const next = editing.id
      ? summaryRows.map((r) => (r.id === editing.id ? entry : r))
      : [...summaryRows, entry]
    update({ summaryRows: next })
    setEditing(null)
  }

  const remove = (id: string) => update({ summaryRows: summaryRows.filter((r) => r.id !== id) })

  const toggleCat = (cat: string, field: "includeCategories" | "excludeCategories") => {
    if (!editing) return
    const current = (editing[field] ?? []) as string[]
    const next    = current.includes(cat) ? current.filter((c) => c !== cat) : [...current, cat]
    setEditing({ ...editing, [field]: next })
  }

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold">Summary rows</h3>
        <p className="text-xs text-muted-foreground mt-0.5">
          Define named total rows shown at the bottom of the BvA table (e.g. &quot;Total Controllable Expenses&quot;).
          If none are defined, a single &quot;Total Expenses&quot; row is shown automatically.
        </p>
      </div>

      {summaryRows.length === 0 && !editing && (
        <p className="text-xs text-muted-foreground italic">
          Default: a single &quot;Total Expenses&quot; row (sum of all categories) will be shown.
        </p>
      )}

      {summaryRows.map((r) => {
        const desc = r.includeCategories?.length
          ? `includes ${r.includeCategories.length} categories`
          : r.excludeCategories?.length
          ? `excludes ${r.excludeCategories.length} categories`
          : "all categories"
        return (
          <div key={r.id} className="flex items-center justify-between text-sm bg-slate-50 border rounded px-3 py-2">
            <div>
              <span className="font-medium">{r.name}</span>
              <span className="text-xs text-muted-foreground ml-2">({desc}, {r.style ?? "bold"})</span>
            </div>
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => setEditing({ ...r, mode: r.includeCategories?.length ? "include" : "exclude" })}
                className="text-xs px-2 py-1 rounded border hover:bg-muted"
              >Edit</button>
              <button type="button" onClick={() => remove(r.id)} className="text-destructive hover:opacity-70 p-1">
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        )
      })}

      {editing ? (
        <div className="border rounded p-4 space-y-3 bg-white text-sm">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-xs font-medium">Row name</label>
              <input
                className="w-full border rounded px-2 py-1.5 text-sm"
                placeholder="e.g. Total Controllable Expenses"
                value={editing.name ?? ""}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium">Style</label>
              <div className="flex gap-2">
                {(["bold", "subtotal"] as const).map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setEditing({ ...editing, style: s })}
                    className={`text-xs px-3 py-1.5 rounded border ${editing.style === s || (!editing.style && s === "bold") ? "bg-primary text-primary-foreground border-primary" : "hover:bg-muted"}`}
                  >
                    {s === "bold" ? "Bold total" : "Subtotal"}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="space-y-1">
            <label className="text-xs font-medium">Category scope</label>
            <div className="flex gap-2 mb-2">
              {(["include", "exclude"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setEditing({ ...editing, mode: m, includeCategories: [], excludeCategories: [] })}
                  className={`text-xs px-3 py-1.5 rounded border ${editing.mode === m ? "bg-primary text-primary-foreground border-primary" : "hover:bg-muted"}`}
                >
                  {m === "include" ? "Include specific" : "Exclude specific (all others)"}
                </button>
              ))}
            </div>
            {editing.mode && allCategories.length > 0 && (
              <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto p-2 border rounded bg-slate-50">
                {allCategories.map((cat) => {
                  const field = editing.mode === "include" ? "includeCategories" : "excludeCategories"
                  const selected = ((editing[field] ?? []) as string[]).includes(cat)
                  return (
                    <button
                      key={cat}
                      type="button"
                      onClick={() => toggleCat(cat, field)}
                      className={`text-xs px-2 py-1 rounded border transition-colors ${selected ? "bg-primary text-primary-foreground border-primary" : "bg-white hover:bg-muted"}`}
                    >
                      {cat}
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          <div className="flex gap-2">
            <button
              type="button"
              onClick={save}
              disabled={!editing.name?.trim()}
              className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-xs font-medium disabled:opacity-50"
            >
              Save row
            </button>
            <button type="button" onClick={() => setEditing(null)} className="px-3 py-1.5 rounded border text-xs hover:bg-muted">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setEditing({ style: "bold", mode: "include", includeCategories: [] })}
          className="flex items-center gap-1.5 text-xs text-primary hover:underline"
        >
          <Plus size={13} /> Add summary row
        </button>
      )}
    </div>
  )
}

// ── Step export ───────────────────────────────────────────────────────────────
//
// Step 6 covers two optional customizations to how the BvA table's rows are
// grouped and totaled for the dashboard's breakdown views: sub-category
// breakouts (extra drill-down rows under a category, via
// SubCategoryBreakoutsPanel) and summary rows (custom named totals at the
// bottom, via SummaryRowsPanel). Neither is required — a customer can click
// straight through to Validate and get sensible defaults (no breakouts, one
// "Total Expenses" row).
export function StepGroupings({
  config,
  update,
  nav,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
  nav: Nav
}) {
  return (
    <div className="space-y-8 max-w-2xl">
      <div>
        <h2 className="text-lg font-semibold">Step 6 — Groupings</h2>
        <p className="text-muted-foreground text-sm mt-1">
          Define sub-category breakout rows and custom summary totals for the BvA table.
          Both sections are optional — click <strong>Next: Validate</strong> to skip.
        </p>
      </div>

      <SubCategoryBreakoutsPanel config={config} update={update} />

      <hr />

      <SummaryRowsPanel config={config} update={update} />

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
