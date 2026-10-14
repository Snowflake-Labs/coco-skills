# SMA CLI Options Reference

Complete parameter reference for the SMA CLI used by `migrate-pyspark-to-snowpark-api`.

## Required Parameters

| Short | Long | Description |
|-------|------|-------------|
| `-i` | `--input` | Path to the input folder (PySpark source) |
| `-o` | `--output` | Path to the output folder (where SMA writes results) |
| `-e` | `--customerEmail` | Customer email |
| `-c` | `--customerCompany` | Customer company |
| `-p` | `--projectName` | Project name (required on first run; reused thereafter) |

## Always-Used Flags

| Flag | Effect |
|------|--------|
| `-y` | Accept license and proceed without an interactive prompt |

## Optional Flags

| Short | Long | Description | Default |
|-------|------|-------------|---------|
| `-x` | `--disableJupyterConversion` | Disable Jupyter / Databricks notebook conversion | Enabled |
| `-f` | `--sql` | SQL flavor: `SparkSql`, `HiveSql`, `Databricks` | `SparkSql` |
| `-d` | `--disableCheckpoints` | Disable checkpoint file generation | Enabled |

## Flag Derivation From Project Config

| Config key | Value | Resulting flag |
|---|---|---|
| `enable_jupyter_conversion` | `no` | `-x` |
| `enable_jupyter_conversion` | `yes` | (no flag — default) |
| `sql_flavor` | not `SparkSql` | `-f <sql_flavor>` |
| `sql_flavor` | `SparkSql` | (no flag — default) |
| `generate_checkpoints` | `no` | `-d` |
| `generate_checkpoints` | `yes` | (no flag — default) |

## Canonical Invocation

```bash
"<sma_cli>" -i "<input>" -o "<output>" \
    -e "<email>" -c "<company>" -p "<project>" -y [optional-flags]
```

**Always** run via the Bash tool with:
- `run_in_background: true`
- `description: "Run SMA conversion in background"`

This returns a `shell_id` that the migrate sub-skill polls every 5–10s via `bash_output`.

## Progress Patterns

Watch for these patterns in `bash_output` to drive user-facing progress:

| Pattern | Meaning |
|---|---|
| `[SMA] Step X/20 - <step_name>: STARTED` | Current step starting |
| `Found X Python files` | File-count discovery |
| `Info: ...` | Informational status line |
| `Conversion was successful.` | Success indicator |
| `Error: ...` | Failure indicator |
| `Execution aborted` | Failure indicator |
