---
name: decide-plan
description: Plan stage — turn the approved specification into a production plan using the platform's plan mode. Plan mode (read-only) produces the plan artifact and acts as a cold review/structuring pass over the spec. Operator approves the plan before Act.
---

# Plan

Turn the approved **specification** (the *what*) into a **production plan** (the *how*) — the sequenced task list the Act phase executes. Plan is where XO meets the platform's plan mode.

## When to use this stage

- After Spec, for significant work (new capabilities, multi-component changes, multi-step deliverables, anything where the path isn't obvious).
- **Skip** for small, clear spec-driven work — the task NextAction is plan enough; go straight to Act.

## Input contract

- An approved spec (`notes/{YYYY-MM}/{project}-wi{N}-spec.md`), or a clear operator-stated requirement.

## How it works — lean into plan mode

Our specification is the robust, evidence-grounded artifact. Feed it into the platform's **plan mode** rather than hand-authoring a parallel plan:

1. Switch to plan mode (read-only).
2. Plan mode produces the plan artifact (`create_plan`) from the spec — the sequenced task list. In effect it performs a **cold review-and-structuring pass** over the specification.
3. The operator approves the plan; then switch to agent mode and execute (Act).

Both artifacts are durable: the spec in `notes/`, the plan under `.snowflake/cortex/plans/`.

## Optional: cold plan critic

For significant or multi-component plans, offer the operator an optional cold pass over the **plan** (distinct from the spec critic, which reviews the spec) before the gate:

> "This is a multi-component plan — want a cold second-pass review of it before Act?"

If accepted, dispatch a fresh `xo-cold-smart` (one that did not produce the plan) before presenting to the operator:

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Critic — plan review (you did not produce this plan)
  REFERENCE: [the approved spec]
  INPUTS: [the plan artifact + relevant findings / WI notes]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/plan-critique.md

  Review this plan against the spec:
  - Sequencing: are steps correctly ordered; does any step depend on a later one?
  - Dependencies: are prerequisites and cross-component seams accounted for?
  - Assumptions: any unverified assumption, or a spec requirement the plan omits?
  - Scope: does the plan stay within the spec — nothing dropped, no scope creep?
  Return APPROVE if the plan is sound, or REVISE with specific gaps.
")
```

Fold its findings in before the operator gate. Offered, never mandatory — keep it proportionate to stakes.

## Security review (when triggered)

Check the plan against `references/refs-security-review-triggers.md`'s trigger table. If the plan touches credential handling, cross-boundary writes, sensitive file I/O, new IPC/API surface, sandbox/privilege context, or authentication flow changes, dispatch a security review of the **plan** before Act — the same review Build already runs on the diff, run one stage earlier so a security-relevant design gets checked before implementation, not only after.

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Critic — security (plan review, pre-implementation)
  REFERENCE: [the approved spec + the plan artifact]
  INPUTS: [relevant findings / WI notes]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/plan-critic-security.md

  Review the plan for security boundary violations, credential exposure, and trust boundary crossings it would introduce.
  Map process/trust boundaries the plan implies. Trace where credentials would flow. Evaluate against the security checklist in refs-security-review-triggers.md.
  Return SAFE, REVISE (specific concern + suggestion), or BLOCK (fundamental design concern).
")
```

| Verdict | Action |
|---|---|
| SAFE | Proceed to the operator gate |
| REVISE | Fold feedback into the plan before presenting |
| BLOCK | Escalate to operator — do not proceed to Act |

This runs independently of the optional cold plan critic above — a plan can warrant a security review without warranting a full sequencing/dependencies review, and vice versa.

## Output contract

- An approved plan (the task list). Update the task `NextAction` to the first Act stage.

## Gate

Operator approves the plan before any Act work begins — *nothing is built before this*.

If the approved spec or approach is stale against current findings or repo state, surface it to the operator before Act.

## Chaining (soft — reminders, not gates)

- **Usually follows:** Spec (a bounding contract to plan against).
- **Leads to — primary:** Provision (set up an isolated workspace, then Build), composed per the entry mode (spec-driven, TDD, …).
- **Or:** Build directly (if no workspace isolation is needed). See `spec.md` for how Spec and Plan relate.
