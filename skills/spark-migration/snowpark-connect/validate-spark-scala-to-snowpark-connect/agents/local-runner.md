# Local Runner

Owns Phase A: copy the test kit, render ScalaTest specs from the
template, run the selected entrypoints locally on Spark + Delta, and
persist source baselines when possible.

## Inputs

- `CONVERSION_ROOT`
- `SKILL_DIRECTORY`

Derived paths:

- `VALIDATION_ROOT = <CONVERSION_ROOT>/Validation`
- `TESTS_DIR       = Validation/tests`
- `RESULTS_DIR     = Validation/results/phase_a`
- `SCHEMAS_DIR     = Validation/shared/schemas` (SoT)
- `ANALYSIS_JSON   = Validation/shared/analysis.json` (generated shim — do not hand-edit)
- `STATE_JSON      = Validation/state.json`

## Ground Rules

1. Copy the shared kit from `$SKILL_DIRECTORY/harness-scala/kit/` instead
   of re-authoring `ScosTrialFixture` or `Helpers` from memory (output
   comparison is done by `$VALIDATOR_SCRIPTS/harness/comparator.py compare`, looped per captured table).
2. Render one `Test<EpId>Spec.scala` per selected entrypoint from
   `TestTemplate.scala.tmpl`.
3. If you find a reusable harness gap during this run, fix the copied
   kit under `Validation/tests/` instead of piling fixes into individual
   test specs.
4. Widget inputs belong in rendered test specs via `WIDGET_ENV_VARS`,
   not in a separate widget manifest file.

## Setting up the test project

Copy the full kit as an sbt project. Use `rsync` so the build output
(`target/`, `project/target/`) is NOT dragged into the trial dir — copying
it forces a full zinc recompile and pulls in the resolved Spark JARs:

```bash
rsync -a --exclude 'target/' --exclude 'project/target/' \
  $SKILL_DIRECTORY/harness-scala/kit/ $TESTS_DIR/
# Fallback if rsync is unavailable:
#   cp -R $SKILL_DIRECTORY/harness-scala/kit/. $TESTS_DIR/ && \
#   rm -rf $TESTS_DIR/target $TESTS_DIR/project/target
```

This gives you:
- `build.sbt` — kit build file (spark, delta, JDBC dependencies)
- `project/` — sbt meta-project
- `src/main/scala/ScosTrialFixture.scala` — base fixture
- `src/main/scala/Helpers.scala` — seedEntrypoint, captureResults,
  cloneGoldenSchemaForTrial, declaredSinkTables
- `src/main/scala/ReflectionEntrypoint.scala` — JVM reflection
  loader for compiled workload JARs
- `templates/TestTemplate.scala.tmpl` — fill-in template for per-entrypoint specs
- `.gitignore.template`

Copy the `.gitignore.template`:

```bash
cp $SKILL_DIRECTORY/harness-scala/kit/.gitignore.template $TESTS_DIR/.gitignore
```

**Critical Rule**: Do NOT redefine path-rewriting, connector-read
intercept, or schema-clone logic in individual `Test*Spec.scala` files.
Always use `Helpers` methods. Workload-specific extensions belong in
dedicated `tests/src/test/scala/<Workload>Extensions.scala` files that
import `Helpers` and add new wrappers.

## Rendering test specs

Render one `Test<EpId>Spec.scala` per selected entrypoint from
`TestTemplate.scala.tmpl`, filling in the fields recorded by the patch author:

- `EP_ID` — entrypoint ID, matches `schemas/manifest.json` / `_meta.json` `"id"`
- `CLASS_NAME` — ScalaTest class name, e.g. `"TestMyEntrypointSpec"`
- `ENTRY_CLASS_NAME` — fully qualified JVM class name
- `ENTRY_METHOD_NAME` — method name (usually `"main"`)
- `ENTRYPOINT_ARGS` — `Array[String]` literal, e.g. `Array("--env", "dev")` or `Array.empty[String]`
- `JAR_PATH_SOURCE` — absolute path to the ORIGINAL source workload JAR (Phase A, local Spark)
- `JAR_PATH_MIGRATED` — absolute path to the MIGRATED `Output/` workload JAR (Phase B, SCOS).
  The rendered spec picks between the two at runtime via `SCOS_FLAVOR` — there is no
  single `JAR_PATH` placeholder in the template.
- `EXTRA_CLASSPATH_SOURCE` / `EXTRA_CLASSPATH_MIGRATED` — pathsep-joined dependency jars
  for thin (non-fat) source/migrated JARs; empty string (`""`) when the JAR is a fat/assembly
  jar or no extra classpath is needed. Every placeholder must be substituted — an unfilled
  `{{TOKEN}}` compiles fine inside the template's `"""..."""` string literals but is
  silently wrong at runtime.
- `TRIAL_DIR` — absolute path to `results/phase_a/<ep_id>/`
- `PHASE_A_DIR` — absolute path to `results/phase_a/<ep_id>/` (Phase B comparison baseline)
- `WIDGET_ENV_VARS` — Map entries, e.g. `"SCOS_WIDGET_ENV" -> "dev",` or empty
- `SCHEMAS_DIR_PATH` — absolute path to `Validation/shared/schemas/` — preferred SoT; sets `SCOS_SCHEMAS_DIR` so `AnalysisJson.load()` reads `manifest.json` + entrypoint dirs directly
- `ANALYSIS_JSON_PATH` — absolute path to `Validation/shared/analysis.json` — shim fallback when schemas are absent
- `STATE_JSON_PATH` — absolute path to `Validation/state.json` — same reason

Place the rendered spec at:
`Validation/tests/src/test/scala/Test<EpId>Spec.scala`

Create the package directory first (it is not in the kit source):
```bash
mkdir -p "$TESTS_DIR/src/test/scala/com/snowflake/scos/kit/generated"
```

Keep these rendered specs minimal.

## Prerequisites (Phase A)

Phase A runs the **original** workload (`Validation/source/`, plain `SparkSession`) on
local Spark+Delta against the seeded mock data — it produces the baseline that Phase B
is compared against. It does **not** run the migrated `Output/` (that uses
`SnowparkConnectSession` and is Phase B's job).

### Build-doctor (Scala analogue of PySpark `seed-venv`)

Before the first Phase A trial run, prove the source build converges. Prefer
fat/assembly; **thin jar + filtered dependency classpath is a valid Phase A
input**. Hard-die only when **no jar of any kind** is produced (missing assembly
plugin alone is not a whole-batch abort when thin jar + classpath succeeded).

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  build-doctor --conv-root $CONVERSION_ROOT
  # Before Phase B also prove Output/:
  #   build-doctor --conv-root $CONVERSION_ROOT --side migrated
```

`build-doctor` runs the source-jar ladder (assembly/shadow → package + exported
runtime classpath), classifies failures, and prints a JSON report — **no tests
run**. Use it after kit stage / before render, or let `run-phase-a` invoke the
same ladder. Per-entrypoint CNF/link failures stay per-trial via classification
below; do not treat a usable thin jar as a batch abort.

The deterministic runner `scos_state.py run-phase-a` handles Phase A end to end:
it builds the original source jar (assembly preferred; thin jar + classpath
fallback), resolves the migrated `Output` jar, renders one spec per entrypoint
with **both** jars baked in (the spec picks one at runtime via `SCOS_FLAVOR`,
and passes `EXTRA_CLASSPATH` for thin-jar Phase A), and runs `sbt test`. Prefer
it over driving the steps by hand:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  run-phase-a --conv-root $CONVERSION_ROOT
```

After the last iteration's fixes, run once with `--verify-all` to catch
regressions across all trials (including those already marked passed):

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  run-phase-a --conv-root $CONVERSION_ROOT --verify-all
```

If any trial regresses, apply the fix and iterate — same loop, no new terminal
state. Do **not** skip `--verify-all` on production validation runs.

> **Mock-data guard (automatic).** `run-phase-a` first runs a deterministic
> pre-flight: if `Validation/shared/schemas/` is missing it mines it
> (`schema_mine`), then seeds typed mocks (hash-gated `datagen`,
> which never clobbers good files) and runs `datagen`. If verify reports
> problems **or datagen cannot be imported**, it **hard-fails with the problem
> list** instead of running sbt against broken/empty mocks — so fix
> `schemas/` tables/`_meta.json` and re-run. An empty baseline is never a reason to
> skip: the guard turns a would-be empty baseline into an actionable datagen
> error. Never pass `--no-mock-guard` on production validation runs (debug
> escape hatch only when mocks are already known good).
>
> **Provision is hash-gated (PySpark parity).** `scos_state.py provision` and
> `run-phase-b` use the shared PySpark `provision_golden_schemas` +
> `Validation/shared/provision_hashes.json`. After inline schema repair, re-run
> `datagen` then `provision` (or just `run-phase-b`) — unchanged tables are
> skipped; changed tables reseed. Use `provision --force-reseed` to clear hashes
> and reload everything. Set `SCOS_SKIP_PROVISION=1` only for deliberate debug.
> **Do NOT pre-skip Phase A by grepping `Output/` for `SnowparkConnectSession`.** The
> migrated `Output/` always contains it — that is expected and is exactly what Phase B
> runs. Skipping Phase A on that basis destroys the baseline and makes the whole
> validation a no-op (`passed_no_baseline` for everything). Phase A must run the
> *original* source. Only mark a trial `phase_a_skipped` when the **original source**
> genuinely cannot run on local OSS Spark (real dialect/Databricks reasons below), not
> because the migrated output uses SCOS.

If you are running the steps manually instead of via `run-phase-a`, run
`build-doctor` first and verify a jar path exists (fat or thin+classpath):

```bash
test -f "$SOURCE_JAR" || echo "PREREQ_FAIL: source JAR not built — run build-doctor / sbt assembly (or package + dependency classpath) in Validation/source/ first"
```

## Iteration loop

Run **all** selected specs in one batched pass — this is the default. One
forked JVM per entrypoint spec, run in bounded parallel, in a single agent
loop with one result-processing pass (do NOT dispatch one `testOnly` per
trial — that pays N JVM cold-starts and N read→compare→record loops):

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
SCOS_FLAVOR=source \
SCOS_TEST_PARALLELISM=4 \
SCOS_RESULTS_DIR=$RESULTS_DIR \
SCOS_CONV_ROOT=$CONVERSION_ROOT \
SCOS_SCHEMAS_DIR=$CONVERSION_ROOT/Validation/shared/schemas \
SCOS_ANALYSIS_JSON=$CONVERSION_ROOT/Validation/shared/analysis.json \
SCOS_STATE_JSON=$CONVERSION_ROOT/Validation/state.json \
SCOS_MOCK_DATA_DIR=$CONVERSION_ROOT/Validation/shared/mock_data \
sbt test 2>&1 | tee $RESULTS_DIR/sbt_source.log
```

Only when you need to isolate a single failing spec for debugging, narrow
to one with `testOnly`:

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
SCOS_FLAVOR=source \
SCOS_RESULTS_DIR=$RESULTS_DIR \
SCOS_CONV_ROOT=$CONVERSION_ROOT \
SCOS_SCHEMAS_DIR=$CONVERSION_ROOT/Validation/shared/schemas \
SCOS_ANALYSIS_JSON=$CONVERSION_ROOT/Validation/shared/analysis.json \
SCOS_STATE_JSON=$CONVERSION_ROOT/Validation/state.json \
SCOS_MOCK_DATA_DIR=$CONVERSION_ROOT/Validation/shared/mock_data \
sbt "testOnly *Test<EpId>Spec" 2>&1 | tee $RESULTS_DIR/sbt_source.log
```

Phase A specs run in **bounded parallel** — one forked JVM per entrypoint spec
(per-suite fork keeps EnvUtil system-property overrides isolated), each with its
own warehouse/checkpoint dir. Concurrency is capped by `SCOS_TEST_PARALLELISM`;
when `--parallelism` is omitted, `run-phase-a` auto-caps from host RAM
(`<8 GB` → 1, `<16 GB` → 2, else → 4). Explicit `--parallelism N` / env always
wins. Lower to `1` only for a reproducible resource limit (each fork starts a
local Spark + Delta session).

Classify failures into:

- **harness issue** — problem in `ScosTrialFixture`, `Helpers`, or the
  kit build configuration
- **mock-data issue** — schema mismatch, missing column, bad CSV
- **compilation/reflection issue** — workload JAR not found,
  `ClassNotFoundException`, `NoSuchMethodException`
- **workload issue** — runtime exception in the workload body that
  prevents a trustworthy local baseline

Route each failure to the matching action:

| Failure | Action |
|---|---|
| `AnalysisException` / `TABLE_OR_VIEW_NOT_FOUND` on a table | Add the missing table under `schemas/entrypoints/<id>/tables/<KEY>.json` with `"access": "read"` (or `"readwrite"`); re-run datagen (see **Inline schema repair** below) |
| `COLUMN_NOT_FOUND` / Spark analysis error on a column | Add the missing column to that table's `columns` array in `schemas/`; re-run datagen |
| `AnalysisException` on a 3-part `CATALOG.SCHEMA.TABLE` name | **Namespace-rebind patch** via `patch-add` (`SCOS_DATABASE_NAME`/`SCOS_OUTPUT_SCHEMA`) — this is plumbing, NOT a skip |
| Parquet type mismatch (`Expected: decimal(10,2), Found: DOUBLE`; `Expected: date, Found: INT64`) | Fix the column's `type` in `schemas/.../tables/<KEY>.json`; re-run datagen |
| Clean run but output empty/all-null (filter keeps no rows, join key doesn't overlap), or a harness failure saying a declared sink produced/captured 0 rows | Add filter literals as `"values"` on columns, or a `joins` edge in `_meta.json`; re-run datagen. Set `allow_empty` on the sink table only for a rare sink that is genuinely intentionally empty. |
| Unpatched I/O — cloud read/write, `dbutils`, secrets | **`patch-add`** so the workload reads `SCOS_INPUT_*` / `SCOS_SINK_*`; see `patch-author.md` |
| Connector read — `spark.read.format("snowflake")…load()` | **`patch-add` per-side**: `source` → `spark.table(...)` rewrite; see `patch-author.md`; never skip |
| `ArgumentException: Set custom transforms…` / `Provovide high level descriptions…` containing `rawInput:` | **Staging CLI args missing or incomplete.** The `Args.*` wrapper uses lift-json which requires ALL case class fields in the JSON. Author complete `cli_args` stubs in `schemas/entrypoints/<id>/_meta.json` per `patch-author.md § Staging CLI args` (placeholders like `__scos_mock__` must match the workload). Container fields (`category`, `region`, `provider`) must match the workload's `CONTAINER_GROUP_MAPPINGS_REVERSED`. |
| `MappingException: No usable value for <field>` (lift-json) | A case class field is missing from the CLI args JSON stub. Add the missing field with its zero value (empty string, `false`, `[]`, or `{}`). **All fields must be present — lift-json does NOT use Scala default values for missing JSON keys.** |
| `file type is not supported` from `Blob.load()` / `S3.load()` | DataLake intercept not firing. Add the `SCOS_CAPTURE_DIR` sentinel bypass to `Blob.load()`, `S3.load()`, `SFTP.load()` per `patch-author.md § DataLake / Blob / S3 / SFTP read interception`. |
| `requirement failed: Number of partitions (0) must be positive` | `SparkUtils.TOTAL_NUMBER_OF_AVAILABLE_CORES = 0` in local Spark. Add a SCOS bypass in `run()` before the stage call per `patch-author.md § Complex staging pipeline bypass`. |
| `StageValidationException: Gold/Silver/Bronze Validation Failed` | Stage joins multiple delta sources whose mock schemas don't match. Add a SCOS bypass in `run()` before `gold.getStage()` / `bronze.getStage()` per `patch-author.md § Complex staging pipeline bypass`. |
| Harness/kit bug (`ScosTrialFixture`, `Helpers`, `build.sbt`) | Edit the copied kit under `Validation/tests/`; escalate if a deeper kit defect |

JVM-specific failure modes to watch for in Phase A:

- `ClassNotFoundException` — compiled JAR is missing, classpath incomplete, or the
  `entrypoint_class` is wrong. Prefer re-running `build-doctor` / `run-phase-a`
  (thin jar needs the filtered dependency classpath on `EXTRA_CLASSPATH`). For
  Phase B, re-run `sbt assembly` in `Output/`.
- `NoSuchMethodException` — `entrypoint_method` does not match the
  actual method signature; verify with `javap -p ClassName`
- **`NoSuchMethodError` containing `remote` or `SparkConnect`** — Phase A loaded the
  *migrated* jar (which calls `SparkSession.builder().remote()`) instead of the original
  source jar. This means the wrong jar was selected: confirm `SCOS_FLAVOR=source` and that
  `run-phase-a` built the source jar from `Validation/source/` (`JAR_PATH_SOURCE` in the
  rendered spec must be non-empty). It is **not** a reason to skip Phase A.
- `KryoException` / `NotSerializableException` — Spark serialization
  issue in the workload; note for human review, do not attempt to fix
- `DeltaAnalysisException` — Delta table path conflict; use a fresh
  `spark.warehouse.dir` per trial (the fixture handles this)
- Scala version mismatch — workload compiled with Scala 2.13 but kit is
  2.12 (or vice versa); check `build.sbt` `scalaVersion`
- **Missing assembly plugin** — not a whole-batch abort when thin jar +
  dependency classpath succeeded; proceed and classify per-trial link failures.
- **Connector read breaks Phase A** — a source-side
  `spark.read.format("snowflake")…load()` (no local connector; its options map is
  often a stripped `%run`-config global) or a `spark.sql`/`spark.table` read with
  a hardcoded prod 3-part name. This is **not** a harness or workload defect: the
  `source` side needs a `spark.table(...)` mock rewrite / literal-prefix rebind
  via `patch-add` (see `patch-author.md` "Connector reads are a per-side patch"). An
  `.option("sfDatabase"/"sfSchema", …)` rebind only fixes the migrated side and
  silently no-ops on `spark.sql`/`spark.table`.

### Inline schema repair (Phase A — do not exit)

**Mock data is owned by `schemas/` — never hand-edit mock files or the generated
`analysis.json` shim.** When a Phase A run hits a mock-data failure (missing
table/column, type mismatch, empty output), the repair loop is:

1. Edit `Validation/shared/schemas/entrypoints/<id>/tables/<KEY>.json` (columns /
   types / mock_file) and/or `_meta.json` (`cli_args`, `joins`, `values`).
2. Regenerate typed mocks and verify (do **not** re-run full `schema_mine` unless
   you need to remine AST — that would overwrite LLM fills):
   ```bash
   uv run --project $SKILL_DIRECTORY/.. python \
     $VALIDATOR_SCRIPTS/datagen.py \
     $CONVERSION_ROOT/Validation/shared/schemas \
     $CONVERSION_ROOT/Validation/shared/mock_data
   uv run --project $SKILL_DIRECTORY/.. python \
     $VALIDATOR_SCRIPTS/datagen.py \
     $CONVERSION_ROOT/Validation/shared/schemas \
     $CONVERSION_ROOT/Validation/shared/mock_data \
     --verify
   ```
   `--verify` must print `[datagen] verify OK` (exit 0) before re-running sbt.
3. Re-run the spec(s) and record the iter with `--fix-category analysis_repair`.

`datagen.py` derives the physical Parquet type from the declared `type` in
`schemas/` tables: `decimal(p,s)` → `decimal128(p,s)`, `timestamp*` →
`timestamp[us]`, `date` → `date32`, `short`/`smallint` → `int16`,
`byte`/`tinyint` → `int8`, `real` → `double`. A correctly declared `type` always
produces a seedable mock.

Only escalate past this loop for harness kit bugs or genuine Phase A skip
conditions — not for fixable schema gaps.

## No shims — patch the I/O instead

There are no JVM shims or mock filesystems in the kit. All non-Spark I/O
(cloud reads/writes, `dbutils`, JDBC, HTTP, secrets, widgets) is rewritten by
the patch-author's `patch-add` blueprint into native Spark + env-var
indirection (`System.getProperty`), or deleted — exactly like the PySpark
validator.

If a Phase A run hits a missing class or an unsupported non-Spark call
(`ClassNotFoundException`, `NoSuchMethodException`, `ClassCastException`), the
cause is an **un-patched I/O dependency**, not a missing stub. Add a
`scos_state.py patch-add` patch that rewrites the offending call to native
Spark / env reads (see `patch-author.md`), then re-run. If the problem is at
the reusable execution seam (session wiring, snapshot capture), fix the copied
kit under `Validation/tests/` instead.

## When to stop Phase A

For each entrypoint: if local execution succeeds and the snapshot looks
trustworthy, record the baseline; otherwise classify the failure and fix it.

**Schema/mock gaps are always repairable — never skip for them.**
`TABLE_OR_VIEW_NOT_FOUND`, `COLUMN_NOT_FOUND`, missing `mock_file`,
`columns: []`, empty/all-null output, and declared-sink-empty failures are
always fixable by inline schema repair (see *Inline schema repair* below),
no matter how many tables are missing — unless the sink is genuinely
intentionally empty, in which case set `allow_empty` on the sink table in
`schemas/`. `phase_a_skipped` is the Phase A analogue of `hard_stuck` — rare, and
never the right response to a schema gap.

**Iteration budget → escalate, do NOT auto-skip.** Phase A is expected to
converge in ~3 iterations. If you are still failing after 3 iterations, that is a
signal to **escalate the specific failure**, not to skip Phase A:

- **Schema/mock gap** (missing table/column, type mismatch, empty output): keep
  repairing inline. The `record-trial-status` gate will REJECT a `hard_stuck` here
  unless you have ≥2 recorded `analysis_repair` iters and pass
  `--analysis-repair-exhausted` — because these are always fixable. Fix
  `schemas/` and regenerate mocks; do not skip.
- **Harness/kit defect**: fix the copied kit under `Validation/tests/`, record a
  `harness_failure` iter, and only then (if truly stuck) `--harness-repair-exhausted`.
- **Un-patchable I/O**: add a blueprint `patch-add`, record a `patch_failure` iter,
  and only then `--patch-repair-exhausted`.
- **Genuine workload/dialect bug**: dispatch the migration-fixer.

There is **no "iteration cap reached" skip**. `phase_a_skipped` requires a
`--reason` naming a specific construct the local runtime genuinely cannot execute
(see *Environment differences and Phase A skip* below); "ran out of iterations" is
not such a reason and the gate treats a blank/generic skip reason as invalid.

A missing Phase A baseline is not free — it downgrades the entrypoint to
`passed_no_baseline`, which ships with **no parity check** and is flagged for human
review. Treat it as a last resort, not a time-saver.

Do not block the entire workflow on one genuinely-unrunnable baseline. Phase B
still runs.

## Environment differences and Phase A skip

Phase A runs the source-flavor Scala workload against local Spark +
Delta. **Skip is a LAST RESORT** — only for a construct the local runtime
genuinely cannot execute, confirmed after patching everything fixable and at
least one real run attempt. Examples of genuine skips:

- `QUALIFY` clauses and Snowflake-specific SQL extensions
- `MERGE INTO` / `LATERAL VIEW` Databricks-dialect variants
- Operations that require a real Snowflake connection at the Spark level

**Never a skip (patch or repair, do not skip):**
- **Missing / unmocked source tables** (`TABLE_OR_VIEW_NOT_FOUND`,
  `COLUMN_NOT_FOUND`) — inline schema repair, regardless of table count (see
  *When to stop Phase A* above).
- **Connector reads** (`spark.read.format("snowflake"/"jdbc"/"redshift")…load()`),
  3-part `CATALOG.SCHEMA.TABLE` names, and other external I/O — these are
  **patches** (`patch-add`), not skips.
- **"Ran out of iterations"** — escalate per the iteration-budget rule above.

When Phase A fails on a genuine environment difference (NOT a workload bug, NOT a
schema gap, NOT patchable I/O), the local-runner MUST:

1. Mark the trial `phase_a_skipped` with a `--reason` that **names the specific
   construct** the local runtime cannot execute (the gate REJECTS a blank or
   generic reason):
   ```bash
   uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
     record-trial-status --conv-root $CONVERSION_ROOT \
     --trial-id <id> --status phase_a_skipped \
     --reason "QUALIFY clause in rank.sql — unsupported in local OSS Spark"
   ```
2. Proceed to Phase B without a Phase A baseline.
3. Phase B auto-promotes a clean run to `passed_no_baseline`, **preserving your
   skip reason** so the final report explains the missing baseline.

Do NOT set `passed_no_baseline` directly — the gate rejects it. Do NOT attempt to
rewrite Snowflake-only SQL into local-Spark equivalents. The honest path is
`phase_a_skipped --reason <construct>` → `passed_no_baseline`.

## Record keeping

Call `record-iter` after each meaningful iteration:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-iter --conv-root $CONVERSION_ROOT --trial-id <id> \
  --phase phase_a --iter <N> --fix-category <category> \
  --notes "<short>"
```

After applying any patch to `tests/`, `Output/`, or `shared/`, record:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-patch --conv-root $CONVERSION_ROOT --trial-id <id> \
  --phase phase_a --file <path-relative-to-conv-root> \
  --reason "<short>" --iter <N>
```

If baselines were authored at all, record:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-milestone --conv-root $CONVERSION_ROOT --milestone tests_authored
```

## Report back

Summarize:

- which entrypoints produced baselines
- which did not, and why
- what harness changes were made
- what JVM-specific issues should be carried into Phase B for human review
