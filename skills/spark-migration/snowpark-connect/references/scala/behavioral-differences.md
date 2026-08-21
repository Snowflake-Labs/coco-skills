# SCOS Scala Behavioral Differences Reference

Behavioral differences between Spark and Snowflake that affect SCOS migrations.
These arise from Snowflake's SQL engine processing queries differently than Spark,
even when the Spark Connect API is preserved.

Fixes use standard Spark APIs — NOT SparkCompat helpers (those are Snowpark-specific).

---

## BD-1: Division by zero

**EWI:** SPRKCNTSCL5000 | **Severity:** Critical

**Spark:** `a / 0` returns NULL silently (non-ANSI mode).
**Snowflake:** `a / 0` throws `Division by zero` error.

**Fix:** Wrap with `when`:
```scala
when(col("b") =!= lit(0), col("a") / col("b")).otherwise(lit(null))
```

---

## BD-2: Type cast failure behavior

**EWI:** SPRKCNTSCL5001 | **Severity:** Critical

**Spark:** Failed casts return NULL silently. `cast("abc" as IntegerType)` → NULL.
**Snowflake:** Failed casts throw a runtime error.

**Fix:** Use `TRY_*` functions via `expr()`:
```scala
df.selectExpr("TRY_TO_NUMBER(col_name) as col_name")
df.selectExpr("TRY_TO_DATE(col_name) as col_name")
```

---

## BD-3: datediff parameter order reversed

**EWI:** SPRKCNTSCL5002 | **Severity:** Critical

**Spark:** `datediff(end, start)` — end first, start second.
**Snowflake:** `DATEDIFF('day', start, end)` — requires part, start first, end second.

**Fix:** Verify parameter order and add `'day'` part. Use `expr()` if needed:
```scala
expr("DATEDIFF('day', start_col, end_col)")
```

---

## BD-4: union() is position-based

**EWI:** SPRKCNTSCL5003 | **Severity:** Critical

**Spark:** `union()` is position-based — silently corrupts data if column orders differ.
**Snowflake:** Same behavior via Spark Connect.

**Fix:** Always use `unionByName()` instead of `union()`:
```scala
df1.unionByName(df2)
```

---

## BD-5: element_at indexing

**EWI:** SPRKCNTSCL5004 | **Severity:** Critical

**Spark:** `element_at` is 1-indexed. `element_at(arr, 1)` → first element.
**Snowflake:** May be 0-indexed depending on Spark Connect translation.

**Fix:** Verify indexing behavior. Add `// SCOS: TODO - verify element_at indexing` comment.

---

## BD-6: NULL handling in concat_ws

**EWI:** SPRKCNTSCL5005 | **Severity:** High

**Spark:** `concat_ws` skips null values. `concat_ws(",", "a", null, "b")` → `"a,b"`.
**Snowflake:** `concat_ws` returns NULL if any argument is null.

**Fix:** Wrap each argument with `coalesce`:
```scala
concat_ws(",", coalesce(col("a"), lit("")), coalesce(col("b"), lit("")))
```

---

## BD-7: ORDER BY null ordering

**EWI:** SPRKCNTSCL5006 | **Severity:** High

**Spark:** ASC → nulls last. DESC → nulls first.
**Snowflake:** ASC → nulls first. DESC → nulls last (opposite defaults).

**Fix:** Use explicit null ordering:
```scala
df.orderBy(col("x").asc_nulls_last, col("y").desc_nulls_first)
```

---

## BD-8: NaN handling

**EWI:** SPRKCNTSCL5007 | **Severity:** High

**Spark:** Supports NaN for float/double. `isnan()` available.
**Snowflake:** No NaN — float NaN operations return NULL.

**Fix:** Replace `isnan()` with `isNull()`:
```scala
when(col("x").isNull, lit(0)).otherwise(col("x"))
```

---

## BD-9: regexp_replace regex dialect

**EWI:** SPRKCNTSCL5008 | **Severity:** High

**Spark:** Java regex — supports `\d`, `\w`, lookahead/lookbehind.
**Snowflake:** POSIX extended regex — no lookahead/lookbehind.

**Fix:** Convert Java regex to POSIX:
- `\d` → `[0-9]`
- `\w` → `[a-zA-Z0-9_]`
- `(?=...)` → NOT SUPPORTED (refactor logic)

---

## BD-10: greatest/least null handling

**EWI:** SPRKCNTSCL5009 | **Severity:** High

**Spark:** `greatest`/`least` skip nulls. Returns null only if ALL args are null.
**Snowflake:** Returns NULL if ANY argument is null.

**Fix:** Wrap with `coalesce` or filter nulls:
```scala
greatest(coalesce(col("a"), lit(Long.MinValue)), coalesce(col("b"), lit(Long.MinValue)))
```

---

## BD-11: concat null handling

**EWI:** SPRKCNTSCL5010 | **Severity:** High

**Spark:** `concat` skips nulls.
**Snowflake:** `concat` returns NULL if any argument is null.

**Fix:** Same as BD-6 — wrap arguments with `coalesce`.

---

## BD-12: regexp_extract no-match behavior

**EWI:** SPRKCNTSCL5011 | **Severity:** High

**Spark:** Returns empty string `""` when no match.
**Snowflake:** Returns NULL when no match.

**Fix:** Wrap with `coalesce`:
```scala
coalesce(regexp_extract(col("s"), pattern, 1), lit(""))
```

---

## BD-13: first()/last() non-determinism

**EWI:** SPRKCNTSCL5012 | **Severity:** High

**Spark:** `first()`/`last()` return first/last value in group (order-dependent).
**Snowflake:** Non-deterministic without explicit ORDER BY.

**Fix:** Use `first()`/`last()` only with explicit window ordering, or add `// SCOS: TODO - verify ordering`.

---

## BD-14: round() banker's rounding

**EWI:** SPRKCNTSCL5013 | **Severity:** Medium

**Spark:** `round()` uses half-up rounding. `round(2.5)` → 3.
**Snowflake:** Uses half-even (banker's) rounding. `round(2.5)` → 2.

**Fix:** For exact Spark behavior, use conditional rounding:
```scala
when(col("x") % 1 === lit(0.5), ceil(col("x"))).otherwise(round(col("x")))
```

---

## BD-15: explode with null/empty arrays

**EWI:** SPRKCNTSCL5014 | **Severity:** Medium

**Spark:** `explode(null)` → no rows. `explode_outer(null)` → one row with null.
**Snowflake:** `explode_outer` requires `FLATTEN(OUTER => TRUE)`.

**Fix:** Use `expr()` for outer explode:
```scala
df.selectExpr("*, LATERAL FLATTEN(input => arr_col, OUTER => TRUE) as exploded")
```

---

## BD-16: String comparison and collation

**EWI:** SPRKCNTSCL5015 | **Severity:** Medium

**Spark:** Always binary (case-sensitive).
**Snowflake:** Depends on database/column collation — may be case-insensitive.

**Fix:** Use explicit case handling:
```scala
upper(col("name")) === lit("ABC")
```

---

## BD-17: months_between return type

**EWI:** SPRKCNTSCL5016 | **Severity:** Medium

**Spark:** Returns Double with fractional months.
**Snowflake:** Returns Integer (whole months only).

**Fix:** Use `DATEDIFF` with manual fractional calculation if precision needed:
```scala
expr("DATEDIFF('day', start_col, end_col) / 30.44")
```

---

## BD-18: Null-safe equality

**EWI:** SPRKCNTSCL5017 | **Severity:** Medium

**Spark:** `<=>` operator or `eqNullSafe`. `null <=> null` → true.
**Snowflake:** Use `EQUAL_NULL` via `expr()`.

**Fix:**
```scala
expr("EQUAL_NULL(a, b)")
```

---

## BD-19: Aggregation result column naming

**EWI:** SPRKCNTSCL5018 | **Severity:** Medium

**Spark:** Auto-generates `sum(revenue)`, `count(id)`.
**Snowflake:** Auto-generates `"SUM(REVENUE)"`, `"COUNT(ID)"` (upper-cased).

**Fix:** Always alias aggregation results:
```scala
df.groupBy("key").agg(sum(col("revenue")).alias("total_revenue"))
```

---

## BD-20: split regex vs literal delimiter

**EWI:** SPRKCNTSCL5019 | **Severity:** Medium

**Spark:** `split(col, pattern)` — pattern is Java regex.
**Snowflake:** `SPLIT(col, delimiter)` — delimiter is literal string.

**Fix:** Remove regex escaping for literal delimiters:
```scala
// Spark: split(col("s"), "\\.")  →  SCOS: split(col("s"), ".")
```

---

## BD-21: Integer division result type

**EWI:** SPRKCNTSCL5020 | **Severity:** Medium

**Spark:** `int / int` returns int (truncated).
**Snowflake:** `int / int` returns DECIMAL.

**Fix:** Wrap with `floor()` for truncated division:
```scala
floor(col("a") / col("b"))
```

---

## BD-22: Boolean casting from strings

**EWI:** SPRKCNTSCL5021 | **Severity:** Low

**Spark:** Accepts "true"/"false" (case-insensitive), "1"/"0".
**Snowflake:** Also accepts "yes"/"no", "on"/"off".

**Fix:** Use explicit matching if strict parsing needed.

---

## BD-23: substring(0) indexing

**EWI:** SPRKCNTSCL5022 | **Severity:** Low

**Spark:** `pos = 0` treated as `pos = 1`.
**Snowflake:** `pos = 0` returns empty string.

**Fix:** Change `pos = 0` to `pos = 1`.

---

## BD-24: groupBy result ordering

**EWI:** SPRKCNTSCL5023 | **Severity:** Low

**Spark:** Non-deterministic but often consistent.
**Snowflake:** Truly non-deterministic.

**Fix:** Always add explicit `orderBy` after aggregation.

---

## BD-25: Timestamp precision

**EWI:** SPRKCNTSCL5024 | **Severity:** Low

**Spark:** Microsecond precision.
**Snowflake:** Nanosecond precision.

**Fix:** Verify precision requirements. Configure `TIMESTAMP_OUTPUT_FORMAT` if needed.

---

## BD-26: approx_count_distinct precision

**EWI:** SPRKCNTSCL5025 | **Severity:** Low

**Spark:** HyperLogLog with configurable relative standard deviation.
**Snowflake:** HyperLogLog, precision not configurable.

**Fix:** Drop precision param. Use `COUNT(DISTINCT col)` if exact control needed.

---

## BD-27: date_format token differences

**EWI:** SPRKCNTSCL5026 | **Severity:** Medium

**Spark:** Java format tokens (`yyyy`, `MM`, `dd`, `HH`, `mm`, `ss`).
**Snowflake:** Different tokens (`YYYY`, `MM`, `DD`, `HH24`, `MI`, `SS`).

**Fix:** Translate format tokens:
| Spark | Snowflake |
|-------|-----------|
| `yyyy` | `YYYY` |
| `dd` | `DD` |
| `HH` | `HH24` |
| `hh` | `HH12` |
| `mm` | `MI` |
| `ss` | `SS` |
| `SSS` | `FF3` |

---

## BD-28: collect_list/collect_set ordering and nulls

**EWI:** SPRKCNTSCL5027 | **Severity:** Medium

**Spark:** `collect_list` preserves order, includes nulls. `collect_set` excludes nulls.
**Snowflake:** `array_agg` does not guarantee order, excludes nulls by default.

**Fix:** Add explicit ordering via `within_group` if needed. Handle nulls manually.

---

## BD-29: broadcast/repartition/coalesce (NEEDS INVESTIGATION)

**EWI:** SPRKCNTSCL5028 | **Severity:** Low

**Spark:** `broadcast()` hints, `repartition(n)`, `coalesce(n)` control partitioning.
**Snowflake/SCOS:** May be silently ignored or cause errors.

**Fix:** Remove distribution hints. Add `// SCOS: TODO - verify broadcast/repartition behavior`.
