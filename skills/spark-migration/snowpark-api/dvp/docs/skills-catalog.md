# DVP Skills Catalog

Complete reference of all DVP skills, organized by category.

> **Design principle:** DVP skills follow an ASG-centric strategy where deterministic algorithms handle ~80% of analysis, AI resolves ~15% using the ASG, and only ~5% requires inspecting source code. Each WARP-based skill also produces anomaly reports that the AI resolves in real-time and that feed back into tool improvements. See [ASG Strategy](asg-strategy.md) for details.

## Skills Summary

| # | Category | Skill Name | Output Type | Status |
|---|----------|-----------|-------------|--------|
| 1 | data-validator | dvp-orchestrator | Orchestration | **Implemented** |
| 2 | data-validator | dvp-entrypoint-identifier | Inventory (JSON) | **Implemented** |
| 3 | data-validator | dvp-asg-generation | ASG (JSON) | Planned |
| 4 | data-validator | dvp-code-adapter | Code (.py) | Planned |
| 5 | data-validator | dvp-io-schema-identifier | Inventory (JSON) | Planned |
| 6 | data-validator | dvp-test-setup-generator | Code (.py) | **Implemented** |
| 7 | data-validator | dvp-test-execution-generator | Code (.py) | Planned |
| 8 | data-validator | dvp-test-validation-generator | Code (.py) | Planned |
| 9 | data-validator | dvp-testing-status-manager | Report (.html) | Planned |
| 10 | data-validator | dvp-synthetic-data-generator | Data (CSV) | Planned |
| 11 | data-validator | stage-conversion | Code (.py, .ipynb) | **Implemented** |
| 12 | data-validator | dvp-migrated-test-fixer | Code (.py) | **Implemented** |
| 13 | tracking-manager | dvp-ewi-tracking-manager | Report (.html) | Planned |
| 14 | tracking-manager | dvp-ewi-fixer | Code (.py) | **Implemented** |
| 15 | tracking-manager | dvp-ewi-extractor | Inventory (JSON) | Planned |
| 16 | tracking-manager | dvp-ewi-dashboard-generator | Orchestration | Planned |
| 17 | migration | snowflake-notebook-migration | Code (.ipynb) | **Implemented** |

---

## Data Validator Skills

### 1. dvp-orchestrator

> **Initializes the DVP workspace and orchestrates the validation pipeline.**

- **Category:** data-validator
- **Status:** **Implemented** -- See [`dvp-orchestrator/SKILL.md`](../dvp-orchestrator/SKILL.md)
- **Responsibility:** Uses the `.snowma` project file to resolve SMA paths (where `<output>` = the conversion folder from `internalConversionOutputPath`), validates the SMA output structure (**requires `<output>/Output/`; `<output>/Reports/` is optional**), creates the `<output>/dvp/` workspace inside the conversion folder, copies source and migrated files, and orchestrates all downstream skills (Steps 6-13): notebook conversion, ASG generation, entrypoint detection, code adaptation, IO schema identification, synthetic data generation, and test setup generation. All sub-skills are invoked as bundled sub-skills — load `snowpark-api/dvp/dvp-<name>/SKILL.md` with the Read tool and follow it inline (see the orchestrator's "Bundled Sub-skill Loading Convention"). Note: `stage-conversion` is NOT part of this pipeline — it runs from the `spark-migration` parent skill only.
- **Inputs:** SMA `<input>` path (user-provided); `<output>` resolved from `.snowma` (`internalConversionOutputPath`) or user-provided
- **Outputs:** DVP folder structure (`<output>/dvp/`), copied source files (`dvp/01-source/`), copied migrated files (`dvp/02-migrated/` *(if Snowpark API selected)* or `dvp/02-migrated_scos/` *(if SCOS selected)*)
- **Depends on:** All other data-validator skills (orchestrates them)
- **Details:** [docs/data-validator/dvp-orchestrator.md](data-validator/dvp-orchestrator.md)

---

### 2. dvp-entrypoint-identifier

> **Generates `entrypoints.json` from an ASG (ASG-only).**

- **Category:** data-validator
- **Status:** **Implemented** -- See [`dvp-entrypoint-identifier/SKILL.md`](../dvp-entrypoint-identifier/SKILL.md)
- **Responsibility:** Detects entry points from ASG JSON using the embedded WARP `EntrypointDetector` and generates `dvp/04-results/entrypoints.json` (ASG-only; no SMA inventories and no source scanning).
- **Inputs:** ASG JSON (`dvp/04-results/XX_asg.json`)
- **Outputs:** Entry Points Inventory (JSON) at `dvp/04-results/entrypoints.json`
- **Depends on:** dvp-asg-generation, dvp-orchestrator
- **Embedded lib:** `entrypoints/` + shared `dvp-orchestrator/warp_core/`
- **Details:** [docs/data-validator/dvp-entrypoint-identifier.md](data-validator/dvp-entrypoint-identifier.md)

---

### 3. dvp-asg-generation

> **Generates the Abstract Semantic Graph (ASG) from source code (PySpark or Scala) — a structured representation of data flow consumed by all downstream skills.**

- **Category:** data-validator
- **Responsibility:** Analyzes original source code (`<sma-input>`) — PySpark (.py) or Scala (.scala) — to produce an ASG file (`XX_asg.json`) and anomaly report (`XX_anomalies.json`). The ASG traces data sources, transformations, sinks, and host language control flow. The anomaly detector identifies structural issues (broken lineage, unknown types, orphans, black-box UDFs).
- **Inputs:** Original source code (`<sma-input>`)
- **Outputs:** ASG (`dvp/04-results/XX_asg.json`), Anomalies (`dvp/04-results/XX_anomalies.json`)
- **Depends on:** dvp-orchestrator (workspace must be initialized)
- **Consumers:** `dvp-entrypoint-identifier`, `dvp-io-schema-identifier`, `dvp-synthetic-data-generator` (reports use anomalies)
- **Embedded lib:** `asg_pyspark/`, `asg_scala/` + shared `dvp-orchestrator/warp_core/`
- **Details:** See [`dvp-asg-generation/SKILL.md`](../dvp-asg-generation/SKILL.md)

---

### 4. dvp-code-adapter (formerly dvp-source-adapter)

> **Adapts source and migrated workloads for testing: session injection, enableHiveSupport (source only), deferred env vars, and records `adapted_source` in entrypoints.json.**

- **Category:** data-validator
- **Responsibility:** Makes minimal changes to workload code for testability. Handles three `__main__` patterns (delegate to function, session+delegation, inline logic extraction). Applies session injection and env var deferral to `dvp/01-source/` and the selected migrated folder. Adds `.enableHiveSupport()` only to `dvp/01-source/` (PySpark). Supports both Python and Scala workloads. Writes `adapted_source` to `entrypoints.json` so downstream skills know the exact callable.
- **Inputs:** Source code (`dvp/01-source/`), Migrated code (`dvp/02-migrated/`, `dvp/02-migrated_scos/`), Entrypoints Inventory (`dvp/04-results/entrypoints.json`)
- **Outputs:** Adapted code in `dvp/01-source/`, `dvp/02-migrated/`, and `dvp/02-migrated_scos/` (in-place); updated `dvp/04-results/entrypoints.json` (with `adapted_source`)
- **Depends on:** dvp-entrypoint-identifier
- **Details:** [`dvp-code-adapter/SKILL.md`](../dvp-code-adapter/SKILL.md)

---

### 5. dvp-io-schema-identifier

> **Identifies input and output data (files and tables) for each pipeline and infers column schemas.**

- **Category:** data-validator
- **Responsibility:** Detects data inputs/outputs from the ASG JSON using `DataIODetector` and infers column schemas via a 6-phase type inference pipeline (evidence before patterns). Combines IO detection and schema extraction in a single pass, producing `data_io_schema.json`. Columns are tagged with confidence levels (`evidence`, `pattern`, `ambiguous`, `placeholder`) — `ambiguous` flags columns whose join-side attribution could not be determined.
- **Inputs:** ASG JSON (`dvp/04-results/XX_asg.json`), Entrypoints Inventory (optional), SMA IO Inventory (optional)
- **Outputs:** I/O Inventory with schemas (JSON) at `dvp/04-results/data_io_schema.json`
- **Depends on:** dvp-asg-generation, dvp-code-adapter
- **Embedded lib:** `data_io/` + shared `dvp-orchestrator/warp_core/`
- **Details:** See [`dvp-io-schema-identifier/SKILL.md`](../dvp-io-schema-identifier/SKILL.md)

---

### 6. dvp-test-setup-generator

> **Creates Python test setup code (Arrange/Given phase).**

- **Category:** data-validator
- **Responsibility:** Creates the Python files to prepare the environment: create tables/files and load input data to run the tests. Also configures the DB session.
- **Inputs:** I/O Inventory, Schema Definitions, Synthetic Test Data
- **Outputs:** Python test setup files (.py)
- **Depends on:** dvp-io-schema-identifier, dvp-synthetic-data-generator
- **Details:** [docs/data-validator/dvp-test-setup-generator.md](data-validator/dvp-test-setup-generator.md)

---

### 7. dvp-test-execution-generator

> **Creates Python test execution code (Act/When phase).**

- **Category:** data-validator
- **Responsibility:** Generates the code to invoke each pipeline under test with the correct parameters and context.
- **Inputs:** Entrypoints Inventory, I/O Inventory
- **Outputs:** Python test execution files (.py)
- **Depends on:** dvp-entrypoint-identifier, dvp-io-schema-identifier
- **Details:** [docs/data-validator/dvp-test-execution-generator.md](data-validator/dvp-test-execution-generator.md)

---

### 8. dvp-test-validation-generator

> **Creates Python test validation code (Assert/Then phase).**

- **Category:** data-validator
- **Responsibility:** Creates the Python files to validate the outputs for each pipeline. Tests that output has rows and matches expectations.
- **Inputs:** I/O Inventory, Schema Definitions, Expected Output Data
- **Outputs:** Python test assertion files (.py)
- **Depends on:** dvp-io-schema-identifier
- **Details:** [docs/data-validator/dvp-test-validation-generator.md](data-validator/dvp-test-validation-generator.md)

---

### 9. dvp-testing-status-manager

> **Generates an HTML dashboard showing pipeline test status.**

- **Category:** data-validator
- **Responsibility:** Shows a list of pipelines and their status: test generated, schema inferred, data inferred, test executed, validation status, match with source output, etc.
- **Inputs:** Entrypoints Inventory, test results, schema/data status
- **Outputs:** HTML status report
- **Depends on:** All test generation skills (reads their status)
- **Details:** [docs/data-validator/dvp-testing-status-manager.md](data-validator/dvp-testing-status-manager.md)

---

### 10. dvp-synthetic-data-generator

> **Generates fake/synthetic data for test inputs.**

- **Category:** data-validator
- **Responsibility:** Generates constraint-aware synthetic data for test inputs using `SyntheticDataGenerator`. Also produces the full WARP report suite (gap analysis, unified report, warp intel, issues report) as the last warp-suite step in the pipeline.
- **Inputs:** `data_io_schema.json` (schemas), `XX_asg.json` (constraints), `XX_anomalies.json` (for reports), `entrypoints.json` (for reports)
- **Outputs:** Synthetic CSV at `dvp/04-results/synthetic_data/`, `XX_gaps.json`, `XX_REPORT.md`, `XX_warp_intel.json`, `XX_issues_report.json`
- **Depends on:** dvp-io-schema-identifier, dvp-asg-generation (for anomalies)
- **Embedded lib:** `synthetic_data/`, `diagnostic_reporter/` + shared `dvp-orchestrator/warp_core/`
- **Details:** See [`dvp-synthetic-data-generator/SKILL.md`](../dvp-synthetic-data-generator/SKILL.md)

---

### 11. stage-conversion

> **Converts cloud storage paths to Snowflake stage syntax.**

- **Category:** data-validator
- **Responsibility:** Parse Python code and detect uses of files located at S3 buckets, Azure Blobs, etc. Replace them with stage syntax as the equivalence for Snowflake migration.
- **Inputs:** SMA migrated source code
- **Outputs:** Modified Python files (.py) with stage syntax
- **Depends on:** None (operates on source code directly)
- **Details:** [docs/data-validator/stage-conversion.md](data-validator/stage-conversion.md)

---

### 12. dvp-migrated-test-fixer

> **Makes migrated Snowpark tests PASS by iteratively fixing PySpark-to-Snowpark API incompatibilities using git-tracked iterations.**

- **Category:** data-validator
- **Status:** **Implemented** -- See [`dvp-migrated-test-fixer/SKILL.md`](../dvp-migrated-test-fixer/SKILL.md)
- **Responsibility:** After `dvp-test-runner` ensures migrated tests **run**, this skill makes them **pass**. Iterates over failing tests, diagnoses Snowpark API incompatibilities (create_map, datediff, parquet writes, alias dot notation, timestamp formats, etc.), applies fixes to `dvp/02-migrated/` code and `dvp/03-tests/migrated/` test infrastructure, commits each fix to git, and retries until all tests pass. Uses source tests as ground truth for correct IO declarations.
- **Inputs:** Migrated code (`dvp/02-migrated/`), migrated tests (`dvp/03-tests/migrated/`), source tests (reference), baseline CSVs, git repository on `sma/migration-process`
- **Outputs:** Fixed migrated code (in-place), fixed test infrastructure (in-place), git commit history on `sma/migration-process`
- **Depends on:** dvp-test-runner (all migrated tests must run first)
- **Details:** [`dvp-migrated-test-fixer/SKILL.md`](../dvp-migrated-test-fixer/SKILL.md)

---

## Tracking Manager Skills

### 13. dvp-ewi-tracking-manager

> **Creates a user-friendly HTML interface for EWI tracking.**

- **Category:** tracking-manager
- **Responsibility:** Creates a user-friendly interface for EWIs. Tracks which EWI was resolved by Cortex or by User.
- **Inputs:** EWI Inventory (JSON from ewi-extractor)
- **Outputs:** HTML dashboard with interactive EWI tracking
- **Depends on:** dvp-ewi-extractor
- **Details:** [docs/tracking-manager/dvp-ewi-tracking-manager.md](tracking-manager/dvp-ewi-tracking-manager.md)

---

### 14. dvp-ewi-fixer

> **Automatically resolves EWI codes in migrated source code.**

- **Category:** tracking-manager
- **Responsibility:** Help user fix specific EWIs -- all at once, or by category/priority. Updates the state of the fix at the EWI inventory.
- **Inputs:** SMA migrated source code (.py files with EWI comments)
- **Outputs:** Modified .py files with EWIs resolved, summary of changes
- **Depends on:** dvp-ewi-extractor (optional, can work standalone)
- **Status:** **Implemented** -- See [dvp-ewi-fixer/SKILL.md](../dvp-ewi-fixer/SKILL.md)

---

### 15. dvp-ewi-extractor

> **Extracts EWI issues from Issues.csv into structured JSON.**

- **Category:** tracking-manager
- **Responsibility:** Analyzes the Issues.csv from SMA and creates a structured JSON codebase for downstream skills.
- **Inputs:** Issues.csv (from SMA migration output)
- **Outputs:** EWI Inventory (JSON)
- **Depends on:** None (first in EWI chain)
- **Details:** [docs/tracking-manager/dvp-ewi-extractor.md](tracking-manager/dvp-ewi-extractor.md)

---

### 16. dvp-ewi-dashboard-generator

> **Orchestrates the EWI tracking manager workflow.**

- **Category:** tracking-manager
- **Responsibility:** Orchestrates the EWI tracking flow: extraction, dashboard generation, and fix tracking.
- **Inputs:** Issues.csv, SMA migrated source code
- **Outputs:** Coordinates ewi-extractor, ewi-tracking-manager, and ewi-fixer
- **Depends on:** dvp-ewi-extractor, dvp-ewi-tracking-manager, dvp-ewi-fixer
- **Details:** [docs/tracking-manager/dvp-ewi-dashboard-generator.md](tracking-manager/dvp-ewi-dashboard-generator.md)

---

## Migration Skills

### 17. snowflake-notebook-migration

> **Migrates Databricks notebooks to Snowflake Workspace notebooks (.ipynb).**

- **Category:** migration
- **Status:** **Implemented** -- See [`snowflake-notebook-migration/SKILL.md`](../../snowflake-notebook-migration/SKILL.md)
- **Responsibility:** Converts Databricks notebooks (`.ipynb` and Databricks-format `.py`/`.scala` files) to Snowflake Workspace notebooks. Preserves original code, only fixes incompatible patterns (magic commands, `dbutils`, `display()`, `_sqldf`, `%run` references). Supports dual-mode operation: standalone (GitHub URL or local directory) and orchestrated (invoked by `spark-migration` orchestrator at Step 10 with `<output>`, `conversion_type`, and branch context). In orchestrated mode, converts notebooks in-place within `<output>/Output/` and commits via `sma_api.git_commit()`. Conversion type determines the setup cell: `scos` uses Snowpark Connect (PySpark kept as-is); `snowpark_api` uses `get_active_session()`.
- **Inputs:** Databricks notebooks (standalone: GitHub URL or local path; orchestrated: `<output>/Output/` notebooks detected by orchestrator scan)
- **Outputs:** Converted Snowflake Workspace `.ipynb` notebooks (standalone: new files; orchestrated: in-place modifications)
- **Depends on:** `snowflake-notebooks` skill (loaded for output formatting: nbformat 4.5, SQL cell structure, Jinja templating, upload workflow)
- **Details:** See [`snowflake-notebook-migration/SKILL.md`](../../snowflake-notebook-migration/SKILL.md) and [`snowflake-notebook-migration/Diagram.md`](../../snowflake-notebook-migration/Diagram.md)
