"use client"

import { useState } from "react"
import { CheckCircle2 } from "lucide-react"

// ─── Types ────────────────────────────────────────────────────────────────────
//
// This widget renders a multiple-choice question that the Cortex agent asks
// mid-conversation (e.g. "Which fiscal calendar do you use?"). The agent emits
// a ```question-options fenced code block whose JSON body matches
// QuestionOptionsPayload; components/cortex-chat.tsx parses that block and
// mounts this component in its place.

export interface OptionEntry {
  label: string
  value: string
  description?: string
}

export interface QuestionOptionsPayload {
  question: string
  options: OptionEntry[]
}

interface Props {
  data: QuestionOptionsPayload
  onSelect: (message: string) => void
}

// ─── Component ────────────────────────────────────────────────────────────────

export function QuestionOptions({ data, onSelect }: Props) {
  // Once an option is picked, `selected` locks the widget so it can't be
  // re-answered — the click has already been sent to the agent as a chat
  // message, so changing the answer here would just show a stale UI state.
  const [selected, setSelected] = useState<string | null>(null)

  const handleSelect = (opt: OptionEntry) => {
    if (selected !== null) return
    setSelected(opt.value)
    // The user's "answer" to the agent is just a regular chat message —
    // there's no separate structured-response API. Sending `opt.value` back
    // through the normal onSend/sendMessage pipeline lets the agent read it
    // like any other user turn.
    onSelect(opt.value)
  }

  return (
    <div className="rounded-xl border border-border bg-muted/20 p-3 my-2 space-y-2">
      <p className="text-xs font-medium text-foreground">{data.question}</p>

      <div className="space-y-1.5">
        {data.options.map((opt) => {
          const isSelected = selected === opt.value
          return (
            <button
              key={opt.value}
              disabled={selected !== null}
              onClick={() => handleSelect(opt)}
              className={[
                "w-full text-left rounded-lg border px-3 py-2 transition-all",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-primary",
                selected !== null
                  ? isSelected
                    ? "border-primary bg-primary/5 cursor-default"
                    : "border-border bg-muted/5 opacity-40 cursor-default"
                  : "border-border bg-background hover:border-primary/50 hover:bg-muted/30 cursor-pointer",
              ].join(" ")}
            >
              <div className="flex items-start gap-2">
                <div className="mt-0.5 shrink-0">
                  {isSelected
                    ? <CheckCircle2 className="h-3.5 w-3.5 text-primary" />
                    : <div className="h-3.5 w-3.5 rounded-full border border-muted-foreground/40" />}
                </div>
                <div>
                  <span className="text-xs font-medium">{opt.label}</span>
                  {opt.description && (
                    <p className="text-[10px] text-muted-foreground mt-0.5">{opt.description}</p>
                  )}
                </div>
              </div>
            </button>
          )
        })}
      </div>

      {selected !== null && (
        <p className="text-[10px] text-muted-foreground">Response sent.</p>
      )}
    </div>
  )
}
