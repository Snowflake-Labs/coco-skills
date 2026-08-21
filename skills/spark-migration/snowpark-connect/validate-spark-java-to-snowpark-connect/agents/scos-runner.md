# SCOS Runner

Owns Phase B: run the rendered ScalaTest specs against real Snowpark
Connect / SCOS, compare against local baselines when they exist, and
drive the final fix loop.

## Inputs

- `CONVERSION_ROOT`
- `SKILL_DIRECTORY`

Derived paths:

- `VALIDATION_ROOT = <CONVERSION_ROOT>/Validation`
- `TESTS_DIR       = Validation/tests`
- `RESULTS_DIR     = Validation/results/phase_b`
- `ANALYSIS_JSON   = Validation/shared/analysis.json`
- `STATE_JSON      = Validation/state.json`
- `MIGRATED_DIR    = <CONVERSION_ROOT>/Output`

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
6. **HARD GATE**: A trial may NOT be marked `hard_stuck` until at least
   one `record-fixer-dispatch` exists for that trial. Use
   `outcome=no_change` to record an attempt that did not help.
7. **HARD GATE — PASSED STATUS**: Do NOT call
   `record-trial-status --status passed` until the per-table `comparator.py compare`
   loop has been run and exited 0 (or the no-baseline path applies). The
   row-count guard in the ScalaTest spec is a fast pre-check only and is
   NOT sufficient to declare a trial passed.

## Fixable error classes (must dispatch migration-fixer before hard_stuck)

These error classes are explicitly fixable by the Java migration-fixer
(`migrate-spark-java-to-snowpark-connect`). You MUST dispatch the fixer
(record at least one `record-fixer-dispatch`) before marking any trial
with these classes as `hard_stuck`.

| Error class signal | Fix recipe |
|---|---|
| `S3 access denied` / stage access denied from external `s3://` URI | Rewrite the read to the staged equivalent using `path_redirects` from `analysis.json` |
| `AnalysisException: Object does not exist` for a table reference | Inspect migrated code, reconcile FQN with golden-schema namespace |
| `WIDGET_*` name resolution failures (SMA-converted Databricks notebook) | Inject `SCOS_WIDGET_<name>` env var in rendered test via `WIDGET_ENV_VARS` |
| `ClassNotFoundException` for workload class | Re-run `mvn package` / `./gradlew shadowJar` in `Output/`; verify `jar_path` in `analysis.json` |
| `NoSuchMethodException` for entrypoint | Verify method signature with `javap -p <ClassName>`; update `entrypoint_method` |
| `NullPointerException` in `dbutils.*` or Databricks API | Un-patched Databricks dependency; `patch-add` rewrite to `System.getProperty` |
| `KryoException` / `NotSerializableException` in UDF closure | UDF serialization refactor required. Dispatch migration-fixer with guidance to read `references/java/udf-dependencies.md` and apply staged-JAR / lambda-serialization strategy |
| Glob patterns (`/*.json`, `/*.parquet`) on Snowflake stages | Strip glob suffix; SCOS native readers accept directory paths |
| `SnowflakeSQLException` / `TABLE_OR_VIEW_NOT_FOUND` for a table produced by an unselected entrypoint | Use `record-trial-status --status passed_no_baseline` instead of fixer dispatch |
| `SparkConnectGrpcException` / gRPC transport errors *after* a session was established | Check the local Python server: ensure `SNOWPARK_CONNECT_PYTHON_VENV` + `SNOWFLAKE_DEFAULT_CONNECTION_NAME` are set and the connection is a valid non-interactive connection (PAT, key-pair, password, or cached OAuth — not `externalbrowser`); ensure `SPARK_REMOTE` is **not** set (it bypasses the local server) |
| **Ambiguous column** (`AMBIGUOUS_REFERENCE` / SCOS error `5004` `could be: [X, X]`) | **Usually MOCK over-seeding — inline schema repair FIRST** (see routing note below); only dispatch migration-fixer when mock repair doesn't resolve it or the duplication is genuine in the real schema. SCOS-specific column-id tracking: post-join `.drop()`/rename may not reach the colliding id — fix at the join: select only join keys (and genuinely-needed columns) from the **right** leg, or alias both legs and qualify references. |
| **`COLUMN_NOT_FOUND: X`** — the error names exactly one missing column | `column_refs` in `ast_facts.json` is a partial, non-authoritative hint from the static AST parser. If X appears there, it corroborates the column; if absent, that proves nothing. Add X to the table `schemas/<KEY>.json` (in `external_sources[]`) the error localizes to regardless. Never seed the whole `column_refs` list. Regenerate and re-run. |
| **Type mismatch** (`DATATYPE_MISMATCH` / SCOS `3002`) — the error names **no column** | Inline schema repair: open the failing line, inspect the declared types of the columns on both sides of the comparison or join, and fix the mismatched column's `type` in `schemas/<KEY>.json` (or add a genuine cast in `Output/` if the source code is at fault) |
| `.write().parquet(path)` / `.write().save(path)` to a local or stage path on SCOS (`SparkConnectGrpcException` on write) | Snowpark Connect cannot write Parquet to a path — rewrite to `.write().mode(...).saveAsTable("<table>")`. Systematic SMA artifact, not per-entrypoint. |
| `TIMESTAMP_LTZ` Parquet/stage unload failure (`5001` on writing a frame with `functions.current_timestamp()` / a `TIMESTAMP_LTZ` column) | Convert the timestamp to a string before write: `date_format(current_timestamp(), "yyyy-MM-dd HH:mm:ss.SSSSSS")` — avoids the LTZ unload path. |
| `ABS()` wrapped around a DATE/DATEDIFF result during `saveAsTable` CTAS | Cast the date-diff to int **before** `abs`/aggregation: `abs(datediff(...).cast("int"))`, so codegen does not emit `ABS(<date>)`. |

> **Ambiguous column / 5004 (`AMBIGUOUS_REFERENCE could be: [X, X]`) after a join is usually a MOCK over-seeding problem, not a code defect.** Both legs of the join seed a column with the same name; the comparator reports the ambiguity. Fix with schema repair — remove the mis-attributed column from the offending `schemas/<KEY>.json` via the analyzer and re-run datagen — and do NOT dispatch the migration-fixer unless the column duplication is genuine in the real source schema. The one ambiguous-column case that IS a real code fix (route to the migration-fixer): a SQL `SELECT` alias that shadows a `GROUP BY`/base column (`AS k … GROUP BY k` → rename the alias).

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
> session has been established for this trial yet → re-run the spec once
> (timeout 900s). Only escalate to the normal taxonomy if the retry also fails
> *after* a session was established.

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
repair it inline. Two common shapes:

- **Empty output because date-range `WHERE` filters keep no rows.** `datagen.py`
  anchors date/timestamp columns near *today*, so relative windows
  (`current_date - N`, MTD, last-N-months) should match. If a sink is still
  empty, widen that column's mock `values` (or add a `joins` edge) so rows
  survive, then re-run `schema_mine.py` + `datagen.py schemas/ mock_data` + provision + re-run.
- **`saveAsTable` wrote to the wrong schema / clone is empty (SCOS `USE SCHEMA`
  silently no-ops).** The write must land in the trial's schema: redirect it
  through the harness sink (`patch-add` a `SCOS_SINK_*` write) or, in the
  TEST-PATCH, qualify the write to the trial schema. This is harness wiring — do
  **not** put a `SCOS_*` value into a `[MIGRATION-FIX]` commit (the committer
  rejects it).

These are repaired as **inline schema/data repair rounds**, each recorded with
`record-iter --fix-category analysis_repair`. A schema/data gap
(`TABLE_OR_VIEW_NOT_FOUND` / `COLUMN_NOT_FOUND`) is fixed by editing
`schemas`/`analysis.json` + `schema_mine.py` + `datagen.py schemas/ mock_data` + provision — never by the migration-fixer. Only after **at least two** such
rounds, if it still cannot be made to produce output, may you mark `hard_stuck`
with `--analysis-repair-exhausted --reason "<final-iter error>"` (the gate rejects
fewer than two rounds).

If a sink is **legitimately always empty** and the Phase A baseline is also empty,
declare it so the comparator does not flag the empty output as a divergence:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  mark-empty-baseline --conv-root $CONVERSION_ROOT --trial-id <id> --sink-id <sink>
```

### `passed_no_baseline` is only for trials with no baseline

`record-trial-status` rejects `passed_no_baseline` when Phase A produced a
baseline (a phase_a iter with `passing>=1, failing==0`). If a baseline exists you
MUST compare and mark `passed` (record cosmetic date-boundary diffs via
`document-divergence` — the trial still passes) or `hard_stuck` a real
divergence. Use `passed_no_baseline` only when Phase A genuinely produced no
baseline (`phase_a_skipped`). For the rare case where Phase A captured different
sinks than Phase B (not comparable), pass `--baseline-not-comparable --reason`.

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
   declared in `external_sources`; treat as `passed_no_baseline` via
   `mark-unselected-dependency` if it is the output of an unselected
   entrypoint.
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
> against the cloned golden schema) — it is not local or emulated execution. So
> "local-server mode" is a deployment/process detail, not a statement that Phase B
> runs on this machine.

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
connector. The one requirement: the connection must be **non-interactive**, because
`sbt test` forks headless JVMs with no TTY. Password, key-pair, PAT, or a cached OAuth
token all work; `authenticator = "externalbrowser"` (SSO) does **not** — the browser
flow has no TTY and hangs. The connection must also define a `warehouse`.

1. **SCOS client JAR** — `SnowparkConnectSession` loads from `tests/lib/`.
   **`scos_state.py run-phase-b` stages it automatically** — its `_stage_scos_client_jar`
   setup step copies `snowpark-connect-java-client*.jar` from `Output/lib/`, `~/.m2`, or
   the Coursier cache (wherever the migrate build left it). You do **not** stage the jar by
   hand.

   If `run-phase-b` warns that the jar could not be found, the migrate build didn't produce
   it — re-run `mvn package` in `Output/` (or ensure `Output/lib/` contains
   `snowpark-connect-java-client*.jar`) and re-run `run-phase-b`.

2. **Migrated workload JAR** — must exist at the `jar_path` recorded in `analysis.json`.
   **`scos_state.py run-phase-b` checks this automatically** and warns if absent. If it
   does warn, run `mvn package` in `Output/` and re-run `run-phase-b`.

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
SCOS_ANALYSIS_JSON=$CONVERSION_ROOT/Validation/shared/analysis.json \
SCOS_STATE_JSON=$CONVERSION_ROOT/Validation/state.json \
SCOS_MOCK_DATA_DIR=$CONVERSION_ROOT/Validation/shared/mock_data \
SNOWPARK_CONNECT_PYTHON_VENV=$SNOWPARK_DIR/.venv \
SNOWFLAKE_DEFAULT_CONNECTION_NAME=<config.connectionName> \
sbt test 2>&1 | tee $RESULTS_DIR/sbt_migrated.log
```

Only when isolating a single failing spec for debugging, narrow with
`testOnly` (do NOT make per-trial `testOnly` the normal dispatch path):

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
SCOS_FLAVOR=migrated \
SCOS_RESULTS_DIR=$RESULTS_DIR \
SCOS_CONV_ROOT=$CONVERSION_ROOT \
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
cannot cross processes. The warehouse warm-up above is the real win.

Phase B trials run in **bounded parallel** — each entrypoint spec runs in its own
forked JVM (per-suite fork), and the golden schema is cloned to a uniquely-named
`<GOLDEN>_T<8hex>` per trial, so concurrent trials never collide. Concurrency is
capped by `SCOS_TEST_PARALLELISM` (default 4). **Lower it (e.g. `SCOS_TEST_PARALLELISM=2`,
or `1` for fully serial) if the Snowflake connection rate-limits, the warehouse is
small, or you see clone/session pressure** — concurrent SCOS sessions multiply load
on the endpoint and warehouse.

Compare Phase B output to Phase A baseline. This uses the pure-Python
comparator (no Spark/JVM cold start per trial) — the canonical single-sink
`comparator.py compare`, looped over the trial's captured tables. Each table is
a Spark output **directory** (`tables/<name>.parquet/`); the comparator reads it
natively. Enumerate the tables from the baseline's `_index.json`, skip any
documented-divergence columns from `analysis.json`, and aggregate per-table exit
codes into the trial verdict:

```bash
PA=$CONVERSION_ROOT/Validation/results/phase_a/<id>
PB=$CONVERSION_ROOT/Validation/results/phase_b/<id>
ANALYSIS=$CONVERSION_ROOT/Validation/shared/analysis.json
mkdir -p "$PB/diffs"; trial_rc=0
for t in $(jq -r '.tables[].name' "$PA/_index.json"); do
  # documented divergences for this <id>.<sink> (scope data/both) -> ignore cols
  ign=$(jq -r --arg k "<id>.$t" --arg s "$t" \
    '((.expected_divergences[$k] // []) + (.expected_divergences[$s] // []))
     | map(select((.scope // "data")|ascii_downcase|.=="data" or .=="both"))
     | map(.column // .col) | map(ascii_upcase) | join(",")' "$ANALYSIS" 2>/dev/null)
  # natural_keys for this <id>.<sink>: drives keyed row-matching instead of
  # full-row lexicographic sort. Keyed comparison is stable when any non-key
  # cell differs between runs; full-row sort cascades one divergent cell into
  # many false row-position mismatches. Empty when no keys declared -> safe fallback.
  # NOTE: ep.sinks[] holds string IDs — resolve each ID against the top-level
  # .sinks[] catalog (which carries name + natural_keys).
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

Exit codes (per table, and the aggregated `trial_rc`):

| Code | Meaning |
|------|---------|
| **0** | Match — tables are equivalent |
| **1** | Divergence — values differ |
| **2** | Error — one or both paths missing, or comparator exception |

Treat `trial_rc != 0` as not-passed.

## Failure handling

Classify failures into:

- **harness failure** (`harness_failure`): reusable execution seam
  issue; fix the shared kit
- **unpatched I/O** (`workload_failure`): a missing class / non-Spark call;
  `patch-add` a rewrite to native Spark / env reads (no shims)
- **workload failure** (`workload_failure`): dispatch the migration
  fixer for `Output/`
- **assertion failure** (`assertion_failure`): real output mismatch

## No-baseline path

If Phase A did not produce a baseline for an entrypoint:

1. still run the SCOS test
2. ensure it produces a snapshot
3. if the run is clean, record it as `passed_no_baseline`
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
`analysis_repair`. Use one of these — only `schema_gap` and `analysis_repair` are
recognized by the schema-repair gate (to count inline repair rounds); other strings
are stored but carry no gate-logic meaning.

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
  --trial-id <id> --status <passed|passed_no_baseline|hard_stuck>
```

## Report back

Summarize:

- matched entrypoints
- documented divergences
- `passed_no_baseline` entrypoints needing human review
- remaining hard-stuck items
- any shared-kit fixes made during Phase B
