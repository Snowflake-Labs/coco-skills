# DVP Skills Pipeline

> **Naming standard:** Under `<output>/dvp/`, use lowercase directories with hyphens (see [SMA Integration](sma-integration.md)). `01-source/` is always present; exactly one of `02-migrated/` (Snowpark API) or `02-migrated_scos/` (SCOS) is present per run.
>
> **Cortex Code UX tip:** For clearer progress, start with `/plan`. If output feels noisy, request a brief summary after each `BASH` command and avoid reading entire large files (preview a few lines/rows instead).

```
SMA Migration Complete
  <sma-input>  (original source — PySpark or Scala)
  <sma-output> (migrated Snowpark code; Reports/ optional)
        │
        ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  (Optional upstream — run by spark-migration orchestrator)   │
  │  snowflake-notebook-migration (Step 10)                          │
  │  IN:  <sma-output>/Output/ (.ipynb, Databricks .py/.scala)       │
  │  OUT: converted notebooks in-place in <sma-output>/Output/       │
  │  Runs AFTER sma-dashboard-generator, BEFORE dvp-ewi-fixer        │
  └──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  STEP 1 · dvp-orchestrator                                       │
│  IN:  <sma-input>, <sma-output> (or .snowma / .snowct)          │
│  OUT: dvp/01-source/           ← copy of original source        │
│       dvp/02-migrated/         ← copy of Snowpark API output (if selected) │
│       dvp/02-migrated_scos/    ← copy of SCOS output (if selected)        │
│       dvp/03-tests/            ← empty, populated later         │
│       dvp/04-results/          ← empty, populated later         │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  STEP 2 · stage-conversion                                  │
│  IN:  selected migrated folder (02-migrated/ OR 02-migrated_scos/) │
│  OUT: modified .py files (S3/Azure/GCS paths → @STAGE syntax)   │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  STEP 3 · dvp-notebook-to-script  [optional, if notebooks]      │
│  IN:  01-source/*.ipynb, *.py (Databricks)                      │
│  OUT: .ipynb.py / .dbx.py scripts (converted to plain Python)   │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  STEP 4 · dvp-asg-generation                [warp-suite]         │
│  IN:  <sma-input> source files  ← reads original source code    │
│  OUT: 04-results/XX_asg.json        (ASG — XX = project prefix) │
│       04-results/XX_anomalies.json  (structural issues in ASG)  │
│  LIB: asg_pyspark, asg_scala + shared warp_core                 │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  STEP 5 · dvp-entrypoint-identifier         [warp-suite]         │
│  IN:  04-results/XX_asg.json                                     │
│  OUT: 04-results/entrypoints.json                                │
│  LIB: entrypoints + shared warp_core                             │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  STEP 6 · dvp-code-adapter                                      │
│  IN:  04-results/entrypoints.json                                │
│       01-source/ + selected migrated folder                      │
│  OUT: adapted code in-place:                                     │
│         · session injection (all, Python + Scala)                │
│         · __main__ extraction to function (Cases B/C)            │
│         · .enableHiveSupport() (Python source only)              │
│         · deferred env vars (all)                                │
│       04-results/entrypoints.json (adds adapted_source field)    │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  STEP 7 · dvp-io-schema-identifier          [warp-suite]         │
│  IN:  04-results/XX_asg.json         (ASG for detection)         │
│       01-source/ adapted code        (fallback / validation)     │
│       04-results/entrypoints.json                                │
│  OUT: 04-results/data_io_schema.json (IO metadata + columns)    │
│         · inputs & outputs (files + tables)                      │
│         · column schemas per entry                               │
│         · key columns for outputs                                │
│  LIB: data_io + shared warp_core                                 │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  STEP 8 · dvp-synthetic-data-generator      [warp-suite]         │
│  IN:  04-results/data_io_schema.json (schemas — primary)         │
│       04-results/XX_asg.json         (constraints + relations)   │
│       04-results/XX_anomalies.json   (for report generation)     │
│       04-results/entrypoints.json    (for report generation)     │
│  OUT: 04-results/synthetic_data/*.csv (one CSV per input entry)  │
│       04-results/XX_gaps.json        (gap report — client)       │
│       04-results/XX_REPORT.md        (unified report — client)   │
│       04-results/XX_warp_intel.json  (telemetry — internal)      │
│       04-results/XX_issues_report.json (operation log — internal)│
│  LIB: synthetic_data, diagnostic_reporter + shared warp_core     │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  STEP 9 · dvp-test-setup-generator                               │
│  IN:  04-results/entrypoints.json                                │
│       04-results/data_io_schema.json                                    │
│       04-results/synthetic_data/*.csv                             │
│  OUT: 03-tests/                                                  │
│         · conftest.py      (base classes, helpers, comparison)   │
│         · config.py        (Snowflake connection — user edits)   │
│         · pytest.ini                                             │
│         · source/          (PySpark test files)                  │
│         · migrated/        (Snowpark API test files, if selected)│
│         · migrated_scos/   (SCOS test files, if selected)        │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
   DVP WORKSPACE READY FOR TESTING

   pytest 03-tests/source/            → generates baseline CSVs
   pytest 03-tests/migrated/          → validates against baselines (if selected)
   pytest 03-tests/migrated_scos/     → validates against baselines (if selected)

        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  STEP 10 · dvp-test-runner                                       │
│  IN:  03-tests/source/ + 03-tests/migrated/                     │
│  OUT: baseline CSVs, test results, sma_storage.sqlite3           │
│       Source tests: all must PASS                                │
│       Migrated tests: all must RUN (failures acceptable)         │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  STEP 11 · dvp-migrated-test-fixer                               │
│  IN:  02-migrated/, 03-tests/migrated/, baselines, git repo     │
│  OUT: fixed 02-migrated/ code, fixed test infrastructure         │
│       Uses git commits to track each fix iteration               │
│       Migrated tests: all must PASS                              │
└──────────────────────────────────────────────────────────────────┘
```

## Artifact Flow

```
<sma-input>
    │
    └──► XX_asg.json ──────┬──► entrypoints.json ──┬──► code-adapter ──► entrypoints.json
         XX_anomalies.json │                        │       (adds adapted_source)
                           │                        ▼
                           ├──► data_io_schema.json ◄── io-identifier
                           │         │
                           │         ▼
                           └──► synthetic_data/*.csv ◄── synthetic-data-generator
                                      │
                                      ├──► XX_gaps.json         (client)
                                      ├──► XX_REPORT.md         (client)
                                      ├──► XX_warp_intel.json   (internal)
                                      ├──► XX_issues_report.json(internal)
                                      │
                                      ▼
                entrypoints.json ──► 03-tests/ ◄── test-setup-generator
```

## WARP Suite Shared Library

Steps 4-5 and 7-8 use code borrowed from the WARP suite (`warp-suite`). The common `warp_core`
package lives inside the orchestrator skill (committed to git), while each skill has its own specific package:

```
dvp/
  dvp-orchestrator/
    warp_core/              <-- SHARED by all 4 skills (IR models, diagnostics, ...)
  dvp-asg-generation/warp/
    asg_pyspark/            <-- PySpark AST parser
    asg_scala/              <-- Scala Spark parser
  dvp-entrypoint-identifier/warp/
    entrypoints/            <-- ASG-based entrypoint detection
  dvp-io-schema-identifier/warp/
    data_io/                <-- IO detection + schema inference from ASG
  dvp-synthetic-data-generator/warp/
    synthetic_data/         <-- Constraint-aware test data generation
    diagnostic_reporter/    <-- Comprehensive analysis from all WARP outputs
```
