"use client";

/**
 * Shared chart utilities — library-agnostic.
 *
 * Import these into any chart component (Recharts, Nivo, etc.) to avoid
 * the two most common usability issues in generated apps:
 *   1. Y-axis labels clipped for large numbers  → getYAxisWidth
 *   2. Tooltip text illegible on dark backgrounds → ChartTooltip
 *
 * Add the chart library of your choice to package.json separately.
 */

/**
 * Compute the pixel width needed for a chart's Y-axis so tick labels never
 * overflow the SVG boundary. Measures the widest formatted label using the
 * Canvas 2D API; falls back to a character-width estimate for SSR.
 *
 * Recharts:  <YAxis width={getYAxisWidth(data, "revenue")} tickFormatter={formatTick} />
 * Nivo:      margin={{ left: getYAxisWidth(data, "revenue") }}
 */
export function getYAxisWidth(
  data: Record<string, unknown>[],
  key: string,
  fontSize = 12,
): number {
  const maxVal = Math.max(...data.map((d) => Math.abs(Number(d[key]) || 0)));
  const label = formatTick(maxVal);

  if (typeof document !== "undefined") {
    try {
      const ctx = document.createElement("canvas").getContext("2d");
      if (ctx) {
        ctx.font = `${fontSize}px sans-serif`;
        return Math.ceil(ctx.measureText(label).width) + 20;
      }
    } catch {
      // SSR or canvas unavailable — fall through to estimate
    }
  }

  // Canvas 2D API isn't available during server-side rendering (no `document`),
  // so fall back to a rough ~8px-per-character estimate instead of measuring.
  return Math.max(40, label.length * 8 + 20); // ~8 px/char fallback
}

/**
 * Format a numeric tick value with K/M abbreviation.
 * Pass this to the YAxis `tickFormatter` prop alongside `getYAxisWidth`.
 */
export function formatTick(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(0)}K`;
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

interface TooltipPayloadEntry {
  name: string;
  value: number;
  color: string;
  dataKey: string;
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: string;
}

/**
 * Chart tooltip with explicit popover styles.
 *
 * Default chart tooltips inherit colors from the DOM, which produces
 * near-invisible text when both the tooltip and chart canvas are dark.
 * This component uses CSS variables from globals.css and is readable on
 * any background theme.
 *
 * Recharts:  <Tooltip content={<ChartTooltip />} />
 * Nivo:      tooltip={({ ... }) => <ChartTooltip active ... />}
 */
export function ChartTooltip({ active, payload, label }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;

  return (
    <div
      style={{
        background: "var(--popover)",
        color: "var(--popover-foreground)",
        border: "1px solid var(--border)",
        borderRadius: 6,
        padding: "8px 12px",
        fontSize: 12,
        boxShadow: "0 4px 12px rgba(0,0,0,0.35)",
        minWidth: 120,
      }}
    >
      {label && (
        <p style={{ marginBottom: 4, fontWeight: 600, marginTop: 0 }}>{label}</p>
      )}
      {payload.map((entry) => (
        <p key={entry.dataKey} style={{ color: entry.color, margin: 0 }}>
          {entry.name}: {Number(entry.value).toLocaleString()}
        </p>
      ))}
    </div>
  );
}
