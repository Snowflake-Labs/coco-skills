# harness-scala — Scala/JVM Test Harness for SCOS Validation

This directory contains the native JVM implementation of the
two-phase differential validator for Spark Scala → Snowpark Connect
(SCOS) migrations. It mirrors the capability of the PySpark validator
but is implemented entirely in Scala/JVM, eliminating Python-specific
glue (no `sitecustomize.py`, no import shadowing, no `cloudpickle`).

## Directory Layout

```
harness-scala/
  control/          ← standalone sbt project (analyze-only fat-JAR)
    src/main/scala/            ← flat layout; package is com.snowflake.scos.validate
      ScosAnalyze.scala     ← deterministic Scalameta AST facts extractor
                              (the `analyze` command); no Python equivalent
      Json.scala            ← small circe JSON helper (die/load/writeAtomic)
      Main.scala            ← thin entry point: dispatches `analyze`
    build.sbt               ← Scala 2.12, circe + Scalameta only, sbt-assembly
    project/
      plugins.sbt           ← sbt-assembly plugin
  # NOTE: provision / cleanup / compare / datagen REUSE the canonical PySpark
  #       validator scripts at ../validate-pyspark-to-snowpark-connect/scripts/
  #       (scos_state.py provision/cleanup.py, datagen.py, harness/comparator.py, patch_engine.py).
  #       State (scos_state.py) and schema mining (schema_mine.py) are THIS skill's
  #       own scripts/ — none of this is in the jar.
  kit/              ← ScalaTest project rendered into Validation/tests/
    src/main/scala/            ← flat layout; package com.snowflake.scos.kit
      ScosTrialFixture.scala     ← BeforeAndAfterAll trait: Phase A =
                                   local[1] Spark + Delta; Phase B =
                                   SnowparkConnectSession + JDBC clone
      Helpers.scala              ← seedEntrypoint, captureResults,
                                   cloneGoldenSchemaForTrial,
                                   declaredSinkTables
      ReflectionEntrypoint.scala ← JVM reflection loader: loads the
                                   compiled workload JAR and invokes
                                   the nominated main method
      DatePin.scala              ← date/timestamp pinning helper
      # No shims: non-Spark I/O (dbutils, S3, HTTP, JDBC, secrets) is rewritten
      # by the patch blueprint, exactly like the PySpark validator.
    src/test/scala/
      KitSpec.scala              ← kit unit tests
    templates/
      TestTemplate.scala.tmpl    ← fill-in template for per-entrypoint specs
    build.sbt               ← ScalaTest + Spark + Delta + SCOS client +
                              workload JAR on classpath
    .gitignore.template
```

> **Note on the flat source layout.** The `.scala` files live directly under
> `src/main/scala/` (and `src/test/scala/`) rather than in the usual
> `com/snowflake/scos/...` package directories. sbt compiles every `.scala`
> file under the source root regardless of subdirectory, and each file keeps
> its original `package` declaration, so the flat layout is functionally
> identical — it just keeps install paths within the Windows MAX_PATH budget
> (see `scripts/audit-windows-path-length.py`).

## Control Plane (Python + a small analyze JAR)

The control plane is **Python**. Two scripts are **this skill's own**
(`$SKILL_DIRECTORY/scripts/`); the rest are **reused unchanged** from the sibling
PySpark validator at `../validate-pyspark-to-snowpark-connect/scripts/`
(`$VALIDATOR_SCRIPTS`):

This skill's own (Scala-specific):
- `scos_state.py` — state machine + workspace commands (init,
  select-entrypoints, scope-entrypoints, status, summary, build-index,
  record-*/mark-*, document-divergence, migrate-divergences, put-schemas, commit,
  patch-add, preflight, prewarm, build-doctor, run-phase-a, run-phase-b).
  `patch-add` imports the reused `patch_engine.py`.
- `schema_mine.py` — converts `analysis.json` into the `schemas/` layout (the
  Scala analog of the PySpark `schema_mine.py`).

Reused from the PySpark validator (`$VALIDATOR_SCRIPTS`, unchanged):
- `scos_state.py provision` / `cleanup.py` — provision the `schemas/` that `schema_mine.py`
  wrote (the **unchanged** PySpark provisioner); `cleanup.py` tears it down.
- `datagen.py schemas/ mock_data` — typed mock generation from `schemas/`.
- `harness/comparator.py compare` (looped per captured table) — pure-Python Phase A/B output comparison.

The **only** JVM piece left is the deterministic `analyze` command, which needs a
real Scala parser (Scalameta) with no Python equivalent. It compiles to a small
fat-JAR (~45 MB; circe + Scalameta only):

```
harness-scala/control/target/scos-analyze.jar
```

```bash
# Deterministic Scala source analysis (AST facts for the data-synthesizer agent)
java -jar $SKILL_DIRECTORY/harness-scala/control/target/scos-analyze.jar \
  analyze --source $CONVERSION_ROOT/Validation/source [--output <path.json>]

# Everything else is Python, e.g.:
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  init --conv-root $CONVERSION_ROOT --connection $CONNECTION_NAME \
       --original-source $ORIGINAL_SOURCE
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/schema_mine.py \
  --conv-root $CONVERSION_ROOT
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  provision --conv-root $CONVERSION_ROOT
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Divergence (comparator) |
| 2 | Partial failure (provisioner: some entrypoints failed) |
| 3 | Connection error |
| 4 | Missing required artifact (`summary` subcommand) |

### Building the control plane

```bash
cd harness-scala/control
sbt assembly
# Output (written directly): target/scos-analyze.jar
# Small fat-jar (~45 MB; circe + Scalameta only). Spark and the Snowflake JDBC
# driver are no longer bundled — those code paths moved to Python.
```

## Test Kit (`kit/`)

An sbt project copied into `Validation/tests/` at the start of each
validation run. The orchestrator (via `local-runner` and `scos-runner`
agents) renders one `Test<EpId>Spec.scala` per selected entrypoint from
`TestTemplate.scala.tmpl`.

### `ScosTrialFixture`

ScalaTest `BeforeAndAfterAll` trait that:

- **Phase A (`SCOS_FLAVOR=source`)**: builds a `SparkSession.master("local[1]")`
  with Delta Lake support and a per-trial temp warehouse dir.
- **Phase B (`SCOS_FLAVOR=migrated`)**: builds a
  `SnowparkConnectSession.builder().getOrCreate()` and clones the
  golden Snowflake schema for the trial via JDBC.

### `Helpers`

| Method | PySpark equivalent |
|--------|--------------------|
| `seedEntrypoint(spark, ep)` | `helpers.seed_entrypoint` |
| `captureResults(spark, ep, resultsDir)` | `helpers.capture_results` |
| `cloneGoldenSchemaForTrial(conn, ep, trial)` | `helpers.clone_golden_schema_for_trial` |
| `declaredSinkTables(ep)` | `helpers.declared_sink_tables` |

### `ReflectionEntrypoint`

Loads the compiled workload JAR via a `URLClassLoader`, resolves the
nominated class and method by name, and invokes it. Accepts optional
`extraJars` (Phase A thin-jar dependency classpath). This decouples the
kit from the workload's build tool — whether sbt, Maven, or Gradle,
the kit always works from the compiled JAR (fat assembly preferred; thin
jar + filtered classpath is a valid Phase A input).

```scala
ReflectionEntrypoint.invoke(
  jarPath     = analysis.jarPath,
  className   = analysis.entrypointClass,
  methodName  = analysis.entrypointMethod,
  args        = Array.empty[String]
  // extraJars = classpath entries from build-doctor / EXTRA_CLASSPATH
)
```

### `TestTemplate.scala.tmpl`

Fill-in template. Replace the following placeholders when rendering:

| Placeholder | Source |
|-------------|--------|
| `{{TRIAL_ID}}` | `analysis.json["entrypoints"][i]["id"]` |
| `{{ENTRYPOINT_CLASS}}` | `analysis.json[...]["entrypoint_class"]` |
| `{{ENTRYPOINT_METHOD}}` | `analysis.json[...]["entrypoint_method"]` |
| `{{ENTRYPOINT_ARGS}}` | `analysis.json[...]["entrypoint_args"]` (Array[String]) |
| `{{JAR_PATH}}` | `analysis.json[...]["jar_path"]` |
| `{{WIDGET_ENV_VARS}}` | `analysis.json[...]["widget_env_vars"]` |
| `{{PATH_REDIRECTS}}` | `analysis.json[...]["path_redirects"]` |

### Building and running the kit

```bash
# Copy the kit into Validation/tests/ (done by local-runner agent)
cp -R $SKILL_DIRECTORY/harness-scala/kit/. $CONVERSION_ROOT/Validation/tests/

# Phase A — local Spark + Delta
cd $CONVERSION_ROOT/Validation/tests
SCOS_FLAVOR=source \
SCOS_RESULTS_DIR=../results/phase_a \
SCOS_CONV_ROOT=$CONVERSION_ROOT \
sbt "testOnly *Test<EpId>Spec"

# Phase B — real SCOS
SCOS_FLAVOR=migrated \
SCOS_RESULTS_DIR=../results/phase_b \
SCOS_CONV_ROOT=$CONVERSION_ROOT \
sbt "testOnly *Test<EpId>Spec"
```

## Environment Variable Contracts

These env vars are honored by both the control plane and the test kit.
They are identical to the PySpark validator's contracts for
cross-tool consistency.

| Variable | Used by | Meaning |
|----------|---------|--------|
| `SCOS_FLAVOR` | kit | `source` (Phase A) or `migrated` (Phase B) |
| `SCOS_RESULTS_DIR` | kit | where to write Parquet snapshots + `_index.json` |
| `SCOS_CONV_ROOT` | kit | path to `Validation/` workspace |
| `SCOS_WIDGET_<NAME>` | rendered tests | widget value injected at runtime |
| `SPARK_REMOTE` | Phase B SCOS session | Snowflake account endpoint; overrides `SnowparkConnectSession` auto-resolution |
| `SNOWPARK_SUBMIT_JOB` | Phase B | set `=true` in sidecar mode |
| `SCOS_CLIENT_CLASS` | kit Phase B | Override the SCOS session class name used by `ScosTrialFixture` via JVM reflection (default: `com.snowflake.snowpark_connect.client.SnowparkConnectSession`). Set this if the SCOS client JAR renames the class. |
| `SCOS_SESSION_CLASS` | kit Phase B | Override the Snowflake session class name used by `ScosTrialFixture` (default: `com.snowflake.snowpark_connect.client.SnowflakeSession`). Set this if the SCOS client JAR renames the class. |

> **JVM environment limitation (important for the patch author).** These `SCOS_*`
> values are set through `EnvUtil` (an in-process override map + system
> properties). The JVM does **not** allow mutating the real process
> environment, so a workload that reads configuration directly via
> `System.getenv("X")` / `sys.env("X")` will **not** see harness-injected
> values — unlike Python's `os.environ` patching. The patch author must
> therefore handle Scala workloads that read env vars directly by one of:
> 1. rewriting `System.getenv`/`sys.env` reads to `EnvUtil.get` (or system
>    properties via `System.getProperty`), or
> 2. launching the workload in a **forked JVM** with the required environment
>    set before process start.
>
> `EnvUtil.setEnv` mirrors values into system properties, so
> `System.getProperty("X")` works in-process; `System.getenv("X")` does not.

## A/B Flow Summary

```
Step 0.5: preflight (scos_state.py preflight --phase a) — hard-fail JDK/sbt gaps
   ↓
Step 1: init (scos_state.py) / prepare-batches
   ↓
Step 1.5: prewarm (scos_state.py prewarm) — stage kit + warm sbt/Coursier [background;
          join before Phase A; never after adapt]
   ↓
Step 2: analyze (data-synthesizer.md) → analysis.json + mock_data/ (all EPs by default;
          optional orchestrator scope-entrypoints; self-verifies: --verify + column_check)
   ↓
Step 3: adapt (patch-author.md) → patch I/O blueprint; compile Output JAR;
          record entrypoint_class/jar_path
   ↓
Step 3.5: build-doctor (scos_state.py build-doctor) — prove source jar (assembly or
          thin jar + filtered dependency classpath); no tests
   ↓
Step 4 / Phase A: local-runner.md / run-phase-a
  │  mock-data guard hard-fails on unseedable mocks
  │  sbt test (SCOS_FLAVOR=source; EXTRA_CLASSPATH for thin jars)
  │  local Spark + Delta (no Snowflake)
  └→ Parquet baselines in results/phase_a/<trial_id>/
   ↓
Step 5: provision (schema_mine.py → scos_state.py provision) → golden Snowflake schemas
   ↓
Step 6 / Phase B: scos-runner.md / run-phase-b
  │  sbt test (SCOS_FLAVOR=migrated)
  │  SnowparkConnectSession (real SCOS)
  │  clone golden schema per trial
  │  comparator.py compare (per-table loop) → exit 0/1/2
  └→ results/phase_b/<trial_id>/ + REPORT.md
   ↓
Step 7: summary (scos_state.py) → run_index.json + events.jsonl (+ auto-compare)
   ↓
Step 8: harvest (scos_state.py) → cherry-pick [MIGRATION-FIX] onto deliverable
   ↓
Step 9: cleanup (cleanup.py) [optional, user-gated]
```

## JVM vs Python: Key Differences

| Concern | Python harness | Scala harness |
|---------|---------------|---------------|
| Module loading | `importlib.import_module` + `sys.path` | `URLClassLoader` + JVM reflection (`ReflectionEntrypoint`) |
| Non-Spark I/O | rewritten by the patch blueprint (no shims) | rewritten by the patch blueprint (no shims) |
| Date pinning | `F.current_date` monkey-patch | `SparkConf` + `SparkListener` |
| Widget injection | `os.environ` + test fixture | `System.setProperty` / `os.environ` in rendered test |
| venv / deps | `uv run --project` + `.venv` | sbt dependency resolution + local Maven cache |
| Notebook flatten | `notebook_io.flatten_cells_to_script(target_language="python")` | `notebook_io.flatten_cells_to_script(target_language="scala")` |

## Notes for Agents

- **data-synthesizer**: scan `.scala` files for column refs using `col("x")`,
  `$"x"`, `.select(...)`, `.groupBy(...)` patterns (the data-synthesizer also
  self-verifies via column_check — there is no separate critic agent).
  Do NOT use Python patterns (`df["col"]`, `F.col(...)`). Promote **all**
  surveyed candidates; never ask which entrypoints to validate.
- **patch-author**: always compile the migrated workload to a JAR before
  authoring the test spec. The kit depends on the JAR, not on raw source.
  Prefer assembly for Phase B; Phase A may use thin jar + classpath via
  `build-doctor`.
- **local-runner / scos-runner**: use `sbt test` (or `sbt "testOnly ..."`).
  Do NOT use `pytest`. There are no `.py` test files in this skill. Run
  `build-doctor` before Phase A; mock/schema gaps → repair; dialect-only →
  `phase_a_skipped --reason <construct>`.
- **No shims**: there are no JVM shims or mock filesystems. A missing class /
  unsupported non-Spark call means an un-patched I/O dependency — add a
  `scos_state.py patch-add` rewrite to native Spark / env reads, then re-run.
