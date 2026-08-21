---
name: sma-dashboard-generator
description: "Generate interactive SMA dashboard to track EWIs from conversion. Triggers: sma dashboard, ewi tracker, track EWIs, conversion issues."
---

# SMA Dashboard

Interactive dashboard for Snowflake Migration Accelerator (SMA) analysis results.

## Modules

| Module | Description |
|--------|-------------|
| **EWI Tracker** | Track and manage EWI (Errors, Warnings, Issues) from conversion |
| **File Tracker** | Track files and their EWIs with line-level status |
| **Dependency Tracker** | View artifact dependencies with island visualization |

## Requirements

The script expects the `Reports/` folder to be in the **root** of the workload directory with at least one of:
- `Reports/Issues.csv` (with data rows)
- `Reports/ArtifactDependencyInventory.csv` (with data rows)

**IMPORTANT**: 
- `sma-output/Reports/` is NOT supported. The `Reports/` folder must be directly in the root.
- This tool ONLY creates/uses `sma_storage.sqlite3`. It will NOT modify any other SQLite files.

**Note**: The dashboard will be generated with only the modules for which data is available:
- **EWI Tracker** and **File Tracker**: Require Issues.csv with data rows
- **Dependency Tracker**: Requires ArtifactDependencyInventory.csv with data rows

If a CSV file exists but only contains the header (no data rows), that module will show "No data available".

## Output Format

Every time you begin a step, sub-step, or significant action, prefix the message with a timestamp in the format `[YYYY-MM-DD HH:MM:SS]`. Obtain the current time by running `date '+%Y-%m-%d %H:%M:%S'` in bash.

Example:
```
[2026-03-24 14:05:32] Starting Step 1: Detect SMA output path...
[2026-03-24 14:05:33] Found SMA output at /path/to/output
[2026-03-24 14:05:34] Step 1 complete.
```

## Usage

### Step 0: Initialize Git

Ensure the workload directory has a git repository on the `sma/migration-process` branch. This is idempotent — if the orchestrator already initialized git, this is a no-op.

```python
result = sma_api.git_ensure_ready("<workload_path>")
```

### Step 1: Ask User for Workload Path

**Ask** user for the path to the SMA output directory:
   - Use `ask_user_question` tool with type "text"
   - Question: "Path to SMA output directory (containing Output, Reports, Logs folders)"
   - This directory contains:
     - `Output/` - Converted .py/.ipynb files
     - `Reports/` - Issues.csv or IssuesConnect.csv
     - `Logs/` - Conversion logs

### Step 2: Verify Requirements

Once you have the workload path, verify the data source exists:

```bash
# Check for Reports/ folder in root (NOT sma-output/Reports/)
ls "<workload_path>/Reports/Issues.csv" 2>/dev/null || \
ls "<workload_path>/Reports/ArtifactDependencyInventory.csv" 2>/dev/null
```

If none exist, inform the user that no valid data source was found. Remind them that `Reports/` must be in the root directory.

### Step 3: Run the SMA Dashboard Manager

```bash
cd "<workload_path>" && python3 "<skill_directory>/scripts/sma_manager.py" --template-dir "<skill_directory>/templates"
```

Where:
- `<workload_path>` is the path provided by the user
- `<skill_directory>` is the directory where this SKILL.md file is located

**IMPORTANT**: Do NOT pass a csv_path argument. Let the script find data sources automatically.

The script will:
1. Auto-detect the data source (SQLite or CSV)
2. Parse and extract unique EWI codes
3. Process ArtifactDependencyInventory.csv if available
4. Calculate dependency islands (connected components)
5. Create `sma-dashboard/` folder with:
   - `manifest.json` - Dashboard configuration and module status
   - `index.html` - Main dashboard with side panel navigation
   - `server/sma_server.py` - Standalone server script
   - `assets/styles.css` - Stylesheet
   - `ewi-tracker/` - EWI tracker, File tracker, and Dependency tracker modules
6. Print the summary
7. Start the dashboard server and open in browser

The server will keep running until the user presses `Ctrl+C`. While running:
- Changes to status/notes are saved automatically to SQLite
- The dashboard stays in sync with the database

### Step 4: Report results

The script outputs a complete summary including:
- List of generated files
- Total EWIs and occurrences
- Breakdown by category
- Dependency islands found
- Status information

**Note**: The EWI Fixer can be invoked separately via the `spark-migration` skill (which loads `snowpark-api/dvp/dvp-ewi-fixer/SKILL.md` as a bundled sub-skill). It uses the same database, so any changes will be reflected when you refresh the dashboard.

### Step 5: Commit Changes to Git

After the dashboard is generated, commit the changes:

```python
result = sma_api.git_commit("<workload_path>", """DVP SMA Dashboard: Generated interactive dashboard

Dashboard: sma-dashboard/
Modules: EWI Tracker, File Tracker, Dependency Tracker
Database: sma_storage.sqlite3""")
```

Verify branches:
```python
result = sma_api.git_verify_branches("<workload_path>")
```

### Script Options

| Argument | Description |
|----------|-------------|
| `--output-base <path>` | Override auto-detected output files location |
| `--template-dir <dir>` | Directory containing HTML templates (required) |
| `--no-open` | Don't open dashboard in browser |

## Output Structure

```
sma-dashboard/
├── manifest.json               # Dashboard configuration
├── index.html                  # Main dashboard with side panel
├── server/
│   └── sma_server.py           # Standalone server script
├── assets/
│   └── styles.css              # Stylesheet
└── ewi-tracker/
    ├── ewi_tracker.html        # EWI Tracker view
    ├── file_tracker.html       # File Tracker view
    ├── dependency_tracker.html # Dependency Tracker view
    └── content/
        ├── files_*.html        # Detail pages per EWI
        └── file_ewis_*.html    # Detail pages per File
```

The folder is **standalone** - you can share it and run the server later with:
```bash
cd sma-dashboard
python3 server/sma_server.py .
```

## Database Tables

### issues
Main table storing EWI data from Issues.csv with status tracking.

### artifact_dependency_inventory
Stores all artifact dependencies from ArtifactDependencyInventory.csv.

### artifact_dependency_summary
Aggregated view of dependencies per file with island assignment.

### artifact_dependency_graph
Stores edges for the dependency graph visualization.

## Status Values

| Status | Description |
|--------|-------------|
| `pending` | Not yet reviewed |
| `in_progress` | Currently being worked on |
| `manual_resolved` | Issue has been manually addressed |
| `auto_resolved` | Issue was automatically resolved |
| `not_auto_resolved` | Auto-resolver could not resolve this issue |
| `wont_fix` | Decided not to fix |

## Database Access Module

A shared Python module provides programmatic access to `sma_storage.sqlite3` so AI agents can query and update dashboard data conversationally, without needing the browser UI.

**Module location:** `skills/spark-migration/snowpark-api/scripts/sma_api.py`

All functions receive `workload_path: str` as their first parameter (the directory containing `sma_storage.sqlite3`).

### Available Functions

All functions receive `workload_path: str` as first parameter.

#### Initialization
| Function | Description |
|----------|-------------|
| `initialize_database` | Create/load `sma_storage.sqlite3`, import Issues.csv, create tables |
| `create_artifact_dependency_tables` | Import ArtifactDependencyInventory.csv and build dependency graph |
| `create_input_files_table` | Import InputFilesInventory.csv |

#### Read — EWI
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

#### Read — Files
| Function | Description |
|----------|-------------|
| `list_files` | List files with their EWI summary |
| `get_file_details` | Detailed EWI info for a specific file |
| `get_ewi_descriptions` | All unique EWI code → description mappings |

#### Read — Dependencies
| Function | Description |
|----------|-------------|
| `get_dependency_summary` | Dependency islands overview |
| `get_file_dependencies` | Dependencies for a specific file |
| `get_dependency_inventory` | Full artifact dependency inventory |
| `get_dependency_graph` | Dependency graph edges |

#### Write — Status
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

#### EWI Fixer
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

#### Reset
| Function | Description |
|----------|-------------|
| `reset_not_resolved_to_pending` | Reset `not_auto_resolved` EWIs back to `pending` |
| `reset_all_to_pending` | Reset ALL EWIs to `pending` |

#### Overview
| Function | Description |
|----------|-------------|
| `save_overview_stats` | Persist overview statistics to the database |

## Final Summary

**MANDATORY**: After completing all steps (whether running standalone or invoked from the orchestrator), ALWAYS present this summary table:

```
SMA Dashboard Complete

┌───────────────────┬──────────┬──────────────────────────────────────────────────────┐
│ Step              │ Status   │ Details                                              │
├───────────────────┼──────────┼──────────────────────────────────────────────────────┤
│ SMA Dashboard     │ Done     │ Generated dashboard at sma-dashboard/                │
└───────────────────┴──────────┴──────────────────────────────────────────────────────┘

Output location: <output>/sma-dashboard/

Git branches:
• main — original code (unmodified)
• sma/migration-process — dashboard generation changes applied
```

**Rules:**
- Status is `Done`, `Skipped`, or `Failed`
- If dashboard generation failed, show `Failed` with brief error
- The git branches section uses `sma_api.git_verify_branches()` to confirm both branches exist
