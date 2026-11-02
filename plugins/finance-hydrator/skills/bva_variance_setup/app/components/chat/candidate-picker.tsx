"use client"

import { useState } from "react"
import { CheckCircle2, Circle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import type { BvaConfig } from "@/lib/config"

// ─── Types ────────────────────────────────────────────────────────────────────
//
// This widget shows up during setup when the agent has scanned the account
// for candidate tables and needs the user to pick one per role (GL/actuals,
// budget, cost center mapping, ledger mapping). The agent emits a
// ```candidates fenced block (or ```json, see cortex-chat.tsx's
// CANDIDATES_FENCE_RE) whose body matches CandidatesPayload; cortex-chat.tsx
// parses it out of the message text and mounts this component in its place.

export interface CandidateEntry {
  table: string
  rows: string
  confidence: "high" | "medium" | "low"
  reason: string
  recommended: boolean
}

export interface CandidatesPayload {
  gl?: CandidateEntry[]
  budget?: CandidateEntry[]
  cc?: CandidateEntry[]
  ledger?: CandidateEntry[]
}

interface Props {
  data: CandidatesPayload
  onConfirm: (message: string) => void
  // Optional: lets the picker also pre-fill the Settings wizard's form state
  // directly (bypassing another round-trip through the agent) once the user
  // confirms their selections. Wired up via CortexChatPanel -> applyAgentDraft.
  onAutofill?: (partial: Partial<BvaConfig>) => void
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

const SECTION_LABELS: Record<keyof CandidatesPayload, string> = {
  gl:     "GL / Actuals",
  budget: "Budget / Forecast",
  cc:     "Cost Center Mapping",
  ledger: "Ledger Account Mapping",
}

const SECTION_ORDER: Array<keyof CandidatesPayload> = ["gl", "budget", "cc", "ledger"]

const CONFIDENCE_ORDER = { high: 0, medium: 1, low: 2 }

function confidenceBadge(c: CandidateEntry["confidence"]) {
  const styles: Record<string, string> = {
    high:   "bg-green-100  text-green-800  border-green-200",
    medium: "bg-yellow-100 text-yellow-800 border-yellow-200",
    low:    "bg-gray-100   text-gray-600   border-gray-200",
  }
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${styles[c]}`}>
      {c.charAt(0).toUpperCase() + c.slice(1)}
    </span>
  )
}

// Short display name: last segment of "DB.SCHEMA.TABLE" → "TABLE"
function tableName(fqn: string) {
  return fqn.split(".").pop() ?? fqn
}

// ─── Single section ───────────────────────────────────────────────────────────
//
// Renders one role's candidates (e.g. all the GL/actuals table options) as a
// radio-style list. Table selection happens locally in this component's
// state (see Main component below) — nothing is sent to the agent until the
// user hits "Use these tables" and handleConfirm() fires for the whole set.

function SectionPicker({
  sectionKey,
  label,
  candidates,
  selected,
  onSelect,
  confirmed,
}: {
  sectionKey: string
  label: string
  candidates: CandidateEntry[]
  selected: string
  onSelect: (table: string) => void
  confirmed: boolean
}) {
  const sorted = [...candidates].sort(
    (a, b) => CONFIDENCE_ORDER[a.confidence] - CONFIDENCE_ORDER[b.confidence]
  )

  return (
    <div>
      <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide mb-1.5">
        {label}
      </p>
      <div className="space-y-1.5">
        {sorted.map((c) => {
          const isSelected = selected === c.table
          return (
            <button
              key={c.table}
              disabled={confirmed}
              onClick={() => !confirmed && onSelect(c.table)}
              className={[
                "w-full text-left rounded-lg border p-2.5 transition-all",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-primary",
                confirmed
                  ? isSelected
                    ? "border-primary/40 bg-primary/5 cursor-default"
                    : "hidden"
                  : isSelected
                  ? "border-primary bg-primary/5 ring-1 ring-primary/30 cursor-pointer"
                  : "border-border bg-background hover:border-primary/40 hover:bg-muted/30 cursor-pointer",
              ].join(" ")}
            >
              <div className="flex items-start gap-2">
                <div className="mt-0.5 shrink-0 text-primary">
                  {isSelected
                    ? <CheckCircle2 className="h-3.5 w-3.5" />
                    : <Circle className="h-3.5 w-3.5 text-muted-foreground/50" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5 mb-0.5">
                    <span className="font-mono text-[11px] font-medium break-all leading-tight">
                      {tableName(c.table)}
                    </span>
                    {confidenceBadge(c.confidence)}
                    {c.recommended && !confirmed && (
                      <span className="text-[10px] text-primary font-medium">★ recommended</span>
                    )}
                  </div>
                  <p className="text-[10px] font-mono text-muted-foreground/70 break-all leading-tight mb-1">
                    {c.table}
                  </p>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-muted-foreground">{c.rows} rows</span>
                    <span className="text-[10px] text-muted-foreground">·</span>
                    <span className="text-[10px] text-muted-foreground">{c.reason}</span>
                  </div>
                </div>
              </div>
            </button>
          )
        })}

        {/* Optional sections allow "None" */}
        {(sectionKey === "cc" || sectionKey === "ledger") && !confirmed && (
          <button
            onClick={() => onSelect("")}
            className={[
              "w-full text-left rounded-lg border p-2 transition-all text-[11px] text-muted-foreground",
              selected === ""
                ? "border-primary bg-primary/5 ring-1 ring-primary/30"
                : "border-border hover:border-muted-foreground/30",
            ].join(" ")}
          >
            <div className="flex items-center gap-2">
              {selected === ""
                ? <CheckCircle2 className="h-3 w-3 text-primary" />
                : <Circle className="h-3 w-3 text-muted-foreground/40" />}
              <span>None / skip</span>
            </div>
          </button>
        )}
      </div>
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export function CandidatePicker({ data, onConfirm, onAutofill }: Props) {
  // Pre-select a sensible default per section so the user usually only needs
  // to review and confirm, not hunt through every candidate. Falls back to
  // the first candidate if none is flagged `recommended` (shouldn't normally
  // happen, but keeps the picker usable either way).
  const initialSelections = () => {
    const init: Partial<Record<keyof CandidatesPayload, string>> = {}
    for (const key of SECTION_ORDER) {
      const candidates = data[key]
      if (!candidates?.length) continue
      const rec = candidates.find((c) => c.recommended) ?? candidates[0]
      init[key] = rec.table
    }
    return init
  }

  const [selections, setSelections] = useState<Partial<Record<keyof CandidatesPayload, string>>>(
    initialSelections
  )
  const [confirmed, setConfirmed] = useState(false)

  const sections = SECTION_ORDER.filter((k) => data[k]?.length)

  const handleConfirm = () => {
    // Build the user's "answer" as a plain chat message summarizing every
    // selection — same mechanism as QuestionOptions: there's no structured
    // response channel, so the confirmation is sent back through the normal
    // chat pipeline and the agent reads it like any other user turn. The
    // trailing instruction nudges the agent to move on to the next setup
    // phase (column verification) rather than re-asking about tables.
    const lines: string[] = ["I'd like to use these tables:"]
    for (const key of SECTION_ORDER) {
      const table = selections[key]
      if (table === undefined) continue
      const sectionLabel = SECTION_LABELS[key]
      lines.push(`- ${sectionLabel}: ${table || "none"}`)
    }
    lines.push("Please proceed to Phase 2 and verify columns for these selections.")
    setConfirmed(true)
    onConfirm(lines.join("\n"))
    // Also autofill the settings form with these table selections directly,
    // so the wizard reflects the choice immediately instead of waiting for
    // the agent to (maybe) echo it back as an ```autofill block.
    if (onAutofill) {
      const partial: Partial<BvaConfig> = { sourceType: "multi_table" }
      if (selections.gl)     partial.actualsTable       = selections.gl
      if (selections.budget) partial.budgetTable        = selections.budget
      if (selections.cc)     partial.costCenterTable    = selections.cc
      if (selections.ledger) {
        partial.ledgerMappingTable = selections.ledger
        partial.categorySource     = "mapping_table"
      }
      onAutofill(partial)
    }
  }

  // GL/actuals and budget are mandatory roles for BvA; cost center and
  // ledger mapping are optional ("None / skip" is a valid choice for those).
  // Only gate the confirm button on the required sections actually having a
  // selection — an optional section with no candidates shown is trivially satisfied.
  const canConfirm =
    !confirmed &&
    (data.gl?.length ? !!selections.gl : true) &&
    (data.budget?.length ? !!selections.budget : true)

  return (
    <div className="rounded-xl border border-border bg-muted/20 p-3 my-2 space-y-3">
      <p className="text-[11px] font-semibold text-foreground">
        {confirmed ? "Selected tables" : "Select tables for each role"}
      </p>

      <div className="space-y-3">
        {sections.map((key) => (
          <SectionPicker
            key={key}
            sectionKey={key}
            label={SECTION_LABELS[key]}
            candidates={data[key]!}
            selected={selections[key] ?? ""}
            onSelect={(table) => setSelections((prev) => ({ ...prev, [key]: table }))}
            confirmed={confirmed}
          />
        ))}
      </div>

      {!confirmed && (
        <Button
          size="sm"
          className="w-full text-xs h-8"
          disabled={!canConfirm}
          onClick={handleConfirm}
        >
          Use these tables →
        </Button>
      )}

      {confirmed && (
        <p className="text-[10px] text-muted-foreground text-center">
          Confirmed — proceeding to column verification.
        </p>
      )}
    </div>
  )
}
