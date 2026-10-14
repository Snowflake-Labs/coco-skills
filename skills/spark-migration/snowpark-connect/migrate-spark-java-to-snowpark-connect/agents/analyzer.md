# Analyzer Agent — Phase 1 Specialist (Java)

Run the SCOS compatibility analyzer on the Java workload and produce `analysis.json`.

## Inputs

Read `migration_state.json` from the conversion root to get:
- `manifest` — list of `.java` files to analyze
- `migrated_dir` — directory containing the copied source files
- `skill_directory` — path to `snowpark-connect/` for `uv run --project`

## Step 0: Determine RAG Backend

Check if Cortex Search RAG is already initialized:
```bash
uv run --project <SKILL_DIRECTORY> \
  python -c "
from snowflake.snowpark import Session
session = Session.builder.create()
try:
    rows = session.sql(\"SHOW CORTEX SEARCH SERVICES LIKE 'SCOS_COMPAT_ISSUES_SERVICE'\").collect()
    print(f'EXISTS {rows[0][\"database_name\"]}.{rows[0][\"schema_name\"]}' if rows else 'NOT_FOUND')
except Exception as e:
    print(f'ERROR {e}')
"
```

- **If `EXISTS`**: add `--rag-backend cortex` to the Step 1 command.
- **If `NOT_FOUND` or `ERROR`**: omit `--rag-backend`.

## Step 1: Run the Analyzer

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/analyze_java.py \
  --path <migrated_dir> \
  --recipe-edits <CONVERSION>/migration_state.json \
  --rag-backend trigger \
  --output <CONVERSION>/analysis.json
```

Wait for completion. Verify `analysis.json` is valid JSON.

## Step 2: Supplement for Known Blind Spots

Scan ALL `.java` files in the manifest for patterns the analyzer may miss:

1. **UDF patterns not in analysis**: `new UDF1`, `new UDF2`, `spark.udf().register(`, `UserDefinedFunction`
2. **`checkpoint()` / `localCheckpoint()`** calls
3. **Map column subscript**: `.apply(col("key"))` pattern on a Column
4. **Catalyst imports**: `org.apache.spark.sql.catalyst.*`
5. **Hadoop/HDFS imports**: `org.apache.hadoop.*`
6. **JavaSparkContext**: `new JavaSparkContext(`, `JavaSparkContext jsc`
7. **Spline imports**: `za.co.absa.spline.*`
8. **RDD aggregate / accumulator / §10 ops** — grep for these tokens (the guide's authoritative Java list; all have `Dataset<Row>` workarounds in `../../references/java/rdd-conversion.md`, so flag them, do NOT punt to a blanket TODO):
   - **Aggregate & reduce**: `aggregate(`, `treeAggregate(`, `treeReduce(`, `.reduce(`, `.fold(`, `foldByKey(`, `aggregateByKey(`, `combineByKey(`, `groupByKey(` (§6.1–6.9).
   - **Accumulators**: `longAccumulator(`, `doubleAccumulator(`, `collectionAccumulator(`, `LongAccumulator`, `DoubleAccumulator`, `CollectionAccumulator`, `AccumulatorV2`, `jsc.accumulator(` (deprecated), and the classic `.forEach(r -> acc.add(` shape → a DataFrame aggregation (§6.10–6.16). Hard gaps only: `foreachPartition` sinks, cache-hit counters across `persist`/`unpersist`, threads polling `acc.value()`, `writeStream().foreachBatch` cross-batch state (§7). Any `JavaSparkContext` or `spark.sparkContext()` hop to reach accumulator/parallelize APIs is blocked under Connect (`SPRKCNTSCL1500`).
   - **UDAF**: `UserDefinedAggregateFunction`, `functions.udaf(`, `registerJavaUDAF` (the last silently becomes a scalar UDF — wrong results, no error) → §6.17.
   - **§10 verified ops**: `groupBy(`, `mapPartitionsWithIndex(`, `partitionBy(`, `repartitionAndSortWithinPartitions(`, `collectAsMap(`, `countApprox(`, `countApproxDistinct(`, `saveAsObjectFile(`, `saveAsSequenceFile(`, `getStorageLevel(`, `context(`, `toDebugString(` (§10). Unambiguous names are auto-detected; the `Dataset` homonyms (`groupBy(`) usually mean a `.toJavaRDD()` hop that should not exist — check whether the receiver is really a `JavaRDD`.

For each pattern NOT already in `analysis.json`, append a supplementary entry:
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
