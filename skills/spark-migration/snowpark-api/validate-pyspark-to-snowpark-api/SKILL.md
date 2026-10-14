---
name: validate-pyspark-to-snowpark-api
description: |
  Post-conversion pipeline owner for the Snowpark API path. Bundled sub-skill of `snowpark-api`. Loaded by `migrate-pyspark-to-snowpark-api` after a fresh SMA run, AND directly by `snowpark-api/SKILL.md` when the user wants to operate on an already-migrated SMA output. Owns: git init, EWI dashboard, notebook migration, EWI fixer, stage conversion, DVP orchestrator (steps 1–13), dashboard re-open, and the final summary. Also the entry point for individual API-side operations.
  Triggers (when invoked directly): already migrated, fix ewis, open sma dashboard, run stage conversion, resume dvp, dvp orchestrator, ewi fixer.
---

# Validate PySpark → Snowpark API (Post-Conversion Pipeline)

> **Bundled sub-skill** under `snowpark-api/`. This file is loaded via the
> Read tool by either `snowpark-api/SKILL.md` (router) or
> `snowpark-api/migrate-pyspark-to-snowpark-api/SKILL.md` (after SMA finishes).
> Not registered as a standalone skill.

Owns **the entire post-conversion tail** for the Snowpark API path:

1. Initialize git and verify SMA output
2. Generate / re-open the EWI dashboard
3. Run Notebook Migration (optional)
4. Run the EWI Fixer (optional)
5. Run Stage Conversion (optional)
6. Run the DVP Orchestrator (steps 1–13) (optional)
7. Re-open the dashboard
8. Print the Final Summary

This sub-skill is also the **direct entry point** for `<intent> = already_migrated` and for individual API-side intents ("fix ewis", "open sma dashboard", "run stage conversion", "resume dvp").

## Inputs (passed inline)

| Variable | Required | Source |
|---|---|---|
| `<intent>` | Yes | `migrate` (handed off by migrate sub-skill) or `already_migrated` (direct from router) |
| `<input>` | Yes (if `<intent>=migrate`) / Optional (already_migrated) | `<config.input_folder>` — passed to DVP orchestrator |
| `<output>` | Yes | Resolved SMA workload root containing `Output/` and `Reports/` |
| `<email>`, `<company>`, `<project>` | Yes | From config |
| `<config_path>` | Yes | `<spark_migration_root>/configurations/<project>.json` |
| `<spark_migration_root>` | Yes | grandparent of this SKILL.md |
| `<snowpark_api_root>` | Yes | parent of this SKILL.md |
| `<start_time>` | Yes | recorded by the parent at its Step 1 |

## Reference Material

- [`../references/post-conversion-options.md`](../references/post-conversion-options.md) — step-by-step gate/skip semantics
- [`../references/final-summary-template.md`](../references/final-summary-template.md) — verbatim template for the Final Summary
- [`../references/sma-api-reference.md`](../references/sma-api-reference.md) — function reference for `sma_api.py`
- [`../references/configuration-schema.md`](../references/configuration-schema.md) — config keys this sub-skill reads/writes
- [`../references/output-layouts.md`](../references/output-layouts.md) — used when `<intent>=already_migrated`
- [`../references/example-workflows.md`](../references/example-workflows.md) — concrete walkthroughs

## Step V0: Resolve `<output>` (already_migrated only)

If `<intent> = migrate`, skip to V1 — `<output>` is already resolved by the migrate sub-skill.

If `<intent> = already_migrated`:

1. If `<output>` was passed in but is not yet at the workload root, apply the resolution rules from [`../references/output-layouts.md`](../references/output-layouts.md) (v1/v2/v3 in order). Update `<output>` to the resolved path.
2. If `<output>` was NOT provided, ask the user (pre-filled with `<config.output_folder>` as default).
3. Verify:
   ```bash
   test -d "<output>/Output" && test -d "<output>/Reports" && \
       test -f "<output>/Reports/Issues.csv"
   ```
   If any check fails, ask the user for the correct path. Do not proceed.

## Step V1: Initialize Git and Verify SMA Output

```bash
cd "<output>"
git rev-parse --is-inside-work-tree 2>/dev/null
```

**If NOT a git repository:**
```bash
git init
git add .
git commit -m "Initial commit: SMA output before migration process"
git branch -M main
```

⛔ `git branch -M main` is **MANDATORY** — the unmodified SMA output must live on `main`.

**If already a git repository:**
- `git status --porcelain` — if clean, skip to branch creation
- If uncommitted changes, ask the user via `ask_user_question`:
  - **Stash changes:** `git stash push -m "Pre-migration stash"` (recover later with `git stash pop`)
  - **Commit changes:** stage + commit pending changes before proceeding
  - **Abort:** stop and let the user resolve manually

**Create / checkout the working branch** (idempotent via `sma_api.git_ensure_branch`):

```python
sma_api.git_ensure_branch("<output>", "sma/migration-process")
```

All subsequent file modifications happen on `sma/migration-process`.

**Verify output structure** (one final check):

```bash
test -d "<output>/Output" && test -f "<output>/Reports/Issues.csv"
```

## Step V2: Generate (or Re-Generate) the EWI Dashboard

```bash
cd "<output>"
```

Load the bundled dashboard generator:

```bash
SMA_DASHBOARD_SKILL="<snowpark_api_root>/sma-dashboard-generator/SKILL.md"
```

Read it with the Read tool and follow its instructions in the current `<output>` working directory. The dashboard generator will:

- Parse `Reports/Issues.csv`
- Build/refresh `sma_storage.sqlite3`
- Generate an interactive EWI tracking dashboard at `<output>/sma-dashboard/`
- Start a local server and open it in the browser

⛔ Do NOT call `skill("sma-dashboard-generator")` — bundled sub-skill, not registered.

For "open sma dashboard" intent: if `<output>/sma-dashboard/` already exists and is recent, you may skip generation and jump straight to V7 (re-open). Otherwise run the generator.

## Step V3: Run Notebook Migration (Optional)

Fetch the API-namespaced config slice once and refer to its keys below:

```bash
python3 '<spark_migration_root>/scripts/config_manager.py' view-section '<config_path>' snowpark_api
```

Also load `run_notebook_migration` from the shared namespace.

Decision table for `run_notebook_migration`:

| Value | Action |
|---|---|
| `no` | Log skip line (see [`../references/post-conversion-options.md`](../references/post-conversion-options.md#skip-messages)). Go to V4. |
| `yes` | Proceed directly to the invocation step below (do NOT scan/ask). |
| (unset) | Scan first, then ask. |

**Scan-first path (unset):** use the detection script from `snowflake-notebook-migration`:

```bash
uv run --project "<spark_migration_root>/snowflake-notebook-migration" \
    python "<spark_migration_root>/snowflake-notebook-migration/scripts/detect_and_parse_notebook.py" \
    --scan "<output>/Output/"
```

- Empty array → silently skip (no prompt). Go to V4.
- Non-empty → `ask_user_question`:
  - **Yes, run Notebook Migration**
  - **No, skip for now** — persist `{"run_notebook_migration": "no"}` then go to V4.

**Invocation** (yes path):

```bash
NB_MIGRATION_SKILL="<spark_migration_root>/snowflake-notebook-migration/SKILL.md"
```

Read with the Read tool and follow inline (foreground only — never `run_in_background`).
Pass this context block:

```
The following context was configured by the spark-migration orchestrator:
- SMA output directory: <output>
- Notebooks source: <output>/Output/
- Git branch: sma/migration-process (already checked out)
- Conversion type: snowpark-api
- Jupyter conversion: <config.enable_jupyter_conversion>

Scan <output>/Output/ for notebook files (.ipynb, .python, Databricks .py,
.scala, .sql) and convert in-place.
Use sma_api.git_commit() for git operations on the sma/migration-process branch.
```

Record the number of notebooks converted for the Final Summary.

## Step V4: Run the EWI Fixer (Optional)

Decision table for `run_ewi_fixer`:

| Value | Action |
|---|---|
| `no` | Log skip line; go to V5. |
| `yes` | Use saved sub-keys (`run_ewi_fixer.ewi_comments`, `run_ewi_fixer.ewi_scope`); proceed to invocation. |
| (unset) | `ask_user_question` "Run EWI Fixer?" — if no, log skip and go to V5; if yes, ask the two sub-questions (comments mode + scope) and persist. |

**Sub-question A (comments):** `ask_user_question` — `mark` (keep with `[FIXED]`/`[NOT-FIXED]` prefix) or `remove` (delete after fix).

**Sub-question B (scope):** `only_pending` / `retry_not_resolved` / `Specific EWI code` / `all_reset`. If "Specific EWI code", also ask for the code (e.g., `SPRKPY1002`) and store as `<ewi_specific_code>` (not persisted).

**Persist** (only the saved keys, not `<ewi_specific_code>`):

```bash
python3 '<spark_migration_root>/scripts/config_manager.py' save '<config_path>' \
    '{"run_ewi_fixer": "yes", "run_ewi_fixer.ewi_comments": "<comments>", "run_ewi_fixer.ewi_scope": "<scope>"}'
```

**Invocation:**

```bash
EWI_FIXER_SKILL="<snowpark_api_root>/dvp/dvp-ewi-fixer/SKILL.md"
```

Read with the Read tool and follow inline (foreground only). Context block:

```
The following options were already configured by the user:
- SMA output directory: <output>
- EWI comment handling: <comments>
- EWIs to process: <scope>
[- Specific EWI code: <ewi_specific_code>]   (only if applicable)

Skip Step 1 questions and proceed directly to Step 2 with these settings.
```

The fixer will use the SQLite DB built by V2, scan converted files, resolve EWI comments, and update the DB.

## Step V5: Run Stage Conversion (Optional)

Decision table for `run_stage_conversion`:

| Value | Action |
|---|---|
| `no` | Log skip line; go to V6. |
| `yes` | Use saved `run_stage_conversion.stage_name`; proceed to invocation. |
| (unset) | Ask the user; on no, persist `"no"`; on yes, proceed (stage_name defaults to `migration_stage` unless asked). |

**Invocation:**

```bash
STAGE_CONV_SKILL="<snowpark_api_root>/stage-conversion/SKILL.md"
```

Read with the Read tool and follow inline (foreground only). Context block:

```
The following context was configured by the spark-migration orchestrator:
- SMA output directory: <output>
- Git branch: sma/migration-process (already checked out by ewi-fixer via sma_api.git_ensure_branch)
- Target files directory: <output>/Output/
- Stage prefix: <config.run_stage_conversion.stage_name> (use as the --prefix value)

Skip Step 6 (git check) — repo and branch are already set up via sma_api git functions.
Work directly on the sma/migration-process branch.
Use the provided stage prefix instead of asking the user in Step 3.
```

## Step V6: Run DVP Orchestrator (Optional)

Read `run_dvp_orchestrator` from the project config (default `yes`). Do NOT ask the user — the value is already saved.

| Value | Action |
|---|---|
| `no` | Log skip line; go to V7. |
| `yes` | Proceed to invocation. |

**Invocation:**

```bash
DVP_ORCH_SKILL="<snowpark_api_root>/dvp/dvp-orchestrator/SKILL.md"
```

Read with the Read tool and follow inline (foreground only). Context block:

```
The following context was configured by the spark-migration orchestrator:
- SMA input directory (PySpark source): <input>
- SMA output directory: <output>   (resolved Conversion-*/sma-output/sma-code-process-* path)
- Conversion type: snowpark-api

Skip Step 1 (Detect SMA Paths) — the paths are already known.
Skip the flavor question in Step 4 — conversion_type is snowpark-api, so the
migrated folder is dvp/02-migrated/.
Proceed directly to Step 2 (Validate SMA Structure).
Execute ALL steps through Step 13 (dvp-test-setup-generator). Do NOT stop early.
```

The orchestrator may load further bundled `dvp-*` sub-skills from `<snowpark_api_root>/dvp/` — none are registered top-level skills.

## Step V7: Re-Open the Dashboard

```bash
cd "<output>/sma-dashboard" && python3 start_server.py
```

`start_server.py` checks if the server is running, finds an available port, starts the server if needed, and opens the user's default browser. Just run it.

## Step V8: Print the Final Summary

⛔ Use the EXACT template at [`../references/final-summary-template.md`](../references/final-summary-template.md).

Before printing, run the pre-display checklist in that reference. Replace ALL placeholders (`N`, `M`, `<output>`, `<project_name>`, `<start_time>`, `<end_time>`, `<duration>`, `<port>`) with real values.

Compute `<duration>` = current time − `<start_time>`. Use `sma_api.git_verify_branches("<output>")` to confirm both `main` and `sma/migration-process` exist before listing them.

If `<intent> = already_migrated`, use the **already_migrated variant** at the bottom of `final-summary-template.md` (row 1 changes; rows 2–7 are unchanged).

If a step was skipped, show only its parent row with `Skipped` and omit sub-steps. If a step failed, show `Failed` with a brief error message and continue the summary.

## Stopping Points

| Condition | Action |
|---|---|
| `<output>` cannot be resolved or `Issues.csv` missing | Stop in V0/V1; ask user for correct path |
| Git working directory not clean and user picks "Abort" | Stop; instruct user to resolve manually |
| `sma-dashboard-generator` fails | Stop; report error; do not run further steps until resolved |
| Any bundled sub-skill is missing at its expected path | Stop and report: "The bundled `<name>` sub-skill is missing at `<path>`. Reinstall the `spark-migration` skill." |

## Direct-Intent Behavior

When invoked directly via the router for an individual operation:

| User intent | Steps run | Steps skipped |
|---|---|---|
| `open sma dashboard` | V0, V1 (idempotent), V2 (only if missing/stale), V7, V8 | V3–V6 |
| `fix ewis` | V0, V1, V2 (only if missing), V4 (default scope=only_pending), V7, V8 | V3, V5, V6 |
| `run stage conversion` | V0, V1, V5, V7, V8 | V2 (if recent), V3, V4, V6 |
| `resume dvp` | V0, V1, V6, V7, V8 | V2 (if recent), V3, V4, V5 |

For these intents, the Final Summary shows `Skipped (not requested)` for any step that was deliberately bypassed by the intent — distinct from `Skipped (configured as disabled)`.
