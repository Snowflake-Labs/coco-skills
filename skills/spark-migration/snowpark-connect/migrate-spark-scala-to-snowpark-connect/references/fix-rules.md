# SCOS Fix Rules Reference — Scala

Rules for fixing SCOS compatibility issues found during analysis of Scala workloads. The fixer agent reads this document when applying fixes to migrated files.

**Related references:**
- `../../references/scala/rdd-conversion.md` — RDD-to-DataFrame conversion rules and examples (required for Rule 2)
- `../../references/scala/udf-dependencies.md` — UDF serialization fix approach for Scala (required for Rule 10)
- `../../references/scala/spark-config.md` — which `spark.conf.set` / `.config` keys SCOS honors vs silently ignores, default deviations, and SCOS-specific knobs (required for Rule 5)
- `../../references/scala/ewi-codes.md` — Official SMA EWI code scheme for Scala (required for `// SCOS:` comment tagging)
- `../../references/scala/troubleshooting.md` — runtime / SQL / connection error playbook (e.g. `RESOURCE_EXHAUSTED` → `ChannelBuilder.MAX_MESSAGE_LENGTH`, `safe_count`/`safe_checkpoint`, QUALIFY pass-through, `USE DATABASE` session context, connection env vars) — consult when a fix needs to resolve a specific runtime error
- `../../references/scala/glue-recipes.md` — AWS Glue → SCOS recipe catalog G1–G12 (`GlueContext`, `DynamicFrame`, `com.amazonaws.services.glue.transforms`, job bookmarks, connector writeback) (**required for Rule 37** — any file importing `com.amazonaws.services.glue`)
- `sql-fix-rules.md` — LLM fix rules for SQL incompatibilities inside `spark.sql("...")` strings and standalone `.sql` files (required when `analysis.json` contains `language:"sql"` rows)

---

## Pre-Fix: Read EWI Codes

Before applying fixes, read `../../references/scala/ewi-codes.md` to understand the official SMA EWI code scheme for Scala. When adding `// SCOS:` comments, include the relevant EWI code where possible. For example:
- `// SCOS: [SPRKCNTSCL1500] RDD operation converted to DataFrame`
- `// SCOS: TODO - [SPRKCNTSCL2500] ML element requires manual migration`

This tagging enables the report generator to map comments to official codes accurately.

---

## Phase 0.5 Recipe Coverage

Several rules below are fully or partially handled by deterministic **Scalafix**
AST rules that run in Phase 0.5 before the LLM analyzer ever sees the file. The
rules emit `// SCOS:`, `// SCOS-WARN:`, or `// SCOS-TODO:` comments naming
themselves; the fixer's job for those lines is to (a) NOT undo the recipe edit and
(b) optionally apply a deeper, context-aware fix on top. See
`../../references/scala/recipes.md` for the full rule catalogue.

| Rule(s) below | Scalafix rule id | Status |
|---|---|---|
| `.checkpoint()` / `.localCheckpoint()` | `ScosCheckpointToCache` | **rewrite — done** |
| Map column subscript (Rule 13) | `ScosMapSubscriptToElementAt` | **rewrite — done** |
| SparkContext properties (Rule 14, `sc.parallelize`/`sc.broadcast`) | `ScosSparkContextPropertyFallbackAnnotate` | annotate — fixer applies the RDD-bucket fix on top |
| `unionByName(..., allowMissingColumns=true)` | `ScosUnionByNameAllowMissingAnnotate` | annotate — fixer should pre-align schemas |
| Wildcard/glob reads (Rule 9) | `ScosWildcardReadAnnotate` | annotate — fixer may rewrite to enumerated paths |
| External-cloud reads (`s3://`, `gs://`, `abfss://`, ...) | `ScosExternalCloudReadAnnotate` | annotate — fixer may rewrite to `@stage/...` |
| Self-join `df.join(df, ...)` (no alias) | `ScosSelfJoinUnaliasedAnnotate` | annotate — fixer should add `.alias()` |
| Driver materialization in loops (`collect`/`toLocalIterator`/`collectAsList` inside a loop) | `ScosDriverHotPathAnnotate` | annotate — fixer should lift out of loop |
| Temp-view multi-use cache (Rule 20) | `ScosTempViewMultiUseCache` | annotate — fixer should insert `.cache()` |
| `UserDefinedTableFunction` / `GenericUDTF` UDTF (Rule 18) | `ScosUdtfCompatibilityModeAnnotate` | annotate — fixer should enable compatibility mode |
| No-op cluster/runtime configs (Rule 5) — `spark.executor.*`, `spark.driver.*`, YARN/K8s keys, ... | `ScosSparkConfigNoopAnnotate` | annotate — flags `// SCOS: TODO`; fixer leaves the line (safe to delete) |
| `import spark.sqlContext.implicits._` (Rule 14a) | `ScosSqlContextImplicitsRewrite` | **rewrite — done** |
| `df.write.format("delta")...save(path)` (Rule 7b) | `ScosDeltaWriteToParquet` | **rewrite — done** |
| `DeltaTable.forPath/forName` | `ScosDeltaTableAnnotate` | annotate — fixer should rewrite to `spark.read.table()` |
| `.repartition(N)` / `.coalesce(N)` no-ops (Rule 4) | `ScosPartitionNoopStrip` | rewrite (no-op strip) |
| Snowflake connector I/O (Rule 8) | `ScosSnowflakeConnectorIO` | **rewrite — done** (literal cases); fixer handles the TODO-flagged remainder |
| `dbutils.widgets.*` | `ScosDbUtilsWidgetsToProperty` | **rewrite — done** |
| `dbutils.secrets.get/getBytes` | `ScosDbUtilsSecretsGetStub` | **rewrite — done** (stub + TODO) |
| `display(df)` / `df.display()` (Databricks) | `ScosDisplayToShow` / `ScosDisplayMethodToShow` | **rewrite — done** |
| `import org.apache.spark.rdd._` (Rule 2) | `ScosRddImportAnnotate` | annotate — fixer applies the RDD-bucket fix on top |
| Exclusive RDD methods (Rule 2) | `ScosRddExclusiveMethodAnnotate` | annotate — fixer applies the RDD-bucket fix on top |
| `df.rdd.persist/cache()` (Rule 2 Bucket B) | `ScosRddPersistToCache` | **rewrite — done** |
| `sc.range(N)` | `ScosScRangeToSparkRange` | **rewrite — done** |
| `sc.textFile("path")` | `ScosScTextfileToReadText` | **rewrite — done** |
| `sc.wholeTextFiles("path")` | `ScosScWholeTextFilesAnnotate` | annotate — no direct DataFrame equivalent |
| `SparkContext.getOrCreate()` bootstrap | `ScosSparkContextGetOrCreateRewrite` | **rewrite — done** |
| `sc.stop()` / `sc.close()` / `sc.setLogLevel()` | `ScosSparkContextNoopCommentOut` | rewrite (comment-out) |
| `df.unpersist(blocking = true)` | `ScosUnpersistDropBlockingArg` | **rewrite — done** |
| `approxCountDistinct(col, rsd)` | `ScosApproxCountDistinctDropRsd` | **rewrite — done** |
| `sc.hadoopConfiguration().set("fs.s3*",...)` | `ScosHadoopConfCredentialAnnotate` | annotate — fixer should rewrite to a storage integration/stage |
| JDBC / Iceberg / table I/O detect | `ScosSparkIoDetectAnnotate` | annotate — fixer should rewrite JDBC to a Snowflake source |
| **AWS Glue (Rule 37)** — see `../../references/scala/glue-recipes.md` | *(no Scalafix Glue recipes)* | all Glue patterns handled directly by the fixer |

When the fixer encounters a line already marked by one of these rules, it MUST
consult `migration_state.json:recipe_edits` to confirm the recipe-managed state
(see "Branch on `kind` FIRST" below) and proceed with the workflow above.

---

## Per-Issue Processing

For EACH issue in `analysis.json`, perform the following:

0. **Branch on `kind` FIRST** (recipe-aware shortcut). Each issue carries a `kind`
   field set by `analyze_scala.py` (when invoked with `--recipe-edits`). Use it to
   route the issue before applying any of the per-rule logic below:

   | `kind` | What it means | Required fixer action |
   |---|---|---|
   | `recipe_validated` | A `Scos*Rewrite` Scalafix rule already fixed this site bytewise in Phase 0.5. `final_risk` is forced to 0.0 and `recipe_id` names the rule. | **Skip** — emit no edit. Verify the inline `// SCOS:` comment naming `recipe_id` is still present in the source; if missing, re-add `// SCOS: validated by <recipe_id>` (recipe audit trail). Move to the next issue. |
   | `recipe_incomplete` | A `Scos*Annotate` rule flagged this site but could not auto-rewrite. `recipe_id` names the rule; `suggested_fixer_action` MAY contain a concrete LLM-proposed rewrite. | **Prefer `suggested_fixer_action` over `fix`** if it is non-null and concrete code (not prose). Apply it verbatim, then append `// SCOS: fixed by fixer on top of <recipe_id>` (do NOT remove the rule's original `// SCOS-WARN:` / `// SCOS-TODO:` comment). If `suggested_fixer_action` is null/empty, fall through to the normal rules below using `fix` and `root_cause`. |
   | `recipe_adjacent` | No rule fired here, but the analyzer thinks the pattern matches a Scalafix rule (`suggested_recipe_id`). | Apply normal rules below using `fix` / `root_cause`. ADDITIONALLY append `// SCOS: recipe-coverage gap - pattern matches <suggested_recipe_id>` so we can mine these for future Phase 0.5 rule additions. |
   | `standard` (default) | No recipe relationship. Covers both analyzer-emitted decidable triggers (`source="trigger_decidable"`) and Phase 1.1 adjudicator-confirmed rows (`adjudicated=true`). | Apply normal rules below. |
   | `needs_adjudication` | The analyzer deferred this block (non-decidable and/or recipe-touched) rather than calling `CORTEX.COMPLETE`. Normally resolved by the Phase 1.1 adjudicator before the fixer runs — confirmed rows arrive as `standard`, dismissed rows as `resolution="safe"`. | **Fallback only.** If still unresolved (`adjudicated` false), do the adjudicator's job inline: read `code` + `deferred_candidates[]` in context; if not a real issue, set `resolution: "safe"` + `resolution_reason` and move on; if real, apply normal rules below using `root_cause` (there is no analyzer `fix` to lean on). |

   For backward compatibility: if `kind` is missing from the issue object (older
   `analysis.json` files predating recipe-awareness, or when the analyzer was run
   without `--recipe-edits`), treat the issue as `kind="standard"`.

1. **Locate the issue**: Find the code at `file` and `lines` in the copied directory.
2. **Assess the risk**: Check the `final_risk` value.
3. **Apply the appropriate action** based on the rules below.
4. **Document the action**: Add a `// SCOS:` comment **or** record a `resolution` verdict on the issue in `analysis.json` (see "Recording resolutions in `analysis.json`") — **except** for no-op operations and configs (Rules 4 and 5), which need neither.

---

## Rules for Fixing based on Risk Score

1. **Must Fix (`final_risk` >= 0.7)**: Apply a fix or rewrite. If impossible, add `// SCOS: TODO - <explanation>`. If you review it and it genuinely needs no action, record `resolution: "safe"` **with** a concrete `resolution_reason` in `analysis.json` (see below).
2. **Should Fix (0.3 <= `final_risk` < 0.7)**: Apply fix if suggested, else `// SCOS: TODO`. If genuinely safe, record `resolution: "safe"` in `analysis.json`.
3. **Fix if possible (`final_risk` < 0.3)**: Fix if possible, else record `resolution: "safe"` in `analysis.json` (no inline comment needed) or leave a brief `// SCOS: <explanation>`.

---

## Recording resolutions in `analysis.json`

After you process an issue, write your verdict back onto that issue object in
`analysis.json` by adding two fields. This is the structured, machine-readable
record the gates (`verify_phase.py --phase 2`) and the validation skill rely on —
it is the alternative to leaving a `// SCOS: ...reviewed, safe` comment in the
source for every finding. This mirrors the PySpark path 1:1.

| Field | Values | Meaning |
|---|---|---|
| `resolution` | `"fixed"` | You applied a fix or rewrite (also leave the inline `// SCOS:` comment). |
| | `"todo"` | Needs manual follow-up (also leave the inline `// SCOS: TODO` comment). |
| | `"safe"` | Reviewed; no action needed. **No inline comment.** Requires `resolution_reason`. |
| | `"perf"` | Performance tip only (also leave the inline `// SCOS: Performance tip` comment). |
| `resolution_reason` | free text | Why. **Mandatory for `"safe"`**; recommended for the rest. |

Example — an `.isEmpty` finding the analyzer flags as a possible `DataFrame.isEmpty`,
but the receiver is a Scala collection, so it is actually fine:

```json
{
  "file": "src/main/scala/com/flashfood/petl/util/Image.scala",
  "lines": "40-40",
  "final_risk": 0.8,
  "resolution": "safe",
  "resolution_reason": "receiver is a scala.collection Map (bound via `val m: Map[..]`), not a Spark DataFrame; Scala collection .isEmpty is fully supported"
}
```

Rules for `resolution`:

- **`"safe"` requires a concrete, code-grounded `resolution_reason`.** The Phase-2
  gate emits a `safe_without_reason` failure for any high-risk
  (`final_risk` >= 0.7) issue marked `safe` with an empty reason, which
  re-triggers the fixer. Do not use `"safe"` as a shortcut to silence a finding
  you have not actually reasoned through.
- **Never upgrade an "unverified" verdict into a confident `"safe"`.** If
  `analysis.json` says to *verify* something and you have no grounding to confirm
  it, keep it as `// SCOS: TODO - verify ...` with `resolution: "todo"`. Do **not**
  assert "supported / safe" based on a method name alone.
- A recorded `resolution` (`fixed`/`safe`/`todo`/`perf`) satisfies the high-risk
  coverage gate **without** an inline marker within ±3 lines of the issue, so a
  legitimately-safe finding no longer forces a noisy comment or a spurious fixer
  re-dispatch.

---

## General Rules

### Rule 1: Use the Tool's Fix
If the issue provides a `fix` value, use it.

### Rule 2: Handle RDDs
RDD usage (`category: "RDD"`, `final_risk` near 1.0) splits into three buckets — **the analyzer issue carries `"unsupported": true|false` to tell you which.** **Read** `../../references/scala/rdd-conversion.md` for the full rules and verified examples.

- **Unsupported (`"unsupported": true`)** — `.rdd` with a closure or partition-level op, `mapPartitions`/`foreachPartition`, `SparkContext` file/accumulator APIs: no DataFrame equivalent. Do **NOT** rewrite or invent a workaround. Preserve the original line and prepend a `// SCOS:` marker (keep the literal `manual refactor` phrase so the Phase 2b gate quarantines the file):
  ```scala
  // SCOS: [SPRKCNTSCL1500] RDD API '.rdd.getNumPartitions' is not supported in Snowpark Connect; manual refactor required.
  println(df.rdd.getNumPartitions)
  ```
  (The Phase 2b gate quarantines these and reports them as manual items, not failures.)
- **Drop-the-hop (`"unsupported": false`, issue `fix` mentions "drop the .rdd accessor")** — the `.rdd` accessor leads to a method that exists directly on DataFrame. Drop the `.rdd` hop and call the same method on the DataFrame:
  - `df.rdd.count()` → `df.count()`; `df.rdd.isEmpty()` → `df.isEmpty()`; `df.rdd.collect()` → `df.collect()`; `df.rdd.first()` → `df.first()`; `df.rdd.take(n)` → `df.take(n)`; `df.rdd.toLocalIterator()` → `df.toLocalIterator()`
  - `df.rdd.cache()` / `persist()` → `df.cache()`; `df.rdd.unpersist()` → `df.unpersist()`
  - `df1.rdd.union(df2.rdd)` → `df1.union(df2)`; `df.rdd.distinct()` → `df.distinct()`; `df1.rdd.intersection(df2.rdd)` → `df1.intersect(df2)`; `df1.rdd.subtract(df2.rdd)` → `df1.exceptAll(df2)`
  - `df.rdd.sample(wr,f)` → `df.sample(wr,f)`; `df.rdd.repartition(n)` → `df.repartition(n)`;  `df.rdd.coalesce(n)` → `df.coalesce(n)`
- **Convertible (`"unsupported": false`, other patterns)** — rewrite to the DataFrame API using the recipe in the issue's `"fix"` field (the analyzer now emits a specific recipe per pattern). Key conversions:
  - `sc.parallelize(Seq[tuple])` → `spark.createDataFrame(seq).toDF(names…)` (**never** `Tuple1.apply` on tuples).
  - `sc.parallelize(Seq[primitive])` → `spark.createDataFrame(seq.map(Tuple1.apply)).toDF("value")`.
  - `createDataFrame(sc.parallelize(Seq[Row]), schema)` / `emptyRDD[Row]` → `spark.createDataFrame(seq.asJava, schema)` (+ `import scala.collection.JavaConverters._`). **Never** nest `createDataFrame`.
  - `reduceByKey(_ + _)` / `groupByKey` / `countByKey` → `groupBy(key).agg(...)`.
  - `sortByKey()` → `df.orderBy(col("key"))`; `sortByKey(ascending=false)` → `df.orderBy(col("key").desc)`.
  - `sampleByKey(wr, fractions)` → `df.sampleBy("key", fractions, seed)`.
  - `mapValues(f)` → `df.withColumn("value", <col-expr from f>)` (translate the closure to a column expression).
  - `flatMapValues(f)` → `df.withColumn("value", <col-expr>).select(explode(col("value")))`.
  - `rdd1.join(rdd2)` → `df1.join(df2, Seq("key"))`; `leftOuterJoin` → `"left"`; `rightOuterJoin` → `"right"`; `fullOuterJoin` → `"outer"`; `cartesian` → `df1.crossJoin(df2)`; `subtractByKey` → `"left_anti"`.
  - `keys()` → `df.select(col("key"))`; `values()` → `df.select(col("value"))`.
  - `takeOrdered(n)` → `df.orderBy(col.asc).limit(n).collect()`; `top(n)` → `df.orderBy(col.desc).limit(n).collect()`.
  - `zipWithIndex()` → `row_number().over(Window.orderBy(<col>)) - 1`; `zipWithUniqueId()` → `monotonically_increasing_id()`.
  - `countByValue()` → `df.groupBy(df.columns.map(col): _*).count().collect()`.
  - `saveAsTextFile(path)` → `df.write.mode("overwrite").text(path)`.
  - `randomSplit(weights)` — **`df.randomSplit()` is itself unsupported in SCOS**; use `df.sample(fraction, seed)` with complementary fractions instead.
  - `sc.broadcast(v)` scalar → use `v` directly; `sc.broadcast(df)` join hint → `df.hint("broadcast")`.

**Route on the reference's verdict tag.** `references/scala/rdd-conversion.md` (§6–§10) tags every recipe with a **verdict** — route on it:
- **[Native] / [Workaround]** → **apply** the rewrite (no TODO). Tag `// SCOS: [SPRKCNTSCL1500]` for an RDD / `SparkContext` / accumulator element, or `// SCOS: [SPRKCNTSCL1000]` for a generic unsupported element (no-op config, custom UDAF, `observe`).
- **[Silent-diff]** → apply the rewrite **and** add a `// SCOS:` guard/note for the drift (e.g. `count(lit(1))` vs `count(col)`, non-identity `fold`/`foldByKey` seed applied once, `round(product(...)).cast("long")` for exact integer products, empty→NULL vs raise, `collect_set`/`collect_list` drop NULLs & lose order, `repartition(n, col)` is a no-op hint). Full list in §4.
- **[Partial]** → apply the closest form **and** `// SCOS: TODO` for the aspect SCOS cannot reproduce (`mapPartitionsWithIndex` split index, `partitionBy`/`repartitionAndSortWithinPartitions` key co-location/ordering, real `StorageLevel` via `getStorageLevel`, external `saveAsObjectFile`/`saveAsSequenceFile` reads, `toDebugString` lineage).
- **[Hard gap]** (⚠perm/⚠ns) → `// SCOS: TODO` naming the op + the Snowflake-native alternative. **Three** accumulator hard gaps are **permanent** (⚠perm): `foreachPartition` sinks (§7.1), cache-hit counting (§7.2), and mid-job `acc.value` progress polling (§7.3). `writeStream.foreachBatch` cross-batch state is **not currently supported** (⚠ns) — [Partial], with a manual per-batch-loop workaround (§7.4), not a permanent no-equivalent.
- **Delete, don't migrate**: `acc.reset()` across stages, merge/collision counters kept only for the Spark UI, `df.observe(...)` metrics (no-op on SCOS — `// SCOS: [SPRKCNTSCL1000]`), and named accumulators for the Spark UI have no meaning on SCOS — delete them (grep `.reset(`, `.observe(`). See §5.

**Accumulators are NOT a blanket TODO.** A driver-side accumulator (count/sum/min/max/avg/distinct-set/sketch, incremented in `foreach`/`map`/a UDF) is a *reduction* → rewrite it as `df.agg(...)` / `df.groupBy(...).agg(...)` per §6.10–6.16. Only the four §7 uses are true hard gaps (three ⚠perm; `writeStream.foreachBatch` is ⚠ns with a workaround). There is **no** `sc.accumulator` / `AccumulatorParam` in Scala; any `spark.sparkContext` hop to reach `sc.longAccumulator`/`sc.collectionAccumulator`/`sc.parallelize`/`sc.broadcast` is blocked under Connect and surfaces as `SPRKCNTSCL1500` (§1) — delete the `SparkContext` hop and express the intent with the session or a DataFrame aggregate.

**128 MB collection ceiling (⚠perm).** A single ARRAY/OBJECT/VARIANT value is capped at ~128 MB uncompressed, so `collect_list`/`collect_set`/map aggregates / a reassembled wide vector build one such value **per group** and a pathological single group **raises**. Reduce inside the aggregate (`sum`, `count`, `approx_count_distinct`) or use `posexplode → groupBy(idx).sum` for vectors — never `collect_list` a wide vector or materialize one huge group (§3).

**UDAF path (§6.17).** A Spark UDAF (`UserDefinedAggregateFunction`, or `Aggregator` submitted via `functions.udaf(...)`) has no supported SCOS execution path: (1) reduce to a built-in `groupBy().agg` (most UDAFs do), else (2) a native Snowflake Java UDAF (registered once in the account catalog, called via `SnowflakeSession` pass-through), else (3) keep genuinely non-SQL logic on Spark. **Never** rely on `spark.udf().registerJavaUDAF` — it does **not** raise; the aggregate flag is silently dropped and the class registers as a **scalar** UDF, giving wrong results with no error (`// SCOS: [SPRKCNTSCL1000]`).

**CRITICAL:** never fabricate an RDD shim to force compilation — no `.rdd` re-introduction, no nested `createDataFrame`, no `Tuple1` wrapping of tuples. A correct EWI is better than type-incorrect or semantically-wrong code.

### Rule 3: Unsupported Formats
Change file formats if required (ORC/Avro → Parquet). Add a downstream impact warning:
```scala
// SCOS: [SPRKCNTSCL1000] ORC format replaced with Parquet — ORC not supported in SCOS
// SCOS: TODO - Verify downstream consumers can accept Parquet instead of ORC
df.write.mode("overwrite").parquet(path)
```

### Rule 4: No-Op Operations
`hint()`, `repartition()`, `coalesce()` are silently ignored in SCOS. Leave as-is, **no comment**.

### Rule 5: No-Op Configs
Unsupported Spark configs (`spark.sql.shuffle.partitions`, `spark.executor.memory`, etc.) are silently ignored. Leave as-is, **no comment**. The deterministic `ScosSparkConfigNoopAnnotate` rule flags cluster/runtime families in Phase 0.5.

> **Reference:** `../../references/scala/spark-config.md` classifies every `spark.conf.set` / `.config` key into PRESERVE (honored — never drop, e.g. `spark.sql.session.timeZone`, `spark.sql.ansi.enabled`), NO-OP (silently ignored — safe to remove), and SCOS-specific (consider adding for parity). Consult it before touching a config line — dropping a PRESERVE key like `spark.sql.session.timeZone` silently shifts every timestamp in the workload.

### Rule 6: Missing Fixes
If `fix` is null, use `root_cause` for a workaround. If unsure: `// SCOS: TODO - <explanation>`.

### Rule 7: File Reads
Check the path in `.read.csv`, `.read.json`, `.read.parquet`, `.load`:
- **Snowflake stage** (`@STAGE_NAME/...`): No comment needed.
- **Cloud storage** (`s3://`, `gs://`, `abfs://`): Add performance tip recommending stage upload.
- **Local/variable paths**: Add performance tip.

```scala
// SCOS: Performance tip - Consider uploading to a Snowflake stage
val df = spark.read.option("header", "true").csv("s3://bucket/path/file.csv")
```

### Rule 7b: Delta Format Reads/Writes (Must Fix — `final_risk >= 0.9`)

`.format("delta")` is **not supported** in Snowpark Connect. Every Delta read and write must be
rewritten to the Snowflake-native equivalent. This is a **must-fix** — do not leave a TODO.

**Delta reads → `spark.read.table()`:**

```scala
// BEFORE:
val legs = spark.read.format("delta").load(stage + "fare_legs/")

// AFTER:
// SCOS: [SPRKCNTSCL1000] Delta format replaced with Snowflake table read — Delta not supported in SCOS
val legs = spark.read.table("fare_legs")
```

Table name inference: use the last meaningful path segment (strip trailing `/`, date partitions, `_delta_log`). If ambiguous, emit `// SCOS: TODO - [SPRKCNTSCL1000] confirm table name`.

**Delta writes → `saveAsTable()`:**

```scala
// BEFORE:
df.write.format("delta").mode("overwrite").save(stage + "settled_legs/")

// AFTER:
// SCOS: [SPRKCNTSCL1000] Delta write replaced with saveAsTable — Delta not supported in SCOS
df.write.mode("overwrite").saveAsTable("settled_legs")
```

**Path-based Parquet writes → `saveAsTable()` (also applies to `.write.save(path)`):**

Snowpark Connect cannot write Parquet to a local or cloud path. Rewrite all path-based writes:

```scala
// BEFORE:
df.write.mode("overwrite").parquet(outputPath)
df.write.save(outputPath)

// AFTER:
// SCOS: [SPRKCNTSCL1000] Path-based Parquet write replaced with saveAsTable — .write.parquet(path) not supported in SCOS
df.write.mode("overwrite").saveAsTable("output_table_name")
```

> **Note:** `sys.env.getOrElse(...)` paths feeding Delta/Parquet reads are also handled
> deterministically in Phase 3 (`update_imports_scala.py`). If you see
> `System.getProperty(...)` wrapping a Delta path after Phase 3, the Delta→table rewrite
> is still required here.

### Rule 8: Snowflake Connector I/O → SnowflakeSession / saveAsTable

**Phase 0.5 deterministic rule `ScosSnowflakeConnectorIO` handles the common literal-option cases automatically.** The LLM fixer is responsible for the remaining cases flagged with `SCOS: TODO`.

The Spark Snowflake connector (`.format("snowflake")` / `.format("net.snowflake.spark.snowflake")`) is unnecessary under SCOS — the workload already runs inside Snowflake. Replace with:

- **Reads** → `new SnowflakeSession(spark).sql(query)`. Never use bare `spark.sql(...)` for Snowflake-specific SQL: `spark.sql` is parsed as Spark SQL and breaks on Snowflake-specific syntax. `SnowflakeSession.sql()` wraps the statement with the `PRIVATE-SNOWFLAKE-SQL` pass-through marker.
- **Writes** → `df.write[.mode(m)].saveAsTable(tableName)`.

```scala
// BEFORE (Snowflake connector read):
val df = spark.read
  .format("snowflake")
  .option("query", "SELECT * FROM DB.SC.T WHERE id > 0")
  .load()

// AFTER:
// SCOS-RECIPE-INSERT-IMPORT: com.snowflake.snowpark_connect.client.SnowflakeSession
val df = new SnowflakeSession(spark).sql("SELECT * FROM DB.SC.T WHERE id > 0")

// BEFORE (Snowflake connector write):
df.write.format("snowflake").option("dbtable", "DB.SC.OUT").mode("overwrite").save()

// AFTER:
df.write.mode("overwrite").saveAsTable("DB.SC.OUT")
```

For session context (database/schema/role/warehouse) previously passed as `.option("sfDatabase", ...)`, use `SnowflakeSession` context methods — see Rule 24.

**Column-ambiguity (SCOS error 5004):** if connector I/O rewrites cause `AMBIGUOUS_REFERENCE` in Phase A/B validation, this is usually a **mock-schema problem** (a join seeds the column onto both legs), not a code defect. Fix in the data (schema repair via the data-synthesizer) before dispatching the migration-fixer. See the SCOS-runner agent for the full routing rule.

### Rule 9: Wildcard/Glob File Reads
Wildcard patterns (`*.json`, `*.csv`) in file reads are **not supported**. Replace with explicit file lists:
```scala
// BEFORE (fails in SCOS):
val df = spark.read.json("@MY_STAGE/*.json")

// AFTER:
val df = spark.read.json("@MY_STAGE/file1.json", "@MY_STAGE/file2.json")
```
If exact files unknown: `// SCOS: TODO - [SPRKCNTSCL1000] Wildcard glob not supported`.

### Rule 10: UDF Serialization (Scala)
UDFs referencing custom classes or non-serializable closures may fail. **Read**
`../../references/scala/udf-dependencies.md` for the full fix approach
(`addArtifact`, staged JARs, inline closures).

- **Option 1 (Dev)**: `REPLClassDirMonitor` for compiled class files
- **Option 2 (Prod)**: `spark.addArtifact(jarPath)` for JAR uploads
- **Option 3**: Staged JARs via `snowpark.connect.udf.java.imports`
- **Inline**: Keep simple UDF logic self-contained in anonymous functions with no
  enclosing-object references

### Rule 11: StructType in UDFs
In SCOS, `StructType` is converted to `Map` in UDFs instead of `Row`/`tuple`. Rewrite field access from numeric index (`e(0)`) to named access (`e("col1")`).

### Rule 12: checkpoint() Not Supported
Replace `checkpoint()` and `localCheckpoint()` with `cache()`:
```scala
// BEFORE:
df.checkpoint(false)

// AFTER:
// SCOS: [SPRKCNTSCL1000] checkpoint() not supported — replaced with cache()
df.cache()
```

> **Usually already done by Phase 0.5.** The deterministic pre-pass ships two
> context-aware checkpoint recipes, so the fixer normally only needs to handle
> what they miss:
> - `checkpoint_to_cache_rewrite` — default, non-iterative contexts → `cache()`
>   (this rule).
> - `dataframe_checkpoint_to_persist_rewrite` — checkpoints inside a `for`/`while`
>   loop → `persist(StorageLevel.MEMORY_AND_DISK)`, because `cache()`
>   (MEMORY_AND_DISK by default for DataFrames) can silently recompute on
>   executor eviction in iterative workloads. This intentionally diverges from
>   the single PySpark `dataframe_checkpoint_to_cache_rewrite` recipe.
>
> If you see `recipe_edits` entries for either recipe on a line, do **not**
> re-rewrite it — just verify the annotation is present.

### Rule 13: Scala Version Compatibility
If the workload uses Scala 2.13, add: `spark.conf.set("snowpark.connect.scala.version", "2.13")`. SCOS defaults to 2.12.

### Rule 14: Unsupported Save Modes
`Append` and `Ignore` save modes are not supported for CSV, JSON, Parquet, Text, XML. Replace with `Overwrite` or `ErrorIfExists`:
```scala
// SCOS: [SPRKCNTSCL1000] Append save mode not supported — replaced with overwrite
df.write.mode("overwrite").csv("@STAGE/output")
```

### Rule 15: Spark Catalyst / Internal APIs
Imports from `org.apache.spark.sql.catalyst.*` are not in the Spark Connect client JAR. Create local drop-in case classes:
```scala
// SCOS: [SPRKCNTSCL1000] Catalyst QualifiedTableName replaced with local case class
package com.myproject.model
case class QualifiedTableName(database: String, name: String) {
  override def toString: String = s"$database.$name"
}
```
**⚠️ CRITICAL**: Replace the import in ALL files that reference the type.

### Rule 16: Hadoop / HDFS APIs
`org.apache.hadoop.*` imports are not available. Remove and replace:

| HDFS Operation | SCOS Replacement |
|----------------|-----------------|
| `df.write.parquet(hdfsPath)` | `df.write.saveAsTable("db.table")` or `df.write.parquet("@stage/path")` |
| `spark.read.parquet(hdfsPath)` | `spark.read.table("db.table")` or `spark.read.parquet("@stage/path")` |
| `FileSystem.get(conf).exists(path)` | Remove — Snowflake manages table existence |
| `FileSystem.get(conf).delete(path)` | `spark.sql("DROP TABLE IF EXISTS db.table")` |

Remove `implicit hdfs: FileSystem` from method signatures. **Trace all callers** (Rule 20).

### Rule 16b: Data Lineage Libraries
Remove Spline (`za.co.absa.spline.*`), DataHub, OpenLineage agents. Remove `.enableLineageTracking()`. Snowflake provides native lineage.

### Rule 16c: Databricks-Specific Imports (`com.databricks.*`, `dbutils`) — MUST ANNOTATE

`com.databricks.*` imports and `dbutils` usage have no SCOS equivalent. **Do NOT silently drop them.** For every `import com.databricks.*` line that cannot be replaced, prepend an annotation comment:

```scala
// SCOS: [SPRKCNTSCL1100] Databricks-only import — no SCOS equivalent; remove or replace with Snowflake Session API
import com.databricks.dbutils_v1.{DBUtilsHolder, DBUtilsV1}
```

For `dbutils.*` call sites that survive (cannot be rewritten):

```scala
// SCOS: [SPRKCNTSCL1100] dbutils.fs / dbutils.widgets / dbutils.notebook have no SCOS equivalent — replace with Snowflake stage ops / session params / stored-proc calls
val path = dbutils.fs.ls("/mnt/data")
```

**Replacement guidance:**

| `dbutils` call | SCOS replacement |
|----------------|-----------------|
| `dbutils.fs.ls(path)` | Remove or use `@stage` path with `spark.read` |
| `dbutils.fs.rm(path)` | `spark.sql("REMOVE @stage/path")` |
| `dbutils.widgets.get("key")` | `sys.env.getOrElse("KEY", "default")` or session parameter |
| `dbutils.secrets.get(scope, key)` | Snowflake secret / external token |
| `dbutils.notebook.run(path, timeout)` | Stored procedure or task DAG |
| `dbutils.notebook.exit(value)` | `return` or exception |

If the entire `dbutils` block can be deleted (e.g. a mount operation that SCOS handles implicitly), delete it and note: `// SCOS: [SPRKCNTSCL1100] Removed Databricks mount — SCOS reads stages directly`.

**⚠️ MANDATORY scan after all edits:** Run:
```bash
# Runs in CoCo bash sandbox (Linux) - safe on any host OS
grep -rn "com\.databricks\|dbutils\." <MIGRATED>/ --include="*.scala" | grep -v "// SCOS\|// EWI"
```
Any match is an **unannotated survivor** — add the annotation before finishing.

### Rule 17: Hive Integration
Remove `enableHiveSupport()`, `HiveContext`, and HWC (`com.hortonworks.spark.sql.hive.*`).

**HWC API → SCOS mapping** (apply to ALL files including tests):

| HWC Call | SCOS Replacement |
|----------|-----------------|
| `hive.sql(query)` | `spark.sql(query)` |
| `hive.executeQuery(query)` | `spark.sql(query)` |
| `hive.table(name)` | `spark.read.table(name)` |
| `hive.session()` | `spark` |
| `hive.setDatabase(db)` | `spark.sql(s"USE $db")` |

**⚠️ CRITICAL**: After removing `implicit val hive: HiveWarehouseSession`, search ALL files for `hive.` references and replace with `spark.sql(...)`.

### Rule 18: Hive DDL Statements
Comment out `MSCK REPAIR TABLE`, `ALTER TABLE RECOVER PARTITIONS`, `CREATE EXTERNAL TABLE`:
```scala
// SCOS: TODO - [SPRKCNTSCL1000] MSCK REPAIR TABLE is Hive-specific.
// Snowflake manages partitions automatically.
// spark.sql("MSCK REPAIR TABLE schema.table")
```

### Rule 19: External Library Parameter Mismatch
After removing parameters (e.g., `hdfs: FileSystem`), check if external library calls still expect them. Add TODO if so.

### Rule 20: ⚠️ Cross-File Consistency (MANDATORY)
When you modify a method signature, remove a method/parameter/variable, or change a type:
1. Grep the **entire codebase** (including tests) for references
2. Update **every caller** to match the new signature
3. Update every subclass/implementation
4. Verify the call chain (callers of callers)
5. Check variable references (`hive.` → `spark.sql(...)`), implicit parameters, companion objects

```bash
# After removing hdfs parameter:
grep -rn "Job\.run" <MIGRATED>/ --include="*.scala"
# After removing HWC variable:
grep -rn "hive\." <MIGRATED>/ --include="*.scala"
# After replacing a Catalyst type:
grep -rn "QualifiedTableName" <MIGRATED>/ --include="*.scala"
# After changing session type:
grep -rn "SparkSession\|sqlContext" <MIGRATED>/ --include="*.scala"
# After removing HDFS FileSystem:
grep -rn "FileSystem\|hadoopConf\|hdfsPath" <MIGRATED>/ --include="*.scala"
```

**Failure to do this is the #1 cause of compilation errors.**

### Rule 21: ⚠️ Import Replacement Emission (MANDATORY)
Only emit syntactically valid Scala import lines. **NEVER** append text, em-dashes, or descriptions after the import path:

**Correct:**
```scala
// SCOS: [SPRKCNTSCL1000] Removed: import org.apache.hadoop.fs.FileSystem
import com.myproject.model.QualifiedTableName
```

**INVALID (causes compilation error):**
```scala
import com.myproject.model.QualifiedTableName — replaced with local model class
```

This applies to ALL import lines — imports must be syntactically pure. Put migration notes in `// SCOS:` comment lines above or below, never inline on the import statement itself.

### Rule 22: ⚠️ Syntax Artifact Cleanup (MANDATORY)
After ALL edits, scan for malformed lines:
```bash
grep -rn '^import .*[—–]' <MIGRATED>/ --include="*.scala"
grep -rn '^—\|^[[:space:]]*—[[:space:]]*$' <MIGRATED>/ --include="*.scala"
grep -rn '^import .* removed' <MIGRATED>/ --include="*.scala"
grep -rn '^import .* //.*→' <MIGRATED>/ --include="*.scala"
```
Fix: move trailing text to comment lines, delete bare em-dash lines. Every import line must compile as Scala.

---

### Rule 23: Map Column Subscript with Column Key
`mapCol(col("key"))` is not supported. Replace with `element_at()`:
```scala
// BEFORE:
val result = df.withColumn("val", categoryMap(col("category_code")))

// AFTER:
// SCOS: [SPRKCNTSCL1000] Map column subscript replaced with element_at()
import org.apache.spark.sql.functions.element_at
val result = df.withColumn("val", element_at(categoryMap, col("category_code")))
```
Literal keys (`mapCol("literal_string")`) still work.

---

### Rule 24: Snowflake-SQL Pass-Through (USE DATABASE / SCHEMA / ROLE / WAREHOUSE)

`spark.sql("USE DATABASE …")` statements do not reliably update the SCOS session context for subsequent DataFrame operations. Lift all USE statements to `SnowflakeSession` calls:

```scala
// BEFORE:
spark.sql("USE DATABASE mydb")
spark.sql("USE SCHEMA myschema")
spark.sql("USE ROLE analyst")
spark.sql("USE WAREHOUSE compute_wh")

// AFTER:
// SCOS: [SPRKCNTSCL3500] USE statements lifted to SnowflakeSession
import com.snowflake.snowpark_connect.client.SnowflakeSession
val sf = new SnowflakeSession(spark)
sf.useDatabase("mydb")
sf.useSchema("myschema")
sf.useRole("analyst")
sf.useWarehouse("compute_wh")
```

`SnowflakeSession.sql(...)` is also available for arbitrary Snowflake SQL that is not a USE statement. Create `sf` once per session — it is lightweight and shares the underlying `SparkSession`.

---

### Rule 25: Snowpark Connect Server URL Resolution

Do NOT hardcode `sc://localhost:15002` in migrated entry points. The server URL is resolved automatically in priority order:

1. `SPARK_REMOTE` environment variable — highest priority. Set to your Snowflake account endpoint.
2. `SNOWPARK_SUBMIT_JOB=true` — sidecar mode, automatically connects to `sc://localhost:15002`.
3. Auto Python venv launch (local dev) — uses `SNOWPARK_CONNECT_PYTHON_VENV`.

```scala
// WRONG — hardcodes a local URL that only works in sidecar mode:
val spark = SparkSession.builder().remote("sc://localhost:15002").getOrCreate()

// CORRECT — resolution is automatic:
import com.snowflake.snowpark_connect.client.SnowparkConnectSession
val spark = SnowparkConnectSession.builder().appName("MyApp").getOrCreate()
```

If you see `sys.env.getOrElse("SPARK_REMOTE", "sc://localhost:15002")` patterns, remove the whole block and replace with `SnowparkConnectSession.builder()`.

---

### Rule 26: Cross-Build-Tool Consistency — Scala Version Suffix

When `scalaVersion` is changed in the build file (e.g. from `2.11` to `2.12`), all cross-compiled artifact coordinates with hardcoded `_2.11` suffixes must be updated:

```scala
// WRONG — version suffix unchanged:
libraryDependencies += "com.example" % "my-lib_2.11" % "1.2.3"

// CORRECT — update to match new scalaVersion:
libraryDependencies += "com.example" % "my-lib_2.12" % "1.2.3"
// Or use %% to let sbt derive the suffix:
libraryDependencies += "com.example" %% "my-lib" % "1.2.3"
```

For Maven: change `_2.11` suffixes to `_${scala.short}` in all `<artifactId>` elements.
For Gradle: change hardcoded `_2.11` strings to `_${scalaShort}` (Groovy) or `_$scalaShort` (Kotlin DSL).

Also check transitive ecosystem libraries: Kafka connectors, Avro, Delta, JSON4S, Shapeless, etc. all publish per-Scala-version artifacts.

---

## Unsupported Dataset/DataFrame APIs

The following DataFrame/Dataset APIs are documented as unsupported in Snowpark Connect. Each must be flagged with a `// SCOS: TODO` or replaced per the guidance below.

| API | Category | EWI Code | Replacement / Guidance |
|-----|----------|----------|------------------------|
| `df.checkpoint()` | No-Op API | SPRKCNTSCL1000 | Replace with `df.cache()`. See Rule 12. |
| `df.localCheckpoint()` | No-Op API | SPRKCNTSCL1000 | Replace with `df.cache()`. See Rule 12. |
| `df.randomSplit(weights)` | No-Op API | SPRKCNTSCL1000 | Use `df.sample(withReplacement=false, fraction=w)` or filter on a random column expression. |
| `df.rdd` | RDD | SPRKCNTSCL1500 | Rewrite to DataFrame API. See `../../references/scala/rdd-conversion.md`. |
| `df.javaRDD` | RDD | SPRKCNTSCL1500 | Rewrite to DataFrame API. |
| `df.toJavaRDD` | RDD | SPRKCNTSCL1500 | Rewrite to DataFrame API. |
| `df.toJSON` | No-Op API | SPRKCNTSCL1000 | Use `df.select(to_json(struct(col("*"))))` and write to a JSON stage file. |
| `df.withWatermark(...)` | No-Op API | SPRKCNTSCL2000 | Streaming API — remove watermark; SCOS is batch only. |
| `df.writeStream` | No-Op API | SPRKCNTSCL2000 | Streaming API — replace with `df.write.mode(...).format(...)`. |
| `df.dropDuplicatesWithinWatermark(...)` | No-Op API | SPRKCNTSCL2000 | Streaming API — use `df.dropDuplicates(cols)` for batch dedup. |
| `df.reduce(func)` | No-Op API | SPRKCNTSCL1000 | Use `df.agg(...)` or `df.groupBy().agg(...)` aggregation. |
| `df.sortWithinPartitions(...)` | No-Op API | SPRKCNTSCL1000 | Use `df.orderBy(...)` at DataFrame level; partitioning managed by Snowflake. |
| `df.queryExecution` | No-Op API | SPRKCNTSCL1000 | Internal Catalyst API; not available via Spark Connect. Remove usage. |
| `df.sqlContext` | No-Op API | SPRKCNTSCL3500 | Deprecated alias for SparkSession; use `spark` directly. |
| `df.isEmpty` | No-Op API | SPRKCNTSCL1000 | Use `df.count() == 0` or `df.limit(1).collect().isEmpty`. |
| `df.toLocalIterator()` | No-Op API | SPRKCNTSCL1000 | Use `df.collect().iterator` for small datasets or process server-side. |

Apply `final_risk >= 0.7` to all entries in this table when found in production code. Add `// SCOS: [SPRKCNTSCL<code>] <api> not supported — <replacement>` on the line before usage.

---

## Behavioral Differences (BD) — Detection and Fix Reference

These are not compilation errors but silent data-correctness issues. Flag each with `// SCOS: TODO - BD-N` when detected.

| BD | EWI | API Pattern | Risk | Spark Behavior | Snowflake Behavior | Fix |
|----|-----|-------------|------|---------------|-------------------|-----|
| BD-1 | SPRKCNTSCL5000 | `a / b` literal `0` divisor | High | Returns NULL | Throws error | `when(col("b") =!= 0, col("a") / col("b")).otherwise(null)` |
| BD-3 | SPRKCNTSCL5002 | `datediff(` | High | `datediff(end, start)` | Requires part + reversed | `expr("DATEDIFF('day', start, end)")` |
| BD-4 | SPRKCNTSCL5003 | `.union(` | High | Position-based | Same — silent corruption risk | Replace with `.unionByName()` |
| BD-8 | SPRKCNTSCL5007 | `isnan(` | High | Returns true for NaN | NaN not supported; returns NULL | Replace `isnan(c)` with `c.isNull` |
| BD-9 | SPRKCNTSCL5008 | `regexp_replace(` | High | Java regex | POSIX regex | Convert `\d`→`[0-9]`, `\w`→`[a-zA-Z0-9_]`; remove lookaheads |
| BD-12 | SPRKCNTSCL5011 | `regexp_extract(` | High | Returns `""` on no-match | Returns NULL | Wrap with `coalesce(regexp_extract(...), lit(""))` |
| BD-13 | SPRKCNTSCL5012 | `first(` / `last(` | High | Order-dependent | Non-deterministic without ORDER BY | Use with explicit window ordering |
| BD-14 | SPRKCNTSCL5013 | `round(` | Medium | Half-up rounding | Banker's rounding | Use `when(x % 1 === 0.5, ceil(x)).otherwise(round(x))` for half-up |
| BD-20 | SPRKCNTSCL5019 | `split(` | Medium | Java regex delimiter | Literal string delimiter | Remove regex escaping: `"\\."` → `"."` |
| BD-27 | SPRKCNTSCL5026 | `date_format(` | Medium | Java tokens (yyyy, HH, mm) | **Same Java tokens** | **DO NOT translate.** `date_format()` uses Java `DateTimeFormatter` patterns in both Spark and Snowflake SCOS. Keep `yyyy`, `HH`, `mm`, `ss`, `SSS` as-is. Never convert to Oracle SQL tokens (`HH24`, `MI`, `SS`, `FF3`) — doing so causes `SparkDateTimeException: Unknown pattern letter: I` at runtime. |
| BD-28 | SPRKCNTSCL5027 | `collect_list(` / `collect_set(` | Medium | Preserves order, includes nulls | Non-deterministic, excludes nulls | Add explicit ordering; filter nulls before collecting |

For a complete list of behavioral differences see `../../references/scala/behavioral-differences.md`.

---

## UDF Dependency Strategies (Reference)

For UDFs that reference custom classes or third-party JARs, three strategies are available. See `../../references/scala/udf-dependencies.md` for full details:

| Strategy | When to Use | Key Method |
|----------|-------------|------------|
| `REPLClassDirMonitor` | Development — auto-monitors compiled classes dir | `spark.registerClassFinder(new REPLClassDirMonitor(path))` |
| `spark.addArtifact(jar)` | Production — upload packaged JAR before UDF calls | `spark.addArtifact("/path/to/app.jar")` |
| `snowpark.connect.udf.java.imports` | Staged JARs already in Snowflake stage | `spark.conf.set("snowpark.connect.udf.java.imports", "[@stage/dep.jar]")` |

**Rule 27:** After migrating a UDF, verify no `broadcast` variable usage remains (see BD-29); capture lookup data directly in the closure instead.

---

### Rule 28: Null `array`/`struct` Read as VARIANT Null — `isNotNull` Filter Leaks a Row

SCOS reads a parquet/source NULL `array<...>`/`struct<...>` value as a **VARIANT null** (JSON `null`), not a SQL `NULL`. So `col("X").isNotNull` returns **true** for a null array/struct on SCOS (it is `false` on Spark) — a row that Spark filters out leaks through on SCOS, producing an extra output row (a real, off-by-one value divergence vs the Phase A baseline, **not** cosmetic).

When a filter/dedup guards an `array<struct<...>>` (or `struct`) column with `isNotNull`, guard with **both** `isNotNull` AND `size(col("X")) > 0` — `size()` returns 0/negative for a VARIANT null and correctly excludes the row.

**BEFORE (extra row leaks on SCOS):**

```scala
df.filter(col("items").isNotNull)
```

**AFTER:**

```scala
// SCOS: null array reads as VARIANT null, so isNotNull is true for it;
// add size()>0 so empty/null arrays are excluded as they are on Spark.
df.filter(col("items").isNotNull && size(col("items")) > 0)
```

Applies to any `isNotNull`-only guard on an `array<...>` or `struct<...>` column.

---

### Rule 29: Column Names Round-Trip UPPERCASE — Exact-Case `df.columns` Membership Breaks

After a DataFrame is written to and re-read through Snowflake (`saveAsTable` then `spark.table`, or any Snowflake-backed source), its column identifiers come back **upper-cased** (Snowflake folds unquoted identifiers), whereas Spark Classic preserves the original (usually lowercase) case. `col("x")`, `filter($"x" === ...)`, and `select("x")` stay **case-insensitive** on SCOS and keep working — no rewrite needed. What breaks is code that inspects `df.columns` or `df.schema.names` and does an **exact-case** membership check:

```scala
// BEFORE (silently false on SCOS — df.columns contains "MY_COL", not "my_col"):
if (df.columns.contains("my_col")) {
  df = df.withColumn("flag", lit(1))
}
```

On SCOS `df.columns` returns `Array("MY_COL")`, so `contains("my_col")` is `false` and a branch or column is silently dropped — a real value divergence, not cosmetic.

**Fix: lower-case both sides.**

```scala
// AFTER:
// SCOS: Snowflake round-trip upper-cases column identifiers; compare case-insensitively.
if (df.columns.map(_.toLowerCase).contains("my_col".toLowerCase)) {
  df = df.withColumn("flag", lit(1))
}
```

Only exact-case `df.columns`/`df.schema.names` string matching needs this rewrite. `col()`/`select()`/`filter()`/`$"..."` are case-insensitive and do not need to change.

### Rule 30: Legacy `SQLContext` / `HiveContext` → `SparkSession`

Legacy `SQLContext(sc)` / `HiveContext(sc)` entry points were deprecated in Spark
2.0 and are **removed in Spark Connect / SCOS** — their methods now live on the
active `SparkSession` (`spark`). The Scalafix rule `ScosSqlContextImplicitsRewrite`
handles `import spark.sqlContext.implicits._` → `import spark.implicits._`
automatically; the LLM fixer handles the remaining call-site references. Rewrite
every reference; tag with `SPRKCNTSCL3500`.

> **CRITICAL — never emit `import implicits._`:** The replacement import is always
> `import spark.implicits._` (with the `spark.` qualifier). Emitting
> `import implicits._` (bare, unqualified) does not compile — Scala has no
> top-level `implicits` object. This applies whether the original import was
> `import sqlContext.implicits._`, `import spark.sqlContext.implicits._`, or any
> other `*.implicits._` form. Always emit `import spark.implicits._`.

```scala
// BEFORE (not available in SCOS):
// val sqlContext = new SQLContext(sc)
// val df = sqlContext.sql("SELECT * FROM t")
// val rows = sqlContext.read.parquet("@stage/data")

// AFTER — use the active `spark` session directly:
// SCOS: [SPRKCNTSCL3500] sqlContext/HiveContext removed in Spark Connect; use spark
val df = spark.sql("SELECT * FROM t")
val rows = spark.read.parquet("@stage/data")
```

Mapping: `sqlContext.sql` → `spark.sql`, `sqlContext.read` → `spark.read`,
`sqlContext.table` → `spark.table`, `sqlContext.createDataFrame` →
`spark.createDataFrame`. `HiveContext` Hive-catalog access maps to Snowflake's
native catalog (fully-qualified `db.schema.table`).

### Rule 31: `@udtf` / `UserDefinedTableFunction` — Natively Supported (enable compatibility mode)

A Scala UDTF extending `UserDefinedTableFunction` / `GenericUDTF` is **natively
supported** in SCOS when compatibility mode is enabled. No structural rewrite is
needed — the SCOS runtime auto-translates the Spark-style `eval()`/`process()`
method to Snowpark's UDTF handler contract. The Scalafix rule
`ScosUdtfCompatibilityModeAnnotate` annotates the class declaration; the LLM
fixer's job is to enable the config once per session.

**Fix**: enable compatibility mode once per session. Keep the class as written.

```scala
// Keep the UDTF class AS-IS. Only add the config once per session:
spark.conf.set("snowpark.connect.udtf.compatibility_mode", "true")

// The class and its process()/eval() method stay unchanged.
class DoubleUDTF extends UserDefinedTableFunction {
  override def outputSchema: StructType = StructType(Seq(
    StructField("id", IntegerType), StructField("doubled", IntegerType)))
  def process(id: Int, v: Int): Seq[Row] = Seq(Row(id, v * 2))
}
spark.udf.register("double_udtf", new DoubleUDTF)
```

**UDAF** (`UserDefinedAggregateFunction`) still requires structural conversion
(there is no server-side UDAF mapping at the time of writing). Convert those to a
Snowpark UDAF: handler class with `accumulate()` / `merge()` / `finish()` methods.

### Rule 32: `withColumnRenamed` to an already-existing column name

`df.withColumnRenamed("a", "b")` when `b` already exists raises
`[COLUMN_ALREADY_EXISTS] The column 'b' already exists` on SCOS. Open-source
Spark and Databricks instead keep both columns (producing two `b`s), so
Databricks-origin code that does this runs there but fails on SCOS. `.drop("b")`
the pre-existing column **before** the rename, or rename to a unique name.

```scala
// BEFORE (fails on SCOS — b already exists):
val df2 = df.withColumnRenamed("a", "b")

// AFTER:
// SCOS: [SPRKCNTSCL1000] withColumnRenamed to existing column — drop pre-existing first
val df2 = df.drop("b").withColumnRenamed("a", "b")
```

### Rule 33: `TIMESTAMP_LTZ`/`TIMESTAMP_TZ` cannot be unloaded to Parquet

Writing a DataFrame that contains a `TIMESTAMP_LTZ`/`TIMESTAMP_TZ` column (e.g.
from `current_timestamp()`) to **Parquet** fails with `100171 (22000): Error
encountered when unloading to PARQUET: TIMESTAMP_TZ and LTZ types are not
supported for unloading to Parquet`. Writing the same frame to a **table**
(`saveAsTable`/`insertInto`) succeeds — this is only a Parquet-*unload*
limitation, not a general write failure (verified on SCOS: `saveAsTable` of
`current_timestamp()` is OK). If a Parquet write is required, convert the column
to a non-LTZ type first: `F.date_format(ts, "yyyy-MM-dd HH:mm:ss.SSSSSS")` (string)
or `ts.cast("timestamp_ntz")`.

```scala
import org.apache.spark.sql.functions._

// BEFORE (fails on SCOS):
df.write.parquet(path)

// AFTER (if Parquet is required):
// SCOS: [SPRKCNTSCL1000] TIMESTAMP_LTZ cannot unload to Parquet — convert to string/NTZ
df.withColumn("ts", date_format(col("ts"), "yyyy-MM-dd HH:mm:ss.SSSSSS"))
  .write.parquet(path)
// or: col("ts").cast("timestamp_ntz")
```

### Rule 34: `abs`/numeric function applied to a `DATE` column

`abs(date_col)` fails with `001044 (42P13): SQL compilation error: Invalid
argument types for function 'ABS': (DATE)`. Direct date arithmetic is fine:
`datediff(a, b)` returns an **int**, `a - b` (date subtraction) returns an
**interval**, and `abs(...)` over either works on SCOS (verified). A **bare
`DATE` passed to `abs`** directly is an SMA mistranslation or latent bug — fix
the upstream expression so `abs` wraps a numeric magnitude (e.g.
`abs(datediff(a, b))`), not a date.

**There is also a codegen path that injects `ABS(DATE)` with no `abs` in the
source: a two-argument `TO_CHAR`/`TO_VARCHAR` applied to a `DATE`.**
`F.expr("TO_CHAR(d, 'YYYYMMDD')")`, `TO_VARCHAR(d, fmt)`, and
`TO_CHAR(TO_DATE(s,'yyyy-MM-dd'), 'YYYYMMDD')` all fail with the same `Invalid
argument types for function 'ABS': (DATE)` — for a date column, `TO_DATE(...)`,
or `current_date()`, with **any** format string. (`TO_CHAR` on a **number** is
fine; the DATE overload is the problem.) **Fix: format dates with Scala
`F.date_format(col, "<java pattern>")`** (`"yyyyMMdd"`, `"yyyy-MM-dd"`, ...) —
works in every case — or `CAST(col AS STRING)` when the default ISO output is
acceptable. Do **not** rewrite `F.date_format` into `TO_CHAR` to "reformat a
date"; that is backwards and triggers this failure. *(verified on SCOS 1.32.0.)*

---

### Rule 35: Implicit `.pivot` Output Columns Keep the Aggregation Alias as a Suffix

An **implicit** pivot — `df.groupBy(...).pivot(col).agg(...)` with **no explicit `values` list** — names its output columns `<PIVOT_VALUE>_<AGG_ALIAS>` on SCOS, whereas Spark Classic drops the alias and emits the bare pivot value. For `df.groupBy("id").pivot("region").agg(sum(col("amount")).alias("total"))`, the pivot value `US` yields column `US_TOTAL` on SCOS (pivot value + alias; columns are also upper-cased per Rule 29) but bare `US` on Spark. Code that inspects `df.columns` with an exact-case suffix check (e.g. `_.endsWith("_total")`) misses the `US_TOTAL` form — the rename never fires and a column is left unrenamed, a real divergence.

**Fix: match the rename/`endsWith` logic case-insensitively AND account for the alias suffix.**

**BEFORE (rename never fires on SCOS — column is `US_TOTAL`, not `US`):**

```scala
val pivoted = df.groupBy("id").pivot("region").agg(sum(col("amount")).alias("total"))
for (c <- pivoted.columns) {  // SCOS: Array("ID", "US_TOTAL", "EU_TOTAL", ...)
  if (c.endsWith("_total"))   // exact-case suffix misses "US_TOTAL"
    pivoted = pivoted.withColumnRenamed(c, c.dropRight("_total".length))
}
```

**AFTER:**

```scala
// SCOS: implicit pivot columns keep the agg alias as a suffix (US -> US_TOTAL; also
// upper-cased per Rule 29); match case-insensitively.
val pivoted = df.groupBy("id").pivot("region").agg(sum(col("amount")).alias("total"))
for (c <- pivoted.columns) {
  if (c.toLowerCase.endsWith("_total"))
    pivoted = pivoted.withColumnRenamed(c, c.dropRight("_total".length))
}
```

---

### Rule 36: SCOS Lacks Implicit `String`→`Timestamp` Coercion on a String Column

Open-source Spark implicitly coerces a `StringType` column to `TimestampType` when comparing it against a timestamp column; **SCOS does not**, so `timestamp_col <= string_col` fails on SCOS with a type-mismatch error that never surfaces on Spark. (Snowflake still coerces well-formed string *literals*, so only a string *column* operand is affected.) **Fix: cast the string column explicitly** — `col.cast(TimestampType)`.

**BEFORE — timestamp ≤ string column (fails on SCOS):**

```scala
df.filter(col("event_ts").leq(col("cutoff_str")))
```

**AFTER:**

```scala
// SCOS does not coerce a StringType column to TimestampType; cast it explicitly.
df.filter(col("event_ts").leq(col("cutoff_str").cast(TimestampType)))
```

**Distinct sub-point — `.cast(IntegerType)` on a `TimestampType` is a true engine difference, not coercion**: Spark returns epoch seconds for `timestamp_col.cast(IntegerType)`; Snowflake rejects `CAST(TIMESTAMP AS INT)` outright. A generic numeric sweep that applies `.cast(IntegerType)` across all columns therefore fails when a column is a timestamp. **Fix: restrict the sweep to string columns** so it never hits a timestamp.

**BEFORE — numeric sweep casts every column (fails on the `Timestamp` column):**

```scala
for (c <- df.columns) {
  if (df.filter(col(c).cast(IntegerType) === 0).count() == df.count())
    df = df.drop(c)
}
```

**AFTER:**

```scala
// Spark returns epoch seconds for cast(TIMESTAMP AS INT); Snowflake rejects it.
// Only run the numeric check on string columns.
val strCols = df.schema.fields.collect { case f if f.dataType == StringType => f.name }
for (c <- strCols) {
  if (df.filter(col(c).cast(IntegerType) === 0).count() == df.count())
    df = df.drop(c)
}
```

### Rule 37: AWS Glue (`com.amazonaws.services.glue.*`) workloads

A Glue Scala ETL job is a Spark Scala job wrapped in the AWS Glue SDK
(`GlueContext`, `DynamicFrame`, `com.amazonaws.services.glue.transforms`,
job bookmarks). None of that surface exists in SCOS, but the PySpark /
Spark DataFrame API underneath it does — so the migration is mostly
**unwrapping** the Glue SDK back to plain DataFrame code, then repointing
I/O at Snowflake.

**Read `../../references/scala/glue-recipes.md` (recipes G1–G12) before
touching any file that imports `com.amazonaws.services.glue`.** There are no
Scalafix Glue recipes in Phase 0.5 — the fixer handles all Glue patterns
directly. Use `// SCOS: [SPRKCNTSCL36xx]` comment codes from
`../../references/scala/ewi-codes.md`.

**Two traps that produce silently WRONG DATA, not an error** — a Glue
migration that compiles, runs, and reports success can still be dropping
columns and rows:

- **Identifier case (G2, `SPRKCNTSCL3602`)**: the Glue Data Catalog exposes
  **lowercase** column names; a native Snowflake read returns **UPPERCASE**.
  The rewrite therefore always emits
  `df = df.toDF(df.columns.map(_.toLowerCase): _*)` immediately after each
  converted read. **Never delete that line as redundant** — without it,
  case-sensitive downstream logic (`schema.fields.find(_.name == "key")`,
  hand-built `Map`s, `df.columns.contains("id")`) matches nothing and
  silently drops those columns (including primary keys). Compare migrated
  column counts against the source. Note this is the inverse direction of
  Rule 29 and both can apply in the same file.

- **Null predicate semantics (G5, `SPRKCNTSCL3604`)**: Glue's `Filter.apply`
  runs a Scala predicate per `DynamicRecord` (JVM `Option` semantics);
  Spark evaluates a `Column` expression under SQL three-valued logic. They
  diverge on NULL. A Glue `(r: DynamicRecord) => r.getField("op").exists(_ != "d")`
  **keeps** null-op rows (`None.exists(...)` is false, so the `.exists`-based
  filter passes null-op rows through), but `col("op") =!= "d"` is NULL for a
  null op so the row is **dropped**. The fix rule: **any negated or `=!=`
  predicate on a nullable column needs an `isNull` guard; positive predicates
  do not.**
  ```scala
  // BEFORE (Glue Scala)
  val upsert = Filter.apply(frame = dyf, f = (r: DynamicRecord) => r.getField("op").exists(_ != "d"))
  val delete  = Filter.apply(frame = dyf, f = (r: DynamicRecord) => r.getField("op").contains("d"))

  // AFTER (SCOS)
  // SCOS: [SPRKCNTSCL3604] guard required — col =!= is NULL for null op, row dropped without isNull
  val upsertDf = df.filter(col("op").isNull || col("op") =!= "d")
  val deleteDf = df.filter(col("op") === "d")  // positive predicate: no guard needed
  ```
  Validate per-branch row counts against the source.

**The rest of what you must handle (no Phase 0.5 recipe does this):**

- **Bootstrap (G1, `SPRKCNTSCL3600`/`3601`)**: replace `new GlueContext(sc)` +
  `Job.init(...)` / `Job.commit()` with `SnowparkConnectSession.builder().getOrCreate()`.
  Replace `GlueArgParser.getResolvedOptions(args, Array(...))` with a plain
  args-array parse. Drop the `SparkContext` instantiation entirely.
- **Catalog reads (G2, `SPRKCNTSCL3602`)**: replace
  `glueContext.getCatalogSource(...).getDynamicFrame()` with
  `spark.read.table(s"$db.$tbl")` + the lowercase normalization line.
- **Catalog writes (G11, `SPRKCNTSCL3609`)**: replace
  `glueContext.getSinkWithFormat(...).writeDynamicFrame(dyf)` with
  `df.write.mode(...).saveAsTable(tgt)`.
- **Bookmarks (G8, `SPRKCNTSCL3606`)**: `Job.init()` / `Job.commit()` are
  removed, which **silently turns an incremental job into a full reprocess**.
  Either re-establish incrementality with an external-stage directory table +
  Stream (per G8), or surface the change as a `Known Limitation` in the
  migration header.
- **Connector writeback (G11, `SPRKCNTSCL3608`)**: the Snowflake Spark
  connector is not usable inside SCOS. Rewrite as
  `saveAsTable(<T>_TEMP)` → `CREATE TABLE IF NOT EXISTS ... WHERE 1=0` →
  `MERGE ...` → `DROP TABLE ..._TEMP`. Process upserts before deletes;
  guard the delete MERGE with `spark.catalog.tableExists(tgt)` for
  delete-only first runs.
- **DynamicFrame wrappers (G6, `SPRKCNTSCL3605`)**: drop `.toDF()` /
  `DynamicFrame(df, gc)` round-trips. Remove the `gc: GlueContext` parameter
  from every helper that threaded it through. Change `dyf.schema().fields`
  to `df.schema.fields` (method call → property access).
- **Transforms (G10, `SPRKCNTSCL3605`)**: `DropFields.apply(...)` →
  `df.drop(...)`, `SelectFields.apply(...)` → `df.select(...)`,
  `RenameField.apply(...)` → `df.withColumnRenamed(...)`.
- **ApplyMapping (G4, `SPRKCNTSCL3603`)**: rewrite as a `select` projection —
  see G4 for the full mapping (Glue type names differ from Spark type names).
- **Thread pools (G12)**: default to serial (`Seq.foreach`); if parallelizing
  with `Future`s or an `ExecutorService`, each table must write to its own
  temp table.

**Completeness bar**: no `com.amazonaws.services.glue` import may survive
anywhere in the output, and no `DynamicFrame` / `GlueContext` / `.apply(frame=`
call may remain live.

---

## Issue Processing Checklist

After processing all issues from `analysis.json`, verify completeness:

- [ ] Every issue in `analysis.json` has been reviewed and carries a verdict
  (an inline `// SCOS:` comment **or** a `resolution` field)
- [ ] All high-risk issues (`final_risk` >= 0.7) have fixes applied, a `TODO`, or a `resolution: "safe"` **with** a `resolution_reason`
- [ ] All medium-risk issues (`final_risk` >= 0.3) have fixes, TODO comments, or a `resolution`
- [ ] All low-risk issues (`final_risk` < 0.3) have fixes or a `resolution`
- [ ] No issue marked `resolution: "safe"` has an empty `resolution_reason`
- [ ] **Recipe-aware checks** (when `kind` field is present on issues):
  - [ ] No `kind="recipe_validated"` issue was re-edited (the Scalafix rule's
    output must round-trip unchanged)
  - [ ] Every `kind="recipe_incomplete"` issue with a non-null
    `suggested_fixer_action` had that action applied verbatim (or was
    explicitly downgraded to a TODO with reason)
  - [ ] Every `kind="recipe_adjacent"` issue has a `// SCOS: recipe-coverage
    gap` annotation naming `suggested_recipe_id`

### Files with No Issues

For files in the manifest that had **no issues** reported by the analysis tool: no changes are needed in this step. These files will still be processed for import updates and migration headers in Phase 3 — **do not** add a migration header yourself here. Confirm you have accounted for them:

```
Step 3 Summary:
  Files with fixes applied: N
  Files with no issues:     M
  Total in manifest:        N + M  ← must match manifest count
```

**Do NOT proceed to import updates until ALL issues have been addressed and the file count is confirmed.**
