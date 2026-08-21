# SMA Dashboard Generator - Architecture Diagram

> **Last Updated**: 2026-03-23
> **Note**: Keep this diagram updated when making changes to the skill.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        SMA DASHBOARD GENERATOR - FLUJO                          │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────┐
│   TRIGGER       │  "sma dashboard", "ewi tracker", "track EWIs"
│   (Cortex)      │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 1: ASK WORKLOAD PATH                                                      │
│  ask_user_question → "<workload_path>"                                          │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 2: VERIFY REQUIREMENTS                                                    │
│  Check: <workload_path>/Reports/Issues.csv                                      │
│         <workload_path>/Reports/ArtifactDependencyInventory.csv                 │
│         <workload_path>/Reports/InputFilesInventory.csv                         │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 3: RUN sma_manager.py                                                     │
│  python3 scripts/sma_manager.py "<workload_path>" --template-dir templates      │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           sma_manager.py INTERNAL FLOW                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌──────────────────────┐                                                       │
│  │ detect_data_source() │ ← Busca CSV o SQLite existente                        │
│  └──────────┬───────────┘                                                       │
│             │                                                                   │
│             ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │                    CREAR/ACTUALIZAR SQLite                              │    │
│  │  ┌─────────────────────────┐  ┌────────────────────────────────────┐    │    │
│  │  │ create_sqlite_from_csv()│  │ create_artifact_dependency_tables()│    │    │
│  │  │ Issues.csv → issues     │  │ ArtifactDependency.csv →           │    │    │
│  │  └─────────────────────────┘  │   - artifact_dependency_inventory  │    │    │
│  │                               │   - artifact_dependency_summary    │    │    │
│  │  ┌─────────────────────────┐  │   - artifact_dependency_graph      │    │    │
│  │  │ create_input_files_table│  └────────────────────────────────────┘    │    │
│  │  │ InputFiles.csv →        │                                            │    │
│  │  │   input_files_inventory │  Calcula: islands, parents, actions        │    │
│  │  └─────────────────────────┘                                            │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│             │                                                                   │
│             ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │                    EXTRAER DATOS                                        │    │
│  │  extract_ewi_data_from_rows() → ewi_data, file_data                     │    │
│  │  read_tool_execution_info() → tool_info                                 │    │
│  │  read_log_info() → log_info                                             │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│             │                                                                   │
│             ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │                    GENERAR HTML                                         │    │
│  │                                                                         │    │
│  │  ┌─────────────────────┐     ┌─────────────────────────────────────┐    │    │
│  │  │ generate_index_html │ ──► │ index.html (iframe container)       │    │    │
│  │  └─────────────────────┘     └─────────────────────────────────────┘    │    │
│  │                                                                         │    │
│  │  ┌─────────────────────────┐ ┌─────────────────────────────────────┐    │    │
│  │  │ Calculate overview_data │►│ ewi-tracker/overview.html           │    │    │
│  │  │ - migration_readiness   │ │ (cards, blockers, complexity)       │    │    │
│  │  │ - blockers              │ └─────────────────────────────────────┘    │    │
│  │  │ - file_complexity       │                                            │    │
│  │  └─────────────────────────┘                                            │    │
│  │                                                                         │    │
│  │  ┌─────────────────────────┐ ┌─────────────────────────────────────┐    │    │
│  │  │generate_ewi_tracker_html│►│ ewi-tracker/ewi_tracker.html        │    │    │
│  │  └─────────────────────────┘ └─────────────────────────────────────┘    │    │
│  │                                                                         │    │
│  │  ┌──────────────────────────┐┌─────────────────────────────────────┐    │    │
│  │  │generate_file_tracker_html││ewi-tracker/file_tracker.html        │    │    │
│  │  └──────────────────────────┘└─────────────────────────────────────┘    │    │
│  │                                                                         │    │
│  │  ┌─────────────────────────┐ ┌─────────────────────────────────────┐    │    │
│  │  │ dependency_tracker.html │►│ ewi-tracker/dependency_tracker.html │    │    │
│  │  └─────────────────────────┘ └─────────────────────────────────────┘    │    │
│  │                                                                         │    │
│  │  ┌─────────────────────────┐ ┌─────────────────────────────────────┐    │    │
│  │  │ test_tracker.html       │►│ ewi-tracker/test_tracker.html       │    │    │
│  │  └─────────────────────────┘ └─────────────────────────────────────┘    │    │
│  │                                                                         │    │
│  │  ┌─────────────────────────┐ ┌─────────────────────────────────────┐    │    │
│  │  │ generate_files_page()   │►│ content/ewi_tracker/files_*.html    │    │    │
│  │  │ (per EWI code)          │ │ (detail: files affected by EWI)     │    │    │
│  │  └─────────────────────────┘ └─────────────────────────────────────┘    │    │
│  │                                                                         │    │
│  │  ┌─────────────────────────┐ ┌─────────────────────────────────────┐    │    │
│  │  │generate_file_ewis_page()│►│ content/file_tracker/file_ewis_*.html│   │    │
│  │  │ (per file)              │ │ (detail: EWIs in specific file)     │    │    │
│  │  └─────────────────────────┘ └─────────────────────────────────────┘    │    │
│  │                                                                         │    │
│  │  ┌──────────────────────────┐┌─────────────────────────────────────┐    │    │
│  │  │generate_dependency_detail││content/dependency_tracker/dep_*.html│    │    │
│  │  │ (per file)               │ │(detail: dependencies per file)     │    │    │
│  │  └──────────────────────────┘└─────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│             │                                                                   │
│             ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  COPIAR ASSETS & SERVIDOR                                               │    │
│  │  - server/sma_server.py, start_server.py                                │    │
│  │  - assets/styles.css                                                    │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│             │                                                                   │
│             ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  WORKLOAD ROOT FILES (deterministic, merge-safe)                        │    │
│  │  _ensure_workload_gitignore() → .gitignore (common entries)             │    │
│  │  _ensure_vscode_settings()    → .vscode/settings.json (pytest config)   │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│             │                                                                   │
│             ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  START SERVER (localhost:8080)                                          │    │
│  │  - Sirve archivos estaticos                                             │    │
│  │  - API endpoints para CRUD via sma_api.py                               │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  OUTPUT STRUCTURE                                                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  <workload>/                                                                    │
│  ├── .gitignore                  ← Common entries (venv, pycache, OS)           │
│  ├── .vscode/settings.json      ← Pytest config, extraPaths (auto-detect)      │
│  ├── sma_storage.sqlite3          ← Base de datos unica                         │
│  └── sma-dashboard/                                                             │
│      ├── manifest.json            ← Config del dashboard                        │
│      ├── index.html               ← Container principal (iframes)               │
│      ├── server/                                                                │
│      │   ├── sma_server.py        ← Servidor HTTP + API                         │
│      │   ├── sma_api.py           ← Copia de módulo DB+Git (standalone)          │
│      │   └── start_server.py      ← Script para iniciar                         │
│      ├── assets/styles.css                                                      │
│      └── ewi-tracker/                                                           │
│          ├── overview.html        ← Vista Overview                              │
│          ├── ewi_tracker.html     ← Vista EWI Tracker                           │
│          ├── file_tracker.html    ← Vista File Tracker                          │
│          ├── dependency_tracker.html ← Vista Dependency                         │
│          ├── test_tracker.html   ← Vista Test Tracker                          │
│          └── content/                                                           │
│              ├── ewi_tracker/files_*.html      ← Detalles por EWI               │
│              ├── file_tracker/file_ewis_*.html ← Detalles por archivo           │
│              └── dependency_tracker/dep_*.html ← Detalles por dependencia       │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 4: POST-GENERATION                                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│  - print_summary() → Muestra resumen al usuario                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Database Schema

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  sma_storage.sqlite3                                                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────┐    ┌────────────────────────────────────────────┐  │
│  │ issues                  │    │ artifact_dependency_inventory              │  │
│  ├─────────────────────────┤    ├────────────────────────────────────────────┤  │
│  │ id (PK)                 │    │ id (PK)                                    │  │
│  │ issue_code              │    │ file_id                                    │  │
│  │ issue_category          │    │ dependency                                 │  │
│  │ issue_description       │    │ type                                       │  │
│  │ file_path               │    │ indirect_dependencies                      │  │
│  │ line_number             │    │ direct_parent                              │  │
│  │ status                  │    │ indirect_parents                           │  │
│  │ notes                   │    └────────────────────────────────────────────┘  │
│  │ updated_at              │                                                    │
│  └─────────────────────────┘    ┌────────────────────────────────────────────┐  │
│                                 │ artifact_dependency_summary                │  │
│  ┌─────────────────────────┐    ├────────────────────────────────────────────┤  │
│  │ input_files_inventory   │    │ file_id (PK)                               │  │
│  ├─────────────────────────┤    │ island                                     │  │
│  │ id (PK)                 │    │ total_dependencies                         │  │
│  │ file_path               │    │ total_user_code_file                       │  │
│  │ technology              │    │ total_issues                               │  │
│  │ extension               │    │ recommended_actions                        │  │
│  │ lines_of_code           │    │ validated                                  │  │
│  │ ignored                 │    └────────────────────────────────────────────┘  │
│  └─────────────────────────┘                                                    │
│                                 ┌────────────────────────────────────────────┐  │
│  ┌─────────────────────────┐    │ artifact_dependency_graph                  │  │
│  │ overview_stats          │    ├────────────────────────────────────────────┤  │
│  ├─────────────────────────┤    │ source                                     │  │
│  │ id (PK)                 │    │ target                                     │  │
│  │ stat_key                │    │ island                                     │  │
│  │ stat_value              │    └────────────────────────────────────────────┘  │
│  │ updated_at              │                                                    │
│  └─────────────────────────┘                                                    │
│                                                                                 │
│  ┌─────────────────────────┐    ┌────────────────────────────────────────────┐  │
│  │ entrypoint_tests        │    │ entrypoint_test_runs                       │  │
│  ├─────────────────────────┤    ├────────────────────────────────────────────┤  │
│  │ id (PK)                 │    │ id (PK)                                    │  │
│  │ entrypoint_name         │    │ test_id (FK → entrypoint_tests.id)         │  │
│  │ entrypoint_source       │    │ status                                     │  │
│  │ test_file               │    │ error_message                              │  │
│  │ test_type               │    │ duration_seconds                           │  │
│  │ status                  │    │ executed_at                                │  │
│  │ created_at              │    └────────────────────────────────────────────┘  │
│  └─────────────────────────┘                                                    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Navigation Flow (Browser)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  NAVEGACION DEL DASHBOARD (BROWSER)                                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  index.html                                                                     │
│  ┌─────────────────┬───────────────────────────────────────────────────────┐    │
│  │  SIDE PANEL     │                    IFRAME CONTENT                     │    │
│  │                 │                                                       │    │
│  │  ┌───────────┐  │   overview.html ──────────────────────────────────►   │    │
│  │  │ Overview  │──┼──►  Cards: Ready/Review/Blocked                       │    │
│  │  └───────────┘  │     Blockers list                                     │    │
│  │                 │     Complexity chart                                  │    │
│  │  ┌───────────┐  │                                                       │    │
│  │  │ EWI       │──┼──► ewi_tracker.html ───────► files_CODE.html          │    │
│  │  │ Tracker   │  │     (tabla de EWIs)          (archivos afectados)     │    │
│  │  └───────────┘  │                                                       │    │
│  │                 │                                                       │    │
│  │  ┌───────────┐  │                                                       │    │
│  │  │ File      │──┼──► file_tracker.html ──────► file_ewis_N.html         │    │
│  │  │ Tracker   │  │     (tabla de archivos)      (EWIs del archivo)       │    │
│  │  └───────────┘  │                                                       │    │
│  │                 │                                                       │    │
│  │  ┌───────────┐  │                                                       │    │
│  │  │Dependency │──┼──► dependency_tracker.html ─► dep_N.html              │    │
│  │  │ Tracker   │  │     (tabla + grafo)          (detalle dependencia)    │    │
│  │  └───────────┘  │     ↑                                                 │    │
│  │                 │     │ URL Hash State (#page=2&sort=file_id&dir=asc)   │    │
│  │                 │     │ Preserves: page, sort, direction, island filter │    │
│  │                 │     │ Uses: history.replaceState(), history.back()    │    │
│  │                 │                                                       │    │
│  │  ┌───────────┐  │                                                       │    │
│  │  │ Test      │──┼──► test_tracker.html                                  │    │
│  │  │ Tracker   │  │     (tests list + run history panel)                  │    │
│  │  └───────────┘  │                                                       │    │
│  │                 │                                                       │    │
│  └─────────────────┴───────────────────────────────────────────────────────┘    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Database Access (sma_api.py)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  sma_api.py — UNIFIED DATABASE AND GIT ACCESS                                   │
│  Location: skills/spark-migration/snowpark-api/scripts/sma_api.py                │
│  All functions receive workload_path: str as first parameter                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Functions used by sma_manager.py (generation):                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  Initialization:                                                        │    │
│  │  - initialize_database         → Create/load sma_storage.sqlite3        │    │
│  │  - create_artifact_dependency_tables → Import dependency CSV            │    │
│  │  - create_input_files_table    → Import input files CSV                 │    │
│  │                                                                         │    │
│  │  Read — EWI:                                                            │    │
│  │  - get_migration_summary       → Readiness summary                      │    │
│  │  - list_ewis                   → EWIs with filters                      │    │
│  │  - get_blockers                → Critical blockers                      │    │
│  │  - get_summary_stats           → Status counts                          │    │
│  │                                                                         │    │
│  │  Read — Files/Dependencies:                                             │    │
│  │  - list_files                  → Files with EWI summary                 │    │
│  │  - get_file_details            → EWIs in specific file                  │    │
│  │  - get_dependency_summary      → Dependency islands (aggregated)        │    │
│  │  - get_dependency_graph        → Graph edges for visualization          │    │
│  │                                                                         │    │
│  │  Write — Status:                                                        │    │
│  │  - update_ewi_status           → Update EWI code status (cascading)     │    │
│  │  - update_file_status          → Update file status (cascading)         │    │
│  │  - update_line_status          → Update specific line status            │    │
│  │  - bulk_update_ewi_status      → Update multiple codes at once          │    │
│  │  - save_overview_stats         → Persist overview statistics            │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  Functions used by sma_server.py (runtime API):                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  Read:                                                                  │    │
│  │  - read_issues_raw(db_path=)   → Raw issue rows for aggregation         │    │
│  │  - extract_ewi_data()          → Aggregate ewi_data + file_data         │    │
│  │  - get_ewi_descriptions()      → Code → description mapping            │    │
│  │  - get_dependency_summary_by_file() → Per-file dependency rows          │    │
│  │  - get_dependency_inventory()  → Full dependency inventory              │    │
│  │  - get_dependency_graph()      → Nodes, edges, islands for viz          │    │
│  │                                                                         │    │
│  │  Write:                                                                 │    │
│  │  - update_ewi_status()         → All lines of EWI across all files      │    │
│  │  - update_ewi_notes()          → Notes for all occurrences of EWI       │    │
│  │  - update_file_status()        → All lines in a file                    │    │
│  │  - update_file_ewi_status()    → All lines of EWI within a file         │    │
│  │  - update_line_status()        → Single line update                     │    │
│  │  - update_dependency_status()  → Dependency + recalculate validation    │    │
│  │  - update_file_validation()    → Direct validation update               │    │
│  │  - update_recommended_actions()→ File recommended actions               │    │
│  │                                                                         │    │
│  │  Read — Tests:                                                          │    │
│  │  - get_tests()                → All registered tests                    │    │
│  │  - get_test_runs()            → Test run history (optionally by test)   │    │
│  │  - has_tests()                → Check if tests table has data           │    │
│  │                                                                         │    │
│  │  Write — Tests:                                                         │    │
│  │  - create_tests_table()       → Create entrypoint_tests + entrypoint_test_runs tables │    │
│  │  - register_tests()           → Register tests from entrypoints         │    │
│  │  - update_test_status()       → Update test status                      │    │
│  │  - insert_test_run()          → Record a test execution run             │    │
│  │                                                                         │    │
│  │  Export:                                                                │    │
│  │  - export_test_results()     → CSV export to 04-results/testing-results │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Key Functions Reference

| Function | File | Purpose |
|----------|------|---------|
| `detect_data_source()` | sma_manager.py | Find CSV/SQLite data sources |
| `create_sqlite_from_csv()` | sma_manager.py | Create issues table from CSV |
| `create_artifact_dependency_tables()` | sma_manager.py | Create dependency tables + islands |
| `create_input_files_table()` | sma_manager.py | Create input files inventory |
| `extract_ewi_data_from_rows()` | extractors/ewi_extractor.py | Extract EWI/file data structures |
| `generate_index_html()` | sma_manager.py | Generate main iframe container |
| `generate_ewi_tracker_html()` | sma_manager.py | Generate EWI tracker view |
| `generate_file_tracker_html()` | sma_manager.py | Generate file tracker view |
| `generate_files_page()` | sma_manager.py | Generate EWI detail pages |
| `generate_file_ewis_page()` | sma_manager.py | Generate file detail pages |
| `generate_dependency_detail_page()` | sma_manager.py | Generate dependency detail pages |
| `save_overview_stats()` | sma_manager.py | Save overview stats to SQLite |
| `print_summary()` | sma_manager.py | Print generation summary |
| `_ensure_workload_gitignore()` | sma_manager.py | Create/merge .gitignore at workload root |
| `_ensure_vscode_settings()` | sma_manager.py | Create/merge .vscode/settings.json at workload root |
| `create_tests_table()` | sma_api.py | Create entrypoint_tests + entrypoint_test_runs tables |
| `register_tests()` | sma_api.py | Register tests from entrypoints |
| `get_tests()` | sma_api.py | Get all registered tests |
| `get_test_runs()` | sma_api.py | Get test run history |
| `insert_test_run()` | sma_api.py | Record a test execution run |
| `update_test_status()` | sma_api.py | Update test status |
| `export_test_results()` | sma_api.py | Export test results to CSV files |

## Templates

| Template | Output | Description |
|----------|--------|-------------|
| `index.html` | `index.html` | Main dashboard with side panel + iframe |
| `overview.html` | `ewi-tracker/overview.html` | Overview cards and charts |
| `ewi_tracker.html` | `ewi-tracker/ewi_tracker.html` | EWI list with filters |
| `file_tracker.html` | `ewi-tracker/file_tracker.html` | File list with EWI counts |
| `dependency_tracker.html` | `ewi-tracker/dependency_tracker.html` | Dependency table + graph |
| `test_tracker.html` | `ewi-tracker/test_tracker.html` | Test list with status + run history |
| `files_detail.html` | `content/ewi_tracker/files_*.html` | Files affected by EWI |
| `file_ewis_detail.html` | `content/file_tracker/file_ewis_*.html` | EWIs in specific file |
| `dependency_detail.html` | `content/dependency_tracker/dep_*.html` | Dependency details |

## Browser State Management (dependency_tracker.html)

The Dependency Tracker uses URL hash state to preserve user navigation context:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  URL HASH STATE FLOW                                                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  dependency_tracker.html                                                        │
│  ┌────────────────────────────────────────────────────────────────────────────┐ │
│  │                                                                            │ │
│  │  User interacts with table (page, sort, filter)                            │ │
│  │                    │                                                       │ │
│  │                    ▼                                                       │ │
│  │  saveStateToHash() ─────────► URL: #page=2&sort=file_id&dir=asc&islands=5  │ │
│  │  (history.replaceState)                                                    │ │
│  │                                                                            │ │
│  │  User clicks row ────────────► dep_N.html (detail page)                    │ │
│  │                                      │                                     │ │
│  │                                      ▼                                     │ │
│  │                               "Back to List" button                        │ │
│  │                               onclick: history.back()                      │ │
│  │                                      │                                     │ │
│  │                                      ▼                                     │ │
│  │  ◄─────────────────────── Browser back navigation                          │ │
│  │                                                                            │ │
│  │  pageshow event (bfcache) OR initial load                                  │ │
│  │                    │                                                       │ │
│  │                    ▼                                                       │ │
│  │  restoreStateFromHash() ──► Reads: page, sort, dir, islands from hash      │ │
│  │                    │                                                       │ │
│  │                    ▼                                                       │ │
│  │  renderTable(false) ──────► Table rendered with restored state             │ │
│  │  (resetPage=false)                                                         │ │
│  │                                                                            │ │
│  └────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                 │
│  Key: summaryData.sort() by file_id ensures row index matches dep_N.html files  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Constraints

| # | Constraint | Reason |
|---|-----------|--------|
| 1 | All DB operations must go through `sma_api.py` functions | sma_server.py and sma_manager.py both use sma_api.py — no direct sqlite3 calls |
| 2 | It is recommended to run this skill in the foreground (not as a background agent) | Foreground execution ensures full tool access and proper user interaction |
