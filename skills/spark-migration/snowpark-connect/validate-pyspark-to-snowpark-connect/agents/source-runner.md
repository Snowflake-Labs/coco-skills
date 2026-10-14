# Source Runner

Owns Phase A: write the tests from the shared kit, run the selected entrypoints
on the source runtime (local PySpark + Delta, or Databricks-connect per
`source_runtime`), and persist source baselines when possible.

**Prior learnings:** Before your first step, read
`$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md` into your context.
It contains patch patterns, schema quirks, and dialect issues discovered by
workers that completed before you. Apply any relevant patterns rather than
rediscovering them.

## Inputs

- `CONVERSION_ROOT`
- `SKILL_DIRECTORY`

Derived paths:

- `VALIDATION_ROOT = <CONVERSION_ROOT>/Validation`
- `TESTS_DIR = Validation/tests`
- `RESULTS_DIR = Validation/results/phase_a`
- `SCHEMAS_DIR = Validation/shared/schemas`
- `STATE_JSON = Validation/state.json`
- `VENV_PYTHON = Validation/shared/.venv-source/bin/python`

CLI prefix for `validate.py` (and other skill scripts that need the project
env): `uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/…`

## Ground Rules

1. Copy the shared test kit from `scripts/harness/` instead of re-authoring
   `conftest.py`, helpers, or the comparator from memory.
2. Render one test file per selected entrypoint from the shared template.
3. The same `test_template.py` handles both `.py` and `.ipynb` entrypoints — no
   per-format choice is needed. The harness dispatches on the entrypoint `path`
   extension (`.ipynb` is translated to Python and `exec`d in-process; there is
   no Jupyter kernel).
4. If the harness needs a fix mid-run, edit the **copied** files under
   `Validation/tests/` directly. Never edit the skill source in `scripts/harness/`,
   and never re-run `install-kit` after authoring — it would overwrite your
   run-local edits.
5. Widget inputs are rewritten to inline literals by the patch blueprint —
   no separate widget manifest file or env-var indirection is needed.

## Authoring the suite

Copy the full kit with the cross-platform installer (preferred — no Unix-only
`cp -R`, skips `__pycache__`, drops a tests/`.gitignore`):

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  install-kit --conv-root $CONVERSION_ROOT
```

This populates `$CONVERSION_ROOT/Validation/tests/` with `conftest.py`,
`helpers.py` (kit helpers), `comparator.py`, `test_template.py`,
and a `.gitignore`. These files are the canonical harness — do NOT rewrite or
regenerate them from memory.

## Test harness composition

The Phase A test harness uses a TWO-FILE composition:

1. **`tests/conftest.py`** — copied from the kit (above). Do NOT rewrite it from
   memory; if it needs a fix, edit the copied `tests/conftest.py` directly.

2. **`tests/helpers.py`** (kit helpers) — copied verbatim from the kit.
   Import from it in your test files:
   ```python
   from helpers import (
       capture_results,
       compare_results,
       intercept_session,
   )
   ```

3. **`tests/test_<ep_id>.py`** — workload-specific tests that import from
   `helpers` and call `intercept_session` / `capture_results` etc. with
   workload-specific arguments.

**Critical Rule**: Do NOT rewrite data I/O in the test files or invent local
path logic. Cloud reads/writes, secrets, and widgets are rewritten by the patch
blueprint (`validate.py patch-add`) so the workload reads/writes
`os.environ["SCOS_INPUT_<id>"]` / `["SCOS_TEST_AUX_<name>"]` /
`["SCOS_SINK_<id>"]`, which the runtime driver sets per flavor. If a Phase A run fails because an I/O call was not yet patched,
add a patch with `validate.py patch-add` (see `agents/patch-author.md`) — do not
monkeypatch readers or hardcode mock paths in the test.

If you find yourself writing `_rewrite_path_for_flavor`, `_WRITE_PATH_MAP`,
boto3 mock objects, or `intercept_cloud_paths`-style helpers from scratch, STOP:
that machinery is gone. The correct fix is a blueprint patch.

## Rendering test files

Render one `test_<ep_id>.py` per selected entrypoint from
`test_template.py`. The test file name's `<ep_id>` must match
`schemas/entrypoints/<id>/` → `entrypoints[].id`. The harness reads `path`, `run_mode`,
`entrypoint_callable`, and `entrypoint_kwargs` from `ep_config` at runtime —
do not duplicate them as template constants.

Customize only `MODULE_GLOBALS_FACTORY` when the entrypoint relies on injected
module-level names (database, schema, aux file paths, etc.). The entrypoint body
is NOT wrapped; `runpy.run_path` executes it directly.

Tables in `schemas/entrypoints/<id>/tables/<KEY>.json` live under a single `tables` dict
keyed by name. Each entry has `access` (`read`/`write`/`readwrite`),
`columns`, `category`, `format`, `original_path`, and `mock_file`.
The harness uses `access` to decide seeding (read/readwrite → mock data) vs
pre-creation (write → empty table).

Data I/O is not configured in the rendered test — it is handled by the blueprint
patches plus the `SCOS_INPUT_<id>` / `SCOS_SINK_<id>` env vars the `trial`
fixture exports.

No shims for Databricks/Snowflake-only PySpark symbols (e.g. `parse_json`,
`VariantType`) — see **Phase A skip** below. Dead external imports (`pyodbc`,
`boto3`) with no live use: delete via blueprint patch, not conftest stubs.

Keep these rendered tests minimal.

## Importability

The harness `conftest.py` adds each path in `schemas/entrypoints/<id>/_meta.json` → `import_roots`
to `sys.path` at collection time, so sibling-module imports resolve without
manual path hacks. If the entrypoint imports from a sibling package that lacks
an `__init__.py`, create the missing `__init__.py` (empty is fine) under
`Validation/source/` so Python's import machinery finds it. Files with spaces
in their names do not need renaming — `runpy.run_path` accepts any filesystem
path.

## Pre-flight

Before the first pytest run, skim each entrypoint's source (and any SQL
templates it loads). **Default: run pytest once and classify from the failure**
— do not pre-emptively skip. Namespace/I/O/connector/`saveAsTable` wiring,
missing mocks, and harness gaps are fixed via patches or inline schema repair
(see the iteration table below), not skipped.

Pre-emptive `phase_a_skipped` is only for constructs the **configured source
runtime** cannot execute (see **Environment differences and Phase A skip** below).
If the blocker might be plumbing, apply patches first and run pytest once.

## Iteration loop

**First step (sequential, before any pytest):** seed the source venv:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  seed-venv --conv-root $CONVERSION_ROOT --phase a
```

Run the selected tests with **one** pytest invocation over the whole tests dir,
**in parallel** via pytest-xdist (`-n auto`). This is required — never loop
pytest once per entrypoint, and never run serially: parallel execution is what
keeps a multi-entrypoint validation fast. The kit is xdist-safe (each trial gets
its own `tmp_path`, a unique local schema, and a unique results dir; xdist
workers are separate processes so per-trial `SCOS_INPUT_*`/`SCOS_SINK_*` env
exports never collide).

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  run-tests --conv-root $CONVERSION_ROOT --phase a
```

(`pytest-xdist` is installed by `seed-venv`. `run-tests` keeps `-n auto` and
passes the required env vars automatically.)

## Record keeping — MANDATORY

**`run-tests` owns the iteration counter and calls `record-iter` automatically
based on the pytest JSON report; never pass an iteration number, and do NOT call
record-iter manually after run-tests.** The default `--iter` value resolves to
the iteration that just ran (written by `run-tests` at the start of each invocation).

After the last iteration's fixes, run once with `--verify-all` to catch
regressions across all trials (including those already marked passed):

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  run-tests --conv-root $CONVERSION_ROOT --phase a --verify-all
```

If any trial regresses, apply the fix and iterate — same loop, no new terminal
state, no new flag semantics.

If `run-tests` stdout prints `no_progress_detected trial=<id> …` for a Phase A
trial, the same failure signature is repeating across iterations — re-running the
identical attempt is wasted. Change approach: try a materially different fix, or —
if the failure is an unrunnable Databricks/module-level construct that cannot
produce a local baseline — mark it `phase_a_skipped` (with a reason) rather than
re-running it. Do not re-run an unchanged Phase A trial after this signal.

If a test appears to run stale code after an edit, purge caches first:
`find $CONVERSION_ROOT/Validation/source -name __pycache__ -type d -prune -exec rm -rf {} +`

Classify each failure by its test result and take the matching action (details
in the sections below). Apply the smallest fix that keeps the shared kit coherent:

| Test result | Action |
|---|---|
| Test passes (no pytest exception) but **`captured_outputs=0`** *and the entrypoint declares a write/display sink* — the write path was never executed | **Diagnose before moving on.** Do NOT record `baseline_produced` and accept 0 outputs as done. Open the source file and look for guards that short-circuit before the write: a file-listing stub returning `[]` causing `max(files)` to raise before `spark.read` is reached, a broad `except:` path swallowing the error, or an `if df.count() > 0:` guard where mock data is empty. Fix: add a `rewrite_main_block_env` patch (`patch-add`) that bypasses the guard entirely and redirects the read to `os.environ["SCOS_INPUT_<ID>"]` and the write to `os.environ["SCOS_SINK_<ID>"]`. See the Patch recipes tables in `patch-author.md` for the file-lister-stub pattern. |
| **`COLUMN_NOT_FOUND: X`** — the error names exactly one missing column | `referenced_columns` in `_meta.json` is a partial, non-authoritative hint parsed from SQL-expression fragments. If X appears there, it corroborates the column; if absent, that proves nothing. Add X to the table the error localizes to regardless. Never seed the whole list. Regenerate with `datagen` and re-run. |
| Test passes and **`captured_outputs=0`** but the entrypoint declares **no** sink (pure DDL/config: only `CREATE TABLE`/config, no DataFrame write or display) | **Expected — a clean run IS the baseline.** The harness accepts a zero-sink clean run automatically (Phase B matches when it also runs clean). Do NOT diagnose, and do NOT `phase_a_skipped` it. |
| Missing table/column (`TABLE_OR_VIEW_NOT_FOUND`), missing `mock_file`, `columns: []`, parquet type mismatch, a clean run with empty/all-null **rows in an output that was written**, or a harness failure saying a declared sink produced/captured 0 rows | **Inline schema repair** (below) — fix the relevant `schemas/entrypoints/<id>/tables/<KEY>.json` and regenerate; never hand-edit mocks. Only mark a sink `allow_empty: "<short reason>"` when the empty result is genuinely intentional. |
| **Ambiguous column** after a join (`AMBIGUOUS_REFERENCE` — `could be: [X, X]`) | **Inline schema repair — usually MOCK over-seeding, not a code bug and not a reason to skip.** The miner attributed a column that a table only receives *via a join* (e.g. a lookup column like `country_cd`) to that table too, so the mock join produces a duplicate absent in the real schema (this includes self-joins). Remove the mis-attributed column from the offending leg's `tables/<KEY>.json` and regenerate. |
| `AnalysisException` on a 3-part `CATALOG.SCHEMA.TABLE` name | **Namespace-rebind patch** via `patch-add` (`SCOS_DATABASE_NAME`/`SCOS_OUTPUT_SCHEMA`) — this is plumbing, NOT a skip |
| Unpatched I/O — cloud read/write, `s3://`/boto3, widgets, secrets | **`patch-add`** (`[TEST-PATCH]`) so the workload reads `SCOS_INPUT_*` / `SCOS_SINK_*` / `SCOS_TEST_AUX_*`; never monkeypatch readers or hardcode mock paths |
| Connector read — `spark.read.format("snowflake")...load()` failing on missing JAR/connection | **`patch-add` per-side**: `source` → `spark.table(f"{os.environ['SCOS_DATABASE_NAME']}.{os.environ['SCOS_OUTPUT_SCHEMA']}.TABLE_NAME")`; `migrated` → keep the `format("snowflake")` read but rebind `sfDatabase`/`sfSchema` to `SCOS_DATABASE_NAME`/`SCOS_OUTPUT_SCHEMA` (SCOS runs on Snowflake). See the Patch recipes tables in `patch-author.md`; never skip — it's plumbing |
| Connector read written as `spark.sql("… FROM DB.SCHEMA.T")` / `spark.table("DB.SCHEMA.T")` with a prod 3-part name | **`patch-add`** namespace-rebind the **literal `DB.SCHEMA` prefix in the string** (see the Patch recipes tables in `patch-author.md`). Do NOT reach for an `.option("sfDatabase"/"sfSchema", …)` rebind — that only matches `format("snowflake")` chains and silently no-ops here |
| Connector read — `spark.read.format("jdbc"/"redshift")...load()` | **`patch-add`** `spark.table(...)` rewrite on the **source** side; also patch the **SCOS side** if `Output/` still has the external read (SCOS lacks the driver). See the Patch recipes tables in `patch-author.md`; never skip |
| Databricks/Snowflake-only SQL (`QUALIFY`, `MERGE INTO`, `PIVOT`, `::` casts) or imports (`parse_json`, `VariantType`) | **`phase_a_skipped`** via `record-trial-status` (below) — do NOT rewrite; Phase B runs it on real SCOS |
| Import error — missing `__init__.py` / sibling module not found | Create the empty `__init__.py` under `Validation/source/` (import roots are already on `sys.path`) |
| Import error — missing **third-party package** (`ModuleNotFoundError: No module named 'pytz'`, etc.) | Install it into the phase venv and re-run: `uv pip install --python $VENV_PYTHON <package>`. The workload's Python runs locally (databricks-connect/PySpark), so its own deps must be in `.venv-source`; workloads without a `requirements.txt` need this. |
| Harness/kit bug (conftest, helpers, comparator) | Edit the copied harness under `Validation/tests/` directly; escalate to the orchestrator if it's a deeper kit defect |

**Mock data is owned by the schema — never hand-edit it.** If a mock is bad
(wrong values, or a parquet type mismatch in setup/seeding such as
`Parquet column cannot be converted: Expected: decimal(10,2), Found: DOUBLE` /
`Expected: timestamp, Found: string` / `Expected: date, Found: INT64`), fix the
column's declared `type` in the relevant `schemas/entrypoints/<id>/tables/<KEY>.json` and **regenerate** —
do not cast or rewrite the parquet/csv by hand. `datagen` derives each parquet
physical type from the declared `type` (`decimal(p,s)`→`decimal128(p,s)`,
`timestamp*`→`timestamp[us]`, `date`→`date32`, `short`/`smallint`→`int16`,
`byte`/`tinyint`→`int8`, `real`→`double`, …), so a correct declared `type`
always produces a seedable mock. `datagen` is a standalone script
(`scripts/datagen.py`), **not** a `validate.py` subcommand:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/datagen.py \
  $SCHEMAS_DIR $CONVERSION_ROOT/Validation/shared/mock_data --all        # --all forces a rewrite of stale mocks
```

Then re-run tests with `run-tests --phase a` (`run-tests` records the iteration and advances the counter automatically).

### Databricks-sourced entrypoints (source_runtime=databricks)

For entrypoints with `source_runtime=databricks`, the Phase A schema-fix loop
is: edit `schemas/entrypoints/<id>/tables/<KEY>.json` (or `_meta.json` for entrypoint-level
fields) → datagen → re-pytest. The harness
reseeds changed tables automatically (hash-gated provisioning on next pytest
run).

### Inline schema repair (Phase A — do not exit)

Per the rule above, **every data problem is fixed in the schema, never in the
mock.** Stay in this runner: edit the relevant `schemas/entrypoints/<trial_id>/tables/<KEY>.json`
(or `_meta.json` for entrypoint-level fields) →
`datagen` (hash-driven; add `--all` to force-rewrite) →
`run-tests --phase a` (records automatically). Route by the
test result:

| Test result | Schema fix |
|---|---|
| `AnalysisException` / `TABLE_OR_VIEW_NOT_FOUND`, missing `mock_file`, `columns: []` | add the table/columns with the right `access` (`read` / `write` / `readwrite`) |
| parquet type mismatch (`Expected: decimal(10,2), Found: DOUBLE`; `Expected: date, Found: INT64`) | fix the column's declared `type` (datagen derives the physical type — see the mapping above) |
| runs clean but output is empty/all-null (a filter keeps no rows, or a join key doesn't overlap) | add the filter literals as `"values"`, or a `joins` edge linking the key columns. For **timestamp/date** columns don't use `"values"` (the verifier can't enum-check temporal `values`) — widen the `entrypoint_kwargs` date bounds instead. Use `allow_empty: "<short reason>"` only for rare sinks that are intentionally empty for this fixture. |

Only escalate to the orchestrator for harness kit bugs or Databricks-only skips
(`phase_a_skipped`), not for fixable schema gaps.

**Committing edits.** Blueprint patches via `patch-add` auto-commit a
`[TEST-PATCH]`. But any **direct** edit you make to `Output/` (not through
patch-add) is left uncommitted and would never reach the deliverable at harvest.
If you make a genuine logic fix to `Output/` during Phase A, commit it with
`validate.py commit --kind migration-fix` before Step 6 `summary` (run_index is
built at summary time). Commit test-only/scaffolding edits with `--kind test-patch`.
Edits to `Validation/tests/` or `Validation/shared/` (the kit, mocks) are not tracked in
the conv-root git and need no commit. Leave a clean working tree when you finish.

## When to stop Phase A

For each entrypoint: if local execution succeeds and the snapshot looks
trustworthy, record the baseline; otherwise classify the failure and fix it.

`phase_a_skipped` is the Phase A analogue of `hard_stuck` — **rare, and never for
a schema/mock gap**. `TABLE_OR_VIEW_NOT_FOUND`, `COLUMN_NOT_FOUND`, missing
`mock_file`, `columns: []`, empty/all-null output, and declared-sink-empty failures are
always fixable by inline schema repair unless the sink is intentionally empty and
you record that rare exception with `allow_empty`.
Skip only for a construct the source runtime genuinely cannot execute (see below).

A missing baseline downgrades the entrypoint to `passed_no_baseline`, shipping with
no parity check. Phase B running later is a fallback, not a licence to abandon a
recoverable baseline — do not skip just because iterations piled up or a batch of
tables looks tedious.

## Environment differences and Phase A skip

Phase A runs the workload's **source** flavor — `source_runtime` in
`_meta.json` selects the harness backend:

- **`spark`** — local PySpark + Delta (default when the file is plain OSS Spark).
- **`databricks`** — Databricks Connect / dbx sandbox when the notebook uses
  `dbutils`, Databricks-only APIs, or the workload was authored for a Databricks
  cluster.

**Default: patch everything fixable; skip only what the source runtime cannot
execute.** Namespace rebinding, connector reads, external I/O, `saveAsTable`, and
harness gaps are patches — not skips. A skip means the construct is genuinely
unsupported on the configured source runtime (parse-time dialect SQL, import-time
Databricks-only symbols, `._jdf` under Spark Connect).

**Patch first (one pytest attempt minimum):**

- 3-part `CATALOG.SCHEMA.TABLE` names and hardcoded `DB.SCHEMA` prefixes in SQL
  strings → namespace-rebind patch (`SCOS_DATABASE_NAME` + `SCOS_OUTPUT_SCHEMA`).
- Connector reads (`format("snowflake"|"jdbc"|"redshift")`) → per-side `patch-add`.
- External I/O (cloud paths, widgets, secrets, file-lister stubs) → `SCOS_INPUT_*` /
  `SCOS_SINK_*` / inline literals.
- `saveAsTable` / connector writes → trial schema or declared sink — not a skip.

**Skip is a LAST RESORT.** `phase_a_skipped` means there will be NO baseline to
compare Phase B against, so the entrypoint ships with weaker verification — only
use it when the construct genuinely cannot execute on the configured source
runtime, confirmed by patches + at least one pytest attempt:

- Parse-time dialect SQL the source runtime rejects (`::` casts, `MERGE INTO`,
  `QUALIFY`, `PIVOT`, `LATERAL VIEW`, …) embedded in `.sql` templates or inline SQL.
- Import-time Databricks-only symbols (`parse_json`, `VariantType`, …).
- `._jdf` access under Spark Connect.

**Never a skip:**
- **Missing / unmocked source tables** (`TABLE_OR_VIEW_NOT_FOUND`,
  `COLUMN_NOT_FOUND`) — inline schema repair, regardless of table count (see *When
  to stop Phase A*).
- **Connector reads** (`format("snowflake"/"jdbc"/"redshift")…load()`) and other
  external I/O are plumbing — patch them (source-side `spark.table(...)` redirect;
  see the table above). Local PySpark has no connector, but the read still runs
  against the seeded mock once patched.
- **A pure DDL/config entrypoint with no declared sink** — it is NOT skipped; it
  runs, and a clean run (no error) is its baseline (the harness accepts zero sinks).

When skipping, record `phase_a_skipped` with the specific construct named in `--reason`:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  record-trial-status --conv-root $CONVERSION_ROOT --trial-id <id> \
  --status phase_a_skipped --reason "QUALIFY clause in rank.sql — unsupported in local PySpark"
```

Phase B still runs on real SCOS → `passed_no_baseline` on success. Do not rewrite
dialect SQL into OSS equivalents or stub `.sql` files.

## Record keeping — MANDATORY

**`run-tests` owns the iteration counter and automatically calls `record-iter`
for every trial that ran (based on the pytest JSON report). Never pass an
iteration number to any command, and do NOT call `record-iter` manually after
a `run-tests` invocation.** Finishing a trial without a recorded iter leaves
`state.json` empty and forces the orchestrator to backfill — this is a runner
failure. Since `run-tests` handles it, you just need to make sure you call
`run-tests` (not raw pytest) for every pytest invocation.

`phase_a_skipped` is the only status Phase A records via `record-trial-status` — it is a transient signal (not a terminal verdict) indicating no local baseline was produced. Phase B resolves the final terminal verdict (`passed`, `passed_no_baseline`, `hard_stuck`) after SCOS comparison.

If Phase B later changes shared schema/data for one entrypoint enough that its
source baseline is no longer representative, refresh just that baseline with a
focused rerun:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  run-tests --conv-root $CONVERSION_ROOT --phase a --trial-id <id>
```

After applying any patch to tests/, Output/, or shared/, record it via:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  record-patch --conv-root $CONVERSION_ROOT --trial-id <id> --phase phase_a \
  --file <path-relative-to-conv-root> --reason "<short>"
```

All `record-*` calls (and the harness's capture/diff hooks) append events to `Validation/events.jsonl` for downstream timeline reconstruction. You don't need to do anything special — events are emitted automatically.

## Report back

Summarize:

- which entrypoints produced baselines
- which did not
- what harness changes were made
- what should be carried into Phase B for human review
