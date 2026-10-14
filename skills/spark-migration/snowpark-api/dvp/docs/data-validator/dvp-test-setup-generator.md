# dvp-test-setup-generator

> Generate pytest scaffolding and workload test files for DVP.

## Overview

| Field | Value |
|-------|-------|
| **Category** | data-validator |
| **Status** | **Implemented** |
| **Output** | pytest project under `dvp/03-tests/` |
| **Depends on** | dvp-orchestrator, dvp-entrypoint-identifier, dvp-io-schema-identifier, dvp-synthetic-data-generator |
| **SKILL.md** | [`dvp-test-setup-generator/SKILL.md`](../../dvp-test-setup-generator/SKILL.md) |

## Responsibility

Create a runnable pytest project that can:

1. Run **source** workloads (local PySpark) and persist **baseline** CSV outputs.
2. Run **exactly one** migrated flavor (Snowpark API *or* SCOS) and compare results against the source baselines.

The generated tests are driven by `dvp/04-results/entrypoints.json` and `dvp/04-results/data_io_schema.json`.

## Inputs

| Input | Required | Location |
|-------|----------|----------|
| Entrypoints inventory | Yes | `dvp/04-results/entrypoints.json` |
| IO schema | Yes | `dvp/04-results/data_io_schema.json` |
| Synthetic data | Yes | `dvp/04-results/synthetic_data/*.csv` |
| Source code (adapted) | Yes | `dvp/01-source/` |
| Migrated code (adapted, selected flavor) | Yes | `dvp/02-migrated/` **or** `dvp/02-migrated_scos/` |

> The DVP workspace folders contain code adapted by `dvp-code-adapter` (session injection + deferred env vars; source also enables Hive support).

## Outputs

| Output | Location | Notes |
|--------|----------|------|
| pytest project | `dvp/03-tests/` | created/overwritten by this skill |
| `pytest.ini` | `dvp/03-tests/pytest.ini` | `testpaths` includes `source` + the selected migrated flavor |
| shared conftest | `dvp/03-tests/conftest.py` | copied from templates |
| source conftest | `dvp/03-tests/source/conftest.py` | copied from templates |
| migrated conftest | `dvp/03-tests/migrated/conftest.py` | copied if `dvp/02-migrated/` exists |
| scos conftest | `dvp/03-tests/migrated_scos/conftest.py` | copied if `dvp/02-migrated_scos/` exists |
| generated tests | `dvp/03-tests/{source|migrated|migrated_scos}/**/test_*.py` | generated from entrypoints |

### `data/` folder (runtime artifact)

`dvp/03-tests/data/expected_output/` is **not** produced by this skill.

It is created when you run the **source** test suite: the source workload outputs are saved as CSV baselines. The selected migrated suite then reads those CSV baselines and compares.

## Workflow

### Step 1: Copy templates

Copy scaffolding from `dvp-test-setup-generator/templates/` into `dvp/03-tests/`:

- Always copy:
  - `conftest.py`, `config.py`, `requirements.txt`
  - `source/conftest.py`
- Copy exactly one migrated flavor based on the workspace:
  - If `dvp/02-migrated/` exists: copy `migrated/conftest.py` and set `pytest.ini` to `testpaths = source migrated`
  - If `dvp/02-migrated_scos/` exists: copy `migrated_scos/conftest.py` and set `pytest.ini` to `testpaths = source migrated_scos`

### Step 2: Read entrypoints

Load `dvp/04-results/entrypoints.json` and select entries where `status != "not_entrypoint"`.

For each entrypoint, preserve subfolder structure and generate one test file in:

- `dvp/03-tests/source/...`
- `dvp/03-tests/migrated/...` **or** `dvp/03-tests/migrated_scos/...`

### Step 3: Generate test files

The generated test files are intentionally small. They:

- import the workload via `import_from(<path>)`
- call a supported entrypoint (`main(spark=...)` preferred; optional fallbacks per template)
- define IO lists (`INPUT_FILES`, `INPUT_TABLES`, `OUTPUT_FILES`, `OUTPUT_TABLES`) derived from `data_io_schema.json`

### Step 4: User config reminder

Remind the user to review `dvp/03-tests/config.py`:

- `CONNECTION_NAME` (matches a configured Snowflake connection)
- `TEST_ROLE`, `TEST_DATABASE`, `TEST_SCHEMA`, `TEST_STAGE`

## Key design notes

### Avoid import-time env var capture

Some workloads read env vars at module import time (e.g., `INPUT_DATA_PATH`). Source tests should import the workload at runtime inside the test method/fixture (not at module scope), and then patch cached module variables if present.

### One migrated flavor per run

Only one migrated suite should be generated and collected per run. The orchestrator asks the user which migrated flavor the SMA `Output/` corresponds to; this skill follows the resulting workspace layout.
