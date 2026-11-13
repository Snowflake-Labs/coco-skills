---
name: act-prove
description: "Prove stage — demonstrate that the work satisfies the spec. Validation adapts to artifact type: automated tests (code), interactive/manual verification (UI/demos), state validation (configs/Snowflake), citation audit (documents), dry run (deployments). Dispatches appropriate subagents per mode."
---

# Prove

Demonstrate that the work satisfies the spec. This stage handles validation regardless of artifact type — the method adapts, but the discipline is the same: **before Ship, prove correctness.**

**Framing:** The stage is named "Prove" because proving correctness is the purpose, regardless of artifact type or validation method.

## Calibrate verification depth

If the spec doesn't pin down how much verification is required, present the options (e.g. unit only / unit + integration / full live before-after) and let the operator choose; default to the lightest sufficient level and escalate only on request. Floor for code changes destined for a shared-repo PR: always re-verify by running the relevant tests — static analysis alone is never enough. This holds even when restoring previously-tested code or deleting code (both can introduce regressions).

For a bug fix, the failing command from Reproduce-bug (below) is the floor and is never skipped; the depth chosen here governs how much verification follows the fix.

**Author exercises before review.** Whoever produced the change runs it and exercises the actual behaviour before it goes to Review or Ship — which surface, which states, which OS/remote if the change can reach those. "It compiles" is not verification, and a reviewer must never be the first person to run the code.

---

## Validation mode decision

| Artifact type | Validation mode | Method |
|---|---|---|
| Code (logic, APIs, data transforms) | **Automated tests** | Write + run tests (TDD, test-after, or reproduce-bug) |
| UI, visual changes, interactive apps | **Interactive/hybrid** | Agent builds + operator verifies in browser/IDE |
| Snowflake objects, configs, infra | **State validation** | Query/check that desired state is achieved |
| Documents, guides, analyses | **Citation audit** | Verify claims against evidence; check completeness |
| Deployments, demos | **Dry run** | Execute the flow end-to-end in staging/preview |

The modes aren't exclusive — complex work may combine them (e.g., automated tests + interactive validation for a web app).

---

## Mode: Automated tests (code)

### Testability assessment

| Change type | Testability | Path |
|---|---|---|
| Logic, algorithms, data transformations | High | Proceed to test authoring |
| API endpoints, service integration | High | Proceed |
| UI behaviour, visual changes | Often low | Consider interactive/hybrid mode |
| Configuration, build changes | Variable | Assess case by case |

**When automated testing isn't practical**, present to operator:

> "This change [describe] is difficult to test automatically because [reason].
>
> Options:
> (a) Proceed with limited automated tests (coverage: [what can be tested])
> (b) Switch to interactive mode — I'll build/deploy, you verify the behaviour
> (c) Suggest a testing approach I may have missed"

### Sub-modes

| Sub-mode | When | Sequence |
|---|---|---|
| TDD (test-first) | Building to a spec, highest confidence | Prove.author (at most one failing test per acceptance criterion) → Build (further tests added one at a time, only as new behaviour appears) → Prove.verify |
| Test-after | Verifying post-implementation | Build → Prove (author + verify) |
| Reproduce-bug | Bug fixes: a failing command first, then the fix | Prove.reproduce → Build → Prove.verify |

### Prove.author — write tests

Load `references/refs-test-design.md` before dispatching. Prove.author writes tests only against the acceptance criteria and the public interfaces confirmed at the Spec gate.

**Dispatch: xo-generator (test author)**

```
runSubagent(subagent_type: "xocortex:xo-generator", prompt: "
  PERSONA: Test-author
  SPECIFICATION: [spec file path: acceptance criteria and confirmed interfaces to test]
  INPUTS: [repo test conventions, test framework, existing test patterns from prior stages or Step 0; references/refs-test-design.md]
  WORKTREE: [worktree path]
  
  Follow references/refs-test-design.md.
  Write a test only where it traces to an acceptance criterion or a confirmed interface, and record that trace in the test name or a comment beside it. Do not write a test you cannot trace.
  In TDD mode: write at most one failing test per acceptance criterion. Each must FAIL until the implementation exists. Further tests come during Build, one at a time, as new behaviour appears.
  Write to test files only — do not touch implementation code.
")
```

**Dispatch: xo-cold-smart Critic (test quality)**

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Critic — test quality
  REFERENCE: [spec; references/refs-test-design.md]
  INPUTS: [worktree path, test files written by test-author]
  OUTPUT_PATH: $XO_SCRATCH/prove-test-critique.md
  
  Review: do tests meaningfully encode the spec's acceptance criteria?
  Are assertions substantive? Does each acceptance criterion have exactly the tests it needs, with no gaps and no extras?
  Mark every test keep, cut, or rewrite, citing the reason, using the cut-or-rewrite conditions in references/refs-test-design.md.
  List any older tests the new ones supersede, as candidates for deletion.
  Return APPROVE, REVISE, or BLOCK.
")
```

### Prove.verify — execute and gate

**Dispatch: xo-cold-fast Tester**

```
runSubagent(subagent_type: "xocortex:xo-cold-fast", prompt: "
  PERSONA: Tester — execute and report
  INPUTS: [worktree path, test command from prior stages or Step 0]
  OUTPUT_PATH: $XO_SCRATCH/prove-test-results.md
  
  Run the test suite. Report: pass/fail counts, failing tests and output, warnings.
  Run focused new tests AND broader regression suite.
")
```

**Dispatch: xo-cold-smart Verifier**

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Verifier — gate vs spec
  REFERENCE: [spec]
  INPUTS: [worktree path, $XO_SCRATCH/prove-test-results.md, critic verdicts; reproduce-bug only: $XO_SCRATCH/prove-repro-before.md, $XO_SCRATCH/prove-repro-after.md, and the Reproduce-bug "Done when" checklist]
  OUTPUT_PATH: $XO_SCRATCH/prove-verdict.md
  
  Verify: all spec requirements implemented and tested? All tests pass?
  When multiple discrete requirements or gates apply, render them as a `- [ ]`/`- [x]` checklist per the Output contract (a checked box is a pass or an explicit `- [x] N/A — {why}`; name the missing artifact for any unchecked item).
  Return VERIFIED or NOT_VERIFIED (gap + ROUTE_TO: generator/test-author/tester).
")
```

**Re-run the relevant test suite after any lint autofix.** Lint autofixes (`ruff --fix`, `--unsafe-fixes`, or a manual rename to satisfy a rule) can be semantically wrong — a lint rule reasons about the flagged line, not whole-function data flow. After applying any autofix, re-run the tests covering the changed code before committing (the whole suite only if the change is broad). If a lint rule conflicts with correct code (e.g. a loop variable used after the loop), prefer a per-file-ignore over contorting the code.

### Sub-mode: Reproduce-bug

No fix is written, and no theory of the cause is formed or tested, until a command fails on the reported symptom and you have shown it running. If the bug report supplies a command or input, run it before opening the suspect code. Read code to build a harness or command only when no runnable command exists yet.

**Roles.** An `xo-generator` reproducer builds the reproduction and any temporary instrumentation, in the worktree or scratch. The `xo-cold-fast` Tester runs it and reports. The orchestrator owns the candidate causes and the operator checkpoint.

**Redact secrets** (tokens, keys, passwords, connection strings) in every command, output, and artifact you show or record.

#### 1. A failing command

The command must:
- check the specific symptom the operator reported, not only that nothing crashes;
- give the same result on every run, run it at least twice to establish this. If the bug is intermittent, run it enough times to state a reproduction rate (for example "fails 7 of 20 runs") and report that rate instead;
- run without a person, unless the only way to reproduce is a script that pauses for a human step.

Ways to get one, cheapest first:
1. An existing test that already fails, or a new failing test.
2. An HTTP or CLI call against a fixture.
3. Replaying a captured input (request, payload, log line, file).
4. A minimal harness that drives just the failing code path.
5. Bisecting between a known-good and a known-bad state (commit, version, config).
6. Running two versions side by side on the same input and diffing the output.

```
runSubagent(subagent_type: "xocortex:xo-generator", prompt: "
  PERSONA: Reproducer
  SPECIFICATION: [bug report or failure evidence; the symptom as the operator reported it]
  INPUTS: [repo test conventions, test/run commands, any captured artifacts]
  WORKTREE: [worktree path, or $XO_SCRATCH for throwaway harnesses]

  Build one command that fails on the reported symptom. Try the cheapest route first:
  existing or new failing test, HTTP/CLI call against a fixture, replayed captured input,
  minimal harness, bisection, differential run of two versions.
  The command checks the reported symptom, gives the same result every run (or you state a
  reproduction rate), and needs no person unless only a human-in-the-loop script can do it.
  Do not fix anything. Do not propose causes. Redact secrets in everything you write.
  Return the exact command, or list what you tried and why each failed to reproduce.
")
```

Dispatch the same Reproducer persona, with the task as its SPECIFICATION, to minimise the reproduction (step 2) and to write the regression test (step 3).

```
runSubagent(subagent_type: "xocortex:xo-cold-fast", prompt: "
  PERSONA: Tester — execute and report
  INPUTS: [worktree path, reproduction command]
  OUTPUT_PATH: $XO_SCRATCH/prove-repro-before.md (before the fix) or $XO_SCRATCH/prove-repro-after.md (after it)

  Run the command at least twice (more if intermittent). Record the invocation and each
  run's output. State the count and failures over runs.
  Redact secrets in everything you record.
")
```

Record every run, whether the Tester or you run it: the invocation and each run's output go in `$XO_SCRATCH/prove-repro-before.md` now, and in `$XO_SCRATCH/prove-repro-after.md` once the fix is in (step 3). Prove.verify reads both.

Show the operator the invocation and its output before handing off to Build. When running unattended, write the invocation and its verbatim output to `$XO_SCRATCH/checkpoint-repro.md` before any edit to a non-test file, and do not edit it afterwards. Later notes go in the WI notes.

**If no failing command can be built, stop.** List what was tried and ask the operator for access, a captured artifact, or permission to add instrumentation. Do not propose causes or fixes until a command fails.

#### 2. Scale to the difficulty

The cause is evident when you can record the chain from symptom to cause fact by fact, and each fact cites the failing command's output or an input the command uses (a fixture, data file, or request body). Comments and names in the code do not count. Record that chain and go straight to the regression test and the fix (step 3).

If any link in the chain needs the suspect code, run the full loop:
1. **Minimise.** Have the reproducer cut the reproduction down until every remaining part is needed for it to fail.
2. **Hypothesise.** Write 3–5 candidate causes. Give each a prediction that would confirm or rule it out. Rank them and present the list to the operator at a checkpoint. Wait for the operator before testing them, unless the operator has told you to proceed unattended.
3. **Test one change at a time.** Change a single thing per run and record what each run showed. Tag every temporary debug line with one unique marker (for example `XO-DEBUG-<short id>`), so a single search finds them all for cleanup.

#### 3. Regression test, then fix

The reproducer writes the regression test (test files only), following `references/refs-test-design.md`. It traces to the reported symptom (or the bug's acceptance criterion) and needs no interface confirmation. The test must reach the bug through the same path the failure takes. If the failing command is already such a test, it is the regression test. If no test can exercise the real failure path, record that as a finding rather than writing a weaker test.

Hand off to Build. The fix must make the failing command pass.

After the last code change, the Tester re-runs the failing command the same number of times as the before-fix run, runs the test suite once, and records both in `prove-repro-after.md`. For an intermittent bug, state the new failure rate. If the code changes again, re-run both and rewrite `prove-repro-after.md`. That run is the evidence.

#### 4. Done when

- [ ] the run in `$XO_SCRATCH/prove-repro-after.md`, taken after the last code change, passes;
- [ ] the regression test passes, or the finding that no faithful test exists is recorded;
- [ ] a search for the debug marker returns nothing;
- [ ] the confirmed cause is recorded in the WI notes, for the commit message and PR body.

Then run full Prove.verify, at the depth set in "Calibrate verification depth".

---

## Mode: Interactive/hybrid (UI, apps, demos)

For work that requires human observation to validate — visual correctness, interaction flows, UX behaviour, or anything not easily captured in automated assertions.

### Process

1. **Agent deploys/starts the artifact** — run the app, start the server, open the preview
2. **Present to operator** what to verify:
   > "The [app/page/demo] is running at [URL/location]. Please verify:
   > - [ ] [Acceptance criterion 1 from spec]
   > - [ ] [Acceptance criterion 2]
   > - [ ] [Visual/behavioural criterion]
   >
   > Let me know what passes, what fails, and any issues you observe."
3. **Operator tests and reports** — interacts with the artifact, provides feedback
4. **Agent records the result** — writes verdict based on operator observations

### When to use

- Browser-based applications (the IDE has a built-in browser)
- Streamlit apps (the IDE can run them directly)
- Visual/layout changes where screenshots don't capture the full picture
- Interactive flows (multi-step forms, navigation, drag-and-drop)
- Anything where "does it look and feel right?" is the acceptance criterion

### Output

Write `$XO_SCRATCH/prove-interactive-verdict.md`:
- What was tested (list from spec)
- What operator confirmed
- Any issues reported
- VERIFIED or NOT_VERIFIED

---

## Mode: State validation (configs, Snowflake objects, infrastructure)

For work that produces system state rather than files — Snowflake objects, IAM roles, network configs, deployment infrastructure.

### Process

1. **Define expected state** from spec (objects exist, permissions granted, data flows correctly)
2. **Run validation queries/checks:**
   ```sql
   -- Example: verify objects exist and are configured correctly
   DESCRIBE TABLE {expected_table};
   SHOW GRANTS ON SCHEMA {target_schema};
   SELECT COUNT(*) FROM {pipeline_output} WHERE {quality_condition};
   ```
3. **Compare actual vs expected** — automated where possible
4. **Write verdict** with evidence (query results)

### When to use

- Snowflake DDL (tables, views, procedures, tasks, stages)
- Permission/role configurations
- Data pipeline validation (rows flow, quality checks pass)
- Infrastructure-as-code deployments
- Environment provisioning verification

### Output

Write `$XO_SCRATCH/prove-state-verdict.md`:
- Expected state (from spec)
- Actual state (from queries/checks)
- Delta (if any)
- VERIFIED or NOT_VERIFIED

---

## Mode: Citation audit (documents, guides, analyses)

For work that makes factual claims — verify claims are backed by evidence, not hallucinated or stale.

### Process

1. **Extract claims** from the document
2. **Trace each claim** back to its source (findings brief, code, test results, external reference)
3. **Check completeness** — does the document address all spec requirements?
4. **Check staleness** — are sources current?

This mode is already embedded in the Document stage's Critic step. Use it here when the Document stage was skipped (e.g., a standalone analysis that went straight from Build to Prove).

### Output

Write `$XO_SCRATCH/prove-citation-verdict.md`:
- Claims checked (count)
- Unbacked claims (if any)
- Completeness vs spec
- VERIFIED or NOT_VERIFIED

---

## Mode: Dry run (deployments, demos, presentations)

For work that needs to execute end-to-end in a staging or preview environment before going live.

### Process

1. **Execute the full flow** in preview/staging — deploy, navigate, demonstrate
2. **Check each acceptance criterion** from spec
3. **Record any issues** — broken flows, missing assets, environment-specific failures
4. **Present results** to operator for sign-off

### When to use

- Before deploying to production (after staging works locally)
- Demo rehearsals (before showing to stakeholders)
- Multi-step workflows where each step depends on the previous

### Output

Write `$XO_SCRATCH/prove-dryrun-verdict.md`:
- Steps executed
- Results per step
- Issues encountered
- VERIFIED or NOT_VERIFIED

---

## CALIBRATE note

CALIBRATE: In TDD mode, Prove.author runs before Build and Prove.verify runs after — the orchestrator interleaves these around Build. The exact handoff timing may need tuning in real use. The intent is clear; the mechanics will be refined.

---

## Test rigs stay up for the round

When tests need standing infrastructure (a sandbox, a runtime, test objects), set it up once and keep it up across the round (the set of tests you run before reporting back to the operator). Run the tests, report, and let the operator decide whether more are needed; rebuilding after every test wastes time. Record the ids in scratch and list what's still standing in the findings. Teardown still gets tested, at the end, once the operator agrees (see `references/act-cleanup.md`).

---

## Output contract

A verdict file in `$XO_SCRATCH/prove-*-verdict.md` — VERIFIED or NOT_VERIFIED with routing (what to fix and where to go).

When the verdict spans multiple discrete criteria or gates, render them as an enumerated checkbox list — one `- [ ]`/`- [x]` line per criterion — not prose. `- [x]` is a verified pass or an explicit `- [x] N/A — {why}`; `- [ ]` is reserved for a FAIL or an unverified criterion, and for each of those name the **specific** missing artifact ("no test on the cancellation path", "packaged build not smoked"), never a generic "add coverage". An unverified criterion is not a pass. Do not report the work as done or ready while any criterion is unverified — carry the open item into the status you report.

After VERIFIED, proceed to Ship.

---

## Chaining (soft — reminders, not gates)

- **Usually follows:** Build (validate the produced artifact) or Document (citation audit on the explanation) — or Spec (TDD: Prove.author writes tests before Build; reproduce-bug: Prove.reproduce makes the failing command before Build).
- **Leads to — primary:** Ship (deliver the work).
- **Or:** back to Build (redo loop on a failing verdict); Review (self-validation before shipping).
