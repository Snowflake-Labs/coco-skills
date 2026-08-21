# RDD → DataFrame Conversion Rules (Java / Snowpark Connect)

Referenced by `references/fix-rules.md` and `agents/fixer.md`.

The Java Spark API (`JavaRDD`, `JavaSparkContext`, `JavaPairRDD`) has no equivalent
in Spark Connect. The Connect client has no `SparkContext`, no executors, and no RDD layer.
`Dataset<Row>` / `Dataset<T>` replace all RDD usage.

## Bucket A — UNSUPPORTED (annotate `// SCOS: [SPRKCNTSCL1500]`, preserve, manual)

Triggers (no DataFrame equivalent):

- `.toJavaRDD()`, `.rdd()`, `javaRDD()` — RDD layer does not exist
- `JavaSparkContext` construction: `new JavaSparkContext(conf)`, `new JavaSparkContext(sc)`
- `jsc.textFile(...)`, `jsc.wholeTextFiles(...)`, `jsc.hadoopFile(...)`, `jsc.sequenceFile(...)`
- `jsc.parallelize(list)` with `mapToPair`, `reduceByKey`, `groupByKey` closures
- `JavaRDD.map(f)`, `flatMap(f)`, `filter(f)`, `mapPartitions(f)` — closure-bearing ops
- `JavaPairRDD.mapValues(f)`, `flatMapValues(f)` — closure on pair RDD
- `JavaRDD.foreach(f)`, `foreachPartition(f)` — side-effecting closures
- `jsc.accumulator(...)` — no driver-side accumulator
- `rdd.saveAsSequenceFile(path)`, `rdd.saveAsObjectFile(path)`
- `JavaRDD.getNumPartitions()`, `JavaRDD.partitions()`

**Action:** preserve the original expression and prepend:
```java
// SCOS: [SPRKCNTSCL1500] RDD API '.toJavaRDD()' is not supported in
// Snowpark Connect; manual refactor required (no RDD layer on the client).
```

---

## Bucket B — DROP-THE-HOP: `df.toJavaRDD().*` shortcuts

Drop the `.toJavaRDD()` accessor and call the same method on `Dataset<Row>` directly:

| Java RDD form | Dataset<Row> equivalent |
|---|---|
| `df.toJavaRDD().count()` | `df.count()` |
| `df.toJavaRDD().collect()` | `df.collectAsList()` |
| `df.toJavaRDD().first()` | `df.first()` |
| `df.toJavaRDD().take(n)` | `df.takeAsList(n)` |
| `df.toJavaRDD().cache()` | `df.cache()` |
| `df.toJavaRDD().unpersist()` | `df.unpersist()` |
| `df.toJavaRDD().repartition(n)` | `df.repartition(n)` |
| `df.toJavaRDD().coalesce(n)` | `df.coalesce(n)` |
| `df1.toJavaRDD().union(df2.toJavaRDD())` | `df1.union(df2)` |
| `df.toJavaRDD().distinct()` | `df.distinct()` |

---

## Bucket C — CONVERTIBLE: `createDataFrame` (`jsc.parallelize`)

Java `jsc.parallelize(List<Row>)` → `spark.createDataFrame(list, schema)`:

```java
// BEFORE:
List<Row> rows = Arrays.asList(RowFactory.create("a", 1), RowFactory.create("b", 2));
JavaRDD<Row> rdd = jsc.parallelize(rows);
Dataset<Row> df = spark.createDataFrame(rdd, schema);

// AFTER:
List<Row> rows = Arrays.asList(RowFactory.create("a", 1), RowFactory.create("b", 2));
Dataset<Row> df = spark.createDataFrame(rows, schema);
```

**Java Encoder form** for typed datasets:
```java
// BEFORE:
JavaRDD<MyBean> beanRdd = jsc.parallelize(Arrays.asList(new MyBean("a", 1)));
Dataset<MyBean> ds = spark.createDataFrame(beanRdd, MyBean.class);

// AFTER:
List<MyBean> beans = Arrays.asList(new MyBean("a", 1));
Dataset<MyBean> ds = spark.createDataset(beans, Encoders.bean(MyBean.class));
```

---

## Bucket C — CONVERTIBLE: `JavaPairRDD` key-based operations

> **Column naming**: When converting PairRDD operations, verify the DataFrame
> column names before applying these patterns. Only pipeline-generated
> `JavaPairRDD`s from `mapToPair` on an existing `Dataset<Row>` reliably use
> `_1`/`_2` or explicit `key`/`value` names. If the original schema had
> different column names, resolve them from the surrounding source context.

| JavaPairRDD op | Dataset<Row> equivalent |
|---|---|
| `rdd.reduceByKey((a,b) -> a+b)` | `df.groupBy("key").agg(functions.sum("value"))` |
| `rdd.groupByKey()` | `df.groupBy("key").agg(functions.collect_list("value"))` ¹ |
| `rdd.countByKey()` | `df.groupBy("key").count()` |
| `rdd.sortByKey()` | `df.orderBy(functions.col("key"))` |
| `rdd.join(rdd2)` | `df.join(df2, "key")` |
| `rdd.leftOuterJoin(rdd2)` | `df.join(df2, df.col("key").equalTo(df2.col("key")), "left")` |
| `rdd.subtractByKey(rdd2)` | `df.join(df2, "key", "left_anti")` |
| `rdd.keys()` | `df.select("key")` |
| `rdd.values()` | `df.select("value")` |

> ¹ `groupByKey()` returns `JavaPairRDD<K, Iterable<V>>` — use
> `functions.collect_list("value")` to preserve the iterable semantics. If the
> downstream code iterates values for a fold/reduce, use `agg(sum/max/min/...)`
> with the appropriate aggregation instead.

---

## Bucket C — CONVERTIBLE: `sc.broadcast`

```java
// BEFORE: Broadcast<Map<String,Integer>> bc = jsc.broadcast(lookupMap);
// AFTER: capture the map directly in lambda closures
final Map<String, Integer> lookup = lookupMap;
```

For broadcast join hints:
```java
// BEFORE: df1.join(functions.broadcast(df2), "key")
// AFTER:
df1.join(df2.hint("broadcast"), df1.col("key").equalTo(df2.col("key")));
```

---

## Decision summary

| Pattern | Bucket | Action |
|---|---|---|
| `JavaRDD.map(f)` / `flatMap(f)` / `filter(f)` (closure) | A | annotate + preserve |
| `JavaSparkContext.*` file ingestion | A | annotate + preserve |
| `jsc.accumulator(...)` | A | annotate + preserve |
| `df.toJavaRDD().count()` / `collect()` / `first()` / `take(n)` | B | drop `.toJavaRDD()` |
| `df.toJavaRDD().cache()` / `unpersist()` / `repartition(n)` | B | drop `.toJavaRDD()` |
| `jsc.parallelize(List<Row>)` | C | `spark.createDataFrame(list, schema)` |
| `jsc.parallelize(List<Bean>)` | C | `spark.createDataset(list, Encoders.bean(...))` |
| `reduceByKey` / `groupByKey` / `countByKey` | C | `groupBy().agg(...)` |
| `rdd.sortByKey()` | C | `df.orderBy(col("key"))` |
| `rdd.join(rdd2)` / join variants | C | `df.join(df2, ...)` |
| `jsc.broadcast(v)` | C | capture `v` in closure directly |

---

## Verdict routing (how to apply the recipes below)

Every recipe below carries a **verdict tag** from the migration guide. The fixer routes on it:

| Verdict | Meaning | Action |
|---|---|---|
| **[Native]** | first-class `Dataset<Row>` equivalent | apply the rewrite; no caveat needed |
| **[Workaround]** | reproduces the intent with a rewrite | apply the rewrite |
| **[Silent-diff]** | rewrite exists but semantics drift silently (no error) | apply the rewrite **and** add a `// SCOS:` guard/note calling out the drift |
| **[Partial]** | intent only — SCOS cannot reproduce some aspect | apply the closest form **and** `// SCOS: TODO` for the lost aspect |
| **[Hard gap]** | no equivalent (⚠perm = permanent, ⚠ns = not currently supported) | `// SCOS: TODO`; for ⚠perm, delete-don't-migrate or keep on Spark |

Tag every applied rewrite: JavaRDD→Dataset rewrites use
`// SCOS: [SPRKCNTSCL1500] <what changed>`; `JavaSparkContext` primitives
(`parallelize`, `broadcast`, `accumulator`, …) use `// SCOS: [SPRKCNTSCL1500]`;
generic unsupported items (no-op config, custom UDAF, `observe`) use
`// SCOS: [SPRKCNTSCL1000]`; hard gaps use
`// SCOS: TODO - <why + Snowflake-native alternative>`.

---

## Accumulators → DataFrame aggregation

**This is the #1 correction: an accumulator is NOT a blanket TODO.** A Spark
driver-side accumulator — `jsc.sc().longAccumulator()` / `.doubleAccumulator()` /
`.collectionAccumulator()`, their types `LongAccumulator` / `DoubleAccumulator` /
`CollectionAccumulator`, or a custom `AccumulatorV2` — incremented inside
`forEach`/`map`/a UDF closure is a *reduction*. A reduction is exactly what
`df.agg(...)` / `df.groupBy(...).agg(...)` do in one SQL pass. Rewrite the
accumulator as the equivalent aggregate. Only the four uses in "True hard gaps"
have no equivalent.

> Java's deprecated `jsc.accumulator(v)` is Bucket A (annotate + preserve) only
> when it drives a mutable side-effect. The **driver-side reduction** form is
> always convertible (see recipes below). `df.observe(...)` metrics are a
> **no-op** on SCOS (`// SCOS: [SPRKCNTSCL1000]`) — compute each metric with an
> explicit `df.agg(...)` instead. Any `jsc.sc()`/`spark.sparkContext()` hop has
> no `SparkContext` under Connect and raises `SPRKCNTSCL1500` (§1).

### Count rows meeting a condition — [Workaround]

```java
// BEFORE (Spark): LongAccumulator incremented in forEach on match
// LongAccumulator negCount = jsc.sc().longAccumulator();
// df.toJavaRDD().foreach(r -> { if (r.getDouble(0) < 0) negCount.add(1); });
// System.out.println(negCount.value());
// AFTER:
// SCOS: [SPRKCNTSCL1500] longAccumulator+forEach row-count -> conditional aggregate
long negCount = (Long) df.agg(
    functions.count(functions.when(functions.col("amount").$less(0), 1)).alias("n")
).first().get(0);
```

### Sum / min / max / avg a column — [Native]

```java
// BEFORE (Spark): custom AccumulatorV2 / DoubleAccumulator reducing a column
// AFTER:
// SCOS: [SPRKCNTSCL1500] AccumulatorV2 reduction -> Dataset aggregate
Row r = df.agg(
    functions.min("amount").alias("min_amount"),
    functions.max("amount").alias("max_amount"),
    functions.sum("amount").alias("total"),
    functions.avg("amount").alias("avg_amount"),
    functions.count("amount").alias("non_null_count")
).first();
```

> **Silent-diff:** SQL `MIN/MAX/SUM/AVG` skip NULLs; if Spark treated NULL as a
> sentinel wrap with `functions.coalesce(functions.col("amount"), functions.lit(0))`.
> `functions.sum(...)` on empty input returns `null`, not 0 — guard with
> `functions.coalesce(functions.sum(...), functions.lit(0L))`.
> `count("amount")` skips NULLs; use `count(functions.lit(1))` to match RDD
> `values.size()`. NULL numeric aggregates come back as boxed `null` — null-check
> before unboxing.

### Collect a bounded set of IDs (`collectionAccumulator`) — [Workaround, ⚠perm at scale]

```java
// BEFORE (Spark): CollectionAccumulator<String> gathering unique IDs via forEach
// CollectionAccumulator<String> seenIds = jsc.sc().collectionAccumulator();
// AFTER -- case A: unique IDs, small cardinality:
// SCOS: [SPRKCNTSCL1500] collectionAccumulator -> collect_set
List<String> uniqueIds = df.agg(
    functions.collect_set("user_id").alias("ids")
).first().getList(0);
// case B: Map of counts (userId -> rowCount):
Map<String, Long> idToCount = new HashMap<>();
for (Row row : df.groupBy("user_id").agg(functions.count("*").alias("n")).collectAsList()) {
    idToCount.put(row.getString(0), row.getLong(1));
}
```

> **Silent-diff:** `collect_set`/`collect_list` silently **drop NULLs** and are
> **unordered**. Near the 128 MB per-value cap prefer
> `df.select("user_id").distinct().collectAsList()` (a row list, not one huge
> ARRAY column) — never assemble one giant server-side collection.

### Count matches / non-matches in a join — [Native]

```java
// BEFORE (Spark): join, then accumulators count matched vs unmatched rows
// AFTER:
Dataset<Row> joined = orders.join(customers,
    orders.col("customer_id").equalTo(customers.col("customer_id")), "left");
// SCOS: [SPRKCNTSCL1500] join match/unmatched accumulators -> conditional aggregates
Row counts = joined.agg(
    functions.count(functions.lit(1)).alias("total"),
    functions.count("customer_name").alias("matched"),   // count(col) skips NULLs
    functions.count(functions.when(functions.col("customer_name").isNull(),
        functions.lit(1))).alias("unmatched")
).first();
```

### Per-key operation / collision counters — [Workaround, ⚠perm]

```java
// BEFORE (Spark): reduceByKey with a LongAccumulator counting merges
// AFTER:
Dataset<Row> totals = df.groupBy("category")
    .agg(functions.sum("amount").alias("total"));
// per-pair merge counts do NOT carry over (SCOS runs one SQL agg, no per-pair merge).
// collision count == rows - distinct keys, computed directly:
// SCOS: [SPRKCNTSCL1500] merge-collision accumulator -> rows minus distinct keys
long collisionCount = df.count() - df.select("category").distinct().count();
```

### Accumulator inside a UDF — [Workaround]

```java
// BEFORE (Spark): a UDF closure increments a LongAccumulator to count error rows
// AFTER: emit an error-flag column, then aggregate it
UserDefinedFunction flag = functions.udf(
    (Double x) -> (x == null || x < 0) ? 1 : 0, DataTypes.IntegerType);
Dataset<Row> res = df.withColumn("errflag", flag.apply(functions.col("amount")));
// SCOS: [SPRKCNTSCL1500] in-UDF error accumulator -> flag column + sum
long errorCount = (Long) res.agg(functions.sum("errflag").alias("errors")).first().get(0);
```

> A `throw` inside a UDF closure **does** propagate to the driver, but a
> closure-side **counter/flag never aggregates** — SCOS runs one SQL query with
> no driver-visible shared mutable state per row. Keep abort logic on the driver:
> `if (df.filter(functions.col("amount").$less(0)).count() > 0) throw ...`.

### Approximate aggregates (histogram / quantile / top-K) — [Native]

```java
// BEFORE (Spark): a custom AccumulatorV2 building a histogram / quantile / top-K sketch
// AFTER:
// SCOS: [SPRKCNTSCL1500] AccumulatorV2 sketch -> APPROX_PERCENTILE / exact groupBy
Row result = df.agg(
    functions.expr("APPROX_PERCENTILE(latency_ms, 0.50)").alias("p50"),
    functions.expr("APPROX_PERCENTILE(latency_ms, 0.99)").alias("p99")
).first();
// top-K by frequency (APPROX_TOP_K raises "Unsupported function name"):
List<Row> top10 = df.groupBy("status").count()
    .orderBy(functions.desc("count")).limit(10).collectAsList();
```

> On SCOS `APPROX_PERCENTILE` maps to Snowflake `PERCENTILE_DISC` — an **exact**
> dataset value, not a t-Digest approximation. **Validate custom-sketch mappings
> against your own Spark baseline** before relying on them.

---

## True hard gaps

**Three** of these are **permanent** hard blockers (⚠perm) with no `Dataset<Row>`
equivalent; the **fourth** is not currently supported (⚠ns) with a documented
workaround. Do not improvise a rewrite for the ⚠perm cases — delete-don't-migrate
or keep the job on Spark.

### `foreachPartition` external side-effects — [Hard gap] [⚠perm]

One long-lived Kafka producer / HTTP client / file handle per partition.
`df.toJavaRDD().foreachPartition(...)` has no server-side execution path — SCOS
runs your Dataset as SQL on a warehouse, so there is no per-partition JVM
process. **Write from the driver instead:**

```java
// SCOS: TODO - foreachPartition per-partition sinks are unsupported (⚠perm, architectural).
// Small/medium volumes — pull rows back and use ONE client-side producer:
KafkaProducer<String, String> producer = new KafkaProducer<>(props);
for (Row r : df.collectAsList()) {
    producer.send(new ProducerRecord<>("events", r.getString(0)));
}
producer.flush(); producer.close();
// Large volumes — land rows in a table and drive egress Snowflake-side:
// df.write().mode(SaveMode.Append).saveAsTable("events_out");
```

### Cache-hit detection — [Hard gap] [⚠perm]

A `LongAccumulator` counting cache hits vs recomputations across
`persist()`/`unpersist()`. Snowflake's result cache is transparent — **no
client-visible hit/miss signal. Delete the counter.**

```java
// SCOS: TODO - cache-hit accumulator has no equivalent (⚠perm): Snowflake's result
// cache is transparent. Delete the counter. For perf debugging query
// TABLE(INFORMATION_SCHEMA.QUERY_HISTORY()) or SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
// (bytes_scanned ~0 indicates a cache hit). If the counter gated a code path, redesign.
```

### Mid-job live progress snapshots — [Hard gap] [⚠perm]

A background thread polling `acc.value()` on a timer. `count()`/`collectAsList()`
blocks the calling thread for the whole Snowflake query and `acc.value()` reads 0
throughout (no warehouse→driver callback).

```java
// SCOS: TODO - live mid-job accumulator progress has no equivalent (⚠perm).
// Post-hoc snapshots — split the job into chunks and record after each:
long total = 0; List<Long> snapshots = new ArrayList<>();
for (int lo = 0; lo < 100; lo += 10) {
    total += df.filter(functions.col("bucket").$greater$eq(lo)
                .and(functions.col("bucket").$less(lo + 10))).count();
    snapshots.add(total);
}
// Real-time UIs: from a SEPARATE session poll
// SELECT * FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION())
```

### `writeStream().foreachBatch` cross-batch state — [Partial] [⚠ns]

Running state across streaming micro-batches. `foreachBatch` is **not currently
supported** (the callback never executes). Workaround: a manual batch loop over
static DataFrames with cross-batch state in a Snowflake table. **Preserves state
but loses trigger semantics.** For true streaming triggers keep the job on Spark.

```java
// SCOS: TODO - writeStream().foreachBatch cross-batch state is not currently supported (⚠ns).
// BOUNDED backfill/reprocessing only — manual batch loop:
spark.sql("DROP TABLE IF EXISTS processed_events");   // reset state ONCE before the loop
for (Dataset<Row> batchDf : yourBatchSource()) {      // YOUR iterator of static Datasets
    batchDf.write().mode(SaveMode.Append).saveAsTable("processed_events");
    long running = (Long) spark.table("processed_events")
        .agg(functions.sum("amount")).first().get(0);
}
// Always re-run from the top (DROP resets state); do NOT resume mid-loop or rows double-count.
```

---

## Aggregate & reduce recipes

All RDD aggregate ops have a `Dataset<Row>` equivalent. The recurring traps are
in "Silent differences & platform limits" below.

### `aggregate(zeroValue, seqOp, combOp)` — [Workaround]

```java
// BEFORE: rdd.aggregate(tuple(0,0), (a,x)->(a._1+x,a._2+1), (a,b)->(a._1+b._1,a._2+b._2))
// AFTER (e.g. an average):
// SCOS: [SPRKCNTSCL1500] rdd.aggregate -> df.agg (read multi-stat off one Row)
Row row = df.agg(
    functions.coalesce(functions.sum("value"), functions.lit(0L)).alias("s"),
    functions.count(functions.lit(1)).alias("c")
).first();
double avg = row.getLong(1) != 0 ? (double) row.getLong(0) / row.getLong(1) : 0.0;
```

> **Watch:** a non-identity `zeroValue` must be applied **once** — `sum(...) + seed`
> (§4①); identity zeros (`0`/`1`/`""`) need no adjustment.

### `treeAggregate(zeroValue, seqOp, combOp, depth)` — [Workaround]

Same result as `aggregate`; **`depth` has no SCOS analogue — drop it** (Snowflake
plans its own aggregation tree).

```java
// BEFORE: rdd.treeAggregate(tuple(0,0.0,0.0), seqOp, combOp)  // mean/variance
// AFTER:
// SCOS: [SPRKCNTSCL1500] rdd.treeAggregate -> df.agg (drop depth)
Row stats = df.agg(functions.avg("v").alias("mean"), functions.var_pop("v").alias("var")).first();
```

### `aggregateByKey(zeroValue, seqFunc, combFunc)` — [Native]

```java
// SCOS: [SPRKCNTSCL1500] rdd.aggregateByKey -> df.groupBy().agg()
Dataset<Row> result = df.groupBy("k").agg(
    functions.sum(functions.coalesce(functions.col("v"), functions.lit(0))).alias("s"),
    functions.count(functions.lit(1)).alias("c")
);
```

### `treeReduce(f, depth)` — [Workaround]

Degenerate `treeAggregate` (no seed); **raises on empty**; `depth` N/A.

```java
// BEFORE: rdd.treeReduce((a,b) -> a+b)  // 15
// AFTER:
// SCOS: [SPRKCNTSCL1500] rdd.treeReduce -> df.agg (drop depth)
long total = (Long) df.agg(functions.coalesce(functions.sum("v"), functions.lit(0L))).first().get(0);
```

### `reduce(f)` — [Native]

```java
// BEFORE: rdd.reduce((a,b) -> a+b)  // 15; raises on empty
// AFTER:
// SCOS: [SPRKCNTSCL1500] rdd.reduce -> df.agg
long total = (Long) df.agg(functions.coalesce(functions.sum("v"), functions.lit(0L))).first().get(0);
```

### `fold(zeroValue, op)` / `foldByKey(zeroValue, func)` — [Workaround] — EXPECTED_DIFF for a non-identity seed

Like `reduce` but seeded; does not raise on empty. RDD applies a non-identity
seed `partitions+1` times (partition-count-dependent); reproduce the **intent**
by applying the seed once.

```java
// BEFORE: rdd.fold(0, (a,b)->a+b)  // 15; rdd.fold(7, (a,b)->a+b)  // partition-scaled!
// AFTER (identity seed):
// SCOS: [SPRKCNTSCL1500] rdd.fold -> df.agg
Object raw = df.agg(functions.sum("v")).first().get(0);
long val = raw != null ? (Long) raw : 0L;   // identity seed -> 15
// Non-identity seed (apply once):
long valSeeded = (raw != null ? (Long) raw : 0L) + 7;   // -> 22, NOT Spark's partition-scaled value
// per-key:
df.groupBy("k").agg(functions.coalesce(functions.sum("v"), functions.lit(0L)));  // foldByKey(0)
```

### `combineByKey(createCombiner, mergeValue, mergeCombiners)` — [Native]

```java
// BEFORE: rdd.combineByKey(v->(v,1), (a,v)->(a._1+v,a._2+1), ...).mapValues(x->x._1/(double)x._2)
// AFTER (per-key average):
// SCOS: [SPRKCNTSCL1500] rdd.combineByKey -> df.groupBy().agg()
Dataset<Row> result = df.groupBy("k").agg(functions.avg("v"));
```

### `groupByKey()` (JavaPairRDD) — [Workaround] [⚠perm at scale]

```java
// BEFORE: rdd.groupByKey()  // JavaPairRDD<K, Iterable<V>>
// AFTER:
// SCOS: [SPRKCNTSCL1500] rdd.groupByKey -> groupBy().collect_list()
Dataset<Row> grouped = df.groupBy("k").agg(functions.collect_list("v").alias("values"));
```

> **⚠perm at scale:** `collect_list` per group is bounded by the 128 MB
> ARRAY/VARIANT ceiling. At high cardinality prefer `agg(sum/max/min/count)`
> or count-only → `approx_count_distinct`.

---

## §10 verified ops

These RDD ops have verified `Dataset<Row>` workarounds. Unambiguous RDD method
names are auto-detected by the analyzer; the `Dataset` homonym `groupBy(` usually
means a `.toJavaRDD()` hop that should not exist — confirm the receiver before
flagging.

| JavaRDD op | Dataset<Row> equivalent | Verdict |
|---|---|---|
| `rdd.groupBy(f)` | `df.groupBy(...).agg(functions.collect_list(...))` — drops NULLs & unordered | [Workaround] |
| `rdd.mapPartitionsWithIndex(f)` | `df.mapPartitions(...)` runs per-partition but **split index is dropped** (SCOS partitions are internal/random) — redesign any index-driven logic | [Partial] |
| `rdd.partitionBy(p)` | **No physical key partitioner** — express intent as `df.groupBy("key").agg(...)` or table `CLUSTER BY (key)` for scan pruning; `numPartitions`/`Partitioner` layout is lost | [Partial] |
| `rdd.repartitionAndSortWithinPartitions(p, cmp)` | `df.repartition(n, col).sortWithinPartitions(...)` expresses intent, but `repartition(n, col)` is a no-op hint and `sortWithinPartitions` degenerates to a global `ORDER BY`; for durable co-location use table `CLUSTER BY` | [Partial] |
| `rdd.collectAsMap()` | `Map<K,V>` from `df.select("k","v").collectAsList()` — last-wins is order-dependent; use `row_number` window for a defined winner; bounded by driver memory | [Workaround] |
| `rdd.countApprox(timeout, confidence)` | `df.count()` — SCOS has no time-bounded/confidence approximate count; timeout & CI dropped, exact `long` returned | [Workaround] |
| `rdd.countApproxDistinct(relativeSD)` | `df.agg(functions.approx_count_distinct(col))` — Native HyperLogLog; `relativeSD` not tunable (dropped); ignores NULLs | [Native] |
| `rdd.saveAsObjectFile(path)` | Only when the object round-trip merely persists a Dataset: `df.write().parquet(path)` / `.saveAsTable(t)` then `spark.read().parquet` / `spark.table`. Reading pre-existing Java-serialized object files → TODO | [Partial] |
| `rdd.saveAsSequenceFile(path)` | **No equivalent** — Java-serialized/Hadoop sink | [Hard gap] |
| `rdd.getStorageLevel()` | `df.storageLevel()` — SCOS returns a **hardcoded** `StorageLevel(useDisk=true, useMemory=true)`; never reports `NONE` or the real level — **never branch on it** | [Partial] |
| `rdd.context()` | No `SparkContext` under Connect — the `.toJavaRDD()`/`.context()` hop is dropped; only terminal property reads survive via a reflection fallback (intent-only). Genuine `JavaSparkContext` capabilities → human TODO | [Partial] |
| `rdd.toDebugString()` | `df.explain()` — closest debugging intent, but emits Snowflake's simplified plan, **not** the RDD lineage chain | [Partial] |

---

## Custom aggregate functions (UDAF path)

A Spark UDAF (`UserDefinedAggregateFunction`, or `Aggregator<IN,BUF,OUT>`
submitted via `functions.udaf(...)`) has no supported SCOS execution path:

1. **Reduce to a built-in `groupBy().agg`** — most UDAFs are sum/min/max/count/avg
   variants and this always works first.
2. **A native Snowflake Java UDAF** — register once in the account catalog, call
   via `SnowflakeSession` SQL pass-through.
3. **Keep on Spark** — genuinely non-SQL logic with no SQL form.

**Never** rely on `spark.udf().registerJavaUDAF(name, cls)` — it does **not**
raise; the aggregate flag is silently dropped and the class registers as a
**scalar** UDF, giving wrong results on grouped data with no error.

```java
// SCOS: [SPRKCNTSCL1000] registerJavaUDAF silently registers a scalar UDF — wrong on groups.
// Replace: (1) built-in groupBy().agg(), or (2) native Snowflake Java UDAF, or (3) keep on Spark.
```
