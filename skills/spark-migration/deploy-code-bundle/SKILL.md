---
name: deploy-code-bundle
description: |
  Deploy a migrated Snowpark/SCOS project to Snowflake as a Code Bundle. Takes
  the migrated project (its .py entrypoint + supporting modules/config), reminds
  the user to fix flagged code, writes a code_bundle.yml spec, uploads the whole
  project to a Snowflake stage, and creates the Code Bundle from the stage — on a
  warehouse (default) or a compute pool. No .ipynb is involved.
  Triggers: deploy code bundle, deploy to code bundle, run as code bundle, create
  code bundle from migration output, execute code bundle, snow bundle.
parent_skill: spark-migration
---

# Deploy Code Bundle

> **Bundled sub-skill of `spark-migration`.** Loaded on-demand by the parent
> `spark-migration` skill via the Read tool (see its "Sub-skill Loading
> Convention") — it is **not** a registered top-level skill. Do not call
> `skill("deploy-code-bundle")`; if you reached this file outside a `spark-migration`
> flow, start at `spark-migration`.

Deploy a migrated project to a Snowflake **Code Bundle** — a packaged Python/Java
job that runs directly on a **warehouse** or a **compute pool (SPCS)** via an
entrypoint file (no notebook, no `.ipynb`):

0. **Remind to fix** — surface the migration's flagged issues so the user fixes
   their code before deploying.
0.5. **Verify namespace** — ensure the target database + schema exist (prompt to
   reuse an existing DB or create one).
0.6. **Compose the entrypoint** — detect the project's entry contract, draft a thin
   launcher (detect → draft → ask), and get human approval. A Code Bundle runs one
   file; a migrated CLI needs this launcher.
0.7. **Prepare spec** — write `code_bundle.yml` (prompt for the compute target;
   default **warehouse**).
1. **Upload** — `PUT` the whole migrated project (including `code_bundle.yml`) to
   a Snowflake stage.
2. **Create** — `CREATE CODE BUNDLE` from the staged project.
3. **Validate (OPT-IN)** — `EXECUTE CODE BUNDLE` end-to-end. **Only when the user
   explicitly asks to validate/run it.** A plain deploy ends at Step 2.

The core deploy (Steps 0–2, plus the 0.6 entrypoint and 0.7 spec) does **no**
conversion or parity check — the migrated code is a pure input; the Step 0.6
launcher is additive and changes no project logic. Step 3's execution is used
**only** on an explicit validate request; it never runs during a normal deploy.

## Prerequisites

- **Code Bundles is a limited-access (PrPr/PuPr) feature** — the target account
  must have it enabled. Preflight before Step 2 by probing the feature with a
  harmless `SHOW`:
  ```bash
  snow sql -q "SHOW CODE BUNDLES;" --connection <CONNECTION>
  ```
  If it errors as unknown/unsupported syntax, stop and tell the user the account
  is not enabled for Code Bundles (request enablement, e.g. the
  `ENABLE_CODE_BUNDLE_*` PrPr form) before continuing. An empty result set means
  the feature is enabled — proceed.
- A Snowflake connection (`snow` CLI or `snowflake.connector`; the scripts use the
  shared `sf_exec` executor which works with either).
- For a **compute_pool** target: an existing compute pool (and a query warehouse
  if the workload issues SQL).

## When to Load

Router dispatches here for deploy intents where the target is a **Code Bundle**
(rather than a Notebook). The migration skill has already produced the migrated
`.py` project.

## Arguments (from router context, or asked once when standalone)

| Parameter | Description |
|-----------|-------------|
| Project dir | Migrated project directory (entrypoint `.py` + supporting modules/config) |
| Entrypoint | The file to run, relative to the project root. Usually **generated in Step 0.6** (`run_bundle.py`); an existing runnable no-arg file may be used as-is |
| Database / Schema | Target for the stage + code bundle |
| Stage | Stage name (created if absent) |
| Compute target | `warehouse` (default) or `compute_pool` |
| Compute pool | Required when target is `compute_pool` |
| Warehouse | `query_warehouse` (compute_pool target; warehouse target uses session context) |
| Bundle name | Object name for the created code bundle |
| Connection | Snowflake connection name |

## Scripts

All steps run deterministic scripts via `uv run --project <SKILL_DIRECTORY> python <path>.py`.
Shared scripts (Steps 0 / 0.5 / 1) live in `<COMMON_SCRIPTS>` = `<SKILL_DIRECTORY>/../deploy-common/scripts`;
bundle-specific scripts (Steps 0.6 / 0.7 / 2 / 3) live in `<SKILL_DIRECTORY>/scripts/`.
**Load** `references/scripts-reference.md` for the full per-script table (location, step, behavior).

## Workflow

Prefix each step with a timestamp `[YYYY-MM-DD HH:MM:SS]` (from `date '+%Y-%m-%d %H:%M:%S'`).

### Step 0: Remind to Fix — BLOCKING

Collect the migration's flagged gaps:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <COMMON_SCRIPTS>/scan_migration_gaps.py \
  --project "<PROJECT_DIR>" --json
```

Present a short **fix-before-deploy checklist** from the output: SCOS `TODO`/`WARN`
markers (unsupported ops, external I/O like S3, `.save()` stage paths), `Issues.csv`
rows, unresolved `<DATABASE>`/`<SCHEMA>`/`<WAREHOUSE>`/`<ROLE>` placeholders, and packages that
may need staging. Then **STOP and ask**:

> "These items were flagged during migration. Deploy anyway, or fix first? (Deploy / I'll fix)"

Do not proceed on "I'll fix". If the scan finds nothing, note that and proceed.

### Step 0.5: Verify the target namespace — may prompt

The deploy scripts use fully-qualified `<DATABASE>.<SCHEMA>` names and the `snow`
CLI needs the database to exist, so check first:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <COMMON_SCRIPTS>/ensure_namespace.py \
  --database <DATABASE> --schema <SCHEMA> --connection <CONNECTION> --json
```

- Verdict `PASS` (both exist) → proceed to Step 0.6.
- Verdict `SKIPPED` (no connection) → report and stop.
- Verdict `MISSING` (exit 2) → **STOP and ask** via `ask_user_question` with two
  options:
  1. **Use an existing database/schema** — user provides a name; restart this
     step with the new `<DATABASE>`/`<SCHEMA>`.
  2. **Create `<DATABASE>.<SCHEMA>` for me** — re-run with `--create`:
     ```bash
     uv run --project <SKILL_DIRECTORY> \
       python <COMMON_SCRIPTS>/ensure_namespace.py \
       --database <DATABASE> --schema <SCHEMA> --connection <CONNECTION> --create --json
     ```
  Do not proceed until the namespace exists.

### Step 0.6: Compose the entrypoint (detect → draft → ask) — may prompt / BLOCKING approve

A Code Bundle runs a single file non-interactively; a migrated CLI (argparse args,
needs a session namespace, reads a local file) will not run as a bare `ENTRYPOINT`.
This step composes a thin **launcher** that reproduces the environment setup and
calls the project's **existing** entry unchanged.

**1. Detect** the entry contract:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/inspect_entrypoint.py \
  --project "<PROJECT_DIR>" --json
```

Read: `entrypoints` (+ argparse `args`/`main_call`), `source_roots`,
`config_candidates`, `reads_namespace`, `local_file_arg_hints`, `needs_wrapper`.

- If `needs_wrapper` is **false** and exactly **one** entrypoint exists → **skip
  generation**; set `<ENTRYPOINT>` to that file and go to Step 0.7.
- If `needs_wrapper` is **false** but there is **more than one** entrypoint → do NOT
  guess: **ask the user** (via `ask_user_question`) which `__main__` file is the target,
  set `<ENTRYPOINT>` to their choice, and go to Step 0.7.
- Otherwise continue.

**2. Ask** (only what can't be safely inferred) via `ask_user_question`:
- the entry callable if multiple `__main__` candidates (as `module:Callable.method`,
  e.g. `main:MainApplication.main`),
- the config path if multiple `config_candidates`,
- the session namespace (default = deploy `<DATABASE>`/`<SCHEMA>`),
- for each `local_file_arg_hints` dest: the **stage path** to localize to it (or "none").

**3. Draft + write** the launcher (generic parts filled automatically):

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/generate_entrypoint.py \
  --project "<PROJECT_DIR>" --out run_bundle.py \
  --entry-import "<module:Callable.method>" --source-root <SRC_ROOT> \
  --namespace-db <DATABASE> --namespace-schema <SCHEMA> \
  --arg <configDest>=<config/relative/path.json> \
  --stage-file "<@DB.SCH.STAGE/path/file>::<local_dir>::<fileArgDest>" \
  --json
```

**4. Approve — BLOCKING**: show the generated file and confirm before proceeding.
Never overwrite an existing entrypoint without `--force` + explicit consent. The
chosen filename becomes `<ENTRYPOINT>` for Step 1 (uploaded with the project) and
Step 3 (`--entrypoint`).

**No behavior change (no BCR)**: the launcher is additive — it edits no migrated
module, reproduces only environment setup (import order, namespace, `sys.path`,
stage→local file), and calls the existing entry with the argparse-equivalent args
(exact `dest` names from detection). Output parity is verified at Step 3.

### Step 0.7: Prepare the code_bundle.yml spec — may prompt

**Ask the compute target** via `ask_user_question` (default **warehouse**):
- **warehouse** — runs on a virtual warehouse. `compute_options.runtime_version` is
  **required** (the script always emits it, default `3.10`); `query_warehouse` is
  **not** valid here — the executing session's warehouse is used (see Step 3).
- **compute_pool** — runs on SPCS; requires `--compute-pool` (and typically a
  `--query-warehouse` if the workload issues SQL).

Use `--curate-requirements` for SCOS projects: it writes `requirements-bundle.txt`
(drops `pyspark` — snowpark-connect vendors it — and adds `snowpark-connect`, which
is **not** auto-provided in the code-bundle runtime) and points the spec at it.

**Namespace note**: do NOT pass `SNOWFLAKE_DATABASE`/`SNOWFLAKE_SCHEMA` via `--env`
— the runtime rejects `SNOWFLAKE_`-prefixed env var names. Set the SCOS session
namespace inside the entrypoint (e.g. `session.sql("USE SCHEMA <db>.<schema>")`).

```bash
# warehouse (default):
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/prepare_code_bundle.py \
  --project "<PROJECT_DIR>" --compute-type warehouse --language python \
  --curate-requirements [--runtime-version <ver>] [--env KEY=VALUE ...] --json

# compute pool (SPCS):
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/prepare_code_bundle.py \
  --project "<PROJECT_DIR>" --compute-type compute_pool --language python \
  --compute-pool <COMPUTE_POOL> --query-warehouse <WAREHOUSE> \
  --curate-requirements [--runtime-version <ver>] [--env KEY=VALUE ...] --json
```

With `--curate-requirements` the requirements file is generated; otherwise
`requirements_file` is auto-detected (`requirements.txt`, else `pyproject.toml`).
The written `code_bundle.yml` lands in the project root so Step 1 uploads it with
the code. The **entrypoint is not** in this spec — it is passed at Step 3 execute.

### Step 1: Upload the migrated project to a stage — BLOCKING

**Approve before uploading (persistent state).** This step creates a stage and `PUT`s
files into the user's account. First run `upload_project.py` with the connection omitted
(or read the plan) to determine the **file count** and **target stage path**, then **ask**:

> "Ready to create stage `@<DATABASE>.<SCHEMA>.<STAGE>` and upload `<N>` files to
> `@<DATABASE>.<SCHEMA>.<STAGE>/<project>/`? (Yes/No)"

Only on "Yes", run:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <COMMON_SCRIPTS>/upload_project.py \
  --project "<PROJECT_DIR>" \
  --database <DATABASE> --schema <SCHEMA> --stage <STAGE> \
  --connection <CONNECTION> --json
```

Creates the stage (directory-enabled) and `PUT`s every project file to
`@<DATABASE>.<SCHEMA>.<STAGE>/<project>/<relpath>`, including `code_bundle.yml`.
It `LIST`s the stage to confirm the files landed.

**Post-check**: `code_bundle.yml`, the entrypoint, and supporting files appear under the
stage path. If the connection is unavailable, the script reports `SKIPPED` —
report the prepared upload plan and stop.

### Step 2: Create the Code Bundle — BLOCKING

**Preflight** the feature (see Prerequisites) and **ask user**: "Ready to create
the code bundle on Snowflake? (Yes/No)". If yes:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/create_code_bundle.py \
  --database <DATABASE> --schema <SCHEMA> --stage <STAGE> \
  --project-name <project> --bundle-name <BUNDLE_NAME> \
  [--comment "<text>"] --connection <CONNECTION> --json
```

Runs `CREATE OR REPLACE CODE BUNDLE <db.schema.name> FROM '@<stage>/<project>/'`,
self-verifies via `DESCRIBE CODE BUNDLE`, and reports the object name.

**Gate** — check the JSON `verdict`:
- `PASS` → `DESCRIBE CODE BUNDLE` succeeded; report the created object (`code_bundle`). Done.
- `SKIPPED` (connection unavailable) → report the prepared `CREATE CODE BUNDLE` plan and stop.
- `FAIL` (exit 2) → display the `error` and the failing `steps`, report that the code bundle
  was **not** created, and stop. Fix the cause (e.g. missing files on the stage, feature not
  enabled, permissions) and re-run Step 2.

### Step 3: Validate by executing the bundle — OPT-IN

**Run this step ONLY when the user explicitly asks to validate / run the deployed
bundle.** A normal deploy ends at Step 2 and must not execute anything. When the user
asks to validate, **Load** `references/execute-code-bundle.md` for the full command,
`--warehouse` requirements, history lookup, and the SCOS scope boundary.

## Stopping Points

- Step 0: after the fix checklist — confirm before deploying.
- Step 0.5: if the namespace is `MISSING` — confirm reuse-vs-create before proceeding.
- Step 0.6: after drafting the entrypoint — **BLOCKING**: show the generated launcher and confirm before proceeding (never overwrite an existing entrypoint without asking).
- Step 0.7: confirm the compute target (default warehouse) before writing the spec.
- Step 1: before uploading — **BLOCKING**: confirm the file count and target stage path (`CREATE STAGE` + `PUT` change account state).
- Step 2: before `CREATE CODE BUNDLE` — confirm (and preflight feature enablement).
- Step 3 runs only on an explicit validate request.

## Success Criteria & Final Summary

Success = the whole project is on the stage (`code_bundle.yml` + entrypoint + supporting
files), `CREATE CODE BUNDLE` succeeded and `DESCRIBE CODE BUNDLE` returns the object (or
`SKIPPED` when no connection), and — if opt-in Step 3 ran — `execute_code_bundle.py` returned `PASS`.

Close with a timestamp `[YYYY-MM-DD HH:MM:SS]` and report the created bundle
(`db.schema.name`), the compute target (warehouse/compute_pool), the stage path
(`@<db>.<schema>.<stage>/<project>/…`), and any Step 0 items deployed with unresolved gaps.
