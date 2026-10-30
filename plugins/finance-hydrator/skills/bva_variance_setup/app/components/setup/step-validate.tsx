"use client"

import { useEffect, useState } from "react"
import { CheckCircle2, XCircle, AlertTriangle, Loader2, ChevronDown, ChevronUp, Sparkles, Trash2 } from "lucide-react"
import { buildFromConfig, buildFinalSql, BVA_BASELINE_COLUMNS, type CustomLogicRule } from "@/lib/config"
import type { BvaConfig } from "@/lib/config"
import { validateCustomLogicSql, validateCustomLogicAlias } from "@/lib/custom-logic-guard"
import { useChatContext, type CustomLogicProposal } from "@/lib/chat-context"

type Nav = { next: () => void; back: () => void }
type Check = { name: string; pass: boolean; detail: string; warning?: boolean }

function newRuleId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `rule_${Date.now()}_${Math.random().toString(36).slice(2)}`
}

/**
 * Step 7 — Preview & validate. This step has two jobs:
 *
 * 1. Let the customer inspect and extend the query with "custom logic" —
 *    one-off business rules (e.g. "split expenses into allocated and
 *    unallocated") that don't fit the generic column-mapping wizard. Rules
 *    are added either by describing them in plain English to the embedded
 *    CoCo agent (which proposes CustomLogicRule objects for review, never
 *    writes them directly) or by a "power user" manual SQL-expression entry.
 *    See README.md §14 for the full design rationale.
 *
 * 2. Run the server-side validation checks (POST /api/setup/validate) that
 *    catch the most common "my numbers look wrong" causes — zero rows, null
 *    key columns, actuals/budget not overlapping, etc. — before the customer
 *    is allowed to save. See app/api/setup/validate/route.ts for what each
 *    check actually does.
 *
 * Two SQL previews are shown because there are two distinct queries in play:
 * "Baseline SQL" is buildFromConfig()'s pure output — exactly what earlier
 * wizard steps configured, unaffected by anything on this page. "Final SQL"
 * is buildFinalSql()'s output, i.e. the baseline wrapped with whatever custom
 * logic rules are currently enabled. The Final SQL panel only appears once
 * there IS a difference, so a customer who never touches custom logic never
 * sees a redundant second preview of the same query.
 */
export function StepValidate({
  config, update, nav,
}: {
  config: BvaConfig
  update: (p: Partial<BvaConfig>) => void
  nav: Nav
}) {
  const [running, setRunning] = useState(false)
  const [checks, setChecks] = useState<Check[] | null>(null)
  const [sample, setSample] = useState<Record<string, unknown>[]>([])
  const [error, setError] = useState("")
  const [showSql, setShowSql] = useState(false)
  const [showFinalSql, setShowFinalSql] = useState(false)
  const [showSample, setShowSample] = useState(false)

  const baselineSql = buildFromConfig(config)
  const rules = config.customLogicRules ?? []
  const finalSql = buildFinalSql(config)
  // The Final SQL panel (below) is only rendered when this is true — i.e.
  // once at least one enabled rule actually changes the query relative to
  // the plain baseline.
  const hasCustomLogic = finalSql !== baselineSql

  // ── Custom logic: freeform text -> agent-proposed rules (pending review) ──
  const { setPageContext, setIsOpen: setChatOpen, sendMessage, busy } = useChatContext()
  const [freeformText, setFreeformText] = useState("")
  const [pendingRules, setPendingRules] = useState<CustomLogicRule[]>([])

  // While this step is mounted, route the persistent chat panel to the
  // custom-logic agent instead of the setup agent. There's no cleanup here
  // to restore "setup" mode on unmount — this component doesn't have
  // access to the wizard's onConfigAccepted callback/existingConfig, so
  // components/settings-wizard.tsx handles the restoration itself whenever
  // the active step leaves 7 (see its applySetupPageContext effect).
  useEffect(() => {
    setPageContext({
      mode: "custom_logic",
      config,
      view: {},
      onCustomLogicProposed: (proposed: CustomLogicProposal[]) => {
        // Client-side re-validation of what the agent proposed — the same
        // guard used for manual entry below (validateCustomLogicSql /
        // validateCustomLogicAlias from lib/custom-logic-guard.ts). This is
        // a fast-feedback pass only; app/api/setup/config/route.ts still
        // re-validates server-side before anything is actually persisted,
        // since a rejected/never-displayed proposal here isn't a security
        // boundary on its own (see README §14).
        const known = [...BVA_BASELINE_COLUMNS, ...rules.filter((r) => r.enabled).map((r) => r.alias).filter(Boolean) as string[]]
        const vetted: CustomLogicRule[] = []
        for (const p of proposed) {
          const sqlCheck = validateCustomLogicSql(p.sql, p.kind, known)
          if (!sqlCheck.ok) continue // agent should retry — silently drop an invalid proposal rather than surface broken SQL
          if (p.kind === "derived_column") {
            if (!p.alias) continue
            const aliasCheck = validateCustomLogicAlias(p.alias, BVA_BASELINE_COLUMNS)
            if (!aliasCheck.ok) continue
          }
          vetted.push({ ...p, id: newRuleId(), enabled: true, source: "agent" })
        }
        // Vetted proposals land in "pending" state, awaiting an explicit
        // Accept click (see acceptPendingRule) — the agent can suggest but
        // never directly writes into config.customLogicRules.
        setPendingRules((prev) => [...prev, ...vetted])
      },
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config, rules])

  const askCoco = () => {
    const text = freeformText.trim()
    // sendMessage() silently no-ops while a prior turn is still in flight
    // (lib/chat-context.tsx) — guard here too so the textarea doesn't clear
    // as if the message were sent when it was actually dropped.
    if (!text || busy) return
    setChatOpen(true)
    sendMessage(text)
    setFreeformText("")
  }

  const acceptPendingRule = (rule: CustomLogicRule) => {
    update({ customLogicRules: [...rules, rule] })
    setPendingRules((prev) => prev.filter((r) => r.id !== rule.id))
  }
  const discardPendingRule = (id: string) => {
    setPendingRules((prev) => prev.filter((r) => r.id !== id))
  }
  // Toggling never deletes the rule — it just flips `enabled`, so
  // buildFinalSql() stops applying it. Re-enabling restores it exactly.
  const toggleRule = (id: string) => {
    update({ customLogicRules: rules.map((r) => (r.id === id ? { ...r, enabled: !r.enabled } : r)) })
  }
  const deleteRule = (id: string) => {
    update({ customLogicRules: rules.filter((r) => r.id !== id) })
  }

  // Manual "power user" rule entry
  const [manualOpen, setManualOpen] = useState(false)
  const [manualName, setManualName] = useState("")
  const [manualKind, setManualKind] = useState<CustomLogicRule["kind"]>("filter")
  const [manualSql, setManualSql] = useState("")
  const [manualAlias, setManualAlias] = useState("")
  const [manualError, setManualError] = useState("")

  const addManualRule = () => {
    // Same guard the agent-proposed path uses above — every enabled rule's
    // alias is added to "known" so a later rule (filter or column) is
    // allowed to safely reference an earlier one's derived column.
    const known = [...BVA_BASELINE_COLUMNS, ...rules.filter((r) => r.enabled).map((r) => r.alias).filter(Boolean) as string[]]
    const sqlCheck = validateCustomLogicSql(manualSql, manualKind, known)
    if (!sqlCheck.ok) { setManualError(sqlCheck.reason); return }
    if (manualKind === "derived_column") {
      const aliasCheck = validateCustomLogicAlias(manualAlias, BVA_BASELINE_COLUMNS)
      if (!aliasCheck.ok) { setManualError(aliasCheck.reason); return }
    }
    const rule: CustomLogicRule = {
      id: newRuleId(),
      name: manualName.trim() || "Custom rule",
      kind: manualKind,
      sql: manualSql.trim(),
      alias: manualKind === "derived_column" ? manualAlias.trim() : undefined,
      enabled: true,
      source: "manual",
    }
    update({ customLogicRules: [...rules, rule] })
    setManualName(""); setManualSql(""); setManualAlias(""); setManualError(""); setManualOpen(false)
  }

  // Runs the current config (baseline + any enabled custom logic) through
  // the server-side checks in app/api/setup/validate/route.ts and stores the
  // pass/fail results plus a 5-row sample for display below.
  const runValidation = async () => {
    setRunning(true)
    setChecks(null)
    setError("")
    try {
      const res = await fetch("/api/setup/validate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(config),
      })
      const data = await res.json()
      if (data.error) throw new Error(data.error)
      setChecks(data.checks)
      setSample(data.sample ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : "Validation failed")
    } finally {
      setRunning(false)
    }
  }

  // "Next: Save" is disabled until validation has been run AND every check
  // passed (warnings are fine — see checks' `warning` flag rendered below).
  const allPass = checks?.every((c) => c.pass) ?? false
  const sampleKeys = sample.length > 0 ? Object.keys(sample[0]) : []

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h2 className="text-lg font-semibold">Step 7 — Preview &amp; validate</h2>
        <p className="text-muted-foreground text-sm mt-1">
          Run validation checks against your assembled query. All checks must pass before saving.
        </p>
      </div>

      {/* Baseline SQL preview */}
      <div className="border rounded-lg overflow-hidden">
        <button
          type="button"
          onClick={() => setShowSql((s) => !s)}
          className="w-full flex items-center justify-between px-4 py-3 bg-muted/30 hover:bg-muted/50 text-sm font-medium transition-colors"
        >
          Baseline SQL
          {showSql ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </button>
        {showSql && (
          <pre className="p-4 text-xs font-mono overflow-x-auto max-h-64 bg-background text-foreground border-t">
            {baselineSql}
          </pre>
        )}
      </div>

      {/* Final SQL preview — only shown once custom logic is enabled */}
      {hasCustomLogic && (
        <div className="border rounded-lg overflow-hidden border-primary/30">
          <button
            type="button"
            onClick={() => setShowFinalSql((s) => !s)}
            className="w-full flex items-center justify-between px-4 py-3 bg-primary/5 hover:bg-primary/10 text-sm font-medium transition-colors"
          >
            Final SQL (with custom logic)
            {showFinalSql ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
          {showFinalSql && (
            <pre className="p-4 text-xs font-mono overflow-x-auto max-h-64 bg-background text-foreground border-t">
              {finalSql}
            </pre>
          )}
        </div>
      )}

      {/* Custom logic panel */}
      <div className="border rounded-lg p-4 space-y-4">
        <div>
          <h3 className="text-sm font-semibold">Custom logic</h3>
          <p className="text-xs text-muted-foreground mt-1">
            Add rules on top of your baseline query — filters, or new columns like an allocation status. Each rule can be
            toggled off or deleted independently; disabling every rule always reverts to the exact baseline query above.
          </p>
        </div>

        {/* Existing rules */}
        {rules.length > 0 && (
          <div className="space-y-2">
            {rules.map((r) => (
              <div key={r.id} className="flex items-start justify-between gap-3 rounded-lg border p-3 text-sm">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{r.name}</span>
                    <span className="text-xs rounded-full border px-2 py-0.5 text-muted-foreground">
                      {r.kind === "derived_column" ? "column" : "filter"}
                    </span>
                    <span className="text-xs rounded-full border px-2 py-0.5 text-muted-foreground">
                      {r.source === "agent" ? "CoCo" : "manual"}
                    </span>
                  </div>
                  <pre className="text-xs font-mono text-muted-foreground mt-1 whitespace-pre-wrap break-all">
                    {r.sql}{r.alias ? ` as ${r.alias}` : ""}
                  </pre>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={() => toggleRule(r.id)}
                    className={`text-xs px-2 py-1 rounded border ${r.enabled ? "border-primary text-primary" : "text-muted-foreground"}`}
                  >
                    {r.enabled ? "Enabled" : "Disabled"}
                  </button>
                  <button type="button" onClick={() => deleteRule(r.id)} className="text-muted-foreground hover:text-destructive">
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Pending rules proposed by the custom-logic agent, awaiting review */}
        {pendingRules.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-primary">Proposed by CoCo — review before adding:</p>
            {pendingRules.map((r) => (
              <div key={r.id} className="rounded-lg border border-primary/40 bg-primary/5 p-3 text-sm space-y-2">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{r.name}</span>
                  <span className="text-xs rounded-full border px-2 py-0.5 text-muted-foreground">
                    {r.kind === "derived_column" ? "column" : "filter"}
                  </span>
                </div>
                <pre className="text-xs font-mono text-muted-foreground whitespace-pre-wrap break-all">
                  {r.sql}{r.alias ? ` as ${r.alias}` : ""}
                </pre>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => acceptPendingRule(r)}
                    className="text-xs px-3 py-1.5 rounded-lg bg-primary text-primary-foreground"
                  >
                    Accept
                  </button>
                  <button
                    type="button"
                    onClick={() => discardPendingRule(r.id)}
                    className="text-xs px-3 py-1.5 rounded-lg border"
                  >
                    Discard
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Freeform natural-language entry -> custom-logic agent */}
        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground">
            Describe additional logic you&apos;d like to add (optional)
          </label>
          <textarea
            value={freeformText}
            onChange={(e) => setFreeformText(e.target.value)}
            placeholder="e.g. Split expenses into allocated and unallocated based on cost center type"
            className="w-full rounded-lg border px-3 py-2 text-sm bg-background min-h-[70px]"
          />
          <button
            type="button"
            onClick={askCoco}
            disabled={!freeformText.trim() || busy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-sm hover:bg-muted transition-colors disabled:opacity-50"
          >
            <Sparkles className="h-3.5 w-3.5" />
            Ask CoCo
          </button>
        </div>

        {/* Manual power-user entry */}
        <div className="border-t pt-3">
          <button
            type="button"
            onClick={() => setManualOpen((o) => !o)}
            className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
          >
            {manualOpen ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            Add a rule manually (advanced)
          </button>
          {manualOpen && (
            <div className="mt-3 space-y-2">
              <input
                value={manualName}
                onChange={(e) => setManualName(e.target.value)}
                placeholder="Rule name"
                className="w-full rounded-lg border px-3 py-2 text-sm bg-background"
              />
              <div className="flex gap-2">
                <select
                  value={manualKind}
                  onChange={(e) => setManualKind(e.target.value as CustomLogicRule["kind"])}
                  className="rounded-lg border px-3 py-2 text-sm bg-background"
                >
                  <option value="filter">Filter (row-level)</option>
                  <option value="derived_column">Derived column</option>
                </select>
                {manualKind === "derived_column" && (
                  <input
                    value={manualAlias}
                    onChange={(e) => setManualAlias(e.target.value)}
                    placeholder="Column alias, e.g. allocation_status"
                    className="flex-1 rounded-lg border px-3 py-2 text-sm bg-background"
                  />
                )}
              </div>
              <textarea
                value={manualSql}
                onChange={(e) => setManualSql(e.target.value)}
                placeholder={
                  manualKind === "filter"
                    ? "e.g. cost_center_id <> '999'"
                    : "e.g. case when cost_center_id like 'UNALLOC%' then 'Unallocated' else 'Allocated' end"
                }
                className="w-full rounded-lg border px-3 py-2 text-sm bg-background font-mono min-h-[60px]"
              />
              {manualError && <p className="text-xs text-destructive">{manualError}</p>}
              <button
                type="button"
                onClick={addManualRule}
                className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-medium"
              >
                Add rule
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Run button */}
      <button
        onClick={runValidation}
        disabled={running}
        className="flex items-center gap-2 px-5 py-2 rounded-lg border-2 border-primary text-primary text-sm font-medium
                   hover:bg-primary hover:text-primary-foreground transition-colors disabled:opacity-60"
      >
        {running && <Loader2 className="h-4 w-4 animate-spin" />}
        {running ? "Running checks…" : "Run validation checks"}
      </button>

      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Checks */}
      {checks && (
        <div className="space-y-2">
          {checks.map((c) => (
            <div
              key={c.name}
              className={`flex items-start gap-3 rounded-lg border p-3 text-sm ${
                c.pass
                  ? c.warning
                    ? "border-yellow-200 dark:border-yellow-800 bg-yellow-50 dark:bg-yellow-950/20"
                    : "border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/20"
                  : "border-destructive/40 bg-destructive/5"
              }`}
            >
              <div className="mt-0.5 shrink-0">
                {c.pass && !c.warning && <CheckCircle2 className="h-4 w-4 text-green-600" />}
                {c.pass && c.warning && <AlertTriangle className="h-4 w-4 text-yellow-600" />}
                {!c.pass && <XCircle className="h-4 w-4 text-destructive" />}
              </div>
              <div>
                <p className="font-medium">{c.name}</p>
                <p className="text-xs text-muted-foreground mt-0.5">{c.detail}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Sample output */}
      {sample.length > 0 && (
        <div className="border rounded-lg overflow-hidden">
          <button
            type="button"
            onClick={() => setShowSample((s) => !s)}
            className="w-full flex items-center justify-between px-4 py-3 bg-muted/30 hover:bg-muted/50 text-sm font-medium transition-colors"
          >
            Sample output ({sample.length} rows)
            {showSample ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
          {showSample && (
            <div className="overflow-x-auto border-t">
              <table className="w-full text-xs">
                <thead className="bg-muted/20">
                  <tr>
                    {sampleKeys.map((k) => (
                      <th key={k} className="text-left px-3 py-2 font-mono font-medium whitespace-nowrap">{k}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sample.map((row, i) => (
                    <tr key={i} className="border-t">
                      {sampleKeys.map((k) => (
                        <td key={k} className="px-3 py-2 font-mono whitespace-nowrap">
                          {row[k] == null ? <span className="text-muted-foreground italic">null</span> : String(row[k])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <div className="flex gap-3 pt-4 border-t">
        <button onClick={nav.back} className="px-4 py-2 rounded-lg border text-sm hover:bg-muted transition-colors">Back</button>
        <button
          onClick={nav.next}
          disabled={!allPass}
          className="px-5 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium
                     disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 transition-opacity"
        >
          {!checks ? "Run checks first" : allPass ? "Next: Save" : "Fix issues before saving"}
        </button>
      </div>
    </div>
  )
}
