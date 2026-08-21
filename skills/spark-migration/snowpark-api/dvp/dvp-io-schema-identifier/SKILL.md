---
name: dvp-io-schema-identifier
description: "Identify input and output data (files and tables) from source workloads and infer column schemas. Triggers: identify IO, find inputs outputs, map data flow, discover tables files, extract schema."
---

# DVP IO Identifier

## Overview

Scans the source PySpark code in `dvp/01-source/` to identify all data inputs and outputs (files and tables), then infers column schemas for each entry. Produces `dvp/04-results/data_io_schema.json` — a unified inventory with full schema information that downstream skills use for test data generation and test setup.

This skill combines IO detection and schema extraction in a single pass, producing a complete `data_io_schema.json` with all fields populated.

## Output Format

The skill produces `dvp/04-results/data_io_schema.json` as an array of IO entries, each with full schema information:

```json
[
  {
    "name": "raw_transactions.csv",
    "full_name": "raw_transactions.csv",
    "source": "workload.py:29",
    "type": "file",
    "format": "csv",
    "detection": "static",
    "role": "input",
    "path": "input/raw_transactions.csv",
    "columns": [
      {"name": "transaction_id", "type": "STRING", "confidence": "evidence"},
      {"name": "customer_id", "type": "STRING", "confidence": "evidence"},
      {"name": "product_id", "type": "STRING", "confidence": "evidence"},
      {"name": "transaction_date", "type": "DATE", "confidence": "pattern"},
      {"name": "transaction_amount", "type": "DECIMAL", "confidence": "pattern"},
      {"name": "return_amount", "type": "DECIMAL", "confidence": "ambiguous"}
    ]
  },
  {
    "name": "TOP_CATEGORIES",
    "full_name": "TOP_CATEGORIES",
    "source": "workload.py:94",
    "type": "table",
    "format": "",
    "detection": "inferred",
    "role": "output",
    "path": "",
    "key_columns": ["category"],
    "columns": [
      {"name": "category", "type": "STRING", "confidence": "evidence"},
      {"name": "total_sales_count", "type": "DECIMAL", "confidence": "evidence"},
      {"name": "category_rank", "type": "DECIMAL", "confidence": "evidence"}
    ]
  }
]
```

### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Short name: filename for files (`raw_transactions.csv`), table name for tables (`CUSTOMER_MASTER`) |
| `full_name` | string | Same as `name` for basic cases (no database/schema prefix) |
| `source` | string | File and line number where the IO operation was found (`workload.py:29`) |
| `type` | string | `file` or `table` |
| `format` | string | File format (`csv`, `parquet`, `json`, etc.). Empty for tables |
| `detection` | string | `static` (explicit in code) or `inferred` (derived from context) |
| `role` | string | `input` or `output` |
| `path` | string | Relative file path as found in code. Empty for tables |
| `columns` | array | Column definitions: `[{"name": "...", "type": "...", "confidence": "..."}]` |
| `key_columns` | array | (outputs only) Columns used in groupBy/distinct/join keys |

### Confidence Values

| Confidence | Meaning |
|------------|---------|
| `evidence` | Strong evidence from code: explicit schema, join key, select, alias, or qualified `df["col"]` |
| `pattern` | Inferred from naming convention (`_date` → DATE, `_amount` → DECIMAL) or usage pattern |
| `ambiguous` | Column appears in a join context and could belong to either side — the detector could not determine the true owner |
| `placeholder` | No evidence; column added with generic schema as a fallback |

### Type Values

| Type | Read Pattern (PySpark) | Write Pattern (PySpark) |
|------|------------------------|-------------------------|
| `file` | `spark.read.csv("path")`, `spark.read.parquet("path")` | `.write.csv("path")`, `.write.parquet("path")` |
| `table` | `spark.table("NAME")`, `spark.read.table("NAME")` | `.write.saveAsTable("NAME")` |

### Detection Values

| Detection | Description |
|-----------|-------------|
| `static` | The IO operation has a literal string path/name directly in the code |
| `inferred` | The path/name is built from variables, environment vars, or f-strings |

## Output Format

Every time you begin a step, sub-step, or significant action, prefix the message with a timestamp in the format `[YYYY-MM-DD HH:MM:SS]`. Obtain the current time by running `date '+%Y-%m-%d %H:%M:%S'` in bash.

Example:
```
[2026-03-24 14:05:32] Starting Step 1: Analyze source files for I/O...
[2026-03-24 14:05:45] Identified 4 inputs and 2 outputs
[2026-03-24 14:05:46] Step 1 complete.
```

## Workflow

### Step 0: Initialize Git

Ensure the workload directory has a git repository on the `sma/migration-process` branch. This is idempotent — if the orchestrator already initialized git, this is a no-op.

```python
result = sma_api.git_ensure_ready("<workload_path>")
```

### Step 1: Validate ASG and Load Entrypoints

**Before any processing**, verify the ASG file exists:
- Check `dvp/04-results/*_asg.json` exists. If not, **STOP with error**: `"ERROR: No ASG found. The ASG is MANDATORY for IO identification. Run dvp-asg-generation first."`
- Do NOT fall back to scanning source code without the ASG.

Then read `dvp/04-results/entrypoints.json` to know which files and functions to analyze. Focus on entries with `status: "validated"` or `status: "pending"`.

### Step 2: Scan for IO Operations

For each source file referenced by an entrypoint, scan for data read/write operations using AST analysis or pattern matching.

#### Input Detection Patterns

| Pattern | Type | Example |
|---------|------|---------|
| `spark.read.csv(path)` | file | `reader.csv(f"{INPUT_PATH}/raw_transactions.csv")` |
| `spark.read.parquet(path)` | file | `spark.read.parquet("input/data.parquet")` |
| `spark.read.json(path)` | file | `spark.read.json("input/config.json")` |
| `spark.read.option(...).csv(path)` | file | `spark.read.option("header", True).csv(path)` |
| `spark.table(name)` | table | `spark.table("CUSTOMER_MASTER")` |
| `spark.read.table(name)` | table | `spark.read.table("PRODUCT_CATALOG")` |

#### Output Detection Patterns

| Pattern | Type | Example |
|---------|------|---------|
| `.write.csv(path)` | file | `df.write.mode("overwrite").csv("output/SUMMARY", header=True)` |
| `.write.parquet(path)` | file | `df.write.parquet("output/result")` |
| `.write.json(path)` | file | `df.write.json("output/report")` |
| `.write.saveAsTable(name)` | table | `df.write.mode("overwrite").saveAsTable("TOP_CATEGORIES")` |

### Step 3: Extract Names and Paths

For each detected IO operation:

1. **Resolve the name/path argument:**
   - Literal string → `detection: "static"`
   - f-string or variable → `detection: "inferred"`, resolve if possible

2. **Determine the name:**
   - Files: use the filename (e.g., `raw_transactions.csv`) or directory name (e.g., `DAILY_SALES_SUMMARY`)
   - Tables: use the table name as-is (e.g., `CUSTOMER_MASTER`)

3. **Set full_name equal to name** (no database/schema prefix for basic cases)

4. **Determine format** from the read/write method:
   - `.csv()` → `"csv"`
   - `.parquet()` → `"parquet"`
   - `.json()` → `"json"`
   - `.table()` / `.saveAsTable()` → `""` (no format for tables)

5. **Record the source location** as `filename:lineno`

### Step 4: Infer Column Schemas

For each IO entry, search the source code for schema information using the following sources (ordered by confidence):

| Source | Confidence | Example |
|--------|------------|---------|
| Explicit `StructType` definitions | High | `StructType([StructField("id", IntegerType()), ...])` |
| `.schema()` calls | High | `df.schema` |
| Column casts/conversions | Medium | `col("amount").cast("decimal(18,2)")` |
| `select()` with aliases | Medium | `df.select(col("x").alias("id"))` |
| SQL DDL in string literals | Medium | `CREATE TABLE ... (id INT, ...)` |
| Column references in transformations | Low | `df["column_name"]`, `df.column_name` |
| Variable naming patterns | Low | Heuristic-based |

For outputs, also identify **key columns** from `groupBy()`, `distinct()`, or join keys used in the transformation that produces the output.

#### Type Mapping (Spark → Snowflake)

| Spark Type | Snowflake Type |
|-----------|---------------|
| `IntegerType` | `INT` |
| `LongType` | `BIGINT` |
| `StringType` | `STRING` |
| `DoubleType` | `DOUBLE` |
| `FloatType` | `FLOAT` |
| `DecimalType(p,s)` | `DECIMAL(p,s)` |
| `BooleanType` | `BOOLEAN` |
| `DateType` | `DATE` |
| `TimestampType` | `TIMESTAMP` |
| `ArrayType` | `ARRAY` |
| `MapType` | `OBJECT` |
| `StructType` | `OBJECT` |

### Step 5: Deduplicate

The same data source may be read multiple times in the code. Deduplicate by `(name, type, role)`:
- Keep the first occurrence (lowest line number) as the `source` reference
- If a name appears as both input and output, keep both entries (different roles)
- Merge column information from multiple references to the same entry

### Step 6: Validate Against Source

Each entry in `data_io_schema.json` must correspond to an actual IO operation in the source code. Do not invent entries that don't exist in the source.

### Step 7: Present Findings

```
IO Identification & Schema Extraction
======================================

Source: dvp/01-source/workload.py

Inputs (5):
  FILES:
    1. raw_transactions.csv    workload.py:29    csv     static    5 columns
    2. returns_data.csv        workload.py:32    csv     static    4 columns
    3. exchange_rates.csv      workload.py:98    csv     static    3 columns
  TABLES:
    4. CUSTOMER_MASTER         workload.py:59            static    4 columns
    5. PRODUCT_CATALOG         workload.py:76            static    4 columns

Outputs (5):
  FILES:
    6. DAILY_SALES_SUMMARY     workload.py:50    csv     inferred  4 columns
    7. CUSTOMER_CLV            workload.py:73    csv     inferred  5 columns
    8. FOREIGN_TRANSACTIONS    workload.py:112   csv     inferred  3 columns
  TABLES:
    9. TOP_CATEGORIES          workload.py:94            inferred  3 columns
   10. UNMATCHED_TRANSACTIONS  workload.py:122           inferred  2 columns

Schema coverage: 10/10 entries have columns defined

Saved to dvp/04-results/data_io_schema.json

Next: Run dvp-synthetic-data-generator to generate test data.
```

### Step 8: Commit Changes to Git

After IO schema is identified and written, commit the changes:

```python
result = sma_api.git_commit("<workload_path>", """DVP IO Schema: Identified N inputs and M outputs

Inputs: N (X files, Y tables)
Outputs: M (X files, Y tables)
Schema coverage: N/N entries have columns defined
Output: dvp/04-results/data_io_schema.json""")
```

Verify branches:
```python
result = sma_api.git_verify_branches("<workload_path>")
```

## SMA Reports as Hints

When available, SMA reports can provide additional hints for IO identification:

| Report | Useful For |
|--------|-----------|
| `IOFilesInventory.csv` | File IO operations detected by SMA |
| `DataFramesInventory.csv` | DataFrame operations and potential table references |
| `SparkUsagesInventory.csv` | Spark API calls including read/write operations |

These are **hints only** — the source of truth is always the actual source code. SMA reports may list operations that don't correspond to real IO (e.g., intermediate DataFrames) or miss dynamically constructed paths.

## Execution Order

```
dvp-orchestrator  →  stage-conversion  →  dvp-notebook-to-script
      |
      v
dvp-asg-generation  →  XX_asg.json + XX_anomalies.json
      |
      v
dvp-entrypoint-identifier  →  entrypoints.json
      |
      v
dvp-code-adapter  (session injection + enableHiveSupport() + defer env vars)
      |
      v
dvp-io-schema-identifier  ← THIS SKILL  →  data_io_schema.json
      |
      v
dvp-synthetic-data-generator  →  04-results/synthetic_data/*.csv + reports
      |
      v
dvp-test-setup-generator  →  03-tests/
```

## Inputs

| Input | Required | Location |
|-------|----------|----------|
| ASG JSON | **MANDATORY** | `dvp/04-results/*_asg.json` — **If this file does not exist, STOP with error. Do NOT attempt IO identification without the ASG.** |
| Source code | Yes | `dvp/01-source/` |
| Entrypoints inventory | Yes | `dvp/04-results/entrypoints.json` |
| IOFilesInventory.csv | No | `<output>/Reports/IOFilesInventory.csv` |
| SparkUsagesInventory.csv | No | `<output>/Reports/SparkUsagesInventory.csv` |

## Outputs

| Output | Format | Location |
|--------|--------|----------|
| IO inventory with schemas | JSON | `dvp/04-results/data_io_schema.json` |

## Stopping Points

- **No entrypoints found:** Stop and ask user to run `dvp-entrypoint-identifier` first
- **No IO operations detected:** Warn user — the workload may use dynamic paths or unsupported patterns
- **Ambiguous path resolution:** When a path is fully dynamic (no literal hints), mark as `detection: "inferred"` and flag for user review
- **No schema inferred:** Leave `columns` empty and flag for user review

## Edge Cases

| Case | Handling |
|------|----------|
| Path built from env var | Extract default value if available, mark `detection: "inferred"` |
| Path built from config file | Mark `detection: "inferred"`, use variable name as hint |
| Same data read as file AND table | Create separate entries — each reflects the actual access pattern |
| Nested function calls for IO | Follow call chain within the same file to find the actual read/write |
| IO inside loops | Single entry — the loop iterates over data, not different sources |
| Conditional IO (if/else) | One entry per distinct source, even if only one executes at runtime |
| No schema found for an entry | Leave `columns` empty, flag for AI or user resolution |
| Conflicting type inferences | Use highest-confidence source, note conflict in diagnostic report |

## Embedded Library

The IO detection and schema inference logic is borrowed from the warp-suite `DataIODetector`:

```
dvp/
  dvp-orchestrator/
    warp_core/               <-- SHARED: diagnostics, spark/pandas function registries
  dvp-io-schema-identifier/
    warp/
      data_io/               <-- warp-suite DataIODetector
    scripts/
      io_schema_identifier.py
```

The `DataIODetector` operates on ASG JSON (not source code), using a 6-phase type inference pipeline:
1. ASG seed (high-confidence `inferred_columns` from the parser)
2. Code analysis (from `inferred_output` fields on transformations)
3. StructField explicit schemas
4. Filter condition analysis (evidence: `col == 'value'` implies STRING)
5. Function usage inference (evidence: `F.sum(col)` implies DECIMAL)
6. Pattern-based inference (fallback: `_id` implies INT, `_dt` implies DATE)

With join propagation between phases to spread inferred types across related tables.

### Python Dependencies

```bash
pip install pydantic
```

## Design Considerations

- Combines IO detection and schema extraction in a single pass from the ASG
- Uses evidence-based type inference before pattern-based fallbacks
- Should merge information from multiple code locations for the same table
- Column names use lowercase (matching PySpark conventions); Snowflake auto-uppercases at runtime
- The output format (`columns` array of `{name, type}` objects) is consumed by `dvp-synthetic-data-generator` and `dvp-test-setup-generator`

## Final Summary

**MANDATORY**: After completing all steps (whether running standalone or invoked from the orchestrator), ALWAYS present this summary table:

```
IO Schema Identification Complete

┌──────────────┬──────────┬──────────────────────────────────────────────────────────┐
│ Step         │ Status   │ Details                                                  │
├──────────────┼──────────┼──────────────────────────────────────────────────────────┤
│ IO Schema    │ Done     │ N inputs, M outputs identified                           │
└──────────────┴──────────┴──────────────────────────────────────────────────────────┘

Output location: <output>/

Git branches:
• main — original code (unmodified)
• sma/migration-process — IO schema changes applied
```

**Rules:**
- Replace `N` and `M` with actual counts of inputs and outputs identified
- Status is `Done`, `Skipped`, or `Failed`
- If no IO was detected, show `Skipped` with reason
- If identification failed, show `Failed` with brief error
- The git branches section uses `sma_api.git_verify_branches()` to confirm both branches exist
