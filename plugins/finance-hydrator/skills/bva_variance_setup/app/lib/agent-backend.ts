/**
 * Shared "run one agent turn, emit JobEvents" layer used by both
 * app/api/agent/chat/route.ts and app/api/agent/setup/route.ts.
 *
 * Dispatches to one of two backends, decided once per container lifetime by
 * `isRestBackendAvailable()` (see lib/agent-availability.ts):
 *   - REST (primary): Cortex Agents REST API with the `code_toolset_all` tool —
 *     no subprocess, no vendored CLI, runs entirely on Snowflake's managed
 *     backend. Bundles bash/write/edit alongside a read-only
 *     `snowflake_sql_execute` tool; safety comes from `permission_policy:
 *     "always_ask"` plus this app's own permission_decision auto-approve (SQL
 *     only) / auto-deny (everything else) — see runTurnRest() below.
 *   - CLI (fallback): the vendored Cortex Code CLI via
 *     `cortex-code-agent-sdk`, spawned as a subprocess. Used automatically
 *     whenever the REST backend isn't available on the account.
 *
 * Both backends support caller's rights via the combined token
 * (`<service>.<caller>`): REST sends it as the Bearer token, CLI writes it
 * into connections.toml. See lib/spcs-auth.ts.
 *
 * Caller's rights is the default and is unconditional whenever the caller
 * header is present — UNLESS an admin has explicitly switched the app to
 * owner's rights via app/api/agent-access-mode (see lib/agent-access-mode.ts).
 * That is a deliberate, admin-gated, deploy-time-changeable choice, not a
 * fallback: runAgentTurn() checks it once per turn and, when set, skips the
 * fail-closed guard below and forces every token-resolution point in both
 * backends to ignore the caller token entirely.
 *
 * Caller's rights with code_toolset_all works: under the combined token the agent's
 * snowflake_sql_execute runs as the signed-in viewer (CURRENT_USER = the
 * viewer, not MANAGED_SERVICE_*), so warehouse + schema USAGE + table SELECT
 * all resolve for the caller.
 * Do not declare snowflake_sql_execute as a standalone tool_spec.type —
 * declaring it standalone provisions nothing and hangs the turn.
 *
 * Access is scoped by restricted caller's rights: the effective privilege is
 * the intersection of what the viewer already holds and what a caller grant on
 * the owner role permits (here via the app's caller role). A viewer therefore
 * cannot reach data they could not query directly.
 *
 * Both backends translate into the exact same JobEvent shapes so the
 * browser-facing job-store/polling contract (lib/job-store.ts) never needs
 * to know which one ran. See README.md's Guardrails and
 * Applying-this-to-other-apps sections for the full writeup.
 *
 * Known limitation: `cancelJob()` (lib/job-store.ts) can only abort a CLI
 * turn (it closes the stored CLI session). A REST turn has no equivalent
 * cancellable handle — hitting "stop" stops the browser from polling, but
 * the outbound REST call keeps running server-side until Snowflake finishes
 * it.
 */

import "./cortex-cli"
import { createCortexCodeSession } from "cortex-code-agent-sdk"
import { ensureSpcsAuth, cleanupSpcsAuth, getSpcsBearerToken } from "./spcs-auth"
import { appendEvent, finishJob, getLatestCallerToken, setJobSession } from "./job-store"
import { isRestBackendAvailable, restBaseUrl } from "./agent-availability"
import { getAgentAccessMode } from "./agent-access-mode"

export interface AgentTurnOptions {
  jobId: string
  /** The message to send this turn (may already include precomputed context, e.g. setup's profiling bundle). */
  message: string
  systemPrompt: string
  /** Opaque continuation token: a CLI session id, or (REST) "threadId:parentMessageId". */
  sessionId?: string
  maxTurns: number
  /** chat/route.ts wants tool_done to include a result preview; setup/route.ts doesn't. */
  includeResultPreview: boolean
  /** Caller's-rights user token from the Sf-Context-Current-User-Token header.
   *  Captured by the route handler (which has next/headers access) and
   *  passed here because the fire-and-forget job runs outside the HTTP
   *  request. For long turns, later REST calls re-read the latest token
   *  relayed via updateCallerToken() (job-store.ts) so the token doesn't
   *  expire mid-turn. */
  callerToken?: string
  /** Set internally by runAgentTurn() from the admin-controlled agent-access
   *  mode. When true, both backends ignore callerToken entirely (including
   *  any relayed via job-store) and run as the app's owner role. Not meant
   *  to be set by callers of runAgentTurn(). */
  forceOwnerMode?: boolean
}

// ─── Shared SQL-tool formatting (used by both backends) ────────────────────
//
// The CLI's "SQL" tool uses input.query; the REST code_toolset_all sandbox's
// snowflake_sql_execute tool uses input.sql — same idea (a SQL string the
// tool is about to run), different field name depending on backend/tool.
// The helpers below normalize that difference and turn the raw tool
// input/output into the short strings the chat UI actually displays.

const MAX_QUERY_CHARS = 4_000
const MAX_RESULT_CHARS = 4_000

// Pulls the SQL text out of a tool call's input, regardless of which of the
// two field names (see comment above) the active backend used.
function sqlFromInput(input: Record<string, unknown> | undefined): string {
  if (!input) return ""
  if (typeof input.query === "string") return input.query.trim()
  if (typeof input.sql === "string") return input.sql.trim()
  return ""
}

// One-line label shown next to the "running query" indicator in the chat UI
// while a query is in flight — collapsed to a single line and hard-capped
// at 80 chars so a multi-line SQL block doesn't blow out the UI.
function summarizeSql(input: Record<string, unknown> | undefined): string {
  const q = sqlFromInput(input)
  if (!q) return "Running query…"
  const collapsed = q.replace(/\s+/g, " ")
  const first = collapsed.slice(0, 80)
  return first.length < collapsed.length ? `${first}…` : first
}

// The full (but still capped) SQL text, sent to the browser once a query
// finishes so the user can expand and inspect exactly what ran — capped
// because an agent-generated query could in principle be large, and this
// text rides along on every polling response, not just once.
function fullQuery(input: Record<string, unknown> | undefined): string {
  const q = sqlFromInput(input)
  if (!q) return ""
  return q.length > MAX_QUERY_CHARS ? `${q.slice(0, MAX_QUERY_CHARS)}\n… (truncated)` : q
}

type ToolResultContent =
  | string
  | Array<{ type?: string; text?: string; json?: unknown }>

// Flattens a tool result (which may be a plain string or an array of
// content blocks, depending on backend) into a single capped preview string
// for the chat UI — same truncation rationale as fullQuery() above.
function resultPreview(content: ToolResultContent | undefined): string {
  if (content === undefined) return ""
  const text = typeof content === "string"
    ? content
    : content.map((c) => c.text ?? (c.json !== undefined ? JSON.stringify(c.json) : "")).join("\n")
  const trimmed = text.trim()
  if (!trimmed) return ""
  return trimmed.length > MAX_RESULT_CHARS ? `${trimmed.slice(0, MAX_RESULT_CHARS)}\n… (truncated)` : trimmed
}

// ─── Public entry point ─────────────────────────────────────────────────────

// The single function both API routes call to run one turn of the agent.
// It never returns a result directly — instead it emits JobEvents (via
// appendEvent) that the browser picks up by polling, and always finishes by
// calling finishJob() so the background job is marked complete even if the
// turn errored out. Three responsibilities happen here, in order: (1) figure
// out which identity this turn should run as (owner vs. caller's rights),
// (2) refuse to silently downgrade to owner's rights if something's wrong,
// (3) pick a backend (REST or CLI) and hand off to it.
export async function runAgentTurn(opts: AgentTurnOptions): Promise<void> {
  try {
    // Admin-controlled override (app/api/agent-access-mode): when explicitly
    // set to "owner", every turn runs under the app's own role for every
    // viewer, and the fail-closed caller's-rights guard below does not apply.
    // Default is "caller" — the guard's original fail-closed behavior is
    // unchanged unless an admin has explicitly opted out.
    const forceOwnerMode = (await getAgentAccessMode()) === "owner"
    opts = { ...opts, forceOwnerMode }

    // Fail closed: in SPCS the agent must run as the signed-in viewer, never
    // as the app's owner role. App Runtime injects Sf-Context-Current-User-Token
    // on every authenticated request, so its absence means something is wrong
    // (header stripped, unauthenticated path, expired session) — and silently
    // continuing would run the turn with the owner's much broader privileges.
    // Local dev has no SPCS token at all and is intentionally exempt: it is
    // reachable only from the developer's own machine. Skipped entirely when
    // forceOwnerMode is set — that is an intentional, admin-made choice, not
    // a missing-header failure.
    const callerToken = getLatestCallerToken(opts.jobId) ?? opts.callerToken
    if (!forceOwnerMode && getSpcsBearerToken() && !callerToken) {
      throw new Error(
        "Caller's rights unavailable: no Sf-Context-Current-User-Token on this request. " +
        "Refusing to fall back to the app's owner role. Reload the page to re-authenticate.",
      )
    }

    const useRest = await isRestBackendAvailable()
    console.log(`[agent-backend][${opts.jobId}] backend=${useRest ? "REST(code_toolset_all)" : "CLI(vendored)"}`)
    if (useRest) {
      await runTurnRest(opts)
    } else {
      await runTurnCli(opts)
    }
    appendEvent(opts.jobId, { type: "done" })
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    console.error(`[agent-backend][${opts.jobId}] turn failed: ${msg}`)
    appendEvent(opts.jobId, { type: "error", message: msg })
    appendEvent(opts.jobId, { type: "done" })
  } finally {
    finishJob(opts.jobId)
  }
}

// ─── CLI backend (fallback — vendored Cortex Code CLI via SDK) ─────────────

// Entry point for the CLI backend: sets up per-turn credentials, delegates
// the actual session/streaming work to runTurnCliInner(), and guarantees
// those credentials get cleaned up afterwards no matter how the turn ends.
async function runTurnCli(opts: AgentTurnOptions): Promise<void> {
  const { jobId, message, systemPrompt, sessionId, maxTurns, includeResultPreview, callerToken, forceOwnerMode } = opts

  // For caller's rights: pass the caller token to ensureSpcsAuth so it writes
  // the combined token (service + caller) into connections.toml. The CLI's
  // snowflake_sql_execute tool then runs as the signed-in viewer, not the
  // service. The caller token's lifetime is controlled by
  // SERVICE_CALLER_TOKEN_VALIDITY_SECS (set to 600s at deploy time).
  // Re-read the latest relayed caller token from the job store for long turns.
  // forceOwnerMode (admin-controlled agent-access mode) overrides all of
  // this — ignore any caller token entirely and run as the owner role.
  const effectiveCallerToken = forceOwnerMode ? undefined : (getLatestCallerToken(jobId) ?? callerToken)
  // jobId namespaces the connections.toml this turn writes (its own
  // SNOWFLAKE_HOME under jobs/<jobId>/) so concurrent viewers' CLI-backend
  // turns can't race on one shared file and cross-contaminate credentials.
  // Cleaned up in the finally block below via cleanupSpcsAuth(jobId).
  const spcsAuth = ensureSpcsAuth(effectiveCallerToken, jobId)
  console.log(`[agent-backend][${jobId}] CLI auth=${effectiveCallerToken ? "caller" : "owner"}`)
  try {
    await runTurnCliInner(opts, spcsAuth, jobId, message, systemPrompt, sessionId, maxTurns, includeResultPreview)
  } finally {
    // Delete this turn's per-job connections.toml directory now that the CLI
    // session is closed — see jobSnowflakeHome() doc comment in spcs-auth.ts.
    cleanupSpcsAuth(jobId)
  }
}

async function runTurnCliInner(
  opts: AgentTurnOptions,
  spcsAuth: ReturnType<typeof ensureSpcsAuth>,
  jobId: string,
  message: string,
  systemPrompt: string,
  sessionId: string | undefined,
  maxTurns: number | undefined,
  includeResultPreview: boolean,
): Promise<void> {
  // Spawns the vendored CLI as a subprocess (createCortexCodeSession) and
  // drives one turn of it: send the user's message, stream back whatever
  // the CLI produces, and translate each CLI-shaped event into this app's
  // own JobEvent shape (the same shape runTurnRest() below produces from
  // very differently-shaped REST events) so the browser-polling code never
  // needs to know which backend actually ran.
  let session: Awaited<ReturnType<typeof createCortexCodeSession>>
  try {
    session = await createCortexCodeSession({
      connection: spcsAuth?.connection
        ?? process.env.SNOWFLAKE_DEFAULT_CONNECTION_NAME
        ?? process.env.SNOWFLAKE_CONNECTION_NAME
        ?? process.env.SNOWFLAKE_CONNECTION,
      env: spcsAuth?.env,
      cliPath: process.env.CORTEX_CODE_CLI_PATH,
      systemPrompt,
      allowedTools: ["SQL"],
      disallowedTools: ["Bash", "Write", "Edit", "Read", "Glob", "Grep", "WebSearch", "WebFetch"],
      // NOTE: a canUseTool callback was removed earlier — providing canUseTool
      // makes the SDK pass `--permission-prompt-tool stdio` and set
      // `sdkManagedPermissions: true`, a negotiation path this vendored CLI
      // build does not appear to complete: every turn hung until the
      // initialize control request timed out. Primary read-only enforcement
      // for the SQL-text case now relies on the REST backend
      // (code_toolset_all's platform-enforced read-only snowflake_sql_execute
      // tool). This CLI fallback path is only reached when the REST backend
      // isn't available, and until CLI support for sdkManagedPermissions is
      // confirmed working, it must rely on allowedTools/disallowedTools alone
      // rather than break entirely.
      ...(sessionId ? { resume: sessionId } : {}),
      maxTurns,
      extraArgs: { "no-auto-update": null },
    })
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    const friendly = msg.includes("ENOENT") || msg.includes("not found") || msg.includes("spawn")
      ? "Cortex Code CLI not found. Run: sh scripts/vendor-cortex-cli.sh"
      : msg
    throw new Error(friendly)
  }

  setJobSession(jobId, session)

  try {
    await session.send(message)
    let outSessionId = sessionId ?? null

    // The CLI SDK's event stream borrows chat-message roles as event types
    // even for things that aren't really messages: "assistant" carries both
    // the model's text and any tool calls it wants to make, "user" carries
    // tool *results* being fed back in (this SDK models a completed tool
    // call as a synthetic "user" turn), and "result" is the one terminal
    // event marking the whole CLI turn as finished.
    for await (const event of session.stream()) {
      if (event.type === "assistant") {
        for (const block of event.content) {
          if (block.type === "text" && block.text)
            appendEvent(jobId, { type: "text", text: block.text })
          if (block.type === "tool_use") {
            const input = block.input as Record<string, unknown>
            appendEvent(jobId, { type: "tool_start", name: "SQL", label: summarizeSql(input), query: fullQuery(input) })
          }
        }
        if (!outSessionId) outSessionId = event.session_id ?? null
      }

      if (event.type === "user") {
        for (const block of event.message.content) {
          if (block.type === "tool_result") {
            appendEvent(jobId, {
              type: "tool_done",
              name: "SQL",
              ...(includeResultPreview ? { result: resultPreview(block.content) } : {}),
            })
          }
        }
      }

      if (event.type === "result") {
        if (event.is_error) appendEvent(jobId, { type: "error", message: event.errors.join(", ") })
        if (outSessionId) appendEvent(jobId, { type: "session_id", sessionId: outSessionId })
        break
      }
    }
  } finally {
    await session.close().catch(() => {})
  }
}

// ─── REST backend (Cortex Agents REST API, code_toolset_all) ──────────────
//
// code_toolset_all bundles bash/write/edit/grep/glob/web_search alongside a
// read-only snowflake_sql_execute tool (SELECT/SHOW only — platform
// enforced). Safety here does NOT come from the system prompt asking the
// model nicely; it comes from `permission_policy: "always_ask"` (set below)
// plus this file auto-responding to every resulting permission request:
// "Allow Once" for snowflake_sql_execute, "Deny" for everything else.
// Never set permission_policy to "always_allow" for this tool type — that
// skips this gate entirely and could let a state-modifying tool run unapproved.

/** The only tool this app ever approves via permission_decision. */
const APPROVED_TOOL_NAME = "snowflake_sql_execute"

/** Required by the Cortex Agents REST API to identify the Bearer token as
 *  OAuth (vs PAT/JWT). Without this, combined caller's-rights tokens can
 *  fail with an internal error. */
const OAUTH_HEADERS = { "X-Snowflake-Authorization-Token-Type": "OAUTH" }

/** Build the auth token for a REST call. Uses the combined caller's-rights
 *  token (service + caller) when available, falling back to the service-only
 *  token. For long turns, re-reads the latest caller token from the job store
 *  so the token doesn't expire mid-turn (the Sf-Context-Current-User-Token
 *  expires in ~2 min, but browser polls relay fresh copies). forceOwnerMode
 *  (admin-controlled agent-access mode) overrides all of this — ignores any
 *  caller token entirely and returns the service-only token. */
function getAuthToken(jobId: string, initialCallerToken?: string, forceOwnerMode?: boolean): string {
  const serviceToken = getSpcsBearerToken()
  if (!serviceToken) return ""
  if (forceOwnerMode) return serviceToken
  // Re-read the latest caller token (relayed by polls) if available,
  // otherwise use the initial one passed at turn start.
  const callerToken = getLatestCallerToken(jobId) ?? initialCallerToken ?? ""
  if (callerToken) {
    return serviceToken + "." + callerToken
  }
  return serviceToken
}

/** sessionId format for this backend: "<threadId>:<parentMessageId>". */
function parseRestSessionId(sessionId?: string): { threadId?: number; parentMessageId?: number } {
  if (!sessionId) return {}
  const m = /^(\d+):(\d+)$/.exec(sessionId)
  if (!m) return {} // e.g. a CLI-style session id from before availability flipped — start a fresh thread
  return { threadId: Number(m[1]), parentMessageId: Number(m[2]) }
}

// Creates a new conversation "thread" on the Cortex Agents REST API — the
// server-side container that groups a sequence of agent:run calls together
// so later calls can reference earlier ones (via parent_message_id) instead
// of re-sending the whole conversation history each time. Called once at
// the start of a brand-new chat; an existing conversation instead reuses
// its threadId, parsed out of the opaque sessionId by parseRestSessionId()
// above.
async function createThread(base: string, token: string): Promise<number> {
  const res = await fetch(`${base}/api/v2/cortex/threads`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${token}`, ...OAUTH_HEADERS },
    body: JSON.stringify({ origin_application: "bva_variance" }),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => "")
    throw new Error(`Failed to create Cortex Agent thread: ${res.status} ${detail.slice(0, 300)}`)
  }
  const json = (await res.json()) as { thread_id: number }
  return json.thread_id
}

/** Full per-request config for a direct (no agent object) agent:run call — every request needs this since there's no persisted agent object to fall back on. */
interface RestToolsConfig {
  tools: Array<{ tool_spec: { type: string; name: string } }>
  tool_resources: Record<string, unknown>
  instructions: { system: string }
}

function buildToolsConfig(systemPrompt: string): RestToolsConfig {
  // `snowflake_sql_execute` is NOT a valid standalone tool_spec.type — it
  // exists only as one of the tools bundled inside `code_toolset_all` (see
  // the Coding Agent docs). Declaring it standalone is accepted by the API
  // and the agent starts planning, but no such tool is ever provisioned, so
  // the turn hangs until timeout. Do not reintroduce it.
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

/**
 * Responds to a pending tool-permission request and returns the response
 * stream for the *resumed* turn — this is not a fire-and-forget side
 * channel, it's a normal follow-up agent:run call whose response continues
 * the paused turn (more tool calls and/or the final answer).
 *
 * Two things confirmed against Snowflake's docs after a live 400/500 here:
 *   1. Every direct agent:run call (no agent object) must carry the full
 *      `tools`/`tool_resources`/`instructions` — there's no persisted agent
 *      config to fall back on. The decision call was previously omitting
 *      all three, which is what the underlying model rejected.
 *   2. Per "Use threads with the Cortex Agent REST API," parent_message_id
 *      must be an *assistant* message id, and a turn that pauses on a
 *      tool_use-with-permission ends its own agent:run call with that
 *      tool_use persisted as the last content item of an assistant message
 *      (metadata.assistant_message_id in the terminal `response` event for
 *      *that* call) — not the parent_message_id the turn started from. The
 *      decision must reference that id, so callers must pass the
 *      assistant_message_id captured from consumeAgentStream() for the
 *      request that produced this toolUseId, not the original turn's
 *      starting parent id.
 */
async function sendPermissionDecision(
  base: string,
  token: string,
  threadId: number,
  parentMessageId: number,
  toolUseId: string,
  decision: "Allow Once" | "Deny",
  toolsConfig: RestToolsConfig,
  reason?: string,
): Promise<Response> {
  const res = await fetch(`${base}/api/v2/cortex/agent:run`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
      authorization: `Bearer ${token}`,
      ...OAUTH_HEADERS,
    },
    body: JSON.stringify({
      thread_id: threadId,
      parent_message_id: parentMessageId,
      stream: true,
      messages: [{
        role: "user",
        content: [{
          type: "permission_decision",
          permission_decision: { tool_use_id: toolUseId, decision, ...(reason ? { reason } : {}) },
        }],
      }],
      ...toolsConfig,
    }),
  })
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => "")
    throw new Error(`Failed to send permission_decision (${decision} for ${toolUseId}): ${res.status} ${detail.slice(0, 300)}`)
  }
  return res
}

/** Accumulated result of consuming one agent:run SSE stream. */
interface StreamState {
  /** Set when this stream ended on a tool_use awaiting approval. */
  pendingPermission?: { toolUseId: string; toolName: string }
  /** The assistant_message_id from this call's terminal `response` event, if any — required as parent_message_id for a permission_decision follow-up, and as the session id for the *next* conversation turn. */
  assistantMessageId?: number
  sawError: boolean
}

/** Parses one `event: ...\ndata: ...` SSE block and mutates `state`/emits JobEvents accordingly. */
function processSseBlock(
  raw: string,
  jobId: string,
  includeResultPreview: boolean,
  state: StreamState,
): void {
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

  switch (eventName) {
    case "response.text.delta": {
      const text = data.text
      if (typeof text === "string" && text) appendEvent(jobId, { type: "text", text })
      break
    }
    case "response.tool_use": {
      const input = data.input as Record<string, unknown> | undefined
      const toolName = typeof data.name === "string" ? data.name : "tool"
      const isSql = toolName === APPROVED_TOOL_NAME
      appendEvent(jobId, {
        type: "tool_start",
        name: isSql ? "SQL" : toolName,
        label: isSql ? summarizeSql(input) : `Running ${toolName}…`,
        query: isSql ? fullQuery(input) : "",
      })
      const permission = data.permission as { options?: string[] } | undefined
      const toolUseId = data.tool_use_id
      if (permission?.options?.length && typeof toolUseId === "string") {
        state.pendingPermission = { toolUseId, toolName }
      }
      break
    }
    case "response.tool_result": {
      const toolName = typeof data.name === "string" ? data.name : "tool"
      appendEvent(jobId, {
        type: "tool_done",
        name: toolName === APPROVED_TOOL_NAME ? "SQL" : toolName,
        ...(includeResultPreview ? { result: resultPreview(data.content as ToolResultContent) } : {}),
      })
      break
    }
    case "error": {
      appendEvent(jobId, { type: "error", message: typeof data.message === "string" ? data.message : "Unknown error" })
      state.sawError = true
      break
    }
    case "response": {
      // Terminal event for *this* agent:run call — carries the ids needed
      // to resume (permission_decision) or continue (next turn).
      const meta = data.metadata as { thread_id?: number; assistant_message_id?: number } | undefined
      if (meta?.thread_id !== undefined && meta?.assistant_message_id !== undefined) {
        state.assistantMessageId = meta.assistant_message_id
        appendEvent(jobId, { type: "session_id", sessionId: `${meta.thread_id}:${meta.assistant_message_id}` })
      }
      if (data.status === "cancelled" || data.status === "timed_out") {
        appendEvent(jobId, { type: "error", message: `Agent run ${data.status}` })
        state.sawError = true
      }
      break
    }
    default:
      break // response.status, response.text, response.thinking(.delta), metadata, etc. — no UI mapping needed
  }
}

/** Reads one agent:run SSE response to completion, returning what was learned about it. */
async function consumeAgentStream(response: Response, jobId: string, includeResultPreview: boolean): Promise<StreamState> {
  const state: StreamState = { sawError: false }
  if (!response.body) {
    state.sawError = true
    return state
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  // SSE (Server-Sent Events) frames the response as a sequence of small
  // text blocks separated by a blank line ("\n\n"), each one holding an
  // `event:`/`data:` pair. Network reads can split or merge those blocks
  // arbitrarily, so this accumulates everything into `buffer` and only
  // hands a chunk off to processSseBlock() once a full blank-line-terminated
  // block has actually arrived — never assume one `read()` call lines up
  // with one SSE block.
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let sepIdx: number
    while ((sepIdx = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, sepIdx)
      buffer = buffer.slice(sepIdx + 2)
      processSseBlock(block, jobId, includeResultPreview, state)
    }
  }
  return state
}

/** Snowflake's generic internal-error code. For code_toolset_all it is most
 *  often a transient sandbox-provisioning capacity issue rather than anything
 *  wrong with the request: the same request that fails instantly will often
 *  succeed on a retry moments later. */
const TRANSIENT_ERROR_CODE = "399525"

/** Attempts before giving up on a transient 399525. */
const MAX_AGENT_RUN_ATTEMPTS = 3

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * POSTs agent:run, retrying only on a transient 399525.
 *
 * Safe to retry: a 399525 here means the sandbox never provisioned, so no tool
 * ran and no side effect occurred. The agent's only tool is read-only SQL in
 * any case.
 *
 * IMPORTANT: `token` is fixed by the caller for the whole turn (see
 * runTurnRest) and reused verbatim on every attempt here, including retries.
 * Do not re-read "the latest" caller token per attempt — a thread/turn must
 * be created and continued under one consistent caller identity. Re-reading
 * mid-turn was the actual cause of the intermittent 399525s this was meant to
 * avoid: if a browser poll relays a fresher caller token between the thread's
 * creation and a later call (a retry, or a permission-decision continuation),
 * switching identity mid-thread makes the sandbox error out (399525) even
 * though each token is individually valid. The caller token is now valid for
 * SERVICE_CALLER_TOKEN_VALIDITY_SECS (600s) — far longer than any single
 * turn — so there is no need to refresh mid-turn.
 *
 * Retries only the transient code — any other failure surfaces immediately, so
 * genuine misconfiguration is not hidden behind a retry loop.
 */
async function postAgentRun(
  base: string,
  jobId: string,
  token: string,
  body: Record<string, unknown>,
): Promise<Response> {
  let lastDetail = ""
  let lastStatus = 0

  for (let attempt = 1; attempt <= MAX_AGENT_RUN_ATTEMPTS; attempt++) {
    const res = await fetch(`${base}/api/v2/cortex/agent:run`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "text/event-stream",
        authorization: `Bearer ${token}`,
        ...OAUTH_HEADERS,
      },
      body: JSON.stringify(body),
    })

    if (res.ok && res.body) return res

    lastStatus = res.status
    lastDetail = await res.text().catch(() => "")

    const isTransient = lastDetail.includes(TRANSIENT_ERROR_CODE)
    if (!isTransient || attempt === MAX_AGENT_RUN_ATTEMPTS) break

    const backoffMs = attempt * 2000
    console.warn(
      `[agent-backend][${jobId}] agent:run attempt ${attempt}/${MAX_AGENT_RUN_ATTEMPTS} hit transient ${TRANSIENT_ERROR_CODE} ` +
      `(sandbox provisioning); retrying in ${backoffMs}ms`,
    )
    if (attempt === 1) {
      appendEvent(jobId, { type: "text", text: "_(Snowflake's agent sandbox was busy — retrying)_\n\n" })
    }
    await sleep(backoffMs)
  }

  const hint = lastDetail.includes(TRANSIENT_ERROR_CODE)
    ? ` — Snowflake's agent sandbox was unavailable after ${MAX_AGENT_RUN_ATTEMPTS} attempts. This is a transient platform capacity issue; please try again.`
    : ""
  throw new Error(`Cortex Agents REST call failed: ${lastStatus} ${lastDetail.slice(0, 300)}${hint}`)
}

// Entry point for the REST backend: creates or resumes a thread, sends the
// user's message via agent:run, and streams the response back as JobEvents.
// If the agent pauses mid-turn to ask for tool permission (see Layer 2 of
// README.md §11), the loop below auto-approves/denies and resumes until the
// turn actually finishes — from the browser's point of view this whole
// back-and-forth happens invisibly inside one runAgentTurn() call.
async function runTurnRest(opts: AgentTurnOptions): Promise<void> {
  const { jobId, message, systemPrompt, sessionId, includeResultPreview, callerToken, forceOwnerMode } = opts

  const base = restBaseUrl()
  const serviceToken = getSpcsBearerToken()
  if (!serviceToken || !base) {
    throw new Error("Cortex Agents REST API unreachable (no SPCS token or SNOWFLAKE_HOST) — this should not happen if isRestBackendAvailable() returned true")
  }

  // Build the auth token: combined caller's-rights token if available,
  // otherwise service-only. getAuthToken re-reads the latest relayed caller
  // token from the job store on every call (for long turns). forceOwnerMode
  // overrides this entirely — see getAuthToken's doc comment.
  const token = getAuthToken(jobId, callerToken, forceOwnerMode)
  console.log(`[agent-backend][${jobId}] REST auth=${forceOwnerMode ? "owner (admin override)" : (callerToken || getLatestCallerToken(jobId) ? "caller" : "owner")}`)
  console.log(`[agent-backend][${jobId}] systemPrompt length=${systemPrompt.length} chars, message length=${message.length} chars`)

  let { threadId, parentMessageId } = parseRestSessionId(sessionId)
  if (threadId === undefined) {
    threadId = await createThread(base, token)
    parentMessageId = 0
  }
  const resolvedThreadId: number = threadId
  const startingParentId = parentMessageId ?? 0
  const toolsConfig = buildToolsConfig(systemPrompt)

  const initialRes = await postAgentRun(base, jobId, token, {
    thread_id: resolvedThreadId,
    parent_message_id: startingParentId,
    messages: [{ role: "user", content: [{ type: "text", text: message }] }],
    stream: true,
    ...toolsConfig,
  })

  let state = await consumeAgentStream(initialRes, jobId, includeResultPreview)

  // A tool_use requiring approval ends its own agent:run call with that
  // tool_use as the last content item of a (now-persisted) assistant
  // message. Resuming means sending the decision as a normal follow-up
  // turn whose parent_message_id is THAT assistant message's id, then
  // consuming its response the same way — which may itself pause on
  // another tool_use, hence the loop. See sendPermissionDecision's doc
  // comment for why this differs from the original (broken) approach.
  // `guard` is just a sanity cap on how many tool_use/permission round-trips
  // one turn can go through — not a real limit expected to be hit, just
  // protection against an unforeseen infinite loop if the API ever kept
  // returning `pendingPermission` forever.
  let guard = 0
  while (state.pendingPermission && guard++ < 20) {
    const { toolUseId, toolName } = state.pendingPermission
    if (state.assistantMessageId === undefined) {
      // Fail closed: no valid parent to resume from — stop rather than guess.
      appendEvent(jobId, {
        type: "error",
        message: `Cannot resolve tool permission for ${toolName}: no assistant message id was returned for this turn.`,
      })
      break
    }
    const approve = toolName === APPROVED_TOOL_NAME
    const decision = approve ? "Allow Once" : "Deny"
    const reason = approve ? undefined : "This agent is read-only — only snowflake_sql_execute is permitted in this session."
    if (!approve) appendEvent(jobId, { type: "text", text: `_(blocked \`${toolName}\` — this agent is read-only)_\n\n` })

    let decisionRes: Response
    try {
      // Reuse the SAME token the thread was created with — do not re-read
      // "the latest" caller token here. See postAgentRun's doc comment: a
      // permission-decision continuation on a thread must authenticate as
      // the same caller identity that created it, or the sandbox errors
      // out with a generic 399525.
      decisionRes = await sendPermissionDecision(base, token, resolvedThreadId, state.assistantMessageId, toolUseId, decision, toolsConfig, reason)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      console.error(`[agent-backend][${jobId}] permission_decision failed for ${toolName}: ${msg}`)
      appendEvent(jobId, { type: "error", message: `Failed to resolve tool permission for ${toolName}: ${msg}` })
      break
    }
    state = await consumeAgentStream(decisionRes, jobId, includeResultPreview)
  }
  // state.sawError is informational only — the error event itself already
  // went out; runAgentTurn's caller doesn't need this turn to throw just
  // because the model-level run reported an error (it already surfaced via
  // the `error` event).
  void state.sawError
}
