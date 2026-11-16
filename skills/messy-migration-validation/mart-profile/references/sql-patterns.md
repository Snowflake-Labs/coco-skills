# Profiling SQL Patterns

Parameterized SQL templates for data mart profiling. Execute these in order, substituting placeholders.

## Placeholders

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `{{TABLE_NAME}}` | Fully qualified table | `ANALYTICS_DB.MARTS.CUSTOMER_MART` |
| `{{COLUMN_NAME}}` | Single column name | `CUSTOMER_ID` |
| `{{FILTER}}` | Optional WHERE clause | `WHERE AS_OF_DATE >= '2024-01-01'` or empty |
| `{{DB}}` | Target database for results | `ANALYTICS_DB` |
| `{{SCHEMA}}` | Target schema for results | `MARTS` |
| `{{RUN_ID}}` | Unique run identifier | UUID string |
| `{{MART_NAME}}` | Short mart name for logging | `CUSTOMER_MART` |

---

## 1. Table Overview

```sql
SELECT
    COUNT(*) AS total_row_count,
    (SELECT COUNT(*)
     FROM INFORMATION_SCHEMA.COLUMNS
     WHERE TABLE_CATALOG = SPLIT_PART('{{TABLE_NAME}}', '.', 1)
       AND TABLE_SCHEMA  = SPLIT_PART('{{TABLE_NAME}}', '.', 2)
       AND TABLE_NAME    = SPLIT_PART('{{TABLE_NAME}}', '.', 3)
    ) AS total_column_count
FROM {{TABLE_NAME}}
{{FILTER}};
```

## 2. Column Metadata

```sql
SELECT
    COLUMN_NAME,
    ORDINAL_POSITION,
    DATA_TYPE,
    IS_NULLABLE,
    COLUMN_DEFAULT,
    CHARACTER_MAXIMUM_LENGTH,
    NUMERIC_PRECISION,
    NUMERIC_SCALE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_CATALOG = SPLIT_PART('{{TABLE_NAME}}', '.', 1)
  AND TABLE_SCHEMA  = SPLIT_PART('{{TABLE_NAME}}', '.', 2)
  AND TABLE_NAME    = SPLIT_PART('{{TABLE_NAME}}', '.', 3)
ORDER BY ORDINAL_POSITION;
```

## 3. Sample Rows

```sql
SELECT * FROM {{TABLE_NAME}} {{FILTER}} LIMIT 10;
```

## 4. Numeric Column Profile (Batched)

Generate a single query covering ALL numeric columns (DATA_TYPE IN ('NUMBER', 'FLOAT', 'DECIMAL', 'INTEGER', 'BIGINT', 'SMALLINT', 'TINYINT', 'DOUBLE', 'REAL')) using UNION ALL. Replace `{{COLUMN_LIST_NUMERIC}}` with one SELECT block per numeric column:

```sql
{{COLUMN_LIST_NUMERIC}}
-- Each block follows this template, joined with UNION ALL:
SELECT
    '{{COLUMN_NAME}}' AS column_name,
    COUNT(*) AS total_rows,
    COUNT({{COLUMN_NAME}}) AS non_null_count,
    COUNT(*) - COUNT({{COLUMN_NAME}}) AS null_count,
    ROUND((COUNT(*) - COUNT({{COLUMN_NAME}}))::FLOAT / NULLIF(COUNT(*), 0) * 100, 2) AS null_pct,
    COUNT(DISTINCT {{COLUMN_NAME}}) AS distinct_count,
    ROUND(COUNT(DISTINCT {{COLUMN_NAME}})::FLOAT / NULLIF(COUNT(*), 0) * 100, 2) AS distinct_pct,
    MIN({{COLUMN_NAME}}) AS min_value,
    MAX({{COLUMN_NAME}}) AS max_value,
    ROUND(AVG({{COLUMN_NAME}}), 6) AS avg_value,
    MEDIAN({{COLUMN_NAME}}) AS median_value,
    ROUND(STDDEV({{COLUMN_NAME}}), 6) AS stddev_value,
    HASH_AGG({{COLUMN_NAME}}) AS hash_agg
FROM {{TABLE_NAME}}
{{FILTER}}
-- UNION ALL (between each block, omit after the last)
```

Example for 3 numeric columns:
```sql
SELECT 'TOTAL_AMOUNT' AS column_name, COUNT(*) AS total_rows, ... FROM {{TABLE_NAME}} {{FILTER}}
UNION ALL
SELECT 'TAX_AMOUNT' AS column_name, COUNT(*) AS total_rows, ... FROM {{TABLE_NAME}} {{FILTER}}
UNION ALL
SELECT 'SUBTOTAL_AMOUNT' AS column_name, COUNT(*) AS total_rows, ... FROM {{TABLE_NAME}} {{FILTER}};
```

## 5. Categorical Column Profile

Run this for each column where DATA_TYPE NOT IN ('NUMBER', 'FLOAT', 'DECIMAL', 'INTEGER', 'BIGINT', 'SMALLINT', 'TINYINT', 'DOUBLE', 'REAL', 'ARRAY', 'BINARY', 'GEOGRAPHY', 'OBJECT', 'VARIANT'):

```sql
SELECT
    '{{COLUMN_NAME}}' AS column_name,
    COUNT(*) AS total_rows,
    COUNT({{COLUMN_NAME}}) AS non_null_count,
    COUNT(*) - COUNT({{COLUMN_NAME}}) AS null_count,
    ROUND((COUNT(*) - COUNT({{COLUMN_NAME}}))::FLOAT / NULLIF(COUNT(*), 0) * 100, 2) AS null_pct,
    COUNT(DISTINCT {{COLUMN_NAME}}) AS distinct_count,
    ROUND(COUNT(DISTINCT {{COLUMN_NAME}})::FLOAT / NULLIF(COUNT(*), 0) * 100, 2) AS distinct_pct,
    MIN({{COLUMN_NAME}})::VARCHAR AS min_value,
    MAX({{COLUMN_NAME}})::VARCHAR AS max_value,
    HASH_AGG({{COLUMN_NAME}}) AS hash_agg
FROM {{TABLE_NAME}}
{{FILTER}};
```

## 6. Top Values for Categorical Columns

Run this for each categorical column to understand value distributions:

```sql
SELECT
    '{{COLUMN_NAME}}' AS column_name,
    {{COLUMN_NAME}} AS value,
    COUNT(*) AS value_count,
    ROUND(COUNT(*)::FLOAT / (SELECT COUNT(*) FROM {{TABLE_NAME}} {{FILTER}}) * 100, 2) AS pct_of_total
FROM {{TABLE_NAME}}
{{FILTER}}
GROUP BY {{COLUMN_NAME}}
ORDER BY value_count DESC
LIMIT 20;
```

## 6a. Full Value List (Analyst Override)

Run this only when the analyst explicitly requests the full value list for a specific column. **Check distinct_pct first** — if > 90%, warn the analyst and require confirmation before proceeding.

```sql
SELECT
    '{{COLUMN_NAME}}' AS column_name,
    {{COLUMN_NAME}} AS value,
    COUNT(*) AS value_count,
    ROUND(COUNT(*)::FLOAT / (SELECT COUNT(*) FROM {{TABLE_NAME}} {{FILTER}}) * 100, 2) AS pct_of_total
FROM {{TABLE_NAME}}
{{FILTER}}
GROUP BY {{COLUMN_NAME}}
ORDER BY value_count DESC;
```

## 7. Key/Uniqueness Analysis

Run this for each candidate key column (columns with high distinct_pct or known business keys):

```sql
SELECT
    '{{COLUMN_NAME}}' AS column_name,
    COUNT(*) AS total_rows,
    COUNT(DISTINCT {{COLUMN_NAME}}) AS distinct_values,
    COUNT(*) - COUNT(DISTINCT {{COLUMN_NAME}}) AS duplicate_count,
    CASE
        WHEN COUNT(*) = COUNT(DISTINCT {{COLUMN_NAME}}) THEN 'UNIQUE'
        ELSE 'NOT UNIQUE (' || (COUNT(*) - COUNT(DISTINCT {{COLUMN_NAME}}))::VARCHAR || ' duplicates)'
    END AS uniqueness_status
FROM {{TABLE_NAME}}
{{FILTER}};
```

## 8. Composite Key Uniqueness

If multiple columns form the grain, test composite uniqueness:

```sql
SELECT
    COUNT(*) AS total_rows,
    COUNT(DISTINCT CONCAT_WS('|', {{GRAIN_COLUMNS}})) AS distinct_combinations,
    COUNT(*) - COUNT(DISTINCT CONCAT_WS('|', {{GRAIN_COLUMNS}})) AS duplicate_rows
FROM {{TABLE_NAME}}
{{FILTER}};
```

## 9. Foreign Key Integrity (Optional)

If a suspected FK relationship exists to `{{FK_TABLE}}` on `{{FK_COLUMN}}`:

```sql
SELECT
    COUNT(*) AS orphan_count,
    COUNT(DISTINCT a.{{COLUMN_NAME}}) AS orphan_distinct_values
FROM {{TABLE_NAME}} a
LEFT JOIN {{FK_TABLE}} b ON a.{{COLUMN_NAME}} = b.{{FK_COLUMN}}
WHERE b.{{FK_COLUMN}} IS NULL
  AND a.{{COLUMN_NAME}} IS NOT NULL;
```

## 10. Insert into PROFILE_RESULTS

After collecting all metrics, insert per-column results:

```sql
INSERT INTO {{DB}}.{{SCHEMA}}.PROFILE_RESULTS
    (RUN_ID, MART_NAME, COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE, IS_NULLABLE,
     TOTAL_ROW_COUNT, DISTINCT_COUNT, DISTINCT_PCT, NULL_COUNT, NULL_PCT,
     MIN_VALUE, MAX_VALUE, AVG_VALUE, MEDIAN_VALUE, STDDEV_VALUE, HASH_AGG, JOB_NAME)
VALUES
    ('{{RUN_ID}}', '{{MART_NAME}}', '{{COLUMN_NAME}}', {{ORDINAL}}, '{{DATA_TYPE}}', '{{IS_NULLABLE}}',
     {{TOTAL_ROWS}}, {{DISTINCT_COUNT}}, {{DISTINCT_PCT}}, {{NULL_COUNT}}, {{NULL_PCT}},
     '{{MIN_VALUE}}', '{{MAX_VALUE}}', {{AVG_VALUE}}, {{MEDIAN_VALUE}}, {{STDDEV_VALUE}}, '{{HASH_AGG}}', '{{JOB_NAME}}');
```

## 11. Verify PROFILE_RESULTS Insert

Run immediately after Pattern 10 to confirm rows were written correctly:

```sql
SELECT COLUMN_NAME, DATA_TYPE, DISTINCT_COUNT, NULL_PCT, HASH_AGG
FROM {{DB}}.{{SCHEMA}}.PROFILE_RESULTS
WHERE RUN_ID = '{{RUN_ID}}'
ORDER BY ORDINAL_POSITION;
```
