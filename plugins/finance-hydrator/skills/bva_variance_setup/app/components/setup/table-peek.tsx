"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Eye, EyeOff, Loader2 } from "lucide-react"
import { getJson } from "@/lib/format"

type Mapping = { label: string; col: string }
type PreviewResult = { rows: Record<string, unknown>[]; columns: string[] }

/**
 * Compact inline context strip for wizard steps that ask the user to recall
 * details about a table they already selected.
 *
 * Shows: SCHEMA.TABLE name · already-mapped column chips · lazy 5-row preview
 */
export function TablePeek({
  fqn,
  label,
  mappings = [],
}: {
  fqn?: string
  label: string       // e.g. "GL / Actuals", "Budget"
  mappings?: Mapping[] // column role assignments from previous steps
}) {
  const [open, setOpen] = useState(false)

  // Lazy: only fires the preview query once the user clicks "Preview" (enabled:
  // open && !!fqn), so simply rendering this component on a wizard step never
  // costs a query. staleTime: Infinity means once fetched for a given table,
  // it won't re-fetch for the rest of the session — the underlying table's
  // data isn't expected to change while the customer is mid-setup.
  const { data, isFetching } = useQuery({
    queryKey:  ["peek", fqn],
    queryFn:   () =>
      getJson<PreviewResult>(
        `/api/setup/discover?action=preview&table=${encodeURIComponent(fqn!)}`,
      ),
    enabled:   open && !!fqn,
    staleTime: Infinity,
  })

  if (!fqn) return null

  // Show SCHEMA.TABLE — drops the leading DB prefix for brevity; full name on hover
  const parts    = fqn.split(".")
  const shortFqn = parts.length >= 2 ? parts.slice(-2).join(".") : fqn

  // Only display mappings the caller actually resolved a column for — a step
  // may pass a mapping whose col is still "" while the user hasn't picked one yet.
  const activeMappings = mappings.filter((m) => !!m.col)

  return (
    <div className="rounded-md border bg-muted/20 text-xs overflow-hidden">
      {/* ── header row ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 px-3 py-1.5 min-w-0 flex-wrap">
        <span className="text-muted-foreground font-medium shrink-0">{label}:</span>
        <span
          className="font-mono text-foreground max-w-[220px] truncate"
          title={fqn}
        >
          {shortFqn}
        </span>

        {activeMappings.length > 0 && (
          <span className="text-muted-foreground/60 truncate flex-1 min-w-0">
            ·{" "}
            {activeMappings.map((m) => `${m.label}: ${m.col}`).join("  ·  ")}
          </span>
        )}

        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="ml-auto flex items-center gap-1 text-muted-foreground hover:text-foreground shrink-0 transition-colors"
        >
          {isFetching ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : open ? (
            <EyeOff className="h-3.5 w-3.5" />
          ) : (
            <Eye className="h-3.5 w-3.5" />
          )}
          <span>{open ? "Hide" : "Preview"}</span>
        </button>
      </div>

      {/* ── data preview strip ──────────────────────────────────────── */}
      {open && (
        <div
          className="border-t overflow-x-auto overflow-y-auto bg-background"
          style={{ maxHeight: 156 }}
        >
          {data?.rows.length ? (
            <table className="w-full text-[11px]">
              <thead>
                <tr className="sticky top-0 bg-muted/90">
                  {data.columns.map((c) => (
                    <th
                      key={c}
                      className="px-2 py-1 text-left font-medium whitespace-nowrap border-b"
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row, i) => (
                  <tr key={i} className={i % 2 === 0 ? "bg-background" : "bg-muted/20"}>
                    {data.columns.map((c) => (
                      <td
                        key={c}
                        className="px-2 py-0.5 whitespace-nowrap text-muted-foreground border-b border-border/30"
                      >
                        {String(row[c] ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="px-3 py-2 text-muted-foreground italic">
              {isFetching ? "Loading…" : "No preview available"}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
