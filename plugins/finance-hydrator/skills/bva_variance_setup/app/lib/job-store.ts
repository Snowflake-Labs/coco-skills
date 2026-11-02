/**
 * In-memory store for background CoCo agent jobs.
 *
 * The SPCS ingress proxy hard-limits HTTP response bodies to ~90 seconds.
 * A full agent turn (REST call to Cortex Agents, or historically the CLI)
 * reliably exceeds that limit, so we cannot stream results over a
 * long-lived SSE connection.
 *
 * The fix: runAgentTurn() (lib/agent-backend.ts) runs as a fire-and-forget
 * async task (module-level, outside any HTTP request) and appends events
 * here as they arrive. The browser polls short-lived GET requests (<1 ms
 * each) to pick up accumulated events. No single HTTP request stays open
 * long enough for the proxy to close it.
 *
 * In plain terms, this is the classic "job ID + polling" pattern for slow
 * background work:
 *   1. The initial HTTP request that kicks off the agent turn returns almost
 *      instantly with just a job id (see createJob below) — it does NOT
 *      wait for the agent to finish.
 *   2. The actual multi-minute agent work happens in the background,
 *      completely detached from that original request/response cycle.
 *   3. The browser repeatedly asks "what's new for job X since event N?"
 *      (pollJob) every second or so, until the job reports done=true.
 * This `store` Map is the shared piece of memory that connects step 2
 * (the background worker writing events) to step 3 (the browser reading
 * them) — it's what lets a slow background task "talk to" a client that
 * isn't holding a connection open to it.
 */

import type { createCortexCodeSession } from "cortex-code-agent-sdk"

type Session = Awaited<ReturnType<typeof createCortexCodeSession>>

export interface JobEvent {
  type: string
  [key: string]: unknown
}

interface Job {
  events: JobEvent[]
  done: boolean
  /** Kept so we can abort if the client cancels (CLI backend only). */
  session: Session | null
  /** Latest caller's-rights user token, relayed by each browser poll (see
   *  updateCallerToken). Refreshed every few seconds so a long agent turn's
   *  later REST calls can pick up a newer token than the one the turn
   *  started with (the Sf-Context-Current-User-Token expires in ~2 min). */
  latestCallerToken: string | null
  createdAt: number
  /** Set when the job finishes. Retention for done jobs is measured from
   *  here, not createdAt — a job can legitimately take 10+ minutes to
   *  finish, so measuring retention from creation time would make it
   *  expire the instant it's marked done. */
  finishedAt: number | null
}

// Module-level singleton — persists across requests in the same Node.js process.
// Keyed by a fresh crypto.randomUUID() per turn (see createJob() below), so
// one viewer's caller-scoped tool results can never be read back by another
// viewer's poll requests, even though this is a single shared Map.
const store = new Map<string, Job>()

// Hard safety-valve lifetime for a job, active or not — guards against
// abandoned jobs (e.g. client closed the tab without cancelling) leaking
// memory forever. Must stay comfortably above the longest turn this app
// actually runs (the setup agent's account-wide table scan can take several
// minutes end to end), or a slow-but-healthy job gets evicted out from under
// itself mid-flight.
const JOB_TTL_MS = 20 * 60 * 1000 // 20 minutes
// Once a job is done, keep it pollable for a short grace period (covers the
// client's last poll picking up trailing events) then clean it up promptly
// rather than waiting for the full TTL.
const DONE_JOB_RETENTION_MS = 2 * 60 * 1000 // 2 minutes

function isExpired(job: Job, now: number): boolean {
  if (job.done) return job.finishedAt !== null && now - job.finishedAt > DONE_JOB_RETENTION_MS
  return now - job.createdAt > JOB_TTL_MS
}

/** Create a new job slot. Call before starting the background task.
 *  This is the "job id" half of the polling pattern: the caller generates an
 *  id, registers an empty event log for it here, kicks off the actual agent
 *  work in the background, and immediately hands the id back to the browser
 *  so it can start polling for progress. */
export function createJob(id: string): void {
  store.set(id, { events: [], done: false, session: null, latestCallerToken: null, createdAt: Date.now(), finishedAt: null })
  // Opportunistically evict stale jobs on every creation.
  const now = Date.now()
  for (const [key, job] of store.entries()) {
    if (key !== id && isExpired(job, now)) store.delete(key)
  }
}

/** Store the CoCo session so it can be aborted if the job is cancelled (CLI backend only). */
export function setJobSession(id: string, session: Session): void {
  const job = store.get(id)
  if (job) job.session = session
}

/** Relay a fresh caller's-rights user token (from each browser poll) so a
 *  long-running REST turn can re-read it before its next outbound agent:run
 *  call. See lib/agent-backend.ts's getAuthToken(). */
export function updateCallerToken(id: string, token: string): void {
  const job = store.get(id)
  if (job) job.latestCallerToken = token
}

/** Read the latest caller token relayed for this job, or null if none. */
export function getLatestCallerToken(id: string): string | null {
  return store.get(id)?.latestCallerToken ?? null
}

/** Append one SSE-shaped event to the job's event list. Called by the
 *  background agent worker as it produces output — this is the "write"
 *  side that the browser's poll loop later reads from via pollJob(). */
export function appendEvent(id: string, event: JobEvent): void {
  store.get(id)?.events.push(event)
}

/** Mark the job done and release the session reference. */
export function finishJob(id: string): void {
  const job = store.get(id)
  if (!job) return
  job.done = true
  job.session = null
  job.finishedAt = Date.now()
}

/**
 * Abort and remove a job (called when the client sends DELETE or navigates away).
 * A CLI turn is cancelled by closing the stored session. A REST turn has no
 * cancellable handle — this only stops the browser from polling; the outbound
 * REST call keeps running server-side until Cortex Agents finishes it.
 */
export function cancelJob(id: string): void {
  const job = store.get(id)
  if (!job) return
  job.session?.close().catch(() => {})
  store.delete(id)
}


/**
 * Return all events after index `after`, plus the done flag.
 * Returns null if the job is unknown or expired.
 *
 * This is the "read" side of the polling pattern: the browser calls this
 * every second or so with the index of the last event it already saw
 * (`after`), and gets back only the new events since then plus whether the
 * job has finished. It keeps polling until `done` comes back true.
 */
export function pollJob(
  id: string,
  after: number,
): { events: JobEvent[]; done: boolean } | null {
  const job = store.get(id)
  if (!job) return null
  if (isExpired(job, Date.now())) {
    store.delete(id)
    return null
  }
  return {
    events: job.events.slice(after),
    done: job.done,
  }
}

// ─── Concurrency guard ───────────────────────────────────────────────────────
//
// This container runs on a small, shared compute pool (CPU_X64_S) with a
// fixed CPU allocation. Originally added because two vendored CoCo CLI child
// processes running at once (e.g. a second browser tab, or a retried request
// fired while the first turn was still mid-flight) fought over that
// allocation badly enough that a second session's handshake could starve for
// the full timeout while an already-running session was mid-turn. The CLI
// path is gone, but serializing turns through this queue is still a
// reasonable default for a small shared compute pool, so it stays: only one
// agent turn ever runs at a time per container, and additional requests wait
// their turn instead of competing for the same resources.
//
// Implementation: `queueTail` is a promise chain — each call to
// runExclusive() tacks its work onto the end of whatever's already queued
// (via .then), so `fn` only starts once every previously-queued `fn` has
// settled, and the NEXT caller's work gets chained onto this one before it
// even runs. This is a simple in-process mutex/queue built entirely out of
// promises, with no external lock needed.
let queueTail: Promise<unknown> = Promise.resolve()

/** Run `fn` only after every previously-queued runExclusive() call has
 *  finished (successfully or not), enforcing "one agent turn at a time"
 *  for this container. See the block comment above for why. */
export function runExclusive<T>(fn: () => Promise<T>): Promise<T> {
  const run = queueTail.then(fn, fn)
  queueTail = run.then(() => undefined, () => undefined)
  return run
}
