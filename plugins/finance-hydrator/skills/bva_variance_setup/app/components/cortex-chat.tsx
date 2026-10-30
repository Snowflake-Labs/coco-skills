"use client"

import { useState, useRef, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import { useQueryClient } from "@tanstack/react-query"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Bot, User, Loader2, CheckCircle2, Sparkles, X, ArrowUp, RotateCcw, ChevronRight, Square, Trash2 } from "lucide-react"
import { useChatContext } from "@/lib/chat-context"
import type { BvaConfig } from "@/lib/config"
import { CandidatePicker } from "@/components/chat/candidate-picker"
import type { CandidatesPayload } from "@/components/chat/candidate-picker"
import { QuestionOptions } from "@/components/chat/question-options"
import type { QuestionOptionsPayload } from "@/components/chat/question-options"
import { ColumnConfirm } from "@/components/chat/column-confirm"
import type { ColumnConfirmPayload } from "@/components/chat/column-confirm"

// Re-export for pages that still type-check against this
export type { ChatViewState } from "@/lib/chat-context"

// ─── Suggested prompts ────────────────────────────────────────────────────────

const SETUP_PROMPTS = [
  { label: "Find my tables",     prompt: "I need to set up BvA. Please search my Snowflake account for financial tables and suggest the right ones." },
  { label: "I know my tables",   prompt: "I know which tables to use. Let me tell you what they are and you can help map the columns." },
  { label: "Review my config",   prompt: "Review my saved config and check for any issues with the setup or column mappings." },
  { label: "Fix $0 actuals",     prompt: "I'm seeing $0 actuals — help me diagnose what's wrong with the join or column mapping." },
]

const ANALYSIS_PROMPTS = [
  { label: "Top variance drivers",   prompt: "What are the top 3 variance drivers this period? Show $ and % for each." },
  { label: "Over-budget categories", prompt: "Which expense categories are most over budget? Rank by absolute $ variance." },
  { label: "Cost center summary",    prompt: "Summarize budget vs. actuals by department or cost center. Highlight the biggest gaps." },
  { label: "Trend vs last quarter",  prompt: "How does this quarter's total actuals compare to the previous quarter? Show the trend." },
  { label: "Generate AI Commentary", prompt: "Generate AI commentary for the category with the largest variance this period." },
]

// ─── Markdown renderer ────────────────────────────────────────────────────────

// `autofill` blocks are applied by a single top-level effect in CortexChatPanel
// (see applyAutofillFromMessages) — NOT from inside the render tree. Applying
// them via a render-embedded effect coupled them to ReactMarkdown re-parsing on
// every streaming update, which caused a "Maximum update depth exceeded" loop.

/**
 * Extract every `autofill` fenced block from the assistant messages, merge them
 * in order (later blocks win), and return the combined partial. Returns null
 * when there are no complete/parseable blocks.
 *
 * `autofill` blocks are the agent's way of pre-populating the Settings wizard
 * form directly, in addition to (or instead of) the interactive widgets below
 * — e.g. after free-text back-and-forth the agent can just emit
 * ```autofill\n{"actualsDateCol": "txn_date"}\n``` and this function picks it
 * up. Scanning ALL messages (not just the latest) and merging in order lets a
 * later turn patch/override an earlier one's autofill without needing to
 * repeat every field.
 */
function extractAutofill(messages: { role: string; text?: string }[]): Partial<BvaConfig> | null {
  const blocks: Partial<BvaConfig>[] = []
  const re = /```autofill\s*\n([\s\S]*?)\n```/g
  for (const m of messages) {
    if (m.role !== "assistant" || !m.text) continue
    let match: RegExpExecArray | null
    while ((match = re.exec(m.text)) !== null) {
      try { blocks.push(JSON.parse(match[1].trim()) as Partial<BvaConfig>) } catch { /* incomplete/streaming — skip */ }
    }
    re.lastIndex = 0
  }
  if (blocks.length === 0) return null
  return Object.assign({}, ...blocks) as Partial<BvaConfig>
}

/** Live/frozen elapsed time label for assistant messages. */
function ElapsedTimer({ startedAt, finalMs }: { startedAt?: number; finalMs?: number }) {
  const [tick, setTick] = useState(0)
  const active = finalMs === undefined && startedAt !== undefined
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setTick((n) => n + 1), 100)
    return () => clearInterval(id)
  }, [active])
  const ms = finalMs ?? (startedAt ? Date.now() - startedAt : undefined)
  if (ms === undefined) return null
  return (
    <span className="text-[10px] text-muted-foreground/60 tabular-nums">
      {(ms / 1000).toFixed(1)}s
    </span>
  )
}

/** LLMs occasionally emit trailing commas in JSON. Strip them before parsing.
 *  Used whenever we parse the JSON body of one of the agent's widget fences
 *  (candidates, question-options, column-confirm) — a strict JSON.parse would
 *  throw on a stray trailing comma and silently fail the whole widget. */
function parseJson<T>(raw: string): T {
  return JSON.parse(raw.trim().replace(/,(\s*[}\]])/g, "$1")) as T
}

// Split message text on ```candidates ... ``` fences BEFORE react-markdown sees them.
// This avoids parse failures inside the code renderer and handles partial JSON during streaming.
const CANDIDATES_FENCE_RE = /```(?:candidates|json)([\s\S]*?)```/g
const CANDIDATE_KEYS: Array<keyof CandidatesPayload> = ["gl", "budget", "cc", "ledger"]

type MsgSegment =
  | { type: "text"; content: string }
  | { type: "candidates"; content: string }

/**
 * Splits raw assistant message text into alternating "text" and "candidates"
 * segments. Candidates blocks are handled specially (pulled out before
 * react-markdown ever sees them) rather than through the ```lang code-block
 * mechanism the other widgets use (see MdMessage's `code` renderer below),
 * because the agent sometimes emits this block under a bare ```json fence
 * instead of ```candidates — react-markdown's code renderer only gets the
 * className, so it can't distinguish "this json block happens to be
 * candidates data" from any other JSON the agent might print. Regex-scanning
 * the raw text first, then trying to JSON.parse + shape-check each match (see
 * CANDIDATE_KEYS), sidesteps that ambiguity — and gracefully leaves anything
 * that fails to parse (e.g. because the LLM hasn't finished streaming it yet)
 * as ordinary "text" until it becomes valid on a later render.
 */
function splitSegments(text: string): MsgSegment[] {
  const parts: MsgSegment[] = []
  let last = 0
  for (const m of text.matchAll(CANDIDATES_FENCE_RE)) {
    const raw = m[1]
    try {
      const data = parseJson<CandidatesPayload>(raw)
      if (CANDIDATE_KEYS.some((k) => Array.isArray(data[k]))) {
        if (m.index! > last) parts.push({ type: "text", content: text.slice(last, m.index) })
        parts.push({ type: "candidates", content: raw })
        last = m.index! + m[0].length
      }
    } catch { /* not a candidates block — leave as text */ }
  }
  if (last < text.length) parts.push({ type: "text", content: text.slice(last) })
  return parts.length ? parts : [{ type: "text", content: text }]
}

function MdMessage({ text, onSend, onAutofill }: { text: string; onSend: (msg: string) => void; onAutofill?: (partial: Partial<BvaConfig>) => void }) {
  // Fix missing spaces between sentences — streaming chunks sometimes concatenate
  // without a space, e.g. "right now.Good" or "app.Let". Only applies when a
  // lowercase/digit precedes the punctuation, so identifiers like
  // YOUR_DB.YOUR_SCHEMA (uppercase throughout) and U.S.A. are unaffected.
  const normalized = text.replace(/([a-z0-9])([.!?])([A-Z])/g, "$1$2 $3")
  // First pass: pull out ```candidates (or bare ```json) blocks so they never
  // reach react-markdown as a generic code block — see splitSegments() above.
  const segments = splitSegments(normalized)
  return (
    <div className="[overflow-wrap:anywhere] break-words">
      {segments.map((seg, i) => {
        if (seg.type === "candidates") {
          try {
            const data = parseJson<CandidatesPayload>(seg.content)
            return <CandidatePicker key={i} data={data} onConfirm={onSend} onAutofill={onAutofill} />
          } catch { /* fall through to text */ }
        }
        return (
          <ReactMarkdown
            key={i}
            remarkPlugins={[remarkGfm]}
            components={{
              p:          ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
              ul:         ({ children }) => <ul className="list-disc pl-4 mb-2 space-y-0.5">{children}</ul>,
              ol:         ({ children }) => <ol className="list-decimal pl-4 mb-2 space-y-0.5">{children}</ol>,
              li:         ({ children }) => <li className="leading-relaxed">{children}</li>,
              h1:         ({ children }) => <h1 className="text-sm font-semibold mb-1.5 mt-2">{children}</h1>,
              h2:         ({ children }) => <h2 className="text-sm font-semibold mb-1.5 mt-2">{children}</h2>,
              h3:         ({ children }) => <h3 className="text-xs font-semibold mb-1 mt-1.5">{children}</h3>,
              strong:     ({ children }) => <strong className="font-semibold">{children}</strong>,
              table:      ({ children }) => <div className="overflow-x-auto mb-2 -mx-1"><table className="text-xs border-collapse w-full">{children}</table></div>,
              th:         ({ children }) => <th className="border border-border px-2 py-1 bg-muted/60 font-medium text-left whitespace-nowrap">{children}</th>,
              td:         ({ children }) => <td className="border border-border px-2 py-1">{children}</td>,
              // Second pass: for any fenced code block react-markdown DOES
              // parse normally (```question-options, ```autofill,
              // ```column-confirm), the fence's language tag arrives here as
              // `className="language-<name>"`. Each of the three interactive
              // widgets — plus the invisible autofill block — is triggered by
              // matching that className rather than a raw regex, since these
              // blocks parse fine as ordinary code fences (unlike the
              // candidates block, which the agent sometimes emits under a
              // generic ```json tag — see splitSegments() above for why that
              // one needs special pre-processing instead).
              code:       ({ className, children }) => {
                if (className === "language-question-options") {
                  try {
                    const data = parseJson<QuestionOptionsPayload>(String(children))
                    return <QuestionOptions data={data} onSelect={onSend} />
                  } catch { /* fall through */ }
                }
                if (className === "language-autofill") {
                  // Rendered invisibly; the actual form autofill is applied by the
                  // top-level effect in CortexChatPanel (extractAutofill), not here.
                  return null
                }
                if (className === "language-column-confirm") {
                  try {
                    const data = parseJson<ColumnConfirmPayload>(String(children))
                    return <ColumnConfirm data={data} onConfirm={onSend} onAutofill={onAutofill} />
                  } catch { /* fall through */ }
                }
                return <code className="bg-muted/30 border border-border/50 rounded px-1 py-0.5 text-[0.8em] font-mono [overflow-wrap:anywhere]">{children}</code>
              },
              pre:        ({ children }) => <pre className="bg-muted/40 rounded-lg p-2.5 text-[0.8em] overflow-x-auto mb-2 font-mono whitespace-pre-wrap [overflow-wrap:anywhere]">{children}</pre>,
              blockquote: ({ children }) => <blockquote className="border-l-2 border-primary/40 pl-3 text-muted-foreground italic mb-2">{children}</blockquote>,
            }}
          >
            {seg.content}
          </ReactMarkdown>
        )
      })}
    </div>
  )
}

// ─── Chat panel (always mounted in layout) ────────────────────────────────────

/**
 * Invalidate every config-derived query so the dashboard + analysis agent
 * re-read the freshly saved config (new tables/columns) instead of the stale
 * cached one. Called after the chat's "Accept & Save" writes the config.
 */
async function invalidateConfigCaches(queryClient: ReturnType<typeof useQueryClient>) {
  await Promise.all(
    ["setup-config", "filters", "bva", "breakdown", "time", "query"].map((key) =>
      queryClient.invalidateQueries({ queryKey: [key] })
    )
  )
}

export function CortexChatPanel() {
  // `messages` and `busy` are driven by lib/chat-context.tsx's polling loop:
  // sendMessage() kicks off a background agent job and the context polls
  // lib/job-store.ts's job (see that file's header for the full job-id +
  // polling design) roughly once a second, appending each new event onto
  // `messages` as it arrives. This component never talks to the job store
  // directly — it only ever re-renders in response to `messages`/`busy`
  // changing, which is what makes text look like it's "streaming" in below.
  const {
    messages, busy, isOpen, setIsOpen,
    sendMessage, stop, clearChat,
    pageMode, existingConfig, onConfigAccepted,
    applyAgentDraft, clearAgentDraft, resetDashboard,
  } = useChatContext()

  const [input,  setInput]  = useState("")
  const [saving, setSaving] = useState(false)
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(new Set())
  const toggleSteps = (i: number) => setExpandedSteps((prev) => {
    const next = new Set(prev)
    if (next.has(i)) next.delete(i); else next.add(i)
    return next
  })
  const scrollRef = useRef<HTMLDivElement>(null)
  const router = useRouter()
  const queryClient = useQueryClient()

  // ── Resizable panel ────────────────────────────────────────────────────────
  const PANEL_WIDTH_KEY = "bva_chat_panel_width"
  const MIN_W = 320
  const MAX_W = 900
  const [panelWidth, setPanelWidth] = useState<number>(() => {
    if (typeof window === "undefined") return 480
    const stored = parseInt(localStorage.getItem(PANEL_WIDTH_KEY) ?? "", 10)
    return isNaN(stored) ? 480 : Math.min(Math.max(stored, MIN_W), MAX_W)
  })

  const dragStartX  = useRef(0)
  const dragStartW  = useRef(0)

  // Keep a ref to latest width so the mouseup closure can persist it
  const panelWidthRef = useRef(panelWidth)
  useEffect(() => { panelWidthRef.current = panelWidth }, [panelWidth])

  const handleResizeMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragStartX.current   = e.clientX
    dragStartW.current   = panelWidthRef.current
    document.body.style.cursor     = "col-resize"
    document.body.style.userSelect = "none"
    const up = () => {
      document.body.style.cursor     = ""
      document.body.style.userSelect = ""
      try { localStorage.setItem(PANEL_WIDTH_KEY, String(panelWidthRef.current)) } catch { /* quota */ }
      document.removeEventListener("mousemove", move)
      document.removeEventListener("mouseup",   up)
    }
    const move = (ev: MouseEvent) => {
      const delta = dragStartX.current - ev.clientX
      const next  = Math.min(Math.max(dragStartW.current + delta, MIN_W), MAX_W)
      setPanelWidth(next)
    }
    document.addEventListener("mousemove", move)
    document.addEventListener("mouseup",   up)
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  // Pre-warm the warehouse when the chat panel opens so the first chat message
  // doesn't hit a cold warehouse resume delay.
  useEffect(() => {
    if (!isOpen) return
    fetch("/api/agent/warmup", { method: "POST" }).catch(() => {})
  }, [isOpen])

  // Apply `autofill` blocks from the agent to the form — ONCE per unique merged
  // content. Deduping on the JSON hash (plus the deep-idempotent applyAgentDraft)
  // guarantees this can never sustain a render loop, even while messages stream.
  // `messages` (and thus the streamed text inside it) mutates on every chunk the
  // background job emits (see lib/chat-context.tsx's poll loop, which appends
  // events as they arrive), so this effect re-runs on every chunk too — the
  // dedup key is what keeps applyAgentDraft() from firing repeatedly for the
  // same content as it streams in byte by byte.
  const appliedAutofillRef = useRef<string>("")
  useEffect(() => {
    const merged = extractAutofill(messages)
    if (!merged) return
    const key = JSON.stringify(merged)
    if (key === appliedAutofillRef.current) return
    appliedAutofillRef.current = key
    applyAgentDraft(merged)
  }, [messages, applyAgentDraft])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const text = input.trim()
    if (!text || busy) return
    setInput("")
    // Reset textarea height after send
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto"
    }
    sendMessage(text)
  }

  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Sync height whenever input changes (covers programmatic setInput calls too)
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = "auto"
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`
  }, [input])

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e as unknown as React.FormEvent)
    }
  }

  const [resetting, setResetting] = useState(false)
  const handleResetConfig = async () => {
    if (!window.confirm("Delete the saved config and start a clean setup? This cannot be undone (though history is preserved).")) return
    setResetting(true)
    try {
      const res = await fetch("/api/setup/config", { method: "DELETE" })
      if (!res.ok) throw new Error(await res.text())
      await invalidateConfigCaches(queryClient)
      // Also clear any localStorage draft so the wizard starts truly blank
      try { localStorage.removeItem("bva_config_draft") } catch { /* ignore */ }
      clearChat()
      // Reset agentDraft and the wizard form to blank
      clearAgentDraft()
      onConfigAccepted?.({ configName: "default", sourceType: "single_table", singleTable: "" })
      // Reset dashboard filters/charts on the main pages + expense breakdown
      resetDashboard()
    } catch (err) {
      alert(`Reset failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setResetting(false)
    }
  }

  const handleAcceptConfig = async (cfg: BvaConfig) => {
    setSaving(true)
    try {
      const res = await fetch("/api/setup/config", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...cfg, configName: cfg.configName || "default" }),
      })
      if (!res.ok) throw new Error(await res.text())
      onConfigAccepted?.(cfg)   // updates wizard state if on settings page
      // Refresh all config-derived data so the dashboard + analysis agent pick
      // up the new tables/columns instead of the stale cached config.
      await invalidateConfigCaches(queryClient)
      router.push("/")          // navigate to dashboard after saving
    } catch (err) {
      alert(`Save failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setSaving(false)
    }
  }

  const handleReviewInWizard = (cfg: BvaConfig) => {
    // Persist the proposed config so the wizard can pick it up even when the
    // user is on a different page (onConfigAccepted is only wired up on Settings).
    try {
      localStorage.setItem("bva_pending_review", JSON.stringify({ config: cfg, savedAt: new Date().toISOString() }))
    } catch { /* storage full — fall back to callback below */ }
    onConfigAccepted?.(cfg)    // pre-populate wizard state if already on settings
    router.push("/settings")   // always navigate to settings
  }

  const prompts     = pageMode === "setup" ? SETUP_PROMPTS : ANALYSIS_PROMPTS
  const placeholder = busy ? "Working…" : pageMode === "setup" ? "Ask about BvA setup…" : "Ask about BvA data…"

  if (!isOpen) return null

  return (
    <aside
      className="relative shrink-0 flex flex-col border-l bg-background"
      style={{ width: panelWidth }}
    >
      {/* Drag-to-resize handle — left edge */}
      <div
        onMouseDown={handleResizeMouseDown}
        className="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize z-10 group"
        title="Drag to resize"
      >
        {/* Visual indicator — thickens on hover/drag */}
        <div className="absolute left-0 top-0 bottom-0 w-px bg-border group-hover:w-1 group-hover:bg-primary/40 transition-all" />
      </div>

      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b shrink-0">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" />
          <span className="font-semibold text-sm">Cortex Chat</span>
          {pageMode === "setup" && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-medium">Setup</span>
          )}
        </div>
        <div className="flex items-center gap-0.5">
          <button
            onClick={clearChat}
            title="Clear conversation"
            className="p-1.5 rounded-md hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
          {pageMode === "setup" && (
            <button
              onClick={handleResetConfig}
              disabled={resetting}
              title="Reset config — delete saved config and start a fresh discovery"
              className="p-1.5 rounded-md hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors text-muted-foreground hover:text-destructive disabled:opacity-40"
            >
              {resetting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            </button>
          )}
          <button
            onClick={() => setIsOpen(false)}
            title="Close"
            className="p-1.5 rounded-md hover:bg-muted transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4 min-h-0">

        {/* Empty state */}
        {messages.length === 0 && (
          <div className="space-y-4 pt-2">
            <div className="text-center pb-1">
              <div className="h-10 w-10 rounded-full bg-primary/10 flex items-center justify-center mx-auto mb-3">
                <Bot className="h-5 w-5 text-primary" />
              </div>
              <p className="text-sm font-medium">
                {pageMode === "setup" ? "BvA Setup Assistant" : "Cortex Chat"}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {pageMode === "setup"
                  ? (existingConfig
                    ? "You have a saved config. I can review or update it."
                    : "I can search your Snowflake account and configure BvA.")
                  : "Powered by Snowflake Cortex — ask anything about your BvA data."}
              </p>
            </div>

            <div className="space-y-2">
              {prompts.map((p) => (
                <button
                  key={p.label}
                  onClick={() => setInput(p.prompt)}
                  disabled={busy}
                  className="w-full text-left px-3 py-2.5 rounded-lg border text-sm hover:bg-muted transition-colors disabled:opacity-40 flex items-start gap-2"
                >
                  <Sparkles className="h-3.5 w-3.5 text-primary shrink-0 mt-0.5" />
                  <span className="font-medium text-xs">{p.label}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Thread */}
        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-2 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            {msg.role === "assistant" && (
              <div className="h-6 w-6 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-0.5">
                <Bot className="h-3.5 w-3.5 text-primary" />
              </div>
            )}

            <div className={`max-w-[90%] min-w-0 space-y-2 ${msg.role === "user" ? "order-first" : ""}`}>
              {/* Tool steps — collapsed single line, expandable to show SQL + results.
                  Each entry in msg.toolSteps is one Snowflake query the agent ran while
                  producing this message (e.g. discovering tables, checking column data);
                  they're appended as job-store events arrive, same as the message text. */}
              {msg.toolSteps && msg.toolSteps.length > 0 && (() => {
                const doneCount = msg.toolSteps.filter((s) => s.done).length
                const isActive  = msg.toolSteps.some((s) => !s.done) && i === messages.length - 1 && busy
                if (!isActive && doneCount === 0) return null
                const expanded = expandedSteps.has(i)
                return (
                  <div className="space-y-1">
                    <button
                      type="button"
                      onClick={() => toggleSteps(i)}
                      className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {isActive
                        ? <Loader2 className="h-3 w-3 animate-spin shrink-0" />
                        : <CheckCircle2 className="h-3 w-3 text-green-500 shrink-0" />}
                      <span>
                        {isActive
                          ? `Querying Snowflake${doneCount > 0 ? ` (${doneCount} done)` : "…"}`
                          : `${doneCount} ${doneCount === 1 ? "query" : "queries"} run`}
                      </span>
                      <ChevronRight className={`h-3 w-3 shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`} />
                    </button>

                    {expanded && (
                      <div className="space-y-2 pl-1">
                        {msg.toolSteps.map((step, si) => (
                          <div key={si} className="rounded-lg border bg-muted/20 p-2 space-y-1.5 text-xs">
                            <div className="flex items-center gap-1.5 font-medium text-muted-foreground">
                              {step.done
                                ? <CheckCircle2 className="h-3 w-3 text-green-500 shrink-0" />
                                : <Loader2 className="h-3 w-3 animate-spin shrink-0" />}
                              <span>Query {si + 1}</span>
                            </div>
                            {step.query && (
                              <pre className="whitespace-pre-wrap break-words rounded-md bg-background/80 border p-2 font-mono text-[11px] leading-relaxed max-h-64 overflow-y-auto">
                                {step.query}
                              </pre>
                            )}
                            {step.result && (
                              <details className="group">
                                <summary className="cursor-pointer text-muted-foreground hover:text-foreground select-none">
                                  Result
                                </summary>
                                <pre className="whitespace-pre-wrap break-words rounded-md bg-background/80 border p-2 mt-1 font-mono text-[11px] leading-relaxed max-h-64 overflow-y-auto">
                                  {step.result}
                                </pre>
                              </details>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })()}

              {/* Message bubble */}
              {msg.text && (
                <div className={`rounded-2xl px-3 py-2.5 text-sm [overflow-wrap:anywhere] break-words min-w-0 overflow-hidden ${
                  msg.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted/40 border"
                }`}>
                  {msg.role === "user"
                    ? <span>{msg.text}</span>
                    : <MdMessage text={msg.text} onSend={sendMessage} onAutofill={applyAgentDraft} />}
                </div>
              )}

              {/* Config proposal card — rendered when the agent has finished setup and
                  returned a complete BvaConfig (msg.proposal), distinct from the
                  in-conversation widgets above which only collect partial answers.
                  The user must explicitly Accept/Review here; nothing is saved
                  automatically just because the agent produced a proposal. */}
              {msg.proposal && (
                <div className="rounded-xl border border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/20 p-3 space-y-2.5">
                  <div className="flex items-center gap-2">
                    <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
                    <p className="text-xs font-medium text-green-800 dark:text-green-200">Configuration ready</p>
                  </div>
                  <p className="text-xs text-muted-foreground">{msg.proposal.summary}</p>
                  <ConfigSummary config={msg.proposal.config} />
                  <div className="flex gap-2 pt-0.5">
                    <button
                      onClick={() => handleAcceptConfig(msg.proposal!.config)}
                      disabled={saving}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-medium hover:opacity-90 disabled:opacity-60 transition-opacity"
                    >
                      {saving && <Loader2 className="h-3 w-3 animate-spin" />}
                      Accept & Save
                    </button>
                    <button
                      onClick={() => handleReviewInWizard(msg.proposal!.config)}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs hover:bg-muted transition-colors"
                    >
                      Review in wizard
                      <ChevronRight className="h-3 w-3" />
                    </button>
                  </div>
                </div>
              )}

              {/* Thinking indicator */}
              {msg.role === "assistant" && !msg.text && !msg.proposal && i === messages.length - 1 && busy && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground py-1">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Working…
                </div>
              )}

              {/* Elapsed time */}
              {msg.role === "assistant" && (msg.startedAt || msg.elapsedMs !== undefined) && (
                <div className="flex justify-end">
                  <ElapsedTimer
                    startedAt={msg.startedAt}
                    finalMs={msg.elapsedMs}
                  />
                </div>
              )}
            </div>

            {msg.role === "user" && (
              <div className="h-6 w-6 rounded-full bg-muted flex items-center justify-center shrink-0 mt-0.5">
                <User className="h-3.5 w-3.5" />
              </div>
            )}
          </div>
        ))}

        <div ref={scrollRef} />
      </div>

      {/* Input */}
      <div className="border-t px-3 py-2.5 shrink-0">
        <form onSubmit={handleSubmit}>
          <div className="flex items-end gap-2 rounded-xl border bg-muted/20 px-3 py-1.5">
            <textarea
              ref={textareaRef}
              rows={1}
              value={input}
              onChange={handleTextareaChange}
              onKeyDown={handleKeyDown}
              disabled={busy}
              placeholder={placeholder}
              className="flex-1 bg-transparent text-sm outline-none disabled:opacity-50 py-1 resize-none overflow-y-auto leading-snug"
            />
            {busy ? (
              <button
                type="button"
                onClick={stop}
                className="h-7 w-7 rounded-full bg-foreground flex items-center justify-center shrink-0 hover:opacity-80 transition-opacity mb-0.5"
                aria-label="Stop"
                title="Stop response"
              >
                <Square className="h-3 w-3 text-background fill-background" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                className="h-7 w-7 rounded-full bg-primary flex items-center justify-center shrink-0 hover:opacity-90 disabled:opacity-40 transition-opacity mb-0.5"
                aria-label="Send"
              >
                <ArrowUp className="h-3.5 w-3.5 text-primary-foreground" />
              </button>
            )}
          </div>
          <p className="text-[10px] text-muted-foreground/50 mt-1 pl-1">
            {busy ? "Generating… click ■ to stop" : "Enter to send · Shift+Enter for new line"}
          </p>
        </form>
      </div>
    </aside>
  )
}

// ─── Config summary strip ─────────────────────────────────────────────────────

function ConfigSummary({ config }: { config: BvaConfig }) {
  const items = [
    config.actualsTable       && { label: "Actuals",      value: shortFqn(config.actualsTable) },
    config.budgetTable        && { label: "Budget",       value: shortFqn(config.budgetTable) },
    config.costCenterTable    && { label: "Cost centers", value: shortFqn(config.costCenterTable) },
    config.ledgerMappingTable && { label: "Ledger map",   value: shortFqn(config.ledgerMappingTable) },
    config.categorySource     && { label: "Categories",   value: config.categorySource.replace(/_/g, " ") },
  ].filter(Boolean) as Array<{ label: string; value: string }>

  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
      {items.map((item) => (
        <div key={item.label} className="flex items-start gap-1">
          <span className="text-muted-foreground shrink-0">{item.label}:</span>
          <span className="font-mono truncate" title={item.value}>{item.value}</span>
        </div>
      ))}
    </div>
  )
}

function shortFqn(fqn: string): string {
  const parts = fqn.split(".")
  return parts[parts.length - 1] ?? fqn
}
