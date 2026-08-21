---
name: validate-pyspark-to-snowpark-connect
description: |
  Validate a completed PySpark to Snowpark Connect migration across an entire
  workload. Surveys every entrypoint, weights each by table complexity, groups them
  into semantic sections, packs them into balanced batches, and runs a pool of
  dedicated SDK worker sessions (one per batch) that each validate one batch in an
  isolated git worktree (original on local PySpark + Delta vs migrated on real
  Snowpark Connect / SCOS). Consolidates the migration fixes onto the deliverable
  branch and produces a merged validation report. Use for SCOS validation, migration
  verification, entrypoint parity checks, and documenting remaining divergences or
  manual-review cases.
  Triggers: validate scos, verify migration, run scos test suite.
parent_skill: snowpark-connect
---

# Validate PySpark to Snowpark Connect Migration

You are the top-level orchestrator. You survey the whole workload, weight every
entrypoint, group them into semantic sections, pack balanced batches, prepare a
git worktree per batch, then either inline a single batch (Step 4A) or launch
`batch.py pool` across dedicated Cortex Code SDK sessions (one per batch, 5
concurrent; Step 4B). Per-batch work follows `agents/batch-runner.md`; the pool
produces the merged report, the inline path runs merge-reports itself.

## Inputs (set by the migrate skill's hand-off)

- `$CONVERSION_ROOT` — path containing `Output/` (the migrated SCOS source).
- `$ORIGINAL_SOURCE` — path to the original PySpark source. Point it at the
  **directory whose internal layout mirrors `Output/`** (usually the parent that
  contains the project, not the inner project dir) so every source file resolves
  at `Output/<same-relative-path>`. The path must exist — a wrong/missing path is
  the most common Step 3 stumble.
- `$CONNECTION_NAME` — Snowflake connection name.
- `$SKILL_DIRECTORY` — this skill's directory.

```bash
RUN="uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts"
```

## Progress UI Emit Hooks

Non-fatal one-liners that feed the live dashboard started by the migrate skill.
`$CONVERSION_ROOT` must be the same `Conversion-SCOS-*` dir the migrate UI was
launched against (it already contains `.migration-ui/`). Skip entirely when
`<config.enable_progress_ui>` is `no` or `.migration-ui/` is absent.

```bash
BUS="python3 $SKILL_DIRECTORY/../../scripts/progress_bus.py"
UI_RUN="$CONVERSION_ROOT"   # same run dir the migrate skill used
```

| When | Command |
|---|---|
| Step 0 / validation starts | `$BUS phase-start --run "$UI_RUN" --phase survey \|\| true` |
| Step 1 survey done | `$BUS phase-end --run "$UI_RUN" --phase survey \|\| true` |
| Phase A begins (4A or first batch) | `$BUS phase-start --run "$UI_RUN" --phase phase-a \|\| true` |
| Phase A done | `$BUS phase-end --run "$UI_RUN" --phase phase-a \|\| true` |
| Phase B begins | `$BUS phase-start --run "$UI_RUN" --phase phase-b \|\| true` |
| Per-entrypoint Phase A/B result | `$BUS validation-ep --run "$UI_RUN" --ep "$EP_ID" --phase a\|b --status passed\|failed\|… --total $N \|\| true` |
| Phase B done | `$BUS phase-end --run "$UI_RUN" --phase phase-b \|\| true` |
| Harvest / merge-reports starts | `$BUS phase-start --run "$UI_RUN" --phase harvest \|\| true` |
| Merged report ready | `$BUS report-ready --run "$UI_RUN" --file "$REPORT" --phase harvest \|\| true` |
| Validation finished | `$BUS milestone --run "$UI_RUN" --phase validation-complete --message "Validation complete" \|\| true` then `$BUS summary --run "$UI_RUN" --data '{"validation_complete":true}' \|\| true` |

**Pool path (Step 4B):** `batch.py pool` emits these automatically when it finds
`.migration-ui/` under `$CONVERSION_ROOT` — you do not need to re-emit them.
**Inline path (Step 4A):** emit the table above yourself at each boundary.

Phase names to use: `survey`, `phase-a`, `phase-b`, `harvest`, `validation`,
`validation-complete`.

## Critical Rules

1. Entrypoint selection is fully automatic. The orchestrator packs batches with
   `batch.py`; `prepare-batches` scopes each worktree's schemas to
   its batch. Workers never ask which entrypoints to validate. (The orchestrator
   may narrow the *whole run* to a subset once, up front, in Step 1.6 — that is
   the only entrypoint-selection prompt, and it is orchestrator-level, not
   per-worker.)
2. `[TEST-PATCH]` commits stay on the validation branch; only `[MIGRATION-FIX]`
   commits reach the deliverable branch when each batch's harvester consolidates.
3. Golden Snowflake schemas (`<slug>_<run_id>_<ep>_GOLDEN`) never collide across
   batches because each worktree's `init` generates its own unique `run_id`.
4. `consolidate` conflicts arise when batches share `Output/` files — each batch's
   **harvester** (inside its session, see `agents/harvester.md`) resolves them
   inline: keep the migration fix; drop `[TEST-PATCH]` I/O rewrites (lines
   referencing `SCOS_*` or validation-only plumbing). Conflicts a harvester cannot
   resolve are surfaced to the orchestrator/user (it does not guess).
5. Never auto-clean schemas or worktrees. Ask once via `AskUserQuestion` before
   any teardown (Step 6 only).
6. All worktrees start at `$BASE_SHA`. Each batch's `[MIGRATION-FIX]` commits land
   on the deliverable branch as that batch's harvester runs **during Step 4**
   (4A or 4B; serialized across sessions by `consolidate`'s harvest lock), not in
   one batched step. Each batch's commits are exactly `$BASE_SHA..validation/<run-id>`.

## Workflow

### Step 0 — Preconditions & base capture

Verify all four inputs are set and non-empty. Verify `$CONVERSION_ROOT` is a git
repository and `$CONVERSION_ROOT/Output/` exists. Stop and report if either
check fails.

```bash
DELIVERABLE_BRANCH=$(git -C $CONVERSION_ROOT rev-parse --abbrev-ref HEAD)
BASE_SHA=$(git -C $CONVERSION_ROOT rev-parse HEAD)
```

Create the shared directory before seeding venvs or starting batches:

```bash
mkdir -p $CONVERSION_ROOT/Validation/shared
```

### Step 1 — Survey & weight

```bash
$RUN/schema_mine.py $ORIGINAL_SOURCE \
  --out $CONVERSION_ROOT/Validation/shared/schemas
```

Produces `schemas/manifest.json` — every entrypoint id, path, `source_runtime`
(`"databricks"` or `"spark"`), and `weight`
(`weight = 1 + 2×read_tables + write_tables + (loc // LOC_WEIGHT_DIVISOR)`, plus
`weight_breakdown`). Reads count double (each must be mocked); the LOC term keeps
large files from being under-rated. SQL-file tables are excluded (not attributable
to one entrypoint at plan time). `manifest.summary` also records
`n_databricks_entrypoints`, used by Step 1.5.

### Step 1.5 — Databricks credentials (only if any entrypoint is Databricks-native)

Read `manifest.summary.n_databricks_entrypoints` from `schemas/manifest.json`.
**If it is 0, skip this step.** Otherwise ask the user **once**, up front, with a
single `AskUserQuestion`:

> "One or more entrypoints are Databricks-native and get a more accurate Phase A
> baseline when run on a real cluster. Do you have a Databricks workspace `.env`
> file with `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, and `DATABRICKS_CLUSTER_ID`?
> If so, paste the full path. (The cluster just needs to be reachable by
> Databricks Connect — Unity Catalog is not required; the harness writes golden
> schemas to `hive_metastore`, or `DATABRICKS_CATALOG` if set.)"

Options: **"Skip — use local PySpark instead"** and **"Enter path"**.

If the user provides a path, validate it once (do not `cat` the file — it holds a
token; the command reads it):

```bash
$RUN/validate.py runtime-detect --conv-root $CONVERSION_ROOT --env-file <path>
```

- `"databricks_env_present": true` — set `DBX_ENV_FILE=<path>`; pass it to
  `prepare-batches` in Step 3 so every worker's harness uses databricks-connect.
- `"databricks_env_present": false` — explain and re-ask once. If still
  unresolved (or the user skips), leave `DBX_ENV_FILE` unset; those entrypoints
  fall back to local PySpark automatically.

### Step 1.6 — Scope to a subset (optional)

By default the whole workload is validated. Some runs only need a specific set
of entrypoints (one pipeline, a few files the user is iterating on). Ask **once**
with a single `AskUserQuestion`:

> "Mining found **N** entrypoints. Validate **all** of them, or just a
> **subset**? For a subset, reply with the entrypoints you want — ids, file
> paths, or a description (e.g. 'just the ingestion DAG')."

Options: **"All N entrypoints"** and **"A subset (I'll list them)"**.

- **All** — skip the rest of this step; go to Step 2.
- **Subset** — the user names the entrypoints in their next message. Resolve
  their answer to concrete entrypoint **ids** from `manifest.json` yourself
  (match on `id`/`path`/section intent), confirm the resolved id list back to
  the user in one line, then prune the mined schemas to exactly that set:

```bash
$RUN/validate.py scope-entrypoints \
  --conv-root $CONVERSION_ROOT \
  --ids "<comma-separated ids to KEEP>"
```

`scope-entrypoints` deletes every unselected entrypoint from
`Validation/shared/schemas/` (manifest index + `entrypoints/<id>/` dirs) in
place — it errors out (exit 2) on any unknown id rather than silently dropping
it. Everything downstream (sectioning, batching, the pool, the merged report)
then sees only the kept subset. No state.json or cap is involved; it runs on the
raw mined output before any worktree exists.

### Step 2 — Semantic sectioning (inline — the orchestrator does this itself, no subagent)

Read `$CONVERSION_ROOT/Validation/shared/schemas/manifest.json`. Group the
entrypoint ids into semantic **sections** — one section per DAG / pipeline /
domain. Use paths, directory structure, table overlap (shared read/write
targets), and naming to judge. Cover **every** mined entrypoint. Each
mined entrypoint id must appear in exactly one section — no duplicates,
no omissions, no ids absent from `manifest.json`. Step 3 enforces this
and will reject `sections.json` with exit 3 before creating any
worktrees, so this is not best-effort.

Write `$CONVERSION_ROOT/Validation/shared/sections.json`:

```json
[
  {
    "section_id": "<slug>",
    "name": "<human name>",
    "rationale": "<why these EPs belong together>",
    "ep_ids": ["<id1>", "<id2>"]
  }
]
```

### Step 3 — Batch & prepare worktrees

One call computes the batch plan from `sections.json`, prints it, and sets up
every batch's worktree — creates it at `$BASE_SHA`, runs `init` (cutting that
worktree's `validation/<run-id>` branch), and copies the mined schemas in pruned
to the batch's entrypoints:

```bash
WT=$CONVERSION_ROOT/Validation/worktrees
$RUN/validate.py prepare-batches \
  --conv-root $CONVERSION_ROOT \
  --sections $CONVERSION_ROOT/Validation/shared/sections.json \
  --base-sha $BASE_SHA \
  --worktrees-dir $WT \
  --schemas $CONVERSION_ROOT/Validation/shared/schemas \
  --connection $CONNECTION_NAME \
  --original-source $ORIGINAL_SOURCE
  # add: --databricks-env-file $DBX_ENV_FILE   (only if set in Step 1.5)
```

`prepare-batches` runs the source copy and source↔Output alignment check ONCE up
front (against the primary conv root) before creating any worktree, so a wrong
`--original-source` fails fast a single time; each worktree then gets a copy of the
validated source with its own fresh `run_id`.

It prints the plan — batch count, per-batch entrypoint count + weight, and any
warnings (e.g. a single entrypoint exceeding the weight cap). **Surface that plan
to the user** (informational; it does not block). It writes one consolidated
artifact, `Validation/shared/batches_prepared.json` — the batch plan **and** the
prepared worktree map:

```
{base_sha, worktrees_dir, max_entrypoints, max_weight, summary, warnings,
 batches:[{batch_id, section_ids, section_names, ep_ids, n_eps, total_weight,
           worktree, run_id, validation_branch, error}]}
```

`prepare-batches` exits **3** (before creating any worktrees) when
`sections.json` fails the coverage check — it prints the offending
entrypoints (duplicated, unsectioned, or unknown). Fix `sections.json`
(Step 2) and rerun Step 3; nothing is partially created.

Exit 0 if every batch prepared; exit 1 if any failed (its `error` field is set) —
report failed batches and continue with the ones that succeeded.

When `$DBX_ENV_FILE` is set (Step 1.5), append `--databricks-env-file
$DBX_ENV_FILE`; `prepare-batches` persists it into every worktree's `state.json`
so each worker's harness uses databricks-connect for its Databricks-native
entrypoints — no per-worker prompt.

#### Choosing the run path

Count the eligible batches — entries in `batches_prepared.json`'s `batches[]`
where `error` is null. State the count to the user, then route:

- **1 eligible batch** → go to **Step 4A** (inline single-batch runner).
- **2 or more eligible batches** → go to **Step 4B** (worker pool).
- **0 eligible batches** → all batches failed; apply the exit-1 handling above
  (report failed batches; there is nothing to run).

### Step 4A — Single batch (inline; no pool)

This path handles exactly one eligible batch. No pool session is created — all
phases run as subagents in this session, well within the 50-subagent ceiling.

Read the sole eligible batch entry from
`$CONVERSION_ROOT/Validation/shared/batches_prepared.json` and capture its
`worktree`, `run_id`, and `validation_branch`. Set the batch-runner inputs
(`CONVERSION_ROOT` = the batch's `worktree`; `PRIMARY_CONV_ROOT` = the original
`$CONVERSION_ROOT` captured in Step 0):

```bash
export CONVERSION_ROOT=<batch.worktree>
export PRIMARY_CONV_ROOT=<primary $CONVERSION_ROOT from Step 0>
export BASE_SHA=$BASE_SHA
export ORIGINAL_SOURCE=$ORIGINAL_SOURCE
export CONNECTION_NAME=$CONNECTION_NAME
export SKILL_DIRECTORY=$SKILL_DIRECTORY
export batch_id=<batch.batch_id>
```

Read `agents/batch-runner.md` and follow it end-to-end **in this session**
(synthesize → patch → Phase A → Phase B → summary → harvest → write batch
learnings), dispatching each phase agent as its own subagent. A single batch
dispatches ~5–6 subagents, well under the 50-subagent ceiling. Do not run 
the batch runner agent itself as a subagent.

There is **no `pool_status.json`** and **no `pool-status` polling** in this
path — progress is visible directly in-session. Proceed to Step 5 only after
the harvester completes and `validate.py summary` exited 0.

### Step 4B — Worker pool (2+ batches; 5 concurrent SDK sessions)

Launch the pool with **`bash run_in_background=true`** — this works in all
environments including CocoBox sandbox (the `monitor` tool is sandbox-incompatible
and will be SIGTERMed on long runs):

```bash
bash(
  command="$RUN/batch.py pool \
    --prepared $CONVERSION_ROOT/Validation/shared/batches_prepared.json \
    --primary-conv-root $CONVERSION_ROOT \
    --original-source $ORIGINAL_SOURCE \
    --connection $CONNECTION_NAME \
    --skill-directory $SKILL_DIRECTORY \
    --pool-size 5",
  run_in_background=true)
```

(`$RUN` expands to the `uv run … python …/scripts` prefix defined at the top.
Databricks credentials are not passed here — `prepare-batches` already persists
the env-file path into every worktree's `state.json` in Step 1.5, and each
worker reads it from there.)

**Friction log (optional).** Pass `--friction-log <path>` to give every batch and
its subagents a shared file for recording papercuts (confusing docs, unclear
errors, wasted iterations). The pool injects the path into each batch-runner
prompt and exports it as `$FRICTION_LOG`; single-line `>>` appends are atomic up
to `PIPE_BUF` on Linux, so concurrent batches don't clobber each other.

`batch.py pool` manages the entire fan-out on the orchestrator's behalf:

- Reads `batches_prepared.json`; skips any batch whose `error` field is non-null.
- Spawns one dedicated SDK cortex session per batch (following
  `agents/batch-runner.md`), keeping `--pool-size` running concurrently and
  starting the next as each finishes. Each session has its own subagent budget,
  removing the old 50-subagent-per-session ceiling.
- Retries a crashed batch once: a fresh session resumes from `state.json`
  milestones.
- Runs `batch.py merge-reports` itself at the end (see Step 5).

**Tuning knobs** (env vars, defaults tuned for a typical Snowflake account —
override only if you hit rate limits or want more parallelism):

| Env var | Default | Effect |
|---------|---------|--------|
| `SCOS_CLEANUP_WORKERS` | `16` | Concurrent `DROP SCHEMA CASCADE` connections opened by `cleanup.py` at Step 6. Clamped to the actual number of schemas found. |
| `SCOS_PYTEST_WORKERS` | one per entrypoint | `pytest-xdist` worker count for Phase B inside each batch. Lower it if the shared warehouse chokes. |
| `SCOS_GET_WORKERS` | `8` | Per-batch thread pool for `GET` of staged sinks from Snowflake into the local sink-capture dir. |

#### Observability

**Immediately after starting the pool**, tell the user the shell ID and that live
status is in `$CONVERSION_ROOT/Validation/pool_status.json` — updated continuously
with every batch's status, phase, session ID, tokens, and timing (readable any
time with `cat`).

`pool_status.json` is the **single source of truth** — updated live as the pool runs, holds the fully terminal state at pool exit. Shape (see `_write_status` in `batch.py`):

| Field | Meaning |
|-------|---------|
| `updated_at` | ISO timestamp of the last write |
| `run.pool_size` | Configured concurrency |
| `run.status` | Pool state: `running` \| `done` \| `partial` |
| `run.started_at` / `run.finished_at` | Pool wall-clock timestamps |
| `run.n_batches` / `run.n_done` / `run.n_failed` | Batch counts |
| `run.metrics_totals` | Aggregate tokens + timing across all completed batches (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `total_tokens`, `duration_ms`, `num_turns`) |
| `merge_report_path` | Path to merged `REPORT.md`; populated after `merge-reports` completes |
| `batches[].status` | Per-batch: `queued` \| `running` \| `done` \| `failed` |
| `batches[].current_phase` | The batch worker's current pipeline stage, derived every 10 s by polling that worktree's `Validation/state.json`. Values: `starting` (worker hasn't written state yet), `synthesizing` (`entrypoints_selected`), `patching` (`synth_deep`), `Phase A` (`patches_authored`; source-runner runs Phase A), `Phase A (n/N done)` (Phase A with trial-progress overlay), `Phase B` (`phase_a_complete`; scos-runner runs Phase B), `Phase B (n/N terminal)` (Phase B with trial-progress overlay), `Phase B complete` (`phase_b_complete` / `phase == phase_b_done`). Every value corresponds to a real file write — a `validate.py record-milestone` / `record-iter` / `record-trial-status` call, or a phase transition from `_advance_phase`, made by the worker or one of its subagents. |
| `batches[].session_id` | SDK session ID for that batch |
| `batches[].metrics` | Per-batch tokens + duration, written when the batch reaches `done` status after `summary.json` verification (staged in `pending_metrics` when the `ResultMessage` arrives, then promoted on success): `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `duration_ms`, `num_turns`, `total_cost_usd` (note: no `total_tokens` — that is only in `run.metrics_totals`) |
| `batches[].summary_json_path` | Path to that batch's `Validation/results/summary.json` (populated after Phase B completes) |
| `batches[].error` | Set if the batch failed after retries |

**Do NOT `cortex resume` a live pool session** — resume attaches interactively
and takes the running batch over.

#### Waiting for completion

Every ~10 min, post the pool status to the user. The pool takes 30-90 min. To
wait without hitting the 120 s default bash timeout, block on the pool's own shell
— it returns at the timeout OR pool exit, whichever is first (do NOT
`bash(command="sleep 600 && ...")`; it gets killed at 120 s):

```text
bash_output(bash_id=<pool_bash_id>, wait=true, timeout_ms=600000)
```

Then run the status command:

```bash
$RUN/batch.py pool-status --root $CONVERSION_ROOT/Validation
```

It prints a header + one line per batch (done / running / queued / failed) with
phase and, once a batch finishes, its tokens (per-batch on completion, not live).
**Post the full stdout verbatim** in a fenced `text` block each iteration (header
plus every batch row — do not paraphrase; the value is the live per-batch phase).
Add at most one sentence of commentary. Stop once the header starts with `[pool]
done in` or `[pool] partial in`; move on to Step 5.

**Exit codes** from `batch.py pool` itself (visible through
`bash_output(bash_id=<pool_bash_id>, wait=false)` once the process exits):

- `0` — every batch reached a `done` batch-state (a batch with internal
  `hard_stuck` EPs is still `done`).
- `1` — at least one batch ended `failed` after its retry. Surface the failed
  `batch_id`(s); the remaining batches' results are still valid and already merged.

### Step 5 — Merged report

**Pool path (4B):** `batch.py pool` runs `batch.py merge-reports` automatically
at the end of Step 4B. Read `pool_status.json` → `merge_report_path`
(= `$CONVERSION_ROOT/Validation/results/REPORT.md`) and surface the path to the
user.

**Inline path (4A):** `pool_status.json` does not exist. Run `merge-reports`
yourself (the command below is idempotent) and take the `REPORT.md` path from
its stdout:

```bash
$RUN/batch.py merge-reports \
  --prepared $CONVERSION_ROOT/Validation/shared/batches_prepared.json \
  --out $CONVERSION_ROOT/Validation
```

Writes `$CONVERSION_ROOT/Validation/run_index.json` and
`$CONVERSION_ROOT/Validation/results/REPORT.md`. Batches with an error or a
missing `Validation/` tree are skipped with a warning. Print the `REPORT.md`
path in your message to the user.

**View the report:**

```bash
uv run --project $SKILL_DIRECTORY/.. python -m streamlit run \
  $SKILL_DIRECTORY/scripts/report/validation_report_app.py \
  -- --run-root $CONVERSION_ROOT/Validation
```

**End state on the deliverable branch:** per-batch `Validation/batches/<id>/`
trees + merged `Validation/run_index.json` and `Validation/results/REPORT.md`,
plus `Output/` = migrated code with all `[MIGRATION-FIX]` commits applied.

### Step 6 — Cleanup gate

Use `AskUserQuestion` **once** to ask whether to:
- **(a) Drop ALL per-batch golden Snowflake schemas** (list each `run_id` from
  `batches_prepared.json`).
- **(b) Tear down git worktrees and `validation-base/*` branches.** Keep the
  `validation/<run-id>` branches for inspection unless the user asks otherwise.

Only on an affirmative answer, for each batch in `batches_prepared.json`:

```bash
$RUN/cleanup.py --conv-root <worktree> --force
git -C $CONVERSION_ROOT worktree remove <worktree>
git -C $CONVERSION_ROOT branch -D validation-base/<batch_id>
```

If declined, give the user the exact commands to run later. Never auto-clean.

### Step 7 — Final display

After Step 5 wrote `REPORT.md` (regardless of the Step 6 cleanup gate), post one
final message to the user that pulls the numbers straight out of the merged artifacts:

1. **Terminal status counts** — read `Validation/run_index.json` → `totals` and
   print them verbatim (overall verdicts + comparison verdicts).
2. **Full entrypoint table** — one row per EP, joining `run_index.json`
   (`entrypoints[]`, keyed by `batch_id`) with `pool_status.json`
   (`batches[].metrics`, same key). Columns: Batch, Entrypoint, Overall,
   Comparison, Time (s), Tokens (input+output+cache), **Reason**
   (`entrypoints[].verdict.reason`). Sort by `batch_id`. **Inline path (4A):** no
   `pool_status.json` — build from `run_index.json` alone, leaving Time/Tokens blank.
2a. **Flag no-baseline / stuck EPs.** For every row whose Overall is
   `passed_no_baseline` or `hard_stuck`, call it out explicitly as **needs human
   review** and print its `verdict.reason`.
3. Finish with the on-disk paths already surfaced in Step 5 (`REPORT.md`,
   `run_index.json`, and the streamlit viewer command).

Do not recompute totals from the EP list — the merger already did it; batches that failed before producing metrics show blank Time / Tokens cells.

## Stopping Points

- Missing hand-off inputs: stop and report the missing input.
- `schema_mine.py` fails: stop and report.
- `prepare-batches` exits 3 (`sections.json` coverage error — an entrypoint is
  duplicated, unsectioned, or unknown): fix `sections.json` so every mined
  entrypoint appears in exactly one section, then rerun Step 3. No worktrees are
  created on a coverage failure. If it prepares some batches but reports a
  per-batch error (exit 1), skip those, surface them, and continue.
- A batch ends `failed` after the pool's retry (fresh session resumed from `state.json`):
  pool exits 1; surface the failed `batch_id`(s). Other batches' results stay valid + merged.
- `validate.py consolidate` exits 1 (run from the harvester): surface the error to the user.
- Cherry-pick conflicts that cannot be resolved by the harvester: surface the
  conflicting commit SHA and files. Other batches continue unaffected.

## Success Criteria

- Every prepared batch reported back: pool exit 0 (Step 4B), or — single batch
  (Step 4A) — the inline batch-runner reached `summary` exit 0 and harvester
  success. OR a batch is reported failed with a clear explanation (pool exit 1 /
  harvester conflict; listed in `pool_status.json` for 4B or inline for 4A).
- All `[MIGRATION-FIX]` commits are on the deliverable branch — workers
  self-reported harvest success.
- `batch.py merge-reports` completed — run automatically by `batch.py pool`
  (Step 4B) or manually by the orchestrator (Step 4A) —
  `Validation/run_index.json` and `Validation/results/REPORT.md` written.
- The merged report explains which results are safe matches, which diverge, and
  which need human review.

## Run Artifacts

- `Validation/run_index.json` — merged master manifest (all batches; consumed by
  UI or downstream tooling)
- `Validation/results/REPORT.md` — merged human-readable summary
- `Validation/batches/<batch_id>/` — per-batch artifact trees (each contains its
  own `run_index.json`, `events.jsonl`, `state.json`, `results/`, `shared/`)
- `Validation/pool_status.json` — sole pool-level artifact (Step 4B only): updated live during the run and holds the fully terminal state at pool exit. Full schema documented in the Step 4B observability table.
- `Validation/worktrees/` — per-batch git worktrees (one per batch, under the
  primary conv root)
- `Validation/shared/schemas/manifest.json` — mined weight manifest
- `Validation/shared/sections.json` — semantic sections (Step 2 output)
- `Validation/shared/batches_prepared.json` — batch plan + worktree map (Step 3
  output; the single source of truth consumed by the worker pool, consolidate,
  merge, and cleanup steps)
