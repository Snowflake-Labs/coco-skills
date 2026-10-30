"use client"

/**
 * Orchestrates the multi-step Settings/Setup wizard: an 8-step flow that
 * walks an admin through pointing the dashboard at their Snowflake tables
 * (source tables, column mapping, fiscal calendar, data-quality fixes,
 * category remapping, groupings, SQL preview/validation, and final save).
 * This component owns the wizard's shared state (the in-progress BvaConfig
 * draft, which step is active, and which steps are unlocked) and renders
 * whichever step component matches the current step; each step component
 * (StepTables, StepColumns, etc.) only knows about `config`/`update`/`nav`.
 *
 * It also reconciles three different sources that can populate the config:
 * a config already saved in Snowflake, a local unsaved draft (so a refresh
 * mid-wizard doesn't lose progress), and a config proposed by the AI Setup
 * chat assistant either "live" (agentDraft) or as an explicit "review this"
 * hand-off (bva_pending_review). See the effect below that reads `existing`
 * for the priority order between these.
 */

import { useEffect, useState, useCallback, useRef } from "react"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Check, ChevronRight, Loader2, Sparkles } from "lucide-react"
import { getJson } from "@/lib/format"
import type { BvaConfig } from "@/lib/config"
import { StepTables } from "@/components/setup/step-tables"
import { StepColumns } from "@/components/setup/step-columns"
import { StepFiscal } from "@/components/setup/step-fiscal"
import { StepQuality } from "@/components/setup/step-quality"
import { StepAdjustments } from "@/components/setup/step-adjustments"
import { StepGroupings } from "@/components/setup/step-groupings"
import { StepValidate } from "@/components/setup/step-validate"
import { StepSave } from "@/components/setup/step-save"
import { SavedConfigsSidebar } from "@/components/setup/saved-configs-sidebar"
import { useChatContext } from "@/lib/chat-context"

const STEPS = [
  { id: 1, label: "Tables" },
  { id: 2, label: "Columns" },
  { id: 3, label: "Fiscal config" },
  { id: 4, label: "Data quality" },
  { id: 5, label: "Remapping" },
  { id: 6, label: "Groupings" },
  { id: 7, label: "Validate" },
  { id: 8, label: "Save" },
]

// Local-storage key for the in-progress (not-yet-saved-to-Snowflake) config
// draft. Lets a user close the tab or refresh mid-wizard without losing
// their edits, without requiring every keystroke to round-trip to Snowflake.
const DRAFT_KEY = "bva_config_draft"

function loadDraft(): { config: BvaConfig; savedAt: string } | null {
  if (typeof window === "undefined") return null
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function saveDraft(config: BvaConfig) {
  if (typeof window === "undefined") return
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ config, savedAt: new Date().toISOString() }))
  } catch { /* storage full — ignore */ }
}

function clearDraft() {
  if (typeof window === "undefined") return
  try { localStorage.removeItem(DRAFT_KEY) } catch { /* ignore */ }
}

// Pending review: a config the AI proposed and the user chose to "Review in wizard".
// Written by the chat panel (bva_pending_review), consumed once here on mount.
const PENDING_REVIEW_KEY = "bva_pending_review"

function loadPendingReview(): { config: BvaConfig; savedAt: string } | null {
  if (typeof window === "undefined") return null
  try {
    const raw = localStorage.getItem(PENDING_REVIEW_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function clearPendingReview() {
  if (typeof window === "undefined") return
  try { localStorage.removeItem(PENDING_REVIEW_KEY) } catch { /* ignore */ }
}

const BLANK_CONFIG: BvaConfig = {
  configName: "default",
  sourceType: "single_table",
  singleTable: "",
}

export function SettingsWizard() {
  const router = useRouter()
  const { setPageContext, setIsOpen: setChatOpen, agentDraft } = useChatContext()
  const [step, setStep] = useState(1)
  const [config, setConfig] = useState<BvaConfig>(BLANK_CONFIG)
  // Guards rendering the step content until we've decided which of the three
  // config sources (saved/draft/pending-review) should win — see the effect
  // below. Without this, the wizard would briefly flash BLANK_CONFIG.
  const [loaded, setLoaded] = useState(false)

  const [aiReviewBanner, setAiReviewBanner] = useState(false)
  const [agentFilledBanner, setAgentFilledBanner] = useState(false)

  // Watch for agent-suggested config from the chat panel and auto-fill form fields.
  // Populate values in place — do NOT auto-advance the step; jumping the user to a
  // different section as they confirm in chat is jarring. They navigate when ready.
  const appliedDraftRef = useRef<string>("")
  useEffect(() => {
    if (!agentDraft || !loaded) return
    // Skip if we've already applied this exact draft — guards against re-running
    // when an unrelated re-render occurs.
    const key = JSON.stringify(agentDraft)
    if (key === appliedDraftRef.current) return
    appliedDraftRef.current = key
    setConfig((prev) => {
      // Bump updatedAt so StepColumns' re-sync effect (keyed on config.updatedAt)
      // fires even when the user is already sitting on the Columns tab — otherwise
      // the agent's picks don't appear until the step is remounted (click in/out).
      const merged = { ...prev, ...agentDraft, updatedAt: new Date().toISOString() }
      saveDraft(merged)
      return merged
    })
    setAgentFilledBanner(true)
  }, [agentDraft, loaded])
  // Load existing config on mount
  const { data: existing } = useQuery({
    queryKey: ["setup-config"],
    queryFn: () => getJson<{ config: BvaConfig | null; tableExists: boolean }>("/api/setup/config"),
  })

  // Decides which config source wins once the Snowflake fetch above resolves.
  // Priority (highest first): an AI proposal explicitly sent for review >
  // a real saved Snowflake config > a locally-cached in-progress draft.
  // Runs at most once (guarded by `loaded`) since this is a one-time
  // "which source do we start from" decision, not something that should
  // re-run every time `existing` happens to change identity.
  useEffect(() => {
    if (!loaded && existing !== undefined) {
      const pending = loadPendingReview()
      const draft = loadDraft()
      if (pending) {
        // Highest priority: an AI proposal the user explicitly chose to review.
        // Overlay it on top of any existing saved config so nothing is lost.
        setConfig({ ...(existing.config ?? {}), ...pending.config })
        setAiReviewBanner(true)
        setStep(2) // jump straight to column mapping to review the AI's picks
        clearPendingReview()
        clearDraft()
      } else if (existing.config) {
        // Snowflake config is authoritative — always prefer it over any draft.
        // Draft is only useful for mid-wizard progress that hasn't been saved to Snowflake yet.
        // Using a stale draft over a real saved config is the main cause of column mapping resets.
        setConfig(existing.config)
        clearDraft() // remove any draft so it can't win on the next open
      } else if (draft) {
        // No Snowflake config — restore in-progress draft unconditionally
        setConfig(draft.config)
      }
      setLoaded(true)
    }
  }, [existing, loaded])

  // Immediately set setup mode on mount so the chat panel routes to the setup
  // endpoint even before the DB query resolves (avoids 400 errors on fast sends).
  useEffect(() => {
    setPageContext({ mode: "setup", config: null, existingConfig: null, view: {} })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Shared "setup" pageContext payload — used both when the existing config
  // first loads AND to restore the chat panel back to "setup" mode whenever
  // the wizard leaves Step 7 (Validate). StepValidate's own effect
  // (components/setup/step-validate.tsx) switches mode to "custom_logic"
  // while it's mounted; it has no access to this callback, so the
  // restoration has to happen from this side once it unmounts.
  const applySetupPageContext = useCallback(() => {
    setPageContext({
      mode: "setup",
      config: existing?.config ?? null,
      existingConfig: existing?.config ?? null,
      view: {},
      onConfigAccepted: (cfg: import("@/lib/config").BvaConfig) => {
        // Just populate wizard state — navigation is handled by the chat panel buttons
        setConfig(cfg)
        clearDraft()
        setAgentFilledBanner(false)
      },
    })
  }, [existing?.config, setPageContext])

  // Update page context once the existing config has loaded from the DB.
  useEffect(() => {
    if (loaded) applySetupPageContext()
  }, [loaded, existing?.config, applySetupPageContext])

  // Restore "setup" mode whenever the wizard is on any step other than
  // Step 7 — see applySetupPageContext's comment above.
  useEffect(() => {
    if (loaded && step !== 7) applySetupPageContext()
  }, [step, loaded, applySetupPageContext])

  const update = useCallback(
    (patch: Partial<BvaConfig>) =>
      setConfig((prev) => {
        const next = { ...prev, ...patch }
        saveDraft(next)
        return next
      }),
    [],
  )

  // Like `update`, but replaces the whole config object rather than merging
  // a partial patch — used when a full config is swapped in wholesale (e.g.
  // picking a saved profile from the sidebar), not when a step edits one field.
  const handleSetConfig = useCallback((c: BvaConfig) => {
    setConfig(c)
    saveDraft(c)
  }, [])

  // A step is unlocked when:
  //   (a) a previously-saved config exists (always allow free navigation), OR
  //   (b) the user has already reached that step in this session, OR
  //   (c) all prior steps are considered "complete" based on config contents.
  //
  // Completion rules (gates):
  //   Step 1 complete → actualsTable (or singleTable) is filled in
  //   Step 2 complete → the three required actuals columns are mapped
  //   Steps 3–7 have no gate (optional or have usable defaults)
  const hasExistingConfig = !!existing?.config

  const stepPassed = (id: number): boolean => {
    switch (id) {
      case 1: return !!(config.actualsTable?.trim() || config.singleTable?.trim())
      case 2: return !!(config.actualsDateCol && config.actualsAmountCol && config.actualsCostCenterCol)
      default: return true
    }
  }

  const canJumpTo = (id: number): boolean => {
    if (hasExistingConfig || id <= step) return true
    // Check every preceding step is complete
    for (let s = 1; s < id; s++) {
      if (!stepPassed(s)) return false
    }
    return true
  }


  const goTo = (n: number) => setStep(Math.min(Math.max(n, 1), STEPS.length))

  // Bundled up and passed to every step component so they can move forward/
  // back or jump directly to a specific step without each one needing to
  // know how `step` is stored.
  const nav = { next: () => goTo(step + 1), back: () => goTo(step - 1), goTo }

  return (
    <div className="max-w-[1400px] mx-auto py-8 px-4">
      <div className="flex items-start gap-6">
      <main className="flex-1 min-w-0 space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">App Settings</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Connect your data sources. The app adapts to what&#39;s available — only showing filters and features
            your data supports.
          </p>
          {existing?.config && (
            <p className="text-xs text-green-600 dark:text-green-400 mt-1">
              Loaded saved configuration. Changes take effect immediately after saving.
            </p>
          )}
          {config.configName && config.configName !== "default" && (
            <p className="text-xs text-blue-600 dark:text-blue-400 mt-1">
              Editing profile: <span className="font-medium">{config.configName}</span>
            </p>
          )}
        </div>
      {/* AI Setup toggle — opens the persistent chat in setup mode */}
        <button
          type="button"
          onClick={() => setChatOpen(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-sm hover:bg-muted transition-colors shrink-0"
        >
          <Sparkles className="h-3.5 w-3.5" />
          AI Setup
        </button>
      </div>

      {/* AI review banner — config proposed by the assistant, loaded for review */}
      {aiReviewBanner && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-primary/30 bg-primary/5 px-4 py-2.5 text-sm">
          <div className="flex items-center gap-2">
            <Sparkles className="h-3.5 w-3.5 shrink-0 text-primary" />
            <span>Loaded the assistant&#39;s proposed configuration. Review each step and save when it looks right.</span>
          </div>
          <button
            type="button"
            onClick={() => setAiReviewBanner(false)}
            className="text-xs opacity-60 hover:opacity-100 shrink-0"
          >
            ✕
          </button>
        </div>
      )}

      {/* Agent autofill banner — live suggestions from the chat panel */}
      {agentFilledBanner && !aiReviewBanner && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-primary/30 bg-primary/5 px-4 py-2.5 text-sm">
          <div className="flex items-center gap-2">
            <Sparkles className="h-3.5 w-3.5 shrink-0 text-primary" />
            <span>CoCo has filled in suggested values — review each field and save when it looks right.</span>
          </div>
          <button
            type="button"
            onClick={() => setAgentFilledBanner(false)}
            className="text-xs opacity-60 hover:opacity-100 shrink-0"
          >
            ✕
          </button>
        </div>
      )}

      {/* Step indicator — always shown (always manual wizard mode now) */}
      <nav className="flex items-center gap-0.5" aria-label="Setup progress">
        {STEPS.map((s, i) => {
          const done = s.id < step
          const active = s.id === step
          return (
            <div key={s.id} className="flex items-center gap-0.5">
              <button
                onClick={() => canJumpTo(s.id) && setStep(s.id)}
                disabled={!canJumpTo(s.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors
                  ${active ? "bg-primary text-primary-foreground" : ""}
                  ${done ? "text-green-600 dark:text-green-400 hover:bg-muted cursor-pointer" : ""}
                  ${canJumpTo(s.id) && !active && !done ? "text-muted-foreground hover:bg-muted cursor-pointer" : ""}
                  ${!canJumpTo(s.id) ? "text-muted-foreground/40 cursor-default" : ""}
                `}
              >
                {done ? <Check className="h-3.5 w-3.5" /> : (
                  <span className="h-4 w-4 rounded-full border text-[10px] flex items-center justify-center">
                    {s.id}
                  </span>
                )}
                {s.label}
              </button>
              {i < STEPS.length - 1 && (
                <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
              )}
            </div>
          )
        })}
      </nav>

      {/* Step content — only render after config is loaded to avoid initRoles running on BLANK_CONFIG */}
      {/* Step content — only render after config is loaded to avoid initRoles running on BLANK_CONFIG */}
      {!loaded && (
        <div className="flex items-center gap-2 text-muted-foreground py-12">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading saved configuration…
        </div>
      )}
      {loaded && step === 1 && <StepTables config={config} update={update} nav={nav} />}
      {loaded && step === 2 && <StepColumns config={config} update={update} nav={nav} />}
      {loaded && step === 3 && <StepFiscal config={config} update={update} nav={nav} />}
      {loaded && step === 4 && <StepQuality config={config} update={update} nav={nav} />}
      {loaded && step === 5 && <StepAdjustments config={config} update={update} nav={nav} />}
      {loaded && step === 6 && <StepGroupings config={config} update={update} nav={nav} />}
      {step === 7 && <StepValidate config={config} update={update} nav={nav} />}
      {step === 8 && (
        <StepSave
          config={config}
          update={update}
          setConfig={handleSetConfig}
          onClearDraft={clearDraft}
          onDone={() => router.push("/")}
          onReset={() => { setConfig(BLANK_CONFIG); clearDraft(); setStep(1) }}
          nav={nav}
        />
      )}
      </main>
      <aside className="w-56 shrink-0 sticky top-8 self-start">
        <SavedConfigsSidebar setConfig={handleSetConfig} goTo={goTo} />
      </aside>
      </div>
    </div>
  )
}
