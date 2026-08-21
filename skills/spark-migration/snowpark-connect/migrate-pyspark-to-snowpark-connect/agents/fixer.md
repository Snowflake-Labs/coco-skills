# Fixer Agent — Phase 2 Specialist

Apply code fixes for SCOS compatibility issues identified in `analysis.json`.

## Inputs

Read `migration_state.json` to get:
- `manifest` — list of `.py` files
- `migrated_dir` — directory with copied source files
- `conversion_root` — for `analysis.json` and gate file

Read `analysis.json` from the conversion root.

## Chunk Mode (Coordinator-Dispatched)

<!-- SNOW-3383531: Chunk mode for large workloads -->
<!-- SNOW-PARALLEL: Worker-pool dispatch — many fixers run concurrently -->
When the coordinator uses chunked dispatch (`coordinator_mode = true`), your prompt context includes:
- `CHUNK_MODE=true`
- `CHUNK_ID=<i>` — chunk index (1-based) as printed by `orchestrate_phases.py`
- `CHUNK_FILES=<list>` — the specific files to process in this chunk
- `PARALLEL_MODE=true` — you are one worker in a pool running **concurrently** with other fixers
- `MIGRATION_STATE_PATH=<path>` — path to `migration_state.json`

**If `CHUNK_MODE=true`**: Process **only** the files in `CHUNK_FILES`, not the full manifest.

### State writes — DO NOT touch `migration_state.json` in parallel mode

**If `PARALLEL_MODE=true` (the default for pooled dispatch), you MUST NOT read-modify-write `migration_state.json`.** Several fixer workers run at the same time; a concurrent read-modify-write on one JSON file silently loses other workers' updates. The **coordinator is the single writer** and records progress after your whole wave returns.

Instead, when you finish your chunk, report results on a single final line the coordinator parses:

```
CHUNK_RESULT id=<CHUNK_ID> processed=<comma-separated files fully done> skipped=<comma-separated files reverted by the compilation guard> issues_fixed=<int> todos=<int>
```

Example: `CHUNK_RESULT id=3 processed=a.py,b.py skipped= issues_fixed=7 todos=2`

Rules for parallel workers:
- Only ever edit the files in your `CHUNK_FILES`. Never edit another chunk's files.
- For the compilation guard you may `git checkout -- <file>` **only for your own files**. Never run `git add -A`, `git commit`, or any index-wide git command — the coordinator owns git checkpoints (one per wave).
- Do not write `migration_state.json`, `2_fixes`, `2_fixes_skipped`, `processed_files`, or `pending_files`. Put that information in the `CHUNK_RESULT` line instead.

### Legacy sequential mode (rare)

Only if `PARALLEL_MODE` is **absent or false** (a single inline chunk) may you write state directly:
```python
import json
state = json.load(open(MIGRATION_STATE_PATH))
for f in CHUNK_FILES:
    if f in state.get('pending_files', []):
        state['pending_files'].remove(f)
    if f not in state.get('processed_files', []):
        state['processed_files'].append(f)
for chunk in state.get('chunks', []):
    if chunk['id'] == CHUNK_ID:
        chunk['status'] = 'done'
json.dump(state, open(MIGRATION_STATE_PATH, 'w'), indent=2)
```
Report: `"Chunk <CHUNK_ID> complete: X files processed"`

## Rules

Load `references/fix-rules.md` for the complete fix rule set. Key rules summary:

| Risk | Action |
|------|--------|
| `final_risk >= 0.7` | **Must fix** — apply fix or rewrite. If impossible, `# SCOS: TODO` + `resolution: "todo"`. |
| `0.3 <= final_risk < 0.7` | **Should fix** — apply fix if suggested, else `# SCOS: TODO` + `resolution: "todo"`. |
| `final_risk < 0.3` | **Fix if trivial**, else `# SCOS: TODO` + `resolution: "todo"`. |

**You IMPLEMENT; you do NOT adjudicate.** Every issue you receive has already
been judged a real incompatibility upstream — by the Phase 1.1b adjudicator
(it already set `resolution: "safe"` on the false positives, which you skip). So:

- **Never set `resolution: "safe"` yourself.** Dismissing a finding as "not a
  real problem" is the adjudicator's decision, not yours — re-deciding it here is
  the double-owner bug we removed. Your resolutions are **`fixed` / `todo` /
  `perf`** only.
- If you **cannot** apply a concrete fix to a confirmed issue, leave a
  `# SCOS: TODO - verify/why` and `resolution: "todo"` — a **visible** heads-up,
  never a silent safe. A needless TODO is cheaper than a hidden miss.
- Inline `# SCOS:` comments are only for applied fixes, TODOs, and perf tips.
- Rows already carrying `resolution: "safe"` (adjudicator-dismissed) or
  `kind` that routes to skip: **leave untouched**, do not edit their code.

See `references/fix-rules.md` → "Recording resolutions in `analysis.json`".

**Critical exceptions (do NOT annotate):**
- No-op operations (`hint()`, `repartition()`, `coalesce()`) — leave as-is, no comment
- No-op configs (`spark.sql.shuffle.partitions`, `spark.executor.memory`, etc.) — leave as-is, no comment

**Do NOT add a migration header.** Never prepend a `SCOS Migration Output`
banner, docstring, or any "migrated by / Phase 2 fixes applied" comment to a
file. The migration header is owned exclusively by Phase 3
(`scripts/update_imports.py`), which builds its `Changes Overview` /
`Known Limitations` sections from the `# SCOS:` comments you leave inline.
If you stamp your own header, its `SCOS Migration Output` text trips Phase 3's
dedup guard and **suppresses the real header** (Phase 3 will not overwrite an
existing one). Only emit the inline `# SCOS:` / `# SCOS: TODO` annotations
described above; leave the file's top-of-file banner to Phase 3.

**Comment prefixes:**
- `# SCOS: [CODE-STATUS] <explanation>` — fix applied (STATUS = Fixed)
- `# SCOS: TODO - [CODE-STATUS] <explanation>` — requires manual review (STATUS = Error)
- `# SCOS: Performance tip - [CODE-STATUS] <explanation>` — optimization recommendation (STATUS = Warning)

**Deterministic EWI code contract:** Each finding in `analysis.json` carries an `ewi_code` and `status_class` field (e.g. `"ewi_code": "SPRKCNTPY5400"`, `"status_class": "Error"`). When emitting a `# SCOS:` comment for a finding:
1. **Always** embed the analysis-provided code and status as `[CODE-STATUS]` immediately after the marker prefix (e.g. `# SCOS: [SPRKCNTPY5400-Fixed] rewrote ...`).
2. If you applied a fix, override the status to `Fixed` regardless of the analysis status_class.
3. If you could NOT fix and the finding needs human action, use status `Error` — **except** when the finding's `status_class` is `IO`. `IO` marks an I/O repoint (external cloud-storage paths like `s3://`/`dbfs:`/`gs://`/`abfss://`, or unsupported file-format writers such as `.text()`/Avro/ORC) whose read/write must be pointed at a Snowflake stage or table. These are human-action **I/O** items, not code-conversion errors, so keep status `IO` on the TODO (e.g. `# SCOS: TODO - [SPRKCNTPY5400-IO] ...`) — do NOT downgrade `IO` to `Error`.
4. **Never** invent or guess EWI codes. If the analysis does not provide an `ewi_code`, omit the bracketed code entirely — the reporter will resolve one from the category.

**`# SCOS: TODO` requirements — every TODO comment MUST contain all three of:**
1. The specific unsupported API or element (e.g. `spark.read.format("com.databricks.spark.redshift")`, `dbutils.secrets.get`).
2. WHY it is unsupported in SCOS (the concrete root cause).
3. The suggested Snowflake-native alternative.

Generic placeholder text such as "high-risk pattern requires manual review" or "requires manual review" with no specifics is **FORBIDDEN** — these produce contentless annotations that inflate Error counts with no actionable guidance.

BAD: `# SCOS: TODO - high-risk pattern requires manual review`
GOOD: `# SCOS: TODO - spark.read.format("com.databricks.spark.redshift") with forward_spark_s3_credentials + s3a tempdir is unsupported in SCOS; load the Redshift data via a Snowflake external/JDBC source or stage instead`

(If you cannot fix a confirmed issue, leave a `# SCOS: TODO` + `resolution:
"todo"` — you do not dismiss findings as `safe`; that was already decided
upstream by the adjudicator.)
- `# SCOS-WARN: <recipe_id>: <message>` — emitted by Phase 0.5 recipes; behavior change or risk
- `# SCOS-TODO: <recipe_id>: <message>` — emitted by Phase 0.5 recipes; explicit follow-up required
- `-- SCOS: <explanation>` / `-- SCOS: TODO - <explanation>` — the **SQL** comment
  prefix; use these (not `#`) when editing a standalone `.sql` file.

## SQL Rewrites (embedded `spark.sql` in your CHUNK_FILES)

Phase 0.6 (`rewrite_sql_files.py`) and the Phase-0.5 `spark_sql_mechanical_rewrite`
recipe have already **deterministically rewritten** the SQL gaps that have a
safe, semantics-preserving syntactic fix (QUALIFY → subquery, `::` → CAST,
LISTAGG WITHIN GROUP, UPDATE…FROM → MERGE, EXPLAIN drops, GROUPING SETS folding,
CACHE/UNCACHE removal). Do NOT redo those.

**Scope: you fix the SQL that lives inside *your* `CHUNK_FILES`** — i.e. embedded
`spark.sql("...")` string literals in the `.py` files assigned to your chunk
(`analysis.json` rows with `language:"sql"` whose `file` is one of your
`CHUNK_FILES`). Address all three kinds of gap on those rows:

- **shape gaps** (`detector:*` — LCA, IN-in-ON, window-without-ORDER-BY,
  multi-column `NOT IN`, …);
- **keyword gaps** (`behavioral:sql.*` — TBLPROPERTIES, LATERAL VIEW, WITH
  RECURSIVE, …);
- **function gaps** (the dual-surface `kb_rules.json` rules — `percentile_approx`
  on dates, `collect_list` window ordering, `to_char` format specifiers, `mode`,
  `corr`, …). **These are the easy ones to miss:** they may have no inline
  marker, so locate them by the row's `file` + `lines`.

Apply each row's `suggested_fixer_action`. Follow the canonical patterns in
**`$SKILL_DIRECTORY/migrate-pyspark-to-snowpark-connect/references/sql-fix-rules.md`** (PySpark application details) and
**`$SKILL_DIRECTORY/references/sql/sql-fix-rules.md`** (full shared rules), or the row's `note`/`suggested_fixer_action`.
Edit the `spark.sql("...")` string literal in place and leave a `# SCOS:` comment.

**Standalone `.sql` files are NOT your responsibility.** They are not in the
manifest and never appear in your `CHUNK_FILES`, so — per the "only ever edit
files in your `CHUNK_FILES`" rule above — do not touch them. They are owned end
to end by **Phase 0.6**, which rewrites the mechanical gaps and stamps
`-- SCOS: TODO -` markers on the judgment-heavy ones for manual review. (Two
parallel fixers grabbing the same un-chunked `.sql` would race; Phase 0.6 is the
single deterministic owner.)

**When you resolve a flagged gap, replace its TODO — do not leave both.** If you
rewrite the SQL for a gap that carries a `-- SCOS: TODO -` (or `# SCOS: TODO` /
`# SCOS-TODO:`) marker, **rewrite that marker in place** into an applied-fix note
(`-- SCOS: <what you changed>` / `# SCOS: <what you changed>`) — do NOT add a new
"fixed" comment while leaving the old TODO behind. Leaving both makes the header
self-contradictory (the same finding appears under *Changes Overview* as
"rewritten" **and** under *Known Limitations* as "TODO"). One finding → one
marker, reflecting its final state.

## Recipe Handoff Protocol

Phase 0.5 (`scripts/preprocess_recipes.py`) has already run before you start.
Recipes are deterministic LibCST rewrites that fix or annotate well-known
patterns; their edits are recorded in `migration_state.json` under
`recipe_edits` and inline as comments in the source.

**When you see a `# SCOS:`, `# SCOS-WARN:`, or `# SCOS-TODO:` comment whose
text contains a recipe id matching `<lowercase_letters_digits_underscores>_(rewrite|annotate|comment)`:**

1. The recipe has already done the bytewise change documented in the
   comment. Do NOT undo it.
2. For `# SCOS:` comments (rewrites): no further action required.
   Examples: `dataframe_checkpoint_to_cache_rewrite`,
   `map_column_subscript_colkey_to_element_at_rewrite`,
   `sparkcontext_property_fallback_rewrite`,
   `tempview_multiuse_cache_rewrite`,
   `udtf_enable_compatibility_mode_rewrite`.
3. For `# SCOS-WARN:` and `# SCOS-TODO:` comments (annotate-only recipes):
   the recipe could not determine the correct rewrite from static
   information alone. You SHOULD attempt a targeted fix when the
   workload context makes it clear. Examples:
   - `wildcard_file_read_todo_annotate`: replace the glob with an
     explicit `LIST @stage` lookup or enumerated file list.
   - `self_join_unaliased_warn_annotate`: add `.alias("l")` /
     `.alias("r")` and update downstream column references.
   - `external_cloud_read_stage_perf_comment`: if a stage already
     exists for the bucket, rewrite the path to the stage form.
   - `unionbyname_allowmissing_schema_align_warn_annotate`:
     pre-align schemas with explicit `lit(None).cast(<type>)`.
   - `driver_materialization_hotpath_warn_annotate`: lift the
     materialization out of the loop, replacing the per-row pattern
     with a single DataFrame operation.
   - `nested_window_functions_annotate`: split a nested
     `lag`/`lead`/`last`/`last_value`/`first`/`first_value` (outer
     function whose args contain another `.over(...)`) into
     `withColumn` steps — materialize the inner window, then apply
     the same outer function.
   - `create_map_array_keys_annotate`: do not emit ARRAY-keyed maps;
     rewrite `create_map(collect_list, collect_list)` to
     `map_from_arrays`, a join, or stringify keys.
4. Do NOT remove the recipe comment when you apply a fix on top of it
   — append `# SCOS: fixed by fixer on top of <recipe_id>` so the
   audit trail in `migration_state.json:recipe_edits` is preserved.
   If the recipe already produced the final code, keep the comment
   as-is.
5. The `recipe_edits` block in `migration_state.json` is the source of
   truth for which (file, line) pairs the recipes touched. Cross-check
   issues from `analysis.json` against this block; an issue that has
   already been recipe-fixed is NOT a new TODO.

## Important: PySpark Version Context

Snowpark Connect is based on Spark Connect protocol, NOT PySpark 4. Do NOT use PySpark 4 APIs or behaviors as a reference for fixes. Customers are typically on PySpark 3.x. All fixes must target Spark Connect compatibility as documented in the fix rules, not PySpark 4 features.

## Targeted re-fix mode (`TARGET_ISSUES`) — gap-scoped retries

**This mode applies whenever your prompt context includes `TARGET_ISSUES`** — the
coordinator sets it when re-dispatching you after a quality-gate `FAIL` (the
gate's `gaps` array) or after a runtime failure (a failing test + its traceback
from the validate/Phase-B run). It exists so a retry is **surgical and additive**,
not a full re-do: re-processing an entire file from scratch is expensive and — as
observed in practice — can *regress* code that already converted correctly (a
stateless re-run has turned 113 residual constructs into 157). Follow these rules:

- **Fix ONLY the issues in `TARGET_ISSUES`.** Each entry gives `file`, `line`,
  the offending `code`, and a `reason` (the gate finding, e.g.
  `unsupported_construct_live` / `rdd_convertible_not_converted`, or a runtime
  error like `AttributeError: 'DataFrame' object has no attribute 'intersection'`).
  Address exactly those locations.
- **Do NOT re-touch already-resolved code.** Any line already carrying a valid
  `# SCOS:` marker or an `analysis.json` issue with `resolution` in
  {`fixed`,`todo`,`safe`,`perf`} that is **not** in `TARGET_ISSUES` is out of
  scope — leave it byte-for-byte unchanged. Do not "improve", re-flow, or
  re-annotate it. Retries must be idempotent and additive.
- **Use the `reason`/traceback as the concrete fix directive.** A runtime
  `AttributeError`/`AnalysisException` tells you exactly what's wrong (e.g.
  `.intersection()` → DataFrame `.intersect()`; a join needs an explicit key/
  schema; `.agg` empty-result handling). Ground the fix in
  `references/python/rdd-conversion.md` for the named op.
- **Still obey the no-fake-completion rules:** a `TARGET_ISSUES` entry is resolved
  only by genuinely converting it (or, for a no-equivalent op, commenting it out +
  a `# SCOS: TODO` — never a silent delete, never a bare label). The gate
  (`unsupported_construct_live`, `rdd_convertible_not_converted`,
  `fixed_but_unchanged`) re-runs after you and will reject a labelled-but-unfixed
  or gutted result.
- Read only what you need: the target file(s) plus the specific regions named in
  `TARGET_ISSUES`. You do **not** need to re-enumerate every issue in the file.

When `TARGET_ISSUES` is **absent**, run the full Workflow below (first pass).

## Workflow

Process files **one at a time** from the manifest:

1. Read the file
2. Find all issues for this file in `analysis.json`
3. **Recipe-aware routing (applies before per-issue fix-rules below)**:
   Each issue carries a `kind` field set by the recipe-aware analyzer.
   Route the issue based on `kind` per `references/fix-rules.md`
   "Per-Issue Processing" Step 0:
   - `kind="recipe_validated"` → **skip the issue entirely** (the Phase 0.5
     `*_rewrite` recipe already fixed it; `final_risk` is 0.0).
   - `kind="recipe_incomplete"` → if `suggested_fixer_action` is non-null
     and looks like concrete code, **apply it verbatim** in preference to
     the generic `fix`; append `# SCOS: fixed by fixer on top of <recipe_id>`.
   - `kind="recipe_adjacent"` → apply the normal rule below for the issue
     type AND append `# SCOS: recipe-coverage gap - pattern matches
     <suggested_recipe_id>` so we can mine these for future recipes.
   - `kind="standard"` (default; older `analysis.json` may say `llm_only` — treat the same) → continue to step 4 below.
   - `kind="needs_adjudication"` → **fallback only.** In defer-adjudication mode
     these rows are normally resolved by the Phase 1.1b adjudicator *before* you
     run — confirmed ones arrive as `kind="standard"` and dismissed ones as
     `resolution="safe"`, so you should rarely see this `kind`. If you DO see a
     row still tagged `needs_adjudication` with `adjudicated` false (Phase 1.1
     was skipped or failed), do the adjudicator's job inline: **confirm-or-dismiss
     first** — read `code` + the `deferred_candidates` array
     (`rule_id`/`anchor`/`root_cause`/`ewi_code`) in the context of the file, and
     be conservative (these triggers fire on token presence and are
     false-positive-prone). If it is NOT a real issue, do NOT edit — set
     `resolution: "safe"` + a code-grounded `resolution_reason` and move on. If it
     IS real, treat it like `standard`: continue to step 4 and ground the fix in
     `references/fix-rules.md` (there is no analyzer `fix` to lean on).
   - `kind` missing or legacy `llm_only` (older `analysis.json`) → treat as `standard`.
4. For each remaining issue, consult `references/fix-rules.md` for the appropriate action:
   - **RDD operations**: each RDD issue carries an `rdd_class` field, and the `fix` names the detected op(s) and points to `../../references/python/rdd-conversion.md`. If `rdd_class` is `convertible` or `mixed`, **look up each named op's DataFrame equivalent in that reference and apply the rewrite** — do NOT leave a `# SCOS: TODO` for a convertible op (for `mixed`, TODO only the named no-equivalent op(s)). If `rdd_class` is `no_equivalent`, add a `# SCOS: TODO` (no `suggested_fixer_action` is provided). **Route on the reference's verdict tag** (Rule 2 in `fix-rules.md`): **[Native]/[Workaround]** → apply the rewrite; **[Silent-diff]** → apply + a `# SCOS:` guard for the drift; **[Partial]** → apply the closest form + `# SCOS: TODO` for the lost aspect; **[Hard gap]** → `# SCOS: TODO`. **Accumulators are NOT a blanket TODO** — a driver-side counter/sum/min/max/avg/distinct-set/sketch is a reduction → rewrite as `df.agg(...)`/`groupBy().agg(...)` (§12); only `foreachPartition` sinks, cache-hit counting, mid-job progress polling, and `writeStream.foreachBatch` state are hard gaps (§13). Mind the **128 MB** per-group collection ceiling and the **UDAF** path (§17 — never `registerJavaUDAF`). Read the reference for the full mapping, the §10 verified ops, and worked examples.
     - **Convert the WHOLE chain — never stamp `-Fixed` on a dangling RDD method.** An RDD expression is `sc.parallelize(...)` / `sc.range(...)` / `sc.textFile(...)` **plus** every downstream method on its result. When you swap the entry point to a DataFrame source you MUST also convert the entire downstream chain (`.map`/`.sum`/`.zip`/`.cartesian`/`.histogram`/`.aggregate`/`.reduce`/`.glom`/`.mapValues`/...). A DataFrame has no such methods, so a comment like `# SCOS: [SPRKCNTPY1500-Fixed]` sitting above a line that still calls `.sum()`/`.map()`/`.zip()` on the rewritten value is a **broken migration, not a fix** — it will crash at runtime. Either fully convert the chain, or (per op) handle the specific no-equivalent op. If a Phase-0.5 recipe already rewrote only the entry point (a `# SCOS: ...sc_parallelize/sc_range/sc_textfile...` comment) but RDD methods still follow, **you must finish the chain** — this is a `recipe_incomplete`, not a completed `recipe_validated` fix.
     - **No-equivalent op → comment out the call, don't leave it live.** For an RDD op with no SCOS equivalent (`glom`, `barrier`, `pipe`, `mapPartitionsWithIndex`, `sc.broadcast(...).value`, …), **comment out the offending call (preserve the original as a comment) and attach a `# SCOS-TODO`** — a live no-equivalent call raises `AttributeError`/`PySparkNotImplementedError` and crashes the whole module, taking the convertible code with it. Never delete outright (that is a silent behavior change) and never leave it live. In a **mixed** chain, convert the convertible ops and comment out only the no-equivalent op(s). See `rdd-conversion.md` §11.
     - **`createDataFrame` from bare scalars raises.** `spark.createDataFrame([1, 2, 3])` fails — wrap each scalar as a 1-tuple with a named column: `spark.createDataFrame([(x,) for x in [1, 2, 3]], ["value"])`. See the reference's "Spark Connect / SCOS specifics".
     - **RDD blind-spot scan** (before finishing a file, grep for tokens the analyzer may not have surfaced as issues — each has a workaround in `rdd-conversion.md`, so fix it, don't punt): aggregate/reduce (`aggregate`, `treeAggregate`, `treeReduce`, `fold`, `foldByKey`, `aggregateByKey`, `combineByKey`, `groupByKey`); accumulators (`sc.accumulator`, `collectionAccumulator`, `AccumulatorParam`, `AccumulatorV2`, `.foreach(...acc.add...)` → §12; hard gaps `foreachPartition`/cache-hit/`acc.value` polling/`foreachBatch` → §13); UDAF (`UserDefinedAggregateFunction`, `functions.udaf`, `Aggregator`, `registerJavaUDAF` → §17); §10 ops (`mapPartitionsWithSplit`, `partitionBy`, `repartitionAndSortWithinPartitions`, `collectAsMap`, `countApprox`, `countApproxDistinct`, `meanApprox`, `sumApprox`, `collectWithJobGroup`, `saveAsObjectFile`, `saveAsPickleFile`, `getStorageLevel`, `toDF`, `context`, `toDebugString` → §16). For the DataFrame homonyms (`groupBy`/`toDF`/`partitionBy`/`context`) confirm the receiver is really an RDD (usually a `.rdd` hop that should not exist).
   - **UDF serialization**: Read `../../references/python/udf-dependencies.md` for Tier 1/2/3 fixes
   - **Spark config (`spark.conf.set` / `.config` / `SparkConf`)**: Read `../../references/python/spark-config.md` to tell honored/semantics-affecting keys (preserve) from silently-ignored cluster/runtime keys (the `spark_config_noop_annotate` recipe already flags those — leave them). Do NOT drop `spark.sql.*`/`snowpark.connect.*`/`spark.app.name`/`spark.jars`/`spark.hadoop.fs.s3a.*` (Rule 5)
   - **Runtime / SQL / connection errors**: When a fix must resolve a specific runtime error — e.g. "Insert value list does not match column list", mixed-case "Object does not exist", `RESOURCE_EXHAUSTED` (gRPC message size), `QUALIFY` parse errors, `safe_count`/`safe_checkpoint`, or connection env-var setup — Read `../../references/python/troubleshooting.md` for the cause and concrete fix
   - **AWS Glue (`awsglue.*`, `SPRKCNTPY36xx`)**: Read `../../references/python/glue-recipes.md` for any file that imports `awsglue` — it is the index (G1/G2/G5 in full, plus the routing table). Then Read whichever sub-file the routing table sends you to - `../../references/python/glue-recipes-transforms.md`, `../../references/python/glue-recipes-types.md`, or `../../references/python/glue-recipes-io.md`. The eight `glue_*` Phase 0.5 recipes already did the mechanical unwrapping — do NOT undo their edits. Three things are left to you, and two of them corrupt data silently rather than raising: (a) **`Filter.apply` (G5, `SPRKCNTPY3604`)** is annotate-only by design — you MUST author the null-safe predicate, because Glue evaluates a Python predicate row-wise while Spark uses SQL three-valued logic, so a naive port of any negated / `!=` predicate silently DROPS null rows (`F.col("op").isNull() | (F.col("op") != "d")`); positive predicates need no guard. (b) **Never delete the `df.toDF(*[c.lower() for c in df.columns])` line** the catalog-read recipe emits (G2, `SPRKCNTPY3602`) — the Glue catalog exposes lowercase identifiers and a native Snowflake read returns UPPERCASE, so removing it silently drops columns, primary keys included. (c) **Connector writeback (G11, `SPRKCNTPY3608`)** and **bookmarks (G8, `SPRKCNTPY3606`)** are annotate-only: author the `saveAsTable` + `spark.sql` MERGE (upserts before deletes, delete MERGE guarded by `tableExists`), and either re-establish incrementality or record the switch to full reprocessing as a `Known Limitation`. No `awsglue` import may survive in the output
   - **Nested-lambda variable capture (`Cannot resolve variable '<v>' within lambda function`)**: Read `../../references/python/nested-lambda-capture.md` before rewriting one — the fix pairs the captured value into the inner collection and reads it off the inner lambda's own parameter, but for `filter` / `array_sort` / `map_filter` / `transform_keys` the pairing alone silently changes the output type and needs the unwrap in that file's table, and the `(element, index)` overload of `F.transform` / `F.filter` has no rewrite at all (annotate instead)
   - **`array_sort` 2-arg comparator (`Unsupported function name array_sort`, `SPRKCNTPY5200`)**: 1-arg `array_sort` is fine; the comparator overload is unmapped. Three paths only (Rule 33): `f(left)-f(right)` → decorate-sort-undecorate (pack multi-level keys into one scalar — SCOS sorts `ARRAY<STRUCT>` by field name, not position); an `isNull`-gated 0 is a stability freeze (nulls-last + caveat, not a key); anything else (e.g. `when(x == sentinel_val, 1).otherwise(x < y)`) → `# SCOS: TODO` with why it is not reducible. Do not apply this to `zip_with`.
  - **Wildcard file reads**: Replace with explicit file lists or add TODO
   - **`checkpoint()`**: Replace with `cache()`
   - **Map column subscript**: Replace `map_col[col("key")]` with `element_at(map_col, col("key"))`
   - **Snowflake Connector pushdown**: Add comment recommending `SnowflakeSession.sql()` — keep original code
   - **Unsupported formats**: Flag with TODO (Avro/ORC/Delta → Parquet)
   - **SparkContext access**: Replace with `spark.conf.get()` or static fallbacks (Rule 14)
   - **Hadoop filesystem**: Flag with TODO for Snowflake stage operations (Rule 15)
   - **USE DATABASE/SCHEMA**: Replace with fully-qualified table references (Rule 16)
   - **JVM-only libraries (Deequ)**: Flag with TODO for native DataFrame alternatives (Rule 17)
   - **ML pipeline patterns**: Flag with TODO for Snowpark ML or scikit-learn (Rule 18)
   - **UDTF/UDAF patterns**: Convert to Snowpark handler classes (Rule 19)
   - **Delta Lake operations**: Replace with Snowflake table operations (Rule 20)
   - **Lazy view re-evaluation**: Insert `.cache()` before `createOrReplaceTempView()` (Rule 21)
   - **Memory anti-patterns**: Follow analyzer's `how_to_fix` guidance (Rule 22)
5. Apply fixes using the Edit tool, and set `resolution` to `"fixed"` / `"todo"`
   / `"perf"` on each issue object in `analysis.json`; those get their inline
   `# SCOS:` comment. If you genuinely cannot apply a fix, use `resolution:
   "todo"` with a `# SCOS: TODO` — **do not** invent a `resolution: "safe"`
   dismissal (dismissal was the adjudicator's call, already made upstream; rows
   it dismissed already carry `resolution: "safe"` and you leave them as-is).
6. **Compilation guard**: After applying fixes to each file, verify syntax:
   ```bash
   python3 -m py_compile <file>
   ```
   If compilation fails:
   - **First check for a pre-existing source error.** Read `migration_state.json :: preexisting_syntax` (written by the Phase 0.5 pre-flight `precompile_check.py`). Each entry is `{file, cell_id, error, auto_fixed}`. If the whole-file / concatenated-cell compile fails **only** because of an entry for *your* file with `auto_fixed: false` — i.e. a cell/line you did **not** edit was already broken in the customer's source — do **NOT** revert the whole file. That pre-existing error is not fixer-caused, the coordinator's gate treats it as an advisory `preexisting_syntax` WARN (not a blocking failure), and reverting would throw away all the valid fixes you just applied. Instead: keep your annotations, leave the pre-existing-broken cell as-is (optionally add a `# SCOS: TODO` describing the unsupported construct), verify your *own* edits compile in isolation, and treat the file as processed.
   - Otherwise (the failure is caused by *your* edit): revert the file (yours only): `cd <CONVERSION> && git checkout -- <file>`
   - Add a comment at the top of the issue location: `# SCOS: SKIPPED - fix reverted (would break syntax)`
   - In `PARALLEL_MODE`, record the file in your `CHUNK_RESULT` `skipped=` list (do **not** write `migration_state.json`). In legacy sequential mode only, log it under `"2_fixes_skipped": ["file.py"]`.
   - Continue to the next file
7. **Record progress.** In `PARALLEL_MODE=true`, do NOT write `migration_state.json` — report everything via the single `CHUNK_RESULT` line (see Chunk Mode). The coordinator records `2_fixes`/`processed_files` for the whole wave. Only in legacy sequential mode write per-file progress:
   ```json
   "2_fixes": {"files_done": ["file1.py"], "files_remaining": ["file2.py", "file3.py"]}
   ```

## Notebook File Handling

When a manifest entry is a notebook (any format supported by `notebook_io` —
`.ipynb`, Databricks-native `.python`/`.scala`/`.sql`, Databricks exported
`.py`/`.scala`):

1. Parse the notebook via the shared module (never hand-roll `json.load`):
   ```python
   import sys
   sys.path.insert(0, '<SKILL_DIRECTORY>/scripts')
   from notebook_io import parse_notebook, write_notebook
   nb = parse_notebook(path)
   ```
2. Iterate `nb.cells`. For each cell:
   - If `cell.cell_type == "markdown"` or `cell.cell_language in {"sql", "r", "shell", "fs", "run"}` → leave untouched.
   - If `cell.cell_language == "python"` → apply the Python fix rules (same as for `.py` files) to `cell.source`, mutating `cell.source` in place.
   - If `cell.cell_language == "scala"` → **cross-language delegation**, see below.
3. After processing every cell, serialize with `write_notebook(path, nb)` — the module preserves the original file format, cell ordering, indentation style, and container key order so unchanged cells round-trip byte-identically.
4. Do NOT use `json.dump(open(path, 'w'))` — the native Databricks formats are NOT standard `.ipynb` JSON and would be corrupted by a naive rewrite.

### Notebook line-offset caveat — set `resolution` for high-risk fixes

The fixer gate (`scos_gates.py fixer`) checks for a `# SCOS:` marker within
±3 lines of the original `analysis.json` line number in the **concatenated
code-cell text**. When you prepend comment lines to a cell, the original
line position shifts in that flattened view and the ±3 window misses the
marker.

**For every `final_risk >= 0.7` fix in a notebook cell**, set both fields
on the issue object in `analysis.json` *in addition to* the inline comment:

```json
"resolution": "fixed",
"resolution_reason": "<one sentence describing the fix applied>"
```

`resolution: "fixed"` satisfies the gate's high-risk coverage check
regardless of line-offset drift. Without it the gate fails with
`high_risk_unmarked` even when the `# SCOS:` comment is present.

## Cross-Language Delegation

Notebooks frequently contain cells in a minority language (e.g. a `%scala`
cell inside a Python-primary notebook). Cross-language notebooks are detected
at Phase 0 via `notebook_index.code_cells_by_language`; when any notebook has
cells in more than one of `{python, scala}`, BOTH `analyze_pyspark.py` and
`analyze_scala.py` must run in Phase 1 and their outputs merged into
`analysis.json`. Each issue row carries a `language` field identifying the
owning analyzer.

For every `cell.cell_language == "scala"` cell encountered during step 2
above:

1. Capture the cell's source and pre-select matching issues from
   `analysis.json` where **all** of:
   - `file == <notebook path>` (same notebook)
   - `cell_id == cell.index` (same cell)
   - `language == "scala"` (only issues the Scala analyzer produced)
   If no such issues exist after cross-language analysis ran, the cell
   has no known Scala-language migration work — emit a passthrough and move
   on (do NOT delegate to CELL_MODE in this case).
2. Delegate to the sibling sub-skill's fixer via `task()`:
   ```
   task(
     subagent_type="general-purpose",
     description="Fix single Scala cell",
     prompt="<content of migrate-spark-scala-to-snowpark-connect/agents/fixer.md>\n\n"
            "CELL_MODE=true\n"
            "CELL_SOURCE=<cell.source>\n"
            "CELL_ISSUES=<subset of analysis.json matching this cell AND language=='scala'>\n"
   )
   ```
3. The Scala fixer returns the transformed cell source as its final textual
   output. Splice the returned source back into `cell.source` — **do not**
   touch any other cell. If the return value is empty or malformed, leave the
   cell unchanged and log a `# SCOS: SKIPPED - scala delegation failed` comment
   at the top of the cell.
4. EWI namespace for the delegated cell: `SPRKCNTSCL*` (the cell's language
   determines the EWI namespace, not the notebook's primary language — this
   makes the `Reports/Issues.csv` record consistent with how downstream tools
   interpret the marker language).

## CELL_MODE (When This Fixer Is Called From the Scala Sub-Skill)

If your prompt context sets `CELL_MODE=true`, you are being invoked by the
Scala sub-skill to fix a single Python cell:

- Read `CELL_SOURCE` and `CELL_ISSUES` from the context. `CELL_ISSUES` has
  already been pre-filtered by the caller to contain only issues where
  `language == "python"` and `cell_id` matches this cell — trust that filter
  and do not re-query `analysis.json`.
- If `CELL_ISSUES` is empty, output `CELL_SOURCE` unchanged (no known Python
  work to do on this cell) — this path is expected when no Python issues were
  found by `analyze_pyspark.py` for this cell.
- Apply the Python fix rules to `CELL_SOURCE` (same rules as step 3 above),
  but do NOT write any files — operate on the string in memory.
- Output ONLY the transformed cell source as your final textual response.
  Do not wrap in code fences or emit any other text. The calling Scala
  fixer splices your output verbatim back into its notebook.
- Do NOT read `migration_state.json`, `analysis.json`, or any other file.
  The calling agent has already pre-selected the issues relevant to this cell.

## Completeness Check

After processing all files:
- Every issue in `analysis.json` carries a verdict: an inline `# SCOS:` comment **or** a `resolution` field
- Every issue with `final_risk >= 0.7` has a fix, TODO, or `resolution: "safe"` **with** a `resolution_reason`
- Every issue with `final_risk >= 0.3` has a fix, comment, TODO, or `resolution`
- No `resolution: "safe"` issue has an empty `resolution_reason`
- File count matches manifest

Report: "Fixes applied: X files processed, Y issues fixed, Z TODOs remaining"

## Output

- Modified files in `<MIGRATED>/` (only your `CHUNK_FILES`)
- In `PARALLEL_MODE`: a single `CHUNK_RESULT` line for the coordinator to merge — **no** `migration_state.json` writes
- In legacy sequential mode only: updated `migration_state.json` with phase 2 status
