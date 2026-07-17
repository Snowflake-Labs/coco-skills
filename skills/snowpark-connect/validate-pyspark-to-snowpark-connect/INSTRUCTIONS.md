
# Validate PySpark to Snowpark Connect Migration

Run the migrated `_scos` workload by importing its real functions/classes and executing them with synthetic data.

## When to Load

[snowpark-connect] Intent Detection: After user indicates validation intent (validate, verify, check, test, review migration).

## Arguments

- `$ARGUMENTS` - Path to migrated `_scos` script, notebook, or directory.

**Derived names used throughout:**
- `<workload>` = basename of `$ARGUMENTS` (e.g., if `$ARGUMENTS` is `/path/to/my_pipeline_scos.py`, then `<workload>` = `my_pipeline_scos`)
- `<workload>_test/` = test directory created alongside the workload

## Rules

1. **Never modify the migrated workload.** Create an editable copy in `<workload>_test/` and add a single `entrypoint.py` file that triggers the main execution flow.
2. **Create synthetic data in the entrypoint.** Mock all external data sources by creating DataFrames before calling workload functions.
3. **Minimal synthetic data.** 2-5 rows per source. Only include columns actually used by the workload.
4. **CRITICAL: Import and call the REAL workload functions.** The entrypoint must import the actual migrated code (e.g., `from modeling_library import model, load_data`) and call it. Do NOT rewrite or duplicate workload logic — no independent test cases like "Test window functions" or "Test joins".
5. **This is a smoke test, not a unit test suite.** The goal is to verify the workload runs end-to-end without exceptions, not to test individual operations or assert data correctness.

## Prerequisites

```bash
uv --version || echo "PREREQ_FAIL: uv not installed"
python --version || echo "PREREQ_FAIL: python not installed"
python -c "from snowflake import snowpark_connect; spark = snowpark_connect.init_spark_session(); print('OK')" || echo "PREREQ_FAIL: Snowflake connection failed"

# Check 4 (notebook workloads only): jupyter nbconvert
jupyter nbconvert --version || echo "PREREQ_FAIL: jupyter nbconvert not installed (required for .ipynb workloads). Install with: pip install nbconvert"
```


## Workflow

You **MUST** perform the phases below **in order**.

### Step 1: Analyze Workload

#### 1.1 Validate migrated workload exists

```bash
test -e "$ARGUMENTS" || echo "ABORT: Migrated workload not found"
```

#### 1.2 Identify external data dependencies

Find all external data access in the workload: `spark.read.*`, `spark.table()`, `spark.sql("SELECT ... FROM ...")`, `boto3`/S3.

**For `.py` files**, search the source directly. **For `.ipynb` notebooks**, search within the `source` arrays of code cells in the notebook JSON.

For each source, determine:
- Table/view name used in spark.table() calls
- Column names and types (infer from downstream usage)

#### 1.3 Analyze workload hierarchy and find entrypoint

Read **ALL files** in the `_scos` workload (both `.py` and `.ipynb`). Build a complete picture of the module/class/function hierarchy:
- Which modules import which other modules
- Which functions call which other functions
- What is the call graph from top-level to low-level functions

**For `.py` files:** The **main entrypoint** is determined by analyzing this entire hierarchy - it's the function at the TOP of the call graph that orchestrates the entire pipeline. Look for:
- High-level functions like `model()`, `run()`, `main()`, `process()`
- Functions that orchestrate other functions (call load, transform, save)
- `if __name__ == "__main__"` blocks

**For `.ipynb` notebooks:** Notebooks execute top-to-bottom through all code cells. The entire notebook IS the entrypoint. To make it importable for the test entrypoint, convert it to a Python script using `jupyter nbconvert` (verified in Prerequisites):

```bash
# Convert notebook to .py script for import
jupyter nbconvert --to script <notebook>_scos.ipynb --output <notebook>_scos_converted
```

The converted script can then be imported from the entrypoint.

**CRITICAL**: The entrypoint must be identified by understanding the full workload structure, not by guessing based on function names. Trace the call hierarchy to find the top-level function that a real customer would invoke to run the entire pipeline.


### Step 2: Setup Test Directory

#### 2.1 Copy workload and create entrypoint

```bash
mkdir -p <workload>_test/

# Copy the migrated workload (works for both .py files, .ipynb notebooks, and directories)
cp -r $ARGUMENTS <workload>_test/

# For notebook workloads, also convert to .py for importing (see Phase 1.3)

# entrypoint.py will be created in the root of the test directory
```

Create `<workload>_test/entrypoint.py` in the root of the test directory.

**Strict Entrypoint Requirements:**
1. **Single File:** All test logic resides in `entrypoint.py`.
2. **Order of Operations:** Init Spark -> Register ALL Synthetic Data as TempViews -> Import Workload -> Call Real Workload Functions.
3. **Data Mocking:** ALL tables accessed via `spark.table()` must be mocked as TempViews BEFORE importing workload modules (since some modules read tables at import time).
4. **Call Real Functions:** Import and call the actual workload functions (e.g., `model()`, `load_data()`). Do NOT rewrite the workload logic in the entrypoint.
5. **Environment:** Must run successfully with `uv run --project <SKILL_DIRECTORY> python entrypoint.py` in the test directory.
6. **NO independent test cases:** Do NOT write code like "Test window functions", "Test joins", etc. The workload code already contains these operations - just call the workload.

**entrypoint.py template:**
```python
"""
Full workload test entrypoint for <workload_name> SCOS migration.
Initializes synthetic data and runs the processing pipeline.
"""
import os
import sys

# Set required environment variables BEFORE any imports
os.environ["SPARK_CONNECT_MODE_ENABLED"] = "1"
os.environ["CATALOG"] = "test_catalog"
# ... other env vars the workload expects ...

# Add the workload to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from snowflake import snowpark_connect
from decimal import Decimal
from pyspark.sql.types import StructType, StructField, StringType, IntegerType, DecimalType

# Initialize SCOS session
spark = snowpark_connect.init_spark_session()

# ============================================================
# SYNTHETIC DATA - Register TempViews for ALL external tables
# ============================================================
# IMPORTANT: Register these BEFORE importing the workload modules,
# because some modules call spark.table() at import time.

# Example: Mock the main data table
data_schema = StructType([
    StructField("id", StringType(), True),
    StructField("amount", DecimalType(38, 18), True),
    # Add columns used by workload
])
data = [("1", Decimal("100.00")), ("2", Decimal("200.00"))]
spark.createDataFrame(data, data_schema).createOrReplaceTempView("main_table")

# ... register ALL other tables the workload accesses ...

# ============================================================
# RUN WORKLOAD - Import and call the REAL functions
# ============================================================
# Now import the workload (after tables are mocked)
from <workload_module> import <main_function>

print("Running workload...")
result = <main_function>(
    # Pass appropriate arguments
)

# Optionally show results
result.show()
print("SUCCESS: Workload completed")
```

**CRITICAL**: The entrypoint MUST call the real workload functions. For example:
- If the workload has `model(data)`, call `model(data)`
- If the workload has `load_data()` followed by `process()`, call both
- Do NOT reimplement the workload logic with simple test operations

**Module-level code handling:**
- If the workload reads tables at module import time, ensure TempViews are registered BEFORE importing
- If the workload has `if __name__ == "__main__"` blocks, copy that code to the entrypoint
- For notebook workloads: import the converted `.py` script (from Phase 1.3 conversion) instead of the `.ipynb` file directly. If the notebook has top-level code (not wrapped in functions), the converted script will execute that code on import - ensure all TempViews are registered first.

#### 2.2 Synthetic data generation rules

- **Minimal data:** 2-5 rows per table is sufficient for a smoke test
- **Cover key paths:** Include at least one row that matches join conditions
- **Include nulls sparingly:** Only where the workload explicitly handles nulls

#### 2.3 Schema inference

Infer schema from how data is used in the workload:
- Look at `spark.table("table_name")` calls and trace how columns are used
- Column references: `df.select("col1", "col2")` → columns col1, col2
- Type hints: `.cast("int")`, `IntegerType()` → integer column
- Operations: `.filter(df.amount > 0)` → numeric column
- Joins: `df1.join(df2, "key")` → both have "key" column

#### 2.4 Mocking external dependencies

**Tables/Views:** Use `spark.createDataFrame(...).createOrReplaceTempView("table_name")`


### Step 3: Run Entrypoint

```bash
cd <workload>_test/
uv run --project <SKILL_DIRECTORY> python entrypoint.py > output.log 2>&1
EXIT_CODE=$?
```

**If the run fails** (`EXIT_CODE != 0`): Do NOT attempt to fix and retry. Read `output.log`, include the error details in the Phase 4 report, and present to user.


### Step 4: Report

```
════════════════════════════════════════════════════════════
FULL WORKLOAD TEST
════════════════════════════════════════════════════════════

Workload: $ARGUMENTS
Test directory: <workload>_test/
Entrypoint: entrypoint.py

Workload functions called:
- <function_name>(<args>) from <module>
- <function_name>(<args>) from <module>

Mocked tables (TempViews):
- <table_name> (N rows): col1, col2, ...
- <table_name> (N rows): col1, col2, ...

Exit code: <0 or N>

Output:
<show relevant output or errors>

════════════════════════════════════════════════════════════
RESULT: ✅ SUCCESS | ❌ FAILED
════════════════════════════════════════════════════════════
```

**Success criteria:** The workload's main function(s) execute without throwing exceptions. The test verifies that the migrated code is syntactically correct and compatible with SCOS APIs - not that business logic produces correct results.


## Stopping Points

- ✋ After Step 1: After analyzing workload hierarchy — verify the identified entrypoint and data dependencies are correct before creating test directory
- ✋ After Step 4: After reporting results — present the report to user

## Success Criteria

- The workload's main function(s) execute without throwing exceptions
- The migrated code is syntactically correct and compatible with SCOS APIs
- All external data sources are mocked as TempViews
- The entrypoint calls real workload functions (not reimplemented logic)

## Output

Validation report (Phase 4 format) summarizing pass/fail status, workload functions called, mocked tables, and any errors encountered.

## Troubleshooting

**ImportError** - Ensure `sys.path` includes the parent directory of the `_scos` workload.

**Module-level code fails** - External data accessed on import; register ALL temp views BEFORE importing the workload module.

**Schema mismatch at runtime** - Re-check column names/types used downstream and update synthetic data.

**Missing dependency** - The workload imports a module not available; install it or report as limitation.

