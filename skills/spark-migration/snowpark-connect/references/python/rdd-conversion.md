# RDD to DataFrame Conversion Reference — Python

RDD operations are **not supported** in SCOS (Snowpark Connect on Snowflake).
Spark Connect has no RDD surface: `SparkContext` is unavailable and
`SparkSession.sparkContext` raises `PySparkNotImplementedError` at the
`.sparkContext` access itself. Any RDD hop is a hard runtime failure, so every
RDD operation **must** be rewritten as DataFrame / Snowpark Connect logic.

**Conversion policy (in priority order):**

1. **Native DataFrame functions** (`pyspark.sql.functions`) — always prefer these.
2. **Window functions / SQL expressions** — for ordering, indexing, ranking.
3. **UDF** — only when the user's per-row logic has no native equivalent.
4. **`# SCOS-TODO [SPRKCNTPY1500]`** — when there is no SCOS equivalent at all
   (see [§11](#11-no-equivalent--emit-a-todo)). Never silently drop the operation.

Tag every rewrite with the EWI code: `# SCOS: [SPRKCNTPY1500] <what changed>`.
SparkContext-specific entry points (`broadcast`, `accumulator`, `setLogLevel`, …)
use `SPRKCNTPY4000` / `SPRKCNTPY4002` instead.

Assume these imports are available in examples:

```python
from pyspark.sql import functions as F, Window
```

---

## Quick reference table

### Creation / entry points

| RDD op | DataFrame equivalent | Notes |
|---|---|---|
| `sc.parallelize(data)` | `spark.createDataFrame(data, schema)` | supply an explicit schema (rewritten by `sc_parallelize_to_createdataframe_rewrite`; schema still must be added) |
| `sc.range(n)` | `spark.range(n)` | returns `DataFrame[id: bigint]` |
| `sc.emptyRDD()` | `spark.createDataFrame([], schema)` | schema required |
| `sc.textFile(path)` | `spark.read.text(path)` | 1-col `DataFrame[value: string]` |
| `sc.wholeTextFiles(path)` | `spark.read.text(path, wholetext=True)` (one row per **file**, not per line) + `F.input_file_name()` for the path | rewritten by `sc_wholetextfiles_to_read_text_rewrite`; verify `input_file_name` in SCOS |
| `sc.binaryFiles(path)` | — | **no SCOS equivalent → TODO**: SCOS registers no `binaryFile` reader (`map_read` accepts only csv/json/parquet/text/xml) and its file I/O is UTF-8 only. Left unrewritten; `sparkcontext_property_fallback_rewrite` annotates it |
| `sc.binaryRecords(path, len)` | — | **no SCOS equivalent → TODO** (binary read unsupported) |
| `sc.sequenceFile / objectFile / pickleFile` | — | **no equivalent → TODO** |
| `sc.hadoopFile / hadoopRDD / newAPIHadoopFile / newAPIHadoopRDD` | — | **no equivalent → TODO** |
| `sc.broadcast(v)` | use the value directly; `F.broadcast(df)` for join hints | `SPRKCNTPY4000` |
| `sc.accumulator(...)` / `sc.collectionAccumulator()` / `AccumulatorParam` / `AccumulatorV2` | rewrite the driver-side accumulator as a **DataFrame aggregation** — `F.count(F.when(cond, 1))` (count matches), `F.sum`/`F.min`/`F.max`/`F.avg` (numeric reductions), `F.collect_set` (distinct IDs), `F.expr("APPROX_PERCENTILE(...)")` (sketches) — see §12 | **has a workaround — do NOT blanket-TODO** (`SPRKCNTPY4000`). Only cache-hit counting, mid-job progress polling, and `foreachPartition` are permanent hard gaps (⚠perm); `writeStream.foreachBatch` is not-currently-supported (⚠ns) with a workaround (§13). |
| `SparkContext.getOrCreate()` / `SparkContext(conf=…)` / `from pyspark import SparkContext` | drop — use the existing `spark` (SparkSession); there is no SparkContext under Connect | `SPRKCNTPY4001` |
| `sc.getConf().get(k)` / `sc.getConf()` | `spark.conf.get(k)` / `spark.conf` | `SPRKCNTPY4000` |
| `sc.hadoopConfiguration.set(k, v)` | drop — Snowflake manages storage auth via a storage integration / stage; cloud creds do not flow through Hadoop conf | `SPRKCNTPY3202` |
| `sc.setLogLevel(level)` | drop — no client-settable cluster log level under Spark Connect | `SPRKCNTPY4000` |
| `from pyspark import RDD` / `from pyspark.rdd import …` | remove the import; rewrite the RDD usage it enables | `SPRKCNTPY1500` (flagged by `pyspark_rdd_import_todo_annotate`) |
| `df.rdd` | remove — use the DataFrame directly | |
| `rdd.toDF(schema)` | **Native** (§16.14): `spark.createDataFrame(data, schema)` with an **explicit** schema (no `sampleRatio` inference on SCOS). Often the `.rdd`/`toDF` hop shouldn't exist at all — if the source came from RDD transforms, rewrite those to DataFrame ops first | `SPRKCNTPY1500` |
| `rdd.context` (→ `sc.applicationId`, …) | **Partial** (§16.15): no `SparkContext` under Connect — the `.rdd`/`.context` hop is dropped; only a terminal property read survives via a `getattr(spark, prop, "scos-unsupported")` fallback (intent-only). Genuine `SparkContext` capabilities → human TODO | `SPRKCNTPY4002` / `SPRKCNTPY4000` |

> **Deterministic detection note.** RDD usage is detected even when the RDD is
> bound to a variable and operated on in a later statement (e.g.
> `rdd = sc.parallelize(...)` then `out = rdd.reduceByKey(add)`). Method names
> that exist **only** on RDD — `reduceByKey`, `reduceByKeyLocally`, `groupByKey`,
> `aggregateByKey`, `foldByKey`, `combineByKey`, `sampleByKey`, `countByKey`,
> `countByValue`, `mapValues`, `flatMapValues`, `keyBy`, `zipWithIndex`,
> `zipWithUniqueId`, `sortByKey`, `mapPartitions`, `mapPartitionsWithIndex`,
> `takeOrdered`, `takeSample`, `saveAsTextFile`, and the aggregate/§10 additions
> `treeAggregate`, `treeReduce`, `collectAsMap`, `countApprox`,
> `countApproxDistinct`, `meanApprox`, `sumApprox`, `collectWithJobGroup`,
> `mapPartitionsWithSplit`, `repartitionAndSortWithinPartitions`,
> `saveAsPickleFile`, `saveAsObjectFile`, `getStorageLevel`, `toDebugString` — are unambiguous, so the
> `rdd_exclusive_method_todo_annotate` recipe annotates any call to them
> regardless of receiver, and the analyzer flags them without requiring a
> co-located `.rdd`/`sc.` token. Ambiguous names that also exist on DataFrame
> (`map`, `filter`, `collect`, `count`, `distinct`, `union`, `join`, …) still
> require an RDD context token to avoid false positives. RDD imports and `: RDD`
> / `-> RDD` type annotations are also detected at file scope.

### Transformations

| RDD op | DataFrame equivalent |
|---|---|
| `rdd.map(f)` | `df.select(...)` / `df.withColumn(...)` |
| `rdd.flatMap(f)` | `df.select(F.explode(...))` |
| `rdd.filter(f)` | `df.filter(cond)` / `df.where(cond)` |
| `rdd.mapValues(f)` | `df.withColumn("value", expr)` |
| `rdd.flatMapValues(f)` | `df.withColumn("value", expr)` then `F.explode` |
| `rdd.keyBy(f)` | `df.withColumn("key", expr)` |
| `rdd.groupBy(f)` | `df.withColumn("key", expr).groupBy("key").agg(F.collect_list(F.struct(...)))` — `collect_list` drops NULLs & is unordered (`F.array_sort` if order matters); `numPartitions`/`partitionFunc` have no SCOS meaning (§16.1) |
| `rdd.mapPartitions(f)` / `mapPartitionsWithIndex(f)` | `df.mapInPandas(...)` or a UDF |
| `rdd.mapPartitionsWithSplit(f)` | **Partial** — deprecated alias of `mapPartitionsWithIndex`; `df.mapInPandas(...)` runs the per-partition Python but **the split index is dropped** (SCOS partitions are internal/random) — redesign any index-driven logic (§16.2) |
| `rdd.pipe(cmd)` | **no equivalent → TODO** |
| `rdd.glom()` | **no equivalent → TODO** (exposes partition layout) |

### Pair / key-value

| RDD op | DataFrame equivalent |
|---|---|
| `rdd.reduceByKey(f)` | `df.groupBy("key").agg(...)` |
| `rdd.reduceByKeyLocally(f)` | `df.groupBy("key").agg(...).collect()` |
| `rdd.groupByKey()` | `df.groupBy("key").agg(F.collect_list("value"))` |
| `rdd.aggregateByKey(z)(seq, comb)` | `df.groupBy("key").agg(...)` |
| `rdd.combineByKey(...)` | `df.groupBy("key").agg(...)` |
| `rdd.foldByKey(z)(f)` | `df.groupBy("key").agg(...)` |
| `rdd.sampleByKey(...)` | `df.sampleBy("key", fractions)` |
| `rdd.keys()` | `df.select("key")` |
| `rdd.values()` | `df.select("value")` |
| `rdd.lookup(k)` | `df.filter(F.col("key") == k).select("value").collect()` |
| `rdd.cogroup(other)` / `groupWith` | full-outer `join` + `F.collect_list` per side |
| `rdd.subtractByKey(other)` | left-anti join on key: `df1.join(df2, "key", "left_anti")` (not detected today; included for completeness) |

### Joins

| RDD op | DataFrame equivalent |
|---|---|
| `rdd1.join(rdd2)` | `df1.join(df2, "key")` |
| `rdd1.leftOuterJoin(rdd2)` | `df1.join(df2, "key", "left")` |
| `rdd1.rightOuterJoin(rdd2)` | `df1.join(df2, "key", "right")` |
| `rdd1.fullOuterJoin(rdd2)` | `df1.join(df2, "key", "outer")` |
| `rdd1.cartesian(rdd2)` | `df1.crossJoin(df2)` |

### Sorting

| RDD op | DataFrame equivalent |
|---|---|
| `rdd.sortByKey()` | `df.orderBy("key")` |
| `rdd.sortBy(f)` | `df.orderBy(expr)` |

### Set operations

| RDD op | DataFrame equivalent |
|---|---|
| `rdd1.union(rdd2)` | `df1.union(df2)` (or `df1.unionByName(df2)`) |
| `rdd1.intersection(rdd2)` | `df1.intersect(df2)` |
| `rdd1.subtract(rdd2)` | **set** semantics (dedups): `df1.subtract(df2)` (SQL `EXCEPT`). **Multiset** (keeps duplicates): `df1.exceptAll(df2)`. RDD `subtract` keeps unmatched duplicates from the left, so `exceptAll` is usually closer — pick by whether dedup is intended. |
| `rdd.distinct()` | `df.distinct()` |

### Aggregation actions

| RDD op | DataFrame equivalent |
|---|---|
| `rdd.reduce(f)` | `df.agg(...)` — associative reduce; **empty raises on RDD, returns NULL on SCOS** — guard (§14.5, §15) |
| `rdd.treeAggregate(z)(seq, comb, depth)` | `df.agg(...)` — same result as `aggregate`; **drop `depth`** (no SCOS analogue); watch the non-identity-seed rule (§14.2) |
| `rdd.treeReduce(f, depth)` | `df.agg(...)` — degenerate `treeAggregate`; **drop `depth`**; raises on empty (§14.4) |
| `rdd.fold(z)(f)` | `df.agg(...)` — seeded; **non-identity seed applies once, not partitions+1 times** (§14.6, §15) |
| `rdd.aggregate(z)(seq, comb)` | `df.agg(...)` — non-identity seed applied once: `F.sum(...) + seed` (§14.1, §15) |
| `rdd.count()` | `df.count()` |
| `rdd.countApprox(timeout, confidence)` | `df.count()` — **Workaround**: SCOS has no time-bounded/confidence approximate count; time budget & CI dropped, you get an exact `int` not a `BoundedFloat` (§16.6) |
| `rdd.countApproxDistinct(relativeSD)` | `df.agg(F.approx_count_distinct(col))` — **Native** (HyperLogLog); `relativeSD` is **not tunable** (dropped); ignores NULLs. **Do not** confuse RDD `countApproxDistinct` with SCOS `approx_count_distinct` semantics beyond the count (§16.7) |
| `rdd.meanApprox(timeout, confidence)` | `df.agg(F.avg(col))` — **Workaround**: exact `float`, no `BoundedFloat`/timeout/CI; empty → NULL (§16.8) |
| `rdd.sumApprox(timeout, confidence)` | `df.agg(F.coalesce(F.sum(col), F.lit(0.0)))` — **Workaround**: exact SUM, no `BoundedFloat`/timeout/CI; `coalesce` restores `sumApprox`'s 0.0 empty behavior (§16.9) |
| `rdd.countByKey()` | `df.groupBy("key").count().collect()` |
| `rdd.countByValue()` | `df.groupBy(df.columns).count().collect()` |
| `rdd.sum() / max() / min() / mean()` | `df.agg(F.sum / F.max / F.min / F.avg(col))` |
| `rdd.variance() / stdev()` | `df.agg(F.var_pop / F.stddev_pop(col))` |
| `rdd.sampleVariance() / sampleStdev()` | `df.agg(F.var_samp / F.stddev_samp(col))` |
| `rdd.stats()` | `df.select(F.count, F.mean, F.stddev, F.min, F.max ...)` or `df.summary()` / `df.describe()` (not detected today; included for completeness) |
| `rdd.histogram(buckets)` | `F.width_bucket(...)` + `groupBy().count()` (verify `width_bucket` in SCOS; else **TODO**) |
| `rdd.countApproxDistinct(relativeSD)` | `df.agg(F.approx_count_distinct(col))` — **drop the `relativeSD`/`rsd` arg**: it is unsupported in Spark Connect (`SnowparkConnectNotImplementedError 4001`), so use default HLL precision. The `approx_count_distinct_drop_rsd_rewrite` recipe strips `rsd` on the DataFrame-function form. Do NOT comment this out — `approx_count_distinct` IS supported. |
| `rdd.countApproxDistinctByKey(relativeSD)` | `df.groupBy("key").agg(F.approx_count_distinct(col))` (drop `relativeSD` as above) |

### Driver / collection actions

| RDD op | DataFrame equivalent |
|---|---|
| `rdd.collect()` | `df.collect()` |
| `rdd.collectAsMap()` | `{r["k"]: r["v"] for r in df.select("k", "v").collect()}` — **Workaround**: last-wins is order-dependent (undefined on both engines); use a `row_number` window for a defined winner; bounded by driver memory (§16.5) |
| `rdd.collectWithJobGroup(groupId, desc, ...)` | `df.collect()` — **Partial**: data returns but **all job-group tracking/cancellation args are silently dropped** (RDD/SparkContext-only) (§16.10) |
| `rdd.first()` | `df.first()` |
| `rdd.take(n)` | `df.take(n)` (= `df.limit(n).collect()`) |
| `rdd.takeOrdered(n)` | `df.orderBy(col).limit(n).collect()` |
| `rdd.takeSample(...)` | `df.sample(frac).limit(n).collect()` |
| `rdd.top(n)` | `df.orderBy(F.col(c).desc()).limit(n).collect()` |
| `rdd.toLocalIterator()` | `df.toLocalIterator()` |
| `rdd.toDebugString()` | `df.explain()` — **Partial**: closest debugging intent, but emits Snowflake's simplified plan, **not** the RDD lineage chain (§16.16) |
| `rdd.foreach(f)` | collect + Python loop, or a side-effecting UDF |
| `rdd.foreachPartition(f)` | `df.mapInPandas(...)` (or **TODO**) |
| `rdd.isEmpty()` | `df.isEmpty()` |
| `rdd.zip(other)` | join on a generated row index (see [§8](#8-zip--indexing)) |
| `rdd.zipWithIndex()` | `row_number()` window (0-based; see [§8](#8-zip--indexing)) |
| `rdd.zipWithUniqueId()` | `F.monotonically_increasing_id()` (unique but **not** contiguous and not stable across recompute/repartition — differs from RDD's partition-derived numbering) |

### Sampling / splitting

| RDD op | DataFrame equivalent |
|---|---|
| `rdd.sample(withReplacement, frac)` | `df.sample(withReplacement, frac, seed)` — keep the `withReplacement` flag (verify with-replacement support in SCOS); do not silently drop it |
| `rdd.randomSplit(weights)` | `df.randomSplit(weights)` |

### Saving

| RDD op | DataFrame equivalent |
|---|---|
| `rdd.saveAsTextFile(path)` | `df.write.text(path)` / `df.write.csv(path)` |
| `rdd.saveAsSequenceFile` | **no equivalent → TODO** (Java-serialized/Hadoop sink) |
| `rdd.saveAsObjectFile(path)` | **Partial** (§16.11): only when the object round-trip merely persists a DataFrame within the job — `df.write.parquet(path)` / `.saveAsTable(t)` then `spark.read.parquet` / `spark.table`. Reading pre-existing external object files or persisting arbitrary Python/Java objects → **TODO** (SCOS has no Java-object reader/writer) |
| `rdd.saveAsPickleFile(path, batchSize)` | **Partial** (§16.12): only when the pickle round-trip merely persists a DataFrame within the job — `df.write.parquet(path)` / `.saveAsTable(t)` then `spark.read.parquet` / `spark.table`. Reading external pickle files or non-tabular objects → **TODO** (SCOS has no pickle reader/writer) |

### Partitioning / caching

| RDD op | DataFrame equivalent |
|---|---|
| `rdd.cache()` | `df.cache()` |
| `rdd.persist(level)` | `df.cache()` (storage level dropped) |
| `rdd.unpersist()` | `df.unpersist()` |
| `rdd.checkpoint() / localCheckpoint()` | `df.cache()` |
| `rdd.repartition(n) / coalesce(n)` | drop the `.rdd` hop; the DataFrame `repartition`/`coalesce` is **accepted** (not a no-op — controls write file count). Leave it; **do not** call it a no-op. |
| `rdd.partitionBy(numPartitions, partitionFunc)` | **Partial** (§16.3): SCOS has **no physical key partitioner**. Express the intent — per-key `df.groupBy("key").agg(...)`, or a table `CLUSTER BY (key)` for scan pruning. `numPartitions`/`partitionFunc` layout is lost (`repartition(n, col)` is a no-op hint, §15) |
| `rdd.repartitionAndSortWithinPartitions(...)` | **Partial** (§16.4): `df.repartition(n, F.col("key")).sortWithinPartitions(...)` expresses the intent, but `repartition(n, col)` is a no-op hint and `sortWithinPartitions` degenerates to a **global `ORDER BY`**; not physically co-located. For durable co-location use a table `CLUSTER BY (key)` |
| `rdd.getStorageLevel()` | `df.storageLevel` — **Partial** (§16.13): SCOS returns a **hardcoded** `StorageLevel(use_disk=True, use_memory=True)`; it can never report `NONE` or the real level — **never branch on it** (manual-review item) |
| `rdd.getNumPartitions()` | no meaningful value under Spark Connect — remove / **TODO** |
| `rdd.isCheckpointed() / getCheckpointFile()` | **no equivalent → TODO** |

> ⚠️ `repartition` / `coalesce`: do **not** annotate the surviving DataFrame call
> as a "no-op". Per the fixer's Rule 4 they are accepted and `repartition(n)` /
> `coalesce(n)` hint the `COPY INTO` output-file count. (Rule 4 covers
> `hint`/`repartition`/`coalesce` only — `partitionBy` is **not** in scope and is
> not flagged by the detector today.)

> **Recipe note — the `.rdd` hop.** When an identical-signature method is reached
> through the unsupported `.rdd` hop, `df_rdd_passthrough_rewrite` drops the hop
> deterministically (`df.rdd.<m>(...)` → `df.<m>(...)`) for
> `isEmpty`, `toLocalIterator`, `collect`, `count`, `first`, `take`, `distinct`,
> `cache`, `unpersist`, `repartition`, `coalesce`. Two `.rdd` methods are handled
> by sibling recipes instead: `df.rdd.persist(level)` →
> `rdd_persist_to_cache_rewrite` (drops the storage level), and
> `df.rdd.getNumPartitions()` → `rdd_no_equivalent_todo_annotate`. Everything else
> on `df.rdd` (`map`, `flatMap`, `keyBy`, `zipWithIndex`, …) is left for the LLM
> fixer.

---

## Worked examples

### 1. Word count (flatMap + map + reduceByKey)

```python
# BEFORE:
# sc.textFile("data.txt").flatMap(lambda x: x.split(" ")) \
#   .map(lambda w: (w, 1)).reduceByKey(lambda a, b: a + b)
# AFTER:
(
    spark.read.text("data.txt")
    .select(F.explode(F.split(F.col("value"), " ")).alias("word"))
    .groupBy("word")
    .agg(F.count("*").alias("count"))
)
```

### 2. map / withColumn

```python
# BEFORE: rdd.map(lambda x: x * 2)
# AFTER:
df.select((F.col("value") * 2).alias("value"))

# BEFORE: rdd.map(lambda r: (r.id, r.amount * 1.1))
# AFTER:
df.select(F.col("id"), (F.col("amount") * 1.1).alias("amount"))
```

### 3. filter

```python
# BEFORE: rdd.filter(lambda x: x > 10)
# AFTER:
df.filter(F.col("value") > 10)
```

### 4. groupByKey / reduceByKey / aggregateByKey

```python
# BEFORE: rdd.groupByKey()
# AFTER:
df.groupBy("key").agg(F.collect_list("value").alias("values"))

# BEFORE: rdd.reduceByKey(lambda a, b: a + b)
# AFTER:
df.groupBy("key").agg(F.sum("value").alias("value"))

# BEFORE: rdd.aggregateByKey(0)(lambda acc, v: acc + v, lambda a, b: a + b)
# AFTER (a + b is just a sum):
df.groupBy("key").agg(F.sum("value").alias("value"))
```

### 5. Joins (all variants)

```python
# BEFORE: rdd1.join(rdd2)            AFTER: df1.join(df2, "key")
# BEFORE: rdd1.leftOuterJoin(rdd2)   AFTER: df1.join(df2, "key", "left")
# BEFORE: rdd1.rightOuterJoin(rdd2)  AFTER: df1.join(df2, "key", "right")
# BEFORE: rdd1.fullOuterJoin(rdd2)   AFTER: df1.join(df2, "key", "outer")
# BEFORE: rdd1.cartesian(rdd2)       AFTER: df1.crossJoin(df2)
```

### 6. Sorting

```python
# BEFORE: rdd.sortByKey()            AFTER: df.orderBy("key")
# BEFORE: rdd.sortBy(lambda r: r[1]) AFTER: df.orderBy(F.col("_2"))
# descending:                        AFTER: df.orderBy(F.col("key").desc())
```

### 7. Aggregation actions (reduce / mean / countByValue)

```python
# BEFORE: rdd.map(lambda r: r.amount).reduce(lambda a, b: a + b)
# AFTER:
df.agg(F.sum("amount").alias("total")).collect()[0]["total"]

# BEFORE: rdd.map(lambda r: r.amount).mean()
# AFTER:
df.agg(F.avg("amount")).collect()[0][0]

# BEFORE: rdd.countByValue()
# AFTER:
df.groupBy(df.columns).count().collect()
```

### 8. zip / indexing

`zipWithIndex` is deterministic and 0-based in Spark. The closest SCOS-safe form
uses a window; it requires an explicit ordering to be deterministic.

```python
# BEFORE: rdd.zipWithIndex()
# AFTER (0-based, deterministic given an order column):
w = Window.orderBy("some_order_col")
df.withColumn("index", F.row_number().over(w) - 1)

# BEFORE: rdd.zipWithUniqueId()   (unique but not contiguous)
# AFTER:
df.withColumn("uid", F.monotonically_increasing_id())
```

> ⚠️ `row_number().over(Window.orderBy(...))` over an unpartitioned window
> serializes all rows through one partition. Acceptable for indexing semantics but
> note the cost. If the original code only needed *a* unique id (not 0..N-1),
> prefer `monotonically_increasing_id()`.

### 9. mapPartitions → mapInPandas / UDF

```python
# BEFORE: rdd.mapPartitions(lambda it: (heavy(x) for x in it))
# AFTER (native if expressible):
df.withColumn("out", heavy_expr(F.col("in")))

# AFTER (when per-row Python logic is unavoidable):
from pyspark.sql.types import StringType

@F.udf(StringType())
def heavy(value):
    return _do_work(value)

df.select(heavy(F.col("in")).alias("out"))
```

### 10. UDF fallback (only when native functions won't work)

```python
from pyspark.sql.types import StringType

@F.udf(StringType())
def complex_transform(val):
    return val.upper() + "_processed"

df.select(complex_transform(F.col("name")).alias("result"))
```

> Prefer native functions. A UDF runs row-by-row on the Snowflake Python worker
> and forfeits vectorized pushdown — use it only when the logic genuinely has no
> column-expression equivalent.

---

## 11. No equivalent → comment out the call and emit a TODO

Some RDD operations have **no** SCOS/Snowflake equivalent. Do not improvise a
rewrite. **Comment out the offending call (do not delete it) and attach a TODO**
so the migrated module still imports and runs — leaving the call *live* would
raise `AttributeError` / `PySparkNotImplementedError` at runtime and crash the
whole file, taking the convertible code around it down too. Preserve the
original line as a comment so the reviewer can see what to migrate:

```python
# SCOS-TODO: [SPRKCNTPY1500] rdd.glom() has no SCOS equivalent (exposes partition
# layout, which Snowflake manages and does not expose). Re-express the logic
# without partition-level access, or operate on the DataFrame directly.
# partitions = sorted_df.glom().collect()   # <- original, no SCOS equivalent
```

- **Comment out, never silently drop.** A commented-out original + TODO is
  visible and reviewable; a deleted line is a silent behavior change.
- **Comment out, don't leave live.** A live no-equivalent call crashes the module
  at runtime; commenting it out keeps the file importable and lets the converted
  code run.
- For a **mixed** chain, convert the convertible ops and comment out only the
  no-equivalent op(s) — do not comment out the whole statement if part of it
  converts cleanly.
- (Test-file note: commenting out an *assertion* that depends on a no-equivalent
  op makes that test vacuously pass. That is acceptable for making a workload
  run; for eval test suites, prefer commenting the assertion **and** leaving the
  TODO so the gap is not mistaken for real coverage.)

```python
# SCOS-TODO: [SPRKCNTPY1500] sc.sequenceFile has no SCOS equivalent
# (Hadoop/Java-serialized I/O). Re-express the data as a supported Snowflake
# source (stage file / table) before migrating.
```

Ops in this bucket:

- **I/O with no analogue:** `sequenceFile`, `objectFile`, `pickleFile`,
  `hadoopFile`, `hadoopRDD`, `newAPIHadoopFile`, `newAPIHadoopRDD`,
  `saveAsSequenceFile`, `binaryFiles`, `binaryRecords`
  (SCOS has no `binaryFile` reader and its file I/O is UTF-8 only). The *save*
  ops `saveAsObjectFile` / `saveAsPickleFile` are instead **Partial** — a
  DataFrame-shaped parquet/table round-trip (§16.11 / §16.12); only *reading*
  pre-existing object/pickle files or persisting non-tabular objects is a TODO.
- **Execution primitives with no analogue:** `pipe` (forks an external process),
  `glom` (exposes partition layout), `getNumPartitions`, `isCheckpointed`,
  `getCheckpointFile`, `id` (RDD identity), `barrier()` / `_is_barrier`
  (barrier-execution mode), `collectWithJobGroup` /
  `sc.cancelJobGroup` / `sc.setJobGroup` (per-job cancellation), and
  `toLocalIterator(prefetchPartitions=...)` (the prefetch hint has no SCOS
  meaning — drop the kwarg, but timing-dependent tests around it cannot be
  reproduced).
- **Partition/serializer internals:** `mapPartitions` / `mapPartitionsWithIndex`
  whose function depends on partition boundaries or index (partitioning is
  Snowflake-managed and not observable), `repartitionAndSortWithinPartitions`,
  and serializer plumbing (`_reserialize`, `BatchedSerializer`,
  `MarshalSerializer`, `CPickleSerializer`).
- **Resource / scheduler APIs:** `ResourceProfile` / `ResourceProfileBuilder` /
  `withResources` / `getResourceProfile` / `ExecutorResourceRequests` /
  `TaskResourceRequests` — SCOS does not expose executor/task resource control.
- **Arbitrary-accumulator aggregation:** `aggregate` / `treeAggregate` / `fold`
  (and the `*ByKey` variants) whose `zeroValue`/`seqOp`/`combOp` mutate a Python
  object (`defaultdict`, `list`, `set`) rather than compose an associative
  column expression — there is no DataFrame `agg` equivalent for arbitrary
  driver-side Python accumulation. (Associative numeric folds like `a + b`
  **are** convertible → `F.sum` etc.; only the mutable-accumulator form is a TODO;
  the convertible forms are covered in §14.)
- **SparkContext primitives** (use `SPRKCNTPY4000`): `broadcast(v)` can usually
  be replaced by using the value directly, or `F.broadcast(df)` for a join hint;
  a broadcast **variable dereferenced inside a `.map` closure**
  (`b = sc.broadcast(x); rdd.map(lambda r: b.value)`) has no SCOS equivalent —
  the closure/executor model differs — so it is a TODO.
  `accumulator` / `collectionAccumulator` / `AccumulatorParam` / `AccumulatorV2`
  are **NOT** blanket no-equivalents — the driver-side counter maps to a
  **DataFrame aggregation** (see [§12](#12-accumulators--dataframe-aggregation)).
  Only four accumulator uses are true hard gaps (see
  [§13](#13-true-hard-gaps)): three are **permanent** (⚠perm) — cache-hit
  counting, mid-job progress polling, and `foreachPartition` sinks — while
  `writeStream.foreachBatch` cross-batch state is **not currently supported**
  (⚠ns) with a manual per-batch-loop workaround, not a permanent no-equivalent.

---

## Verdict routing (how to apply the recipes below)

Every recipe in §12–§17 carries a **verdict tag** from the migration guide. The
fixer routes on it:

| Verdict | Meaning | Action |
|---|---|---|
| **[Native]** | first-class DataFrame equivalent | apply the rewrite; no caveat needed |
| **[Workaround]** | reproduces the intent with a rewrite | apply the rewrite |
| **[Silent-diff]** | rewrite exists but semantics drift silently (no error) | apply the rewrite **and** add a `# SCOS:` guard/note calling out the drift |
| **[Partial]** | intent only — SCOS cannot reproduce some aspect | apply the closest form **and** `# SCOS: TODO` for the lost aspect (index, ordering, layout, job-group, …) |
| **[Hard gap]** | no equivalent (⚠perm = permanent, ⚠ns = not currently supported) | `# SCOS: TODO`; for ⚠perm, delete-don't-migrate or keep on Spark |

Tag every applied rewrite: RDD→DataFrame rewrites use
`# SCOS: [SPRKCNTPY1500] <what changed>`; SparkContext primitives
(`accumulator`, `broadcast`, `parallelize`, …) use `# SCOS: [SPRKCNTPY4000]`;
property reads replaced by a `getattr` fallback use `# SCOS: [SPRKCNTPY4002]`;
hard gaps use `# SCOS: TODO - <why + Snowflake-native alternative>`.

---

## 12. Accumulators → DataFrame aggregation

**This is the #1 correction: an accumulator is NOT a blanket TODO.** A Spark
driver-side accumulator incremented inside `foreach`/`map`/a UDF is a *reduction*
— and a reduction is exactly what `df.agg(...)` / `df.groupBy(...).agg(...)` do
in one SQL pass. Rewrite the accumulator as the equivalent aggregate. Only the
four uses in [§13](#13-true-hard-gaps) have no equivalent.

> `df.observe()` metrics are a **no-op** on SCOS — compute each metric with an
> explicit `df.agg(...)` instead. A **module-level** `sc.accumulator(0)` /
> top-level `spark.sparkContext` call raises **on import** — move it inside a
> function or delete it.

### 12.1 Count rows meeting a condition — [Workaround]

```python
# BEFORE (Spark): LongAccumulator (sc.longAccumulator()) incremented in foreach on match
# neg_count = sc.accumulator(0)
# df.foreach(lambda r: neg_count.add(1) if r["amount"] is not None and r["amount"] < 0 else None)
# print(neg_count.value)
# AFTER:
# SCOS: [SPRKCNTPY4000] accumulator+foreach row-count -> conditional aggregate
neg_count = df.agg(F.count(F.when(F.col("amount") < 0, 1)).alias("n")).collect()[0]["n"]
```

### 12.2 Sum / min / max / avg a column — [Native]

```python
# BEFORE (Spark): custom AccumulatorParam / DoubleAccumulator (sc.doubleAccumulator()) reducing a column
# AFTER:
# SCOS: [SPRKCNTPY4000] AccumulatorParam reduction -> DataFrame aggregate
result = df.agg(
    F.min("amount").alias("min_amount"), F.max("amount").alias("max_amount"),
    F.sum("amount").alias("total"),      F.avg("amount").alias("avg_amount"),
    F.count("amount").alias("non_null_count"),
).collect()[0]
```

> **Silent-diff:** SQL `MIN/MAX/SUM/AVG` skip NULLs; if Spark treated NULL as a
> sentinel, wrap with `F.coalesce(F.col("amount"), F.lit(0))`. `F.sum` on empty
> input returns NULL, not 0 — guard with `coalesce` (§15).

### 12.3 Collect a bounded set of IDs — [Workaround, ⚠perm at scale]

```python
# BEFORE (Spark): collectionAccumulator gathering unique IDs
# seen_ids = sc.collectionAccumulator(); df.foreach(lambda r: seen_ids.add(r["user_id"]))
# AFTER:
# case A — unique IDs, small cardinality:
# SCOS: [SPRKCNTPY4000] collectionAccumulator -> collect_set
unique_ids = set(df.agg(F.collect_set("user_id").alias("ids")).collect()[0]["ids"])
# case B — dict of counts (user_id -> row count):
id_to_count = {r["user_id"]: r["n"]
               for r in df.groupBy("user_id").agg(F.count("*").alias("n")).collect()}
```

> **Silent-diff:** `collect_set`/`collect_list` silently **drop NULLs** and are
> **unordered** (§15). Near the 128 MB per-value cap (§15) prefer
> `df.select("user_id").distinct().collect()` (a row set, not one ARRAY column) —
> never assemble one huge server-side collection.

### 12.4 Count matches / non-matches in a join — [Native]

```python
# BEFORE (Spark): join, then accumulators count matched vs unmatched rows
# AFTER:
joined = orders.join(customers, "customer_id", "left")
# SCOS: [SPRKCNTPY4000] join match/unmatched accumulators -> conditional aggregates
counts = joined.agg(
    F.count("*").alias("total"),
    F.count("customer_name").alias("matched"),        # count(col) skips NULLs — the point
    F.count(F.when(F.col("customer_name").isNull(), 1)).alias("unmatched"),
).collect()[0]
```

### 12.5 Per-key operation / collision counters — [Workaround, ⚠perm]

```python
# BEFORE (Spark): reduceByKey/aggregateByKey with an accumulator counting merges
# AFTER:
totals = df.groupBy("category").agg(F.sum("amount").alias("total"))
# collision count == rows - unique keys, computed directly (per-pair merge counts
# do NOT carry over — SCOS runs one SQL aggregation, no per-pair merge):
# SCOS: [SPRKCNTPY4000] merge-collision accumulator -> rows minus distinct keys
collision_count = df.count() - df.select("category").distinct().count()
```

### 12.6 Accumulator inside a UDF — [Workaround]

```python
# BEFORE (Spark): a UDF closure increments an accumulator to count error rows
# AFTER: emit an error-flag column, then aggregate it
def process_amount(x):
    return (None, 1) if (x is None or x < 0) else (x * 1.1, 0)
udf = F.udf(process_amount, StructType([StructField("value", DoubleType()),
                                        StructField("error", IntegerType())]))
res = df.withColumn("out", udf(F.col("amount")))
# SCOS: [SPRKCNTPY4000] in-UDF error accumulator -> flag column + F.sum
error_count = res.agg(F.sum(F.col("out.error")).alias("errors")).collect()[0]["errors"]
```

> A `raise` inside a UDF/`mapInPandas`/`pandas_udf` closure **does** propagate to
> the driver, but a closure-side **counter/flag never aggregates** (the closure
> runs per-partition with no driver-visible shared state). Keep abort logic on
> the driver: `if df.filter(cond).count() > 0: raise ...`.

### 12.7 Approximate aggregates (histogram / quantile / top-K) — [Native]

```python
# BEFORE (Spark): a custom AccumulatorV2 building a histogram / quantile / top-K sketch
# AFTER:
# SCOS: [SPRKCNTPY4000] AccumulatorV2 sketch -> APPROX_PERCENTILE / exact groupBy
result = df.agg(
    F.expr("APPROX_PERCENTILE(latency_ms, 0.50)").alias("p50"),
    F.expr("APPROX_PERCENTILE(latency_ms, 0.99)").alias("p99"),
).collect()[0]
# top-K by frequency — APPROX_TOP_K raises "Unsupported function name" via F.expr;
# use an exact groupBy + count:
top_10 = df.groupBy("status").count().orderBy(F.desc("count")).limit(10).collect()
```

> On SCOS `APPROX_PERCENTILE` is mapped to Snowflake `PERCENTILE_DISC` — an
> **exact** dataset value, not a t-Digest approximation (no ~1% error).
> `APPROX_TOP_K` is **not** available via `F.expr` — use the exact
> `groupBy(col).count().orderBy(F.desc("count")).limit(k)`.
>
> **Validate against your own Spark baseline (not a paired-run capture):** the
> custom-sketch / quantile / top-K mappings here reflect known SCOS behavior,
> not a captured paired Spark↔SCOS run — confirm numerical parity on your data
> before relying on them.

---

## 13. True hard gaps

**Three** of these are **permanent** hard blockers (⚠perm) with no DataFrame
equivalent — `foreachPartition` external side-effects (§13.1), cache-hit
detection (§13.2), and mid-job live progress (§13.3). Do not improvise a
rewrite for those; delete-don't-migrate or keep the job on Spark. The **fourth**,
`writeStream.foreachBatch` (§13.4), is **not currently supported** (⚠ns) rather
than permanently impossible: it is **[Partial]** and has a documented manual
per-batch-loop workaround until SCOS adds support — do not describe it as a
no-equivalent hard gap.

### 13.1 `foreachPartition` external side-effects — [Hard gap] [⚠perm]

One long-lived Kafka producer / HTTP client / file handle per partition.
`df.rdd.foreachPartition(...)` raises `PySparkNotImplementedError` at the API
boundary (before the closure runs). SCOS runs your DataFrame as SQL on a
warehouse — there is no per-partition Python process. **Write from the driver
instead:**

```python
# SCOS: TODO - foreachPartition per-partition sinks are unsupported (⚠perm, architectural).
# Small/medium volumes — pull rows back and use ONE client-side producer:
producer = KafkaProducer(bootstrap_servers="...")
for r in df.collect():
    producer.send("events", r["value"].encode())
producer.flush()
# Large volumes — land rows in a table and drive egress Snowflake-side (Kafka connector / task):
# df.write.mode("append").saveAsTable("events_out")
```

### 13.2 Cache-hit detection — [Hard gap] [⚠perm]

An accumulator counting cache hits vs recomputations across
`persist()`/`unpersist()`. Snowflake's result cache is transparent — **no
client-visible hit/miss signal. Delete the counter.**

```python
# SCOS: TODO - cache-hit accumulator has no equivalent (⚠perm): Snowflake's result
# cache is transparent. Delete the counter. For perf debugging query
# SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY or TABLE(INFORMATION_SCHEMA.QUERY_HISTORY())
# (bytes_scanned ~0 indicates a cache hit). If the counter gated a code path, redesign.
```

### 13.3 Mid-job live progress snapshots — [Hard gap] [⚠perm]

A background thread polling `acc.value` on a timer. `.count()`/`.collect()`
blocks the thread and `acc.value` reads 0 throughout (no warehouse→driver
callback).

```python
# SCOS: TODO - live mid-job accumulator progress has no equivalent (⚠perm).
# Post-hoc snapshots — split the job into chunks and record after each:
snapshots, total = [], 0
for lo in range(0, 100, 10):
    total += df.filter((F.col("bucket") >= lo) & (F.col("bucket") < lo + 10)).count()
    snapshots.append(total)
# Real-time UIs: from a SEPARATE session poll
# SELECT * FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION())
```

### 13.4 `writeStream.foreachBatch` cross-batch state — [Partial] [⚠ns]

Running state across streaming micro-batches. `foreachBatch` is **not currently
supported** (the callback never executes). Workaround: a manual batch loop over
static DataFrames with cross-batch state in a Snowflake table — **preserves
state but loses trigger semantics**. For true streaming triggers keep the job on
Spark.

```python
# SCOS: TODO - writeStream.foreachBatch cross-batch state is not currently supported (⚠ns).
# BOUNDED backfill/reprocessing only — manual batch loop:
spark.sql("DROP TABLE IF EXISTS processed_events")   # reset state ONCE before the loop
def process_batch(df_batch):
    df_batch.write.mode("append").saveAsTable("processed_events")               # accumulate
    return spark.table("processed_events").agg(F.sum("amount")).collect()[0][0]  # running total
for batch_df in your_batch_source():   # YOUR iterator of static DataFrames
    total = process_batch(batch_df)
# Always re-run from the top (DROP resets state); do NOT resume mid-loop or rows double-count.
```

---

## 14. Aggregate & reduce recipes (`aggregate` / `treeAggregate` / `reduce` / `fold` family)

All nine RDD-aggregate ops have a DataFrame equivalent. The recurring traps are
collected in [§15](#15-silent-differences--platform-limits); each recipe notes
which apply.

### 14.1 `aggregate(zeroValue, seqOp, combOp)` — [Workaround]

```python
# BEFORE: rdd.aggregate((0,0), lambda a,x:(a[0]+x,a[1]+1), lambda a,b:(a[0]+b[0],a[1]+b[1]))  # (15,5)
# AFTER (e.g. an average):
# SCOS: [SPRKCNTPY1500] rdd.aggregate -> df.agg (read multi-stat off one Row)
row = df.agg(F.coalesce(F.sum("value"), F.lit(0)).alias("s"), F.count("*").alias("c")).first()
avg = row["s"] / row["c"] if row["c"] else None
```
Multi-stat in one pass: `df.agg(F.count("*"), F.sum("value"), F.min("value"), F.max("value")).first()`.
**Watch:** a non-identity `zeroValue` must be applied **once** — `F.sum(...) + seed` (§15); identity zeros (`0/1/""/[]`) need no adjustment.

### 14.2 `treeAggregate(zeroValue, seqOp, combOp, depth)` — [Workaround]

Same result as `aggregate`; **`depth` has no SCOS analogue — drop it.**

```python
# BEFORE: n,s,ss = rdd.treeAggregate((0,0.0,0.0), seqOp, combOp)  # mean/variance
# AFTER:
# SCOS: [SPRKCNTPY1500] rdd.treeAggregate -> df.agg (drop depth)
df.agg(F.avg("v").alias("mean"), F.var_pop("v").alias("var")).first()
# Dense fixed-width vector — explode & sum per index (bounded by width, not N):
[r["s"] for r in vdf.select(F.posexplode("vec").alias("i", "v"))
                    .groupBy("i").agg(F.sum("v").alias("s")).orderBy("i").collect()]
```
> Never `collect_list` a vector column — it builds one big array and hits the 128 MB ceiling (§15).

### 14.3 `aggregateByKey(zeroValue, seqFunc, combFunc)` — [Native]

```python
# BEFORE: rdd.aggregateByKey((0,0), lambda a,v:(a[0]+v,a[1]+1), lambda a,b:(a[0]+b[0],a[1]+b[1]))
# AFTER:
# SCOS: [SPRKCNTPY1500] rdd.aggregateByKey -> df.groupBy().agg()
df.groupBy("k").agg(F.sum(F.coalesce("v", F.lit(0))).alias("s"), F.count("*").alias("c"))
```
Per-key distinct set: `F.array_sort(F.collect_set("v"))` (watch 128 MB at extreme cardinality; for count-only use `F.approx_count_distinct`). `numPartitions`/`Partitioner`: drop (result is partition-independent).

### 14.4 `treeReduce(f, depth)` — [Workaround]

Degenerate `treeAggregate` (no seed); **raises on empty**; `depth` N/A.

```python
# BEFORE: rdd.treeReduce(operator.add)  # 15 ;  rdd.treeReduce(operator.mul)  # 120
# AFTER:
df.agg(F.coalesce(F.sum("v"), F.lit(0)))          # sum (F.min/F.max for reduce-min/max)
df.agg(F.round(F.product("v")).cast("long"))      # product — round+cast guard (§15)
# empty input: add `if df.count()==0: raise ...` to match Spark, or coalesce to identity
```

### 14.5 `reduce(f)` — [Native]

```python
# BEFORE: rdd.reduce(operator.add)  # 15 ; raises on empty
# AFTER:
df.agg(F.coalesce(F.sum("v"), F.lit(0))).first()[0]   # F.min/F.max for min/max
```
Product, empty-input, and non-associative `f` handled exactly as in §14.4/§15.

### 14.6 `fold(zeroValue, op)` / `foldByKey(zeroValue, func)` — [Workaround]

Like `reduce` but seeded; does not raise on empty.

```python
# BEFORE: rdd.fold(0, add) # 15 ;  rdd.fold(7, add) # 36 (partition-scaled!)
# AFTER:
df.agg(F.sum("v")).first()[0] or 0        # identity zero (or 0 restores empty=0, §15)
df.agg(F.sum("v")).first()[0] + 7         # seed once — 22, NOT Spark's partition-scaled 36 (§15)
# per-key:
df.groupBy("k").agg(F.coalesce(F.sum("v"), F.lit(0)))       # foldByKey(0, add)
df.groupBy("k").agg(F.round(F.product("v")).cast("long"))   # foldByKey(1, mul)
```

### 14.7 `combineByKey(...)` — [Native]

The most general per-key primitive (the others delegate to it).

```python
# BEFORE: rdd.combineByKey(lambda v:(v,1), lambda a,v:(a[0]+v,a[1]+1), ...).mapValues(lambda x:x[0]/x[1])
# AFTER (per-key average):
df.groupBy("k").agg(F.avg("v"))
# collect list/set per key:
df.groupBy("k").agg(F.array_sort(F.collect_list("v")).alias("list"),
                    F.array_sort(F.collect_set("v")).alias("set"))
```
Bespoke combiner with no SQL form: `df.groupBy("k").applyInPandas(fn, schema)` (Python-only; from Java use a native Java UDAF, §17, or keep on Spark).

### 14.8 `groupByKey()` — [Workaround] [⚠perm at scale]

```python
# BEFORE: rdd.groupByKey().mapValues(sorted)  # {'a':[1,2],...}
# AFTER:
df.groupBy("k").agg(F.array_sort(F.collect_list("v")))                     # collect per key
df.groupBy("k").agg(F.count("*").alias("star"), F.count("v").alias("v"))   # star counts NULLs, v doesn't
df.groupBy("k").agg(F.array_sort(F.collect_set("v")))                      # distinct (struct-wrap to keep NULLs)
```
> A single huge per-key group **raises** on SCOS (128 MB, no spill) where Spark's
> `collect_list` also OOMs — reduce inside the aggregate for large groups (§15).

---

## 15. Silent differences & platform limits

These give a **wrong answer with no error** (or are permanent platform limits).
Grep your codebase up front, then apply the guard inline in the recipe that hits
each one.

- **128 MB collection ceiling — [⚠perm].** A single ARRAY/OBJECT/VARIANT value
  is capped at ~128 MB uncompressed. `collect_list`/`collect_set`/map aggregates
  / a reassembled wide vector compile to one such value **per group** with no
  spill; a pathological single group **raises** a clean 128 MB error (Spark OOMs
  on the same input). **Mitigation:** reduce inside the aggregate (`F.sum`,
  `F.count`, `F.approx_count_distinct`, per-key `applyInPandas`); element-wise
  vector sums via `posexplode→groupBy(idx).sum` stay bounded. Fine for
  normal-sized groups.
- **Non-identity seed × partitions.** RDD `fold`/`aggregate` applies a
  non-identity `zeroValue` `partitions+1` times, so the RDD result is
  partition-count-dependent. Reproduce the **intent**: reduce, then apply the
  seed **once** — `F.sum("v") + seed`. Identity seeds (`0/1/""/[]`) need no
  adjustment.
- **`count("*")` vs `count(col)`.** `count(col)` skips NULLs; `count("*")` counts
  every row. Match RDD `len(values)` with `count("*")`. `countDistinct` over
  multiple cols drops rows where **any** col is NULL.
- **Empty input → NULL vs raise.** RDD `reduce`/`treeReduce` **raise** on empty;
  the DataFrame aggregate returns **NULL**. Guard with
  `F.coalesce(..., F.lit(0))`, or `if df.count()==0: raise ...` to match Spark.
- **`F.product` float noise.** Compiles to `exp(sum(ln x))` →
  `119.99999999999997` for an exact-integer product. Guard exact ints/decimals
  with `F.round(F.product("v")).cast("long")`.
- **`collect_set`/`collect_list` drop NULLs and are unordered.** Struct-wrap to
  keep NULLs (a struct is never NULL); `F.array_sort` for determinism (but see
  the struct-array sort note below).
- **Struct-array sort order — [⚠perm].** `array_sort` over a STRUCT array does
  **not** sort by the leading field on SCOS, and comparator-lambda `array_sort`
  is unsupported; a pre-aggregate `orderBy` does **not** guarantee intra-group
  `collect_list` order. For order-dependent output use the zero-padded
  string-prefix pattern (§15, "order-dependent output" below).
- **`repartition(n, keyCol)` is a no-op hint.** SCOS accepts it but does **not**
  physically reshuffle by key. Patterns relying on per-key co-location silently
  give wrong results — express per-key intent in the query (`groupBy`) or use a
  table `CLUSTER BY` key.
- **`df.sample()` seed ignored — [Silent-diff, ⚠perm].** Produces a valid sample
  but the **seed is ignored** (nondeterministic) and `withReplacement=True` is
  unsupported. For a **reproducible** split, bucket a stable key instead of
  seeding: `df.filter(F.pmod(F.hash("id"), F.lit(100)) < 80)` is the 80% side,
  `>= 80` the 20% (deterministic across runs). Reservoir-sampling reproducibility
  does not carry over. This sampling behavior reflects known SCOS behavior, not a
  paired-run capture — validate the sampling/reproducibility change against your
  own Spark baseline.
- **Unsupported sketch functions.** `count_min_sketch` — **[Workaround]**
  (server-side UDAF): ships and works; pass `epsilon` as a **float** literal (a
  decimal epsilon triggers an internal error). `bloom_filter_agg` — **[Hard gap,
  ⚠ns]** (raises "Unsupported function name") → `# SCOS: TODO`. For per-key
  frequency use exact `df.groupBy(key).count()`; `F.approx_count_distinct`
  answers distinct-**cardinality** only, not frequency.

**Order-dependent output (`array_sort` on structs) — [Workaround].** To order
values by a key, encode the key as a zero-padded string prefix, sort the scalar
strings, then strip the prefix (lexical sort == numeric sort). Copy-pasteable:

```python
# order values in `ch` by integer key `id` (non-negative keys; offset signed keys before padding)
enc = df.withColumn("enc", F.concat(F.lpad(F.col("id").cast("string"), 10, "0"), F.col("ch")))
enc.agg(F.array_join(
    F.transform(F.array_sort(F.collect_list("enc")),
                lambda s: s.substr(F.lit(11), F.length(s) - F.lit(10))), "").alias("s")).first()["s"]
# per-key variant: build `enc` from a df that HAS the group key `k`, then groupBy("k").agg(...)
```
> If order matters but there is **no** ordering column, it was never stored —
> **Hard gap**: add an explicit sequence column at ingest, then use the pattern.

---

## 16. Additional verified RDD operations (§10 of the guide)

Independently verified against the SCOS runtime. Quick-reference rows are in the
tables above; the per-op detail and the exact SCOS caveat are here.

### 16.1 `groupBy(f, numPartitions, partitionFunc)` — [Workaround]

```python
# BEFORE: rdd.groupBy(lambda x: x % 2)   # {0:[2,4], 1:[1,3,5]}
# AFTER: materialize the grouping key as a column, then groupBy + collect
# SCOS: [SPRKCNTPY1500] rdd.groupBy -> withColumn(key) + groupBy + collect_list
(df.withColumn("key", F.col("value") % 2)
   .groupBy("key").agg(F.collect_list(F.struct("value")).alias("items")))   # array_sort(items) if order matters
```
`collect_list` drops NULLs and is unordered; `numPartitions`/`partitionFunc` have no SCOS meaning.

### 16.2 `mapPartitionsWithSplit(f)` — [Partial]

Deprecated alias of `mapPartitionsWithIndex`. SCOS runs the per-partition Python
via `mapInPandas` but **cannot surface the split index** (partitions are
internal/random).

```python
# SCOS: [SPRKCNTPY1500] mapPartitionsWithSplit -> mapInPandas (index-independent only)
def double(itr):
    for pdf in itr:
        yield pdf.assign(value=pdf["value"] * 2)
df.mapInPandas(double, schema="value long")   # split index dropped
# SCOS: TODO - any index-driven logic must be redesigned (index genuinely unavailable)
```

### 16.3 `partitionBy(numPartitions, partitionFunc)` — [Partial]

SCOS has **no physical key partitioner**. Express the intent only:

```python
# (a) if partitionBy fed a per-key aggregation/reduce, do it in the query:
grouped = df.groupBy("key").agg(F.collect_list(F.struct("key", "value")))
# (b) if it was clustering for scan pruning, request it at the table:
# spark.sql("ALTER TABLE t CLUSTER BY (key)")
# SCOS: TODO - partitionBy's same-key-in-one-physical-partition guarantee and custom
# partitionFunc/numPartitions layout are lost (repartition-by-expression is a no-op).
```

### 16.4 `repartitionAndSortWithinPartitions(...)` — [Partial]

```python
# SCOS: [SPRKCNTPY1500] repartitionAndSortWithinPartitions -> repartition + sortWithinPartitions (intent only)
df.repartition(8, F.col("key")).sortWithinPartitions(F.col("key").asc())
# SCOS: TODO - repartition(n, col) is a no-op file-count hint; sortWithinPartitions degrades to a
# global ORDER BY; rows are NOT co-located by key. For durable co-location use a table CLUSTER BY (key).
```

### 16.5 `collectAsMap()` — [Workaround]

```python
# BEFORE: m = rdd.collectAsMap()   # {'a':3,'b':2} (last-wins, order-dependent)
# AFTER:
# SCOS: [SPRKCNTPY1500] collectAsMap -> dict comprehension over df.collect()
m = {r["k"]: r["v"] for r in df.select("k", "v").collect()}
# deterministic last-wins (needs an ordering column 'ord'):
# w = Window.partitionBy("k").orderBy(F.col("ord").desc())
# m = {r["k"]: r["v"] for r in df.withColumn("_rn", F.row_number().over(w)).filter(F.col("_rn")==1).collect()}
```
Matches Spark's equally-undefined last-wins ordering; bounded by driver memory.

### 16.6 `countApprox(timeout, confidence)` — [Workaround]

```python
# SCOS: [SPRKCNTPY1500] countApprox -> exact df.count() (time budget/confidence dropped)
total = df.count()   # exact int, not a BoundedFloat with a confidence range
```

### 16.7 `countApproxDistinct(relativeSD)` — [Native]

```python
# SCOS: [SPRKCNTPY1500] countApproxDistinct -> F.approx_count_distinct (Snowflake HLL)
n = df.agg(F.approx_count_distinct("value")).first()[0]
```
`relativeSD` is **not** supported (a second argument raises); Snowflake's fixed
HLL precision is used. Ignores NULLs (may differ by one where NULLs are
meaningful). **Not** the same as blindly upgrading — this is a verified Native map.

### 16.8 `meanApprox(timeout, confidence)` — [Workaround]

```python
# SCOS: [SPRKCNTPY1500] meanApprox -> exact F.avg (BoundedFloat/timeout/CI dropped)
mean = df.agg(F.avg("amount")).first()[0]   # empty -> NULL (acceptable for a best-effort approx)
```

### 16.9 `sumApprox(timeout, confidence)` — [Workaround]

```python
# SCOS: [SPRKCNTPY1500] sumApprox -> exact SUM (BoundedFloat/timeout/CI dropped)
total = df.agg(F.coalesce(F.sum("value"), F.lit(0.0))).first()[0]   # coalesce restores sumApprox's 0.0-on-empty
```

### 16.10 `collectWithJobGroup(groupId, description, ...)` — [Partial]

```python
# SCOS: [SPRKCNTPY1500] collectWithJobGroup -> df.collect()
results = df.collect()
# SCOS: TODO - job-group tagging (groupId/description/interruptOnCancel) is dropped;
# job-group tracking/cancellation lives only on the RDD/SparkContext surface (no SCOS equivalent).
```

### 16.11 `saveAsObjectFile(path)` — [Partial]

```python
# Only when the RDD held DataFrame-shaped rows and the round-trip persists within the job:
# SCOS: [SPRKCNTPY1500] saveAsObjectFile -> parquet/table round-trip (DataFrame-shaped rows only)
df.write.mode("overwrite").parquet("/tmp/objdata")   # or .saveAsTable("t")
reloaded = spark.read.parquet("/tmp/objdata")        # or spark.table("t")
# SCOS: TODO - reading pre-existing external object files, or persisting arbitrary
# Python/Java objects, has NO SCOS path (Parquet/table needs a tabular schema).
```

### 16.12 `saveAsPickleFile(path, batchSize)` — [Partial]

```python
# Only when the pickle round-trip merely persists a DataFrame within the job:
# SCOS: [SPRKCNTPY1500] saveAsPickleFile -> parquet/table round-trip
df.write.mode("overwrite").parquet("/path/data")     # or .saveAsTable("MY_TABLE")
loaded = spark.read.parquet("/path/data")            # or spark.table("MY_TABLE")
# SCOS: TODO - reading pre-existing/external pickle files, or persisting non-tabular
# Python objects, has no SCOS path (SCOS registers no pickle reader/writer).
```

### 16.13 `getStorageLevel()` — [Partial]

```python
# SCOS: [SPRKCNTPY1500] rdd.getStorageLevel -> df.storageLevel (hardcoded — do NOT branch on it)
lvl = df.storageLevel   # always StorageLevel(use_disk=True, use_memory=True); never NONE, never the real level
# SCOS: TODO - any logic that branches on the returned StorageLevel is a manual-review item
# (persist() also discards the requested level with only a warning).
```

### 16.14 `toDF(schema, sampleRatio)` — [Native]

```python
# BEFORE: rdd.toDF(["id", "name"])
# AFTER: spark.createDataFrame with an EXPLICIT schema (no sampleRatio inference on SCOS)
# SCOS: [SPRKCNTPY1500] rdd.toDF -> spark.createDataFrame(data, explicit_schema)
schema = StructType([StructField("id", LongType(), True), StructField("name", StringType(), True)])
df = spark.createDataFrame([(1, "a"), (2, "b")], schema)
```
If the source came from upstream RDD transforms, rewrite those to DataFrame ops first; `toDF` is only the final schema-attach step.

### 16.15 `context` (→ a SparkContext diagnostic property) — [Partial]

```python
# BEFORE: sc = rdd.context ; app_id = sc.applicationId
# AFTER: no SparkContext under Connect — the .rdd/.context hop is dropped; only a
# terminal property read survives via a getattr fallback so diagnostic code still runs.
# SCOS: [SPRKCNTPY4002] rdd.context property read -> getattr fallback
app_id = getattr(spark, "applicationId", "scos-unsupported")
# SCOS: TODO - genuine SparkContext capabilities (parallelize/broadcast/accumulators) have no fallback (SPRKCNTPY4000).
```

### 16.16 `toDebugString()` — [Partial]

```python
# BEFORE: print(rdd.toDebugString().decode())   # RDD lineage chain
# AFTER: closest debugging intent is DataFrame.explain (Snowflake's simplified plan, NOT a lineage chain)
# SCOS: [SPRKCNTPY1500] rdd.toDebugString -> df.explain (intent-only substitute)
df.explain()
```

---

## 17. Custom aggregate functions (UDAF) — [Hard gap · native path]

A Spark UDAF (`UserDefinedAggregateFunction`, or `Aggregator` + `functions.udaf`)
has **no supported execution path** on SCOS. **Avoid
`spark.udf().registerJavaUDAF(...)`** — the aggregate flag is ignored and it
registers as a **scalar** UDF (silent footgun). Do this instead, in order:

1. **Built-in aggregate** — most UDAFs reduce to `groupBy().agg(...)` (see §14).
2. **Native Snowflake Java UDAF** — register once (persisted in the Snowflake
   account catalog), then call it through `SnowflakeSession` pass-through (from
   Java/Scala): `Aggregator<IN,BUF,OUT>` maps 1:1 —
   `zero→initialize, reduce→accumulate, merge→merge, finish→finish`. Call via
   `sf.sql(...)`, **not** `expr()`/`callUDF()` (both the function and the table
   live in Snowflake, so Spark can't resolve them). Handlers with dependencies:
   upload a JAR to a stage and use `IMPORTS='@stage/x.jar' HANDLER='pkg.Class'`.
   In Python, a `@udaf` handler class (accumulate/merge/finish, registered via
   `session.udaf.register`) is the analogue.
3. **Keep on Spark** if the logic is genuinely non-SQL (external calls, ML
   inference, or iterative state that isn't initialize/accumulate/merge/finish).

```python
# SCOS: TODO - Spark UDAF has no supported execution path; replace with a built-in
# groupBy().agg (§14), a native Snowflake Java/Python UDAF, or keep on Spark.
# Do NOT use registerJavaUDAF — it silently registers as a scalar UDF.
```

---

## Spark Connect / SCOS specifics

- There is **no `SparkContext`**. Replace `sc.<x>` with the equivalent on
  `spark` (the `SparkSession`) or a DataFrame; an unported `sc.<x>` will raise
  `PySparkNotImplementedError` at runtime.
- **`sc = spark.sparkContext` → DROP the binding; do NOT alias it to `sc = spark`.**
  This is the single most important RDD-migration decision and a common trap. The
  accessor `spark.sparkContext` itself raises under Connect, but the fix is *not*
  to rebind `sc` — because `sc`'s methods map to **different** targets, so no
  single object can stand in for it:

  | RDD-era call | migrates to | (on `SparkSession`?) |
  |---|---|---|
  | `sc.parallelize(data)` | `spark.createDataFrame([(x,) for x in data], ["value"])` | ✗ |
  | `sc.range(n)` | `spark.range(n)` | ✓ |
  | `sc.textFile(p)` | `spark.read.text(p)` | ✗ (on `spark.read`) |
  | `sc.emptyRDD()` | `spark.createDataFrame([], "value: bigint")` | ✗ |
  | `sc.broadcast(v)` | use `v` directly / `F.broadcast(df)` | ✗ |
  | `sc.setLogLevel` / `setCheckpointDir` / `cancelJobGroup` | drop / no-op | ✗ |
  | `sc.accumulator(...)` | no equivalent → comment out + TODO | ✗ |

  `sc = spark` "works" only for the rare op that also exists on `SparkSession`
  (`range`); for `parallelize`/`textFile`/`emptyRDD`/`broadcast` it produces
  `AttributeError: 'SparkSession' object has no attribute 'parallelize'` — and it
  *masks* unconverted calls behind a valid-looking binding. Correct migration:
  **remove the `sc = spark.sparkContext` line and rewrite each `sc.<op>` at its
  call site** (table above). After that, `sc` is referenced nowhere and the file
  is clean. A migration that leaves `sc = spark` **and** live `sc.parallelize(...)`
  calls is the failure mode — neither dropped nor converted.
- `df.rdd` is unavailable — never route through it. Operations like
  `df.rdd.isEmpty()` / `df.rdd.toLocalIterator()` have direct DataFrame methods
  (`df.isEmpty()`, `df.toLocalIterator()`).
- `repartition` / `coalesce` are **accepted** on DataFrames and are **not** pure
  no-ops; do not remove them or label them "no-op".
- `checkpoint()` / `localCheckpoint()` are not supported by the Connect client —
  use `df.cache()`.
- Prefer `spark.createDataFrame(data, schema)` with an **explicit schema** when
  replacing `parallelize` / `emptyRDD`; schema inference over Python literals is
  fragile and sometimes unsupported.
- **`createDataFrame` from a flat list of scalars fails** — `createDataFrame`
  needs rows, not bare values. Wrap each scalar in a 1-tuple and name the column:

  ```python
  # WRONG (raises): sc.parallelize([1, 2, 3]) -> spark.createDataFrame([1, 2, 3])
  # RIGHT:
  spark.createDataFrame([(x,) for x in [1, 2, 3]], ["value"])
  # sc.parallelize(range(10)) -> spark.createDataFrame([(x,) for x in range(10)], ["value"])
  # sc.emptyRDD()             -> spark.createDataFrame([], "value: bigint")
  ```

### Convert the WHOLE chain, never just the entry point

`sc.parallelize(...)` / `sc.range(...)` / `sc.textFile(...)` produce an **RDD**;
after you swap the entry point to a DataFrame source, **every downstream method
in the chain must also be converted** to its DataFrame form (or TODO'd if it has
no equivalent). A DataFrame has no `.map` / `.sum` / `.zip` / `.cartesian` /
`.histogram` / `.aggregate` / `.reduce` / `.glom` — leaving any of them on the
rewritten value is a broken migration, **not** a fix. Convert the expression as a
unit:

```python
# BEFORE: sc.parallelize([1, 2, 3]).sum()
# AFTER:
spark.createDataFrame([(x,) for x in [1, 2, 3]], ["value"]).agg(F.sum("value")).collect()[0][0]

# BEFORE: rdd = sc.parallelize(seq); rdd.sortByKey().collect()   # seq = [(k, v), ...]
# AFTER:
df = spark.createDataFrame(seq, ["key", "value"])
[(r["key"], r["value"]) for r in df.orderBy("key").collect()]
```

### SparkContext entry points & config (`getOrCreate` / `getConf` / `hadoopConfiguration`)

```python
# BEFORE: explicit SparkContext bootstrap (RuntimeError on SCOS)
# from pyspark import SparkContext
# sc = SparkContext.getOrCreate()
# data = sc.parallelize([1, 2, 3])
# AFTER: there is no SparkContext — use the existing SparkSession `spark`:
data = spark.createDataFrame([(i,) for i in [1, 2, 3]], ["value"])

# BEFORE: reading a conf via SparkContext
# n = sc.getConf().get("spark.sql.shuffle.partitions")
# AFTER:
n = spark.conf.get("spark.sql.shuffle.partitions")

# BEFORE: storage auth via Hadoop conf
# sc.hadoopConfiguration.set("fs.s3a.access.key", "AKIA...")
# AFTER: drop it — Snowflake authenticates cloud storage via a storage
# integration / external stage, not Hadoop conf. (SPRKCNTPY3202)
```

> `getConf()` / `setLogLevel()` map to (or drop against) the `spark`
> session — they do not need a `SparkContext`. `hadoopConfiguration` cloud
> credentials have no SCOS analogue: re-point the read at a Snowflake stage.

### SparkContext EWI codes: `4000` vs `4002`

`SPRKCNTPY4000` = an unsupported SparkContext **element/method call** that must be
migrated (`sc.parallelize`, `sc.broadcast`, `sc.accumulator`, `sc.setLogLevel`, …).
`SPRKCNTPY4002` = a SparkContext **property read** that was replaced with a static
fallback so diagnostic code keeps running (handled deterministically by the
`sparkcontext_property_fallback_rewrite` recipe). Example:

```python
# BEFORE: app_id = spark.sparkContext.applicationId
# AFTER  (property read → getattr fallback; .sparkContext hop dropped):
# SCOS: [SPRKCNTPY4002] sparkContext property read replaced with a getattr fallback
app_id = getattr(spark, "applicationId", "scos-unsupported")
```

A SparkContext **method call** cannot use the getattr fallback (it would
`TypeError` when invoked) — those stay `SPRKCNTPY4000` and are migrated to the
SparkSession/Snowpark Connect surface (e.g. `parallelize` → `createDataFrame`).
