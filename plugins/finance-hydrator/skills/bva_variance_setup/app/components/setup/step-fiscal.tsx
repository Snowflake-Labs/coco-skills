"use client"

import { useState, useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import { getJson } from "@/lib/format"
import type { BvaConfig } from "@/lib/config"
import { TablePeek } from "@/components/setup/table-peek"

type Nav = { next: () => void; back: () => void }
type ProfileResult = {
  fiscalInfo: {
    fyCol: string | null
    fqCol: string | null
    fyStartMonth: number | null
    rollupValues: string[]
  }
  grain: string
}

const MONTH_NAMES = [
  "", "January (Jan)", "February (Feb)", "March (Mar)", "April (Apr)",
  "May (May)", "June (Jun)", "July (Jul)", "August (Aug)",
  "September (Sep)", "October (Oct)", "November (Nov)", "December (Dec)",
]

// ── Step 3: Fiscal year configuration ────────────────────────────────────────
//
// Not every company's fiscal year starts in January — many run Feb–Jan,
// Jul–Jun, Oct–Sep, etc. — so "fiscal year start month" has to be confirmed
// explicitly rather than assumed. It feeds fyExpr()/fqExpr() in lib/config.ts,
// which use it to translate calendar dates into FY/quarter labels whenever the
// source table doesn't already carry pre-computed fiscal columns (fiscalPath
// "B"). This step also lets the user say whether their table already has
// those pre-computed columns (fiscalPath "A") instead of deriving them, mapping
// roughly to Phase 3 of the setup agent's discovery flow (see ARCHITECTURE.md).
export function StepFiscal({
  config, update, nav,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
  nav: Nav
}) {
  const sourceTable = config.sourceType === "single_table" ? config.singleTable : config.actualsTable
  const isSingle = config.sourceType === "single_table"

  // Probe actuals table for fiscal detection (multi_table only)
  const { data: profile } = useQuery({
    queryKey: ["profile", sourceTable, "fiscal"],
    queryFn: () => getJson<ProfileResult>(`/api/setup/profile?table=${encodeURIComponent(sourceTable!)}`),
    enabled: !!sourceTable && !isSingle,
  })

  // Auto-populate from profile on first load. The server-side profiler
  // (behind /api/setup/profile) has already inspected the actuals table for
  // likely fiscal-year/quarter columns and a plausible start month; this only
  // fills in fields the user hasn't already set, so a returning user's saved
  // choices are never silently overwritten by a fresh guess.
  useEffect(() => {
    if (!profile) return
    const fi = profile.fiscalInfo
    if (fi.fyStartMonth && !config.fyStartMonth) {
      update({ fyStartMonth: fi.fyStartMonth })
    }
    if (fi.fyCol && !config.fiscalYearColActuals) {
      update({
        fiscalPath: "A",
        fiscalYearColActuals: fi.fyCol,
        fiscalQuarterColActuals: fi.fqCol ?? undefined,
      })
    }
    if (fi.rollupValues.length > 0 && !config.rollupExclusions?.length) {
      update({ rollupExclusions: fi.rollupValues })
    }
  }, [profile]) // eslint-disable-line react-hooks/exhaustive-deps

  const fyStartMonth = config.fyStartMonth ?? 1
  const fiscalPath = config.fiscalPath ?? "B"

  // For single-table path, fiscal config is simpler (just confirm anchor version)
  const [selectedFiscalYears, setSelectedFiscalYears] = useState<string[]>([])

  return (
    <div className="space-y-8 max-w-2xl">
      <div>
        <h2 className="text-lg font-semibold">Step 3 — Fiscal year configuration</h2>
        <p className="text-muted-foreground text-sm mt-1">
          Configure how fiscal years and quarters are defined in your data.
          Suggested values are pre-filled below — confirm or adjust as needed.
        </p>
      </div>

      {/* FY start month */}
      <div className="space-y-3">
        <label className="text-sm font-medium block">Fiscal year start month</label>
        <p className="text-xs text-muted-foreground">
          When does your fiscal year begin? This determines how calendar dates map to FY labels and quarters.
          {profile?.fiscalInfo.fyStartMonth && (
            <span className="text-primary ml-1">
              (Suggested: {MONTH_NAMES[profile.fiscalInfo.fyStartMonth]})
            </span>
          )}
        </p>
        <select
          className="border rounded px-3 py-2 text-sm bg-background"
          value={fyStartMonth}
          onChange={(e) => update({ fyStartMonth: Number(e.target.value) })}
        >
          {MONTH_NAMES.slice(1).map((name, i) => (
            <option key={i + 1} value={i + 1}>{name}</option>
          ))}
        </select>
      </div>

      {/* Fiscal path (multi-table only) */}
      {!isSingle && (
        <div className="space-y-3">
          <label className="text-sm font-medium block">Fiscal period columns</label>
          <p className="text-xs text-muted-foreground">
            Does your actuals table already have pre-computed fiscal_year and fiscal_quarter columns,
            or should the app derive them from a raw date column?
          </p>
          <TablePeek
            fqn={config.actualsTable}
            label="GL / Actuals"
            mappings={[
              { label: "Date",        col: config.actualsDateCol       ?? "" },
              { label: "Cost center", col: config.actualsCostCenterCol ?? "" },
              { label: "Amount",      col: config.actualsAmountCol     ?? "" },
            ]}
          />
          <div className="space-y-2">
            <label className="flex items-start gap-3 cursor-pointer">
              <input
                type="radio"
                name="fiscalPath"
                value="A"
                checked={fiscalPath === "A"}
                onChange={() => update({ fiscalPath: "A" })}
                className="mt-0.5"
              />
              <div>
                <p className="text-sm font-medium">My data already has fiscal year and quarter columns</p>
                <p className="text-xs text-muted-foreground">I&#39;ll pick which columns hold the fiscal year and quarter values.</p>
                {fiscalPath === "A" && (
                  <div className="mt-2 flex gap-2 flex-wrap">
                    <label className="text-xs text-muted-foreground">
                      FY column:
                      <input
                        className="ml-1 border rounded px-2 py-0.5 text-xs font-mono bg-background w-40"
                        value={config.fiscalYearColActuals ?? ""}
                        onChange={(e) => update({ fiscalYearColActuals: e.target.value })}
                        placeholder="e.g. fiscal_year"
                      />
                    </label>
                    <label className="text-xs text-muted-foreground">
                      Quarter column (optional):
                      <input
                        className="ml-1 border rounded px-2 py-0.5 text-xs font-mono bg-background w-40"
                        value={config.fiscalQuarterColActuals ?? ""}
                        onChange={(e) => update({ fiscalQuarterColActuals: e.target.value })}
                        placeholder="e.g. fiscal_quarter"
                      />
                    </label>
                  </div>
                )}
              </div>
            </label>
            <label className="flex items-start gap-3 cursor-pointer">
              <input
                type="radio"
                name="fiscalPath"
                value="B"
                checked={fiscalPath === "B"}
                onChange={() => update({ fiscalPath: "B", fiscalYearColActuals: undefined, fiscalQuarterColActuals: undefined })}
                className="mt-0.5"
              />
              <div>
                <p className="text-sm font-medium">My data has transaction dates — calculate fiscal periods from them</p>
                <p className="text-xs text-muted-foreground">
                  I&#39;ll pick a date column and the app will derive fiscal year and quarter using the start month above.
                </p>
                {fiscalPath === "B" && (
                  <div className="mt-2">
                    <label className="text-xs text-muted-foreground">
                      Date column name:
                      <input
                        className="ml-1 border rounded px-2 py-0.5 text-xs font-mono bg-background w-40"
                        value={config.actualsDateCol ?? ""}
                        onChange={(e) => update({ actualsDateCol: e.target.value })}
                        placeholder="e.g. transaction_date"
                      />
                    </label>
                  </div>
                )}
              </div>
            </label>
          </div>
        </div>
      )}

      {/* Budget period format (multi-table only) */}
      {!isSingle && (
        <div className="space-y-3">
          <label className="text-sm font-medium block">Budget period format</label>
          <p className="text-xs text-muted-foreground">How are budget periods represented in your budget table?</p>
          <TablePeek
            fqn={config.budgetTable}
            label="Budget"
            mappings={[
              { label: "Period",      col: config.budgetPeriodCol     ?? "" },
              { label: "Amount",      col: config.budgetAmountCol     ?? "" },
              { label: "Cost center", col: config.budgetCostCenterCol ?? "" },
            ]}
          />
          <div className="space-y-2">
            {[
              { value: "date", label: "Date column", desc: "A DATE or TIMESTAMP column (most common)" },
              { value: "fiscal_quarter_string", label: "Fiscal quarter string", desc: "Text like 'FY2026 Q1' or '2026-Q1'" },
              { value: "annual", label: "Annual budget only", desc: "One row per cost center per year; no quarterly breakdown" },
            ].map((opt) => (
              <label key={opt.value} className="flex items-start gap-3 cursor-pointer">
                <input
                  type="radio"
                  name="budgetPeriodFormat"
                  value={opt.value}
                  checked={(config.budgetPeriodFormat ?? "date") === opt.value}
                  onChange={() => update({ budgetPeriodFormat: opt.value as BvaConfig["budgetPeriodFormat"] })}
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
      )}

      {/* Rollup exclusions (when PATH A and rollup values detected) */}
      {!isSingle && fiscalPath === "A" && (profile?.fiscalInfo.rollupValues.length ?? 0) > 0 && (
        <div className="space-y-2">
          <label className="text-sm font-medium block">Rollup row exclusions</label>
          <p className="text-xs text-muted-foreground">
            Non-standard quarter values found: <code className="bg-muted px-1 rounded">
              {profile!.fiscalInfo.rollupValues.join(", ")}
            </code>. These are likely full-year summary rows that double-count spend. Exclude them?
          </p>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={!!config.rollupExclusions?.length}
              onChange={(e) =>
                update({ rollupExclusions: e.target.checked ? profile!.fiscalInfo.rollupValues : [] })
              }
            />
            Exclude rollup rows from query
          </label>
        </div>
      )}

      {/* Version filter defaults — real per-version fiscal year/quarter coverage,
          computed from data on save (see lib/config-server.ts's updateVersionPeriods).
          Selecting a version on the dashboard uses this to decide whether to narrow
          the Fiscal Year / Quarter filters, instead of guessing from the version
          NAME (many version names are forecast-vintage labels, not period-scope
          labels — e.g. "Q1 Forecast" can span many quarters). */}
      {!isSingle && (
        <div className="space-y-3">
          <label className="text-sm font-medium block">Version filter defaults</label>
          <p className="text-xs text-muted-foreground">
            When a version below is selected on the dashboard, the Fiscal Year / Quarter filters
            update to match. Detected automatically from your data the first time each version is
            seen (after saving) — edit if a detected value looks wrong. Clear a field to let it be
            re-detected on the next save.
          </p>
          {Object.keys(config.versionPeriods ?? {}).length === 0 ? (
            <p className="text-xs text-muted-foreground italic">
              No versions detected yet — save this configuration once, then return here to review
              or override version filter defaults.
            </p>
          ) : (
            <div className="border rounded-lg overflow-hidden divide-y text-xs">
              {Object.entries(config.versionPeriods ?? {}).map(([version, vp]) => (
                <div key={version} className="flex items-center gap-2 px-3 py-2">
                  <span className="flex-1 min-w-0 font-mono truncate" title={version}>{version}</span>
                  <label className="flex items-center gap-1 text-muted-foreground">
                    Years:
                    <input
                      className="ml-1 border rounded px-2 py-1 text-xs font-mono bg-background w-28"
                      value={vp.years.join(", ")}
                      placeholder="e.g. FY2027"
                      onChange={(e) => {
                        const years = e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
                        update({ versionPeriods: { ...config.versionPeriods, [version]: { ...vp, years } } })
                      }}
                    />
                  </label>
                  <label className="flex items-center gap-1 text-muted-foreground">
                    Quarters:
                    <input
                      className="ml-1 border rounded px-2 py-1 text-xs font-mono bg-background w-28"
                      value={vp.quarters.join(", ")}
                      placeholder="e.g. Q1"
                      onChange={(e) => {
                        const quarters = e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
                        update({ versionPeriods: { ...config.versionPeriods, [version]: { ...vp, quarters } } })
                      }}
                    />
                  </label>
                  <button
                    type="button"
                    title="Remove override — will be re-detected on next save"
                    onClick={() => {
                      const next = { ...config.versionPeriods }
                      delete next[version]
                      update({ versionPeriods: next })
                    }}
                    className="shrink-0 px-2 py-1 rounded border text-muted-foreground hover:bg-muted transition-colors"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Actuals anchor version — needed for BOTH single_table and multi_table.
          Every planning_version row shares the same actual_expense values (actuals
          aren't version-specific), so the app must pin one version to read actuals
          from — otherwise they'd be double/triple/quadruple-counted across versions. */}
      <div className="space-y-2">
        <label className="text-sm font-medium block">Actuals anchor version</label>
        <p className="text-xs text-muted-foreground">
          When showing actuals, the app reads from one pinned planning version to avoid
          double-counting. Enter the exact version name that holds your canonical actuals
          (must match a real <code>planning_version</code> value in your {isSingle ? "source table" : "budget table"}).
        </p>
        <input
          className="border rounded px-3 py-2 text-sm font-mono bg-background w-full max-w-md"
          value={config.actualsAnchorVersion ?? ""}
          onChange={(e) => update({ actualsAnchorVersion: e.target.value })}
          placeholder="e.g. Board Plan"
        />
        <p className="text-xs text-muted-foreground">
          Leave blank to auto-select (uses whichever version has the most matching rows in your data).
        </p>
      </div>

      <div className="flex gap-3 pt-4 border-t">
        <button onClick={nav.back} className="px-4 py-2 rounded-lg border text-sm hover:bg-muted transition-colors">Back</button>
        <button
          onClick={nav.next}
          className="px-5 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
        >
          {isSingle ? "Next: Validate" : "Next: Data quality"}
        </button>
      </div>
    </div>
  )
}
