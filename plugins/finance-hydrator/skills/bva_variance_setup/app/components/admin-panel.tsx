"use client"

/**
 * Single admin-only entry point for controls that should never be visible to
 * a regular viewer OR sit open on screen during a live screen-share: the
 * caller's/owner's-rights toggle (agent-access-controls.tsx) and the
 * discovery-scan-scope override (discovery-scope-controls.tsx).
 *
 * Renders nothing for non-admin viewers — not even the gear icon. For an
 * admin, renders only a small, unlabeled gear icon; the actual controls stay
 * hidden until deliberately clicked open. This is meant to be configured
 * once before a live demo, not left visible during it.
 *
 * isAdmin is read once here (via the agent-access-mode status endpoint,
 * which already computes it) and used to gate the whole panel — the two
 * inner control components don't re-gate themselves; the real security
 * boundary is server-side on every write regardless.
 */

import { useEffect, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Settings2, X } from "lucide-react"
import { getJson } from "@/lib/format"
import { AgentAccessControls } from "@/components/agent-access-controls"
import { DiscoveryScopeControls } from "@/components/discovery-scope-controls"

type StatusResponse = { isAdmin: boolean }

export function AdminPanel() {
  // Reuses the agent-access-mode status endpoint purely for its isAdmin flag
  // — it already has to compute this server-side, so there's no need for a
  // separate "am I admin" endpoint just for this panel's visibility check.
  const { data } = useQuery({
    queryKey: ["agent-access-mode"],
    queryFn: () => getJson<StatusResponse>("/api/agent-access-mode"),
  })
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // Closes the popover on an outside click or Escape — standard dropdown/
  // popover dismissal behavior, wired up only while it's open.
  useEffect(() => {
    if (!open) return
    const handlePointer = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false)
    }
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    document.addEventListener("mousedown", handlePointer)
    document.addEventListener("keydown", handleKey)
    return () => {
      document.removeEventListener("mousedown", handlePointer)
      document.removeEventListener("keydown", handleKey)
    }
  }, [open])

  // Non-admins (and while isAdmin is still loading) get nothing rendered at
  // all — not a disabled icon, not a placeholder. See the file-level comment
  // for why this needs to be invisible rather than just inert.
  if (!data?.isAdmin) return null

  return (
    <div ref={containerRef} className="flex justify-end relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="Admin settings"
        aria-label="Admin settings"
        aria-expanded={open}
        className={
          "p-1.5 rounded-md transition-colors " +
          (open ? "bg-accent text-foreground" : "text-muted-foreground/40 hover:text-muted-foreground hover:bg-accent/50")
        }
      >
        <Settings2 className="h-4 w-4" />
      </button>
      {open && (
        <div className="absolute right-0 top-10 z-20 w-[380px] rounded-xl border border-border bg-background shadow-xl overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-border bg-muted/40">
            <span className="text-sm font-semibold">Admin settings</span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close"
              className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="divide-y divide-border">
            <div className="p-4">
              <AgentAccessControls />
            </div>
            <div className="p-4">
              <DiscoveryScopeControls />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
