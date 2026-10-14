---
name: migrate-pyspark-to-snowpark-api
description: |
  Fresh PySpark → Snowpark API conversion via the SMA CLI. Bundled sub-skill of `snowpark-api`, loaded only after the parent has determined that the user wants a NEW SMA conversion (intent=migrate, language=Python). Owns SMA CLI detection, the SMA invocation itself, progress monitoring, and SMA output-layout resolution. Delegates the entire post-conversion tail to `validate-pyspark-to-snowpark-api`.
---

# Migrate PySpark to Snowpark API (SMA CLI)

> **Bundled sub-skill** under `snowpark-api/`. This file is loaded via the
> Read tool by `snowpark-api/SKILL.md` (see its "Bundled Sub-skill Loading
> Convention"). Not registered as a standalone skill.

This sub-skill performs **only** the SMA CLI conversion step. After the SMA
output is resolved, control is handed off to
`validate-pyspark-to-snowpark-api/SKILL.md` inline.

## Inputs (passed inline by the parent router)

| Variable | Required | Source |
|---|---|---|
| `<input>` | Yes | `<config.input_folder>` |
| `<output>` | Yes | `<config.output_folder>` |
| `<email>` | Yes | `<config.email>` |
| `<company>` | Yes | `<config.company>` |
| `<project>` | Yes | `<config.project_name>` |
| `<config_path>` | Yes | `<spark_migration_root>/configurations/<project>.json` |
| `<spark_migration_root>` | Yes | grandparent of this SKILL.md (the `spark-migration/` skill root) |
| `<snowpark_api_root>` | Yes | parent of this SKILL.md (the `snowpark-api/` sub-skill root) |
| `<start_time>` | Yes | recorded by the parent at its Step 1 |

## Reference Material

- [`../references/sma-cli-options.md`](../references/sma-cli-options.md) — complete CLI flag reference
- [`../references/output-layouts.md`](../references/output-layouts.md) — v1/v2/v3 output layouts and resolution rules
- [`../references/configuration-schema.md`](../references/configuration-schema.md) — config keys this sub-skill reads and writes

## Step M1: Validate SMA CLI Path

Load the global config to read `sma_cli_path`:

```bash
python3 '<spark_migration_root>/scripts/config_manager.py' load-global '<spark_migration_root>'
```

- **If `sma_cli_path` is set and valid** (`test -x "<sma_cli_path>"`):
  store as `<sma_cli>` and proceed to Step M2.
- **If `sma_cli_path` is set but invalid** (binary missing/non-executable):
  inform the user and re-run the detection scan (see parent `spark-migration`
  Step 0.2). Once a new path is validated and saved globally, continue.
- **If `sma_cli_path` is NOT set**:
  go back to `spark-migration` Step 0.2 (the canonical detection routine) —
  do NOT scan independently. After it persists a valid path, resume here.

Do not proceed until `<sma_cli>` is set to a verified executable.

## Step M2: Collect CLI-Specific Fields

Fetch the Snowpark API slice of the project config:

```bash
python3 '<spark_migration_root>/scripts/config_manager.py' \
    view-section '<config_path>' snowpark_api
```

Three CLI-specific keys drive the optional flags:

| Config key | Variable used | Default |
|---|---|---|
| `enable_jupyter_conversion` | `Y` if `yes`, else `N` | `yes` (Y) |
| `sql_flavor` | `SparkSql` / `HiveSql` / `Databricks` | `SparkSql` |
| `generate_checkpoints` | `Y` if `yes`, else `N` | `yes` (Y) |

If all three keys are already set in the config, skip asking and use the saved
values. Otherwise, ask only for the missing keys via `ask_user_question`,
then persist the answers:

```bash
python3 '<spark_migration_root>/scripts/config_manager.py' \
    save '<config_path>' \
    '{"enable_jupyter_conversion": "yes", "sql_flavor": "SparkSql", "generate_checkpoints": "yes"}'
```

## Step M3: Run the SMA CLI

Derive optional flags using the table in
[`../references/sma-cli-options.md`](../references/sma-cli-options.md#flag-derivation-from-project-config),
then run the SMA CLI **in the background**:

```bash
"<sma_cli>" -i "<input>" -o "<output>" \
    -e "<email>" -c "<company>" -p "<project>" -y [optional-flags]
```

Use the Bash tool with:
- `run_in_background: true`
- `description: "Run SMA conversion in background"`

Capture the returned `<shell_id>`.

⛔ **Never** run SMA in the foreground — large workloads exceed agent timeouts.

## Step M4: Monitor Progress

Poll every 5–10 seconds:

```
bash_output(bash_id: "<shell_id>")
```

Surface progress to the user using the patterns in
[`../references/sma-cli-options.md`](../references/sma-cli-options.md#progress-patterns).

**Success criteria:** `Conversion was successful.` and exit code 0.

**Failure handling:**
- `Error:` lines or non-zero exit code → stop and surface the error verbatim to the user.
- Stuck for >10 minutes with no new output → ask the user whether to wait or abort. Do not silently retry.
- **Common quick fixes** before re-running:
  - `chmod +x "<sma_cli>"` if the binary lost execute permissions
  - Verify no other `sma` process is already running
  - Confirm `<input>` is a directory of PySpark files (not a single notebook)

## Step M5: Resolve SMA Output Layout

After successful completion, resolve `<output>` to the actual workload root.

Apply the detection rules from
[`../references/output-layouts.md`](../references/output-layouts.md) in order:

1. **v1 (timestamped):** if `<output>/Conversion-*` exists, pick the most
   recent and set `<output>` to it.
2. **v2 (flat):** else if `<output>/sma-output/` exists, set `<output>` to it.
3. **v3 (dual):** else if `<output>/Conversion_SnowparkAPI/sma-code-process-*`
   exists, pick the most recent and set `<output>` to it. (Snowpark API path
   only ever uses `Conversion_SnowparkAPI`; never `Conversion_SnowparkConnect`.)
4. **Otherwise:** keep `<output>` unchanged.

After resolution, verify:

```bash
test -d "<output>/Output" && test -d "<output>/Reports" && \
    test -f "<output>/Reports/Issues.csv"
```

If any check fails, stop. The SMA conversion silently failed. Show the user
`<output>/Logs/` contents (if present) and ask whether to re-run.

⛔ **CRITICAL: Do NOT copy `Output/`, `Reports/`, or `Logs/` upward.** All
subsequent steps must work inside the resolved `<output>` folder.

Log: `Detected SMA <v1|v2|v3> format. Resolved output path: <output>`

## Step M6: Hand Off to the Validator

After successful resolution, load the validator and follow it inline:

```bash
VALIDATOR_SKILL="<snowpark_api_root>/validate-pyspark-to-snowpark-api/SKILL.md"
test -f "$VALIDATOR_SKILL" || { \
    echo "MISSING: $VALIDATOR_SKILL — reinstall spark-migration"; \
    exit 1; }
```

Read `$VALIDATOR_SKILL` with the Read tool and follow its instructions
verbatim, passing this context inline:

```
The following context was configured by migrate-pyspark-to-snowpark-api:
- <intent>           = migrate
- <input>            = <input>
- <output>           = <output>         (resolved SMA workload root)
- <email>            = <email>
- <company>          = <company>
- <project>          = <project>
- <config_path>      = <config_path>
- <spark_migration_root> = <spark_migration_root>
- <snowpark_api_root>    = <snowpark_api_root>
- <start_time>       = <start_time>
- <sma_layout>       = v1 | v2 | v3
- <conversion_done>  = true

Skip any "ask for output path" steps — <output> is already resolved.
Proceed directly to your Step V1 (initialize git + verify SMA output).
Execute V1 through V8 to completion. Do NOT stop early. Do NOT return to me.
```

⛔ **Do not** call `skill("validate-pyspark-to-snowpark-api")`. It is a
bundled sub-skill, not a registered top-level skill.

Control does NOT return to this sub-skill after the validator is invoked —
the validator owns the rest of the pipeline (dashboard, notebook migration,
EWI fixer, stage conversion, DVP orchestrator, final summary).

## Stopping Points

| Condition | Action |
|---|---|
| `<sma_cli>` cannot be validated | Stop; defer to parent Step 0.2 |
| SMA CLI exits non-zero | Stop; show error to user; ask whether to retry or abort |
| SMA hangs >10 min with no new output | Stop; ask user; do not auto-retry |
| Output layout cannot be resolved | Stop; show `<output>/Logs/`; ask user for correct path |
| `Reports/Issues.csv` missing after resolution | Stop; treat as silent SMA failure |

## Outputs (handed to the validator)

- Resolved `<output>` (v1 `Conversion-*` / v2 `sma-output/` / v3 `sma-code-process-*`)
- `<output>/Output/` — Snowpark Python converted code
- `<output>/Reports/Issues.csv`, `InputFilesInventory.csv`, `ArtifactDependencyInventory.csv`
- (optional) `<output>/Logs/` — SMA logs
- `<config_path>` updated with any CLI-specific keys the user just answered
