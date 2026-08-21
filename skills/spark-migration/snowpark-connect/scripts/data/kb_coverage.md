# Unified Trigger-KB — Coverage

Raw rules ingested: **1374**
Unique anchors after merge: **427**
Auto-firing rules (reliable literal trigger): **307**
Reference-only rules (manual, no auto-trigger): **120**
Anchors backed by >1 source: **145**

## Raw rules by source

| Source | Rules |
|---|---|
| gaps-report | 113 |
| behavioral | 169 |
| csv | 1092 |

## Merged rules by severity

| Severity | Rules |
|---|---|
| high | 30 |
| medium | 234 |
| low | 163 |

## Merged rules by disposition

| Disposition | Rules |
|---|---|
| annotate | 371 |
| awareness | 37 |
| rewrite | 19 |

## Merged rules by trigger kind

| Kind | Rules |
|---|---|
| python_or_sql | 186 |
| manual | 120 |
| python_method | 105 |
| sql_construct | 16 |

## Sample high-severity rules

- **collect_list** [P0/annotate] — Snowflake's `ARRAY_AGG` in window context does not respect the `ORDER BY` direction for element accumulation order — it always ret
- **com.hortonworks.spark.sql.hive.llap.HiveWarehouseSession.session** [trigger/annotate] — HiveWarehouseSession (the Hortonworks/Cloudera connector for Hive LLAP from Spark) is not supported in Snowpark Connect; replace w
- **CrossValidator** [trigger/annotate] — PySpark ML CrossValidator with ParamGridBuilder (hyperparameter tuning) is not available in Snowpark Connect; replace with Snowfla
- **dbutils.fs.cp** [trigger/annotate] — dbutils.fs.cp/mv/rm perform DBFS file operations not available in Snowpark Connect; use Snowflake stage operations instead (COPY I
- **dbutils.fs.ls** [trigger/annotate] — dbutils.fs (ls/head/mkdirs) is a DBFS/cloud-storage filesystem abstraction not available in Snowpark Connect; use Snowflake stages
- **dbutils.fs.mount** [trigger/annotate] — dbutils.fs.mount/unmount attach cloud storage as DBFS mount points and are not available in Snowpark Connect; use Snowflake extern
- **dbutils.notebook.exit** [trigger/annotate] — dbutils.notebook.exit() returns a value to a parent Databricks notebook and has no Snowpark Connect equivalent; use stored-procedu
- **dbutils.notebook.run** [trigger/annotate] — dbutils.notebook.run() runs another Databricks notebook as a child job and is not available in Snowpark Connect (no notebook-orche
- **dbutils.secrets.get** [trigger/annotate] — dbutils.secrets (get/list/listScopes) is Databricks secret management and is not available in Snowpark Connect; use Snowflake Secr
- **dbutils.widgets.text** [trigger/annotate] — dbutils.widgets (text/get/dropdown) create interactive notebook parameters in Databricks and are not available in Snowpark Connect
- **df.sampleBy** [trigger/annotate] — DataFrame.sampleBy / stat.sampleBy is non-deterministic in SCOS: it maps to Snowflake sampling and ignores the seed, so the sample
- **explode_outer** [trigger/annotate] — explode_outer filters out NULL elements from an array before exploding in SCOS, so NULL array elements are dropped from the output

## Manual additions — RDD aggregate/accumulator migration guide

The totals above are the build-pipeline output and are **not** recomputed here.
The following rules were appended by hand from the SCOS RDD-migration guide
(`rdd_guide:*` rule_ids, `sources: scos-rdd-migration-guide`) to close the
aggregate/accumulator and §10 "additional verified RDD operations" gap. Each
`kind: python_method`, auto-firing on its RDD-exclusive method leaf, pointing the
fixer at the matching section of `references/python/rdd-conversion.md`:

- **Aggregate ops** (`treeAggregate`, `treeReduce`) — [Workaround], `medium`.
- **§10 verified ops** — `collectAsMap`, `countApprox`, `meanApprox`,
  `sumApprox`, `collectWithJobGroup`, `mapPartitionsWithSplit`,
  `repartitionAndSortWithinPartitions`, `saveAsPickleFile`, `saveAsObjectFile`,
  `getStorageLevel`, `toDebugString` — [Workaround]/[Partial], `medium`;
  `countApproxDistinct` ([Native], `low`) maps to `F.approx_count_distinct` (do
  not conflate with the DataFrame API of the same intent). `saveAsObjectFile` and
  `saveAsPickleFile` are the two sibling [Partial] save ops (parquet/table
  round-trip; §16.11 / §16.12) — kept symmetric across every layer.
- **Accumulator constructor** — `collectionAccumulator` — [Workaround],
  `medium`, `SPRKCNTPY4000` (driver-side accumulator → DataFrame aggregation, not
  a blanket TODO; §12).
- **Augmented** (not duplicated) the existing `foreachPartition`,
  `registerJavaUDAF`, and `UserDefinedAggregateFunction` notes with the
  hard-gap / UDAF routing from the guide (§13 / §17).

**Durability — preserve across KB regeneration.** The `rdd_guide:*` rules are
**hand-appended** to `kb_rules.json`, not derived from the CSV/build pipeline.
Any future CSV-driven KB regeneration MUST re-append (or otherwise retain) them
— a naive regenerate-from-CSV would silently drop the entire
aggregate/accumulator/§10 coverage and the RDD reference-sync + trigger-KB firing
tests (`test_rdd_migration_guide_rules_fire`) would then fail. Treat
`sources: scos-rdd-migration-guide` as the marker for these manual records.

## Manual additions — Scala/Java RDD aggregate/accumulator migration guide

The Scala/Java mirror of the section above. 15 `rdd_guide_scala:*` rules were
appended by hand from the SCOS Scala/Java RDD-migration guide
(`sources: scos-rdd-migration-guide`, `ewi_code: SPRKCNTSCL1500`,
`kind: python_method`), auto-firing on the RDD-exclusive method leaf and pointing
the fixer at the matching section of `references/scala/rdd-conversion.md`:

- **Aggregate ops** (`treeAggregate` §6.2, `treeReduce` §6.4) — [Workaround],
  `medium`.
- **§10 verified ops** — `collectAsMap` (§10.5), `countApprox` (§10.6),
  `meanApprox` (§10.8), `sumApprox` (§10.9), `mapPartitionsWithIndex` (§10.2),
  `repartitionAndSortWithinPartitions` (§10.4), `saveAsObjectFile` /
  `saveAsSequenceFile` (§10.11), `getStorageLevel` (§10.13), `toDebugString`
  (§10.16) — [Workaround]/[Partial], `medium`; `countApproxDistinct` (§10.7,
  [Native], `low`) maps to `approx_count_distinct`. `saveAsObjectFile` and
  `saveAsSequenceFile` are the sibling [Partial] save ops (parquet/table
  round-trip) — kept symmetric with the Python `saveAsObjectFile` /
  `saveAsPickleFile` pair.
- **Accumulator constructors** — `longAccumulator` (§6.10) and
  `collectionAccumulator` (§6.12) — [Workaround], `medium`. Scala has NO
  top-level `sc.accumulator`; a driver-side count/sum/collect accumulator
  updated in a `foreach` maps to a conditional DataFrame aggregate
  (`agg(sum(when(...)))`, `collect_set`, `groupBy.count`), NOT a blanket TODO.
  Only cache-hit / mid-job `acc.value` / `foreachPartition` / `foreachBatch`
  are true hard gaps.

**Durability — preserve across KB regeneration.** Like the `rdd_guide:*` records,
the `rdd_guide_scala:*` rules are **hand-appended**, not derived from the CSV/build
pipeline. Any future regeneration MUST re-append (or retain) them or the
Scala RDD reference-sync + trigger-KB firing coverage is silently dropped. Treat
`sources: scos-rdd-migration-guide` + `ewi_code: SPRKCNTSCL1500` as the marker for
these manual Scala records.
