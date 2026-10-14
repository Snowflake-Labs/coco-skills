---
name: validate-spark-java-to-snowpark-connect
description: |
  Validate a completed Spark Java to Snowpark Connect (SCOS) migration by
  surveying the workload, asking the user to choose up to 10 entrypoints,
  synthesizing mock data, provisioning isolated test schemas, running the
  original workload on local Spark + Delta, running the migrated workload on
  real Snowpark Connect / SCOS, and comparing end-state snapshots. Use for
  SCOS validation, migration verification, entrypoint parity checks, and
  documenting remaining divergences or manual-review cases.
  Triggers: validate java scos, verify java migration, run java scos test suite,
  validate spark java, check java migration, test java migration correctness.
parent_skill: snowpark-connect
allowed-tools: Read, Write, Bash, Task, AskUserQuestion
---

# Validate Spark Java to Snowpark Connect Migration

You are the orchestrator. Keep the workflow simple, stateful, and easy
to audit. The reusable runtime lives in `harness-scala/kit/` (shared with
the Scala validator); agents should not re-describe or re-invent it from scratch.

## Inputs (set by the migrate skill's hand-off)

- `$CONVERSION_ROOT` — path containing `Output/` (the migrated SCOS source).
- `$ORIGINAL_SOURCE` — path to the original Java source.
- `$CONNECTION_NAME` — Snowflake connection name.
- `$DATABASE` — Snowflake database for golden schemas (default: `SCOS_VALIDATION`; also readable from `$SCOS_VALIDATION_DATABASE` env var).
- `$SKILL_DIRECTORY` — this skill's directory.
- `$VALIDATOR_SCRIPTS` — `$SKILL_DIRECTORY/../validate-pyspark-to-snowpark-connect/scripts` (the canonical PySpark validator scripts, reused by this skill).

## Progress UI Emit Hooks

Non-fatal one-liners that feed the live dashboard started by the migrate skill.
`$CONVERSION_ROOT` must be the same `Conversion-SCOS-*` dir the migrate UI was
launched against (it already contains `.migration-ui/`). Skip entirely when
`<config.enable_progress_ui>` is `no` or `.migration-ui/` is absent.

```bash
BUS="python3 $SKILL_DIRECTORY/../../scripts/progress_bus.py"
UI_RUN="$CONVERSION_ROOT"
```

| When | Command |
|---|---|
| Validation starts | `$BUS phase-start --run "$UI_RUN" --phase survey \|\| true` |
| Survey / batch prep done | `$BUS phase-end --run "$UI_RUN" --phase survey \|\| true` |
| Phase A begins | `$BUS phase-start --run "$UI_RUN" --phase phase-a \|\| true` |
| Phase A done | `$BUS phase-end --run "$UI_RUN" --phase phase-a \|\| true` |
| Phase B begins | `$BUS phase-start --run "$UI_RUN" --phase phase-b \|\| true` |
| Per-entrypoint result | `$BUS validation-ep --run "$UI_RUN" --ep "$EP_ID" --phase a\|b --status passed\|failed\|… --total $N \|\| true` |
| Phase B done | `$BUS phase-end --run "$UI_RUN" --phase phase-b \|\| true` |
| Harvest / merge-reports | `$BUS phase-start --run "$UI_RUN" --phase harvest \|\| true` |
| Merged report ready | `$BUS report-ready --run "$UI_RUN" --file "$REPORT" --phase harvest \|\| true` |
| Validation finished | `$BUS milestone --run "$UI_RUN" --phase validation-complete --message "Validation complete" \|\| true` then `$BUS summary --run "$UI_RUN" --data '{"validation_complete":true}' \|\| true` |

**Pool path:** `$VALIDATOR_SCRIPTS/batch.py pool` emits these automatically when
it finds `.migration-ui/` under `$CONVERSION_ROOT`. **Inline path:** emit the
table above yourself at each boundary.

## Constraints

- **Single Snowflake connection.** All entrypoints in a single run must
  target the same Snowflake database via the same connection.
- **Java source files and JVM projects only.** Entrypoints must be
  `.java` files within Maven or Gradle projects. Pure Python entrypoints
  are out of scope; use `validate-pyspark-to-snowpark-connect` for those.
  Scala entrypoints are out of scope; use
  `validate-spark-scala-to-snowpark-connect` for those.
- **Databricks notebooks not supported.** Java is not a Databricks notebook
  language; notebook-flattening is not available. Workloads exported from
  Databricks as job JARs are fully supported.
- **Explicit table dependencies.** All table reads must be declared in
  `analysis.json["entrypoints"][i]["external_sources"]` with
  `category: "table"`.
- **Explicit file dependencies.** All file reads must be declared in
  `external_sources` with `category: "file"` and a `mock_file` reference.
- **Up to ~10 entrypoints per run (single-batch).** For larger workloads, use the
  multi-batch parallel workflow (see below) to split entrypoints into sections and
  validate them in parallel across multiple git worktrees.
- **Requires Scala skill and sbt/Scala toolchain.** The runtime test harness is
  borrowed from `validate-spark-scala-to-snowpark-connect/harness-scala/kit/`
  (shared Scala ScalaTest kit). Java workload JARs are executed through the kit
  via `ReflectionEntrypoint`/`URLClassLoader` (JVM bytecode is language-agnostic).
  Both the sibling Scala skill and `sbt` + Scala must be installed alongside this
  skill. The local-runner preflight check will fail fast if the kit path is missing.

## Critical Rules

1. Ask the user to choose entrypoints. Do not auto-select them.
2. Validate at most about 10 entrypoints in one run unless the user
   explicitly asks for more.
3. Use `Validation/` as the workspace root for this skill.
4. Keep `Validation/source/` and `Output/` as the two code trees under
   test.
5. Use the shared test kit in `../validate-spark-scala-to-snowpark-connect/harness-scala/kit/` for both phases.
6. Local Phase A always uses a local Spark + Delta runtime
   (`SparkSession.master("local[1]")`).
7. Migrated Phase B must use real `SnowparkConnectSession.builder().
   getOrCreate()`; do not stub it.
 8. There are no shims or mock filesystems. Non-Spark I/O (cloud reads/writes,
    `dbutils`, JDBC, HTTP, secrets, widgets) is rewritten by the patch blueprint
    into native Spark reads + env-var indirection (`System.getProperty`), or
    deleted. Every rewrite is added via `scos_state.py patch-add`.
9. Keep per-entrypoint runs isolated:
   - local: fresh per-test warehouse dir and Delta checkpoint path
   - SCOS: clone a pre-provisioned golden Snowflake schema per trial

   Because each trial is fully isolated, **always run the selected entrypoint
   specs in bounded parallel** — one batched `sbt test` over the whole tests dir
   (one forked JVM per spec, capped by `SCOS_TEST_PARALLELISM`, default 4), in
   BOTH Phase A and Phase B. Never dispatch one `testOnly` per entrypoint and
   never run serially (serial multi-entrypoint validation is unacceptably slow).
   Only lower `SCOS_TEST_PARALLELISM` (e.g. `1`) for a specific, reproducible
   resource limit (memory, Snowflake rate-limiting, small warehouse), and report
   it as harness friction to fix.
10. If Phase A cannot produce a trustworthy baseline, still run Phase B
    and flag the result for human review.
11. All test-only `Output/` changes (the blueprint I/O patches) are committed
    on the `validation/<run-id>` branch with the `[TEST-PATCH]` prefix; genuine
    SCOS code fixes use `[MIGRATION-FIX]` (via `scos_state.py commit --kind
    migration-fix --trial-ids <id>`). Harvest (Step 9) cherry-picks only
    `[MIGRATION-FIX]` onto the deliverable; `[TEST-PATCH]` commits are never
    cherry-picked. `[MIGRATION-FIX]` commits must be production-safe — the
    committer rejects any that add `SCOS_*` harness identifiers to `Output/`.
12. In multi-batch mode, each worktree has a unique `run_id` so its golden
    Snowflake schema (`{slug}_{run_id}`) never collides with another batch's.
    Never share `state.json` across worktrees.
13. `[MIGRATION-FIX]` commits are cherry-picked per-batch via
    `scos_state.py consolidate` (serialized by git's own index.lock; callers
    retry on exit 6). `[TEST-PATCH]` commits are never consolidated.

## Phase A vs Phase B: environment differences

Phase A runs the source Java workload on local Spark + Delta. Some SQL
constructs (e.g. `QUALIFY`, Databricks-specific `MERGE INTO` variants)
are not supported by open-source Spark SQL. When Phase A fails due to
such environment differences, the trial is marked `phase_a_skipped` and
Phase B proceeds without a local baseline. Phase B runs on real SCOS
which supports the full Snowflake SQL surface. Successful Phase B runs
without a baseline produce `passed_no_baseline` for operator review.

## Prerequisites

Before starting the workflow, verify:

```bash
# Java 11+ required
java -version || echo "PREREQ_FAIL: Java not found"

# Maven or Gradle (based on the workload's build tool)
mvn --version || gradle --version \
  || echo "PREREQ_FAIL: No Java build tool found"

# sbt required for the shared test kit (harness-scala/kit/)
sbt --version || echo "PREREQ_FAIL: sbt not found (needed for the test kit)"

# Snowflake connector (Python; replaces the old JDBC driver requirement)
uv run --project $SKILL_DIRECTORY/.. python -c "import snowflake.connector" \
  || echo "PREREQ_FAIL: snowflake-connector-python not available"

uv --version || echo "PREREQ_FAIL: uv not installed"

# Analyze JAR — the deterministic `analyze` command (JavaParser AST facts).
# Build it with `mvn package` in harness-java/control/:
test -f "$SKILL_DIRECTORY/harness-java/control/target/scos-analyze-java.jar" \
  || echo "PREREQ_FAIL: scos-analyze-java.jar not built; run mvn package in harness-java/control/"

# Snowflake connection check
uv run --project $SKILL_DIRECTORY/.. python -c "
import snowflake.connector
snowflake.connector.connect(connection_name='$CONNECTION_NAME').cursor().execute('SELECT CURRENT_ACCOUNT()')
" || echo "PREREQ_FAIL: Snowflake connection failed"
```

## Workflow

The orchestrator always follows Steps 0–4 below. For small workloads (≤ 8
entrypoints or a single logical section), Step 4A runs the single batch inline;
for larger workloads Step 4B fans out to a pool of concurrent workers. In both
cases each worker (or the inline orchestrator) executes the
[Per-Batch Workflow](#per-batch-workflow) below.

### Step 0 — Capture base SHA

Before branching any worktrees, capture the current HEAD so every worktree starts
from the same commit:

```bash
# Runs in CoCo bash sandbox (Linux only)
BASE_SHA=$(git -C $CONVERSION_ROOT rev-parse HEAD)
```

### Step 1 — Survey, select, and weight

Dispatch **`agents/analyzer.md`** against the primary `$CONVERSION_ROOT` to
produce `Validation/shared/analysis.json` with `entrypoints[].weight` (heavier
= more tables / complex SQL). Skip if `analysis.json` already has complete
`entrypoints[]`. Optionally scope to a subset before sectioning:

```bash
# Optional: restrict to specific entrypoints before sectioning
uv run --project $SKILL_DIRECTORY/.. \
  python $SKILL_DIRECTORY/scripts/scos_state.py \
  scope-entrypoints --conv-root $CONVERSION_ROOT --ids "ep1,ep2,ep3"
```

The analyzer runs `scos-analyze-java.jar analyze` → `ast_facts.json`, then
`ast_to_analysis.py --mode survey` → `entrypoint_candidates[]` and records the
`analyzer_survey` milestone. After the user selects entrypoints, run
`ast_to_analysis.py --mode deep` to resolve per-entrypoint external sources, sinks,
mock data, schemas, and patch-author hints. The patch author also uses
`scan_date_calls.py` to locate `current_date()`/`current_timestamp()` calls that
need deterministic pinning before patching.

**Analyzer exit gate:** the analyzer self-verifies before recording
`analyzer_deep` — there is no separate critic agent. It runs two deterministic
gates: mock-file coverage (`datagen.py`, unchanged shared script) and
column/sink coverage (`column_check.py`). See `agents/analyzer.md`. Do not
hand off to the patch author until both exit `0`:

```bash
uv run --project $SKILL_DIRECTORY/.. python \
  $SKILL_DIRECTORY/scripts/schema_mine.py --conv-root $CONVERSION_ROOT
uv run --project $SKILL_DIRECTORY/.. python \
  $VALIDATOR_SCRIPTS/datagen.py \
  $CONVERSION_ROOT/Validation/shared/schemas \
  $CONVERSION_ROOT/Validation/shared/mock_data \
  --verify
uv run --project $SKILL_DIRECTORY/.. python \
  $SKILL_DIRECTORY/scripts/column_check.py --conv-root $CONVERSION_ROOT
```

Exit codes: `0` = ok, `1` = mock-data problems (fix and re-run), `2` =
`analysis.json` missing. Run the gate inside the analyzer dispatch.

> **Analyzer scope boundary.** The analyzer is dispatched for Steps 0–1 only.
> After recording the `analyzer_deep` milestone, the analyzer agent **MUST stop
> and return control to this orchestrator**. Steps 2–4 (sectioning, prepare-batches,
> per-batch execution) and beyond are separate orchestrator-dispatched steps.

### Step 2 — Semantic sectioning (inline — orchestrator, no subagent)

Group entrypoints into sections by shared schema/lineage. Create
`Validation/shared/sections.json` directly (inline; no subagent needed):

```json
[
  {"section_id": "orders",  "section_name": "Orders pipeline",  "ep_ids": ["ep1","ep2"]},
  {"section_id": "billing", "section_name": "Billing pipeline", "ep_ids": ["ep3","ep4"]}
]
```

Each `ep_id` must appear exactly once (enforced by `prepare-batches` coverage
check). Group entrypoints that share mock tables to reduce cross-batch data
re-use friction. A single catch-all section is valid.

### Step 3 — Prepare worktrees

```bash
uv run --project $SKILL_DIRECTORY/.. \
  python $SKILL_DIRECTORY/scripts/scos_state.py \
  prepare-batches \
    --conv-root        $CONVERSION_ROOT \
    --sections         $CONVERSION_ROOT/Validation/shared/sections.json \
    --original-source  $ORIGINAL_SOURCE \
    --connection       $CONNECTION_NAME \
    --database         $DATABASE \
    --base-sha         $BASE_SHA \
    --max-entrypoints  8 \
    --max-weight       40
```

This validates coverage, LPT-bins entrypoints into balanced batches, creates one
git worktree per batch under `Validation/worktrees/<batch_id>/` at `$BASE_SHA`,
inits each worktree with a unique `run_id`, scopes `analysis.json` per batch, and
writes `Validation/shared/batches_prepared.json` (batch plan + worktree map). Exit
1 if any batch failed setup; re-run with `--force` to retry.

### Step 4A — Single batch (inline, no SDK sessions)

When `batches_prepared.json` has exactly one batch, or you prefer inline
execution without launching an SDK pool:

Read the sole batch entry from
`$CONVERSION_ROOT/Validation/shared/batches_prepared.json` and capture its
`worktree`, `run_id`, and `validation_branch`. Set the batch-runner inputs:

```bash
# Runs in CoCo bash sandbox (Linux only)
export CONVERSION_ROOT=<batch.worktree>
export PRIMARY_CONV_ROOT=<primary $CONVERSION_ROOT from Step 0>
export BASE_SHA=$BASE_SHA
export ORIGINAL_SOURCE=$ORIGINAL_SOURCE
export CONNECTION_NAME=$CONNECTION_NAME
export SKILL_DIRECTORY=$SKILL_DIRECTORY
export batch_id=<batch.batch_id>
```

Read `agents/batch-runner.md` and follow it end-to-end **in this session**
(prewarm → analyze → patch-author → `prevalidate --phase a` → Phase A →
provision → `prevalidate --phase b` → Phase B → summary → harvest →
batch learnings), dispatching each phase agent as its own subagent. Do **not**
run batch-runner as a subagent itself — run it inline.

There is **no `pool_status.json`** in this path — progress is visible
directly in-session. Proceed to Step 5 only after the harvester completes and
`scos_state.py summary` exited 0.

### Step 4B — Multiple batches (parallel pool)

When there are 2+ batches, launch the async worker pool:

```bash
uv run --project $SKILL_DIRECTORY/../validate-pyspark-to-snowpark-connect \
  python $VALIDATOR_SCRIPTS/batch.py pool \
    --prepared          $CONVERSION_ROOT/Validation/shared/batches_prepared.json \
    --primary-conv-root $CONVERSION_ROOT \
    --original-source   $ORIGINAL_SOURCE \
    --connection        $CONNECTION_NAME \
    --skill-directory   $SKILL_DIRECTORY \
    --pool-size         3 \
    --control-script    scos_state.py \
    --retries           1
```

The pool spawns up to 3 concurrent SDK sessions, each running
`agents/batch-runner.md` for one batch. It polls each worktree's `state.json`
every 10 s, writes `Validation/pool_status.json` (live + terminal), and
auto-runs `merge-reports` on completion.

**JVM concurrency:** `pool_size` × `SCOS_TEST_PARALLELISM` concurrent forked JVMs
(default 3 × 4 = 12). Lower `SCOS_TEST_PARALLELISM` to 2 if the host has < 16 GB
RAM or Snowflake rate-limits small warehouses. The Maven local repository
(`~/.m2`) is **shared** across worktrees — dependency downloads happen only once
even with multiple concurrent workers.

**Multi-batch merged artifacts:**
- `Validation/run_index.json` — merged master manifest (all batches)
- `Validation/results/REPORT.md` — merged human-readable summary
- `Validation/pool_status.json` — per-batch pool status (Step 4B only)
- `Validation/worktrees/<batch_id>/` — per-batch artifact trees

### Step 5 — Merged report

**Pool path (4B):** `batch.py pool` runs `batch.py merge-reports` automatically.
Read `pool_status.json` → `merge_report_path`
(= `$CONVERSION_ROOT/Validation/results/REPORT.md`) and surface the path.

**Inline path (4A):** `pool_status.json` does not exist. Run merge-reports
yourself (idempotent) and take the `REPORT.md` path from its stdout:

```bash
uv run --project $SKILL_DIRECTORY/../validate-pyspark-to-snowpark-connect \
  python $VALIDATOR_SCRIPTS/batch.py merge-reports \
    --prepared $CONVERSION_ROOT/Validation/shared/batches_prepared.json \
    --out      $CONVERSION_ROOT/Validation
```

Writes `Validation/run_index.json` and `Validation/results/REPORT.md`.

**View the report:**

```bash
uv run --project $SKILL_DIRECTORY/.. python -m streamlit run \
  $SKILL_DIRECTORY/scripts/report/validation_report_app.py \
  -- --run-root $CONVERSION_ROOT/Validation
```

### Step 6 — Cleanup gate

Use `AskUserQuestion` **once** to ask whether to:
- **(a) Drop ALL per-batch golden Snowflake schemas** (list each `run_id` from
  `batches_prepared.json`).
- **(b) Tear down git worktrees and `validation-base/*` branches.** Keep the
  `validation/<run-id>` branches for inspection unless the user asks.

Only on an affirmative answer, for each batch in `batches_prepared.json`:

```bash
uv run --project $SKILL_DIRECTORY/../validate-pyspark-to-snowpark-connect \
  python $VALIDATOR_SCRIPTS/cleanup.py --conv-root <worktree> --force
git -C $CONVERSION_ROOT worktree remove <worktree>
git -C $CONVERSION_ROOT branch -D validation-base/<batch_id>
```

If declined, give the user the exact commands to run later. Never auto-clean.

### Step 7 — Final display

After Step 5 wrote `REPORT.md`, post one final message to the user:

1. **Terminal status counts** — read `Validation/run_index.json` → `totals` and
   print them verbatim (overall verdicts + comparison verdicts).
2. **Full entrypoint table** — one row per EP from `Validation/run_index.json`
   (`entrypoints[]`, keyed by `batch_id`). Columns: Batch, Entrypoint, Overall,
   Comparison, Time (s), **Reason**. The **Reason** cell is
   `entrypoints[].verdict.reason` — already in `run_index.json`, no extra
   lookups needed. Sort by `batch_id`. **Inline path (4A):** build the table
   from `run_index.json` alone; `pool_status.json` is absent (Reason still comes
   from `verdict.reason`).
2a. **Flag no-baseline / stuck EPs.** For every row whose Overall is
   `passed_no_baseline` or `hard_stuck`, call it out explicitly as **needs human
   review** and print its `verdict.reason`.
3. Finish with the on-disk paths already surfaced in Step 5 (`REPORT.md`,
   `run_index.json`, and the streamlit viewer command).

Do not recompute totals from the EP list — the merger already did it.

## Orchestration notes (efficiency)

These keep wall-time and token use down across the multi-agent run:

- **Snapshot growing state files per dispatch.** `events.jsonl` and
  `run_index.json` grow as the run proceeds; re-reading them in full on every
  turn is wasteful. Read them once when you dispatch a runner agent and pass
  that snapshot down, rather than re-reading the whole file each turn.
- **Poll `state.json`, do not dead-wait.** Run the Phase A / Phase B runners as
  foreground agents and poll `Validation/state.json` for trial-status progress,
  so a stuck trial can be intervened on. Do not block on a single long
  `agent_output(wait=true)` that can sit idle until the 900s timeout.
- **Batch the trial run.** Dispatch one batched `sbt test` over all selected
  specs (bounded by `SCOS_TEST_PARALLELISM`) and process results in one pass —
  not one `testOnly` per trial. See `agents/scos-runner.md` / `local-runner.md`.
- **Overlap the JVM warm-up with authoring (Step 4).** The first `sbt`/kit
  build and dependency resolution (`sbt update` + compiling the harness kit) is
  the slowest serial cost. Kick off `scos_state.py prewarm` in the
  **background** right after Step 1 (init), then keep doing Step 2–3 authoring
  (analysis, mock data, patch-author patches) while it runs; join it before Phase A
  so the runner does not pay cold-start time. A warm sbt/Coursier cache
  also speeds every later iteration.
- **In multi-batch mode, share the Maven local repository.** Set
  `MAVEN_OPTS="-Dmaven.repo.local=$HOME/.m2/repository"` in the env before
  launching the pool. All worktrees reuse the same local artifact
  cache, so the hundreds-of-MB Spark/Delta download happens only once across N
  concurrent workers.

## Stopping Points

- Missing hand-off inputs: stop and report the missing input.
- `prepare-batches` exits 3 (sections.json coverage error — entrypoint
  duplicated, unsectioned, or unknown): fix `sections.json` so every
  entrypoint appears in exactly one section, then rerun Step 3. No worktrees
  are created on a coverage failure. If it prepares some batches but reports a
  per-batch error (exit 1), skip those, surface them, and continue.
- A batch ends `failed` after the pool's retry: the pool exits 1; surface the
  failed `batch_id`(s). Other batches' results are still valid and already merged.
- `scos_state.py consolidate` exits 1 (run from the harvester): surface the
  error to the user.
- Cherry-pick conflicts that cannot be resolved by the harvester: surface the
  conflicting commit SHA and files. Other batches continue unaffected.

## Success Criteria

- Every prepared batch session reported back: pool exit 0 (Step 4B), or —
  single batch (Step 4A) — the inline batch-runner reached `summary` exit 0 and
  harvester success. OR a batch is reported failed with a clear explanation (pool
  exit 1 / harvester conflict; batch listed in `pool_status.json` for 4B or
  reported inline for 4A).
- All `[MIGRATION-FIX]` commits are on the deliverable branch — workers
  self-reported harvest success.
- `batch.py merge-reports` completed — run automatically by `batch.py pool`
  (Step 4B) or manually by the orchestrator (Step 4A) —
  `Validation/run_index.json` and `Validation/results/REPORT.md` written.
- The merged report explains which results are safe matches, which diverge, and
  which need human review.

## Output

- Primary: `scos_state.py summary`
- Durable state:
  - `Validation/state.json` (includes `git.{original_branch,validation_branch,harvested}`)
  - `Validation/shared/analysis.json`
  - `Validation/shared/patch_blueprint.json` (the test-patch record)
  - `Validation/shared/mock_data/`
  - `Validation/tests/`
  - `Validation/results/`

## Run artifacts

After a run completes, the canonical artifacts are:

- `Validation/run_index.json` — master manifest
- `Validation/events.jsonl` — append-only timeline of all state
  transitions
- `Validation/state.json` — orchestrator state
- `Validation/results/REPORT.md` — human-readable summary
- `Validation/results/{phase_a,phase_b}/<trial_id>/` — captured outputs
  + diffs
- `Validation/results/phase_b/<trial_id>/stage_snapshot/` — Snowflake
  table snapshots (`passed_no_baseline` only)

### `Validation/run_index.json` schema

Master manifest for downstream consumers (UIs, dashboards). Generated
by `scos_state.py build-index`, called automatically from `scos_state.py summary`.

```json
{
  "run": {
    "id": "<uuid>",
    "started_at": "<ISO timestamp>",
    "completed_at": "<ISO timestamp> | null",
    "status": "passed | partial | in_progress",
    "skill_version": "...",
    "connection": "<connection_name>",
    "database": "<database>",
    "schema_namespace": "<schema>"
  },
  "milestones": {"<name>": {"status": "done|pending", "completed_at": null}},
  "entrypoints": [
    {
      "id": "<trial_id>",
      "source_path": "...",
      "phase_a": {
        "verdict": "baseline_produced | no_baseline | phase_a_skipped",
        "iters": "<int>",
        "captured_outputs": [{"name": "...", "path": "...", "rows": null, "schema": null}],
        "patches_applied": [...],
        "errors": [...]
      },
      "phase_b": {
        "verdict": "<trial status>",
        "iters": "<int>",
        "captured_outputs": [...],
        "patches_applied": [...],
        "errors": [...],
        "scos_query_ids": [...],
        "fixer_dispatches": [...],
        "stage_snapshot_paths": [...],
        "migration_fix_commits": [{"sha": "...", "subject": "...(no [MIGRATION-FIX] prefix)", "body": "...(optional)"}]
      },
      "comparison": {
        "verdict": "match | cosmetic_divergence | real_divergence | no_baseline",
        "diffs": [{"table": "...", "diff_path": "...", "schema_match": true, "row_count_a": null, "row_count_b": null, "tier": "...", "verdict": "..."}],
        "documented_divergences": [...]
      },
      "trial_dir": "results/phase_b/<trial_id>/",
      "verdict": {"overall": "<status>", "reason": "..."}
    }
  ],
  "artifacts_index": {
    "analysis": "shared/analysis.json",
    "schemas": "shared/schemas.json | null",
    "patch_blueprint": "shared/patch_blueprint.json | null",
    "mock_data": [{"trial_id": "...", "files": [...]}],
    "auxiliary_sql": [...],
    "rendered_tests": [...]
  },
  "events": "events.jsonl | null",
  "fixer_dispatches": [...],
  "documented_divergences": [...],
  "warnings": [...],
  "parse_errors": [{"path": "results/phase_b/<trial_id>/_index.json", "error": "...", "trial_id": "...", "phase": "phase_a|phase_b"}]
}
```

## Troubleshooting

Common issues and solutions:

- **JAR classpath conflicts** between the workload and the kit: ensure the
  workload JAR is shaded (Maven Shade plugin or Gradle shadow) so its
  Spark/Scala dependencies don't collide with the kit's classpath.
- **SCOS session connection** (local-server mode: `SNOWPARK_CONNECT_PYTHON_VENV` +
  `SNOWFLAKE_DEFAULT_CONNECTION_NAME`; do not set `SPARK_REMOTE`). "Local-server" =
  the translation server runs locally; Phase B **compute still executes in Snowflake**.
- **Maven/Gradle build failures** (missing dependencies, shade plugin misconfigured):
  run `mvn dependency:tree` or `gradle dependencies` to identify conflicts.
- **SCOS client JAR not found** during prewarm: the JAR is cached at
  `~/.cache/scos/jars/` by the migration skill's Phase 2b compile gate. If
  absent, download it from Maven Central (see prewarm instructions in
  `agents/batch-runner.md`).
- **Delta table path conflicts** in local Phase A: each trial uses a unique
  warehouse dir; if trials share Delta paths, set `SCOS_WAREHOUSE_DIR` per trial.
- **Snowflake JDBC authentication issues**: use `System.getProperty` instead of
  direct JDBC URLs in the migrated code; the harness injects credentials via
  env-var indirection.
- **`ParquetFileFormat.$deserializeLambda$` failures on Java 17 + Spark 3.5**
  (SerializedLambda / URLClassLoader conflict during Phase A): set
  `SCOS_PHASE_A_SUBPROCESS=1` in the environment before running `scos_state.py
  run-phase-a` / the sbt test invocation. This runs the Phase A workload in a
  child JVM (`SubprocessLauncher`) instead of in-process via
  `ReflectionEntrypoint`, isolating the lambda deserialization from the kit's
  classloader. Default is `0` (in-process, faster — no extra JVM spawn). The
  flag lives in the shared Scala test kit (`ScosTrialFixture.scala`) that this
  validator stages and reuses, so it applies to Java trials exactly as it does
  to Scala trials.
