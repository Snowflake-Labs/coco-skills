/**
 * One-time, cached probe for whether the `code_toolset_all` Cortex Agents tool
 * type is usable on this account (requires `code_toolset_all` access on the
 * account; see README.md → Deploying). Also implicitly proves that the SPCS (Snowpark Container
 * Services — the platform hosting this app's own container) session token
 * works as a REST `Authorization: Bearer` header against the Cortex Agents
 * REST API,
 * which is otherwise untested — nothing else in this app has needed it
 * before, since `lib/snowflake.ts` uses the Snowflake SDK, not raw REST.
 *
 * Must declare the SAME tool type that lib/agent-backend.ts's
 * buildToolsConfig() actually sends. A probe that tests a different tool than
 * the backend uses reports availability for something the app never calls.
 *
 * Note: `snowflake_sql_execute` is not a valid standalone tool type — it is
 * only one of the tools bundled inside `code_toolset_all`. Declaring it
 * standalone is accepted by the API but provisions nothing, so the agent
 * hangs. Do not "simplify" this probe to that type.
 *
 * `code_toolset_all` bundles bash/write/edit alongside `snowflake_sql_execute`.
 * If `permission_policy` were set to `always_allow`, approval for
 * state-modifying tools would be skipped entirely. This app now always requests
 * `permission_policy: "always_ask"` (see lib/agent-backend.ts's
 * runTurnRest()), which streams a `response.tool_use` event carrying
 * `permission.options` for every state-modifying tool call, and requires the
 * client to reply with a `permission_decision`. runTurnRest() auto-approves
 * only `snowflake_sql_execute` (already read-only at the platform level —
 * SELECT/SHOW only) and auto-denies everything else (`bash`, `write`,
 * `edit`). Never set `permission_policy: "always_allow"` for this tool type.
 *
 * Probed once per container lifetime (triggered from the warmup route so it
 * doesn't add latency to a real user turn) and cached — never re-probed
 * per-turn. If it fails for ANY reason (feature not enabled, token rejected,
 * network error), the result is cached as `false` and every turn uses the
 * vendored-CLI fallback instead. This is a deliberately conservative
 * default: we'd rather under-use the new path than risk a flaky per-turn
 * retry loop.
 */

import { getSpcsBearerToken } from "./spcs-auth"

// `cached` holds the probe's answer for the lifetime of this container
// (null = not probed yet). `inflight` de-dupes concurrent callers so two
// requests arriving before the first probe finishes share one network call
// instead of firing two.
let cached: boolean | null = null
let inflight: Promise<boolean> | null = null

/** Shared with agent-backend.ts so both use the exact same base-URL logic. */
export function restBaseUrl(): string | undefined {
  if (process.env.SNOWFLAKE_ACCOUNT_URL) return process.env.SNOWFLAKE_ACCOUNT_URL
  if (process.env.SNOWFLAKE_HOST) return `https://${process.env.SNOWFLAKE_HOST}`
  return undefined
}

/**
 * Fires one real (but harmless — a single "ping" message) agent:run request
 * against the REST API and reports whether it succeeded. This is the actual
 * network call behind isRestBackendAvailable(); that function just adds
 * caching/de-duping around this one-shot check.
 */
async function probe(): Promise<boolean> {
  const token = getSpcsBearerToken()
  const base = restBaseUrl()
  // No SPCS context (e.g. local dev outside a container) — the REST path
  // has nothing to authenticate with, so don't even try.
  if (!token || !base) {
    console.log("[agent-availability] probe skipped: no SPCS token or SNOWFLAKE_HOST, agent turns will fail")
    return false
  }

  try {
    const res = await fetch(`${base}/api/v2/cortex/agent:run`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        stream: false,
        messages: [{ role: "user", content: [{ type: "text", text: "ping" }] }],
        tools: [{ tool_spec: { type: "code_toolset_all", name: "code_toolset_all" } }],
        tool_resources: {
          code_toolset_all: {
            permission_policy: { type: "always_ask" },
            disabled_skills: ["dashboard", "streamlit"],
          },
        },
      }),
    })
    if (!res.ok) {
      const detail = await res.text().catch(() => "")
      console.log(`[agent-availability] probe failed: HTTP ${res.status} ${detail.slice(0, 300)}, agent turns will fail`)
    } else {
      console.log("[agent-availability] probe succeeded — code_toolset_all is available on this account")
    }
    return res.ok
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    console.log(`[agent-availability] probe threw: ${msg}, agent turns will fail`)
    return false
  }
}

/**
 * Returns the cached availability, probing once on first call. This is the
 * function agent-backend.ts actually calls to decide REST vs. CLI per turn —
 * every call after the first one returns instantly from `cached` rather than
 * re-hitting the network, which is what makes this safe to call on every
 * single agent turn without adding latency.
 */
export async function isRestBackendAvailable(): Promise<boolean> {
  if (cached !== null) return cached
  if (!inflight) {
    inflight = probe().then((result) => {
      cached = result
      inflight = null
      return result
    })
  }
  return inflight
}

/**
 * Fire-and-forget: kick off the probe without awaiting it (called from
 * warmup). The point is to pay the probe's network round-trip during
 * container startup/warmup, before any real user is waiting on a chat
 * response, so the very first actual agent turn doesn't have to eat that
 * latency itself.
 */
export function primeAvailabilityProbe(): void {
  void isRestBackendAvailable().catch(() => {})
}
