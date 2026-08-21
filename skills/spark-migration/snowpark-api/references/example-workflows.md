# Example Workflows (Snowpark API path)

Concrete conversational walkthroughs for the three common entry points.

## Example 1 — Fresh SMA Conversion

User asks: *"convert this pyspark project with SMA"*

```
spark-migration parent: [Step 1–5 collect config; conversion_type = snowpark-api,
                         migration_status = migrate]
spark-migration parent: → loads snowpark-api/SKILL.md (the router)

snowpark-api/SKILL.md: Detect Language → Python, <intent> = migrate
                      → loads snowpark-api/migrate-pyspark-to-snowpark-api/SKILL.md

migrate-pyspark-to-snowpark-api:
  Step M1: Validate SMA CLI path (from global config)
  Step M2: Collect CLI-specific fields (sql_flavor, jupyter, checkpoints)
  Step M3: Run SMA CLI in background
  Step M4: Poll progress (every 5–10s via bash_output)
  Step M5: Detect SMA output layout (v1/v2/v3), resolve <output>
  Step M6: Hand off to validate-pyspark-to-snowpark-api/SKILL.md inline

validate-pyspark-to-snowpark-api:
  Step V1: Initialize git, verify <output>/Output/ + <output>/Reports/
  Step V2: Run sma-dashboard-generator (bundled)
  Step V3: Run snowflake-notebook-migration (if run_notebook_migration=yes)
  Step V4: Run dvp-ewi-fixer (if run_ewi_fixer=yes)
  Step V5: Run stage-conversion (if run_stage_conversion=yes)
  Step V6: Run dvp-orchestrator (if run_dvp_orchestrator=yes) — steps 1–13
  Step V7: Re-open dashboard
  Step V8: Print Final Summary
```

## Example 2 — Already Migrated

User asks: *"I already ran SMA — set up the dashboard and run the EWI fixer"*

```
spark-migration parent: [Step 1–4: conversion_type = snowpark-api,
                         migration_status = already_migrated;
                         Step 4 validates <output>, resolves SMA layout]
spark-migration parent: → loads snowpark-api/SKILL.md (the router)

snowpark-api/SKILL.md: Detect Language, <intent> = already_migrated
                      → loads snowpark-api/validate-pyspark-to-snowpark-api/SKILL.md
                        (skipping the migrate sub-skill entirely)

validate-pyspark-to-snowpark-api: runs V1–V8 as above.
```

## Example 3 — Direct Intent: "fix ewis"

User asks: *"fix the ewis in /Users/me/proj/converted"* — no prior config flow.

`spark-migration`'s router catches the `"fix ewis"` trigger and routes here:

```
spark-migration parent: [Step 0 ensures SMA CLI not needed; Step 1 finds or
                         creates a config; sets conversion_type=snowpark-api,
                         migration_status=already_migrated]
spark-migration parent: → loads snowpark-api/SKILL.md

snowpark-api/SKILL.md: <intent> = already_migrated
                      → loads snowpark-api/validate-pyspark-to-snowpark-api/SKILL.md

validate-pyspark-to-snowpark-api:
  Step V1: validate <output>; init git if needed (skips if branch exists)
  Step V2: ensure sma-dashboard exists (re-run dashboard generator if missing)
  Step V3: skip (run_notebook_migration unchanged)
  Step V4: run dvp-ewi-fixer with user-provided scope (default only_pending)
  Step V5–V7: skip unless user asks
  Step V8: Print Final Summary (showing only the steps that ran).
```

The same flow applies to **"open sma dashboard"**, **"run stage conversion"**, **"resume DVP"** — the validator is the universal entry point for individual API-side operations.

## Example 4 — Scala Workload (Not Supported via API Path)

User asks: *"convert this Spark Scala project using SMA"*

```
spark-migration parent: routes to snowpark-api/SKILL.md (user asked for SMA)

snowpark-api/SKILL.md: Detect Language → Scala
                       → Inform user: "Snowpark API conversion for Scala is
                         not yet supported via this sub-skill. Would you like
                         to switch to Snowpark Connect (SCOS) instead, which
                         does support Scala?"
                       → If yes: parent re-routes to snowpark-connect/SKILL.md
                       → If no: stop.
```
