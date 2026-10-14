# UDF Dependencies in SCOS — Scala

When SCOS executes Scala UDFs, the function closure is serialized and sent to Snowflake's server-side worker. Unlike PySpark (which uses cloudpickle), Scala UDFs rely on Java serialization and Spark Connect's class upload mechanism. Two types of errors can occur:

1. **ClassNotFoundException** — The UDF references classes not available on the server
2. **JAR dependency not available** — The UDF imports third-party libraries that don't exist on the server

This guide covers how to resolve both.

---

## Quick Reference: Config Keys & Methods

| Config Key / Method | Purpose |
|---|---|
| `spark.registerClassFinder(classFinder)` | Monitor and auto-upload compiled classes |
| `spark.addArtifact("path/to/jar")` | Upload JAR dependencies to the server |
| `snowpark.connect.udf.java.imports` | Stage-based JAR imports: `[@stage/dep.jar]` |
| `snowpark.connect.udf.packages` | Python packages (for PySpark UDFs called from Scala) |
| `snowpark.connect.scala.version` | Set Scala version: `"2.12"` (default) or `"2.13"` |

---

## UDF-backed builtin functions (performance note)

A handful of Spark **builtin** functions have no native Snowflake SQL
equivalent, so SCOS implements them with a server-side Python/Java UDF. They
produce correct results but run **slower** than native expressions and carry the
usual UDF overhead (serialization, per-row execution). Prefer a native
alternative on hot paths; otherwise they are safe to keep.

**Genuinely UDF-backed in SCOS** (source: `expression/map_unresolved_function.py`):

| Function | Notes |
|---|---|
| `crc32` | full UDF |
| `format_number` | full UDF |
| `format_string` / `printf` | full UDF |
| `from_csv` | full UDF |
| `map_concat` | full UDF |
| `map_from_arrays` | full UDF |
| `bit_count` | UDF only for integral inputs |
| `encode` | UDF only for the bare UTF-16 charset |
| `transform` | UDF only for the 2-arg *index* variant (`transform(col, (x, i) => …)`) |

**Commonly assumed slow but actually native** (do *not* rewrite these for
performance — they compile to native Snowflake SQL): `array_repeat`,
`percentile_approx`, `map_values`, `map_filter`, `map_zip_with`,
`transform_keys`, `transform_values`, `xxhash64`, `schema_of_csv`,
`schema_of_json`, `bin`, `conv`.

> This list reflects the live engine; re-derive from
> `map_unresolved_function.py` if SCOS changes, rather than hand-editing drift in.

The Phase 0.5 Scalafix rule `ScosApproxCountDistinctDropRsd` and the annotate
rules handle the deterministic call sites; the analyzer flags the
**unconditionally** UDF-backed calls (`crc32`, `format_number`,
`format_string`/`printf`, `from_csv`, `map_concat`, `map_from_arrays`) with a
`// SCOS:` perf hint at the call site. The three **conditional** entries
(`bit_count`, `encode`, `transform`) are intentionally *not* auto-flagged —
whether they fall on the UDF path depends on argument types/shape that can't be
determined statically, so they are left to the analyzer/author to judge.

---

## Hard rule: never reimplement library functions

When a UDF needs a library that is not available on the SCOS worker
(e.g. a Java/Scala analytics library, a geohashing utility, a custom Kalman
filter), the only acceptable fixes are the class-upload / JAR-staging options
below. **Do NOT rewrite the library's logic in pure Scala/stdlib
equivalents.** Inlining `GeoHash.encode` as a base32 bit-packer, replacing a
`BallTree.query` with a distance loop, etc. silently changes correctness
semantics and defeats the whole point of behavioral parity testing.

If none of the upload / stage options is feasible for a given library, stop and
flag the UDF for human review. The validation skill should mark the trial
`hard_stuck` rather than accept a reimplementation.

---

# Part 1: Class Upload for UDFs

By default, the server-side worker only has the Spark Connect client classes available. Any custom classes (your UDFs, data classes, helper objects) must be uploaded.

## Option 1 (Recommended): REPLClassDirMonitor

Monitor your compiled classes directory and automatically upload new/changed classes:

```scala
import com.snowflake.snowpark_connect.client.SnowparkConnectSession
import org.apache.spark.sql.connect.client.REPLClassDirMonitor

val spark = SnowparkConnectSession.builder().appName("MyApp").getOrCreate()
val classFinder = new REPLClassDirMonitor("/absolute/path/to/target/scala-2.12/classes")
spark.registerClassFinder(classFinder)
```

This is the recommended approach for development. The class finder monitors the directory and uploads any new or modified class files to the Spark Connect server.

**When to use:**
- During development and testing
- When UDFs reference multiple classes in your project
- When classes change frequently (iterative development)

## Option 2: Upload JAR Artifacts

For production or packaged applications, upload the compiled JAR:

```scala
spark.addArtifact("/absolute/path/to/my-app.jar")
```

You can upload multiple JARs if your UDFs depend on external libraries:

```scala
spark.addArtifact("/path/to/my-app.jar")
spark.addArtifact("/path/to/dependency.jar")
```

**When to use:**
- Production deployments
- When the application is packaged as a JAR
- When you want to include the workload JAR itself (if a class finder is not used)

## Option 3: Staged JARs

If JAR files are already uploaded to a Snowflake stage:

```scala
spark.conf.set("snowpark.connect.udf.java.imports",
  "[@mystage/dependency.jar, @db.schema.stage/other_dependency.jar]")
```

**When to use:**
- JARs are already available in Snowflake stages
- Shared dependencies used across multiple workloads
- Production environments where JARs are managed centrally

---

# Part 2: Self-Contained UDFs

For simple UDFs, keep all logic self-contained to avoid class upload requirements:

```scala
import org.apache.spark.sql.functions.udf

val normalize = udf((value: String) => {
  if (value == null) ""
  else value.trim.toLowerCase
})

df.select(normalize(col("name")).alias("normalized_name"))
```

When the UDF is simple enough that all logic fits in one anonymous function, serialization typically works without additional class uploads.

**When to use:**
- UDF logic is straightforward (transformation, validation, formatting)
- No calls to helper methods defined elsewhere
- No imports of custom classes

---

# Part 3: Complex UDF Patterns

## Case Classes in UDFs

If your UDF uses case classes, ensure they are uploaded:

```scala
case class Config(threshold: Double, mode: String)

val processWithConfig = udf((value: Double, configJson: String) => {
  // Parse config inline to avoid serialization of Config class
  val threshold = configJson.split(",")(0).toDouble
  value > threshold
})
```

Alternatively, register the class finder (Option 1) to auto-upload the case class.

## Broadcast Variables

Broadcast variables work differently in Spark Connect. If your UDF references broadcast data, capture it in the closure:

```scala
// BEFORE (may fail):
val broadcastMap = spark.sparkContext.broadcast(myMap)
val myUdf = udf((key: String) => broadcastMap.value.getOrElse(key, "default"))

// AFTER (capture data directly):
val localMap = myMap  // capture the map value in the closure
val myUdf = udf((key: String) => localMap.getOrElse(key, "default"))
```

---

# Part 4: Scala Version Compatibility

By default, Snowpark Connect for Spark uses Scala 2.12. If your workload is built with Scala 2.13:

```scala
import com.snowflake.snowpark_connect.client.SnowparkConnectSession

// In session builder
val spark = SnowparkConnectSession.builder()
  .appName("MyApp")
  .config("snowpark.connect.scala.version", "2.13")
  .getOrCreate()

// Or via session configuration after creation
spark.conf.set("snowpark.connect.scala.version", "2.13")
```

Ensure your `build.sbt` uses the matching Scala version:
```scala
scalaVersion := "2.13.12"  // must match the config setting
```

---

## Decision Flowchart

```
UDF needs external code?
├── No → Keep it self-contained (inline closure)
└── Yes
    ├── Code is in your project's compiled classes?
    │   └── Yes → Option 1: REPLClassDirMonitor (dev) or Option 2: addArtifact (prod)
    ├── Code needs third-party JAR?
    │   └── Yes → spark.addArtifact("/path/to/dep.jar")
    │            OR snowpark.connect.udf.java.imports for staged JARs
    └── Code uses broadcast variables?
        └── Yes → Capture data in closure instead of using broadcast
```

---

## Key Differences from PySpark UDFs

| Aspect | PySpark | Scala |
|--------|---------|-------|
| Serialization mechanism | cloudpickle | Java serialization |
| Custom code upload | `snowpark.connect.udf.python.imports` | `REPLClassDirMonitor` / `addArtifact` |
| Package management | `snowpark.connect.udf.packages` | JAR-based (`addArtifact` or staged JARs) |
| Module patching workaround | `__module__ = "__main__"` | Not applicable — use class finder |
| StructType in UDF | Converts to `tuple` (Spark) / `Row` | Converts to `dict`/`Map` in SCOS |
