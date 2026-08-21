---
name: validate-spark-scala-to-snowpark-connect
description: |
  Validate a completed Spark Scala to Snowpark Connect (SCOS) migration across
  an entire workload. Surveys every entrypoint, weights each by table complexity,
  optionally scopes to a subset, packs balanced batches, synthesizes mock data,
  provisions isolated test schemas, runs the original workload on local Spark +
  Delta, runs the migrated workload on real Snowpark Connect / SCOS, and compares
  end-state snapshots. Use for SCOS validation, migration verification, entrypoint
  parity checks, and documenting remaining divergences or manual-review cases.
  Triggers: validate scala scos, verify scala migration, run scala scos test suite,
  validate spark scala, check scala migration, test scala migration correctness.
parent_skill: snowpark-connect
allowed-tools: Read, Write, Bash, Task, AskUserQuestion
---

# Validate Spark Scala to Snowpark Connect Migration

You are the orchestrator. Keep the workflow simple, stateful, and easy
to audit. The reusable runtime lives in `harness-scala/`; agents should
not re-describe or re-invent it from scratch.

## Inputs (set by the migrate skill's hand-off)

- `$CONVERSION_ROOT` — path containing `Output/` (the migrated SCOS source).
- `$ORIGINAL_SOURCE` — path to the original Scala source.
- `$CONNECTION_NAME` — Snowflake connection name.
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
| Validation starts (Step 0) | `$BUS phase-start --run "$UI_RUN" --phase survey \|\| true` |
| Survey / batch prep done | `$BUS phase-end --run "$UI_RUN" --phase survey \|\| true` |
| Phase A begins | `$BUS phase-start --run "$UI_RUN" --phase phase-a \|\| true` |
| Phase A done | `$BUS phase-end --run "$UI_RUN" --phase phase-a \|\| true` |
| Phase B begins | `$BUS phase-start --run "$UI_RUN" --phase phase-b \|\| true` |
| Per-entrypoint result | `$BUS validation-ep --run "$UI_RUN" --ep "$EP_ID" --phase a\|b --status passed\|failed\|… --total $N \|\| true` |
| Phase B done | `$BUS phase-end --run "$UI_RUN" --phase phase-b \|\| true` |
| Harvest / merge-reports | `$BUS phase-start --run "$UI_RUN" --phase harvest \|\| true` |
| Merged report ready | `$BUS report-ready --run "$UI_RUN" --file "$REPORT" --phase harvest \|\| true` |
| Validation finished | `$BUS milestone --run "$UI_RUN" --phase validation-complete --message "Validation complete" \|\| true` then `$BUS summary --run "$UI_RUN" --data '{"validation_complete":true}' \|\| true` |

**Pool path (Step 4B):** `$VALIDATOR_SCRIPTS/batch.py pool` emits these
automatically when it finds `.migration-ui/` under `$CONVERSION_ROOT`.
**Inline path (Step 4A):** emit the table above yourself at each boundary.

## Constraints

- **Single Snowflake connection.** All entrypoints in a single run must
  target the same Snowflake database via the same connection.
- **Scala source files and JVM projects only.** Entrypoints must be
  `.scala` files, sbt/Maven/Gradle projects, or Databricks notebooks
  whose dominant language is Scala. Pure Python entrypoints are out of
  scope; use `validate-pyspark-to-snowpark-connect` for those.
- **Explicit table dependencies.** All table reads must be declared under
  `Validation/shared/schemas/entrypoints/<id>/tables/` with
  `"access": "read"` (or `"readwrite"`) and `category: "table"`.
- **Explicit file dependencies.** All file reads must be declared the same way
  with `category: "file"` and a `mock_file` reference.
- **All discovered entrypoints by default.** Larger workloads are split into
  batches via `prepare-batches --max-entrypoints 8` (a **batch packing** cap,
  not a selection cap) and validated in parallel across git worktrees.

## Coordinator Notes (operational pitfalls — read before starting)

- **`Validation/source/` must be a real copy, not a symlink.** `scos-analyze.jar`
  does not follow symlinks; it will report `0 file(s)` and produce an empty
  `ast_facts.json`.  Use `rsync -a --exclude='.git' --exclude='target' src/ dest/`
  to populate it.

- **String weights in schemas.** Prefer mining via `schema_mine.py`, which
  writes **numeric** weights into `schemas/manifest.json`. Older runs may still
  have `"weight": "high"` / `"medium"` / `"low"` string labels; `batch.py`
  `prepare-batches` accepts these labels. If you see
  `invalid literal for int() with base 10: 'low'` you are on an older build —
  convert the strings to integers manually (high→20, medium→10, low→5)
  before calling `prepare-batches`, or re-run `schema_mine.py`.

- **Two different `analysis.json` files.** Migration
  `$CONVERSION_ROOT/analysis.json` is a flat **issue array** from
  `analyze_scala.py`. Validation uses `Validation/shared/schemas/` as source of
  truth; `Validation/shared/analysis.json` is a **generated JVM shim** (entrypoint
  catalog). Do not confuse them.

- **`merge-reports` permission denied on parquet files.**  Spark writes Phase A/B
  result and mock-data parquet files with read-only permissions (mode 444).
  `merge-reports` copies them into the `Validation/batches/` tree and will fail if
  those copies are already present and read-only.  Run
  `chmod -R 755 $CONVERSION_ROOT/Validation/batches` before calling `merge-reports`
  to clear any stale locked copies.

- **`consolidate` — correct invocation.** Run `consolidate` against the **primary
  repo**, not a worktree:
  ```bash
  $RUN consolidate --conv-root $PRIMARY_CONV_ROOT --base-sha $BASE_SHA
  ```
  Target a single batch's branch with `--branches <branch_name>` to avoid
  scanning other batches' stale MIGRATION-FIX commits.

- **`git filter-branch` leaves `refs/original/` backup refs.** After using
  `filter-branch` to rename commits, delete the backup before running `consolidate`:
  ```bash
  git -C $WORKTREE update-ref -d refs/original/refs/heads/<branch>
  git -C $WORKTREE gc --prune=now
  ```
  Otherwise the consolidate scanner finds the old SHAs in `--all` and rejects them.

## Critical Rules

1. Entrypoint selection is fully automatic — validate **all** discovered
   entrypoints by default. `prepare-batches` scopes each worktree to its batch.
   Workers never ask which entrypoints to validate. (The orchestrator may narrow
   the *whole run* to a subset once, up front, in Step 1.6 — that is the only
   entrypoint-selection prompt, and it is orchestrator-level, not per-worker.)
2. Use `Validation/` as the workspace root for this skill.
3. Keep `Validation/source/` and `Output/` as the two code trees under
   test.
4. Use the shared test kit in `harness-scala/kit/` for both phases.
5. Local Phase A always uses a local Spark + Delta runtime
   (`SparkSession.master("local[1]")`).
6. Migrated Phase B must use real `SnowparkConnectSession.builder().
   getOrCreate()`; do not stub it.
7. There are no shims or mock filesystems. Non-Spark I/O (cloud reads/writes,
    `dbutils`, JDBC, HTTP, secrets, widgets) is rewritten by the patch blueprint
    into native Spark reads + env-var indirection (`System.getProperty`), or
    deleted. Every rewrite is added via `scos_state.py patch-add`.
8. Keep per-entrypoint runs isolated:
   - local: fresh per-test warehouse dir and Delta checkpoint path
   - SCOS: clone a pre-provisioned golden Snowflake schema per trial

   Because each trial is fully isolated, **always run the selected entrypoint
   specs in bounded parallel** — one batched `sbt test` over the whole tests dir
   (one forked JVM per spec, capped by `SCOS_TEST_PARALLELISM`; when
   `--parallelism` is omitted, `run-phase-a` / `run-phase-b` auto-cap from host
   RAM), in BOTH Phase A and Phase B. Never dispatch one `testOnly` per
   entrypoint and never run serially (serial multi-entrypoint validation is
   unacceptably slow). Only lower `SCOS_TEST_PARALLELISM` (e.g. `1`) for a
   specific, reproducible resource limit, and report it as harness friction.
9. If Phase A cannot produce a trustworthy baseline, still run Phase B
    and flag the result for human review.
10. All test-only `Output/` changes (the blueprint I/O patches) are committed
    on the `validation/<run-id>` branch with the `[TEST-PATCH]` prefix; genuine
    SCOS code fixes use `[MIGRATION-FIX]` (via `scos_state.py commit --kind
    migration-fix --trial-ids <id>`). Harvest (Step 9) cherry-picks only
    `[MIGRATION-FIX]` onto the deliverable; `[TEST-PATCH]` commits are never
    cherry-picked. `[MIGRATION-FIX]` commits must be production-safe — the
    committer rejects any that add `SCOS_*` harness identifiers to `Output/`.
11. In multi-batch mode, each worktree has a unique `run_id` so its golden
    Snowflake schema (`{slug}_{run_id}`) never collides with another batch's.
    Never share `state.json` across worktrees.
12. `[MIGRATION-FIX]` commits are cherry-picked per-batch via
    `scos_state.py consolidate` (serialized by git's own index.lock; callers
    retry on exit 6). `[TEST-PATCH]` commits are never consolidated.

## Phase A vs Phase B: environment differences

Phase A runs the source Scala workload on local Spark + Delta. Some SQL
constructs (e.g. `QUALIFY`, Databricks-specific `MERGE INTO` variants)
are not supported by open-source Spark SQL. When Phase A genuinely cannot
execute due to such an environment difference, the trial is marked
`phase_a_skipped` **with a `--reason` naming the specific construct** (the
`record-trial-status` gate rejects a blank or generic reason), and Phase B
proceeds without a local baseline. Phase B runs on real SCOS which supports the
full Snowflake SQL surface. A successful Phase B run without a baseline is
**derived** to `passed_no_baseline` — never set directly — and it carries the
preserved skip reason so the operator report always explains the missing
baseline.

**A skip is a last resort, not a shortcut.** Missing/unmocked tables and columns
are inline schema repairs; connector reads, 3-part names, and external I/O are
blueprint patches; running out of iterations means escalate (fixer dispatch or a
documented repair-exhaustion track) — none of these are valid skip reasons. A
skipped Phase A ships with no parity check and is flagged for human review.
`run-phase-a` also runs a deterministic mock-data guard (schema_mine + datagen
seed/verify) and hard-fails on unseedable mocks, so an empty baseline surfaces as
an actionable datagen error rather than a silent skip.

## Prerequisites

Before starting the workflow, verify Snowflake connectivity and tooling.

```bash
# Java 8/11/17 required for Phase A's local Spark 3.5 (Spark 3.5 does NOT support
# Java 21+).
java -version || echo "PREREQ_FAIL: Java not found"

# sbt, Maven, or Gradle (based on the workload's build tool)
sbt --version || mvn --version || gradle --version \
  || echo "PREREQ_FAIL: No Scala build tool found"

# Snowflake connector (Python; replaces the old JDBC driver requirement)
uv run --project $SKILL_DIRECTORY/.. python -c "import snowflake.connector" \
  || echo "PREREQ_FAIL: snowflake-connector-python not available"

uv --version || echo "PREREQ_FAIL: uv not installed"

# Analyze JAR — the only JVM piece left: the deterministic `analyze` command
# (Scalameta AST facts) used by the data-synthesizer agent. Provision, compare, datagen,
# and patch reuse the canonical PySpark scripts at $VALIDATOR_SCRIPTS; state
# (scos_state.py) and schema mining (schema_mine.py) are this skill's own scripts/.
# The jar is small (~45 MB, circe + Scalameta only); build it with `sbt assembly` in
# harness-scala/control/:
test -f "$SKILL_DIRECTORY/harness-scala/control/target/scos-analyze.jar" \
  || echo "PREREQ_FAIL: scos-analyze.jar not built; run sbt assembly in harness-scala/control/"

# Snowflake connection check
uv run --project $SKILL_DIRECTORY/.. python -c "
import snowflake.connector
snowflake.connector.connect(connection_name='$CONNECTION_NAME').cursor().execute('SELECT CURRENT_ACCOUNT()')
" || echo "PREREQ_FAIL: Snowflake connection failed"

# notebook_io (stdlib-only; needed only for notebook workloads)
python3 -c "
import sys
sys.path.insert(0, '$SKILL_DIRECTORY/../scripts')
from notebook_io import flatten_cells_to_script
print('notebook_io OK')
" || echo "INFO: notebook_io unavailable (only needed for notebook workloads)"
```

## Workflow

The orchestrator always follows Steps 0–4 below. For small workloads (≤ 8
entrypoints or a single logical section), Step 4A runs the single batch inline;
for larger workloads Step 4B fans out to a pool of concurrent workers. In both
cases each worker (or the inline orchestrator) follows `agents/batch-runner.md`.

### Step 0 — Capture base SHA

Before branching any worktrees, capture the current HEAD so every worktree starts
from the same commit:

```bash
# Runs in CoCo bash sandbox (Linux only)
BASE_SHA=$(git -C $CONVERSION_ROOT rev-parse HEAD)
```

### Step 1 — Survey and weight

Mine the workload into ``Validation/shared/schemas/`` with a single deterministic
command (PySpark parity). Scalameta runs under the hood via ``scos-analyze.jar``;
agents do **not** hand-build ``analysis.json``.

```bash
# Ensure Validation/source/ is a real copy (not a symlink) first — see Coordinator Notes.
mkdir -p $CONVERSION_ROOT/Validation/shared
# If Validation/source is not yet populated:
#   rsync -a --exclude='.git' --exclude='target' $ORIGINAL_SOURCE/ \
#     $CONVERSION_ROOT/Validation/source/

uv run --project $SKILL_DIRECTORY/.. \
  python $SKILL_DIRECTORY/scripts/schema_mine.py \
  --conv-root $CONVERSION_ROOT
```

Produces ``Validation/shared/schemas/manifest.json`` — every entrypoint id, path,
numeric ``weight``, and ``llm_todo`` gaps. Also writes a generated
``Validation/shared/analysis.json`` JVM shim (do not hand-edit; regenerate with
``scos_state.py schemas-to-analysis`` after schema repairs).

Skip re-mining if ``schemas/manifest.json`` already has complete ``entrypoints[]``.
Do **not** ask the user to pick entrypoints here — workers never ask; optional
narrowing is Step 1.6 only.

### Step 1.6 — Scope to a subset (optional)

By default the whole workload is validated. Some runs only need a specific set
of entrypoints (one pipeline, a few files the user is iterating on). Ask **once**
with a single `AskUserQuestion`:

> "Survey found **N** entrypoints. Validate **all** of them, or just a
> **subset**? For a subset, reply with the entrypoints you want — ids, file
> paths, or a description (e.g. 'just the ingestion DAG')."

Options: **"All N entrypoints"** and **"A subset (I'll list them)"**.

- **All** — skip the rest of this step; go to Step 2.
- **Subset** — the user names the entrypoints in their next message. Resolve
  their answer to concrete entrypoint **ids** from `schemas/manifest.json`
  (or `analysis.json` shim) yourself (match on `id`/`path`/section intent),
  confirm the resolved id list back to the user in one line, then prune to
  exactly that set:

```bash
uv run --project $SKILL_DIRECTORY/.. \
  python $SKILL_DIRECTORY/scripts/scos_state.py \
  scope-entrypoints --conv-root $CONVERSION_ROOT --ids "ep1,ep2,ep3"
```

`scope-entrypoints` rewrites `schemas/` (and the analysis shim) to keep only the
listed ids (exit 2 on unknown ids). Everything downstream (sectioning, batching,
the pool, the merged report) then sees only the kept subset. This is the **only**
entrypoint-selection prompt; it is orchestrator-level, not per-worker.

### Step 2 — Semantic sectioning (inline — orchestrator, no subagent)

Group entrypoints into sections by shared schema/lineage. Create
`Validation/shared/sections.json` directly (inline; no subagent needed):

```json
[
  {"section_id": "orders",  "name": "Orders pipeline",  "ep_ids": ["ep1","ep2"]},
  {"section_id": "billing", "name": "Billing pipeline", "ep_ids": ["ep3","ep4"]}
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
    --base-sha         $BASE_SHA \
    --max-entrypoints  8 \
    --max-weight       40
```

This validates coverage, LPT-bins entrypoints into balanced batches, creates one
git worktree per batch under `Validation/worktrees/<batch_id>/` at `$BASE_SHA`,
inits each worktree with a unique `run_id`, copies + scopes `schemas/` (and the
analysis shim) per batch, and writes `Validation/shared/batches_prepared.json`
(batch plan + worktree map). Exit 1 if any batch failed setup; re-run with
`--force` to retry.

`--max-entrypoints 8` is a **batch packing** cap (split into more batches), not a
selection cap — every scoped entrypoint is still validated.

### Step 3.5 — Prewarm (background overlap)

Right after `prepare-batches` (each worktree is already `init`ed), kick off
`scos_state.py prewarm` in the **background** for each worktree so kit staging +
sbt/Coursier warm-up overlaps with Step 4 authoring. Join before Phase A —
never start Phase A on a cold kit, and never defer prewarm until after
patch-author.

```bash
# Per worktree (background). Honest prewarm: exits non-zero and does NOT set
# venv_prewarmed if JDK cannot be resolved or sbt is missing.
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  prewarm --conv-root <worktree>
```

For the inline single-batch path, `agents/batch-runner.md` Step 1 also checks
the milestone and runs prewarm if needed. Prefer overlapping it with analyze /
patch-author rather than waiting until Phase A.

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
provision → `phase-reset --to b` → `prevalidate --phase b` → Phase B →
summary → harvest → batch learnings), dispatching each phase agent as its own
subagent. Do **not** run batch-runner as a subagent itself — run it inline.

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

**JVM concurrency:** `pool_size` × `SCOS_TEST_PARALLELISM` concurrent forked JVMs.
Default `--pool-size 3`. When `--parallelism` is omitted, `run-phase-a` /
`run-phase-b` auto-cap from available RAM (`<8 GB` → 1, `<16 GB` → 2, else → 4);
an explicit `--parallelism N` always wins. Lower further only for Snowflake
rate-limits on small warehouses. The Coursier/Ivy cache (`~/.cache/coursier`,
`~/.ivy2`) is **shared** across worktrees — dependency downloads happen only once
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
- **Prewarm is Step 3.5**, not an afterthought — overlap kit/`sbt` warm-up with
  analyze + patch-author; join before Phase A. A warm Coursier cache also speeds
  every later iteration.
- **Gate both phases with `prevalidate`.** Before Phase A run
  `scos_state.py prevalidate --phase a`; between phases run
  `phase-reset --to b` then `prevalidate --phase b`. Both write
  `Validation/shared/prevalidation_report.json` and must exit 0 (or 2 with only
  warnings) after batch-fixing every blocking finding in one pass.
  Phase A also blocks on incomplete `cli_args`, missing `intermediate_tables`
  schemas, `local[0]`/`repartition(0)`, and dynamic-path `llm_todo`s. Phase B
  additionally blocks on unpatched excel/mongo/file I/O and non-table sink
  strategy gaps. After the last Phase A/B iteration, run once with
  `--verify-all`. Use `known-patches suggest` (Scala-native) before patching;
  `run-phase-a/b` auto-retry once on transient startup errors; provision is
  hash-gated (`provision --force-reseed` to reload all). `build-doctor` is a
  **fallback diagnostic** when a compile finding needs a fuller JAR/classpath
  report — not a required main-flow step.
- **In multi-batch mode, share the Coursier/Ivy cache.** Set
  `COURSIER_CACHE=~/.cache/coursier` and `SBT_OPTS="-Dsbt.ivy.home=$HOME/.ivy2"`
  in the env before launching the pool. All worktrees reuse the same local artifact
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
  - `Validation/shared/schemas/` (source of truth for I/O contracts)
  - `Validation/shared/analysis.json` (generated JVM shim — do not hand-edit)
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
        "diffs": [{"table": "...", "diff_path": "...", "schema_match": true, "row_count_a": null, "row_count_b": null, "verdict": "..."}],
        "documented_divergences": [...]
      },
      "trial_dir": "results/phase_b/<trial_id>/",
      "verdict": {"overall": "<status>", "reason": "..."}
    }
  ],
    "artifacts_index": {
    "analysis": "shared/analysis.json  (generated JVM shim)",
    "schemas": "shared/schemas/",
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

See `$SKILL_DIRECTORY/../references/scala/troubleshooting.md` for common issues and solutions,
including:

- Honest prewarm / `prevalidate` / `phase-reset` (build-doctor as fallback)
- `prevalidate` blocks `sinks=[]` when `ast_facts` still shows writes (no fake
  `no_sink_baseline`); run-index surfaces `phase_a.verdict=no_sink_baseline`
  for confirmed smoke-only trials
- `allow_empty` is intentional-empty only — UDF/connector gaps use
  `expected_divergences` (`scope=udf`); empty Phase B capture is not a soft pass
- Transient startup retry kills stale SCOS servers before the 900s re-run;
  fix hang root causes before raising `SCOS_TRIAL_TIMEOUT_SECS`
- Thin-jar + filtered dependency classpath for Phase A
- Mock-guard hard-fail and filter/join mock enrichment
- Host-aware `SCOS_TEST_PARALLELISM` capping
- JAR classpath conflicts between the workload and the kit
- SCOS session connection (local-server mode: `SNOWPARK_CONNECT_PYTHON_VENV` +
  `SNOWFLAKE_DEFAULT_CONNECTION_NAME`; do not set `SPARK_REMOTE`). "Local-server" =
  the translation server runs locally; Phase B **compute still executes in Snowflake**.
- Scala version mismatches (2.12 vs 2.13)
- Delta table path conflicts in local Phase A
- Snowflake JDBC authentication issues
- **`ParquetFileFormat.$deserializeLambda$` failures on JVM 17 + Spark 3.5**
  (SerializedLambda / URLClassLoader conflict during Phase A): set
  `SCOS_PHASE_A_SUBPROCESS=1` to run the workload in a child JVM
  (`SubprocessLauncher`) instead of in-process via `ReflectionEntrypoint`
