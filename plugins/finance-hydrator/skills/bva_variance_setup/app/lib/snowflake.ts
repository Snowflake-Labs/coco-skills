/**
 * Snowflake query helper. Call from any server component or route handler:
 *   const rows = await querySnowflake("SELECT ...")
 *   const rows = await querySnowflake("SELECT ...", { callersRights: true })
 *   const rows = await querySnowflake("SELECT ...", { warehouse: "MY_WH" })
 *
 * Which helper to use:
 *   - querySnowflake — default for simple, fast queries (small lookups, tight filters,
 *     typically well under ~10 seconds end-to-end).
 *   - querySnowflakeLongRunning — use when the work may exceed ~10 seconds or is
 *     warehouse-heavy (large scans, big aggregations, COPY/EXPORT, long-running
 *     procedures, resuming warehouse, etc.). Submits async, polls by query id, then
 *     fetches rows:
 *       const rows = await querySnowflakeLongRunning("CALL MY_LONG_JOB(...)")
 *   Same auth options (callersRights, pools) apply to both.
 *
 * Logging: by default prints concise lines (auth path, timing, truncated SQL, queryId on
 * success). Set SNOWFLAKE_SDK_QUIET=1 to disable. SDK internal logs stay at ERROR unless
 * you change snowflake.configure below.
 *
 * Auth is auto-detected (in priority order):
 *   1. SPCS token file (/snowflake/session/token) — read fresh on every call
 *   2. SNOWFLAKE_USER + SNOWFLAKE_PASSWORD env vars — password auth (local dev)
 *   3. ~/.snowflake/config.toml default connection — zero-config local dev
 *
 * Connection pooling:
 *   Owner's rights: single pool per process, recreated when the service token rotates.
 *   Caller's rights: one pool per user+role (keyed by combined token), all drained
 *     when the service token rotates.
 *   Local dev pools (password / toml) are shared and never rotated.
 *
 * SPCS caller's rights helpers:
 *   queryWithToken(query, token) — runs a query with an explicit OAuth token.
 *   getServiceToken() — reads the SPCS service token from /snowflake/session/token.
 *   buildCallerRightsToken(callerUserToken) — combines service + caller tokens.
 *
 * Secrets:
 *   getSecret(name, type) — reads a Snowflake SECRET value at runtime. Use this instead of
 *     reading SNOWFLAKE_SECRET_* env vars or querying the SECRET object directly (see the
 *     getSecret docstring below). The secret must be declared under `secrets:` in app.yml to
 *     be available in SPCS.
 *
 * Cleanup:
 *   closePool() — drains and destroys all active pools on shutdown.
 */

import { headers } from "next/headers"
import fs from "fs"
import path from "path"
import os from "os"
import snowflake from "snowflake-sdk"

snowflake.configure({
  logLevel: "ERROR",
  ...(process.env.SNOWFLAKE_SDK_DISABLE_OCSP === "true" && { disableOCSPChecks: true, ocspFailOpen: true }),
})

const SPCS_TOKEN_PATH = "/snowflake/session/token"

const LOG_PREFIX = "[snowflake]"

/** Single-line SQL preview for logs (keeps log volume small). */
function previewSql(sql: string, maxLen = 200): string {
  const s = sql.replace(/\s+/g, " ").trim()
  if (s.length <= maxLen) return s
  return `${s.slice(0, maxLen)}…`
}

function sfLogQuiet(): boolean {
  const v = process.env.SNOWFLAKE_SDK_QUIET
  return v === "1" || v === "true"
}

function sfLog(message: string): void {
  if (sfLogQuiet()) return
  console.log(`${LOG_PREFIX} ${message}`)
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * Build the `parameters` fragment for `conn.execute`. Sets QUERY_WAREHOUSE_NAME
 * for a per-query warehouse override (no separate `USE WAREHOUSE` round-trip) and
 * QUERY_TAG for observability (so slow queries can be traced in QUERY_HISTORY).
 */
function stmtParams(warehouse?: string, tag?: string): { parameters?: Record<string, unknown> } {
  const params: Record<string, unknown> = {}
  if (warehouse) params.QUERY_WAREHOUSE_NAME = warehouse
  if (tag) params.QUERY_TAG = tag
  return Object.keys(params).length ? { parameters: params } : {}
}

/** SDK v2 supports these; @types/snowflake-sdk may lag — keep cast at boundary. */
type PollingConnection = snowflake.Connection & {
  getQueryStatus: (queryId: string) => Promise<string>
  getResultsFromQueryId: (opts: { queryId: string }) => Promise<{ streamRows: () => NodeJS.ReadableStream }>
  isStillRunning: (status: string) => boolean
  isAnError: (status: string) => boolean
  getQueryStatusThrowIfError: (queryId: string) => Promise<string>
}

type ExecuteOptions = Parameters<snowflake.Connection["execute"]>[0] & { asyncExec?: boolean }

/** Drains a Snowflake result-set stream into a plain in-memory array of row objects. */
function streamRowsToArray(statement: { streamRows: () => NodeJS.ReadableStream }): Promise<Record<string, any>[]> {
  return new Promise((resolve, reject) => {
    const rows: Record<string, any>[] = []
    statement
      .streamRows()
      .on("error", (err: Error) => reject(err))
      .on("data", (row: Record<string, any>) => rows.push(row))
      .on("end", () => resolve(rows))
  })
}

/**
 * Submit `asyncExec`, poll `getQueryStatus` until the warehouse finishes, then fetch rows.
 * Keeps status logs sparse (default: at most once per minute while running).
 */
async function runLongRunningOnConnection(
  conn: PollingConnection,
  query: string,
  options: {
    pollIntervalMs: number
    statusLogIntervalMs: number
    maxWaitMs?: number
  },
  warehouse?: string,
  tag?: string,
): Promise<Record<string, any>[]> {
  const queryId = await new Promise<string>((resolve, reject) => {
    conn.execute({
      sqlText: query,
      asyncExec: true,
      ...stmtParams(warehouse, tag),
      complete: (err, stmt) => {
        if (err) reject(new Error(`Async submit failed: ${err.message}`))
        else resolve(stmt!.getQueryId())
      },
    } as ExecuteOptions)
  })

  const t0 = Date.now()
  sfLog(`long-running submitted queryId=${queryId} sql=${JSON.stringify(previewSql(query))}`)

  let lastStatusLog = t0
  while (true) {
    if (options.maxWaitMs !== undefined && Date.now() - t0 > options.maxWaitMs) {
      throw new Error(
        `Long-running query timed out after ${options.maxWaitMs}ms (queryId=${queryId})`,
      )
    }

    const status = await conn.getQueryStatus(queryId)
    if (conn.isAnError(status)) {
      try {
        await conn.getQueryStatusThrowIfError(queryId)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        throw new Error(`Query failed (queryId=${queryId}): ${msg}`)
      }
      throw new Error(`Query failed (queryId=${queryId}): ${status}`)
    }
    if (!conn.isStillRunning(status)) break

    const now = Date.now()
    if (now - lastStatusLog >= options.statusLogIntervalMs) {
      sfLog(
        `long-running poll queryId=${queryId} status=${status} elapsedMs=${now - t0}`,
      )
      lastStatusLog = now
    }
    await sleep(options.pollIntervalMs)
  }

  const statement = await conn.getResultsFromQueryId({ queryId })
  const rows = await streamRowsToArray(statement)
  sfLog(
    `long-running complete queryId=${queryId} rows=${rows.length} totalMs=${Date.now() - t0}`,
  )
  return rows
}

// --- Connection pool configuration ---
//
// A "connection pool" here is a small set of already-authenticated Snowflake
// connections that get reused across queries instead of opening a brand new
// TCP + auth handshake for every single query (that handshake is slow —
// hundreds of ms — so paying it once per pool and reusing connections is
// what keeps per-query latency low). `min`/`max` below bound how many live
// connections a pool is allowed to hold at once.
//
// This app needs TWO kinds of pools, not one, because Snowflake queries can
// run under two different identities:
//   - Owner's rights: every query runs as the app's own service identity,
//     regardless of who is clicking around in the browser. There's exactly
//     one identity involved, so exactly one pool suffices for the whole
//     process.
//   - Caller's rights: the query should run AS the actual logged-in user
//     (their own role/privileges), not as the app's service identity. Each
//     distinct user+role combination is a different Snowflake identity and
//     therefore needs its own authenticated connection — you can't share one
//     pooled connection across two different users the way you can for
//     owner's rights. That's why caller's-rights pools are a Map keyed by
//     identity instead of a single slot.

const POOL_CONFIG = {
  min: 0, // Start with no connections (lazy init)
  max: 10, // Scale up to 10 concurrent connections
}

// Owner's rights: single pool, recreated when the service token rotates.
// SPCS periodically rotates the service's own OAuth token; when that
// happens, any connections still holding the old token would start failing,
// so `ownersPoolToken` lets us detect the rotation and swap in a fresh pool
// (see getOwnersPool below) rather than let requests silently start failing.
let ownersPool: ReturnType<typeof snowflake.createPool> | null = null
let ownersPoolToken = ""

// Caller's rights: one pool per combined token (user+role), keyed by combined token.
// All pools are drained when the service token rotates.
// This Map-keyed-by-identity shape is the correct pattern for anything that
// caches a caller's-rights result — see the matching invariant comment in
// lib/config-server.ts for what NOT to do (a single shared slot).
const callersPool = new Map<string, ReturnType<typeof snowflake.createPool>>()
let callersServiceToken = ""

// Local dev pools.
let passwordPool: ReturnType<typeof snowflake.createPool> | null = null
let tomlPool: ReturnType<typeof snowflake.createPool> | null = null

function baseConfig(): snowflake.ConnectionOptions {
  const application = "SnowflakeAppRuntime"
  const base: snowflake.ConnectionOptions = { application }
  if (process.env.SNOWFLAKE_ACCOUNT) base.account = process.env.SNOWFLAKE_ACCOUNT
  if (process.env.SNOWFLAKE_WAREHOUSE) base.warehouse = process.env.SNOWFLAKE_WAREHOUSE
  if (process.env.SNOWFLAKE_ACCOUNT_URL) base.accessUrl = process.env.SNOWFLAKE_ACCOUNT_URL
  // SNOWFLAKE_HOST is commonly injected by eval/CI frameworks
  if (!base.accessUrl && process.env.SNOWFLAKE_HOST) {
    base.accessUrl = `https://${process.env.SNOWFLAKE_HOST}`
  }
  if (process.env.SNOWFLAKE_ROLE) base.role = process.env.SNOWFLAKE_ROLE
  if (process.env.SNOWFLAKE_DATABASE) base.database = process.env.SNOWFLAKE_DATABASE
  if (process.env.SNOWFLAKE_SCHEMA) base.schema = process.env.SNOWFLAKE_SCHEMA
  return base
}

// --- ~/.snowflake/connections.toml + config.toml reader ---

interface TomlConnection {
  account?: string
  user?: string
  password?: string
  host?: string
  port?: string | number
  warehouse?: string
  database?: string
  schema?: string
  region?: string
  role?: string
  authenticator?: string
  protocol?: string
  token_file_path?: string
  [key: string]: unknown
}

let _tomlConfigCache: { defaultName: string; connections: Record<string, TomlConnection> } | null | undefined

function normalizeConnectionsToml(doc: Record<string, any>): Record<string, TomlConnection> {
  const result: Record<string, TomlConnection> = {}
  for (const [key, val] of Object.entries(doc)) {
    if (key === "default_connection_name" || key === "connections") continue
    if (typeof val === "object" && val !== null && !Array.isArray(val)) {
      result[key] = val as TomlConnection
    }
  }
  if (typeof doc.connections === "object" && doc.connections !== null && !Array.isArray(doc.connections)) {
    Object.assign(result, doc.connections as Record<string, TomlConnection>)
  }
  return result
}

/**
 * Resolve a Snowflake connection from local TOML config files.
 *
 * Tries two file layouts (in order):
 *   1. ~/.snowflake/connections.toml — supports both nested ([connections.name])
 *      and legacy (top-level [name]) formats; nested wins on conflict.
 *   2. ~/.snowflake/config.toml — connections nested under [connections.*] sections.
 *
 * Both files may contain a `default_connection_name` key. The env var
 * SNOWFLAKE_CONNECTION_NAME takes priority, then SNOWFLAKE_DEFAULT_CONNECTION_NAME
 * (set by Cortex Code for SnowCLI compatibility), then the file-level default.
 *
 * Result is cached for the lifetime of the process.
 */
export function readTomlDefaultConnection(): TomlConnection | null {
  if (_tomlConfigCache === undefined) {
    _tomlConfigCache = null // mark as attempted
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { parse } = require("smol-toml") as { parse: (s: string) => Record<string, any> }
      const snowDir = process.env.SNOWFLAKE_HOME ?? path.join(os.homedir(), ".snowflake")

      let defaultName = ""
      let connections: Record<string, TomlConnection> = {}

      // connections.toml: top-level sections are connections
      const connPath = path.join(snowDir, "connections.toml")
      if (fs.existsSync(connPath)) {
        const doc = parse(fs.readFileSync(connPath, "utf8"))
        if (typeof doc.default_connection_name === "string") defaultName = doc.default_connection_name
        connections = normalizeConnectionsToml(doc)
      }

      // config.toml: connections nested under [connections.*], also has default_connection_name
      const configPath = path.join(snowDir, "config.toml")
      if (fs.existsSync(configPath)) {
        const doc = parse(fs.readFileSync(configPath, "utf8"))
        if (!defaultName && typeof doc.default_connection_name === "string") {
          defaultName = doc.default_connection_name
        }
        // Only use config.toml connections if connections.toml had none
        if (Object.keys(connections).length === 0) {
          connections = (doc.connections ?? {}) as Record<string, TomlConnection>
        }
      }

      // General SNOWFLAKE_<KEY> env vars overlay on every toml connection (spec §3.B)
      const envOverlay: Partial<TomlConnection> = {}
      const CONN_KEYS = new Set(["account","user","password","database","schema","role","warehouse","protocol","host","port","region","authenticator"])
      for (const [k, v] of Object.entries(process.env)) {
        if (!k.startsWith("SNOWFLAKE_") || k === "SNOWFLAKE_CONNECTION_NAME" || k === "SNOWFLAKE_DEFAULT_CONNECTION_NAME" || k === "SNOWFLAKE_HOME") continue
        const field = k.slice("SNOWFLAKE_".length).toLowerCase()
        if (CONN_KEYS.has(field)) (envOverlay as Record<string, string>)[field] = v!
      }
      if (Object.keys(envOverlay).length > 0) {
        for (const name of Object.keys(connections)) {
          connections[name] = { ...connections[name], ...envOverlay }
        }
      }

      // SNOWFLAKE_CONNECTION_NAME overrides file-level default
      if (process.env.SNOWFLAKE_CONNECTION_NAME) {
        defaultName = process.env.SNOWFLAKE_CONNECTION_NAME
      } else if (process.env.SNOWFLAKE_DEFAULT_CONNECTION_NAME) {
        defaultName = process.env.SNOWFLAKE_DEFAULT_CONNECTION_NAME
      }

      _tomlConfigCache = { defaultName, connections }
    } catch {
      // parse error or smol-toml not installed — silently skip
      return null
    }
  }

  if (!_tomlConfigCache) return null

  const { defaultName, connections } = _tomlConfigCache
  const names = Object.keys(connections)
  if (names.length === 0) return null

  const conn = (defaultName && connections[defaultName]) || connections[names[0]]
  return conn ?? null
}

/** Reset the connection config cache. Exported for testing only. */
export function resetTomlConfigCache() {
  _tomlConfigCache = undefined
}

function tomlConnectionConfig(conn: TomlConnection): snowflake.ConnectionOptions {
  // Use SDK's normalizeConnectionOptions for snake_case -> camelCase + key aliases
  // Type assertion needed until @types/snowflake-sdk includes normalizeConnectionOptions
  const normalize = (snowflake as unknown as { normalizeConnectionOptions: (o: Record<string, unknown>) => snowflake.ConnectionOptions }).normalizeConnectionOptions
  const normalized = normalize(conn as Record<string, unknown>)
  // SDK doesn't handle host/port/protocol -> accessUrl, so we do it here
  if (conn.host && !normalized.accessUrl) {
    const protocol = conn.protocol ?? "https"
    const port = conn.port ? `:${conn.port}` : ""
    normalized.accessUrl = `${protocol}://${conn.host}${port}`
  }
  // The SDK only reads token_file_path when it loads the TOML itself
  // (createConnection with no options). Because we build the options object and
  // pass it to createConnection/createPool, the SDK ignores token_file_path and
  // sends an empty OAuth token, which the server rejects with error 390303.
  // So read the token file ourselves for plain "oauth" auth.
  if (
    !normalized.token &&
    typeof normalized.authenticator === "string" &&
    normalized.authenticator.toUpperCase() === "OAUTH" &&
    conn.token_file_path
  ) {
    normalized.token = fs.readFileSync(conn.token_file_path, "utf8").trim()
  }
  return normalized
}

// --- Pool helpers ---

/**
 * Get (or lazily create) the single owner's-rights pool for this process.
 * If the SPCS service token has rotated since the pool was built, the old
 * pool's connections are now authenticated with a stale token, so it's
 * drained and a fresh pool is created with the new token.
 */
function getOwnersPool(serviceToken: string): ReturnType<typeof snowflake.createPool> {
  if (ownersPool && ownersPoolToken !== serviceToken) {
    sfLog("pool: draining owner's-rights pool (SPCS service token rotated)")
    ownersPool.drain()
    ownersPool = null
  }
  if (!ownersPool) {
    sfLog("pool: creating owner's-rights OAuth pool (SPCS)")
    ownersPool = snowflake.createPool(
      { ...baseConfig(), authenticator: "OAUTH", token: serviceToken },
      POOL_CONFIG,
    )
    ownersPoolToken = serviceToken
  }
  return ownersPool
}

/**
 * Get (or lazily create) the caller's-rights pool for one specific
 * user+role identity (`combinedToken` = service token + that user's token).
 * Each identity gets its own pool since a pooled connection is tied to
 * whichever identity authenticated it — unlike owner's-rights, these can't
 * be shared across users.
 */
function getCallersPool(combinedToken: string, serviceToken: string): ReturnType<typeof snowflake.createPool> {
  // Service token rotated — all combined tokens are stale, drain everything.
  if (callersServiceToken !== serviceToken) {
    if (callersPool.size > 0) {
      sfLog(`pool: draining ${callersPool.size} caller's-rights pool(s) (SPCS service token rotated)`)
    }
    for (const pool of callersPool.values()) pool.drain()
    callersPool.clear()
    callersServiceToken = serviceToken
  }
  if (!callersPool.has(combinedToken)) {
    sfLog("pool: creating caller's-rights OAuth pool (SPCS)")
    callersPool.set(
      combinedToken,
      snowflake.createPool(
        { ...baseConfig(), authenticator: "OAUTH", token: combinedToken },
        POOL_CONFIG,
      ),
    )
  }
  return callersPool.get(combinedToken)!
}

function getPasswordPool(): ReturnType<typeof snowflake.createPool> {
  if (!passwordPool) {
    sfLog("pool: creating password-auth pool (SNOWFLAKE_USER)")
    passwordPool = snowflake.createPool(
      {
        ...baseConfig(),
        username: process.env.SNOWFLAKE_USER,
        password: process.env.SNOWFLAKE_PASSWORD,
      },
      POOL_CONFIG,
    )
  }
  return passwordPool
}

function getTomlPool(conn: TomlConnection): ReturnType<typeof snowflake.createPool> {
  if (!tomlPool) {
    sfLog("pool: creating TOML default-connection pool (~/.snowflake)")
    tomlPool = snowflake.createPool({ ...tomlConnectionConfig(conn), ...baseConfig() }, POOL_CONFIG)
  }
  return tomlPool
}

/** Run `query` on a connection borrowed from `pool`, returning it when done (or on error). */
function queryWithPool(
  pool: ReturnType<typeof snowflake.createPool>,
  query: string,
  authTag: string,
  warehouse?: string,
  tag?: string,
): Promise<Record<string, any>[]> {
  return pool.use(async (conn) => {
    const t0 = Date.now()
    sfLog(`query start mode=${authTag} sql=${JSON.stringify(previewSql(query))}`)
    return new Promise<Record<string, any>[]>((res, rej) => {
      conn.execute({
        sqlText: query,
        ...stmtParams(warehouse, tag),
        complete: (err, stmt, rows) => {
          const ms = Date.now() - t0
          const qid =
            stmt && typeof stmt.getQueryId === "function" ? ` queryId=${stmt.getQueryId()}` : ""
          if (err) {
            sfLog(`query error mode=${authTag} afterMs=${ms}${qid}: ${err.message}`)
            rej(new Error(`Query failed: ${err.message}`))
          } else {
            const out = (rows ?? []) as Record<string, any>[]
            sfLog(`query ok mode=${authTag} rows=${out.length} afterMs=${ms}${qid}`)
            res(out)
          }
        },
      })
    })
  })
}

/** Long-running variant of {@link queryWithPool}: borrows a pooled connection, then submits/polls/fetches. */
function queryWithPoolLongRunning(
  pool: ReturnType<typeof snowflake.createPool>,
  query: string,
  authTag: string,
  longOpts: { pollIntervalMs: number; statusLogIntervalMs: number; maxWaitMs?: number },
  warehouse?: string,
  tag?: string,
): Promise<Record<string, any>[]> {
  return pool.use(async (conn) => {
    sfLog(`long-running start mode=${authTag} sql=${JSON.stringify(previewSql(query))}`)
    return runLongRunningOnConnection(conn as unknown as PollingConnection, query, longOpts, warehouse, tag)
  })
}

// --- One-shot connection helper (used for per-request tokens) ---

/**
 * Open a brand-new (unpooled) connection with `config`, run one query, then
 * destroy the connection. Used for explicit-token queries (queryWithToken)
 * where the token is different on every call, so pooling it wouldn't help —
 * there's nothing to reuse across requests.
 */
function connectAndQuery(
  config: snowflake.ConnectionOptions,
  query: string,
  authTag: string,
  warehouse?: string,
  tag?: string,
): Promise<Record<string, any>[]> {
  const conn = snowflake.createConnection(config)
  const t0 = Date.now()
  sfLog(`query start mode=${authTag} (one-shot) sql=${JSON.stringify(previewSql(query))}`)
  return new Promise((resolve, reject) => {
    conn.connect((err) => {
      if (err) {
        sfLog(`connect failed mode=${authTag}: ${err.message}`)
        return reject(new Error(`Snowflake connection failed: ${err.message}`))
      }
      conn.execute({
        sqlText: query,
        ...stmtParams(warehouse, tag),
        complete: (err, stmt, rows) => {
          conn.destroy(() => {})
          const ms = Date.now() - t0
          const qid =
            stmt && typeof stmt.getQueryId === "function" ? ` queryId=${stmt.getQueryId()}` : ""
          if (err) {
            sfLog(`query error mode=${authTag} afterMs=${ms}${qid}: ${err.message}`)
            reject(new Error(`Query failed: ${err.message}`))
          } else {
            const out = (rows ?? []) as Record<string, any>[]
            sfLog(`query ok mode=${authTag} rows=${out.length} afterMs=${ms}${qid}`)
            resolve(out)
          }
        },
      })
    })
  })
}

/** Long-running variant of {@link connectAndQuery}: one-shot connection, then submit/poll/fetch. */
function connectAndQueryLongRunning(
  config: snowflake.ConnectionOptions,
  query: string,
  authTag: string,
  longOpts: { pollIntervalMs: number; statusLogIntervalMs: number; maxWaitMs?: number },
  warehouse?: string,
  tag?: string,
): Promise<Record<string, any>[]> {
  const conn = snowflake.createConnection(config)
  const t0 = Date.now()
  sfLog(`long-running start mode=${authTag} (one-shot) sql=${JSON.stringify(previewSql(query))}`)
  return new Promise((resolve, reject) => {
    conn.connect((err) => {
      if (err) {
        sfLog(`connect failed mode=${authTag}: ${err.message}`)
        return reject(new Error(`Snowflake connection failed: ${err.message}`))
      }
      runLongRunningOnConnection(conn as unknown as PollingConnection, query, longOpts, warehouse, tag)
        .then((rows) => {
          conn.destroy(() => {})
          sfLog(`long-running one-shot done mode=${authTag} totalMs=${Date.now() - t0}`)
          resolve(rows)
        })
        .catch((e) => {
          conn.destroy(() => {})
          reject(e)
        })
    })
  })
}

interface QueryOptions {
  callersRights?: boolean
  /**
   * Run this query on a specific warehouse. Sets the QUERY_WAREHOUSE_NAME
   * statement parameter for the execution. When omitted, the connection's
   * default warehouse is used.
   */
  warehouse?: string
  /**
   * Optional QUERY_TAG for observability — surfaces in QUERY_HISTORY so slow
   * queries from a given feature (e.g. "bva:discover-bundle") can be traced.
   */
  tag?: string
}

/** Extra options for `querySnowflakeLongRunning` / `queryWithTokenLongRunning`. */
export interface LongRunningQueryOptions extends QueryOptions {
  /**
   * How often to call GS while the query is running (default 5000).
   * Kept conservative to limit log volume and API chatter.
   */
  pollIntervalMs?: number
  /**
   * While running, emit at most one `[snowflake] long-running poll …` line per this many ms (default 60000).
   */
  statusLogIntervalMs?: number
  /** Fail if the query is still not finished after this many ms (optional). */
  maxWaitMs?: number
}

function resolveLongRunningOpts(
  o: LongRunningQueryOptions,
): { pollIntervalMs: number; statusLogIntervalMs: number; maxWaitMs?: number } {
  return {
    pollIntervalMs: o.pollIntervalMs ?? 5000,
    statusLogIntervalMs: o.statusLogIntervalMs ?? 60_000,
    maxWaitMs: o.maxWaitMs,
  }
}

export async function querySnowflake(query: string, options: QueryOptions = {}): Promise<Record<string, any>[]> {
  const { callersRights = false, warehouse, tag } = options
  const serviceToken = getServiceToken()

  if (serviceToken) {
    if (callersRights) {
      const callerToken = (await headers()).get("sf-context-current-user-token") ?? ""
      if (!callerToken) {
        throw new Error(
          "No sf-context-current-user-token header. Ensure the app is running in SPCS with caller's rights enabled.",
        )
      }
      const combinedToken = serviceToken + "." + callerToken
      return queryWithPool(getCallersPool(combinedToken, serviceToken), query, "spcs-caller", warehouse, tag)
    }
    // Retry once on terminated connection (service token rotated mid-flight).
    //
    // A pooled connection can be sitting idle for a while between queries. If
    // the SPCS service token rotates during that idle time, the connection
    // object still LOOKS valid (it's in the pool, not closed) but the
    // Snowflake side has already terminated the session tied to the old
    // token — so the very next query on it fails with a "terminated
    // connection" error, even though nothing is wrong with this specific
    // query. Rather than surface that transient failure to the caller, we
    // detect this exact error, throw away the whole (now-stale) pool, build
    // a fresh one with the current token, and retry the query exactly once.
    // If it fails again after that, something else is actually wrong and we
    // let the error propagate normally.
    try {
      return await queryWithPool(getOwnersPool(serviceToken), query, "spcs-owner", warehouse, tag)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      if (msg.includes("terminated connection")) {
        sfLog("query retry: terminated connection — clearing owner pool and retrying once")
        ownersPool?.drain()
        ownersPool = null
        return queryWithPool(getOwnersPool(serviceToken), query, "spcs-owner", warehouse, tag)
      }
      throw err
    }
  }

  // Local dev: no SPCS token, so caller's rights is not possible.
  if (callersRights) {
    console.warn("[snowflake] useCallersRights=true has no effect outside SPCS — using local dev credentials")
  }

  // Explicit env vars: password auth via pooled connections
  if (process.env.SNOWFLAKE_USER && process.env.SNOWFLAKE_PASSWORD) {
    return queryWithPool(getPasswordPool(), query, "password", warehouse, tag)
  }

  // ~/.snowflake/connections.toml or config.toml: use the default connection (local dev)
  const tomlConn = readTomlDefaultConnection()
  if (tomlConn) {
    return queryWithPool(getTomlPool(tomlConn), query, "toml", warehouse, tag)
  }

  throw new Error(
    "No Snowflake credentials found. Provide one of:\n" +
    "  1. SPCS token file at /snowflake/session/token\n" +
    "  2. SNOWFLAKE_USER + SNOWFLAKE_PASSWORD env vars\n" +
      "  3. ~/.snowflake/config.toml with a default connection"
  )
}

/**
 * Same auth and pooling as {@link querySnowflake}, but submits with `asyncExec`, then polls
 * by `queryId` until the statement finishes (intended for queries that often exceed ~1 minute).
 */
export async function querySnowflakeLongRunning(
  query: string,
  options: LongRunningQueryOptions = {},
): Promise<Record<string, any>[]> {
  const { callersRights = false, warehouse, tag, ...longRest } = options
  const longOpts = resolveLongRunningOpts({ callersRights, ...longRest })
  const serviceToken = getServiceToken()

  if (serviceToken) {
    if (callersRights) {
      const callerToken = (await headers()).get("sf-context-current-user-token") ?? ""
      if (!callerToken) {
        throw new Error(
          "No sf-context-current-user-token header. Ensure the app is running in SPCS with caller's rights enabled.",
        )
      }
      const combinedToken = serviceToken + "." + callerToken
      return queryWithPoolLongRunning(
        getCallersPool(combinedToken, serviceToken),
        query,
        "spcs-caller",
        longOpts,
        warehouse,
        tag,
      )
    }
    return queryWithPoolLongRunning(getOwnersPool(serviceToken), query, "spcs-owner", longOpts, warehouse, tag)
  }

  if (callersRights) {
    console.warn("[snowflake] useCallersRights=true has no effect outside SPCS — using local dev credentials")
  }

  if (process.env.SNOWFLAKE_USER && process.env.SNOWFLAKE_PASSWORD) {
    return queryWithPoolLongRunning(getPasswordPool(), query, "password", longOpts, warehouse, tag)
  }

  const tomlConn = readTomlDefaultConnection()
  if (tomlConn) {
    return queryWithPoolLongRunning(getTomlPool(tomlConn), query, "toml", longOpts, warehouse, tag)
  }

  throw new Error(
    "No Snowflake credentials found. Provide one of:\n" +
    "  1. SPCS token file at /snowflake/session/token\n" +
    "  2. SNOWFLAKE_USER + SNOWFLAKE_PASSWORD env vars\n" +
    "  3. ~/.snowflake/config.toml with a default connection"
  )
}

// --- SPCS caller's rights helpers ---

export function getServiceToken(): string {
  try {
    return fs.readFileSync(SPCS_TOKEN_PATH, "utf8").trim()
  } catch {
    return ""
  }
}

export function buildCallerRightsToken(callerUserToken: string): string {
  const serviceToken = getServiceToken()
  if (!serviceToken) {
    throw new Error("No SPCS service token available at " + SPCS_TOKEN_PATH)
  }
  return serviceToken + "." + callerUserToken
}

/**
 * Run a query using an explicit OAuth token (for service-rights or caller's-rights).
 * Creates a fresh connection each time since the token may differ per request.
 * Pass `warehouse` to run the query on a specific warehouse (QUERY_WAREHOUSE_NAME).
 */
export async function queryWithToken(query: string, token: string, warehouse?: string): Promise<Record<string, any>[]> {
  return connectAndQuery({ ...baseConfig(), authenticator: "OAUTH", token }, query, "oauth-token", warehouse)
}

/**
 * One-shot OAuth connection: async submit + poll + fetch (see {@link querySnowflakeLongRunning}).
 */
export async function queryWithTokenLongRunning(
  query: string,
  token: string,
  options: Omit<LongRunningQueryOptions, "callersRights"> = {},
): Promise<Record<string, any>[]> {
  const { warehouse, ...longRest } = options
  const longOpts = resolveLongRunningOpts({ callersRights: false, ...longRest })
  return connectAndQueryLongRunning(
    { ...baseConfig(), authenticator: "OAUTH", token },
    query,
    "oauth-token",
    longOpts,
    warehouse,
  )
}

// --- Secrets ---

/**
 * Snowflake secret types supported by {@link getSecret}. Mirrors the `TYPE` of a
 * Snowflake `SECRET` object. (`GENERIC_STRING`, `PASSWORD`, and `OAUTH2` are supported.)
 */
export enum SecretType {
  /** Single opaque string (e.g. an API key). `getSecret` returns the string. */
  GENERIC_STRING = "GENERIC_STRING",
  /** Username + password pair. `getSecret` returns `{ username, password }`. */
  PASSWORD = "PASSWORD",
  /** OAuth2 access token. `getSecret` returns a `Promise<string>` — see the docstring. */
  OAUTH2 = "OAUTH2",
}

/** Value returned for a {@link SecretType.PASSWORD} secret. */
export interface PasswordSecret {
  username: string
  password: string
}

/** Directory where SPCS mounts declared secrets (one subdirectory per secret). */
const SPCS_SECRETS_DIR = "/secrets"

/** `SNOWFLAKE_SECRET_<NAME>_<FIELD>` — the env var the runtime injects (name upper-cased). */
function secretEnvVarName(name: string, field: string): string {
  return `SNOWFLAKE_SECRET_${name.toUpperCase()}_${field}`
}

/** Read a mounted secret file (SPCS) if present and non-empty; otherwise null. */
function readMountedSecretFile(name: string, file: string): string | null {
  const filePath = path.join(SPCS_SECRETS_DIR, name, file)
  try {
    const contents = fs.readFileSync(filePath, "utf8")
    if (typeof contents === "string" && contents.trim() !== "") {
      return contents.trim()
    }
  } catch {
    // File not mounted (e.g. local dev).
  }
  return null
}

/**
 * Read one field of a secret.
 *
 * In SPCS the runtime mounts each declared secret as files under
 * `/secrets/<name>/<field>`. We read that file first, and fall back to the
 * `SNOWFLAKE_SECRET_<NAME>_<FIELD>` env var (upper-case) for local development
 * where no files are mounted.
 *
 * @param name  Logical secret name (matches app.yml `secrets[].name`).
 * @param file  Mounted file name (lower-case), e.g. `secret_string`, `username`, `password`.
 * @param env   Env var field suffix (upper-case), e.g. `SECRET_STRING`, `USERNAME`, `PASSWORD`.
 */
function readSecretField(name: string, file: string, env: string): string {
  // 1. Mounted secret file (SPCS): /secrets/<name>/<file>
  const mounted = readMountedSecretFile(name, file)
  if (mounted !== null) return mounted

  // 2. Env var fallback (local dev): SNOWFLAKE_SECRET_<NAME>_<FIELD>
  const envVar = secretEnvVarName(name, env)
  const value = process.env[envVar]
  if (value) return value

  throw new Error(
    `Secret "${name}" field "${file}" is not available. Expected the mounted file ` +
      `${path.join(SPCS_SECRETS_DIR, name, file)} (SPCS) or the env var ${envVar} (local dev). ` +
      `Declare the secret under the top-level "secrets:" block in app.yml (see README → Secrets).`,
  )
}

// --- OAUTH2 tokens ---
//
// In SPCS the runtime mounts an OAUTH2 secret's (auto-rotated) access token at
// /secrets/<name>/access_token, so we read that file fresh on every call. Locally
// there is no mount and the token expires after a few minutes, so the app mints and
// refreshes tokens itself via the OAuth2 client-credentials grant, caching them in
// memory until shortly before they expire.

interface CachedOAuthToken {
  token: string
  /** Epoch ms after which the cached token must be refreshed. */
  expiresAtMs: number
}

/** Refresh a cached token this many ms before it actually expires. */
const OAUTH_REFRESH_MARGIN_MS = 60_000
/** Fallback lifetime (ms) when the token endpoint omits `expires_in`. */
const OAUTH_DEFAULT_TTL_MS = 300_000

const oauthTokenCache = new Map<string, CachedOAuthToken>()
// De-dupe concurrent refreshes for the same secret so we mint one token, not N.
const oauthInflight = new Map<string, Promise<string>>()

/** Mint a fresh access token via the OAuth2 client-credentials grant and cache it. */
async function fetchClientCredentialsToken(name: string, tokenUrl: string): Promise<string> {
  const clientId = process.env[secretEnvVarName(name, "OAUTH_CLIENT_ID")]
  const clientSecret = process.env[secretEnvVarName(name, "OAUTH_CLIENT_SECRET")]
  const scope = process.env[secretEnvVarName(name, "OAUTH_SCOPE")]

  const body = new URLSearchParams({ grant_type: "client_credentials" })
  if (clientId) body.set("client_id", clientId)
  if (clientSecret) body.set("client_secret", clientSecret)
  if (scope) body.set("scope", scope)

  const res = await fetch(tokenUrl, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => "")
    throw new Error(
      `OAuth2 secret "${name}": token endpoint ${tokenUrl} returned ${res.status} ${res.statusText}. ${detail.slice(0, 300)}`,
    )
  }
  const json = (await res.json()) as { access_token?: string; expires_in?: number }
  if (!json.access_token) {
    throw new Error(`OAuth2 secret "${name}": token endpoint response contained no access_token.`)
  }
  const ttlMs = typeof json.expires_in === "number" ? json.expires_in * 1000 : OAUTH_DEFAULT_TTL_MS
  oauthTokenCache.set(name, { token: json.access_token, expiresAtMs: Date.now() + ttlMs })
  return json.access_token
}

/** Local-dev OAuth token: cached client-credentials token (auto-refreshed) or a static env token. */
async function getLocalOAuthToken(name: string): Promise<string> {
  const cached = oauthTokenCache.get(name)
  if (cached && Date.now() < cached.expiresAtMs - OAUTH_REFRESH_MARGIN_MS) {
    return cached.token
  }

  const tokenUrl = process.env[secretEnvVarName(name, "OAUTH_TOKEN_URL")]
  if (tokenUrl) {
    // De-dupe concurrent refreshes so a burst of requests mints a single token.
    let inflight = oauthInflight.get(name)
    if (!inflight) {
      inflight = fetchClientCredentialsToken(name, tokenUrl).finally(() => oauthInflight.delete(name))
      oauthInflight.set(name, inflight)
    }
    return inflight
  }

  // Escape hatch for quick local testing: a static token (NOT auto-refreshed — it will expire).
  const staticToken = process.env[secretEnvVarName(name, "ACCESS_TOKEN")]
  if (staticToken) return staticToken

  throw new Error(
    `OAuth2 secret "${name}" is not available locally. Set ` +
      `${secretEnvVarName(name, "OAUTH_TOKEN_URL")} + ${secretEnvVarName(name, "OAUTH_CLIENT_ID")} + ` +
      `${secretEnvVarName(name, "OAUTH_CLIENT_SECRET")} for the client-credentials refresh flow ` +
      `(recommended — auto-refreshes), or ${secretEnvVarName(name, "ACCESS_TOKEN")} for a short-lived ` +
      `static token (see README → Secrets).`,
  )
}

/** Resolve an OAuth2 access token: SPCS mounted file (auto-rotated) first, else app-level flow. */
async function readOAuth2Token(name: string): Promise<string> {
  const mounted = readMountedSecretFile(name, "access_token")
  if (mounted !== null) return mounted
  return getLocalOAuthToken(name)
}

/**
 * Read a Snowflake SECRET value at runtime.
 *
 * Use this helper rather than reading `SNOWFLAKE_SECRET_*` env vars yourself or
 * querying the SECRET object directly (e.g. `SYSTEM$GET_SECRET`) — a direct
 * runtime query is insecure and is not supported.
 *
 * The secret must be declared under a `secrets:` block in `app.yml` so the runtime
 * mounts it. `secrets:` is a TOP-LEVEL key (a sibling of `install:`/`run:`/
 * `profile:`) — do NOT nest it under `run:`, or it is ignored:
 *
 *   secrets:                  # top-level, NOT under run:
 *     - name: API_KEY
 *       secret: db.schema.api_key_secret
 *
 * Resolution (mounted file first, then local-dev fallback):
 *   - GENERIC_STRING → /secrets/<name>/secret_string   | SNOWFLAKE_SECRET_<NAME>_SECRET_STRING
 *   - PASSWORD       → /secrets/<name>/username         | SNOWFLAKE_SECRET_<NAME>_USERNAME
 *                      /secrets/<name>/password         | SNOWFLAKE_SECRET_<NAME>_PASSWORD
 *   - OAUTH2 (async) → /secrets/<name>/access_token in SPCS (mounted, auto-rotated). Locally the
 *                      app mints + refreshes tokens via the OAuth2 client-credentials grant using
 *                      SNOWFLAKE_SECRET_<NAME>_OAUTH_TOKEN_URL / _OAUTH_CLIENT_ID / _OAUTH_CLIENT_SECRET
 *                      (+ optional _OAUTH_SCOPE), caching until just before expiry. `getSecret`
 *                      returns a `Promise<string>` for OAUTH2 — `await` it.
 *
 * @example
 *   const apiKey = getSecret("API_KEY", SecretType.GENERIC_STRING)
 *   const { username, password } = getSecret("DB_CREDS", SecretType.PASSWORD)
 *   const token = await getSecret("MY_OAUTH", SecretType.OAUTH2)
 */
export function getSecret(name: string, type: SecretType.GENERIC_STRING): string
export function getSecret(name: string, type: SecretType.PASSWORD): PasswordSecret
export function getSecret(name: string, type: SecretType.OAUTH2): Promise<string>
export function getSecret(name: string, type: SecretType): string | PasswordSecret | Promise<string>
export function getSecret(
  name: string,
  type: SecretType,
): string | PasswordSecret | Promise<string> {
  switch (type) {
    case SecretType.GENERIC_STRING:
      return readSecretField(name, "secret_string", "SECRET_STRING")
    case SecretType.PASSWORD:
      return {
        username: readSecretField(name, "username", "USERNAME"),
        password: readSecretField(name, "password", "PASSWORD"),
      }
    case SecretType.OAUTH2:
      return readOAuth2Token(name)
    default:
      throw new Error(`Unsupported secret type: ${String(type)}`)
  }
}

// --- Pool lifecycle ---

/**
 * Gracefully drain and destroy all active connection pools.
 * Call during server shutdown for clean resource cleanup.
 */
export async function closePool(): Promise<void> {
  const drainPromises: Promise<void>[] = []

  if (ownersPool) {
    const p = ownersPool
    ownersPool = null
    ownersPoolToken = ""
    drainPromises.push(p.drain())
  }

  for (const pool of callersPool.values()) {
    drainPromises.push(pool.drain())
  }
  callersPool.clear()

  if (passwordPool) {
    const p = passwordPool
    passwordPool = null
    drainPromises.push(p.drain())
  }

  if (tomlPool) {
    const p = tomlPool
    tomlPool = null
    drainPromises.push(p.drain())
  }

  await Promise.all(drainPromises)
}

/** Returns the Snowflake account base URL for REST API calls (Cortex Analyst, etc.). */
export function getSnowflakeBaseUrl(): string | null {
  if (process.env.SNOWFLAKE_ACCOUNT_URL) return process.env.SNOWFLAKE_ACCOUNT_URL
  if (process.env.SNOWFLAKE_HOST) return `https://${process.env.SNOWFLAKE_HOST}`
  const conn = readTomlDefaultConnection()
  if (!conn) return null
  if (conn.host) {
    const protocol = (conn.protocol as string | undefined) ?? "https"
    const port = conn.port ? `:${conn.port}` : ""
    return `${protocol}://${conn.host}${port}`
  }
  if (conn.account) {
    // "MYORG-MY_ACCOUNT" → "myorg-my-account.snowflakecomputing.com"
    return `https://${(conn.account as string).toLowerCase().replace(/_/g, "-")}.snowflakecomputing.com`
  }
  return null
}

/**
 * Returns the Authorization header value for Snowflake REST API calls.
 * Both SPCS service tokens and external OAuth tokens use "Bearer ..." format.
 * Callers must also send `X-Snowflake-Authorization-Token-Type: OAUTH`.
 * (The "Snowflake Token=..." form is rejected with 401/390104 by the Cortex
 * inference endpoint when using an SPCS service token.)
 */
export function getRestApiAuthHeader(): string {
  const spcsToken = getServiceToken()
  if (spcsToken) return `Bearer ${spcsToken}`
  const conn = readTomlDefaultConnection()
  if (conn) {
    const auth = (typeof conn.authenticator === "string" ? conn.authenticator : "").toUpperCase()
    if (auth === "OAUTH" && conn.token_file_path) {
      try {
        return `Bearer ${fs.readFileSync(conn.token_file_path as string, "utf8").trim()}`
      } catch { /* token file missing or unreadable */ }
    }
  }
  return `Bearer ` // will 401 — no valid local auth available
}
