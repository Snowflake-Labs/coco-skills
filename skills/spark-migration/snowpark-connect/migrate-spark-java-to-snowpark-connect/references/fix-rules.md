# SCOS Fix Rules Reference — Java

Rules for fixing SCOS compatibility issues found during analysis of Java Spark workloads.
The fixer agent reads this document when applying fixes to migrated Java files.

**Related references:**
- `../../references/java/rdd-conversion.md` — Java RDD-to-DataFrame conversion rules (required for Rule 2)
- `../../references/java/udf-dependencies.md` — UDF JAR/class upload strategies for Java (required for Rule 10)
- `../../references/java/ewi-codes.md` — Official SMA EWI code scheme for Java (required for `// SCOS:` comment tagging)

---

## Pre-Fix: Read EWI Codes

Before applying fixes, read `../../references/java/ewi-codes.md`. When adding `// SCOS:` comments,
include the relevant EWI code. For example:
- `// SCOS: [SPRKCNTSCL1500] JavaRDD operation converted to Dataset`
- `// SCOS: TODO - [SPRKCNTSCL2500] ML element requires manual migration`

---

## Per-Issue Processing

For EACH issue in `analysis.json`:
1. **Locate** the code at `file` and `lines`.
2. **Assess** the `final_risk` value.
3. **Apply** the appropriate action.
4. **Document**: add a `// SCOS:` comment **or** a `resolution` verdict in `analysis.json`.

---

## Rules for Fixing based on Risk Score

1. **Must Fix (`final_risk` >= 0.7)**: Apply fix. If impossible, add `// SCOS: TODO - <explanation>`. If genuinely safe, record `resolution: "safe"` **with** `resolution_reason`.
2. **Should Fix (0.3 <= `final_risk` < 0.7)**: Apply fix if suggested, else `// SCOS: TODO`.
3. **Fix if possible (`final_risk` < 0.3)**: Fix if possible, else `resolution: "safe"` or `// SCOS: <explanation>`.

---

## Recording resolutions in `analysis.json`

Add `resolution` and `resolution_reason` to the issue object:

| Field | Values | Meaning |
|---|---|---|
| `resolution` | `"fixed"` | Fix applied (also leave `// SCOS:` comment). |
| | `"todo"` | Needs manual follow-up (also leave `// SCOS: TODO`). |
| | `"safe"` | Reviewed; no action needed. **Requires `resolution_reason`**. |
| | `"perf"` | Performance tip (also leave `// SCOS: Performance tip` comment). |
| `resolution_reason` | free text | Why. **Mandatory for `"safe"`**. |

---

## Comment prefixes (Java uses `//` same as Scala):
- `// SCOS: <explanation>` — fix applied or reviewed
- `// SCOS: [SPRKCNTSCL####] <explanation>` — fix carrying its EWI code
- `// SCOS: TODO - <explanation>` — requires manual review
- `// SCOS: Performance tip - <explanation>` — optimization recommendation

### MUST NOT undo deterministic pre-processing (binding)

Phase 0.5c (JavaParser AST rules) already applied byte-perfect rewrites. You **MUST NOT**
modify any region with:
- `// SCOS-RECIPE-PRESERVED-CONFIG: k=v` markers
- `// SCOS-RECIPE-INSERT-AFTER-BUILDER: ...` hints
- Any `// SCOS-WARN:` or `// SCOS-TODO:` emitted by a javaparser rule

Treat `migration_state.json :: recipe_edits` lines with `recipe_id` in the
`javaparser:` namespace as off-limits.

---

## General Rules

### Rule 1: Use the Tool's Fix
If the issue provides a `fix` value, use it.

### Rule 2: Handle RDDs / JavaRDD
Java RDD usage (`JavaRDD`, `JavaPairRDD`, `JavaSparkContext`) splits into three buckets.
**Read** `../../references/java/rdd-conversion.md` for the full rules.

- **Unsupported (Bucket A)**: preserve and prepend `// SCOS: [SPRKCNTSCL1500] ... manual refactor required`
- **Drop-the-hop (Bucket B)**: drop `.toJavaRDD()` hop and call method on `Dataset<Row>`
- **Convertible (Bucket C)**: rewrite to `spark.createDataFrame(list, schema)` or `groupBy().agg()`

**Route on the reference's verdict tag.** `references/java/rdd-conversion.md` (§6–§10) tags every recipe with a **verdict** — route on it:
- **[Native] / [Workaround]** → **apply** the rewrite (no TODO). Tag `// SCOS: [SPRKCNTSCL1500]` for a JavaRDD / `JavaSparkContext` / accumulator element, or `// SCOS: [SPRKCNTSCL1000]` for a generic unsupported element (no-op config, custom UDAF, `observe`).
- **[Silent-diff]** → apply the rewrite **and** add a `// SCOS:` guard/note for the drift (e.g. `count(functions.lit(1))` vs `count(col)`, non-identity `fold`/`foldByKey` seed applied once, `functions.round(product(...)).cast("long")` for exact integer products, empty→NULL vs raise, `collect_set`/`collect_list` drop NULLs & lose order, `repartition(n, col)` is a no-op hint). Full list in §4.
- **[Partial]** → apply the closest form **and** `// SCOS: TODO` for the aspect SCOS cannot reproduce (`mapPartitionsWithIndex` split index, `partitionBy`/`repartitionAndSortWithinPartitions` key co-location/ordering, real `StorageLevel` via `getStorageLevel`, external `saveAsObjectFile`/`saveAsSequenceFile` reads, `toDebugString` lineage).
- **[Hard gap]** (⚠perm/⚠ns) → `// SCOS: TODO` naming the op + the Snowflake-native alternative. **Three** accumulator hard gaps are **permanent** (⚠perm): `foreachPartition` sinks (§7.1), cache-hit counting (§7.2), and mid-job `acc.value()` progress polling (§7.3). `writeStream().foreachBatch` cross-batch state is **not currently supported** (⚠ns) — [Partial], with a manual per-batch-loop workaround (§7.4), not a permanent no-equivalent.
- **Delete, don't migrate**: `acc.reset()` across stages, merge/collision counters kept only for the Spark UI, `df.observe(...)` metrics (no-op on SCOS — `// SCOS: [SPRKCNTSCL1000]`), and named accumulators for the Spark UI have no meaning on SCOS — delete them (grep `.reset(`, `.observe(`). See §5.

**Accumulators are NOT a blanket TODO.** A driver-side accumulator (count/sum/min/max/avg/distinct-set/sketch, incremented in `forEach`/`map`/a UDF) is a *reduction* → rewrite it as `df.agg(...)` / `df.groupBy(...).agg(...)` per §6.10–6.16. Only the four §7 uses are true hard gaps (three ⚠perm; `writeStream().foreachBatch` is ⚠ns with a workaround). Java uses `jsc.sc().longAccumulator()`/`jsc.sc().doubleAccumulator()`/`jsc.sc().collectionAccumulator()` or `spark.sparkContext().longAccumulator()` etc. — any such `JavaSparkContext`/`sparkContext()` hop is blocked under Connect and surfaces as `SPRKCNTSCL1500` (§1) — delete the hop and express the intent with a DataFrame aggregate.

**128 MB collection ceiling (⚠perm).** A single ARRAY/OBJECT/VARIANT value is capped at ~128 MB uncompressed, so `collect_list`/`collect_set`/map aggregates / a reassembled wide vector build one such value **per group** and a pathological single group **raises**. Reduce inside the aggregate (`functions.sum(...)`, `functions.count(...)`, `functions.approx_count_distinct(...)`) or use `posexplode → groupBy(idx).sum` for vectors — never `collect_list` a wide vector or materialize one huge group (§3).

**UDAF path (§6.17).** A Spark UDAF (`UserDefinedAggregateFunction`, or an `Aggregator` submitted via `functions.udaf(...)`) has no supported SCOS execution path: (1) reduce to a built-in `groupBy().agg` (most UDAFs do), else (2) a native Snowflake Java UDAF (registered once in the account catalog, called via `SnowflakeSession` pass-through), else (3) keep genuinely non-SQL logic on Spark. **Never** rely on `spark.udf().registerJavaUDAF` — it does **not** raise; the aggregate flag is silently dropped and the class registers as a **scalar** UDF, giving wrong results with no error (`// SCOS: [SPRKCNTSCL1000]`).

### Rule 3: Unsupported Formats
ORC/Avro → Parquet:
```java
// SCOS: [SPRKCNTSCL1000] ORC format replaced with Parquet
df.write().mode(SaveMode.Overwrite).parquet(path);
```

### Rule 4: No-Op Operations
`.hint()`, `.repartition()`, `.coalesce()` are silently ignored. Leave as-is, **no comment**.

### Rule 5: No-Op Configs
Unsupported Spark configs are silently ignored. Leave as-is, **no comment**.

### Rule 6: Missing Fixes
If `fix` is null, use `root_cause` for a workaround. If unsure: `// SCOS: TODO`.

### Rule 7: File Reads
- Snowflake stage `@STAGE/...` — no comment needed
- Cloud storage `s3://`, `gs://`, `abfs://` — add performance tip
```java
// SCOS: Performance tip - Consider uploading to a Snowflake stage
Dataset<Row> df = spark.read().option("header","true").csv("s3://bucket/path");
```

### Rule 8: Snowflake Connector Pushdown
If code uses `.format("snowflake")`, recommend `SnowflakeSession.sql()`. Keep original + add comment.

### Rule 9: Wildcard/Glob File Reads
Not supported. Replace with explicit file lists or `// SCOS: TODO - [SPRKCNTSCL1000]`.

### Rule 10: UDF Serialization
Java UDFs referencing custom classes may fail. **Read** `../../references/java/udf-dependencies.md`.
- Development: `REPLClassDirMonitor` for compiled class files
- Production: `spark.addArtifact()` for JAR uploads
- Staged: `snowpark.connect.udf.java.imports`

### Rule 11: checkpoint() Not Supported
Replace `.checkpoint()` with `.cache()`:
```java
// SCOS: [SPRKCNTSCL1000] checkpoint() not supported — replaced with cache()
df.cache();
```
> Usually already done by Phase 0.5c ScosCheckpointToCache rule.

### Rule 12: Java API Session init
SparkSession/JavaSparkContext creation → SnowparkConnectSession:
```java
// SCOS: [SPRKCNTSCL3500] Converted to Snowpark Connect session
import com.snowflake.snowpark_connect.client.SnowparkConnectSession;
SnowparkConnectSession spark = SnowparkConnectSession.builder()
    .appName("MyApp")
    .getOrCreate();
```
> Usually already done by Phase 0.5c ScosSparkSessionBuilderRewrite rule.

### Rule 16: Hadoop / HDFS APIs
`org.apache.hadoop.*` not available. Remove and replace:
```java
// SCOS: [SPRKCNTSCL1000] HDFS write replaced with Snowflake table
df.write().mode(SaveMode.Overwrite).saveAsTable("db.table");
```

### Rule 17: Hive Integration
Remove `enableHiveSupport()`, `HiveContext`, HWC:
```java
// Replace: hive.sql(query)
spark.sql(query);
```

### Rule 20: Cross-File Consistency (MANDATORY)
When modifying a method signature, grep the entire codebase for callers and update them.
```bash
grep -rn "MyClass\|myMethod" <MIGRATED>/ --include="*.java"
```
**Failure to do this is the #1 cause of compilation errors.**

### Rule 21: Import Emission (MANDATORY)
Only emit syntactically valid Java import lines. Never append text after the import path:
```java
// CORRECT:
// SCOS: [SPRKCNTSCL1000] Removed: import org.apache.hadoop.fs.FileSystem
import com.myproject.model.MyClass;

// INVALID (causes compilation error):
import com.myproject.model.MyClass — replaced with local class
```

### Rule 22: Syntax Artifact Cleanup (MANDATORY)
After all edits, scan for malformed lines:
```bash
grep -rn '^import .*[—–]' <MIGRATED>/ --include="*.java"
grep -rn '^—\|^[[:space:]]*—[[:space:]]*$' <MIGRATED>/ --include="*.java"
```

### Rule 24: Snowflake-SQL Pass-Through (USE DATABASE / SCHEMA / ROLE / WAREHOUSE)
```java
// SCOS: [SPRKCNTSCL3500] USE statements lifted to SnowflakeSession
import com.snowflake.snowpark_connect.client.SnowflakeSession;
SnowflakeSession sf = new SnowflakeSession(spark);
sf.useDatabase("mydb");
sf.useSchema("myschema");
sf.useRole("analyst");
sf.useWarehouse("compute_wh");
```

### Rule 25: No hardcoded sc://localhost:15002
Do NOT hardcode remote URLs. Use:
```java
SnowparkConnectSession spark = SnowparkConnectSession.builder().appName("App").getOrCreate();
```

---

### Rule 26: Column Names Round-Trip UPPERCASE — Exact-Case `df.columns()` Membership Breaks

After a DataFrame is written to and re-read through Snowflake (`saveAsTable` then `spark.table(...)`, or any Snowflake-backed source), its column identifiers come back **upper-cased** (Snowflake folds unquoted identifiers), whereas Spark Classic preserves the original (usually lowercase) case. `col("x")`, `.filter(col("x").equalTo(...))`, and `.select("x")` stay **case-insensitive** on SCOS and keep working — no rewrite needed. What breaks is code that inspects `df.columns()` / `df.schema().fieldNames()` and does an **exact-case** membership check:

```java
// BEFORE (silently false on SCOS — df.columns() contains "MY_COL", not "my_col"):
if (Arrays.asList(df.columns()).contains("my_col")) {
    df = df.withColumn("flag", functions.lit(1));
}
```

On SCOS `df.columns()` returns `{"MY_COL"}`, so `contains("my_col")` is `false` and a branch or column is silently dropped — a real value divergence, not cosmetic.

**Fix: lower-case both sides.**

```java
// AFTER:
// SCOS: Snowflake round-trip upper-cases column identifiers; compare case-insensitively.
boolean hasMyCol = Arrays.stream(df.columns())
    .anyMatch(c -> c.equalsIgnoreCase("my_col"));
if (hasMyCol) {
    df = df.withColumn("flag", functions.lit(1));
}
```

Only exact-case `df.columns()`/`df.schema().fieldNames()` string matching needs this rewrite. `col()`/`select()`/`filter()` are case-insensitive and do not need to change.

---

## Unsupported Dataset/DataFrame APIs (Java)

Same API surface as Scala — all unsupported SCOS APIs apply to `Dataset<Row>` in Java:

| API | EWI Code | Replacement |
|-----|----------|-------------|
| `df.checkpoint()` | SPRKCNTSCL1000 | `df.cache()` |
| `df.rdd()` | SPRKCNTSCL1500 | Rewrite to Dataset API |
| `df.toJavaRDD()` | SPRKCNTSCL1500 | Rewrite to Dataset API |
| `df.randomSplit(weights)` | SPRKCNTSCL1000 | `df.sample(fraction, seed)` |
| `df.writeStream()` | SPRKCNTSCL2000 | `df.write().mode(...)` |
| `df.queryExecution()` | SPRKCNTSCL1000 | Remove — internal API |

---

## Behavioral Differences (Java)

Same engine-level differences as Scala. See `../../references/java/behavioral-differences.md`
for Java-syntax fix examples.
