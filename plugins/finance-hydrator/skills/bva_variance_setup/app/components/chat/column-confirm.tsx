"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import type { BvaConfig } from "@/lib/config"

// ─── Types ────────────────────────────────────────────────────────────────────
//
// Second step of setup, after table selection (candidate-picker.tsx): the
// agent has inspected the chosen tables' columns and proposes a mapping from
// each BvA role (date, amount, cost center, ...) to an actual column name.
// The agent emits a ```column-confirm fenced block whose JSON body matches
// ColumnConfirmPayload (see cortex-chat.tsx's MdMessage `code` renderer,
// which special-cases this fence language and mounts this component).

export interface ColumnConfirmPayload {
  gl?: {
    table?: string
    date?: string
    amount?: string
    cost_center?: string
    account?: string
    fiscal_year?: string
    fiscal_quarter?: string
  }
  budget?: {
    table?: string
    period?: string
    amount?: string
    cost_center?: string
    category?: string
    version?: string
  }
  cc?: {
    table?: string
    code?: string
    name?: string
    department?: string
    /** @deprecated legacy alias — treated as `department` */
    rollup?: string
  }
  ledger?: {
    table?: string
    account?: string
    category?: string
  }
}

interface Props {
  data: ColumnConfirmPayload
  onConfirm: (message: string) => void
  // Optional: also writes the mapped columns straight into the Settings
  // wizard's form state once confirmed, so the wizard reflects them
  // immediately rather than depending on the agent to echo an autofill block.
  onAutofill?: (partial: Partial<BvaConfig>) => void
}

// ─── Row definitions ──────────────────────────────────────────────────────────
//
// Flat list of every (label, section, field) triple that can appear in the
// confirmation table below. Flattening here — rather than nesting sections
// in JSX — makes it trivial to skip any role the payload didn't map (see
// `rows` in the component) and keeps the render logic to a single .map().

type RowDef = { label: string; section: keyof ColumnConfirmPayload; field: string }

const ROW_DEFS: RowDef[] = [
  { label: "GL date",            section: "gl",     field: "date" },
  { label: "GL amount",          section: "gl",     field: "amount" },
  { label: "GL cost center",     section: "gl",     field: "cost_center" },
  { label: "GL account",         section: "gl",     field: "account" },
  { label: "GL fiscal year",     section: "gl",     field: "fiscal_year" },
  { label: "GL fiscal quarter",  section: "gl",     field: "fiscal_quarter" },
  { label: "Budget period",      section: "budget", field: "period" },
  { label: "Budget amount",      section: "budget", field: "amount" },
  { label: "Budget cost center", section: "budget", field: "cost_center" },
  { label: "Budget category",    section: "budget", field: "category" },
  { label: "Budget version",     section: "budget", field: "version" },
  { label: "CC mapping code",     section: "cc",     field: "code" },
  { label: "Cost center name",     section: "cc",     field: "name" },
  { label: "Cost center department", section: "cc",   field: "department" },
  { label: "Ledger account",       section: "ledger", field: "account" },
  { label: "Ledger category",      section: "ledger", field: "category" },
]

// ─── Component ────────────────────────────────────────────────────────────────

export function ColumnConfirm({ data, onConfirm, onAutofill }: Props) {
  const [confirmed, setConfirmed] = useState(false)

  // Flatten ROW_DEFS into only the rows the agent actually mapped a column
  // for — a role with no mapping (e.g. no ledger table was used) is simply
  // omitted from the confirmation table rather than shown as blank.
  const rows = ROW_DEFS.flatMap(({ label, section, field }) => {
    const block = data[section] as Record<string, string | undefined> | undefined
    // `cc.department` accepts the legacy `rollup` alias.
    const col = field === "department" ? (block?.department ?? block?.rollup) : block?.[field]
    if (!col) return []
    return [{ label, col }]
  })

  // Nothing to confirm if the agent's payload didn't map any columns at all.
  if (rows.length === 0) return null

  const handleConfirm = () => {
    // As with the other widgets, the "confirm" action is just a plain chat
    // message summarizing what was approved — sent back through the normal
    // pipeline so the agent can read it and move on to the next setup phase.
    const lines = ["These column mappings look correct:"]
    for (const { label, col } of rows) lines.push(`- ${label}: ${col}`)
    lines.push("Please proceed to Phase 3.")
    setConfirmed(true)
    onConfirm(lines.join("\n"))
    // Also autofill the settings form with column mappings
    if (onAutofill) {
      const partial: Partial<BvaConfig> = {}
      // Table names (safety net in case CandidatePicker wasn't used)
      if (data.gl?.table)     partial.actualsTable       = data.gl.table
      if (data.budget?.table) partial.budgetTable        = data.budget.table
      if (data.cc?.table)     partial.costCenterTable    = data.cc.table
      if (data.ledger?.table) partial.ledgerMappingTable = data.ledger.table
      // Actuals column mapping
      if (data.gl?.date)         partial.actualsDateCol        = data.gl.date
      if (data.gl?.amount)       partial.actualsAmountCol      = data.gl.amount
      if (data.gl?.cost_center)  partial.actualsCostCenterCol  = data.gl.cost_center
      if (data.gl?.account)      partial.actualsAccountCol     = data.gl.account
      if (data.gl?.fiscal_year)  partial.fiscalYearColActuals  = data.gl.fiscal_year
      if (data.gl?.fiscal_quarter) partial.fiscalQuarterColActuals = data.gl.fiscal_quarter
      // Pre-computed fiscal columns present → fiscalPath "A" so StepFiscal reveals
      // the FY/quarter column inputs (they're hidden under the default path "B").
      if (data.gl?.fiscal_year || data.gl?.fiscal_quarter) partial.fiscalPath = "A"
      // Budget column mapping
      if (data.budget?.period)       partial.budgetPeriodCol      = data.budget.period
      if (data.budget?.amount)       partial.budgetAmountCol      = data.budget.amount
      if (data.budget?.cost_center)  partial.budgetCostCenterCol  = data.budget.cost_center
      if (data.budget?.category)     partial.budgetCategoryCol    = data.budget.category
      if (data.budget?.version)      partial.budgetVersionCol     = data.budget.version
      // Cost center column mapping
      if (data.cc?.code)   partial.ccIdCol   = data.cc.code
      if (data.cc?.name)   partial.ccNameCol = data.cc.name
      // `rollup` is a legacy alias for the department rollup column — never the CC name.
      const ccDept = data.cc?.department ?? data.cc?.rollup
      if (ccDept)          partial.ccDeptCol = ccDept
      // Ledger column mapping
      if (data.ledger?.account)  partial.ledgerAccountKeyCol = data.ledger.account
      if (data.ledger?.category) partial.ledgerCategoryCol   = data.ledger.category
      // If ledger columns are present, the category source must be mapping_table
      if (data.ledger?.account || data.ledger?.category || data.ledger?.table) {
        partial.categorySource = "mapping_table"
      }
      onAutofill(partial)
    }
  }

  return (
    <div className="rounded-xl border border-border bg-muted/20 p-3 my-2 space-y-2.5">
      <p className="text-[11px] font-semibold text-foreground">
        {confirmed ? "Column mappings confirmed" : "Recommended column mappings — confirm or ask to change any"}
      </p>

      <div className="overflow-x-auto">
        <table className="text-xs border-collapse w-full">
          <thead>
            <tr>
              <th className="border border-border px-2 py-1 bg-muted/60 font-medium text-left text-[11px]">Role</th>
              <th className="border border-border px-2 py-1 bg-muted/60 font-medium text-left text-[11px]">Column</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ label, col }) => (
              <tr key={label} className="even:bg-muted/10">
                <td className="border border-border px-2 py-1 text-[11px] text-muted-foreground whitespace-nowrap">{label}</td>
                <td className="border border-border px-2 py-1">
                  <code className="font-mono text-[11px] bg-muted/50 px-1.5 py-0.5 rounded break-all">{col}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!confirmed ? (
        <Button size="sm" className="w-full text-xs h-8" onClick={handleConfirm}>
          These look correct →
        </Button>
      ) : (
        <p className="text-[10px] text-muted-foreground text-center">Confirmed — proceeding to Phase 3.</p>
      )}
    </div>
  )
}
