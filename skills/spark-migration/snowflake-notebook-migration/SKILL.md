---
name: snowflake-notebook-migration
description: "Migrates Databricks (DBX) notebooks to Snowflake Workspace notebooks. Preserves original code, only fixes incompatible patterns, flags unsupported features as migration notes. Use when: migrating Databricks notebooks, converting DBX notebooks to Snowflake, fixing Databricks-specific patterns. Triggers: migrate databricks, convert dbx, databricks to snowflake, dbx notebook, migrate notebook, databricks migration."
---

# Databricks Notebook Migration

Migrate Databricks notebooks to Snowflake Workspace notebooks (`.ipynb`). This is a **migration, not a rewrite**. The goal is to preserve as much of the original notebook as possible while only fixing patterns that are incompatible with Snowflake.

This file is the orchestrator: it routes to mode-specific flows and defines what is shared across modes. Concrete rules and per-mode workflows live in `references/`.

## When to Use

- **Standalone mode**: user provides a GitHub URL, local directory, or single Databricks notebook file for migration to Snowflake Workspace format. See `references/standalone-mode.md`.
- **Orchestrator mode**: the `spark-migration` orchestrator invokes this skill with a context message containing pre-filled paths and conversion configuration. See `references/orchestrator-mode.md`.

Do NOT use this skill for creating new Snowflake notebooks from scratch — use `snowflake-notebooks` directly instead.

## Prerequisites

- `uv` must be installed (used to run validation and scanning scripts).
- `git` and `gh` CLI (required only for the GitHub repo workflow in standalone mode).
- `cortex artifact` CLI (required only for Snowflake Workspace upload).

**Before creating the converted notebook, load the `snowflake-notebooks` skill:**

```
skill("snowflake-notebooks")
```

**IMPORTANT**: Use `skill("snowflake-notebooks")` in the foreground — **NEVER** spawn it as a background agent (`run_in_background=true`).

**NEVER** use the Task tool, `run_in_background`, or subagents to perform notebook conversions. All conversions must run **inline, sequentially, in the foreground**. Subagents do not have the skill loaded and will not follow the naming convention, validation loop, or delete-after-convert rules.

This migration skill defines WHAT to change; the `snowflake-notebooks` skill defines HOW to format the output notebook (nbformat 4.5, SQL cell structure with `%%sql -r` + `name` + `resultVariableName` metadata, Jinja templating between cells, and upload workflow with deeplink generation).

## Core Principle: Preserve Everything, Fix Only Incompatible Patterns

Every cell from the original notebook must appear in the converted notebook. For each cell:

1. If the cell has **NO** incompatible patterns → copy it **as-is**. Do not touch it.
2. If the cell has a **mix** of compatible and incompatible lines → only remove or comment out the incompatible lines. Keep all other lines exactly as they are, including variable names, imports, comments, and print statements.
3. If the **ENTIRE** cell is unsupported (e.g., all lines use an unsupported library) → convert to a markdown cell preserving the original code in a fenced code block with a migration note.

**Do NOT:**

- Summarize, rewrite, or rename variables.
- Add cells that are not in the original.
- Add boilerplate (session initialization, imports) that was not in the original.
- Apply vNext best practices to migrated code — this is a migration, not a rewrite. Keep variable names, import paths, and function calls exactly as they are unless they match an incompatible pattern in `references/transformation-rules.md`. Other notebooks may `%run` this file and depend on its exact variable names and session state.

## Incompatible Pattern Rules

All cell-level transformations are defined in `references/transformation-rules.md`. That file is the single source of truth for:

- Magic commands (`%md`, `%sql`, `%scala`, `%r`, `%sh`, `%fs`, `%run`).
- SQL cell referencing (`_sqldf` → `resultVariableName`, SQL-to-SQL Jinja `{{var}}`).
- Databricks APIs (`dbutils.widgets`, `dbutils.notebook`, `dbutils.secrets`, `dbutils.fs`).
- `display()` handling for PySpark / Snowpark / pandas / matplotlib.
- Unsupported APIs (RDD, ML, streaming, external DBs, custom libraries).
- Silently-ignored no-ops (`DataFrame.hint`, `DataFrame.repartition`).

To add a new pattern, append a row to that file. Do NOT add rule prose here.

## File Naming Convention

When creating the converted `.ipynb` file, **append** `.ipynb` to the full original filename instead of replacing the extension. This prevents silent overwrites when two source files share a stem (e.g. `config.py` and `config.ipynb`).

| Original | Output filename |
|---|---|
| `config.py` | `config.py.ipynb` |
| `report.python` | `report.python.ipynb` |
| `etl.scala` | `etl.scala.ipynb` |
| `queries.sql` | `queries.sql.ipynb` |
| `dashboard.ipynb` | `dashboard.ipynb` (unchanged) |

**Rule**: If the source file already ends in `.ipynb`, keep the name as-is. For all other extensions, append `.ipynb` to the full filename (including the original extension).

This convention is deterministic, collision-free, and preserves provenance of the original format. It applies to **both standalone mode and orchestrator mode** — the collision risk exists in both (standalone directories and SMA output in `<output>/Output/` can both contain files that share a stem).

## Mode Detection

Check whether the first message after the skill loads contains the orchestrator context pattern (`The following context was configured by the spark-migration orchestrator:`):

- **Orchestrator mode** (context present) → **Load** `references/orchestrator-mode.md` and follow its workflow. Supports both Snowpark Connect (SCOS) and Snowpark API conversions, converts notebooks in-place, commits via `sma_api.git_commit()`.
- **Standalone mode** (no context, default) → **Load** `references/standalone-mode.md` and follow its workflow. User provides a GitHub URL, local directory, or single file. Targets Snowpark Connect (SCOS) only.

Both modes accept the same input formats (see the File Naming Convention table above) and always produce `.ipynb` as the conversion target. Mode only affects workflow (orchestrator context, git integration, upload offer) — not which files are eligible for conversion.

## PySpark Setup Cell

Applies to **standalone mode** and **orchestrator mode with `scos` conversion type**. In `snowpark_api` orchestrator mode, PySpark has already been converted to Snowpark API by the SMA CLI — skip this section.

When the notebook contains PySpark code, keep the PySpark syntax unchanged. Snowpark Connect provides a PySpark 3.5.3-compatible interface that runs on Snowflake's engine. Do **not** rewrite to Snowpark Python API equivalents (`.with_column()`, `.group_by()`, `.to_df()`, `snowflake.snowpark.functions`, etc.).

### If the notebook uses PySpark

If the notebook contains any `from pyspark...`, `import pyspark`, or `spark.*` calls, add this combined bootstrap + connection cell as the very first code cell. It must run before any PySpark imports — import order matters.

```python
from snowflake import snowpark_connect
from snowflake.snowpark_connect.resources_initializer import wait_for_resource_initialization
from snowflake.snowpark.context import get_active_session

# Initialize Snowpark Connect (must run before any pyspark imports)
spark = snowpark_connect.init_spark_session()
wait_for_resource_initialization()

# TODO: Update database, schema, and warehouse for your environment
_session = get_active_session()
_session.sql("USE DATABASE <DATABASE>").collect()
_session.sql("USE SCHEMA <SCHEMA>").collect()
_session.sql("USE WAREHOUSE <WAREHOUSE>").collect()
```

`snowpark-connect` is pre-installed in Snowflake Workspace notebooks. It vendors PySpark internally — no separate `pip install pyspark` is needed. See [Snowpark Connect in Notebooks](https://docs.snowflake.com/en/developer-guide/snowpark-connect/snowpark-connect-workloads-snowflake-notebook) for details.

### If the notebook does NOT use PySpark

Add a SQL cell as the first cell instead:

```sql
-- Configure the connection (uncomment and update as needed)
-- USE ROLE <ROLE>;
-- USE DATABASE <DATABASE>;
-- USE SCHEMA <SCHEMA>;
-- USE WAREHOUSE <WAREHOUSE>;
```

## Migration Note Format

When a rule's action is "convert to markdown migration note" (see `references/transformation-rules.md`), use this template:

````markdown
> **Migration Note**: [Brief description of the issue]
> [Explanation of why it's incompatible]
>
> **Owner action required**: [What the owner needs to decide/do]

Original code:
```[language]
[original code preserved exactly]
```
````

## Success Criteria

After conversion, verify (use `validate_notebook.py` per notebook and `validate_directory.py` for the full directory):

- Output is valid `.ipynb` (parseable JSON, nbformat 4.5).
- Every cell from the original appears in the converted notebook (unchanged, modified, or as a migration note).
- No remaining `_sqldf` references — all replaced with a named `resultVariableName`.
- All `%run` paths end in `.ipynb`.
- SQL cells have `resultVariableName` metadata.
- A Migration Summary cell is present at the end.
- Output filenames preserve the original extension for non-`.ipynb` sources (e.g. `config.py` → `config.py.ipynb`, not `config.ipynb`).

## Validation Feedback Loop

After converting each notebook, run validation. If errors are found, fix and re-validate (maximum 3 attempts):

1. **Convert** the notebook following the mode-specific workflow.
2. **Validate**: `uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/validate_notebook.py <output.ipynb>`
3. **If errors**: fix the reported issues in the converted notebook.
4. **Re-validate**: run step 2 again.
5. **Proceed** when validation passes (exit code 0).

If validation still fails after 3 attempts, log the remaining errors in the migration summary cell and continue to the next notebook. Do not block the entire migration on a single notebook.

After all notebooks are converted, run `validate_directory.py` on the output directory to verify no stale originals remain and all expected `.ipynb` files exist.

## Troubleshooting

**Notebook file can't be parsed (corrupt JSON or unexpected format)**
- Skip the file, log it in the migration summary as "skipped — parse error", continue with remaining notebooks.

**`%run` target doesn't exist at the expected path**
- Add a migration note in the cell flagging the missing target. Do not fail the conversion.

**`cortex artifact create notebook` fails**
- Report the error to the user. The converted `.ipynb` file is still valid locally.

**`sma_api.git_commit()` fails (orchestrator mode)**
- Report the error. The converted files are already written to disk — the user can commit manually.

## Output

- Converted `.ipynb` file(s) in Snowflake Workspace notebook format (nbformat 4.5).
- Migration summary markdown cell at the end of each converted notebook.
- Combined migration summary reported to the user.
- (Standalone mode, optional) PR via `gh pr create` with converted notebooks — see `references/standalone-mode.md`.
- (Standalone mode, optional) Notebooks uploaded to Snowflake Workspace via `cortex artifact create notebook` — see `references/standalone-mode.md`.
- (Orchestrator mode) Git commit via `sma_api.git_commit()` on the `sma/migration-process` branch — see `references/orchestrator-mode.md`.

## Scripts

Four Python scripts under `scripts/` handle parsing, dependency scanning, and validation. See `references/tools.md` for usage, arguments, and when-to-use guidance:

- `detect_and_parse_notebook.py` — format-agnostic notebook parser.
- `scan_dependencies.py` — `%run` and Python import dependency graph.
- `validate_notebook.py` — per-notebook validation (supports `--finalize`).
- `validate_directory.py` — post-conversion directory check.
