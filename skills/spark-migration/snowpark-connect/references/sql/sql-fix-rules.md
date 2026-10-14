# SCOS SQL Fix Rules — Shared Reference

> **Language-neutral guidance.** The rules below correct the **SQL string
> content**, not the host source file. The same incompatibilities apply whether
> the SQL lives inside a Scala `spark.sql("...")` call, a `.sql` file, or a
> PySpark `spark.sql("...")` call — the SQL engine is the same. Only the comment
> prefix and host-expression syntax differ; see the language-specific
> `references/sql-fix-rules.md` in each skill for "How to apply" details.

These rules cover SQL incompatibilities the **deterministic** SQL rewriter
(`scripts/rag/sql_rewrite.py`, run in Phase 0.6 for standalone `.sql` files and
in the Phase-0.5 mechanical-rewrite step for embedded `spark.sql("...")` strings)
does **not** auto-fix because they need schema or business-logic context. Each
finding for one of these arrives in `analysis.json` with `language: "sql"`,
`kind: "llm_only"`, and a `suggested_fixer_action`.

## Rules

### `detector:window_without_order_by` — window function missing ORDER BY
Spark raises `AnalysisException` for an unordered `ROW_NUMBER`/`RANK`/`LAG`/… ;
SCOS/Snowflake permits it and returns a nondeterministic result. **Not
auto-rewritten** — there is no safe syntactic fix. Synthesizing `ORDER BY` from
the `PARTITION BY` keys does *not* help: those keys are constant within a
partition, so peer rows stay tied and the order is still arbitrary. Add the
column(s) that define the *intended* order (an event timestamp, a surrogate id),
which needs domain knowledge of what the window is supposed to rank.
```sql
-- before
ROW_NUMBER() OVER (PARTITION BY customer_id)
-- after  (event_ts is the real ordering key, not customer_id)
ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY event_ts)
```

### `detector:multicolumn_not_in` — multi-column (tuple) NOT IN
Only the multi-column form `(a, b) NOT IN (...)` diverges (single-column `NOT IN`
shares the same three-valued NULL semantics in both engines). **Not
auto-rewritten** — a plain-equality `NOT EXISTS` is NOT equivalent: `NOT IN`
yields UNKNOWN and drops the row when any compared value is NULL, while an
equality `NOT EXISTS` returns TRUE and keeps it. Preserve the NULL handling:
confirm the probe/subquery columns are `NOT NULL` before using `NOT EXISTS`, or
filter NULLs / use `IS NOT DISTINCT FROM` so it matches Spark.
```sql
-- before
WHERE (a, b) NOT IN (SELECT a, b FROM u)
-- after  (only safe when u.a, u.b are NOT NULL — otherwise filter/IS NOT DISTINCT FROM)
WHERE NOT EXISTS (SELECT 1 FROM u WHERE u.a = t.a AND u.b = t.b)
```

### `detector:lca_alias_collision` — output alias shadows a GROUP BY / base column
Snowflake rejects `ambiguous column name`; Spark and Databricks resolve by context
(a `GROUP BY`/`ORDER BY` name binds to the column, not the same-named `SELECT`
alias). Rename the output alias so it no longer matches a grouped/base column, and
update downstream references.
```sql
-- before
SELECT SUM(v) AS k FROM t GROUP BY k
-- after
SELECT SUM(v) AS k_total FROM t GROUP BY k
```

### `detector:in_subquery_in_on_clause` — IN (SELECT …) in a LEFT JOIN ON
SCOS collapses the LEFT OUTER join to INNER. Move the predicate to a WHERE clause
or a derived table so non-matching left rows are preserved.
```sql
-- before
FROM l LEFT JOIN r ON l.id = r.id AND l.k IN (SELECT k FROM allow)
-- after
FROM l LEFT JOIN r ON l.id = r.id
WHERE l.k IN (SELECT k FROM allow)   -- or pre-filter via a derived table
```

### `detector:lateral_view_unsupported_generator` — unsupported LATERAL VIEW generator
SCOS supports only `FLATTEN` / `SPLIT_TO_TABLE` as LATERAL VIEW generators.
Rewrite `EXPLODE`/`POSEXPLODE`/`INLINE`/`STACK` using the DataFrame API
(`.explode()` / `.posexplode()`) or a supported table function; qualify the
generated columns.

### `detector:multi_generator_select` — more than one generator in a SELECT
SCOS allows at most one table-valued generator per SELECT. Chain successive
`.select(explode(...))` calls, or LATERAL FLATTEN one array at a time.

### `detector:transform_using_unsupported` — Hive script transform
`SELECT TRANSFORM(...) USING 'cmd'` has no SCOS equivalent. Reimplement the
external-script logic as a Snowflake UDF/UDTF or a DataFrame transformation.

### `detector:tablesample_unsupported` — TABLESAMPLE
Rejected by SCOS. Use DataFrame `.sample(fraction)` on the read, or Snowflake
`SAMPLE` via a `SnowflakeSession` passthrough query — not `TABLESAMPLE` inside
`spark.sql(...)`.

### `detector:insert_overwrite_partition` — INSERT OVERWRITE … PARTITION
On a Snowflake (FDN) table this needs
`spark.conf.set('snowpark.connect.sql.emulatePartitionOverwritesForSnowflakeTables','true')`
before the statement, or migrate the target to an Iceberg table. (This fix is on
the host-language side — set the conf; the SQL itself is unchanged.)

### `behavioral:sql.merge-not-matched-by-source` — 3-arm MERGE
SCOS does not support `WHEN NOT MATCHED BY SOURCE`. Split into a `DELETE … WHERE
NOT EXISTS (SELECT 1 FROM <source> WHERE <on>)` followed by a two-arm MERGE
(`MATCHED` + `NOT MATCHED`).

### `behavioral:sql.lateral-view-outer` — LATERAL VIEW OUTER
The `OUTER` flag is silently dropped, losing empty/NULL-array parent rows.
Re-introduce them with a `UNION ALL` of the non-matching rows:
```sql
SELECT ... FROM base LATERAL VIEW explode(arr) t AS c
UNION ALL
SELECT ..., NULL AS c FROM base WHERE arr IS NULL OR size(arr) = 0
```

### `behavioral:sql.with-recursive` — recursive CTE
Not supported. Rewrite as a bounded loop accumulating DataFrame unions, or run a
native Snowflake recursive CTE via `SnowflakeSession(spark).sql(...)`.

### `behavioral:sql.cluster-by` / `behavioral:sql.distribute-by`
`CLUSTER BY` → `df.orderBy()` (global) or `df.write.partitionBy()` at write time.
`DISTRIBUTE BY` → remove, or `df.repartition()` — note SCOS treats repartition as
a hint, so the shuffle guarantee is not preserved.

### `detector:window_case_aggregate` — CASE expression as a window aggregate
`CASE WHEN … THEN MIN(x) ELSE ANY_VALUE(x) END OVER (…)` — Snowflake's window
resolution can't pick the aggregate via a conditional branch. Lift the CASE out
of the window: compute each branch's window in its own column, then CASE over the
windowed results in an outer projection.

### `detector:cast_to_interval` — CAST(… AS INTERVAL …)
`CAST(-122 AS INTERVAL YEAR TO MONTH)` fails in Snowflake (Spark casts implicitly).
Build the interval explicitly from the number (`n * INTERVAL '1' MONTH` /
`make_interval`) instead of casting a number to an interval type.

### `detector:json_path_wildcard` — `[*]` array wildcard in a JSON path
`get_json_object(val, '$.store.book[*].category')` — Snowflake JSON paths accept
only numeric indices. Rewrite with `LATERAL FLATTEN(INPUT => <array>)` and project
the target field per element (or a UDF).

### `detector:correlated_subquery_unsupported` — correlated scalar subquery with set-op / GROUP BY
A correlated scalar subquery whose body has `UNION`/`INTERSECT`/`EXCEPT` or a
`GROUP BY` is rejected by Snowflake (error 002031). **Candidate** (the correlation
check is heuristic). Decorrelate: rewrite as a LEFT JOIN to a pre-aggregated /
pre-UNIONed derived table keyed on the correlation column.

### `detector:identifier_dynamic` — dynamic `IDENTIFIER(...)`
`IDENTIFIER('a' || 'b')(...)` (dynamic TVF) or `IDENTIFIER(...)` building an object
name in DDL — unsupported in Snowflake. Resolve the name in the host language and
emit static SQL, or use a stored procedure / `EXECUTE IMMEDIATE`.

### `detector:map_unsupported_key` — `map()` with a non-VARCHAR/integer key
`map(1.23, 'a')` / `map(true, 'b')` — Snowflake MAP keys must be VARCHAR or
`NUMBER(p,0)`. Cast the key to STRING (or an integer code): `map(CAST(k AS STRING), v)`.
Note this changes the key type — verify downstream lookups.

### `detector:corr_distinct` — `CORR(DISTINCT x, y)`
Snowflake silently drops the `DISTINCT`, giving a slightly different correlation
than Spark. Deduplicate explicitly: `SELECT corr(x, y) FROM (SELECT DISTINCT x, y
FROM t)`. (Detected lexically — sqlglot cannot parse `CORR(DISTINCT …)`.)

## Function-level gaps (kb_rules.json, dual-surface)

Most SQL incompatibilities are **function-level**: a function behaves differently
or is unsupported on Snowflake, and the same gap applies to `df.select(fn(...))`
*and* `SELECT fn(...)`. These come from `kb_rules.json` and appear in
`analysis.json` as `language:"sql"` rows whose `rule_id` starts with `gaps:` /
`behavioral:` and whose function name is in the call. They are easy to overlook
because they often have **no inline `-- SCOS:` marker** — locate them by the
row's `file`+`lines`.

**General rule:** apply the row's `suggested_fixer_action` (falling back to its
`note`). There is no single syntactic transform — the fix depends on the
function. Common ones:

- **`percentile_approx` / `percentile_disc` / `percentile_cont` / `avg` on a
  DATE/TIMESTAMP argument** → cast the argument to epoch seconds
  (`unix_timestamp(col)` / `CAST(col AS DOUBLE)`), compute, then cast back.
- **`collect_list` / `array_agg` in a window** → Snowflake ignores the window
  `ORDER BY` direction for accumulation; sort the array explicitly
  (`array_sort(...)`) or restructure so order is deterministic.
- **`to_char` with `PR` / `S` / `MI` format specifiers** → rewrite the sign/format
  handling explicitly (`CASE`/concatenation); those specifiers are unsupported.
- **`to_date` / `to_timestamp` / `date_format` with unsupported pattern letters**
  (`D`/`DD`/`DDD`, `G`/`Q`/`F`/`K`/`V`/`z`/`O`, `[...]`) → use supported letters or
  reformat in two steps.
- **`mode(expr, true)`** (deterministic tie-break) → not supported; drop the 2nd
  arg and accept non-deterministic ties, or compute the mode explicitly.
- **`laplace_cdf`, `bloom_filter_agg`, two-arg `hll_union`** (missing) → reimplement
  via a UDF or a supported equivalent.
- **`rand()` / `hash()` / statistical fns** where the gap is value reproducibility
  / precision → usually a documented behavioral difference, not a rewrite; annotate.

### `::` cast shorthand inside `spark.sql(...)`
Snowflake's `expr::type` cast shorthand (e.g. `'5'::int`, `col::date`) is **not
valid Spark SQL** — the Spark parser inside `spark.sql(...)`/`selectExpr(...)`
raises `ParseException [PARSE_SYNTAX_ERROR] Syntax error at or near ':'`. This
appears in hand-written or SMA-emitted SQL that used Snowflake syntax. Rewrite to
the standard form `CAST(expr AS type)`. *(verified on SCOS 1.32.0.)*

When the function isn't listed here, follow the finding's `note` —
`kb_rules.json` carries a `fix` for each.
