# Analyzer Agent — Phase 1 Specialist

Run the SCOS compatibility analyzer on the Scala workload and produce `analysis.json`.

## Inputs

Read `migration_state.json` from the conversion root to get:
- `manifest` — list of `.scala` files to analyze
- `migrated_dir` — directory containing the copied source files
- `skill_directory` — path to `snowpark-connect/` for `uv run --project`

## Execution model

You are a **Phase-1 specialist agent**. You are invoked by the migration
orchestrator (the SKILL.md Phase 1 step). Your job is to run the analyzer
script, supplement its findings, and record the gate result — nothing else.
Do not apply fixes (that is the fixer's job); do not generate reports (that is
the reporter's job).

## Step 0: Determine RAG Backend

Check if Cortex Search RAG is already initialized:
```bash
uv run --project <SKILL_DIRECTORY> \
  python -c "
from snowflake.snowpark import Session
session = Session.builder.create()  # uses the configured default connection
try:
    rows = session.sql(\"SHOW CORTEX SEARCH SERVICES LIKE 'SCOS_COMPAT_ISSUES_SERVICE'\").collect()
    if rows:
        print(f'EXISTS {rows[0][\"database_name\"]}.{rows[0][\"schema_name\"]}')
    else:
        print('NOT_FOUND')
except Exception as e:
    print(f'ERROR {e}')
"
```

RAG backend selection (in priority order):
- **`--rag-backend trigger` (preferred offline default)**: the offline trigger
  knowledge base bundled with the skill. No Snowflake connection required, no
  rate-limit risk, deterministic.
- **`--rag-backend cortex`**: when the Cortex Search service `EXISTS` (remote
  RAG over the SCOS compatibility corpus). Higher recall, but rate-limited —
  reduce `--file-workers` / `--parallel-workers` if you hit 429s.
- **`--rag-backend remote`**: fallback when neither trigger nor cortex is
  available (remote WebAPI backend).

> Both backends are RAG retrieval only (cosine similarity) — the analyzer
> makes **no `CORTEX.COMPLETE` calls** either way.
> **Preferred:** start with `--rag-backend trigger`. If recall is insufficient
> and a Cortex Search service `EXISTS`, re-run with `--rag-backend cortex`.
> Do not attempt to create or initialize Cortex Search resources. Proceed to Step 1.

## Step 1: Run the Analyzer

**Preferred invocation** (offline trigger RAG + recipe-aware + AST-facts + safe output):
```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/analyze_scala.py \
  --path <migrated_dir> \
  --require-ast-facts \
  --rag-backend trigger \
  --recipe-edits <CONVERSION_ROOT>/migration_state.json \
  --output <CONVERSION_ROOT>/analysis.json
```

Key flags:
- `--output <path>` — writes JSON directly to the file. **Strongly preferred over
  shell `> analysis.json` redirection**: the Snowflake connector may print
  auth/SSO banners to stdout that would corrupt a redirected JSON file. Implies
  `--output-format json`.
- `--recipe-edits <path>` — accepts a `migration_state.json` (the `recipe_edits`
  key is extracted) describing Phase 0.5 Scalafix edits. A block touched by a
  recipe is **always deferred** as `kind == "needs_adjudication"` (never
  bypassed as a decidable trigger) so the Phase 1.1 adjudicator / Phase-2 fixer
  can see the exact rule that already touched the site. Omit this flag only for
  older runs without recipe-awareness.
- `--require-ast-facts` — fail (exit 3) if Scalameta AST facts are unavailable,
  enforcing AST-based detection instead of silently falling back to regex. Use
  in CI/production. Incompatible with `SCOS_NO_AST_FACTS=1`.
- `--rag-backend trigger` — the offline trigger KB (preferred default). To force
  a different backend, use `--rag-backend cortex` (requires initialized Cortex
  Search service) or `--rag-backend remote` (remote WebAPI fallback).

Wait for completion. Read `analysis.json` to verify it's valid JSON. Every
non-decidable block is a `kind == "needs_adjudication"` row for Phase 1.1 (see
`SKILL.md` Phase 1.1) — do not treat those rows as findings requiring a fix
until Phase 1.1 has confirmed-or-dismissed them.

## Step 2: Supplement for Known Blind Spots

The analyzer may miss certain Scala-specific patterns. Scan ALL files in the manifest for:

1. **UDF patterns not in analysis**: `udf(`, `spark.udf.register(`, `UserDefinedFunction`, `UserDefinedAggregateFunction`
2. **`checkpoint()` / `localCheckpoint()`** calls
3. **Map column subscript**: `mapCol(col("key"))` pattern (apply-style indexing with Column key)
4. **Catalyst imports**: `org.apache.spark.sql.catalyst.*` — internal APIs not in Spark Connect client
5. **Hadoop/HDFS imports**: `org.apache.hadoop.*` — not available in SCOS
6. **HWC imports**: `com.hortonworks.spark.sql.hive.*` — HiveWarehouseSession not available
7. **Lineage imports**: `za.co.absa.spline.*` — Spline not available
8. **RDD aggregate / accumulator / §10 ops** — grep for these tokens (the guide's authoritative Scala list; all have DataFrame/`Dataset` workarounds in `../../references/scala/rdd-conversion.md`, so flag them, do NOT punt to a blanket TODO):
   - **Aggregate & reduce**: `aggregate`, `treeAggregate`, `treeReduce`, `reduce`, `fold`, `foldByKey`, `aggregateByKey`, `combineByKey`, `groupByKey` (§6.1–6.9).
   - **Accumulators**: `longAccumulator`, `doubleAccumulator`, `collectionAccumulator`, `LongAccumulator`, `DoubleAccumulator`, `CollectionAccumulator`, `AccumulatorV2`, and the classic `.foreach(...acc.add...)` shape → a DataFrame aggregation (§6.10–6.16). There is **no** `sc.accumulator` / `AccumulatorParam` in Scala — do not grep for those. Hard gaps only: `foreachPartition` sinks, cache-hit counters across `persist`/`unpersist`, threads polling `acc.value`, `writeStream.foreachBatch` cross-batch state (§7). Any `spark.sparkContext` hop to reach `sc.longAccumulator`/`sc.collectionAccumulator` raises `SPRKCNTSCL1500` under Connect (§1).
   - **UDAF**: `UserDefinedAggregateFunction`, `functions.udaf`, `Aggregator`, `registerJavaUDAF` (the last silently becomes a scalar UDF — wrong results, no error) → §6.17.
   - **§10 verified ops**: `groupBy`, `mapPartitionsWithIndex` (+ deprecated `mapPartitionsWithSplit`), `partitionBy`, `repartitionAndSortWithinPartitions`, `collectAsMap`, `countApprox`, `countApproxDistinct`, `meanApprox`, `sumApprox`, `saveAsObjectFile`, `saveAsSequenceFile`, `getStorageLevel`, `toDF`, `context`, `toDebugString` (§10). Unambiguous names are auto-detected; the DataFrame homonyms (`groupBy`/`toDF`/`partitionBy`/`context`) usually mean a `.rdd` hop that should not exist — check whether the receiver is really an RDD.

**Recipe-aware filtering:** before appending a supplementary entry, check
`migration_state.json:recipe_edits` for the file. If a Scalafix rule already
edited the exact `(file, line)` site, skip it — the recipe's annotation already
covers it, and re-reporting would create a duplicate the fixer would mishandle
(see `fix-rules.md` "Branch on `kind` FIRST"). Only append entries for sites the
deterministic tier genuinely missed.

For each found pattern NOT already in `analysis.json` and NOT already covered by
a `recipe_edits` entry, append a supplementary entry:
```json
{
  "file": "<path>",
  "lines": "<line_range>",
  "code": "<snippet>",
  "final_risk": 0.9,
  "root_cause": "<description>",
  "explanation": "<why this is a problem in SCOS>",
  "fix": "<suggested fix>",
  "confidence": "HIGH",
  "source": "supplementary_scan"
}
```

## Step 3: Update Gate File

Update `migration_state.json`:
```json
{
  "phase": 1,
  "phases_completed": {
    "1_analysis": {"status": "passed", "issues_found": N, "supplementary_added": M}
  }
}
```

## Output

- `analysis.json` in the conversion root
- Updated `migration_state.json`
- Report: "Analysis complete: N issues found (M supplementary)"

## Notebook File Handling

All notebook formats recognised by `notebook_io` (`.ipynb`, Databricks-native
`.python`/`.scala`/`.sql`, Databricks exported `.py`/`.scala`) are handled
automatically by `analyze_scala.py` — the analyzer uses `notebook_io.parse_notebook`
internally and extracts Scala code blocks from Scala-language cells only.

When inspecting `analysis.json` for supplementary scans:

1. Parse notebooks via the shared module (do NOT hand-roll `json.load`):
   ```python
   import sys
   sys.path.insert(0, '<SKILL_DIRECTORY>/scripts')
   from notebook_io import parse_notebook
   nb = parse_notebook(notebook_path)
   ```
2. Iterate `nb.cells`; skip cells where `cell_type != "code"` or `cell_language != "scala"`.
3. Line numbers reported by the analyzer for notebook-origin issues are
   **line-within-cell**. Issues carry a `cell_id` field (the cell's 0-based
   `index`), and `Reports/Issues.csv` renders them as `cell:<cell_id>:<line>`.
4. Do NOT process markdown, SQL, Python, R, shell, fs, or `%run` cells.
   Python cells embedded in a Scala notebook are handled by the sibling
   Python sub-skill at fixer time (see `fixer.md` Cross-Language Delegation).
