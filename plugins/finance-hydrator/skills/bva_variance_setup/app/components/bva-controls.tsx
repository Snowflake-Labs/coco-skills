"use client"

/** Shared Select / MultiSelect controls used across BvA pages. */

import { useState, useRef, useEffect } from "react"
import { ChevronDown, Search } from "lucide-react"
import type { FilterPreset } from "@/lib/config"

/** Plain single-value dropdown (native `<select>`) with a label above it
 *  and an optional hint line below — used for Version/Fiscal Year/Quarter
 *  filters where exactly one value is ever selected. */
export function Select({
  label,
  value,
  options,
  onChange,
  disabled,
  hint,
}: {
  label: string
  value: string
  options: string[]
  onChange?: (v: string) => void
  disabled?: boolean
  hint?: string
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <select
        className="border rounded-md px-3 py-2 bg-background text-sm min-w-[170px] disabled:opacity-50 disabled:cursor-not-allowed"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange?.(e.target.value)}
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
      {hint && (
        <span className="text-xs text-muted-foreground max-w-[190px] leading-snug">{hint}</span>
      )}
    </label>
  )
}

// ── SavedPresetsBar ───────────────────────────────────────────────────────────

/**
 * Row of chips for saved filter presets ("saved views"), plus a control to
 * save the dashboard's current filter selection as a new named preset.
 * Presets are how a user (or the chat agent, via the ```save_preset``` block
 * — see lib/chat-context.tsx) bookmarks a specific combination of
 * version/year/quarter/org/category/cost-center filters for one-click reuse.
 */
export function SavedPresetsBar({
  presets,
  currentFilters,
  onLoad,
  onSave,
  onDelete,
}: {
  presets: FilterPreset[]
  currentFilters: FilterPreset["filters"]
  onLoad: (preset: FilterPreset) => void
  onSave: (name: string, filters: FilterPreset["filters"]) => void
  onDelete: (id: string) => void
}) {
  // Whether the "save as new preset" name-input row is currently shown
  const [saving, setSaving] = useState(false)
  // In-progress text for the new preset's name, while `saving` is true
  const [nameInput, setNameInput] = useState("")

  // True when any filter differs from its untouched default — used below to
  // decide whether it's worth showing "+ Save current view" at all.
  const hasActiveFilters =
    currentFilters.organizations.length > 0 ||
    currentFilters.categories.length > 0 ||
    currentFilters.costCenters.length > 0 ||
    !!currentFilters.version2 ||
    !!currentFilters.fiscalYear ||
    !!currentFilters.quarter

  // Hide entirely when no presets and no active filters
  if (presets.length === 0 && !hasActiveFilters) return null

  const handleSave = () => {
    const trimmed = nameInput.trim()
    if (!trimmed) return
    onSave(trimmed, currentFilters)
    setNameInput("")
    setSaving(false)
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted-foreground shrink-0">Saved views:</span>

      {/* Existing preset chips */}
      {presets.map((p) => (
        <div
          key={p.id}
          className="flex items-center gap-1 pl-2.5 pr-1 py-1 rounded-full border bg-muted/30 hover:bg-muted/60 transition-colors"
        >
          <button
            type="button"
            className="hover:underline"
            onClick={() => onLoad(p)}
          >
            {p.name}
          </button>
          <button
            type="button"
            className="h-3.5 w-3.5 rounded-full text-muted-foreground hover:text-destructive flex items-center justify-center"
            onClick={() => onDelete(p.id)}
            title={`Delete "${p.name}"`}
          >
            ✕
          </button>
        </div>
      ))}

      {/* Save current view */}
      {saving ? (
        <div className="flex items-center gap-1">
          <input
            type="text"
            autoFocus
            placeholder="View name…"
            value={nameInput}
            onChange={(e) => setNameInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSave()
              if (e.key === "Escape") { setSaving(false); setNameInput("") }
            }}
            className="px-2 py-0.5 text-xs border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary w-32"
          />
          <button
            type="button"
            onClick={handleSave}
            disabled={!nameInput.trim()}
            className="px-2 py-0.5 rounded bg-primary text-primary-foreground text-xs disabled:opacity-50 hover:opacity-90"
          >
            Save
          </button>
          <button
            type="button"
            onClick={() => { setSaving(false); setNameInput("") }}
            className="text-muted-foreground hover:text-foreground"
          >
            ✕
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setSaving(true)}
          className="text-muted-foreground hover:text-foreground underline"
        >
          + Save current view
        </button>
      )}
    </div>
  )
}


/**
 * Multi-value dropdown filter (checkboxes + search) used for
 * Organization/Category/Cost Center/ad-hoc custom-logic filters, where zero
 * or more values can be selected at once (an empty selection means "All").
 * Built from scratch (not a native `<select multiple>`) so it can show a
 * search box, a "select/deselect all" shortcut, and a compact "N selected"
 * summary on the closed trigger button.
 */
export function MultiSelect({
  label,
  selected,
  options,
  onChange,
  minWidth = 180,
}: {
  label: string
  selected: string[]
  options: string[]
  onChange: (v: string[]) => void
  minWidth?: number
}) {
  // Whether the dropdown panel is currently expanded
  const [open, setOpen] = useState(false)
  // Current text in the dropdown's search box (cleared whenever it closes)
  const [search, setSearch] = useState("")
  const containerRef = useRef<HTMLDivElement>(null)

  // Close dropdown when clicking outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
        setSearch("")
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [])

  const filtered = search.trim()
    ? options.filter((o) => o.toLowerCase().includes(search.toLowerCase()))
    : options

  const summary = selected.length === 0 ? "All" : `${selected.length} selected`

  const toggle = (o: string, checked: boolean) =>
    onChange(checked ? [...selected, o] : selected.filter((x) => x !== o))

  return (
    <div className="flex flex-col gap-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <div ref={containerRef} className="relative">
        {/* Trigger button */}
        <button
          type="button"
          style={{ minWidth }}
          className="w-full border rounded-md px-3 py-2 bg-background text-sm text-left flex items-center justify-between gap-2 hover:border-foreground/40 transition-colors"
          onClick={() => {
            setOpen((o) => !o)
            setSearch("")
          }}
        >
          <span className={selected.length === 0 ? "text-muted-foreground/70" : ""}>
            {summary}
          </span>
          <div className="flex items-center gap-1 shrink-0">
            {selected.length > 0 && (
              <button
                type="button"
                className="h-4 w-4 rounded-full bg-muted-foreground/20 text-muted-foreground hover:bg-destructive/20 hover:text-destructive flex items-center justify-center text-[10px] leading-none"
                onClick={(e) => { e.stopPropagation(); onChange([]) }}
                title="Clear selection"
              >
                ✕
              </button>
            )}
            <ChevronDown
              className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
            />
          </div>
        </button>

        {/* Dropdown panel */}
        {open && (
          <div
            className="absolute z-50 mt-1 border rounded-md bg-background shadow-lg"
            style={{ minWidth: Math.max(minWidth, 240) }}
          >
            {/* Search input */}
            <div className="flex items-center gap-2 px-3 py-2 border-b">
              <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <input
                type="text"
                placeholder="Search…"
                value={search}
                autoFocus
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") { setOpen(false); setSearch("") }
                }}
                className="flex-1 text-sm bg-transparent outline-none placeholder:text-muted-foreground/60"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => setSearch("")}
                  className="text-muted-foreground hover:text-foreground text-xs"
                >
                  ✕
                </button>
              )}
            </div>

            {/* Options list */}
            <div className="max-h-60 overflow-y-auto p-1">
              {/* Select all / Deselect all */}
              {filtered.length > 0 && (
                <button
                  type="button"
                  className="text-xs text-muted-foreground px-2 py-1 underline hover:text-foreground w-full text-left"
                  onClick={() => {
                    const filteredSet = new Set(filtered)
                    const allSelected = filtered.every((o) => selected.includes(o))
                    if (allSelected) {
                      onChange(selected.filter((v) => !filteredSet.has(v)))
                    } else {
                      onChange([...selected, ...filtered.filter((o) => !selected.includes(o))])
                    }
                  }}
                >
                  {filtered.every((o) => selected.includes(o))
                    ? `Deselect all${search ? " matching" : ""} (${filtered.length})`
                    : `Select all${search ? " matching" : ""} (${filtered.length})`}
                </button>
              )}
              {filtered.length === 0 ? (
                <p className="text-xs text-muted-foreground px-2 py-2 text-center">No matches</p>
              ) : (
                filtered.map((o) => (
                  <label
                    key={o}
                    className="flex items-center gap-2 px-2 py-1.5 rounded text-sm cursor-pointer hover:bg-muted/50"
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(o)}
                      onChange={(e) => toggle(o, e.target.checked)}
                      className="shrink-0"
                    />
                    <span className="truncate">{o}</span>
                  </label>
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
