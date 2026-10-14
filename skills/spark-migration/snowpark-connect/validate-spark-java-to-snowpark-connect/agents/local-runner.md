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
- `ANALYSIS_JSON   = Validation/shared/analysis.json`
- `STATE_JSON      = Validation/state.json`

## Ground Rules

> **Kit dependency preflight — run before any sbt step:**
> ```bash
> KIT_PATH="$SKILL_DIRECTORY/../validate-spark-scala-to-snowpark-connect/harness-scala/kit"
> test -d "$KIT_PATH" || { echo "PREREQ_FAIL: Scala skill kit not found at $KIT_PATH — install validate-spark-scala-to-snowpark-connect alongside this skill and ensure sbt is on PATH"; exit 1; }
> which sbt > /dev/null 2>&1 || { echo "PREREQ_FAIL: sbt not found — install sbt (https://www.scala-sbt.org/download) and ensure it is on PATH"; exit 1; }
> ```
> Java workload JARs are loaded by the shared Scala ScalaTest kit via
> `ReflectionEntrypoint`/`URLClassLoader`. Both the sibling skill directory and
> the sbt/Scala toolchain must be present.

1. Copy the shared kit from `$SKILL_DIRECTORY/../validate-spark-scala-to-snowpark-connect/harness-scala/kit/`
   instead of re-authoring `ScosTrialFixture` or `Helpers` from memory (output
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
(`target/`, `project/target/`) is NOT dragged into the trial dir:

```bash
rsync -a --exclude 'target/' --exclude 'project/target/' \
  $SKILL_DIRECTORY/../validate-spark-scala-to-snowpark-connect/harness-scala/kit/ $TESTS_DIR/
# Fallback if rsync is unavailable:
#   cp -R $SKILL_DIRECTORY/../validate-spark-scala-to-snowpark-connect/harness-scala/kit/. $TESTS_DIR/ && \
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
cp $SKILL_DIRECTORY/../validate-spark-scala-to-snowpark-connect/harness-scala/kit/.gitignore.template $TESTS_DIR/.gitignore
```

**Critical Rule**: Do NOT redefine path-rewriting, connector-read
intercept, or schema-clone logic in individual `Test*Spec.scala` files.
Always use `Helpers` methods.

## Rendering test specs

Render one `Test<EpId>Spec.scala` per selected entrypoint from
`TestTemplate.scala.tmpl`, filling in the fields recorded by the patch author:

- `EP_ID` — entrypoint ID, matches analysis.json `"id"`
- `CLASS_NAME` — ScalaTest class name, e.g. `"TestMyEntrypointSpec"`
- `ENTRY_CLASS_NAME` — fully qualified JVM class name (the Java class)
- `ENTRY_METHOD_NAME` — method name (usually `"main"`)
- `ENTRYPOINT_ARGS` — `Array[String]` literal, e.g. `Array("--env", "dev")` or `Array.empty[String]`
- `JAR_PATH_SOURCE` — absolute path to the ORIGINAL source workload JAR (Phase A, local Spark)
- `JAR_PATH_MIGRATED` — absolute path to the MIGRATED `Output/` workload JAR (Phase B, SCOS).
  The rendered spec picks between the two at runtime via `SCOS_FLAVOR` — there is no
  single `JAR_PATH` placeholder in the template.
- `EXTRA_CLASSPATH_SOURCE` / `EXTRA_CLASSPATH_MIGRATED` — pathsep-joined dependency jars
  for thin (non-fat) source/migrated JARs; empty string (`""`) when the JAR is a fat/assembly
  jar or no extra classpath is needed. **Every placeholder must be substituted — an
  unfilled `{{TOKEN}}` compiles fine inside the template's `"""..."""` string literals but
  is silently wrong at runtime (e.g. a literal `{{EXTRA_CLASSPATH_SOURCE}}` string gets
  filtered out as a nonexistent path, silently dropping the real classpath).**
- `TRIAL_DIR` — absolute path to `results/phase_a/<ep_id>/`
- `PHASE_A_DIR` — absolute path to `results/phase_a/<ep_id>/` (Phase B comparison baseline)
- `WIDGET_ENV_VARS` — Map entries, e.g. `"SCOS_WIDGET_ENV" -> "dev",` or empty
- `ANALYSIS_JSON_PATH` — absolute path to `Validation/shared/analysis.json` (shim fallback)
- `SCHEMAS_DIR_PATH` — absolute path to `Validation/shared/schemas/` — preferred source of
  truth; sets `SCOS_SCHEMAS_DIR` so `AnalysisJson.load()` reads `manifest.json` +
  entrypoint dirs directly. Empty string when schemas are absent (falls back to
  `ANALYSIS_JSON_PATH`).
- `STATE_JSON_PATH` — absolute path to `Validation/state.json`

Place the rendered spec at:
`Validation/tests/src/test/scala/Test<EpId>Spec.scala`

Create the package directory first:
```bash
mkdir -p "$TESTS_DIR/src/test/scala/com/snowflake/scos/kit/generated"
```

Keep these rendered specs minimal.

## Prerequisites (Phase A)

Phase A runs the **original** workload (`Validation/source/`, plain `SparkSession`) on
local Spark+Delta against the seeded mock data — it produces the baseline that Phase B
is compared against. It does **not** run the migrated `Output/` (that uses
`SnowparkConnectSession` and is Phase B's job).

The deterministic runner `scos_state.py run-phase-a` handles this end to end: it builds
the original source jar (`mvn package` / `./gradlew shadowJar` in
`Validation/source/`, with a `package` fallback), resolves the migrated `Output` jar,
renders one spec per entrypoint with **both** jars baked in (the spec picks one at
runtime via `SCOS_FLAVOR`), and runs `sbt test`. Prefer it over driving the steps by hand:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  run-phase-a --conv-root $CONVERSION_ROOT
```

> **Do NOT pre-skip Phase A by grepping `Output/` for `SnowparkConnectSession`.** The
> migrated `Output/` always contains it — that is expected and is exactly what Phase B
> runs. Skipping Phase A on that basis destroys the baseline and makes the whole
> validation a no-op (`passed_no_baseline` for everything). Phase A must run the
> *original* Java source. Only mark a trial `phase_a_skipped` when the **original source**
> genuinely cannot run on local OSS Spark (real dialect/Databricks reasons below), not
> because the migrated output uses SCOS.

If you are running the steps manually instead of via `run-phase-a`, build the source jar
first and verify it exists:

```bash
test -f "$SOURCE_JAR" || echo "PREREQ_FAIL: source JAR not built — run mvn package (or ./gradlew shadowJar) in Validation/source/ first"
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
SCOS_ANALYSIS_JSON=$CONVERSION_ROOT/Validation/shared/analysis.json \
SCOS_STATE_JSON=$CONVERSION_ROOT/Validation/state.json \
SCOS_MOCK_DATA_DIR=$CONVERSION_ROOT/Validation/shared/mock_data \
sbt "testOnly *Test<EpId>Spec" 2>&1 | tee $RESULTS_DIR/sbt_source.log
```

Phase A specs run in **bounded parallel** — one forked JVM per entrypoint spec
(per-suite fork keeps EnvUtil system-property overrides isolated), each with its
own warehouse/checkpoint dir. Concurrency is capped by `SCOS_TEST_PARALLELISM`
(default 4); lower it to `1` for fully serial if the machine is memory-constrained
(each fork starts a local Spark + Delta session).

Classify failures into:

- **harness issue** — problem in `ScosTrialFixture`, `Helpers`, or the
  kit build configuration
- **mock-data issue** — schema mismatch, missing column, bad CSV
- **compilation/reflection issue** — workload JAR not found,
  `ClassNotFoundException`, `NoSuchMethodException`
- **workload issue** — runtime exception in the workload body

Route each failure to the matching action:

| Failure | Action |
|---|---|
| `AnalysisException` / `TABLE_OR_VIEW_NOT_FOUND` on a table | Add the missing table to `external_sources[]` in `analysis.json`; re-run `schema_mine.py` + datagen |
| `COLUMN_NOT_FOUND` / Spark analysis error on a column | Add the missing column to the source's `schema` array; re-run `schema_mine.py` + datagen |
| `AnalysisException` on a 3-part `CATALOG.SCHEMA.TABLE` name | **Namespace-rebind patch** via `patch-add` |
| Parquet type mismatch | Fix the column's `type` in `external_sources[].schema`; re-run `schema_mine.py` + datagen |
| Clean run but output empty/all-null | Add filter literals as `"values"`, or a `joins` edge; re-run datagen |
| Unpatched I/O — cloud read/write, `dbutils`, secrets | **`patch-add`** so the workload reads `SCOS_INPUT_*` / `SCOS_SINK_*` |
| Connector read — `spark.read().format("snowflake")…load()` | **`patch-add` per-side**: `source` → `spark.read().parquet(...)` rewrite; see `patch-author.md`; never skip |
| Harness/kit bug | Edit the copied kit under `Validation/tests/` |

JVM-specific failure modes to watch for in Phase A:

- `ClassNotFoundException` — compiled JAR is missing or the
  `entrypoint_class` is wrong; re-run `mvn package` / `./gradlew shadowJar`
- `NoSuchMethodException` — `entrypoint_method` does not match the
  actual method signature; verify with `javap -p ClassName`
- **`NoSuchMethodError` containing `remote` or `SparkConnect`** — Phase A loaded the
  *migrated* jar instead of the original source jar. Confirm `SCOS_FLAVOR=source`.
- `NullPointerException` in `dbutils.*` or Databricks API — un-patched
  Databricks dependency; add a `patch-add` rewrite
- `DeltaAnalysisException` — Delta table path conflict; use a fresh
  `spark.warehouse.dir` per trial (the fixture handles this)
- **Connector read breaks Phase A** — a source-side
  `spark.read().format("snowflake")…load()` (no local connector; its options map is
  often a stripped config global) or a `spark.sql`/`spark.table` read with a
  hardcoded prod 3-part name. This is **not** a harness or workload defect: the
  `source` side needs a `spark.table(...)` mock rewrite / literal-prefix rebind
  via `patch-add` (see `patch-author.md` "Connector reads are a per-side patch").
  An `.option("sfDatabase"/"sfSchema", …)` rebind only fixes the migrated side and
  silently no-ops on `spark.sql`/`spark.table`.

### Inline schema repair (Phase A — do not exit)

**Mock data is owned by `analysis.json` — never hand-edit mock files.** When a
Phase A run hits a mock-data failure, the repair loop is:

1. Edit `analysis.json` — add or fix the relevant `external_sources[].schema`
   columns or table entry.
2. Re-run `schema_mine.py`:
   ```bash
   uv run --project $SKILL_DIRECTORY/.. python \
     $SKILL_DIRECTORY/scripts/schema_mine.py --conv-root $CONVERSION_ROOT
   ```
3. Regenerate typed mocks and verify:
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
4. Re-run the spec(s) and record the iter with `--fix-category analysis_repair`.

`datagen.py` derives the physical Parquet type from the declared `type` in
`analysis.json`:

| Declared type | Parquet physical type |
|---|---|
| `decimal(p,s)` | `decimal128(p,s)` |
| `timestamp*` | `timestamp[us]` |
| `date` | `date32` |
| `short` / `smallint` | `int16` |
| `byte` / `tinyint` | `int8` |
| `real` | `double` |

A correctly declared `type` always produces a seedable mock.

## No shims — patch the I/O instead

There are no JVM shims or mock filesystems in the kit. All non-Spark I/O
is rewritten by the patch-author's `patch-add` blueprint into native Spark +
env-var indirection (`System.getProperty`), or deleted.

If a Phase A run hits a missing class or an unsupported non-Spark call
(`ClassNotFoundException`, `NoSuchMethodException`, `NullPointerException`), the
cause is an **un-patched I/O dependency**. Add a `scos_state.py patch-add` patch
that rewrites the offending call to native Spark / env reads (see
`patch-author.md`), then re-run.

## When to stop Phase A

For each entrypoint: if local execution succeeds and the snapshot looks
trustworthy, record the baseline; otherwise classify the failure and fix it.

**Schema/mock gaps are always repairable — never skip for them.**
`TABLE_OR_VIEW_NOT_FOUND`, `COLUMN_NOT_FOUND`, missing `mock_file`,
`columns: []`, and empty/all-null output are always fixable by inline schema
repair (see *Environment differences and Phase A skip* below), no matter how
many tables are missing. `phase_a_skipped` is reserved for genuine environment
differences — rare, and never the right response to a schema gap. The
3-iteration cap below is a guard against genuine thrash, not an escape hatch
for fixable schema issues — add the missing table to `external_sources[]` and
repair the schema instead.

**Hard iteration cap (enforce, not advisory):** Phase A gets at most **3**
iterations per entrypoint. At the START of every Phase A attempt, check the
recorded `phase_a_iters` for the trial; if it is already `>= 3`, do NOT start
another iteration — immediately mark the trial `phase_a_skipped`:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-trial-status --conv-root $CONVERSION_ROOT \
  --trial-id <id> --status phase_a_skipped \
  --reason "phase A iteration cap (3) reached"
```

Do not block the entire workflow on one missing baseline. Phase B still
runs.

## Environment differences and Phase A skip

Phase A runs the source Java workload against local Spark + Delta. Some
constructs cannot be executed in this environment:

- `QUALIFY` clauses and Snowflake-specific SQL extensions
- `MERGE INTO` / `LATERAL VIEW` Databricks-dialect variants
- Operations that require a real Snowflake connection at the Spark level

**Never a skip:**
- **Missing / unmocked source tables** (`TABLE_OR_VIEW_NOT_FOUND`,
  `COLUMN_NOT_FOUND`) — inline schema repair, regardless of table count (see
  *When to stop Phase A* above).

When Phase A fails on such an environment difference (NOT a workload
bug), the local-runner MUST:

1. Mark the trial with status `phase_a_skipped`:
   ```bash
   uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
     record-trial-status --conv-root $CONVERSION_ROOT \
     --trial-id <id> --status phase_a_skipped \
     --reason "<short reason>"
   ```
2. Proceed to Phase B without a Phase A baseline.

Do NOT attempt to rewrite Snowflake-only SQL into local-Spark equivalents.

## Record keeping

Call `record-iter` after each meaningful iteration:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-iter --conv-root $CONVERSION_ROOT --trial-id <id> \
  --phase phase_a --iter <N> --fix-category <category> \
  --notes "<short>"
```

After applying any patch:

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
