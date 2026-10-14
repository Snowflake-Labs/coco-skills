---
name: patch-author
description: Author the patch blueprint that rewrites non-Spark I/O (cloud reads/writes, secrets, widgets, external deps) into native Spark + env-var indirection, or deletes it. Each patch is smoke-tested and committed via `validate.py patch-add`.
---

# Patch Author

This agent prepares the patch blueprint — the small, auditable set of
search/replace edits that make the workload runnable under the harness without
any shims or mock filesystems. Non-Spark I/O (cloud reads/writes, secrets,
widgets, external deps) is never shimmed or mocked — it is **rewritten to native
Spark** (reading/writing `os.environ["SCOS_INPUT_<id>"]` /
`["SCOS_SINK_<id>"]`), turned into an **inline literal** (secrets, widgets), or
**deleted** (logging/telemetry side effects).

Patches are **validation plumbing only** — namespace rebinds, path redirects, dead
import removal. **Do not rewrite SQL dialect** (`QUALIFY`, `DATEADD`, etc.) or
other SCOS migration logic here; Phase B's migration fixer commits those as
`[MIGRATION-FIX]` on `Output/`. Patch a `.sql` path only when the harness must
redirect where the workload loads a template (same rule as any other non-Spark
I/O).

**Prior learnings:** Before your first step, read
`$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md` into your context.
It contains patch patterns, schema quirks, and dialect issues discovered by
workers that completed before you. Apply any relevant patterns rather than
rediscovering them.

## Workflow — work ONE pattern at a time

Mirror the data-synthesizer: get a worklist, then fix one item, apply it, move
on. **Do NOT read the whole batch, draft every patch, and submit one giant batch
at the end** — that is slow and produces sprawling blueprints. Find one pattern,
apply it fully via `patch-add`, then take the next.

### Step 1 — Auto-apply known patches + generate the worklist

Run the known-patches library once. It applies common, high-confidence rewrites
deterministically — widget-to-env rebinds (`dbutils.widgets.get` reads,
`dbutils.widgets.text/dropdown/...` declarations, ipywidgets parameter widgets),
`os.system` removals, `sys.path.insert`/`append` cleanup, `dbutils.notebook.exit`
rewrites, `.saveAsTable` env indirection — and writes an **investigation
worklist** for the residual I/O it cannot safely auto-fix.

```bash
# 1. Generate suggestions + worklist — auto-scoped to this batch's entrypoints
#    (writes known_patch_suggestions.json AND patch_investigation.json)
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  known-patches suggest --conv-root $CONVERSION_ROOT

# 2. Apply the auto-suggested patches atomically
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  patch-add --conv-root $CONVERSION_ROOT \
  --from-file $CONVERSION_ROOT/Validation/known_patch_suggestions.json
```

If `known_patch_suggestions.json` is empty or `patch-add` reports all entries
were deduped (already applied), proceed to the worklist.

### Step 2 — Work the investigation worklist, one pattern at a time

`Validation/patch_investigation.json` has `summary` (counts per category) and
`sites` — each `{category, relative_file, line, text, occurrences, hint}`. Sites
span each entrypoint's whole **import closure** (the entrypoint file plus its
transitive static imports and `%run` targets, recorded on `_meta.closure`), so
I/O in imported helper modules is flagged too — not just the entrypoint file.
These are candidates the auto-patcher could not safely fix; they *probably* need
a patch, but you confirm and author it. Categories: `cloud_read_write`,
`cloud_sdk`, `connector_read`, `namespace_read`, `sf_namespace_option`,
`file_open`, `external_dep`, `display`, and `scos_io_annotation` (I/O the
migrate skill's `spark_io_detect` recipe already flagged in `Output/` with a
`# SCOS: […] spark_io_detect:` marker — the highest-signal sites).

Loop literally:

1. Take the next site (or the group of same-category sites that share one rewrite).
2. Open its `relative_file` at `line`; confirm the exact rewrite from the source.
3. Author it and apply it **now** via a single `patch-add` — one `patch-add` per
   pattern. If the identical rewrite applies to 2+ files, use ONE glob entry
   (see the Collapsing section below).
4. Move to the next site. Do not accumulate a giant pending batch.

Look up the exact rewrite for each category in the pattern → recommended-patch
tables in the **Patch recipes** section below.
Treat known-patches as file-specific — if one file already has a call commented
out or absent, do not force a duplicate just to mirror a sibling.

### Step 3 — Sweep for what the scanner missed

The worklist covers each entrypoint's static import + `%run` closure, but is
still **not exhaustive** — regex misses dynamic/obfuscated I/O (f-string paths,
helper-wrapped reads, indirect writes), and the closure misses **dynamic imports**
(`importlib`, runtime `sys.path.append`, `__import__`) and **relative imports**
(`from . import x`). After working it, do one proactive pass over each batch
entrypoint and any helper it loads dynamically or via a relative import, for
non-Spark I/O the detectors didn't catch, same one-pattern-at-a-time discipline.

### Proactive plumbing checklist (before Phase A)

These are the highest-leverage patches — missing them causes late Phase B repair
loops or false `hard_stuck`. Apply during the initial pass, not after runners fail:

1. **Namespace rebind** — every config token and hardcoded `DB.SCHEMA` prefix
   used in `spark.table(...)`, `spark.sql("…")`, and `.sql` templates →
   `SCOS_DATABASE_NAME` / `SCOS_OUTPUT_SCHEMA`. Regex-rebind literal prefixes
   like `PROD_DB.PROD_SCHEMA.` in SQL strings; `.option("sfDatabase"/"sfSchema")`
   alone does **not** fix `spark.sql`/`spark.table` reads.
2. **Connector reads (per-side)** — `format("snowflake")` / `jdbc` / `redshift`:
   **`source`** block → full rewrite to
   `spark.table(f"{os.environ['SCOS_DATABASE_NAME']}.{os.environ['SCOS_OUTPUT_SCHEMA']}.TABLE")`;
   **`migrated`** block → keep the connector chain but rebind `sfDatabase`/`sfSchema`,
   or rewrite to `spark.table` when SCOS lacks the driver. Never leave the source
   side on `format("snowflake")…load()`.
3. **`saveAsTable` / connector writes** — SCOS `USE SCHEMA` can no-op; writes
   must land in the trial namespace. Redirect to `os.environ["SCOS_SINK_<ID>"]`
   for capture, or qualify the table to `SCOS_OUTPUT_SCHEMA`. Declare the sink in
   `schemas` with `access: "write"` (or `"readwrite"`).
4. **`entrypoint_kwargs`** — populate namespace tokens and config values like
   `DATABASE_NAME`, `SCHEMA_STAGING`, `output_schema`. Store them in `_meta.json`
   for guard-bypass and wiring, using their exact keys (e.g. `DATABASE_NAME`, not
   `SCOS_DATABASE_NAME`).
5. **Missing config modules** — when the source imports a config module only to obtain
   runtime parameters, patch those reads to `os.environ["<kwarg_key>"]`. The harness
   injects each `entrypoint_kwargs` entry as an env var under its **verbatim key name** —
   the stub must read the exact key name, no `SCOS_` prefix or case change.

The `patches_authored` milestone fires at the end of the full pass (auto +
worklist + sweep), not after any single step.

You are patching a **batch** — a subset of entrypoints. See the scope note before the Inputs section below.

## Inputs

- `Validation/shared/schemas/entrypoints/<id>/_meta.json` — declares entrypoint-level
  fields (`run_mode`, `source_runtime`, `joins`, etc.).
- `Validation/shared/schemas/entrypoints/<id>/tables/` — one `<KEY>.json` per table
  entry; each declares `access: read|write|readwrite` per table. Tables are dicts keyed
  by name (not arrays).
- `Validation/source/` — the original PySpark code (Phase A).
- `Output/` — the migrated SCOS code (Phase B).

## The model

The harness sets, per trial:

- `SCOS_INPUT_<ID>` — path to a **relational** file-category read table (local
  mock in Phase A; staged path in Phase B). `<ID>` is upper-snake from the table
  dict key (original-case alias also exported when needed).
- `SCOS_TEST_AUX_<NAME>` — path to a **non-relational** file table (`relational:
  false`; local mock in Phase A, staged path in Phase B). `<NAME>` is the
  uppercased last segment of the table dict key. `SCOS_INPUT_<NAME>` is set to
  the same value as an alias.
- `SCOS_SINK_<ID>` — a capture directory for a non-table write.

**Env-var cheat sheet.** Derive names only from table **dict keys** in
`entrypoints/<id>/tables/<KEY>.json` — not from `entrypoint_kwargs` placeholder strings.
Use `helpers.expected_env_vars(ep_config)` when unsure. Config blobs and SQL
templates belong in `tables` with `relational: false`, not in
`entrypoint_kwargs` alone. `entrypoint_kwargs` are a separate channel: the harness
exports each as an env var under its **verbatim key name** (read via
`os.environ["<kwarg_key>"]` — **not** `SCOS_KWARGS_*`, and never uppercased).

**Script entrypoints with `argparse`.** `runpy.run_path(..., run_name="__main__")`
runs `parse_args()` before patched `main()` bodies execute. Prefer letting the
harness inject `sys.argv` from `entrypoint_kwargs` + `cli_args` in the schema
(see `test_template.py` / `helpers.build_script_argv`). Only add a
`rewrite_main_block_env` patch when the workload needs custom logic the harness
cannot express.

Table-category tables are mocked by datagen, loaded into the trial schema, and
read via `spark.table` / SQL — they need **no** patch and **no** env var.

So your job is to find every place the workload touches something that is
**not** plain Spark-on-the-configured-session, and rewrite it — using the
pattern → recommended-patch tables in the **Patch recipes** section below.

Rules that govern all of them:

- **Table-form reads/writes already on the session** (`spark.table(...)`,
  `saveAsTable(...)`) need no patch **unless** the write targets a production
  schema or an external connector — then redirect/qualify to the trial namespace
  or a declared `SCOS_SINK_*`.
- **Namespace rebinds are plumbing, not dialect.** 3-part `CATALOG.SCHEMA.TABLE`
  names (from tokens or hardcoded literals) rebind to `SCOS_DATABASE_NAME` /
  `SCOS_OUTPUT_SCHEMA`. `.option("sfDatabase"/"sfSchema")` alone fixes only the
  migrated-side connector read — never a `spark.sql`/`spark.table` read.
- **Dead top-level imports** (`import pyodbc`/`boto3` with every use removed) →
  delete via patch (`replace: ""`); blueprint patches apply to `source/` too, so
  do not stub them in conftest.
- These rebinds/redirects are **`[TEST-PATCH]` validation plumbing** (not
  cherry-picked at harvest). Do **not** ask the Phase B migration-fixer to bake
  `SCOS_*` env reads into `Output/` — dialect fixes (`parse_json` → `PARSE_JSON`)
  are migration fixes; namespace/I/O wiring stays in patches.

## Patch recipes — pattern → recommended patch

The catalog to consult per worklist category. Each row is one I/O / namespace /
boilerplate pattern and the patch that resolves it. `SCOS_INPUT_<ID>` /
`SCOS_TEST_AUX_<NAME>` / `SCOS_SINK_<ID>` are the per-trial env vars the harness
exports; `SCOS_DATABASE_NAME` / `SCOS_OUTPUT_SCHEMA` are the trial namespace. All
rows are `[TEST-PATCH]` plumbing (not cherry-picked at harvest) unless noted.

### Cloud / file I/O

| Pattern | Recommended patch |
|---|---|
| `boto3.client('s3')...get_object(...)` read | `spark.read.<fmt>(os.environ["SCOS_INPUT_<ID>"])` |
| `spark.read...load("s3://…")` / `dbfs:/…` | `spark.read.<fmt>(os.environ["SCOS_INPUT_<ID>"])` |
| `open(path).read()` / config or SQL-template file (`relational: false`) | `open(os.environ["SCOS_TEST_AUX_<NAME>"]).read()` (or `SCOS_INPUT_<NAME>` alias) |
| **Non-Spark local-filesystem reader** on a relational file table (e.g. `pandas.read_csv`, `pyarrow`, a bare `open()` parse) | A bare path swap is NOT enough: in Phase B `SCOS_INPUT_<ID>` is a Snowflake **stage URI** (`@db.schema.stage/…`) that a filesystem reader can't open. Read it through Spark instead — `spark.read.<fmt>(os.environ["SCOS_INPUT_<ID>"])` (add `.toPandas()` if a pandas frame is needed downstream). |
| `df.write...save("s3://…")` (non-table) | `df.write...save(os.environ["SCOS_SINK_<ID>"])` |
| In-loop / parameterized read (`for t in tables: spark.read(f"…{t}…")`, table flagged `dynamic_read`) | Redirect to the ONE seeded mock: `spark.read.<fmt>(os.environ["SCOS_INPUT_<ID>"])` — the loop var drops out of the path. Do NOT seed one file per table unless the data-synthesizer split it into distinct entries. |
| `boto3...secretsmanager...get_secret_value` | Inline literal, e.g. `secret = "DUMMY"` |
| `dbutils.widgets.get("env")` | Inline literal, e.g. `env = "dev"` (usually auto-applied by known-patches) |
| `dbutils.fs.*`, `requests.*`, `pyodbc.*`, logging/telemetry | Delete (`replace: ""`) or rewrite to native Spark |
| File-lister stub (`list_files`/`dbutils.fs.ls` already returning `[]`) + downstream `spark.read.load(dynamic_path)` inside a `try/except` or `if files:` guard the read/write never reaches | **`rewrite_main_block_env`**: replace the whole guarded block with a direct `spark.read.format(...).load(os.environ["SCOS_INPUT_<ID>"])` and redirect the write to `os.environ["SCOS_SINK_<ID>"]`. The file-listing + `max(files, key=…)` logic is dead once the lister is stubbed. |
| Intermediate file re-read (workload writes parquet then re-reads it same run) | Patch BOTH: `SCOS_SINK_<ID>` on the write, `SCOS_INPUT_<ID>` on the re-read. Not snapshotted, but the pipeline breaks at the re-read without it. |
| `df.display()` / `display(df)` (Databricks viewers — `NameError` off-Databricks) | If the data-synthesizer flagged `display_only: true` and synthesized `display_<n>` sinks: `display(EXPR)` → `(EXPR).write.mode("overwrite").parquet(os.environ["SCOS_SINK_DISPLAY_<N>"])`; `df.display()` likewise. Else delete (`replace: ""`). `displayHTML(...)` → always delete. `df.show()` is valid Spark — leave it. |
| `# SCOS: [SPRKCNTPY…] spark_io_detect: …` annotation (migrate skill flagged this I/O) | Investigate the annotated read/write and apply the matching row above — it needs a stage/table or `SCOS_INPUT`/`SCOS_SINK` redirect. |

### Connector reads (per-side — the two copies differ)

| Pattern | Recommended patch |
|---|---|
| `spark.read.format("snowflake").option("query"/"dbtable", …).load()` | **`source`** block: rewrite to `spark.table(f"{os.environ['SCOS_DATABASE_NAME']}.{os.environ['SCOS_OUTPUT_SCHEMA']}.TABLE")` + inline `.withColumnRenamed`/`.select`/`.filter` to replay the query. **`migrated`** block: keep `format("snowflake")…load()` (SCOS runs on Snowflake) but rebind `sfDatabase`/`sfSchema` to the trial env. **⚠ Rebinding `sfDatabase`/`sfSchema` alone is the migrated-side fix ONLY** — the source side must replace the whole `format("snowflake")…load()` (incl. `.options(**sfOptions)`, else `NameError`). Never skip. |
| `spark.read.format("jdbc"/"redshift").option(…).load()` | Same `spark.table(...)` rewrite on the **source** side. **Check `Output/`:** SCOS has no JDBC/Redshift driver — if `Output/` still has the external read, rewrite it to `spark.table(...)` too (a `migrated` block). Source-only when `Output/` already uses `spark.table()`. |
| Connector read written as `spark.sql("… FROM PROD_DB.PROD_SCHEMA.T")` / `spark.table("PROD_DB.PROD_SCHEMA.T")` | Rebind the literal `PROD_DB.PROD_SCHEMA.` prefix (see Namespace rows). Do NOT use an `.option("sfDatabase"/"sfSchema")` rebind here — it matches only `format("snowflake")` chains and silently no-ops. |

### Namespace rebind (3-part names → trial catalog)

OSS Spark's session catalog is 2-level, so an unregistered leading token raises
`AnalysisException` in Phase A. This is **plumbing, not dialect** — patch it.

**`SCOS_OUTPUT_SCHEMA` is the whole schema qualifier — don't double-qualify.** On
the SCOS side it is already `DB.SCHEMA` (a bare schema in Phase A local), so
`os.environ["SCOS_OUTPUT_SCHEMA"]` used **directly** as the qualifier is correct on
both sides. Prepending `SCOS_DATABASE_NAME` on top of it builds an invalid 4-part
`DB.DB.SCHEMA.T` under SCOS — the `{SCOS_DATABASE_NAME}.{SCOS_OUTPUT_SCHEMA}` form
below is a **source-side / Phase-A-local** shape only.

| Pattern | Recommended patch |
|---|---|
| Catalog token in `f"{DATABASE_NAME}.{SCHEMA}.T"` / `.sql` templates | Set `DATABASE_NAME = os.environ["SCOS_DATABASE_NAME"]` in the patched `__main__` block (Phase-A-local only — under SCOS the schema token already carries the DB, so leave `DATABASE_NAME` empty there; see the note above). f-strings + `.sql` share the global. |
| Schema token(s) in 3-part names | Bind every schema qualifier to `os.environ["SCOS_OUTPUT_SCHEMA"]` (both phases). Hardcoding a prod schema leaves seeded tables in one namespace while the workload reads another. |
| Hardcoded literal `spark.table("PROD_DB.PROD_SCHEMA.T")` (no token) | Regex-rebind the literal prefix, e.g. `\bPROD_DB\.PROD_SCHEMA\.` → the trial namespace. |
| Dynamic name with prod prefix, e.g. `spark.table('PROD_DB.TABLE_%s' % part)` (`category: table`) | Rebind the `PROD_DB.` prefix to the trial env. NOT a `SCOS_INPUT_*` file redirect (those are `category: file` only). |
| `.option("sfDatabase", "PROD_DB")` | → `.option("sfDatabase", os.environ["SCOS_DATABASE_NAME"])` (migrated side; does NOT fix a source-side connector read). |
| `.option("sfSchema", "PROD_SCHEMA")` | → `.option("sfSchema", os.environ["SCOS_OUTPUT_SCHEMA"])`. |

### Workload-wide boilerplate (each ONE glob entry — usually auto-applied)

| Pattern (appears in most files) | Recommended patch |
|---|---|
| `# MAGIC %run ../config $arg="..."` notebook include | **Do NOT delete.** `%run` is a live include: the harness translates `# MAGIC %run <t>` → `_nb_run("<t>", globals())` and runs the target in-process. Only the path token is the target; trailing widget args (`$arg="..."`) are dropped and flagged `# NEEDS-REVIEW`. If the child branches on those args, set the equivalent global in a patch first. Ensure the target include exists on both sides. |
| `dbutils.notebook.exit(<args>)` | regex+replace_all → `sys.exit(0)` (or `""` if no `import sys`, common in `.ipynb`). |
| logger init (`logger_key = …; my_logger = Logger(...).new_logger()`) | regex+replace_all → `""` |
| `my_logger.<method>(...)` telemetry calls | regex+replace_all → `""` |
| `my_s3_utils = utils_s3.UtilsS3(path_prefix)` construction | regex+replace_all → `""` |
| dead top-level `import pyodbc` / `import boto3` (every use removed) | `replace: ""` — blueprint patches apply to `source/` too, so Phase A sees the fix. |

## Authoring patches — always via `validate.py patch-add`

NEVER hand-edit `Output/` or `Validation/source/` directly for I/O rewrites.
**Also never hand-edit `patch_blueprint.json` directly** — every entry must go
through `patch-add`, which *rejects* a search that matches 0 times. A patch typed
straight into the blueprint is never applied to disk and silently overstates
coverage (e.g. a `.option("sfDatabase", …)` rebind sitting in the blueprint while
the source still reads the prod qualifier). If `events.jsonl` shows far fewer
`patch_added` events than blueprint entries, the blueprint has drifted from the
applied source — re-apply each patch via `patch-add`.
Submit **one pattern per `patch-add` call** (per the workflow loop above) — a
pattern is one literal edit, or one glob/regex entry covering the same rewrite
across many files. Each call applies atomically and commits one `[TEST-PATCH]`.
Do not accumulate every pattern into a single end-of-batch submission:

> **Generate the batch file with a Python script using `json.dumps`, not by hand.**
> For any patch whose `search`/`replace` has backslash line-continuations (`\`),
> nested quotes, or multi-line `try/except` blocks, hand-escaping `\\` vs `\n` is
> error-prone and a frequent cause of "search not found". Build the patch dicts in
> Python and `json.dump` them to the batch file — the escaping is then correct by
> construction.

```json
{
  "patches": [
    {
      "id": "ingress_users",
      "relative_file": "src/jobs/users.py",
      "note": "s3 read -> native spark read via SCOS_INPUT env",
      "search": "df = spark.read.parquet(\"s3://acme/raw/users\")",
      "replace": "df = spark.read.parquet(os.environ[\"SCOS_INPUT_USERS\"])"
    },
    {
      "id": "drop_telemetry",
      "replace_all": true,
      "relative_file": "src/jobs/users.py",
      "note": "remove telemetry calls",
      "search": "        telemetry.emit(\"users_loaded\")\n",
      "replace": ""
    }
  ]
}
```

Each entry is keyed on **one `relative_file`** — the path relative to the project
root. The engine derives both physical copies from it: `Validation/source/<rel>`
(the Phase A PySpark copy) and `Output/<rel>` (the Phase B SCOS copy). You never
type `Validation/source/...` or `Output/...` prefixes. A top-level
`search`/`replace` is applied to **both** copies, which is the common case (the
migrated `Output/` file usually still contains the same line you're rewriting).

**When the two copies have drifted** (the exact `search` text differs between the
PySpark and SCOS versions), add a `source` and/or `migrated` sub-block with its
own `search`/`replace`. **The presence of a sub-block also selects which sides to
patch** — if you include only `migrated`, only the `Output/` copy is patched:

```json
{
  "id": "fix_users_read",
  "relative_file": "src/jobs/users.py",
  "source":   {"search": "<exact text in the PySpark file>", "replace": "..."},
  "migrated": {"search": "<exact text in the SCOS file>",    "replace": "..."}
}
```

Do not write `source` and `migrated` blocks with **identical** `search`/`replace`
— that is just the top-level form written twice. Use a top-level `search`/`replace`
instead. (If you do submit identical per-side blocks anyway, `patch-add` auto-folds
them into the compact top-level form when it stores the blueprint.)

To patch only one copy (e.g. a SCOS-only fix), include just that side's block:

```json
{ "id": "scos_only_fix", "relative_file": "src/jobs/users.py",
  "migrated": {"search": "...", "replace": "..."} }
```

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  patch-add --conv-root $CONVERSION_ROOT --from-file /tmp/patches.json
```

`patch-add` is the gatekeeper. It processes the batch **in order** against an
in-memory working copy (so two entries editing the same file stack correctly).
For each present side of each entry it:

1. checks the `search` matches **exactly once** (with `replace_all=false`),
2. checks the patched file still parses (`ast.parse`),

and only if **every** entry passes does it write the files, append them to
`Validation/shared/patch_blueprint.json`, and commit **both** the `Output/` and
`Validation/source/` sides as one `[TEST-PATCH]` commit (so `git revert`-ing that
commit cleanly undoes both sides). If any entry fails, **nothing** is written and
it tells you which entry/side failed and why — fix that entry and resubmit the batch.

(A single entry, or a bare `[...]` list, is also accepted — it's just a batch of
one. The `{"patches": [...]}` shape mirrors the blueprint file itself.)

### Rules

- **One logical change per `id`.** A single entry already covers both copies
  (source + migrated) of the *same* data asset via its `relative_file` — do not
  split one rewrite into two entries.
- **Prefer a top-level `search`/`replace`.** Because env-var indirection makes the
  replacement identical on Phase A / Phase B / dbx, the same line usually exists
  in both copies, so one top-level pair patches both. Only drop to per-side
  `source`/`migrated` blocks when the `search` text has drifted between copies.
- **Ensure `import os`** exists in the file if your `replace` uses
  `os.environ`. If it's missing, add a separate small patch that inserts it.
- **Disambiguate identical lines** by widening `search` with surrounding
  context until it's unique, or set `replace_all: true` when you genuinely want
  to rewrite every occurrence (e.g. deleting all logging calls). When the SAME
  expression appears more than once (e.g. `spark.read.parquet(f'{s3_hist}')` read
  twice), include the **following line(s)** in `search` to pin the right
  occurrence — the next line is usually what differs (a `.select(...)`, a
  backslash line-continuation, or a distinct assignment), so add it verbatim
  (escaping `\n` / `\\` exactly).
- **Removals**: set `replace` to `""`. If deleting a statement would leave an
  empty block, the compile check will reject it — replace with `pass` instead.
- If `patch-add` reports "search not found", re-open the file and copy the exact
  text (the migrated file may differ from what you expected).
- **Env-var ids must exist in `schemas`.** Whenever you introduce a
   `SCOS_SINK_<ID>` or `SCOS_INPUT_<ID>` env-var redirect for a target not
   already declared in `schemas`, ensure the corresponding id is
   registered in the entrypoint's `tables`. The
   harness only generates env vars for declared ids — an undeclared id will
   `KeyError` at runtime. The env-var suffix is **canonical upper-snake** from
   the table dict key: non-alphanumerics → `_`, then **trailing `_`
   stripped** (table key `data_` → `SCOS_INPUT_DATA`, not
  `SCOS_INPUT_DATA_`). `patch-add` warns on trailing-underscore env refs and
  ids not present in schemas.

> ⚠ **Batch scope:** Scope all patches to this batch's entrypoint source files.
> Derive any `relative_file` glob from the batch entrypoint paths in
> `Validation/shared/schemas/manifest.json`, not from the full repo tree.
> A repo-wide glob (e.g. `**/*.py`) will match files outside your batch —
> including files with pre-existing syntax errors that fail the `ast.parse` gate.
> Use a scoped glob (e.g. `notebooks/<subdir>/**/*.py`) when entries are identical
> across files; use per-file entries for non-identical patches.
>
> ⚠ **Shared-prefix globs also match out-of-batch siblings.** A prefix glob
> (`Foo*.py`) can match files not in your batch, pulling them into your patch
> (cherry-pick conflicts at harvest) and tripping the `ast.parse` gate on files you
> don't own. When batch entrypoints only share a name prefix with non-batch files,
> use per-file entries instead.

## Collapsing repeated patches, globs & notebooks

When a worklist pattern repeats across 2+ files, collapse it into ONE glob
`patch-add` entry rather than N per-file entries — the single biggest lever for
keeping the blueprint small. The boilerplate rows in the Patch recipes section
above each become one such glob.

**Regex / glob mechanics:**

- **`"regex": true`** — `search` is a Python regex (default flags — no
  DOTALL/MULTILINE; opt in via inline `(?s)`/`(?m)`). `replace` supports
  backreferences (`\1`, `\g<name>`). The uniqueness + `ast.parse` gates still apply.
- **Glob `relative_file`** (`*`, `?`, `[`) — expands against each side's prefix
  (`Validation/source/` and `Output/`). Zero-match files are skipped; the entry
  fails only if NO file matches. Glob entries must use top-level `search`/`replace`
  (no per-side blocks); use per-file entries when the two copies need different
  rewrites. Scope globs to this batch's dirs (see the Batch scope note above).
- **`Output/` lines may carry trailing `# SCOS: <note>`** comments from the
  migration tool. Don't anchor `search` at `)`/`"`; use `[^\n]*` to tolerate
  trailing content on cross-side patches.
- **f-string pitfall (notebooks):** don't nest the same quote inside an f-string
  expr — read into a local first (`_s = os.environ["SCOS_OUTPUT_SCHEMA"]`).

**`.ipynb` notebooks:** `search` matches per code cell but uniqueness holds across
the whole notebook; no cross-cell spans. The compile gate runs `ast.parse` via
`notebook_source`; `%sql`/`%run` cells translate at runtime — patch the underlying
Python.

**Recovering from an over-broad patch:** each `patch-add` is one `[TEST-PATCH]`
commit staging both sides, so `git revert <sha>` (or `git reset --hard HEAD~1` if
it's the tip) restores both; delete the entry from `patch_blueprint.json` and
resubmit a tighter one. Dry-run a broad regex on a scratch copy first
(`python -c "import re; print(re.sub(PAT, REPL, open(F).read()))"`).

## Synthetic input data

For each file-category table with read access, ensure its mock data file exists
under `Validation/shared/mock_data/<ep_id>/<mock_file>` (the data-synthesizer + datagen
produce these). Relational file tables use `SCOS_INPUT_<ID>`; non-relational
tables use `SCOS_TEST_AUX_<NAME>`. Both point at the local mock in Phase A and
the staged copy in Phase B. You do not create `mock_s3/` / `mock_dbfs/` trees
anymore — there is a single per-table data file.

## Iterating later

You do the first pass, but you are not the only author. If a runner hits a
missed dependency during Phase A or Phase B, it adds another patch with the same
`patch-add` command. Keep the blueprint append-only and each entry minimal.

**Do not re-author a patch that is already in the blueprint.** Each new entry
should be a *genuinely different* change. `patch-add` auto-dedupes by content:
an entry whose `relative_file` + `search` + `replace` (per side, plus
`replace_all`) matches one already in the blueprint — or repeats earlier in the
same batch — is **skipped** (reported as `(deduped)`, not re-applied, not
re-committed), regardless of its `id`. So giving the same rewrite a new `id`
does **not** get it in twice. Before adding, check
`Validation/shared/patch_blueprint.json`; only submit entries that change
something not already covered. If you need to *change* an existing patch, submit
it with the **same `id`** and a **different** `search`/`replace` — that replaces
the prior entry rather than duplicating it.

## Record

When the initial pass is complete:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  record-milestone --conv-root $CONVERSION_ROOT --milestone patches_authored
```
