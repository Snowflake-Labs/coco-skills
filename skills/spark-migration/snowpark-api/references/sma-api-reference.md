# `sma_api.py` Reference

Shared Python module providing programmatic access to `sma_storage.sqlite3` and the SMA migration git workflow. Used by `sma-dashboard-generator`, `dvp-ewi-fixer`, `stage-conversion`, and the `dvp-*` family.

**Module location:** `<snowpark_api_root>/scripts/sma_api.py`
(i.e. `data-engineering/spark-migration/snowpark-api/scripts/sma_api.py`)

All EWI/DB functions receive `workload_path: str` as their first parameter — the SMA output directory containing `sma_storage.sqlite3`.

## Available Functions

### Initialization

| Function | Description |
|----------|-------------|
| `initialize_database` | Create/load `sma_storage.sqlite3`, import Issues.csv, create tables |
| `create_artifact_dependency_tables` | Import ArtifactDependencyInventory.csv and build dependency graph |
| `create_input_files_table` | Import InputFilesInventory.csv |

### Read — EWI

| Function | Description |
|----------|-------------|
| `get_migration_summary` | High-level readiness summary (files, EWIs, blockers, readiness) |
| `list_ewis` | List EWIs with optional category/status filters |
| `get_blockers` | List critical blocker EWIs that prevent migration |
| `get_pending_ewi_codes` | Distinct pending EWI codes with descriptions |
| `get_ewis_by_code` | EWIs for a specific code, optionally filtered by status |
| `get_ewis_by_file` | EWIs for a specific file, optionally filtered by status |
| `get_summary_stats` | Status counts across all EWIs |
| `get_ewi_code_stats` | Per-code statistics |

### Read — Files

| Function | Description |
|----------|-------------|
| `list_files` | List files with their EWI summary |
| `get_file_details` | Detailed EWI info for a specific file |
| `get_ewi_descriptions` | All unique EWI code → description mappings |

### Read — Dependencies

| Function | Description |
|----------|-------------|
| `get_dependency_summary` | Dependency islands overview |
| `get_file_dependencies` | Dependencies for a specific file |
| `get_dependency_inventory` | Full artifact dependency inventory |
| `get_dependency_graph` | Dependency graph edges |

### Write — Status

| Function | Description |
|----------|-------------|
| `update_ewi_status` | Update status/notes for an EWI code (cascading to all rows) |
| `update_file_status` | Update status for all EWIs in a file (cascading) |
| `update_line_status` | Update status for a specific line in a file |
| `bulk_update_ewi_status` | Update status for multiple EWI codes at once |
| `update_ewi_notes` | Update only notes for an EWI code |
| `update_ewi_status_single` | Update status for a single EWI row (code + file_id + line) |
| `update_dependency_status` | Update status for a dependency edge |
| `update_file_validation` | Mark a file as validated |
| `update_recommended_actions` | Set recommended actions for a file |

### EWI Fixer

| Function | Description |
|----------|-------------|
| `generate_fix_id` | Generate a new fix session ID |
| `insert_fix_result` | Record a single fix attempt |
| `batch_insert_fix_results` | Batch insert multiple fix results |
| `get_fix_results` | Get fix results for a session |
| `get_fix_results_stats` | Get success/failed counts for a session |
| `insert_summary_start` | Create summary record with start time |
| `update_summary_end` | Complete summary record with final results |
| `get_fix_summary` | Get summary record for a session |

### Reset

| Function | Description |
|----------|-------------|
| `reset_not_resolved_to_pending` | Reset `not_auto_resolved` EWIs back to `pending` |
| `reset_all_to_pending` | Reset ALL EWIs to `pending` |

### Overview

| Function | Description |
|----------|-------------|
| `save_overview_stats` | Persist overview statistics to the database |

### Git Helpers

| Function | Description |
|----------|-------------|
| `git_ensure_branch` | Idempotently create/checkout `sma/migration-process` from `main` |
| `git_commit` | Stage + commit changes with a deterministic message format |
| `git_verify_branches` | Confirm both `main` and `sma/migration-process` exist; used by the Final Summary |

## Import Convention

```python
import sys
import importlib.util

SMA_API_PATH = "<snowpark_api_root>/scripts/sma_api.py"  # resolve from skill root

spec = importlib.util.spec_from_file_location("sma_api", SMA_API_PATH)
sma_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sma_api)
```

Or, equivalently, add the directory to `sys.path` and `import sma_api`.

## Cross-Platform Notes

- All SQLite paths are absolute; no chdir is required
- Git helpers are pure `subprocess.run` calls — no Git library dependency
- Functions are safe to call repeatedly (idempotent)
