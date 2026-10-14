---
name: act-prove
description: "Prove stage — demonstrate that the work satisfies the spec. Validation adapts to artifact type: automated tests (code), interactive/manual verification (UI/demos), state validation (configs/Snowflake), citation audit (documents), dry run (deployments). Dispatches appropriate subagents per mode."
---

# Prove

Demonstrate that the work satisfies the spec. This stage handles validation regardless of artifact type — the method adapts, but the discipline is the same: **before Ship, prove correctness.**

**Framing:** The stage is named "Prove" because proving correctness is the purpose, regardless of artifact type or validation method.

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
| TDD (test-first) | Building to a spec, highest confidence | Prove.author → Build → Prove.verify |
| Test-after | Verifying post-implementation | Build → Prove (author + verify) |
| Reproduce-bug | Bug fixes — reproduce first, then fix | Prove.reproduce → Build → Prove.verify |

### Prove.author — write tests

**Dispatch: xo-generator (test author)**

```
runSubagent(subagent_type: "xocortex:xo-generator", prompt: "
  PERSONA: Test-author
  SPECIFICATION: [spec file path — tests encode the acceptance criteria]
  INPUTS: [repo test conventions, test framework, existing test patterns from prior stages or Step 0]
  WORKTREE: [worktree path]
  
  Write tests that encode acceptance criteria from the spec.
  In TDD mode: tests should FAIL until implementation exists.
  Write to test files only — do not touch implementation code.
")
```

**Dispatch: xo-cold-smart Critic (test quality)**

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Critic — test quality
  REFERENCE: [spec]
  INPUTS: [worktree path, test files written by test-author]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/prove-test-critique.md
  
  Review: do tests meaningfully encode the spec's acceptance criteria?
  Are assertions substantive? Is coverage adequate?
  Return APPROVE, REVISE, or BLOCK.
")
```

### Prove.verify — execute and gate

**Dispatch: xo-cold-fast Tester**

```
runSubagent(subagent_type: "xocortex:xo-cold-fast", prompt: "
  PERSONA: Tester — execute and report
  INPUTS: [worktree path, test command from prior stages or Step 0]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/prove-test-results.md
  
  Run the test suite. Report: pass/fail counts, failing tests and output, warnings.
  Run focused new tests AND broader regression suite.
")
```

**Dispatch: xo-cold-smart Verifier**

```
runSubagent(subagent_type: "xocortex:xo-cold-smart", prompt: "
  PERSONA: Verifier — gate vs spec
  REFERENCE: [spec]
  INPUTS: [worktree path, $XOCORTEX_HOME/tmp/wi{N}/prove-test-results.md, critic verdicts]
  OUTPUT_PATH: $XOCORTEX_HOME/tmp/wi{N}/prove-verdict.md
  
  Verify: all spec requirements implemented and tested? All tests pass?
  Return VERIFIED or NOT_VERIFIED (gap + ROUTE_TO: generator/test-author/tester).
")
```

### Sub-mode: Reproduce-bug

1. Read bug report or failure evidence
2. Write a minimal reproduction test (xo-generator, test files only)
3. Confirm the test fails as expected (xo-cold-fast Tester)
4. Hand off to Build — the fix must make this test pass
5. Full Prove.verify after Build

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

Write `$XOCORTEX_HOME/tmp/wi{N}/prove-interactive-verdict.md`:
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

Write `$XOCORTEX_HOME/tmp/wi{N}/prove-state-verdict.md`:
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

Write `$XOCORTEX_HOME/tmp/wi{N}/prove-citation-verdict.md`:
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

Write `$XOCORTEX_HOME/tmp/wi{N}/prove-dryrun-verdict.md`:
- Steps executed
- Results per step
- Issues encountered
- VERIFIED or NOT_VERIFIED

---

## CALIBRATE note

CALIBRATE: In TDD mode, Prove.author runs before Build and Prove.verify runs after — the orchestrator interleaves these around Build. The exact handoff timing may need tuning in real use. The intent is clear; the mechanics will be refined.

---

## Output contract

A verdict file in `$XOCORTEX_HOME/tmp/wi{N}/prove-*-verdict.md` — VERIFIED or NOT_VERIFIED with routing (what to fix and where to go).

After VERIFIED, proceed to Ship.

---

## Chaining (soft — reminders, not gates)

- **Usually follows:** Build (validate the produced artifact) or Document (citation audit on the explanation) — or Spec (TDD: Prove.author writes tests before Build).
- **Leads to — primary:** Ship (deliver the work).
- **Or:** back to Build (redo loop on a failing verdict); Review (self-validation before shipping).
