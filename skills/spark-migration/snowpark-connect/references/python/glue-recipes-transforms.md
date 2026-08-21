# AWS Glue → SCOS Recipe Catalog — transforms

Sub-file of [`glue-recipes.md`](glue-recipes.md), which is the **entry point**: read its
preamble, EWI table and recipe-routing table first. This file holds the row-shaping DynamicFrame transforms.

> **Validation scope.** Read it in the [index preamble](glue-recipes.md) — not restated here.
> In short: G1–G12 were validated on SCOS, and a recipe derived from source rather than run is
> **not live-verified** and says so at its own heading.

## G3 — `ResolveChoice` → no-op or explicit cast

DynamicFrame *choice types* only arise because a DynamicFrame defers schema
resolution. Reading a typed Snowflake table cannot produce one, so
`ResolveChoice(choice="match_catalog")` has nothing to resolve.

```python
# BEFORE
Resolved = ResolveChoice.apply(frame=DyF, choice="match_catalog")

# AFTER — delete the call entirely
Resolved = df
```

If one specific column genuinely needs coercion, be explicit:
`df.withColumn(c, F.col(c).cast(<type>))`.

## G4 — `ApplyMapping` → `select` + `cast` + `alias`

`ApplyMapping` does three things at once and **all three** must survive the
port: it *projects* (only mapped columns remain — every unmapped column is
dropped), it *renames*, and it *casts*. A failed Glue cast yields `null` rather
than raising, which Spark's `cast` also does — so the semantics line up.

```python
# BEFORE
Out = ApplyMapping.apply(frame=DyF, mappings=[
    ("src_id",   "string", "id",    "bigint"),
    ("src_name", "string", "name",  "string"),
])
```

```python
# AFTER
_CAST = {"bigint": "long", "integer": "int", "string": "string",
         "boolean": "boolean", "double": "double", "float": "float",
         "short": "short", "byte": "byte", "decimal": "decimal",
         "timestamp": "timestamp", "date": "date", "null": "string"}

out = df.select(*[
    F.col(f"`{src}`").cast(_CAST.get(ttype, ttype)).alias(tgt)
    for (src, _stype, tgt, ttype) in mappings
])
```

Notes:

- The backticks around `src` matter — Glue column names routinely contain dots
  and spaces, which Spark would otherwise parse as a nested-field path.
- Glue type names are not all Spark type names; `bigint`→`long`,
  `integer`→`int` and `null`→`string` are the ones that actually bite.
- Because this is a `select`, the projection semantics come for free. Do **not**
  rewrite it as a chain of `withColumnRenamed` + `withColumn` — that keeps the
  unmapped columns and silently changes the output schema.

## G6 — DynamicFrame ↔ DataFrame lifecycle → drop the wrappers

`X.toDF()` and `DynamicFrame.fromDF(df, gc, "ctx")` are a pure round-trip with
no SCOS equivalent. Remove them and pass DataFrames directly.

```python
# BEFORE
def enrich(gc: GlueContext, dyf: DynamicFrame) -> DynamicFrame:
    df = dyf.toDF()
    df = df.withColumn("loaded_at", F.current_timestamp())
    return DynamicFrame.fromDF(df, gc, "enriched")

# AFTER
def enrich(df: "DataFrame") -> "DataFrame":
    return df.withColumn("loaded_at", F.current_timestamp())
```

Also:

- Drop the `gc` / `glueContext` parameter from every helper signature it
  threaded through.
- `dyf.schema().fields` → `df.schema.fields` — a **method** on DynamicFrame, a
  **property** on DataFrame. Forgetting the parens change raises
  `TypeError: 'StructType' object is not callable`.
- When lowering a signature that spans multiple lines, replace the **entire**
  signature starting at the `def` token. Editing from the second line onward
  leaves a dangling `def enrich(gc: GlueContext,` above the new one — two
  consecutive `def` lines and a `SyntaxError`.

## G7 — `DynamicFrameCollection` custom transforms → plain df-in/df-out

A Glue "Custom Transform" node receives and returns a single-frame collection
keyed `"CustomTransform"`. Collapse the ceremony.

```python
# BEFORE
def my_xform(gc, dfc, cols):
    df = dfc.select(list(dfc.keys())[0]).toDF()
    ...  # transform
    return DynamicFrameCollection({"CustomTransform": DynamicFrame.fromDF(df, gc)}, gc)

# AFTER
def my_xform(df, cols):
    ...  # same transform, native
    return df
```

While you are in here, prefer native column expressions over Python UDFs where
they are equivalent — the Glue idiom
`after[c] if before is None or before[c] is None else before[c]` is exactly
`F.coalesce(F.col("before")[c], F.col("after")[c])`, and the native form both
runs faster and avoids the UDF dependency-packaging problem entirely.

## G10 — `DropFields` / `SelectFields` / `RenameField` → native DataFrame ops

| Glue transform | SCOS equivalent |
|---|---|
| `DropFields.apply(frame=f, paths=["a", "b"])` | `df.drop("a", "b")` |
| `SelectFields.apply(frame=f, paths=["a", "b"])` | `df.select("a", "b")` |
| `RenameField.apply(frame=f, old_name="a", new_name="b")` | `df.withColumnRenamed("a", "b")` |
| `Join.apply(frame1=a, frame2=b, keys1=[...], keys2=[...])` | `a.join(b, on=..., how="inner")` |
| `SplitFields` / `SelectFromCollection` | plain `select` on the one frame you want |
| `Map.apply(frame=f, f=fn)` | native column expressions; a UDF only as a last resort |

`Relationalize` and `Unbox` have no one-liner equivalent — they flatten nested
structures into multiple frames. Convert explicitly with
`select` + `explode` + `F.col("s.*")` and check the resulting frame count.

