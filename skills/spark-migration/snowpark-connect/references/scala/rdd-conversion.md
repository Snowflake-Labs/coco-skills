# RDD → DataFrame Conversion Rules (Scala / Snowpark Connect)

Referenced by the `migrate-spark-scala-to-snowpark-connect` skill
(`references/fix-rules.md` Rule 2 and `agents/fixer.md`).

> **Deterministic tier.** The Phase 0.5 Scalafix rules `ScosRddImportAnnotate`
> (annotates `import org.apache.spark.rdd._`), `ScosRddExclusiveMethodAnnotate`
> (annotates exclusive RDD methods like `.glom`/`.pipe`/`.mapPartitions`), and
> `ScosRddPersistToCache` (rewrites `rdd.persist`/`cache` to DataFrame
> equivalents) handle the deterministic subset automatically — see
> `references/scala/recipes.md` for the full rule list and their emitted
> markers. The fixer must not undo these `recipe_edits`; it layers the LLM
> fixes on top of them.

## Quick Reference

| RDD pattern | Bucket | DataFrame equivalent |
| --- | --- | --- |
| `.rdd.map(f)` / `.flatMap(f)` / `.filter(f)` / `.foreach(f)` (closure) | **A** | annotate `[SPRKCNTSCL1500]` + preserve |
| `.rdd.mapPartitions(f)` / `foreachPartition` / `glom` / `pipe` | **A** | annotate `[SPRKCNTSCL1500]` + preserve |
| `.rdd.getNumPartitions` / `.rdd.partitions` | **A** | annotate `[SPRKCNTSCL1500]` + preserve |
| `sc.textFile` / `hadoopFile` / `sequenceFile` / `new SparkContext` | **A** | annotate `[SPRKCNTSCL1500]` + preserve |
| `.rdd.stats()` / `.rdd.histogram(buckets)` | **A** | annotate `[SPRKCNTSCL1500]` + preserve |
| `.longAccumulator` / `.doubleAccumulator` / `.collectionAccumulator` / `AccumulatorV2` | **C** | driver-side count/sum/min/max/collect → `df.agg(...)` (see "Accumulators → DataFrame aggregation"). **NOT** a blanket TODO |
| `treeAggregate(z)(seq, comb, depth)` / `treeReduce(f, depth)` | **C** | `df.agg(...)` — drop `depth` (see "Aggregate & reduce recipes") |
| `rdd.collectAsMap()` | **C** | dict/`Map` from `df.select(k,v).collect()` (§10.5) |
| `countApprox` / `countApproxDistinct` / `meanApprox` / `sumApprox` | **C** | exact `df.count()` / `approx_count_distinct` / `avg` / `sum` (§10.6–10.9) |
| `repartitionAndSortWithinPartitions(...)` | **C (Partial)** | `df.repartition(n, col).sortWithinPartitions(...)` — intent only (§10.4) |
| `rdd.saveAsObjectFile(path)` | **C (Partial)** | parquet/table round-trip for DataFrame-shaped rows (§10.11) |
| `rdd.toDebugString` | **C (Partial)** | `df.explain()` — simplified plan, not RDD lineage (§10.16) |
| `df.rdd.count()` / `collect()` / `first()` / `take(n)` / `isEmpty()` | **B** | `df.count()` etc. — drop `.rdd` |
| `df.rdd.cache()` / `persist()` / `unpersist()` | **B** | `df.cache()` etc. — drop `.rdd` |
| `df1.rdd.union(df2.rdd)` / `distinct()` / `intersection` / `subtract` | **B** | `df1.union(df2)` etc. — drop `.rdd` |
| `df.rdd.repartition(n)` / `coalesce(n)` | **B** | `df.repartition(n)` / `df.coalesce(n)` — drop `.rdd` |
| `sc.parallelize(Seq[tuple/case class])` | **C** | `spark.createDataFrame(seq).toDF(names…)` |
| `sc.parallelize(Seq[primitive])` | **C** | `spark.createDataFrame(seq.map(Tuple1.apply)).toDF("value")` |
| `sc.parallelize(Seq[Row], schema)` / `emptyRDD[Row]` | **C** | `spark.createDataFrame(seq.asJava, schema)` |
| `reduceByKey` / `reduceByKeyLocally` / `groupByKey` / `countByKey` / `aggregateByKey` / `foldByKey` / `combineByKey` | **C** | `groupBy(key).agg(...)` |
| `rdd.sortByKey()` | **C** | `df.orderBy(col("key"))` |
| `rdd1.join(rdd2)` / `leftOuterJoin` / `rightOuterJoin` / `fullOuterJoin` | **C** | `df1.join(df2, Seq("key"), "inner/left/right/outer")` |
| `rdd1.subtractByKey(rdd2)` | **C** | `df1.join(df2, Seq("key"), "left_anti")` |
| `rdd.mapValues(f)` / `flatMapValues(f)` | **C** | `df.withColumn(...)` / `df.withColumn(...).select(explode(...))` |
| `rdd.saveAsTextFile(path)` | **C** | `df.write.mode("overwrite").text(path)` |
| `sc.broadcast(v)` scalar | **C** | use `v` directly |
| `sc.broadcast(df)` join hint | **C** | `df.hint("broadcast")` |

---

## Why RDD is unsupported

Snowpark Connect (Spark Connect) is a thin **declarative client**: it builds an
unresolved logical plan and ships it to the server for execution. There is **no
`SparkContext`, no executors, and no RDD layer on the client**, and the backend
does not run arbitrary JVM closures. The Connect `Dataset`/`DataFrame` class
literally has **no `.rdd` member** (`scalac` reports `value rdd is not a member of
org.apache.spark.sql.DataFrame`).

So every RDD usage falls into one of three buckets:

- **Bucket A — unsupported.** No DataFrame equivalent → annotate and refactor
  manually. **Never fabricate a shim.**
- **Bucket B — drop-the-hop.** `df.rdd.METHOD()` where the same METHOD exists on
  DataFrame directly (no closure needed) → drop the `.rdd` accessor and call the
  method on the DataFrame.
- **Bucket C — convertible.** Has a supported DataFrame form that requires a
  rewrite (e.g. `sc.parallelize` → `createDataFrame`, pair ops → `groupBy.agg`).

---

## Bucket A — UNSUPPORTED (annotate `// SCOS: [SPRKCNTSCL1500]`, preserve, manual)

Triggers (no DataFrame equivalent):

- `.rdd.map(f)`, `.rdd.flatMap(f)`, `.rdd.filter(f)` and any closure-bearing RDD transform — JVM closures are opaque; rewrite manually
- `.rdd.mapValues(f)`, `.rdd.flatMapValues(f)` — closure on pair RDD
- `.rdd.foreach(f)`, `.rdd.foreachPartition(f)` — side-effecting closure
- `.rdd.mapPartitions(f)`, `mapPartitionsWithIndex(f)` — partition-level execution
- `.rdd.getNumPartitions`, `.rdd.partitions` / `.partitions.length` — meaningless under SC
- `.rdd.glom()`, `.rdd.pipe(cmd)` — no equivalent
- `.rdd.stats()` — RDD-only `StatCounter` (count, mean, stdev, max, min); no DataFrame equivalent — compute with `df.describe()` or `df.agg(count, mean, stddev, max, min)` manually
- `.rdd.histogram(buckets)` — RDD-only histogram returning `(edges, counts)`; use `df.stat.freqItems()` / `df.select(hex(col).cast(...).alias("bucket")).groupBy("bucket").count()` or `df.approxQuantile(...)` depending on the bucket type, or compute in-driver via `df.select(col).rdd.map(...)` is **not** the answer — Bucket A manual refactor
- `.javaRDD`, `.toJavaRDD` — no equivalent
- `SparkContext` ingestion: `sc.textFile`, `sc.wholeTextFiles`, `sc.hadoopFile`, `sc.hadoopRDD`, `sc.newAPIHadoopFile`, `sc.newAPIHadoopRDD`, `sc.sequenceFile`, `sc.objectFile`, `new SparkContext`
- `rdd.saveAsSequenceFile(path)` — Hadoop-serialised sink, no SCOS equivalent (the reader entry point `sc.objectFile` / `sc.sequenceFile` is likewise no-equivalent)
- `import org.apache.spark.rdd._`

> **Not Bucket A:** `saveAsObjectFile` is **[Partial]** (a parquet/table round-trip
> for DataFrame-shaped rows — §10.11), and accumulators
> (`longAccumulator` / `doubleAccumulator` / `collectionAccumulator` /
> `AccumulatorV2`) are **CONVERTIBLE [Workaround]** (a driver-side reduction maps
> to `df.agg(...)` — see "Accumulators → DataFrame aggregation"). There is **no**
> `sc.accumulator` / `AccumulatorParam` in Scala — that is a PySpark spelling; do
> not annotate a bare `accumulator` substring (false positives).

**Action:** leave the original expression in place and prepend a `// SCOS:`
marker that embeds the EWI code `[SPRKCNTSCL1500]` (single marker vocabulary —
parity with PySpark's `# SCOS: [SPRKCNTPY####]`; do **not** use a bare `// EWI:`
prefix). Keep the literal phrase `manual refactor` so the Phase 2b gate
quarantines the file. Do **NOT** delete the logic, and do **NOT** invent a
replacement (no `.rdd` re-introduction, no nested `createDataFrame`, no `Tuple1`
wrapping).

```scala
// SCOS: [SPRKCNTSCL1500] RDD API '.rdd.getNumPartitions' is not supported in
// Snowpark Connect; manual refactor required (no RDD layer on the client).
println(df.rdd.getNumPartitions)
```

The Phase 2b type-check gate **quarantines** files whose only failures are these
annotated RDD lines (it keys on the `SPRKCNTSCL1500` … `manual refactor` text, not
the comment prefix, so it will not revert them); they are reported as
manual-intervention items, not migration failures.

---

## Bucket B — DROP-THE-HOP: `df.rdd.*` shortcuts

These patterns use `.rdd` only as a gateway to a method that exists **identically**
on DataFrame. Drop the `.rdd` accessor and call the same method directly — no
closure or RDD knowledge required.

### Terminal actions

| RDD form | DataFrame equivalent |
| --- | --- |
| `df.rdd.count()` | `df.count()` |
| `df.rdd.isEmpty()` | `df.isEmpty()` |
| `df.rdd.collect()` | `df.collect()` |
| `df.rdd.first()` | `df.first()` |
| `df.rdd.take(n)` | `df.take(n)` |
| `df.rdd.toLocalIterator()` | `df.toLocalIterator()` |

### Caching / persistence

| RDD form | DataFrame equivalent |
| --- | --- |
| `df.rdd.cache()` / `df.rdd.persist()` | `df.cache()` |
| `df.rdd.unpersist()` | `df.unpersist()` |

### Set operations (both arguments are DataFrames)

| RDD form | DataFrame equivalent | Notes |
| --- | --- | --- |
| `df1.rdd.union(df2.rdd)` | `df1.union(df2)` / `df1.unionByName(df2)` | use `unionByName` when column order differs |
| `df.rdd.distinct()` | `df.distinct()` | |
| `df1.rdd.intersection(df2.rdd)` | `df1.intersect(df2)` | deduplicated |
| `df1.rdd.subtract(df2.rdd)` | `df1.except(df2)` (dedup) or `df1.exceptAll(df2)` (multiset) | RDD `subtract` preserves left duplicates → prefer `exceptAll` |

### Sampling / splitting

| RDD form | DataFrame equivalent |
| --- | --- |
| `df.rdd.sample(withReplacement, frac)` | `df.sample(withReplacement, frac)` |
| `rdd.takeSample(withReplacement, n, seed)` | `df.sample(frac).limit(n).collect()` — **[Workaround]**: no exact-`n` bounded sampler; the `seed` is ignored (nondeterministic on SCOS, §4) and `withReplacement = true` is unsupported. For a reproducible subset use the hash-bucket pattern in "Silent differences & platform limits" |

> `df.rdd.randomSplit(weights)` is **not** a valid drop-the-hop — `df.randomSplit()`
> is itself unsupported in SCOS. This is a Bucket C case; see "CONVERTIBLE: saving
> and splitting" below.

### Repartitioning

| RDD form | DataFrame equivalent |
| --- | --- |
| `df.rdd.repartition(n)` | `df.repartition(n)` |
| `df.rdd.coalesce(n)` | `df.coalesce(n)` |

```scala
// BEFORE:
val n    = df.rdd.count()
val rows = df.rdd.collect()
val uniq = df.rdd.distinct()
val both = df1.rdd.union(df2.rdd)

// AFTER:
val n    = df.count()
val rows = df.collect()
val uniq = df.distinct()
val both = df1.union(df2)
```

> **Note:** these are only safe to convert when the source of `.rdd` is a
> DataFrame (not an independently-constructed `RDD[Row]` or `RDD[(K,V)]`). When
> in doubt, trace the origin of the value before `.rdd`.

---

## Bucket C — CONVERTIBLE: `createDataFrame` (`sc.parallelize` / `sc.emptyRDD`)

`createDataFrame` **is** the correct SCOS target. Pick the overload by element type
(verified against `spark-connect-client-jvm` 3.5.x):

### C1. `Seq` of tuples / case classes (a `Product`)

```scala
// before: val rdd = sc.parallelize(Seq(("a", 1), ("b", 2)))   // used as a DataFrame
val df = spark.createDataFrame(Seq(("a", 1), ("b", 2))).toDF("key", "value")
```

**NEVER** `Seq(...).map(Tuple1.apply)` here — it compiles but **collapses the
tuple into a single struct column `_1`** (wrong schema).

### C2. `Seq` of primitives (NOT a `Product`) — Tuple1 wrap is required

```scala
val df = spark.createDataFrame(Seq(1, 2, 3).map(Tuple1.apply)).toDF("value")
```

`createDataFrame[A <: Product]` rejects a bare `Seq[Int]` (`inferred type
arguments [Int] do not conform to ... bounds [A <: Product]`), so primitives
**must** be wrapped.

### C3. `createDataFrame(sc.parallelize(rows), schema)` / `createDataFrame(sc.emptyRDD[Row], schema)`

Here `rows: Seq[Row]`. Drop the RDD and pass a `java.util.List[Row]` — the client
has `createDataFrame(rows: java.util.List[Row], schema: StructType)` but **no**
`Seq[Row]` overload:

```scala
import scala.collection.JavaConverters._   // Scala 2.12 (use scala.jdk.CollectionConverters for 2.13)

val df    = spark.createDataFrame(rows.asJava, schema)
val empty = spark.createDataFrame(Seq.empty[Row].asJava, schema)
```

**NEVER** nest: `createDataFrame(createDataFrame(rows.map(Tuple1.apply)).toDF("_1"), schema)`
does **not** type-check — there is no `createDataFrame(DataFrame, StructType)`
overload.

---

## Bucket C — CONVERTIBLE: key-based pair operations → `groupBy().agg(...)`

Once the source is a DataFrame, RDD pair ops become relational aggregations:

| RDD pair op                 | DataFrame equivalent                         |
| --------------------------- | -------------------------------------------- |
| `reduceByKey(_ + _)`        | `groupBy(key).agg(sum(value))`               |
| `reduceByKey(_ max _)`      | `groupBy(key).agg(max(value))`               |
| `reduceByKeyLocally(f)`     | `groupBy(key).agg(...).collect().toMap`      |
| `groupByKey()`              | `groupBy(key)`                               |
| `countByKey()`              | `groupBy(key).count()`                       |
| `aggregateByKey(z)(sf, cf)` | `groupBy(key).agg(...)`                      |
| `foldByKey(z)(f)`           | `groupBy(key).agg(...)`                      |
| `combineByKey(c, m, r)`     | `groupBy(key).agg(...)`                      |

```scala
import org.apache.spark.sql.functions.{explode, split, col, sum, count}

// Word count (flatMap + reduceByKey):
// BEFORE: sc.textFile("data.txt").flatMap(_.split(" ")).map(w => (w, 1)).reduceByKey(_ + _)
// AFTER:
spark.read.text("data.txt")
  .select(explode(split(col("value"), " ")).alias("word"))
  .groupBy("word")
  .agg(count("*").alias("count"))

// reduceByKey sum:
// BEFORE: sc.parallelize(Seq(("a", 1), ("a", 2))).reduceByKey(_ + _)
val df     = spark.createDataFrame(Seq(("a", 1), ("a", 2))).toDF("word", "count")
val result = df.groupBy("word").agg(sum("count").as("count"))
```

Note the ordering: convert the `parallelize`/`createDataFrame` source **first** so
the key/value column names exist, **then** rewrite the pair op against those
columns. If the reducer is an arbitrary non-associative lambda that has no `agg`
form, treat it as Bucket A (`// SCOS: [SPRKCNTSCL1500]` + manual).

---

## Bucket C — CONVERTIBLE: sorting and ordering

| RDD form | DataFrame equivalent | Notes |
| --- | --- | --- |
| `rdd.sortByKey()` | `df.orderBy(col("key"))` | replace `"key"` with the actual key column |
| `rdd.sortBy(f, ascending)` | `df.orderBy(<col-expr from f>)` (`.desc` when `ascending = false`) | `f` must be a column expression; an arbitrary closure key has no relational form (Bucket A) |
| `rdd.sortByKey(ascending = false)` | `df.orderBy(col("key").desc)` | |
| `rdd.takeOrdered(n)` | `df.orderBy(col("key").asc).limit(n).collect()` | for custom `Ordering`, match the ordering expression |
| `rdd.top(n)` | `df.orderBy(col("key").desc).limit(n).collect()` | |

---

## Bucket C — CONVERTIBLE: pair joins

All four RDD pair-join variants + `cogroup` and `subtractByKey` map to DataFrame joins:

| RDD form | DataFrame equivalent |
| --- | --- |
| `rdd1.join(rdd2)` | `df1.join(df2, Seq("key"))` |
| `rdd1.leftOuterJoin(rdd2)` | `df1.join(df2, Seq("key"), "left")` |
| `rdd1.rightOuterJoin(rdd2)` | `df1.join(df2, Seq("key"), "right")` |
| `rdd1.fullOuterJoin(rdd2)` | `df1.join(df2, Seq("key"), "outer")` |
| `rdd1.cartesian(rdd2)` | `df1.crossJoin(df2)` |
| `rdd1.cogroup(rdd2)` | `df1.join(df2, Seq("key"), "outer")` + `collect_list` per side |
| `rdd1.subtractByKey(rdd2)` | `df1.join(df2, Seq("key"), "left_anti")` |

```scala
// BEFORE: rdd1.join(rdd2)  →  AFTER:
val result = df1.join(df2, Seq("key"))

// BEFORE: rdd1.subtractByKey(rdd2)  →  AFTER (left-anti join):
val result = df1.join(df2, Seq("key"), "left_anti")
```

---

## Bucket C — CONVERTIBLE: pair accessors and sampling

| RDD form | DataFrame equivalent |
| --- | --- |
| `rdd.keys()` | `df.select(col("key"))` (use the actual key column name) |
| `rdd.values()` | `df.select(col("value"))` (use the actual value column name) |
| `rdd.keyBy(f)` | `df.withColumn("key", <col-expr from f>)` — materialize the key column, then use it in `groupBy`/`join`; `f` must be a column expression (an arbitrary closure → scalar JVM UDF) |
| `rdd.sampleByKey(withReplacement, fractions)` | `df.sampleBy("key", fractions, seed)` |
| `rdd.countByValue()` | `df.groupBy(df.columns.map(col): _*).count().collect()` |

---

## Bucket C — CONVERTIBLE: mapValues / flatMapValues

These require translating the closure to a column expression (inspect the closure body):

| RDD form | DataFrame equivalent |
| --- | --- |
| `rdd.mapValues(f)` | `df.withColumn("value", <col-expr from f>)` |
| `rdd.flatMapValues(f)` | `df.withColumn("value", <col-expr from f>).select(explode(col("value")))` |

```scala
// BEFORE: rdd.mapValues(_ * 2)
// AFTER  (first convert parallelize source to df with named columns):
df.withColumn("value", col("value") * 2)

// BEFORE: rdd.flatMapValues(_.split(","))
// AFTER:
df.withColumn("value", split(col("value"), ",")).select(explode(col("value")))
```

---

## Bucket C — CONVERTIBLE: indexing

| RDD form | DataFrame equivalent | Notes |
| --- | --- | --- |
| `rdd.zipWithIndex()` | `df.withColumn("index", row_number().over(Window.orderBy(<col>)) - 1)` | requires an explicit ordering column; result is 0-based |
| `rdd.zipWithUniqueId()` | `df.withColumn("uid", monotonically_increasing_id())` | unique but NOT contiguous; not stable across repartition |
| `rdd1.zip(rdd2)` | add a matching `row_number()` index to each DataFrame, then `df1.join(df2, Seq("_idx"))` | **[Partial]** — positional element-wise zip has no direct SCOS form; the index-join reproduces the intent only when both sides share a deterministic ordering column (the RDD positional guarantee is not reproducible) |

```scala
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions.{row_number, monotonically_increasing_id}

// zipWithIndex (0-based, deterministic given an order column):
val w = Window.orderBy("order_col")
df.withColumn("index", row_number().over(w) - 1)

// zipWithUniqueId (unique, not contiguous):
df.withColumn("uid", monotonically_increasing_id())
```

> ⚠️ `row_number()` over an unpartitioned window serialises all rows through one
> executor. If only a unique id is needed (not 0..N-1), prefer
> `monotonically_increasing_id()`.

---

## Bucket C — CONVERTIBLE: saving and splitting

| RDD form | DataFrame equivalent | Notes |
| --- | --- | --- |
| `rdd.saveAsTextFile(path)` | `df.write.mode("overwrite").text(path)` | one line per row in the `value` column |
| `rdd.randomSplit(weights)` | ⚠ **`df.randomSplit()` is itself unsupported in SCOS** | use `df.sample(fraction, seed)` with complementary fractions, or add `df.withColumn("split", rand() < fraction)` |

---

## Bucket C — CONVERTIBLE: `sc.broadcast`

`sc.broadcast(v)` has no equivalent, but its intent is almost always achievable
without it:

- **Scalar / lookup value** — use `v` directly. Snowpark Connect broadcasts small
  values to the server automatically; no explicit `Broadcast` wrapper is needed.
- **DataFrame join hint** — replace `F.broadcast(df)` hint with
  `df.hint("broadcast")` or use `broadcast(df)` from
  `org.apache.spark.sql.functions`.

```scala
// BEFORE: val lookup = sc.broadcast(Map("a" -> 1, "b" -> 2))
// AFTER (scalar lookup used directly):
val lookup = Map("a" -> 1, "b" -> 2)

// BEFORE: df1.join(F.broadcast(df2), "key")
// AFTER:
df1.join(df2.hint("broadcast"), Seq("key"))
// or: import org.apache.spark.sql.functions.broadcast
df1.join(broadcast(df2), Seq("key"))
```

---

## Decision summary

| Pattern | Bucket | Action |
| --- | --- | --- |
| `.rdd.map(f)` / `.flatMap(f)` / `.filter(f)` / `.foreach(f)` (closure) | A | `// SCOS: [SPRKCNTSCL1500]` + preserve + manual |
| `.rdd.mapValues(f)` / `.flatMapValues(f)` (closure, `.rdd`-sourced) | A | `// SCOS: [SPRKCNTSCL1500]` + preserve + manual |
| `.rdd.mapPartitions` / `foreachPartition` / `glom` / `pipe` | A | `// SCOS: [SPRKCNTSCL1500]` + preserve + manual |
| `.rdd.getNumPartitions` / `.rdd.partitions` | A | `// SCOS: [SPRKCNTSCL1500]` + preserve + manual |
| `sc.textFile`/`hadoopFile`/`sequenceFile`/`objectFile`, `new SparkContext` | A | `// SCOS: [SPRKCNTSCL1500]` + preserve + manual |
| `rdd.saveAsSequenceFile(path)` | A | `// SCOS: [SPRKCNTSCL1500]` + preserve + manual |
| `rdd.saveAsObjectFile(path)` | C (Partial) | parquet/table round-trip + `// SCOS: TODO` for the Java-object payload (§10.11) |
| `longAccumulator` / `doubleAccumulator` / `collectionAccumulator` / `AccumulatorV2` | C | `df.agg(...)` reduction — **not** a blanket TODO (see accumulator section) |
| `treeAggregate` / `treeReduce` | C | `df.agg(...)`, drop `depth` |
| `collectAsMap` / `countApprox` / `countApproxDistinct` / `meanApprox` / `sumApprox` | C | §10.5–10.9 rewrites |
| `repartitionAndSortWithinPartitions` / `toDebugString` | C (Partial) | §10.4 / §10.16 intent-only rewrites |
| `df.rdd.count()` / `isEmpty()` / `collect()` / `first()` / `take(n)` / `toLocalIterator()` | B | drop `.rdd` → `df.count()` etc. |
| `df.rdd.cache()` / `persist()` / `unpersist()` | B | drop `.rdd` → `df.cache()` etc. |
| `df1.rdd.union(df2.rdd)` / `distinct()` / `intersection` / `subtract` | B | drop both `.rdd` hops → `df1.union(df2)` etc. |
| `df.rdd.sample(wr,f)` / `repartition(n)` / `coalesce(n)` | B | drop `.rdd` → same method on DataFrame |
| `sc.parallelize(Seq[Product])` | C1 | `createDataFrame(seq).toDF(names…)` |
| `sc.parallelize(Seq[primitive])` | C2 | `createDataFrame(seq.map(Tuple1.apply)).toDF("value")` |
| `createDataFrame(sc.parallelize(Seq[Row]), schema)` / `emptyRDD[Row]` | C3 | `createDataFrame(seq.asJava, schema)` + `JavaConverters` |
| `reduceByKey`/`reduceByKeyLocally`/`groupByKey`/`countByKey`/`aggregateByKey`/`foldByKey`/`combineByKey` | C | `groupBy(key).agg(...)` |
| `rdd.sortByKey()` | C | `df.orderBy(col("key"))` / `.orderBy(col("key").desc)` |
| `rdd.takeOrdered(n)` / `rdd.top(n)` | C | `df.orderBy(col.asc/desc).limit(n).collect()` |
| `rdd1.join(rdd2)` / `leftOuterJoin` / `rightOuterJoin` / `fullOuterJoin` | C | `df1.join(df2, Seq("key"), "inner/left/right/outer")` |
| `rdd1.cartesian(rdd2)` | C | `df1.crossJoin(df2)` |
| `rdd1.cogroup(rdd2)` | C | `df1.join(df2, Seq("key"), "outer")` + `collect_list` |
| `rdd1.subtractByKey(rdd2)` | C | `df1.join(df2, Seq("key"), "left_anti")` |
| `rdd.keys()` / `rdd.values()` | C | `df.select(col("key"))` / `df.select(col("value"))` |
| `rdd.sampleByKey(wr, fractions)` | C | `df.sampleBy("key", fractions, seed)` |
| `rdd.mapValues(f)` (after `sc.parallelize`) | C | `df.withColumn("value", <col-expr from f>)` |
| `rdd.flatMapValues(f)` (after `sc.parallelize`) | C | `df.withColumn("value", <expr>).select(explode(...))` |
| `rdd.zipWithIndex()` | C | `row_number().over(Window.orderBy(<col>)) - 1` |
| `rdd.zipWithUniqueId()` | C | `df.withColumn("uid", monotonically_increasing_id())` |
| `rdd.countByValue()` | C | `df.groupBy(df.columns.map(col): _*).count().collect()` |
| `rdd.saveAsTextFile(path)` | C | `df.write.mode("overwrite").text(path)` |
| `rdd.randomSplit(weights)` | C | ⚠ `df.randomSplit()` is unsupported in SCOS — use `df.sample()` |
| `sc.broadcast(v)` scalar | C | use `v` directly |
| `sc.broadcast(df)` join hint | C | `df.hint("broadcast")` or `broadcast(df)` |

---

## Scala-Specific Considerations

- Use `spark.implicits._` for implicit conversions from Scala collections to DataFrames
- Prefer `col("columnName")` or `$"columnName"` (requires `spark.implicits._`) for column references
- When converting `sc.parallelize`, `Seq(...).toDF(...)` works for tuples/case classes via implicits; for `Seq[Row]` use `createDataFrame(seq.asJava, schema)`
- For typed transformations, prefer the `Dataset[T]` API with case classes over RDD `.map()` — it preserves compile-time type safety while being fully supported in SCOS

---

## Verdict routing (how to apply the recipes below)

Every recipe in the sections below carries a **verdict tag** from the migration
guide (§6–§10). The fixer routes on it:

| Verdict | Meaning | Action |
|---|---|---|
| **[Native]** | first-class DataFrame equivalent | apply the rewrite; no caveat needed |
| **[Workaround]** | reproduces the intent with a rewrite | apply the rewrite |
| **[Silent-diff]** | rewrite exists but semantics drift silently (no error) | apply the rewrite **and** add a `// SCOS:` guard/note calling out the drift |
| **[Partial]** | intent only — SCOS cannot reproduce some aspect | apply the closest form **and** `// SCOS: TODO` for the lost aspect (index, ordering, layout, `depth`, …) |
| **[Hard gap]** | no equivalent (⚠perm = permanent, ⚠ns = not currently supported) | `// SCOS: TODO`; for ⚠perm, delete-don't-migrate or keep on Spark |

Tag every applied rewrite: RDD→DataFrame rewrites use
`// SCOS: [SPRKCNTSCL1500] <what changed>`; SparkContext primitives
(`parallelize`, `broadcast`, `accumulator`, …) and delete-outright behavioral
items use `// SCOS: [SPRKCNTSCL1000]`; hard gaps use
`// SCOS: TODO - <why + Snowflake-native alternative>`.

---

## Accumulators → DataFrame aggregation

**This is the #1 correction: an accumulator is NOT a blanket TODO.** A Spark
driver-side accumulator — the factory forms `sparkContext.longAccumulator` /
`.doubleAccumulator` / `.collectionAccumulator`, their class types
`LongAccumulator` / `DoubleAccumulator` / `CollectionAccumulator`, or a custom
`AccumulatorV2` — incremented inside `foreach`/`map`/a UDF closure is a *reduction* — and a
reduction is exactly what `df.agg(...)` / `df.groupBy(...).agg(...)` do in one
SQL pass. Rewrite the accumulator as the equivalent aggregate. Only the four
uses in "True hard gaps" below have no equivalent.

> There is **no** `sc.accumulator(...)` / `AccumulatorParam` in Scala — those are
> PySpark spellings. Scala uses `spark.sparkContext.longAccumulator` etc.
> `df.observe(...)` metrics are a **no-op** on SCOS (`// SCOS: [SPRKCNTSCL1000]`)
> — compute each metric with an explicit `df.agg(...)` instead. A top-level
> `spark.sparkContext.*` call has no `SparkContext` under Connect.

### Count rows meeting a condition — [Workaround]

```scala
// BEFORE (Spark): LongAccumulator incremented in foreach on match
// val negCount = spark.sparkContext.longAccumulator
// df.foreach { r => if (!r.isNullAt(0) && r.getDouble(0) < 0) negCount.add(1) }
// println(negCount.value)
// AFTER:
// SCOS: [SPRKCNTSCL1500] longAccumulator+foreach row-count → conditional aggregate
val negCount = df.agg(count(when(col("amount") < 0, 1)).as("n")).first().getLong(0)
```

### Sum / min / max / avg a column — [Native]

```scala
// BEFORE (Spark): custom AccumulatorV2 / DoubleAccumulator reducing a column
// AFTER:
// SCOS: [SPRKCNTSCL1500] AccumulatorV2 reduction → DataFrame aggregate
val r = df.agg(
  min("amount").as("min_amount"), max("amount").as("max_amount"),
  sum("amount").as("total"),      avg("amount").as("avg_amount"),
  count("amount").as("non_null_count")
).first()
```

> **Silent-diff:** SQL `MIN/MAX/SUM/AVG` skip NULLs; if Spark treated NULL as a
> sentinel, wrap with `coalesce(col("amount"), lit(0))`. `sum` on empty input
> returns NULL, not 0 — guard with `coalesce(sum(...), lit(0L))` (§4⑤).
> `count("amount")` skips NULLs; use `count(lit(1))` to match RDD `values.size`
> (§4②). NULL numeric aggregates come back as boxed `null` — read with
> `r.getAs[java.lang.Long]("total")` and null-check.

### Collect a bounded set of IDs (`collectionAccumulator`) — [Workaround, ⚠perm at scale]

```scala
// BEFORE (Spark): collectionAccumulator gathering unique IDs via foreach
// val seenIds = spark.sparkContext.collectionAccumulator[String]
// AFTER — case A: unique IDs, small cardinality:
// SCOS: [SPRKCNTSCL1500] collectionAccumulator → collect_set
val uniqueIds = df.agg(collect_set("user_id").as("ids")).first().getSeq[String](0).toSet
// case B: Map of counts (user_id -> row count):
val idToCount = df.groupBy("user_id").agg(count(lit(1)).as("n"))
  .collect().map(r => r.getString(0) -> r.getLong(1)).toMap
```

> **Silent-diff:** `collect_set`/`collect_list` silently **drop NULLs** and are
> **unordered** (§4⑥). Near the 128 MB per-value cap prefer
> `df.select("user_id").distinct().collect()` (a row set, not one huge ARRAY
> column) — never assemble one giant server-side collection.

### Count matches / non-matches in a join — [Native]

```scala
val joined = orders.join(customers, Seq("customer_id"), "left")
// SCOS: [SPRKCNTSCL1500] join match/unmatched accumulators → conditional aggregates
val counts = joined.agg(
  count(lit(1)).as("total"),                                   // count(*) — every row
  count("customer_name").as("matched"),                        // count(col) skips NULLs — the point
  count(when(col("customer_name").isNull, 1)).as("unmatched")
).first()   // total=4, matched=2, unmatched=2
```

### Per-key operation / collision counters — [Workaround, ⚠perm]

```scala
// BEFORE (Spark): reduceByKey with a longAccumulator counting merges
// AFTER:
val totals = df.groupBy("category").agg(sum("amount").as("total"))
// per-pair merge counts do NOT carry over (SCOS runs one SQL aggregation, no
// per-pair merge). collision count == rows - distinct keys, computed directly:
// SCOS: [SPRKCNTSCL1500] merge-collision accumulator → rows minus distinct keys
val collisionCount = df.count() - df.select("category").distinct().count()
```

### Accumulator inside a UDF — [Workaround]

```scala
// BEFORE (Spark): a JVM UDF closure increments a longAccumulator to count error rows
// AFTER: emit an error-flag COLUMN, then aggregate it (no UDF-side counter reaches the driver)
val flag = when(col("amount").isNull || col("amount") < 0, 1).otherwise(0)
val adj  = when(col("amount").isNull || col("amount") < 0, lit(null)).otherwise(col("amount") * 1.1)
val res  = df.withColumn("errflag", flag).withColumn("adjusted", adj)
// SCOS: [SPRKCNTSCL1500] in-UDF error accumulator → flag column + sum
val errorCount = res.agg(sum(col("errflag"))).first().getLong(0)
```

> A `throw` inside a scalar UDF closure **does** propagate to the driver, but a
> closure-side **counter/flag never aggregates** — SCOS runs one SQL query with
> no driver-visible shared mutable state per row (§4⑦). Keep abort logic on the
> driver: `if (df.filter(col("amount") < 0).count() > 0) throw new IllegalStateException(...)`.

### Approximate aggregates (histogram / quantile / top-K) via a custom `AccumulatorV2` — [Native]

```scala
// BEFORE (Spark): a custom AccumulatorV2 building a histogram / quantile / top-K sketch
// AFTER:
// SCOS: [SPRKCNTSCL1500] AccumulatorV2 sketch → APPROX_PERCENTILE / exact groupBy
val row = df.agg(
  expr("APPROX_PERCENTILE(latency_ms, 0.50)").as("p50"),
  expr("APPROX_PERCENTILE(latency_ms, 0.99)").as("p99")
).first()
// top-K by frequency — APPROX_TOP_K is NOT available via expr(); use an exact groupBy + count:
val top10 = df.groupBy("status").count().orderBy(desc("count"), asc("status")).limit(10).collect()
```

> On SCOS `APPROX_PERCENTILE` maps to Snowflake `PERCENTILE_DISC` — an **exact**
> discrete dataset value, not a t-Digest approximation (so p99 may differ from
> Spark's interpolated `percentile()`; use `expr("percentile(col, q)")` for the
> interpolated value). `APPROX_TOP_K` raises "Unsupported function name" via
> `expr` — use the exact `groupBy(col).count().orderBy(desc("count")).limit(k)`
> with a deterministic tie-break. **Validate custom-sketch mappings against your
> own Spark baseline** before relying on them.

---

## True hard gaps

**Three** of these are **permanent** hard blockers (⚠perm) with no DataFrame
equivalent; the **fourth** is not currently supported (⚠ns) with a documented
workaround. Do not improvise a rewrite for the ⚠perm cases — delete-don't-migrate
or keep the job on Spark.

### `foreachPartition` external side-effects — [Hard gap] [⚠perm]

One long-lived Kafka producer / HTTP client / file handle per partition.
`df.rdd.foreachPartition(...)` does not even compile on SCOS (`value rdd is not a
member`), and `Dataset.foreachPartition` has no server-side execution path —
SCOS runs your DataFrame as SQL on a warehouse, so there is no per-partition JVM
process. **Write from the driver instead:**

```scala
// SCOS: TODO - foreachPartition per-partition sinks are unsupported (⚠perm, architectural).
// Small/medium volumes — pull rows back and use ONE client-side producer:
val producer = new KafkaProducer[String, String](props)
df.collect().foreach(r => producer.send(new ProducerRecord("events", r.getAs[String]("value"))))
producer.flush(); producer.close()
// Large volumes — land rows in a table and drive egress Snowflake-side (Kafka connector / task):
// df.write.mode("append").saveAsTable("events_out")
```

`.javaRDD` / `.toJavaRDD` are compile errors for the same reason (no RDD layer).

### Cache-hit detection — [Hard gap] [⚠perm]

A `longAccumulator` counting cache hits vs recomputations across
`persist()`/`unpersist()`. Snowflake's result cache is transparent — **no
client-visible hit/miss signal. Delete the counter.**

```scala
// SCOS: TODO - cache-hit accumulator has no equivalent (⚠perm): Snowflake's result
// cache is transparent. Delete the counter. For perf debugging query
// TABLE(INFORMATION_SCHEMA.QUERY_HISTORY()) or SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
// (bytes_scanned ~0 indicates a cache hit). If the counter gated a code path, redesign.
```

### Mid-job live progress snapshots — [Hard gap] [⚠perm]

A background thread polling `acc.value` on a timer. `count()`/`collect()` blocks
the calling thread for the whole Snowflake query and `acc.value` reads 0
throughout (no warehouse→driver callback).

```scala
// SCOS: TODO - live mid-job accumulator progress has no equivalent (⚠perm).
// Post-hoc snapshots — split the job into chunks and record after each:
var total = 0L; val snapshots = scala.collection.mutable.ArrayBuffer[Long]()
for (lo <- 0 until 100 by 10) {
  total += df.filter(col("bucket") >= lo && col("bucket") < lo + 10).count()
  snapshots += total
}
// Real-time UIs: from a SEPARATE session poll
// SELECT * FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION())
```

### `writeStream.foreachBatch` cross-batch state — [Partial] [⚠ns]

Running state across streaming micro-batches. `foreachBatch` is **not currently
supported** (the callback never executes). Workaround: a manual batch loop over
static DataFrames with cross-batch state in a Snowflake table — **preserves
state but loses trigger semantics**. For true streaming triggers keep the job on
Spark.

```scala
// SCOS: TODO - writeStream.foreachBatch cross-batch state is not currently supported (⚠ns).
// BOUNDED backfill/reprocessing only — manual batch loop:
spark.sql("DROP TABLE IF EXISTS processed_events")   // reset state ONCE before the loop
def processBatch(batchDf: DataFrame): Long = {
  batchDf.write.mode("append").saveAsTable("processed_events")               // accumulate
  spark.table("processed_events").agg(sum("amount")).first().getLong(0)      // running total
}
for (batchDf <- yourBatchSource()) {   // YOUR iterator of static DataFrames
  val total = processBatch(batchDf)
}
// Always re-run from the top (DROP resets state); do NOT resume mid-loop or rows double-count.
```

---

## Aggregate & reduce recipes (`aggregate` / `treeAggregate` / `treeReduce` / `reduce` / `fold` family)

All of these RDD-aggregate ops have a DataFrame equivalent. The recurring traps
are collected in "Silent differences & platform limits" below.

### `aggregate(zeroValue)(seqOp, combOp)` — [Workaround]

```scala
// BEFORE: rdd.aggregate((0,0))((a,x)=>(a._1+x,a._2+1), (a,b)=>(a._1+b._1,a._2+b._2))  // (15,5)
// AFTER (e.g. an average):
// SCOS: [SPRKCNTSCL1500] rdd.aggregate → df.agg (read multi-stat off one Row)
val row = df.agg(coalesce(sum("value"), lit(0L)).as("s"), count(lit(1)).as("c")).first()
val avg = if (row.getLong(1) != 0) row.getLong(0).toDouble / row.getLong(1) else null
```

**Watch:** a non-identity `zeroValue` must be applied **once** — `sum(...) + seed`
(§4①); identity zeros (`0`/`1`/`""`/empty) need no adjustment.

### `treeAggregate(zeroValue)(seqOp, combOp, depth)` — [Workaround]

Same result as `aggregate`; **`depth` has no SCOS analogue — drop it** (Snowflake
plans its own aggregation tree).

```scala
// BEFORE: val (n,s,ss) = rdd.treeAggregate((0,0.0,0.0))(seqOp, combOp)  // mean/variance
// AFTER:
// SCOS: [SPRKCNTSCL1500] rdd.treeAggregate → df.agg (drop depth)
df.agg(avg("v").as("mean"), var_pop("v").as("var")).first()   // var MUST be var_pop, not var_samp
// Dense fixed-width vector — explode & sum per index (bounded by width, not N):
vdf.select(posexplode(col("vec")).as(Seq("i", "v")))
   .groupBy("i").agg(sum("v").as("s")).orderBy("i").collect().map(_.getAs[Long]("s"))
```

> Never `collect_list` a vector column — it builds one big array and hits the
> 128 MB ceiling. For ragged/custom merges with no SQL form, use a native Java
> UDAF (see "Custom aggregate functions") or keep on Spark;
> `applyInPandas`/`mapInPandas` are **Python-only** and unavailable from the
> Scala/Java client.

### `aggregateByKey(zeroValue)(seqFunc, combFunc)` — [Native]

```scala
// SCOS: [SPRKCNTSCL1500] rdd.aggregateByKey → df.groupBy().agg()
df.groupBy("k").agg(sum(coalesce(col("v"), lit(0))).as("s"), count(lit(1)).as("c"))
```

Per-key distinct set: `array_sort(collect_set("v"))` (watch 128 MB at extreme
cardinality; count-only → `approx_count_distinct`). `numPartitions`/`Partitioner`:
drop (result is partition-independent). Non-identity seed: `sum("v") + lit(seed)`.

### `treeReduce(f, depth)` — [Workaround]

Degenerate `treeAggregate` (no seed); **raises on empty**; `depth` N/A.

```scala
// BEFORE: rdd.treeReduce(_ + _)  // 15 ;  rdd.treeReduce(_ * _)  // 120
// AFTER:
df.agg(coalesce(sum("v"), lit(0L)))              // sum (min/max for reduce-min/max)
df.agg(round(product("v")).cast("long"))         // product — round+cast guard (§4④)
// empty input: add `if (df.count()==0) throw ...` to match Spark, or coalesce to identity
```

### `reduce(f)` — [Native]

```scala
// BEFORE: rdd.reduce(_ + _)  // 15 ; raises on empty
// AFTER:
df.agg(coalesce(sum("v"), lit(0L))).first().getLong(0)   // min("v")/max("v") for min/max
```

Product, empty-input, and non-associative `f` are handled exactly as in
`treeReduce`/§4. A genuinely non-associative reduce lambda has no `agg` form →
treat as Bucket A or a native Java UDAF.

### `fold(zeroValue)(op)` / `foldByKey(zeroValue)(func)` — [Workaround] — EXPECTED_DIFF for a non-identity seed

Like `reduce` but seeded; does not raise on empty. RDD applies a non-identity
seed `partitions+1` times (partition-count-dependent); reproduce the **intent**
by applying the seed once.

```scala
// BEFORE: rdd.fold(0)(_ + _) // 15 ;  rdd.fold(7)(_ + _) // partition-scaled (e.g. 92)!
// AFTER:
Option(df.agg(sum("v")).first().get(0)).map(_.asInstanceOf[Long]).getOrElse(0L)   // identity seed → 15
df.agg(sum("v")).first().getLong(0) + 7          // seed once → 22, NOT Spark's partition-scaled value (§4①)
// per-key:
df.groupBy("k").agg(coalesce(sum("v"), lit(0L)))          // foldByKey(0)(_ + _)
df.groupBy("k").agg(round(product("v")).cast("long"))     // foldByKey(1)(_ * _)
df.groupBy("k").agg(sum("v") + lit(10))                   // foldByKey(10)(_ + _) — seed once per key
```

### `combineByKey(createCombiner, mergeValue, mergeCombiners)` — [Native]

The most general per-key primitive (the others delegate to it).

```scala
// BEFORE: rdd.combineByKey(v=>(v,1), (a,v)=>(a._1+v,a._2+1), ...).mapValues(x=>x._1.toDouble/x._2)
// AFTER (per-key average):
df.groupBy("k").agg(avg("v"))
// collect list/set per key:
df.groupBy("k").agg(array_sort(collect_list("v")).as("list"),
                    array_sort(collect_set("v")).as("set"))
```

Bespoke combiner with no SQL form → a native Java UDAF (see "Custom aggregate
functions") or keep on Spark.

### `groupByKey()` (RDD) — [Workaround] [⚠perm at scale]

```scala
// BEFORE: rdd.groupByKey().mapValues(_.toSeq.sorted)  // {"a":[1,2],...}
// AFTER:
df.groupBy("k").agg(array_sort(collect_list("v")))                 // collect per key
df.groupBy("k").agg(count(lit(1)).as("star"), count("v").as("v"))  // star counts NULLs, v doesn't
df.groupBy("k").agg(array_sort(collect_set("v")))                  // distinct (struct-wrap to keep NULLs)
```

> A single huge per-key group **raises** on SCOS (128 MB, no spill) where Spark's
> `collect_list` also OOMs — reduce inside the aggregate for large groups.

### Typed `Dataset[T]` / `KeyValueGroupedDataset` aggregate paths — [Native] · closures are a [Hard gap]

The **relational** groupBy path stays typed and is fully supported:

```scala
// SCOS: [SPRKCNTSCL1500] typed groupByKey closure → relational groupBy(col).agg(...)
df.groupBy(col("k")).agg(sum("v").as("s"), count(lit(1)).as("c")).as[(String, Long, Long)]
```

> `ds.groupByKey(_.k).reduceGroups(...)` / `mapGroups` / `flatMapGroups` and typed
> `Aggregator`s are a genuine Scala-only **[Hard gap] [SPRKCNTSCL1000]** — SCOS
> does not execute arbitrary JVM aggregation closures server-side. Rewrite to the
> relational form above or port to a native Java UDAF.

---

## Silent differences & platform limits

These give a **wrong answer with no error** (or are permanent platform limits).
Grep your codebase up front, then apply the guard inline in the recipe that hits
each one.

- **128 MB collection ceiling — [⚠perm].** A single ARRAY/OBJECT/VARIANT value
  is capped at ~128 MB uncompressed. `collect_list`/`collect_set`/map aggregates
  / a reassembled wide vector compile to one such value **per group** with no
  spill; a pathological single group **raises** a clean 128 MB error (Spark OOMs
  on the same input). **Mitigation:** reduce inside the aggregate (`sum`,
  `count`, `approx_count_distinct`); element-wise vector sums via
  `posexplode → groupBy(idx).sum` stay bounded.
- **Non-identity seed × partitions (§4①).** RDD `fold`/`aggregate` applies a
  non-identity `zeroValue` `partitions+1` times, so the RDD result is
  partition-count-dependent. Reproduce the **intent**: reduce, then apply the
  seed **once** — `sum("v") + seed`. Identity seeds need no adjustment.
- **`count(col)` vs `count(lit(1))` (§4②).** `count(col)` skips NULLs;
  `count(lit(1))` (≡ SQL `count(*)`) counts every row — match RDD `values.size`
  with `count(lit(1))`. `count("*")` as a string is unreliable in the Scala API;
  prefer `count(lit(1))` or `expr("count(*)")`. `countDistinct` over multiple
  cols drops rows where **any** col is NULL.
- **Empty input → NULL vs raise (§4⑤).** RDD `reduce`/`treeReduce` **raise** on
  empty; the DataFrame aggregate returns **NULL**. Guard with
  `coalesce(..., lit(0L))`, or `if (df.count()==0) throw ...` to match Spark.
- **`product` float noise (§4④).** Compiles to `exp(sum(ln x))` →
  `119.99999999999997` for an exact-integer product. Guard exact ints/decimals
  with `round(product("v")).cast("long")`.
- **`collect_set`/`collect_list` drop NULLs and are unordered (§4⑥).** Struct-wrap
  to keep NULLs (a struct is never NULL); `array_sort` for determinism (but see
  the struct-array sort note below).
- **Struct-array sort order — [⚠perm] (§4③).** `array_sort` over a STRUCT array
  does **not** sort by the leading field on SCOS, and comparator-lambda
  `array_sort` is unsupported; a pre-aggregate `orderBy` does **not** guarantee
  intra-group `collect_list` order. For order-dependent output use the
  zero-padded string-prefix pattern below.
- **`repartition(n, keyCol)` is a no-op hint (§4⑧).** SCOS accepts it but does
  **not** physically reshuffle by key. Patterns relying on per-key co-location
  silently give wrong results — express per-key intent in the query (`groupBy`)
  or use a table `CLUSTER BY` key.
- **`df.sample()` seed ignored — [Silent-diff, ⚠perm].** Produces a valid sample
  but the **seed is ignored** (nondeterministic) and `withReplacement = true` is
  unsupported. For a **reproducible** split, bucket a stable key instead of
  seeding: `df.filter(pmod(hash(col("id")), lit(100)) < 80)` is the 80% side,
  `>= 80` the 20%. Note `hash()` maps to Snowflake's native `HASH()` (not
  Murmur3), so the kept row set is stable **within** SCOS but differs from Spark;
  for exact cross-engine identity use `pmod(col("id"), lit(100)) < 80` or an
  `xxhash64`/`md5`-based bucket. Validate the sampling change against your own
  Spark baseline.
- **Unsupported sketch functions.** `count_min_sketch` — **[Workaround]**
  (server-side UDAF): ships via `expr("count_min_sketch(id, 1e-2, 0.99e0, 42)")`
  and returns an opaque BINARY blob; pass `epsilon` as a **float** literal in
  exponent notation (a bare decimal `0.01` errors; a `CAST(... AS DOUBLE)` is not
  foldable and also errors). `bloom_filter_agg` — **[Hard gap, ⚠ns]** (raises
  "Unsupported function name") → `// SCOS: TODO`. For per-key frequency use exact
  `df.groupBy(key).count()`; `approx_count_distinct` answers distinct-**cardinality**
  only, not frequency.

**Order-dependent output (`array_sort` on structs) — [Workaround] (§9).** To order
values by an integer key, encode the key as a zero-padded string prefix, sort the
scalar strings (lexical == numeric), then strip the prefix. Copy-pasteable:

```scala
// order values in `ch` by integer key `id` (non-negative keys; offset signed keys before padding)
// SCOS: [SPRKCNTSCL1000] ordered collect_list → zero-padded prefix sort
val enc = df.withColumn("enc", concat(lpad(col("id").cast("string"), 10, "0"), col("ch")))
enc.agg(array_join(
    transform(array_sort(collect_list("enc")),
              (s: Column) => s.substr(lit(11), length(s) - lit(10))), "").as("s")).first().getString(0)
// per-key: build `enc` from a df that HAS the group key `k`, then groupBy("k").agg(...)
```

> *Java:* the `Column => Column` lambda in `transform(...)` is awkward — use the
> SQL string form: `expr("array_join(transform(array_sort(collect_list(enc)), s -> substr(s, 11, length(s) - 10)), '')")`.
> If order matters but there is **no** ordering column, it was never stored —
> **Hard gap**: add an explicit sequence column at ingest, then use the pattern.

---

## Additional verified RDD operations (§10 of the guide)

Independently verified against the SCOS runtime. Quick-reference rows are in the
tables above; the per-op detail and the exact SCOS caveat are here. (All
`.rdd`-sourced forms are compile errors on the SCOS `Dataset`.)

### `groupBy(f, numPartitions, partitionFunc)` — [Workaround] — EXPECTED_DIFF for NULL keys/values

```scala
// BEFORE: rdd.groupBy(_ % 2)   // {0:[2,4], 1:[1,3,5]}
// AFTER: materialize the grouping key as a column, then groupBy + collect
// SCOS: [SPRKCNTSCL1500] rdd.groupBy → withColumn(key) + groupBy + collect_list
df.withColumn("key", expr("v % 2"))
  .groupBy("key").agg(sort_array(collect_list("v")).as("items"))   // array_sort if order matters
```

`collect_list` drops NULLs and is unordered (a NULL-key group comes back `[]`,
not `[null]`); `numPartitions`/`partitionFunc` have no SCOS meaning.

### `mapPartitionsWithIndex` / `mapPartitionsWithSplit(f)` — [Partial]

`mapPartitionsWithSplit` is a deprecated alias of `mapPartitionsWithIndex`. SCOS
can run index-independent per-partition logic via a scalar JVM UDF or a column
expression, but **cannot surface the split index** (partitions are
internal/random).

```scala
// SCOS: TODO - the split/partition index is genuinely unavailable; any index-driven
// logic must be redesigned. Index-independent per-partition work → a scalar JVM UDF or column expr.
```

### `partitionBy(numPartitions, partitionFunc)` — [Partial]

SCOS has **no physical key partitioner**. Express the intent only:

```scala
// (a) if partitionBy fed a per-key aggregation/reduce, do it in the query:
val grouped = df.groupBy("key").agg(collect_list(struct("key", "value")))
// (b) if it was clustering for scan pruning, request it at the table:
// spark.sql("ALTER TABLE t CLUSTER BY (key)")
// SCOS: TODO - same-key-in-one-physical-partition guarantee and custom partitionFunc/numPartitions
// layout are lost (repartition-by-expression is a no-op hint).
```

### `repartitionAndSortWithinPartitions(...)` — [Partial]

```scala
// SCOS: [SPRKCNTSCL1500] repartitionAndSortWithinPartitions → repartition + sortWithinPartitions (intent only)
df.repartition(8, col("key")).sortWithinPartitions(col("key").asc)
// SCOS: TODO - repartition(n, col) is a no-op file-count hint; sortWithinPartitions degrades to a
// global ORDER BY; rows are NOT co-located by key. For durable co-location use a table CLUSTER BY (key).
```

### `collectAsMap()` — [Workaround]

```scala
// BEFORE: val m = rdd.collectAsMap()   // {"a":1,"b":2} (last-wins, order-dependent)
// AFTER:
// SCOS: [SPRKCNTSCL1500] collectAsMap → Map from df.select(k,v).collect()
val m = df.select("k", "v").collect().map(r => r.getAs[String]("k") -> r.getAs[Int]("v")).toMap
// deterministic last-wins (needs an ordering column 'ord'):
// val w = Window.partitionBy("k").orderBy(col("ord").desc)
// df.withColumn("_rn", row_number().over(w)).filter(col("_rn") === 1).select("k","v").collect()...
```

Matches Spark's equally-undefined last-wins ordering; bounded by driver memory.

### `countApprox(timeout, confidence)` / `countByValue()` — [Workaround]

```scala
// SCOS: [SPRKCNTSCL1500] countApprox → exact df.count() (time budget/confidence dropped)
val total = df.count()   // exact Long, not a BoundedDouble with a confidence range
// countByValue():
df.groupBy(df.columns.map(col): _*).count().collect()
```

### `countApproxDistinct(rsd)` — [Native]

```scala
// SCOS: [SPRKCNTSCL1500] countApproxDistinct → approx_count_distinct (Snowflake HLL)
val n = df.agg(approx_count_distinct("value")).first().getLong(0)
```

The `rsd` argument is **not** supported (a second argument raises); Snowflake's
fixed HLL precision is used. Ignores NULLs.

### `meanApprox(timeout, confidence)` — [Workaround]

```scala
// SCOS: [SPRKCNTSCL1500] meanApprox → exact avg (BoundedDouble/timeout/CI dropped)
val mean = df.agg(avg("amount")).first().getDouble(0)   // empty → NULL (coalesce/count guard if needed)
```

### `sumApprox(timeout, confidence)` — [Workaround]

```scala
// SCOS: [SPRKCNTSCL1500] sumApprox → exact SUM (BoundedDouble/timeout/CI dropped)
val total = df.agg(coalesce(sum("value"), lit(0.0))).first().getDouble(0)   // coalesce restores empty→0.0
```

### `saveAsObjectFile(path)` — [Partial]

```scala
// Only when the RDD held DataFrame-shaped rows and the round-trip persists within the job:
// SCOS: [SPRKCNTSCL1500] saveAsObjectFile → parquet/table round-trip (DataFrame-shaped rows only)
df.write.mode("overwrite").parquet("/tmp/objdata")   // or .saveAsTable("t")
val reloaded = spark.read.parquet("/tmp/objdata")    // or spark.table("t")
// SCOS: TODO - reading pre-existing external object files, or persisting arbitrary Java objects,
// has NO SCOS path (parquet/table needs a tabular schema; the sc.objectFile reader is no-equivalent).
```

### `getStorageLevel` / `getNumPartitions` / `partitions` — [Partial]

```scala
// SCOS: [SPRKCNTSCL1500] rdd.getStorageLevel → df.storageLevel (hardcoded — do NOT branch on it)
val lvl = df.storageLevel   // always StorageLevel(useDisk, useMemory); never NONE, never the real level
// SCOS: TODO - any logic that branches on the returned StorageLevel is a manual-review item
// (persist()/cache() also discard the requested level with only a warning). Partition count/metadata
// (getNumPartitions / partitions) is not exposed under Connect.
```

### `sc.parallelize(...).toDF(...)` / `rdd.toDF(schema)` — [Native]

```scala
// BEFORE: sc.parallelize(data).toDF(schema)
// AFTER: an EXPLICIT schema (no sampleRatio inference on SCOS)
// SCOS: [SPRKCNTSCL1500] rdd.toDF → Seq(...).toDF / spark.createDataFrame(rows.asJava, schema)
val df = Seq((1, "a"), (2, "b")).toDF("id", "name")                 // tuples/case classes via implicits
// Seq[Row]: spark.createDataFrame(rows.asJava, schema); primitives: Seq(1,2,3).map(Tuple1.apply)
```

If the source came from upstream RDD transforms, rewrite those to DataFrame ops
first; `toDF` is only the final schema-attach step.

### `toDebugString` — [Partial]

```scala
// BEFORE: println(rdd.toDebugString)   // RDD lineage chain
// AFTER: closest debugging intent is DataFrame.explain (Snowflake's simplified plan, NOT a lineage chain)
// SCOS: [SPRKCNTSCL1500] rdd.toDebugString → df.explain (intent-only substitute)
df.explain()
```

---

## Custom aggregate functions (UDAF) — [Hard gap · native path]

A Spark UDAF (`UserDefinedAggregateFunction`, or `Aggregator` + `functions.udaf`)
has **no supported execution path** on SCOS. **Avoid
`spark.udf().registerJavaUDAF(...)`** — the aggregate flag is ignored and it
registers as a **scalar** UDF (silent footgun). Do this instead, in order:

1. **Built-in aggregate** — most UDAFs reduce to `groupBy().agg(...)` (see above).
2. **Native Snowflake Java UDAF** — register once (persisted in the Snowflake
   account catalog), then call it through `SnowflakeSession` pass-through:
   `Aggregator<IN,BUF,OUT>` maps 1:1 —
   `zero→initialize, reduce→accumulate, merge→merge, finish→finish`. Call via
   `sf.sql(...)`, **not** `expr()`/`callUDF()` (both the function and the table
   live in Snowflake, so Spark can't resolve them). Handlers with dependencies:
   upload a JAR to a stage and use `IMPORTS='@stage/x.jar' HANDLER='pkg.Class'`.
3. **Keep on Spark** if the logic is genuinely non-SQL (external calls, ML
   inference, or iterative state that isn't initialize/accumulate/merge/finish).

```scala
// SCOS: TODO - Spark UDAF has no supported execution path; replace with a built-in
// groupBy().agg (see aggregate recipes), a native Snowflake Java UDAF, or keep on Spark.
// Do NOT use registerJavaUDAF — it silently registers as a scalar UDF.
```
