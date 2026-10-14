---
name: batch-runner
description: Per-batch validation worker. Validates one batch of entrypoints end-to-end inside a git worktree the orchestrator has already prepared — synthesize → patch → Phase A → Phase B → summary → harvest. Self-contained: returns only after the batch's [MIGRATION-FIX] commits are on the deliverable branch.
---

# Batch Runner (Worker)

This agent validates one batch of entrypoints end-to-end inside a git worktree
the orchestrator has already prepared, then harvests this batch's fixes back to
the primary deliverable branch before returning. Think of it like a developer on
a feature branch: validate, then merge your real commits back to main.

## Preconditions (the orchestrator has done these)

`validate.py prepare-batches` already created this worktree, ran `init`, and
scoped its schemas. So when you start:

- `$CONVERSION_ROOT/Validation/` is initialized — `state.json` exists and the
  worktree is on its `validation/<run-id>` branch.
- `$CONVERSION_ROOT/Validation/shared/schemas/` is **already pruned to exactly
  this batch's entrypoints** (mine + selection happened upstream). The data-synthesizer
  neither mines nor selects.
- `$CONVERSION_ROOT/Validation/source/` holds the original source for Phase A.

**Prior learnings:** Before Step 1, read
`$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md` into your context.
It contains patch patterns, schema quirks, and dialect issues discovered by
workers that completed before you. Apply any relevant patterns rather than
rediscovering them.

## Inputs (handed off by the orchestrator)

- `$CONVERSION_ROOT` — path to this batch's git worktree (contains `Output/` and
  the prepared `Validation/`).
- `$PRIMARY_CONV_ROOT` — path to the **primary** conversion repo (not this
  worktree). Passed by the orchestrator; used only in the harvest step.
- `$BASE_SHA` — the git SHA all worktrees branched from. Used by consolidate.
- `$ORIGINAL_SOURCE` — path to the original PySpark source.
- `$CONNECTION_NAME` — Snowflake connection name.
- `$SKILL_DIRECTORY` — this skill's directory.

```bash
RUN="uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts"
```

## Constraints

- **Single Snowflake connection.** All entrypoints in this batch target tables in
  the same Snowflake database via the same connection. Multi-database /
  multi-account workloads are not supported.
- **Python source files only.** Entrypoints are `.py` files (top-level scripts,
  package modules, or Databricks notebook-style files). `.ipynb` notebooks and
  Java/Scala entrypoints are out of scope.
- **Explicit table dependencies.** All table reads/writes are declared in
  `schemas/entrypoints/<id>/tables/<KEY>.json` with `access:
  "read"|"write"|"readwrite"` and `category: "table"`. Implicit Hive metastore /
  Glue catalog lookups are not auto-discovered — the data-synthesizer must surface them.
- **SQL files must run.** Entrypoints that execute a project `.sql` template
  depend on every table that file reads. The data-synthesizer merges tables from
  `sql_files.json` into the entrypoint's `tables` dict: **read** tables →
  `access: "read"` (full columns; datagen always mocks them); **write-only**
  tables → `access: "write"`. SQL files are read from the sibling source path;
  they are never mocked.
- **Explicit file dependencies.** All file reads are declared in `tables` with
  `category: "file"`. Relational file tables need `columns` and `format`;
  non-relational ones need `relational: false`, `format`, and `document_schema`.
  Datagen writes `mock_file` paths after seeding.

## Critical Rules

1. The workspace is already scoped to this batch. The data-synthesizer completes the
   pre-mined, pre-selected schemas — it does not mine or select, and it never
   asks the user which entrypoints to validate.
2. Use `Validation/` as the workspace root.
3. Keep `Validation/source/` and `Output/` as the two code trees under test.
4. Use the shared test kit in `scripts/harness/` for both flavors.
5. Phase A defaults to local PySpark+Delta. Databricks-native entrypoints (by
   `source_runtime`) use databricks-connect when the orchestrator resolved and
   persisted workspace credentials before this worker started (in
   `state.json`); otherwise they fall back to local PySpark automatically. This
   worker neither prompts for nor detects credentials.
6. Migrated Phase B must use real `snowpark_connect`; do not shim it.
7. There are no shims or mock filesystems. Non-Spark I/O (cloud reads/writes,
   secrets, widgets, external deps) is rewritten by the **patch blueprint** into
   native Spark + env-var indirection, or deleted. Every patch is added via
   `validate.py patch-add`, which smoke-tests it (unique match + still compiles)
   before committing the `Output/` side as `[TEST-PATCH]`. Any subagent may add
   patches on the fly when a run reveals a missed dependency.
8. Keep per-entrypoint runs isolated:
   - local: fresh per-test warehouse and schema
   - SCOS: clone a pre-provisioned golden Snowflake schema per test

   Because each trial is fully isolated, **always run the batch entrypoint tests
   in parallel** — one pytest invocation over the whole tests dir with
   pytest-xdist (`-n auto`), in BOTH Phase A and Phase B. Never loop pytest
   per-entrypoint and never run serially. Each runner's `seed-venv` installs
   `pytest-xdist`. Only fall back to `-n 0` for a specific, reproducible
   concurrency error, and report it as harness friction to fix.
9. If Phase A cannot produce a trustworthy baseline, still run Phase B and flag
   the result for human review.
10. All test-only changes to `Output/` (the blueprint I/O patches) are committed
    on the `validation/<run-id>` branch with the `[TEST-PATCH]` prefix; genuine
    logic fixes use `[MIGRATION-FIX]`. After summary, **this worker** dispatches
    `agents/harvester.md` (Step 6) to cherry-pick its own `[MIGRATION-FIX]`
    commits onto the deliverable branch via `validate.py consolidate` (serialized
    against other batches by the harvest lock). `[TEST-PATCH]` commits are not
    cherry-picked.
11. Keep `[TEST-PATCH]` edits within this batch's own entrypoint files. The
    worktree holds the full `Output/` tree but only this batch's entrypoints are
    under test; patches that touch out-of-batch files cause avoidable
    consolidation cherry-pick conflicts. Scope all `relative_file` globs to this
    batch's entrypoint directories (see the batch scope blockquote in `patch-author.md`).

## Phase A vs Phase B: environment differences

Phase A runs the original source on local PySpark + Delta. Some SQL constructs
(e.g. `QUALIFY`, Databricks-specific `MERGE INTO` variants) are not supported by
open-source Spark SQL. When Phase A fails due to such environment differences,
the trial is marked `phase_a_skipped` — that is **not** a terminal verdict and
does **not** exempt the entrypoint from Phase B. Phase B still runs every batch
entrypoint on real SCOS (full Snowflake SQL surface). Trials without a Phase A
baseline that succeed on SCOS end as `passed_no_baseline` for operator review.

| Status | Phase | Terminal? | Meaning |
|--------|--------|-----------|---------|
| `phase_a_skipped` | A | No | No local baseline. Phase B still required. |
| `passed` | B | Yes | SCOS output matched Phase A baseline. |
| `passed_no_baseline` | B | Yes | SCOS succeeded; manual review of captured output. |
| `hard_stuck` | B | Yes | Rare terminal state: no credible Phase B fix path remains. |

## Workflow

### Step 1 — Synthesize

Dispatch **`agents/data-synthesizer.md`** once. The schemas are already mined and scoped
to this batch, so the data-synthesizer goes straight to completing the analysis: run
`datagen`, take the first problem, map it to one repair unit, fix only that unit, run
incremental datagen, and repeat until `datagen` returns `"ok": true`. Then resolve or
explicitly dismiss any remaining warnings before handing control back. Entrypoints must
use the project's real `.sql` templates — never stub them with fake SQL.

**Data-synthesizer exit gate:** `manifest.complete: true` alone is not enough. Do not
proceed to Step 2 until `datagen.py` exits 0 and prints `"ok": true`, and the
data-synthesizer has resolved or explicitly dismissed every warning from the final run.
Run all data-synthesizer steps inside the subagent only.

### Step 2 — Author patches

Dispatch **`agents/patch-author.md`** once. Submit patches via `validate.py
patch-add` (batch, smoke-tested). Runners may add more patches later with the
same command.

### Step 3 — Run Phase A

Dispatch **`agents/source-runner.md`** once. It attaches to the pool-shared
`Validation/shared/.venv-source` (seeding it only when the shared venv is not
yet populated — typically for the first batch of a run), then runs all batch
entrypoints in one parallel pytest pass. Handles local PySpark + Delta or
Databricks-connect source per `source_runtime`. `phase_a_skipped` is not
terminal — Phase B still runs every batch entrypoint.

### Step 4 — Run Phase B on SCOS

Dispatch **`agents/scos-runner.md`** once per Phase B round — **every** batch
entrypoint, including `phase_a_skipped`. The runner attaches to the
pool-shared `Validation/shared/.venv-scos` (seeding only when not yet
populated), then stays alive for the round: on `TABLE_OR_VIEW_NOT_FOUND` /
`COLUMN_NOT_FOUND` it edits `schemas/entrypoints/<id>/tables/<KEY>.json` (or
`_meta.json` for entrypoint-level fields), regenerates mocks, and
re-runs pytest (golden schemas are provisioned automatically by the harness on
first use and reseeded on schema edits via hash-gated provisioning). Dispatch
**one** migration-fixer per round only for **code/dialect** failures (see
`agents/scos-runner.md`). Treat this as a per-trial diagnosis loop inside the
batch: schema/data, patch/plumbing, harness, or code fix. Repeat until every
trial is terminal.

Do not combine full SQL-catalog repair + full Phase B re-run in a single
mega-agent turn on large workloads — the runner's inline repair loop is one
trial/batch at a time.

### Step 5 — Summarize

Before summary: every batch entrypoint must reach a **terminal** status in
`state.json` (`passed`, `passed_no_baseline`, or `hard_stuck`). Summary may
auto-recover trials still marked `pending` from Phase B iter data; any trial that
remains non-terminal blocks summary. Every `Output/` change must already be
committed — `run_index.json` reads the validation-branch git log at summary time
(`[MIGRATION-FIX]` and `[TEST-PATCH]` alike).

Commit any outstanding Output/ changes first (skip if none):

```bash
$RUN/validate.py \
  commit --conv-root $CONVERSION_ROOT --kind migration-fix \
         --trial-ids "<trial id(s)>" --message "<what + why>"
```

Use `--kind test-patch` for harness-only changes. Uncommitted direct edits to
`Output/` block summary.

Then run summary:

```bash
$RUN/validate.py \
  summary --conv-root $CONVERSION_ROOT
```

Summary writes `results/summary.json` and `results/REPORT.md`, builds
`run_index.json`, then verifies all required artifacts exist. It is fully offline
— the SCOS output for every trial (including `passed_no_baseline`) is already
captured to `results/phase_b/<trial>/tables/` during Phase B.

The summary should clearly separate:

- matched entrypoints,
- documented divergences,
- Phase B runs with no Phase A baseline, and
- hard-stuck items.

`summary` exit codes:

- **1** — blocked preconditions: non-terminal trials after auto-recovery, or
  uncommitted `Output/` changes.
- **4** — summary ran but a required artifact is missing: `results/summary.json`,
  `results/REPORT.md`, `run_index.json`; or `events.jsonl` is absent (it must
  exist from prior state-recording calls).

Do NOT report back until `summary` exits 0.

### Step 6 — Harvest fixes to the deliverable branch

After summary exits 0, dispatch **`agents/harvester.md`** as a **foreground
(synchronous) subagent**. Pass:

- `WORKTREE_CONV_ROOT` = `$CONVERSION_ROOT`
- `PRIMARY_CONV_ROOT` = `$PRIMARY_CONV_ROOT`
- `VALIDATION_BRANCH` = your `validation/<run-id>` (read it from
  `state.json["git"]["validation_branch"]` if you don't have it)
- `BASE_SHA`, `SKILL_DIRECTORY`

The harvester serialises automatically: if another batch is currently harvesting,
it waits and retries (like a developer waiting for their `git push` to be
accepted). It returns only when your `[MIGRATION-FIX]` commits are on
the deliverable branch — or it reports an unresolvable conflict for you to
surface to the orchestrator.

### Step 7 — Write batch learnings

After harvest completes, append a section to
`$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md`:

```
### Batch <batch_id>
- <specific, actionable learning>
- <specific, actionable learning>
```

Include: patch patterns that worked, schema quirks (e.g. TIMESTAMP_NTZ
handling), Phase A dialect skips, systemic issues affecting multiple
entrypoints. Be precise: "boto3 S3 reads: rewrite using `SCOS_INPUT_*` env
var, patch P7 template" not "had S3 issues". Skip anything trivial or
batch-specific with no generalisation value.

Write as a single `open(path, 'a').write(content)` call (POSIX O_APPEND) so
concurrent workers' sections don't interleave. Each section starts with
`### Batch <batch_id>` so even a rare interleave remains readable. Write
this as the last action before your final report message. The worker finishes
after batch learnings are written; its final chat message is its report (see
Report Back).

## Stopping Points

- Snowflake provisioning fails: stop and report the failure.
- Phase A cannot produce a baseline: continue to Phase B and mark for manual review.
- Phase B cannot reach SCOS or still diverges after fixer attempts exhausted:
  mark those trials `hard_stuck` only when there is truly no credible next fix.
  Do not use `hard_stuck` just because many iterations have passed. It is
  terminal and does not block summary, but it should be rare.
- `validate.py summary` blocks on non-terminal trials: resolve them (re-dispatch
  the SCOS runner if needed), then re-run summary.
- Harvester returns an unresolvable conflict: surface it to the orchestrator with
  the conflicting commit SHA and file(s). Do NOT remove worktrees or schemas.
- If re-dispatched on an existing worktree, `Validation/` and its schemas are
  intact — read `state.json` milestones and resume from the first incomplete
  step. Do not re-run the data-synthesizer or regenerate mock data.

Do **not** stop for schema drops or worktree teardown — those are the
orchestrator's responsibility after all workers have reported.

## Success Criteria

- Every batch entrypoint runs Phase B and reaches a **terminal** verdict before
  Step 5: `passed`, `passed_no_baseline`, or `hard_stuck`. (`phase_a_skipped` is
  Phase-A-only and does not count.)
- `validate.py summary` exits 0 (all required artifacts present, including
  `run_index.json`).
- All `Output/` changes are committed on the `validation/<run-id>` branch before
  summary runs.
- `agents/harvester.md` completes (exit 0) — this batch's `[MIGRATION-FIX]`
  commits are on the primary deliverable branch.
- Batch learnings written to `$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md` (Step 7).

## Report Back

After `validate.py summary` exits 0, your final message must include:

- **`results/summary.json` path** — `$CONVERSION_ROOT/Validation/results/summary.json`
- **Per-EP terminal-status table:**

| ep_id | terminal_status | notes |
|-------|----------------|-------|
| ...   | passed / passed_no_baseline / hard_stuck | optional reason |

(The orchestrator already has this batch's `run_id` and `validation_branch` from
`batches_prepared.json`; you do not need to restate them.)

## Artifacts

- `Validation/state.json` — includes `git.{original_branch,validation_branch}` and `run_id`
- `Validation/shared/patch_blueprint.json` — the test-patch record
- `Validation/shared/schemas/` — `manifest.json`, `entrypoints/<id>/` directories
- `Validation/shared/mock_data/`
- `Validation/tests/`
- `Validation/results/` — `summary.json`, `REPORT.md`, `run_index.json`, `phase_a/`, `phase_b/`
- `Validation/events.jsonl` — append-only timeline of all state transitions

## Trial schema (`state.json`)

The worktree's `Validation/state.json` persists trial status and iteration records. Use it to inspect progress and diagnose stalls:

- **Terminal status**: `trials[<id>].status` (string, values: `"passed"`, `"passed_no_baseline"`, `"hard_stuck"`, or `"pending"`; `"phase_a_skipped"` appears mid-run but is not terminal).
- **Phase A iterations**: `trials[<id>].phase_a_iters` (flat list of iteration objects; each `{iter: int, passing: int, failing: int, ...}`).
- **Phase B iterations**: `trials[<id>].phase_b_iters` (flat list of iteration objects; same shape).
- **Example query** (machine-readable): `validate.py status --json` prints `{<id>: {status, phase_a_pass, phase_b_pass}}` (trial IDs as top-level keys) for programmatic inspection.

To check status mid-batch:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  status --conv-root $CONVERSION_ROOT --json
```
