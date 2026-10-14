---
name: patch-author
description: Make the workload runnable under the JVM harness — author the I/O patch blueprint (cloud reads/writes, dbutils, secrets, widgets, env reads), ensure entrypoints are callable, and compile the migrated workload to a JAR. Patches are smoke-tested and committed via `scos_state.py patch-add`.
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
commits those as `[MIGRATION-FIX]` on `Output/`. Patch a Java file only when
the harness must redirect where the workload loads data or reads config.

## Inputs

- `Validation/shared/analysis.json`
- `Validation/source/`
- `Output/`

## Output

Update `analysis.json` with the exact fields `TestTemplate.scala.tmpl`
needs (`entrypoint_class`, `entrypoint_method`, `jar_path`,
`build_tool`), and make the smallest source edits needed to support
them.

## Cases

### Case A: already a standard main class

If the file exposes `public static void main(String[] args)`,
no source edits are needed.

Record:

```json
{
  "entrypoint_class": "com.example.MyClass",
  "entrypoint_method": "main",
  "build_tool": "maven"
}
```

and move on.

### Case B: no main method

If the `.java` file has a pipeline class without `public static void main`,
create a thin wrapper class that invokes the pipeline:

```java
public class ValidationEntrypoint {
    public static void main(String[] args) {
        new MyPipelineClass().run(args);
    }
}
```

Place the wrapper alongside the original file in the copied source tree.
Do not modify the original source directly.

### Case C: scopt / CLI argument-driven entrypoint

If the entrypoint only works when `args` contains specific values
(flags, config paths, etc.):

- Record the default args needed for a no-op or minimal test run in
  `entrypoint_args[]` (a string array).
- Do not rewrite the argument parser.

> **Databricks notebooks:** Databricks does not execute Java as a native
> notebook cell language. Java workloads are always standalone `.java` files.
> If the workload was exported from a Databricks workspace (e.g. as a job JAR),
> treat it as Case A/B/D as applicable — do NOT apply notebook-flattening steps.
> `notebook_io` cell-flatten tooling is Python/Scala only.

### Case D: multi-module Maven / Gradle project

1. Detect the build tool from `analysis.json["build_tool"]`.
2. Verify the project compiles and produces a fat/assembly JAR:
   - **Maven**: run `mvn package -DskipTests` and locate
     `target/*-jar-with-dependencies.jar` or the shaded JAR
   - **Gradle**: run `./gradlew shadowJar` or `./gradlew jar` and
     locate `build/libs/*.jar`
3. Record `jar_path` relative to `CONVERSION_ROOT`.
4. The kit will load entrypoints from this JAR via `ReflectionEntrypoint`.

If no fat-JAR plugin is configured, add the minimal assembly config to
the build file (e.g., `maven-shade-plugin` to `pom.xml` for Maven, or
`shadowJar` plugin to `build.gradle` for Gradle).

## Mandatory API scan

**Before authoring any patches, run the deterministic scans below.** A successful
`mvn package` does NOT prove the workload is harness-safe — `dbutils` and other
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
grep -rn "System\.getenv" Validation/source/src Output/src
```

### Auto-generate `System.getenv` → `System.getProperty` patches

**Do this first**, before hand-authoring any per-file entries. The JVM harness
injects configuration via `System.setProperty()` (through `EnvUtil`), so
`System.getenv()` calls will not see harness-injected values. Apply these glob
patches via a single `patch-add` call so both sides stay in lockstep:

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
cat > /tmp/sys_env_patches.json << 'EOF'
{"patches": [
   {"id": "system_getenv_with_default",
    "relative_file": "src/**/*.java",
    "regex": true, "replace_all": true,
    "note": "System.getenv with default -> System.getProperty (EnvUtil injects via setProperty only)",
    "search": "System\\.getenv\\(\\s*(\"[^\"]*\")\\s*\\)\\s*!=\\s*null\\s*\\?\\s*System\\.getenv\\(\\s*\\1\\s*\\)\\s*:\\s*(\"[^\"]*\")",
    "replace": "System.getProperty(\\1, \\2)"},
  {"id": "system_getenv_or_default",
   "relative_file": "src/**/*.java",
   "regex": true, "replace_all": true,
   "note": "Optional.ofNullable(System.getenv(...)).orElse(...) -> System.getProperty(..., ...)",
   "search": "Optional\\.ofNullable\\(\\s*System\\.getenv\\(\\s*(\"[^\"]*\")\\s*\\)\\s*\\)\\.orElse\\(\\s*(\"[^\"]*\")\\s*\\)",
   "replace": "System.getProperty(\\1, \\2)"},
  {"id": "system_getenv_to_property",
   "relative_file": "src/**/*.java",
   "regex": true, "replace_all": true,
   "note": "System.getenv(\"K\") -> System.getProperty(\"K\") — EnvUtil injects via setProperty only",
   "search": "System\\.getenv\\(\\s*(\"[^\"]*\")\\s*\\)",
   "replace": "System.getProperty(\\1)"},
  {"id": "current_date_to_pinned_lit",
   "relative_file": "src/**/*.java",
   "regex": true, "replace_all": true,
   "note": "functions.current_date() -> deterministic lit",
   "search": "functions\\.current_date\\(\\)",
   "replace": "functions.to_date(functions.lit(System.getProperty(\"SCOS_PINNED_DATE\", java.time.LocalDate.now().toString())))"},
  {"id": "current_timestamp_to_pinned_lit",
   "relative_file": "src/**/*.java",
   "regex": true, "replace_all": true,
   "note": "functions.current_timestamp() -> deterministic lit",
   "search": "functions\\.current_timestamp\\(\\)",
   "replace": "functions.to_timestamp(functions.lit(System.getProperty(\"SCOS_PINNED_TIMESTAMP\", java.time.LocalDateTime.now().toString())))"}
]}
EOF
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  patch-add --conv-root $CONVERSION_ROOT --from-file /tmp/sys_env_patches.json
```

If `patch-add` reports zero matches for a given entry, that is fine — skip it.
If it reports matches, check the diff to confirm no string literals were accidentally rewritten.

> **Patches are file-specific — 0 matches is not a bug.** A pattern present in one
> file need not be forced onto its siblings. If a file already has the offending
> call removed, commented out, or absent (an already-migrated line, a
> `dbutils`/`System.exit`-style side effect that isn't there), do **not** author a
> duplicate removal patch just to mirror another file. Let the glob's zero-match
> skip handle it.

### I/O rewrite table

For each hit from the grep scan, apply the corresponding rewrite. If the
same pattern appears in many files, write **one glob entry** (see "Collapsing
repeated patches").

| Original (Java) | Patch `replace` |
|---|---|
| `spark.read()...load("s3://…")` / `"abfs://…"` / `"gs://…"` (hardcoded cloud URI) | `spark.read().<fmt>(System.getProperty("SCOS_INPUT_<ID>"))` |
| `df.write()...save("s3://…")` / non-table sink | `df.write()...save(System.getProperty("SCOS_SINK_<ID>"))` |
| `dbutils.fs.mounts().map(m -> m.mountPoint()).contains(x)` | `false` |
| `dbutils.fs.mount(…)` (any form) | `/* no-op */` |
| `dbutils.fs.unmount(…)` | `/* no-op */` |
| `dbutils.fs.ls(…)` | `Collections.emptyList()` |
| `dbutils.fs.mkdirs(…)` | `true` |
| `dbutils.fs.cp(…)` | `true` |
| `dbutils.fs.rm(…)` | `true` |
| `dbutils.fs.refreshMounts()` | `true` |
| `dbutils.secrets.get(scope, key)` | `System.getProperty("SCOS_SECRET_" + key.toUpperCase().replace("-", "_"), "test_secret_" + key)` |
| `dbutils.widgets.get("x")` | inline string literal matching entrypoint default, e.g. `"dev"` |
| `dbutils.notebook.exit(…)` | `System.exit(0)` |
| `dbutils.notebook.run(…)` | `""` — use `"replace": "\"\""` in the patch (returns `String`; `"replace": ""` means delete, which breaks `String x = dbutils.notebook.run(...)`) |
| `display(df)` / `df.display()` (Databricks notebook viewer) | delete (`replace: ""`), or if flagged `display_only: true`, rewrite to `df.write().mode("overwrite").parquet(System.getProperty("SCOS_SINK_DISPLAY_<N>"))` |
| `displayHTML(…)` | delete (`replace: ""`) — always |
| `System.getenv("X")` | `System.getProperty("X")` |
| `spark.read().format("snowflake").option("query"/"dbtable", …).load()` | per-side patch — see "Connector reads" below |
| `spark.read().format("jdbc"/"redshift").option(…).load()` | per-side patch — see "Connector reads" below |
| Non-Spark JVM file reader (`new FileInputStream(path)`, `Files.readAllBytes(Paths.get(path))`, `Files.lines(Paths.get(path))`, Jackson/CSV from a path, etc.) | Guard at the top of the read: `String _scosIn = System.getProperty("SCOS_INPUT_<ID>"); String path = (_scosIn != null && !_scosIn.isEmpty()) ? _scosIn : originalPath;` then feed the redirected path into the reader |

Table-form reads/writes that already run on the session
(`spark.table(…)`, `saveAsTable(…)`) need no patch.

> **⚠ `dbutils.notebook.exit(...)` → `System.exit(0)` can kill output capture.**
> The kit runs each trial in a **forked test JVM** and captures outputs (tables +
> file sinks) **after** the entrypoint returns. The JVM has no `System.exit`
> interception, so a `System.exit(...)` reached mid-run terminates the fork
> **before** capture — the trial loses its baseline (shows up as a failed capture /
> `passed_no_baseline`, not a real result). This is safe **only** when the exit is
> genuinely the last thing the workload does. If `dbutils.notebook.exit` sits behind
> an early-exit guard (`if (cond) { dbutils.notebook.exit(...); }`) that still has real
> Spark work after it, do **not** map it to `System.exit(0)` — instead choose
> `entrypoint_kwargs` / widget values that bypass the guard, or delete the guarded
> exit, so the workload runs to completion and its outputs are captured. (A general
> JVM exit-interception mechanism is a tracked follow-up; until it lands, keep
> terminating rewrites off any path that precedes a sink.)

### Dead imports after deletion

When a Databricks import (e.g. `import com.databricks.dbutils_v1.DBUtilsHolder.dbutils;`
or any Databricks API import) is left at the top of a file after all its call
sites are deleted, the import itself must be patched out (`replace: ""`).
Blueprint patches apply to `Validation/source/` as well as `Output/` — do not
leave dead imports that cause compile errors when the Databricks JAR is removed
from the classpath.

### Intermediate file re-reads

When the workload writes a file-format intermediate (e.g. Parquet, Delta)
and later re-reads it in the same run, BOTH the write and the subsequent
read need path-redirect patches (`SCOS_SINK_<ID>` on the write,
`SCOS_INPUT_<ID>` on the re-read). Without redirection the pipeline breaks
at the re-read step.

### Helper-method-hidden file reads (path buried in a utility/helper class)

The `SCOS_INPUT_*` redirect does **NOT** automatically apply when
`spark.read()` is called inside a utility or helper method that receives a
dynamically-built DBFS/cloud path (e.g. `dataLake.load()`, `Bronze.loadDataFile()`,
`Read.asDf(location = getBronzeLocation(banners))`). A simple grep of the
entrypoint file finds no `dbfs:/` literal, yet the workload reads live cloud
paths at runtime.

**Detection step**: After the literal-URI scan, run:

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
# Find path-building helpers that emit cloud URIs
grep -rn '"dbfs:/\|"s3://\|"abfs://' Validation/source/src Output/src \
  | grep -v "spark\.read\|spark\.write\|//\s*SCOS"
```

For each hit, trace which `external_source` in `analysis.json` it corresponds to,
then add a guard at the **top of the helper that does the actual cloud read**,
immediately after any parameter setup and before the cloud call:

```java
// ADD after parameter setup, before the cloud read:
String _scosInput = System.getProperty("SCOS_INPUT_<ID>");
if (_scosInput != null && !_scosInput.isEmpty()) {
    return spark.read().parquet(_scosInput);  // match the helper's return type
}
```

Replace `<ID>` with `src.id.toUpperCase().replaceAll("[^A-Z0-9]", "_")` from
the matching `analysis.json` external_source. Apply to **both**
`Validation/source/` and `Output/` copies.

### Indirect file reads (env var whose default is an S3/HDFS URI)

The `SCOS_INPUT_*` redirect applies to `spark.read().csv("s3://...")` literals.
It does **NOT** automatically apply when the S3 path is hidden inside a
`System.getenv` / `System.getProperty` default:

```java
// Example — the S3 path is invisible to a simple grep of spark.read calls:
String rawPath = System.getProperty("FARECARD_RAW_TAPS_PATH", "s3://farecard-raw/taps/") + runDate;
Dataset<Row> df = spark.read().option("header", "true").csv(rawPath);   // ← no S3 literal here
```

**Detection step**: After running the `spark.read...load("s3://...")` scan, also
grep for S3/HDFS URIs in property defaults:

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
grep -rn '"s3://\|"s3a://\|"gs://\|"abfs://\|"wasb://\|"hdfs://' Validation/source/src Output/src \
  | grep "getOrDefault\|getProperty\|System\.getenv\|orElse"
```

For each hit, trace the variable to its `spark.read()` call and apply the
`SCOS_INPUT_<ID>` redirect to the **property line**, not the `spark.read()` line:

```json
{"id": "raw_taps_scos_input",
 "relative_file": "src/main/java/.../DataLoader.java",
 "note": "Redirect S3 CSV read to SCOS_INPUT so harness can inject staged path",
 "migrated": {
   "search": "System.getProperty(\"FARECARD_RAW_TAPS_PATH\", \"s3://farecard-raw/taps/\")",
   "replace": "System.getProperty(\"SCOS_INPUT_RAW_TAPS\", \"\")"
 },
 "source": {
   "search": "System.getProperty(\"FARECARD_RAW_TAPS_PATH\", \"s3://farecard-raw/taps/\")",
   "replace": "System.getProperty(\"SCOS_INPUT_RAW_TAPS\", \"\")"
 }}
```

Also register the source in `analysis.json` as `category: "file"` with a
`mock_file` reference so the provisioner can stage it. If no local mock exists,
record the entrypoint as `requires_real_data` and mark it `file_only`.

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
Aliased imports like `import static org.apache.spark.sql.functions.*` followed by
bare `current_date()` are **not matched** and must be found manually:

```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
grep -rn "functions\.current_date()\|functions\.current_timestamp()" Validation/source/src Output/src
grep -rn "\bcurrent_date()\|\bcurrent_timestamp()" Validation/source/src Output/src \
  | grep -v "functions\."   # show bare / static-imported hits
```

For any bare / aliased hit, author a targeted per-file patch using the same
`functions.to_date` / `functions.to_timestamp` form. After applying all patches,
the verification grep should return 0:

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

- **Correct form**: `functions.to_date(functions.lit(System.getProperty("SCOS_PINNED_DATE", java.time.LocalDate.now().toString())))` and `functions.to_timestamp(functions.lit(System.getProperty("SCOS_PINNED_TIMESTAMP", java.time.LocalDateTime.now().toString())))`.
- **NEVER use `functions.expr("scos_pinned_date()")` or `functions.expr("scos_pinned_timestamp()")`.**
- **NEVER use `DatePin.pinnedDateCol(spark)` in workload code.** `DatePin` lives in the harness kit, not the workload JAR.

## I/O patch patterns (Java)

### Cloud file reads

```java
// BEFORE (reads from S3/Azure/GCS — fails without cloud credentials):
Dataset<Row> df = spark.read().parquet("s3://bucket/path/data.parquet");

// AFTER (reads from harness-injected path):
Dataset<Row> df = spark.read().parquet(System.getProperty("SCOS_INPUT_DATA_SOURCE"));
```

Record the redirect in `path_redirects` (analysis.json):
```json
{"s3://bucket/path/data.parquet": "mock_data/<ep_id>/data.parquet"}
```

### Cloud file writes

```java
// BEFORE:
df.write().mode("overwrite").parquet("s3://bucket/output/");

// AFTER:
df.write().mode("overwrite").parquet(System.getProperty("SCOS_SINK_OUTPUT"));
```

### Connector reads (format("snowflake"))

Connector reads are a **per-side patch**: the source side rewrites to
`spark.table(...)` or `spark.read().parquet(...)` against mock data; the
migrated side uses `SCOS_INPUT_*` env indirection (the connector maps to the
provisioned golden schema at runtime).

Source-side patch:
```java
// BEFORE: spark.read().format("snowflake").option("dbtable", "ORDERS").load()
// AFTER:  spark.read().parquet(System.getProperty("SCOS_INPUT_ORDERS"))
```

### dbutils / Databricks widgets

```java
// BEFORE: dbutils.widgets.get("env")
// AFTER:  System.getProperty("SCOS_WIDGET_ENV", "dev")
```

### Secrets

```java
// BEFORE: dbutils.secrets.get(scope="prod", key="api_key")
// AFTER:  "mock_api_key_for_testing"  // inline literal, or delete if unused
```

## Build the workload JAR

After patches are applied, compile the workload:

**Maven:**
```bash
cd $CONVERSION_ROOT/Output && mvn package -DskipTests -q
```

**Gradle:**
```bash
cd $CONVERSION_ROOT/Output && ./gradlew shadowJar -q
```

Locate the output JAR and record `jar_path` in `analysis.json`.

## Rules

- Prefer leaving files alone when the entrypoint is already callable.
- Do not rewrite business logic or fix SCOS compatibility issues here.
- Do not create Python `__init__.py` files — not applicable to JVM.
- Do not inline widget mocks, session mocks, or library shims here.
- Do not edit `Validation/tests/`; that is the runners' area.

### Direct environment-variable reads (JVM limitation)

The harness injects `SCOS_*` / widget values through `EnvUtil` (an in-process
override map + system properties). The JVM **cannot** mutate the real process
environment, so a workload that reads config directly via `System.getenv("X")`
will NOT see harness-injected values (this differs from Python's `os.environ`
patching). If a selected entrypoint reads env vars directly:

- Rewrite `System.getenv("X")` to `System.getProperty("X")` (which `EnvUtil.setEnv`
  populates), or
- Document that the workload must be launched in a forked JVM with the environment
  set before process start.

`System.getProperty` works in-process; `System.getenv` does not.

## Authoring source edits — via `patch-add`

All source edits — `System.getenv` → `System.getProperty`,
`current_date()`/`current_timestamp()` → pinned literal, `dbutils.*` stubs, cloud
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
  {"id": "env_widget", "relative_file": "src/Job.java",
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
- `.java` files must still **compile** after the edit — when `javac` is on PATH
  the engine may run a syntax check as a pre-commit gate; otherwise it relies on
  the build below (the authoritative check).

The blueprint at `Validation/shared/patch_blueprint.json` is the audit trail;
both the `Output/` and `Validation/source/` sides are committed together as one
`[TEST-PATCH]` commit (so a `git revert` undoes both sides). These harness I/O
edits are **never** cherry-picked onto the deliverable at harvest — they exist
only to make both phases runnable under the kit. Keep entries minimal and the
blueprint append-only — do not re-author a patch already present (identical
entries are auto-deduped).

Structural wrapping (the new wrapper class in Case B) and the build/compile step
are NOT patches — author the wrapper file directly and build as below.

### Connector reads are a per-side patch (source ≠ migrated)

A Snowflake connector read needs **different** edits on the two sides, so author
it as a per-side patch (`source`/`migrated` sub-blocks):

- **`spark.read().format("snowflake").option("query"/"dbtable", …).load()`** —
  - **`source` (Phase A, local Spark):** there is no Snowflake connector locally.
    Replace the *entire* `format("snowflake")…load()` read (including any
    `.options(...)` line) with a
    `spark.table(System.getProperty("SCOS_DATABASE_NAME") + "." + System.getProperty("SCOS_OUTPUT_SCHEMA") + ".TABLE")`
    mock, then inline `.withColumnRenamed`/`.select`/`.filter` to replay the
    query's aliases/WHERE.
  - **`migrated` (Phase B, SCOS):** SCOS runs *on* Snowflake, so the
    `format("snowflake")…load()` stays and works — but `sfDatabase`/`sfSchema`
    point at **production**; rebind them to `System.getProperty("SCOS_DATABASE_NAME")` /
    `System.getProperty("SCOS_OUTPUT_SCHEMA")` so the read hits the per-trial
    golden clone.
- **`spark.read().format("jdbc"/"redshift").option(…).load()`** — same per-side
  treatment. SCOS has no JDBC/Redshift driver; if `Output/` still has this form,
  rewrite the `migrated` side to `spark.table(...)` too.
- **Hardcoded literal 3-part name** (`spark.sql("… FROM DB.SCHEMA.T …")` or
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

Java workloads often build fully-qualified `DB.SCHEMA.TABLE` names from config
tokens (`spark.table(databaseName + "." + schemaName + ".T")`, or the same tokens
inside SQL strings). Open-source Spark's session catalog is 2-level, so an
unregistered leading token raises `AnalysisException` in Phase A. This is
**plumbing, not dialect** — patch it:

- **Catalog/database token:** rebind the leading qualifier to
  `System.getProperty("SCOS_DATABASE_NAME")` in **both** phases (Phase A sets it
  to `spark_catalog`; Phase B sets the Snowflake database).
- **Schema token(s):** rebind every schema qualifier used in 3-part names to
  `System.getProperty("SCOS_OUTPUT_SCHEMA")` in **both** phases. The harness
  creates a fresh per-trial schema and exports its name as `SCOS_OUTPUT_SCHEMA`.
- These rebinds are `[TEST-PATCH]` validation plumbing — never cherry-pick them
  at harvest.

### Missing config/helper modules → env, not new source tables

When the standalone workload `import`s an absent config/helper class (or reads a
statically-injected constant) **only to obtain runtime parameters** — a database
name, schema token, batch date, feature flag — rebind those reads to a
`System.getProperty(...)` that the harness injects: a `SCOS_WIDGET_<NAME>` value
declared in the entrypoint's `widget_env_vars`, or the already-wired
`SCOS_DATABASE_NAME` / `SCOS_OUTPUT_SCHEMA` globals. Do **not** ask the analyzer
to invent a new `external_sources[]` file table for a module that carries no row
data into the computation — it is a parameter source, not a mock input.

## Collapsing repeated patches (regex + globs)

**This is the single biggest lever for keeping the blueprint small.** Real
workloads carry the same Databricks boilerplate in nearly every file (`dbutils`
calls, logger init, mount guards). Authoring one entry **per file** balloons the
blueprint — write ONE glob entry instead.

**Rule: if the exact same `search`/`replace` applies to 2+ files, write ONE glob
entry — never N per-file entries.** `patch-add` prints a `HINT:` when it detects
this; treat it as a directive to consolidate.

**`"regex": true`** — `search` is a Python regex. `replace` supports backreferences
(`\1`, `\g<name>`). The same uniqueness and parse gates apply.

**Glob `relative_file`** (contains `*`, `?`, or `[`) — the engine expands
against each side's prefix directory. Files with zero matches are silently
skipped; the entry fails only if NO file matches at all. Glob entries must use
top-level `search`/`replace` (no per-side blocks). If the `Validation/source/`
and `Output/` copies need **different** rewrites, use per-file entries with
`source`/`migrated` blocks instead of a glob.

> **Shared-prefix globs also match out-of-batch siblings.** A prefix glob
> (`Foo*.java`) can match files not in your batch, pulling them into your patch
> (cherry-pick conflicts at harvest) and tripping the parse gate on files
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

Delete all `dbutils.notebook.exit(...)` calls across all Java source files:

```json
{"id": "strip_notebook_exit",
 "relative_file": "src/**/*.java",
 "regex": true, "replace_all": true,
 "note": "dbutils.notebook.exit -> System.exit(0) everywhere",
 "search": "dbutils\\.notebook\\.exit\\([^\\n]*\\)",
 "replace": "System.exit(0)"}
```

**Warning:** regex + replace_all + glob is powerful — keep the pattern tight.
The parse gate and the auditable blueprint entry are the safety net. Prefer a
literal patch when a single unique site is involved.

### Before submitting — consolidation pass

Group your drafted patches by `(search, replace, regex, replace_all)`. For any
group spanning 2+ files, replace the whole group with a single glob entry over
their common directory (e.g. `src/**/*.java`).

### Recovering from an over-broad patch

Each `patch-add` lands as one `[TEST-PATCH]` commit staging both the `Output/`
and `Validation/source/` sides, so recovery is a single git operation:

1. `git revert <test-patch-sha>` — restores both sides.
2. Delete the offending entry from `Validation/shared/patch_blueprint.json`,
   then resubmit a tighter patch via `patch-add`.

## Record

For each selected entrypoint, write the fields into `analysis.json`
that `TestTemplate.scala.tmpl` will consume directly:

```json
{
  "entrypoint_path": "relative/path/or/dir",
  "entrypoint_class": "com.example.MyClass",
  "entrypoint_method": "main",
  "entrypoint_args": [],
  "jar_path": "Output/target/workload-jar-with-dependencies.jar",
  "build_tool": "maven",
  "widget_env_vars": {
    "SCOS_WIDGET_ENV": "dev"
  },
  "path_redirects": {
    "s3://bucket/path/file.csv": "mock_data/<ep_id>/file.csv"
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
