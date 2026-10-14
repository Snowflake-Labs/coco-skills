# AWS Glue → SCOS Recipe Catalog (Python)

AWS Glue ETL jobs are PySpark jobs wrapped in a Glue-only API surface
(`GlueContext`, `DynamicFrame`, `awsglue.transforms`, `gluetypes`, job
bookmarks). None of that surface exists in Snowpark Connect, but the PySpark
underneath it does — so a Glue migration is almost entirely a matter of
**unwrapping** Glue back to plain DataFrame code, then repointing I/O at
Snowflake.

**G1–G12** were validated on SCOS against a real Glue 5.0 / Spark 3.5 workload
(three silver→Snowflake CDC jobs plus a ~4000-line shared helper module), with
transform parity diffed against live Glue output. Any recipe added to this catalog
later must state its own validation status at its heading; it does **not** inherit
this one: a recipe derived from source rather than run is **not live-verified**.

> **Read this whole file before converting a Glue job.** Two recipes — **G2**
> (identifier case) and **G5** (null predicate semantics) — describe failures
> that produce **silently wrong data** rather than an error. A migration that
> compiles, runs, and reports success can still be dropping columns and rows if
> those two are missed.

## EWI codes

| Code | Family | Recipes |
|------|--------|---------|
| `SPRKCNTPY3600` | Glue entry point (`GlueContext`, `Job`, `.spark_session`) | G1 |
| `SPRKCNTPY3601` | Glue job arguments (`getResolvedOptions`) | G1 |
| `SPRKCNTPY3602` | Glue Data Catalog read | G2 |
| `SPRKCNTPY3603` | `ApplyMapping` | G4 |
| `SPRKCNTPY3604` | `Filter.apply` null semantics | G5 |
| `SPRKCNTPY3605` | DynamicFrame lifecycle + transforms | G3, G6, G7, G10 |
| `SPRKCNTPY3606` | Job bookmarks | G8 |
| `SPRKCNTPY3607` | `gluetypes` comparisons | G9 |
| `SPRKCNTPY3608` | Snowflake connector writeback | G11 |
| `SPRKCNTPY3609` | Glue Data Catalog write | G11 |

Full code catalog: [`ewi-codes.md`](ewi-codes.md).

## Phase 0.5 recipe coverage

These Glue patterns are rewritten **deterministically** by LibCST recipes before
the analyzer or the LLM fixer sees the code. Do not re-do their work by hand;
do finish the parts marked *annotate*.

| Recipe id | Covers | Status |
|---|---|---|
| `glue_applymapping_to_select_rewrite` | G4 | **rewrite — done** for literal mapping lists; annotates otherwise |
| `glue_catalog_io_to_table_rewrite` | G2, G11 (catalog sink) | **rewrite — done** incl. the lowercase normalization |
| `glue_connector_writeback_todo_annotate` | G11 (preactions/postactions) | annotate — fixer must author the staged MERGE |
| `glue_filter_apply_null_semantics_annotate` | G5 | annotate — **deliberately never auto-rewritten** (see G5) |
| `glue_getresolvedoptions_to_argparse_rewrite` | G1 (args) | **rewrite — done** for literal key lists; annotates otherwise |
| `glue_gluetypes_isinstance_rewrite` | G9 | **rewrite — done** |
| `glue_session_bootstrap_rewrite` | G1 (session), G8 (`job.*` removal) | **rewrite — done** |
| `glue_transforms_to_dataframe_rewrite` | G3, G6, G7, G10 | **rewrite — done** for the supported transforms; annotates otherwise |

---

## Recipe routing

G1, G2 and G5 are **in this file** — the entry point plus the two recipes whose failure mode is
silently wrong data. Everything else lives in a sub-file:

| Recipe | Subject | Where |
|---|---|---|
| **G1** | Entry point: `GlueContext` / `Job` / `getResolvedOptions` | this file |
| **G2** | Catalog read → `read.table` + **lowercase normalization** | this file |
| **G5** | `Filter.apply` null-operation trap | this file |
| G3 | `ResolveChoice` → no-op or explicit cast | [transforms](glue-recipes-transforms.md) |
| G4 | `ApplyMapping` → `select` + `cast` + `alias` | [transforms](glue-recipes-transforms.md) |
| G6 | DynamicFrame ↔ DataFrame lifecycle | [transforms](glue-recipes-transforms.md) |
| G7 | `DynamicFrameCollection` custom transforms | [transforms](glue-recipes-transforms.md) |
| G10 | `DropFields` / `SelectFields` / `RenameField` | [transforms](glue-recipes-transforms.md) |
| G9 | `gluetypes` instances → `pyspark.sql.types` | [types](glue-recipes-types.md) |
| G8 | Job bookmarks → external stage + Stream | [I/O](glue-recipes-io.md) |
| G11 | Connector writeback (preactions/postactions MERGE) | [I/O](glue-recipes-io.md) |
| G12 | `ThreadPoolExecutor` over a shared session | [I/O](glue-recipes-io.md) |

*A reference of the form "`glue-recipes.md` recipe G3" still resolves: this table routes it.*

---

## G1 — Entry point: `GlueContext` / `Job` / `getResolvedOptions`

A Glue job's bootstrap is three coupled objects. SCOS replaces all of them with
one session.

```python
# BEFORE (Glue)
import sys
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext

args = getResolvedOptions(sys.argv, ["JOB_NAME", "INPUT_DATABASE", "TARGET_TABLE"])
sc = SparkContext.getOrCreate()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args["JOB_NAME"], args)
# ... pipeline ...
job.commit()
```

```python
# AFTER (SCOS)
import argparse
from snowflake import snowpark_connect

_parser = argparse.ArgumentParser()
for _key in ("JOB_NAME", "INPUT_DATABASE", "TARGET_TABLE"):
    _parser.add_argument(f"--{_key}")
args = vars(_parser.parse_known_args()[0])

# Warehouse / database / schema / role come from the connection, not from code.
spark = snowpark_connect.init_spark_session()
# ... pipeline ...
# job.init(...) / job.commit() have NO SCOS equivalent and are deleted.
# If the job relied on bookmarks for incrementality, see G8 — deleting
# job.commit() alone silently turns an incremental job into a full reprocess.
```

Rules:

- `SparkContext.getOrCreate()` + `GlueContext(sc)` + `Job(glueContext)` collapse
  into the single `init_spark_session()` call.
- `<glueContext>.spark_session` must lose the `.spark_session` hop — after the
  rewrite the variable already *is* the session, so leaving it raises
  `AttributeError`.
- Keep `args` a **dict keyed by the bare name** (`args["JOB_NAME"]`, not
  `args.JOB_NAME`) so every downstream lookup keeps working unchanged. That is
  why the recipe emits `vars(...)` rather than the argparse namespace.
- `parse_known_args()` (not `parse_args()`) — Glue passes extra
  runtime parameters that would otherwise abort the job.

## G2 — Catalog read → `spark.read.table` + **lowercase normalization**

```python
# BEFORE
DyF = glueContext.create_dynamic_frame.from_catalog(
    database=db, table_name=tbl, transformation_ctx="ctx",
    additional_options={...})
```

```python
# AFTER
df = spark.read.table(f"{db}.{tbl}")
# CRITICAL: the Glue Data Catalog exposes LOWERCASE column names; a native
# Snowflake read returns UPPERCASE. Normalize so downstream case-sensitive
# logic behaves identically.
df = df.toDF(*[c.lower() for c in df.columns])
```

> ⚠️ **The lowercase line is not cosmetic.** Without it, any case-sensitive
> downstream logic — `field.name in target_columns` membership tests,
> hand-built mapping dicts, `create_*_mappings` helpers — matches nothing and
> **silently drops the affected columns**. In the validated workload this lost
> the primary key (`document_id` vs `DOCUMENT_ID`) with no error raised.
> Verify migrated column counts against the source, do not just check that the
> job runs.

`transformation_ctx=` is the bookmark handle and has no equivalent — drop the
kwarg and handle incrementality per G8. `additional_options` / `format_options`
are Glue-reader-specific and drop too; if one of them was doing real work (e.g.
a `mergeSchema`), reproduce it explicitly.

## G5 — `Filter.apply` null-operation trap (parity-critical)

**This is the single highest-value Glue recipe. A naive port silently loses
rows.** Glue runs a *Python* predicate row-wise (Python truthiness); Spark
evaluates a *Column* expression under SQL three-valued logic. On a nullable
column they disagree.

```python
# BEFORE (Glue) — Python: None != "d" is True, so null-op rows are KEPT
Upsert = Filter.apply(frame=DyF, f=lambda row: row["op"] != "d")
Delete = Filter.apply(frame=DyF, f=lambda row: row["op"] == "d")
```

```python
# AFTER (SCOS)
# A naive F.col("op") != "d" yields NULL for a null op, so the row is DROPPED.
# Restore Glue semantics with an explicit isNull() guard:
upsert_df = df.filter(F.col("op").isNull() | (F.col("op") != "d"))

# The positive predicate needs NO guard: == 'd' is NULL for a null op, which is
# falsy in a filter, so the row is excluded — exactly as in Glue.
delete_df = df.filter(F.col("op") == "d")
```

The asymmetry is the whole point:

| Glue predicate | Naive Spark port | Correct Spark port |
|---|---|---|
| `row["c"] != X` | ❌ drops null rows | `F.col("c").isNull() \| (F.col("c") != X)` |
| `row["c"] == X` | ✅ already correct | `F.col("c") == X` |
| `not row["c"]` | ❌ drops null rows | `F.col("c").isNull() \| (~F.col("c"))` |
| `row["c"] > X` | ✅ already correct | `F.col("c") > X` |

Rule of thumb: **any negated or `!=` predicate on a nullable column needs an
`isNull()` guard; positive predicates do not.**

Because recovering the correct guard requires knowing whether the column is
nullable and what the predicate *meant*, this is deliberately **annotate-only**
in Phase 0.5 — the recipe flags it with `SPRKCNTPY3604` and the fixer must
author the rewrite. Validate the row counts of each branch against the source.

## Connection / environment note

`snowpark_connect.init_spark_session()` honors
**`SNOWFLAKE_DEFAULT_CONNECTION_NAME`**. It does *not* honor
`SNOWFLAKE_CONNECTION_NAME` — setting only that variable falls through to the
default connection, which in a typical dev setup uses
`authenticator=externalbrowser` and will **hang a non-interactive job on a
browser SSO prompt** (and can surface confusingly as
`TABLE_OR_VIEW_NOT_FOUND`, because the browser identity lands in a different
account context). Always set `SNOWFLAKE_DEFAULT_CONNECTION_NAME` explicitly for
automated Glue-replacement runs.

## Migration checklist

Work top to bottom; the ordering matters because later steps assume the session
and I/O are already converted.

- [ ] **G1** Bootstrap collapsed to `init_spark_session()`; `.spark_session`
      hop removed; `job.init` / `job.commit` deleted; args still a dict.
- [ ] **G2** Every catalog read repointed **and** followed by the lowercase
      normalization. Compare migrated column counts against the source.
- [ ] **G8** If bookmarks were in use, incrementality re-established — or the
      switch to full reprocessing explicitly accepted and documented.
- [ ] **G4** Every `ApplyMapping` is a `select` (projection preserved), with
      Glue→Spark type names mapped.
- [ ] **G5** Every negated / `!=` predicate carries an `isNull()` guard. Diff
      per-branch row counts against the source.
- [ ] **G3/G6/G7/G10** All DynamicFrame wrappers and transforms gone; no
      `gc` parameters left; `dyf.schema()` → `df.schema`.
- [ ] **G9** `gluetypes` value comparisons converted to `isinstance`.
- [ ] **G11** Connector writes rewritten as `saveAsTable` + `spark.sql` MERGE,
      upserts before deletes, delete MERGE guarded by `tableExists`.
- [ ] **G12** Thread pools serialized, or per-thread temp tables verified.
- [ ] No `awsglue` import survives anywhere in the output.
