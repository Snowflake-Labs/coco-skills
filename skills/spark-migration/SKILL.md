---
name: spark-migration
description: |
  Migrate Spark scripts and notebooks to Snowflake. Routes to one of two bundled conversion paths and orchestrates the post-conversion pipeline. **Default path: Snowpark Connect (SCOS)**, which preserves the PySpark API surface. The SMA / Snowpark API path is invoked only when the user explicitly asks for it. Also **deploys** the output as a **Notebook** (`deploy-notebook`) or **Code Bundle** (`deploy-code-bundle`).
  Triggers: convert spark, migrate pyspark, migrate spark, migrate to snowpark, convert to snowpark, snowpark connect, scos, scos migration, migrate to snowpark connect, migrate to scos, snowpark api, sma cli, sma conversion, run sma, snowflake.snowpark rewrite, already migrated, already ran sma, sma dashboard, fix ewis, stage conversion, dvp orchestrator, resume dvp, assess pyspark, assess spark, assess databricks, spark assessment, databricks assessment, spark workload assessment, migration readiness, migration readiness report, spark readiness, spark compatibility, spark compatibility report, pyspark compatibility, pyspark compatibility report, databricks compatibility, databricks to snowflake, pyspark to snowflake, spark to snowflake, spark snowflake, migration feasibility, migration effort, migration effort estimate, migration complexity, migration scope, how hard is migration, how complex is migration, analyze spark, analyze pyspark, analyze databricks, spark analysis, pyspark analysis, workload analysis, pre-migration analysis, spark workload analysis, check spark, check pyspark, check databricks, check compatibility, spark compatibility check, scan spark, scan pyspark, spark audit, audit spark, review spark, understand spark, understand pyspark, understand databricks, evaluate spark workload, should I migrate to snowflake, can my spark run on snowflake, snowflake readiness, scope the migration, deploy notebook, create notebook from migration output, deploy code bundle, create code bundle from migration output.
---

# Spark Migration

Routes Spark → Snowflake conversion requests to one of two bundled paths and owns the cross-path configuration, intent classification, and per-step dispatch.

## ⛔ Default Path: Snowpark Connect (SCOS)

Any unqualified request to "convert spark", "migrate pyspark", "migrate to snowpark", etc. routes to **`snowpark-connect/SKILL.md`**, which preserves the PySpark API surface (`withColumn`, `groupBy`, `spark.createDataFrame`). This is the recommended default for the vast majority of users.

Route to **`snowpark-api/SKILL.md`** (SMA CLI / `snowflake.snowpark` snake_case rewrite) **only** when the user explicitly mentions:

- "SMA", "SMA CLI", or "run sma"
- "Snowpark API" or "`snowflake.snowpark`"
- An already-migrated SMA output (intents like "open sma dashboard", "fix ewis", "run stage conversion", "resume dvp")
- An existing project config with `conversion_type` set to `snowpark-api` (or any legacy alias)

If the intent is ambiguous, ask the user which path they want — **do not silently default to the SMA path**.

## ⛔ Deploy Path (notebook or code bundle)

When the user wants to **deploy** the migration's output to Snowflake (rather than convert Spark), route to the deploy sub-skill matching the requested target:

- **Notebook** → **`deploy-notebook/SKILL.md`** — uploads the project (`.ipynb` main file + supporting files) to a stage and creates a **Notebook**. Intents: "deploy notebook", "deploy the migrated notebook", "create notebook from migration output".
- **Code Bundle** → **`deploy-code-bundle/SKILL.md`** — composes an entrypoint, writes `code_bundle.yml`, uploads the `.py` project to a stage, and creates a **Code Bundle** that runs on a warehouse or compute pool. Intents: "deploy code bundle", "run as code bundle", "execute code bundle", "create code bundle from migration output".

See **Step 6: Deploy Dispatch**.

## Flows

1. **(a) Already migrated** — User already has SMA/snowpark-connect output. Provide the result path, verify structure, initialize git, then run the post-conversion pipeline.
2. **(b) Snowpark Connect conversion** — Load `snowpark-connect/SKILL.md`. **This is the default.**
3. **(c) Snowpark API conversion (SMA CLI)** — Load `snowpark-api/SKILL.md`. **Explicit opt-in only.**
4. **(d) Deploy** — Deploy the migration's output to Snowflake as a **Notebook** (`deploy-notebook/SKILL.md`) or a **Code Bundle** (`deploy-code-bundle/SKILL.md`): upload the migrated project to a stage and create the object. See **Step 6: Deploy Dispatch**.

## Output Format

Every time you begin a step, sub-step, or significant action, prefix the message with a timestamp in the format `[YYYY-MM-DD HH:MM:SS]`. Obtain the current time by running `date '+%Y-%m-%d %H:%M:%S'` in bash.

⛔ **Final Summary:** the active conversion path owns its own Final Summary template. For the Snowpark API path, see [`snowpark-api/references/final-summary-template.md`](snowpark-api/references/final-summary-template.md). For the Snowpark Connect path, follow `snowpark-connect/SKILL.md`. Do NOT improvise your own summary format.

## Sub-skill Loading Convention

All sub-skills referenced by name in this document (`snowpark-connect`, `snowpark-api`, `deploy-notebook`, `deploy-code-bundle`, and `snowflake-notebook-migration`) are bundled **inside this skill's own directory tree** — they are NOT separately installed top-level skills and they will NOT appear in the skill registry. This is intentional: the parent owns user-facing triggers; sub-skills are workers loaded on demand to avoid trigger collisions.

To run a sub-skill (NOT via `skill("<name>")`):

1. Resolve its `SKILL.md` path relative to **this** SKILL.md's directory (`<skill_directory>`):

   | Sub-skill | Bundled path |
   |---|---|
   | `snowpark-connect` | `<skill_directory>/snowpark-connect/SKILL.md` |
   | `snowpark-api` | `<skill_directory>/snowpark-api/SKILL.md` |
   | `deploy-notebook` | `<skill_directory>/deploy-notebook/SKILL.md` |
   | `deploy-code-bundle` | `<skill_directory>/deploy-code-bundle/SKILL.md` |
   | `snowflake-notebook-migration` | `<skill_directory>/snowflake-notebook-migration/SKILL.md` |

2. **Read** that file with the Read tool and follow its instructions verbatim, passing the orchestrator context inline as the next turn's content.

3. If the file is missing at the expected path, **STOP** and report:
   > The bundled `<name>` sub-skill is missing at `<path>`. Reinstall the `spark-migration` skill.

   Do NOT fall back to a registry lookup — that lookup will fail.

The `snowpark-api/` sub-skill further loads its own children (`migrate-pyspark-to-snowpark-api`, `validate-pyspark-to-snowpark-api`, `sma-dashboard-generator`, `stage-conversion`, `dvp/dvp-*`) using the same convention — see `snowpark-api/SKILL.md` for its own loading table.

## Directory Layout

```
spark-migration/                                    ← <skill_directory>
├── SKILL.md                                        (this file — thin router)
├── Diagram.md                                      (cross-path layout)
├── scripts/config_manager.py                       (shared config manager)
├── configurations/<project>.json                   (per-project state)
├── config.json                                     (global state, e.g. sma_cli_path)
├── snowflake-notebook-migration/                   (shared by both paths)
├── snowpark-connect/                               (SCOS path — DEFAULT)
│   ├── SKILL.md
│   ├── migrate-pyspark-to-snowpark-connect/
│   ├── migrate-spark-scala-to-snowpark-connect/
│   ├── validate-pyspark-to-snowpark-connect/
│   └── ...
├── deploy-common/                                  (shared deploy scripts)
│   └── scripts/                                    (sf_exec, scan_migration_gaps, ensure_namespace, upload_project)
├── deploy-notebook/                                (deploy path — Notebook target)
│   ├── SKILL.md                                    (remind → namespace → upload → create notebook)
│   └── scripts/                                    (create_notebook, prepare_validation, execute_notebook)
├── deploy-code-bundle/                             (deploy path — Code Bundle target)
│   ├── SKILL.md                                    (remind → namespace → compose entrypoint → prepare yml → upload → create bundle)
│   └── scripts/                                    (inspect_entrypoint, generate_entrypoint, prepare_code_bundle, create_code_bundle, execute_code_bundle)
└── snowpark-api/                                   (SMA / Snowpark API path)
    ├── SKILL.md                                    (router for the API path)
    ├── scripts/sma_api.py                          (SMA SQLite/git/EWI helpers)
    ├── references/                                 (extracted long-form docs)
    ├── migrate-pyspark-to-snowpark-api/SKILL.md
    ├── validate-pyspark-to-snowpark-api/SKILL.md
    ├── sma-dashboard-generator/SKILL.md
    ├── stage-conversion/SKILL.md
    └── dvp/dvp-*/SKILL.md   (orchestrator + 9 children)
```

For the historical moved-paths table (where files lived before this redesign), see [`Diagram.md`](Diagram.md).

## Usage

### Step 0: Prerequisites Check

Run at skill startup before any other step.

#### 0.1 Check Git

```bash
git --version 2>/dev/null && echo "found" || echo "not found"
```

If Git is **not found**, install it for the current platform:

| Platform | Command |
|----------|---------|
| macOS (Homebrew available: `brew --version`) | `brew install git` |
| macOS (no Homebrew) | Instruct user to run `xcode-select --install`, wait for it to complete, then confirm. |
| Linux (Debian/Ubuntu) | Verify sudo first: `sudo -n true 2>/dev/null \|\| echo "sudo password required"`. If available non-interactively: `sudo apt-get install -y git`. Otherwise instruct the user. |
| Linux (RHEL/CentOS/Amazon) | Same sudo check, then `sudo yum install -y git`. |
| Windows | `winget install --id Git.Git` |

Verify with `git --version` after install. Stop if still unavailable.

#### 0.2 Check SMA CLI (only when conversion_type = snowpark-api)

Skip this sub-step for SCOS and already-migrated SCOS flows.

```bash
SMA_FOUND=$(which sma 2>/dev/null)
if [ -z "$SMA_FOUND" ]; then
  SMA_FOUND=$(find /Users/Shared/AplicacionesSMA "$HOME/AplicacionesSMA" \
    "$HOME/Applications/SMA-CLI" /Applications/SMA-CLI /opt/sma /usr/local/bin \
    -maxdepth 4 -name "sma" -type f 2>/dev/null | head -1)
fi
echo "${SMA_FOUND:-not found}"
```

**Found:** save to global config and proceed:
```bash
python3 '<skill_directory>/scripts/config_manager.py' save-global '<skill_directory>' '{"sma_cli_path": "<SMA_FOUND>"}'
```
Print: `✅ SMA CLI found at <SMA_FOUND>`

**Not found:** ask the user for the absolute path to the `sma` binary (typically `/Users/me/SMA-CLI-arm64-mac/orchestrator/sma`). Validate with `test -x "<path>"`. On valid path, save globally as above. Do not proceed without a verified SMA CLI.

### Step 0.5: Progress UI (Experimental — Snowpark Connect path only)

The live progress dashboard is an **experimental Snowpark Connect (SCOS)
feature** and is **OFF by default**. Do **not** launch it here — at this point
the conversion path is not yet resolved, and the SMA / `snowpark-api` path does
not use this UI.

**Only launch the UI when the user explicitly asks for it** (e.g. "show the
progress UI", "open the migration dashboard"). If the user has not explicitly
asked, the dashboard must never start — there must be no accidental launches.

**Analysis-only flows skip this step entirely.** If the intent is assessment /
readiness report (routing to `assess-pyspark-workload`, or loading a Scala/Java
migration skill to stop after Phase 1a), do not mention, offer, or launch the
Progress UI — it only makes sense for full multi-phase migrations.

When (and only when) the user has explicitly requested it **and** the flow is a
full migration, the Snowpark Connect path launches the server itself, once the
conversion folder (`<CONVERSION>`) is resolved, so the server tails the exact
event log the SCOS phases write to. See the **Progress UI Emit Hooks
(Experimental)** section of
[`snowpark-connect/migrate-pyspark-to-snowpark-connect/SKILL.md`](snowpark-connect/migrate-pyspark-to-snowpark-connect/SKILL.md).

No action is required in this step. The launcher enforces this with a
`--confirm-experimental` interlock (a no-op without it), honours
`<config.enable_progress_ui>` (default `no`), is idempotent, and is non-fatal —
migration never blocks on the UI.

### Step 1: Load Configuration

**Record `<start_time>` = current time** (used for duration in the Final Summary).

Load the global config:

```bash
python3 '<skill_directory>/scripts/config_manager.py' load-global '<skill_directory>'
```

Store as `<global_config>`. `sma_cli_path` is read from here (not the project config).

List per-project configurations:

```bash
python3 '<skill_directory>/scripts/config_manager.py' list '<skill_directory>/configurations'
```

If configurations exist, display the numbered list and ask via `ask_user_question`:
- **Use existing configuration** — user selects by name or number → load it
- **Create new configuration** — proceed to step 1.3

If none exist, go directly to 1.3.

#### 1.2 Load Existing

```bash
python3 '<skill_directory>/scripts/config_manager.py' load '<config_path>'
```

`load` merges defaults for missing keys AND normalizes legacy `conversion_type` aliases (`scos`, `snowpark_connect`, `snowpark_api`) to their canonical forms (`snowpark-connect`, `snowpark-api`), persisting the normalized form back to disk.

Store the result as `<config>` and `<config_path>` for later writes. Go to Step 2.

#### 1.3 Create New

Ask for the 5 required project fields in one numbered list:

```
New configuration — please provide the following:

  1. Project Name:            (used as configuration filename)
  2. Source Code Path:        (PySpark or Spark Scala source directory)
  3. Output Folder:           (where converted code will be saved)
  4. Customer Email:
  5. Customer Company:

Example: "1. my_project, 2. /Users/me/spark-etl, 3. /Users/me/output, 4. user@co.com, 5. Acme Inc"
```

`#1` (Project Name) is **required**. `#2`–`#5` may be deferred.

```bash
python3 '<skill_directory>/scripts/config_manager.py' create '<skill_directory>/configurations' '<project_name>'
```

Then persist any provided fields:

```bash
python3 '<skill_directory>/scripts/config_manager.py' save '<config_path>' \
    '{"input_folder": "<input>", "output_folder": "<output>", "email": "<email>", "company": "<company>"}'
```

Include only keys the user provided. Store `<config_path>` and `<config>`. Go to Step 2.

### Step 2: Review Configuration

If `<config>` has saved values, present a single summary of all 18 settings (Project / Conversion / Post-Conversion) and ask:

- **Use these settings** — proceed
- **Edit settings** — present the numbered list below and accept partial updates

```
 ── Project ──────────────────────────────────
  1. Source Code Path:        <config.input_folder or (not set)>
  2. Output Folder:           <config.output_folder or (not set)>
  3. Customer Email:          <config.email or (not set)>
  4. Customer Company:        <config.company or (not set)>
  5. Project Name:            <config.project_name>

 ── Conversion ───────────────────────────────
  6. Conversion Type:         <config.conversion_type>     (snowpark-connect [default] / snowpark-api)
  7. Migration Status:        <config.migration_status>    (migrate / already_migrated)
  8. SMA CLI Path:            <global_config.sma_cli_path or (not set)>   (snowpark-api only; saved globally)
  9. Jupyter Conversion:      <config.enable_jupyter_conversion>  (yes / no; snowpark-api only)
 10. SQL Flavor:              <config.sql_flavor>          (SparkSql / HiveSql / Databricks; snowpark-api only)
 11. Generate Checkpoints:    <config.generate_checkpoints>  (yes / no; snowpark-api only)

 ── Post-Conversion ──────────────────────────
 12. Run Notebook Migration:  <config.run_notebook_migration>
 13. Run EWI Fixer:           <config.run_ewi_fixer>            (snowpark-api only)
 14. EWI Comments:            <config.run_ewi_fixer.ewi_comments>
 15. EWI Scope:               <config.run_ewi_fixer.ewi_scope>
 16. Run Stage Conversion:    <config.run_stage_conversion>     (snowpark-api only)
 17. Stage Name:              <config.run_stage_conversion.stage_name>
 18. Run DVP Orchestrator:    <config.run_dvp_orchestrator>     (snowpark-api only)
```

Map numbers to keys per the table in [`snowpark-api/references/configuration-schema.md`](snowpark-api/references/configuration-schema.md). Persist with `save` (project keys) and `save-global` (`sma_cli_path` only).

The post-conversion keys 13–18 are **SMA-only**. For SCOS flows, they are read but the post-conversion pipeline is owned by `snowpark-connect/`.

### Step 3: Determine Migration Status and Conversion Path

Use `<config.migration_status>` and `<config.conversion_type>` (canonical: `snowpark-connect` or `snowpark-api`) to route:

| `migration_status` | `conversion_type` | Route to |
|---|---|---|
| `already_migrated` | `snowpark-connect` | `snowpark-connect/SKILL.md` (already-migrated entry) |
| `already_migrated` | `snowpark-api` | `snowpark-api/SKILL.md` with `<intent>=already_migrated` |
| `migrate` | `snowpark-connect` (default) | `snowpark-connect/SKILL.md` |
| `migrate` | `snowpark-api` | `snowpark-api/SKILL.md` with `<intent>=migrate` |

If `<config.conversion_type>` is unset, default to `snowpark-connect`. If `<config.migration_status>` is unset, ask the user.

### Step 4: Validate Existing Output (already_migrated only)

When `<config.migration_status> = already_migrated`:

Ask for the output path (pre-fill `<config.output_folder>`). Store as `<output>`.

```bash
test -d "<output>/Output" && test -d "<output>/Reports" && echo "Valid" || echo "Invalid"
```

If invalid, check for SMA v1 `Conversion-*` subfolder:

```bash
ls -d "<output>"/Conversion-* 2>/dev/null | sort | tail -1
```

If present, resolve `<output>` to it and re-validate. If still invalid, ask the user for the correct path. (Full v1/v2/v3 resolution rules: [`snowpark-api/references/output-layouts.md`](snowpark-api/references/output-layouts.md).)

Once `<output>` is validated, go to Step 5.

### Step 5: Dispatch to Conversion Path

#### `snowpark-connect` route (default)

```bash
SNOWPARK_CONNECT_SKILL="<skill_directory>/snowpark-connect/SKILL.md"
```

Read with the Read tool and follow inline. Context block:

| Parameter | Value | Sub-skill variable |
|-----------|-------|--------------------|
| Source path | `<input>` | `$ARGUMENTS` |
| Output path | `<output>` | `$OUTPUT` |
| Customer Email | `<email>` | `$EMAIL` |
| Customer Company | `<company>` | `$COMPANY` |
| Project Name | `<project>` | `$PROJECT` |
| Invoker identity | `orchestrator` | `snowpark_connect_invoker` |
| Migration status | `<config.migration_status>` | Pass through unchanged |

Include `snowpark_connect_invoker: orchestrator` verbatim — the SCOS sub-skills read this flag to suppress their standalone Phase-6 notebook handoff (which would otherwise duplicate the work owned by `snowflake-notebook-migration`).

The SCOS sub-skill owns the entire conversion AND post-conversion pipeline for its path; control does not return here.

#### `snowpark-api` route (explicit opt-in)

```bash
SNOWPARK_API_SKILL="<skill_directory>/snowpark-api/SKILL.md"
```

Read with the Read tool and follow inline. Context block:

```
The following context was configured by the spark-migration orchestrator:
- <intent>           = migrate | already_migrated      (per Step 3 routing)
- <input>            = <config.input_folder>
- <output>           = <config.output_folder>          (already validated for already_migrated)
- <email>            = <config.email>
- <company>          = <config.company>
- <project>          = <config.project_name>
- <config_path>      = <skill_directory>/configurations/<project>.json
- <spark_migration_root> = <skill_directory>
- <snowpark_api_root>    = <skill_directory>/snowpark-api
- <start_time>       = <start_time>

Detect source language and route to the appropriate child.
Do NOT return here — the API path owns its own final summary.
```

The `snowpark-api/` router will:

1. Detect language (Python only is supported; Scala stops with a switch-to-SCOS prompt)
2. Route by `<intent>` to either `migrate-pyspark-to-snowpark-api/` (fresh conversion) or `validate-pyspark-to-snowpark-api/` (already-migrated / individual operations)
3. The chosen child runs the entire pipeline through the Final Summary

### Step 6: Deploy Dispatch (Flow d — deploy intent)

When the request is to **deploy** the migration's output (not to convert Spark), skip Steps 3–5 and dispatch to the deploy sub-skill for the requested target.

**Notebook target** — **Read** `deploy-notebook/SKILL.md` with the Read tool and follow it inline:

```bash
DEPLOY_NOTEBOOK_SKILL="<skill_directory>/deploy-notebook/SKILL.md"
```

| Parameter | Value | Sub-skill variable |
|-----------|-------|--------------------|
| Project dir | migrated project dir containing the `.ipynb` (e.g. `<output>/Output/`) | `$PROJECT_DIR` |
| Main notebook | the `.ipynb` file (relative to the project dir) | `$MAIN_IPYNB` |
| Target db/schema/stage/warehouse/notebook name | as configured | `$DATABASE/$SCHEMA/$STAGE/$WAREHOUSE/$NOTEBOOK_NAME` |
| Connection | Snowflake connection name | `$CONNECTION` |

The `deploy-notebook/` sub-skill runs its own steps (remind-to-fix → verify namespace → upload the project to a stage → create the Notebook) and owns its Final Summary. Control does not return here.

**Code Bundle target** — **Read** `deploy-code-bundle/SKILL.md` with the Read tool and follow it inline:

```bash
DEPLOY_CODE_BUNDLE_SKILL="<skill_directory>/deploy-code-bundle/SKILL.md"
```

| Parameter | Value | Sub-skill variable |
|-----------|-------|--------------------|
| Project dir | migrated project dir (e.g. `<output>/Output/`) | `$PROJECT_DIR` |
| Entrypoint | the file to run (generated in Step 0.6), relative to the project dir | `$ENTRYPOINT` |
| Target db/schema/stage | as configured | `$DATABASE/$SCHEMA/$STAGE` |
| Bundle name | code bundle object name | `$BUNDLE_NAME` |
| Compute | warehouse (default) or compute_pool | `$COMPUTE_TYPE`/`$COMPUTE_POOL`/`$WAREHOUSE` |
| Connection | Snowflake connection name | `$CONNECTION` |

The `deploy-code-bundle/` sub-skill runs its own steps (remind-to-fix → verify namespace → compose entrypoint → prepare spec → upload → create the bundle) and owns its Final Summary. Control does not return here.

## Direct-Intent Triggers

When the user opens a session with intents like **"fix ewis"**, **"open sma dashboard"**, **"run stage conversion"**, or **"resume dvp"**, route them as follows:

1. Run Step 0 (prerequisites). SMA CLI check is skipped — these intents operate on existing output.
2. Run Step 1 to locate or create a config. Force-set `conversion_type=snowpark-api` and `migration_status=already_migrated` if not already.
3. Run Step 4 to validate `<output>`.
4. Skip Step 5 dispatch and load `snowpark-api/validate-pyspark-to-snowpark-api/SKILL.md` directly, passing the intent (`fix ewis` / `open sma dashboard` / etc.) in the context block — the validator's "Direct-Intent Behavior" table maps each intent to the subset of steps to run.

For **deploy** intents (**"deploy notebook"**, **"create notebook from migration output"**, **"deploy code bundle"**, **"run as code bundle"**, **"create code bundle from migration output"**), go directly to **Step 6: Deploy Dispatch** — do not run the conversion Steps 3–5.

## Database & Helpers

The `sma_api.py` module (SQLite + git + EWI helpers) lives at:

```
<skill_directory>/snowpark-api/scripts/sma_api.py
```

For the function reference, see [`snowpark-api/references/sma-api-reference.md`](snowpark-api/references/sma-api-reference.md).

## Error Handling

| Condition | Action |
|---|---|
| Git unavailable after install attempt | Stop in Step 0.1; ask user to install manually |
| SMA CLI cannot be located/validated (API path only) | Stop in Step 0.2; ask user for path |
| `<config.conversion_type>` is `snowpark-api` but user clearly wants SCOS | Ask the user to confirm; offer to flip via Edit settings |
| `<output>` exists but lacks `Output/` and `Reports/` (already_migrated) | Stop in Step 4; ask for correct path |
| Bundled sub-skill missing at expected path | Stop and report: "The bundled `<name>` sub-skill is missing at `<path>`. Reinstall the `spark-migration` skill." |

Per-step error handling lives inside the bundled sub-skills. See `snowpark-connect/SKILL.md` and `snowpark-api/SKILL.md`.

## Outputs

Both conversion paths produce a converted workload with:

| Output | Location |
|--------|----------|
| Converted code | `<output>/Output/` |
| Issues report | `<output>/Reports/Issues.csv` |
| Inventory | `<output>/Reports/InputFilesInventory.csv` |
| Dependency inventory | `<output>/Reports/ArtifactDependencyInventory.csv` |
| EWI dashboard (snowpark-api) | `<output>/sma-dashboard/` |
| DVP workspace (snowpark-api) | `<output>/dvp/` |

The Snowpark Connect path produces an equivalent but path-distinct `Reports/` layout — see `snowpark-connect/SKILL.md`.

## Example Workflows

See [`snowpark-api/references/example-workflows.md`](snowpark-api/references/example-workflows.md) for concrete conversational walkthroughs of the four common entry points (fresh SMA, already-migrated, direct intent, Scala fallback).

For Snowpark Connect example walkthroughs, see `snowpark-connect/SKILL.md`.
