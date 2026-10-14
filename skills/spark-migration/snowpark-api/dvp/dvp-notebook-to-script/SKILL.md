---
name: dvp-notebook-to-script
description: "Convert Jupyter notebooks to Python scripts for testing. Triggers: convert notebook, notebook to script, ipynb to py, databricks notebook."
---

# DVP Notebook to Script

## Overview

Converts notebooks to Python scripts. Supports two input formats:

1. **Jupyter (`.ipynb`)** — Standard JSON notebook format
2. **Databricks Source (`.py`)** — Exported via Databricks Repos or CLI, identified by `# Databricks notebook source` header

Scripts are generated in one of two modes:

- **Wrapped (default):** All cell code inside `def run()`, preserving shared scope. Called from `if __name__ == "__main__"`. Executable and importable without side effects.
- **Flat (`%run` targets):** Cell code at module level, no `def run()` wrapper. Used for notebooks that are executed via `%run` from other notebooks, so that `exec()` shares the caller's variable namespace — matching Databricks behavior.

## Why This Skill?

| Challenge | Solution |
|-----------|----------|
| Notebooks require Jupyter kernel to execute | Convert to standalone Python scripts |
| Snowflake vNext kernel cannot be emulated | Scripts can run with standard Python + Snowpark |
| Cell-based execution is hard to debug | Cell tracking with `Notebook` helper class |
| Variables shared across cells | Single `def run()` function preserves shared scope |
| Importing a script executes everything | `__main__` guard allows import without side effects |
| `%run` shares namespace across notebooks | Flat scripts + `exec()` in caller's globals |

## Output Format

Every time you begin a step, sub-step, or significant action, prefix the message with a timestamp in the format `[YYYY-MM-DD HH:MM:SS]`. Obtain the current time by running `date '+%Y-%m-%d %H:%M:%S'` in bash.

Example:
```
[2026-03-24 14:05:32] Starting Step 1: Convert notebooks to scripts...
[2026-03-24 14:05:45] Converted notebook.ipynb to notebook.py
[2026-03-24 14:05:46] Step 1 complete.
```

## Execution Order

This skill must run **BEFORE** `dvp-entrypoint-identifier`:

```
dvp-orchestrator
      │
      ▼
dvp-notebook-to-script  ← Converts .ipynb → .ipynb.py, .py (dbx) → .dbx.py
      │
      ▼
dvp-entrypoint-identifier  (now detects entry points in converted scripts)
```

## Output Files

| Original | Format | Generated |
|----------|--------|-----------|
| `myNotebook.ipynb` | Jupyter | `myNotebook.ipynb.py` |
| `pipelines/etl.ipynb` | Jupyter | `pipelines/etl.ipynb.py` |
| `config.py` (with DBX header) | Databricks | `config.dbx.py` |
| `plk/acr_dashboard_data.py` (with DBX header) | Databricks | `plk/acr_dashboard_data.dbx.py` |
| (any directory with notebooks) | — | `dvp_notebook_helper.py` |

The output extension makes it clear these are converted notebooks:
- `.ipynb.py` — converted from Jupyter
- `.dbx.py` — converted from Databricks source format
- Originals are never modified

## Conversion Logic

> **Note:** The conversion is performed automatically by `notebook_to_script.py`. The AI does not need to implement this logic manually—just invoke the script. This section documents the behavior for reference.

### Key Behaviors

| Behavior | Description |
|----------|-------------|
| **Wrapped vs flat** | Entrypoint notebooks get `def run()` wrapper; `%run` targets are flat (module-level code) for shared-namespace `exec()` |
| **Sequential numbering** | Cell numbers (001, 002...) reflect notebook position across ALL cell types |
| **Region markers** | `# region 001` / `# endregion 001` for IDE folding and navigation |
| **Cell tracking** | `nb.cell("001")` marks each cell for error reporting |
| **Markdown as comments** | Markdown cells become Python comments prefixed with `#` |
| **SQL cell detection** | Cells starting with SQL keywords become `session.sql()` calls |
| **Magic command handling** | `%`, `%%`, `!` commands are routed through `nb.magic()` |
| **`%run` handling** | Cross-notebook `%run` calls become `nb.run()` which `exec()`s the flat target into the caller's globals |
| **Two-pass scan** | First pass detects `%run` references to determine which notebooks are targets (flat) vs entrypoints (wrapped) |
| **Databricks source format** | `.py` files with `# Databricks notebook source` header are auto-detected and parsed |
| **Error reporting** | Try/except wrapper with `nb.report_error()` identifies failing cell |
| **Helper module** | `dvp_notebook_helper.py` is copied to each output directory |

### Input Notebook Structure

```json
{
  "cells": [
    {
      "cell_type": "markdown",
      "source": ["# Data Pipeline"],
      "metadata": {}
    },
    {
      "cell_type": "code",
      "source": ["import pandas as pd\ndf = pd.read_csv('data.csv')"],
      "metadata": {}
    },
    {
      "cell_type": "code",
      "source": ["df_filtered = df[df.status == 'active']"],
      "metadata": {}
    }
  ]
}
```

### Input: Databricks Source Format (.py)

Databricks Repos and the CLI export notebooks as `.py` files with comment markers:

```python
# Databricks notebook source
from datetime import datetime

# COMMAND ----------

# MAGIC %md
# MAGIC # Notebook Goal
# MAGIC sync data from snowflake to rds

# COMMAND ----------

# MAGIC %run ../config $brand="plk"

# COMMAND ----------

df = spark.read.format("snowflake").options(**sfOptions).load()
```

| Marker | Purpose |
|--------|---------|
| `# Databricks notebook source` | File header (line 1) — identifies the file as a notebook |
| `# COMMAND ----------` | Cell separator |
| `# MAGIC %md` | Markdown cell content |
| `# MAGIC %run` | Cross-notebook execution |
| `# MAGIC %sql` | SQL cell content |
| (no `# MAGIC` prefix) | Code cell |

Detection is automatic: the converter checks if the first line of any `.py` file matches the header. Files already ending in `.ipynb.py` or `.dbx.py` are skipped.

### Output Python Script

```python
"""
Auto-generated from: myNotebook.ipynb
Generated by: dvp-notebook-to-script
Generated at: 2026-02-14T12:00:00+00:00
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / '03-tests'))

from dvp_notebook_helper import Notebook


def run():
    nb = Notebook("myNotebook.ipynb")

    try:

        # region 001 (markdown) ================================
        nb.cell("001", "markdown")

        # # Data Pipeline
        # endregion 001 ----------------------------------------

        # region 002 ===========================================
        nb.cell("002")

        import pandas as pd
        df = pd.read_csv('data.csv')
        # endregion 002 ----------------------------------------

        # region 003 ===========================================
        nb.cell("003")

        df_filtered = df[df.status == 'active']
        # endregion 003 ----------------------------------------

        nb.finish()

    except Exception as _e:
        nb.report_error(_e)
        raise


if __name__ == "__main__":
    run()
```

## Helper Module: `dvp_notebook_helper.py`

The `Notebook` class provides runtime support:

```python
class Notebook:
    def __init__(self, name: str, caller_file: str = None):
        """Initialize and log execution start. caller_file (__file__) is used to resolve %run paths."""
    
    def cell(self, number: str, cell_type: str = "code"):
        """Mark start of a cell for tracking."""
    
    def magic(self, kind: str, args: str):
        """Handle Jupyter magic commands (%pip, !, %%time, etc.)."""
    
    def run(self, notebook_ref: str, params: str = ""):
        """Execute %run target: resolves the ref, reads the flat script, exec()s into caller globals."""
    
    def finish(self):
        """Log successful completion."""
    
    def report_error(self, exception: Exception):
        """Report which cell caused an error with traceback."""
```

**Logging output example:**
```
12:00:01 [INFO] dvp.notebook.myNotebook.ipynb - Starting execution of notebook myNotebook.ipynb
12:00:01 [INFO] dvp.notebook.myNotebook.ipynb - MAGIC [!]: pip install pandas
12:00:05 [INFO] dvp.notebook.myNotebook.ipynb - Execution of notebook myNotebook.ipynb finished successfully (4.2s)
```

**Error output example:**
```
12:00:03 [ERROR] dvp.notebook.myNotebook.ipynb - ERROR in notebook myNotebook.ipynb - Cell 003 after 2.1s
Traceback (most recent call last):
  ...
```

## Workflow

### Step 0: Initialize Git

Ensure the workload directory has a git repository on the `sma/migration-process` branch. This is idempotent — if the orchestrator already initialized git, this is a no-op.

```python
result = sma_api.git_ensure_ready("<workload_path>")
```

### Step 1: Find Notebooks

Scan source directory for `.ipynb` files and Databricks `.py` files:

```
dvp/01-source/
├── pipelines/
│   ├── etl_pipeline.ipynb      ← Will be converted (.ipynb)
│   └── helpers.py              ← Ignored (no DBX header)
├── analysis/
│   └── data_analysis.ipynb     ← Will be converted (.ipynb)
├── .databricks/notebooks/
│   ├── config.py               ← Will be converted (DBX header detected)
│   └── plk/
│       └── acr_dashboard.py    ← Will be converted (DBX header detected)
└── utils.py                    ← Ignored (no DBX header)
```

### Step 2: Scan for `%run` Targets

Before converting, the converter scans all notebooks for `%run` references:

```
  %run targets (will generate flat): pipeline_config
```

Any notebook referenced by `%run` is marked as a target and will be generated **flat** (no `def run():` wrapper).

### Step 3: Convert Each Notebook

For each notebook:

1. **Detect format**: `.ipynb` (JSON) or `.py` with `# Databricks notebook source` header
2. **Parse cells**: JSON cells for `.ipynb`, `# COMMAND ----------` splits for Databricks
3. **Choose mode**: flat (if `%run` target) or wrapped (entrypoint)
4. **Emit** cell code with cell tracking — at module level (flat) or inside `def run()` (wrapped)
5. **Write** to `<name>.ipynb.py` (Jupyter) or `<name>.dbx.py` (Databricks)
6. **Copy** `dvp_notebook_helper.py` to the `dvp/03-tests/` directory

### Step 3: Report Summary

```
Found 2 notebook(s) to convert.
  Converting: 2_scoring.ipynb
    → 2_scoring.ipynb.py (21 code, 2 markdown)
  Converting: 1_dataprep.ipynb
    → 1_dataprep.ipynb.py (45 code, 2 markdown)

==================================================
Notebook Conversion Summary
==================================================
Total notebooks: 2
  Converted: 2
  Failed: 0

Converted notebooks:
  ✓ 2_scoring.ipynb → 2_scoring.ipynb.py
      21 code cells, 2 markdown cells
  ✓ 1_dataprep.ipynb → 1_dataprep.ipynb.py
      45 code cells, 2 markdown cells
==================================================

Next: Run dvp-entrypoint-identifier to detect entry points.
```

### Step 4: Commit Changes to Git

After all notebooks are converted, commit the changes:

```python
result = sma_api.git_commit("<workload_path>", """DVP Notebook Converter: Converted N notebooks to scripts

Converted: N notebooks (M Jupyter, K Databricks)
Generated: N .ipynb.py / .dbx.py files
Helper: dvp_notebook_helper.py copied to dvp/03-tests/""")
```

Verify branches:
```python
result = sma_api.git_verify_branches("<workload_path>")
```

## Inputs

| Input | Required | Location |
|-------|----------|----------|
| Jupyter notebooks | No | `dvp/01-source/**/*.ipynb` |
| Databricks source notebooks | No | `dvp/01-source/**/*.py` (with `# Databricks notebook source` header) |

At least one of the above must be present.

## Outputs

| Output | Format | Location |
|--------|--------|----------|
| Converted Jupyter scripts | Python | `dvp/01-source/**/*.ipynb.py` |
| Converted Databricks scripts | Python | `dvp/01-source/**/*.dbx.py` |
| Helper module | Python | `dvp/03-tests/dvp_notebook_helper.py` |

## Cell Conversion Details

### Code Cells

Code cells are emitted inside `def run()` with region markers:

```python
        # region 005 ===========================================
        nb.cell("005")

        df = spark.read.parquet("data.parquet")
        df_filtered = df.filter(df.amount > 100)
        # endregion 005 ----------------------------------------
```

### Markdown Cells

Markdown cells become Python comments:

```python
# region 003 (markdown) ================================
nb.cell("003", "markdown")

# # Data Processing
# This section processes the raw data.
# - Step 1: Load data
# - Step 2: Transform
# endregion 003 ----------------------------------------
```

### SQL Cells

Cells starting with SQL keywords are detected and converted to `session.sql()`:

```python
# region 007 (sql) =====================================
nb.cell("007", "sql")

# SQL Cell - converted to session.sql()
_sql = """
SELECT * FROM customers WHERE active = true
"""

# Execute SQL and store result
_sql_result_007 = session.sql(_sql)
# endregion 007 ----------------------------------------
```

### Magic Commands

IPython magic commands are routed through `nb.magic()`:

```python
# Original notebook cell:
%pip install pandas
%%time
!ls -la

# Converted:
nb.magic("%pip", "install pandas")
nb.magic("%%time", "")
nb.magic("!", "ls -la")
```

### `%run` (Cross-Notebook Execution)

The `%run` magic executes another notebook in the same process, sharing variables. It's common in Databricks workflows and also valid in standard Jupyter notebooks.

```python
# Original (Databricks):
# MAGIC %run ../config $brand="plk"

# Original (Jupyter):
%run ../config $brand="plk"

# Converted (both formats produce the same output):
nb.run("../config", "$brand=\"plk\"")
```

**How it works at runtime:**

1. `nb.run()` resolves `"../config"` relative to the caller's directory
2. Finds the converted file (`config.dbx.py` or `config.ipynb.py`)
3. Reads the file and `exec()`s it into the caller's `globals()`
4. All variables, classes, and functions from the target become available in the caller — matching Databricks' shared-namespace behavior

**Restriction:** The target notebook must be a **flat script** (generated without `def run()` wrapper). The converter handles this automatically via a two-pass scan:
- Pass 1: scan all notebooks for `%run` references to build a set of targets
- Pass 2: generate flat scripts for targets, wrapped scripts for entrypoints

If `nb.run()` encounters a non-flat target (e.g., converted before the scan detected it), it emits a warning:
```
WARNING - %run target config.dbx.py is NOT a flat script. Variables will not be shared.
```

## Edge Cases

| Case | Handling |
|------|----------|
| Empty notebook | Create minimal script with try/except wrapper |
| No code cells | Create script with only markdown comments |
| Syntax errors in cells | Preserve as-is (will fail at runtime with cell info) |
| Cell with imports | Imports are inside `run()`, available to all cells in the function |
| `.py` file without DBX header | Ignored — treated as a regular Python file |
| `.py` file already ending in `.dbx.py` or `.ipynb.py` | Skipped — already converted |
| `%run` with parameters | Path and params split: `nb.run("../config", "$brand=\"plk\"")` |
| `%run` without parameters | Path only: `nb.run("../config")` |

## Stopping Points

- **No notebooks found:** Report and continue (not an error)
- **Invalid notebook JSON:** Warn and skip that notebook
- **Write failure:** Report error for that notebook, continue with others
- **Not inside DVP structure:** Abort with instructions to run orchestrator first

## Final Summary

**MANDATORY**: After completing all steps (whether running standalone or invoked from the orchestrator), ALWAYS present this summary table:

```
Notebook Conversion Complete

┌───────────────────────┬──────────┬──────────────────────────────────────────────┐
│ Step                  │ Status   │ Details                                      │
├───────────────────────┼──────────┼──────────────────────────────────────────────┤
│ Notebook Conversion   │ Done     │ Converted N notebooks to scripts             │
└───────────────────────┴──────────┴──────────────────────────────────────────────┘

Output location: <output>/

Git branches:
• main — original code (unmodified)
• sma/migration-process — notebook conversion changes applied
```

**Rules:**
- Replace `N` with actual count of notebooks converted
- Status is `Done`, `Skipped`, or `Failed`
- If no notebooks were found, show `Skipped` with reason
- If conversion failed, show `Failed` with brief error
- The git branches section uses `sma_api.git_verify_branches()` to confirm both branches exist
