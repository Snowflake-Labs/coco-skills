---
name: snowpark-api
description: |
  Snowpark Python API conversion path via the SMA CLI. Bundled sub-skill of `spark-migration`, invoked by the parent ONLY when the user explicitly asks for an SMA / Snowpark API rewrite (`snowflake.snowpark`, snake_case `with_column` / `group_by` / `session.create_dataframe`). Owns: SMA CLI detection, conversion, EWI dashboard, notebook migration, EWI fixer, stage conversion, DVP orchestration, and the API-side final summary.
  Use when: user explicitly asked for SMA / SMA CLI / Snowpark API / `snowflake.snowpark` rewrite, OR user wants to operate on an already-converted SMA output (open dashboard, fix EWIs, run stage conversion, resume DVP).
  Triggers: snowpark api, sma cli, sma conversion, run sma, snowflake.snowpark rewrite, snake_case snowpark, fix ewis, ewi fixer, sma dashboard, stage conversion, resume dvp, dvp orchestrator, already migrated sma.
---

# Snowpark API (SMA CLI) Migration

Sub-skill for the **Snowpark Python API** conversion path of `spark-migration`. The SMA CLI converts PySpark to `snowflake.snowpark` (snake_case `with_column`, `group_by`, `session.create_dataframe`).

> **Bundled sub-skill of `spark-migration`.** This SKILL.md is loaded
> on-demand by the parent `spark-migration` skill via the Read tool
> (see its "Sub-skill Loading Convention" section) — it is **not**
> registered as a standalone top-level skill in the Cortex Code skill
> registry, by design, to avoid trigger collisions with its parent.
> Do not call `skill("snowpark-api")`; if you reached this file
> outside of a `spark-migration` flow, start at `spark-migration` instead.

## Default-Path Reminder

⛔ **The default `spark-migration` path is Snowpark Connect (SCOS)** via the sibling `snowpark-connect/` sub-skill, which preserves the PySpark API surface (`withColumn`, `groupBy`, `spark.createDataFrame`). This `snowpark-api/` sub-skill is the **opt-in** alternative, selected only when the user explicitly asks for SMA / Snowpark API.

Do **NOT** route here if the user said "convert spark", "migrate pyspark", or anything that does not name SMA / Snowpark API explicitly. Those go to `snowpark-connect/`.

## When to Use

- User explicitly mentions "SMA", "SMA CLI", "Snowpark API", or "`snowflake.snowpark` rewrite"
- Parent `spark-migration` skill has set `<config.conversion_type> = "snowpark-api"` (or any legacy alias normalized to it: `snowpark_api`)
- User wants to operate on an existing SMA output (already-migrated flow) — open the EWI dashboard, run the EWI fixer, perform stage conversion, or resume DVP

## Inputs (from parent orchestrator)

The parent passes the following inline as the next turn's context when delegating to this sub-skill:

| Variable | Source | Description |
|---|---|---|
| `<input>` | `<config.input_folder>` | PySpark source directory or file |
| `<output>` | `<config.output_folder>` | Target / existing SMA output directory |
| `<email>` | `<config.email>` | Customer email (passed to SMA CLI) |
| `<company>` | `<config.company>` | Customer company (passed to SMA CLI) |
| `<project>` | `<config.project_name>` | Project name (passed to SMA CLI) |
| `<config_path>` | `<spark_migration_root>/configurations/<project>.json` | Project config for persisting SMA-only choices |
| `<global_config>` | Loaded from `<spark_migration_root>/config.json` | Provides `sma_cli_path` if already detected |
| `<start_time>` | Recorded at parent Step 1 | Used for the Final Summary duration |
| `<intent>` | Parent decides | `migrate` (fresh SMA conversion) or `already_migrated` (operate on existing output) |

Sub-skill behavior also depends on `<config.*>` keys (`enable_jupyter_conversion`, `sql_flavor`, `generate_checkpoints`, `run_ewi_fixer`, `run_ewi_fixer.ewi_comments`, `run_ewi_fixer.ewi_scope`, `run_stage_conversion`, `run_stage_conversion.stage_name`, `run_dvp_orchestrator`). Use `config_manager.py view-section snowpark_api` to fetch just this slice.

## Path Resolution

`<spark_migration_root>` resolves to the grandparent of every `snowpark-api/<child>/SKILL.md` — the `spark-migration/` directory that contains the shared `scripts/config_manager.py` and `snowflake-notebook-migration/` skill.

`<snowpark_api_root>` resolves to **this** sub-skill's directory — `spark-migration/snowpark-api/`. The child skills (`migrate-pyspark-to-snowpark-api/`, `validate-pyspark-to-snowpark-api/`, `sma-dashboard-generator/`, `stage-conversion/`, `dvp/`) live underneath it.

```
spark-migration/                                  ← <spark_migration_root>
├── SKILL.md                                      (parent, thin router)
├── scripts/config_manager.py                     (shared)
├── snowflake-notebook-migration/                 (shared)
└── snowpark-api/                                 ← <snowpark_api_root>
    ├── SKILL.md                                  (this file)
    ├── scripts/sma_api.py                        (SMA SQLite/git/EWI helpers)
    ├── references/                               (extracted long-form docs)
    ├── migrate-pyspark-to-snowpark-api/SKILL.md
    ├── validate-pyspark-to-snowpark-api/SKILL.md
    ├── sma-dashboard-generator/SKILL.md
    ├── stage-conversion/SKILL.md
    └── dvp/dvp-*/SKILL.md  (orchestrator + 9 children)
```

## Bundled Sub-skill Loading Convention

This sub-skill loads its children by **reading their `SKILL.md` files** with the Read tool, NOT by registry lookup. Children load each other the same way.

| Intent / role | Bundled path (Read with Read tool, follow inline) |
|---|---|
| Fresh SMA conversion | `<snowpark_api_root>/migrate-pyspark-to-snowpark-api/SKILL.md` |
| Post-conversion tail / already-migrated entry | `<snowpark_api_root>/validate-pyspark-to-snowpark-api/SKILL.md` |
| EWI dashboard generator | `<snowpark_api_root>/sma-dashboard-generator/SKILL.md` |
| Stage path replacement | `<snowpark_api_root>/stage-conversion/SKILL.md` |
| DVP pipeline | `<snowpark_api_root>/dvp/dvp-orchestrator/SKILL.md` |
| Notebook migration (shared, at parent root) | `<spark_migration_root>/snowflake-notebook-migration/SKILL.md` |

If any file is missing, **STOP** and report:
> The bundled `<name>` sub-skill is missing at `<expected_path>`.
> Reinstall the `spark-migration` skill.

## Intent Detection

```
Start (parent has delegated to this sub-skill)
  ↓
Detect Language
  ├─→ Python (.py, PySpark, Databricks, pyspark)
  │     ├─→ <intent>=migrate          → load migrate-pyspark-to-snowpark-api/SKILL.md
  │     └─→ <intent>=already_migrated → load validate-pyspark-to-snowpark-api/SKILL.md
  │
  ├─→ Scala (.scala, Spark Scala, build.sbt)
  │     → Not yet supported via this sub-skill.
  │       Ask the user whether they want to switch to `snowpark-connect`,
  │       which does support Scala.
  │
  └─→ Ambiguous → Ask the user which language the workload uses
```

### Step 1: Detect Language

Determine source language from:
- **Explicit mention**: "PySpark", "Python Spark"
- **File extensions**: `.py` → Python
- **Import patterns**: `from pyspark` / `import pyspark` → Python
- **Build files**: `requirements.txt` / `pyproject.toml` → Python

If the language cannot be determined, ask the user. If the user says Scala, inform them that Snowpark API conversion for Scala is not yet supported via this sub-skill and ask whether they want to switch to `snowpark-connect` (SCOS) instead.

### Step 2: Route by Intent

| `<intent>` | Bundled SKILL.md to load |
|---|---|
| `migrate` | `<snowpark_api_root>/migrate-pyspark-to-snowpark-api/SKILL.md` |
| `already_migrated` | `<snowpark_api_root>/validate-pyspark-to-snowpark-api/SKILL.md` |

Read the chosen file with the Read tool and follow its instructions inline. Pass the full inputs block from the parent's context unchanged.

The migrate sub-skill owns SMA CLI detection + run + output resolution, then **delegates to the validator** to run the post-conversion tail (dashboard → notebook → EWI → stage → DVP → final summary). The validator is also the direct entry point for `already_migrated` flows and for individual API-side intents ("open dashboard", "fix ewis", "run stage conversion", "resume DVP").

Control does NOT return to the parent until the entire pipeline completes (or the user explicitly aborts).

## Config

This sub-skill reads and writes only the `snowpark_api` namespace via:

```bash
python3 '<spark_migration_root>/scripts/config_manager.py' view-section '<config_path>' snowpark_api
```

That returns the SMA-only project keys (`sql_flavor`, `enable_jupyter_conversion`, `generate_checkpoints`, `run_ewi_fixer`, `run_ewi_fixer.ewi_comments`, `run_ewi_fixer.ewi_scope`, `run_stage_conversion`, `run_stage_conversion.stage_name`, `run_dvp_orchestrator`).

The global `sma_cli_path` is read from `<spark_migration_root>/config.json` via `config_manager.py load-global`.

Schema reference: [`references/configuration-schema.md`](references/configuration-schema.md).

## Stopping Points

None at this routing level — stopping points are defined inside `migrate-pyspark-to-snowpark-api/SKILL.md` and `validate-pyspark-to-snowpark-api/SKILL.md`.

## Output

The migrate sub-skill produces an SMA `Conversion-*` (v1) or `sma-output/` (v2) or `Conversion_SnowparkAPI/sma-code-process-*` (v3) folder containing:

- `Output/` — Snowpark Python (snake_case) converted code
- `Reports/Issues.csv`, `Reports/InputFilesInventory.csv`, `Reports/ArtifactDependencyInventory.csv`
- `sma-dashboard/` — interactive EWI tracking dashboard (after validator runs the dashboard generator)
- `dvp/` — validation pipeline workspace (after validator runs the DVP orchestrator)

Plus per-step optional artifacts: EWI fixes, stage-rewritten paths, generated test suites. See `references/output-layouts.md` for the full layout and v1/v2/v3 path resolution rules.

The final summary template lives at [`references/final-summary-template.md`](references/final-summary-template.md) and is rendered by the validator at the end of the pipeline.
