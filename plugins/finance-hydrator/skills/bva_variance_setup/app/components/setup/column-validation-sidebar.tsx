"use client"

import { CheckCircle2, XCircle, AlertTriangle } from "lucide-react"

// This file powers the persistent "Column validation" sidebar shown next to
// the table-mapping steps of the wizard (Step 2 and friends). Its job is to
// answer, live as the customer maps columns: "which required roles (amount,
// date, cost center, ...) are still unmapped, which have more than one
// column competing for the same role, and which cross-table join keys line
// up?" It never touches the database — everything here works off column
// NAMES already loaded by the wizard step, either via a naive regex guess
// (ROLE_PATTERNS, used only to suggest candidates) or via the customer's own
// explicit role assignment.

// ── Role patterns for client-side candidate suggestions ───────────────────────

const ROLE_PATTERNS: Record<string, RegExp> = {
  amount:          /AMOUNT|AMT|EXPENSE|SPEND|COST|VALUE|BALANCE|DEBIT|CREDIT|NET/,
  period:          /DATE|PERIOD|MONTH|TIMESTAMP/,
  fiscal_year:     /FISCAL_?YEAR|^FY[_0-9]/,
  fiscal_quarter:  /FISCAL_?QUARTER|FISCAL_?QTR|^FQ[_0-9]/,
  account:         /ACCOUNT|ACCT|GL_CODE|LEDGER_CODE|^GL$/,
  cost_center:     /COST_?CENTER|CC_ID|CC_CODE|COST_CTR/,
  category:        /CATEGOR|SPEND_CAT|EXPENSE_TYPE|EXPENSE_CLASS|ACCOUNT_TYPE|LEDGER_ACCOUNT|ADAPTIVE/,
  version:         /VERSION|PLAN_NAME|FORECAST_NAME|SCENARIO/,
  department:      /DEPARTMENT|DEPT|^ORG$|DIVISION/,
  cc_name:         /COST_?CENTER_?NAME|CC_?NAME/,
  ledger_key:      /ACCOUNT|ACCT|GL_CODE|LEDGER_CODE|^GL$|^ACCOUNT_ID$|^ACCT_CODE$/,
  ledger_category: /CATEGOR|EXPENSE_TYPE|EXPENSE_CLASS|MAPPING|GROUPING/,
}

function candidatesForRole(role: string, allCols: string[], assignedRoles: Record<string, string>): string[] {
  const pat = ROLE_PATTERNS[role]
  if (!pat) return []
  const assigned = new Set(Object.values(assignedRoles).filter(Boolean))
  // Only suggest columns not already assigned to SOME role — a column can't
  // usefully fill two roles at once, and offering an already-used column as
  // a "candidate" would just create a new conflict instead of resolving one.
  return allCols
    .filter((c) => {
      const cur = assignedRoles[c] ?? ""
      return !cur && pat.test(c.toUpperCase())
    })
    .slice(0, 3)
}

// ── Validation rules ──────────────────────────────────────────────────────────

export type ValidationCheck = {
  role: string
  label: string
  count: number
  severity: "ok" | "error" | "warning"
  message: string
  candidates: string[]
  mappedCol?: string      // count===1: the single mapped column
  conflictCols?: string[] // count>1:  all columns mapped to this role
}

function countRole(roles: Record<string, string>, role: string): number {
  return Object.values(roles).filter((v) => v === role).length
}

/**
 * Core rule: a role (e.g. "amount", "cost_center") should be mapped to
 * EXACTLY ONE column.
 *   - 0 mapped  -> error (or "warning" via opts.zeroSeverity, for roles that
 *                  are genuinely optional, e.g. cost center on a table with
 *                  no cost-center dimension)
 *   - 1 mapped  -> ok — this is the only "happy path" outcome
 *   - 2+ mapped -> warning — ambiguous; the customer accidentally tagged more
 *                  than one column with the same role and must pick one
 * candidates/conflictCols feed the clickable chip UI below (CheckRow) that
 * lets the customer resolve either case without leaving the sidebar.
 */
function checkExact(
  role: string,
  label: string,
  roles: Record<string, string>,
  allCols: string[],
  opts: { zeroSeverity?: "error" | "warning"; manyMsg?: string } = {},
): ValidationCheck {
  const n = countRole(roles, role)
  const zero = opts.zeroSeverity ?? "error"
  if (n === 0) return {
    role, label, count: 0,
    severity: zero,
    message: zero === "error" ? `No ${label.toLowerCase()} column mapped` : `Not mapped — optional`,
    candidates: candidatesForRole(role, allCols, roles),
  }
  if (n > 1) return {
    role, label, count: n,
    severity: "warning",
    message: opts.manyMsg ?? `${n} columns mapped — pick the correct one`,
    candidates: [],
    conflictCols: Object.entries(roles).filter(([, r]) => r === role).map(([c]) => c),
  }
  return { role, label, count: 1, severity: "ok", message: "", candidates: [],
    mappedCol: Object.entries(roles).find(([, r]) => r === role)?.[0] }
}

// One validateX() per source table role — each simply lists which roles that
// table needs mapped and how strict each one is (error vs. optional warning).

export function validateActuals(
  roles: Record<string, string>,
  allCols: string[],
  opts: { categoryRequired: boolean },
): ValidationCheck[] {
  const checks: ValidationCheck[] = [
    checkExact("amount",  "Amount (spending)",  roles, allCols),
    checkExact("period",  "Date/period",        roles, allCols, { manyMsg: "Multiple date cols — pick the period column" }),
    checkExact("cost_center", "Cost center ID", roles, allCols, { manyMsg: "Multiple CC cols — pick the join key", zeroSeverity: "warning" }),
  ]
  if (opts.categoryRequired) {
    checks.push(checkExact("category", "Expense category", roles, allCols))
  }
  // account is optional, but must be tracked so the GL ↔ Ledger Mapping join card can read mappedCol
  checks.push(checkExact("account", "Ledger account", roles, allCols, { zeroSeverity: "warning" }))
  return checks
}

export function validateBudget(
  roles: Record<string, string>,
  allCols: string[],
): ValidationCheck[] {
  const checks: ValidationCheck[] = [
    checkExact("amount",  "Amount (spending)",  roles, allCols),
    checkExact("period",  "Date/period",        roles, allCols, { manyMsg: "Multiple date cols — pick the period column" }),
    checkExact("cost_center", "Cost center ID", roles, allCols, { manyMsg: "Multiple CC cols — pick the join key", zeroSeverity: "warning" }),
    checkExact("category", "Expense category",  roles, allCols),
    checkExact("version",  "Planning version",  roles, allCols, { zeroSeverity: "warning" }),
  ]
  return checks
}

export function validateLedger(
  roles: Record<string, string>,
  allCols: string[],
): ValidationCheck[] {
  return [
    checkExact("ledger_key",      "Account code (join key)", roles, allCols),
    checkExact("ledger_category", "Mapped category",         roles, allCols),
  ]
}

export function validateCC(
  roles: Record<string, string>,
  allCols: string[],
): ValidationCheck[] {
  return [
    checkExact("cost_center", "Cost center ID",   roles, allCols),
    checkExact("cc_name",     "Cost center name",  roles, allCols, { zeroSeverity: "warning" }),
    // "department" is the value set by the "Department/org" dropdown option
    checkExact("department",  "Department/org",    roles, allCols, { zeroSeverity: "warning",
      manyMsg: "Multiple dept columns — pick the one used for grouping" }),
  ]
}

// ── Role display labels (used in join cards) ────────────────────────────────

const ROLE_LABELS: Record<string, string> = {
  cost_center:     "Cost center ID",
  account:         "Ledger account",
  ledger_key:      "Account code",
  cc_name:         "Cost center name",
  department:      "Department/org",
  amount:          "Amount (spending)",
  period:          "Date/period",
  version:         "Planning version",
  category:        "Expense category",
}

// ── Cross-table join definitions ────────────────────────────────────────────
// These describe the actual joins buildFromConfig() (lib/config.ts) performs
// at SQL-build time — GL joins to Budget and to Cost Centers on cost_center,
// and to the Ledger Mapping table on account code. The sidebar uses this list
// purely to render a "join key" health card per pair: if either side of a
// join isn't mapped, that join will silently produce no/garbage matches once
// the SQL runs, so it's worth surfacing before the customer gets to Step 7.

type JoinDef = {
  label: string
  left:  { tableKey: string; tableLabel: string; role: string }
  right: { tableKey: string; tableLabel: string; role: string }
}

const JOIN_DEFINITIONS: JoinDef[] = [
  {
    label: "GL ↔ Budget",
    left:  { tableKey: "actuals", tableLabel: "GL",            role: "cost_center" },
    right: { tableKey: "budget",  tableLabel: "Budget",         role: "cost_center" },
  },
  {
    label: "GL ↔ Cost Centers",
    left:  { tableKey: "actuals", tableLabel: "GL",            role: "cost_center" },
    right: { tableKey: "cc",      tableLabel: "Cost Centers",  role: "cost_center" },
  },
  {
    label: "GL ↔ Ledger Mapping",
    left:  { tableKey: "actuals", tableLabel: "GL",             role: "account"     },
    right: { tableKey: "ledger",  tableLabel: "Ledger Mapping", role: "ledger_key"  },
  },
]

// ── Shared scroll+flash helper ────────────────────────────────────────────────
// These DOM-poking helpers are the sidebar's way of pointing at a column
// living in a big, possibly-collapsed/filtered table rendered elsewhere on
// the page (the actual mapping step). Rather than threading refs/callbacks
// through several component layers, they locate elements by the `table-${key}`
// / `col-${tableKey}-${col}` id convention those table components already
// expose, expand/un-filter them as needed, then scroll to and briefly
// highlight the target so the customer can see what the sidebar is pointing at.

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

async function scrollToCol(tableKey: string, col: string) {
  const tableEl = document.getElementById(`table-${tableKey}`)

  // Expand the table if it's collapsed
  if (tableEl?.getAttribute("data-collapsed") === "true") {
    ;(tableEl.querySelector("[data-expand-btn]") as HTMLButtonElement | null)?.click()
    await wait(60)
  }

  // Show all columns if the target row isn't in the DOM yet (hidden by filter)
  if (!document.getElementById(`col-${tableKey}-${col}`) && tableEl) {
    ;(tableEl.querySelector("[data-showall-btn]") as HTMLButtonElement | null)?.click()
    await wait(60)
  }

  const el = document.getElementById(`col-${tableKey}-${col}`)
  if (!el) return
  el.scrollIntoView({ behavior: "smooth", block: "center" })
  el.style.backgroundColor = "#fef9c3"
  el.style.transition = "background-color 1.2s ease"
  setTimeout(() => {
    el.style.backgroundColor = ""
    setTimeout(() => { el.style.transition = "" }, 1300)
  }, 800)
}

async function scrollToTable(tableKey: string) {
  const tableEl = document.getElementById(`table-${tableKey}`)
  if (!tableEl) return
  // Expand if collapsed so the content is visible after scrolling
  if (tableEl.getAttribute("data-collapsed") === "true") {
    ;(tableEl.querySelector("[data-expand-btn]") as HTMLButtonElement | null)?.click()
    await wait(60)
  }
  tableEl.scrollIntoView({ behavior: "smooth", block: "start" })
}

/**
 * Persistently highlight all conflicting rows; clears on next document click.
 * Used when a role has 2+ columns mapped to it (see checkExact's n > 1 case) —
 * this lights up every competing column at once so the customer can compare
 * them side by side and decide which one to keep, then clicks elsewhere to
 * dismiss the highlight.
 */
function highlightConflicts(tableKey: string, cols: string[]) {
  // clear any existing persistent highlights
  document.querySelectorAll("[data-conflict]").forEach((el) => {
    ;(el as HTMLElement).style.backgroundColor = ""
    el.removeAttribute("data-conflict")
  })
  // apply amber highlight to each conflict row
  cols.forEach((col) => {
    const el = document.getElementById(`col-${tableKey}-${col}`)
    if (!el) return
    el.style.backgroundColor = "#fef3c7"
    el.style.transition = ""
    el.setAttribute("data-conflict", "1")
  })
  // scroll the table into view
  document.getElementById(`table-${tableKey}`)?.scrollIntoView({ behavior: "smooth", block: "start" })
  // clear on next click anywhere (small delay so this click doesn't immediately clear)
  setTimeout(() => {
    const clear = () => {
      document.querySelectorAll("[data-conflict]").forEach((el) => {
        ;(el as HTMLElement).style.backgroundColor = ""
        el.removeAttribute("data-conflict")
      })
      document.removeEventListener("mousedown", clear)
    }
    document.addEventListener("mousedown", clear)
  }, 50)
}

// ── Sidebar component ─────────────────────────────────────────────────────────

type TableSection = {
  key: string
  label: string
  checks: ValidationCheck[]
}

// ── Join key pair display ─────────────────────────────────────────────────────

// Renders one side (one table) of a join-key pair card: the table name (click
// to scroll to it), which role it maps for this join, and the mapped column
// (click to scroll to and flash that specific column), or "not mapped".
function JoinSide({
  tableKey, tableLabel, role, col,
}: {
  tableKey: string
  tableLabel: string
  role: string
  col: string | undefined
}) {
  const roleLabel = ROLE_LABELS[role] ?? role
  return (
    <div className="space-y-0.5 min-w-0">
      <button
        type="button"
        onClick={() => scrollToTable(tableKey)}
        className="text-[10px] text-muted-foreground font-medium truncate hover:text-foreground transition-colors text-left w-full"
        title={`Scroll to ${tableLabel} table`}
      >
        {tableLabel}
      </button>
      <div className="text-[10px] text-muted-foreground/70 italic truncate">map: {roleLabel}</div>
      {col ? (
        <button
          type="button"
          onClick={() => scrollToCol(tableKey, col)}
          className="font-mono text-[10px] text-primary hover:underline text-left truncate max-w-full block"
          title={`Click to locate ${col} in the table`}
        >
          {col}
        </button>
      ) : (
        <span className="text-[10px] text-yellow-600 italic">not mapped</span>
      )}
    </div>
  )
}

function JoinKeySection({ tables }: { tables: TableSection[] }) {
  const presentKeys = new Set(tables.map((t) => t.key))
  // Only show a join card once BOTH tables it needs are actually present in
  // this wizard session (e.g. the "GL ↔ Ledger Mapping" join only applies
  // when a ledger mapping table was configured at all).
  const activeJoins = JOIN_DEFINITIONS.filter(
    (j) => presentKeys.has(j.left.tableKey) && presentKeys.has(j.right.tableKey),
  )
  if (activeJoins.length === 0) return null

  const getMappedCol = (tableKey: string, role: string): string | undefined =>
    tables.find((t) => t.key === tableKey)?.checks.find((c) => c.role === role)?.mappedCol

  return (
    <div className="space-y-2">
      <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Join keys</p>
      {activeJoins.map((join) => {
        const leftCol  = getMappedCol(join.left.tableKey,  join.left.role)
        const rightCol = getMappedCol(join.right.tableKey, join.right.role)
        const bothMapped = !!leftCol && !!rightCol
        return (
          <div key={join.label} className="border rounded-lg p-2 space-y-2">
            <div className="flex items-center gap-1">
              {bothMapped
                ? <CheckCircle2 className="h-3 w-3 text-green-500 shrink-0" />
                : <AlertTriangle className="h-3 w-3 text-yellow-500 shrink-0" />
              }
              <span className="text-[11px] font-medium">{join.label}</span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <JoinSide tableKey={join.left.tableKey}  tableLabel={join.left.tableLabel}  role={join.left.role}  col={leftCol}  />
              <JoinSide tableKey={join.right.tableKey} tableLabel={join.right.tableLabel} role={join.right.role} col={rightCol} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function ValidationSidebar({
  tables,
  onSetRole,
  onNext,
  nextLabel,
}: {
  tables: TableSection[]
  onSetRole: (tableKey: string, col: string, role: string) => void
  onNext: () => void
  nextLabel?: string
}) {
  const allErrors   = tables.flatMap((t) => t.checks.filter((c) => c.severity === "error"))
  const allWarnings = tables.flatMap((t) => t.checks.filter((c) => c.severity === "warning"))
  const errorCount   = allErrors.length
  const warningCount = allWarnings.length
  // Only hard errors block moving forward — warnings (ambiguous mappings,
  // optional-but-missing columns) are surfaced but don't gate progress.
  const canProceed   = errorCount === 0

  return (
    <div className="flex flex-col gap-4 bg-background border rounded-xl p-4 shadow-sm">
      <p className="text-sm font-semibold">Column validation</p>

      <JoinKeySection tables={tables} />

      {tables.map((section) => (
        <div key={section.key} className="space-y-1">
          <button
            type="button"
            onClick={() => scrollToTable(section.key)}
            className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition-colors text-left w-full"
            title="Click to scroll to this table"
          >
            {section.label}
          </button>
          {section.checks.map((check) => (
            <CheckRow
              key={check.role}
              check={check}
              tableKey={section.key}
              onCandidate={(col) => onSetRole(section.key, col, check.role)}
            />
          ))}
        </div>
      ))}

      {/* Summary + Next */}
      <div className="border-t pt-3 space-y-2">
        <p className="text-xs text-muted-foreground">
          {errorCount === 0 && warningCount === 0 && (
            <span className="text-green-600">All required roles mapped</span>
          )}
          {errorCount > 0 && (
            <span className="text-destructive">{errorCount} error{errorCount > 1 ? "s" : ""} to fix</span>
          )}
          {errorCount > 0 && warningCount > 0 && <span> · </span>}
          {warningCount > 0 && (
            <span className="text-yellow-600">{warningCount} warning{warningCount > 1 ? "s" : ""}</span>
          )}
        </p>
        <button
          onClick={onNext}
          disabled={!canProceed}
          className="w-full px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium
                     disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 transition-opacity"
        >
          {canProceed ? (nextLabel ?? "Next") : `Fix ${errorCount} error${errorCount > 1 ? "s" : ""} first`}
        </button>
      </div>
    </div>
  )
}

function CheckRow({
  check,
  tableKey,
  onCandidate,
}: {
  check: ValidationCheck
  tableKey: string
  onCandidate: (col: string) => void
}) {
  const Icon =
    check.severity === "ok"      ? CheckCircle2 :
    check.severity === "warning" ? AlertTriangle :
    XCircle

  const iconCls =
    check.severity === "ok"      ? "text-green-500" :
    check.severity === "warning" ? "text-yellow-500" :
    "text-destructive"

  // A row is clickable in one of two mutually-exclusive ways: "ok" rows let
  // you jump to the one column that's mapped; "warning" rows with 2+
  // conflicting columns let you highlight all of them at once for comparison.
  const isNavigable = check.severity === "ok" && !!check.mappedCol
  const isConflict  = check.severity === "warning" && (check.conflictCols?.length ?? 0) > 1
  const isClickable = isNavigable || isConflict

  const handleClick = () => {
    if (isNavigable && check.mappedCol) scrollToCol(tableKey, check.mappedCol)
    if (isConflict  && check.conflictCols) highlightConflicts(tableKey, check.conflictCols)
  }

  return (
    <div className="space-y-1">
      <div
        className={`flex items-start gap-1.5 ${
          isClickable ? "cursor-pointer rounded hover:bg-muted/40 -mx-1 px-1 py-0.5" : ""
        }`}
        onClick={isClickable ? handleClick : undefined}
        title={
          isNavigable ? `Click to locate ${check.mappedCol} in the table` :
          isConflict  ? `Click to highlight ${check.conflictCols?.length} conflicting columns` :
          undefined
        }
      >
        <Icon className={`h-3.5 w-3.5 mt-0.5 shrink-0 ${iconCls}`} />
        <div className="min-w-0">
          <span className={`text-xs ${check.severity === "error" ? "text-destructive" : check.severity === "warning" ? "text-yellow-700 dark:text-yellow-400" : "text-foreground"}`}>
            {check.label}
          </span>
          {check.message && check.severity !== "ok" && (
            <p className="text-[11px] text-muted-foreground leading-snug mt-0.5">{check.message}</p>
          )}
        </div>
      </div>
      {/* Candidate chips */}
      {check.candidates.length > 0 && (
        <div className="ml-5 flex flex-wrap gap-1">
          {check.candidates.map((col) => (
            <button
              key={col}
              onClick={() => onCandidate(col)}
              className="text-[10px] font-mono bg-primary/10 text-primary rounded px-1.5 py-0.5 hover:bg-primary/20 transition-colors"
              title={`Set ${col} as ${check.label}`}
            >
              {col}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
