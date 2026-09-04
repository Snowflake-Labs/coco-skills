

# Migrate PySpark to SCOS

Migrate a PySpark workload to be compatible with Snowflake SCOS (Snowpark Connect for Spark).

## When to Load

[snowpark-connect] Intent Detection: After user indicates migration intent (convert, migrate, update imports, rewrite for SCOS).

## Arguments

- `$ARGUMENTS` - Path to the PySpark file or directory to migrate

## Prerequisites

### SCOS Local Environment

Before migrating, verify the local SCOS testing environment is ready:

```bash
conda activate scos && python -c "from snowflake import snowpark_connect; print('SCOS environment OK')" 2>/dev/null || echo "SCOS_ENV_NOT_READY"
```

**If output is `SCOS_ENV_NOT_READY`**: The local SCOS environment is not set up. **Load** `scos-local-testing/SKILL.md` first to create the conda environment and configure the Snowflake connection. Return to this skill after setup is complete.

**⚠️ MANDATORY STOPPING POINT**: Do NOT proceed with migration until the SCOS local environment is confirmed working.

### uv Package Manager

Check if uv is installed:
```bash
uv --version
```

If not installed:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Snowflake Connection

A valid Snowflake connection must be configured. The default connection name is `default`, but you can specify a different name using `--connection <name>`.

### RAG Knowledge Base

The analyzer requires a Cortex Search Service with known SCOS compatibility issues. Step 0 will check and initialize this automatically if needed.

## Tools

### Tool: analyze_pyspark.py

**Description**: Analyzes PySpark scripts for SCOS compatibility issues using RAG-based pattern matching and LLM validation.

**Usage:**
```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/analyze_pyspark.py \
  --path <FILE_OR_DIR> \
  --output-format json > analysis.json
```

**Arguments:**
- `--path`: Path to PySpark file or directory (required)
- `--output-format`: Output format - `text` or `json` (default: text)
- `--risk-threshold`: Minimum risk to report 0-1 (default: 0.1)
- `--connection`: Snowflake connection name (default: default)

**When to use:** First step of any migration

## Workflow

You are an expert migration agent specializing in converting PySpark workloads to run on Snowflake SCOS (Snowpark Connect for Spark). 
Your goal is to produce a functional, SCOS-compatible version of the provided code while preserving the original business logic.
You **MUST** perform the steps below **STEP by STEP**.

### Step 0: Setup RAG Resources (One-Time)

**Goal:** Ensure the RAG knowledge base exists for compatibility analysis.

**Check if RAG is already initialized:**
```bash
uv run --project <SKILL_DIRECTORY> \
  python -c "
from snowflake.snowpark import Session
session = Session.builder.config('connection_name', 'default').create()
result = session.sql('''
SELECT COUNT(*) as cnt FROM SCOS_MIGRATION.INFORMATION_SCHEMA.CORTEX_SEARCH_SERVICES 
WHERE SERVICE_NAME = 'SCOS_COMPAT_ISSUES_SERVICE'
''').collect()
print('EXISTS' if result[0]['CNT'] > 0 else 'NOT_FOUND')
"
```

**If output is `EXISTS`**, skip to Step 1.

**If output is `NOT_FOUND`**:

⚠️ **MANDATORY STOPPING POINT** - Do NOT proceed without user input.

Ask the user:
```
The RAG knowledge base is not set up yet. I need to initialize it once.

Please provide your Snowflake warehouse name for creating the Cortex Search Service:
```

Wait for the user to provide the warehouse name, then run:
```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/rag/scos_rag.py --warehouse <USER_PROVIDED_WAREHOUSE>
```

**Note:** This setup only needs to run once per Snowflake account. Subsequent migrations will reuse the existing RAG resources.

### Step 1: Analyze the Workload

Run the compatibility analysis tool to detect issues and output them to a JSON file:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/analyze_pyspark.py \
  --path $ARGUMENTS --output-format json > analysis.json
```

**Wait for the analysis to complete.**

Then, read the `analysis.json` file. It contains a list of potential compatibility issues with the following structure:

```json
[
  {
    "file": "src/etl/transformations.py",
    "lines": "142-142",
    "code": "combined = df1.unionByName(df2, allowMissingColumns=True)",
    "final_risk": 0.4,
    "root_cause": "unionByName with allowMissingColumns may fail if there are type mismatches between corresponding columns in the two DataFrames",
    "explanation": "This code may fail if the DataFrames have columns with matching names but incompatible types. If schemas are compatible or only missing columns exist, it should work correctly.",
    "fix": "Ensure column types match between DataFrames before union, or explicitly cast columns to compatible types",
    "confidence": "MEDIUM"
  }
]
```

**Fields**:

- `file`: Path to the source file
- `lines`: Line range of the problematic code
- `code`: The code snippet flagged for review
- `final_risk`: Float (0.0-1.0) indicating failure probability
- `root_cause`: Why this code may fail in SCOS
- `explanation`: Detailed explanation of the risk
- `fix`: Suggested fix (may be `null` if no direct fix)
- `confidence`: Prediction confidence (HIGH/MEDIUM/LOW)


### Step 2: Create Migration Copy

**⚠️ CRITICAL: NEVER modify the original files. You MUST create a copy first.**

```bash
# For a single file:
cp $ARGUMENTS ${ARGUMENTS%.py}_scos.py

# For a directory:
cp -r $ARGUMENTS ${ARGUMENTS}_scos
```

If it is a directory, do not add or remove any files from the copy. Both directories MUST have exactly the same structure.

**Verify the copy exists before proceeding:**
```bash
# For a single file:
ls -la ${ARGUMENTS%.py}_scos.py

# For a directory:
ls -la ${ARGUMENTS}_scos/
```

**⚠️ MANDATORY STOPPING POINT**: Do NOT proceed to Step 3 until the copy is confirmed. ALL subsequent edits MUST target ONLY the `_scos` copy. If you find yourself editing the original path, STOP immediately.

### Step 3: Apply Fixes from the Analysis output

**For EACH issue in `analysis.json`**, perform the following:

1. **Locate the issue**: Find the code at `file` and `lines` in the **copied** directory.
2. **Assess the risk**: Check the `final_risk` value.
3. **Apply the appropriate action** based on the rules below.
4. **Document the action**: Next to the code chunk that you've just processed **ALWAYS** add a code comment explaining the potential issue root cause and explain the decision you have made: `# SCOS: <explanation>`. Add a comment regardless of whether you have decided to apply a fix or not.

**Rules for Fixing based on Risk Score:**
1. **Must Fix (`final_risk` >= 0.7)**: These are critical compatibility issues. You **MUST** apply a fix or rewrite the logic. If no direct fix is available, you must rewrite the code to avoid the unsupported feature.
2. **Should Fix (0.3 <= `final_risk` < 0.7)**: These are likely issues. You **SHOULD** apply a fix if one is suggested. If unsure, add a warning comment or TODO.
3. **Fix if possible (`final_risk` < 0.3)**: These are minor risks or potential false positives. You **MUST still review them** and apply a fix if possible. If the code is safe, just add a comment `# SCOS: <explanation>`.

**General Rules:**
1.  **Use the Tool's Fix**: If the issue object provides a `fix` value, use it. It is tailored to the specific error.
2.  **Handle RDDs**: RDD operations (`final_risk` near 1.0) are not supported. You MUST rewrite them using DataFrame transformations or SQL expressions. **Read** `references/rdd-conversion.md` for detailed conversion rules and examples.
3.  **Unsupported Formats**: Change file formats if required (e.g., ORC/Avro -> Parquet).
4.  **No-Op Operations**: For operations like `hint()` or `repartition()` that are ignored in SCOS, either remove them or comment them out with a note: `# SCOS: No-op, ignored`.
5.  **No-Op Configs**: Spark configs that are not supported by SCOS (category: "No-Op Config") are silently ignored. Add a comment next to the config setting: `# SCOS: No-op config, ignored by SCOS`. Common no-op configs include `spark.sql.shuffle.partitions`, `spark.executor.memory`, `spark.driver.memory`, `spark.sql.adaptive.enabled`, etc.
6.  **Missing Fixes**: If `fix` is null, use the `root_cause` to determine the best workaround. If unsure, add a TODO comment: `# SCOS: TODO - <explanation>`.
7.  **File Reads**: For file read operations (`.read.csv`, `.read.json`, `.read.parquet`, `.load`), check the path being read:
    -   **Already using Snowflake stage** (`@STAGE_NAME/...` or `@~/...`): No comment needed, this is optimal.
    -   **External cloud storage** (paths starting with `s3://`, `s3a://`, `gs://`, `abfs://`, `wasb://`, `adl://`): Add performance comment recommending Snowflake stage upload.
    -   **Local paths or variables**: If the path is a variable, trace it to determine if it's external cloud storage. Add performance comment recommending Snowflake stage upload for both.
    
    ```python
    # SCOS: Performance tip - Consider uploading this file to a Snowflake stage
    # for faster processing. Use: session.file.put("local_path", "@STAGE_NAME/path")
    df = spark.read.csv("s3://bucket/path/file.csv", header=True)
    ```

#### Issue Processing Checklist

After processing all issues, verify completeness:

- [ ] Each issue has been reviewed
- [ ] All high-risk issues (`final_risk` >= 0.7) have fixes applied
- [ ] All medium-risk issues (`final_risk` >= 0.3) have fixes or TODO comments
- [ ] All low-risk issues (`final_risk` < 0.3) have fixes or TODO comments

**Do NOT proceed to Step 4 until ALL issues have been addressed.**

### Step 4: Update Imports and Session Creation

SCOS requires using the Snowpark Connect client. You must update imports and session initialization.

**⚠️ CRITICAL: For directory migrations, you MUST apply Steps 4 and 5 to EVERY `.py` file in the copied directory.**

Before proceeding, enumerate all Python files that need to be updated:
```bash
# For a directory migration, list all Python files:
find ${ARGUMENTS}_scos -name "*.py" -type f
```

You MUST iterate through this entire list and update each file individually. Do not skip any file.

#### 4.1 Update Session Initialization
**Identify the main entry point of the application.**

Initialize the Snowpark Connect session **ONLY ONCE** in the main entry point (e.g., `main.py` or the primary script).

**In the main entry point ONLY, replace session creation with:**
```python
from snowflake import snowpark_connect

spark = snowpark_connect.init_spark_session()
```

**In all other files:**
- Remove redundant session initialization.
- Ensure the file uses the active session (e.g., via `snowpark_connect.get_session()` after updating imports - make sure there is `from snowflake import snowpark_connect` import, or by passing the `spark` object).

#### 4.2 Remove Unsupported Imports
**For EACH Python file**, remove imports that are NOT supported in SCOS.

**Imports to REMOVE:**

| Unsupported Import | Action |
| :--- | :--- |
| `databricks.connect` | Remove - use `snowpark_connect` in entry point |
| `databricks.sdk.runtime` | Remove |
| `delta.tables` | Remove - Delta format not supported |

**Example Transformation:**
```python
# BEFORE
from pyspark.sql import SparkSession
from databricks.connect import DatabricksSession
from databricks.sdk.runtime import dbutils

# AFTER
from pyspark.sql import SparkSession
# databricks imports removed - not supported in SCOS
```

**Note:** Standard PySpark imports (`pyspark.sql.functions`, `pyspark.sql.types`, etc.) are generally supported and do NOT need to be changed.

### Step 5: Add Migration Header

**For EACH Python file in the migrated directory**, add a docstring in the following format at the very top:

```python
"""
SCOS Migration Output
=====================
Source File: [Insert original file path, e.g., $ARGUMENTS/filename.py]
Migrated on: [Insert Current Date, e.g., 2023-10-27]

Changes Overview:
- [Lines 10-12] Replaced legacy SparkSession initialization with snowpark_connect.
- [Lines 45-50] Updated import statements to use Spark Connect equivalents.
- [Lines 88-92] [Description of another fix applied]

Known Limitations:
- [List any unaddressed TODOs, manual interventions required, or risks specific to THIS file]
"""
```

**IMPORTANT:** Every change listed in the 'Changes Overview' must be prefixed with the specific line numbers affected (e.g., [Lines 12-15]).

**Checklist before proceeding to Step 6:**
- [ ] All `.py` files in the migrated directory have been identified
- [ ] Each file has had unsupported imports removed (databricks, delta, etc.)
- [ ] Each file that creates a SparkSession has been updated to use snowpark_connect
- [ ] Each file has a migration header docstring added at the top

### Step 6: Verify Migration

**For EACH migrated Python file**, perform the following checks:

1.  **Syntax Check**: Run a syntax check on ALL generated files to ensure no parse errors were introduced.
    ```bash
    # For a single file:
    python3 -m py_compile ${ARGUMENTS%.py}_scos.py
    
    # For a directory (check ALL .py files):
    find ${ARGUMENTS}_scos -name "*.py" -exec python3 -m py_compile {} \;
    ```

2.  **Manual Review**: For **EACH** migrated file, verify:
    -   All imports are correct (no mixed `pyspark.sql` and `pyspark.sql.connect` for the same classes).
    -   The `snowpark_connect` initialization is present (in files that create sessions).
    -   The migration header docstring is present at the top of the file.
    -   No critical `TODO` items remain that block execution.

3.  **Directory Migration Completeness Check**: If migrating a directory, confirm:
    -   The number of `.py` files in the original and migrated directories match.
    -   Every `.py` file has a migration header comment.
    -   Run this to verify all files were processed:
    ```bash
    # Count files in original vs migrated
    echo "Original: $(find $ARGUMENTS -name '*.py' | wc -l) files"
    echo "Migrated: $(find ${ARGUMENTS}_scos -name '*.py' | wc -l) files"
    ```

## Success Criteria

- All `.py` files migrated with `_scos` suffix
- All syntax checks pass
- All high-risk issues (`final_risk` >= 0.7) have fixes applied
- All files have migration header docstrings
- File count matches between original and migrated directories

## Troubleshooting

**Error: uv not found**
- Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Restart terminal after installation

**Error: Snowflake connection failed**
- Verify `default` connection is configured (or use `--connection <name>`)
- Check credentials and network connectivity

**Error: Analysis returns empty results**
- Verify the path contains `.py` files
- Check if files contain PySpark code

**Error: Syntax check fails after migration**
- Review the specific file for incomplete edits
- Check for mismatched quotes or brackets in string replacements

**Error: Import errors after migration**
- Ensure unsupported imports (databricks, delta) are removed
- Verify `snowpark_connect` initialization is correct

**Error: RAG resources exist but access denied**
- The RAG knowledge base was set up by another user. Ask your Snowflake admin to grant access:
```sql
GRANT USAGE ON DATABASE SCOS_MIGRATION TO ROLE <your_role>;
GRANT USAGE ON SCHEMA SCOS_MIGRATION.PUBLIC TO ROLE <your_role>;
GRANT SELECT ON TABLE SCOS_MIGRATION.PUBLIC.SCOS_COMPAT_ISSUES TO ROLE <your_role>;
GRANT USAGE ON CORTEX SEARCH SERVICE SCOS_MIGRATION.PUBLIC.SCOS_COMPAT_ISSUES_SERVICE TO ROLE <your_role>;
```

## Output

Present the migrated code clearly. If multiple files were migrated, list them.
Do not remove the `analysis.json` file.
