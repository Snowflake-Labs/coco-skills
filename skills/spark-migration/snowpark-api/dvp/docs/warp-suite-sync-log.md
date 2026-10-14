# WARP Suite Sync Log

Tracks updates to vendored warp-suite packages in DVP skills.
Source: `/Users/mvega/snowbox-mvegalvarez/warp-suite`

## Sync Procedure

Each warp-suite package maps from `packages/<pkg>/src/<pkg>/` to a DVP location:

| warp-suite package | DVP destination |
|---|---|
| `warp_core` | `dvp-orchestrator/warp_core/` |
| `asg_pyspark` | `dvp-asg-generation/warp/asg_pyspark/` |
| `asg_scala` | `dvp-asg-generation/warp/asg_scala/` |
| `entrypoints` | `dvp-entrypoint-identifier/warp/entrypoints/` |
| `data_io` | `dvp-io-schema-identifier/warp/data_io/` |
| `synthetic_data` | `dvp-synthetic-data-generator/warp/synthetic_data/` |
| `diagnostic_reporter` | `dvp-synthetic-data-generator/warp/diagnostic_reporter/` |

To sync all packages:

```bash
SRC=/Users/mvega/snowbox-mvegalvarez/warp-suite/packages
DST=skills/dvp

rsync -av --delete $SRC/warp_core/src/warp_core/           $DST/dvp-orchestrator/warp_core/
rsync -av --delete $SRC/asg_pyspark/src/asg_pyspark/       $DST/dvp-asg-generation/warp/asg_pyspark/
rsync -av --delete $SRC/asg_scala/src/asg_scala/           $DST/dvp-asg-generation/warp/asg_scala/
rsync -av --delete $SRC/entrypoints/src/entrypoints/       $DST/dvp-entrypoint-identifier/warp/entrypoints/
rsync -av --delete $SRC/data_io/src/data_io/               $DST/dvp-io-schema-identifier/warp/data_io/
rsync -av --delete $SRC/synthetic_data/src/synthetic_data/  $DST/dvp-synthetic-data-generator/warp/synthetic_data/
rsync -av --delete $SRC/diagnostic_reporter/src/diagnostic_reporter/ $DST/dvp-synthetic-data-generator/warp/diagnostic_reporter/
```

After syncing, clean `__pycache__` directories and run tests.

---

## 2026-03-17

**Packages:** warp_core, asg_pyspark, asg_scala, entrypoints, data_io, synthetic_data, diagnostic_reporter

**What's new:**
- **Strict Identity & Dynamic Path Resolution** (`asg_pyspark/parser/spark_ast.py`, `warp_core/gap_analyzer.py`, `warp_core/unified_report.py`): refactored identity handling for more accurate path resolution.
- **cursor.execute() identity** (`asg_pyspark/parser/spark_ast.py`, `warp_core/ir/pyspark_models.py`): recognizes `cursor.execute()` calls as data operations; artifact `Typing` exclusion to reduce noise.
- **f-string fix** (`asg_pyspark/parser/spark_ast.py`): prevents f-string prefix from stripping plain variable names.
- **SQL DDL catalog** (`warp_core/catalog/ddl_catalog.py`): new standard SQL DDL reader for Python workloads; replaces `hive_ddl.py`.
- **Naming conventions** (`warp_core/schema/naming_conventions.py`): extended with text, type, and aggregate column-naming patterns backed by industry standards.

---

## 2026-03-16

**Packages:** warp_core (33 files), asg_pyspark (20), asg_scala (6), entrypoints (2), data_io (2), synthetic_data (5), diagnostic_reporter (2)

**What's new:**
- **Config file scanner** (`warp_core/config_file_scanner.py`): detects companion `.json`/`.yaml` config files in workload directories and classifies them by content type (data sources, schemas, pipeline wiring). Observational only — surfaced in ASG and reports.
- **Config ingestion strategies** (`warp_core/config_ingestion/`): platform-specific parsers for Airflow, Databricks, AWS Glue, and Telenav config formats.
- **warp_core** grew from 23 to 33 files with additional IR models and utilities.

---

## 2026-03-11

**Packages:** warp_core (23 files), asg_pyspark (20), asg_scala (6), entrypoints (2), data_io (2), synthetic_data (5), diagnostic_reporter (2)

**What's new:**
- **Entrypoint format**: `origin` field now emitted by detector (default `"ASG"`), `status` defaults to `"detected"`, new `reason` field (`main_guard`, `notebook`, `spark_session_creation`, `main_method`), `lineno` removed as standalone field (encoded in `source`).
- **data_io**: improved column attribution in joins, `ambiguous` confidence for unresolved columns.
- **SyntheticDataGenerator**: removed `ambiguity_actions.json` output, ambiguity handled internally.

---

## 2026-03-05

**Packages:** warp_core, asg_pyspark, asg_scala, entrypoints, data_io, synthetic_data, diagnostic_reporter

**What's new:**
- Initial integration of all 7 warp-suite packages into DVP skills.
- Shared `warp_core` at `dvp-orchestrator/warp_core/` (committed to git), skill-specific packages in each skill's `warp/`.
- ASG supports both PySpark (Python) and Scala workloads.
- Anomaly detection (`XX_anomalies.json`) attached to `dvp-asg-generation`.
- Comprehensive reports (`XX_gaps.json`, `XX_REPORT.md`, `XX_warp_intel.json`, `XX_issues_report.json`) attached to `dvp-synthetic-data-generator`.
