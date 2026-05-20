---
name: improve-literal-search-cortex-analyst
title: Improve Analyst Literal Search
summary: Enrich Cortex Analyst semantic models and views with enum flags, sample values, and Cortex Search Services for better literal matching.
description: "Use when Cortex Analyst fails to match natural-language queries to literal column values (e.g., \"iced tea\" vs \"Ice Tea\"), or when you want to add `is_enum`, `sample_values`, or Cortex Search Services to dimensions in a semantic model YAML or semantic view. Triggers: literal search, cortex search service, cortex analyst search, sample values, dimension matching, fuzzy search, value lookup, search integration, improve analyst, literal values, enrich semantic view, optimize string columns, configure enum fields."
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Bash
  - Read
  - Write
  - Edit
prompt: Improve literal search on my semantic view DB.SCHEMA.MY_VIEW so Cortex Analyst matches product names and regions correctly.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Improve Literal Search for Cortex Analyst

## Overview

Cortex Analyst maps natural language to SQL, but it can miss literal values when user phrasing differs from stored values (e.g., "iced tea" vs "Ice Tea"). This skill enriches dimensions in a semantic model YAML or a semantic view so Analyst matches reliably:

- **Low cardinality (<= 10 distinct values):** add `is_enum: true` and full `sample_values`.
- **High cardinality (> 10 distinct values):** create a Cortex Search Service and reference it from the dimension, plus 3 `sample_values`.

Supports local YAML, YAML on a stage, and Snowflake semantic views.

## Workflow

### 1. Confirm connection
Run `SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE();` and confirm before any DDL.

### 2. Load the model
- **Semantic view:** `SHOW SEMANTIC VIEWS IN <db>.<schema>;` then `SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('<DB.SCHEMA.VIEW>');`
- **Local YAML:** read from disk.
- **Stage YAML:** read with a no-delimiter file format so long files stay in one row:
  ```sql
  CREATE TEMP FILE FORMAT IF NOT EXISTS _yaml_fmt
    TYPE = 'CSV' FIELD_DELIMITER = NONE RECORD_DELIMITER = NONE;
  SELECT $1 FROM @<stage>/<path>/model.yaml (FILE_FORMAT => '_yaml_fmt');
  ```

### 3. Analyze dimensions
For each text dimension without search config:
```sql
SELECT COUNT(DISTINCT <col>) AS distinct_count
FROM <base_table> WHERE <col> IS NOT NULL;
```
Pull all values for low-cardinality columns, 3 samples for high-cardinality.

### 4. Apply enrichments

Low cardinality (YAML):
```yaml
- name: region
  expr: region_column
  is_enum: true
  sample_values: ["North America", "Europe", "Asia Pacific"]
```

High cardinality — create the search service:
```sql
CREATE OR REPLACE CORTEX SEARCH SERVICE <db>.<schema>.<table>_<col>_ss
  ON <col>
  WAREHOUSE = <warehouse>
  TARGET_LAG = '1 day'
  AS (SELECT DISTINCT <col> FROM <db>.<schema>.<table>);
```

Reference it in YAML:
```yaml
- name: product_name
  expr: product_name_column
  cortex_search_service:
    service: <table>_<col>_ss
    literal_column: <col>
    database: <db>
    schema: <schema>
  sample_values: ["Example A", "Example B"]
```

For semantic views, recreate via `GET_DDL('SEMANTIC_VIEW', ...)` or `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(...)`. In DDL, place `WITH CORTEX SEARCH SERVICE` **before** `COMMENT`, use fully qualified table names, and `COPY GRANTS`:
```sql
CREATE OR REPLACE SEMANTIC VIEW <db>.<schema>.<view>
  TABLES (<db>.<schema>.<table> COMMENT = '...')
  DIMENSIONS (
    t.col1 AS col1
      WITH CORTEX SEARCH SERVICE <db>.<schema>.<svc>
      COMMENT = '...'
  )
  WITH EXTENSION (CA = '{"tables":[{"name":"t","dimensions":[{"name":"col2","is_enum":true,"sample_values":["A","B"]}]}]}')
  COPY GRANTS;
```

### 5. Validate
```sql
SHOW CORTEX SEARCH SERVICES IN <schema>;
SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('<DB.SCHEMA.VIEW>');
```
Then test:
```bash
cortex analyst query "<question with a literal value>" --view=<view>
# or --model=<file.yaml>
```

## Common Mistakes

- **Wrong DDL order:** `COMMENT` before `WITH CORTEX SEARCH SERVICE` — fails to parse. Put the search clause first.
- **Unqualified table names** in the `TABLES (...)` clause — use `DB.SCHEMA.TABLE`.
- **Malformed `WITH EXTENSION (CA = '...')` JSON** — validate before running.
- **Missing privileges:** role needs `CREATE CORTEX SEARCH SERVICE` on the schema and the `SNOWFLAKE.CORTEX_USER` database role.
- **Stage YAML truncated** when read with default CSV delimiters — use `FIELD_DELIMITER = NONE` and `RECORD_DELIMITER = NONE`.
- **Querying before indexing finishes** — `TARGET_LAG` must elapse before initial results are available.
- **Skipping `sample_values` on high-cardinality columns** — Analyst still benefits from a few examples even when a search service is attached.
- **Forgetting `COPY GRANTS`** on `CREATE OR REPLACE SEMANTIC VIEW` — downstream grants disappear.
