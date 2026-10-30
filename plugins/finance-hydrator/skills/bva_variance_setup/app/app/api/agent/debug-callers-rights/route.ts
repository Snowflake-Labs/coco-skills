import { headers } from "next/headers"
import { getSpcsBearerToken } from "@/lib/spcs-auth"
import { restBaseUrl } from "@/lib/agent-availability"
import { checkIsAdmin } from "@/lib/agent-access-mode"
import { getConfigTable, getMaterializedTable } from "@/lib/config-server"

export const dynamic = "force-dynamic"

// ─── Caller's-rights diagnostic ─────────────────────────────────────────────
//
// Determines whether `code_toolset_all` works under caller's rights (the
// combined `<service>.<caller>` token), which is the one question that decides
// the app's agent architecture.
//
// An EARLIER version of this route was misleading: it read only the first 5KB
// of the SSE stream and then cancelled the reader, so it observed the agent
// *starting to plan* and reported that as a pass. It never confirmed a tool
// actually ran. Two cases were therefore recorded as working that did not.
//
// This version proves execution instead of inferring it:
//   - consumes the stream to completion (no byte cap, no early cancel)
//   - answers the `always_ask` permission request, because without a
//     permission_decision reply the turn legitimately stalls and a truncated
//     reader would misread that stall as a platform failure
//   - asserts on a real `response.tool_result`, and returns the SQL output so
//     the identity the query ran as is directly visible
//   - captures any `error` payload verbatim (e.g. 399525)
//
// Cases, both thread-scoped with code_toolset_all:
//   A  service token only     -> expect tool_result, CURRENT_USER = service
//   B  combined caller token  -> expect tool_result, CURRENT_USER = the viewer
//
// B returning the viewer's identity with a real tool_result is a decisive
// positive: keep the REST backend and caller's rights. B failing 399525 while
// A passes is a decisive negative: fall back to CLI-primary. If A fails, the
// harness is wrong rather than the platform — fix it before reading B.

// Snowflake requires this header on REST calls that authenticate with an
// OAuth-style bearer token (both the raw service token and the combined
// service+caller token used below are this token type).
const OAUTH_HEADER = { "X-Snowflake-Authorization-Token-Type": "OAUTH" }

/** The only tool this app ever approves, matching runTurnRest(). */
const APPROVED_TOOL_NAME = "snowflake_sql_execute"

/** Whole-run wall clock. Bounds a hang so it is reported, not inflicted on
 *  the caller. Generous enough to cover sandbox cold start. */
const DEADLINE_MS = 120_000

/** Identical in shape to what buildToolsConfig() sends, so a pass here is
 *  evidence about the real code path and not about a simplified variant. */
function toolsConfig(systemPrompt: string): Record<string, unknown> {
  return {
    tools: [{ tool_spec: { type: "code_toolset_all", name: "code_toolset_all" } }],
    tool_resources: {
      code_toolset_all: {
        permission_policy: { type: "always_ask" },
        disabled_skills: ["dashboard", "streamlit"],
      },
    },
    instructions: { system: systemPrompt },
  }
}

const BASE_SYSTEM_PROMPT =
  "You are a diagnostic probe. Run exactly one SQL query using the " +
  "snowflake_sql_execute tool, then state the result verbatim. Do not " +
  "explain or elaborate."

/**
 * Pads the system prompt to approximately `chars` so prompt size can be
 * isolated as a variable. The real chat route sends ~21,400 chars while the
 * original probe sent ~150, and the real route fails 399525 in ~1.2s where the
 * probe passes — so size is the leading suspect and needs to be measured, not
 * assumed. Padding is realistic filler rather than a repeated single character,
 * to avoid a degenerate input that the service might treat differently.
 */
function paddedPrompt(chars: number): string {
  if (chars <= BASE_SYSTEM_PROMPT.length) return BASE_SYSTEM_PROMPT
  const filler =
    "Context: the BvA dataset tracks budget versus actuals by cost center, " +
    "expense category, fiscal year, and planning version. Variance is actuals " +
    "minus budget. Prefer fully qualified table names. "
  let out = BASE_SYSTEM_PROMPT + "\n\n"
  while (out.length < chars) out += filler
  return out.slice(0, chars)
}

// A single statement that proves identity AND real data access at once.
// `SELECT CURRENT_USER()` alone is NOT sufficient evidence: it needs no
// warehouse and no object privileges, so it passes even when the caller has
// no access to anything. Reading a real table additionally requires a
// warehouse, USAGE on the database and schema, and SELECT on the table —
// which under restricted caller's rights must be held by the caller AND
// permitted by a caller grant on the owner role.
//
// The table MUST be this deployment's own resolved materialized table
// (getMaterializedTable(), same as the dashboard uses) — never a hardcoded
// absolute path. An earlier version of this probe hardcoded an absolute
// table path, which (a) is environment-specific and (b) would just silently
// fail to resolve on any other deployment.
function probeMessage(table: string): string {
  return (
    "Run this exact SQL and report the result verbatim: " +
    "SELECT CURRENT_USER() AS who, CURRENT_ROLE() AS role, COUNT(*) AS row_count " +
    `FROM ${table}`
  )
}

interface StreamOutcome {
  events: string[]
  sawToolUse: boolean
  sawToolResult: boolean
  toolNames: string[]
  /** Text of every tool_result — where CURRENT_USER()/CURRENT_ROLE() surface. */
  toolResults: string[]
  /** Assistant text, which normally restates the SQL result. */
  assistantText: string
  errors: string[]
  /** Set when the run paused awaiting approval, so the caller can answer it. */
  pendingPermission?: { toolUseId: string; toolName: string }
  /** Required as parent_message_id when replying with a permission_decision. */
  assistantMessageId?: number
  timedOut: boolean
}

/** A blank StreamOutcome to accumulate into as consume() reads SSE events. */
function emptyOutcome(): StreamOutcome {
  return {
    events: [],
    sawToolUse: false,
    sawToolResult: false,
    toolNames: [],
    toolResults: [],
    assistantText: "",
    errors: [],
    timedOut: false,
  }
}

/** Flattens tool_result content, which arrives as text and/or json parts. */
function contentToText(content: unknown): string {
  if (typeof content === "string") return content
  if (!Array.isArray(content)) return content === undefined ? "" : JSON.stringify(content)
  return content
    .map((c) => {
      const part = c as { text?: string; json?: unknown }
      if (typeof part.text === "string") return part.text
      if (part.json !== undefined) return JSON.stringify(part.json)
      return ""
    })
    .filter(Boolean)
    .join("\n")
}

/**
 * Parses one `event:`/`data:` block from the SSE stream and folds it into
 * `out`. SSE frames are separated by a blank line; consume() below splits
 * the raw byte stream on that boundary and hands each chunk here one at a
 * time. Unrecognized event types are ignored — this only reacts to the
 * handful of event names the diagnostic actually needs (text, tool_use,
 * tool_result, error, and the final response/status event).
 */
function processBlock(raw: string, out: StreamOutcome): void {
  let eventName = "message"
  const dataLines: string[] = []
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice("event:".length).trim()
    else if (line.startsWith("data:")) dataLines.push(line.slice("data:".length).trim())
  }
  if (dataLines.length === 0) return

  let data: Record<string, unknown>
  try {
    data = JSON.parse(dataLines.join("\n"))
  } catch {
    return
  }

  out.events.push(eventName)

  switch (eventName) {
    case "response.text.delta": {
      if (typeof data.text === "string") out.assistantText += data.text
      break
    }
    case "response.tool_use": {
      out.sawToolUse = true
      const name = typeof data.name === "string" ? data.name : "unknown"
      out.toolNames.push(name)
      const permission = data.permission as { options?: string[] } | undefined
      const toolUseId = data.tool_use_id
      if (permission?.options?.length && typeof toolUseId === "string") {
        out.pendingPermission = { toolUseId, toolName: name }
      }
      break
    }
    case "response.tool_result": {
      // The assertion that matters: a tool genuinely executed and returned.
      out.sawToolResult = true
      const text = contentToText(data.content)
      if (text) out.toolResults.push(text.slice(0, 600))
      break
    }
    case "error": {
      out.errors.push(JSON.stringify(data).slice(0, 600))
      break
    }
    case "response": {
      const meta = data.metadata as { assistant_message_id?: number } | undefined
      if (meta?.assistant_message_id !== undefined) out.assistantMessageId = meta.assistant_message_id
      if (data.status === "cancelled" || data.status === "timed_out") {
        out.errors.push(`run status: ${String(data.status)}`)
      }
      break
    }
    default:
      break
  }
}

/** Reads one agent:run SSE response to completion, or until the deadline. */
async function consume(res: Response, deadline: number): Promise<StreamOutcome> {
  const out = emptyOutcome()
  if (!res.body) {
    out.errors.push("no response body")
    return out
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  try {
    while (true) {
      if (Date.now() > deadline) {
        out.timedOut = true
        void reader.cancel().catch(() => {})
        break
      }
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sep: number
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        processBlock(buffer.slice(0, sep), out)
        buffer = buffer.slice(sep + 2)
      }
    }
  } catch (e) {
    out.errors.push(`stream read failed: ${String(e).slice(0, 300)}`)
  }
  return out
}

/** Opens a fresh Cortex Agents conversation thread to run the probe in. */
async function createThread(base: string, token: string): Promise<number> {
  const res = await fetch(`${base}/api/v2/cortex/threads`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${token}`, ...OAUTH_HEADER },
    body: JSON.stringify({ origin_application: "bva_debug" }),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => "")
    throw new Error(`createThread ${res.status}: ${detail.slice(0, 400)}`)
  }
  return ((await res.json()) as { thread_id: number }).thread_id
}

/**
 * POSTs one turn to the Cortex Agents `agent:run` endpoint (either the
 * initial probe message or a later permission_decision reply) and returns
 * either the raw streaming Response for consume() to read, or an error
 * summary if the call itself failed before any streaming started.
 */
async function agentRun(
  base: string,
  token: string,
  body: Record<string, unknown>,
): Promise<{ ok: boolean; status: number; detail?: string; res?: Response }> {
  const res = await fetch(`${base}/api/v2/cortex/agent:run`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
      authorization: `Bearer ${token}`,
      ...OAUTH_HEADER,
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => "")
    return { ok: false, status: res.status, detail: detail.slice(0, 600) }
  }
  return { ok: true, status: res.status, res }
}

// Everything captured about running one test case (e.g. "service token" or
// "combined caller token") end to end, surfaced verbatim in the JSON response
// so a human can inspect exactly what happened without re-running anything.
interface CaseResult {
  label: string
  systemPromptChars?: number
  httpStatus?: number
  httpError?: string
  threadId?: number
  sawToolUse?: boolean
  sawToolResult?: boolean
  toolNames?: string[]
  toolResults?: string[]
  assistantText?: string
  events?: string[]
  errors?: string[]
  permissionRounds?: number
  timedOut?: boolean
  elapsedMs?: number
  verdict?: string
  error?: string
}

/**
 * Runs one full turn: open a thread, send the probe, then answer each
 * permission request until the model stops asking. Mirrors runTurnRest()'s
 * handshake so the result reflects production behavior.
 */
async function runCase(
  label: string,
  base: string,
  token: string,
  table: string,
  systemPrompt: string = BASE_SYSTEM_PROMPT,
): Promise<CaseResult> {
  const started = Date.now()
  const deadline = started + DEADLINE_MS
  const result: CaseResult = { label }
  result.systemPromptChars = systemPrompt.length

  try {
    const threadId = await createThread(base, token)
    result.threadId = threadId
    const config = toolsConfig(systemPrompt)

    const first = await agentRun(base, token, {
      thread_id: threadId,
      parent_message_id: 0,
      messages: [{ role: "user", content: [{ type: "text", text: probeMessage(table) }] }],
      stream: true,
      ...config,
    })
    result.httpStatus = first.status
    if (!first.ok || !first.res) {
      result.httpError = first.detail
      result.verdict = `FAIL — agent:run returned HTTP ${first.status}`
      result.elapsedMs = Date.now() - started
      return result
    }

    let outcome = await consume(first.res, deadline)
    const events = [...outcome.events]
    const toolNames = [...outcome.toolNames]
    const toolResults = [...outcome.toolResults]
    const errors = [...outcome.errors]
    let assistantText = outcome.assistantText
    let sawToolUse = outcome.sawToolUse
    let sawToolResult = outcome.sawToolResult
    let rounds = 0

    // Answer permission requests. Without this the turn stalls by design and
    // the stall would be misattributed to the platform.
    while (outcome.pendingPermission && rounds < 10 && Date.now() < deadline) {
      rounds++
      const { toolUseId, toolName } = outcome.pendingPermission
      if (outcome.assistantMessageId === undefined) {
        errors.push(`cannot resolve permission for ${toolName}: no assistant_message_id`)
        break
      }
      const approve = toolName === APPROVED_TOOL_NAME
      const next = await agentRun(base, token, {
        thread_id: threadId,
        parent_message_id: outcome.assistantMessageId,
        stream: true,
        messages: [
          {
            role: "user",
            content: [
              {
                type: "permission_decision",
                permission_decision: {
                  tool_use_id: toolUseId,
                  decision: approve ? "Allow Once" : "Deny",
                  ...(approve ? {} : { reason: "read-only diagnostic" }),
                },
              },
            ],
          },
        ],
        ...config,
      })
      if (!next.ok || !next.res) {
        errors.push(`permission_decision HTTP ${next.status}: ${next.detail ?? ""}`)
        break
      }
      outcome = await consume(next.res, deadline)
      events.push(...outcome.events)
      toolNames.push(...outcome.toolNames)
      toolResults.push(...outcome.toolResults)
      errors.push(...outcome.errors)
      assistantText += outcome.assistantText
      sawToolUse = sawToolUse || outcome.sawToolUse
      sawToolResult = sawToolResult || outcome.sawToolResult
    }

    result.sawToolUse = sawToolUse
    result.sawToolResult = sawToolResult
    result.toolNames = toolNames
    result.toolResults = toolResults
    result.assistantText = assistantText.slice(0, 600)
    // Event names collapse to unique-in-order; the raw sequence is long and
    // repetitive, and only which kinds appeared is diagnostic.
    result.events = [...new Set(events)]
    result.errors = errors
    result.permissionRounds = rounds
    result.timedOut = outcome.timedOut
    result.elapsedMs = Date.now() - started

    if (errors.some((e) => e.includes("399525"))) {
      result.verdict = "FAIL — 399525 internal error"
    } else if (sawToolResult) {
      result.verdict = "PASS — tool executed; read toolResults for the identity"
    } else if (outcome.timedOut) {
      result.verdict = `FAIL — no tool_result within ${DEADLINE_MS / 1000}s (hang)`
    } else if (sawToolUse) {
      result.verdict = "FAIL — tool_use seen but never produced a tool_result"
    } else {
      result.verdict = "FAIL — agent never attempted a tool call"
    }
    return result
  } catch (e) {
    result.error = String(e).slice(0, 600)
    result.verdict = "FAIL — threw"
    result.elapsedMs = Date.now() - started
    return result
  }
}

/**
 * Shared implementation for both GET and POST below: runs Case A (service
 * token) and, if a caller token is present on the request, Case B (combined
 * caller token) — or, if `sizes` is supplied, runs the system-prompt-size
 * sweep instead of the normal A/B matrix. See the module comment above for
 * what the two cases mean and how to read the result.
 */
async function handleDiagnostic(sizes: number[] | undefined) {
  const serviceToken = getSpcsBearerToken()
  const base = restBaseUrl()
  if (!serviceToken || !base) {
    return Response.json(
      { error: "No SPCS service token or SNOWFLAKE_HOST", hasServiceToken: !!serviceToken, hasBase: !!base },
      { status: 500 },
    )
  }

  // Only present on a real authenticated browser request — App Runtime
  // injects it. curl from a shell will not have it.
  const callerToken = (await headers()).get("sf-context-current-user-token") ?? ""
  const combined = serviceToken + "." + callerToken

  // This deployment's own materialized table — see probeMessage()'s doc
  // comment for why this must never be a hardcoded absolute path.
  const configTable = await getConfigTable()
  const table = getMaterializedTable(configTable)

  const out: Record<string, unknown> = {
    probeTable: table,
    hasCallerToken: !!callerToken,
    callerTokenLength: callerToken.length,
    serviceTokenLength: serviceToken.length,
  }

  if (sizes?.length) {
    if (!callerToken) {
      return Response.json({ ...out, error: "Sweep needs a caller token — call from the browser." }, { status: 400 })
    }
    out.note =
      "System-prompt size sweep, combined token, fresh thread each time. " +
      "The real chat route sends ~21,400 chars and fails 399525 in ~1.2s."
    const sweep: CaseResult[] = []
    for (const n of sizes) {
      sweep.push(await runCase(`combined token, systemPrompt=${n} chars`, base, combined, table, paddedPrompt(n)))
    }
    out.sweep = sweep.map((r) => ({
      systemPromptChars: r.systemPromptChars,
      verdict: r.verdict,
      httpStatus: r.httpStatus,
      elapsedMs: r.elapsedMs,
      is399525: (r.httpError ?? "").includes("399525") || (r.errors ?? []).some((e) => e.includes("399525")),
      httpError: r.httpError?.slice(0, 200),
    }))
    return Response.json(out, { status: 200 })
  }

  out.note = "Case B is the decision. Read verdict, then toolResults for the identity the SQL ran as."

  // A first: it validates the harness. If A fails, B is uninterpretable.
  out.caseA_service_token = await runCase("A: code_toolset_all + service token", base, serviceToken, table)

  out.caseB_combined_token = callerToken
    ? await runCase("B: code_toolset_all + combined caller token", base, combined, table)
    : { skipped: "No caller token — call this route from the browser, not curl." }

  return Response.json(out, { status: 200 })
}

/**
 * Admin-only gate shared by GET/POST. This diagnostic runs Case A under the
 * app's OWNER role with no caller-privilege check — without this gate, any
 * signed-in viewer (not just an admin) could use it to make the owner role
 * execute a query and read back row counts / identity they otherwise have
 * no access to. Same admin definition as the agent-access-mode toggle:
 * viewer's own role is (or contains) the app's owner role.
 */
async function requireAdmin(): Promise<Response | null> {
  const admin = await checkIsAdmin()
  if (!admin.isAdmin) {
    return Response.json({ error: "Admin only" }, { status: 403 })
  }
  return null
}

// GET so this can be opened as a plain URL in a browser (no body needed —
// always runs the default A/B matrix, never the sizes sweep).
export async function GET() {
  const denied = await requireAdmin()
  if (denied) return denied
  return handleDiagnostic(undefined)
}

export async function POST(req: Request) {
  const denied = await requireAdmin()
  if (denied) return denied
  // Optional `sizes` sweeps system-prompt length under the combined token, so
  // the size threshold can be located in one deploy instead of one per test.
  let sizes: number[] | undefined
  try {
    const body = (await req.json()) as { sizes?: unknown }
    if (Array.isArray(body?.sizes)) {
      sizes = body.sizes.filter((n): n is number => typeof n === "number" && n > 0 && n <= 200_000)
    }
  } catch {
    // No body / not JSON — run the default A/B matrix.
  }
  return handleDiagnostic(sizes)
}
