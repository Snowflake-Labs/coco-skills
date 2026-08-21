# AWS Glue → SCOS Recipe Catalog — types and column expressions

Sub-file of [`glue-recipes.md`](glue-recipes.md), which is the **entry point**: read its
preamble, EWI table and recipe-routing table first. This file holds the `gluetypes` type system and the column-expression surface.

> **Validation scope.** Read it in the [index preamble](glue-recipes.md) — not restated here.
> In short: G1–G12 were validated on SCOS, and a recipe derived from source rather than run is
> **not live-verified** and says so at its own heading.

## G9 — `gluetypes` instances → `pyspark.sql.types` isinstance

Glue's `gluetypes` classes are singletons compared by value; the Spark
equivalents must be compared by type.

```python
# BEFORE
if field.dataType in [BooleanType(), IntegerType(), LongType(), NullType()]:
    ...

# AFTER
import pyspark.sql.types as T

if isinstance(field.dataType, (T.BooleanType, T.IntegerType, T.LongType, T.NullType)):
    ...
```

Note the instantiation drops: `BooleanType()` (an instance, in a list) becomes
`T.BooleanType` (the class, in an isinstance tuple).

