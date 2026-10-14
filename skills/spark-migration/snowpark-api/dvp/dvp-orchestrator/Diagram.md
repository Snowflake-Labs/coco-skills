# DVP Orchestrator - Architecture Diagram

> **Last Updated**: 2026-03-20
> **Note**: Keep this diagram updated when making changes to the skill.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                       DVP ORCHESTRATOR - PIPELINE FLOW                          │
│  ⚠️  Execute ALL steps 1-13 without stopping. Never ask to continue.           │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────┐
│   TRIGGER       │  "start DVP", "initialize workspace", "setup validation",
│   (Cortex)      │  "run DVP", "create DVP structure"
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 1: DETECT SMA PATHS                                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  Format Detection Algorithm:                                            │    │
│  │                                                                         │    │
│  │  1. Search for .snowma in <input>                                       │    │
│  │     ├─► Found → v1 format: use internalConversionOutputPath as <output> │    │
│  │     └─► Not found → step 2                                              │    │
│  │                                                                         │    │
│  │  2. Look for sma-output/ in sibling output folder                       │    │
│  │     ├─► Found → v2 format: use sma-output/ as <output>                  │    │
│  │     └─► Not found → step 3                                              │    │
│  │                                                                         │    │
│  │  3. v3: Conversion_SnowparkAPI / Conversion_SnowparkConnect +          │    │
│  │     sma-code-process-*                                                  │    │
│  │     ├─► Found → latest sma-code-process-* under chosen folder = <out> │    │
│  │     └─► Not found → step 4                                              │    │
│  │                                                                         │    │
│  │  4. Legacy Conversion-* (exclude Conversion_SnowparkAPI and               │    │
│  │     Conversion_SnowparkConnect)                                         │    │
│  │     ├─► Found → v1 (no .snowma): most recent Conversion-*             │    │
│  │     └─► Not found → step 5                                              │    │
│  │                                                                         │    │
│  │  5. Ask user for both <input> and <output> paths                        │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  Output: <input> (source PySpark), <output> (SMA output), format (v1/v2/v3)    │
│                                                                                 │
│  NOTE: When invoked from spark-migration, Step 1 is SKIPPED.               │
│  Paths are provided via context from spark-migration Step 5.6/Step 3.1.    │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 2: VALIDATE SMA STRUCTURE                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌──────────────────────────┬──────────┬────────────────────────────────────┐   │
│  │ Path                     │ Required │ Description                        │   │
│  ├──────────────────────────┼──────────┼────────────────────────────────────┤   │
│  │ <output>/Output/         │ Yes      │ Migrated Snowpark Python code      │   │
│  │ <output>/Reports/        │ No       │ SMA inventories and reports        │   │
│  │ <output>/Logs/           │ No       │ SMA execution logs                 │   │
│  │ <output>/Reports/Issues.csv │ No    │ EWI issues                         │   │
│  └──────────────────────────┴──────────┴────────────────────────────────────┘   │
│                                                                                 │
│  STOP if <output>/Output/ missing. Continue if Reports/ missing.               │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 3: CREATE DVP WORKSPACE                                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Location depends on format:                                                    │
│  ┌─────────┬──────────────────────────────────────────────────┐                │
│  │ v1      │ <output>/dvp/  (inside Conversion-*/)            │                │
│  │ v2      │ <output>/dvp/  (inside sma-output/)             │                │
│  │ v3      │ <output>/dvp/  (inside latest sma-code-process/) │                │
│  └─────────┴──────────────────────────────────────────────────┘                │
│                                                                                 │
│  DVP folder structure:                                                          │
│  ┌────────────────────────────────────────────────────┐                        │
│  │  dvp/                                              │                        │
│  │  ├── 01-source/           (adapted source files)   │                        │
│  │  ├── 02-migrated/         (Snowpark API)           │                        │
│  │  │   OR 02-migrated_scos/ (SCOS)                   │                        │
│  │  ├── 03-tests/            (generated pytest files)  │                        │
│  │  ├── 04-results/          (skill outputs)           │                        │
│  │  └── 05-assets/FromCustomer/                       │                        │
│  └────────────────────────────────────────────────────┘                        │
│                                                                                 │
│  If dvp/ exists → ask: Overwrite / Merge / Abort                               │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 4: COPY SOURCE FILES                                                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  1. <input>/* ──────────────────────► dvp/01-source/                           │
│     (preserve structure, exclude .snowma/.snowct/checkpoints.json)              │
│                                                                                 │
│  2. v3: copy target implied by <output> (Conversion_SnowparkAPI vs           │
│     Conversion_SnowparkConnect).                                            │
│     v1/v2: use conversion_type from orchestrator context (if provided),       │
│     otherwise ask user: Snowpark API or SCOS?                                  │
│                                                                                 │
│  3. <output>/Output/* ──► dvp/02-migrated/       (if Snowpark API)             │
│                       ──► dvp/02-migrated_scos/  (if SCOS)                     │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 5: REPORT SUMMARY                                                         │
│  Brief summary: DVP workspace, source, migrated paths, next steps              │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 5.5: INITIALIZE GIT REPOSITORY (via sma_api)                              │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  sma_api.git_ensure_ready("<output>")                                          │
│  ┌────────────────────────────────────────────────────────────────────┐         │
│  │  1. git init + initial commit (if not a repo)                      │         │
│  │  2. Stash uncommitted changes (if dirty)                           │         │
│  │  3. Create/switch to sma/migration-process branch                  │         │
│  └────────────────────────────────────────────────────────────────────┘         │
│                                                                                 │
│  Idempotent: no-op if already initialized by prior skill.                      │
│                                                                                 │
│  Result: two branches:                                                          │
│  • main ─────────────────── original SMA output (unmodified)                   │
│  • sma/migration-process ── all DVP changes applied                            │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 6: load dvp-notebook-to-script/SKILL.md (bundled)                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Converts .ipynb and Databricks .py files to testable Python scripts.          │
│                                                                                 │
│  Input:  dvp/01-source/, dvp/02-migrated[_scos]/                               │
│  Output: .ipynb.py, .dbx.py scripts (added alongside originals)               │
│                                                                                 │
│  ⛔ Do NOT delete any files from 01-source/ or 02-migrated*/.                 │
│  If no notebooks found → skip (not an error), proceed to Step 7.               │
│                                                                                 │
│  ⚠️ MUST load bundled SKILL.md inline — do NOT run manually or via Task.    │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 7: DEPENDENCY BOOTSTRAP (shared venv)                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  1. Preflight check:                                                            │
│     python -c "import pydantic; import sqlglot; import pytest; ..."             │
│                                                                                 │
│  2. If fails → bootstrap with uv:                                               │
│     cd dvp && uv venv .venv && uv pip install -r requirements.txt              │
│                                                                                 │
│  3. Use dvp/.venv/bin/python for all subsequent commands.                       │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌═════════════════════════════════════════════════════════════════════════════════┐
║  ⛔ ASG-CENTRIC PIPELINE (Steps 8-13) — ALL MANDATORY                          ║
║     All steps load bundled SKILL.md INLINE. No Task tool. No background.     ║
╘═════════════════════════════════════════════════════════════════════════════════╛
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 8: load dvp-asg-generation/SKILL.md (bundled)                             │
│  ⛔ HARD GATE — pipeline stops if this fails                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Input:  <sma-input> (original source code)                                    │
│  Output: dvp/04-results/*_asg.json                                             │
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────┐               │
│  │  ASG exists?                                                 │               │
│  │  ├─► YES → proceed to Step 9                                │               │
│  │  └─► NO  → STOP pipeline. Show Final Summary with:          │               │
│  │           ASG = "Failed", Steps 9-13 = "Blocked"            │               │
│  └──────────────────────────────────────────────────────────────┘               │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 9: load dvp-entrypoint-identifier/SKILL.md (bundled)                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Input:  dvp/04-results/*_asg.json                                             │
│  Output: dvp/04-results/entrypoints.json                                       │
│                                                                                 │
│  Uses WARP EntrypointDetector for deterministic detection.                      │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 10: load dvp-code-adapter/SKILL.md (bundled)                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Input:  dvp/04-results/entrypoints.json                                       │
│  Adapts: dvp/01-source/, dvp/02-migrated[_scos]/                               │
│                                                                                 │
│  Actions:                                                                       │
│  • Session injection (optional session parameter)                               │
│  • .enableHiveSupport() for source PySpark code                                │
│  • Deferred environment variable reads                                          │
│  • Writes adapted_source field into entrypoints.json                           │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 11: load dvp-io-schema-identifier/SKILL.md (bundled)                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Input:  dvp/04-results/*_asg.json, dvp/01-source/                             │
│  Output: dvp/04-results/data_io_schema.json                                    │
│                                                                                 │
│  Combines IO detection + schema extraction in a single pass.                   │
│  ASG for deterministic detection, AI for anomaly resolution.                   │
│                                                                                 │
│  Verify data_io_schema.json exists before proceeding.                          │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 12: load dvp-synthetic-data-generator/SKILL.md (bundled)                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Input:  dvp/04-results/data_io_schema.json (role=="input" entries)             │
│          dvp/04-results/*_asg.json (optional, for constraints)                  │
│  Output: dvp/data/*.csv (one per input entry)                                  │
│          + anomaly report                                                       │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 13: load dvp-test-setup-generator/SKILL.md (bundled)   ← LAST STEP        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Input:  dvp/04-results/entrypoints.json (with adapted_source from Step 10)    │
│  Output: dvp/03-tests/source/          (baseline CSV test files)               │
│          dvp/03-tests/migrated/        (Snowpark API) OR                       │
│          dvp/03-tests/migrated_scos/   (SCOS)                                  │
│          + conftest files, test config, shared fixtures                         │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌═════════════════════════════════════════════════════════════════════════════════┐
║  ⛔ MANDATORY GUARD: Have ALL Steps 1-13 been attempted?                       ║
║     YES → proceed to Final Summary                                             ║
║     NO  → go back to the first incomplete step                                 ║
╘═════════════════════════════════════════════════════════════════════════════════╛
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  FINAL SUMMARY                                                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌────────────────────────┬──────────┬────────────────────────────────────────┐ │
│  │ Step                   │ Status   │ Details                                │ │
│  ├────────────────────────┼──────────┼────────────────────────────────────────┤ │
│  │ DVP Workspace          │ Done     │ Initialized with source + migrated    │ │
│  │ Notebook Conversion    │ Done     │ Converted N notebooks to scripts      │ │
│  │ ASG Generation         │ Done     │ Generated ASG from N source files     │ │
│  │ Entrypoint Detection   │ Done     │ Identified N entrypoints              │ │
│  │ Code Adapter           │ Done     │ Adapted N files for testing           │ │
│  │ IO Schema              │ Done     │ N inputs, M outputs identified        │ │
│  │ Synthetic Data         │ Done     │ Generated N CSV files                 │ │
│  │ Test Setup             │ Done     │ Generated test project with N tests   │ │
│  └────────────────────────┴──────────┴────────────────────────────────────────┘ │
│                                                                                 │
│  Output location: <output>/                                                     │
│  Git branches:                                                                  │
│  • main ─────────────────── original SMA output (unmodified)                   │
│  • sma/migration-process ── all DVP changes applied                            │
│                                                                                 │
│  Verified via: sma_api.git_verify_branches("<output>")                         │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Invocation Context

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STANDALONE INVOCATION                                                          │
│  User triggers dvp-orchestrator directly.                                       │
│  → Runs Steps 1-13 (Step 1 detects paths automatically)                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  INVOKED FROM spark-migration                                               │
│  spark-migration Step 11 passes <input>, <output>, format.                 │
│  → Step 1 is SKIPPED (paths from Step 5.6/3.1 context)                         │
│  → Runs Steps 2-13                                                              │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow Between Steps

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                                                 │
│  <input> (source) ──► Step 4 ──► dvp/01-source/                               │
│  <output>/Output/  ──► Step 4 ──► dvp/02-migrated[_scos]/                     │
│                                                                                 │
│  dvp/01-source/    ──► Step 6 (notebook-to-script)                             │
│  dvp/02-migrated/  ──► Step 6 (notebook-to-script)                             │
│                                                                                 │
│  <input>           ──► Step 8 ──► dvp/04-results/*_asg.json                   │
│                         ├──► Step 9 ──► dvp/04-results/entrypoints.json        │
│                         ├──► Step 11 ──► dvp/04-results/data_io_schema.json    │
│                         └──► Step 12 (constraints)                              │
│                                                                                 │
│  entrypoints.json  ──► Step 10 (code-adapter) ──► adapted_source field         │
│                    ──► Step 13 (test-setup-generator) ──► dvp/03-tests/        │
│                                                                                 │
│  data_io_schema.json ──► Step 12 ──► dvp/data/*.csv                           │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```
