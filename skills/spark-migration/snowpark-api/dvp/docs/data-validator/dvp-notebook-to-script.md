# dvp-notebook-to-script

> Convert Jupyter notebooks and Databricks source files to Python scripts for testing.

## Overview

| Field | Value |
|-------|-------|
| **Category** | data-validator |
| **Status** | **Implemented** |
| **Output** | Python scripts (`.ipynb.py`, `.dbx.py`) + helper module |
| **Depends on** | dvp-orchestrator (workspace must be initialized) |
| **SKILL.md** | [`dvp-notebook-to-script/SKILL.md`](../../dvp-notebook-to-script/SKILL.md) |

## Responsibility

Converts Jupyter notebooks (`.ipynb`) and Databricks notebook source files (`.py` with `# Databricks notebook source` header) to Python scripts with module-level code that preserves the shared namespace behavior. This enables testing of notebook-based pipelines without requiring a Jupyter kernel or Databricks runtime.

## Design Decisions

### Why Module-Level Code Instead of Functions Per Cell?

The initial approach wrapped each notebook cell in a function (`cell_001()`, `cell_002()`, etc.) with context passing between them. This was abandoned for several reasons:

#### Problem 1: Variable Scope Isolation

```python
# Function-based approach (ABANDONED)
def cell_001(ctx=None):
    locals().update(ctx)
    df = pd.read_csv("data.csv")
    return locals()

def cell_002(ctx=None):
    locals().update(ctx)  # df is NOT accessible here!
    df_filtered = df[df.x > 0]  # NameError: df not defined
    return locals()
```

`locals().update(ctx)` does NOT make variables available in the function's scope—it only updates the `locals()` dict, which is read-only in CPython. Variables from previous cells were inaccessible.

#### Problem 2: Closures and Nested Functions

Notebooks often define functions that reference variables from outer scope:

```python
# Cell 1
threshold = 100

# Cell 2
def filter_data(df):
    return df[df.amount > threshold]  # Captures 'threshold' from outer scope
```

With the function-per-cell approach, `threshold` would not be in scope when `filter_data` is defined, breaking closures.

#### Problem 3: Class Definitions

Classes defined in one cell and used in another need shared namespace:

```python
# Cell 1
class DataProcessor:
    def process(self, df): ...

# Cell 2
processor = DataProcessor()  # Needs DataProcessor in scope
```

#### Solution: Module-Level Code

By emitting cell code directly at module level, we preserve Jupyter's shared namespace behavior naturally:

```python
# Module-level approach (CURRENT)
nb.cell("001")
df = pd.read_csv("data.csv")

nb.cell("002")
df_filtered = df[df.x > 0]  # Works! df is in module scope
```

### Why a Helper Module (`dvp_notebook_helper.py`)?

Instead of embedding helper code in every generated script, we use a separate module that:

1. **Keeps generated scripts clean** - Only business logic, no boilerplate
2. **Centralizes updates** - Fix bugs in one place
3. **Provides cell tracking** - `nb.cell("003")` records current cell for error reporting
4. **Handles magic commands** - `nb.magic("!", "pip install x")` logs and can be extended
5. **Structured error reporting** - When a cell fails, `nb.report_error()` identifies which one

### Why Flat vs Wrapped Scripts?

The converter produces two script styles based on whether a notebook is a `%run` target:

**Wrapped scripts** (default) — notebooks that are entry points or standalone:
```python
def run():
    nb = Notebook("pipeline.ipynb", __file__)
    try:
        nb.cell("001")
        # cell code...
        nb.finish()
    except Exception as _e:
        nb.report_error(_e)
        raise

if __name__ == "__main__":
    run()
```

**Flat scripts** (`# DVP:FLAT`) — notebooks called via `%run` from other notebooks:
```python
# DVP:FLAT — this script is a %%run target (no def run wrapper)
nb = Notebook("config.py", __file__)

nb.cell("001")
# cell code (variables/functions injected into caller's namespace)

nb.finish()
```

Flat scripts have no `def run()` wrapper so that their variables and functions are injected directly into the caller's global namespace via `exec()`, matching how `%run` works in Databricks/Jupyter.

### Why Try/Except Wrapper?

Wrapped scripts use try/except to:

1. **Identify failing cell** - `nb.report_error(_e)` prints which cell caused the error
2. **Preserve stack trace** - `raise` re-raises the exception after logging
3. **Log execution time** - `nb.finish()` reports total execution time on success

```python
try:
    nb.cell("001")
    # cell code...
    
    nb.cell("002")
    # cell code...
    
    nb.finish()

except Exception as _e:
    nb.report_error(_e)  # Logs: "ERROR in notebook X - Cell 002 after 1.3s"
    raise
```

### Why Region Markers?

`# region 001 ===` / `# endregion 001 ---` markers enable:

1. **IDE code folding** - Collapse cells in VS Code, PyCharm, etc.
2. **Navigation** - Jump between cells using IDE outline
3. **Visual distinction** - `===` clearly marks region start, `---` marks region end

## Execution Order

This skill runs **BEFORE** `dvp-entrypoint-identifier`:

```
dvp-orchestrator
      │
      ▼
dvp-notebook-to-script  ← Step 6
      │
      ▼
dvp-entrypoint-identifier  ← Step 7
```

## Supported Formats

| Source Format | Detection | Output Extension |
|---------------|-----------|-----------------|
| Jupyter notebook (`.ipynb`) | JSON with `cells` array | `.ipynb.py` |
| Databricks notebook source (`.py`) | `# Databricks notebook source` header | `.dbx.py` |

The converter auto-detects the format. Databricks `.py` files use `# COMMAND ----------` as cell separators and `# MAGIC` prefixes for markdown/SQL cells.

## Output Files

| Original | Generated |
|----------|-----------|
| `myNotebook.ipynb` | `myNotebook.ipynb.py` |
| `myNotebook.py` (Databricks) | `myNotebook.dbx.py` |
| (any directory with notebooks) | `dvp_notebook_helper.py` |

The output extension (`.ipynb.py` or `.dbx.py`) makes it clear this is a converted notebook while keeping the original intact.

## Workflow

### Step 1: Find Notebooks

Scan directory for `.ipynb` files and Databricks `.py` files (files starting with `# Databricks notebook source`). Excludes `.ipynb_checkpoints`.

### Step 2: Detect `%run` Targets

Scan all notebooks for `%run` references. Notebooks that are targets of `%run` calls are marked for flat generation (no `def run()` wrapper) so their variables and functions are available in the caller's namespace.

### Step 3: Convert Each Notebook (Two-Pass)

**Pass 1 — Flat targets first:** Convert `%run` targets as flat scripts.

**Pass 2 — Remaining notebooks:** Convert all other notebooks as wrapped scripts.

For each notebook:
1. Read the source (JSON for `.ipynb`, text for Databricks `.py`)
2. Parse cells (code, markdown, SQL)
3. Emit module-level code with `nb.cell()` markers
4. Convert `%run` commands to `nb.run()` calls
5. Convert markdown cells to `#` comments
6. Wrap in try/except with error reporting (wrapped scripts only)
7. Write to `<original>.ipynb.py` or `<original>.dbx.py`

### Step 4: Copy Helper Module

Copy `dvp_notebook_helper.py` to the `03-tests/` directory (referenced via relative path from generated scripts).

### Step 5: Report Summary

Reports notebooks converted, their style (flat/wrapped), and any failures.

## Inputs

| Input | Required | Location |
|-------|----------|----------|
| Source notebooks (`.ipynb`) | Yes* | `dvp/01-source/**/*.ipynb` and `dvp/02-migrated/**/*.ipynb` |
| Source notebooks (Databricks `.py`) | Yes* | `dvp/01-source/**/*.py` and `dvp/02-migrated/**/*.py` |

\* At least one notebook in either format must be present.

## Outputs

| Output | Format | Location |
|--------|--------|----------|
| Converted scripts (from `.ipynb`) | Python | `dvp/01-source/**/*.ipynb.py` and `dvp/02-migrated/**/*.ipynb.py` |
| Converted scripts (from Databricks `.py`) | Python | `dvp/01-source/**/*.dbx.py` and `dvp/02-migrated/**/*.dbx.py` |
| Helper module | Python | `dvp/03-tests/dvp_notebook_helper.py` |

**Important:** The `01-source/` and `02-migrated/` folders contain both the original files and the generated scripts (`.ipynb.py`, `.dbx.py`). No files are deleted by the converter. The unmodified originals also remain in their original locations (`input/` and `sma-output/Output/`).

## Conversion Example

### Input Notebook

```json
{
  "cells": [
    {"cell_type": "markdown", "source": ["# Data Pipeline"]},
    {"cell_type": "code", "source": ["import pandas as pd\ndf = pd.read_csv('data.csv')"]},
    {"cell_type": "code", "source": ["df_filtered = df[df.status == 'active']"]}
  ]
}
```

### Output Script

```python
"""
Auto-generated from: pipeline.ipynb
Generated by: dvp-notebook-to-script
"""
from dvp_notebook_helper import Notebook

nb = Notebook("pipeline.ipynb")

try:

    # ==========================================================
    # region 001 (markdown)
    nb.cell("001", "markdown")

    # # Data Pipeline

    # endregion 001

    # ==========================================================
    # region 002
    nb.cell("002")

    import pandas as pd
    df = pd.read_csv('data.csv')

    # endregion 002

    # ==========================================================
    # region 003
    nb.cell("003")

    df_filtered = df[df.status == 'active']

    # endregion 003

    nb.finish()

except Exception as _e:
    nb.report_error(_e)
    raise
```

## Cell Type Handling

| Cell Type | Conversion |
|-----------|------------|
| **Code** | Emitted as-is at module level |
| **Markdown** | Converted to `#` comments |
| **SQL** (`%%sql`) | Marked with `nb.cell("NNN", "%%sql")`, content as comments + `pass` |
| **Shell** (`!command`) | Converted to `nb.magic("!", "command")` |
| **`%run`** | Converted to `nb.run("target", "args")` |
| **Other magics** (`%`, `%%`) | Routed through `nb.magic()` for logging |

## `%run` Handling

The converter detects `%run` references across all notebooks and handles them specially:

1. **Target detection:** Scans all cells for `%run ./path` or `%run path` patterns
2. **Flat generation:** Targets of `%run` are converted as flat scripts (no `def run()` wrapper)
3. **Runtime resolution:** `nb.run("./target", "args")` resolves to the converted script (`.dbx.py` or `.ipynb.py`) and executes it via `exec()` in the caller's global namespace
4. **Path priority:** Looks for `.dbx.py` first, then `.ipynb.py`, in the same directory as the caller

## Edge Cases

| Case | Handling |
|------|----------|
| Empty notebook | Create minimal script with try/except wrapper |
| No code cells | Create script with only markdown comments |
| Magic commands (`%`, `!`) | Route through `nb.magic()` for logging |
| SQL cells | Marked as `%%sql`, content preserved as comments |
| `%run` target | Generate flat script (no `def run()` wrapper) |
| Circular `%run` | Not detected; will cause recursion at runtime |
| Databricks `.py` with `# MAGIC` | Strip prefix, detect markdown/SQL by `%md`/`%sql` |
| Syntax errors in cells | Preserve as-is (fails at runtime with cell info) |
| No notebooks found | Continue to next step (not an error) |
