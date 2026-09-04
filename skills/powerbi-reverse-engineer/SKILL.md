---
id: powerbi-reverse-engineer
name: powerbi-reverse-engineer
skill-name: $powerbi-reverse-engineer
description: "Reverse engineer a Power BI semantic model into a Snowflake Semantic View with complete DDL output. Supports .pbit/.pbix files or live models via the Power BI Modeling MCP Server."
prompt: "$powerbi-reverse-engineer convert my Power BI file at /path/to/report.pbit into a Snowflake Semantic View"
language: en
status: Published
author: Josh Crittenden
type: community
---

# Power BI Reverse Engineer

# When to Use
- User provides a `.pbit` or `.pbix` file and wants to convert it to a Snowflake Semantic View
- User wants to migrate Power BI business logic (DAX measures) to Snowflake SQL metrics
- User wants to enable Cortex Agents or Snowflake Intelligence for data already modeled in Power BI
- User has the Power BI Modeling MCP Server running and wants to reverse engineer a live model
- Do NOT use for auditing best practices only (use `$powerbi-bpa` instead)

# What This Skill Provides
Extracts the complete semantic model from a Power BI file or live model, analyzes source tables, relationships, and DAX measures, then generates a `.sql` file containing all recommended database objects (views, tables) and the `CREATE OR REPLACE SEMANTIC VIEW` DDL.

# References
- [Snowflake Semantic Views](https://docs.snowflake.com/en/user-guide/views-semantic/overview)
- [Cortex Analyst](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/cortex-analyst)
- [Power BI Modeling MCP Server](https://github.com/microsoft/powerbi-modeling-mcp)

# Instructions

## Step 1: Determine Input Method and Extract Model

**Ask** the user how they want to provide their Power BI model. Two methods are supported:

### Option A: File-based (.pbit or .pbix)

1. **Extract** the DataModelSchema from the archive:
   ```bash
   mkdir -p /tmp/pbit_extract
   unzip -o "<file_path>" DataModelSchema -d /tmp/pbit_extract
   iconv -f UTF-16LE -t UTF-8 /tmp/pbit_extract/DataModelSchema > /tmp/pbit_extract/DataModelSchema.json
   ```

2. **Parse** the JSON:
   ```python
   import json
   with open('/tmp/pbit_extract/DataModelSchema.json', 'r') as f:
       data = json.load(f)
   model = data.get('model', {})
   tables = model.get('tables', [])
   relationships = model.get('relationships', [])
   ```

### Option B: Power BI Modeling MCP Server

If the user has the [Power BI Modeling MCP Server](https://github.com/microsoft/powerbi-modeling-mcp) running:

1. **Connect** to the model:
   - For Power BI Desktop: `Connect to '[File Name]' in Power BI Desktop`
   - For Fabric workspace: `Connect to semantic model '[Model Name]' in Fabric Workspace '[Workspace Name]'`

2. **Retrieve** all model metadata via MCP tools:
   - `model_operations` — overall model properties
   - `table_operations` (list) — all tables
   - `column_operations` (list) — columns per table with data types and expressions
   - `measure_operations` (list) — all DAX measures with expressions
   - `relationship_operations` (find) — all relationships with cardinality and cross-filter behavior
   - `security_role_operations` — RLS definitions
   - `named_expression_operations` (list) — Power Query M expressions and parameters

**Output:** Parsed model object with tables, relationships, measures, and M expressions.

**If error occurs:**
- File not a valid ZIP: Check file extension, try different encoding
- DataModelSchema not found: File may be corrupted or incompatible PBI version
- UTF-16 decode fails: Try UTF-16BE instead of UTF-16LE
- MCP connection fails: Verify Power BI Desktop is open or Fabric workspace is accessible

## Step 2: Analyze Power Query (M Expressions)

**Goal:** Identify actual source table names and data sources for every table.

**Extract** M expressions from `partitions[].source.expression` (file-based) or via `named_expression_operations` (MCP).

**Analyze** each M expression to identify source:

| Pattern | Source Type | What to Extract |
|---------|------------|-----------------|
| `Snowflake.Databases(...)` | Snowflake | Server, warehouse, role, database, schema, table name |
| `Sql.Database(...)` | SQL Server | Server, database, query/table |
| `PowerPlatform.Dataflows(...)` | Power Platform Dataflow | Workspace ID, dataflow ID, entity name |
| `Table.FromRows(Json.Document(...))` | Static/Embedded | Inline data (decode Base64) |
| DAX expression (type = "calculated") | DAX Calculated Table | Parse DAX CALENDAR/ADDCOLUMNS |
| `OData.Feed(...)` | OData | Service URL, entity |
| `Web.Contents(...)` | Web/REST API | URL |
| `Excel.Workbook(...)` | Excel | File path |

**Also identify:**
- Computed columns from Power Query (`Table.AddColumn` calls): FK/PK columns, derived columns, type transformations
- Power BI parameters (e.g., `SnowflakeDatabase1`) in M expressions

**⚠️ IMPORTANT:** Capture actual physical table names from M expressions (e.g., `Schema{[Name = "CUSTOMER_SCORECARD"]}`) as these differ from Power BI logical names.

**Output:** Source mapping table (PBI table → source type → physical name).

## Step 3: Analyze the Semantic Model

**Goal:** Document all tables, columns, relationships, and measures.

**3a. Tables and Columns:**
Categorize each column as:
- **Physical columns** (have `sourceColumn`) → Dimensions or Facts
- **Computed columns** (have DAX `expression`) → Note the expression
- **Power Query computed** (from M `Table.AddColumn`) → Note derivation

**3b. Relationships:**
Extract all relationships noting: fromTable, fromColumn, toTable, toColumn, isActive, crossFilteringBehavior. Flag inactive relationships (used via USERELATIONSHIP in DAX).

**3c. DAX Measures:**
Categorize every measure:
1. **Simple aggregations** (SUM, COUNT, AVG, MIN, MAX) → Convert directly to SQL metrics
2. **Ratio/division** (DIVIDE) → Convert to DIV0(...) metrics
3. **Period wrappers** (SELECTEDVALUE + SWITCH for MTD/QTD/YTD) → Document in AI_SQL_GENERATION
4. **Year-over-year** (SAMEPERIODLASTYEAR, PARALLELPERIOD) → Convert with date filter logic
5. **Rolling calculations** (DATESINPERIOD) → Document in AI_SQL_GENERATION
6. **Display/formatting** (FORMAT, UNICHAR, text concat) → SKIP (UI-only)
7. **Complex DAX** (CALCULATETABLE, SUMMARIZE) → Convert to closest SQL equivalent or note complexity

**3d. Row-Level Security (RLS):**
Document any RLS roles and table permissions.

**3e. Calculated Tables:**
Tables with `partitions[].source.type == "calculated"` are DAX-generated. Extract the DAX and plan SQL DDL conversion (e.g., CALENDAR → GENERATOR-based date table).

**⚠️ STOPPING POINT:** Present the full analysis to the user:
- Table mapping (PBI name → source → physical table)
- Column counts per table
- Measure categorization summary (N convertible, N skip, N manual review)
- Non-Snowflake sources identified
- RLS if present

## Step 4: Ask User for Target Configuration

**Ask** the user:
1. **Target database.schema** for the semantic view
2. **Semantic view name** (suggest based on PBI file name)
3. **Confirm** which non-Snowflake sources to comment out vs. include with placeholder table names
4. **Output file path** for the `.sql` file (suggest `<name>_semantic_view.sql` in working directory)

**⚠️ STOPPING POINT:** Wait for user confirmation before generating DDL.

## Step 5: Generate Complete .sql File

**Goal:** Produce a single `.sql` file containing ALL database objects and the semantic view DDL.

**Structure the .sql file as follows:**

```sql
/*
==========================================================================
  Power BI to Snowflake Semantic View
  Source: <filename or model name>
  Generated: <date>
  Target: <database>.<schema>.<semantic_view_name>

  Table Mapping:
    PBI Logical Name        → Snowflake Physical Table
    ────────────────────────────────────────────────────
    <table1>                → <db.schema.physical_table1>
    <table2>                → <db.schema.physical_table2>

  Non-Snowflake Sources (commented out):
    <table3>                → Power Platform Dataflow (not in Snowflake)

  Measure Conversion Summary:
    Converted:     N measures
    Skipped (UI):  N measures
    Manual Review: N measures
==========================================================================
*/

-- ============================================================
-- SECTION 1: SUPPORTING DATABASE OBJECTS
-- ============================================================

-- Date Dimension Table (converted from DAX CALENDAR)
CREATE TABLE IF NOT EXISTS <db>.<schema>.DIM_DATE AS
SELECT ...
FROM (SELECT ... FROM TABLE(GENERATOR(ROWCOUNT => ...)));

-- Helper Views (materialized FK columns, etc.)
CREATE OR REPLACE VIEW <db>.<schema>.<view_name> AS
SELECT ...
FROM ...;

-- ============================================================
-- SECTION 2: SEMANTIC VIEW
-- ============================================================

CREATE OR REPLACE SEMANTIC VIEW <db>.<schema>.<name>

  TABLES (...)
  RELATIONSHIPS (...)
  FACTS (...)
  DIMENSIONS (...)
  METRICS (...)

  COMMENT = '...'
  AI_SQL_GENERATION '...'
  AI_QUESTION_CATEGORIZATION '...'
;

-- ============================================================
-- SECTION 3: UNCONVERTED / MANUAL REVIEW ITEMS
-- ============================================================
-- The following DAX measures could not be automatically converted.
-- Manual SQL equivalents should be authored and added as metrics.
--
-- Measure: <name>
-- DAX: <expression>
-- Reason: <why it couldn't be converted>
```

**Rules for DDL generation:**

| Element | Rule |
|---------|------|
| Tables from Snowflake | Include with actual physical table name |
| Tables from non-Snowflake | Comment out with note: `-- sourced from <source>, not in Snowflake` |
| FK/PK computed columns | Include as dimensions (assume materialized in Snowflake) |
| Relationships using Snowflake tables | Include |
| Relationships involving non-SF tables | Comment out |
| Simple DAX aggregations | Convert to SQL metrics |
| Complex DAX measures | Convert to closest SQL equivalent |
| Display-only measures (FORMAT/UNICHAR) | Skip, document in Section 3 |
| Period wrappers (MTD/QTD/YTD) | Skip, document pattern in AI_SQL_GENERATION |
| Pipeline/non-SF dependent measures | Comment out |
| Inactive relationships | Comment out with note about USERELATIONSHIP usage |

**SYNONYMS:** Add synonyms for key business columns (customer name, product line, dates, status fields).

**AI_SQL_GENERATION:** Include instructions covering:
- Key metric definitions and business logic
- Period filtering approach (MTD/QTD/YTD via date filters)
- Any special calculation rules
- FK derivation logic for reference

**AI_QUESTION_CATEGORIZATION:** Describe the business domains this model covers.

**⚠️ STOPPING POINT:** Present the generated `.sql` file to the user for review before finalizing.

## Step 6: Save and Present Final Deliverables

**Save** the `.sql` file to the path confirmed in Step 4.

**Present** to user:
1. **File location** of the generated `.sql` file
2. **Summary** — table count, relationship count, metrics converted, items needing manual review
3. **Next steps** — deploy to Snowflake, create Cortex Agent, add verified queries

**If error occurs:**
- M expression unrecognizable: Flag as unknown source, ask user to identify
- DAX too complex to convert: Document in Section 3 of the .sql file
- Unknown error: Ask user for guidance

# Best Practices
- Always capture physical table names from M expressions, not Power BI logical names
- Column names with spaces require quoting in Snowflake DDL
- Semantic view RELATIONSHIPS only support column references (no expressions/CONCAT), so FK columns must be materialized
- Inactive PBI relationships correspond to DAX USERELATIONSHIP() and represent alternate join paths
- Power BI parameters appear in M expressions as variable references; resolve them to actual values when possible

# Stopping Points
- ✋ After Step 3 — Analysis review before DDL generation
- ✋ After Step 4 — Target configuration confirmation
- ✋ After Step 5 — Generated .sql file review before saving

**Resume rule:** Upon user approval, proceed directly to the next step without re-asking.

# Output
- `<name>_semantic_view.sql` — Single .sql file containing supporting objects + semantic view DDL + unconverted items documentation

# Examples

## Example 1: File-based reverse engineering
User: $powerbi-reverse-engineer convert ~/reports/sales.pbit to a semantic view in ANALYTICS_DB.REPORTING
Assistant: Extracts DataModelSchema, analyzes M expressions and DAX measures, presents analysis for review, generates complete .sql file with supporting DDL and semantic view.

## Example 2: MCP-based reverse engineering
User: $powerbi-reverse-engineer reverse engineer the model open in Power BI Desktop called "Customer 360" into a semantic view
Assistant: Connects via MCP, retrieves full model metadata, analyzes sources and measures, presents analysis, asks for target database.schema, generates .sql file.

## Example 3: Fabric workspace model
User: $powerbi-reverse-engineer convert semantic model "Finance Reporting" from workspace "Corp Finance" to FINANCE_DB.SEMANTIC_LAYER.FINANCE_SV
Assistant: Connects to Fabric via MCP, retrieves model, maps tables to Snowflake sources, converts DAX to SQL metrics, generates .sql file with all DDL.
