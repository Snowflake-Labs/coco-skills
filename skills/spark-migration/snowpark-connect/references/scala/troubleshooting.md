# SCOS Migration & Validation Troubleshooting — Scala

Common issues and solutions for migrating and validating Spark Scala workloads with Snowpark Connect (SCOS).

---

## Setup & Environment Issues

### Error: uv not found

**Cause:** The `uv` package manager is not installed (needed for running the analyzer).

**Fix:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
Restart your terminal after installation.

---

### Error: Snowflake connection failed

**Cause:** The Snowflake connection is not configured or credentials are invalid.

**Fix:**
- Verify the `default` connection is configured (or use `--connection <name>`)
- Check credentials and network connectivity
- Ensure your Snowflake account is accessible

---

### Error: RAG resources exist but access denied

**Cause:** The RAG knowledge base was set up by another user and you don't have access.

**Fix:** Ask your Snowflake admin to grant access:
```sql
GRANT USAGE ON DATABASE SCOS_MIGRATION TO ROLE <your_role>;
GRANT USAGE ON SCHEMA SCOS_MIGRATION.PUBLIC TO ROLE <your_role>;
GRANT SELECT ON TABLE SCOS_MIGRATION.PUBLIC.SCOS_COMPAT_ISSUES TO ROLE <your_role>;
GRANT USAGE ON CORTEX SEARCH SERVICE SCOS_MIGRATION.PUBLIC.SCOS_COMPAT_ISSUES_SERVICE TO ROLE <your_role>;
```

---

### Error: RESOURCE_EXHAUSTED / message larger than max (gRPC 128MB limit)

**Cause:** The Spark Connect client caps a single gRPC message at 128 MB. Large
local data (e.g. a wide literal relation built from a large in-memory `Seq`, or
a big `createDataFrame` from a Scala collection) exceeds it and the call fails
with `RESOURCE_EXHAUSTED` / "message larger than max".

**Example error:**
```
io.grpc.StatusRuntimeException: RESOURCE_EXHAUSTED: ... Received message larger than max (134217728)
```

**Fix:** Raise the client-side limit **before** creating the session, then
re-init:
```scala
import org.apache.spark.sql.connect.client.ChannelBuilder

ChannelBuilder.MAX_MESSAGE_LENGTH = 512 * 1024 * 1024  // 512 MB

import com.snowflake.snowpark_connect.client.SnowparkConnectSession
val spark = SnowparkConnectSession.builder().appName("MyApp").getOrCreate()
```
Better still, avoid shipping large data inline — write it to a stage/table and
read it back rather than materializing it through the client.

---

## Migration Issues

### Phase 0.5 AST pre-processing failed (no scalafix runner)

**Cause:** `phases_completed["0_5b_scalafix"]["status"] == "failed"` after
running `preprocess_scalafix.py`, which then exits **1**. Phase 0.5 is the
sole, mandatory deterministic pre-processing tier — Scala migrations are
SBT/JVM projects — so a missing runner is a **hard failure, not a skip**, and
the migration MUST NOT advance to Phase 1. Possible reasons in `skip_reason`:

| `skip_reason` | Meaning |
|---|---|
| `scalafix-cli absent; sbt unavailable (...); Coursier unavailable (...)` | No runner found: `scalafix-cli` not on PATH, no `sbt`+JVM, and `cs`/`coursier` absent (bootstrap failed/disabled). See fix below. |
| `cs launch could not start scalafix-cli: <detail>` | Coursier-fallback smoke-check failed.  Check the detail (coordinate not resolved, network error, etc.). |
| `sbt export failed: <detail>` / `scalafix.cli.Cli did not start: <detail>` | The sbt runner could not compile rules / resolve scalafix-cli.  The resolver then falls back to Coursier. |
| `auto-launch disabled (--no-auto-launch)` | Coursier auto-launch was explicitly disabled. |

**Fix (required — the phase is mandatory):**

1. **Easiest:** install `sbt` (the preferred runner — most Scala dev machines
   already have it) plus a JVM.  Phase 0.5 will compile the rules and run
   scalafix through the pinned wrapper (`scripts/scalafix_sbt/`) automatically.
2. Coursier is **auto-bootstrapped** by default — if it failed, check network
   connectivity or install manually: <https://get-coursier.io>
3. Verify the Coursier coordinate resolves: `cs launch ch.epfl.scala:scalafix-cli_2.12.20:0.14.3 -- --version`.
4. Re-run Phase 0.5 (the phase is idempotent — safe to re-run; already-processed files are skipped).
5. To disable the sbt runner (forcing Coursier): `--no-sbt` / `SCOS_SCALAFIX_USE_SBT=0`.
6. To disable Coursier auto-launch: `--no-auto-launch` / `SCOS_SCALAFIX_AUTO_LAUNCH=0`.

**Note:** there is no regex fallback — the regex recipe tier was removed and
Scalafix is the only deterministic pre-processing tier. You must provide a
runner (`sbt`+JVM, `scalafix-cli`, or Coursier) for the migration to proceed.

---

### Error: Analysis returns empty results

**Cause:** The path doesn't contain Spark Scala code or `.scala` files.

**Fix:**
- Verify the path contains `.scala` files
- Check if files contain Spark code (imports from `org.apache.spark`)

---

### Error: Compilation fails after migration

**Cause:** Incomplete edits or malformed code introduced during migration.

**Fix:**
- Review the specific file for incomplete edits
- Check for mismatched brackets or unclosed string literals
- Run `scalac <file>` or use `sbt compile` to identify the exact compilation error
- Verify import statements are syntactically valid

---

### Error: Import errors after migration

**Cause:** Unsupported imports remain or Snowpark Connect session initialization is incorrect.

**Fix:**
- Ensure unsupported imports (`org.apache.spark.graphx`, `delta`) are removed
- Verify Snowpark Connect session initialization is correct:
  ```scala
  import org.apache.spark.sql.SparkSession
  import org.apache.spark.sql.connect.client.REPLClassDirMonitor

  val spark = SparkSession.builder()
    .remote("sc://localhost:15002")
    .getOrCreate()
  ```

---

### Error: Scala version mismatch

**Cause:** The workload is built with Scala 2.13 but Snowpark Connect defaults to 2.12.

**Fix:** Set the Scala version configuration:
```scala
val spark = SparkSession.builder()
  .remote("sc://localhost:15002")
  .config("snowpark.connect.scala.version", "2.13")
  .getOrCreate()
```

Or via session config:
```scala
spark.conf.set("snowpark.connect.scala.version", "2.13")
```

---

## SQL patterns: native pass-through and counts

### Pattern: `safe_count` and `safe_checkpoint` via SnowflakeSession

Driver-side `df.count()` can be slow/hang on large data. A common idiom is to run
the count (or a checkpoint) natively through Snowflake:

```scala
import com.snowflake.snowpark_connect.client.SnowflakeSession
val sf = new SnowflakeSession(spark)

// safe_checkpoint: materialize via CTAS, then read back (always works)
sf.sql("CREATE OR REPLACE TEMP TABLE ckpt AS SELECT * FROM (<query>)")
val dfCkpt = spark.table("ckpt")

// safe_count: native COUNT(*) against a registered view
df.createOrReplaceTempView("v")
val n = sf.sql("SELECT COUNT(*) FROM v").collect()(0)(0)
```

**Caveat (important):** `safe_count` against a Spark **TempView** fails by default
with `TABLE_OR_VIEW_NOT_FOUND` — a Spark `createOrReplaceTempView` is client-side
only, so native SQL can't see it. Either:
- set `spark.conf.set("snowpark.connect.temporary.views.create_in_snowflake", "true")`
  **before** creating the view (so it becomes a real Snowflake object), or
- use the `safe_checkpoint` CTAS form, which always materializes a real table.

`safe_checkpoint` (CTAS) works regardless of the config; prefer it when in doubt.

---

### Pattern: QUALIFY must run as native Snowflake SQL

`QUALIFY` is a Snowflake SQL extension and is **not** in the Spark SQL grammar, so
`spark.sql("... QUALIFY ...")` raises `PARSE_SYNTAX_ERROR` (the Spark 3.5 parser
rejects it). Run it through native pass-through instead:

```scala
import com.snowflake.snowpark_connect.client.SnowflakeSession
val sf = new SnowflakeSession(spark)
val rows = sf.sql("""
    SELECT * FROM t
    QUALIFY ROW_NUMBER() OVER (PARTITION BY k ORDER BY ts DESC) = 1
""").collect()
```

The migration skill's deterministic rewrite (`detector:qualify_unsupported` →
`rw_qualify` in `scripts/data/sql_rules.json`) instead rewrites a `QUALIFY` clause
into an equivalent `ROW_NUMBER()` subquery so it stays valid Spark SQL — use that
when you want to keep the query in `spark.sql(...)`.

---

## Validation Issues

### Early preflight fails at init (JDK / sbt / analyze jar)

**Cause:** `scos_state.py preflight --phase a` (Step 0.5) and `init` /
`prepare-batches` hard-fail when Java 8/11/17 cannot be resolved, `sbt` is
missing, or the environment is otherwise unfit — before survey/synthesize so
agents do not burn tokens on a doomed run.

**Fix:**
```bash
uv run --project <SKILL_DIRECTORY>/.. python <SKILL_DIRECTORY>/scripts/scos_state.py \
  preflight --conv-root <ws> --phase a
# Remediate from the printed list, then re-run. Common fixes:
#   cs java-home --jvm temurin:17   # or install openjdk-17
#   install sbt; ensure scos-analyze.jar via sbt assembly in harness-scala/control/
```

---

### Honest prewarm: `venv_prewarmed` not set / prewarm exits non-zero

**Cause:** `scos_state.py prewarm` no longer marks success when JDK cannot be
resolved or `sbt` is absent. A stale “prewarmed” milestone used to hide cold
kits until Phase A.

**Fix:** Run prewarm **after init / prepare-batches and before Phase A**
(overlap with analyze/patch-author; never after adapt). Fix JDK/sbt, then:
```bash
python scripts/scos_state.py prewarm --conv-root <ws>
```
Kit `Test/compile` failure also hard-fails and does **not** set `venv_prewarmed`
— fix the staged kit (or re-copy from `harness-scala/kit/`) and re-run prewarm.

---

### Thin jar + dependency classpath (Phase A and Phase B)

**Cause:** Workloads often lack `sbt-assembly` / produce thin jars. Phase A/B
used to abort or CNF when assembly-style discovery failed.

**Fix:** Prefer fat/assembly when available. Otherwise `build-doctor` /
`run-phase-a` / `run-phase-b` fall back to package + **filtered** runtime
classpath (Spark / Delta / Hadoop / provided-scope / GAV-path artifacts
excluded so they do not collide with the kit’s local Spark). Thin jar +
non-empty classpath is a **valid** input for **both** phases; hard-die when
**no jar** exists **or** a thin jar has an empty filtered classpath.
Gradle uses an init-script `scosPrintRuntimeClasspath` task to export real
paths (not the text dependency tree).

```bash
python scripts/scos_state.py build-doctor --conv-root <ws> --side source
python scripts/scos_state.py build-doctor --conv-root <ws> --side migrated
python scripts/scos_state.py run-phase-a --conv-root <ws>
python scripts/scos_state.py run-phase-b --conv-root <ws>
```

---

### Build-doctor (Scala analogue of PySpark `seed-venv`)

**Cause:** Agents need a pre-Phase-A / pre-Phase-B “prove the workload builds”
command without running trials.

**Fix:**
```bash
python scripts/scos_state.py build-doctor --conv-root <ws> --side source
python scripts/scos_state.py build-doctor --conv-root <ws> --side migrated
```
JSON report only — no tests. Classifies unresolved dependency / compile /
no-build-tool / thin-jar-empty-classpath failures. Reports kept/dropped
classpath entry counts. Use before `run-phase-a` / `run-phase-b`, or rely on
the same ladder inside those commands.

---

### Mock-guard hard-fail (datagen import / verify)

**Cause:** Unseedable mocks or a failed `datagen` import used to soft-skip into
empty/stale baselines and skip pressure.

**Fix:** `run-phase-a` hard-fails with an actionable problem list. Repair
`analysis.json` schemas → `schema_mine.py` → `datagen` → `--verify`. Never use
`--no-mock-guard` on production validation runs. Mock/schema gaps are
**never** `phase_a_skipped` reasons — only named dialect constructs are.

---

### Host-aware parallelism (OOM / too many forked Sparks)

**Cause:** Default pool × parallelism can fork many local Spark JVMs on small
hosts.

**Fix:** When `--parallelism` is omitted, `run-phase-a` / `run-phase-b`
auto-cap from available RAM (`<8 GB` → 1, `<16 GB` → 2, else → 4). Explicit
`--parallelism N` always wins. Leave `--pool-size 3` as the Scala pool default;
lower further only for Snowflake rate-limits. Log/report when capped — that is
harness friction, not a workload defect.

---

### Empty sinks / filter-join mock enrichment

**Cause:** Mined schemas missing `values` / `join_key` → filters match nothing
and joins do not overlap → empty sinks → analysis-repair loops.

**Fix:** `ast_to_analysis` (regex hedge) and Scalameta `filters`/`joins` facts
enrich column `values` and `join_key`. Confirm with `datagen --peek` and add
literal domains / join edges in `analysis.json` when still missing. Complex
predicates stay `llm_todo` — do not invent values.

---

### Phase A produces no baseline / every trial ends `passed_no_baseline` (JDK 21)

**Cause:** Phase A runs the original workload on a local **Spark 3.5** session, which
supports **Java 8/11/17 only**. On a Java 21 host (e.g. Ubuntu 24.04's
`default-jdk` → OpenJDK 21) the local `SparkSession`/Arrow init throws
`InaccessibleObjectException` / `NoSuchMethodError` at startup — before any table is
captured — so `sbt test` writes no `_index.json` and every trial degrades to a
no-baseline pass. The verdict then flips to `passed` if you re-run on a fixed JVM,
which is the idempotency symptom.

**Fix:** No manual JDK pin needed — Step 0.5 `scos_state.py preflight` and
`run-phase-a`/`run-phase-b` resolve a Java 8/11/17 JDK, auto-provisioning
**Temurin 17** via Coursier (`cs java-home --jvm temurin:17`) when none is
installed, and pin the sbt build + `Test/fork` JVM to it. If preflight cannot
find or provision a compatible JDK it **hard-fails** (exit 3) with remediation
instead of producing a no-baseline pass:

```bash
python scripts/scos_state.py preflight --conv-root <ws> --phase a   # exit 0 = ready
# or install one directly:
apt-get install -y openjdk-17-jdk-headless   # Debian/Ubuntu
```

A whole-batch “no source jar” run hard-fails; thin jar + classpath is not that
failure. An environment failure is never recorded as `phase_a_skipped`. The only
route to a no-baseline verdict is an explicit, reasoned
`record-trial-status phase_a_skipped --reason <specific unsupported construct>`.

---

### `ParquetFileFormat.$deserializeLambda$` failures on JVM 17 + Spark 3.5

**Cause:** `SerializedLambda` / `URLClassLoader` conflict during Phase A when
`ParquetFileFormat`'s lambda closure is deserialized through the kit's classloader
(`ReflectionEntrypoint`). Manifests as a `ClassCastException` or `AbstractMethodError`
inside `$deserializeLambda$` that halts the workload before any tables are captured.

**Fix:** Set `SCOS_PHASE_A_SUBPROCESS=1` in the environment before running
`scos_state.py run-phase-a` / the sbt test invocation. This runs the Phase A
workload in a child JVM (`SubprocessLauncher`) instead of in-process via
`ReflectionEntrypoint`, isolating the lambda deserialization from the kit's
classloader. Default is `0` (in-process, faster — no extra JVM spawn). The
same flag governs Java workloads that reuse this shared Scala test kit.

```bash
SCOS_PHASE_A_SUBPROCESS=1 python scripts/scos_state.py run-phase-a --conv-root $CONVERSION_ROOT
```

---

### ImportError / ClassNotFoundException

**Cause:** The workload class or module cannot be found on the classpath.

**Fix:** Ensure:
- The compiled `.class` files are on the classpath
- `build.sbt` includes the `spark-connect-client-jvm` dependency:
  ```scala
  libraryDependencies += "org.apache.spark" %% "spark-connect-client-jvm" % "3.5.6"
  ```
- JVM options include module compatibility flags:
  ```scala
  javaOptions ++= Seq("--add-opens=java.base/java.nio=ALL-UNNAMED")
  ```

---

### UDF class not found on server

**Cause:** UDF or custom code references classes not available on Snowflake's server-side worker.

**Fix:** Apply the approach from `references/scala/udf-dependencies.md`:
- **Option 1 (Preferred):** Register a `REPLClassDirMonitor` to monitor and upload class files
- **Option 2:** Upload JAR dependencies via `spark.addArtifact()`
- **Option 3:** Use staged JARs via `snowpark.connect.udf.java.imports`

---

### Schema mismatch at runtime

**Cause:** Synthetic data schema doesn't match what the workload expects.

**Fix:** Re-check column names and types used downstream. In Scala, pay attention to:
- Implicit type conversions (ByteType/ShortType/IntegerType → LongType in SCOS)
- StructType in UDFs (SCOS returns `dict` / `Map` instead of `tuple` / `Row`)
- NullType is inferred as StringType in SCOS

---

### Stage creation fails

**Cause:** Warehouse is inactive or user lacks permissions.

**Fix:**
- Verify the warehouse is active: `ALTER WAREHOUSE <name> RESUME`
- Verify the user has `CREATE STAGE` privilege
- Check that the database and schema in the active session are accessible

---

### spark.read with wildcard pattern fails (AssertionError, ERROR CODE: 5001)

**Cause:** Wildcard/glob patterns (`*.json`, `*.csv`, `*.parquet`) in file read paths are not supported in SCOS.

**Example error:**
```
SparkConnectGrpcException: AssertionError (ERROR CODE: 5001)
```

Triggered by code like:
```scala
val df = spark.read.json("@MY_STAGE/*.json")
```

**Fix:** Replace wildcard reads with explicit file lists:
```scala
// BEFORE (fails):
val df = spark.read.json("@MY_STAGE/*.json")

// AFTER (works):
val df = spark.read.json(
  "@MY_STAGE/file1.json",
  "@MY_STAGE/file2.json",
  "@MY_STAGE/file3.json"
)
```

---

### Error: UNSUPPORTED_DATA_TYPE on map column subscript

**Cause:** Using `mapCol(col("key"))` (apply-style indexing) to index a map column with another `Column` as the key. In Spark Connect, `Column.apply` only accepts literal values, not `Column` expressions.

**Example error:**
```
UNSUPPORTED_DATA_TYPE: Unsupported DataType 'Column'
```

Triggered by code like:
```scala
val categoryMap = map(lit("A"), lit(1), lit("B"), lit(2))
val result = df.withColumn("val", categoryMap(col("category_code")))
```

**Fix:** Replace apply-style indexing with `element_at()`, which accepts `Column` arguments and works in both classic and Connect modes:
```scala
import org.apache.spark.sql.functions.element_at

// BEFORE (fails in Connect):
val result = df.withColumn("val", categoryMap(col("category_code")))

// AFTER (works in both):
val result = df.withColumn("val", element_at(categoryMap, col("category_code")))
```

Apply-style indexing with **literal** keys (e.g., `mapCol("some_string")`) still works.

---

## UDF & Serialization Issues

### ClassNotFoundException for UDF classes

**Cause:** Spark Connect serializes UDF closures and sends them to the Snowflake server. If the closure references classes not available on the server, it throws ClassNotFoundException.

**Fix:** Register a class finder or upload dependencies:

```scala
// Option 1: REPLClassDirMonitor
import org.apache.spark.sql.connect.client.REPLClassDirMonitor
val classFinder = new REPLClassDirMonitor("/absolute/path/to/target/scala-2.12/classes")
spark.registerClassFinder(classFinder)

// Option 2: Upload JAR
spark.addArtifact("/absolute/path/to/dependency.jar")

// Option 3: Staged JAR
spark.conf.set("snowpark.connect.udf.java.imports",
  "[@mystage/dependency.jar, @db.schema.stage/other_dependency.jar]")
```

---

### StructType differences in UDFs

**Cause:** Snowpark Connect converts StructType to `dict`/`Map` in UDFs, not `tuple`/`Row` like native Spark.

**Fix:** Access struct fields by name (`"_1"`, `"_2"`, or field names) instead of numeric index:
```scala
// BEFORE (Spark): e(0)
// AFTER (SCOS): e("_1") or e("col1")
```

---

### Iterator type not supported in UDFs

**Cause:** `Iterator` is not supported as an input or return type in SCOS UDFs.

**Fix:** Rewrite to use non-iterator patterns:
```scala
// BEFORE (not supported):
def func(iterator: Iterator[Row]): Iterator[Row] = { ... }

// AFTER: Use mapInPandas or applyInPandas patterns instead, or
// restructure to work on individual rows/batches
```

---

## Data Source Issues

### Unsupported save modes

**Cause:** `Append` and `Ignore` save modes are not supported for CSV, JSON, Parquet, Text, and XML in SCOS.

**Fix:** Use `Overwrite` or `ErrorIfExists` save modes:
```scala
// BEFORE (not supported):
df.write.mode("append").csv("@STAGE/output")

// AFTER:
df.write.mode("overwrite").csv("@STAGE/output")
```

### Unsupported file formats

**Cause:** Avro and ORC file formats are not supported in SCOS.

**Fix:** Convert to Parquet format. Note: downstream consumers expecting the original format must be updated too.
```scala
// BEFORE:
val df = spark.read.format("avro").load("data.avro")

// AFTER:
val df = spark.read.parquet("data.parquet")
```

---

## Cross-File Consistency Issues

### Error: method/parameter not found after migration

**Cause:** A method signature was changed in one file (e.g., removing `hdfs: FileSystem` parameter) but callers in other files were not updated. This is the most common cause of compilation failures after migration.

**Fix:** After every signature change, grep the entire codebase for callers:
```bash
grep -rn "methodName" <MIGRATED>/ --include="*.scala"
```
Update every call site to match the new signature.

---

### Error: Catalyst class not found (QualifiedTableName, TableIdentifier, etc.)

**Cause:** `org.apache.spark.sql.catalyst.*` classes are Spark internals not exposed via Spark Connect.

**Fix:** Define a local replacement case class with the same interface:
```scala
case class QualifiedTableName(database: String, name: String) {
  override def toString: String = s"$database.$name"
}
```

---

### Error: Hadoop / FileSystem class not found

**Cause:** `org.apache.hadoop.*` classes are not available in SCOS. Code using `FileSystem`, `Path`, `hadoopConfiguration` will not compile.

**Fix:** Remove all Hadoop imports and usages. Replace HDFS operations with Snowflake stage operations or DataFrame I/O.

---

### Error: Hive Warehouse Connector not found

**Cause:** `com.hortonworks.spark.sql.hive.*` or `enableHiveSupport()` is not available in SCOS.

**Fix:** Remove all Hive integration code. Hive tables must be migrated to Snowflake tables separately.

---

### Error: pom.xml / build.sbt version incompatibility

**Cause:** The build file still declares Scala 2.11, Spark 2.x, or Java 8 targets.

**Fix:** Update to Scala 2.12+, Spark 3.5+, Java 11+. Replace `spark-core`/`spark-sql` with `spark-connect-client-jvm`. Remove Hive, Hadoop, and incompatible library dependencies.

---

### Error: Tests fail with "Connection refused" on sc://localhost:15002

**Cause:** Test files were converted to use Spark Connect remote URL, but no server is running.

**Fix:** Test files should keep `master("local[*]")` for local/CI execution. Only production entrypoints should use Snowpark Connect session initialization.

---

## Snowpark Connect Scala-Specific Issues

### Error: Maven artifact `org.apache.spark:spark-connect-client-jvm` not found

**Cause:** The dependency used for open-source Spark Connect is not the correct artifact for Snowpark Connect. The Snowflake-specific client has a different Maven coordinate.

**Fix:** Replace the OSS Spark Connect dependency with the Snowflake-published artifact:

```xml
<!-- WRONG (OSS Spark Connect): -->
<dependency>
  <groupId>org.apache.spark</groupId>
  <artifactId>spark-connect-client-jvm_2.12</artifactId>
  <version>3.5.6</version>
</dependency>

<!-- CORRECT (Snowpark Connect): -->
<dependency>
  <groupId>com.snowflake</groupId>
  <artifactId>snowpark-connect-java-client_2.12</artifactId>
  <version><!-- use latest published version --></version>
</dependency>
```

For sbt:
```scala
// WRONG:
"org.apache.spark" %% "spark-connect-client-jvm" % "3.5.6"

// CORRECT (Scala 2.12):
"com.snowflake" % "snowpark-connect-java-client_2.12" % "<latest>"
// CORRECT (Scala 2.13):
"com.snowflake" % "snowpark-connect-java-client_2.13" % "<latest>"
```

Detect the correct suffix from `scalaVersion` in your build file:
```scala
val scalaShort = scalaVersion.value.split('.').take(2).mkString(".")
// → "2.12" or "2.13"
```

---

### Error: `SnowparkConnectServerException` thrown at startup

**Cause:** The Snowpark Connect session cannot reach the server. The client tries multiple resolution strategies in order:
1. `SPARK_REMOTE` environment variable (e.g. `sc://my-account.snowflakecomputing.com`)
2. `SNOWPARK_SUBMIT_JOB=true` — sidecar mode connecting to `sc://localhost:15002`
3. Auto Python venv launch (local dev mode)

If none of these are configured correctly, the session throws at `getOrCreate()`.

**Fix:**
- For Snowflake-hosted execution: set `SPARK_REMOTE` to your account endpoint before launching.
- For local development: ensure the Snowpark Connect Python venv is set up and `SNOWPARK_CONNECT_PYTHON_VENV` points to it.
- For sidecar/job submission: set `SNOWPARK_SUBMIT_JOB=true`.

```bash
# Option 1: explicit endpoint
export SPARK_REMOTE="sc://<account>.snowflakecomputing.com"

# Option 2: local dev venv
export SNOWPARK_CONNECT_PYTHON_VENV="/path/to/scos_venv"

# Option 3: sidecar mode
export SNOWPARK_SUBMIT_JOB=true
```

Session builder:
```scala
import com.snowflake.snowpark_connect.client.SnowparkConnectSession

val spark = SnowparkConnectSession.builder()
  .appName("MyApp")
  .getOrCreate()
```

---

### Error: Apache Arrow reflection failure at startup (`InaccessibleObjectException`)

**Cause:** Java 9+ module system blocks reflective access that Apache Arrow requires internally. Missing `--add-opens` JVM flags cause the session to fail at initialization.

**Example error:**
```
java.lang.reflect.InaccessibleObjectException: Unable to make ... accessible:
module java.base does not "opens java.nio" to unnamed module
```

**Fix:** Add all three required `--add-opens` flags to your JVM launch options:

**sbt** (`build.sbt`):
```scala
Test / javaOptions ++= Seq(
  "--add-opens=java.base/java.nio=org.apache.arrow.memory.core,ALL-UNNAMED",
  "--add-opens=java.base/jdk.internal.misc=org.apache.arrow.memory.core,ALL-UNNAMED",
  "--add-opens=jdk.unsupported/sun.misc=org.apache.arrow.memory.core,ALL-UNNAMED"
)
```

**Maven** (`pom.xml` inside `maven-surefire-plugin`):
```xml
<configuration>
  <argLine>
    --add-opens=java.base/java.nio=org.apache.arrow.memory.core,ALL-UNNAMED
    --add-opens=java.base/jdk.internal.misc=org.apache.arrow.memory.core,ALL-UNNAMED
    --add-opens=jdk.unsupported/sun.misc=org.apache.arrow.memory.core,ALL-UNNAMED
  </argLine>
</configuration>
```

**Gradle** (`build.gradle`):
```groovy
test {
  jvmArgs '--add-opens=java.base/java.nio=org.apache.arrow.memory.core,ALL-UNNAMED',
          '--add-opens=java.base/jdk.internal.misc=org.apache.arrow.memory.core,ALL-UNNAMED',
          '--add-opens=jdk.unsupported/sun.misc=org.apache.arrow.memory.core,ALL-UNNAMED'
}
```

**Gradle Kotlin DSL** (`build.gradle.kts`):
```kotlin
tasks.test {
  jvmArgs(
    "--add-opens=java.base/java.nio=org.apache.arrow.memory.core,ALL-UNNAMED",
    "--add-opens=java.base/jdk.internal.misc=org.apache.arrow.memory.core,ALL-UNNAMED",
    "--add-opens=jdk.unsupported/sun.misc=org.apache.arrow.memory.core,ALL-UNNAMED"
  )
}
```

---

### Error: `spark.sql("USE DATABASE …")` has no effect on SCOS

**Cause:** In native Spark, `spark.sql("USE DATABASE foo")` updates the session's default database. In Snowpark Connect, `spark.sql()` is forwarded to Snowflake's SQL engine but does not persist session-level state the same way — the USE statement is executed but the session context in the client may not reflect it for subsequent DataFrame operations.

**Fix:** Use `SnowflakeSession` to set session context explicitly:

```scala
import com.snowflake.snowpark_connect.client.SnowparkConnectSession
import com.snowflake.snowpark_connect.client.SnowflakeSession

val spark = SnowparkConnectSession.builder().appName("MyApp").getOrCreate()
val sf = new SnowflakeSession(spark)

// Instead of: spark.sql("USE DATABASE mydb")
sf.useDatabase("mydb")

// Instead of: spark.sql("USE SCHEMA myschema")
sf.useSchema("myschema")

// Instead of: spark.sql("USE ROLE myrole")
sf.useRole("myrole")

// Instead of: spark.sql("USE WAREHOUSE mywarehouse")
sf.useWarehouse("mywarehouse")

// Direct SQL pass-through still works for non-USE statements:
sf.sql("SELECT CURRENT_DATABASE()")
```
