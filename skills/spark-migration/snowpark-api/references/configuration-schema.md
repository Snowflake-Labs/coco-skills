# Configuration Schema (Snowpark API view)

Every project owned by `spark-migration` has a JSON config file at
`<spark_migration_root>/configurations/<project_name>.json`. The shared
`<spark_migration_root>/scripts/config_manager.py` is the only writer; do
not edit configs by hand.

The Snowpark API sub-skill operates on a **namespaced view** of that config
plus the **global** `sma_cli_path` from `<spark_migration_root>/config.json`.

## Per-Project Keys (Snowpark API namespace)

| Key | Type | Values | Default | Owner |
|---|---|---|---|---|
| `sql_flavor` | str | `SparkSql`, `HiveSql`, `Databricks` | `SparkSql` | `migrate-pyspark-to-snowpark-api` |
| `enable_jupyter_conversion` | str | `yes`, `no` | `yes` | `migrate-pyspark-to-snowpark-api` |
| `generate_checkpoints` | str | `yes`, `no` | `yes` | `migrate-pyspark-to-snowpark-api` |
| `run_ewi_fixer` | str | `yes`, `no` | `yes` | `validate-pyspark-to-snowpark-api` |
| `run_ewi_fixer.ewi_comments` | str | `mark`, `remove` | `mark` | `validate-pyspark-to-snowpark-api` |
| `run_ewi_fixer.ewi_scope` | str | `only_pending`, `retry_not_resolved`, `all_reset` | `only_pending` | `validate-pyspark-to-snowpark-api` |
| `run_stage_conversion` | str | `yes`, `no` | `yes` | `validate-pyspark-to-snowpark-api` |
| `run_stage_conversion.stage_name` | str | any string | `migration_stage` | `validate-pyspark-to-snowpark-api` |
| `run_dvp_orchestrator` | str | `yes`, `no` | `yes` | `validate-pyspark-to-snowpark-api` |

## Per-Project Keys (Shared with SCOS)

These live in the same JSON file but are owned by the parent. The Snowpark API
sub-skill **reads** them — it does not write them.

| Key | Type | Default | Notes |
|---|---|---|---|
| `project_name` | str | (required) | Used as the config filename and `-p` to SMA CLI |
| `input_folder` | str | (asked) | Maps to SMA CLI `-i` |
| `output_folder` | str | (asked) | Maps to SMA CLI `-o` |
| `email` | str | (asked) | Maps to SMA CLI `-e` |
| `company` | str | (asked) | Maps to SMA CLI `-c` |
| `conversion_type` | str | `snowpark-connect` | Canonical values: `snowpark-connect`, `snowpark-api`. See alias normalization below |
| `migration_status` | str | `migrate` | `migrate` or `already_migrated` |
| `run_notebook_migration` | str | `yes` | Shared with SCOS post-conversion tail |

## Global Keys

Loaded from `<spark_migration_root>/config.json`:

| Key | Type | Default | Notes |
|---|---|---|---|
| `sma_cli_path` | str | (auto-detected or asked) | Absolute path to the `sma` binary |

## Conversion Type — Alias Normalization

`conversion_type` is normalized on **every** load and **every** save:

| Input (alias) | Canonical (persisted) |
|---|---|
| `scos`, `snowpark_connect`, `snowpark-connect` | `snowpark-connect` |
| `snowpark_api`, `snowpark-api` | `snowpark-api` |

Aliases on disk are rewritten in-place by `config_manager.load_configuration()`. Sub-skills only ever see canonical values.

## Accessing the Snowpark API Slice

The sub-skill fetches just its keys via:

```bash
python3 '<spark_migration_root>/scripts/config_manager.py' \
    view-section '<config_path>' snowpark_api
```

This returns a JSON object containing only the API-only keys above (the `sma_cli_path` is fetched separately via `load-global`).

The available namespaces are:

| Namespace | Includes |
|---|---|
| `shared` | `project_name`, `input_folder`, `output_folder`, `email`, `company`, `conversion_type`, `migration_status`, `run_notebook_migration` |
| `snowpark_api` | All API-only keys above |

## Persisting a Single Key

```bash
python3 '<spark_migration_root>/scripts/config_manager.py' \
    save '<config_path>' '{"run_stage_conversion": "no"}'
```

`save` always performs a deterministic merge (sort_keys=True) and re-normalizes `conversion_type` if present. To persist global `sma_cli_path`:

```bash
python3 '<spark_migration_root>/scripts/config_manager.py' \
    save-global '<spark_migration_root>' '{"sma_cli_path": "/path/to/sma"}'
```
