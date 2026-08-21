# AWS Glue → SCOS Recipe Catalog — I/O, bookmarks and concurrency

Sub-file of [`glue-recipes.md`](glue-recipes.md), which is the **entry point**: read its
preamble, EWI table and recipe-routing table first. This file holds catalog/connector I/O, bookmark replacement and session concurrency.

> **Validation scope.** Read it in the [index preamble](glue-recipes.md) — not restated here.
> In short: G1–G12 were validated on SCOS, and a recipe derived from source rather than run is
> **not live-verified** and says so at its own heading.

## G8 — Job bookmarks → external-stage directory table + Stream

Glue job bookmarks (the `transformation_ctx=` handle plus `job.init()` /
`job.commit()`) track which S3 files a job has already consumed. There is no
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

## G11 — Connector writeback (temp table + preactions/postactions MERGE)

The Snowflake Spark connector (`net.snowflake.spark.snowflake`) is **not usable
inside SCOS** — it would round-trip Snowflake→Snowflake, and the connector-only
`preactions` / `postactions` hooks have no execution path. The
Debezium/CDC "stage into a temp table, then MERGE in postactions" pattern is
very common in Glue jobs and must be rewritten.

```python
# BEFORE
df.write.format("net.snowflake.spark.snowflake").options(**sfOpts) \
  .option("dbtable", f'{schema}."{T}_TEMP"') \
  .option("preactions",  "CREATE TABLE IF NOT EXISTS ...; CREATE TRANSIENT TABLE ..._TEMP ...;") \
  .option("postactions", 'MERGE INTO ... USING ..._TEMP AS "s" ON ... ; DROP TABLE ..._TEMP;') \
  .mode("append").save()
```

```python
# AFTER (SCOS) — the same two-step stage + MERGE, actually runnable
staged.write.mode("overwrite").saveAsTable(f"{schema}.{T}_TEMP")
spark.sql(f"CREATE TABLE IF NOT EXISTS {schema}.{T} AS SELECT * FROM {schema}.{T}_TEMP WHERE 1=0")
spark.sql(
    f"MERGE INTO {schema}.{T} AS t USING {schema}.{T}_TEMP AS s "
    f"ON t.{pk} = s.{pk} "
    f"WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *"
)
spark.sql(f"DROP TABLE IF EXISTS {schema}.{T}_TEMP")
# delete path: WHEN MATCHED THEN DELETE
```

Hard-won ordering and syntax rules:

- **Process upserts before deletes.** After dedup there is exactly one row per
  PK, so a given PK only ever appears in one branch; doing upserts first keeps
  the two MERGEs independent.
- **Guard the delete MERGE** with `spark.catalog.tableExists(tgt)` — on a
  delete-only first run the target table does not exist yet.
- **Unqualified identifiers and Spark MERGE syntax only.** `spark.sql` on SCOS
  accepts **Spark SQL**, not Snowflake SQL. In Spark SQL `"..."` is a *string
  literal* (identifiers use backticks), and `DELETE ... USING` is
  Snowflake/Postgres-only. `MERGE ... WHEN MATCHED THEN UPDATE SET * / INSERT *`
  with unquoted identifiers is valid Spark and works. Likewise
  `current_account()` is a Snowflake builtin (not a Spark function) and
  `USE WAREHOUSE` is not Spark grammar — the warehouse comes from the
  connection. `USE DATABASE` / `USE SCHEMA` *are* valid Spark and do work.

## G12 — `ThreadPoolExecutor` over a shared session

A single SCOS Spark Connect session is shared across worker threads. Default to
**serial** execution (`max_workers=1`) when porting a Glue job's thread pool. If
you do parallelize, ensure each table writes to its **own** temp table — the
G11 pattern uses a `_TEMP` name derived from the target, so two threads writing
the same target will corrupt each other's staging data.

