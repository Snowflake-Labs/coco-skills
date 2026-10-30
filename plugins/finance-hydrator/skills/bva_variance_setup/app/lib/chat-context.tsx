"use client"

/**
 * React context that wires the embedded chat assistant panel up to whichever
 * dashboard/setup page is currently mounted.
 *
 * In plain terms, this is a two-way channel:
 *   - Page -> agent: the current page calls setPageContext() with a
 *     PageContext describing what mode it's in (setup / analysis /
 *     custom_logic), what config/filters are currently active, and a set of
 *     callback functions (onFilterUpdate, onSavePreset, onSwitchConfig, ...)
 *     that the page wants invoked if the agent asks for that action.
 *   - Agent -> page: when the user sends a chat message, sendMessage() posts
 *     that context to the appropriate backend agent endpoint. The agent's
 *     reply can embed special fenced code blocks (```filter, ```save_preset,
 *     ```switch_config, etc.) that this file parses out of the response text
 *     and dispatches to the matching callback from PageContext — that's how
 *     "the agent changed the filters" or "the agent proposed a config"
 *     actually reaches the page's own state.
 * Because the callbacks live on a ref (pageContextRef) rather than plain
 * state, sendMessage always sees whatever the CURRENTLY mounted page most
 * recently registered, without sendMessage itself needing to be recreated
 * every time the page's filters change.
 */

import { createContext, useCallback, useContext, useRef, useState } from "react"
import type { BvaConfig, CustomLogicRule, FilterPreset } from "@/lib/config"

// ─── Types ────────────────────────────────────────────────────────────────────

export interface AvailableFilters {
  versions: string[]
  years: string[]
  organizations: string[]
  costCenters: string[]
  categories: string[]
  quarters: string[]
}

export interface ChatViewState {
  version1?: string
  version2?: string
  fiscalYear?: string
  quarter?: string
  organizations?: string[]
  costCenters?: string[]
  categories?: string[]
  fyStartMonth?: number
  /** Existing saved presets — sent to the agent so it knows what's already saved */
  presets?: Array<{ id: string; name: string }>
  /** Named config profiles — sent to the agent so it can suggest switching */
  configs?: Array<{ name: string; actualsTable: string | null }>
  /** All selectable options — sent to the agent so it knows what values exist */
  available?: AvailableFilters
}

export type FilterUpdate = Partial<Pick<ChatViewState,
  "version1" | "version2" | "fiscalYear" | "quarter" |
  "organizations" | "costCenters" | "categories"
>>

export type CustomLogicProposal = Omit<CustomLogicRule, "id" | "enabled" | "source">

// The "page -> agent" and "agent -> page" contract for one currently-mounted
// page. The current page builds one of these and hands it to setPageContext()
// every time its own state changes (filters, config, etc.) so the chat panel
// is always working off a fresh snapshot. `mode` selects which backend agent
// endpoint sendMessage() talks to; the various `on*` callbacks are how the
// agent's parsed response (see the ```block``` parsing in sendMessage below)
// flows back into that page's own React state.
export interface PageContext {
  mode: "setup" | "analysis" | "custom_logic"
  config: BvaConfig | null
  view: ChatViewState
  existingConfig?: BvaConfig | null
  onConfigAccepted?: (cfg: BvaConfig) => void
  /** Called when the agent emits a ```filter``` block to update the dashboard */
  onFilterUpdate?: (update: FilterUpdate) => void
  /** Called when the agent emits a ```save_preset``` block to persist the current view */
  onSavePreset?: (name: string, filters: FilterPreset["filters"]) => void
  /** Called when the agent emits a ```switch_config``` block — requires human confirmation in the UI */
  onSwitchConfig?: (name: string) => void
  /** Called when the agent emits a ```suggest_overrides``` block (only when agentCanEditConfig=true) */
  onSuggestOverrides?: (patch: { add?: Record<string, string>; remove?: string[] }) => void
  /** Called when the agent emits a ```suggest_commentary``` block */
  onSuggestCommentary?: (rows: Array<{ category: string; text: string }>) => void
  /** Called when the custom-logic agent (mode: "custom_logic") emits a ```customlogic``` block —
   *  proposed rules still need server-side re-validation and human review before they're enabled. */
  onCustomLogicProposed?: (rules: CustomLogicProposal[]) => void
}

export interface ChatMessage {
  role: "user" | "assistant"
  text: string
  toolSteps?: Array<{ name: string; label: string; done: boolean; query?: string; result?: string }>
  proposal?: { config: BvaConfig; summary: string }
  /** ms since epoch when the assistant message was created */
  startedAt?: number
  /** total ms from startedAt to response complete */
  elapsedMs?: number
}

type SseEvent =
  | { type: "text";       text: string }
  | { type: "tool_start"; name: string; label: string; query?: string }
  | { type: "tool_done";  name: string; result?: string }
  | { type: "session_id"; sessionId: string }
  | { type: "error";      message: string }
  | { type: "done" }

interface ChatContextValue {
  messages: ChatMessage[]
  busy: boolean
  isOpen: boolean
  setIsOpen: (v: boolean | ((prev: boolean) => boolean)) => void
  sendMessage: (text: string) => void
  stop: () => void
  clearChat: () => void
  setPageContext: (ctx: PageContext) => void
  pageMode: "setup" | "analysis" | "custom_logic"
  existingConfig: BvaConfig | null | undefined
  onConfigAccepted: ((cfg: BvaConfig) => void) | undefined
  agentDraft: Partial<BvaConfig> | null
  applyAgentDraft: (partial: Partial<BvaConfig>) => void
  clearAgentDraft: () => void
  /** Bumps when the user resets config — dashboards watch this to clear filters/charts. */
  dashboardResetSignal: number
  resetDashboard: () => void
}

// ─── Session localStorage helpers ────────────────────────────────────────────
//
// The chat backend is a multi-turn conversation thread identified by a
// session id. Persisting that id in localStorage (rather than only in
// memory) means a page refresh doesn't lose the thread — the next message
// still resumes the same conversation instead of starting over.

const SESSION_KEY = "bva_chat_session_id"

function loadSessionId(): string | null {
  if (typeof window === "undefined") return null
  return localStorage.getItem(SESSION_KEY)
}
function saveSessionId(id: string) {
  if (typeof window === "undefined") return
  localStorage.setItem(SESSION_KEY, id)
}
function clearSessionId() {
  if (typeof window === "undefined") return
  localStorage.removeItem(SESSION_KEY)
}

// ─── Poll retry helpers ───────────────────────────────────────────────────────
//
// The analysis-mode poll loop hits the SPCS ingress proxy ~1x/sec for the
// life of a chat turn. A single transient connection blip there throws
// "Failed to fetch" and — without retries — kills the whole turn even though
// the background CoCo job keeps running server-side. Retry a few times with
// short backoff before giving up.

function sleepAbortable(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException("", "AbortError")); return }
    const id = setTimeout(resolve, ms)
    signal.addEventListener("abort", () => { clearTimeout(id); reject(new DOMException("", "AbortError")) }, { once: true })
  })
}

const POLL_MAX_RETRIES = 3
const POLL_RETRY_BASE_MS = 500

async function fetchWithRetry(url: string, signal: AbortSignal): Promise<Response> {
  let lastErr: unknown
  for (let attempt = 0; attempt <= POLL_MAX_RETRIES; attempt++) {
    try {
      const res = await fetch(url, { signal })
      if (res.ok) return res
      lastErr = new Error(`Poll request failed: ${res.status}`)
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") throw err
      lastErr = err
    }
    if (attempt < POLL_MAX_RETRIES) await sleepAbortable(POLL_RETRY_BASE_MS * (attempt + 1), signal)
  }
  throw lastErr
}

// ─── Context ──────────────────────────────────────────────────────────────────

const ChatContext = createContext<ChatContextValue | null>(null)

export function useChatContext() {
  const ctx = useContext(ChatContext)
  if (!ctx) throw new Error("useChatContext must be used inside ChatProvider")
  return ctx
}

// ─── Provider ─────────────────────────────────────────────────────────────────

/** Wraps the app so any page/component can read/write chat state via
 *  useChatContext(). Mount once near the root — the chat panel and every
 *  dashboard/setup page share this single instance. */
export function ChatProvider({ children }: { children: React.ReactNode }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy]         = useState(false)
  const [isOpen, setIsOpen]     = useState(false)

  // Page context stored in a ref so sendMessage always reads the latest
  // without needing to be recreated when filters change
  const pageContextRef = useRef<PageContext>({
    mode: "analysis",
    config: null,
    view: {},
  })
  // Mirror just the mode/existingConfig/onConfigAccepted into state so
  // the panel can re-render when the user navigates between pages
  const [pageMode,         setPageMode]         = useState<"setup" | "analysis" | "custom_logic">("analysis")
  const [existingConfig,   setExistingConfig]   = useState<BvaConfig | null | undefined>(undefined)
  const [onConfigAccepted, setOnConfigAccepted] = useState<((cfg: BvaConfig) => void) | undefined>(undefined)
  const [agentDraft, setAgentDraft] = useState<Partial<BvaConfig> | null>(null)

  const applyAgentDraft = useCallback((partial: Partial<BvaConfig>) => {
    setAgentDraft((prev) => {
      const merged = { ...(prev ?? {}), ...partial } as Partial<BvaConfig>
      // Deep (content) equality: returning the SAME reference when nothing
      // actually changed skips the state update, which is what prevents render
      // loops. A shallow compare fails here because parsed autofill payloads
      // contain fresh array/object references (accountFilterPrefixes,
      // clusterPatterns, capabilities…) whose identity differs every parse even
      // when the content is identical.
      if (prev && JSON.stringify(prev) === JSON.stringify(merged)) return prev
      return merged
    })
  }, [])

  const clearAgentDraft = useCallback(() => {
    setAgentDraft(null)
  }, [])

  // Reset signal for dashboard filters/charts. Bumped by the trashcan/reset action;
  // BvaDashboard and ExpenseBreakdown watch it and clear their local filter state.
  const [dashboardResetSignal, setDashboardResetSignal] = useState(0)
  const resetDashboard = useCallback(() => {
    setDashboardResetSignal((n) => n + 1)
  }, [])

  const sessionIdRef = useRef<string | null>(null)
  // Holds the in-flight request's AbortController so the user can cancel it
  const abortRef = useRef<AbortController | null>(null)
  // Load session from localStorage once (client-side only)
  if (typeof window !== "undefined" && sessionIdRef.current === null) {
    const stored = loadSessionId()
    if (stored) sessionIdRef.current = stored
  }

  // ── setPageContext ───────────────────────────────────────────────────────
  //
  // Called by whichever page is currently mounted (typically in an effect
  // that re-runs when its filters/config change) to publish the "page ->
  // agent" half of the contract described in the file header. Storing the
  // full context in a ref (not state) means sendMessage always reads the
  // latest values without this function needing to trigger a re-render of
  // sendMessage's own closure.

  const setPageContext = useCallback((ctx: PageContext) => {
    pageContextRef.current = ctx
    setPageMode(ctx.mode)
    setExistingConfig(ctx.existingConfig)
    // Store in a ref-compatible way — wrap in arrow to avoid useState setter confusion
    setOnConfigAccepted(() => ctx.onConfigAccepted)
  }, [])

  // ── clearChat ───────────────────────────────────────────────────────────

  const clearChat = useCallback(() => {
    setMessages([])
    sessionIdRef.current = null
    clearSessionId()
  }, [])

  // ── sendMessage ─────────────────────────────────────────────────────────
  //
  // Sends the user's text to whichever backend agent endpoint matches the
  // current page's mode, then drives the background-job polling loop (see
  // lib/job-store.ts) that streams the agent's reply back into `messages`
  // piece by piece as it becomes available, and finally hands any parsed
  // ```block``` actions back to the current page via its PageContext
  // callbacks (see processEvent below).

  const sendMessage = useCallback(async (text: string) => {
    if (busy) return
    const ctx = pageContextRef.current

    setMessages((prev) => [...prev, { role: "user", text }])
    setBusy(true)
    const assistantStartedAt = Date.now()
    setMessages((prev) => [...prev, { role: "assistant", text: "", toolSteps: [], startedAt: assistantStartedAt }])

    const controller = new AbortController()
    abortRef.current = controller

    const endpoint = ctx.mode === "setup"
      ? "/api/agent/setup"
      : ctx.mode === "custom_logic"
        ? "/api/agent/custom-logic"
        : "/api/agent/chat"
    const payload  = ctx.mode === "setup"
      ? { message: text, config: ctx.existingConfig ?? null, sessionId: sessionIdRef.current ?? undefined }
      : ctx.mode === "custom_logic"
        ? { message: text, config: ctx.config, sessionId: sessionIdRef.current ?? undefined }
        : { message: text, config: ctx.config, view: ctx.view, sessionId: sessionIdRef.current ?? undefined }

    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      })
      if (!res.ok) throw new Error(`Server error ${res.status}`)

      let assistantText = ""

      // Handles a single parsed event from either transport (polling or SSE).
      // Each event type below either appends to the streaming assistant text,
      // tracks a tool-call step for display, or — for the terminal "done"
      // event — scans the FULL accumulated assistantText for one of several
      // ```fenced``` block types the agent may have emitted (```json for a
      // setup proposal, ```filter to update dashboard filters, etc.). When a
      // block is found and parses as valid JSON, it's stripped out of the
      // visible chat text and handed to the matching PageContext callback —
      // this is the "agent -> page" half of the two-way flow described in
      // the file header.
      const processEvent = (event: SseEvent) => {
        if (event.type === "text") {
          assistantText += event.text
          setMessages((prev) => {
            const updated = [...prev]
            const last    = updated[updated.length - 1]
            if (last?.role === "assistant") updated[updated.length - 1] = { ...last, text: assistantText }
            return updated
          })
        }

        if (event.type === "tool_start") {
          setMessages((prev) => {
            const updated = [...prev]
            const last    = updated[updated.length - 1]
            if (last?.role === "assistant") {
              updated[updated.length - 1] = { ...last, toolSteps: [...(last.toolSteps ?? []), { name: event.name, label: event.label, query: event.query, done: false }] }
            }
            return updated
          })
        }

        if (event.type === "tool_done") {
          setMessages((prev) => {
            const updated = [...prev]
            const last    = updated[updated.length - 1]
            if (last?.role === "assistant") {
              const steps  = last.toolSteps ?? []
              const idx    = steps.findIndex((s) => !s.done)
              const result = event.result
              const next   = idx === -1
                ? steps
                : steps.map((s, i) => i === idx ? { ...s, done: true, result } : s)
              updated[updated.length - 1] = { ...last, toolSteps: next }
            }
            return updated
          })
        }

        if (event.type === "session_id") {
          sessionIdRef.current = event.sessionId
          saveSessionId(event.sessionId)
        }

        if (event.type === "done" && ctx.mode === "setup") {
          const match = assistantText.match(/```json\s*\n([\s\S]*?)\n```/)
          if (match) {
            try {
              const proposed = JSON.parse(match[1]) as BvaConfig
              const cleanText = assistantText.replace(/```json\s*\n[\s\S]*?\n```/g, "").trim()
              setMessages((prev) => {
                const updated = [...prev]
                const last    = updated[updated.length - 1]
                if (last?.role === "assistant") {
                  updated[updated.length - 1] = {
                    ...last,
                    text: cleanText,
                    proposal: { config: proposed, summary: "Configuration ready to save." },
                  }
                }
                return updated
              })
            } catch { /* JSON malformed — just show text as-is */ }
          }
        }

        if (event.type === "done" && ctx.mode === "custom_logic") {
          const match = assistantText.match(/```customlogic\s*\n([\s\S]*?)\n```/)
          if (match && ctx.onCustomLogicProposed) {
            try {
              const proposed = JSON.parse(match[1]) as CustomLogicProposal[]
              const cleanText = assistantText.replace(/```customlogic\s*\n[\s\S]*?\n```/g, "").trim()
              ctx.onCustomLogicProposed(proposed)
              setMessages((prev) => {
                const updated = [...prev]
                const last    = updated[updated.length - 1]
                if (last?.role === "assistant") updated[updated.length - 1] = { ...last, text: cleanText }
                return updated
              })
            } catch { /* JSON malformed — just show text as-is; agent should retry */ }
          }
        }

        if (event.type === "done" && ctx.mode === "analysis") {
          let displayText = assistantText

          const filterMatch = displayText.match(/```filter\s*\n([\s\S]*?)\n```/)
          if (filterMatch && ctx.onFilterUpdate) {
            try {
              const update = JSON.parse(filterMatch[1]) as FilterUpdate
              ctx.onFilterUpdate(update)
              displayText = displayText.replace(/```filter\s*\n[\s\S]*?\n```/g, "").trim()
            } catch { /* malformed block — ignore */ }
          }

          const presetMatch = displayText.match(/```save_preset\s*\n([\s\S]*?)\n```/)
          if (presetMatch && ctx.onSavePreset) {
            try {
              const { name, ...filters } = JSON.parse(presetMatch[1]) as { name: string } & FilterPreset["filters"]
              ctx.onSavePreset(name, filters)
              displayText = displayText.replace(/```save_preset\s*\n[\s\S]*?\n```/g, "").trim()
            } catch { /* malformed block — ignore */ }
          }

          const switchMatch = displayText.match(/```switch_config\s*\n([\s\S]*?)\n```/)
          if (switchMatch && ctx.onSwitchConfig) {
            try {
              const { name } = JSON.parse(switchMatch[1]) as { name: string }
              if (name) {
                ctx.onSwitchConfig(name)
                displayText = displayText.replace(/```switch_config\s*\n[\s\S]*?\n```/g, "").trim()
              }
            } catch { /* malformed block — ignore */ }
          }

          const overrideMatch = displayText.match(/```suggest_overrides\s*\n([\s\S]*?)\n```/)
          if (overrideMatch && ctx.onSuggestOverrides) {
            try {
              const raw = JSON.parse(overrideMatch[1]) as { add?: Record<string, unknown>; remove?: unknown[] }
              // Validate shape before handing it to the UI — an agent-hallucinated
              // non-string value here would otherwise crash the whole dashboard when
              // rendered as a React child (see components/bva-dashboard.tsx's banner).
              const add: Record<string, string> = {}
              for (const [k, v] of Object.entries(raw.add ?? {})) {
                if (typeof v === "string") add[k] = v
              }
              const remove = Array.isArray(raw.remove) ? raw.remove.filter((v): v is string => typeof v === "string") : []
              if (Object.keys(add).length > 0 || remove.length > 0) {
                ctx.onSuggestOverrides({ add: Object.keys(add).length ? add : undefined, remove: remove.length ? remove : undefined })
                displayText = displayText.replace(/```suggest_overrides\s*\n[\s\S]*?\n```/g, "").trim()
              }
            } catch { /* malformed block — ignore */ }
          }

          const commentaryMatch = displayText.match(/```suggest_commentary\s*\n([\s\S]*?)\n```/)
          if (commentaryMatch && ctx.onSuggestCommentary) {
            try {
              const rows = JSON.parse(commentaryMatch[1]) as Array<{ category: string; text: string }>
              ctx.onSuggestCommentary(rows)
              displayText = displayText.replace(/```suggest_commentary\s*\n[\s\S]*?\n```/g, "").trim()
            } catch { /* malformed block — ignore */ }
          }

          if (displayText !== assistantText) {
            setMessages((prev) => {
              const updated = [...prev]
              const last    = updated[updated.length - 1]
              if (last?.role === "assistant") updated[updated.length - 1] = { ...last, text: displayText }
              return updated
            })
          }
        }

        if (event.type === "error") {
          setMessages((prev) => {
            const updated = [...prev]
            const last    = updated[updated.length - 1]
            if (last?.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                text: last.text + (last.text ? "\n\n" : "") + `**Error:** ${event.message}`,
              }
            }
            return updated
          })
          // A turn that ends in a real error leaves the underlying thread in an
          // unknown state — continuing it (via the sessionId/resume mechanism)
          // is how a single bad turn turned into a run of repeated failures in
          // the past, with no visible signal to the user that anything was
          // wrong. Drop the thread pointer (not the visible transcript) so the
          // next message always starts a fresh thread instead of resuming a
          // possibly-broken one.
          sessionIdRef.current = null
          clearSessionId()
        }
      }

      // Both endpoints (chat and setup) run CoCo as a fire-and-forget background
      // job and return { jobId } immediately — each GET poll request stays open
      // < 1 ms so the SPCS proxy's ~90-second response-body limit can never fire,
      // no matter how long the underlying CoCo turn takes. Individual poll
      // requests are retried a few times on transient failure so a single
      // dropped connection doesn't kill the whole turn.
      //
      // This loop is the client-side half of the job-id/polling pattern (see
      // lib/job-store.ts for the server side): grab the jobId once, then keep
      // asking "any new events for this job since index `after`?" once a
      // second until the server reports done=true.
      const { jobId } = await res.json() as { jobId: string }
      let after = 0

      while (true) {
        if (controller.signal.aborted) break

        const pollRes = await fetchWithRetry(
          `${endpoint}?jobId=${encodeURIComponent(jobId)}&after=${after}`,
          controller.signal,
        )

        const { events, done } = await pollRes.json() as { events: SseEvent[]; done: boolean }
        for (const ev of events) { processEvent(ev); after++ }
        if (done) break

        // 1 s between polls; respects abort signal
        await sleepAbortable(1_000, controller.signal)
      }
    } catch (e) {
      const aborted = e instanceof DOMException && e.name === "AbortError"
      if (!aborted) {
        // Same reasoning as the SSE "error" branch above: a turn that threw
        // (e.g. the initial POST failed, or polling exhausted its retries)
        // leaves the thread in an unknown state — don't resume it next time.
        sessionIdRef.current = null
        clearSessionId()
      }
      setMessages((prev) => {
        const updated = [...prev]
        const last    = updated[updated.length - 1]
        if (last?.role === "assistant") {
          if (aborted) {
            // User cancelled — keep whatever streamed so far, add a subtle note
            const note = "_Stopped._"
            updated[updated.length - 1] = {
              ...last,
              text: last.text ? `${last.text}\n\n${note}` : note,
            }
          } else {
            updated[updated.length - 1] = {
              ...last,
              text: `**Error:** ${e instanceof Error ? e.message : String(e)}`,
            }
          }
        }
        return updated
      })
    } finally {
      const elapsedMs = Date.now() - assistantStartedAt
      setMessages((prev) => {
        const updated = [...prev]
        const last = updated[updated.length - 1]
        if (last?.role === "assistant") updated[updated.length - 1] = { ...last, elapsedMs }
        return updated
      })
      abortRef.current = null
      setBusy(false)
    }
  }, [busy])

  // ── stop ────────────────────────────────────────────────────────────────

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  return (
    <ChatContext.Provider value={{
      messages,
      busy,
      isOpen,
      setIsOpen,
      sendMessage,
      stop,
      clearChat,
      setPageContext,
      pageMode,
      existingConfig,
      onConfigAccepted,
      agentDraft,
      applyAgentDraft,
      clearAgentDraft,
      dashboardResetSignal,
      resetDashboard,
    }}>
      {children}
    </ChatContext.Provider>
  )
}
