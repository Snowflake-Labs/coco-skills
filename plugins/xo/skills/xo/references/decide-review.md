---
name: decide-review
description: Review stage — cold Critics (spec-and-chain, and standards for code diffs) evaluate a completed artifact and its supporting work-chain against a named reference; a Briefer renders the verdict; the orchestrator self-corrects before the operator gate. Load on the assess-for-gate and respond-to-feedback pathways.
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

**Scope boundary for external PRs.** When reviewing a PR authored by another team or an external contributor (a repo you don't own or set standards for), restrict findings to the four dimensions above. Do not raise process/governance findings (missing evaluations, CI-coverage mandates, contribution-convention violations) unless the operator asks or you own the repo's standards. Process authority belongs to the repo owner.

### Congruence with the discussion

Where the target has an existing discussion, evaluate your findings against it before forming the verdict: which points are already raised (by other reviewers or automated review), which are already resolved, which are genuinely new. Congruence is not conformity — you may dissent, but dissent must *engage* the prior comments, not ignore them; do not relitigate points already resolved.

### Code correctness and product/UX acceptance are separate gates

Code-correctness review and product/UX sign-off are distinct gates with different owners, and they are ordered: correctness first, product/UX after. A clean code review does not stand in for product or UX acceptance, and approving the code does not approve the experience — a user-visible change still needs its own product/UX owner's sign-off.

### Self-validation (work built without a spec)

Empirical or exploratory work that was built before any spec is validated after the fact, not forced back through Spec-first Build or TDD. This is a valid route; do not insist on a forward spec for work that already exists.

1. Write a retrospective spec in the WI notes: what the change fixes or adds and why, the expected side effects, and the success criteria.
2. Run Steps 1–3 with that retrospective spec as the reference. The Spec-and-chain Critic's risk dimension covers necessity (every change needed for the stated purpose), correctness, side effects, and redundancy.

## Step 1: Review Critics

Dispatch two `xo-cold-smart` Critics in parallel, in a single message (both have evaluation authority and source access). Do not use `xo-bounded-writer` for this step — it cannot evaluate, only render.

- **Spec-and-chain Critic** (first prompt below): the four dimensions and congruence with the discussion.
- **Standards Critic** (second prompt below): the diff against the repo's documented code standards and the smell baseline in `references/refs-code-smells.md`. For external PRs (see "Scope boundary for external PRs"), do not dispatch it unless the operator asks.

The Standards Critic applies to code diffs. For an artifact with no code (a document, a spec), dispatch only the Spec-and-chain Critic.

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Spec-and-chain Critic — find where this work fails or is weak against its reference. Do not approve to satisfy; report defects. Your mandate is to find what is weak or wrong, not to ratify completion — dissent is the point.
  REFERENCE: [spec or retrospective spec + stated intent]
  INPUTS: [artifact + diff; source files; work-chain artifacts — survey/analysis/workshop notes, test results; discussion state — existing comments/reviews/thread resolutions, for targets that have one]
  OUTPUT_PATH: $XO_SCRATCH/review-critique.md

  How to read (method — apply before judging any line):
  - Work from the diff to see what changed, then read each changed file in full to judge that change in context — the diff scopes the delta, the full file (and its call sites, next bullet) is what you judge it against. Neither alone suffices: diff-only misses the surrounding context; file-only loses track of what actually changed. For an outsized file, read enough around and connected to the change to judge it soundly.
  - Read one hop out — the producers and consumers / call sites of every new event, gate, flag, cache key, or disposed object the change introduces.
  - Trace every branch — error, cancel, timeout, dispose, cold-start — not only the happy path; the blocker usually lives in the branch the author did not walk.
  - Hold advertised guarantees to the code: any comment, doc, or PR-description claim (validation, "callers wait", "fire-and-forget") is checked against the actual enforcement, never accepted at face value.

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
  Separately from the verdict, flag any **empirical gate**: the code reads as sound but a required verification step for this artifact has not been run yet (CI not green, a packaged build not smoked, a live state/query result not yet checked). This is distinct from an unverifiable external-world claim above — that must not drive the verdict; an empirical gate is a **hold pending confirmation, not a defect**, reading "correct pending confirmation of X". Its resolution is to run the check, not to change the code, so do not rate it blocker or reject on its account. An open gate does not by itself lower an otherwise-clean verdict: if the four dimensions are sound, the verdict stays approve with the gate noted as a pending-confirmation hold. This covers genuinely pending external checks (CI, build smoke, live-state) only — never the untested core function of an artifact meant to be run (e.g. a shareable runbook's own steps). That is inadequate test coverage: rate it on the test-adequacy dimension, not as a benign gate.
  End with a verdict (approve / request-changes / reject) and the evidence that supports it.
")

runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Standards Critic — judge the diff against the repo's own code standards. Report defects; do not approve to satisfy.
  REFERENCE: [the repo's documented code standards: CODING_STANDARDS.md, the code-style parts of CONTRIBUTING.md and AGENTS.md, lint and formatter config; references/refs-code-smells.md]
  INPUTS: [artifact + diff; changed files in full]
  OUTPUT_PATH: $XO_SCRATCH/review-standards.md

  For each changed file or hunk, report:
  - every breach of a documented repo standard, citing the file and the rule. Rate each blocker / major / minor.
  - any smell from references/refs-code-smells.md that the diff introduces or worsens, as 'possible [smell]' with the hunk. Smells are judgement calls: rate them minor.
  Apply the three rules in references/refs-code-smells.md: the repo's documented standards win over the smell list, a smell alone never justifies request-changes, and anything tooling already enforces is skipped.
  If the repo documents no code standards, say so and judge against the smell baseline alone.
  Include a `FILES_REVIEWED:` line listing every file actually reviewed, and a `STANDARDS_SOURCES:` line listing the standards files you read.
  Report only findings supported by evidence.
  End with a verdict (approve / approve-with-comments / request-changes / reject). If the only findings are smells, the verdict is approve-with-comments at most.
")
```

When the subject requires build/test verification (risk factors present), gather empirical evidence first (load `references/refs-verified-review.md`) and pass it to the Spec-and-chain Critic as INPUTS.

## Step 2: Briefer

Dispatch `xo-bounded-writer` to render the Critics' findings for the operator. The Briefer renders only; it does not produce or revise the verdict.

```
runSubagent(subagent_type: "xocortex:xo-bounded-writer", prompt: "
  PERSONA: Briefer
  INPUT_PATH: $XO_SCRATCH/review-critique.md, $XO_SCRATCH/review-standards.md when present (and any other review-*.md from this pathway)
  OUTPUT_PATH: $XO_SCRATCH/review-brief.md

  Render an operator brief that states the verdict and the evidence justifying it. Structure:
  - Verdict (approve / approve-with-comments / request-changes / reject) + one-line justification. The verdict is the more severe of the two axes' verdicts (reject > request-changes > approve-with-comments > approve). Smell-only findings lower it to approve-with-comments at most.
  - Scope assessed: which artifact, against which reference.
  - Under a **Standards** heading: documented-standard breaches and possible smells, each with severity and cited evidence; name the worst finding. If the Standards Critic was not dispatched, say so and why.
  - Under a **Spec and chain** heading (naming the worst finding):
    - Spec compliance: met / unmet / diverged.
    - Weaknesses and risks: each with severity and cited evidence.
    - Test and foundation adequacy: whether tests and upstream analysis are sufficient to trust the artifact. State test adequacy on its own explicit line — if the artifact has no test surface, say so and why that is acceptable; never omit this line.
  - Empirical gates (if any): list any required-but-not-yet-run check (CI, build smoke, live-state) separately from weaknesses/risks, with no severity attached — it is a pending-confirmation hold, not a defect.
  - For respond-to-feedback pathways: items addressed, items deferred, and the reason for each deferral.
  Lead with the verdict. State disagreements between findings. Cite sources.
")
```

## Step 3: Self-correct, then gate

1. Read `review-brief.md` before presenting it.
2. **Verify factual findings before you act on them.** If a blocker or major finding rests on a claim about the external world (a URL, third-party API behaviour, a product/UI name, a version, external docs) — especially from a Critic that lacked web/tool access — verify it against a live/authoritative source before it drives the verdict. A claim the Critic could not verify is not a defect until confirmed: downgrade or drop it if verification contradicts it. Then, for a finding that survives verification (unmet requirement, inadequate tests, unsound foundation), return to the relevant stage (Spec / Build / Prove), fix the defect, and re-run Review. Do not present a known-defective artifact to the operator — and do not present a false defect either. A deeper pass may supersede an earlier verdict — your own or a prior reviewer's: if it surfaces a blocker after an approve, revoke the approval and say so plainly ("superseding my earlier approval"). A prior approve is not a floor. An **empirical gate** (the code reads as sound but a required check simply has not been run) is not a defect: resolve it by running the check — route to Prove, or wait for the pending CI/build — before presenting the brief; do not route it through "fix the defect", and do not let it alone force a request-changes.
3. When no blocker or major finding remains, run the milestone awareness gate check (`references/core-guidelines.md`, Freshness and concurrency), then present the brief to the operator, who gates: approve, approve with comments, request changes, or reject. Always surface any open empirical gate in that brief — never drop it — and never report the artifact as done/ready/shippable/PASS while a gate is open: its status is "correct pending confirmation of X" until the check runs or the operator accepts the risk.
4. **Pre-post gate (remote/shared targets).** Before any irreversible action against a remote or shared target (posting a review, publishing, merging), two things must hold: the operator has approved the brief (item 3), and you have reconciled against the target's CURRENT state. Immediately before posting, re-fetch that state — **both code and discussion** — because it may have moved while you analysed; "the commit hasn't changed" is not sufficient evidence the object is unchanged. Reconcile your findings against it:
   - New commits changed the artifact → re-run the relevant part of Review.
   - New discussion (comments, reviews, resolutions) already covers your findings → do not duplicate. Switch to a lighter synthesis: endorse or relate to what is there, add only what is genuinely new, state your net position, and prefer leaving a standing review-state in place over re-submitting an echo. (Not a ban on full re-reviews — a full re-review is right when the artifact genuinely changed and the discussion does not already cover it.)
   - A point you were going to raise has been raised-and-resolved → drop it or acknowledge the resolution; never re-raise as if fresh.
5. **Verdict must match the action.** When the target supports distinct review actions (a PR: approve / request-changes / comment), submit the event that matches the verdict you presented — approve → approve, approve-with-comments → approve with the comments in the review body, request-changes → request-changes, comment-only → comment. Never present "approve" and then post only a comment. After posting, verify the landed review state reflects the intended event. An empirical gate is a hold, not a review event: never post request-changes (or a comment) on a remote/shared target merely because a check is still pending — hold the review, let the check complete, then reconcile (item 4) and post the verdict that then applies.

## Load on demand

| Resource | When to load |
|---|---|
| `references/refs-complexity-gate.md` | respond-to-feedback pathway — classify each feedback item as trivial or substantive |
| `references/refs-verified-review.md` | risk factors present — gather build/test evidence to feed the Spec-and-chain Critic |
| `references/refs-author-test-plan.md` | the subject has an author-provided test plan |
| `references/refs-code-smells.md` | Standards Critic dispatched (Step 1) — smell baseline for judging the diff |

## Chaining (soft — reminders, not gates)

- **Before advancing:** if this stage produced a finding worth keeping, update notes + task now.
- **Usually follows:** a completed artifact to evaluate — a Build, a Ship-candidate, an incoming PR, or empirical commits.
- **Leads to — primary:** return to Spec / Build / Prove when Review finds defects requiring iteration.
- **Or:** on acceptance — Capture (durable learnings), then Cleanup. Capture before teardown.
