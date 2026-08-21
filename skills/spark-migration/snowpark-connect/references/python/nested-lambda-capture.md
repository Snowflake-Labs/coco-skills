# Nested-Lambda Variable Capture

The full procedure for Rule 32 of `migrate-pyspark-to-snowpark-connect/references/fix-rules.md`. Read this before rewriting any finding from `parity:higher-order-functions#nested-lambda-variable-capture`.

## The failure

**An inner lambda cannot read an *enclosing* lambda's variable** — `AnalysisException: Cannot resolve variable '<v>' within lambda function` (`SNOWPARK CONNECT ERROR CODE: 4001`). SCOS keeps the parameters of the lambda it is currently compiling in a **single slot that entering a nested lambda replaces rather than pushes**, so by the time the inner body is compiled the outer parameter no longer exists. A lambda in SCOS can therefore see exactly two things: **its own parameters**, and **the parent dataframe's columns**.

## The fix

**This is fixable, and the failure is narrower than it looks.** Nesting higher-order functions is fully supported, and an inner lambda may freely read parent dataframe columns and literals. Only the *capture* fails. **Fix: pair the captured value into the inner collection in the outer lambda's body, then read it off the inner lambda's own parameter.** Done completely — pairing plus the unwrap the inner higher-order function needs — this only re-plumbs where the value comes from, so no assertion or expected value moves.

```python
# BEFORE — the inner lambda reads `g`, a parameter of the outer lambda.
df.select(F.transform("groups", lambda g: F.aggregate(g, F.lit(0), lambda acc, v: acc + v * F.size(g))))

# AFTER
# SCOS: [SPRKCNTPY5400-Fixed] a nested lambda cannot read an enclosing lambda's
# variable, so F.size(g) is paired into the inner array and read off the merge
# lambda's own parameter.
df.select(
    F.transform(
        "groups",
        lambda g: F.aggregate(
            F.zip_with(
                g,
                F.array_repeat(F.size(g), F.size(g)),
                lambda _e, _c: F.struct(_e.alias("e"), _c.alias("c")),
            ),
            F.lit(0),
            lambda acc, p: acc + p["e"] * p["c"],
        ),
    )
)
```

The pairing is always the same shape: replace the inner collection `X` with `F.zip_with(X, F.array_repeat(<captured>, F.size(X)), lambda _e, _c: F.struct(_e.alias("e"), _c.alias("c")))`, then rewrite the inner lambda's body so the element is `p["e"]` and the formerly-captured value is `p["c"]`. `<captured>` may be any expression of the outer parameter (the parameter itself, `F.size(...)` of it, an array). The field names are arbitrary; what matters is that they are **explicit**. For a **map**-valued inner collection, rebuild it around the values: `F.map_from_arrays(F.map_keys(m), F.zip_with(F.map_values(m), F.array_repeat(<captured>, F.size(F.map_values(m))), lambda _v, _c: F.struct(_v.alias("v"), _c.alias("c"))))`.

**`F.array_repeat`'s count must be exactly `F.size(X)`.** Pair against any other length and `F.zip_with` silently NULL-pads to the longer of the two on **both** engines rather than failing, so the result gains extra NULL-valued elements with no error and a Spark-vs-SCOS comparison will not catch it.

**Use `F.zip_with` with an explicitly aliased `F.struct` — never `F.arrays_zip`.** `F.arrays_zip` produces generated field names that SCOS cannot resolve when read off a lambda parameter; it raises `Duplicate field name 'namedlambdavariable()'` un-aliased and `invalid identifier` even with `.alias()` on both arguments — and it raises at the **top level with no capture at all**, so it is not a substitute here.

## Unwrap when the inner HOF returns its input collection

**Then check what the inner HOF returns, because pairing changes the element type.** The rewrite above is complete only for the higher-order functions whose result is built from the *lambda's return value*. For the ones that return the *collection they were given*, the struct leaks into the result and the output type silently changes — those need one more step to unwrap it:

| Inner HOF | Result is built from | What the pairing needs |
|---|---|---|
| `transform`, `aggregate`/`reduce`, `exists`, `forall`, `zip_with`, `transform_values` | the lambda's return value | nothing more — the recipe above is complete |
| `filter`, `array_sort` | the input **array** | wrap the call: `F.transform(F.filter(<paired>, lambda p: <pred on p["e"], p["c"]>), lambda p: p["e"])` |
| `map_filter` | the input **map** | wrap the call: `F.transform_values(F.map_filter(<paired>, lambda k, p: <pred>), lambda k, p: p["v"])` |
| `transform_keys` | lambda keys, **input values** | wrap the call: `F.transform_values(F.transform_keys(<paired>, lambda k, p: <newkey>), lambda k, p: p["v"])` |
| `map_zip_with` | the lambda's return value, but over **two** maps and a 3-argument lambda | pair only the map whose value the capture replaces, and read it off that argument (`v1["v"]` or `v2["v"]`); leave the other map alone |

Skipping the unwrap does not raise on either engine — it returns `array<struct<e,c>>` where the workload expected `array<int>`, so it surfaces later as a schema or assertion mismatch far from the edit. Always re-check the migrated site's schema against the original.

## What has been verified on SCOS

Verified on SCOS end-to-end for `transform`, `aggregate`/`reduce`, `filter` (with the unwrap), `exists`, `forall`, `zip_with` and `transform_values`, and across the cases that usually break a pairing rewrite: empty inner collections, NULL elements, a NULL inner collection, three levels of nesting, and array-valued captured expressions. Values matched open-source Spark exactly in every case. `transform_keys`, `map_filter`, `map_zip_with` and `array_sort` share the identical capture mechanism and are detected, but their rewrites have **not** been probed on SCOS — apply the table above and verify the schema before trusting the result.

## When this rule does NOT apply

- **The inner lambda reads a parent dataframe column or a literal** (`F.col("x")`, `F.lit(2)`) — that already works on SCOS. Do not rewrite it.
- **The outer variable appears only in the inner call's *collection* argument** (`lambda g: F.aggregate(F.array_repeat(F.size(g), 2), ...)`) — that argument is evaluated in the outer scope, so there is no capture.
- **The inner lambda's own parameter shadows the outer name** — same name, different variable, no capture.

## The one form with no rewrite: the index overload

**The *index* overload of `F.transform` / `F.filter`.** This is specifically `F.transform(col, lambda x, i: ...)` and `F.filter(col, lambda x, i: ...)`, where the second lambda parameter is the **element index** — SCOS materializes that index through a non-SQL UDF, so nested inside *any* lambda it raises `Non-SQL UDF is not supported inside lambda expression` **even with no capture** (and `F.filter(col, lambda x, i: ...)` fails on SCOS at the top level too). The pairing above cannot help: there is no inner one-argument lambda to move the value into.

**Do not confuse this with a two-argument lambda in general** — almost every HOF in this family takes one, and they are all fine: `aggregate`'s merge is `(acc, x)`, `zip_with` is `(x, y)`, `transform_values` / `map_filter` / `transform_keys` are `(k, v)`, `array_sort`'s comparator is `(left, right)`. Only the index overload of `transform` and `filter` is affected. Do not synthesize the index with `F.sequence(F.lit(0), F.size(X) - 1)` either: on an **empty** collection Spark infers a step of `-1` and yields `[0, -1]`, which silently pads the result — and it is wrong the same way on both engines, so a Spark-vs-SCOS comparison will not catch it. Annotate these sites, and state the real reason:

```python
# SCOS: TODO - [SPRKCNTPY5400-Error] the (element, index) overload of F.transform
# is rejected inside a nested lambda on SCOS, so the element-index cannot be
# recovered here; rewrite the logic to not need the index, or run this step
# outside the higher-order function.
```

## Do not misattribute the failure

**Never annotate a plain capture with "there is no SQL-level workaround" or "non-SQL UDFs are not supported inside lambdas".** Both are wrong for this failure: no UDF is involved, and the rewrite above is a working SQL-level workaround. If a `Non-SQL UDF` note fires on a lambda that contains no UDF call, it is a false positive — check for the `Cannot resolve variable` mechanism instead.

## Coverage boundary: the detector only sees the DataFrame API

The gate needs a Python AST to tell an inner lambda's parameters from an enclosing one's, so HOF lambdas written as SQL text inside `spark.sql("... transform(g, z -> aggregate(z, 0, (a, v) -> a + v * size(z))) ...")` produce no finding. The underlying limitation is Snowflake SQL's, not the client's, so the SQL form fails the same way at runtime — if a workload puts higher-order functions in SQL strings, read them by hand for this shape.
