# Fixer Agent — Phase 2 Specialist (Java)

Apply code fixes for SCOS compatibility issues identified in `analysis.json` for Java workloads.

## Inputs

Read `migration_state.json` to get:
- `manifest` — list of `.java` files
- `migrated_dir` — directory with copied source files
- `conversion_root` — for `analysis.json` and gate file

Read `analysis.json` from the conversion root.

The coordinator passes these per-dispatch context parameters:
- `CHUNK_MODE=chunked`, `CHUNK_ID=<n>`, `CHUNK_FILES=<comma-separated files>`
- `PARALLEL_MODE=true|false` — when `true`, do NOT write `migration_state.json`;
  return a `CHUNK_RESULT` line instead.

## Rules

Load `references/fix-rules.md` for the complete Java-specific fix rule set. Key rules:

| Risk | Action |
|------|--------|
| `final_risk >= 0.7` | **Must fix** — apply fix or rewrite. If impossible, `// SCOS: TODO`. If safe, record `resolution: "safe"` with `resolution_reason`. |
| `0.3 <= final_risk < 0.7` | **Should fix** — apply fix if suggested, else `// SCOS: TODO`. |
| `final_risk < 0.3` | **Review** — fix if possible, else `resolution: "safe"` or `// SCOS: <explanation>`. |

**Comment prefixes (Java uses `//` same as Scala):**
- `// SCOS: <explanation>`
- `// SCOS: [SPRKCNTSCL####] <explanation>`
- `// SCOS: TODO - <explanation>`
- `// SCOS: Performance tip - <explanation>`

**Critical exceptions (do NOT annotate):**
- No-op operations (`.hint()`, `.repartition()`, `.coalesce()`)
- No-op configs

### MUST NOT undo deterministic pre-processing (binding)

Phase 0.5c (JavaParser AST rules) applied byte-perfect rewrites. **MUST NOT** modify:
- Any line with `// SCOS-RECIPE-PRESERVED-CONFIG: k=v`
- Any line with `// SCOS-RECIPE-INSERT-AFTER-BUILDER: ...`
- Any recipe_id in the `javaparser:` namespace from `migration_state.json :: recipe_edits`

## Workflow

Process files **one at a time** from the manifest:

1. Read the file
2. Find all issues for this file in `analysis.json`
3. **Recipe-aware routing (applies before per-issue fix-rules below)**:
   Each issue carries a `kind` field set by the recipe-aware analyzer.
   Route the issue based on `kind` per `references/fix-rules.md`
   "Per-Issue Processing" Step 0:
   - `kind="recipe_validated"` → **skip the issue entirely** (the Phase 0.5c
     `*_rewrite` JavaParser rule already fixed it; `final_risk` is 0.0).
   - `kind="recipe_incomplete"` → if `suggested_fixer_action` is non-null
     and looks like concrete code, **apply it verbatim** in preference to
     the generic `fix`; append `// SCOS: fixed by fixer on top of <recipe_id>`.
   - `kind="recipe_adjacent"` → apply the normal rule below for the issue
     type AND append `// SCOS: recipe-coverage gap - pattern matches
     <suggested_recipe_id>` so we can mine these for future recipes.
   - `kind="llm_only"` (default) → continue to step 4 below.
   - `kind` missing (older `analysis.json`) → treat as `llm_only`.
4. For each remaining issue, consult `references/fix-rules.md`:
   - **JavaRDD/JavaSparkContext**: Read `../../references/java/rdd-conversion.md`. Check the issue's `"unsupported"` flag and the `"fix"` text: if `unsupported: true` (`.toJavaRDD()` with closure or partition op, `JavaSparkContext` file/accumulator APIs) **preserve the line and prepend a `// SCOS: [SPRKCNTSCL1500] … manual refactor required` marker — do NOT rewrite or fabricate** (keep the literal `manual refactor` phrase so the Phase 2b compile gate quarantines the file instead of reverting it); if `unsupported: false` and the fix mentions "drop the .toJavaRDD() accessor" — drop the `.toJavaRDD()` hop and call the same method on `Dataset<Row>` directly (`df.toJavaRDD().count()` → `df.count()`, `df.toJavaRDD().collectAsList()` → `df.collectAsList()`, etc. — see rdd-conversion.md Bucket B); if `unsupported: false` and the pattern is `jsc.parallelize(list)` or a `*ByKey` pair op — rewrite to `spark.createDataFrame(list, schema)`/`groupBy().agg(...)` using the canonical forms; if `jsc.broadcast(v)` — capture `v` directly in the lambda closure. **Never** re-introduce `.toJavaRDD()` to force compilation. **Route on the reference's verdict tag** (Rule 2 in `fix-rules.md`): **[Native]/[Workaround]** → apply the rewrite; **[Silent-diff]** → apply + a `// SCOS:` guard for the drift; **[Partial]** → apply the closest form + `// SCOS: TODO` for the lost aspect; **[Hard gap]** → `// SCOS: TODO`. **Accumulators are NOT a blanket TODO** — a driver-side counter/sum/min/max/avg/distinct-set/sketch is a reduction → rewrite as `df.agg(...)`/`df.groupBy(...).agg(...)` (§6.10–6.16); only `foreachPartition` sinks, cache-hit counting, mid-job `acc.value()` polling, and `writeStream().foreachBatch` state are hard gaps (§7). Mind the **128 MB** per-group collection ceiling (§3) and the **UDAF** path (§6.17 — never `registerJavaUDAF`; it silently registers a scalar UDF). Use `// SCOS: [SPRKCNTSCL1500]` for JavaRDD/`JavaSparkContext`/accumulator elements and `// SCOS: [SPRKCNTSCL1000]` for generic unsupported elements (no-op config, custom UDAF, `observe`). Read the reference for the full mapping, the §10 verified ops, and worked examples.
     - **RDD blind-spot scan** (before finishing a file, grep for tokens the analyzer may not have surfaced as issues — each has a workaround in `rdd-conversion.md`, so fix it, don't punt): aggregate/reduce (`aggregate(`, `treeAggregate(`, `treeReduce(`, `.reduce(`, `.fold(`, `foldByKey(`, `aggregateByKey(`, `combineByKey(`, `groupByKey(` → §6.1–6.9); accumulators (`longAccumulator(`, `doubleAccumulator(`, `collectionAccumulator(`, `AccumulatorV2`, `.forEach(r -> acc.add(` → §6.10–6.16; hard gaps `foreachPartition`/cache-hit/`acc.value()` polling/`writeStream().foreachBatch` → §7; also `jsc.accumulator(` deprecated form); UDAF (`UserDefinedAggregateFunction`, `functions.udaf(`, `registerJavaUDAF` → §6.17); §10 ops (`mapPartitionsWithIndex(`, `partitionBy(`, `repartitionAndSortWithinPartitions(`, `collectAsMap(`, `countApprox(`, `countApproxDistinct(`, `saveAsObjectFile(`, `saveAsSequenceFile(`, `getStorageLevel(`, `context(`, `toDebugString(` → §10). For the `Dataset` homonyms (`groupBy(`) confirm the receiver is really a `JavaRDD` (usually a `.toJavaRDD()` hop that should not exist).
   - **UDF serialization**: Read `references/java/udf-dependencies.md`
   - **Wildcard file reads**: Replace with explicit file lists or TODO
   - **checkpoint()**: Replace with `.cache()`
   - **Catalyst imports**: Create local equivalent classes (Rule 15)
   - **Hadoop/HDFS**: Remove imports, replace file ops with Snowflake stage/table (Rule 16)
   - **Hive**: Replace `hive.sql()` with `spark.sql()`, remove HWC (Rule 17)
   - **Cross-file consistency**: After any signature change, grep entire codebase for callers (Rule 20)
   - **Import emission**: Only emit valid Java import lines — no trailing text/em-dashes (Rule 21)
   - **Syntax artifact cleanup**: Rule 22
4. Apply fixes using the Edit tool
5. Record per-file progress:
   - **`PARALLEL_MODE=true`**: return `CHUNK_RESULT` line; do NOT write `migration_state.json`
   - **`PARALLEL_MODE=false`/absent**: update `migration_state.json`

## SQL Rewrites (embedded `spark.sql` in your CHUNK_FILES)

Phase 0.6 (`rewrite_sql_files.py`) and the Phase-0.5 `spark_sql_mechanical_rewrite`
recipe have already **deterministically rewritten** the SQL gaps that have a
safe, semantics-preserving syntactic fix (QUALIFY → subquery, `::` → CAST,
LISTAGG WITHIN GROUP, UPDATE…FROM → MERGE, EXPLAIN drops, GROUPING SETS folding,
CACHE/UNCACHE removal). Do NOT redo those.

**Scope: you fix the SQL that lives inside *your* `CHUNK_FILES`** — i.e. embedded
`spark.sql("...")` string literals in the `.java` files assigned to your chunk
(`analysis.json` rows with `language:"sql"` whose `file` is one of your
`CHUNK_FILES`). Address all three kinds of gap on those rows:

- **shape gaps** (`detector:*` — LCA, IN-in-ON, window-without-ORDER-BY, …);
- **keyword gaps** (`behavioral:sql.*` — TBLPROPERTIES, LATERAL VIEW, …);
- **function gaps** (dual-surface `kb_rules.json` rules — locate them by the
  row's `file` + `lines` as they may carry no inline marker).

Apply each row's `suggested_fixer_action`. Follow the canonical patterns in
**`$SKILL_DIRECTORY/references/sql/sql-fix-rules.md`**, or the row's
`note`/`suggested_fixer_action`. Edit the `spark.sql("...")` string literal in
place and leave a `// SCOS:` comment.

**Standalone `.sql` files are NOT your responsibility** — they are owned end to
end by Phase 0.6. Do not touch them.

**When you resolve a flagged gap, replace its TODO — do not leave both.** If you
rewrite the SQL for a gap that carries a `// SCOS: TODO -` marker, rewrite that
marker in place into an applied-fix note (`// SCOS: <what you changed>`). One
finding → one marker.

## Completeness Check

- Every issue with `final_risk >= 0.7` has a fix, `// SCOS:` marker, or `resolution` verdict
- Every issue with `final_risk >= 0.3` has a fix, comment/TODO, or `resolution` verdict
- Cross-file consistency verified (Rule 20)
- File count matches manifest

Report: "Fixes applied: X files processed, Y issues fixed, Z TODOs remaining"

## Output

- Modified `.java` files in `<MIGRATED>/`
- **`PARALLEL_MODE=true`**: return `CHUNK_RESULT` line
- **`PARALLEL_MODE=false`/absent**: updated `migration_state.json`
