"use client"

/**
 * Controls for whether the embedded Cortex Agent runs under caller's rights
 * (per-viewer scoping, the default) or the app's owner role. See
 * lib/agent-access-mode.ts for the security reasoning.
 *
 * No isAdmin gating here — this is meant to be rendered inside AdminPanel
 * (components/admin-panel.tsx), which already gates the whole panel on
 * isAdmin and owns the "hidden until you click the gear" behavior. The real
 * security boundary is server-side regardless (every write re-checks admin
 * status itself).
 */

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert"
import { getJson } from "@/lib/format"

type AgentAccessMode = "caller" | "owner"
type StatusResponse = { mode: AgentAccessMode; isAdmin: boolean }

// Confirmation copy shown before actually flipping the mode — this is a
// real security-relevant change (see lib/agent-access-mode.ts), so both
// directions get an explicit "are you sure" with the consequence spelled out.
const CONFIRM_TO_OWNER =
  "Switch the agent to the app's owner role for every viewer? " +
  "Everyone will see the same access as this app's role, not their own — this is a real reduction in per-viewer scoping."
const CONFIRM_TO_CALLER =
  "Switch the agent back to caller's rights? Each viewer will only see what they already have direct access to " +
  "(requires the owner role to hold GRANT CALLER privileges — see the deploy docs if queries start failing)."

/** Shape returned by GET /api/agent/debug-callers-rights (default, non-sweep run). */
interface CallersRightsCheckResponse {
  error?: string
  caseA_service_token?: { verdict?: string }
  caseB_combined_token?: { verdict?: string; toolResults?: string[] } | { skipped: string }
}

/** Plain-language read of the raw diagnostic — nobody clicking this button
 *  should need to parse a verdict string or JSON to know what to do next. */
type CheckSummary = { tone: "pass" | "fail" | "retry" | "error"; title: string; detail: string }

// Translates the raw debug-callers-rights API response into one of a small
// set of user-facing outcomes. This is the only place that has to understand
// the diagnostic's internal shape (caseA/caseB, verdict strings, error code
// 399525) — everything below just renders whatever summarize() returns.
function summarize(data: CallersRightsCheckResponse): CheckSummary {
  if (data.error) {
    return { tone: "error", title: "Couldn't run the check", detail: data.error }
  }
  const caseA = data.caseA_service_token?.verdict ?? ""
  const caseB = data.caseB_combined_token
  if (!caseB || "skipped" in caseB) {
    return {
      tone: "error",
      title: "Couldn't run the check",
      detail: "No caller identity was found on this request — try reloading the page and checking again.",
    }
  }
  const verdictB = caseB.verdict ?? ""
  if (!caseA.startsWith("PASS")) {
    return {
      tone: "error",
      title: "Base check failed",
      detail:
        "Even the app's own role couldn't run a query, so caller's rights can't be evaluated yet. " +
        "This points to a deployment issue (missing warehouse or table access for the owner role), not caller's rights specifically.",
    }
  }
  if (verdictB.startsWith("PASS")) {
    const identity = caseB.toolResults?.[0]
    return {
      tone: "pass",
      title: "Caller's rights is working",
      detail: identity
        ? `The agent ran a real query as the signed-in viewer. Result: ${identity}`
        : "The agent ran a real query as the signed-in viewer. Nothing more to do.",
    }
  }
  if (verdictB.includes("399525")) {
    return {
      tone: "retry",
      title: "Transient platform error — try again",
      detail:
        "Snowflake's agent sandbox was briefly unavailable (error 399525, a known transient capacity issue). " +
        "This isn't a caller's-rights problem — click the button again in a moment.",
    }
  }
  return {
    tone: "fail",
    title: "Caller's rights isn't working yet",
    detail:
      "Queries as the signed-in viewer aren't succeeding. Either set up caller grants for the app's owner role " +
      "(see the deploy docs), or switch to owner's rights below.",
  }
}

export function AgentAccessControls() {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ["agent-access-mode"],
    queryFn: () => getJson<StatusResponse>("/api/agent-access-mode"),
  })
  const [checking, setChecking] = useState(false)
  const [checkResult, setCheckResult] = useState<CheckSummary | null>(null)

  // Confirms with the admin, then POSTs the new mode. The server re-verifies
  // admin status itself (see checkIsAdmin in lib/agent-access-mode.ts) — this
  // client-side confirm dialog is just a "did you mean to do that" guard, not
  // the actual security boundary.
  const setMode = async (mode: AgentAccessMode) => {
    const confirmed = window.confirm(mode === "owner" ? CONFIRM_TO_OWNER : CONFIRM_TO_CALLER)
    if (!confirmed) return
    try {
      const res = await fetch("/api/agent-access-mode", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mode }),
      })
      if (res.ok) {
        queryClient.invalidateQueries({ queryKey: ["agent-access-mode"] })
      } else {
        const json = await res.json().catch(() => null) as { error?: string } | null
        alert(`Couldn't change the access mode: ${json?.error ?? `HTTP ${res.status}`}`)
      }
    } catch (e) {
      alert(`Couldn't change the access mode: ${e instanceof Error ? e.message : "network error"}`)
    }
  }

  // Runs the live diagnostic that actually exercises caller's rights (as
  // opposed to just reading the stored mode) — useful after switching modes
  // or granting privileges, to confirm the agent can really query as the
  // signed-in viewer rather than just trusting the setting.
  const runCheck = async () => {
    setChecking(true)
    setCheckResult(null)
    try {
      const res = await fetch("/api/agent/debug-callers-rights")
      const json = (await res.json()) as CallersRightsCheckResponse
      setCheckResult(res.ok ? summarize(json) : { tone: "error", title: "Couldn't run the check", detail: json.error ?? `HTTP ${res.status}` })
    } catch (e) {
      setCheckResult({ tone: "error", title: "Couldn't run the check", detail: String(e) })
    } finally {
      setChecking(false)
    }
  }

  const mode = data?.mode

  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">Agent access mode</span>
        {mode && (
          <Badge variant="secondary" className="font-normal">
            {mode === "caller" ? "Caller's rights" : "Owner's rights"}
          </Badge>
        )}
      </div>
      <p className="text-xs text-muted-foreground leading-relaxed">
        Whether the embedded agent runs queries as each signed-in viewer (caller&apos;s rights) or as this
        app&apos;s own role (owner&apos;s rights) for everyone.
      </p>
      {/* Caller's rights = each viewer only sees data they personally have access
          to in Snowflake (the secure default). Owner's rights = everyone sees
          whatever this app's own role can see, regardless of the viewer's own
          grants — simpler to get working, but a real widening of access. */}
      <div className="grid grid-cols-2 gap-2">
        <Button
          variant={mode === "caller" ? "default" : "outline"}
          size="sm"
          onClick={() => setMode("caller")}
          disabled={!mode || mode === "caller"}
        >
          Caller&apos;s rights
        </Button>
        <Button
          variant={mode === "owner" ? "default" : "outline"}
          size="sm"
          onClick={() => setMode("owner")}
          disabled={!mode || mode === "owner"}
        >
          Owner&apos;s rights
        </Button>
      </div>
      <Button variant="outline" size="sm" className="w-full" onClick={runCheck} disabled={checking}>
        {checking ? "Checking…" : "Check caller's rights"}
      </Button>
      {checkResult && (
        <Alert variant={checkResult.tone === "error" || checkResult.tone === "fail" ? "destructive" : "default"}>
          <AlertTitle>
            {checkResult.tone === "pass" ? "✅ " : checkResult.tone === "retry" ? "⏳ " : "⚠️ "}
            {checkResult.title}
          </AlertTitle>
          <AlertDescription>{checkResult.detail}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}
