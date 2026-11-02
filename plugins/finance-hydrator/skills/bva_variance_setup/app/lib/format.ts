/** Shared $'000 formatting and delta-coloring used across all BvA pages. */

// Standard finance red/green pair for variance display — red = spent more
// than planned (bad), green = spent less than planned (good). Kept as named
// constants (rather than inlined hex) so deltaColor() below and any chart
// legend stay visually consistent.
export const OVER = "#dc2626"   // unfavorable (over budget)
export const UNDER = "#16a34a"  // favorable (under budget)

// Dashboards report dollar figures in thousands (not raw dollars) because
// finance/budget amounts here are large enough that raw-dollar tables would
// be unreadable — this is standard FP&A presentation convention, not a
// precision loss anyone downstream needs.
export function commaK(v: number): string {
  return Math.round(v / 1000).toLocaleString("en-US")
}

/** Format a USD value in $'000 with accounting parentheses for negatives. */
export function fmtK(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—"
  return v < 0 ? `($${commaK(-v)})` : `$${commaK(v)}`
}

/** Format a percentage with accounting parentheses for negatives. */
export function fmtPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—"
  return v < 0 ? `(${Math.abs(v).toFixed(1)}%)` : `${v.toFixed(1)}%`
}

/** Favorable = actual under budget = delta < 0 = green. Over budget = red. */
export function deltaColor(v: number | null | undefined): string | undefined {
  if (v == null || v === 0) return undefined
  return v > 0 ? OVER : UNDER
}

// Collapses every non-Actuals series (any planning version) down to a single
// "Forecast" label for compact UI contexts (e.g. legends) where showing each
// version's full name would be too noisy.
export function shortSeries(name: string): string {
  return name === "Actuals" ? "Actuals" : "Forecast"
}

// Thin fetch wrapper used by client components to call this app's own API
// routes: throws on a non-2xx response or an `{ error }` payload, so callers
// can just `await` and rely on a thrown Error instead of manually checking
// res.ok on every call site.
export async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  const json = await res.json()
  if (!res.ok || json.error) throw new Error(json.error ?? `HTTP ${res.status}`)
  return json as T
}
