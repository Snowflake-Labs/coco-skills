# SCOS Runner

Owns Phase B: run the rendered ScalaTest specs against real Snowpark
Connect / SCOS, compare against local baselines when they exist, and
drive the final fix loop.

**Prior learnings:** Before your first step, read
`$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md` into your context
and apply any relevant patch / schema / JVM patterns.

## Inputs

- `CONVERSION_ROOT`
- `SKILL_DIRECTORY`
- `PRIMARY_CONV_ROOT` (for batch-learnings)
- `VALIDATOR_SCRIPTS` — `$SKILL_DIRECTORY/../validate-pyspark-to-snowpark-connect/scripts`

Derived paths:

- `VALIDATION_ROOT = <CONVERSION_ROOT>/Validation`
- `TESTS_DIR       = Validation/tests`
- `RESULTS_DIR     = Validation/results/phase_b`
- `SCHEMAS_DIR     = Validation/shared/schemas` (SoT)
- `ANALYSIS_JSON   = Validation/shared/analysis.json` (generated shim — do not hand-edit)
- `STATE_JSON      = Validation/state.json`
- `MIGRATED_DIR    = <CONVERSION_ROOT>/Output`

```bash
RUN="uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py"
```

## Critical Rules

1. Phase B must use real `SnowparkConnectSession.builder().getOrCreate()`
   (from `com.snowflake.snowpark_connect.client.SnowparkConnectSession`).
   Do not shim or fake the SCOS session.
2. Do not skip an entrypoint just because Phase A failed to produce a
   baseline.
3. If a baseline exists, compare against it via `comparator.py compare` looped over the trial's tables (the
   pure-Python comparator; see the compare step below).
4. If no baseline exists, still run SCOS, snapshot the result, and flag
   the trial for manual review (`passed_no_baseline`).
5. Reuse the copied shared kit. Fix the copied kit under
   `Validation/tests/` if something is wrong with the harness.
6. **HARD GATE**: A trial may NOT be marked `hard_stuck` until **one of the
   following is on record**:
   - A `record-fixer-dispatch` (for code/dialect errors — fixer dispatch is mandatory
     even when you believe it is unfixable; use `outcome=no_change`), **OR**
   - `--analysis-repair-exhausted` after ≥2 `analysis_repair` iters (for schema/data
     gaps: `TABLE_OR_VIEW_NOT_FOUND`, `COLUMN_NOT_FOUND`, empty sinks), **OR**
   - `--harness-repair-exhausted` after ≥1 `harness_failure` iter (for kit/spec defects
     that cannot be resolved by editing the copied kit under `Validation/tests/`), **OR**
   - `--patch-repair-exhausted` after ≥1 `patch_failure` iter (for I/O that cannot be
     patched via `patch-add` after a genuine attempt).
   The `--reason` flag is **always required** regardless of which track. The gate rejects
   hard_stuck with only `record-fixer-dispatch` but no reason.
7. **HARD GATE — PASSED STATUS**: Do NOT call
   `record-trial-status --status passed` until the per-table `comparator.py compare`
   loop has been run and exited 0 (or the no-baseline path applies). The
   row-count guard in the ScalaTest spec is a fast pre-check only and is
   NOT sufficient to declare a trial passed. The code gate also enforces this:
   `record-trial-status` rejects `passed` unless the latest Phase B iter shows
   `passing>=1, failing==0`.
8. **Mock/schema gaps → repair, never skip.** `TABLE_OR_VIEW_NOT_FOUND`,
   `COLUMN_NOT_FOUND`, empty sinks, and datagen verify failures are inline
   schema repairs (`analysis_repair`). Only genuine dialect/environment gaps
   that local OSS Spark cannot execute may remain as `phase_a_skipped` with a
   named construct in `--reason` (e.g. `QUALIFY`, `VariantType`) — Phase B still
   runs and may derive `passed_no_baseline`.

## Fixable error classes (must dispatch migration-fixer before hard_stuck)

These error classes are explicitly fixable by the Scala migration-fixer
(`migrate-spark-scala-to-snowpark-connect`). You MUST dispatch the fixer
(record at least one `record-fixer-dispatch`) before marking any trial
with these classes as `hard_stuck`.

| Error class signal | Fix recipe |
|---|---|
| `S3 access denied` / stage access denied from external `s3://` URI | Rewrite the read to the staged equivalent using `path_redirects` from `schemas/entrypoints/<id>/_meta.json` |
| `AnalysisException: Object does not exist` for a table reference | Inspect migrated code, reconcile FQN with golden-schema namespace |
| `WIDGET_*` name resolution failures (SMA-converted Databricks notebook) | Inject `SCOS_WIDGET_<name>` env var in rendered test via `WIDGET_ENV_VARS` |
| `ClassNotFoundException` for workload class | Re-run `sbt assembly` in `Output/`; verify `jar_path` in `schemas/scala_meta.json` |
| `NoSuchMethodException` for entrypoint | Verify method signature with `javap -p <ClassName>`; update `entrypoint_method` |
| `KryoException` / `NotSerializableException` / UDF `ClassNotFoundException` | Prefer documenting with `document-divergence --scope udf` (or rely on `known-patches suggest` seeding `expected_divergences`). Soft-pass when declared. Only dispatch migration-fixer when a real SCOS-compatible rewrite is required — see `$SKILL_DIRECTORY/../references/scala/udf-dependencies.md` |
| Glob patterns (`/*.json`, `/*.parquet`) on Snowflake stages | Strip glob suffix; SCOS native readers accept directory paths |
| `SnowflakeSQLException` / `TABLE_OR_VIEW_NOT_FOUND` for a table produced by an unselected entrypoint | Use `mark-unselected-dependency --trial-id <id> --reason "<upstream ep>"` instead of fixer dispatch (it derives `passed_no_baseline`). Do NOT `record-trial-status --status passed_no_baseline` directly — the gate rejects a direct set |
| `SparkConnectGrpcException` (Scala variant) / gRPC transport errors *after* a session was established | Check the local Python server: ensure `SNOWPARK_CONNECT_PYTHON_VENV` + `SNOWFLAKE_DEFAULT_CONNECTION_NAME` are set and the connection is a valid non-interactive connection (PAT, key-pair, password, or cached OAuth — not `externalbrowser`); ensure `SPARK_REMOTE` is **not** set (it bypasses the local server) |
| **`COLUMN_NOT_FOUND: X`** — the error names exactly one missing column | `column_refs` in `ast_facts.json` is a partial, non-authoritative hint from the static AST parser. If X appears there, it corroborates the column; if absent, that proves nothing. Add X to the table `schemas/entrypoints/<id>/tables/<KEY>.json` the error localizes to regardless. Never seed the whole `column_refs` list. Regenerate and re-run. |
| **Type mismatch** (`DATATYPE_MISMATCH` / SCOS `3002`) — the error names **no column** | Inline schema repair: open the failing line, inspect the declared types of the columns on both sides of the comparison or join, and fix the mismatched column's `type` in `schemas/entrypoints/<id>/tables/<KEY>.json` (or add a genuine cast in `Output/` if the source code is at fault) |
| **Ambiguous column** (`AMBIGUOUS_REFERENCE` / SCOS error `5004` `could be: [X, X]`) | **Usually MOCK over-seeding — inline schema repair FIRST** (see routing note below); only dispatch migration-fixer when mock repair doesn't resolve it or the duplication is genuine in the real schema. SCOS-specific column-id tracking: post-join `.drop()`/rename may not reach the colliding id — fix at the join: select only join keys (and genuinely-needed columns) from the **right** leg, or alias both legs (`left.as("l").join(right.as("r"), …)`) and qualify references. |
| `.write.parquet(path)` / `.write.save(path)` to a local or stage path on SCOS (`SparkConnectGrpcException` on write) | Snowpark Connect cannot write Parquet to a path — rewrite to `.write.mode(...).saveAsTable("<table>")`. Systematic SMA artifact, not per-entrypoint. |
| `TIMESTAMP_LTZ` Parquet/stage unload failure (`5001` on writing a frame with `functions.current_timestamp()` / a `TIMESTAMP_LTZ` column) | Convert the timestamp to a string before write: `date_format(current_timestamp(), "yyyy-MM-dd HH:mm:ss.SSSSSS")` — avoids the LTZ unload path. |
| `ABS()` wrapped around a DATE/DATEDIFF result during `saveAsTable` CTAS (Snowpark Connect codegen quirk) | Cast the date-diff to int **before** `abs`/aggregation: `abs(datediff(...).cast("int"))`, so codegen does not emit `ABS(<date>)`. |

> **Ambiguous column / 5004 (`AMBIGUOUS_REFERENCE could be: [X, X]`) after a join is usually a MOCK over-seeding problem, not a code defect.** Both legs of the join seed a column with the same name; the comparator reports the ambiguity. Fix with schema repair — remove the mis-attributed column from the offending `schemas/entrypoints/<id>/tables/<KEY>.json` via the data-synthesizer and re-run datagen — and do NOT dispatch the migration-fixer unless the column duplication is genuine in the real source schema. The one ambiguous-column case that IS a real code fix (route to the migration-fixer): a SQL `SELECT` alias that shadows a `GROUP BY`/base column (`AS k … GROUP BY k` → rename the alias).

> **Transient startup errors are NOT fixer-dispatch cases.** A gRPC error
> `4001`, a generic transport error on the *first* trial, or any failure that
> occurs **before a SCOS session was ever successfully established** is a
> cold-start / transient-startup condition, not a workload defect. Do NOT
> dispatch the migration-fixer for these. Instead retry the trial once with a
> long timeout (≈15 min) so a resuming warehouse / cold SCOS endpoint has time
> to come up:
>
> ```
> transient_startup_errors = [
>   "4001",                       # gRPC startup / channel-not-ready
>   "UNAVAILABLE", "DEADLINE_EXCEEDED",
>   "failed to connect", "connection refused",
> ]
> ```
>
> Retry policy: if the error class is in `transient_startup_errors` AND no SCOS
> session has been established for this trial yet → **kill stale local SCOS
> Python servers first** (`pkill -f snowpark_connect` / equivalent), then
> re-run the spec once with `SCOS_TRIAL_TIMEOUT_SECS=900`.
> `run-phase-b` does this automatically on transient log signatures.
> Only escalate to the normal taxonomy if the retry also fails *after* a
> session was established.
>
> **Timeouts are symptoms.** Fix hang root causes (unpatched sinks, missing
> mocks, stuck gRPC from a prior hung trial) before raising
> `SCOS_TRIAL_TIMEOUT_SECS` further. Do not treat a longer timeout as the
> primary fix.

The fixer dispatch is mandatory even when you believe the error is
unfixable — record `outcome=no_change` to acknowledge attempting it.

### When to mark `passed` vs `hard_stuck` after fixer dispatches

- If `tables_captured >= 1` AND remaining diffs are documented as
  `cosmetic_divergence` → mark `passed`.
- If the fixer returned `partial` or `success` on at least one dispatch
  AND the workload produces real outputs → prefer `passed` (with
  `document-divergence`) over `hard_stuck`.
- Mark `hard_stuck` ONLY when: `tables_captured == 0` after the final
  dispatch, AND the last fixer outcome was `no_change`, AND the error
  class is outside the fixer's scope.
- **NEVER mark `hard_stuck` if a SCOS session was never successfully
  established for the trial.** A trial that never got past session
  startup has not been validated — it is a transient-startup condition
  (see the `transient_startup_errors` retry above). Retry it; if it still
  cannot establish a session, that is an environment/connection problem
  for human review, not a `hard_stuck` workload verdict.

### A clean SCOS run (exit 0) is NEVER `hard_stuck`

If the workload executed end-to-end but the sink is empty or the clone schema
has no output, that is a **data or write-wiring gap**, not a code failure —
repair it inline (do not mark `hard_stuck` with a reason like "runs end-to-end,
code is correct, only mock data / coverage"; that is the lazy-out the gate
rejects).

- **SCOS zero-row sink capture** — a declared sink that produced/captured 0 rows
  is reported by the harness as a critical failure. Default action: fix schema/data
  coverage so the sink becomes non-empty. Set `"allow_empty": true` on the sink
  table under `schemas/entrypoints/<id>/tables/<KEY>.json` only when the empty
  result is genuinely intentional for this fixture.

Two additional common shapes:

- **Empty output because date-range `WHERE` filters keep no rows.** `datagen.py`
  anchors date/timestamp columns near *today*, so relative windows
  (`current_date - N`, MTD, last-N-months) should match. If a sink is still
  empty, widen that column's mock `values` (or add a `joins` edge) so rows
  survive, then re-run `schema_mine.py` + `datagen.py schemas/ mock_data` + provision + re-run.
  For **timestamp/date** columns do NOT set `values` (the verifier can't enum-check
  temporal `values`) — widen the `entrypoint_kwargs` date bounds instead.
- **`saveAsTable` wrote to the wrong schema / clone is empty (SCOS `USE SCHEMA`
  silently no-ops).** The write must land in the trial's schema: redirect it
  through the harness sink (`patch-add` a `SCOS_SINK_*` write) or, in the
  TEST-PATCH, qualify the write to the trial schema. This is harness wiring — do
  **not** put a `SCOS_*` value into a `[MIGRATION-FIX]` commit (the committer
  rejects it).

These are repaired as **inline schema/data repair rounds**, each recorded with
`record-iter --fix-category analysis_repair`. A schema/data gap
(`TABLE_OR_VIEW_NOT_FOUND` / `COLUMN_NOT_FOUND`) is fixed by editing
`schemas/` + `datagen.py schemas/ mock_data` + provision — never by the
migration-fixer. A harness failure reporting a declared sink produced/captured
0 rows is also a schema/data gap: fix mock coverage so rows reach the sink;
only in the rare case where the sink is genuinely intentionally empty should
you set `"allow_empty": true` on that table in `schemas/`. Only after **at least two**
such rounds, if it still cannot be made to produce output, may you mark
`hard_stuck` with `--analysis-repair-exhausted --reason "<final-iter error>"`
(the gate rejects fewer than two rounds).

If a sink is **legitimately always empty** and the Phase A baseline is also empty,
declare it so the comparator does not flag the empty output as a divergence:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  mark-empty-baseline --conv-root $CONVERSION_ROOT --trial-id <id> --sink-id <sink>
```

### `passed_no_baseline` is DERIVED — never set it directly

`record-trial-status` **rejects a direct `passed_no_baseline`**. It is reached
only two ways:

1. **Derived promotion** — a trial that Phase A marked `phase_a_skipped`
   (with a required `--reason`) and that then passes Phase B is auto-promoted to
   `passed_no_baseline`, **preserving the skip reason** (`phase_a_skip_reason`) so
   the report explains the missing baseline. You do nothing extra — `summary`
   does the promotion.
2. **`--baseline-not-comparable --reason`** — the rare case where Phase A DID run
   but captured different sinks than Phase B (not comparable). This requires a
   `--reason`.

If Phase A produced a comparable baseline (a phase_a iter with `passing>=1,
failing==0`) you MUST compare and mark `passed` (record cosmetic date-boundary
diffs via `document-divergence` — the trial still passes) or `hard_stuck` a real
divergence. Escaping a failed comparison to `passed_no_baseline` is exactly the
false verdict the gate blocks. For an unselected upstream dependency, use
`mark-unselected-dependency` (it derives `passed_no_baseline` for you).

### Committing migration fixes

When a migration-fixer dispatch edits `Output/` and the trial then makes
progress, commit those edits so harvest can deliver them. Make **one**
`[MIGRATION-FIX]` commit per round (not per file), naming the trial(s) it fixes:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  commit --conv-root $CONVERSION_ROOT --kind migration-fix \
         --trial-ids "<trial id(s)>" --message "<what + why>"
```

`[MIGRATION-FIX]` commits are cherry-picked onto the deliverable at harvest, so
they must be production-safe: **never** commit `SCOS_*` env rebinds, harness
schema/database tokens, or other validation plumbing as a migration-fix — the
committer rejects it (exit 2). Namespace/catalog rebinds and other harness
wiring are `[TEST-PATCH]` patches authored via `patch-add` (they stay on the
validation branch and are not delivered).

Additional migration-fix rules:
- When the divergence is a known class (e.g. Snowflake-uppercase `df.columns`
  membership per Rule 29 in the fix-rules), fix **every** occurrence of that
  pattern in the file in one pass — not just the failing line.

## Error classification policy

1. **Compilation failure in rendered test** (ScalaTest collection fails,
   syntax error in `Test*Spec.scala`) → `harness_failure`. Fix the
   rendered spec or the kit template; do not dispatch migration-fixer.
2. **SCOS rejects the request** (`AnalysisException`, `ParseException`
   before execution) → `workload_failure`; dispatch the fixer.
3. **SCOS ran but produced wrong values** (ScalaTest assertion fails,
   divergence vs Phase A baseline) → `assertion_failure`; dispatch
   fixer if the divergence is fixable (datetime format, type widening);
   otherwise document with `document-divergence`.
4. **Missing data** (`TABLE_OR_VIEW_NOT_FOUND`, `COLUMN_NOT_FOUND` for
   provisioned objects) → treat as `harness_failure` if the object was
   declared in `external_sources`; use `mark-unselected-dependency --trial-id
   <id> --reason "<upstream ep>"` (which DERIVES `passed_no_baseline`) if it is
   the output of an unselected entrypoint. Do NOT `record-trial-status --status
   passed_no_baseline` directly — the gate rejects it.
5. **Missing class / unsupported non-Spark call** (`ClassNotFoundException`,
   `NoSuchMethodException`, `UnsatisfiedLinkError`) → an un-patched I/O
   dependency, not a missing shim. Add a `scos_state.py patch-add` patch that
   rewrites the offending call to native Spark / env reads (see
   `patch-author.md`), then re-run. There are no shims.

Do NOT dispatch the migration-fixer for `unselected_dependency` errors.

## Prerequisites (Phase B)

Before running Phase B, verify both JARs are present AND the Snowflake connection
is configured for non-interactive forked-JVM authentication.

### 0. Connection setup (**do this first**)

`SnowparkConnectSession.builder().getOrCreate()` runs in **local-server mode**: the JVM
client launches a local Python SCOS server from `SNOWPARK_CONNECT_PYTHON_VENV`, and **that
Python server** resolves the Snowflake connection (exactly like `cortex` / the Python
connector). The JVM client does **not** read `connections.toml` itself, and you should
**not** set `SPARK_REMOTE` — setting it forces remote mode and bypasses the local server.

> **"Local" here means only the Spark→Snowpark translation server, NOT the compute.**
> All DataFrame work in Phase B executes **in Snowflake** (the connection's warehouse,
> against the cloned golden schema) — it is not local or emulated execution. This is the
> same model as PySpark's `init_spark_session()`, where the server just happens to run
> in-process. So "local-server mode" is a deployment/process detail, not a statement that
> Phase B runs on this machine.

So Phase B needs exactly two things, both of which `scos_state.py run-phase-b` sets for you:

- `SNOWPARK_CONNECT_PYTHON_VENV` → the skill's `.venv` (so the JVM can launch the server), and
- `SNOWFLAKE_DEFAULT_CONNECTION_NAME` → the run's connection (so the Python server picks it).

If you drive `sbt test` by hand instead of `run-phase-b`, export both as real OS env vars
(the Python server is a child process — it reads the OS environment, not JVM properties):

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
export SNOWPARK_CONNECT_PYTHON_VENV="$SNOWPARK_DIR/.venv"
export SNOWFLAKE_DEFAULT_CONNECTION_NAME="<the connection name in state.json config.connectionName>"
# do NOT export SPARK_REMOTE
```

If the session falls back to `sc://localhost:15002` and times out, the local server did not
start — almost always `SNOWPARK_CONNECT_PYTHON_VENV` is unset/wrong.

### Connection setup (use your existing connection)

Point the run at your existing Snowflake connection (`config.connectionName` in
`~/.snowflake/connections.toml`) — the same one you use for `cortex` / the Python
connector. The Python SCOS server reads it via `SNOWFLAKE_DEFAULT_CONNECTION_NAME` and
activates its `warehouse`/`database`/`role`, exactly like PySpark's `init_spark_session`.
No `SPARK_REMOTE` or explicit token env var is needed.

The one requirement: the connection must be **non-interactive**, because `sbt test`
forks headless JVMs (`Test / fork = true`) with no TTY. Password, key-pair, PAT, or a
cached OAuth token all work; `authenticator = "externalbrowser"` (SSO) does **not** —
the browser flow has no TTY and hangs. The connection must also define a `warehouse`
(the kit issues no `USE WAREHOUSE`; the session inherits the connection's).

Both consumers read this one connection: the Python SCOS server (Phase B compute) and
the JVM JDBC path (per-trial golden-schema clone). Key-pair is read from
`connections.toml` (`private_key_file` / `private_key_path`); the
`SNOWFLAKE_PRIVATE_KEY_FILE` env var overrides it if set.

If you only have an SSO login, add a non-interactive method (e.g. a PAT):

```sql
ALTER USER <your_user> ADD PROGRAMMATIC ACCESS TOKEN scos_validation_pat;
-- then set authenticator = "programmatic_access_token" + token = "<value>" in connections.toml
```

1. **SCOS client JAR** — `SnowparkConnectSession` loads from `tests/lib/`.
   **`scos_state.py run-phase-b` stages it automatically** — its `_stage_scos_client_jar`
   setup step copies `snowpark-connect-java-client*.jar` from `Output/lib/`, `~/.m2`, or
   the Coursier cache (wherever the migrate build left it). You do **not** stage the jar by
   hand.

   If `run-phase-b` warns that the jar could not be found, the migrate build didn't produce
   it — re-run `sbt assembly` in `Output/` (or otherwise ensure `Output/lib/` contains
   `snowpark-connect-java-client*.jar`) and re-run `run-phase-b`.

2. **Migrated workload JAR** — must exist at the `jar_path` recorded in
   `schemas/scala_meta.json` (also mirrored on the analysis shim).
   **`scos_state.py run-phase-b` checks this automatically** and warns if absent. If it
   does warn, run `sbt assembly` in `Output/` and re-run `run-phase-b`.

## Pre-flight gate (run before any Phase B trial)

Run the single-pass prevalidate command — it covers the SCOS venv check (former
PF-1), I/O completeness, dep_check, and unsupported-construct checks in one pass:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  prevalidate --phase b --conv-root $CONVERSION_ROOT
```

Exit 0 is required before issuing `run-phase-b` or `sbt test`. If prevalidate
exits 1 (blocking findings), fix each finding and re-run with `--force` to bypass
the cache:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  prevalidate --phase b --conv-root $CONVERSION_ROOT --force
```

Common blocking findings and fixes:

| Finding check | Fix |
|---|---|
| `scos_venv` | `uv pip install --python $SNOWPARK_CONNECT_PYTHON_VENV/bin/python snowpark-connect` |
| `io_completeness` | Add an I/O patch via `scos_state.py patch-add` for cloud URI / JDBC / excel / mongo / literal file writes |
| `sink_strategy` | Remap excel/mongo sinks to `saveAsTable` / `SCOS_SINK_*`. Use `allow_empty` **only** when empty output is intentional — never for UDF/connector gaps (use `expected_divergences` with `scope=udf` instead) |
| `cli_args` | Fill incomplete `cli_args` / `entrypoint_kwargs` stubs (no JAR rebuild needed) |
| `intermediate_tables` | Declare typed `intermediate_tables[]`, re-run `schema_mine` so CREATE/seed works |
| `unsupported_construct` | Dispatch the migration-fixer for RDD ops / streaming (UDFs are warnings) |
| `entry_class` | Fix `schemas/entrypoints/<id>/_meta.json` `entrypoint_class` to match the compiled class name (include `$` for companion objects) |
| `dep_check` | Set `SCOS_KIT_SPARK_VERSION` / `SCOS_KIT_DELTA_VERSION` to match `Output/build.sbt` |

A failed venv (`scos_venv` blocking finding) will produce a
`transient_startup_error` for every trial. Fix it before running Phase B — none of
those trials are validated and none should be marked `hard_stuck`.

Prefer the deterministic runner over hand-driving `sbt test`:

## Phase B loop (PySpark parity)

1. Gate Phase B once:

   ```bash
   $RUN prevalidate --phase b --conv-root $CONVERSION_ROOT
   ```

2. Run all pending Phase B trials together:

   ```bash
   $RUN run-phase-b --conv-root $CONVERSION_ROOT
   ```

3. For each failed trial, choose the right fix path:
   - **data/schema repair**
   - **patch/plumbing repair**
   - **harness repair**
   - **code/dialect fix**

4. If SCOS produced full output but differs from Phase A, decide whether the diff
   is acceptable or material:
   - acceptable / cosmetic → `document-divergence`, then pass
   - materially wrong / missing → re-enter the diagnosis loop
   - **non-deterministic tie-break** — a "keep one row per group" step
     (`row_number().over(partitionBy(K).orderBy(O)).where($"rank" === 1)`,
     `dropDuplicates`, `distinct`) selected a different row on SCOS vs Spark
     because the `orderBy` column `O` has ties → set `"unique": true` on `O`
     in that table's schema JSON, regenerate mocks, and re-run. Do NOT document
     this as an acceptable divergence. **Caveat:** if `O` is also a join key
     (it appears in the entrypoint's `joins`/`range_join_edges`), datagen
     ignores `"unique"` on it to preserve join overlap — instead pick a non-join
     tiebreaker column to make unique.

5. Re-run Phase B and repeat until every trial reaches a terminal status.

6. Finish with one regression sweep:

   ```bash
   $RUN run-phase-b --conv-root $CONVERSION_ROOT --verify-all
   ```

**Always use `run-phase-b`, not raw `sbt test`.** It records iters and auto-promotes
clean Phase B trials to `passed` / `passed_no_baseline`.

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  run-phase-b --conv-root $CONVERSION_ROOT
```

After the last iteration's fixes, run once with `--verify-all` to catch
regressions across all trials (including those already marked passed):

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  run-phase-b --conv-root $CONVERSION_ROOT --verify-all
```

## Run model

The `ScosTrialFixture` already knows how to:

- clone a golden Snowflake schema per trial (via JDBC),
- boot a real `SnowparkConnectSession`,
- prefer stage-backed reads for provisioned file inputs,
- snapshot final state to Parquet.

Run the tests with **one batched pass over all selected specs** — this is
the default and the single biggest wall-time + token win (it collapses N JVM
cold-starts and N read→compare→record loops into one):

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
# Run the batched test suite with all required env vars. The SCOS session uses
# local-server mode: SNOWPARK_CONNECT_PYTHON_VENV lets the JVM launch the local Python
# server, and SNOWFLAKE_DEFAULT_CONNECTION_NAME tells that server which connection to use.
# Do NOT set SPARK_REMOTE (it forces remote mode and bypasses the local server).
SCOS_FLAVOR=migrated \
SCOS_TEST_PARALLELISM=4 \
SCOS_RESULTS_DIR=$RESULTS_DIR \
SCOS_CONV_ROOT=$CONVERSION_ROOT \
SCOS_SCHEMAS_DIR=$CONVERSION_ROOT/Validation/shared/schemas \
SCOS_ANALYSIS_JSON=$CONVERSION_ROOT/Validation/shared/analysis.json \
SCOS_STATE_JSON=$CONVERSION_ROOT/Validation/state.json \
SCOS_MOCK_DATA_DIR=$CONVERSION_ROOT/Validation/shared/mock_data \
SNOWPARK_CONNECT_PYTHON_VENV=$SNOWPARK_DIR/.venv \
SNOWFLAKE_DEFAULT_CONNECTION_NAME=<config.connectionName> \
sbt test 2>&1 | tee $RESULTS_DIR/sbt_migrated.log
```

> **`SNOWPARK_CONNECT_PYTHON_VENV`**: The SCOS client (`SnowparkConnectSession.builder().getOrCreate()`)
> starts a local Python server using the `snowflake.snowpark_connect` package. Without this env var,
> it looks for `python3` on PATH and fails with `snowpark-connect package not found`. Point it at
> the uv venv where the package is installed.

> **`SNOWFLAKE_DEFAULT_CONNECTION_NAME`**: The local Python server resolves the Snowflake
> connection from this (the JVM client itself does not read `connections.toml`). It must name
> a non-interactive (PAT) connection — see Prerequisites §0. Do NOT set `SPARK_REMOTE`:
> in local-server mode it overrides the client's internal routing and causes gRPC
> channel-closed errors.

Only when isolating a single failing spec for debugging, narrow with
`testOnly` (do NOT make per-trial `testOnly` the normal dispatch path):

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
SCOS_FLAVOR=migrated \
SCOS_RESULTS_DIR=$RESULTS_DIR \
SCOS_CONV_ROOT=$CONVERSION_ROOT \
SCOS_SCHEMAS_DIR=$CONVERSION_ROOT/Validation/shared/schemas \
SCOS_ANALYSIS_JSON=$CONVERSION_ROOT/Validation/shared/analysis.json \
SCOS_STATE_JSON=$CONVERSION_ROOT/Validation/state.json \
SCOS_MOCK_DATA_DIR=$CONVERSION_ROOT/Validation/shared/mock_data \
sbt "testOnly *Test<EpId>Spec" 2>&1 | tee $RESULTS_DIR/sbt_migrated.log
```

### Warm the warehouse once before dispatching trials

Most of the per-trial wall time is Snowflake **warehouse resume** (account-side
cold start), not SCOS client init. Before the batched `sbt test`, issue one
cheap no-op so the warehouse is hot when the forked trial JVMs connect:

```bash
cortex --connection "$SCOS_CONNECTION" sql -q "SELECT 1" >/dev/null 2>&1 || true
```

Do NOT try to share a warm session across trials via a suite-level `beforeAll`
`lazy val`: `build.sbt` forks a JVM **per spec**, so an in-process `lazy val`
cannot cross processes. A shared in-process session would require disabling the
per-suite fork (which breaks EnvUtil's `System.setProperty` isolation) or an
external long-lived SCOS server. The warehouse warm-up above is the real win.

Phase B trials run in **bounded parallel** — each entrypoint spec runs in its own
forked JVM (per-suite fork), and the golden schema is cloned to a uniquely-named
`<GOLDEN>_T<8hex>` per trial, so concurrent trials never collide. Concurrency is
capped by `SCOS_TEST_PARALLELISM`; when `--parallelism` is omitted, `run-phase-b`
auto-caps from host RAM (`<8 GB` → 1, `<16 GB` → 2, else → 4). Explicit
`--parallelism N` always wins. **Lower further** if the Snowflake connection
rate-limits, the warehouse is small, or you see clone/session pressure —
concurrent SCOS sessions multiply load on the endpoint and warehouse.

Compare Phase B output to Phase A baseline. This uses the pure-Python
comparator (no Spark/JVM cold start per trial) — the canonical single-sink
`comparator.py compare`, looped over the trial's captured tables. Each table is
a Spark output **directory** (`tables/<name>.parquet/`); the comparator reads it
natively. Enumerate the tables from the baseline's `_index.json`, skip any
documented-divergence columns from `schemas/manifest.json`
`expected_divergences` (also mirrored on the analysis shim), and aggregate
per-table exit codes into the trial verdict:

```bash
PA=$CONVERSION_ROOT/Validation/results/phase_a/<id>
PB=$CONVERSION_ROOT/Validation/results/phase_b/<id>
# Prefer schemas SoT; shim is a generated fallback with the same keys.
DIV_SRC=$CONVERSION_ROOT/Validation/shared/schemas/manifest.json
[ -f "$DIV_SRC" ] || DIV_SRC=$CONVERSION_ROOT/Validation/shared/analysis.json
# natural_keys live on the generated analysis shim sink catalog
ANALYSIS=$CONVERSION_ROOT/Validation/shared/analysis.json
mkdir -p "$PB/diffs"; trial_rc=0
for t in $(jq -r '.tables[].name' "$PA/_index.json"); do
  # documented divergences for this <id>.<sink> (scope data/both) -> ignore cols
  ign=$(jq -r --arg k "<id>.$t" --arg s "$t" \
    '((.expected_divergences[$k] // []) + (.expected_divergences[$s] // []))
     | map(select((.scope // "data")|ascii_downcase|.=="data" or .=="both"))
     | map(.column // .col) | map(ascii_upcase) | join(",")' "$DIV_SRC" 2>/dev/null)
  # natural_keys for this <id>.<sink>: drives keyed row-matching instead of
  # full-row lexicographic sort. Keyed comparison is stable when any non-key
  # cell differs between runs; full-row sort cascades one divergent cell into
  # many false row-position mismatches. Empty when no keys declared -> safe fallback.
  # NOTE: ep.sinks[] holds string IDs, not inline objects — resolve each ID
  # against the top-level .sinks[] catalog (which carries name + natural_keys).
  keys=$(jq -r --arg ep "<id>" --arg t "$t" \
    '. as $root
     | $root.entrypoints[]? | select(.id == $ep) | .sinks[]?
     | . as $sid
     | $root.sinks[]? | select(.id == $sid and .name == $t)
     | .natural_keys // [] | join(",")' "$ANALYSIS" 2>/dev/null)
  uv run --project $SKILL_DIRECTORY/.. python \
    $VALIDATOR_SCRIPTS/harness/comparator.py compare \
    --baseline "$PA/tables/$t.parquet" --shadow "$PB/tables/$t.parquet" \
    --output "$PB/diffs/$t.json" ${ign:+--ignore-columns "$ign"} \
    ${keys:+--key-columns "$keys"}
  rc=$?; [ "$rc" -gt "$trial_rc" ] && trial_rc=$rc
done
echo "trial compare exit: $trial_rc"
```

Exit codes (per table, and the aggregated `trial_rc`): `0` = match, `1` =
divergence, `2` = error. Treat `trial_rc != 0` as not-passed.

## Failure handling

Classify failures into:

- **harness failure** (`harness_failure`): reusable execution seam
  issue; fix the shared kit
- **unpatched I/O** (`workload_failure`): a missing class / non-Spark call;
  `patch-add` a rewrite to native Spark / env reads (no shims)
- **workload failure** (`workload_failure`): dispatch the migration
  fixer for `Output/`
- **assertion failure** (`assertion_failure`): real output mismatch

### Do not soft-green empty captures

- Phase B `passing=0, failing=0` (or sink `5001` / empty stage GET) is **not**
  `passed_no_baseline`. Stay `pending` or `hard_stuck` until sinks are
  remapped/captured or explicitly scoped out via `expected_divergences`.
- Do **not** set `allow_empty` on every sink to force a green run when UDFs or
  connectors fail — declare `scope=udf` divergences instead.
- `auto-recovered: phase_b_failure` means `hard_stuck`, not a soft pass.

## No-baseline path

If Phase A did not produce a baseline for an entrypoint (it was marked
`phase_a_skipped` with a reason):

1. still run the SCOS test
2. ensure it produces a snapshot
3. if the run is clean, record the Phase B iter (`record-iter`) and let `summary`
   **auto-promote** the trial to `passed_no_baseline` — do NOT
   `record-trial-status --status passed_no_baseline` yourself (the gate rejects a
   direct set). The preserved `phase_a_skip_reason` flows into the report.
4. leave the captured SCOS result ready for the final summary to route
   into manual review

## Record keeping

Call `record-iter` after each meaningful iteration:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-iter --conv-root $CONVERSION_ROOT --trial-id <id> \
  --phase phase_b --fix-category <category> \
  --notes "<short>"
```

`--fix-category` meaningful values: `harness_failure`, `patch_failure`,
`workload_failure`, `assertion_failure`, `unselected_dependency`, `schema_gap`,
`analysis_repair`. Use one of these — the gate interprets these specific values:

| Category | Gate-logic meaning |
|---|---|
| `schema_gap` / `analysis_repair` | Counted by `--analysis-repair-exhausted` (requires ≥2 rounds) |
| `harness_failure` | Counted by `--harness-repair-exhausted` (requires ≥1 round) |
| `patch_failure` | Counted by `--patch-repair-exhausted` (requires ≥1 round) |
| others | Stored verbatim; no gate-logic effect |

If you dispatch the migration fixer:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-fixer-dispatch --conv-root $CONVERSION_ROOT \
  --trial-id <id> --error-class <class> --outcome <no_change|partial|success>
```

`--error-class` meaningful values: `harness_failure`, `patch_failure`,
`workload_failure`, `assertion_failure`, `unselected_dependency`. Use one of these
— the value is stored verbatim but the gate only interprets the above. Map code or
dialect errors to `workload_failure`.

After applying any patch to `tests/`, `Output/`, or `shared/`:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-patch --conv-root $CONVERSION_ROOT --trial-id <id> \
  --phase phase_b --file <path-relative-to-conv-root> \
  --reason "<short>"
```

When a trial reaches a terminal state:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-trial-status --conv-root $CONVERSION_ROOT \
  --trial-id <id> --status <passed|hard_stuck>
```

`passed` requires the latest Phase B iter to be green (`passing>=1, failing==0`);
`hard_stuck` requires a fixer dispatch or a documented repair-exhaustion flag
(`--analysis-repair-exhausted` / `--harness-repair-exhausted` /
`--patch-repair-exhausted`) and a `--reason`. `passed_no_baseline` is **not**
set here — it is derived (see *`passed_no_baseline` is DERIVED* above).

## Report back

Summarize:

- matched entrypoints
- documented divergences
- `passed_no_baseline` entrypoints needing human review
- remaining hard-stuck items
- any shared-kit fixes made during Phase B
