# Snowpark API Post-Conversion Options

This reference enumerates every post-conversion step driven by `validate-pyspark-to-snowpark-api/SKILL.md`, the keys that gate it, and the bundled sub-skill it loads.

| # | Step | Gate key | Default | Sub-skill loaded |
|---|---|---|---|---|
| 1 | Initialize git + verify SMA output | (always run) | — | (inline in validator) |
| 2 | Generate EWI dashboard | (always run) | — | `<snowpark_api_root>/sma-dashboard-generator/SKILL.md` |
| 3 | Notebook migration | `run_notebook_migration` | `yes` | `<spark_migration_root>/snowflake-notebook-migration/SKILL.md` |
| 4 | EWI fixer | `run_ewi_fixer` | `yes` | `<snowpark_api_root>/dvp/dvp-ewi-fixer/SKILL.md` |
| 5 | Stage conversion | `run_stage_conversion` | `yes` | `<snowpark_api_root>/stage-conversion/SKILL.md` |
| 6 | DVP orchestrator (steps 1–13) | `run_dvp_orchestrator` | `yes` | `<snowpark_api_root>/dvp/dvp-orchestrator/SKILL.md` |
| 7 | Open dashboard + final summary | (always run) | — | (inline in validator) |

## Per-Step Gates

For each gated step, the validator:

1. Reads the key from the project config (`view-section snowpark_api`)
2. If `"no"` → log "Skipping `<step>` (configured as disabled). You can run it later using the `<sub-skill-name>` skill." and continue to the next step
3. If `"yes"` → load the bundled SKILL.md and follow it inline, passing the orchestrator context (see [example-workflows.md](example-workflows.md))
4. If unset → ask the user via `ask_user_question`, then persist the answer to the project config via `config_manager.py save`

## EWI Fixer Sub-keys

When `run_ewi_fixer = yes`, the validator passes two sub-keys to the fixer:

| Key | Values | Default | Meaning |
|---|---|---|---|
| `run_ewi_fixer.ewi_comments` | `mark`, `remove` | `mark` | `mark` keeps EWI comments with `[FIXED]`/`[NOT-FIXED]` prefix; `remove` deletes them after fix |
| `run_ewi_fixer.ewi_scope` | `only_pending`, `retry_not_resolved`, `all_reset` | `only_pending` | Which EWIs to process |

A fourth option, **"Specific EWI code"**, is available when asking the user interactively. Selecting it captures an `ewi_specific_code` (e.g., `SPRKPY1002`) which is passed inline to the fixer for that run; it is NOT persisted to the project config.

## Stage Conversion Sub-keys

When `run_stage_conversion = yes`, the validator passes:

| Key | Default | Meaning |
|---|---|---|
| `run_stage_conversion.stage_name` | `migration_stage` | Prefix used as the Snowflake stage name when replacing embedded paths |

The sub-skill receives this as its `--prefix` value and skips its own Step 3 question.

## DVP Orchestrator Context

When `run_dvp_orchestrator = yes`, the validator passes the following block to skip detection inside the orchestrator:

```
The following context was configured by the spark-migration orchestrator:
- SMA input directory (PySpark source): <input>
- SMA output directory: <output>
  (Already resolved to the Conversion-* / sma-output/ / sma-code-process-* folder.)
- Conversion type: snowpark-api

Skip Step 1 (Detect SMA Paths) — the paths are already known.
Skip the flavor question in Step 4 — conversion_type is snowpark-api, so the
migrated folder is dvp/02-migrated/.
Proceed directly to Step 2 (Validate SMA Structure).
Execute ALL steps through Step 13 (dvp-test-setup-generator). Do NOT stop early.
```

The orchestrator's `<dvp_root>` (the directory containing its SKILL.md's parent) is `<snowpark_api_root>/dvp/`. All `dvp-*` sub-skills it loads are bundled under that root.

## Skip Messages

When a step is skipped (config `no` or user opted out), use this exact wording so logs are greppable:

| Step | Skip line |
|---|---|
| Notebook migration | `Skipping Notebook Migration (configured as disabled). You can run it later using the snowflake-notebook-migration skill.` |
| EWI fixer | `Skipping EWI Fixer (configured as disabled). You can run it later using the dvp-ewi-fixer skill.` |
| Stage conversion | `Skipping Stage Conversion (configured as disabled). You can run it later using the stage-conversion skill.` |
| DVP orchestrator | `Skipping DVP initialization (configured as disabled). You can run it later using the dvp-orchestrator skill.` |
