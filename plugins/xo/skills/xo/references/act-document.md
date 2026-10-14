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
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/doc-guide-draft.md
  
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
  INPUTS: $XOCORTEX_HOME/tmp/wi{N}/doc-guide-draft.md
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/doc-audit.md
  
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

## Output contract

Draft + clean audit. If the artifact is a shippable guide or reference doc, its final output must include the provenance frontmatter from `references/refs-guide-frontmatter.md`. Present the work to the operator:

> "The [guide/report/document] is ready — citation audit passed. It's at `[path]`. Please review and let me know how you'd like to proceed."

Do not push to Ship unprompted. The operator decides the next step.

Route to **Review** (`references/decide-review.md`) as the formal gate — the operator accepts, requests revisions, or parks the work.

---

## Chaining (soft — reminders, not gates)

- **Before advancing:** update notes + task with what was produced and the citation-audit result.
- **Usually follows:** Distil (findings exist) — or Build (the thing is built, now explain it).
- **Leads to — primary:** Review (`references/decide-review.md` — operator gate: accept, revise, or park).
- **After approval:** Ship (publish/deliver); Capture (if the guide becomes canonical).
