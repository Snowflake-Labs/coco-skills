---
name: baseline-model
parent_skill: messy-migration-validation
description: "Baseline model development — build a comparable baseline from one or more legacy tables. Column mapping, join logic, naming conventions, transforms, filters, and primary key definition. Triggers: baseline model, baseline, column mapping, build model, map columns, source mapping."
---

# Baseline Model Development

**You are using the baseline-model sub-skill. Follow these instructions exactly.**

## When to Use

- Building a baseline view from one or more legacy tables
- Creating column mappings between source systems and a target data mart
- Defining join logic, transforms, and filters for a comparable baseline
- The new mart should ideally be profiled first (UC1) to understand its structure

## Workflow

### Prerequisites

1. Read `../references/conventions.md` — naming standards and table schemas
2. The new mart should ideally be profiled first (UC1) to understand its structure

### Step 1: Gather Inputs

Ask the analyst for:
1. **New mart**: Fully qualified name of the mart being validated
2. **Mart column scope** (optional): Does the analyst want to map all mart columns or a specific subset? If the mart has many columns (e.g., 50+), proactively ask. Accept a list of column names, a pattern, or "all" (default). Unmapped columns outside the scope are tracked as `out_of_scope_mart_columns` in the mapping artifact.
3. **Legacy table(s)**: One or more fully qualified table names from the legacy system
4. **Known join relationships**: Any known keys or joins between legacy tables (if multi-table)
5. **Known column mappings**: Any mappings the analyst already knows (e.g., "CUST_NM maps to CUSTOMER_NAME")
6. **Filters**: Any filter conditions to apply (e.g., date ranges, source system codes, soft-delete exclusions)
7. **Supporting assets**: Ask broadly for any reference material that can inform the model:
   - Entity-relationship diagrams (ERDs)
   - Data dictionaries or column-level documentation
   - Existing reports or dashboards built on the legacy data
   - ETL/ELT documentation or lineage diagrams
   - Business rules or transformation logic documents
   - Prior validation work or mapping spreadsheets

   These assets are not required but significantly accelerate discovery. If provided, use them to inform join logic, column matching, transform definitions, and filter conditions throughout the workflow.

**[STOP]** Confirm the tables, column scope, and any known context before proceeding.

### Step 2: Schema Discovery

For each legacy table and the new mart, retrieve column metadata:

```sql
SELECT COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_CATALOG = '{{DATABASE}}'
  AND TABLE_SCHEMA  = '{{SCHEMA}}'
  AND TABLE_NAME    = '{{TABLE}}'
ORDER BY ORDINAL_POSITION;
```

Also retrieve sample data (10 rows) from each table to aid matching.

Present the column inventories side-by-side to the analyst as a table (see `../references/conventions.md` — Presenting Large Lists).

**[STOP]** Analyst reviews schema inventories before proceeding to join identification.

## Step 3: Join Column Identification (Multi-Table)

If the baseline is built from multiple legacy tables:

1. **Start with known joins** provided by the analyst
2. **Infer candidate joins** by looking for:
   - Exact column name matches between legacy tables
   - Similar column names (e.g., `INVOICE_DIST_ID` ↔ `INVOICE_DISTRIBUTION_ID`)
   - Columns with matching cardinality and sample value overlap
3. **Verify join columns** with an anti-join count (do not CROSS JOIN — it multiplies row counts and does not scale):

```sql
SELECT COUNT(DISTINCT a.{{COL_A}}) AS distinct_a,
       (SELECT COUNT(DISTINCT {{COL_B}}) FROM {{TABLE_B}}) AS distinct_b,
       COUNT(DISTINCT IFF(b.{{COL_B}} IS NULL, a.{{COL_A}}, NULL)) AS non_matching,
       (SELECT COUNT(*) - COUNT(DISTINCT {{COL_B}}) FROM {{TABLE_B}}) AS right_side_duplicates
FROM {{TABLE_A}} a
LEFT JOIN {{TABLE_B}} b ON a.{{COL_A}} = b.{{COL_B}};
```

`non_matching = 0` confirms every key on side A resolves; `right_side_duplicates = 0` confirms the join cannot fan out. Batch all candidate joins into one query with UNION ALL.

4. **Determine relationship type**: one-to-one, one-to-many, many-to-many

**[STOP]** Present proposed join columns and relationship type. Wait for analyst confirmation.

### Step 4: Column Matching

Only match baseline columns against mart columns that are **in scope**. Mart columns outside the analyst's scope are not candidates for matching — track them separately as `out_of_scope_mart_columns` in the YAML.

Match baseline columns to in-scope mart columns using three tiers of confidence:

### Matching Strategy

| Tier | Match Type | Confidence | Action |
|------|-----------|------------|--------|
| 1 | **Exact name match** | HIGH | Auto-map, always include regardless of data match |
| 2 | **Name pattern match** | HIGH | Auto-map (e.g., `INVOICE_NUM` ↔ `INVOICE_NUMBER`, `SUPPLIER_NUM` ↔ `VENDOR_NUMBER`) |
| 3 | **Semantic/data match** | MEDIUM | Propose to analyst, verify with sample data comparison |

For name pattern matching, use these heuristics:
- Suffix patterns: `_NUM` ↔ `_NUMBER`, `_AMT` ↔ `_AMOUNT`, `_DESC` ↔ `_DESCRIPTION`, `_DT` ↔ `_DATE`
- Prefix patterns: `CUST_` ↔ `CUSTOMER_`, `INV_` ↔ `INVOICE_`
- Abbreviation expansion

For ambiguous matches, **verify with data**:

```sql
SELECT COUNT(*) AS match_count
FROM {{MART}} m
JOIN {{SOURCE}} s ON m.{{MART_COL}}::VARCHAR = s.{{SOURCE_COL}}::VARCHAR
WHERE m.{{MART_COL}} IS NOT NULL;
```

### Naming Convention for Mapped Columns

When the baseline column maps to a mart column but has a different name:
- The mapping artifact preserves both original names
- If a combined semantic view is later created, source columns are renamed with a prefix: `{SOURCE_ALIAS}_{MART_COLUMN_NAME}`
  - Example: If source alias is `SRC` and mart column is `ORDER_NUM`, the source column appears as `SRC_ORDER_NUM` in the semantic view

**[STOP]** Present column matches as a table — one row per mart column with legacy source, tier, confidence, and transform (see `../references/conventions.md` — Presenting Large Lists). Flag unmatched columns from both sides. Wait for analyst to:
- Confirm auto-mapped columns
- Resolve ambiguous mappings
- Accept or reject proposed matches
- Note any columns intentionally excluded

### Step 5: Define Primary Keys

Identify or confirm the primary key for both the mart and the baseline:
1. Check DDL for defined PKs: `SHOW PRIMARY KEYS IN TABLE {{TABLE_NAME}};`
2. If no PK defined, propose candidate keys based on uniqueness analysis from profiling
3. For multi-table baselines, the composite PK is the join key set

**[STOP]** Confirm primary keys with analyst.

### Step 6: Define Transforms

For each mapped column pair, identify any required transformations:
- Data type casts: `CAST(source AS NUMBER(18,2))`
- String normalization: `UPPER(TRIM(source))`
- Date format alignment: `TO_DATE(source, 'YYYY-MM-DD')`
- Null handling: `COALESCE(source, default)`
- Rounding: `ROUND(source, precision)`

**[STOP]** Present proposed transforms to the analyst for confirmation.

## Step 7: Define Filters

Document any filter conditions that must be applied:
- **Mart filters**: e.g., `WHERE SOURCE_SYSTEM = 'ERP_A'`
- **Source filters**: e.g., `WHERE EFFECTIVE_DATE >= '2024-01-01'`
- **Soft-delete exclusions**: e.g., `WHERE IS_DELETED = FALSE`

Give each filter a descriptive name for reference.

**[STOP]** Confirm filters with the analyst before generating the mapping artifact.

## Step 8: Generate Mapping Artifact

Produce a YAML mapping file saved to `validation-workspace/mappings/{mart-name}-mapping.yaml`:

```yaml
mapping:
  mart: {{MART_DATABASE}}.{{MART_SCHEMA}}.{{MART_TABLE}}
  golden_sources:
    - table: {{SOURCE_DATABASE_1}}.{{SOURCE_SCHEMA_1}}.{{SOURCE_TABLE_1}}
      alias: {{SOURCE_ALIAS_1}}
    - table: {{SOURCE_DATABASE_2}}.{{SOURCE_SCHEMA_2}}.{{SOURCE_TABLE_2}}
      alias: {{SOURCE_ALIAS_2}}
  joins:
    - left: {{SOURCE_ALIAS_1}}
      right: {{SOURCE_ALIAS_2}}
      type: INNER | LEFT | FULL OUTER
      conditions:
        - left_col: {{LEFT_JOIN_COL_1}}
          right_col: {{RIGHT_JOIN_COL_1}}
        - left_col: {{LEFT_JOIN_COL_2}}
          right_col: {{RIGHT_JOIN_COL_2}}
      relationship: one-to-one | one-to-many | many-to-many
  primary_keys:
    mart:
      - {{MART_PK_COL_1}}
      - {{MART_PK_COL_2}}
    golden_source:
      - {{GOLDEN_PK_COL_1}}
      - {{GOLDEN_PK_COL_2}}
  grain:
    - {{GRAIN_COL_1}}
    - {{GRAIN_COL_2}}
  columns:
    - target: {{MART_COLUMN}}
      source: {{SOURCE_COLUMN}}
      source_table: {{SOURCE_ALIAS}}
      confidence: HIGH | MEDIUM
      match_type: exact | name_pattern | semantic
      transform: null | UPPER(TRIM(source)) | CAST(source AS NUMBER(18,2))
  unmatched_mart_columns:
    - {{MART_ONLY_COL_1}}
    - {{MART_ONLY_COL_2}}
  out_of_scope_mart_columns:
    - {{OUT_OF_SCOPE_COL_1}}
  unmatched_source_columns:
    - {{SOURCE_ONLY_COL_1}}
  filters:
    mart:
      - name: {{FILTER_NAME}}
        expression: "{{FILTER_EXPRESSION}}"
    source:
      - name: {{FILTER_NAME}}
        expression: "{{FILTER_EXPRESSION}}"
```

Present the complete mapping to the analyst.

**[STOP]** Analyst reviews and confirms the mapping artifact.

### Step 9: Generate Baseline View SQL

From the mapping artifact, generate a CREATE VIEW or CTE that produces the comparable baseline:

```sql
CREATE OR REPLACE VIEW {{DB}}.{{SCHEMA}}.{{MART_NAME}}_GOLDEN_VW AS
SELECT
    s1.{{SOURCE_COL}} AS {{TARGET_COL}},
    ...
FROM {{SOURCE_TABLE_1}} s1
JOIN {{SOURCE_TABLE_2}} s2
  ON s1.{{JOIN_COL}} = s2.{{JOIN_COL}}
WHERE {{FILTERS}};
```

Execute the DDL and verify the view is queryable.

**[STOP]** Present the generated view SQL and row count to the analyst before proceeding.

## Step 10: Log to Testing Record

Log per `../references/conventions.md` — Logging to VALIDATION_TEST_LOG:
- `TEST_TYPE`: 'model'
- `RESULT_SUMMARY`: Count of mapped columns, join conditions, filter count
- `STATUS`: 'info'

## Step 11: Generate Semantic View (Mandatory)

The semantic view is the primary deliverable of this use case — it unifies the baseline view and mart view into a single queryable layer for UC3.

**Primary path:** delegate to the bundled `agent-studio` skill (semantic-view → creation). Pass both tables (new mart and baseline view), the primary keys, and the mapping metadata as context.

**Fallback:** the `agent-studio` generator runs on the CLI connection's default role, which may differ from the session role. If generation reports that the tables are not accessible, or returns no `semanticYaml`, do not grant privileges or switch roles. Author the YAML from the mapping artifact instead:

```yaml
name: {{MART_NAME}}_SV
description: Validation semantic view joining the new mart (MART) with its baseline (BASELINE)
tables:
  - name: MART
    base_table: {database: {{MART_DB}}, schema: {{MART_SCHEMA}}, table: {{MART_TABLE}}}
    primary_key: {columns: [{{PK_COL}}]}
    dimensions: []        # one entry per mapped non-numeric column (name, expr, data_type, description)
    time_dimensions: []   # DATE / TIMESTAMP columns
    facts: []             # numeric measures
    metrics:
      - {name: MART_ROW_COUNT, expr: COUNT({{PK_COL}})}
  - name: BASELINE
    base_table: {database: {{DB}}, schema: {{SCHEMA}}, table: {{MART_NAME}}_GOLDEN_VW}
    primary_key: {columns: [{{PK_COL}}]}
    # same dimensions / time_dimensions / facts as MART, plus BASELINE_ROW_COUNT
relationships:
  - name: MART_TO_BASELINE
    left_table: MART
    right_table: BASELINE
    relationship_columns: [{left_column: {{PK_COL}}, right_column: {{PK_COL}}}]
    join_type: left_outer
    relationship_type: one_to_one
```

Validate without creating anything:

```sql
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{{DB}}.{{SCHEMA}}', $${{SEMANTIC_VIEW_YAML}}$$, TRUE);
```

Save the YAML to `validation-workspace/semantic-views/{mart-name}-sv.yaml`.

**[STOP]** Present the semantic view YAML to the analyst for review.

## Step 12: Deploy Semantic View to Snowflake

Deploy the semantic view from local YAML to Snowflake using `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML` (only after the analyst approved the YAML in Step 11):

```sql
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(
    '{{DB}}.{{SCHEMA}}',
    $${{SEMANTIC_VIEW_YAML}}$$
);
```

Verify deployment:

```sql
SHOW SEMANTIC VIEWS IN SCHEMA {{DB}}.{{SCHEMA}};
```

**[STOP]** Confirm the semantic view is deployed and queryable.

## Deliverables

This use case terminates here. The three primary deliverables are:

1. **Baseline view** — `{{DB}}.{{SCHEMA}}.{{MART_NAME}}_GOLDEN_VW`
2. **Mart view** — the original new mart (already exists)
3. **Semantic view** — deployed to Snowflake, unifying baseline and mart for UC3

The mapping YAML (`validation-workspace/mappings/{mart-name}-mapping.yaml`) is a supporting artifact consumed by UC3.

## Output

- `validation-workspace/mappings/{mart-name}-mapping.yaml` — mapping artifact (supporting)
- Baseline view created in Snowflake
- `validation-workspace/semantic-views/{mart-name}-sv.yaml` — semantic view YAML
- Semantic view deployed to Snowflake via `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML`
- `VALIDATION_TEST_LOG` entry

## Important Rules

1. Always verify join columns with actual data — never assume based on name alone
2. Present column matches with confidence levels — let the analyst make final decisions
3. Document everything in the mapping YAML — it is consumed by UC3 and UC4
4. Multi-table baselines are the norm, not the exception — the workflow must handle them gracefully
5. Preserve original column names in the mapping; use aliases only in generated views
6. The use case terminates at semantic view deployment — do not offer to skip directly to UC3
7. At every [STOP] point, emit a one-line session hygiene reminder per `../references/conventions.md` — Session Hygiene. Generate a handoff summary at use case completion.
