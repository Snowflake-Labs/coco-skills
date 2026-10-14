---
name: decide-review
description: Review stage — a cold Critic evaluates a completed artifact and its supporting work-chain against a named reference; a Briefer renders the verdict; the orchestrator self-corrects before the operator gate. Load on the assess-for-gate and respond-to-feedback pathways.
---

# Review

Review evaluates a completed artifact against a reference and produces a justified verdict. It is the cold-critic counterpart to Build: Build proposes (warm), Review critiques (cold, fresh subagent that did not produce the artifact).

Review is a critical evaluation, not a synthesis of existing notes. Do not approve on the basis of passing tests alone. Review is **read-only**: never edit the code under review, not even an obvious fix — report the finding and make any fix in a separate, gated step.

## Required inputs

1. **Artifact** — the completed work to evaluate (a diff, a Build output, an incoming PR, empirical commits, a document).
2. **Reference** — the standard to judge against: the spec, plus the source files and the stated intent. If no spec exists (empirical / no-spec work), write a retrospective spec first (state what the work was required to do), then judge against it. Reviewing without a reference is prohibited.
3. **Work-chain artifacts** — the upstream outputs that justify the artifact: survey/analysis findings, workshop notes, test results. Review assesses these too (see Scope).
4. **Discussion state (for targets that have one)** — for a PR, a commented document, a Slack or issue thread: the existing comments, reviews, and thread resolutions. This is a first-class Review input, not background — a review of a PR that ignores the PR's own discussion is incomplete.

## Scope — evaluate the whole chain

Evaluate four dimensions, not the implementation alone:

1. **Spec compliance** — which requirements are met, unmet, or diverged; what is out of scope (scope creep).
2. **Test adequacy** — whether tests exercise the specified behaviour and its edge cases, or only pass; what is untested.
3. **Foundation soundness** — whether the upstream survey/analysis was sufficient and the chosen approach was justified, or whether the artifact rests on unverified assumptions.
4. **Risk** — regressions, side effects, necessity of each change, redundancy.

### Congruence with the discussion

Where the target has an existing discussion, evaluate your findings against it before forming the verdict: which points are already raised (by other reviewers or automated review), which are already resolved, which are genuinely new. Congruence is not conformity — you may dissent, but dissent must *engage* the prior comments, not ignore them; do not relitigate points already resolved.

## Step 1: Review Critic

Dispatch `xo-cold-smart` as the Review Critic (has evaluation authority and source access). Do not use `xo-bounded-writer` for this step — it cannot evaluate, only render.

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Reviewer — find where this work fails or is weak against its reference. Do not approve to satisfy; report defects. Your mandate is to find what is weak or wrong, not to ratify completion — dissent is the point.
  REFERENCE: [spec or retrospective spec + stated intent]
  INPUTS: [artifact + diff; source files; work-chain artifacts — survey/analysis/workshop notes, test results; discussion state — existing comments/reviews/thread resolutions, for targets that have one]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/review-critique.md

  Evaluate against the reference across four dimensions:
  - Spec compliance: requirements met / unmet / diverged; scope creep.
  - Test adequacy: do tests exercise specified behaviour and edge cases, or only pass; what is untested.
  - Foundation soundness: was upstream survey/analysis sufficient and the approach justified, or does the work rest on unverified assumptions.
  - Risk: regressions, side effects, necessity, redundancy.
  - Congruence with the discussion (targets that have one): which findings are already raised or resolved in the existing comments/reviews (including automated review), which are genuinely new. Flag any point that would relitigate a resolved thread. Dissent is allowed but must engage the prior comments.
  Cite evidence for every finding (file:line, spec section, test name). Rate each: blocker / major / minor.
  Include a `FILES_REVIEWED:` line listing every file or artifact actually reviewed.
  Report only findings supported by evidence. Do not assert unverified concerns and do not invent risks the artifact does not exhibit.
  Claims about the external world (URLs, third-party API behaviour, product/UI names, versions, external docs) are only as current as your training data. If you cannot verify such a claim with the tools available, mark it "unverified — needs live confirmation" and do NOT rate it blocker or major; a fact you could not check must not drive the verdict.
  Address all four dimensions explicitly — never silently omit one. If a dimension does not apply (e.g. the artifact has no test surface), state "not applicable" and why; do not skip it.
  End with a verdict (approve / request-changes / reject) and the evidence that supports it.
")
```

When the subject requires build/test verification (risk factors present), gather empirical evidence first (load `references/refs-verified-review.md`) and pass it to the Critic as INPUTS.

## Step 2: Briefer

Dispatch `xo-bounded-writer` to render the Critic's findings for the operator. The Briefer renders only; it does not produce or revise the verdict.

```
runSubagent(subagent_type: "xocortex:xo-bounded-writer", prompt: "
  PERSONA: Briefer
  INPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/review-critique.md (and any other review-*.md from this pathway)
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/review-brief.md

  Render an operator brief that states the verdict and the evidence justifying it. Structure:
  - Verdict (approve / request-changes / reject) + one-line justification.
  - Scope assessed: which artifact, against which reference.
  - Spec compliance: met / unmet / diverged.
  - Weaknesses and risks: each with severity and cited evidence.
  - Test and foundation adequacy: whether tests and upstream analysis are sufficient to trust the artifact. State test adequacy on its own explicit line — if the artifact has no test surface, say so and why that is acceptable; never omit this line.
  - For respond-to-feedback pathways: items addressed, items deferred, and the reason for each deferral.
  Lead with the verdict. State disagreements between findings. Cite sources.
")
```

## Step 3: Self-correct, then gate

1. Read `review-brief.md` before presenting it.
2. **Verify factual findings before you act on them.** If a blocker or major finding rests on a claim about the external world (a URL, third-party API behaviour, a product/UI name, a version, external docs) — especially from a Critic that lacked web/tool access — verify it against a live/authoritative source before it drives the verdict. A claim the Critic could not verify is not a defect until confirmed: downgrade or drop it if verification contradicts it. Then, for a finding that survives verification (unmet requirement, inadequate tests, unsound foundation), return to the relevant stage (Spec / Build / Prove), fix the defect, and re-run Review. Do not present a known-defective artifact to the operator — and do not present a false defect either.
3. When no blocker or major finding remains, present the brief to the operator, who gates: approve, request changes, or reject.
4. **Pre-post gate (remote/shared targets).** Before any irreversible action against a remote or shared target (posting a review, publishing, merging), two things must hold: the operator has approved the brief (item 3), and you have reconciled against the target's CURRENT state. Immediately before posting, re-fetch that state — **both code and discussion** — because it may have moved while you analysed; "the commit hasn't changed" is not sufficient evidence the object is unchanged. Reconcile your findings against it:
   - New commits changed the artifact → re-run the relevant part of Review.
   - New discussion (comments, reviews, resolutions) already covers your findings → do not duplicate. Switch to a lighter synthesis: endorse or relate to what is there, add only what is genuinely new, state your net position, and prefer leaving a standing review-state in place over re-submitting an echo. (Not a ban on full re-reviews — a full re-review is right when the artifact genuinely changed and the discussion does not already cover it.)
   - A point you were going to raise has been raised-and-resolved → drop it or acknowledge the resolution; never re-raise as if fresh.
5. **Verdict must match the action.** When the target supports distinct review actions (a PR: approve / request-changes / comment), submit the event that matches the verdict you presented — approve → approve, request-changes → request-changes, comment-only → comment. Never present "approve" and then post only a comment. After posting, verify the landed review state reflects the intended event.

## Load on demand

| Resource | When to load |
|---|---|
| `references/refs-complexity-gate.md` | respond-to-feedback pathway — classify each feedback item as trivial or substantive |
| `references/refs-verified-review.md` | risk factors present — gather build/test evidence to feed the Critic |
| `references/refs-author-test-plan.md` | the subject has an author-provided test plan |

## Chaining (soft — reminders, not gates)

- **Before advancing:** if this stage produced a finding worth keeping, update notes + task now.
- **Usually follows:** a completed artifact to evaluate — a Build, a Ship-candidate, an incoming PR, or empirical commits.
- **Leads to — primary:** return to Spec / Build / Prove when Review finds defects requiring iteration.
- **Or:** on acceptance — Capture (durable learnings), then Cleanup. Capture before teardown.
