# Preconditions and Setup

Requirements and setup steps before running DVP skills.

## Required

### 1. SMA Migration Completed

The source code must have been converted from PySpark or Scala Spark to Snowpark using the **Snowpark Migration Accelerator (SMA)**. DVP operates **after** the SMA migration is done. Mixed Python/Scala workloads are not yet supported by DVP.

DVP supports **SMA output formats v1, v2, and v3** (v2 and v3 can both exist in the field — detection prefers `sma-output/` when present, then v3):

| Format | Structure | Path Resolution |
|--------|-----------|-----------------|
| **v1 (legacy)** | `Conversion-<timestamp>/` | Via `.snowma` file's `internalConversionOutputPath`, or most recent legacy `Conversion-*` |
| **v2** | `sma-output/` | Auto-detected (no `.snowma` file) |
| **v3** | `Conversion_SnowparkAPI` / `Conversion_SnowparkConnect` + `sma-code-process-*` | Auto-detected: latest execution under the chosen **`Conversion_SnowparkAPI`** or **`Conversion_SnowparkConnect`** folder |

You need the SMA `<input>` path. DVP will attempt to auto-detect the output format:

| Path | Description | How to get it |
|------|-------------|---------------|
| **`<input>`** | The folder with the original Python Spark source code (immutable) | v1: from `.snowma` or user-provided. **v2/v3: always user-provided** (`.snowct` has no paths) |
| **`<output>`** | The conversion or execution folder (v1: `Conversion-*/`, v2: `sma-output/`, v3: latest `sma-code-process-*` under **`Conversion_SnowparkAPI`** or **`Conversion_SnowparkConnect`**) | v1: from `.snowma` or legacy detection. v2: `sma-output/`. v3: latest run under the chosen **`Conversion_SnowparkAPI`** or **`Conversion_SnowparkConnect`** folder. Fallback: user-provided |

> **Note:** SMA v2/v3 may ship a `.snowct` project file instead of `.snowma`, but it only contains a project ID -- not input/output paths. DVP will ask for `<input>` if it can't be auto-resolved.

What you should have in `<output>`:
- `Output/` -- Migrated Snowpark Python code (`.py` files)
- `Reports/` -- SMA inventories (`.csv`), `Issues.csv`, and `AssessmentReport.json` *(optional)*
- `Reports/SnowConvertReports/` -- Detailed SnowConvert reports with timestamps *(optional)*
- `Logs/` -- SMA execution logs

See [SMA Integration and Folder Structure](sma-integration.md) for the full layout and format details.

### 2. Cortex Code CLI

The Cortex Code CLI must be installed and accessible from the command line.

```bash
# Verify installation
cortex --version
```

This is used for:
- Code analysis and pattern detection
- EWI processing and resolution
- Integration with the Snowflake ecosystem

## Optional

### 3. IDE or Editor

Any IDE or editor can be used. Recommended options:

| Editor | Notes |
|--------|-------|
| **Cursor** | Recommended -- AI-native IDE with skill support |
| **VS Code** | Widely used, with good Python/Snowpark extensions |
| **CoCo Desktop** | Cortex Code Desktop -- native SMA integration |
| **PyCharm** | Full Python IDE with debugging support |

### 4. Python Environment

For running generated tests:

```bash
# Python 3.8+
python3 --version

# Recommended: virtual environment
python3 -m venv dvp-env
source dvp-env/bin/activate

# Install Snowpark
pip install snowflake-snowpark-python
```

### 5. Snowflake Account

Required for running generated tests (not for test generation):

- Snowflake account with permissions to create temporary tables/stages
- Connection credentials (account, user, password/key, role, warehouse, database)
- Recommended: use a dedicated test schema to isolate test data

## Project Directory Structure

DVP creates its workspace inside the conversion folder at `<output>/dvp/` (alongside `Output/`, `Reports/`, and `Logs/`):

```
<output>/                        # v1: Conversion-<timestamp>/ or v2: sma-output/ or v3: .../sma-code-process-*/
├── Output/                      # Migrated code (SMA output)
├── Reports/                     # Inventories (SMA output)
├── Logs/                        # Logs (SMA output)
└── dvp/                         # DVP workspace (created by DVP)
    ├── 01-source/               # Copy of original Spark code from <input> (mutable)
    ├── 02-migrated/             # Copy of migrated code from <output>/Output (if Snowpark API selected)
    ├── 02-migrated_scos/        # Copy of migrated code from <output>/Output (if SCOS selected)
    ├── 03-tests/                # Generated pytest files (one per pipeline)
    ├── 04-results/              # Skill outputs: inventories, schemas, data, reports
    └── 05-assets/
        └── FromCustomer/        # Customer-provided schemas, data, etc.
```

DVP needs **both** `<input>` and `<output>` paths because:
- `<input>` is copied to `dvp/01-source/` (originals remain immutable)
- `<output>/Output/` is copied to the selected migrated folder (`dvp/02-migrated/` **or** `dvp/02-migrated_scos/`)

See [SMA Integration and Folder Structure](sma-integration.md) for the complete layout with examples.

## Environment Variables

For test execution (not required for test generation):

```bash
export SNOWFLAKE_ACCOUNT="your_account"
export SNOWFLAKE_USER="your_user"
export SNOWFLAKE_PASSWORD="your_password"  # or use key-pair auth
export SNOWFLAKE_ROLE="your_role"
export SNOWFLAKE_WAREHOUSE="your_warehouse"
export SNOWFLAKE_DATABASE="your_database"
export SNOWFLAKE_SCHEMA="your_test_schema"
```

## Operator Tips (Cortex Code UX)

When running DVP skills in the Cortex Code CLI, you will see intermediate tool steps like `READ`, `EDIT`, and `BASH`. This is normal.

To get clearer progress feedback and reduce noisy output:

- Prefer **plan mode** before running long workflows: `/plan`.
- Ask for **updates after each command**:
  - "After each BASH, summarize: what you attempted, what happened, and the next step."
- Avoid reading large files unless necessary:
  - "Do not read entire CSVs; only show a small preview (e.g., 5-10 lines)."

## Quick Start

1. Run SMA to migrate your Spark source code to Snowpark
2. Note both the `<input>` and `<output>` paths used
3. DVP will create `<output>/dvp/` (inside the conversion folder) and populate the workspace
4. Start with either:
   - **Test generation:** Use `dvp-orchestrator` or individual data-validator skills
   - **EWI management:** Use `dvp-ewi-dashboard-generator` or individual tracking-manager skills
