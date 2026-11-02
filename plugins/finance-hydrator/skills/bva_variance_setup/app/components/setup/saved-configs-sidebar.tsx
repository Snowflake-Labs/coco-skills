"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, Pencil, Zap, Trash2, RefreshCw, Check } from "lucide-react"
import type { BvaConfig } from "@/lib/config"

function getJson<T>(url: string): Promise<T> {
  return fetch(url).then((r) => r.json())
}

type ProfileEntry = {
  name: string
  updatedAt: string
  sourceType: string | null
  actualsTable: string | null
}

interface Props {
  setConfig: (c: BvaConfig) => void
  goTo: (n: number) => void
}

/**
 * Persistent sidebar (shown alongside the wizard, not a step of its own)
 * listing every named config a customer has saved under Step 7 ("Save
 * configuration"). A named config is a full BvaConfig snapshot the customer
 * can come back to later — e.g. one profile per business unit, or a
 * scratch copy to experiment with before committing.
 *
 * Only one config is ever "active" at a time: the one currently powering the
 * live dashboard, stored under the special name "default" (excluded from this
 * list — see the .filter() below). This sidebar lets the customer, without
 * leaving whatever wizard step they're on:
 *  - Edit  — load a saved config back into the wizard to tweak it
 *  - Activate — copy it into "default" and re-point the dashboard at it
 *  - Delete — remove it permanently
 */
export function SavedConfigsSidebar({ setConfig, goTo }: Props) {
  const [activating, setActivating] = useState<string | null>(null)
  const [deleting, setDeleting]     = useState<string | null>(null)
  const queryClient = useQueryClient()

  const { data: profilesData, refetch, isFetching } = useQuery({
    queryKey: ["config-profiles"],
    queryFn:  () => getJson<{ configs: ProfileEntry[]; activeProfile: string | null }>("/api/setup/config?list=true"),
    staleTime: 30_000,
  })
  const profiles      = (profilesData?.configs ?? []).filter((p) => p.name !== "default")
  const activeProfile = profilesData?.activeProfile ?? null

  // Loads a saved profile's full config into the in-memory wizard state and
  // jumps to Step 1, so the customer can review/edit it. This does NOT make
  // it the active dashboard config — that only happens on Save (Step 7) or
  // via the explicit "Activate" button below.
  const handleEdit = async (name: string) => {
    try {
      const data = await getJson<{ config: BvaConfig | null }>(`/api/setup/config?name=${encodeURIComponent(name)}`)
      if (data.config) {
        setConfig(data.config)
        goTo(1)
      }
    } catch { /* non-fatal */ }
  }

  const handleActivate = async (name: string) => {
    setActivating(name)
    try {
      // Server-side: copies this named profile's config into "default" and
      // triggers re-materialization, so the live dashboard now reflects it.
      await fetch("/api/setup/config/activate", {
        method:  "POST",
        headers: { "content-type": "application/json" },
        body:    JSON.stringify({ name }),
      })
      // Every query the dashboard/wizard reads config through must be
      // invalidated so components re-fetch against the newly-activated
      // config instead of serving stale cached data.
      await Promise.all(
        ["setup-config", "filters", "bva", "breakdown", "time", "query", "config-profiles"].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] })
        )
      )
    } catch { /* non-fatal */ } finally {
      setActivating(null)
    }
  }

  const handleDelete = async (name: string) => {
    if (!window.confirm(`Delete saved configuration "${name}"? This cannot be undone.`)) return
    setDeleting(name)
    try {
      await fetch(`/api/setup/config?name=${encodeURIComponent(name)}`, { method: "DELETE" })
      await queryClient.invalidateQueries({ queryKey: ["config-profiles"] })
    } catch { /* non-fatal */ } finally {
      setDeleting(null)
    }
  }

  return (
    <div className="rounded-lg border border-border/60 bg-muted/20 p-3 space-y-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium">Saved configurations</p>
        <button
          type="button"
          onClick={() => refetch()}
          disabled={isFetching}
          className="text-muted-foreground hover:text-foreground transition-colors disabled:opacity-40"
          title="Refresh list"
        >
          <RefreshCw className={`h-3 w-3 ${isFetching ? "animate-spin" : ""}`} />
        </button>
      </div>

      {profiles.length === 0 ? (
        <p className="text-muted-foreground text-xs leading-relaxed">
          No saved configurations yet. Enter a custom name in Step 7 and save to create one.
        </p>
      ) : (
        <div className="space-y-1">
          {profiles.map((p) => {
            const isActive = p.name === activeProfile
            return (
              <div
                key={p.name}
                className={`rounded-md border px-2.5 py-2 space-y-1.5 ${
                  isActive
                    ? "border-primary/60 bg-primary/5"
                    : "border-border/40 bg-background"
                }`}
              >
                <div className="min-w-0 flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-medium truncate">{p.name}</p>
                    <p className="text-muted-foreground truncate">
                      {p.actualsTable?.split(".").pop() ?? p.sourceType ?? "—"} &middot;{" "}
                      {new Date(p.updatedAt).toLocaleDateString()}
                    </p>
                  </div>
                  {isActive && (
                    <span className="flex items-center gap-0.5 shrink-0 rounded-full bg-primary/15 text-primary px-1.5 py-0.5 text-[10px] font-medium">
                      <Check className="h-2.5 w-2.5" />
                      Active
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => handleEdit(p.name)}
                    className="flex items-center gap-1 px-1.5 py-0.5 rounded border border-border/60 hover:bg-muted transition-colors"
                    title="Load into wizard for editing"
                  >
                    <Pencil className="h-3 w-3" />
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => handleActivate(p.name)}
                    disabled={activating === p.name || isActive}
                    className={`flex items-center gap-1 px-1.5 py-0.5 rounded border transition-colors disabled:opacity-60 ${
                      isActive
                        ? "border-primary/40 text-primary cursor-default"
                        : "border-border/60 hover:bg-muted"
                    }`}
                    title={isActive ? "This is the currently active configuration" : "Make this the active configuration"}
                  >
                    {activating === p.name ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : isActive ? (
                      <Check className="h-3 w-3" />
                    ) : (
                      <Zap className="h-3 w-3" />
                    )}
                    {isActive ? "Active" : "Activate"}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(p.name)}
                    disabled={deleting === p.name}
                    className="flex items-center gap-1 px-1.5 py-0.5 rounded border border-border/60 text-muted-foreground hover:text-destructive hover:border-destructive/50 transition-colors disabled:opacity-50 ml-auto"
                    title="Delete this configuration"
                  >
                    {deleting === p.name
                      ? <Loader2 className="h-3 w-3 animate-spin" />
                      : <Trash2 className="h-3 w-3" />
                    }
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
