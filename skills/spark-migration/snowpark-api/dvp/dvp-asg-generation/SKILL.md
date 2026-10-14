---
name: dvp-asg-generation
description: "Generate Abstract Semantic Graph (ASG) from source code (PySpark or Scala) — a structured representation of data flow consumed by downstream DVP skills."
---

# DVP ASG Generation

## Overview

Generates an Abstract Semantic Graph (ASG) from the original source code (`<sma-input>`) — supports PySpark (Python) or Scala Spark workloads. The ASG provides a structured, noise-free representation of the workload's data flow that downstream DVP skills consume instead of reading raw source code.

## What is an ASG?

ASG stands for **Abstract Semantic Graph**. It is a structured JSON summary of the source code focused on the **flow of data**: where data comes from (inputs), how it is transformed, and where it goes (outputs).

Source code is full of noise — logging, exception handling, configuration boilerplate, comments, dead code. The ASG strips all of that away and captures only what matters for understanding the workload's data pipeline.

## Outputs

| Output | Location | Description |
|--------|----------|-------------|
| ASG | `dvp/04-results/XX_asg.json` | Data flow graph (`XX` = project prefix, e.g. `RBI`, `example00`) |
| Anomalies | `dvp/04-results/XX_anomalies.json` | Structural issues detected in the ASG (broken lineage, unknown types, etc.) |

### `XX_asg.json` — Project ASG

Identifies Spark API usage (PySpark or Scala Spark) to trace the complete data flow:

- **Data sources (inputs)**: tables read via `spark.table()`, `spark.read.csv()`, `spark.read.jdbc()`, etc.
- **Transformations**: DataFrame operations (`.filter()`, `.groupBy()`, `.join()`, `.withColumn()`, etc.)
- **Data sinks (outputs)**: tables written via `.write.saveAsTable()`, `.write.csv()`, `.write.parquet()`, etc.
- **Host language logic flow**: control flow (`if/else`, loops, function calls) that connects Spark operations, ensuring the data flow graph is continuous

The key insight is that Spark operations don't exist in isolation — they are embedded in host language code (Python or Scala). The ASG traces **both** the Spark data operations and the host language logic that connects them, producing a continuous graph from input to output.

### Config file scanning

Many enterprise pipelines externalise data source paths, schemas, or pipeline wiring into `.json`/`.yaml` config files. The ASG generation step scans the workload directory for these files and classifies them by content type. Results are surfaced in the ASG and reports to flag that config-driven data sources exist but were not analysed statically. Platform-specific strategies are available for Airflow, Databricks, and AWS Glue configurations.

## Input

| Input | Location | Description |
|-------|----------|-------------|
| Original source code | `<sma-input>` | The original source files — PySpark (.py) or Scala (.scala) (pre-migration) |

The skill runs against the **original source code** in the `<sma-input>` folder — the same files that were input to the SMA migration tool.

## How Downstream Skills Use the ASG

The ASG files help the AI focus on what matters without wading through noisy source code:

| Downstream Skill | How it uses the ASG |
|------------------|---------------------|
| `dvp-entrypoint-identifier` | Identifies entry points (main functions, script execution points) from the graph's execution roots |
| `dvp-io-schema-identifier` | Detects input/output data sources (tables, files) and infers column schemas from the graph's source, sink, and transformation nodes |
| `dvp-synthetic-data-generator` | Uses schema information derived from the ASG to generate realistic synthetic test data |

Without the ASG, each of these skills would need to parse and interpret raw source code directly — slower, noisier, and less reliable.

## Embedded Library

The ASG generation logic is borrowed from the WARP suite. `warp_core` lives inside the orchestrator skill (`dvp-orchestrator/warp_core/`), while the ASG-specific packages are local to this skill:

```
dvp/
  dvp-orchestrator/
    warp_core/             <-- SHARED: IR models, slicer, schema, diagnostics
  dvp-asg-generation/
    warp/
      asg_pyspark/         <-- PySpark AST parser, analysis, reports
      asg_scala/           <-- Scala Spark parser (NEW)
```

To use this code, both `dvp-orchestrator/` (shared) and the skill's `warp/` must be on `sys.path`:

```python
import sys
from pathlib import Path

_SKILL_LIB = Path(__file__).resolve().parent.parent / "warp"
_SHARED_LIB = Path(__file__).resolve().parent.parent.parent / "dvp-orchestrator"
sys.path.insert(0, str(_SHARED_LIB))
sys.path.insert(0, str(_SKILL_LIB))

# PySpark workloads
from asg_pyspark.parser.spark_ast import parse_spark_directory

# Scala workloads
from asg_scala import parse_scala_directory
```

### Python Dependencies

The embedded library requires three external packages. **Before running any ASG generation, verify they are installed and install them if missing:**

```bash
pip install pydantic astroid sqlglot
```

| Package | Required by | Purpose |
|---------|-------------|---------|
| `pydantic` | `warp_core` (IR models) | ASG data models, validation, JSON serialization (`model_dump_json`) |
| `astroid` | `asg_pyspark` (parser) | Python variable inference — resolves `table_name = "raw_data"` to its string value |
| `sqlglot` | `asg_pyspark` / `asg_scala` (parser) | SQL parsing for embedded SQL statements (`spark.sql("SELECT ...")`) |

**Verification step** — run this before proceeding with ASG generation:

```python
import importlib
missing = [pkg for pkg in ("pydantic", "astroid", "sqlglot") if importlib.util.find_spec(pkg) is None]
if missing:
    import subprocess
    subprocess.check_call(["pip", "install"] + missing)
```

## ⛔ Hard Requirement — WARP Library

The embedded WARP library (`asg_pyspark` for PySpark or `asg_scala` for Scala) is **MANDATORY**. There is no fallback path.

**If the library import fails, you MUST:**
1. **STOP immediately** — do NOT skip, do NOT attempt AI-based ASG generation, do NOT continue to downstream skills
2. **Show an error message:**
   ```
   ERROR: ASG generation requires the WARP library (asg_pyspark / asg_scala)
   which is not installed in the expected location:
     - dvp-orchestrator/warp_core/
     - dvp-asg-generation/lib/asg_pyspark/
     - dvp-asg-generation/lib/asg_scala/

   The DVP pipeline CANNOT continue without the ASG.
   All downstream skills (entrypoint-identifier, io-schema-identifier,
   synthetic-data-generator, test-setup-generator) depend on it.

   Action required: Install the WARP library or contact the DVP team.
   ```
3. **Show the Final Summary table** with status `Failed`
4. **Return control to the orchestrator** (or user) with a failure signal — the pipeline must not proceed

**⚠️ NEVER skip ASG generation silently. NEVER let downstream skills run without an ASG.**

## Output Format

Every time you begin a step, sub-step, or significant action, prefix the message with a timestamp in the format `[YYYY-MM-DD HH:MM:SS]`. Obtain the current time by running `date '+%Y-%m-%d %H:%M:%S'` in bash.

Example:
```
[2026-03-24 14:05:32] Starting Step 1: Locate source files...
[2026-03-24 14:05:45] Generated ASG for job_customer_stats.py
[2026-03-24 14:05:46] Step 1 complete.
```

## Execution Steps

### Step 0: Initialize Git

Ensure the workload directory has a git repository on the `sma/migration-process` branch. This is idempotent — if the orchestrator already initialized git, this is a no-op.

```python
result = sma_api.git_ensure_ready("<workload_path>")
```

### Step 1: Verify Dependencies and Locate Source Code

1. Check that `pydantic`, `astroid`, and `sqlglot` are installed; `pip install` any that are missing
2. Identify the `<sma-input>` folder containing the original source files (from the orchestrator, `.snowma` config, or user-provided)

### Step 2: Parse Source Code

Analyze all source files in `<sma-input>` (`.py` for PySpark, `.scala` for Scala):
- Parse AST to understand control flow (functions, conditionals, loops, variable assignments)
- Identify Spark API calls (read operations, DataFrame transformations, write operations)
- Trace variable assignments to resolve dynamic values (table names stored in variables, file paths built from config)
- Parse embedded SQL statements for table/column references

### Step 3: Build ASG

Construct `XX_asg.json` by tracing data flow through the codebase.

**For PySpark workloads** (`.py` files) — use `asg_pyspark`:

```python
from asg_pyspark.parser.spark_ast import parse_spark_directory
asg = parse_spark_directory(sma_input_path)
```

**For Scala workloads** (`.scala` files) — use `asg_scala`:

```python
from asg_scala import parse_scala_directory
asg = parse_scala_directory(sma_input_path)
```

Both parsers produce the same `ASG` model and perform:
1. Identify all **data source operations** (inputs): `spark.table()`, `spark.read.*()`, etc.
2. Follow each DataFrame through its **transformation chain**: `.filter()`, `.join()`, `.groupBy()`, `.withColumn()`, etc.
3. Identify all **data sink operations** (outputs): `.write.saveAsTable()`, `.write.*()`, etc.
4. Connect these through the **host language logic flow**: function calls, variable assignments, conditionals, loops
5. Record source file locations (`file:lineno`) for each node in the graph
6. Run schema propagation to infer column types across the graph

### Step 4: Run Anomaly Detection

After building the ASG, run the anomaly detector to identify structural issues:

```python
from asg_pyspark.analysis.anomaly_detector import AnomalyDetector

detector = AnomalyDetector()
anomaly_report = detector.analyze(asg, migration_id=project_prefix)
```

The anomaly detector scans the ASG for:
- **SCH_001**: Columns with UNKNOWN type (schema inference gaps)
- **LIN_001**: Transformations without source (broken lineage)
- **LIN_002**: Data sources/sinks not connected to any flow (orphans)
- **LOG_001**: UDFs calling external libraries (black-box functions)
- **REF_002**: Unresolved parameter references (missing bindings)
- **JOIN_001**: Joins without explicit conditions
- **PAR_001**: Parser regex fallbacks (parser limitations)

The anomaly report is consumed by the reports step in `dvp-synthetic-data-generator` to produce gap analysis and the unified report.

### Step 5: Write Outputs

Save both files to `dvp/04-results/`:
- `XX_asg.json` — the ASG (project prefix derived from `.snowma` project name, folder name, or user-provided)
- `XX_anomalies.json` — the anomaly report

### Step 6: Commit Changes to Git

After outputs are written, commit the changes:

```python
result = sma_api.git_commit("<workload_path>", """DVP ASG Generator: Generated ASG for <project>

ASG: dvp/04-results/XX_asg.json
Anomalies: dvp/04-results/XX_anomalies.json
Source files analyzed: N""")
```

Verify branches:
```python
result = sma_api.git_verify_branches("<workload_path>")
```

## Prerequisites

| Prerequisite | Reason |
|-------------|--------|
| `<sma-input>` folder exists | Contains the original source code to analyze (PySpark or Scala) |
| `dvp/04-results/` directory exists | Created by `dvp-orchestrator` during workspace initialization |
| `pydantic` installed | ASG data models and JSON serialization |
| `astroid` installed | Python variable value inference (PySpark workloads only) |
| `sqlglot` installed | Embedded SQL statement parsing (PySpark and Scala) |

## Execution Order

```
dvp-orchestrator  →  stage-conversion  →  dvp-notebook-to-script
      |
      v
dvp-asg-generation  <-- THIS SKILL
      |
      v
dvp-entrypoint-identifier  (consumes ASG)
      |
      v
dvp-code-adapter  (consumes entrypoints.json)
      |
      v
dvp-io-schema-identifier  (consumes ASG + entrypoints.json)
      |
      v
dvp-synthetic-data-generator  (consumes data_io_schema.json + ASG + reports)
      |
      v
dvp-test-setup-generator  (consumes entrypoints.json + data_io_schema.json)
```

## Design Rationale

See [ASG Strategy](../docs/asg-strategy.md) for the full design rationale, including:
- The three-layer resolution approach (deterministic, AI+ASG, AI+source code)
- The anomaly feedback loop
- WARP tool embedding strategy

## Final Summary

**MANDATORY**: After completing all steps (whether running standalone or invoked from the orchestrator), ALWAYS present this summary table:

```
ASG Generation Complete

┌──────────────────┬──────────┬──────────────────────────────────────────────────┐
│ Step             │ Status   │ Details                                          │
├──────────────────┼──────────┼──────────────────────────────────────────────────┤
│ ASG Generation   │ Done     │ Generated ASG from N source files                │
└──────────────────┴──────────┴──────────────────────────────────────────────────┘

Output location: <output>/

Git branches:
• main — original code (unmodified)
• sma/migration-process — ASG generation changes applied
```

**Rules:**
- Replace `N` with actual count of source files processed
- Status is `Done` or `Failed` (there is NO `Skipped` — ASG generation is mandatory)
- If the WARP library is not available, show `Failed` with: `"WARP library not installed — pipeline cannot continue"`
- If parsing failed, show `Failed` with brief error
- **On `Failed`: do NOT proceed to any downstream skill. Return error to orchestrator.**
- The git branches section uses `sma_api.git_verify_branches()` to confirm both branches exist
