import { existsSync, readFileSync, writeFileSync, mkdirSync, rmSync } from "fs"
import { homedir } from "os"
import { join } from "path"

// ─── SPCS authentication helper ──────────────────────────────────────────────
//
// SPCS = Snowpark Container Services, Snowflake's platform for running this
// app's own Docker container. Snowflake mounts a short-lived OAuth token at
// /snowflake/session/token inside that container, which proves the container
// itself is who it claims to be (the app's "owner" identity, i.e. the role
// the app was deployed under).
//
// The CoCo CLI does NOT auto-detect this file, so this module writes a fresh
// connections.toml entry (the Snowflake CLI's standard credentials file)
// before every session. Without this, the CLI falls back to interactive
// browser auth and times out at 120 s ("Control request timeout: initialize")
// — there's no browser to click through inside a headless container.
//
// This module also builds "caller's rights" tokens: rather than every agent
// query running as the app's own owner role, the signed-in viewer's own
// identity can be layered on top so the query runs (and is privilege-checked)
// as that viewer. See lib/agent-backend.ts's file header and README.md §10
// for the full caller's-rights vs. owner's-rights picture; this file just
// supplies the low-level token plumbing both backends build on.
//
// Shared by every route that spawns a CoCo session (chat, setup) so the
// auth logic can't silently drift between them.

export interface SpcsAuth {
  connection: string
  env: Record<string, string>
}

/**
 * Read the raw SPCS session OAuth token for use as a REST `Authorization: Bearer`
 * header (e.g. calling the Cortex Agents REST API directly). Distinct from
 * ensureSpcsAuth() below, which writes a connections.toml entry for the CoCo
 * CLI — this is just the token, no file writes. Returns undefined outside an
 * SPCS container (e.g. local dev), where this file simply doesn't exist.
 */
export function getSpcsBearerToken(): string | undefined {
  try {
    const token = readFileSync("/snowflake/session/token", "utf8").trim()
    return token || undefined
  } catch {
    return undefined
  }
}

/**
 * Build the combined caller's-rights token: `<service>.<caller>`.
 * Snowflake's server-side auth understands this dotted format: it splits on
 * the "." and checks both identities — the app's own service token (proving
 * which app is calling) and the viewer's caller token (proving who the
 * signed-in user is) — then runs the query as the intersection of what each
 * one is allowed to do. See README.md §10 ("Understanding caller grants").
 * Returns the service-only token if no caller token is provided, which makes
 * the query run as the app's owner role instead (no caller identity to add).
 */
export function buildCallerRightsToken(callerToken?: string): string | undefined {
  const serviceToken = getSpcsBearerToken()
  if (!serviceToken) return undefined
  if (callerToken) return serviceToken + "." + callerToken
  return serviceToken
}

/**
 * jobId comes from crypto.randomUUID() (see job-store.ts) — safe to use
 * directly as a directory name, but we still strip anything outside
 * [A-Za-z0-9_-] before touching the filesystem so a malformed/attacker-
 * influenced jobId can never escape the jobs/ directory (path traversal).
 */
function sanitizeJobId(jobId: string): string {
  return jobId.replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 128) || "unknown"
}

/**
 * Per-job connections.toml directory, keyed by jobId. Every CLI-backend turn
 * gets its own SNOWFLAKE_HOME instead of sharing one file across all
 * concurrent viewers — without this, two viewers' CLI-backend turns racing
 * on the same ~/.snowflake/connections.toml could read back the wrong
 * caller's combined token (a cross-user credential mixup, not just a
 * cross-user data leak). Call cleanupSpcsAuth(jobId) when the turn finishes.
 */
function jobSnowflakeHome(jobId: string): string {
  const baseDir = process.env.SNOWFLAKE_HOME ?? join(homedir(), ".snowflake")
  return join(baseDir, "jobs", sanitizeJobId(jobId))
}

/**
 * Prepares everything the vendored CLI needs to authenticate as this SPCS
 * container, optionally layering a viewer's caller's-rights token on top.
 * Writes a per-job connections.toml file (see jobSnowflakeHome() above) and
 * returns the connection name + env vars the CLI session should be started
 * with. Returns undefined when there's no SPCS token to work with at all
 * (e.g. local dev outside a container) or if anything else about the
 * container's identity looks wrong — callers treat undefined as "run the CLI
 * with whatever ambient Snowflake connection is configured instead."
 */
export function ensureSpcsAuth(callerToken?: string, jobId?: string): SpcsAuth | undefined {
  const tokenPath = "/snowflake/session/token"
  if (!existsSync(tokenPath)) return undefined

  try {
    const serviceToken = readFileSync(tokenPath, "utf8").trim()
    const host  = process.env.SNOWFLAKE_HOST ?? ""
    if (!serviceToken || !host) return undefined

    // For caller's rights: combine the service token with the caller token.
    // The combined token format is `<service-oauth-token>.<Sf-Context-Current-User-Token>`.
    // The Snowflake server splits at the "." and resolves both identities.
    // The caller token's lifetime is controlled by SERVICE_CALLER_TOKEN_VALIDITY_SECS
    // (default 2 min, extendable up to 7 days). The app sets this to 600 (10 min)
    // at deploy time — see README.md §14 (Step 5 — Verify and hand off).
    const token = callerToken ? serviceToken + "." + callerToken : serviceToken

    // The Snowflake CLI requires an `account` field in the connection config.
    // Use SNOWFLAKE_ACCOUNT if available; otherwise fall back to the first
    // hostname segment (e.g. "myorg-my-account" from the
    // full SPCS hostname).
    const account = process.env.SNOWFLAKE_ACCOUNT ?? host.split(".")[0]

    // Per-job SNOWFLAKE_HOME (see jobSnowflakeHome doc comment above) — falls
    // back to the shared default dir only when no jobId is available.
    const snowflakeDir = jobId ? jobSnowflakeHome(jobId) : (process.env.SNOWFLAKE_HOME ?? join(homedir(), ".snowflake"))
    mkdirSync(snowflakeDir, { recursive: true, mode: 0o700 })

    // Write a complete connections.toml entry so the CoCo CLI can authenticate
    // using the SPCS OAuth token — no browser required.
    // Re-written each call so the token is always fresh.
    // When callerToken is provided, the combined token makes the CLI's
    // snowflake_sql_execute tool run as the signed-in viewer, not the service.
    // mode 0o600: readable/writable only by the container's own user, since
    // this file holds a live combined caller+service token.
    const toml = [
      "[spcs]",
      `account = "${account}"`,
      `host = "${host}"`,
      `authenticator = "oauth"`,
      `token = "${token}"`,
    ].join("\n") + "\n"

    writeFileSync(join(snowflakeDir, "connections.toml"), toml, { mode: 0o600 })

    // Also pass SPCS auth via env vars.  The CLI binary supports:
    //   SNOWFLAKE_RUNNING_INSIDE_SPCS — triggers SPCS auto-detection
    //   SNOWFLAKE_TOKEN               — inline OAuth token
    //   SNOWFLAKE_TOKEN_FILE_PATH     — path to the token file
    //   SNOWFLAKE_HOME                — per-job connections.toml directory
    // These act as belt-and-suspenders alongside the connections.toml entry.
    return {
      connection: "spcs",
      env: {
        SNOWFLAKE_RUNNING_INSIDE_SPCS: "true",
        SNOWFLAKE_TOKEN: token,
        SNOWFLAKE_TOKEN_FILE_PATH: tokenPath,
        ...(jobId ? { SNOWFLAKE_HOME: snowflakeDir } : {}),
      },
    }
  } catch {
    return undefined
  }
}

/**
 * Remove the per-job connections.toml directory written by ensureSpcsAuth().
 * Call once the CLI-backend turn finishes (success or failure) so combined
 * tokens don't linger on disk longer than the turn that needed them.
 */
export function cleanupSpcsAuth(jobId: string): void {
  try {
    rmSync(jobSnowflakeHome(jobId), { recursive: true, force: true })
  } catch {
    // best-effort cleanup; a leftover per-job dir is isolated by jobId and
    // will be orphaned but not readable by other jobs
  }
}
