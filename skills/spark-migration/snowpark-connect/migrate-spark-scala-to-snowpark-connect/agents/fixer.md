# Fixer Agent — Phase 2 Specialist

Apply code fixes for SCOS compatibility issues identified in `analysis.json` for Scala workloads.

## Inputs

Read `migration_state.json` to get:
- `manifest` — list of `.scala` files
- `migrated_dir` — directory with copied source files
- `conversion_root` — for `analysis.json` and gate file

Read `analysis.json` from the conversion root.

The coordinator passes these per-dispatch context parameters:
- `CHUNK_MODE=chunked`, `CHUNK_ID=<n>`, `CHUNK_FILES=<comma-separated files>` —
  process **only** the files in `CHUNK_FILES`, not the whole manifest.
- `PARALLEL_MODE=true|false` — when `true`, you are one of several fixers
  running concurrently in a wave. **Do NOT write `migration_state.json`** (the
  coordinator is the single writer and concurrent writes would race); instead
  return a `CHUNK_RESULT` line (see Output). When `false` (or absent), you may
  update state yourself as the sole writer.

## Rules

Load `references/fix-rules.md` for the complete Scala-specific fix rule set. Key rules summary:

| Risk | Action |
|------|--------|
| `final_risk >= 0.7` | **Must fix** — apply fix or rewrite. If impossible, add `// SCOS: TODO`. If genuinely safe, record `resolution: "safe"` with a concrete `resolution_reason` in `analysis.json`. |
| `0.3 <= final_risk < 0.7` | **Should fix** — apply fix if suggested, else `// SCOS: TODO`. If genuinely safe, record `resolution: "safe"` in `analysis.json`. |
| `final_risk < 0.3` | **Review** — fix if possible, else record `resolution: "safe"` in `analysis.json` (no inline comment) or leave a brief `// SCOS: <explanation>`. |

Every issue ends with a verdict: an inline `// SCOS:` comment **or** a
`resolution` field on the issue object in `analysis.json` (see
`references/fix-rules.md` → "Recording resolutions in `analysis.json`"). A
recorded `resolution` (`fixed`/`safe`/`todo`/`perf`) satisfies the Phase-2
high-risk coverage gate without an inline marker — `resolution: "safe"` requires
a non-empty `resolution_reason`. This is the same verdict model PySpark uses.

**Critical exceptions (do NOT annotate):**
- No-op operations (`hint()`, `repartition()`, `coalesce()`) — leave as-is, no comment
- No-op configs (`spark.sql.shuffle.partitions`, `spark.executor.memory`, etc.) — leave as-is, no comment

**Comment prefixes (Scala uses `//` not `#`):**
- `// SCOS: <explanation>` — fix applied or reviewed
- `// SCOS: [SPRKCNTSCL####] <explanation>` — fix/annotation carrying its EWI code (embed the code inline, exactly as PySpark does with `# SCOS: [SPRKCNTPY####]`)
- `// SCOS: TODO - <explanation>` — requires manual review
- `// SCOS: Performance tip - <explanation>` — optimization recommendation

> **Single marker vocabulary (parity with PySpark).** Every inline annotation
> you leave uses the `// SCOS:` prefix — there is **no** separate `// EWI:`
> prefix. When an EWI code applies (e.g. an unsupported API), embed it as a
> `[SPRKCNTSCL####]` token inside the `// SCOS:` comment. The Phase-2 coverage
> gate (`verify_phase.py`) confirms a `// SCOS` marker sits within ±3 lines of
> each high-risk issue's line range, **or** that the issue carries a recognized
> `resolution` verdict (`fixed`/`safe`/`todo`/`perf`) in `analysis.json`
> (`resolution: "safe"` additionally requires a `resolution_reason`). A bare
> `// EWI:` line is still accepted for backward-compat, but new output MUST use
> the `// SCOS: [SPRKCNTSCL####]` form.

### MUST NOT undo deterministic pre-processing (binding)

Phase 0.5 (the sole deterministic tier — AST-grade Scalafix rules) already
applied byte-perfect rewrites before you ran. You **MUST NOT** modify, collapse,
re-order, or delete any AST-managed region. Specifically:

- **Never touch a line carrying a recipe marker comment** — `// SCOS-RECIPE-PRESERVED-CONFIG: <k>=<v>`, `// SCOS-RECIPE-INSERT-AFTER-BUILDER: ...`, `// SCOS-RECIPE-INSERT-IMPORT: <class>`, or any `// SCOS-WARN:` / `// SCOS-TODO:` emitted by a Scalafix rule — and never remove the `.config(...)` / `.conf.set(...)` line it guards. `// SCOS-RECIPE-INSERT-IMPORT:` lines are consumed by Phase 3 (`update_imports_scala.py`) to inject the corresponding import statement; removing them prevents the import from being added. Collapsing a `SparkSession`/`SnowparkConnectSession` builder chain and silently dropping a preserved `spark.sql.session.timeZone` (or any other preserved config) is the canonical regression this rule prevents.
- **Treat `migration_state.json :: recipe_edits` as off-limits.** Any `file:line` with a `recipe_edits` anchor (its `recipe_id` is in the `scalafix:<RuleName>` namespace) was already handled deterministically — do not re-flag, re-rewrite, or revert it. If an `analysis.json` issue points at an AST-managed line, assume it is already fixed and skip it.
- If you believe an AST-managed region is wrong, add a `// SCOS: TODO - recipe region needs review` comment **above** it — do not edit the region itself.

The Phase 2 verifier (`verify_phase.py --phase 2`) asserts the recipe markers
you were given still survive (you did not delete a preserve-config region);
dropping one is a hard gate failure. Phase 3 then verifies the config is
actually materialized after the session is rebuilt.

## Workflow

Process files **one at a time** from the manifest:

1. Read the file
2. Find all issues for this file in `analysis.json`
3. **Branch each issue on `kind` FIRST** (recipe-aware shortcut — see
   `references/fix-rules.md` → "Branch on `kind` FIRST"). When the analyzer was
   invoked with `--recipe-edits`, each issue carries a `kind` field:
   - `recipe_validated` — a `Scos*Rewrite` Scalafix rule already fixed this site.
     **Skip** (do not re-edit). Verify the inline `// SCOS:` comment naming the
     `recipe_id` is still present. Count it in the `skipped` field of CHUNK_RESULT.
   - `recipe_incomplete` — a `Scos*Annotate` rule flagged it but could not
     rewrite. **Prefer the issue's `suggested_fixer_action` over `fix`** if it is
     non-null and concrete code (not prose). Apply it verbatim, then append
     `// SCOS: fixed by fixer on top of <recipe_id>` (do NOT remove the rule's
     original `// SCOS-WARN:` / `// SCOS-TODO:` comment). If `suggested_fixer_action`
     is null/empty, fall through to the normal rules below.
   - `recipe_adjacent` — no rule fired but the analyzer matched one. Apply normal
     rules, and append `// SCOS: recipe-coverage gap - pattern matches <suggested_recipe_id>`.
   - `standard` (default) — apply normal rules below. This covers both
     analyzer-emitted decidable triggers (`source="trigger_decidable"`) and
     Phase 1.1b adjudicator-confirmed rows (`adjudicated=true`).
   - `needs_adjudication` — **fallback only.** The analyzer never calls
     `CORTEX.COMPLETE`; every non-decidable (and every recipe-touched) block is
     deferred as `kind="needs_adjudication"` and normally resolved by the
     Phase 1.1b adjudicator *before* you run — confirmed rows arrive as
     `kind="standard"` and dismissed rows as `resolution="safe"`, so you should
     rarely see this `kind` still unresolved. If you DO see a row still tagged
     `needs_adjudication` with `adjudicated` false (Phase 1.1b was skipped or a
     worker sidecar was missing — `UNRESOLVED_left>0`), do the adjudicator's job
     inline: **confirm-or-dismiss first** — read `code` + the
     `deferred_candidates` array (each entry is `kind="scos_issue"` with
     `category`/`reason`/`risk`/`ewi_code`/`how_to_fix`, or `kind="rag_match"`
     with `root_cause`/`score`/`test_name`) in the context of the file, and be
     conservative (these triggers fire on token presence and are
     false-positive-prone). If it is NOT a real issue, do NOT edit — set
     `resolution: "safe"` + a code-grounded `resolution_reason` and move on. If it
     IS real, treat it like `standard`: continue to step 4 and ground the fix in
     `references/fix-rules.md` (there is no analyzer `fix` to lean on).
   - `kind` missing (older `analysis.json`) — treat as `standard`.

   **Recipe Handoff Protocol (positive, not just negative).** For an
   `annotate`-only recipe site you are completing, you are handed the recipe's
   annotation as a *handoff*: read the `recipe_id` from the issue (or the inline
   `// SCOS:` comment), apply the deeper context-aware fix the recipe could not,
   and leave **both** the recipe's original marker and your completion comment.
   Do not treat the annotation as a warning to leave alone — treat it as a
   work item the deterministic tier routed to you. Only `Scos*Rewrite` rules
   (`kind="recipe_validated"`) are truly done; `Scos*Annotate` rules expect you
   to finish the job.

4. For each remaining issue, consult `references/fix-rules.md` for the appropriate action:
   - **`spark.sparkContext` → typed null stub** — `SnowparkConnectSession` has no
     `sparkContext` method. When `lazy val sc = spark.sparkContext` appears in a
     shared singleton (e.g. `ETL.scala`), replace it with a compilation-safe stub
     so the compile gate passes and downstream callers still reference a typed
     value:
     ```scala
     // SCOS: [SPRKCNTSCL1500] SparkContext not available in SCOS — stub for compilation; remove all sc.xxx usages
     lazy val sc: org.apache.spark.SparkContext = null.asInstanceOf[org.apache.spark.SparkContext]
     ```
   - **Dead imports from spark-core internals** — `org.apache.spark.metrics.UserMetricsSystems`
     and similar internal Spark classes are absent from `spark-connect-client-jvm`.
     If the symbol is imported but never referenced in the file body, remove the
     import entirely and leave a `// SCOS:` comment in its place:
     ```scala
     // SCOS: removed unused import org.apache.spark.metrics.UserMetricsSystems (spark-core internal, not in SCOS classpath)
     ```
     Do NOT add these as TODO markers — they are unused dead code, not actionable migration work.
   - **RDD operations**: Read `../../references/scala/rdd-conversion.md`. Check the issue's `"unsupported"` flag and the `"fix"` text: if `unsupported: true` (`.rdd` with closure or partition op, `mapPartitions`/`foreachPartition`, SparkContext file/accumulator APIs) **preserve the line and prepend a `// SCOS: [SPRKCNTSCL1500] … manual refactor required` marker — do NOT rewrite or fabricate** (keep the literal `manual refactor` phrase so the Phase 2b compile gate quarantines the file instead of reverting it); if `unsupported: false` and the fix mentions "drop the .rdd accessor" — drop the `.rdd` hop and call the same method on the DataFrame directly (`df.rdd.count()` → `df.count()`, `df1.rdd.union(df2.rdd)` → `df1.union(df2)`, `df.rdd.distinct()` → `df.distinct()`, etc. — see rdd-conversion.md Bucket B); if `unsupported: false` and the pattern is `sc.parallelize`/`sc.emptyRDD` or a `*ByKey` pair op — rewrite to `createDataFrame`/`groupBy().agg(...)` using the canonical forms; if `sc.broadcast(v)` — use `v` directly or `df.hint("broadcast")` for a join hint. **Never** wrap tuples in `Tuple1`, **never** nest `createDataFrame`, **never** re-introduce `.rdd` to force compilation. **Route on the reference's verdict tag** (Rule 2 in `fix-rules.md`): **[Native]/[Workaround]** → apply the rewrite; **[Silent-diff]** → apply + a `// SCOS:` guard for the drift; **[Partial]** → apply the closest form + `// SCOS: TODO` for the lost aspect; **[Hard gap]** → `// SCOS: TODO`. **Accumulators are NOT a blanket TODO** — a driver-side counter/sum/min/max/avg/distinct-set/sketch is a reduction → rewrite as `df.agg(...)`/`df.groupBy(...).agg(...)` (§6.10–6.16); only `foreachPartition` sinks, cache-hit counting, mid-job `acc.value` polling, and `writeStream.foreachBatch` state are hard gaps (§7). Mind the **128 MB** per-group collection ceiling (§3) and the **UDAF** path (§6.17 — never `registerJavaUDAF`; it silently registers a scalar UDF). Use `// SCOS: [SPRKCNTSCL1500]` for RDD/`SparkContext`/accumulator elements and `// SCOS: [SPRKCNTSCL1000]` for generic unsupported elements (no-op config, custom UDAF, `observe`). Read the reference for the full mapping, the §10 verified ops, and worked examples.
     - **RDD blind-spot scan** (before finishing a file, grep for tokens the analyzer may not have surfaced as issues — each has a workaround in `rdd-conversion.md`, so fix it, don't punt): aggregate/reduce (`aggregate`, `treeAggregate`, `treeReduce`, `reduce`, `fold`, `foldByKey`, `aggregateByKey`, `combineByKey`, `groupByKey` → §6.1–6.9); accumulators (`longAccumulator`, `doubleAccumulator`, `collectionAccumulator`, `AccumulatorV2`, `.foreach(...acc.add...)` → §6.10–6.16; hard gaps `foreachPartition`/cache-hit/`acc.value` polling/`writeStream.foreachBatch` → §7 — note there is no `sc.accumulator`/`AccumulatorParam` in Scala); UDAF (`UserDefinedAggregateFunction`, `functions.udaf`, `Aggregator`, `registerJavaUDAF` → §6.17); §10 ops (`mapPartitionsWithIndex`, `partitionBy`, `repartitionAndSortWithinPartitions`, `collectAsMap`, `countApprox`, `countApproxDistinct`, `meanApprox`, `sumApprox`, `saveAsObjectFile`, `saveAsSequenceFile`, `getStorageLevel`, `toDF`, `context`, `toDebugString` → §10). For the DataFrame homonyms (`groupBy`/`toDF`/`partitionBy`/`context`) confirm the receiver is really an RDD (usually a `.rdd` hop that should not exist).
   - **Delta format reads/writes (Rule 7b — must fix)**: `.format("delta").load(path)` → `spark.read.table("<table_name>")`. `.format("delta").save(path)`, `.write.parquet(path)`, and `.write.save(path)` → `.write.mode("overwrite").saveAsTable("<table_name>")`. Infer the table name from the last meaningful path segment. This is `final_risk >= 0.9` — do NOT leave a TODO. See fix-rules.md Rule 7b for examples.
   - **`sys.env` calls (Phase 3 handled — verify only)**: `sys.env.getOrElse("K","d")` / `sys.env("K")` / `sys.env.get("K")` are rewritten deterministically to `System.getProperty(...)` by Phase 3 (`update_imports_scala.py`). If you encounter any surviving `sys.env` call (Phase 3 was skipped or missed it), rewrite it yourself: `sys.env.getOrElse("K","d")` → `System.getProperty("K","d")`, `sys.env("K")` → `System.getProperty("K")`, `sys.env.get("K")` → `Option(System.getProperty("K"))`.
   - **UDF serialization**: Read `../../references/scala/udf-dependencies.md` for Scala-specific fixes (REPLClassDirMonitor, addArtifact, staged JARs)
   - **AWS Glue (`com.amazonaws.services.glue.*`, `SPRKCNTSCL36xx`)**: Read `../../references/scala/glue-recipes.md` (recipes G1–G12) for any file that imports `com.amazonaws.services.glue`. There are no Scalafix Glue recipes — the fixer handles all patterns. Two things corrupt data silently: (a) **identifier case (G2, `SPRKCNTSCL3602`)** — always emit `df = df.toDF(df.columns.map(_.toLowerCase): _*)` after every converted catalog read, never remove it; (b) **`Filter.apply` null semantics (G5, `SPRKCNTSCL3604`)** — `col("op") =!= "d"` drops null-op rows; guard negated predicates as `col("op").isNull || col("op") =!= "d"`. You must also author: connector writeback (G11, `SPRKCNTSCL3608`) as `saveAsTable` + `spark.sql` MERGE (upserts before deletes, delete MERGE guarded by `spark.catalog.tableExists`), and bookmark loss (G8, `SPRKCNTSCL3606`) either re-established via external-stage directory table + Stream or recorded as a `Known Limitation`. No `com.amazonaws.services.glue` import may survive in the output
   - **Wildcard file reads**: Replace with explicit file lists or add TODO
   - **`checkpoint()`**: Replace with `cache()`
   - **Map column subscript**: Replace `mapCol(col("key"))` with `element_at(mapCol, col("key"))`
   - **Catalyst imports**: Create local case class replacements (Rule 15)
   - **Hadoop/HDFS**: Remove imports, replace file ops with Snowflake stage/table (Rule 16)
   - **HWC**: Replace `hive.sql()` → `spark.sql()`, remove HWC declarations (Rule 17)
   - **Hive DDL**: Comment out with TODO (Rule 18)
   - **Cross-file consistency**: After any signature change, grep entire codebase for callers (Rule 20)
   - **Import emission**: Only emit valid Scala import lines — no trailing text/em-dashes (Rule 21)
   - **Syntax artifact cleanup**: Clean up trailing text, bare em-dashes, orphaned comments (Rule 22)
    - **Databricks survivors scan (Rule 16c — MANDATORY)**: After all edits, run the survivor scan from **Rule 16c in `references/fix-rules.md`** and add a `// SCOS: [SPRKCNTSCL1100]` annotation above every unannotated `com.databricks` / `dbutils` line. Any line without a `// SCOS:` or `// EWI:` annotation above it is a hard failure in the Tier 2 test suite.
    - **SQL Rewrites (`language:"sql"` issues)**: issues in `analysis.json` with
      `language: "sql"` point at SQL incompatibilities inside `spark.sql("...")`
      string literals (in a `.scala` file) or standalone `.sql` files. Edit the
      **SQL string content**, not the host `.scala` syntax. Consult
      `references/sql-fix-rules.md` (Scala application details) and
      `../../references/sql/sql-fix-rules.md` (full shared rules) for the per-detector fix guidance
      (`window_without_order_by`, `multicolumn_not_in`, `lca_alias_collision`,
      `in_subquery_in_on_clause`, `lateral_view_unsupported_generator`,
      `insert_overwrite_partition`, `behavioral:sql.*`, function-level gaps, the
      `::` cast shorthand, etc.). Leave a `// SCOS:` comment above the statement
      in the `.scala` file (or a `-- SCOS:` comment in a `.sql` file) describing
      the change. A `// SCOS-TODO: ScosSparkSqlMechanicalRewrite: …` marker means
      the Phase 0.5 mechanical rewrite could not auto-fix it — finish it here.
5. Apply fixes using the Edit tool
6. Record per-file progress:
   - **`PARALLEL_MODE=true`**: do NOT write `migration_state.json`. Accumulate
     the files you completed and report them in the `CHUNK_RESULT` line (Output).
     The coordinator merges every wave member's result into state once.
   - **`PARALLEL_MODE=false`/absent**: update `migration_state.json` yourself:
     ```json
     "2_fixes": {"files_done": ["File1.scala"], "files_remaining": ["File2.scala"]}
     ```

## Phase 2b Compile Gate Notes

- **Mask before the gate.** `notebooks/*.scala` (top-level expressions) and
  `project/*.scala` (sbt meta, `import sbt._`) cause cascade `expected class or
  object definition` errors that misattribute to every file. Rename them to
  `*.scala.nb` before running `revert_failing_scala_files.py` and restore
  immediately after.
- **sbt fallback.** When the project has deps beyond `spark-connect-client-jvm`
  (e.g. `dbutils-api`, local `lib/` JARs), standalone `scalac` fails on every
  file. Run `sbt compile` in `<MIGRATED>` instead — sbt reads `build.sbt` and
  resolves the full classpath, and naturally excludes notebooks and
  `project/` files. Record `"compile_tool": "sbt compile"` in Phase 2b state.

## Binding conventions

- **`// SCOS: TODO` three-element format.** A TODO marker must carry all three:
  (1) the `// SCOS: TODO -` prefix, (2) the EWI code as a `[SPRKCNTSCL####]`
  token, and (3) a one-line explanation. A bare `// SCOS: TODO` with no code and
  no reason is not actionable and will not satisfy the Phase-2 coverage gate for
  a high-risk issue. Form:
  ```scala
  // SCOS: TODO - [SPRKCNTSCL1500] RDD .rdd.map closure has no DataFrame equivalent; manual refactor required
  ```

- **Never add a migration header.** The migration header block comment
  (`// SCOS Migration Output ...`) is added in **Phase 3** by
  `update_imports_scala.py`, not by the fixer. Adding it yourself causes a
  duplicate-header failure in Phase 3. Do not prepend any `// SCOS Migration`
  block to a file here.

- **IO status preservation in the EWI contract.** When you annotate an I/O call
  (e.g. a cloud-path read, a Delta write), preserve the **`Status`** the
  `Issues.csv` generator will assign. The report generator maps the EWI code +
  comment category to a Status (`Error` / `IO` / `Warning` / `Fixed`). Do not
  downgrade a `// SCOS: [SPRKCNTSCL3200]` (IO) finding to a plain
  `// SCOS:` (Warning) comment — that silently changes the row's Status and
  breaks the feedback-report filter. Keep the EWI code inline so the report
  maps the finding to the correct Status.

- **Notebook line-offset caveat (`resolution: "fixed"` requirement).** For a
  notebook-origin issue, the analyzer reports line numbers **within the cell**
  (not the file). The Phase-2 coverage gate's ±3-line marker window is also
  cell-relative. When you fix a notebook issue, record `resolution: "fixed"` on
  the issue object in `analysis.json` (the inline `// SCOS:` comment alone may
  not sit within ±3 file-lines of a cell-relative `lines` range, so the
  recorded verdict is what satisfies the gate for notebook issues).

## Completeness Check

After processing all files:
- Every issue in `analysis.json` with `final_risk >= 0.7` has a fix, a `// SCOS:`/`// SCOS: TODO` marker within ±3 lines of its `lines` range, **or** a recognized `resolution` verdict (`resolution: "safe"` **with** a `resolution_reason`)
- Every issue with `final_risk >= 0.3` has a fix, comment/TODO, or a `resolution` verdict
- Cross-file consistency verified (Rule 20) — no calls to removed methods/parameters remain
- File count matches manifest

Report: "Fixes applied: X files processed, Y issues fixed, Z TODOs remaining"

## Output

- Modified files in `<MIGRATED>/`
- **`PARALLEL_MODE=true`**: return a single `CHUNK_RESULT` line for the coordinator
  to merge — do NOT write `migration_state.json`:
  ```
  CHUNK_RESULT chunk_id=<n> files_done=File1.scala,File2.scala issues_fixed=<count> skipped=<count> todos=<count>
  ```
  - `files_done` — comma-separated base names of the files you completed (no path).
  - `issues_fixed` — count of `analysis.json` issues you applied a fix/rewrite for
    (or whose `suggested_fixer_action` you applied verbatim for `kind="recipe_incomplete"`).
  - `skipped` — count of `kind="recipe_validated"` issues you left untouched (the
    Scalafix rule already fixed them).
  - `todos` — count of issues left as `// SCOS: TODO` / `resolution: "todo"`.
- **`PARALLEL_MODE=false`/absent**: updated `migration_state.json` with phase 2 status.

## Notebook File Handling

When a manifest entry is a notebook (any format recognised by `notebook_io` —
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
   - If `cell.cell_language == "scala"` → apply the Scala fix rules (same as for `.scala` files) to `cell.source`, mutating `cell.source` in place.
   - If `cell.cell_language == "python"` → **cross-language delegation**, see below.
3. After processing every cell, serialize with `write_notebook(path, nb)`.

## Cross-Language Delegation

Notebooks frequently contain cells in a minority language (e.g. a `%python`
cell inside a Scala-primary notebook). Cross-language notebooks are detected
at Phase 0 via `notebook_index.code_cells_by_language`; when any notebook has
cells in more than one of `{python, scala}`, BOTH `analyze_pyspark.py` and
`analyze_scala.py` must run in Phase 1 and their outputs merged into
`analysis.json`. Each issue row carries a `language` field identifying the
owning analyzer.

For every `cell.cell_language == "python"` cell encountered during step 2
above:

1. Capture the cell's source and pre-select matching issues from
   `analysis.json` where **all** of:
   - `file == <notebook path>` (same notebook)
   - `cell_id == cell.index` (same cell)
   - `language == "python"` (only issues the Python analyzer produced)
   If no such issues exist after cross-language analysis ran, the cell has no
   known Python-language migration work — emit a passthrough and move on
   (do NOT delegate to CELL_MODE in this case).
2. Delegate to the sibling sub-skill's fixer via `task()`:
   ```
   task(
     subagent_type="general-purpose",
     description="Fix single Python cell",
     prompt="<content of migrate-pyspark-to-snowpark-connect/agents/fixer.md>\n\n"
            "CELL_MODE=true\n"
            "CELL_SOURCE=<cell.source>\n"
            "CELL_ISSUES=<subset of analysis.json matching this cell AND language=='python'>\n"
   )
   ```
3. The Python fixer returns the transformed cell source as its final textual
   output. Splice the returned source back into `cell.source`. If the return
   value is empty or malformed, leave the cell unchanged and log a
   `// SCOS: SKIPPED - python delegation failed` comment at the top of the cell.
4. EWI namespace for the delegated cell: `SPRKCNTPY*` (the cell's language
   determines the EWI namespace, not the notebook's primary language).

## CELL_MODE (When This Fixer Is Called From the Python Sub-Skill)

If your prompt context sets `CELL_MODE=true`, you are being invoked by the
Python sub-skill to fix a single Scala cell:

- Read `CELL_SOURCE` and `CELL_ISSUES` from the context. `CELL_ISSUES` has
  already been pre-filtered by the caller to contain only issues where
  `language == "scala"` and `cell_id` matches this cell — trust that filter
  and do not re-query `analysis.json`.
- If `CELL_ISSUES` is empty, output `CELL_SOURCE` unchanged (no known Scala
  work to do on this cell) — this path is expected when no Scala issues were
  found by `analyze_scala.py` for this cell.
- Apply the Scala fix rules to `CELL_SOURCE` (same rules as step 3 above),
  but do NOT write any files — operate on the string in memory.
- Output ONLY the transformed cell source as your final textual response.
  Do not wrap in code fences or emit any other text. The calling Python
  fixer splices your output verbatim back into its notebook.
- Do NOT read `migration_state.json`, `analysis.json`, or any other file.
  The calling agent has already pre-selected the issues relevant to this cell.
