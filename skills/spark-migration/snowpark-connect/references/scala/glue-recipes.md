# AWS Glue → SCOS Recipe Catalog (Scala)

AWS Glue ETL Scala jobs wrap PySpark's Glue counterpart behind the
`com.amazonaws.services.glue.*` SDK (`GlueContext`, `DynamicFrame`,
`com.amazonaws.services.glue.transforms`, job bookmarks). None of that
surface exists in Snowpark Connect, but the Spark DataFrame API underneath
it does — so a Glue Scala migration is almost entirely **unwrapping** the
Glue SDK back to plain DataFrame code, then repointing I/O at Snowflake.

Every "After" pattern below mirrors the validated Python recipe catalog
(`references/python/glue-recipes.md`) in Scala idioms. The behavioral rules
and parity-critical traps are identical across languages; the API surface
names and syntax differ.

> **Read this whole file before converting a Glue Scala job.** Two recipes —
> **G2** (identifier case) and **G5** (null predicate semantics) — describe
> failures that produce **silently wrong data** rather than an error. A
> migration that compiles, runs, and reports success can still be dropping
> columns and rows if those two are missed.

## EWI codes

| Code | Family | Recipes |
|------|--------|---------|
| `SPRKCNTSCL3600` | Glue entry point (`GlueContext`, `Job`, `getSparkSession`) | G1 |
| `SPRKCNTSCL3601` | Glue job arguments (`GlueArgParser.getResolvedOptions`) | G1 |
| `SPRKCNTSCL3602` | Glue Data Catalog read | G2 |
| `SPRKCNTSCL3603` | `ApplyMapping` | G4 |
| `SPRKCNTSCL3604` | `Filter.apply` null semantics | G5 |
| `SPRKCNTSCL3605` | DynamicFrame lifecycle + transforms | G3, G6, G7, G10 |
| `SPRKCNTSCL3606` | Job bookmarks | G8 |
| `SPRKCNTSCL3608` | Snowflake connector writeback | G11 |
| `SPRKCNTSCL3609` | Glue Data Catalog write | G11 |

Full code catalog: [`ewi-codes.md`](ewi-codes.md).

## Phase 0.5 recipe coverage

There are **no Scalafix Glue recipes** in Phase 0.5 — the deterministic
LibCST recipes from the Python path (`glue_session_bootstrap_rewrite`,
`glue_catalog_io_to_table_rewrite`, etc.) are Python-only. The fixer handles
all Glue patterns directly per Rule 37 in `fix-rules.md`. This means:

- No Glue site is pre-annotated or pre-rewritten before the LLM fixer runs.
- The fixer is responsible for **all** mechanical rewrites and all
  judgment-required completions (G5 null-safe predicates, G8 bookmarks,
  G11 connector writeback).
- Check `migration_state.json :: recipe_edits` — any Glue line with a
  `recipe_id` in the `scalafix:` namespace is a coincidental match, not a
  Glue-specific recipe; handle it per the normal recipe-routing rules.

---

## G1 — Entry point: `GlueContext` / `Job` / `GlueArgParser`

A Glue Scala job's bootstrap is three coupled objects. SCOS replaces all of
them with one session.

```scala
// BEFORE (Glue Scala)
import com.amazonaws.services.glue.GlueContext
import com.amazonaws.services.glue.util.{Job, GlueArgParser}
import org.apache.spark.SparkContext
import scala.collection.JavaConverters._

val sysArgs = GlueArgParser.getResolvedOptions(args, Array("JOB_NAME", "INPUT_DATABASE", "TARGET_TABLE"))
val sc = new SparkContext()
val glueContext = new GlueContext(sc)
val spark = glueContext.getSparkSession()
Job.init(sysArgs("JOB_NAME"), glueContext, sysArgs.asJava)
// ... pipeline ...
Job.commit()
```

```scala
// AFTER (SCOS)
import com.snowflake.snowpark_connect.client.SnowparkConnectSession

// Parse job parameters from command-line args (mirrors the Python argparse path).
// Warehouse / database / schema / role come from the connection, not from code.
val sysArgs: Map[String, String] = args
  .sliding(2, 2)
  .collect { case Array(k, v) if k.startsWith("--") => k.stripPrefix("--") -> v }
  .toMap

val spark = SnowparkConnectSession.builder().getOrCreate()
// Job.init(...) / Job.commit() have NO SCOS equivalent and are deleted.
// If the job relied on bookmarks for incrementality, see G8 — deleting
// Job.commit() alone silently turns an incremental job into a full reprocess.
```

Rules:

- `new SparkContext()` + `new GlueContext(sc)` + `Job.init(...)` collapse into
  the single `SnowparkConnectSession.builder().getOrCreate()` call.
- `glueContext.getSparkSession()` must lose the `.getSparkSession()` hop — after
  the rewrite the variable already *is* the session, so leaving it raises a
  compilation error.
- Keep `sysArgs` a **Map keyed by the bare name** (`sysArgs("JOB_NAME")`) so
  every downstream lookup keeps working unchanged.
- `GlueArgParser.getResolvedOptions(args, Array(...))` returns a
  `java.util.Map[String, String]`; calling `.asScala.toMap` produces the Scala
  equivalent. The simple sliding-window parse above avoids the Glue dependency
  entirely and works for `--KEY VALUE` pairs passed by Glue or SCOS runners.

## G2 — Catalog read → `spark.read.table` + **lowercase normalization**

```scala
// BEFORE
val dyf = glueContext.getCatalogSource(
  database = db,
  tableName = tbl,
  transformationContext = "ctx",
  additionalOptions = JsonOptions(Map("mergeSchema" -> "true"))
).getDynamicFrame()
```

```scala
// AFTER
var df = spark.read.table(s"$db.$tbl")
// CRITICAL: the Glue Data Catalog exposes LOWERCASE column names; a native
// Snowflake read returns UPPERCASE. Normalize so downstream case-sensitive
// logic behaves identically.
df = df.toDF(df.columns.map(_.toLowerCase): _*)
```

> ⚠️ **The lowercase line is not cosmetic.** Without it, any case-sensitive
> downstream logic — `schema.fields.find(_.name == "document_id")`, hand-built
> mapping `Map`s, `df.columns.contains("key")` membership tests — matches
> nothing and **silently drops the affected columns**. In the validated Python
> workload this lost the primary key (`document_id` vs `DOCUMENT_ID`) with no
> error raised. Verify migrated column counts against the source.

`transformationContext` is the bookmark handle and has no equivalent — drop
the parameter and handle incrementality per G8. `additionalOptions` /
`JsonOptions(...)` are Glue-reader-specific and drop too; if one was doing
real work (e.g. `mergeSchema`), reproduce it explicitly.

## G3 — `ResolveChoice` → no-op or explicit cast

DynamicFrame *choice types* only arise because a DynamicFrame defers schema
resolution. Reading a typed Snowflake table cannot produce one, so
`ResolveChoice` with `"match_catalog"` has nothing to resolve.

```scala
// BEFORE
val resolved = ResolveChoice.apply(frame = dyf, choice = "match_catalog")

// AFTER — delete the call entirely
val resolved = df
```

If one specific column genuinely needs coercion, be explicit:
`df.withColumn(c, col(c).cast(<type>))`.

## G4 — `ApplyMapping` → `select` + `cast` + `alias`

`ApplyMapping` does three things at once and **all three** must survive the
port: it *projects* (only mapped columns remain — every unmapped column is
dropped), it *renames*, and it *casts*. A failed cast yields `null` in both
Glue and Spark — the semantics line up.

```scala
// BEFORE
val mappings = Seq(
  ("src_id",   "string", "id",   "bigint"),
  ("src_name", "string", "name", "string")
)
val out = ApplyMapping.apply(frame = dyf, mappings = mappings)
```

```scala
// AFTER
import org.apache.spark.sql.functions.col

val CAST_MAP = Map(
  "bigint" -> "long", "integer" -> "int", "string" -> "string",
  "boolean" -> "boolean", "double" -> "double", "float" -> "float",
  "short" -> "short", "byte" -> "byte", "decimal" -> "decimal",
  "timestamp" -> "timestamp", "date" -> "date", "null" -> "string"
)
val out = df.select(mappings.map { case (src, _, tgt, ttype) =>
  col(s"`$src`").cast(CAST_MAP.getOrElse(ttype, ttype)).as(tgt)
}: _*)
```

Notes:

- The backticks around `src` matter — Glue column names routinely contain
  dots and spaces, which Spark would otherwise parse as a nested-field path.
- Glue type names are not all Spark type names: `bigint`→`long`,
  `integer`→`int`, and `null`→`string` are the ones that actually bite.
- Because this is a `select`, the projection semantics come for free. Do
  **not** rewrite it as a chain of `withColumnRenamed` + `withColumn` — that
  keeps the unmapped columns and silently changes the output schema.

## G5 — `Filter.apply` null-operation trap (parity-critical)

**This is the single highest-value Glue recipe. A naive port silently loses
rows.** Glue runs a Scala predicate per `DynamicRecord` (JVM truthiness on
`Option`); Spark evaluates a `Column` expression under SQL three-valued
logic. On a nullable column they disagree.

```scala
// BEFORE (Glue Scala) — Option semantics: None != "d" is evaluated via
// .exists(_ != "d") which returns false for None, KEEPING the null-op row.
val upsert = Filter.apply(frame = dyf, f = (r: DynamicRecord) => r.getField("op").exists(_ != "d"))
val delete  = Filter.apply(frame = dyf, f = (r: DynamicRecord) => r.getField("op").contains("d"))
```

```scala
// AFTER (SCOS)
import org.apache.spark.sql.functions.col

// A naive col("op") =!= "d" yields NULL for a null op (SQL three-valued
// logic), so the row is DROPPED. Restore Glue semantics with isNull guard:
val upsertDf = df.filter(col("op").isNull || col("op") =!= "d")

// The positive predicate needs NO guard: === "d" is NULL for a null op,
// which is falsy in a filter, so the row is excluded — exactly as in Glue.
val deleteDf = df.filter(col("op") === "d")
```

The asymmetry is the whole point:

| Glue predicate (Scala) | Naive Spark port | Correct Spark port |
|---|---|---|
| `.exists(_ != X)` | ❌ drops null rows | `col("c").isNull \|\| col("c") =!= X` |
| `.contains(X)` | ✅ already correct | `col("c") === X` |
| `.exists(v => !v)` | ❌ drops null rows | `col("c").isNull \|\| !col("c")` |
| `.exists(_ > X)` | ✅ already correct | `col("c") > X` |

Rule of thumb: **any negated or `.exists(_ != ...)` predicate on a nullable
column needs an `isNull` guard; positive predicates do not.**

Because recovering the correct guard requires knowing whether the column is
nullable and what the predicate *meant*, this must be done by the fixer —
do not leave the `Filter.apply` call live. Validate branch row counts against
the source.

## G6 — DynamicFrame ↔ DataFrame lifecycle → drop the wrappers

`dyf.toDF()` and `DynamicFrame(df, gc)` (or `DynamicFrame.fromDF(df, gc, "ctx")`)
are a pure round-trip with no SCOS equivalent. Remove them and pass
DataFrames directly.

```scala
// BEFORE
def enrich(gc: GlueContext, dyf: DynamicFrame): DynamicFrame = {
  val df = dyf.toDF()
  val enriched = df.withColumn("loaded_at", current_timestamp())
  DynamicFrame(enriched, gc)
}

// AFTER
def enrich(df: DataFrame): DataFrame = {
  df.withColumn("loaded_at", current_timestamp())
}
```

Also:

- Drop the `gc` / `glueContext` parameter from every helper signature it
  threaded through.
- `dyf.schema()` (a method call on DynamicFrame) → `df.schema` (a property
  on DataFrame — no parentheses). Forgetting the change raises
  `error: value schema is not a member of org.apache.spark.sql.Dataset`.
- When lowering a signature that spans multiple lines, replace the **entire**
  signature starting at the `def` token. Editing from the second parameter
  line onward leaves a dangling `def enrich(gc: GlueContext,` above the new
  one — two consecutive `def` tokens and a compilation error.

## G7 — `DynamicFrameCollection` custom transforms → plain df-in/df-out

A Glue Custom Transform node receives and returns a single-frame collection.
Collapse the ceremony.

```scala
// BEFORE
def myXform(gc: GlueContext, dfc: DynamicFrameCollection, cols: Seq[String]): DynamicFrameCollection = {
  val df = dfc.select("CustomTransform").toDF()
  // ... transform ...
  DynamicFrameCollection(Map("CustomTransform" -> DynamicFrame(df, gc)), gc)
}

// AFTER
def myXform(df: DataFrame, cols: Seq[String]): DataFrame = {
  // same transform, native
  df
}
```

Prefer native column expressions over UDFs where equivalent — the Glue idiom
`if (before == null || before.getField(c) == null) after.getField(c) else before.getField(c)`
is exactly `coalesce(col("before")(c), col("after")(c))`, and the native form
both runs faster and avoids the UDF dependency-packaging problem entirely.

## G8 — Job bookmarks → external-stage directory table + Stream

Glue job bookmarks (`transformationContext` handle plus `Job.init()` /
`Job.commit()`) track which S3 files a job has already consumed. There is no
SCOS equivalent, and **deleting the calls silently converts an incremental job
into one that reprocesses everything on every run.**

Replacement pattern:

1. Create an **external stage** over the same S3 prefix, with a
   **directory table**.
2. Create a **Stream** on that stage — it yields newly-landed files with
   `metadata$action = 'INSERT'`.
3. Consume the stream inside a **DML transaction** so the offset advances only
   on success.
4. `COPY INTO` the silver table from the streamed file list, then read the
   table normally.

Validated behavior: baseline (pre-existing) files are ignored, and only files
that land after stream creation appear as `INSERT`. This gives the same
at-least-once, advance-on-success semantics the bookmark provided.

## G9 — Not applicable (Scala)

The Python `awsglue.gluetypes` singleton-comparison pattern (`SPRKCNTPY3607`)
has **no Scala equivalent**. The Glue Scala SDK uses the standard JVM /
Spark type system (`org.apache.spark.sql.types.*`), and `instanceof` on Spark
type objects is already the correct Scala idiom. No rewrite needed.

## G10 — `DropFields` / `SelectFields` / `RenameField` → native DataFrame ops

| Glue Scala transform | SCOS equivalent |
|---|---|
| `DropFields.apply(frame=f, paths=Seq("a","b"))` | `df.drop("a", "b")` |
| `SelectFields.apply(frame=f, paths=Seq("a","b"))` | `df.select("a", "b")` |
| `RenameField.apply(frame=f, oldName="a", newName="b")` | `df.withColumnRenamed("a", "b")` |
| `Join.apply(frame1=a, frame2=b, keys1=Seq(...), keys2=Seq(...))` | `a.join(b, cond, "inner")` |
| `SplitFields` / `SelectFromCollection` | plain `select` on the one frame you want |

`Relationalize` and `Unbox` have no one-liner equivalent — they flatten nested
structures into multiple frames. Convert explicitly with
`select` + `explode` + `col("s.*")` and check the resulting frame count.

## G11 — Connector writeback (temp table + preactions/postactions MERGE)

The Snowflake Spark connector (`net.snowflake.spark.snowflake`) is **not
usable inside SCOS** — it would round-trip Snowflake→Snowflake, and the
connector-only `preactions` / `postactions` hooks have no execution path. The
CDC "stage into a temp table, then MERGE in postactions" pattern is very common
in Glue Scala jobs and must be rewritten.

```scala
// BEFORE — Snowflake connector pattern (common in Glue CDC jobs)
df.write
  .format("net.snowflake.spark.snowflake")
  .options(sfOptions ++ Map(
    "dbtable" -> s"${tgt}_TEMP",
    "preactions" -> s"CREATE OR REPLACE TEMPORARY TABLE ${tgt}_TEMP LIKE $tgt",
    "postactions" -> s"""
      MERGE INTO $tgt t USING ${tgt}_TEMP s
        ON t.id = s.id
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *;
      DROP TABLE IF EXISTS ${tgt}_TEMP
    """
  ))
  .mode("overwrite")
  .save()
```

```scala
// AFTER (SCOS) — saveAsTable + spark.sql MERGE
df.write.mode("overwrite").saveAsTable(s"${tgt}_TEMP")

spark.sql(s"""
  CREATE TABLE IF NOT EXISTS $tgt AS
    SELECT * FROM `${tgt}_TEMP` WHERE 1=0
""")

// Process upserts before deletes (after dedup a PK is only ever in one branch).
spark.sql(s"""
  MERGE INTO $tgt t USING `${tgt}_TEMP` s
    ON t.id = s.id
    WHEN MATCHED THEN UPDATE SET *
    WHEN NOT MATCHED THEN INSERT *
""")

// Guard the delete MERGE with tableExists for delete-only first runs.
if (spark.catalog.tableExists(tgt)) {
  spark.sql(s"""
    MERGE INTO $tgt t USING `${tgt}_TEMP` s
      ON t.id = s.id
      WHEN MATCHED THEN DELETE
  """)
}

spark.sql(s"DROP TABLE IF EXISTS `${tgt}_TEMP`")
```

Rules:

- `spark.sql` on SCOS accepts **Spark SQL only**: unquoted identifiers,
  backtick quoting (not double quotes), `WHEN MATCHED THEN UPDATE SET * /
  INSERT *`, and no `DELETE ... USING` standalone syntax.
- **Process upserts before deletes** (after dedup a PK is only ever in one
  branch).
- **Guard the delete MERGE with `spark.catalog.tableExists(tgt)`** for
  delete-only first runs.
- Glue Data Catalog writes (`glueContext.getSinkWithFormat(...).writeDynamicFrame(dyf)`)
  also fall under this recipe; rewrite as `df.write.mode(...).saveAsTable(tgt)`.

## G12 — Thread pools

A single SCOS session is shared across threads. Default to serial processing;
if parallelizing with `scala.concurrent.Future` or `java.util.concurrent`,
each table must write to its **own** temp table.
