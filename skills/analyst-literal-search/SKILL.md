---
name: analyst-literal-search
title: Improve Analyst Literal Search
summary: Enrich Cortex Analyst semantic models and views with enums, sample values, and Cortex Search Services for better literal matching.
description: "Use when Cortex Analyst fails to match natural-language phrases to literal column values (e.g., 'iced tea' not finding 'Ice Tea'), or when you want to enrich a semantic model/view with is_enum flags, sample_values, and Cortex Search Service references on string dimensions. Triggers: literal search, cortex search service, cortex analyst search, sample values, dimension matching, fuzzy search, value lookup, search integration, improve analyst, literal values, enrich semantic view, optimize string columns, configure enum fields."
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: My Cortex Analyst can't match user phrases to actual column values. Help me enrich my semantic view with sample values and Cortex Search Services.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Improve Literal Search for Cortex Analyst

## Overview

Cortex Analyst matches natural-language queries to literal column values better when dimensions carry hints. This skill enriches a semantic model YAML or semantic view by:

- Adding `is_enum: true` + all distinct values for **low-cardinality** dimensions (≤10 distinct values).
- Creating a **Cortex Search Service** + 3 `sample_values` for **high-cardinality** dimensions (>10 distinct values).

Supports local YAML, staged YAML, and semantic views.

## Workflow

### Step 1: Confirm connection and source

Verify the Snowflake connection:
```sql
SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE();
```

Ask the user for: source (local YAML / staged YAML / semantic view FQN) and warehouse for search services (XSMALL is fine).

For a semantic view, list and read it:
```sql
SHOW SEMANTIC VIEWS IN <db>.<schema>;
SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('<DB.SCHEMA.VIEW>');
```

For staged YAML, read the file as one value to avoid record splitting:
```sql
CREATE TEMP FILE FORMAT IF NOT EXISTS _yaml_fmt
  TYPE = 'CSV' FIELD_DELIMITER = NONE RECORD_DELIMITER = NONE;
SELECT $1 FROM @<stage>/<path>/model.yaml (FILE_FORMAT => '_yaml_fmt');
```

### Step 2: Analyze dimensions

For each text dimension lacking config, query cardinality and sample values:
```sql
SELECT COUNT(DISTINCT <col>) AS distinct_count
FROM <base_table> WHERE <col> IS NOT NULL;

SELECT DISTINCT <col> FROM <base_table>
WHERE <col> IS NOT NULL ORDER BY <col> LIMIT <N>;
```

Categorize each as enum, search-service, or already-configured.

### Step 3: Present recommendations

Show a table of dimension → distinct count → recommendation. Ask for `TARGET_LAG` (default `'1 day'`).

⚠️ STOPPING POINT: Get explicit user approval on which dimensions to configure before any DDL.

### Step 4: Apply enum dimensions (low cardinality)

Edit the YAML / view to include:
```yaml
dimensions:
  - name: region
    expr: region_column
    is_enum: true
    sample_values: ["North America", "Europe", "Asia Pacific"]
```

### Step 5: Create search services and update model (high cardinality)

⚠️ STOPPING POINT: Confirm service names and warehouse before running `CREATE OR REPLACE`.

```sql
CREATE OR REPLACE CORTEX SEARCH SERVICE <db>.<schema>.<table>_<col>_ss
  ON <col>
  WAREHOUSE = <wh>
  TARGET_LAG = '<lag>'
  AS (SELECT DISTINCT <col> FROM <db>.<schema>.<table>);
```

For YAML models, add:
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

For semantic views, recreate via DDL. Required syntax:

- `WITH CORTEX SEARCH SERVICE` must come **before** `COMMENT`.
- `WITH EXTENSION (CA = '<json>')` carries `is_enum` / `sample_values`.
- Use fully qualified table names in `TABLES`.
- Use `COPY GRANTS`.

```sql
CREATE OR REPLACE SEMANTIC VIEW DB.SCHEMA.MY_VIEW
  TABLES (DB.SCHEMA.ORDERS COMMENT = 'Orders')
  DIMENSIONS (
    ORDERS.PRODUCT_NAME AS PRODUCT_NAME
      WITH CORTEX SEARCH SERVICE DB.SCHEMA.ORDERS_PRODUCT_NAME_SS
      COMMENT = 'Product',
    ORDERS.REGION AS REGION COMMENT = 'Region'
  )
  WITH EXTENSION (CA = '{"tables":[{"name":"ORDERS","dimensions":[{"name":"REGION","is_enum":true,"sample_values":["NA","EU","APAC"]}]}]}')
  COPY GRANTS;
```

Verify:
```sql
SHOW CORTEX SEARCH SERVICES IN <schema>;
```

### Step 6: Validate

```bash
cortex analyst query "<question with a literal value>" --view=<view>
```

Confirm matches resolve correctly.

## Scripts

For semantic views, the bundled scripts automate Steps 2 and 5. Dependencies are in `pyproject.toml` / `uv.lock`; run with `uv`. `<SKILL_DIR>` is this skill's directory. `--connection` defaults to `SNOWFLAKE_CONNECTION_NAME`, else the connector's default connection.

- `scripts/analyze_table.py` (read-only): reads the semantic view YAML, finds string dimensions, queries cardinality and sample values on each base table, and writes recommendations (`enum` for ≤10 distinct values, `search_service` otherwise) to a JSON file.
  ```bash
  uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/analyze_table.py \
    --semantic-view DB.SCHEMA.VIEW --warehouse <wh> --output analysis.json [--connection <name>]
  ```
- `scripts/apply_changes.py` (destructive): from `analysis.json`, runs `CREATE OR REPLACE CORTEX SEARCH SERVICE` for high-cardinality dimensions and recreates the semantic view via `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML` (which copies existing grants) with `is_enum`, `sample_values`, and search service references. It prints the planned statements and executes only after an interactive `y` or `--yes`.
  ```bash
  uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/apply_changes.py \
    --analysis analysis.json [--update-lag '1 day'] [--connection <name>] [--yes]
  ```

⚠️ STOPPING POINT: Before running `apply_changes.py`, show the user the planned changes (from `analysis.json`, or a run without `--yes` that prints the statements and aborts) and get explicit approval. Only pass `--yes` after approval.

## Common Mistakes

- **`COMMENT` before `WITH CORTEX SEARCH SERVICE`** — DDL fails. Search clause comes first.
- **Unqualified table names** in the `TABLES (...)` clause — use `DB.SCHEMA.TABLE`.
- **Malformed JSON** in `WITH EXTENSION (CA = '...')` — validate before running.
- **Missing `SNOWFLAKE.CORTEX_USER` role** — search service creation fails.
- **Skipping `LIMIT`** when sampling distinct values on huge columns — slow query.
- **Setting `TARGET_LAG` too aggressive** for the warehouse — increases cost without accuracy gain. Start with `'1 day'`.
- **Not waiting for indexing** before testing — initial index build takes time.

## Stopping Points

- Step 3 — approve dimension recommendations and `TARGET_LAG` before any DDL.
- Step 5 — confirm service names, warehouse, and view DDL before `CREATE OR REPLACE`.

## Troubleshooting

- *Search service creation denied*: grant `CREATE CORTEX SEARCH SERVICE` on the schema and `SNOWFLAKE.CORTEX_USER` to the role.
- *Semantic view name not found*: run `SHOW SEMANTIC VIEWS` first; user input often differs slightly.
- *No matching improvement*: confirm the service is `READY` via `SHOW CORTEX SEARCH SERVICES` and that `TARGET_LAG` has elapsed since creation.
