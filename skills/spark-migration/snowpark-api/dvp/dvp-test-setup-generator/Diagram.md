# dvp-test-setup-generator — Architecture Diagram

> Last updated: 2026-03-23

## Overview

Generates a pytest project under `dvp/03-tests/` and registers tests in `sma_storage.sqlite3` for dashboard tracking.

## Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                   dvp-test-setup-generator                      │
│                                                                 │
│  Inputs:                                                        │
│    dvp/04-results/entrypoints.json                              │
│    dvp/04-results/data_io_schema.json                           │
│    dvp/04-results/synthetic_data/*.csv                          │
│    templates/ (conftest.py, config.py, requirements.txt,        │
│                DVP-TESTING.md, etc.)                              │
│                                                                 │
│  Steps:                                                         │
│    Step 0: git_ensure_ready()                                   │
│    Step 1: Copy templates → dvp/03-tests/                       │
│    Step 2: Read entrypoints (status == "detected")              │
│    Step 3: Generate class-based test_*.py per entrypoint         │
│            (inherits BaseSourceWorkloadTest / BaseMigrated...)   │
│            Populates INPUT_FILES, OUTPUT_FILES from io_schema    │
│    Step 4: register_tests.py → sma_storage.sqlite3  ◄── NEW    │
│    Step 5: Remind user to configure config.py                   │
│    Step 6: git_commit()                                         │
│                                                                 │
│  Outputs:                                                       │
│    <workload_path>/                                             │
│    ├── .gitignore              ◄── test-specific entries merged  │
│    │                               (Spark, Hive, pytest_cache)   │
│    ├── DVP-TESTING.md          ◄── at workload root             │
│    └── dvp/03-tests/                                            │
│        ├── conftest.py                                          │
│        ├── config.py                                            │
│        ├── pytest.ini                                           │
│        ├── requirements.txt                                     │
│        ├── source/                                              │
│        │   ├── conftest.py                                      │
│        │   └── test_*.py  (class per entrypoint, inherits base)   │
│        └── migrated/ (or migrated_scos/)                        │
│            ├── conftest.py                                      │
│            └── test_*.py  (class per entrypoint, inherits base) │
│                                                                 │
│    sma_storage.sqlite3                                          │
│    ├── entrypoint_tests   (one row per test file)               │
│    └── entrypoint_test_runs (populated later by test execution) │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Runtime: Pytest Hook (auto-tracking)

The root `conftest.py` template includes a `pytest_runtest_makereport` hook that automatically records test results in `sma_storage.sqlite3` when tests are executed.

```
pytest dvp/03-tests/ -v
    │
    ├── test passes/fails/skips
    │       │
    │       ▼
    │   pytest_runtest_makereport hook (conftest.py)
    │       │
    │       ├── extract entrypoint name from test file stem
    │       ├── detect test_type from parent dir (source/migrated)
    │       ├── lookup test_id in entrypoint_tests
    │       └── INSERT into entrypoint_test_runs + UPDATE entrypoint_tests.status
    │
    └── sma_storage.sqlite3
        ├── entrypoint_tests     (status updated per test)
        └── entrypoint_test_runs (one row per test execution)
```

Tracking activates only when `sma_storage.sqlite3` exists and has `entrypoint_tests` table. Failures in tracking never break test execution (all wrapped in try/except).

## Scripts

```
dvp-test-setup-generator/
├── SKILL.md
├── Diagram.md
├── scripts/
│   └── register_tests.py    ← CLI: --workload-path <path>
└── templates/
    ├── conftest.py
    ├── config.py
    ├── pytest.ini
    ├── requirements.txt
    ├── DVP-TESTING.md
    ├── source/
    │   └── conftest.py
    ├── migrated/
    │   └── conftest.py
    └── migrated_scos/
        └── conftest.py
```

## register_tests.py

All paths are relative to `workload_path` (v1: `Conversion-*/`, v2: `sma-output/`, v3: latest `sma-code-process-*` under **`Conversion_SnowparkAPI`** or **`Conversion_SnowparkConnect`**). The same relative paths apply — `workload_path/dvp/`, `workload_path/sma_storage.sqlite3`, and `workload_path/sma-dashboard/` once `<output>` is resolved.

```
entrypoints.json ──► register_tests.py ──► sma_api.register_tests()
                          │                        │
                      scan dvp/03-tests/        sma_storage.sqlite3
                      source/ + migrated/       ├── entrypoint_tests
                      match by stem             └── (creates table if needed)
                      (strip - and _ for match,
                       handles CamelCase↔snake_case)
                          │
                     _update_dashboard_manifest()
                     └── workload_path/sma-dashboard/manifest.json
```

### Dependencies

- `sma_api.py` at `skills/spark-migration/snowpark-api/scripts/sma_api.py`
  - `register_tests(workload_path, tests_list)` — dedup insert by entrypoint_name + test_type
  - `create_tests_table()` — called automatically by register_tests

### Responsibility Split

- **`.gitignore` (common entries)** and **`.vscode/settings.json`** are generated by the **dashboard generator** (`sma_manager.py`), which runs in all flows.
- **`.gitignore` (test-specific entries)** (Spark, Hive, pytest_cache) and **`DVP-TESTING.md`** are handled here by `register_tests.py`, which only runs in the test setup flow.
