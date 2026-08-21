# Validation Test Kit

This directory is the single reusable runtime for the
`validate-pyspark-to-snowpark-connect` skill.

Copy it into `Validation/tests/` with the cross-platform installer:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  install-kit --conv-root $CONVERSION_ROOT
```

Then render `test_template.py` into one `test_<ep_id>.py` per selected
entrypoint. (`test_template.py` itself is `collect_ignore`d by the kit
`conftest.py`, so pytest never tries to run the un-rendered template.)

## Runtime abstraction

Three runtimes share a single execution body (`_executor.run_and_capture`);
only session provisioning and orchestration differ:

| Flavor       | Module               | Session source                        | Phase |
|-------------|---------------------|---------------------------------------|-------|
| `local`     | `local_runtime.py`  | In-process PySpark + Delta catalog     | A     |
| `databricks`| `databricks_runtime.py` | Remote Databricks cluster (databricks-connect) | A |
| `scos`      | `scos_runtime.py`   | Snowpark Connect against cloned schema | B     |

### Driver-driven pipeline

```
rendered test_<ep>.py
  -> driver.run_validation_trial(request)
     -> runtime = get_runtime(flavor)
     -> runtime.provision(request)     # idempotent, hash-gated
     -> runtime.run_trial(request)     # clone golden + run + capture
```

The `driver` module assembles a `TrialRequest`, selects the flavor, calls
`provision(request)` then `run_trial(request)`, and (for Phase B) compares
against the Phase A baseline.

### Entrypoint formats (`.py` and `.ipynb`)

`_executor._load_entrypoint_module` dispatches on the entrypoint `path`
extension, so all three runtimes handle both formats without a per-format code
path:

- **`.py`** — imported as a module via `importlib` (top-level runs on import;
  callable mode invokes `mod.<callable>(spark, ...)` afterward).
- **`.ipynb`** — translated in-process by `notebook_source.py` (`%%sql`/`%sql`
  → `spark.sql(...)` per statement, `%run`/`dbutils.notebook.run(...)` →
  `_nb_run(...)`, other magics neutralized), then `compile`d + `exec`d into a
  fresh module — no Jupyter kernel. Notebooks are always script mode; module
  globals and `_nb_run` are injected before exec. `_nb_run` merges a `%run`
  target's cells into the caller's namespace (Databricks copy-paste semantics),
  resolving relative to the caller's dir, the workload root, then `sys.path`; a
  `%run` child that exits cleanly ends only the child. A SQL cell's result is
  bound so downstream cells resolve it: `_sqldf` always (Databricks' implicit
  last-SQL-cell binding) plus a named var when the magic carries `-r <name>` /
  `--result <name>` (Snowflake Workspace), e.g. `%%sql -r my_result` →
  `my_result = _sqldf = spark.sql(...)`.

A clean `sys.exit(0)`/`exit()` (the shape `dbutils.notebook.exit(...)` is patched
into) is treated as normal completion; any other exit code fails the trial.


### Provisioning (hash-gated)

Both SCOS and Databricks runtimes use a shared LOCAL hash store
(`shared/provision_hashes.json`). Reseed is gated on stored-hash mismatch OR
table absent (checked via SHOW TABLES). No Snowflake COMMENTs or Delta
TBLPROPERTIES are used for hash tracking.

- **`scos`**: seeds Snowflake golden schemas (one per entrypoint)
  by delegating to the internal `_scos_provision` module. Creates tables, stages
  mock data, and runs COPY INTO.
- **`databricks`**: prewarms the cluster, resolves the target catalog
  (`hive_metastore` by default, or `DATABRICKS_CATALOG`; write-probed), and seeds
  golden schemas from the unified `tables` dict.
- **`local`**: no-op (mock data already on disk; seeding is per-trial).

## Schemas-based analysis

Entrypoint metadata lives in `shared/schemas/` as individual JSON files
referenced by `shared/schemas/manifest.json`. The function
`helpers.assemble_analysis(schemas_dir)` loads them at test time, producing the
`analysis` dict with `entrypoints` and `import_roots`. There is no single
`analysis.json` — the schemas directory IS the analysis.

## File-sink capture naming

All runtimes use bare `io_id` names (e.g. `column_data`) for file-sink
captures — no `sink__` prefix. This keeps Phase A and Phase B captures
comparable without renaming.

## Phase-scoped virtual environments

`validate.py seed-venv --phase {a,b}` creates isolated venvs under
`Validation/shared/`. In a pool run, one venv is seeded per phase per pool
and shared across every batch worktree (idempotent — the runner's seed-venv
call becomes a fast no-op for batches after the first):

- `Validation/shared/.venv-source` — PySpark + Delta + databricks-connect (Phase A)
- `Validation/shared/.venv-scos` — snowflake-snowpark-python + snowpark-connect (Phase B)

## What the kit owns

- local Phase A session setup (PySpark + Delta)
- SCOS Phase B session setup
- per-trial isolation
- snapshot capture
- result comparison
- `SCOS_INPUT_<id>` / `SCOS_TEST_AUX_<name>` / `SCOS_SINK_<id>` env resolution per flavor

## No shims, no mock filesystem

Non-Spark I/O (cloud reads/writes, secrets, widgets, external deps) is never
shimmed or mocked — it is rewritten by the **patch blueprint**
(`scripts/patch_engine.py`, applied via `validate.py patch-add`).

## Layout

```text
harness/
  conftest.py
  test_template.py
  helpers.py
  comparator.py
  runtimes/
    __init__.py      # registry + architecture docs
    base.py          # ABC + dataclasses
    driver.py        # pytest entry point
    _executor.py     # run_and_capture
    local_runtime.py
    databricks_runtime.py
    scos_runtime.py
    _scos_provision.py
```

## Isolation model

- **Phase A**: fresh local warehouse plus fresh local schema per test
- **Phase B**: clone the entrypoint's golden Snowflake schema per test

File-category inputs are staged once by the harness provisioning under
`@<db>.<golden_schema>.<stage>/<run_id>/inputs/<rel>`; Phase B reads them from
that stage via `SCOS_INPUT_<id>` or `SCOS_TEST_AUX_<name>`. Table-category **sources** are loaded from
`mock_data/<ep_id>/` into the trial schema (Phase A: `seed_entrypoint`; Phase B:
golden-schema clone + provision COPY). The workload reads them via
`spark.table` / SQL (no env var).

## Environment variables

| Variable | Purpose | Default | Set by | Read by |
|----------|---------|---------|--------|---------|
| `SCOS_VALIDATION_DATABASE` | Snowflake database for validation runs | `SCOS_VALIDATION` | validate.py `init` | harness runtimes, cleanup.py |
| `SCOS_FLAVOR` | Run flavor: `local` (Phase A default) or `scos` (Phase B) | `local` | conftest.py | conftest.py, driver.py |
| `SCOS_INPUT_<ID>` | Path to a relational file-category source (local file in Phase A, `@stage/...` in Phase B) | — | conftest.py `trial` | the blueprint-rewritten workload |
| `SCOS_SINK_<ID>` | Capture path for a non-table sink write | — | conftest.py `trial` | the blueprint-rewritten workload |
| `SCOS_SINK_CAPTURE_DIR` | Per-trial root where path-form sink writes land | — | conftest.py `trial` | test_template.py (capture) |
| `SCOS_MOCK_DATA_DIR` | Root of per-entrypoint mock data | `Validation/shared/mock_data` | conftest.py | helpers.py |
| `SCOS_RESULTS_DIR` | Directory where captured results are written | `Validation/tests/results/<ep>` | conftest.py | test_template.py, helpers.py |
| `SCOS_STATE_JSON` | Path to `state.json` for harness lookups | — | conftest.py | conftest.py |
| `SCOS_SCHEMAS_DIR` | Path to `shared/schemas` | — | conftest.py | helpers.py |
| `SCOS_RUN_ID` | Unique ID for the current test run | random hex | conftest.py | helpers.py |
| `SCOS_PINNED_DATE` | Override pinned date for deterministic runs | today's date | operator | conftest.py |
| `SCOS_PIN_DATE_DISABLED` | Set to `1` to disable date pinning entirely | unset | operator | conftest.py |
| `SCOS_PUT_WORKERS` | Max concurrent stage PUT workers | `8` | operator | harness runtimes |
| `SCOS_GET_WORKERS` | Max concurrent staged-sink GET workers | `8` | operator | harness runtimes |
| `SCOS_OUTPUT_SCHEMA` | Per-trial schema where tables are seeded and captured | — | conftest.py `trial` | patched workloads, helpers.py |
| `SCOS_DATABASE_NAME` | Catalog/database token for 3-part table names | — | conftest.py `trial` | patched workloads |
| `SCOS_TRIAL_START_TS` | Epoch timestamp when trial started | — | conftest.py | helpers.py |
| `SCOS_TEST_AUX_<NAME>` | Path to a non-relational file source | — | conftest.py `trial` | patched workloads |
| `SCOS_DISABLE_INTERMEDIATE_DISCOVERY` | Set to `1` to skip auto-discover of intermediate tables | unset | operator | harness runtimes |
| `SCOS_SPARK_JARS_PACKAGES` | Comma-separated Maven coords for `spark.jars.packages` | auto-detect avro | operator | conftest.py |
| `SCOS_CONV_ROOT` | Conversion root directory | — | conftest.py | driver.py |
| `SCOS_DATABRICKS_ENV_FILE` | Path to `.env` file with Databricks credentials | — | operator / state.json | runtimes/__init__.py, cleanup.py |
| `SPARK_CONNECT_MODE_ENABLED` | Set to `1` in Phase B to activate Snowpark Connect path | — | conftest.py | helpers.py |
