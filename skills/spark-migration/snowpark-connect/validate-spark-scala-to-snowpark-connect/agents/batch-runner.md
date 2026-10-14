---
name: batch-runner
description: Per-batch validation worker for the Scala validator. Validates one batch of entrypoints end-to-end inside a git worktree the orchestrator has already prepared — analyze → patch-author → Phase A → Phase B → summary → harvest. Self-contained: returns only after the batch's [MIGRATION-FIX] commits are on the deliverable branch.
---

# Batch Runner (Worker) — Scala Validator

This agent validates one batch of entrypoints end-to-end inside a git worktree
the orchestrator has already prepared, then consolidates this batch's fixes back
to the primary deliverable branch before returning.

## Preconditions (the orchestrator has done these)

`scos_state.py prepare-batches` already created this worktree, ran `init`, and
scoped its `schemas/` (and analysis shim). So when you start:

- `$CONVERSION_ROOT/Validation/` is initialized — `state.json` exists and the
  worktree is on its `validation/<run-id>` branch.
- `$CONVERSION_ROOT/Validation/shared/schemas/` is **already scoped to
  exactly this batch's entrypoints**. The data-synthesizer neither mines nor selects.
- `$CONVERSION_ROOT/Validation/source/` holds the original Scala source for Phase A.
- The kit may already be pre-warmed (Step 3.5 / this agent's Step 1). Check
  `state.json["milestones"]["venv_prewarmed"]` and run `prewarm` if false —
  before analyze/adapt, never after.

**Prior learnings:** Before Step 1, read
`$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md` into your context.
It contains patch patterns, schema quirks, and JVM issues discovered by
workers that completed before you. Apply any relevant patterns rather than
rediscovering them.

## Inputs (handed off by the orchestrator)

- `$CONVERSION_ROOT` — path to this batch's git worktree (contains `Output/` and
  the prepared `Validation/`).
- `$PRIMARY_CONV_ROOT` — path to the **primary** conversion repo (not this
  worktree). Used only in the consolidate step.
- `$BASE_SHA` — git SHA all worktrees branched from. Used by `consolidate`.
- `$ORIGINAL_SOURCE` — path to the original (unmigrated) Scala source.
- `$CONNECTION_NAME` — Snowflake connection name.
- `$SKILL_DIRECTORY` — this skill's directory.

```bash
RUN="uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py"
VALIDATOR_SCRIPTS="$SKILL_DIRECTORY/../validate-pyspark-to-snowpark-connect/scripts"
```

## JVM concurrency note

Each batch runs Phase A/B with `SCOS_TEST_PARALLELISM` concurrent forked JVM
test processes. If the pool has `--pool-size 3` (Scala default), peak concurrent
JVMs ≈ `3 × parallelism`. When `--parallelism` is omitted, runners auto-cap from
available RAM (`<8 GB` → 1, `<16 GB` → 2, else → 4). Lower further only for
Snowflake rate-limits. The Coursier/Ivy cache at `~/.cache/coursier` and
`~/.ivy2` is shared across all worktrees so dependency downloads happen only once.

## Constraints

- **Compiled Scala source.** Phase A requires a workload JAR via the
  build-doctor ladder (`sbt assembly` preferred; thin jar + filtered dependency
  classpath is valid). The JAR is loaded by the kit's `ReflectionEntrypoint` via
  `URLClassLoader` (with `EXTRA_CLASSPATH` for thin jars).
- **Single Snowflake connection.** All entrypoints target tables in the same
  database via the same connection.
- **Notebooks.** Scala/Python notebooks are flattened by `patch-author` via
  `notebook_io.flatten_cells_to_script(target_language="scala")`.
- **No Python venvs.** The kit is an sbt project; `scos_state.py build-doctor`
  is the Scala analogue of PySpark `seed-venv`. Phase B uses the real SCOS Java
  client JAR in `Validation/tests/lib/`.

## Critical Rules

1. The workspace is already scoped to this batch. The data-synthesizer completes the
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
   `scos_state.py patch-add` (scalac parser gate; atomic + committed as
   `[TEST-PATCH]`). Any subagent may add patches on the fly.
6. Keep per-entrypoint runs isolated:
   - Phase A: fresh per-trial local warehouse dir
   - Phase B: clone a pre-provisioned golden Snowflake schema per trial
     (`<GOLDEN>_T<8hex>` — one clone per spec)
7. Run all batch entrypoints in a **single batched `sbt test`** pass (bounded by
   `SCOS_TEST_PARALLELISM`). Never loop `sbt testOnly` per trial individually.
   Only fall back to per-trial `testOnly` to isolate a specific compilation error.
8. **Mock/schema gaps → repair, never skip.** Dialect-only gaps →
   `phase_a_skipped --reason <construct>` and still run Phase B (derived to
   `passed_no_baseline` on success). **There is no "3-iter cap → skip."** After
   ~3 iterations, escalate the specific failure: schema/mock gaps → inline repair
   (`--analysis-repair-exhausted`); harness/kit defects → fix kit then
   `--harness-repair-exhausted`; un-patchable I/O → `patch-add` then
   `--patch-repair-exhausted`; code/dialect → fixer dispatch. The gate rejects a
   blank or boilerplate `phase_a_skipped` reason.
9. All test-only changes to `Output/` (blueprint I/O patches) are committed as
   `[TEST-PATCH]`; genuine SCOS logic fixes are committed as `[MIGRATION-FIX]`.
   After summary exits 0, **Step 8 dispatches `agents/harvester.md`**, which
   calls `scos_state.py consolidate` on the primary repo to cherry-pick this
   batch's `[MIGRATION-FIX]` commits onto the deliverable branch (serialized by
   git's index.lock; harvester retries on exit 6). Do **not** call `consolidate`
   yourself in Steps 6–7.

   **Harness-safety rule (hard):** A `[MIGRATION-FIX]` commit MUST NOT introduce
   any of the following into `Output/` files: `SCOS_INPUT_`, `SCOS_OUTPUT_`,
   `SCOS_CAPTURE_DIR`, `SCOS_SINK_`, `SCOS_MOCK_`, `SCOS_PINNED_TIMESTAMP`,
   or any other `SCOS_*` environment-variable intercept pattern.  These are
   test-harness identifiers — they break the production deliverable.  If a code
   change requires both a production-safe fix (e.g., a `spark.table` try-catch)
   **and** a harness-intercept (e.g., `SCOS_OUTPUT_SCHEMA` branch), split them
   into two commits: one `[MIGRATION-FIX]` containing only the production fix,
   one `[TEST-PATCH]` containing the harness intercept.  `scos_state.py
   consolidate` enforces this and will refuse to harvest if violated.
10. Keep `[TEST-PATCH]` edits within this batch's own entrypoint files to avoid
    consolidation cherry-pick conflicts with other batches.

## Phase A vs Phase B: environment differences

| Status | Phase | Terminal? | Meaning |
|--------|--------|-----------|---------|
| `phase_a_skipped` | A | No | No local baseline (requires `--reason`). Phase B still required. |
| `passed` | B | Yes | SCOS output matched Phase A baseline (requires green Phase B iter). |
| `passed_no_baseline` | B | Yes | **DERIVED** from `phase_a_skipped` + clean Phase B; never set directly. Manual review. |
| `hard_stuck` | B | Yes | No credible Phase B fix path (requires fixer dispatch or exhaustion flag + `--reason`). |

## Workflow

### Step 1 — Prewarm (before analyze / adapt)

Check `state.json["milestones"]["venv_prewarmed"]`. If false, run prewarm **now**
(background if analyze/patch-author can overlap, but **join before Phase A**).
Never defer prewarm until after patch-author.

```bash
$RUN prewarm --conv-root $CONVERSION_ROOT
```

Honest prewarm: exits non-zero and does **not** set `venv_prewarmed` if JDK
cannot be resolved/provisioned or `sbt` is missing. On success it stages the kit
(`rsync harness-scala/kit/ Validation/tests/`) and runs `sbt -batch Test/compile`
to warm Coursier + zinc incremental cache.

### Step 2 — Analyze (synthesize)

Dispatch **`agents/data-synthesizer.md`** once. Schemas are already mined and
scoped to this batch, so the data-synthesizer goes straight to completing them:
run `datagen.py`, take the first problem, fix one repair unit in
`Validation/shared/schemas/`, run datagen, and repeat until `datagen.py` prints
`"ok": true`. Then resolve or explicitly dismiss any remaining warnings. It does
NOT re-run the survey or re-select entrypoints.
(`prevalidate` / `column_check --conv-root` refresh the analysis shim from
`schemas/` automatically — do not hand-edit `analysis.json`.)

**Data-synthesizer exit gate:** `manifest.complete: true` alone is not enough. Do
not proceed to Step 3 until `datagen.py` exits 0 and prints `"ok": true`, and the
data-synthesizer has resolved or explicitly dismissed every warning from the final
run. Run all data-synthesizer steps inside the subagent only.

### Step 3 — Author patches and compile JAR

Dispatch **`agents/patch-author.md`** once. It:
- Creates wrapper `object.main()` for entrypoints that lack one
- Flattens Scala notebooks via `notebook_io`
- Compiles the migrated `Output/` workload to a JAR (`sbt assembly` preferred for
  Phase B; Phase A source builds use `build-doctor` / `run-phase-a` ladder)
- Applies I/O blueprint patches atomically via `scos_state.py patch-add`
- Commits all `[TEST-PATCH]` changes and records `patches_authored` + `workload_built`

### Step 3.5 — Pre-validate Phase A

Run the single-pass static gate before starting any trials:

```bash
$RUN prevalidate --phase a --conv-root $CONVERSION_ROOT
```

Exit 0 is required before Phase A. If prevalidate exits 1 (blocking findings),
batch-fix every blocking item from `prevalidation_report.json` in **one** pass
and re-run once with `--force`. Honor `rebuild_required` on findings: mock/schema/
analysis fixes do **not** need a JAR rebuild; compile / classpath / I/O code patches
do — rebuild **at most once** after the batch fix, then re-validate. Do not rebuild
per finding.

**Timeouts are symptoms.** If Phase A/B hangs, fix the I/O / mock / stale-SCOS
root cause first; rely on the built-in one-shot transient retry (kill stale
server + 900s) before raising `SCOS_TRIAL_TIMEOUT_SECS` further.

Common fixes:

- **`entry_class`**: update `schemas/entrypoints/<id>/_meta.json` `entrypoint_class`
  to the compiled class, then run `scos_state.py schemas-to-analysis`
  name exactly (include `$` for Scala companion objects / modules).
- **`dep_check` version mismatch**: set `SCOS_KIT_SPARK_VERSION` /
  `SCOS_KIT_DELTA_VERSION` / `SCOS_KIT_DELTA_ARTIFACT` env vars to match Output/.
- **`sbt_compile` errors**: fix the source file and rebuild (`sbt assembly`).
- **`mock_data` failures**: run `schema_mine.py` → `datagen.py` inline and
  repair schemas.

`prevalidate` writes a structured JSON report to
`Validation/shared/prevalidation_report.json`. It is hash-cached: a re-run with no
file changes exits 0 immediately — use `--force` after editing source files.

**`build-doctor` is now a fallback diagnostic tool.** Use it when prevalidate's
`sbt_compile` finding does not provide enough detail to pinpoint the error, or when
you need the JAR path/classpath for manual inspection. It is no longer a required
step in the main flow.

### Step 4 — Run Phase A

Prefer the deterministic runner:

```bash
$RUN run-phase-a --conv-root $CONVERSION_ROOT
```

(`--parallelism` omitted → host-aware auto-cap. Pass an explicit value only to
override.)

`run-phase-a` runs a **mock-data guard** first (schema_mine → datagen seed/verify)
and hard-fails if mocks are unseedable or datagen cannot import — so fix
`schemas/` tables rather than skipping Phase A. Never pass `--no-mock-guard`
on production validation runs.

Or dispatch **`agents/local-runner.md`** if you need interactive diagnosis of
JVM compilation or harness failures. Phase A stages the kit, runs build-doctor /
source-jar ladder, renders `Test<EpId>Spec.scala` per trial, then runs one batched
`sbt test`.

**No 3-iter auto-skip.** After ~3 Phase A iterations, escalate the specific
failure (schema repair → `--analysis-repair-exhausted`; harness fix →
`--harness-repair-exhausted`; patch fix → `--patch-repair-exhausted`; code/dialect
→ fixer dispatch). Only mark `phase_a_skipped --reason <specific construct>` when
the original source genuinely cannot execute on local OSS Spark. The gate rejects
a blank or boilerplate reason.

### Step 5 — Provision Snowflake

```bash
$RUN provision --conv-root $CONVERSION_ROOT
```

`run-phase-b` also auto-provisions when schemas are missing, so this step can be
skipped when Phase B auto-provision covers it.

### Step 5.5 — Phase reset + Pre-validate Phase B

Clear stale Phase A artefacts and gate Phase B on a clean static check:

```bash
$RUN phase-reset --to b --conv-root $CONVERSION_ROOT
$RUN prevalidate --phase b --conv-root $CONVERSION_ROOT
```

`phase-reset --to b` wipes compiled test-classes (`tests/target/`) and stale
rendered specs from `tests/src/test/scala/phase_{a,b}/` (plus any legacy flat
`tests/src/test/scala/*.scala`), then resets the `tests_authored` milestone so
`run-phase-b` re-renders clean specs into `phase_b/` only.

`prevalidate --phase b` adds to Phase A checks:
- **SCOS venv (PF-1)**: `SNOWPARK_CONNECT_PYTHON_VENV` can `import snowpark_connect`.
- **I/O completeness**: greps `Output/src` for unpatched cloud URI / JDBC / RDD
  patterns that block Phase B (mirrors patch-author Step 0 scans).
- **Unsupported constructs**: RDD ops / streaming / external I/O are
  Phase-B-blocking; UDFs are warnings (often fixable / re-registerable).

Exit 0 is required before `run-phase-b`. Fix every blocking finding and re-run
`prevalidate --phase b --force` to confirm before proceeding.

### Step 5.6 — Pre-register known SCOS behavioral divergences

Before the first `run-phase-b`, scan for known divergence categories and
document them upfront so Phase B terminates in one pass rather than burning
iterations auto-recovering.  This is the Scala equivalent of PySpark's
`scos-runner.md` "acceptable / cosmetic → document-divergence, then pass"
routing rule.

**Scala companion object UDFs** — detect `$.MODULE$` in UDF/mapPartitions
calls (see data-synthesizer.md "Scala companion object UDFs" section).  For
each affected trial/sink:
```bash
$RUN document-divergence --conv-root $CONVERSION_ROOT \
  --trial-id <ep_id> --sink-id <sink_id> --column __all__ --scope udf \
  --reason "Scala companion object UDF not available server-side (platform limitation)"
```

**Known SCOS behavioral divergences** — document these before Phase B if the
Phase A baseline contains output from the relevant operators:

| Divergence | Condition | `--scope` | Typical `--reason` |
|---|---|---|---|
| NULL ordering | Phase A output has nullable columns and `ORDER BY` | `data` | "Spark NULLs-last vs SCOS ordering" |
| Timestamp precision | Phase A timestamps differ from SCOS (µs vs ms) | `data` | "Spark µs vs SCOS ms timestamp precision" |
| Floating-point repr | `Double`/`Float` columns printed differently | `serialization` | "Spark vs SCOS float serialization" |
| Row count from empty-table guard | Workload has `if (df.count() > 0)` guarding write | `data` | "Empty-guard: Phase A mock differs from prod fixture" |

Document per column and sink:
```bash
$RUN document-divergence --conv-root $CONVERSION_ROOT \
  --trial-id <ep_id> --sink-id <sink_id> --column <col> --scope data \
  --reason "<reason>" \
  --baseline-sample "<sample from Phase A>" \
  --shadow-sample "<expected SCOS sample>"
```

Once `documented_divergences` is non-empty, `comparison_verdict()` returns
`cosmetic_divergence` (not `real_divergence`) and the trial passes on the first
clean Phase B iteration.



### Step 6 — Run Phase B on SCOS

Prefer the deterministic runner:

```bash
$RUN run-phase-b --conv-root $CONVERSION_ROOT
```

(`--parallelism` omitted → host-aware auto-cap.)

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

- Source build produces **no jar** after `build-doctor` (total compile failure /
  no build tool): stop and report the classified cause + log path. Thin jar +
  classpath success is **not** a stopping point — proceed to Phase A.
- Phase A cannot produce a trustworthy baseline after exhausting all repair tracks
  (schema repair + harness fix + patch fix + fixer dispatch for code issues):
  mark `phase_a_skipped --reason <specific construct>`, continue to Phase B.
  Note: "ran out of iterations" and boilerplate reasons are not valid — the gate
  rejects them. Mock/schema gaps are never a skip.
- Phase B cannot reach SCOS after transient retry: stop and report.
- After fixer attempts exhausted with no progress: mark `hard_stuck` (rare —
  terminal, but only when there is truly no credible next fix).
- `scos_state.py summary` blocks on non-terminal trials: resolve them, then
  re-run summary.
- Harvester returns an unresolvable conflict: surface the conflicting commit SHA
  and file(s) to the orchestrator. Do NOT remove worktrees or schemas.
- If re-dispatched on an existing worktree, read `state.json` milestones and
  resume from the first incomplete step. Do not re-run the data-synthesizer or
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
- `Validation/shared/schemas/` — scoped to this batch's entrypoints (SoT)
- `Validation/shared/analysis.json` — generated JVM shim (do not hand-edit)
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
