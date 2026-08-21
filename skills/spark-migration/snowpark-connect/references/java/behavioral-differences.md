# SCOS Java Behavioral Differences Reference

Behavioral differences between Spark Java API and Snowflake that affect SCOS migrations.
These are the same engine-level differences as Scala — Spark's Java API and Scala API
compile to the same JVM bytecode and hit the same Snowflake SQL engine.

Fixes use standard Spark Java APIs — NOT SparkCompat helpers.

See `references/scala/behavioral-differences.md` for the full list. The entries below
highlight Java-specific syntax for the most common fixes.

---

## BD-1: Division by zero

**EWI:** SPRKCNTSCL5000 | **Severity:** Critical

**Spark:** `a / 0` returns NULL silently (non-ANSI mode).
**Snowflake:** `a / 0` throws `Division by zero` error.

**Fix (Java):**
```java
functions.when(functions.col("b").notEqual(functions.lit(0)),
    functions.col("a").divide(functions.col("b")))
    .otherwise(functions.lit(null));
```

---

## BD-3: datediff parameter order reversed

**EWI:** SPRKCNTSCL5002 | **Severity:** Critical

**Spark:** `datediff(end, start)` — end first, start second.
**Snowflake:** `DATEDIFF('day', start, end)` — requires part, start first, end second.

**Fix (Java):**
```java
df.selectExpr("DATEDIFF('day', start_col, end_col)");
```

---

## BD-4: union() is position-based

**EWI:** SPRKCNTSCL5003 | **Severity:** Critical

**Fix (Java):**
```java
df1.unionByName(df2);
```

---

## BD-8: NaN handling

**EWI:** SPRKCNTSCL5007 | **Severity:** High

**Fix (Java):**
```java
// Replace functions.isnan(col("x")) with:
functions.col("x").isNull();
```

---

## BD-9: regexp_replace regex dialect

**EWI:** SPRKCNTSCL5008 | **Severity:** High

**Fix (Java):** Convert `\d` → `[0-9]`, `\w` → `[a-zA-Z0-9_]`; remove lookaheads.

---

## BD-12: regexp_extract no-match behavior

**EWI:** SPRKCNTSCL5011 | **Severity:** High

**Fix (Java):**
```java
functions.coalesce(functions.regexp_extract(functions.col("s"), pattern, 1), functions.lit(""));
```

---

## BD-20: split regex vs literal delimiter

**EWI:** SPRKCNTSCL5019 | **Severity:** Medium

**Fix (Java):** Remove Java regex escaping for literal delimiters:
```java
// Spark: functions.split(col("s"), "\\.")  →  SCOS: functions.split(col("s"), ".")
```

---

## BD-27: date_format token differences

**EWI:** SPRKCNTSCL5026 | **Severity:** Medium

**Fix (Java):** Same token translation as Scala:
| Spark token | Snowflake token |
|-------------|----------------|
| `yyyy` | `YYYY` |
| `dd` | `DD` |
| `HH` | `HH24` |
| `mm` | `MI` |
| `ss` | `SS` |
| `SSS` | `FF3` |

---

## BD-30: Integral type widening

**EWI:** SPRKCNTSCL5029 | **Severity:** Medium

**Spark:** `ByteType`, `ShortType`, `IntegerType` arithmetic may stay narrow; `handleIntegralOverflow` defaults to `false` (silently wraps).
**Snowflake:** Many numeric operations promote narrower integer types to `LongType` automatically.

**Fix (Java):**
```java
// Cast explicitly before arithmetic to guarantee the expected type:
functions.col("small_col").cast(DataTypes.LongType).plus(functions.col("other"));
```

---

## BD-31: STRUCT field ordering

**EWI:** SPRKCNTSCL5030 | **Severity:** Medium

**Spark:** Struct fields are ordered by insertion order.
**Snowflake:** Struct (OBJECT) fields are sorted alphabetically by key.

**Fix (Java):**
```java
// Select struct fields by name after any struct operation rather than relying on positional index:
df.select(functions.col("my_struct").getField("z_field"),
          functions.col("my_struct").getField("a_field"));
```

---

## BD-32: Timestamp type mapping (TIMESTAMP_TYPE_MAPPING)

**EWI:** SPRKCNTSCL5031 | **Severity:** Medium

**Spark:** `TimestampType` always maps to UTC-based microsecond timestamps.
**Snowflake:** `TimestampType` cast behavior depends on the `TIMESTAMP_TYPE_MAPPING` account/session parameter (default may be `TIMESTAMP_NTZ` or `TIMESTAMP_LTZ` depending on account configuration), which can silently shift values.

Note: BD-25 / SPRKCNTSCL5024 covers the related precision difference (microseconds vs nanoseconds).

**Fix (Java):**
```java
// Set session timezone explicitly to ensure consistent NTZ behavior:
session.conf().set("spark.sql.session.timeZone", "UTC");
// Or use selectExpr with an explicit TIMESTAMP_NTZ cast:
df.selectExpr("CAST(ts_col AS TIMESTAMP_NTZ) AS ts_col");
```

---

## BD-33: Parquet pre-Gregorian timestamp rebase

**EWI:** SPRKCNTSCL5032 | **Severity:** Medium

**Spark:** Parquet timestamps before 1582-10-15 (Julian/Gregorian switchover) are rebased automatically during read/write.
**Snowflake:** No automatic rebase — timestamps before 1582-10-15 read from Parquet may be silently corrupted by up to 10 days.

**Fix (Java):**
```java
// Validate that source data contains no pre-Gregorian timestamps before migration:
df.filter(functions.col("ts_col").lt(functions.lit("1582-10-15").cast(DataTypes.DateType)))
  .count(); // Must be 0 or handle explicitly
```

---

For the complete list of behavioral differences and their Scala equivalents,
see `references/scala/behavioral-differences.md`. The Java API produces identical
logical plans to the Scala API, so all entries apply equally.

---

## BD-IO-1: Text writes require exactly one string column

**EWI:** SPRKCNTSCL1000 | **Severity:** High

**Spark:** `df.write().text("path")` works on any single-column DataFrame, coercing the value to string.
**Snowflake/SCOS:** Text writes require the column to already be of `StringType`. Any non-string column will fail at write time.

**Fix (Java):**
```java
// Cast the target column to StringType before writing as text:
df.select(functions.col("my_col").cast(DataTypes.StringType))
  .write().mode(SaveMode.Overwrite).text("@my_stage/output/");
```

> Also note: `ignore` write mode is not supported for CSV, JSON, or text writes in Snowpark Connect.
> Replace `SaveMode.Ignore` with `SaveMode.Overwrite` or `SaveMode.Append`, or check existence before writing.

---

## BD-IO-2: Partition filter pushdown does not prune file reads

**EWI:** SPRKCNTSCL1000 | **Severity:** Medium

**Spark:** When reading a partitioned directory (e.g. `path/year=2023/month=01/`), a `filter` on the partition key prunes the file scan — only matching partition directories are opened.
**Snowflake/SCOS:** Partition filters on stage-based reads do **not** automatically prune file access. All files matching the base path are read; the filter runs as a post-scan predicate. For large partitioned datasets this causes full-scan I/O instead of partition-level pruning.

**Fix (Java):**
```java
// Instead of relying on partition pruning, use explicit stage paths per partition:
Dataset<Row> df = spark.read().parquet("@my_stage/data/year=2023/month=01/");
// Or load via a Snowflake table with WHERE clause to leverage table micro-partition pruning:
Dataset<Row> df2 = spark.sql("SELECT * FROM my_table WHERE year = 2023 AND month = 1");
```
