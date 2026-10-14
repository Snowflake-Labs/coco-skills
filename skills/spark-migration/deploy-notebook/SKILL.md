---
name: deploy-notebook
description: |
  Deploy a migrated Snowpark/SCOS project to Snowflake as a Notebook. Takes the
  .ipynb produced by the migration skill (plus its supporting project files),
  reminds the user to fix flagged code, uploads the whole project to a Snowflake
  stage, and creates the Notebook from the stage.
  Triggers: deploy notebook, deploy migrated notebook, create notebook from migration output,
  upload project to stage, deploy scos notebook, deploy snowpark notebook.
parent_skill: spark-migration
---

# Deploy Notebook

> **Bundled sub-skill of `spark-migration`.** Loaded on-demand by the parent
> `spark-migration` skill via the Read tool (see its "Sub-skill Loading
> Convention") — it is **not** a registered top-level skill. Do not call
> `skill("deploy-notebook")`; if you reached this file outside a `spark-migration`
> flow, start at `spark-migration`.

Deploy a migrated project to a Snowflake **Notebook**:

0. **Remind to fix** — surface the migration's flagged issues so the user fixes
   their code before deploying.
0.5. **Verify namespace** — ensure the target database + schema exist (prompt to
   reuse an existing DB or create one).
1. **Upload** — `PUT` the whole migrated project to a Snowflake stage.
2. **Create** — `CREATE NOTEBOOK` from the staged project.
3. **Validate (OPT-IN)** — run the notebook end-to-end. **Only when the user
   explicitly asks to validate/run it.** A plain deploy ends at Step 2.

The core deploy (Steps 0–2) does **no** conversion or parity check — the `.ipynb`
is produced by the migration skill and is a pure input. Step 3's runtime/package
context (`environment.yml`, `snow notebook deploy`, headless execute) is used
**only** on an explicit validate request; it never runs during a normal deploy.

## When to Load

Router dispatches here for deploy intents. The migration skill has already emitted
a Snowflake-notebook `.ipynb` inside the migrated project.

## Arguments (from router context, or asked once when standalone)

| Parameter | Description |
|-----------|-------------|
| Project dir | Migrated project directory (contains the `.ipynb` main file + supporting `.py`/config) |
| Main `.ipynb` | The notebook file to run (relative to the project dir) |
| Database / Schema | Target for the stage + notebook |
| Stage | Stage name (created if absent) |
| Warehouse | `QUERY_WAREHOUSE` for the notebook |
| Notebook name | Object name for the created notebook |
| Connection | Snowflake connection name |

## Prerequisites

- A Snowflake connection (`snow` CLI or `snowflake.connector`; the scripts use the
  shared `sf_exec` executor which works with either).
- `git` is not required.
- **Step 3 (Validate) only**: the `snow` CLI must be available (e.g.
  `uvx --from snowflake-cli snow ...`) — the CLI's `snow notebook deploy` is the
  only path that applies `environment.yml`; SQL `CREATE NOTEBOOK FROM stage` does
  not install packages.

## Scripts

Run via `uv run --project <SKILL_DIRECTORY> python <path>.py` (the `--project`
venv always supplies `snowflake-connector-python`).

**Shared vs notebook-specific.** Steps 0 / 0.5 / 1 use scripts shared with the
other deploy sub-skills; they live in the sibling `deploy-common/scripts/` dir.
For brevity, `<COMMON_SCRIPTS>` = `<SKILL_DIRECTORY>/../deploy-common/scripts`.
Notebook-specific scripts (Steps 2, 3) live in `<SKILL_DIRECTORY>/scripts/`.

| Script | Location | Step | Type |
|--------|----------|------|------|
| `scan_migration_gaps.py` | deploy-common | 0 | deterministic — collects `# SCOS:`/`WARN`/`TODO` markers, `Reports/Issues.csv`, unresolved `<DATABASE>`/`<SCHEMA>`/`<WAREHOUSE>` placeholders, `requirements.txt` |
| `ensure_namespace.py` | deploy-common | 0.5 | deterministic — `SHOW DATABASES`/`SHOW SCHEMAS` existence check; with `--create`, `CREATE DATABASE`/`SCHEMA IF NOT EXISTS` |
| `upload_project.py` | deploy-common | 1 | deterministic — `CREATE STAGE` + recursive `PUT` preserving relative paths |
| `create_notebook.py` | deploy-notebook | 2 | deterministic — `CREATE NOTEBOOK` + set warehouse + add live version + `SHOW NOTEBOOKS` + deeplink |
| `prepare_validation.py` | deploy-notebook | 3 (opt-in) | deterministic — writes `environment.yml` (snowpark-connect + scikit-learn + numpy) + `snowflake.yml` |
| `execute_notebook.py` | deploy-notebook | 3 (opt-in) | deterministic — `EXECUTE NOTEBOOK` headless + PASS/FAIL report |

## Workflow

Prefix each step with a timestamp `[YYYY-MM-DD HH:MM:SS]` (from `date '+%Y-%m-%d %H:%M:%S'`).

### Step 0: Remind to Fix — BLOCKING

Collect the migration's flagged gaps:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <COMMON_SCRIPTS>/scan_migration_gaps.py \
  --project "<PROJECT_DIR>" --json
```

Present a short **fix-before-deploy checklist** from the output: SCOS `TODO`/`WARN`
markers (unsupported ops, external I/O like S3, `.save()` stage paths), `Issues.csv`
rows, unresolved `<DATABASE>`/`<SCHEMA>`/`<WAREHOUSE>` placeholders, and packages that
may need staging. Then **STOP and ask**:

> "These items were flagged during migration. Deploy anyway, or fix first? (Deploy / I'll fix)"

Do not proceed on "I'll fix". If the scan finds nothing, note that and proceed.

### Step 0.5: Verify the target namespace — may prompt

The deploy scripts use fully-qualified `<DATABASE>.<SCHEMA>` names and the `snow`
CLI (Step 3) needs the database to exist, so check first:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <COMMON_SCRIPTS>/ensure_namespace.py \
  --database <DATABASE> --schema <SCHEMA> --connection <CONNECTION> --json
```

- Verdict `PASS` (both exist) → proceed to Step 1.
- Verdict `SKIPPED` (no connection) → report and stop.
- Verdict `MISSING` (exit 2) → **STOP and ask** via `ask_user_question` with two
  options:
  1. **Use an existing database/schema** — user provides a name; restart this
     step with the new `<DATABASE>`/`<SCHEMA>`.
  2. **Create `<DATABASE>.<SCHEMA>` for me** — re-run with `--create`:
     ```bash
     uv run --project <SKILL_DIRECTORY> \
       python <COMMON_SCRIPTS>/ensure_namespace.py \
       --database <DATABASE> --schema <SCHEMA> --connection <CONNECTION> --create --json
     ```
  Do not proceed to Step 1 until the namespace exists.

### Step 1: Upload the migrated project to a stage — BLOCKING

This step makes persistent changes to the account (creates the stage if absent and
`PUT`s every project file). **Ask user** before running it — do not rely on Step 0's
prompt, which only appears when the migration scan flags issues:

> "Ready to create/use stage `@<DATABASE>.<SCHEMA>.<STAGE>` and upload the project? (Yes/No)"

Only on **Yes**, run:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <COMMON_SCRIPTS>/upload_project.py \
  --project "<PROJECT_DIR>" \
  --database <DATABASE> --schema <SCHEMA> --stage <STAGE> \
  --connection <CONNECTION> --json
```

Creates the stage (directory-enabled) and `PUT`s every project file to
`@<DATABASE>.<SCHEMA>.<STAGE>/<project>/<relpath>` so the notebook's imports resolve.
It `LIST`s the stage to confirm the `.ipynb` + modules landed.

**Gate** — check the JSON `verdict`:
- `PASS` → the main `.ipynb` and supporting files appear under the stage path; proceed to Step 2.
- `SKIPPED` (connection unavailable) → report the prepared upload plan and stop.
- `FAIL` → display the `failures` list from the JSON output, report how many files were not
  uploaded (`failed_count`), and **stop before Step 2** (do not create a notebook over an
  incomplete stage). Fix the cause (e.g. permissions, path) and re-run Step 1.

### Step 2: Create the Notebook — BLOCKING

**Ask user**: "Ready to create the notebook on Snowflake? (Yes/No)". If yes:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/create_notebook.py \
  --database <DATABASE> --schema <SCHEMA> --stage <STAGE> \
  --project-name <project> --main-file "<MAIN_IPYNB>" \
  --warehouse <WAREHOUSE> --notebook-name <NOTEBOOK_NAME> \
  --connection <CONNECTION> --json
```

Runs `CREATE OR REPLACE NOTEBOOK … FROM '@<stage>/<project>/' MAIN_FILE='<main.ipynb>'
QUERY_WAREHOUSE=<wh>`, sets the notebook warehouse, adds a LIVE version (so it opens/
runs), self-verifies via `SHOW NOTEBOOKS`, and returns the Snowsight deeplink.

**Gate**: `SHOW NOTEBOOKS` returns the object. Report the deeplink. Done.

### Step 3: Validate by running the notebook — OPT-IN

**Run this step ONLY when the user explicitly asks to validate / run the deployed
notebook.** A normal deploy ends at Step 2 and must not touch `environment.yml`,
`snowflake.yml`, or execute anything.

**SCOS runtime context** (why this step differs from Step 2): a warehouse notebook
running Snowpark Connect needs Python 3.10 and the `snowpark-connect` package,
plus any driver-side deps the workload uses (e.g. `scikit-learn`/`numpy`). These
are declared in `environment.yml`, which is applied **only** by `snow notebook
deploy` — SQL `CREATE NOTEBOOK FROM stage` ignores it. `snow notebook deploy`
provisions a Python-3.10 runtime automatically; if you instead use SQL `CREATE
NOTEBOOK` and need a runtime version string, discover the valid one via
`SHOW PARAMETERS LIKE '%DATAFRAME_PROCESSOR_RUNTIME_ENVIRONMENT_VERSION%'`
(e.g. `3.10-2.0`).

1. **Write the validation artifacts** into the project:
   ```bash
   uv run --project <SKILL_DIRECTORY> \
     python <SKILL_DIRECTORY>/scripts/prepare_validation.py \
     --project "<PROJECT_DIR>" --notebook-file "<MAIN_IPYNB>" \
     --notebook-name <NOTEBOOK_NAME> --query-warehouse <WAREHOUSE> \
     [--extra-packages pkg1,pkg2] --json
   ```
   Add `--extra-packages` for any additional **Anaconda** deps the workload needs
   on the kernel (omit PyPI-only packages like `pygeohash`; those go to UDF
   workers via `snowpark.connect.udf.packages` in code).

2. **Deploy via the CLI** (applies `environment.yml`; pass the database so the
   CLI has a current namespace — see Step 0.5):
   ```bash
   cd "<PROJECT_DIR>" && \
     snow notebook deploy --replace --connection <CONNECTION> \
     --database <DATABASE> --schema <SCHEMA> --warehouse <WAREHOUSE>
   ```

3. **Execute headless and report**:
   ```bash
   uv run --project <SKILL_DIRECTORY> \
     python <SKILL_DIRECTORY>/scripts/execute_notebook.py \
     --notebook <DATABASE>.<SCHEMA>.<NOTEBOOK_NAME> --connection <CONNECTION> --json
   ```
   `PASS` → report success. `FAIL` → surface the error tail. Headless
   `EXECUTE NOTEBOOK` returns only the failing cell's exception; for richer
   diagnostics, instrument the notebook's run cell to write a log to a stage.

**Scope boundary**: failures rooted in SCOS code incompatibilities (import order
of `snowpark_connect` before `pyspark`, `USE SCHEMA` namespace, `udf.imports` for
first-party modules, `sklearn` import relocation, `to_timestamp` format patterns (Java → Snowflake), etc.)
are the **migration skill's** responsibility, not deploy — route those back to
`spark-migration`.

## Stopping Points

- Step 0: after the fix checklist — confirm before deploying.
- Step 0.5: if the namespace is `MISSING` — confirm reuse-vs-create before proceeding.
- Step 1: before creating the stage + uploading files — confirm.
- Step 2: before `CREATE NOTEBOOK` — confirm.
- Step 3 runs only on an explicit validate request.

## Success Criteria

- The whole project is on the stage (`.ipynb` main file + supporting files).
- `CREATE NOTEBOOK` succeeded and `SHOW NOTEBOOKS` returns the object (or `SKIPPED`
  reported when no connection is available).
- Validation (opt-in): `execute_notebook.py` returns `PASS`.

## Final Summary

Timestamp `[YYYY-MM-DD HH:MM:SS]` and report: the created notebook (`db.schema.name`),
the stage path the project was uploaded to, the Snowsight deeplink, and any Step 0
items the user chose to deploy with unresolved.

## Output

```
Snowflake:
  @<db>.<schema>.<stage>/<project>/…   ← uploaded project (ipynb + modules)
  <db>.<schema>.<notebook_name>        ← created NOTEBOOK object
```
