---
name: xo
description: "**[REQUIRED]** XO operator workflow system — the single entry point. Triage classifies the request, routes to the right stage reference, and composes multi-stage pathways. Recording (save) and Recall run underneath. Triggers: work item, WI, task, implement, investigate, plan, review, execute, what should I do, where do I start, savepoint, resume."
---

# xo

The single skill for the XO workflow system. It triages a request, routes to the stage reference that carries the procedure, and composes pathways across stages. Stages are references in `references/` (this skill, one directory), not separate skills. This file is the operating instructions.

---

## Session Prerequisites (Always First)

Before routing any work:

1. Load `references/save-protocol.md` (recording discipline — diary, notes, task, savepoint, recovery) and `references/core-guidelines.md` (cross-cutting operating rules).
2. Recording is active for the whole session: savepoint at milestones and gates; on a "pick up WI-N" / "resume", follow Session Recovery in `save-protocol.md` first.

## Setup & install (self-knowledge)

When asked how XO is set up or whether the install is complete: XO requires `node`, `git`, `gh`, and `jq`. The bundled installer and runbook live at the plugin root as `setup.sh` and `SETUP.md`; at runtime that root is exposed as `CORTEX_PLUGIN_ROOT`. Running `setup.sh` sets `XOCORTEX_HOME`, adds `node` to the hooks' PATH so hooks can fire reliably, and can point XO at a git-backed vault or the local fallback vault. `XOCORTEX_HOME` set means setup completed; if it is unset, setup has not been run and XO is using local fallback behavior.

---

## Hard rules — navigating the workflow (do not skip)

XO is a directed workflow graph, not a set of recipe cards: defined entry points, a forward spine, deliberate loops back over completed work (redo, review, capture), and multiple exits. You compose a path through it — but the rules below and the operator gates are fixed, not optional. "Choose-your-own-adventure" describes the branching, not a licence to freestyle.

1. **Triage before acting.** Run Step 0 on every new request before any other action.
2. **Load the stage reference before you act.** The procedure — steps and prescribed subagent — lives only in the stage's reference (see Reference Index), not in this routing table. Acting from the routing table alone, or dispatching `generalPurpose` instead of a stage's prescribed subagent, is a failure.
3. **Honour operator gates.** Between phases the operator confirms before you continue. Never self-advance past a gate.
4. **"Small fix" does not grant permission to skip stages.** The Act sequence (Provision → Build → Prove) is never skipped. Only upstream scoping (Observe/Orient/Decide) may be skipped, and only when it already happened — see Skip rules.
5. **Never silently infer a pathway for substantive work.** Surface it; let the operator confirm.
6. **Surface conflicts during discussion; don't self-resolve.** When working out how to solve a problem, if you spot a conflict, inconsistency, or gap, surface it and agree a direction with the operator — do not pick a plausible fix and apply it. This governs the discussion phase, not execution of an already-agreed plan.
7. **Search the vault by terms via Recall; look up a known identity directly.** If you are searching notes/tasks/diary by topic, concept, or terms, use **Recall**. If you already have a specific identity (WI-N, exact filename, exact path), load or grep it directly.

---

## Step 0 — Triage (always first)

Decide two things: **how heavy** this is, and **which pathway** (if any) the operator already wants.

**Quick** — a question, lookup, or explanation you can answer directly.
→ Answer it. Then offer lightly: *"Want to go deeper / make this a work item?"* No WI, no stages, no ceremony unless they escalate.

**Substantive** — investigation, design, or anything that changes code/state or spans multiple steps.
→ Scope before doing:
1. **Did the operator signal a pathway?** ("just investigate", "spec-driven build", "review this PR") — confirm it; don't re-decide.
2. **If intent is open/ambiguous**, ask 1–3 clarifying questions (one turn) to close the question first.
3. **Assess readiness** — what has the operator actually given you?
   - Findings/evidence from prior investigation, or just a bare task?
   - A spec/requirements, or just intent?
   - A target workspace identified, or just a topic?
   - An existing WI with notes, or cold-start?
   Start the pathway from where evidence actually begins — not from where a WI's NextAction optimistically assumes you are.
4. **Propose the pathway** menu-style, one recommendation. Apply the Work Item Gate. Enter only on operator agreement.

Clarify only when the ambiguity changes *what* you build or *which* pathway — not for details you can assume and confirm later.

**Recall routing.** If the operator gives a known specific identity (WI-N, exact filename, exact path), load or grep it directly. If you need to search the vault by terms, topic, or concept, run **Recall** (`references/observe-recall.md`) to resolve the target before routing.

---

## Routing Table

Each row points to the stage reference to load (Hard rule 2).

| User intent | Load |
|---|---|
| "pick up WI-N", "continue", "resume" | `references/save-protocol.md` (Session Recovery) → then **WI pickup** (below) → load the NextAction's stage reference |
| vault search by terms / topic / concept | `references/observe-recall.md`, then route on the resolved target |
| "what's active?", "status", "portfolio" | Portfolio Overview (below) |
| research, survey, map the territory, where does X live, broad overview | `references/observe-survey.md` |
| investigate, deep dive, establish facts, verify, how does X work | `references/observe-analyse.md` |
| compile findings, what did we learn, distil this | `references/orient-distil.md` |
| design options, what approaches exist, stress-test a design | `references/orient-workshop.md` |
| write the spec, define what we'll produce, requirements | `references/decide-spec.md` |
| build a plan, plan the implementation | `references/decide-plan.md` |
| review this PR/changes, validate my work, respond to review comments | `references/decide-review.md` |
| capture, crystallise, close the loop, keep for next time | `references/decide-capture.md` |
| set up workspace, worktree, provision environment | `references/act-provision.md` |
| implement, build, produce, create, configure, make this, fix this | `references/act-build.md` |
| explain, document, write the guide, produce a report | `references/act-document.md` |
| prove it works, validate, verify, test, run tests | `references/act-prove.md` |
| ship, deliver, publish, deploy, raise PR, send to requestor | `references/act-ship.md` |
| clean up, tear down, remove workspace, wrap up | `references/act-cleanup.md` |
| savepoint, save, record, checkpoint | `references/save-protocol.md` |

If intent spans phases ("investigate and implement"), propose a pathway (below).

---

## Work Item Gate

Before routing to a stage (except quick reactive tasks), verify a WI exists.

| Situation | WI required? | Action |
|---|---|---|
| Resuming existing WI | Exists | Proceed |
| Quick reactive task, typo, short lookup | No | Route directly |
| New investigation, implementation, or design | Yes | Check; if none, ask |

When it fires:
> "This looks like structured work. (a) Create a WI and start now? (b) Create and defer? (c) Attach to an existing WI? (d) Keep it lightweight — no WI?"

WI creation procedure is in `references/save-protocol.md`.

---

## WI pickup (by number)

1. **Find it:** glob `tasks/current/*wi{N}-*.md` — the project prefix varies; match on the number, not the filename start. (Notes: `notes/**/*wi{N}-*.md`.)
2. **Read it:** Status, NextAction, scope, Category, **Scratch**.
3. **Ensure scratch exists:** the task file's `Scratch:` field is the authoritative path (`$XOCORTEX_HOME/tmp/wi{N}/`). `ls` it; if missing, `mkdir -p` it once. If the field is absent, add `Scratch: $XOCORTEX_HOME/tmp/wi{N}/` to the task file. Never invent an alternative location.
4. **Check priors exist** before executing the NextAction:
   - Notes for this WI? If none, no prior investigation is recorded.
   - A spec/findings brief? If NextAction implies Build but no spec exists, scoping was skipped.
   - Scratch artifacts in the `Scratch:` dir? If none, no production has happened yet.
   - Target workspace in expected state? (branch exists? code moved on?)
5. **Report the gap if priors are missing** — do not assume readiness from a NextAction:
   > "WI-{N} says NextAction is [X], but I don't see [notes/spec/survey/workspace]. That suggests [stage] hasn't run. (a) run it first, (b) skip — you brief me, (c) it exists elsewhere — point me to it."
6. **Proceed only once resolved.** Then load the stage reference for the NextAction before acting (Hard rule 2). A WI written days ago may be stale — verify before committing to its plan.

---

## Pathways (known-good slices)

Infer which fits, propose menu-style with one recommendation. Don't invent arbitrary orderings; if none fits, compose a slice of the spine that keeps stage order and gates. Never skip the Spec gate before building.

**Front-load the pathway's references.** On entering a pathway, name the stage references the pathway will need and load each as you reach its stage — for a clearly declared pathway (e.g. "review this PR"), load the entry reference now and tell yourself which downstream references are coming, so the work runs as one task rather than repeated route-outs.

Full spine, by phase:
`Observe (Survey → Analyse) → Orient (Distil → Workshop) → Decide (Spec → Plan) → Act (Provision → Build/Document → Prove → Ship)`

| Pathway | Sequence | For |
|---|---|---|
| **spec-driven** | Workshop → Spec → Plan → Provision → Build → Prove → Ship | new feature to a clear design |
| **TDD** | Spec → Plan → Provision → Prove.author → Build → Prove.verify → Ship | bug fixes, high-confidence, regression prevention |
| **empirical-improvement** | Analyse → Distil → Assess-for-gate → Spec → Plan → Build… | refactoring/optimising existing code |
| **open-build** | full spine | novel cold-start work |
| **document-a-feature** | Analyse → Distil → Provision → Document → Prove.citation-audit → Ship | the feature exists, the guide doesn't |
| **create-deliverable** | Distil → Spec → Provision → Build → Prove.validate → Ship.deliver | non-code artifact for a requestor |
| **prototype-app** | Workshop → Spec → Provision.local-infra → Build → Prove.interactive → Ship.deploy | demo, Streamlit app, prototype |
| **configure-environment** | Analyse → Spec → Provision.snowflake → Build → Prove.state-validation → Capture | Snowflake objects, permissions, pipelines, infra |
| **assess-for-gate** | (Survey/Analyse if unfamiliar) → Review (cold Critic vs spec/source/intent → justified brief) → decide gate | quality judgment before merge/publish/ship; Review critiques the whole chain, not just the diff |
| **respond-to-feedback** | Survey (subject + ALL feedback) → Analyse (classify trivial/substantive via complexity-gate) → trivial: Build / substantive: Spec → Build → Brief → gate | PR/doc/Slack/email comments |

**Wagon ruts (seed Observe from Triage context):**
- "review this PR / check this PR" → assess-for-gate; seed: PR diff + risk factors
- "someone left comments" → respond-to-feedback; seed: subject + feedback channel
- "validate my changes / check my empirical work" → assess-for-gate; seed: fresh changes
- "is this ready to ship/merge/publish?" → assess-for-gate; seed: artifact + gate type

---

## Reflexes — small offers

Offer the *adjacent* stage as a single step on substantial work. Offer once, easy to decline, never nag.

| When… | Offer |
|---|---|
| Produced a substantial artifact (code, doc, spec, design, analysis) | a cold **Critic** vs intent — and its **foundations** |
| Produced/modified an artifact | **Prove** (test for code, interactive for UI, state-check for configs, citation-audit for docs) |
| Entering unfamiliar territory | **Survey/Analyse** first — and check for relevant available skills (especially Snowflake-technology skills); prefer them over improvising |
| Open design fork + you have findings | **Workshop** to explore angles |
| Solved something non-obvious / learned a durable fact | **Capture** |
| Work genuinely done and accepted | **Cleanup** — see guard |

**Critic runs backward:** if an artifact's foundations are weak (speculation, no evidence), offer the missing *prior* stage (Analyse/Distil) before building further.

**Guards:**
- Gentle, single, bypassable. If declined, drop it.
- **Cleanup is terminal-only.** Never offer Cleanup / commit / push while the operator is still reviewing or validating. Wait for explicit "done." Never bundle commit + push + teardown.
- For *starting* significant work, lean into plan mode (Spec → Plan); no separate offer.

---

## Skip rules

Only upstream scoping (Observe/Orient/Decide) may be skipped, and only when it already happened.

- **Resuming with an existing plan:** validate the plan against current state, then run full Act.
- **Reactive fix / typo:** skip upstream scoping only; still Provision → Build (with critic) → Prove.
- **Spec already exists:** validate it isn't stale, then Plan → full Act.

---

## Portfolio Overview

On "what's active?" / "status": read `tasks/index.md`; present active items grouped Now / Next / Later (focus Now/Next); ask if they want detail on any.

---

## Reference Index

Load lazily when routing — do not pre-load (they consume context). `references/save-protocol.md` and `references/core-guidelines.md` are the exception: load both at session start.

**Always-on (recording + rules)**
| Reference | Purpose |
|---|---|
| `references/save-protocol.md` | Recording: savepoint, Session Recovery, WI creation, task updates, urgency levels |
| `references/core-guidelines.md` | Cross-cutting operating rules: grounding, source order, gates, storage, per-phase invariants |
| `references/save-recovery.md` | Detailed recovery procedures (context reset, stale metadata) |
| `references/save-task-conventions.md` | Task file schema: fields, ordering, Scratch field |
| `references/save-three-layer.md` | Recording model: diary/task/notes separation |
| `references/save-context-urgency.md` | Hook urgency thresholds and checkpoint state machine |

**Observe**
| Reference | Purpose |
|---|---|
| `references/observe-survey.md` | Survey: breadth-first discovery to locate where the subject lives |
| `references/observe-analyse.md` | Analyse: depth-first investigation to establish verified facts |
| `references/observe-recall.md` | Recall: search the memory vault (notes/tasks/diary) — a first-class action; prefer over bare grep |
| `references/refs-survey-methods.md` | Traversal patterns, source hierarchy, trust signals (load from survey/analyse) |

**Orient**
| Reference | Purpose |
|---|---|
| `references/orient-distil.md` | Distil: compile raw evidence into a findings brief |
| `references/orient-workshop.md` | Workshop: parallel-angle exploration → human-synthesised proposal |

**Decide**
| Reference | Purpose |
|---|---|
| `references/decide-spec.md` | Spec: write the bounding contract for execution |
| `references/decide-plan.md` | Plan: turn the spec into an implementation plan (plan mode) |
| `references/decide-review.md` | Review: cold Critic vs reference → justified verdict; self-check before gate |
| `references/decide-capture.md` | Capture: durable learning notes + optional promote-out |
| `references/refs-complexity-gate.md` | TRIVIAL vs SUBSTANTIVE classification (load from review) |
| `references/refs-verified-review.md` | Build-and-test protocol for empirical review (load from review) |
| `references/refs-author-test-plan.md` | Author-provided test plan extraction and merging (load from review) |

**Act**
| Reference | Purpose |
|---|---|
| `references/act-provision.md` | Provision: workspace setup (worktree, scratch, local infra, Snowflake, cloud) |
| `references/act-build.md` | Build: generator + parallel critics + verifier; scope discipline |
| `references/act-document.md` | Document: explain an existing artifact; drafter isolation + citation discipline |
| `references/act-prove.md` | Prove: automated tests, interactive/hybrid, state validation, citation audit, dry run |
| `references/act-ship.md` | Ship: multi-mode delivery (PR, publish, deploy, share, route-to-requestor) |
| `references/act-cleanup.md` | Cleanup: teardown + hygiene (Curate - supervised-dreaming memory pass, durable commit/push) |
| `references/refs-workspace-setup.md` | Worktree creation patterns, branch naming (load from provision) |
| `references/refs-security-review-triggers.md` | Conditions requiring a security Critic in Build (load from build) |
| `references/refs-findings-contract.md` | Why the Document drafter is codebase-blind; claim tracing (load from document) |
| `references/refs-guide-frontmatter.md` | Provenance metadata for externally-contributed guides (load from document/build) |

**Shared**
| Reference | Purpose |
|---|---|
| `references/refs-terminology.md` | Standard terminology across stages |
