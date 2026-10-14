# validate-pyspark-to-snowpark-connect

This skill validates a completed PySpark to Snowpark Connect migration across an
entire workload. It surveys every entrypoint, packs them into balanced batches by
semantic section and weight, runs each batch in an isolated worker environment,
and compares end states at scale.

## Goal

The workflow is:

1. Mine and weight every workload entrypoint (`weight = 1 + 2×reads + writes + loc//50`).
2. Group entrypoints into semantic sections and pack balanced batches, then
   validate them in a pool of 5 concurrent Cortex Code SDK sessions (one per
   batch), managed by `batch.py pool`.
3. Mine each entrypoint's table schemas (with read/write `access`) statically and
   synthesize mock inputs from them (the LLM only resolves explicitly-flagged gaps).
4. Provision an isolated test environment for each entrypoint.
5. Run the original workload locally on PySpark + Delta when possible.
6. Run the migrated workload on real Snowpark Connect / SCOS.
7. Snapshot the final schema state for each run and compare it.
8. Iterate on harness or migration issues until the results match, or clearly
   flag what still needs human review.

## Design Principles

- **Two tiers.** `SKILL.md` is the orchestrator: it surveys + weights the
  workload, groups entrypoints into semantic sections, packs balanced batches,
  and prepares one git worktree per batch. It then launches `batch.py pool`,
  which spawns one dedicated **Cortex Code SDK session per batch** (5 concurrent,
  replenished as each finishes). Because each batch runs in its own session, it
  gets its own subagent budget — sidestepping the 50-subagent-per-session cap of
  the old in-session pool. Each session follows `agents/batch-runner.md` (the
  data-synthesizer / patch-author / runners pipeline).
- **Observable.** `batch.py pool` keeps a single `pool_status.json` (per-batch
  `status`, `current_phase`, `session_id`, plus per-batch and pool-wide
  token/time metrics — updated live and fully terminal at pool exit). The
  orchestrator runs `batch.py pool-status --root …` every ~10 minutes and
  echoes a header + per-batch report to the user so a 30-90 min pool run
  isn't a silent black box. Don't `cortex resume` a live pool session — it
  takes the batch over.
- **Auto-batched.** Entrypoints are grouped by semantic section (DAG / pipeline /
  domain) and packed into batches under an entrypoint-count cap and a weight cap.
  `schema_mine.py` emits a per-entrypoint `weight` (`1 + 2×reads + writes +
  loc//50` — the LOC term keeps large files from looking trivial); `batch.py`
  packs whole sections together (a section's entrypoints stay grouped; several
  small sections share a batch when they fit both caps), splitting only a section
  too big to fit on its own. Defaults: 10 entrypoints / weight 80 per batch.
- **Worktree isolation.** `validate.py prepare-batches` sets up one git worktree
  per batch under `<conv-root>/Validation/worktrees/` — created at the same
  `BASE_SHA`, initialized, with its schemas pruned to that batch's entrypoints.
  Each worktree's unique `run_id` keeps its golden Snowflake schemas from
  colliding with any other worker's.
- **A shared uv cache** (`UV_CACHE_DIR`) lets all worktrees hardlink packages
  instead of recopying them — the disk-efficiency mechanism for the pool.
- **Deterministic-first analysis.** `schema_mine.py` statically mines entrypoints,
  tables (with read/write `access`), and column schemas from the AST + embedded
  SQL, and `datagen.py` synthesizes mock inputs from those schemas. The LLM only
  resolves explicitly-flagged gaps (`llm_todo`), adds dependencies that flow
  through external libs, and reviews mock-data realism — it never guesses schemas.
- Each entrypoint gets its own isolated mock data and schema lifecycle.
- The shared test kit under `scripts/harness/` is the single runtime the runners
  copy and customize.
- Phase A uses local PySpark+Delta or a Databricks cluster (auto-detected from the
  data-synthesizer's `source_runtime` classification, resolved non-interactively).
- Phase B must hit real `snowpark_connect`; it is never shimmed.
- There are no library shims or mock filesystems. Non-Spark I/O (cloud
  reads/writes, secrets, widgets, dead external deps) is rewritten by a
  smoke-tested **patch blueprint** into native Spark + env-var indirection, or
  deleted. Any subagent can add patches on the fly via `validate.py patch-add`.
- Test-only `Output/` changes live as `[TEST-PATCH]` commits on each worker's
  `validation/<run-id>` branch; `[MIGRATION-FIX]` commits from all workers are
  cherry-picked onto the deliverable branch via `validate.py consolidate` (which
  auto-discovers the `validation/*` branches). `[TEST-PATCH]` commits stay on the
  validation branch for inspection.
- If no trustworthy local baseline can be produced, Phase B still runs and the
  result is flagged for manual review instead of being silently skipped.

## High-Level Flow

1. **Survey & weight** — the orchestrator runs `schema_mine.py $ORIGINAL_SOURCE
   --out Validation/shared/schemas`, producing `manifest.json` with every
   entrypoint, its `tables` (read/write `access`), and a per-entrypoint `weight`.
2. **Scope (optional)** — the orchestrator asks once whether to validate the whole
   workload or just a subset. For a subset it resolves the user's answer to
   entrypoint ids and runs `validate.py scope-entrypoints --ids …`, which prunes
   the mined `schemas/` in place so only the kept entrypoints flow into sectioning,
   batching, and the report.
3. **Section** — the orchestrator groups entrypoint ids into semantic sections
   (`sections.json`), one per DAG / pipeline / domain.
4. **Batch & prepare worktrees** — a single `validate.py prepare-batches` call
   packs balanced batches from the sections (honoring the entrypoint-count and
   weight caps), prints the plan, then creates one git worktree per batch at
   `BASE_SHA`, runs `init` (cutting each `validation/<run-id>` branch), and copies
   the mined schemas in pruned to that batch's entrypoints. It writes one
   consolidated `batches_prepared.json` (the batch plan + worktree map).
   (`batch.py` exposes the same batching logic standalone for a
   side-effect-free plan preview into its own `batches.json`.)
5. **Worker pool** — the orchestrator launches `batch.py pool`, which spawns up
   to 5 dedicated Cortex Code SDK sessions concurrently, one per batch,
   replenishing with the next batch as each finishes. Each session follows
   `agents/batch-runner.md`; its workspace is already scoped, so it goes straight
   to:
   - the data-synthesizer **completes** the batch's schemas (resolves `llm_todo`, merges
     `sql_files`, runs datagen),
   - the patch-author writes the I/O blueprint,
   - the source runner produces Phase A baselines (local PySpark + Delta, or
     Databricks Connect),
   - the SCOS runner runs Phase B against cloned golden schemas on real
     `snowpark_connect` and compares snapshots,
   - `validate.py summary` writes the batch's `run_index.json` once every trial
     is terminal (`passed`, `passed_no_baseline`, or `hard_stuck`),
   - the harvester cherry-picks the batch's own `[MIGRATION-FIX]` commits onto the
     deliverable branch via `validate.py consolidate` (serialized across sessions
     by a session lock; conflicts keep the migration fix and drop `[TEST-PATCH]`
     scaffolding), then appends its cross-batch learnings.

   `batch.py pool` tracks `pool_status.json` — a single JSON file with status +
   `current_phase` + session id + per-batch and pool-wide token/time metrics
   per batch, updated live and fully terminal at pool exit.
6. **Merged report** — `batch.py pool` runs `batch.py merge-reports` itself once the pool
   drains, assembling every batch's `Validation/` tree into
   `Validation/batches/<id>/` and writing the merged `Validation/run_index.json` +
   `Validation/results/REPORT.md`.
7. **Cleanup gate** — the orchestrator asks once before dropping per-batch golden
   schemas, worktrees, or `validation-base/*` branches; nothing is auto-dropped.

## Human Review Path

When Phase A cannot produce a trustworthy baseline, the skill still:

- runs the migrated entrypoint on SCOS,
- captures its outputs,
- reports why the baseline was unavailable, and
- flags the SCOS result for manual review.

`SKILL.md` is the authoritative runbook for the orchestrator;
`agents/batch-runner.md` is the per-batch worker runbook.
