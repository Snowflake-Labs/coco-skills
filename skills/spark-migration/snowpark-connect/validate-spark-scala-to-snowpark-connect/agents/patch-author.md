---
name: patch-author
description: Make the workload runnable under the JVM harness — author the I/O patch blueprint (cloud reads/writes, dbutils, secrets, widgets, env reads), create wrapper objects / flatten notebooks, and compile the migrated workload to a JAR. Patches are smoke-tested and committed via `scos_state.py patch-add`.
---

# Patch Author

This agent prepares the patch blueprint — the small, auditable set of
search/replace edits that make the workload runnable under the harness without
any shims or mock filesystems. Non-Spark I/O (cloud reads/writes, `dbutils`,
secrets, widgets, external deps) is never shimmed or mocked — it is **rewritten
to native Spark** (reading/writing `System.getProperty("SCOS_INPUT_<ID>")` /
`("SCOS_SINK_<ID>")`), turned into an **inline literal** (secrets, widgets), or
**deleted** (mount guards, logging/telemetry side effects).

Patches are **validation plumbing only** — I/O rewrites, namespace rebinds, path
redirects, dead import removal. **Do not rewrite SQL dialect** (`QUALIFY`,
`DATEADD`, etc.) or other SCOS migration logic here; Phase B's migration fixer
commits those as `[MIGRATION-FIX]` on `Output/`. Patch a Scala file only when
the harness must redirect where the workload loads data or reads config.

## Inputs

- `Validation/shared/schemas/` (SoT — edit `_meta.json` for harness fields)
- `Validation/shared/analysis.json` (generated JVM shim — do not hand-edit)
- `Validation/source/`
- `Output/`

## Output

Update `schemas/entrypoints/<id>/_meta.json` with the exact fields
`TestTemplate.scala.tmpl` needs (`entrypoint_class`, `entrypoint_method`,
`cli_args`, …). `jar_path` / `build_tool` may live on the shared shim
(`scala_meta.json` / analysis); prefer `_meta.json` for per-EP fields.
Make the smallest source edits needed to support them. Prevalidate
regenerates `analysis.json` from schemas.

## Cases

### Case A: already a standard main object

If the file exposes `object X { def main(args: Array[String]): Unit }`
or `object X extends App`, no source edits are needed.

Record:

```json
{
  "entrypoint_class": "com.example.X",
  "entrypoint_method": "main",
  "build_tool": "sbt"
}
```

and move on.

### Case B: top-level script body (no main object)

If the `.scala` file has executable statements at the top level without
a wrapping `object`, create a thin wrapper object that invokes the
body:

```scala
object ValidationEntrypoint extends App {
  // include or inline the top-level body here
}
```

Place the wrapper alongside the original file in the copied source tree.
Do not modify the original source directly.

### Case C: Databricks / Scala notebook

If the workload is a Databricks notebook (`.ipynb` with Scala kernel,
Databricks-native `.scala` / `.sql` JSON, Databricks exported `.scala`):

1. Flatten to a `.scala` script using `notebook_io.flatten_cells_to_script`
   with `target_language="scala"` (stdlib-only; call with `python3`, not
   `uv run --project`):

   ```bash
   python3 -c "
   import sys
   sys.path.insert(0, '<SKILL_DIRECTORY>/../scripts')
   from notebook_io import flatten_cells_to_script
   script = flatten_cells_to_script('<notebook_path>', target_language='scala')
   open('<output_path>_converted.scala', 'w').write(script)
   "
   ```

2. Wrap the flattened `.scala` body in a main object:

   ```scala
   object ValidationEntrypoint extends App {
     // ===== BEGIN FLATTENED NOTEBOOK CELLS =====
     // paste body of *_converted.scala here
     // Non-Scala cells are already // commented out
     // ===== END FLATTENED NOTEBOOK CELLS =====
   }
   ```

3. Record `entrypoint_class: "ValidationEntrypoint"` and
   `entrypoint_method: "main"`.

4. Cross-language cells (Python cells delegated to the Python fixer
   during migration) appear as `//`-commented lines in the flattened
   output. Do NOT re-execute them here.

### Case D: scopt / CLI argument-driven entrypoint

If the entrypoint only works when `args: Array[String]` contains
specific values (flags, config paths, etc.):

- Record the default args needed for a no-op or minimal test run in
  `cli_args[]` (a string array). **IMPORTANT: the field name is `cli_args`, NOT `entrypoint_args`** — the spec renderer reads `ep["cli_args"]`; writing `entrypoint_args` silently produces `Array.empty[String]` and the workload crashes on missing required arguments.
- Do not rewrite the argument parser.

### Case E: multi-file sbt / Maven / Gradle project

1. Detect the build tool from `schemas/scala_meta.json` → `build_tool`
   (or `schemas/manifest.json` → `summary.build_tool`).
2. Verify the project compiles and produces a JAR:
   - **Prefer fat/assembly** when the plugin is present:
     - **sbt**: `sbt assembly` → `target/scala-*/…-assembly*.jar`
     - **Maven**: `mvn package -DskipTests` → shaded / `*-jar-with-dependencies.jar`
     - **Gradle**: `./gradlew shadowJar` → `build/libs/*.jar`
   - **Thin jar is valid for Phase A** when assembly/shadow is missing: `sbt package`
     / `mvn package` / `./gradlew jar` plus the filtered dependency classpath that
     `scos_state.py build-doctor` / `run-phase-a` export into `EXTRA_CLASSPATH`.
     Do **not** treat a missing assembly plugin as a hard failure when a thin jar
     exists — Phase A loads it via `ReflectionEntrypoint` + extra jars. (Phase B
     still expects an Output assembly from this agent when possible.)
3. Record `jar_path` relative to `CONVERSION_ROOT` in
   `schemas/scala_meta.json` (shim refresh picks it up).
4. The kit will load entrypoints from this JAR via `ReflectionEntrypoint`.

If no fat-JAR plugin is configured, you may add the minimal assembly config to
the build file (e.g., `addSbtPlugin("com.eed3si9n" % "sbt-assembly" %
"2.2.0")` to `project/plugins.sbt` for sbt) — preferred for Phase B Output —
but Phase A can proceed with thin jar + classpath without that plugin.

## Step 0 — Exhaustive I/O pre-scan (run before any patching)

**Prefer the Scala-native known-patches sweep first** (PySpark parity). It writes
confident auto-patches plus a residual investigation worklist — do not run
PySpark `patch_engine` detectors on `.scala` (they emit Python rewrites).

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  known-patches suggest --conv-root $CONVERSION_ROOT
```

Artifacts:
- `Validation/shared/known_patch_suggestions.json` — apply via `patch-add --from-file`
- `Validation/shared/patch_investigation.json` — work the residual sites by category
- Seeds `expected_divergences` with `scope=udf` when AST UDFs are present

Then apply suggestions and finish any residual sites from the investigation file.
Also run the greps below against BOTH `Validation/source/src` and `Output/src`
to catch anything the detectors missed.

```bash
# 1. Cloud URI literals in spark.read / spark.write (most common re-run cause)
grep -rn '"s3://\|"s3a://\|"gs://\|"abfss://\|"wasbs://\|"wasb://\|"hdfs://\|"dbfs:/' \
  Validation/source/src Output/src 2>/dev/null \
  | grep -v "//\s*SCOS_INPUT\|System\.getProperty" \
  | grep -v "\.scala:[0-9]*:.*//.*suppress"

# 2. JDBC / external-DB reads
grep -rn 'spark\.read\.format("jdbc\|\.option("url"\|\.option("driver"\|\.jdbc(' \
  Validation/source/src Output/src 2>/dev/null \
  | grep -v "//\s*SCOS_INPUT\|System\.getProperty"

# 3. Hard-coded CSV / parquet / JSON / ORC paths not yet redirected to SCOS_INPUT
grep -rn 'spark\.read\.\(csv\|parquet\|json\|orc\|text\|format\)' \
  Validation/source/src Output/src 2>/dev/null \
  | grep -v 'System\.getProperty("SCOS_INPUT\|//\s*TEST-PATCH'

# 4. Delta / Iceberg table loads via hard-coded paths
grep -rn '\.format("delta"\|\.format("iceberg"\|DeltaTable\.forPath\|DeltaTable\.forName' \
  Validation/source/src Output/src 2>/dev/null \
  | grep '"[^"]*://' \
  | grep -v 'System\.getProperty'

# 5. SparkContext / RDD file reads (incompatible with Spark Connect Phase B)
grep -rn 'new SparkContext\|sc\.textFile\|sc\.hadoopFile\|sc\.sequenceFile\|sc\.objectFile\|getOrCreate.*SparkContext' \
  Output/src 2>/dev/null \
  | grep -v "//\s*SCOS_INPUT\|//\s*TEST-PATCH"

# 6. S3/HDFS URIs hidden inside env var defaults (missed by spark.read grep)
grep -rn '"s3://\|"s3a://\|"gs://\|"abfss://\|"hdfs://' \
  Validation/source/src Output/src 2>/dev/null \
  | grep 'getOrElse\|getProperty\|System\.getenv\|sys\.env' \
  | grep -v 'System\.getProperty("SCOS_INPUT'

# 7. Dynamic path builders — helper methods that receive cloud paths as arguments
grep -rn '"dbfs:/\|"s3://\|s"dbfs:\|s"s3:' \
  Validation/source/src Output/src 2>/dev/null \
  | grep -v "spark\.read\|spark\.write\|//\s*SCOS"
```

**For every hit:** create a patch blueprint entry (or a per-side patch) using the I/O
rewrite table below. Do not proceed to the Databricks API scan or JAR compilation
until every hit in the pre-scan above either has a patch entry or is explicitly
dismissed with a note (e.g. already redirected, unreachable code path, dead import).

The pre-scan output is your complete patch work-list. Fix the entire list in one
patch-author pass rather than discovering items one at a time during Phase A trial
runs.

**Exit gate (batch-runner):** after this agent records `patches_authored` /
`workload_built`, the batch-runner runs `scos_state.py prevalidate --phase a`
(and later `--phase b`). That command re-runs the I/O completeness grep and folds
findings into `Validation/shared/prevalidation_report.json`. If prevalidate
reports blockers, return here and **batch-fix every finding in one pass** from
the report (and `patch_investigation.json` / `known_patch_suggestions.json` when
present). Honor `rebuild_required` on each finding:

- `rebuild_required: false` (mock / schema / `cli_args` / analysis) → fix JSON
  only; **do not** rebuild JARs
- `rebuild_required: true` (I/O code patches, compile, classpath) → apply all
  code patches first, then rebuild **at most once**, then re-run
  `prevalidate --force`

Do not rediscover one error per trial iteration, and do not rebuild per finding.

## Mandatory Databricks API scan

**Before authoring any patches, run the deterministic scans below.** A successful
`sbt assembly` does NOT prove the workload is harness-safe — `dbutils` and other
Databricks APIs compile fine (the Databricks JAR is on the classpath) but NPE at
runtime in a non-Databricks JVM. Every hit must become a patch entry.

**Date determinism scan (run first):**

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
uv run --project $SKILL_DIRECTORY/.. python \
  $SKILL_DIRECTORY/scripts/scan_date_calls.py \
  --conv-root $CONVERSION_ROOT \
  --output /tmp/date_patches.json
# If non-zero (hits found), apply immediately:
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  patch-add --conv-root $CONVERSION_ROOT --from-file /tmp/date_patches.json
```

Then grep both source trees for remaining Databricks-specific APIs:

```bash
# Run against BOTH trees
grep -rn "dbutils\." Validation/source/src Output/src
grep -rn "displayHTML\|\.display()" Validation/source/src Output/src
grep -rn "System\.getenv\|sys\.env" Validation/source/src Output/src
```

### Auto-generate `sys.env` → `System.getProperty` patches

**Do this first**, before hand-authoring any per-file entries. The migration
skill's Phase 3 (`update_imports_scala.py`) rewrites `sys.env` calls deterministically
in `Output/`, but `Validation/source/` (the Phase A copy) is not touched by migration.
Apply these glob patches via a single `patch-add` call so both sides stay in lockstep:

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
cat > /tmp/sys_env_patches.json << 'EOF'
{"patches": [
   {"id": "sys_env_get_or_else",
    "relative_file": "src/**/*.scala",
    "regex": true, "replace_all": true,
    "note": "sys.env.getOrElse -> System.getProperty (JVM cannot inject via System.getenv)",
    "search": "sys\\.env\\.getOrElse\\(\\s*(\"[^\"]*\")\\s*,\\s*(\"[^\"]*\")\\s*\\)",
    "replace": "System.getProperty(\\1, \\2)"},
  {"id": "sys_env_get",
   "relative_file": "src/**/*.scala",
   "regex": true, "replace_all": true,
   "note": "sys.env.get -> Option(System.getProperty(...))",
   "search": "sys\\.env\\.get\\(\\s*(\"[^\"]*\")\\s*\\)",
   "replace": "Option(System.getProperty(\\1))"},
  {"id": "sys_env_direct",
   "relative_file": "src/**/*.scala",
   "regex": true, "replace_all": true,
   "note": "sys.env(\"K\") -> System.getProperty(\"K\")",
   "search": "sys\\.env\\(\\s*(\"[^\"]*\")\\s*\\)",
   "replace": "System.getProperty(\\1)"},
  {"id": "system_getenv_to_property",
   "relative_file": "src/**/*.scala",
   "regex": true, "replace_all": true,
   "note": "System.getenv(\"K\") -> System.getProperty(\"K\") — EnvUtil injects via setProperty only",
   "search": "System\\.getenv\\(\\s*(\"[^\"]*\")\\s*\\)",
   "replace": "System.getProperty(\\1)"},
   {"id": "current_date_to_pinned_lit",
    "relative_file": "src/**/*.scala",
    "regex": true, "replace_all": true,
    "note": "functions.current_date() -> deterministic lit: Catalyst inlines CurrentDate before any listener fires; replace with a Column literal reading from the system property DatePin.install() publishes",
    "search": "functions\\.current_date\\(\\)",
    "replace": "functions.to_date(functions.lit(System.getProperty(\"SCOS_PINNED_DATE\", java.time.LocalDate.now().toString)))"},
   {"id": "current_timestamp_to_pinned_lit",
    "relative_file": "src/**/*.scala",
    "regex": true, "replace_all": true,
    "note": "functions.current_timestamp() -> deterministic lit — same Catalyst inlining problem",
    "search": "functions\\.current_timestamp\\(\\)",
    "replace": "functions.to_timestamp(functions.lit(System.getProperty(\"SCOS_PINNED_TIMESTAMP\", java.time.LocalDate.now().toString + \" 00:00:00\")))"}
]}
EOF
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  patch-add --conv-root $CONVERSION_ROOT --from-file /tmp/sys_env_patches.json
```

If `patch-add` reports zero matches for a given entry (the workload has no `sys.env` calls),
that is fine — skip it. If it reports matches, check the diff to confirm no string literals
were accidentally rewritten.

> **Note on Output/ side:** Phase 3 already rewrote `sys.env` in `Output/` during migration.
> The `migrated` side of the patch may be a no-op (0 matches) or may catch any Phase-3 misses.
> Either outcome is correct.

> **Patches are file-specific — 0 matches is not a bug.** A pattern present in one
> file need not be forced onto its siblings. If a file already has the offending
> call removed, commented out, or absent (an already-migrated line, a
> `dbutils`/`os.system`-style side effect that isn't there), do **not** author a
> duplicate removal patch just to mirror another file. Let the glob's zero-match
> skip handle it.

For each hit, apply the corresponding rewrite from the table below. If the
same pattern appears in many files, write **one glob entry** (see "Collapsing
repeated patches").

### I/O rewrite table

| Original (Scala) | Patch `replace` |
|---|---|
| `spark.read...load("s3://…")` / `"dbfs:/…"` / `"wasbs://…"` (hardcoded cloud URI) | `spark.read.<fmt>(System.getProperty("SCOS_INPUT_<ID>"))` |
| `df.write...save("s3://…")` / non-table sink | `df.write...save(System.getProperty("SCOS_SINK_<ID>"))` |
| `dbutils.fs.mounts.map(_.mountPoint).contains(x)` | `false` |
| `dbutils.fs.mount(...)` (any form, single- or multi-line) | `()` |
| `dbutils.fs.unmount(...)` | `()` |
| `dbutils.fs.ls(...)` | `Seq()` |
| `dbutils.fs.mkdirs(...)` | `true` |
| `dbutils.fs.cp(...)` | `true` |
| `dbutils.fs.rm(...)` | `true` |
| `dbutils.fs.refreshMounts()` | `true` |
| `dbutils.secrets.get(scope = scope, key = key)` | `System.getProperty(s"SCOS_SECRET_$${key.toUpperCase.replace("-","_")}", s"test_secret_$$key")` |
| `dbutils.secrets.get(scope, key)` (positional) | same as above |
| `dbutils.widgets.get("x")` | inline string literal matching entrypoint default, e.g. `"dev"` |
| `dbutils.notebook.exit(...)` | `System.exit(0)` |
| `dbutils.notebook.run(...)` | empty-string literal `""` — use `"replace": "\"\""` in the patch (it returns a `String`; `"replace": ""` means *delete*, which breaks `val x = dbutils.notebook.run(...)`) |
| `display(df)` / `df.display()` (Databricks notebook viewer) | delete (`replace: ""`), or if the data-synthesizer flagged `display_only: true`, rewrite to `df.write.mode("overwrite").parquet(System.getProperty("SCOS_SINK_DISPLAY_<N>"))` |
| `displayHTML(...)` | delete (`replace: ""`) — always |
| `System.getenv("X")` | `System.getProperty("X")` |
| `sys.env("X")` / `sys.env.get("X")` | `System.getProperty("X")` (or `Option(System.getProperty("X"))` for `.get`) |
| `spark.read.format("snowflake").option("query"/"dbtable", …).load()` | per-side patch — see "Connector reads" below |
| `spark.read.format("jdbc"/"redshift").option(…).load()` | per-side patch — see "Connector reads" below |
| `spark.read.parquet/csv/json/orc(System.getProperty("SCOS_INPUT_<ID>"))` (file-source read) | **per-side patch** on `migrated` side only — see "File-source reads" below |
| Non-Spark JVM file reader (`scala.io.Source.fromFile(path)`, `java.nio.file.Files.readAllBytes(Paths.get(path))`, `new java.io.FileInputStream(path)`, etc.) | Guard at the top of the read: `val _scosIn = Option(System.getProperty("SCOS_INPUT_<ID>")).filter(_.nonEmpty); val path = _scosIn.getOrElse(originalPath)`, then feed the redirected path into the reader |
| `df.write…parquet/csv(System.getProperty("SCOS_SINK_<ID>"))` (file sink, `migrated` side) | **per-side patch** — see "File-category sinks in Phase B" below |

Table-form reads/writes that already run on the session
(`spark.table(...)`, `saveAsTable(...)`) need no patch.

> **⚠ `dbutils.notebook.exit(...)` → `System.exit(0)` can kill output capture.**
> The kit runs each trial in a **forked test JVM** and captures outputs (tables +
> file sinks) **after** the entrypoint returns. The JVM has no `System.exit`
> interception, so a `System.exit(...)` reached mid-run terminates the fork
> **before** capture — the trial loses its baseline (shows up as a failed capture /
> `passed_no_baseline`, not a real result). This is safe **only** when the exit is
> genuinely the last thing the workload does. If `dbutils.notebook.exit` sits behind
> an early-exit guard (`if (cond) dbutils.notebook.exit(...)`) that still has real
> Spark work after it, do **not** map it to `System.exit(0)` — instead choose
> `entrypoint_kwargs` / widget values that bypass the guard, or delete the guarded
> exit, so the workload runs to completion and its outputs are captured. (A general
> JVM exit-interception mechanism is a tracked follow-up; until it lands, keep
> terminating rewrites off any path that precedes a sink.)

### Dead imports after deletion

When `import com.databricks.dbutils_v1.DBUtilsHolder.dbutils` (or any
Databricks import) is left at the top of a file after all its call sites are
deleted, the import itself must be patched out (`replace: ""`). Blueprint
patches apply to `Validation/source/` as well as `Output/` — do not leave
dead imports that cause IDE warnings or potential future compile errors when
the Databricks JAR is removed from the classpath.

### Intermediate file re-reads

When the workload writes a file-format intermediate (e.g. Parquet, Delta)
and later re-reads it in the same run, BOTH the write and the subsequent
read need path-redirect patches (`SCOS_SINK_<ID>` on the write,
`SCOS_INPUT_<ID>` on the re-read). Without redirection the pipeline breaks
at the re-read step.

### Helper-method-hidden file reads (path buried in a utility/helper class)

The `SCOS_INPUT_*` redirect also does **NOT** automatically apply when
`spark.read` is called inside a utility or helper method that receives a
dynamically-built DBFS/cloud path (e.g. `datalake.load()`, `Bronze.loadDataFile()`,
`Read.asDf(location = getBronzeLocation(banners))`). A simple grep of the entrypoint
file finds no `dbfs:/` literal, yet the workload reads live cloud paths at runtime.

**Detection step**: After the literal-URI scan, run:

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
# Find path-building helpers that emit cloud URIs
grep -rn '"dbfs:/\|"s3://\|s"dbfs:\|s"s3:' Validation/source/src Output/src \
  | grep -v "spark\.read\|spark\.write\|//\s*SCOS"
```

For each hit, trace which external source table under
`schemas/entrypoints/<id>/tables/` it corresponds to,
then add a guard at the **top of the helper that does the actual cloud read**,
immediately after any `metaData`/parameter setup and before the cloud call:

```scala
// ADD after metaData/setup, before the cloud read:
val _scosInput = Option(System.getProperty("SCOS_INPUT_<ID>")).filter(_.nonEmpty)
if (_scosInput.isDefined) {
  val _mockDf = spark.read.parquet(_scosInput.get)
  return (_mockDf, metaData)  // match the helper's return type
}
```

Replace `<ID>` with `src.id.toUpperCase.replaceAll("[^A-Z0-9]", "_")` from the
matching schemas table `_table_key` / `id`. Apply to **both** `Validation/source/`
and `Output/` copies. For secondary reads within the same method (e.g. a Delta
metadata read via `Read.asDf(location = "dbfs:...")`):

```scala
val previousPathsDf: DataFrame =
  Option(System.getProperty("SCOS_INPUT_FILE_METADATA_DELTA")).filter(_.nonEmpty)
    .map(p => spark.read.parquet(p))
    .getOrElse(Read.asDf(location = location, schema = Some(FILE_METADATA_SCHEMA)))
```

### Indirect file reads (env var whose default is an S3/HDFS URI)

The `SCOS_INPUT_*` redirect applies to `spark.read.csv("s3://...")` literals.
It does **NOT** automatically apply when the S3 path is hidden inside an
environment variable default:

```scala
// Example — the S3 path is invisible to a simple grep of spark.read calls:
val rawPath = sys.env.getOrElse("FARECARD_RAW_TAPS_PATH", "s3://farecard-raw/taps/") + runDate
val df = spark.read.option("header", "true").csv(rawPath)   // ← no S3 literal here
```

**Detection step**: After running the mandatory `spark.read...load("s3://...")` scan,
also grep for S3/HDFS URIs in env var defaults:

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
grep -rn '"s3://\|"s3a://\|"gs://\|"abfs://\|"wasb://\|"hdfs://' Validation/source/src Output/src \
  | grep "getOrElse\|getProperty\|System\.getenv\|sys\.env"
```

For each hit, trace the variable to its `spark.read` call and apply the
`SCOS_INPUT_<ID>` redirect to the **env var line**, not the `spark.read` line:

```json
{"id": "raw_taps_scos_input",
 "relative_file": "src/main/scala/.../TapEventLoader.scala",
 "note": "Redirect S3 CSV read to SCOS_INPUT so harness can inject staged path",
 "migrated": {
   "search": "sys.env.getOrElse(\"FARECARD_RAW_TAPS_PATH\", \"s3://farecard-raw/taps/\")",
   "replace": "System.getProperty(\"SCOS_INPUT_RAW_TAPS\", \"\")"
 },
 "source": {
   "search": "System.getProperty(\"FARECARD_RAW_TAPS_PATH\", \"s3://farecard-raw/taps/\")",
   "replace": "System.getProperty(\"SCOS_INPUT_RAW_TAPS\", \"\")"
 }}
```

Also register the source under
`schemas/entrypoints/<id>/tables/<KEY>.json` as `category: "file"` with a
`mock_file` reference so the provisioner can stage it:

```json
{"_table_key": "src_raw_taps",
 "access": "read",
 "name": "raw_taps",
 "category": "file",
 "mock_file": "raw_taps.parquet",
```

### DataLake / Blob / S3 / SFTP read interception (`DataLake.load()`)

Framework workloads that use a `DataLake` abstraction (`Blob`, `S3`, `SFTP` subclasses
via `getPartnerDataLake(...)`) never call `spark.read` directly — the call is buried
inside `DataLake.load(path, fileType, ...)`. The `SCOS_INPUT_*` redirect does NOT fire
automatically; you must add a bypass at the TOP of each concrete `load()` method body
(before the `fileType match` dispatch):

```scala
// ADD at the top of Blob.load() / S3.load() / SFTP.load() body:
val _scos_dl_cap = Option(System.getProperty("SCOS_CAPTURE_DIR"))
                    .orElse(Option(System.getenv("SCOS_CAPTURE_DIR")))
                    .filter(_.nonEmpty).getOrElse("")
if (_scos_dl_cap.nonEmpty) {
  val _scos_dl_mock = Seq(
    "SCOS_INPUT_SRC_DYNAMIC_LOAD_1", "SCOS_INPUT_DATALAKE_MOCK",
    "SCOS_INPUT_DATALAKE_1",          "SCOS_INPUT_DELTA_MOCK"
  ).flatMap(k => Seq(
    Option(System.getProperty(k)).filter(_.nonEmpty),
    Option(System.getenv(k)).filter(_.nonEmpty)
  ).flatten).headOption.getOrElse("")
  if (_scos_dl_mock.nonEmpty) {
    return org.apache.spark.sql.SparkSession.builder().getOrCreate().read.parquet(_scos_dl_mock)
  }
}
```

Use `SCOS_CAPTURE_DIR` (always set by the subprocess harness) as the sentinel rather
than checking `new java.io.File(path).exists()` — the file-existence check can fail if
the mock path contains spaces or the subprocess working directory differs.

Apply to `Blob.scala`, `S3.scala`, `SFTP.scala` on both `Validation/source/` and
`Output/` sides. The harness sets `SCOS_INPUT_SRC_DYNAMIC_LOAD_1` automatically from
`schemas/` file-category tables with a `mock_file` that exists
under `Validation/shared/mock_data/<ep_id>/`.

**Container fields for `FileProperties`**: when `getPartnerDataLake` validates the
container map, it requires `category`, `region`, and `provider` in addition to `name`,
`storageAccount`, and `storage`. The `cli_args` stub for `files` in
`schemas/entrypoints/<id>/_meta.json` must include all these fields:

```json
"container": {
  "name": "__scos_mock__",
  "storageAccount": "__scos_mock__",
  "storageAccountAccessKey": "__scos_mock__",
  "region": "__scos_mock__",
  "provider": "azure",
  "storage": "blob",
  "category": "partner-data"
}
```

### Staging CLI args: `Args.getFileProperties()`, `getStageProperties()`, `getSyntheticDataProperties()`, `getDatabaseProperties()`, `getTaxProperties()`

Flashfood-style `Args.*` wrappers use **lift-json** extraction, which does **NOT** use
Scala default parameter values. Every field in the target case class must be present
in the JSON — omitting any field throws `MappingException` wrapped as `ArgumentException`
before the workload's I/O code is ever reached.

Author `cli_args` stubs in `schemas/entrypoints/<id>/_meta.json` for every detected `Args.*` wrapper
call (from the Step 0 I/O / AST pre-scan). Use `__scos_mock__` as placeholder
strings only temporarily — replace them with values that match the
workload's validation:

| Wrapper call | `cli_args` key | Stub value | Notes |
|---|---|---|---|
| `getFileProperties()` | `files` | `[{...all fields...}]` | `container.category` must match `CONTAINER_GROUP_MAPPINGS_REVERSED`; adjust `provider`, `storage`, `region` per env |
| `getStageProperties()` | `stageTransform` | `[]` | Empty = no transforms; workload reads & writes data unchanged |
| `getSyntheticDataProperties()` | `files` | `[{counts:0,...}]` | `counts=0` skips the generation for-loop body → clean exit |
| `getDatabaseProperties()` | `databaseProperties` | `[]` | Empty list = no DB write attempted |
| `getTaxProperties()` | `taxes` | `{inputTaxRateDatabaseProperties:{...},outputTaxesDatabaseProperties:[]}` | All `DatabaseProperties` fields required, including `category`, `optionalParams` |

### Complex staging pipeline bypass (`Bronze`, `Silver`, `Gold`, `ImageTransform`)

When a workload's Phase A baseline requires running `Bronze.transform()`,
`Gold.transform()`, or similar multi-stage pipelines whose schemas diverge from the
mock data schema (gold schema ≠ bronze input schema), add a short-circuit bypass
directly inside `run()` — before the stage `getStage()` / `transform()` call — that:

1. Reads the mock goldDf from `Read.asDf()` (already redirected by delta mock intercept)
2. Writes it via `Write.dfToDelta()` (already redirected to `SCOS_CAPTURE_DIR`)
3. Returns early with `this.state = FINISHED`

```scala
// ADD in run() before val bronze = new Bronze(argsObj) / gold.getStage() etc:
val _scos_bypass = System.getProperty("SCOS_CAPTURE_DIR", "")
val _scos_out    = System.getProperty("SCOS_OUTPUT_SCHEMA", "")
if (_scos_bypass.nonEmpty || _scos_out.nonEmpty) {
  val _mockDf = com.flashfood.petl.load.deltalake.Read.asDf(
    location = getGoldLocation(banners = banners))
  com.flashfood.petl.load.deltalake.Write.dfToDelta(
    _mockDf, location = getGoldLocation(banners = banners),
    mode = org.apache.spark.sql.SaveMode.Overwrite)
  this.state = com.flashfood.petl.TaskState.FINISHED
  return
}
```

Use this pattern whenever:
- The stage relies on `TOTAL_NUMBER_OF_AVAILABLE_CORES` (= 0 in local Spark → `repartition(0)` crash)
- The stage joins multiple delta sources whose mock schemas are not aligned
- The stage reads from production DBs (`ReadDatabase.read()`, MongoDB, JDBC) that have
  no local equivalent

For **true no-sink entrypoints** (`sinks=[]` / no write-access tables in
`schemas/` **and**
`ast_facts.json` shows no writes / write_helpers / unresolved_writes for that
path) whose pipeline only touches unavailable production infra, an early return
under harness env is acceptable so the trial records a clean exit. The harness
then records `no_sink_baseline` and Phase B may smoke-pass with nothing to
compare.

If AST still shows writes but `sinks=[]`, that is an **analysis bug** — re-mine
sinks (or add `SCOS_SINK_*` / `saveAsTable` remaps). Do **not** early-return to
force a fake no-sink pass.

If no local mock exists (the cloud data is real production data), record the
entrypoint as `requires_real_data` in a `notes` field and mark it `file_only`:
the entrypoint cannot be validated without actual data staged in Snowflake.
Leave it `pending` / document for human review — do not invent
`passed_no_baseline` from a capture failure.

## Date-Determinism Warning

Workloads that call `org.apache.spark.sql.functions.current_date()` or
`functions.current_timestamp()` **directly** (i.e., not through
`spark.sql("SELECT current_date")`) are **not affected by the harness's
date-pinning mechanism** in Phase A.

Catalyst inlines `current_date()` / `current_timestamp()` as `CurrentDate` /
`CurrentTimestamp` expressions during analysis — before any listener-level rewrite
runs — making the resulting dates non-deterministic between Phase A and Phase B
runs, causing **spurious A/B divergences** that are not real migration defects.

**Required action**: the auto-glob patches above (`current_date_to_pinned_lit` and
`current_timestamp_to_pinned_lit`) rewrite the `functions.`-qualified form
automatically. The replacement reads `SCOS_PINNED_DATE` / `SCOS_PINNED_TIMESTAMP`
from `System.getProperty` — both of which `DatePin.install()` writes before each
trial — and falls back to today's date when unset. No kit dependency or `spark`
reference is required in workload code.

**Detection scope limitation**: the auto-glob only catches `functions.current_date()`.
The most common Scala idiom — wildcard import (`import functions._`) followed by
bare `current_date()`, or an aliased import `F.current_date()` — is **not matched**
by the auto-glob and must be found manually:

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
# All three forms — qualified, aliased, bare
grep -rn "functions\.current_date()\|functions\.current_timestamp()" Validation/source/src Output/src
grep -rn "\bcurrent_date()\|\bcurrent_timestamp()" Validation/source/src Output/src \
  | grep -v "functions\."   # show only bare / aliased hits
```

For any bare / aliased hit, author a targeted per-file patch using the same
`functions.to_date` / `functions.to_timestamp` form, replacing the bare call with
`to_date(lit(System.getProperty("SCOS_PINNED_DATE", java.time.LocalDate.now().toString)))`.
After applying all patches, the verification grep should return 0:

```bash
grep -rn "\bcurrent_date()\|\bcurrent_timestamp()\|functions\.current_date()\|functions\.current_timestamp()" \
  Validation/source/src Output/src
# → should return 0 results
```

After all patches are applied, confirm no `expr("scos_pinned_*")` calls leaked in:

```bash
# MUST return 0 results — expr("scos_pinned_*") causes Phase B to fail with SCOS ERROR CODE: 4001
grep -rn 'expr("scos_pinned_' Validation/source/src Output/src
```

If the grep returns hits, replace with the pinned-literal form:

- **Correct form**: `functions.to_date(functions.lit(System.getProperty("SCOS_PINNED_DATE", java.time.LocalDate.now().toString)))`
  and `functions.to_timestamp(functions.lit(System.getProperty("SCOS_PINNED_TIMESTAMP", java.time.LocalDate.now().toString + " 00:00:00")))`.
  These are standard Spark Column expressions — no kit import needed, no `spark` session
  in scope required, compiles in any workload JAR.
- **NEVER use `functions.expr("scos_pinned_date()")` or `functions.expr("scos_pinned_timestamp()")`.**
  These call a SQL function by name; `scos_pinned_date` / `scos_pinned_timestamp` are
  NOT registered as Snowflake UDFs, so Phase B will fail with
  `SCOS ERROR CODE: 4001 — Unsupported function name scos_pinned_date`.
- **NEVER use `DatePin.pinnedDateCol(spark)` in workload code.** `DatePin` lives in
  the harness kit module (`com.snowflake.scos.kit`) which is NOT a dependency of the
  workload JAR — inserting it into workload source causes `not found: value DatePin`
  at `sbt assembly` time. `DatePin.install(spark)` is called by the harness; only
  the resulting system properties (`SCOS_PINNED_DATE`, `SCOS_PINNED_TIMESTAMP`) are
  visible to workload code via `System.getProperty`.

Record each affected call site in the `notes` array of the relevant
`schemas/entrypoints/<id>/_meta.json` so the reviewer can verify the
substitution is semantically equivalent.

## Rules

> ⚠ **Batch scope:** Scope all patches to this batch's entrypoint source files.
> Derive any `relative_file` glob from the batch entrypoint paths in
> `Validation/shared/schemas/manifest.json`, not from the full repo tree.
> A repo-wide glob (e.g. `**/*.scala`) will match files outside your batch —
> including files with pre-existing syntax errors that fail the `scalac` gate.
> Use a scoped glob (e.g. `src/main/scala/<subdir>/**/*.scala`) when entries are
> identical across files; use per-file entries for non-identical patches.
>
> ⚠ **Shared-prefix globs also match out-of-batch siblings.** A prefix glob
> (`Foo*.scala`) can match files not in your batch, pulling them into your patch
> (cherry-pick conflicts at harvest) and tripping the compile gate on files you
> don't own. When batch entrypoints only share a name prefix with non-batch files,
> use per-file entries instead.

- Prefer leaving files alone when the entrypoint is already callable.
- Do not rewrite business logic or fix SCOS compatibility issues here.
- Do not create Python `__init__.py` files — not applicable to JVM.
- Do not inline widget mocks, session mocks, or library shims here.
- Do not edit `Validation/tests/`; that is the runners' area.

### Direct environment-variable reads (JVM limitation)

The harness injects `SCOS_*` / widget values through `EnvUtil` (an in-process
override map + system properties). The JVM **cannot** mutate the real process
environment, so a workload that reads config directly via `System.getenv("X")`
or `sys.env("X")` will NOT see harness-injected values (this differs from
Python's `os.environ` patching). If a selected entrypoint reads env vars
directly, do one of the following during adaptation:

- Rewrite `System.getenv("X")` / `sys.env("X")` / `sys.env.get("X")` reads to
  `System.getProperty("X")` (which `EnvUtil.setEnv` populates), or
- Document that the workload must be launched in a forked JVM with the
  environment set before process start.

`System.getProperty` works in-process; `System.getenv` does not.

## Authoring source edits — via `patch-add`

All source edits — `System.getenv`/`sys.env` → `System.getProperty`,
`current_date()`/`current_timestamp()` → `DatePin`, `dbutils.*` stubs, cloud
I/O → `SCOS_*` env indirection — must be authored as **blueprint patches**, not
freehand edits. Each patch is a search/replace keyed on a single
`relative_file`; the engine derives and patches BOTH the Phase A copy
(`Validation/source/<rel>`) and the Phase B copy (`Output/<rel>`), so the two
sides stay in lockstep and the change is auditable.

Write the batch to a temp file and apply it atomically:

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  patch-add --conv-root $CONVERSION_ROOT --from-file /tmp/patches.json
```

> **Generate the batch file with a Python script using `json.dumps`, not by hand.**
> For any patch whose `search`/`replace` contains backslash sequences, multi-line
> string spans, or nested quotes, hand-escaping is error-prone and a frequent cause
> of "search not found". Build the patch dicts in Python and `json.dump` them to the
> batch file — the escaping is correct by construction.

```json
{"patches": [
  {"id": "env_widget", "relative_file": "src/Job.scala",
   "search": "System.getenv(\"SCOS_WIDGET_ENV\")",
   "replace": "System.getProperty(\"SCOS_WIDGET_ENV\")"}
]}
```

Rules enforced (batch rejected with exit 2, nothing written, if any fail):
- each side's `search` matches **exactly once** (set `"replace_all": true` to
  rewrite every occurrence, e.g. deleting all logging calls);
- **Disambiguate repeated patterns** by widening `search` with surrounding context
  until it is unique. When the SAME expression appears more than once in a file,
  include the **following line(s)** in `search` to pin the right occurrence — the
  next line is usually what differs (a different `.select(...)`, variable binding,
  or assignment), so add it verbatim (escaping `\n` / `\\` exactly).
- when the two sides have drifted, give per-side `source`/`migrated` sub-blocks
  (the presence of a sub-block also selects which sides to patch);
- `.scala`/`.sc` files must still **parse** after the edit — when `scalac` is
  on PATH the engine runs `scalac -Ystop-after:parser` as a pre-commit syntax
  gate; otherwise it relies on the build below (the authoritative check).

The blueprint at `Validation/shared/patch_blueprint.json` is the audit trail;
both the `Output/` and `Validation/source/` sides are committed together as one
`[TEST-PATCH]` commit (so a `git revert` undoes both sides). These harness I/O
edits are **never** cherry-picked onto the deliverable at harvest — they exist
only to make both phases runnable under the kit. Keep entries minimal and the
blueprint append-only — do not re-author a patch already present (identical
entries are auto-deduped).

Structural wrapping (the new wrapper `object` in Cases B/C) and the
build/compile step are NOT patches — author the wrapper file directly and build
as below.

### File-source reads are a per-side patch (source ≠ migrated)

When the workload reads a file-category source via `spark.read.parquet(path)` (or
`.csv`, `.json`, `.orc`) and `path` is already patched to
`System.getProperty("SCOS_INPUT_<ID>")`, the **`source` side works as-is** for
Phase A — the harness injects a local file path and `spark.read.parquet` reads it.

For Phase B (`migrated` side), **author a separate `migrated`-side patch** that
replaces `spark.read.parquet(System.getProperty("SCOS_INPUT_<ID>"))` with
`spark.table(System.getProperty("SCOS_INPUT_<ID>"))`.

Why: the Phase B harness (`ScosTrialFixture._setupPhaseB`) overrides `SCOS_INPUT_*`
for `file` category sources with a Snowflake 3-part table name (the provisioner
loaded the mock parquet into a Snowflake table). `spark.read.parquet("DB.SCH.T")`
fails in SCOS with `File doesn't exist`; `spark.table("DB.SCH.T")` resolves it
correctly.

```json
{
  "id": "file_read_phase_b",
  "migrated": {
    "file": "Output/src/main/scala/…/MyJob.scala",
    "search": "spark.read.parquet(System.getProperty(\"SCOS_INPUT_SRC_DATA\"))",
    "replace": "spark.table(System.getProperty(\"SCOS_INPUT_SRC_DATA\"))"
  }
}
```

This is a `[TEST-PATCH]` — do not cherry-pick to the deliverable.

### File-category sinks in Phase B (stage capture — preferred)

When the workload's sink is a file write (`spark.write.parquet(path)` / `.csv(path)`)
patched to `System.getProperty("SCOS_SINK_<ID>")`, **Phase A capture works as-is**
— the harness reads the local parquet directory.

**Phase B (preferred, PySpark parity):** the harness creates a per-trial
`SCOS_SINKS` stage, sets `SCOS_SINK_<ID>` to
`@"<db>"."<clone>"."SCOS_SINKS"/<io_id>`, then `GET`s staged files into local
`sink_captures/` before `captureResults`. Keep the **same**
`System.getProperty("SCOS_SINK_<ID>")` write on both sides — no `saveAsTable`
remap required for parquet/csv/json file sinks.

**Excel / mongo / blob sinks** are not stage-native. Prefer remapping
(migrated-side) to `.parquet(System.getProperty("SCOS_SINK_<ID>"))` or
`saveAsTable(...)`. Use `allow_empty: "<short reason>"` **only** when empty
output is intentional for this fixture — never for UDF `ClassNotFound`,
unreachable connectors, or unpatched I/O (use `expected_divergences` with
`scope=udf` instead).

`prevalidate --phase b` blocks on unpatched excel/mongo/literal file writes
(`io_completeness` / `sink_strategy`).

### File-category sinks — legacy saveAsTable remap (optional)

If stage writes fail on a particular SCOS client build, author a `migrated`-side
patch replacing the file write with `saveAsTable`:

```json
{
  "id": "sink_savetable_phase_b",
  "migrated": {
    "file": "Output/src/main/scala/…/MyJob.scala",
    "search": ".write.mode(\"overwrite\").parquet(System.getProperty(\"SCOS_SINK_MAIN\"))",
    "replace": ".write.mode(\"overwrite\").saveAsTable(new java.io.File(System.getProperty(\"SCOS_SINK_MAIN\", \"main\").stripSuffix(\"/\")).getName)"
  }
}
```

The harness reads back from `$cloneSchema.<basename>` (e.g. `$cloneSchema.main`).
Remove `.partitionBy(...)`, `.option("header",...)`, and `.option("delimiter",...)` from
the `migrated` side — Snowflake tables don't have Hive partitioning or CSV options.
Keep those options on the `source` side so Phase A capture is unaffected.

This is a `[TEST-PATCH]` — do not cherry-pick to the deliverable.

### Connector reads are a per-side patch (source ≠ migrated)

A Snowflake connector read needs **different** edits on the two sides, so author
it as a per-side patch (`source`/`migrated` sub-blocks):

- **`spark.read.format("snowflake").option("query"/"dbtable", …).load()`** —
  - **`source` (Phase A, local Spark):** there is no Snowflake connector locally,
    and the options map is often a `%run`-config-injected global that no longer
    exists standalone (→ `NotFound`/`NullPointer`). Replace the *entire*
    `format("snowflake")…load()` read (including the `.options(...)` line) with a
    `spark.table(s"${System.getProperty("SCOS_DATABASE_NAME")}.${System.getProperty("SCOS_OUTPUT_SCHEMA")}.TABLE")`
    mock, then inline `.withColumnRenamed`/`.select`/`.filter` to replay the
    query's aliases/WHERE.
  - **`migrated` (Phase B, SCOS):** SCOS runs *on* Snowflake, so the
    `format("snowflake")…load()` stays and works — but `sfDatabase`/`sfSchema`
    point at **production**; rebind them to `System.getProperty("SCOS_DATABASE_NAME")` /
    `System.getProperty("SCOS_OUTPUT_SCHEMA")` so the read hits the per-trial
    golden clone.
- **`spark.read.format("jdbc"/"redshift").option(…).load()`** — same per-side
  treatment. SCOS has no JDBC/Redshift driver; if `Output/` still has this form,
  rewrite the `migrated` side to `spark.table(...)` too.
- **Hardcoded literal 3-part name** (`spark.sql(s"… FROM DB.SCHEMA.T …")` or
  `spark.table("DB.SCHEMA.T")` with the prod `DB.SCHEMA` baked into the string):
  rebind the **literal prefix** itself (a `regex` patch, e.g. `\bDB\.SCHEMA\.` →
  the trial namespace). This applies to **both** sides.

> **⚠ Rebinding `sfDatabase`/`sfSchema` ALONE is the `migrated`-side fix only.**
> It does NOT make the `source` side runnable, and `.option("sfDatabase"/"sfSchema", …)`
> rebinds **only** match `format("snowflake")` chains — they silently no-op on
> `spark.sql`/`spark.table` reads (the prod qualifier stays, a real bug seen in
> practice). Never hand-edit `patch_blueprint.json`; every entry must go through
> `patch-add` (which rejects a 0-match search) — fewer `patch_added` events than
> blueprint entries means the blueprint has drifted from disk.

### Namespace rebind (3-part names → harness catalog)

Scala workloads often build fully-qualified `DB.SCHEMA.TABLE` names from config
tokens (`spark.table(s"$databaseName.$schemaName.T")`, or the same tokens inside
SQL strings). Open-source Spark's session catalog is 2-level, so an unregistered
leading token raises `AnalysisException` in Phase A. This is **plumbing, not
dialect** — patch it:

- **Catalog/database token:** rebind the leading qualifier to
  `System.getProperty("SCOS_DATABASE_NAME")` in **both** phases (Phase A sets it
  to `spark_catalog`; Phase B sets the Snowflake database).
- **Schema token(s):** rebind every schema qualifier used in 3-part names to
  `System.getProperty("SCOS_OUTPUT_SCHEMA")` in **both** phases. The harness
  creates a fresh per-trial schema and exports its name as `SCOS_OUTPUT_SCHEMA`.
  Hardcoding a production schema leaves seeded tables in one namespace while the
  workload reads another.
- These rebinds are `[TEST-PATCH]` validation plumbing — never cherry-pick them
  at harvest.

**`SCOS_OUTPUT_SCHEMA` is the whole schema qualifier — don't double-qualify.** On
the SCOS side it is already `DB.SCHEMA` (a bare schema in Phase A local), so
`System.getProperty("SCOS_OUTPUT_SCHEMA")` used **directly** as the qualifier is
correct on both sides. Prepending `SCOS_DATABASE_NAME` on top of it builds an
invalid 4-part `DB.DB.SCHEMA.T` under SCOS — the
`s"${System.getProperty("SCOS_DATABASE_NAME")}.${System.getProperty("SCOS_OUTPUT_SCHEMA")}.T"`
form is a **Phase-A-local-only** shape (where `SCOS_DATABASE_NAME` resolves to
`spark_catalog`); under SCOS it double-qualifies and fails.

### Missing config/helper modules → env, not new source tables

When the standalone workload `import`s an absent config/helper module (or reads a
`%run`-injected global) **only to obtain runtime parameters** — a database name,
schema token, batch date, feature flag — rebind those reads to a
`System.getProperty(...)` that the harness injects: a `SCOS_WIDGET_<NAME>` value
declared in the entrypoint's `widget_env_vars`, or the already-wired
`SCOS_DATABASE_NAME` / `SCOS_OUTPUT_SCHEMA` globals. Do **not** ask the
data-synthesizer to invent a new file table under `schemas/entrypoints/<id>/tables/`
for a module that
carries no row data into the computation — it is a parameter source, not a mock
input.

## Collapsing repeated patches (regex + globs)

**This is the single biggest lever for keeping the blueprint small.** Real
workloads carry the same Databricks boilerplate in nearly every file (`dbutils`
calls, logger init, mount guards). Authoring one entry **per file** balloons the
blueprint — write ONE glob entry instead.

**Rule: if the exact same `search`/`replace` applies to 2+ files, write ONE glob
entry — never N per-file entries.** `patch-add` prints a `HINT:` when it detects
this; treat it as a directive to consolidate.

**`"regex": true`** — `search` is a Python regex (default flags; opt in to
DOTALL/MULTILINE via inline `(?s)`/`(?m)`). `replace` supports backreferences
(`\1`, `\g<name>`). The same uniqueness and parse gates apply.

**Glob `relative_file`** (contains `*`, `?`, or `[`) — the engine expands
against each side's prefix directory. Files with zero matches are silently
skipped; the entry fails only if NO file matches at all. Glob entries must use
top-level `search`/`replace` (no per-side blocks). If the `Validation/source/`
and `Output/` copies need **different** rewrites, use per-file entries with
`source`/`migrated` blocks instead of a glob.

> **Shared-prefix globs also match out-of-batch siblings.** A prefix glob
> (`Foo*.scala`) can match files not in your batch, pulling them into your patch
> (cherry-pick conflicts at harvest) and tripping the `ast.parse` gate on files
> you don't own. When batch entrypoints only share a name prefix with non-batch
> files, use per-file entries instead.

Typical workload-wide boilerplate that should each be ONE glob entry:

| Boilerplate (appears in many files) | One glob entry |
|---|---|
| `dbutils.notebook.exit(<args>)` | `regex: true`, `replace_all: true` → `System.exit(0)` |
| logger init / telemetry calls | `regex: true`, `replace_all: true` → `""` |
| `dbutils.fs.refreshMounts()` | literal, `replace_all: true` → `true` |
| `.option("sfDatabase", "PROD_DB")` | `regex: true`, `replace_all: true` → `.option("sfDatabase", System.getProperty("SCOS_DATABASE_NAME"))` |
| `.option("sfSchema", "PROD_SCHEMA")` | `regex: true`, `replace_all: true` → `.option("sfSchema", System.getProperty("SCOS_OUTPUT_SCHEMA"))` |

### Worked examples

Delete all `dbutils.notebook.exit(...)` calls across all Scala source files:

```json
{"id": "strip_notebook_exit",
 "relative_file": "src/**/*.scala",
 "regex": true, "replace_all": true,
 "note": "dbutils.notebook.exit -> System.exit(0) everywhere",
 "search": "dbutils\\.notebook\\.exit\\([^\\n]*\\)",
 "replace": "System.exit(0)"}
```

Rebind a hardcoded production schema across all files that reference it:

```json
{"id": "rebind_sf_schema",
 "relative_file": "src/**/*.scala",
 "regex": true, "replace_all": true,
 "note": "point connector reads at the per-trial schema, not prod",
 "search": "\\.option\\(\"sfSchema\", \"PROD_SCHEMA\"\\)",
 "replace": ".option(\"sfSchema\", System.getProperty(\"SCOS_OUTPUT_SCHEMA\"))"}
```

**Warning:** regex + replace_all + glob is powerful — keep the pattern tight.
The parse gate and the auditable blueprint entry are the safety net. Prefer a
literal patch when a single unique site is involved.

### Before submitting — consolidation pass

Group your drafted patches by `(search, replace, regex, replace_all)`. For any
group spanning 2+ files, replace the whole group with a single glob entry over
their common directory (e.g. `src/**/*.scala`).

### Recovering from an over-broad patch

Each `patch-add` lands as one `[TEST-PATCH]` commit staging both the `Output/`
and `Validation/source/` sides, so recovery is a single git operation:

1. `git revert <test-patch-sha>` — restores both sides.
2. Delete the offending entry from `Validation/shared/patch_blueprint.json`,
   then resubmit a tighter patch via `patch-add`.

## Record

For each selected entrypoint, write the fields into
`schemas/entrypoints/<id>/_meta.json` that `TestTemplate.scala.tmpl` will
consume (via the regenerated analysis shim):

```json
{
  "entrypoint_path": "relative/path/or/dir",
  "entrypoint_class": "com.example.MyObject",
  "entrypoint_method": "main",
  "cli_args": [],
  "jar_path": "Output/target/scala-2.12/workload-assembly.jar",
  "build_tool": "sbt",
  "widget_env_vars": {
    "SCOS_WIDGET_ENV": "dev"
  },
  "path_redirects": {
    "s3://bucket/path/file.csv": "mock_s3/bucket/path/file.csv"
  },
  "notes": ["why this adaptation was needed"]
}
```

Only record fields the downstream test actually needs. Keep absent or
unused sections empty rather than inventing placeholders.

**Stage-path env vars (`*_STAGE_PATH`)** are directory prefixes by convention.
You do not need to hand-append a trailing `/` when authoring `widget_env_vars`:
`EnvUtil.setEnv` normalizes any `*_STAGE_PATH` key (or any `@db.schema.stage/...`
value) to end with `/` at injection time, unless the value points at a single
file (`.../data.parquet`). Rely on that convention instead of patching slashes
into individual workloads.

Then record both milestones — `patches_authored` once the blueprint patches are
applied via `patch-add`, and `workload_built` once entrypoints are callable and
the workload JAR is built:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-milestone --conv-root $CONVERSION_ROOT --milestone patches_authored
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-milestone --conv-root $CONVERSION_ROOT --milestone workload_built
```
