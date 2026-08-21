# Data Synthesizer

Loaded by `agents/batch-runner.md` (and optionally the orchestrator for a
single-batch deep fill). **`Validation/shared/schemas/` is the source of truth**
(PySpark parity). The orchestrator already ran `schema_mine.py` and
`prepare-batches` pruned schemas to this batch. This agent runs headless: it
completes remaining schema work, generates mocks, verifies them, resolves or
dismisses warnings, and returns only after the final `datagen.py`
exits `0`, reports `"ok": true`, and leaves no unresolved warnings.

**Do not mine again, do not re-select entrypoints, and never prompt the user.**
Optional whole-run narrowing is orchestrator-only (`SKILL.md` Step 1.6 →
`scope-entrypoints`).

Before Step 1, read
`$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md` into your context and
apply any relevant patterns.

## Exit Gate

Do not finish until:

1. `$VALIDATOR_SCRIPTS/datagen.py $SCHEMAS_DIR $MOCK_DATA_DIR` exits `0`
   and prints `"ok": true`
2. Warnings are resolved or explicitly dismissed

`manifest.complete: true` alone is not enough.

`column_check` and the JVM `analysis.json` shim are refreshed automatically by
`scos_state.py prevalidate` (and by `column_check.py --conv-root` when you run
it). You do **not** need a separate `schemas-to-analysis` step at exit — keep
editing `schemas/` only.

## Inputs

- `CONVERSION_ROOT`
- `SKILL_DIRECTORY`
- `VALIDATOR_SCRIPTS` — `$SKILL_DIRECTORY/../validate-pyspark-to-snowpark-connect/scripts`

Derived paths:

- `VALIDATION_ROOT = <CONVERSION_ROOT>/Validation`
- `SOURCE_ROOT     = Validation/source`
- `MIGRATED_ROOT   = <CONVERSION_ROOT>/Output`
- `SHARED_DIR      = Validation/shared`
- `SCHEMAS_DIR     = Validation/shared/schemas`
- `MOCK_DATA_DIR   = Validation/shared/mock_data`
- `AUXILIARY_DIR   = Validation/shared/auxiliary`

```bash
RUN="uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py"
DG="uv run --project $SKILL_DIRECTORY/.. python $VALIDATOR_SCRIPTS"
```

## Preconditions

- `SCHEMAS_DIR` contains `manifest.json` and `entrypoints/<id>/` scoped to this
  batch (or the full run after Step 1 mine).
- Each entrypoint may still contain `llm_todo`, empty `columns`, guessed names,
  missing kwargs / `cli_args`, or other mined gaps.
- **Edit `schemas/` only** (`entrypoints/<id>/_meta.json` and
  `entrypoints/<id>/tables/<KEY>.json`). Do **not** hand-edit
  `Validation/shared/analysis.json` — it is a generated JVM shim. Prevalidate
  and `column_check --conv-root` regenerate it from `schemas/` automatically.

## Repair loop (PySpark parity)

**Hard rule: fix one unit, run datagen, then and only then move to the next unit.**

1. `$DG/datagen.py $SCHEMAS_DIR $MOCK_DATA_DIR`
2. If `problems` is null → missing-I/O pass → warnings → done
3. Else take the first problem string from `problems` (a dict keyed by `<ep>/<table>` or `<ep>`) → map to one repair unit → edit only that unit
4. `$DG/datagen.py $SCHEMAS_DIR $MOCK_DATA_DIR` and check output
5. Repeat

If `problems` contains `<ep>: entrypoint ... declares colliding table names`, delete or
rename the duplicate `tables/<KEY>.json` — the same physical table was declared twice by
the miner. Not a `_meta.json` edit.

### Repair unit types

1. **Table unit** — one `entrypoints/<id>/tables/<KEY>.json`
2. **Entrypoint-meta unit** — one `entrypoints/<id>/_meta.json`
   (includes Scala `entrypoint_class`, `entrypoint_method`, `cli_args`, joins)
3. **Join unit** — join edges in `_meta.json`

### Scala-specific meta fields (on `_meta.json`)

| Field | Required for |
|-------|----------------|
| `entrypoint_class` | JVM reflection (`TestTemplate`) — FQCN |
| `entrypoint_method` | default `main` |
| `cli_args` / `entrypoint_kwargs` | concrete non-stub values when Args/main needs them; the harness injects each key via `System.getProperty(key)` — the key name in `_meta.json` must match **verbatim** what the workload reads |
| `weight` | numeric (schema_mine sets this; do not use string labels) |

Optional (prevalidate / column_check run these for you):

```bash
$RUN/scos_state.py schemas-to-analysis --conv-root $CONVERSION_ROOT
$RUN/column_check.py --conv-root $CONVERSION_ROOT
```

---

## Schema fill notes (reference — edit `schemas/` only)

> Orchestrator Step 1 already ran `schema_mine.py` (jar → ast_facts → schemas/).
> Use the sections below as semantic guidance when filling `llm_todo` gaps
> inside `schemas/entrypoints/<id>/tables/*.json` and `_meta.json`.
>
> **Never hand-author or hand-edit `Validation/shared/analysis.json`.** It is a
> generated JVM shim (`schemas_to_analysis_shim` / `prevalidate`). If
> `schemas/manifest.json` is missing, re-run mining — do not invent a catalog:

```bash
$RUN/schema_mine.py --conv-root $CONVERSION_ROOT
```

**Never ask the user which entrypoints to validate.** Optional whole-run
narrowing is orchestrator-only (`SKILL.md` Step 1.6 → `scope-entrypoints`).
Workers never prompt.

### Survey / candidate discovery (historical — superseded)

The goals below were the old agent survey. Today `schema_mine` owns them.
If you still need to reason about candidates, read
`schemas/manifest.json` + `ast_facts.json` — do not write a new analysis file.

1. Entrypoint candidates / weights → `schemas/manifest.json` `entrypoints[]`
2. `source_roots[]` / `build_tool` → `schemas/scala_meta.json` (and manifest summary)
3. Unresolved migrate-skill issues → `schemas/scala_meta.json` `migration_issues[]`
4. Deep fill → edit per-EP `_meta.json` + `tables/*.json` under `schemas/`

### Entrypoint candidates

Treat a `.scala` file or directory as a candidate when it looks like a
workload entrypoint:

- `object X { def main(args: Array[String]): Unit = ... }` — standard
  Scala main object
- `object X extends App { ... }` — App trait entrypoint
- `object X extends DelayedInit` — scopt-style CLI driver
- Databricks `.scala` notebook markers (`// Databricks notebook source`,
  `// COMMAND ---------`)
- Databricks exported Python/Scala `.ipynb` notebooks with `"language":"scala"`
- Files containing `SparkSession.builder` or
  `SnowparkConnectSession.builder` at top level
- Job/pipeline-named paths: `*Pipeline.scala`, `*Job.scala`, `*Driver.scala`

> **Use the deterministic `analyze` command first.** Instead of eyeballing
> source for entrypoints/reads/writes, run the control-plane parser and reason
> over its facts:
>
> ```bash
> java -jar "$SKILL_DIRECTORY/harness-scala/control/target/scos-analyze.jar" \
>   analyze --source "$SOURCE_ROOT" --output "$SHARED_DIR/ast_facts.json"
> ```
>
> Immediately materialize the survey skeleton from those facts (do not hand-build
> `entrypoint_candidates[]` from scratch):
>
> ```bash
> uv run --project $SKILL_DIRECTORY/.. python \
>   $SKILL_DIRECTORY/scripts/ast_to_analysis.py --conv-root $CONVERSION_ROOT --mode survey
> ```
>
> (`analyze` is the only command still on the JVM — it needs a real Scala parser
> (Scalameta); there is no equivalent in Python. State/control lives in
> `$SKILL_DIRECTORY/scripts/scos_state.py`. Shared PySpark scripts at
> `$VALIDATOR_SCRIPTS` cover `datagen.py`, `cleanup.py`, and
> `harness/comparator.py`.)
>
> `ast_facts.json` lists, per file: `objects`/`classes`, `entrypoints`
> (objects/classes with a `main`/`run` method), `imports`,
> `spark_session_created`, `reads` (`parquet`/`csv`/`json`/`load`/`table`/...),
> `writes` (`save`/`saveAsTable`/`insertInto`/format terminals), `table_refs`,
> and `column_refs` (`col("x")` / `$"x"`, plus the string args of
> `select`/`groupBy`/`orderBy`/`sort`/`sortBy`/`drop`/`dropDuplicates`), and
> `write_helpers` (functions whose body writes a DataFrame, **including
> transitively** — a function that delegates to a writer, e.g.
> `writeToSchema → fullLoad → .write.saveAsTable`). Use
> these as ground truth for
> candidate discovery, external sources, and sink targets; reserve LLM
> judgement for *semantics* (which source matters, schema inference, mock data)
> rather than re-parsing Scala by hand. **A call to any `write_helpers` name from
> an entrypoint is a sink** — declare it in `sinks[]` even though the actual
> `.write` is several calls away (delegating data-mart entrypoints otherwise look
> like they have no write targets). `parse_ok=false` entries flag files the
> parser could not read (e.g. raw Databricks notebooks before flattening).

For each candidate, emit:

```json
{
  "id": "relative_path_slug",
  "path": "relative/path.scala",
  "kind": "scala_object | extends_app | notebook | sbt_project",
  "entry_kind": "entrypoint_main | entrypoint_utility | library | passthrough",
  "call": "com.example.MyObject::main",
  "rationale": "why this looks like an entrypoint"
}
```

Do not emit extra viability scoring or auto-selection metadata.

### Source roots and build tool

Detect the build tool by presence of:
- `build.sbt` → `sbt`
- `pom.xml` → `maven`
- `build.gradle` / `build.gradle.kts` → `gradle`
- None of the above → `unknown`

Discover the minimal source roots needed for the harness to compile the
workload JAR. Standard layouts:

- sbt: `src/main/scala`, `src/main/java`
- Maven: `src/main/scala`, `src/main/java`
- Gradle: `src/main/scala`, `src/main/java`
- Flat: `.` (single-file workloads)

Record `build_tool` and `source_roots[]` in `schemas/scala_meta.json`
(and keep `manifest.json` `summary` in sync). Prefer re-running
`schema_mine.py --conv-root …` over hand-authoring.

### Migration issues

If `$CONVERSION_ROOT/analysis.json` exists (migration **issue array**, not the
validation shim), copy unresolved items into
`schemas/scala_meta.json` → `migration_issues[]`.

### Survey output

Do **not** write `Validation/shared/analysis.json`. Mining already produced
`schemas/manifest.json` (+ `scala_meta.json`). If you ran a legacy survey
bootstrap, immediately convert with `schema_mine.py --conv-root …` and continue
editing only under `schemas/`.

Then record:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-milestone --conv-root $CONVERSION_ROOT --milestone synth_survey
```

### Promote all candidates (no user prompt)

Validate **all** discovered entrypoints by default. Orchestrator Step 1.6 /
`prepare-batches` owns scoping. If you must list ids for a tool, read them from
`schemas/manifest.json` — never invent a shortlist and never ask the user:

```bash
# All mined ids — do NOT ask the user; do NOT cap at ~10.
ALL_IDS=$(python3 -c "
import json, pathlib
m = json.loads(pathlib.Path('$CONVERSION_ROOT/Validation/shared/schemas/manifest.json').read_text())
print(','.join(e['id'] for e in (m.get('entrypoints') or []) if e.get('id')))
")
# Optional whole-run narrow is orchestrator-only (SKILL.md Step 1.6).
```

If the orchestrator already ran `scope-entrypoints`, `entrypoint_candidates` /
`entrypoints` are already the kept subset — promote that set (still no ask).

Then build the deep-analysis skeleton deterministically before LLM judgement:

```bash
uv run --project $SKILL_DIRECTORY/.. python \
  $SKILL_DIRECTORY/scripts/ast_to_analysis.py --conv-root $CONVERSION_ROOT --mode deep
```

Resolve only the `llm_todo` items the skeleton flagged (type confirmation,
`natural_keys`, ambiguous column attribution, non-relational document schemas).
Prefer Scalameta filter/join facts when present; regex-enriched `values` /
`join_key` from `ast_to_analysis` fill gaps so mocks exercise filters and joins.
Then continue straight into deep analysis in the same dispatch.

## Deep Analysis Mode

Run this mode after `schemas/manifest.json` lists entrypoints (all discovered
entrypoints, or the orchestrator-scoped subset).

Scope everything to those entrypoints and their reachable files.

### For each selected entrypoint

Edit under `schemas/entrypoints/<id>/`:

- `tables/*.json` — reads (`access: read`/`readwrite`) and writes (`access: write`)
- intermediate / shared handoff tables (typed columns; same EP or cross-EP)
- `_meta.json` — `cli_args` / `entrypoint_kwargs` with **concrete non-stub values**
  when the entrypoint reads `Args.*` / `main(Array[String])` (empty/`TODO`/`<placeholder>`
  values are prevalidate-blocking; fixing them does **not** require a JAR rebuild)
- `_meta.json` — `joins`, `widget_env_vars`, `notes`, patch-author hints as needed
- mock paths via each table’s `mock_file` under `Validation/shared/mock_data/<ep_id>/`

### Intermediate tables (declare → CREATE → seed)

Pipeline handoffs that one entrypoint writes and another (or the same) reads
**must** appear as typed tables under `schemas/` (write on the producer, read on
the consumer — `schema_mine` also copies producer sink columns onto consumer
reads). The harness folds write tables into sinks so `seedEntrypoint` pre-creates
empty tables (Phase A) and provision creates them in the golden schema (Phase B).
Missing
schema → `TABLE_OR_VIEW_NOT_FOUND` loops. `prevalidate` blocks when an
intermediate lacks columns or is absent from `schemas/` after `schema_mine`.

Handoff rows are empty by default after CREATE. If a later stage needs non-empty
intermediates, put the seed data in `mock_data/` / schema `values` for the
producing entrypoint (or an explicit mock for the table name) — do not rely on
`seed_sql` strings alone.

### `allow_empty` sinks

Only set `allow_empty: "<short reason>"` (or `allowEmpty`) on a sink when empty
output is **intentional** (e.g. a header-only export that is empty for this
fixture by design). The kit skips those sinks in critical-empty checks and
`schema_mine` passes the flag into `schemas/`.

**Never** use `allow_empty` to paper over:
- missing mocks / bad schemas
- unpatched excel / mongo / file I/O
- UDF `ClassNotFoundException` or other JVM UDF gaps on SCOS
- unreachable external connectors

For UDF or connector unavailability that is an accepted platform gap, declare
`expected_divergences` with `scope: "udf"` (or `serialization`) via
`scos_state.py document-divergence` / known-patches seeding — **not**
`allow_empty` on every sink. Blanket `allow_empty` makes Phase B capture zero
tables and leaves the trial **unverified**; that is not a pass.

### True no-sink entrypoints

`sinks: []` is valid **only** when Scalameta `ast_facts.json` also shows no
writes / write_helpers / unresolved_writes for the entrypoint path.
`prevalidate` and `run-phase-a` refuse a synthetic `no_sink_baseline` when AST
still shows writes — re-mine sinks instead. A confirmed no-sink clean run is
execution smoke only (nothing to row-compare).

### Key: sinks land in `schemas/` tables with `access: "write"`

Prefer declaring write targets as table files under
`entrypoints/<id>/tables/<KEY>.json` with `"access": "write"`. When regenerating
the analysis shim, both `sinks` and `external_sinks` are populated for dual
consumers (`column_check`, JVM `AnalysisJson`). Do **not** hand-edit only
`analysis.json["external_sinks"]` and skip schemas — that drifts the SoT.

### External connector sinks (MongoDB, SFTP, CosmosDB, JDBC)

When deep analysis identifies a sink whose write method targets an external
connector (MongoDB, SFTP, Azure Cosmos DB, JDBC writes via `format("mongo")` /
`format("com.mongodb.spark")` / `CosmosDbConfig` / `.write.jdbc(...)`) that
cannot be reached in a Snowflake environment, set `allow_empty` **and**
register an `expected_divergence` for it:

```json
{
  "id": "sink_mongodb_products",
  "kind": "mongodb",
  "allow_empty": "external connector — MongoDB not reachable in SCOS",
  "columns": []
}
```

Add it as a write table under `schemas/entrypoints/<id>/tables/<KEY>.json`
with `"access": "write"` (and `allow_empty` / expected divergence as above).
`prevalidate --phase b` will then downgrade the finding from blocking to a
warning, and Phase B will skip the empty-table check for that sink.  Do **not**
leave external connector sinks unregistered: an unregistered MongoDB/SFTP write
still runs in Phase B and gets TABLE_OR_VIEW_NOT_FOUND (error 5001), causing
hard_stuck.

### External sources (schemas tables)

Capture the sources needed to execute the entrypoint as
`schemas/entrypoints/<id>/tables/<KEY>.json` files with `"access": "read"`
(or `"readwrite"`). Scan all `.scala` files reachable from the entrypoint:

- table reads: `spark.table("name")`, `spark.read.table("name")`,
  SQL `FROM` references in `spark.sql("SELECT ... FROM ...")`,
  `spark.read.format("snowflake").load(...)`
- file reads: `.read.csv(...)`, `.read.parquet(...)`, `.read.json(...)`,
  `.read.text(...)`, `.read.format(...).load(...)`
- connector reads that need mocked tabular inputs

Do NOT invent table files for `.sql` script templates — those are cataloged in
`schemas/sql_files.json` by `schema_mine`.

Each table file (fields map 1:1 onto the mined / shim source record):

```json
{
  "_table_key": "orders_source",
  "access": "read",
  "name": "orders_source",
  "category": "table | file | jdbc | snowflake",
  "original_path": "literal from source when available",
  "reader_method": "table | csv | parquet | json | ...",
  "reader_options": {},
  "mock_file": "orders.csv",
  "subpath": "optional/stage/layout/override",
  "columns": [{"name": "id", "type": "long"}, ...]
}
```

`mock_file` path resolution: always relative to
`Validation/shared/mock_data/<ep_id>/`. Never use paths like
`../shared/x.csv`.

> **FIELD NAME IS `mock_file`, NOT `mock_path`.**  The Scala harness case
> class maps `mock_file` → `ExternalSource.mockFile`.  Writing `mock_path`
> instead silently produces `None` → `SCOS_INPUT_*` is never set → workload
> reads the live cloud path at runtime → Phase A/B both fail with "Loaded data
> was empty". Always use `"mock_file": "filename.parquet"` (filename only,
> relative to the ep mock dir). Never write `"mock_path"`.

### Column reference extraction (Scala patterns)

When inferring schemas from workload code, extract column references
using these Scala Spark patterns:

- `col("colName")` and `functions.col("colName")`
- `$"colName"` (implicit encoder syntax)
- `.select("col1", "col2")` and `.select(col(...), col(...))`
- `.filter($"col" === value)`, `.where($"col" > 0)`
- `.groupBy("col1", "col2")`, `.groupBy($"col")`
- `.agg(sum("col"), avg("col"), count("col"))`
- `.withColumn("newCol", expr)` — `newCol` is output, not input
- `.join(other, "joinKey")`, `.join(other, Seq("k1", "k2"))`
- `.orderBy("col")`, `.sortBy("col")`
- `StructField("colName", DataType, ...)` — use declared type directly

**Confirm types — don't trust an all-`string` guess.** Column *names* mined from
`col(...)`/`$"..."`/`.select(...)` come with no type, so they default to
`string`. When a source's columns came from these patterns (not an explicit
`StructType`/`StructField` or a `.cast(...)`) and **every** type is `string`,
that is a guess, not a fact: infer the real types from how the columns are used
(arithmetic/`sum`/`avg` → numeric; date filters/`datediff` → date/timestamp;
`===`/`>`/`<` against typed literals → that literal's type) rather than leaving
the all-string default, which silently produces wrong mock data.

### Dynamic-path file sources (`DataLake.load()`, `Blob.load()`, `S3.load()`, helper-buried reads)

When `original_path` is a runtime variable (contains "dynamic", `getDbfsPath`,
`location`, `updatedPath`, or is described as a DBFS/Azure/S3 mount path), the
synthesizer **cannot** infer the schema from the path literal.  The correct
approach is to infer columns from how the loaded DataFrame is consumed:

1. **Trace downstream usage**: follow the variable holding the loaded DataFrame
   and collect every `.select(...)`, `.filter(...)`, `.groupBy(...)`,
   `.withColumn(...)`, `.join(...)` reference on it.  Those are the columns that
   must exist in the mock.
2. **Check for an explicit `StructType`**: the `DataLake.load()` call or its
   wrapper often accepts an optional `schema: Option[StructType]` argument.
   If present, use it directly — it is the authoritative source.
3. **Check `FileProperties` / case class fields**: when the workload validates
   a `FileProperties` container before loading, the container fields define the
   access keys (storage account, container name, region, etc.) that must appear
   in `schemas/entrypoints/<id>/_meta.json` → `cli_args` / `entrypoint_kwargs`
   (`files` container) — not in the source columns.  The *source columns* come
   from the actual file content, not the container metadata.
4. **Fallback**: if columns cannot be inferred (no downstream usage visible),
   seed with a minimal single-column schema `[{"name": "value", "type": "string"}]`
   and rely on Phase B's inline schema-repair loop to surface missing columns at
   runtime.  **Never leave `columns: []` for a file source with a `mock_file`**
   — an empty schema produces an empty mock parquet that silently passes Phase A
   with zero rows and nothing for Phase B to compare.

> **PySpark parity**: PySpark uses `rewrite_main_block_env` to completely
> bypass file-listing guards (`if files:`) and inject mock data via
> `os.environ["SCOS_INPUT_<ID>"]`.  The Scala equivalent is the
> `DataLake.load()` bypass recipe in `patch-author.md` (checking
> `System.getProperty("SCOS_INPUT_<ID>")`).  The **key shared requirement** is
> that the mock file must have at least the columns the workload projects from
> it — without them, Phase A captures an empty DataFrame, Phase B has nothing
> to compare, and the trial becomes `passed_no_baseline` with zero rows rather
> than a meaningful comparison.

### Scala companion object UDFs (pre-register divergence before Phase B)

Scala companion objects used as UDFs (`Image$.MODULE$.process`,
`spark.udf.register("fn", SomeObject.method _)`) cannot be resolved
server-side in Snowpark Connect — the JVM class is not available on the SCOS
Python server.  This causes `ClassNotFoundException: <Object>$` in Phase B,
which is a **platform limitation, not a migration regression**.

**Detection during deep analysis**: scan `Output/src` for:
- `$.MODULE$` method references passed to `mapPartitions`, `map`, or `spark.udf.register`
- `udf(SomeObject.someMethod _)` patterns
- Explicit `@UDFRegistration` annotations

**Action**: for each detected Scala object UDF, register the expected divergence
**before Phase B starts**:

```bash
$RUN document-divergence --conv-root $CONVERSION_ROOT \
  --trial-id <ep_id> \
  --sink-id <sink_id> \
  --column __all__ \
  --scope udf \
  --reason "Scala companion object UDF not available server-side (SCOS platform limitation)"
```

This writes `expected_divergences` to **`schemas/manifest.json`** (SoT) and
refreshes the analysis shim. `known-patches suggest` also seeds UDF divergences
into the manifest when present.

With `documented_divergences` set, `comparison_verdict()` returns
`cosmetic_divergence` rather than `real_divergence` on the first Phase B
iteration that runs cleanly — the trial passes immediately rather than
burning 3-4 iterations auto-recovering.  **Do NOT use `allow_empty` for this
case** — the sink may still capture rows; the divergence is in the UDF output
columns, not the row count.

**Connector/JDBC reads need the underlying source columns, not just aliases.** A
`spark.read.format("snowflake").option("query"/"dbtable", …)` or `spark.table`
read often projects output aliases, but the workload also filters/joins on
columns that never appear in the projection. Declare the physical WHERE/JOIN
source columns in the source `schema` too — otherwise Phase B fails
`COLUMN_NOT_FOUND` on a column the mock never created.

**Runtime-substituted read names.** When a read's table or file path is built
at runtime, the analyzer records it with an unresolved `name` (e.g. `null` or a
partial literal) and an `llm_todo` hint. Common Scala shapes:
- String interpolation with a runtime segment: `spark.table(s"$schema.tbl")`,
  `spark.read.parquet(s"$basePath/run_$date")`
- Concatenation: `spark.table(schema + ".my_table")`
- `String.format("db.tbl_%s", suffix)` / `.formatted(...)`

The analyzer resolves a trailing literal dotted segment from `+` concat
(`schema + ".my_table"` → `my_table`) and constant-folded interpolations, but
NOT runtime-computed slots. For those: open `defined_at`, substitute the same
values the workload uses at run time, and update the entry's `name` and
`original_path` to the fully-substituted name so the mock matches what the code
reads.

### Sinks

Capture the outputs that should be snapshotted and compared:

```json
{
  "id": "orders_output",
  "name": "orders_output",
  "kind": "table | file | non_spark",
  "method": "saveAsTable | parquet | insertInto | ...",
  "original_target": "literal target when available",
  "schema": [...],
  "natural_keys": ["order_id"]
}
```

`natural_keys` is **required for meaningful A/B comparison**: the scos-runner passes
it to the comparator as `--key-columns`, enabling stable keyed row-matching. Without
it the comparator falls back to full-row lexicographic sort — one divergent cell
cascades into many false mismatches. Use the primary key(s) of the output table, or
the business-key columns that uniquely identify a row (e.g. `["route_id", "read_ts"]`).
If no natural key exists (e.g. order-dependent aggregation output), declare
`"natural_keys": []` explicitly to suppress the analyzer warning.

### Intermediate tables

When one selected entrypoint writes a table that another reads:

```json
{
  "name": "db.schema.table_name",
  "writer_entrypoint_id": "upstream_entrypoint",
  "reader_entrypoint_ids": ["downstream_entrypoint"],
  "schema": [...],
  "seed_strategy": "empty | from_source_join",
  "seed_sql": ""
}
```

### Schemas and mock data

Every source should get mock data under `Validation/shared/mock_data/<ep_id>/`.

**Generate relational mocks deterministically** — do NOT hand-author tabular
data. Once schemas tables have `columns` + `mock_file`, run the typed
generator (same engine as the PySpark validator). It
writes type-correct, edge-case-covering data, and columns sharing a name across
an entrypoint's sources draw from a shared pool so joins actually match:

```bash
# schemas/ is already SoT after Step 1 — only re-mine if tables/meta changed
# and you need a fresh projection / cross-EP inheritance pass:
uv run --project $SKILL_DIRECTORY/.. python \
  $SKILL_DIRECTORY/scripts/schema_mine.py --conv-root $CONVERSION_ROOT
# Generate typed mocks from schemas/
uv run --project $SKILL_DIRECTORY/.. python \
  $VALIDATOR_SCRIPTS/datagen.py \
  $CONVERSION_ROOT/Validation/shared/schemas \
  $CONVERSION_ROOT/Validation/shared/mock_data
# then confirm coverage:
uv run --project $SKILL_DIRECTORY/.. python \
  $VALIDATOR_SCRIPTS/datagen.py \
  $CONVERSION_ROOT/Validation/shared/schemas \
  $CONVERSION_ROOT/Validation/shared/mock_data
```

Use the LLM only for **judgement** the generator can't do: inferring the schema
itself, and authoring **non-tabular** inputs (config/JSON document blobs — a
source whose `schema` is not a column list). Use `relational: false` +
`document_schema` for config/document blobs and relational `columns` for tabular
extracts. `document_schema` is a **shape shorthand**, not JSON Schema:
`{"field": "<spark type>", "nested": {...}, "arr": ["<spark type>"]}` — e.g.
`{"env": "string", "retries": "int", "hosts": ["string"]}`. A JSON-Schema-style
`{"type": "object", "properties": {...}}` is copied literally into the mock and
produces a nonsense document that nothing flags. Hand-author or fix a specific
mock only when datagen flags a gap or a workload needs particular values.

**Repair policy is the one-unit loop above (PySpark parity):** fix one unit →
datagen → verify → next unit. Do not batch-fix every problem before re-verify.

**Datagen is hash-driven and incremental** — it regenerates only tables whose
schema hash changed (or whose mock is missing) and leaves the rest untouched, so
cheap re-runs after each edit are safe. **Do not wipe `mock_data` just because
`--verify` failed** — that defeats the hash mechanism. Force a full regenerate
(`--all`, or `rm -rf $MOCK_DATA_DIR`) **only** when an edit renamed or removed a
source/sink (datagen does not prune orphaned mock files from a rename/drop).

Requirements (the generator satisfies these for relational sources; verify them
for any hand-authored mock):

- every mock file exists,
- CSV-style mocks MUST have a real delimited header row as row 1,
- at least one data row exists,
- schema is good enough to exercise the workload,
- generated values should not all be identical.

### Auxiliary files

If the workload reads config or SQL files, materialize simple test-safe
versions under `Validation/shared/auxiliary/` and record them in
`auxiliary_files[]`.

Record widget/config values expected from the environment as plain notes
under the entrypoint (env var name `SCOS_WIDGET_<NAME>`).

### Verify the analysis (deterministic gates)

Before recording `synth_deep`, run the deterministic exit gates. There is no
separate critic agent — use the tools below instead of a manual body-scan.

**Gate 1 — mock files exist:**

```bash
uv run --project $SKILL_DIRECTORY/.. python \
  $SKILL_DIRECTORY/scripts/schema_mine.py --conv-root $CONVERSION_ROOT
uv run --project $SKILL_DIRECTORY/.. python \
  $VALIDATOR_SCRIPTS/datagen.py \
  $CONVERSION_ROOT/Validation/shared/schemas \
  $CONVERSION_ROOT/Validation/shared/mock_data
```

**Gate 2 — column coverage + write_helper sinks (replaces manual body-scan):**

```bash
uv run --project $SKILL_DIRECTORY/.. python \
  $SKILL_DIRECTORY/scripts/column_check.py --conv-root $CONVERSION_ROOT
```

Both gates must exit `0`. Gate 1 prints `"ok": true` in JSON; gate 2 prints
`[column_check] verify OK`. If gate 2 lists missing columns, edit
`schemas/entrypoints/<id>/tables/<KEY>.json` and re-run `datagen.py` until clean
(do **not** hand-edit `analysis.json`).

When gate 2 flags gaps the AST extractor cannot attribute (helper-method
column gaps, `.agg(sum("col"))`, `StructField` declarations), supplement
`ast_facts.json` with targeted source reads — do not re-parse the whole workload
by hand.

Re-derive obviously-wrong types instead of leaving an all-`string` mined
schema: numeric for `sum`/arithmetic, date/timestamp for date filters /
`datediff`. For a `format("snowflake")` / `spark.table` connector read, ensure
the schema includes the WHERE/JOIN columns the workload uses, not just the
projected output aliases.

- **`array<struct<...>>` mistyped as `string`.** A source column feeding
  `explode`/`flatten`, or produced by `collect_list`/`collect_set` of a struct,
  is `array<struct<...>>`, not `string` (the AST extractor defaults to `string`
  for unresolved complex types). Fix the type from the call site if you can spot
  it; otherwise the Phase B inline-repair loop will surface it.

**Sink declarations.** For every sink confirm `kind`/`method` are present,
table/file sinks have a non-empty schema, and the target identifier is not
blank. Gate 2 (`column_check.py`) also flags `write_helpers` without matching `sinks[]`.

If either gate adds columns or sinks, re-run `schema_mine.py` then
`datagen.py schemas/ mock_data` and `column_check.py --conv-root ...`;
confirm both exit `0` before recording the milestone.

### Warning handling

`datagen.py` emits `warnings` separately from `problems`. Warnings are
not part of `ok`, but you must resolve or explicitly dismiss them before finishing
— an unhandled join warning silently produces empty joins at runtime. Handle them
only once `problems` is empty, one edit at a time:

- **Join-overlap warnings** (a column appears in ≥2 sources but datagen won't pool
  it — no `joins` edge, no shared `values`): if it is a real join key, add a
  `joins` edge (or a shared `values` domain) in
  `schemas/entrypoints/<id>/_meta.json`, then re-run `datagen.py` so the linked
  columns draw from one pool. A star-pattern key shared across many sources is
  **one** edit — add all its edges at once, then regenerate once (not one edge
  at a time).
- **Confirmed non-keys**: set `"join_key": false` on the column to dismiss the
  warning. This only silences the *warning*; it does **not** dismiss a real
  `join overlap empty` *problem* once a column is already in an established pool.
- Pure `join_key: false` dismissals: set the field and run datagen. Do not skip
  the datagen run just for dismissals.
- **Non-deterministic tie-break** — a "keep one row per group" step
  (window `row_number().over(Window.partitionBy(K).orderBy(O)).where($"rank" === 1)`,
  `dropDuplicates`, `distinct`) selects a different row on SCOS vs Spark because
  the `orderBy` column `O` has ties → set `"unique": true` on `O` in that table's
  schema JSON, regenerate mocks, and re-run. Do NOT document this as an acceptable
  divergence. **Caveat:** if `O` is also a join key (it appears in this
  entrypoint's `joins`/`range_join_edges`), datagen ignores `"unique"` on it to
  preserve join overlap — instead pick a non-join column to make unique.

### Review generated mocks with `--peek`

Once `problems` is null and warnings are handled, spot-check the mocks against the
workload source (the `python_repl` kernel has no pandas/pyarrow, so use `--peek`):

```bash
uv run --project $SKILL_DIRECTORY/.. python \
  $VALIDATOR_SCRIPTS/datagen.py \
  $CONVERSION_ROOT/Validation/shared/mock_data/<ep_id>/<mock_file> --peek
```

It prints per-column dtype, null count, distinct count, and sample values. Confirm:

- declared **types** match the workload's real expectations (an amount cast to
  decimal isn't left `string`; a date column isn't an int);
- **literal filter domains** are represented — when the source filters on a small
  fixed set (`col.isin(...)`, SQL `IN (...)`, `=== "610A"`), the mock must contain
  those values or the filter yields **zero rows** with random mocks. Add them as
  `"values"` on the column (for **timestamp/date** columns do NOT set `values` —
  the verifier can't enum-check temporal `values`; widen the `entrypoint_kwargs`
  date bounds instead);
- **join keys** that must match across sources actually overlap;
- sample values are plausible for the domain (a `latitude` in `[-90, 90]`);
- nullable columns still contain nulls and NOT NULL keys do not.

Prefer a systematic schema fix + re-run `schema_mine.py` + `datagen.py` over
hand-editing a mock (a later datagen regenerate overwrites hand edits). If you edit
a mock or schema here, run one final datagen before recording the milestone.

### Deep-analysis output

Edit only under `Validation/shared/schemas/` (`_meta.json` + `tables/*.json`).
Do not hand-edit the analysis shim — `prevalidate` / `column_check --conv-root`
regenerate it. Then record:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/scos_state.py \
  record-milestone --conv-root $CONVERSION_ROOT --milestone synth_deep
```

## Self-check

Before finishing, verify:

1. `schemas/manifest.json` lists this batch’s entrypoints (orchestrator-scoped —
   never an agent-picked shortlist).
2. Every entrypoint has tables under `schemas/entrypoints/<id>/tables/` with
   read/write access, columns, and `mock_file` where needed.
3. `schemas/scala_meta.json` (or manifest summary) has `build_tool` /
   `source_roots[]` when the kit needs them.
4. No user-facing entrypoint-selection prompt was issued (orchestrator owns
   optional `scope-entrypoints` in Step 1.6).
5. **Hard gate:** `datagen.py schemas/ mock_data` exits `0`.
   Prefer also running `column_check.py --conv-root` (or rely on
   `prevalidate`, which refreshes the shim and runs column_check). Do not
   record `synth_deep` while either gate reports problems; fix and re-run.
6. No `warnings` from the final datagen run remain unresolved or undismissed.
7. Every `llm_todo` on selected entrypoints / tables is resolved (or explicitly
   documented as a known gap with a fix plan).
8. **Batch gate (orchestrator):** after synth + patch-author, the batch-runner
   runs `scos_state.py prevalidate --phase a`, which re-checks schema/mock/
   column completeness via the aggregated
   `Validation/shared/prevalidation_report.json`. If prevalidate reports
   `mock_data` / `column_check` / `analysis_completeness` blockers, return here,
   batch-fix every finding from that report in one pass (edit `schemas/` only),
   then re-run `prevalidate --phase a --force` — do not enter Phase A with open
   blockers.
