---
name: act-build
description: "Build stage — produce the artifact against the spec. xo-generator (proposer) + parallel xo-cold-smart Critics + xo-cold-smart Verifier. Works for any artifact type: code, documents, configurations, SQL objects, apps. Scope discipline is mandatory."
---

# Build

Produce the artifact specified by the spec. The generator creates; the critics review from multiple angles in parallel; the verifier gates the full package. This pattern applies regardless of artifact type — code, documents, configurations, SQL objects, apps, or data pipelines.

## When to use this stage

- Producing any artifact against an approved spec
- Code changes in a worktree, SQL objects in Snowflake, documents in scratch space, apps in a dev environment
- Whenever the work transitions from "what should we produce" (Spec) to "produce it"

---

## Input contract

- Approved spec (`notes/{YYYY-MM}/{project}-wi{N}-spec.md`)
- Workspace location (from Provision — worktree path, scratch dir, or Snowflake schema context)
- Production conventions (from Step 0 discovery — see below)
- In TDD mode: failing tests written by Prove (author)

---

## Step 0: Load context (mandatory before producing)

Before dispatching the generator, confirm you have production conventions available. If prior stages (Survey, Analyse, Spec) already established these, **load them from notes/findings — do not re-derive**. Step 0 is a cache check, not a survey.

**If conventions are already known** (from Observe findings, spec notes, or prior session notes for this repo/workspace):
- Load the relevant findings or knowledge note
- Pass conventions directly to the generator

**If conventions are NOT yet known** (short-pathway skip, reactive fix, first touch on this workspace):
- Do minimal reconnaissance appropriate to the artifact type (see below)
- Record findings in WI notes so future stages don't repeat this

### Minimal reconnaissance (only when prior stages didn't cover this)

**Repository work:** Read `AGENTS.md`/`CLAUDE.md`; scan patterns near the change site; identify build/test commands; check security-relevant context (see `references/refs-security-review-triggers.md`)

**Snowflake objects:** Check existing naming conventions in target schema; confirm role/permissions context; identify existing patterns

**Documents/guides:** Check style guide or existing docs; identify format requirements; locate evidence sources

If the produced artifact is a shippable guide or doc (for example a README or reference doc), apply `references/refs-guide-frontmatter.md` provenance frontmatter. See Document.

**Apps/demos:** Check framework conventions; identify available services; confirm runtime environment

---

## Step 1: Generator (xo-generator)

The only agent with write authority for the artifact.

```
runSubagent(subagent_type: "xocortex:xo-generator", prompt: "
  PERSONA: Proposer — produce the artifact
  SPECIFICATION: [spec file path or requirements]
  INPUTS: [conventions from prior stages or Step 0, existing context]
  WORKTREE: [workspace path]
  
  Produce what the specification requires.
  Read existing work near each change site to understand patterns and style before editing.
  Change ONLY what the specification requires — report anything out-of-scope under OBSERVED.
  Output a REQUIREMENT/FILE/CHANGE/RATIONALE record for each change made.
")
```

---

## Step 2: Parallel critics (xo-cold-smart)

**Always dispatch:** general critic (scope, correctness, conventions).

**Also dispatch as appropriate:** security critic (when security triggers apply — see `references/refs-security-review-triggers.md`), architecture critic (cross-cutting changes), API surface critic (public interface changes).

Check `references/refs-security-review-triggers.md` to determine if security persona is mandatory.

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Critic — general correctness and scope
  REFERENCE: [spec]
  INPUTS: [worktree path, incremental diff: git diff HEAD, branch diff: git diff main..HEAD]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/build-critic-general.md
  
  Review the implementation diff for:
  1. Scope compliance (only what the spec requires)
  2. Safety and correctness
  3. Repo convention adherence
  4. Collateral damage (changes outside spec scope)
  5. Foundations — is the implementation grounded in the spec/findings, or built on speculation? Flag plausible-but-ungrounded choices and name the missing basis.
  
  Return APPROVE, REVISE (specific concern + suggestion + severity), or BLOCK (red lines).
")

runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Critic — security
  REFERENCE: [change description + what security boundaries exist]
  INPUTS: [worktree path, incremental diff]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/build-critic-security.md
  
  Review for security boundary violations, credential exposure, and trust boundary crossings.
  Map process/trust boundaries. Trace credential flow. Evaluate the security checklist.
  Return SAFE, REVISE, or BLOCK.
  [Load security-review-triggers.md criteria]
")
```

### Critic routing

| Verdict | Action |
|---|---|
| All APPROVE / SAFE | Proceed to Verifier |
| Any REVISE | Return generator with critic feedback; re-implement; re-run critics |
| Any BLOCK | Escalate to operator — do not continue until resolved |

**Maximum 3 cycles per artifact-critic loop.** After 3 rejections in the same loop, escalate — repeated attempts degrade quality. A new operator correction starts a fresh loop; it does not consume the prior cap.

---

## Step 3: Verifier (xo-cold-smart)

Unified gate: code + critics + tests vs spec.

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Verifier — unified gate
  REFERENCE: [spec]
  INPUTS: [worktree path, critic verdicts from $XOCORTEX_HOME/tmp/wi{N}/, test results if available]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/build-verifier.md
  
  Verify the full package against the spec:
  1. Each requirement: implemented? tested? test passed?
  2. Scope compliance: no changes outside spec?
  3. Test adequacy: do tests cover requirements (not just implementation)?
  4. Repo compliance: conventions followed, no debug code left?
  
  Return VERIFIED (ready to commit) or NOT_VERIFIED (specify gap + ROUTE_TO: generator/tester).
")
```

---

## Output contract

After VERIFIED: Build is complete. The artifact satisfies the spec per the Verifier's assessment. Present to operator:

> "The [artifact] is ready — Verifier passed. [Brief summary of what was produced]. [Materiality of any correction applied: TRIVIAL (noted) or SUBSTANTIVE (re-critique found [issue], changed [what changed] since that feedback).] How would you like to proceed?"

Do not push to Ship unprompted. The operator decides the next step. Typical routes:
- **Prove** (validate it works — tests, interactive verification, state check)
- **Review** (`references/decide-review.md` — formal operator gate with brief)
- **Ship** (only if Prove was already done in TDD mode, or operator explicitly approves direct delivery)

---

## Calibration note

CALIBRATE: The parallel critics pattern (dispatch simultaneously, merge verdicts) is the default. For simple changes, dispatching a single general critic may be sufficient — use judgment on whether security persona is warranted.

---

## Chaining (soft — reminders, not gates)

- **Before advancing:** update notes + task with what was built and the verifier verdict.
- **Usually follows:** Provision (workspace is set up) + Plan/Spec. No approved plan? Consider Plan first.
- **Leads to — primary:** Prove (validate the artifact satisfies the spec).
- **Or:** Review (formal operator gate before proceeding); Ship directly for trivial, low-risk deliverables.
