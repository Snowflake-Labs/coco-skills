---
name: decide-spec
description: Spec stage — write the bounding contract that execution stages consume. Structures intent as Goal/Requirements/Constraints/Output. The operator gates before any execution begins.
---

# Spec

Write the specification — the **bounding contract** that turns an approved approach into a precise, actionable definition of what will be produced. Every downstream execution stage (Build, Prove, Document) works from the spec; it is the scoping artifact for the Act phase.

A good spec is not about enumerating every detail — it is about bounding the execution space precisely enough that an agent can work on a closed question ("does this satisfy the spec?") rather than an open one ("what should I build?").

## When to use this stage

- An approach has been approved in Workshop and you're ready to commit to execution
- The operator has stated a clear requirement and you need to formalise it before acting
- You're using test-driven development: the spec's acceptance criteria become the tests

---

## Input contract

- Workshop proposal (`notes/{YYYY-MM}/{project}-wi{N}-workshop.md`) OR operator-stated requirement
- Active WI context (task file)
- Optional: prior spec or related specification for reference

---

## Structure: intent-driven format

Organise the spec around the four blocks of intent-driven development:

```markdown
# Spec — [brief title]

## Goal
[One sentence: what outcome this achieves. Not how — what.]

## Requirements
[What must be true for this to be done. Testable, observable assertions.]
- R1: [requirement]
- R2: [requirement]

## Constraints
[What must NOT be violated. Scope limits, compatibility, safety requirements.]
- C1: [constraint]
- C2: [constraint]

## Non-goals
[What this spec explicitly does NOT cover. Ground in evidence where possible.]
- NG1: [out-of-scope item] — [evidence: abandoned PR / WONTFIX decision / NOT_SUPPORTED test]
- NG2: [out-of-scope item]

## Decisions
[Design decisions made while authoring, and open questions deferred.]
- Locked: [decision] — [rationale / evidence]
- Deferred: [open question] — [what would resolve it]

## Source map (chain of evidence)
[Include when the spec leans on expanded or high-importance concepts that live elsewhere; skip for self-contained specs.]
**Dereference rule:** Dereference a tagged concept's source before producing for it. Never improvise a tagged term. If a source or anchor is missing, STOP and ask.

### Source keys
- [KEY] -> [authoritative file/path/section]
- [KEY2] -> [authoritative file/path/section]

### Concept -> source
| Spec term / concept | Authoritative source |
|---|---|
| [tagged concept] | [KEY] [section / anchor] |
| [tagged concept] | [KEY2] [section / anchor] |

## Output / Acceptance criteria
[How we know this is done. What the agent can check. What the operator observes.]
- Done when: [condition]
- Anchor-integrity check: [before Build, every tagged concept resolves to a real source and nothing is left to improvisation]
- Verify by: [how to verify]
```

**The spec is not a task list.** It defines the desired state; Act stages work out how to get there.

**Non-goals should be evidence-grounded.** When writing non-goals, check the findings brief for abandoned approaches, WONTFIX decisions, and tests that assert unsupported behaviour — these are scope exclusions backed by prior art, not invented declarations. An NGx with evidence is far stronger than a bare declaration.

**Record decisions.** Capture choices made during authoring as Locked (with rationale) and open questions as Deferred (with what would resolve them); review Deferred items before Act, since an unresolved open question is a scope risk.

**Use a source map when the spec depends on shorthand.** If the spec leans on expanded or high-importance concepts that live in other files or sections — framing, numbers, quotes, definitions, redaction fences, named examples — add a `Source map (chain of evidence)` section. It turns shorthand into an explicit dereference contract for downstream execution. If the spec is self-contained and every requirement is stated in full, skip it.

**Dereference before producing.** A tagged concept is a pointer, not a license to paraphrase from memory. Before Build, Document, or Prove use a tagged term, dereference its source and confirm the exact framing, numbers, and limits. If the authoritative source or anchor is missing, stop and ask the operator rather than guessing.

---

## Optional: draft spec critic

For complex or high-stakes specs, dispatch a `xo-cold-smart` critic to challenge the draft before the operator gates it:

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Critic — spec review
  REFERENCE: [the draft spec]
  INPUTS: [relevant findings brief or prior WI notes]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/spec-critique.md
  
  Review this spec draft. For each section (Goal/Requirements/Constraints/Non-goals/Output):
  - Is the intent clear and unambiguous?
  - Are requirements testable/observable?
  - Are there missing constraints or unstated assumptions?
  - **Are non-goals explicit and evidence-grounded?** Check the findings brief for abandoned approaches, WONTFIX decisions, or prior art that was deliberately excluded — missing non-goals are a scope-creep risk.
  - **Testing-adequacy: do the acceptance criteria actually verify the goal?** Check each Requirement has a corresponding way to observe it's met — acceptance criteria that can't be checked make the spec unplannable downstream, not just incomplete.
  
  Return APPROVE if the spec is sound, or REVISE with specific gaps.
")
```

Address any REVISE feedback before presenting to the operator.

## Optional: anchor-integrity verifier

When the spec uses a source map, dispatch a `xo-cold-smart` critic to verify the chain before the operator gates it:

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Critic — anchor-integrity check
  REFERENCE: [the draft spec]
  INPUTS: [source files or findings the source map cites]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/spec-anchor-integrity.md
  
  Review the spec's Source map (chain of evidence).
  - Does every tagged concept resolve to a real source section or anchor?
  - Are any tagged terms left to improvisation or unsupported paraphrase?
  - Do the cited sources actually support the framing, numbers, quotes, and definitions the spec leans on?
  
  Return APPROVE if every tagged concept dereferences cleanly, or REVISE with the missing or broken anchors.
")
```

Address any REVISE feedback before presenting to the operator.

---

## Operator gate (mandatory)

**No execution begins without operator approval of the spec.**

For build/act-pathway WIs, acceptance criteria must be present and operator-confirmed before Act. Investigation/observe (and other non-build) WIs state the deliverable *form* instead (e.g. "findings brief at the scratch path") and must not be blocked for lacking acceptance criteria.

Present the spec:
> "Here is the spec for [brief title]. Please review:
> - Goal: [one line]
> - [N] requirements, [M] constraints
> - Done when: [acceptance criteria summary]
>
> Approve to proceed to Act, or let me know what to adjust."

| Response | Action |
|---|---|
| Approved | Write spec to notes file; update task NextAction; proceed to Act |
| Correction | Revise spec; re-present |
| Reject | Return to Workshop (`references/orient-workshop.md`) for approach re-design |

---

## Spec and plan mode

The harness offers a **plan mode** that the agent switches into for significant work (new capabilities, multi-component changes, multi-step deliverables, ambiguous problems). Lean into it — do not fight it or duplicate it. The relationship is:

- **The specification is ours** — the robust, evidence-grounded contract produced by this stage (built on the Observe/Orient chain of evidence). It is more reliable than ad-hoc planning because every requirement traces back to findings.
- **Plan mode produces the plan from the spec.** For significant work, write the specification first (agent mode, to notes), then **feed it into plan mode**. Plan mode is read-only and emits the plan artifact (`create_plan`) — the sequenced task list. In effect, plan mode performs a **cold review-and-structuring pass over the specification**, turning the contract into executable steps.
- **On approval**, switch to agent mode and execute (Build/Prove/…). Both artifacts are durable: the spec in `notes/`, the plan under `.snowflake/cortex/plans/`.

**Size picks the mechanism:**
- *Significant work* → write the spec, then use plan mode to produce the plan from it.
- *Smaller spec-driven work* (doesn't warrant plan mode) → a lightweight spec note is enough; proceed directly to Build.

Do not author a separate parallel plan inside this stage — the spec is the contract; the **Plan stage** (`references/decide-plan.md`) turns it into the plan via plan mode (or, for small work, the task NextAction carries the steps).

---

## Output contract

Write the approved spec to `notes/{YYYY-MM}/{project}-wi{N}-spec.md`. Update the task file:
- Add `Spec: notes/{YYYY-MM}/{project}-wi{N}-spec.md` to the task frontmatter
- Update `NextAction` to the first Act stage (or "produce plan from spec via plan mode" for significant work)

---

## Entry modes and TDD

The spec informs how the Act phase is sequenced:

- **Standard (spec-first)**: Spec → Plan → Provision → Build → Prove → Ship
- **TDD (bracket-first)**: Spec → Plan → Prove (write failing tests encoding the spec's acceptance criteria) → Build (implement to green) → Prove (verify) → Ship
- **Empirical (discovery-first)**: No upfront spec; after production, write a retrospective spec and enter Review (Mode C: Self-Validation)

State the intended sequence at the bottom of the spec so the Act phase knows how to compose.

---

## Chaining (soft — reminders, not gates)

- **Usually follows:** Workshop (an approach was chosen) — or Distil / the operator directly if the approach is obvious.
- **Leads to — primary:** Plan (turn the spec into implementation steps via plan mode).
- **Or:** Prove.author (TDD — write tests from the spec before building).
