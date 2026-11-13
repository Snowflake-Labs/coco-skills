---
name: act-document
description: "Document stage — produce a well-founded explanation of something that exists. Parallel to Build (which produces the thing itself). Core discipline: drafter isolation (codebase-blind) + citation tracing. Assumes findings/evidence from prior Observe/Distil stages."
---

# Document

Produce a user-facing explanation — guide, reference, report, README — with citation discipline. Every claim traces to evidence. The drafter is structurally isolated from source material, writing only from compiled findings. Any commands, code, or reference implementations in the output must be executed and verified before inclusion — only tested content ships; never caveat ("this should work") in place of testing.

**Distinction from Build:** Build produces the *thing itself* (code, config, app). Document produces the *explanation of the thing*. Build's constraint is scope discipline (don't exceed the spec). Document's constraint is citation discipline (don't claim beyond the evidence).

## When to use this stage

- Explaining something that already exists (a feature, a system, a process, a decision)
- Producing user-facing guides, API references, onboarding docs, how-to material
- Creating reports or analyses where factual accuracy matters and claims must be traceable

**When NOT to use:** If the "document" IS the primary deliverable (e.g., a spec, a design doc, a proposal) — that's Build. Document is specifically for explaining an existing thing with isolation guarantees.

---

## Input contract

- **Findings brief** from prior Distil stage (`notes/{YYYY-MM}/{project}-wi{N}-distil.md` or equivalent)
- OR **citation sources** (gathered in Survey: external docs, knowledge notes, stakeholder input)
- Target audience and scope (from Spec, or operator direction)

**If findings don't exist yet:** Route back to Observe → Distil first. Document does not survey or investigate — it writes from what's already established.

---

## The isolation model

Load `references/refs-findings-contract.md` for the full rationale.

The drafter (`xo-bounded-writer`) has **no codebase access** — no grep, glob, or bash tools. Its only input is the findings brief. This creates a hard contract:

- If a fact is in findings → drafter can state it
- If a fact is NOT in findings → drafter cannot state it (it has no way to discover it)

This structurally prevents unbacked claims — the most common documentation failure mode.

---

## Process

### Step 1: Draft (xo-bounded-writer — CODEBASE-BLIND)

For shippable guides and reference docs, load `references/refs-guide-frontmatter.md` before drafting so the intended provenance frontmatter is part of the output shape from the start.

```
runSubagent(subagent_type: "xocortex:xo-bounded-writer", prompt: "
  PERSONA: Drafter — write the explanation
  INPUT_PATH: [findings brief path — THE ONLY SOURCE]
  OUTPUT_PATH: $XO_SCRATCH/doc-guide-draft.md
  
  Write a user guide based solely on the findings brief. Target audience: [audience].
  Lead with 'how to do X', not 'how X works internally'. Use tables for reference material,
  code blocks for examples. Keep sections short and scannable.
  Do NOT add claims not in the findings brief. If a topic has UNTESTED items, omit it.
")
```

### Step 2: Critic (xo-cold-smart — citation audit)

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Critic — citation audit
  REFERENCE: [findings brief path]
  INPUTS: $XO_SCRATCH/doc-guide-draft.md
  OUTPUT_PATH: $XO_SCRATCH/doc-audit.md
  
  Audit every factual claim in the guide against the findings brief.
  Return PASS (backed), CONTRADICTION (guide contradicts finding), or UNBACKED (claim not in findings).
  List each issue with the specific claim and what the findings say.
")
```

Address CONTRADICTION and UNBACKED items: fix the guide or request additional findings from Observe. Re-run critic until clean.

### Step 3: Finalize

After clean audit:
1. Apply frontmatter from `references/refs-guide-frontmatter.md` (provenance metadata) to every shippable guide or reference doc the stage produces
2. Present to operator

---

## Source-cited variant

When sources are external references rather than codebase findings:

1. **Compile citations** from gathered sources (Survey output, knowledge notes, stakeholder input):
   ```markdown
   ### C1: [claim summary]
   - Claim: [the assertion]
   - Source: [where this comes from]
   - Trust: [when, who, type of source]
   ```
2. **Draft** — same isolation: drafter receives only the citations document
3. **Critic** — attribution audit: every claim has a source, trust signal is present

---

## Authoring skills and procedure files

When the document is itself a procedure a cold agent will execute — a skill, an agent/monitor definition, a runbook — three disciplines apply on top of citation discipline:

- **Prescriptive, single imperative path.** Author it as "do this exactly", not a menu of co-equal options — a cold agent reads optionality ("either X or alternatively Y") as licence to improvise. Where a genuine branch exists, key it on an observable condition as a deterministic branch ("if X, do Y; otherwise Z"), not a preference. Reserve real user choices for an explicit question to the operator, not embedded in the procedure.
- **Ship clean instructions.** The shipped file carries operating instructions only — never design deliberation ("why we chose X not Y"), decision rationale, "this is not a Z" framing, developer/person names, hardcoded environment-specific identifiers, or framework-internal vocabulary a first-time reader lacks. The rationale and alternatives-rejected belong in the spec and WI notes. Before declaring such a file done, dispatch a cold-reader Critic (persona: a capable reader with none of your session/system context) to confirm it defines its terms at or before first use and orients a first-timer.
- **Run every step before it ships, or mark it untested.** A runbook's own commands and procedures are its deliverable, not a pending gate — execute each before presenting the runbook as ready. If one cannot be run, mark it inline as `UNTESTED` at that step and say so in the handoff; never present a runbook as ready or complete while a step is silently unexercised.

---

## Output contract

Draft + clean audit. If the artifact is a shippable guide or reference doc, its final output must include the provenance frontmatter from `references/refs-guide-frontmatter.md`. Present the work to the operator:

> "The [guide/report/document] is ready — citation audit passed. It's at `[path]`. Please review and let me know how you'd like to proceed."

Do not push to Ship unprompted. The operator decides the next step.

Route to **Review** (`references/decide-review.md`) as the formal gate — the operator accepts, requests revisions, or parks the work.

---

## Coherence discipline

When an artifact is corrected across multiple turns, rewrite it into one coherent position — do not append dated "CORRECTION (date)" sections or inline "UPDATED" asides, and do not narrate your own abandoned steps or argue against earlier wrong drafts. Fold corrections into the body; the edit history belongs in diary/task savepoints, not the deliverable. After any round of corrections, sweep the whole artifact for internal consistency — a fix in one section must be reflected everywhere it bears (tables, summaries, positioning). State the clean current position and legitimate forward-looking caveats only. This is the prose instance of the `core-guidelines.md` "Coherent work, not a patch pile" principle.

---

## Chaining (soft — reminders, not gates)

- **Before advancing:** update notes + task with what was produced and the citation-audit result.
- **Usually follows:** Distil (findings exist) — or Build (the thing is built, now explain it).
- **Leads to — primary:** Review (`references/decide-review.md` — operator gate: accept, revise, or park).
- **After approval:** Ship (publish/deliver); Capture (if the guide becomes canonical).
