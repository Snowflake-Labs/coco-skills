# Data Synthesizer

Loaded by `agents/batch-runner.md`. The worktree's `Validation/shared/schemas`
is already mined and pruned to this batch's entrypoints. This agent runs
headless: it completes the remaining schema work, generates mocks, verifies
them, resolves or dismisses warnings, and returns only after the final
`datagen.py` exits `0`, reports `"ok": true`, and leaves no
unresolved warnings.

Before Step 1, read
`$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md` into your context and
apply any relevant patterns. Do not mine again, do not re-select entrypoints,
and never prompt the user.

## Exit Gate

Do not finish or return to the worker until the last
`$RUN/datagen.py $SCHEMAS_DIR $MOCK_DATA_DIR` exits `0`, prints
`"ok": true`, and all warnings from that final state are resolved or
explicitly dismissed.

## Inputs

- `CONVERSION_ROOT`
- `SKILL_DIRECTORY`

Derived paths:

- `VALIDATION_ROOT = <CONVERSION_ROOT>/Validation`
- `SOURCE_ROOT = Validation/source`
- `MIGRATED_ROOT = <CONVERSION_ROOT>/Output`
- `SHARED_DIR = Validation/shared`
- `MOCK_DATA_DIR = Validation/shared/mock_data`
- `SCHEMAS_DIR = Validation/shared/schemas`

Runner shorthand:

```bash
RUN="uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts"
```

Use `$RUN/<script>.py` throughout. Never invoke the skill scripts with bare
`python`.

## Preconditions

The orchestrator already prepared the batch:

- `SCHEMAS_DIR` contains `manifest.json`, `entrypoints/<id>/`, and optionally
  `sql_files.json`, scoped to this batch only.
- Each entrypoint may still contain `llm_todo`, `columns: []`, guessed names,
  missing kwargs, or other mined gaps.
- The miner already did the broad discovery; your job is to close the remaining
  gaps, not to re-trace whole pipelines.

## Step 1 — Repair Units

**Hard rule: fix one unit, run datagen, then and only then move to the next unit.**
Do not read ahead into other tables. Do not plan the whole batch. Do not batch
multiple unrelated fixes before a datagen run. The loop below is not a suggestion —
follow it literally every iteration.

Work strictly from `datagen.py` output. Do not build a queue. Do not try to
fix the whole batch at once. Read the current `problems` output (a dict keyed by
table/entrypoint whose values are lists of problem strings), take the first
problem string, map it to one repair unit, fix that unit, run datagen, and check
the output again. Stay on that same unit until its problems disappear. Then restart
from the new first problem.

### Repair unit definition

A repair unit is the smallest schema-edit scope that can make the current first
problem disappear. You may edit only one unrelated repair unit between datagen
runs. If multiple current problems map to the same unit, fix all of them before
the next datagen run.

Repair unit types:

1. **Table unit** — one `entrypoints/<id>/tables/<KEY>.json`
2. **Entrypoint-meta unit** — one `entrypoints/<id>/_meta.json`
3. **Join unit** — one join-overlap problem fixed in `entrypoints/<id>/_meta.json`
4. **SQL-row unit** — one `sql_files.json` row plus the entrypoint table files it
   must merge into

Not valid repair units:

- the entire batch
- “all tables with the same shape”
- a one-off Python rewrite script that bulk-edits unrelated files

### Problem-to-unit mapping

Map the first problem string (a value in `problems`) to a repair unit with these rules:

1. `sql_files[...]: ...` → SQL-row unit for that `path`
2. `<ep>.tables.<table>: ...` → table unit
3. `<ep>/<table>: ...` or `<ep>/<table>.<col>: ...` → table unit
4. `<ep>: entrypoint ... declares colliding table names ...` → delete or rename
   the duplicate `tables/<KEY>.json` (the miner declared the same physical table
   twice, usually once bare and once schema-qualified). Not a `_meta.json` edit.
   The whole entrypoint is withheld until the names differ by more than case.
5. Any other `<ep>: ...` schema / required-field problem → entrypoint-meta unit

An empty-join-overlap **warning** maps to a join unit in that entrypoint's
`_meta.json` (see Warning handling).

After you map the first problem, scan the rest of the current `problems` output
strings and collect only the other problems that map to that same unit. Do not pull in
problems from any other unit.

### Exact repair loop

Run this literally:

1. Run `$RUN/datagen.py $SCHEMAS_DIR $MOCK_DATA_DIR`.
2. If `problems` is null (no blocking failures), leave the repair loop and run the missing-I/O pass.
3. Otherwise, take the first problem string from `problems` (walk its per-table lists).
4. Map it to exactly one repair unit using the rules above.
5. Collect every other current problem that maps to that same unit.
6. Read only the schema files and source locations needed for that unit.
7. Edit only that unit.
8. Run `$RUN/datagen.py $SCHEMAS_DIR $MOCK_DATA_DIR` and check the output.
9. If any problem for that same unit remains, keep working that unit.
10. If no problem for that unit remains, restart from the new first problem.

Always run datagen after a repair-unit edit. The hash mechanism makes cheap
re-runs safe, even when the edit did not affect generated mocks.
An intermediate `datagen.py` run may exit non-zero while other repair
units are still incomplete; that is expected mid-loop. `datagen`'s `"ok": true`
is the only completion gate.

**Counts are not repair units.** If `problems` holds 60 `llm_todo` strings across
15 tables, fix one table, run datagen, then move to the next. Do not read all 15
source files, and do not draft fixes for all 60 problems, before the first
datagen run.

### Missing-I/O pass

After the repair loop reaches `problems == null`, do one proactive pass over each
batch entrypoint and any imported helper modules it relies on. The goal is to
catch real inputs or outputs that `schema_mine.py` never declared, because
datagen can only complain about tables that already exist in `schemas/`.

For `.ipynb` entrypoints the miner translates `%%sql`/`%sql` cell bodies to
`spark.sql(...)` before mining, so table references inside SQL magic cells are
picked up into lineage automatically. Data-carrying `%fs`/`%sh`/`%%bash` magics
are neutralized with a `# NEEDS-REVIEW` marker — scan those for reads/writes the
miner could not see.

Keep this pass simple:

1. Scan each entrypoint once for obvious unmined I/O.
2. Add any real missing read inputs or write targets to the right repair unit.
3. Run datagen once.
4. If new `problems` appear, go back to the normal repair loop.
5. If `problems` is still null, continue to warning handling.

Do not turn this into a second full analysis pass. Only add I/O when the source
clearly depends on it at runtime.
Before adding anything, compare the candidate call against tables already
declared for that entrypoint. Repeated loads of the same input, helper wrappers
around an existing table, or dead side effects do not justify a new table row.
If the code path is only patch plumbing, leave it for the patch-author.

### Source-reading discipline

Read source only when the current repair unit truly needs it:

- For `columns: []` or `llm_todo: open column set`, open the file at
  `defined_at:line` and pull only the columns visible in nearby
  `.select()/.selectExpr()/.withColumn()/.filter()/.groupBy()` calls.
- Do not re-trace transitive joins, renames, or full downstream pipelines.
- Unknown columns can be left off; Phase B's inline schema-repair loop catches
  what runtime proves missing.

**Make the edit as soon as you have enough information.** Do not keep reading
more files looking for certainty — incomplete column sets are fine; Phase B
will surface what is missing at runtime. If you have read the source for a
unit and can see the relevant columns or path, write the edit now.

### Common fixes by unit

**Table units**

- `llm_todo` for runtime-variable sink / runtime table name:
  keep the placeholder key, set `original_path` to the templated source form,
  set `access` to `write` or `readwrite`, then delete the todo.
- `open column set` / `columns: []`:
  add the visible source columns only; do not chase downstream lineage.
- Distinct-value columns:
  set `"unique": true` on a column and datagen emits all-distinct values for it.
  Use it for primary-key-like columns, or for a window/dedup `orderBy` column so a
  "keep one row per group" step breaks ties identically on SCOS and Spark.
  Note: `"unique"` is ignored on a column that is also a shared join key (datagen
  keeps its pooled values so joins still overlap) — pick a non-join column to make
  unique.
- Type upgrades:
  replace guessed `string` with the real type when the source shows it clearly
  (cast, `StructType`, numeric ops, `explode`, `collect_list`, etc.). If a read
  column feeds `explode`/`flatten`, or is built by `collect_list`/`collect_set`
  of a struct, it is usually `array<struct<...>>`, not `string`.
- Name recovery:
  fix `original_path` when the mined name is a placeholder, `%`-format,
  `.format()` expansion, dynamic path, or hardcoded production identifier. For
  `dynamic_read`, keep one entry for shared-schema fan-in (`union`) and split
  by `fanout.value` only when the per-value DataFrames have distinct shapes that
  later get joined.
- Schema-only lookups are not read tables:
  delete entries used only for `.schema`.
- Non-relational file/config tables:
  fill `document_schema`, or delete the entry if it is a false positive.
- Helper-referenced columns:
  if a cleaning/helper function touches several tables and the miner missed some
  referenced columns, add the union of the helper's referenced columns to each
  affected table.
- Write-only schemas:
  fill columns for write tables too, and remove Snowflake-folded duplicates such
  as `ITEM` + `item`.
- Intermediate tables with both read and write entries:
  if the same table appears with `access: "read"` and `access: "write"` (or
  `"readwrite"`) and the column sets differ, align them — the read entry must
  include at least the columns the workload projects from it, and the write
  entry must include at least the columns the workload writes. Merge into a
  single `"readwrite"` entry with the union of columns when the table is both
  produced and consumed within the same entrypoint.
- Invocation facts that belong on the table:
  `reader_options`, `values`, `original_path`, source-shape
  connector columns, and any file-format facts belong in the table file. Do NOT
  put `values` on timestamp/date columns — the verifier can't enum-check temporal
  `values`; constrain the range via `entrypoint_kwargs` date bounds instead.
  `reader_options.sep` belongs here for non-default CSV delimiters, and
  `reader_options.header` must be `false` when the workload reads CSV without a
  header. For connector/JDBC reads, declare the physical source columns, not
  only projected aliases; include columns used in `WHERE`/`JOIN`/`ON`/`GROUP BY`.
  For hardcoded production qualifiers, keep `original_path` accurate so the
  patch-author can rebind it later.

**Entrypoint-meta units**

- `source_runtime` must be `"databricks"` or `"spark"` when you finish.
- `entrypoint_kwargs` goes in `_meta.json`; the harness injects each entry as an
  env var under its **verbatim key name** (read via `os.environ["<key>"]` — no
  `SCOS_KWARGS_` prefix, no case change) for all run modes. When the source imports a missing config
  module only to obtain runtime parameters, the stub must read the exact `entrypoint_kwargs` key name.
- Use `entrypoint_kwargs` for namespace tokens and config globals such as
  `DATABASE_NAME`, `SCHEMA_STAGING`, and `output_schema`, and choose values that
  bypass top-of-file early-exit guards.
- If the source imports a missing helper/config module only to obtain runtime
  parameters, treat those names as `entrypoint_kwargs` facts, not as new
  tables.
- `cli_args` belongs in `_meta.json` when a script entrypoint parses args.
- Connector-category reads (`category: "connector"`, `format: "snowflake"`)
  still need relevant namespace facts such as `sfDatabase` / `sfSchema`.

**Join units**

- Fix join-overlap problems only by editing `_meta.json` `joins`.
- Use the exact JSON edge shape that verify suggests.
- Re-run datagen after every join edit; pooled values are regenerated from the
  join graph.
- When verify names multiple tables in one join problem, make sure every
  readable table named in that problem is actually connected in `joins`. Do not
  rely on write-only sinks or unrelated tables to bridge the pool.
- Star-pattern joins (one key shared across many tables) count as one join unit:
  add all required edges for that key in a single `_meta.json` edit, then run
  datagen once. Do not add one edge at a time and re-run datagen after each.
- Do not assume stale mocks just because a join edit did not help on the first
  try. First confirm the edge direction, column names, and participating tables.
  Normal datagen should pick up join edits. Use `--all` only if the join graph
  is now clearly correct and the same join problem still persists.
- `join_key: false` only dismisses warnings. It does not dismiss a real
  join-overlap problem once the column is in a pool.

**SQL-row units**

- Work one `sql_files[path]` row at a time.
- Link that SQL file to the entrypoint that executes it.
- Merge every table the row requires into the entrypoint schemas.
- For SQL reads, `access` must include `read`; write-only is wrong.
- Every merged table needs full columns; `columns: []` is never acceptable.
- Skip CTE names only.
- Clear the row's `llm_todo` when the row is fully merged.
- Always use the real `.sql` template. Never stub SQL to make Phase A pass.

### Missed tables and false positives

The miner misses some I/O shapes. While fixing the current unit, you may add or
delete tables when the source makes it obvious:

- Add missed read inputs such as helper-loaded configs, external files, dynamic
  `spark.sql(f"...")` reads, or imported helper-module inputs the job truly
  needs. Common examples: `boto3`, `requests`, `open()` on mounted paths,
  `smart_open`, `fsspec`, and `dbutils.fs`.
- Use `relational: false` + `document_schema` for config/document blobs and
  relational `columns` for tabular extracts. `document_schema` is a **shape
  shorthand**, not JSON Schema: `{"field": "<spark type>", "nested": {...},
  "arr": ["<spark type>"]}` — e.g.
  `{"env": "string", "retries": "int", "hosts": ["string"]}`. A JSON-Schema-style
  `{"type": "object", "properties": {...}}` is copied literally into the mock and
  produces a nonsense document that nothing flags.
- Add missed write targets with `access: "write"`, including connector/JDBC
  writes and `saveAsTable` targets even when there is no captured sink baseline.
- `boto3.put_object`, REST posts, DynamoDB writes, and Kafka writes are usually
  write-only side effects, not read tables. If they do not feed data into the
  computation, leave them for the patch blueprint instead of inventing a read
  source.
- Delete false positives such as enum/pivot-literal artifacts, duplicate rows,
  or fake document entries that are really `json.loads` on a DB column.

## Step 2 — Incremental Datagen

Run normal datagen after every repair-unit edit:

```bash
$RUN/datagen.py $SCHEMAS_DIR $MOCK_DATA_DIR
```

Datagen is hash-driven and incremental. It regenerates only tables whose schema
hash changed or whose mock is missing, and it annotates `mock_file` on the
matching table entries.

Do not wipe `mock_data` just because verify failed. Full regeneration is allowed
only when the current repair unit renamed or removed a table or entrypoint:

```bash
# only after rename/remove
rm -rf $MOCK_DATA_DIR
# or:
$RUN/datagen.py $SCHEMAS_DIR $MOCK_DATA_DIR --all
```

## Step 3 — Verify And Review

### 3a. Deterministic self-check

Run:

```bash
$RUN/datagen.py $SCHEMAS_DIR $MOCK_DATA_DIR
```

It returns:

```json
{"ok": true|false, "seeded": {...}, "skipped": {...}, "documents": {...}, "gated_tables": ["<ep>.<table>", ...] | null, "problems": {"<key>": ["<problem>", ...]} | null, "warnings": [...], "pool_warnings": [...]}
```

**If `ok` is false, stay in Step 1. Do not proceed.**

One datagen run checks:
- unresolved `llm_todo` items
- entrypoint schema validity, including a non-null `source_runtime`
- `sql_files` merge correctness into entrypoint schemas
- empty required columns
- duplicate Snowflake column names (case folding)
- generated mock content (parquet/CSV/JSON files are readable)
- declared-vs-actual column types in mocks
- nullability constraints in mocks
- enum domain coverage in mocks

An empty join overlap is a **warning**, not a problem: each mock is valid on its
own and the defect is in the relationship between them, so the data must not be
withheld from every table sharing the column. Resolve or dismiss it like any
other warning.

`problems` is the full blocking set — a dict keyed by `<ep>`, `<ep>.<table>` or
`sql_files[<path>]`, mapped to their problem strings. `ok` is false whenever
`problems` is non-null. Every table named in `gated_tables` got no data this run
and any stale mock of it was deleted, so `mock_data` always reflects the latest
run. datagen never edits or deletes your schema files. `complete` is **not** in
this output — it is derived from remaining todos and persisted to
`manifest.json`; do not hand-set it. `warnings` are not part of `ok`, but you
still must handle them before finishing.

### Warning handling

When `problems` is null, handle `warnings` before the LLM review. Process them
in listed order, using the same one-unit-at-a-time discipline:

1. Take the first warning.
2. Collect every current warning that can be resolved by the same edit.
3. If they require `joins` or shared `values`, resolve them in one edit, then
   run datagen.
4. If they are pure `join_key: false` dismissals, batch those dismissals in the
   affected table files, then run datagen. Do not skip the datagen run just for
   dismissals.

Use "resolve" for real `joins` / `values` fixes and "dismiss" only for
confirmed non-join-key warnings. Large star-pattern join warnings for one
entrypoint belong in one `_meta.json` edit, not one edge at a time.

Do not enter Step 3b while warnings remain unresolved or undismissed.

### 3b. LLM review with `--peek`

After `problems` is null and warnings are handled, inspect the generated mocks:

```bash
$RUN/datagen.py $MOCK_DATA_DIR/<ep_id>/<mock_file> --peek
```

Check:

- declared types match the workload's real expectations
- enum/categorical columns contain realistic allowed values
- literal filter domains are represented in mocks when the source uses small
  fixed sets such as `isin(...)`, `IN (...)`, or exact code comparisons
- join keys that must match really overlap
- sample values are plausible for the domain
- nullable columns still contain nulls and NOT NULL keys do not

Prefer systematic schema fixes plus another datagen run over hand-editing mock
files. Hand-edited mocks are allowed only for one-off value tweaks and will be
overwritten by a later datagen regenerate.

## Step 4 — Record Milestone

Only after the last `datagen` run returned `"ok": true` and no unresolved warnings
remain:

```bash
$RUN/validate.py \
  record-milestone --conv-root $CONVERSION_ROOT --milestone synth_deep
```

If you edited mocks in Step 3b, run one final verify before recording the
milestone.

## Step 5 — Delete `sql_files.json`

Delete `sql_files.json` only after all data generation, all SQL merges, the
final verify, and the milestone record are complete:

```bash
rm -f $SCHEMAS_DIR/sql_files.json
```

Do not delete it earlier; `datagen` checks the merged entrypoint schemas
against it while it exists.

## Schema Contract

Entrypoint JSON must validate against
`validate-pyspark-to-snowpark-connect/schemas/entrypoint.schema.json`.
Verify runs that schema first, so fix schema-path errors before debugging
mock-file or SQL-catalog details.

Key fields:

- `run_mode`: `"script"` or `"callable"`
- `source_runtime`: `"databricks"` or `"spark"`
- `tables`: dict keyed by name; each entry has `access`, `columns`, and optional
  `category`
- `mock_file`: filename relative to `mock_data/<ep_id>/`
- non-relational tables: `relational: false`, `format`, `document_schema`

## Self-check

Before finishing, confirm:

1. The last `datagen.py` run returned `"ok": true` and exit code `0`.
2. No warnings from the final datagen run remain unresolved or undismissed.
3. `complete: true` in `manifest.json` (derived by datagen, not set by hand).
4. Every batch entrypoint has `run_mode`, `import_roots`, and `entrypoint_kwargs`.
5. Every readable file table has a `mock_file` that exists on disk.
6. Every SQL read table is declared with `access` including `read` and full columns.
7. No `llm_todo` remains on batch entrypoints or SQL catalog rows.
8. Every batch entrypoint has `source_runtime` set to `"databricks"` or `"spark"`.
