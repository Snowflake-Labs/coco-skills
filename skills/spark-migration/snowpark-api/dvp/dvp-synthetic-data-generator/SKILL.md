---
name: dvp-synthetic-data-generator
description: "Generate synthetic test data (CSV files) for all pipeline inputs based on data_io_schema.json and optionally the ASG for constraints and relationships."
---

# DVP Synthetic Data Generator

## Overview

Generates one synthetic CSV file per input entry in `data_io_schema.json`, producing realistic test data that exercises the pipeline's join, filter, aggregation, and write logic. When the ASG is available, the generator uses `column_constraints` and `column_relationships` to produce smarter, constraint-aware data.

Uses the `SyntheticDataGenerator` from `warp-suite` (borrowed into `warp/`).

## Output Format

Every time you begin a step, sub-step, or significant action, prefix the message with a timestamp in the format `[YYYY-MM-DD HH:MM:SS]`. Obtain the current time by running `date '+%Y-%m-%d %H:%M:%S'` in bash.

Example:
```
[2026-03-24 14:05:32] Starting Step 1: Read data_io_schema.json...
[2026-03-24 14:05:45] Generated synthetic data for customers.csv
[2026-03-24 14:05:46] Step 1 complete.
```

## Execution Steps

### Step 0: Initialize Git

Ensure the workload directory has a git repository on the `sma/migration-process` branch. This is idempotent — if the orchestrator already initialized git, this is a no-op.

```python
result = sma_api.git_ensure_ready("<workload_path>")
```

### Step 1: Read `data_io_schema.json`

Load `dvp/04-results/data_io_schema.json` and filter for entries where `role == "input"`.

### Step 2: Auto-detect ASG (optional)

Look for `*_asg.json` in the same `04-results/` directory. If found, extract:
- **`column_constraints`**: filter predicates, IN lists, NOT NULL, BETWEEN ranges
- **`column_relationships`**: join keys linking tables together

These make the generated data constraint-aware (e.g., `country` only generates values from an `IN` list found in the code).

### Step 3: Generate Data

Run the `data_generator.py` script:

```bash
python dvp-synthetic-data-generator/scripts/data_generator.py \
  --data-io dvp/04-results/data_io_schema.json \
  --output-dir dvp/04-results/synthetic_data/
```

Optional arguments:
- `--asg path/to/XX_asg.json` — explicit ASG path (auto-detected via `*_asg.json` glob if omitted)
- `--rows 10` — rows per table (default: 10)
- `-v` — verbose logging

The generator:
1. Reads `data_io_schema.json` and filters `role: "input"` entries
2. Loads ASG constraints and relationships (if available)
3. Builds join key pools from `column_relationships` to ensure matching FKs
4. Detects branch values from `IN`/`ENUM` constraints for coverage
5. Generates rows per table using the `JOIN_AWARE` strategy
6. Writes each CSV to `dvp/04-results/synthetic_data/{name_lowercase}.csv`

### Step 4: Report Summary

```
Synthetic Data Generation Summary
=======================================

Mode: ASG-aware (constraints + relationships)
ASG:  dvp/04-results/RBI_asg.json

Generated 5 input files in dvp/04-results/synthetic_data/:
  customer_master.csv              5 rows   4 columns
  product_catalog.csv              5 rows   4 columns
  raw_transactions.csv            10 rows   5 columns
  returns_data.csv                 3 rows   4 columns
  exchange_rates.csv               6 rows   3 columns

Join keys (shared value pools):
  product_id
  customer_id

Branch values (from ASG constraints):
  country: [["US", "MX", "BR"]]
```

### Step 5: Commit Changes to Git

After synthetic data and reports are generated, commit the changes:

```python
result = sma_api.git_commit("<workload_path>", """DVP Synthetic Data: Generated N CSV files

Mode: ASG-aware (constraints + relationships) / schema-only
Generated: N input files in dvp/04-results/synthetic_data/
Reports: XX_gaps.json, XX_REPORT.md""")
```

Verify branches:
```python
result = sma_api.git_verify_branches("<workload_path>")
```

## Inputs

| Input | Required | Location |
|-------|----------|----------|
| `data_io_schema.json` | Yes | `dvp/04-results/data_io_schema.json` — must have `columns` populated by `dvp-io-schema-identifier` |
| ASG JSON | No | `dvp/04-results/XX_asg.json` — for constraints and relationships (auto-detected via `*_asg.json`) |
| Anomalies JSON | No | `dvp/04-results/XX_anomalies.json` — for gap analysis and reports (auto-detected) |
| Entrypoints JSON | No | `dvp/04-results/entrypoints.json` — for report context (auto-detected) |

## Outputs

| Output | Format | Location |
|--------|--------|----------|
| Synthetic input CSVs | CSV | `dvp/04-results/synthetic_data/{name_lowercase}.csv` |
| Gap report | JSON | `dvp/04-results/XX_gaps.json` — actionable defect list with score impact |
| Unified report | Markdown | `dvp/04-results/XX_REPORT.md` — human-readable analysis (score, I/O, gaps) |
| WARP Intel | JSON | `dvp/04-results/XX_warp_intel.json` — semantic telemetry (internal) |
| Issues report | JSON | `dvp/04-results/XX_issues_report.json` — pipeline operation log (internal) |

The report outputs are generated as a post-generation step because this skill is the last warp-suite step in the pipeline — by this point all artifacts (ASG, anomalies, entrypoints, data_io_schema, synthetic data) are available.

### CSV Conventions

- Column headers are **UPPERCASE** regardless of the casing in `data_io_schema.json` (e.g., `customer_id` in the schema → `CUSTOMER_ID` in the CSV)
- **Lowercase filenames** (e.g., `CUSTOMER_MASTER` → `customer_master.csv`)
- Always `.csv` format, even for table inputs
- Values match declared types in `data_io_schema.json`

### Handling Ambiguous Columns

Columns with `"confidence": "ambiguous"` in `data_io_schema.json` appear when the detector could not determine which side of a join owns the column. These columns are still generated in the synthetic CSV, but may cause **ambiguity errors at runtime** if the same column name appears in multiple tables involved in a join. When this occurs, the AI should review the source code to determine the correct owner and remove the column from the wrong table's schema before regenerating.

## Generation Strategies

The borrowed `SyntheticDataGenerator` supports three strategies:

| Strategy | Description | When Used |
|----------|-------------|-----------|
| `JOIN_AWARE` (default) | Ensures join keys have matching values across tables | Most workloads with joins |
| `CONSTRAINT_FIRST` | Focuses on satisfying filter constraints | Workloads with complex filters |
| `BRANCH_COVERAGE` | Generates datasets for different code branches | Conditional workloads |

## How the ASG Improves Generation

Without ASG (schema-only mode):
- Values are generated purely from column types and name hints
- Join relationships are inferred from shared column names

With ASG:
- **`column_constraints`**: `IN` lists become the only allowed values (e.g., `country IN ("US", "MX")` → only those values generated). `NOT NULL` constraints prevent null generation.
- **`column_relationships`**: explicit join keys get shared value pools, ensuring all FK references resolve correctly across tables
- **Branch coverage**: conditional filters are detected and data is generated to exercise multiple code paths

## Embedded Library

The generation and reporting logic is borrowed from `warp-suite`. `warp_core` lives inside the orchestrator skill (`dvp-orchestrator/warp_core/`), while skill-specific packages are local:

```
dvp/
  dvp-orchestrator/
    warp_core/                    <-- SHARED: diagnostics, IR models, gap_analyzer,
                                      unified_report, warp_intel
  dvp-synthetic-data-generator/
    scripts/
      data_generator.py           <-- CLI wrapper
    warp/
      synthetic_data/
        __init__.py               <-- SyntheticDataGenerator, GenerationStrategy
        generator.py              <-- Core generation logic
        adversarial.py            <-- NoiseInjector for edge-case data
        logic_solver.py           <-- Z3-based constraint solving (optional)
        validator.py              <-- Row tracing and coverage tracking
      diagnostic_reporter/
        __init__.py               <-- DiagnosticReporter
        reporter.py               <-- Comprehensive analysis from all WARP outputs
```

### Report Generation (Post-Step)

After synthetic data is generated, the skill produces the full WARP report suite using modules from `warp_core`:

```python
from warp_core.unified_report import generate_unified_report, _build_context, compute_score
from warp_core.gap_analyzer import analyze_gaps
from warp_core.warp_intel import build_warp_intel
```

These produce:
- **`XX_gaps.json`** — actionable defect list with score impact (client-facing)
- **`XX_REPORT.md`** — human-readable analysis with scores, I/O summary, gap summary (client-facing)
- **`XX_warp_intel.json`** — semantic telemetry: parser fallbacks, heuristics, unhandled patterns (internal)
- **`XX_issues_report.json`** — pipeline operation log aggregating issues from all detectors (internal)

### Python Dependencies

The borrowed code requires `pydantic` for the report models (`gap_analyzer`, `unified_report`, `warp_intel` use pydantic). The optional `logic_solver.py` requires `z3-solver` but is not imported eagerly.

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
dvp-code-adapter  (consumes entrypoints.json)
      |
      v
dvp-io-schema-identifier  →  data_io_schema.json
      |
      v
dvp-synthetic-data-generator  ← THIS SKILL  →  synthetic_data/*.csv + reports
      |
      v
dvp-test-setup-generator  →  03-tests/
```

## Prerequisites

- `dvp/04-results/data_io_schema.json` must exist with `columns` populated for input entries
- `dvp/04-results/XX_asg.json` is optional but recommended for constraint-aware generation
- `dvp/04-results/XX_anomalies.json` is optional but needed for gap analysis and report generation
- `dvp/04-results/entrypoints.json` is optional but consumed by the report generator
- `dvp/04-results/synthetic_data/` directory will be created if it doesn't exist
- `pydantic` installed (required by the report generation modules)

## Final Summary

**MANDATORY**: After completing all steps (whether running standalone or invoked from the orchestrator), ALWAYS present this summary table:

```
Synthetic Data Generation Complete

┌──────────────────┬──────────┬──────────────────────────────────────────────────────┐
│ Step             │ Status   │ Details                                              │
├──────────────────┼──────────┼──────────────────────────────────────────────────────┤
│ Synthetic Data   │ Done     │ Generated N CSV files                                │
└──────────────────┴──────────┴──────────────────────────────────────────────────────┘

Output location: <output>/

Git branches:
• main — original code (unmodified)
• sma/migration-process — synthetic data generation changes applied
```

**Rules:**
- Replace `N` with actual count of CSV files generated
- Status is `Done`, `Skipped`, or `Failed`
- If no input schemas were available, show `Skipped` with reason
- If generation failed, show `Failed` with brief error
- The git branches section uses `sma_api.git_verify_branches()` to confirm both branches exist
