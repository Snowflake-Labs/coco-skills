---
name: batch-runner
description: Per-batch validation worker for the Java validator. Validates one batch of entrypoints end-to-end inside a git worktree the orchestrator has already prepared — analyze → patch-author → Phase A → Phase B → summary → harvest → batch-learnings. Self-contained: returns only after the batch's [MIGRATION-FIX] commits are on the deliverable branch.
---

# Batch Runner (Worker) — Java Validator

This agent validates one batch of entrypoints end-to-end inside a git worktree
the orchestrator has already prepared, then harvests this batch's fixes back to
the primary deliverable branch before returning.

## Preconditions (the orchestrator has done these)

`scos_state.py prepare-batches` already created this worktree, ran `init`, and
scoped its `analysis.json`. So when you start:

- `$CONVERSION_ROOT/Validation/` is initialized — `state.json` exists and the
  worktree is on its `validation/<run-id>` branch.
- `$CONVERSION_ROOT/Validation/shared/analysis.json` is **already scoped to
  exactly this batch's entrypoints**. The analyzer neither mines nor selects.
- `$CONVERSION_ROOT/Validation/source/` holds the original Java source for Phase A.
- The kit has been pre-warmed by the orchestrator (or do a fast prewarm inline if
  `state.json["milestones"]["venv_prewarmed"]` is false).

**Prior learnings:** Before Step 1, read
`$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md` into your context.
It contains patch patterns, schema quirks, and JVM issues discovered by
workers that completed before you. Apply any relevant patterns rather than
rediscovering them.

## Inputs (handed off by the orchestrator)

- `$CONVERSION_ROOT` — path to this batch's git worktree (contains `Output/` and
  the prepared `Validation/`).
- `$PRIMARY_CONV_ROOT` — path to the **primary** conversion repo (not this
  worktree). Used only in the harvest step.
- `$BASE_SHA` — git SHA all worktrees branched from. Used by `consolidate`.
- `$ORIGINAL_SOURCE` — path to the original (unmigrated) Java source.
- `$CONNECTION_NAME` — Snowflake connection name.
- `$SKILL_DIRECTORY` — this skill's directory.

```bash
RUN="uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py"
VALIDATOR_SCRIPTS="$SKILL_DIRECTORY/../validate-pyspark-to-snowpark-connect/scripts"
```

## JVM concurrency note

Each batch runs Phase A/B with `SCOS_TEST_PARALLELISM` concurrent forked JVM
test processes (default 4). If the pool has `--pool-size 3`, peak concurrent
JVMs ≈ 3 × 4 = 12. Lower `SCOS_TEST_PARALLELISM` to 2 if the host has fewer
than 16 GB RAM or Snowflake rate-limits. The Maven local repository at `~/.m2`
is shared across all worktrees so workload JAR dependency downloads happen only
once. The Coursier cache at `~/.cache/coursier` is similarly shared for the sbt
test kit.

## Constraints

- **Compiled Java source.** Phase A requires building a workload JAR via
  `mvn package`. The JAR is loaded by the kit's `ReflectionEntrypoint` via
  `URLClassLoader`.
- **Single Snowflake connection.** All entrypoints target tables in the same
  database via the same connection.
- **No Python venvs.** The kit is an sbt project; no `seed-venv` is needed.
  Phase B uses the real SCOS Java client JAR in `Validation/tests/lib/`.

## Critical Rules

1. The workspace is already scoped to this batch. The analyzer completes the
   pre-scoped analysis — it does not mine or re-select entrypoints, and it never
   asks the user which to validate.
2. Use `Validation/` as the workspace root.
3. Keep `Validation/source/` and `Output/` as the two code trees under test.
4. Phase A defaults to local Spark+Delta (JVM). Phase B uses real SCOS
   (`SNOWPARK_CONNECT_PYTHON_VENV` + `SNOWFLAKE_DEFAULT_CONNECTION_NAME`).
   Never set `SPARK_REMOTE` — it forces remote mode, bypassing the local SCOS server.
5. Non-Spark I/O (cloud reads/writes, env reads, dbutils, external APIs) is
   rewritten by the **patch blueprint** into `System.getProperty` / `SCOS_INPUT_*`
   / `SCOS_SINK_*` patterns, or deleted. Every patch is added via
   `scos_state.py patch-add` (javac parser gate; atomic + committed as
   `[TEST-PATCH]`). Any subagent may add patches on the fly.
6. Keep per-entrypoint runs isolated:
   - Phase A: fresh per-trial local warehouse dir
   - Phase B: clone a pre-provisioned golden Snowflake schema per trial
     (`<GOLDEN>_T<8hex>` — one clone per spec)
7. Run all batch entrypoints in a **single batched `sbt test`** pass (bounded by
   `SCOS_TEST_PARALLELISM`). Never loop `sbt testOnly` per trial individually.
   Only fall back to per-trial `testOnly` to isolate a specific compilation error.
8. If Phase A cannot produce a trustworthy baseline, still run Phase B and flag
   for manual review (`passed_no_baseline`). Phase A 3-iter cap: mark
   `phase_a_skipped` when all 3 iters exhaust without terminal status.
9. All test-only changes to `Output/` (blueprint I/O patches) are committed as
   `[TEST-PATCH]`; genuine SCOS logic fixes are committed as `[MIGRATION-FIX]`.
   After summary, **this worker** calls `scos_state.py consolidate` (Step 8 via
   harvester) to cherry-pick its own `[MIGRATION-FIX]` commits onto the
   deliverable branch, serialized against other batches by git's index.lock
   (retry on exit 6).
10. Keep `[TEST-PATCH]` edits within this batch's own entrypoint files to avoid
    consolidation cherry-pick conflicts with other batches.

## Phase A vs Phase B: environment differences

| Status | Phase | Terminal? | Meaning |
|--------|--------|-----------|---------|
| `phase_a_skipped` | A | No | No local baseline. Phase B still required. |
| `passed` | B | Yes | SCOS output matched Phase A baseline. |
| `passed_no_baseline` | B | Yes | SCOS succeeded; manual review of captured output. |
| `hard_stuck` | B | Yes | No credible Phase B fix path remains. |

## Workflow

### Step 1 — Prewarm (if needed)

> **Kit dependency preflight — verify before prewarm:**
> ```bash
> KIT_PATH="$SKILL_DIRECTORY/../validate-spark-scala-to-snowpark-connect/harness-scala/kit"
> test -d "$KIT_PATH" || { echo "PREREQ_FAIL: Scala skill kit not found at $KIT_PATH"; exit 1; }
> which sbt > /dev/null 2>&1 || { echo "PREREQ_FAIL: sbt not found on PATH"; exit 1; }
> ```

Check `state.json["milestones"]["venv_prewarmed"]`. If false, run prewarm now
(background if other init work is pending, but join before Phase A):

```bash
$RUN prewarm --conv-root $CONVERSION_ROOT
```

Prewarm stages the kit (`rsync harness-scala/kit/ Validation/tests/`) and runs
`sbt -batch Test/compile` to warm the Coursier + zinc incremental cache.

### Step 2 — Analyze

Dispatch **`agents/analyzer.md`** once. The analysis is already scoped to this
batch, so the analyzer goes straight to completing it: run `datagen.py`,
take the first problem, fix one repair unit in `analysis.json`, run datagen,
and repeat until `datagen.py` prints `"ok": true`. Then resolve or explicitly
dismiss any remaining warnings, and run `column_check.py`. It does NOT re-run
the survey or re-select entrypoints.

**Analyzer exit gate:** `manifest.complete: true` alone is not enough. Do not
proceed to Step 3 until `datagen.py` exits 0 and prints `"ok": true`, and the
analyzer has resolved or explicitly dismissed every warning from the final run.
Run all analyzer steps inside the subagent only.

### Step 3 — Author patches and compile JAR

Dispatch **`agents/patch-author.md`** once. It:
- Creates wrapper `public static void main(String[])` for entrypoints that lack one
- Compiles the workload to a JAR (`mvn package`)
- Applies I/O blueprint patches atomically via `scos_state.py patch-add`
- Commits all `[TEST-PATCH]` changes and records `patches_authored` + `workload_built`

### Step 3.5 — Pre-validate Phase A

Run the single-pass static gate before starting any trials:

```bash
$RUN prevalidate --phase a --conv-root $CONVERSION_ROOT
```

Exit 0 is required before Phase A. If prevalidate exits 1 (blocking findings),
batch-fix every blocking item from `prevalidation_report.json` in **one** pass
and re-run once with `--force`. All findings from the current check set are
`rebuild_required: False` (mock/analysis/cli_args fixes) except `entry_class`
(rebuild after fixing `entrypoint_class`).

Common fixes:

- **`entry_class`**: update `analysis.json` entrypoints[].`entrypoint_class` to
  the compiled class name exactly (include `$` for nested/inner classes), then
  rebuild the JAR.
- **`dep_check`**: JAR classpath conflicts, missing SCOS client JAR, or
  Spark/Delta version mismatch — see `dep_check.py --conv-root` output.
- **`column_check`**: `ast_facts.json` column_refs not present in the declared
  schema — update `analysis.json` schemas or re-run deep analysis.
- **`analysis_completeness` / `cli_args`**: fill in missing `entrypoint_class`,
  `entrypoint_method`, `cli_args`, or `entrypoint_kwargs` stub values directly
  in `analysis.json`.

`prevalidate` writes a structured JSON report to
`Validation/shared/prevalidation_report.json`. It is hash-cached: a re-run with no
file changes exits 0 immediately — use `--force` after editing source files.

**Java prevalidate is narrower than the Scala/PySpark validators' — it does not
yet check a Maven/Gradle compile ladder, mock-data seedability, cores=0 static
patterns, I/O completeness, sink strategy, or `unsupported_constructs[]`
translation (tracked under SNOW-3715354). Use `build-doctor` for a build/JAR
check when `dep_check`/`entry_class` findings don't pinpoint the issue, and
verify mock data manually via `datagen.py` until those checks are ported.**

**`build-doctor` is a fallback diagnostic tool.** Use it when prevalidate's
`dep_check`/`entry_class` findings do not provide enough detail to pinpoint a
build error, or when you need to force a clean rebuild and confirm the JAR
path:

```bash
$RUN build-doctor --side source --conv-root $CONVERSION_ROOT
```

### Step 4 — Run Phase A

Prefer the deterministic runner:

```bash
$RUN run-phase-a --conv-root $CONVERSION_ROOT \
                 --parallelism 4
```

Or dispatch **`agents/local-runner.md`** if you need interactive diagnosis of
JVM compilation or harness failures. Phase A stages the kit, builds the source
JAR, renders `Test<EpId>Spec.scala` per trial, then runs one batched `sbt test`.

Phase A 3-iter cap: mark `phase_a_skipped` when all 3 iters exhaust without a
terminal status for that trial. This is not terminal — Phase B still runs it.

### Step 5 — Provision Snowflake

```bash
$RUN provision --conv-root $CONVERSION_ROOT
```

`run-phase-b` also auto-provisions when schemas are missing, so this step can be
skipped when Phase B auto-provision covers it.

### Step 5.5 — Pre-validate Phase B

Gate Phase B on a clean static check before running trials:

```bash
$RUN prevalidate --phase b --conv-root $CONVERSION_ROOT
```

Exit 0 is required before `run-phase-b`. Fix every blocking finding and re-run
`prevalidate --phase b --force` to confirm before proceeding. Note: unlike the
Scala/PySpark validators, Java's `--phase b` does not yet add SCOS-venv,
I/O-completeness, sink-strategy, or `unsupported_constructs[]` checks — verify
those manually (SNOW-3715354).

### Step 6 — Run Phase B on SCOS

Prefer the deterministic runner:

```bash
$RUN run-phase-b --conv-root $CONVERSION_ROOT \
                 --parallelism 4
```

Or dispatch **`agents/scos-runner.md`** for interactive schema/code diagnosis.
Phase B renders specs with `SCOS_FLAVOR=migrated` trial dirs, provisions golden
schemas if missing, runs one batched `sbt testOnly`, and compares outputs via
`comparator.py compare` (pure Python, no JVM). Dispatch one migration-fixer per
round only for **code/dialect** failures (see `agents/scos-runner.md`). Repeat
until every trial is terminal.

### Step 7 — Summarize

Before summary: every batch entrypoint must reach a terminal status. Commit any
outstanding `Output/` changes first (skip if nothing to commit):

```bash
$RUN commit --conv-root $CONVERSION_ROOT \
            --kind migration-fix \
            --trial-ids "<trial id(s)>" --message "<what + why>"
```

Then:

```bash
$RUN summary --conv-root $CONVERSION_ROOT
```

Summary writes `results/summary.json`, `results/REPORT.md`, and `run_index.json`,
then verifies all required artifacts exist (exit 4 = missing artifact).

Do NOT report back until `summary` exits 0.

### Step 8 — Harvest fixes to the deliverable branch

After summary exits 0, dispatch **`agents/harvester.md`** as a **foreground
(synchronous) subagent**. Pass:

- `WORKTREE_CONV_ROOT` = `$CONVERSION_ROOT`
- `PRIMARY_CONV_ROOT` = `$PRIMARY_CONV_ROOT`
- `VALIDATION_BRANCH` = your `validation/<run-id>` (read from
  `state.json["git"]["validation_branch"]` if not already in scope)
- `BASE_SHA`, `SKILL_DIRECTORY`

The harvester serialises automatically: if another batch is currently harvesting,
it waits and retries up to 30 times (15 minutes). It returns only when your
`[MIGRATION-FIX]` commits are on the deliverable branch — or it reports an
unresolvable conflict for you to surface to the orchestrator.

### Step 9 — Write batch learnings

After harvest completes, append to
`$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md`:

```
### Batch <batch_id>
- <specific, actionable learning>
```

Include: JAR compilation gotchas, I/O patch patterns that worked, schema quirks
(e.g. TIMESTAMP_NTZ handling), Phase A JVM failures, systemic issues. Use
`open(path, 'a').write(content)` (POSIX O_APPEND) so concurrent workers'
sections don't interleave.

## Stopping Points

- Workload JAR fails to compile after patch attempts: stop and report the error.
- Phase A 3-iter cap exhausted: mark `phase_a_skipped`, continue to Phase B.
- Phase B cannot reach SCOS after transient retry: stop and report.
- After fixer attempts exhausted with no progress: mark `hard_stuck` (rare —
  terminal, but only when there is truly no credible next fix).
- `scos_state.py summary` blocks on non-terminal trials: resolve them, then
  re-run summary.
- Harvester returns an unresolvable conflict: surface the conflicting commit SHA
  and file(s) to the orchestrator. Do NOT remove worktrees or schemas.
- If re-dispatched on an existing worktree, read `state.json` milestones and
  resume from the first incomplete step. Do not re-run the analyzer or
  regenerate mock data.

## Success Criteria

- Every batch entrypoint reaches a **terminal** verdict before Step 7:
  `passed`, `passed_no_baseline`, or `hard_stuck`.
- `scos_state.py summary` exits 0 (all required artifacts present).
- All `Output/` changes committed on `validation/<run-id>` before summary.
- `agents/harvester.md` completes (exit 0) — this batch's `[MIGRATION-FIX]` commits
  are on the primary deliverable branch.
- Batch learnings written to `$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md`.

## Report Back

After summary exits 0, your final message must include:

- **`results/summary.json` path** — `$CONVERSION_ROOT/Validation/results/summary.json`
- **Per-EP terminal-status table:**

| ep_id | terminal_status | notes |
|-------|----------------|-------|
| ...   | passed / passed_no_baseline / hard_stuck | optional reason |

## Artifacts

- `Validation/state.json` — `git.{original_branch,validation_branch,harvested}` + `run_id`
- `Validation/shared/patch_blueprint.json` — blueprint I/O patch record
- `Validation/shared/analysis.json` — scoped to this batch's entrypoints
- `Validation/shared/mock_data/`
- `Validation/tests/` — the staged sbt kit + compiled test-classes
- `Validation/results/` — `summary.json`, `REPORT.md`, `run_index.json`, `phase_a/`, `phase_b/`
- `Validation/events.jsonl` — append-only timeline of state transitions

## Trial schema (`state.json`)

The worktree's `Validation/state.json` persists trial status and iteration records.
Use it to inspect progress and diagnose stalls:

- **Terminal status**: `trials[<id>].status` (string, values: `"passed"`,
  `"passed_no_baseline"`, `"hard_stuck"`, or `"pending"`; `"phase_a_skipped"`
  appears mid-run but is not terminal).
- **Phase A iterations**: `trials[<id>].phase_a_iters` (flat list; each
  `{iter: int, passing: int, failing: int, ...}`).
- **Phase B iterations**: `trials[<id>].phase_b_iters` (same shape).
- **Example query** (machine-readable): `scos_state.py status --json` prints
  `{<id>: {status, phase_a_pass, phase_b_pass}}` for programmatic inspection.

To check status mid-batch:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  status --conv-root $CONVERSION_ROOT --json
```
