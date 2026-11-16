# Comparison SQL Patterns

Parameterized SQL templates for baseline comparison. Inspired by the `show_diff()` stored procedure approach — PK-based FULL OUTER JOIN with column-level mismatch detection and float tolerance.

## Placeholders

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `{{MART_TABLE}}` | Fully qualified mart table or view | `ANALYTICS_DB.MARTS.CUSTOMER_MART` |
| `{{GOLDEN_TABLE}}` | Fully qualified baseline table or view | `ANALYTICS_DB.STAGING.CUSTOMER_MART_GOLDEN_VW` |
| `{{PK_JOIN}}` | JOIN condition on primary keys | `m.CUSTOMER_ID = g.CUSTOMER_ID AND m.AS_OF_DATE = g.AS_OF_DATE` |
| `{{PK_COLUMNS}}` | Comma-separated PK column list | `CUSTOMER_ID, AS_OF_DATE` |
| `{{COLUMN_NAME}}` | Single column to compare | `TOTAL_REVENUE` |
| `{{MART_COL}}` | Mart-side column name | `TOTAL_REVENUE` |
| `{{GOLDEN_COL}}` | Golden-side column (may differ from mart) | `REV_AMT` |
| `{{MART_FILTER}}` | Optional mart WHERE clause | `WHERE SOURCE_SYSTEM = 'ERP_A'` |
| `{{GOLDEN_FILTER}}` | Optional golden WHERE clause | `WHERE EFFECTIVE_DATE >= '2024-01-01'` |
| `{{FLOAT_TOLERANCE_PCT}}` | Percent tolerance for float comparison | `1` |
| `{{GROUP_BY_COLUMNS}}` | Optional GROUP BY dimensions for row count and aggregate breakdowns | `REGION, PRODUCT_LINE` |
| `{{GROUP_BY_JOIN}}` | JOIN condition on GROUP BY columns for Pattern 1.3b | `m_agg.REGION = g_agg.REGION AND m_agg.PRODUCT_LINE = g_agg.PRODUCT_LINE` |
| `{{MART_FILTER_ALIAS}}` | Mart filter as a table alias qualifier (for use after FULL OUTER JOIN) | `-- no filter` or inject as CTE |
| `{{GOLDEN_FILTER_ALIAS}}` | Golden filter as a table alias qualifier (for use after FULL OUTER JOIN) | `-- no filter` or inject as CTE |
| `{{UNION_OF_COLUMN_DIFFS}}` | UNION ALL of all column-level diff SELECTs from Pattern 2.2 | `SELECT ... UNION ALL SELECT ...` |
| `{{NUM_COLUMNS}}` | Integer count of columns checked in the comparison | `12` |
| `{{NUM_COLUMNS_FAILED}}` | Integer count of columns with at least one mismatch | `3` |
| `{{PK_COL}}` | Single primary key column (2.1b, 1.4b) | `CUSTOMER_ID` |
| `{{SEGMENT_COL}}` | Dimension used to summarize unmatched keys (2.1b) | `REGION_CODE` |

---

## Phase 1: Aggregate Comparison

### 1.1 Row Count Comparison

```sql
SELECT
    'MART' AS source,
    COUNT(*) AS row_count
FROM {{MART_TABLE}} {{MART_FILTER}}
UNION ALL
SELECT
    'GOLDEN' AS source,
    COUNT(*) AS row_count
FROM {{GOLDEN_TABLE}} {{GOLDEN_FILTER}};
```

### 1.2 Row Count by Dimension

Only run if `{{GROUP_BY_COLUMNS}}` were provided by the analyst:

```sql
SELECT
    'MART' AS source,
    {{GROUP_BY_COLUMNS}},
    COUNT(*) AS row_count
FROM {{MART_TABLE}} {{MART_FILTER}}
GROUP BY {{GROUP_BY_COLUMNS}}

UNION ALL

SELECT
    'GOLDEN' AS source,
    {{GROUP_BY_COLUMNS}},
    COUNT(*) AS row_count
FROM {{GOLDEN_TABLE}} {{GOLDEN_FILTER}}
GROUP BY {{GROUP_BY_COLUMNS}}

ORDER BY {{GROUP_BY_COLUMNS}}, source;
```

### 1.3 Aggregate Comparison — Numeric Columns

Run **one batched query** covering all numeric column pairs (UNION ALL of one row per column). Skip identifier and primary-key columns — sums and averages of IDs are meaningless. Includes `_DIFF` and `_DIFF_PCT` for all metrics (sum, avg, min, max). The single-column block below is repeated per column:

```sql
SELECT
    '{{MART_COL}}' AS column_name,
    m_agg.total_rows AS mart_rows,
    g_agg.total_rows AS golden_rows,
    m_agg.sum_val AS mart_sum,
    g_agg.sum_val AS golden_sum,
    m_agg.sum_val - g_agg.sum_val AS sum_diff,
    CASE WHEN g_agg.sum_val = 0 THEN NULL
         ELSE ROUND(ABS(m_agg.sum_val - g_agg.sum_val) / ABS(g_agg.sum_val) * 100, 4)
    END AS sum_diff_pct,
    m_agg.avg_val AS mart_avg,
    g_agg.avg_val AS golden_avg,
    m_agg.avg_val - g_agg.avg_val AS avg_diff,
    CASE WHEN g_agg.avg_val = 0 THEN NULL
         ELSE ROUND(ABS(m_agg.avg_val - g_agg.avg_val) / ABS(g_agg.avg_val) * 100, 4)
    END AS avg_diff_pct,
    m_agg.min_val AS mart_min,
    g_agg.min_val AS golden_min,
    m_agg.min_val - g_agg.min_val AS min_diff,
    CASE WHEN g_agg.min_val = 0 THEN NULL
         ELSE ROUND(ABS(m_agg.min_val - g_agg.min_val) / ABS(g_agg.min_val) * 100, 4)
    END AS min_diff_pct,
    m_agg.max_val AS mart_max,
    g_agg.max_val AS golden_max,
    m_agg.max_val - g_agg.max_val AS max_diff,
    CASE WHEN g_agg.max_val = 0 THEN NULL
         ELSE ROUND(ABS(m_agg.max_val - g_agg.max_val) / ABS(g_agg.max_val) * 100, 4)
    END AS max_diff_pct
FROM (
    SELECT COUNT(*) AS total_rows,
           SUM({{MART_COL}}) AS sum_val,
           ROUND(AVG({{MART_COL}}), 6) AS avg_val,
           MIN({{MART_COL}}) AS min_val,
           MAX({{MART_COL}}) AS max_val
    FROM {{MART_TABLE}} {{MART_FILTER}}
) m_agg
CROSS JOIN (
    SELECT COUNT(*) AS total_rows,
           SUM({{GOLDEN_COL}}) AS sum_val,
           ROUND(AVG({{GOLDEN_COL}}), 6) AS avg_val,
           MIN({{GOLDEN_COL}}) AS min_val,
           MAX({{GOLDEN_COL}}) AS max_val
    FROM {{GOLDEN_TABLE}} {{GOLDEN_FILTER}}
) g_agg;
```

### 1.3b Aggregate Comparison by Dimension

Only run if `{{GROUP_BY_COLUMNS}}` were provided. Same metrics as 1.3 but grouped by dimension to surface where divergences concentrate:

```sql
SELECT
    '{{MART_COL}}' AS column_name,
    {{GROUP_BY_COLUMNS}},
    m_agg.total_rows AS mart_rows,
    g_agg.total_rows AS golden_rows,
    m_agg.sum_val AS mart_sum,
    g_agg.sum_val AS golden_sum,
    m_agg.sum_val - g_agg.sum_val AS sum_diff,
    CASE WHEN g_agg.sum_val = 0 THEN NULL
         ELSE ROUND(ABS(m_agg.sum_val - g_agg.sum_val) / ABS(g_agg.sum_val) * 100, 4)
    END AS sum_diff_pct,
    m_agg.avg_val AS mart_avg,
    g_agg.avg_val AS golden_avg,
    m_agg.avg_val - g_agg.avg_val AS avg_diff,
    CASE WHEN g_agg.avg_val = 0 THEN NULL
         ELSE ROUND(ABS(m_agg.avg_val - g_agg.avg_val) / ABS(g_agg.avg_val) * 100, 4)
    END AS avg_diff_pct
FROM (
    SELECT {{GROUP_BY_COLUMNS}},
           COUNT(*) AS total_rows,
           SUM({{MART_COL}}) AS sum_val,
           ROUND(AVG({{MART_COL}}), 6) AS avg_val
    FROM {{MART_TABLE}} {{MART_FILTER}}
    GROUP BY {{GROUP_BY_COLUMNS}}
) m_agg
JOIN (
    SELECT {{GROUP_BY_COLUMNS}},
           COUNT(*) AS total_rows,
           SUM({{GOLDEN_COL}}) AS sum_val,
           ROUND(AVG({{GOLDEN_COL}}), 6) AS avg_val
    FROM {{GOLDEN_TABLE}} {{GOLDEN_FILTER}}
    GROUP BY {{GROUP_BY_COLUMNS}}
) g_agg
  ON {{GROUP_BY_JOIN}}
ORDER BY sum_diff_pct DESC NULLS LAST;
```

### 1.4 HASH_AGG Comparison

Quick exact-match check per column using hash aggregation. Use this form only when row counts match (Pattern 1.1); otherwise every column mismatches simply because the row sets differ — use 1.4b instead.

```sql
SELECT
    '{{MART_COL}}' AS column_name,
    m.hash_val AS mart_hash,
    g.hash_val AS golden_hash,
    CASE WHEN m.hash_val = g.hash_val THEN 'MATCH' ELSE 'MISMATCH' END AS status
FROM (SELECT HASH_AGG({{MART_COL}}) AS hash_val FROM {{MART_TABLE}} {{MART_FILTER}}) m
CROSS JOIN (SELECT HASH_AGG({{GOLDEN_COL}}) AS hash_val FROM {{GOLDEN_TABLE}} {{GOLDEN_FILTER}}) g;
```

### 1.4b HASH_AGG on Matched Keys (row counts differ)

Restrict to keys present on both sides so the check isolates value differences from row-set differences. One query covers all columns; each column returns TRUE (match) or FALSE (mismatch):

```sql
SELECT
    COUNT(*) AS matched_rows,
    HASH_AGG(m.{{MART_COL_1}}) = HASH_AGG(g.{{GOLDEN_COL_1}}) AS "{{MART_COL_1}}",
    HASH_AGG(m.{{MART_COL_2}}) = HASH_AGG(g.{{GOLDEN_COL_2}}) AS "{{MART_COL_2}}"
    -- one expression per mapped non-key column
FROM {{MART_TABLE}} m
JOIN {{GOLDEN_TABLE}} g ON {{PK_JOIN}};
```

---

## Phase 2: Row-Level Diff (Targeted)

Only run on columns/segments that diverge in Phase 1.

### 2.1 Key Match Analysis

Identify rows present in one table but not the other:

```sql
SELECT 'MART_ONLY' AS source, {{PK_COLUMNS}}
FROM {{MART_TABLE}} {{MART_FILTER}}
MINUS
SELECT 'MART_ONLY' AS source, {{PK_COLUMNS}}
FROM {{GOLDEN_TABLE}} {{GOLDEN_FILTER}}

UNION ALL

SELECT 'GOLDEN_ONLY' AS source, {{PK_COLUMNS}}
FROM {{GOLDEN_TABLE}} {{GOLDEN_FILTER}}
MINUS
SELECT 'GOLDEN_ONLY' AS source, {{PK_COLUMNS}}
FROM {{MART_TABLE}} {{MART_FILTER}};
```

Use 2.1 to export key lists. To present results to the analyst, use 2.1b.

### 2.1b Key Match Summary by Segment

Summarize unmatched keys by `{{GROUP_BY_COLUMNS}}` (or any descriptive dimension) so thousands of keys reduce to a few segments that can be classified:

```sql
WITH k AS (
    SELECT 'MART_ONLY' AS source, {{PK_COLUMNS}}
    FROM (SELECT {{PK_COLUMNS}} FROM {{MART_TABLE}} MINUS SELECT {{PK_COLUMNS}} FROM {{GOLDEN_TABLE}})
    UNION ALL
    SELECT 'GOLDEN_ONLY', {{PK_COLUMNS}}
    FROM (SELECT {{PK_COLUMNS}} FROM {{GOLDEN_TABLE}} MINUS SELECT {{PK_COLUMNS}} FROM {{MART_TABLE}})
)
SELECT k.source,
       COALESCE(m.{{SEGMENT_COL}}, g.{{SEGMENT_COL}}) AS segment,
       COUNT(*) AS keys,
       MIN(k.{{PK_COL}}) AS min_key,
       MAX(k.{{PK_COL}}) AS max_key
FROM k
LEFT JOIN {{MART_TABLE}} m   ON k.source = 'MART_ONLY'   AND k.{{PK_COL}} = m.{{PK_COL}}
LEFT JOIN {{GOLDEN_TABLE}} g ON k.source = 'GOLDEN_ONLY' AND k.{{PK_COL}} = g.{{PK_COL}}
GROUP BY 1, 2
ORDER BY 1, 2;
```

### 2.2 Column-Level Row Diff (show_diff pattern)

FULL OUTER JOIN on PK, then compare each column. Run for each mapped column pair:

**For non-float columns:**
```sql
SELECT
    {{PK_COLUMNS}},
    '{{MART_COL}}' AS column_not_matching,
    NVL(m.{{MART_COL}}::VARCHAR, 'NULL') || ' <> ' || NVL(g.{{GOLDEN_COL}}::VARCHAR, 'NULL') AS mart_vs_golden_mismatch,
    m.{{MART_COL}} AS mart_value,
    g.{{GOLDEN_COL}} AS golden_value
FROM {{MART_TABLE}} m {{MART_FILTER_ALIAS}}
FULL OUTER JOIN {{GOLDEN_TABLE}} g {{GOLDEN_FILTER_ALIAS}}
  ON {{PK_JOIN}}
WHERE NVL(m.{{MART_COL}}::VARCHAR, '__NULL__') <> NVL(g.{{GOLDEN_COL}}::VARCHAR, '__NULL__')
LIMIT 1000;
```

**For float columns (with tolerance):**
```sql
SELECT
    {{PK_COLUMNS}},
    '{{MART_COL}}' AS column_not_matching,
    NVL(m.{{MART_COL}}::VARCHAR, 'NULL') || ' <> ' || NVL(g.{{GOLDEN_COL}}::VARCHAR, 'NULL') AS mart_vs_golden_mismatch,
    m.{{MART_COL}} AS mart_value,
    g.{{GOLDEN_COL}} AS golden_value,
    ROUND(ABS(NVL(DIV0NULL((NVL(m.{{MART_COL}}, -999) - NVL(g.{{GOLDEN_COL}}, -999)), NVL(m.{{MART_COL}}, -999)), -999)) * 100, 4) AS pct_difference
FROM {{MART_TABLE}} m {{MART_FILTER_ALIAS}}
FULL OUTER JOIN {{GOLDEN_TABLE}} g {{GOLDEN_FILTER_ALIAS}}
  ON {{PK_JOIN}}
WHERE ABS(NVL(DIV0NULL((NVL(m.{{MART_COL}}, -999) - NVL(g.{{GOLDEN_COL}}, -999)), NVL(m.{{MART_COL}}, -999)), -999)) * 100 > {{FLOAT_TOLERANCE_PCT}}
LIMIT 1000;
```

### 2.3 Full Column Comparison Summary

Aggregate mismatch counts across all columns for a compact divergence report:

```sql
SELECT column_not_matching, COUNT(*) AS mismatch_count
FROM (
    -- Union of all column-level diffs from 2.2 above
    {{UNION_OF_COLUMN_DIFFS}}
) diffs
GROUP BY column_not_matching
ORDER BY mismatch_count DESC;
```

### 2.4 Overall Match Score

```sql
SELECT
    (SELECT COUNT(*) FROM {{MART_TABLE}} {{MART_FILTER}}) AS mart_rows,
    (SELECT COUNT(*) FROM {{GOLDEN_TABLE}} {{GOLDEN_FILTER}}) AS golden_rows,
    COUNT(*) AS matched_key_count,
    {{NUM_COLUMNS}} AS columns_checked,
    {{NUM_COLUMNS_FAILED}} AS columns_with_mismatches,
    ROUND((1 - ({{NUM_COLUMNS_FAILED}}::FLOAT / NULLIF({{NUM_COLUMNS}}, 0))) * 100, 1) AS percent_passed
FROM {{MART_TABLE}} m
JOIN {{GOLDEN_TABLE}} g ON {{PK_JOIN}};
```

---

## Results

### 3.1 Insert into COMPARISON_RESULTS

One row per check. Allowed `COMPARISON_TYPE`, `DIMENSION_KEY`, and `STATUS` values are defined in `../../references/conventions.md` — COMPARISON_RESULTS Values.

```sql
INSERT INTO {{DB}}.{{SCHEMA}}.COMPARISON_RESULTS
    (RUN_ID, MART_NAME, GOLDEN_SOURCE, COMPARISON_TYPE, DIMENSION_KEY, COLUMN_NAME, MART_VALUE, GOLDEN_VALUE, DIFFERENCE, STATUS)
SELECT '{{RUN_ID}}', '{{MART_FQN}}', '{{GOLDEN_FQN}}', t, d, c, mv, gv, df, s
FROM VALUES
    ('row_count',     'ALL', '*',            '{{MART_ROWS}}', '{{GOLDEN_ROWS}}', '{{ROW_DIFF}}', 'MISMATCH'),
    ('aggregate_sum', 'ALL', '{{MART_COL}}', '{{MART_SUM}}',  '{{GOLDEN_SUM}}',  '{{SUM_DIFF}}', 'MISMATCH')
    -- one tuple per check
AS v(t, d, c, mv, gv, df, s);
```
