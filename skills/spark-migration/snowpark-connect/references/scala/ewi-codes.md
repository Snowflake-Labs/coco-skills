# EWI Code Reference for SCOS Migration — Scala

Official Snowpark Migration Accelerator (SMA) issue codes used when generating dashboard-compatible reports for Scala workloads. Consult this reference when classifying issues during Step 3 (Apply Fixes) and when generating Reports/ CSV files.

## Code Prefixes

| Prefix | Language | Source |
|--------|----------|--------|
| `SPRKCNTSCL` | Scala | Snowpark Connect for Scala |
| `SPRKSCL` | Scala | Snowpark API (general Scala) |
| `SSC-EWI` | SQL | SnowConvert SQL |

## Snowpark Connect Scala Codes (SPRKCNTSCL)

| Code | Display Name | Message | Category | Doc URL |
|------|--------------|---------|----------|---------| 
| `SPRKCNTSCL0099` | Partial migration (fallback) | The file `<element>` was not fully processed by the LLM migration agent — a deterministic fallback transformation was applied. Manual review required. | Warning | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl0099) |
| `SPRKCNTSCL1000` | Generic unsupported element | The element `<element>` is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl1000) |
| `SPRKCNTSCL1100` | Databricks-specific API | The element `<element>` of the Databricks library (`com.databricks.*` / `dbutils`) is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl1100) |
| `SPRKCNTSCL1500` | RDD API | The element `<element>` of the library RDD is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl1500) |
| `SPRKCNTSCL2000` | Streaming library | The element `<element>` of the library Streaming is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl2000) |
| `SPRKCNTSCL2500` | ML library | The element `<element>` of the library ML is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl2500) |
| `SPRKCNTSCL3000` | MLLIB library | The element `<element>` of the library MLLIB is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl3000) |
| `SPRKCNTSCL3200` | External cloud I/O / Hadoop credentials | The element `<element>` reads from an external cloud path or uses Hadoop credentials not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl3200) |
| `SPRKCNTSCL3500` | Spark Session | The element `<element>` of the library Spark Session is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl3500) |
| `SPRKCNTSCL5000` | BD-1: Division by zero | Behavioral difference: division-by-zero error/return semantics differ between Spark and Snowflake | Critical | [Link](references/scala/behavioral-differences.md#bd-1-division-by-zero) |
| `SPRKCNTSCL5001` | BD-2: Type cast failure behavior | Behavioral difference: invalid-cast failure behavior differs between Spark and Snowflake | Critical | [Link](references/scala/behavioral-differences.md#bd-2-type-cast-failure-behavior) |
| `SPRKCNTSCL5002` | BD-3: datediff parameter order reversed | Behavioral difference: `datediff` parameter order is reversed in Snowflake | Critical | [Link](references/scala/behavioral-differences.md#bd-3-datediff-parameter-order-reversed) |
| `SPRKCNTSCL5003` | BD-4: union() is position-based | Behavioral difference: `union()` is position-based, not name-based | Critical | [Link](references/scala/behavioral-differences.md#bd-4-union-is-position-based) |
| `SPRKCNTSCL5004` | BD-5: element_at indexing | Behavioral difference: `element_at` indexing semantics differ | Critical | [Link](references/scala/behavioral-differences.md#bd-5-element_at-indexing) |
| `SPRKCNTSCL5005` | BD-6: NULL handling in concat_ws | Behavioral difference: `concat_ws` NULL handling differs | High | [Link](references/scala/behavioral-differences.md#bd-6-null-handling-in-concat_ws) |
| `SPRKCNTSCL5006` | BD-7: ORDER BY null ordering | Behavioral difference: `ORDER BY` null ordering differs | High | [Link](references/scala/behavioral-differences.md#bd-7-order-by-null-ordering) |
| `SPRKCNTSCL5007` | BD-8: NaN handling | Behavioral difference: NaN handling differs | High | [Link](references/scala/behavioral-differences.md#bd-8-nan-handling) |
| `SPRKCNTSCL5008` | BD-9: regexp_replace regex dialect | Behavioral difference: `regexp_replace` regex dialect (Java→POSIX) differs | High | [Link](references/scala/behavioral-differences.md#bd-9-regexp_replace-regex-dialect) |
| `SPRKCNTSCL5009` | BD-10: greatest/least null handling | Behavioral difference: `greatest`/`least` NULL handling differs | High | [Link](references/scala/behavioral-differences.md#bd-10-greatestleast-null-handling) |
| `SPRKCNTSCL5010` | BD-11: concat null handling | Behavioral difference: `concat` NULL handling differs | High | [Link](references/scala/behavioral-differences.md#bd-11-concat-null-handling) |
| `SPRKCNTSCL5011` | BD-12: regexp_extract no-match behavior | Behavioral difference: `regexp_extract` no-match behavior differs | High | [Link](references/scala/behavioral-differences.md#bd-12-regexp_extract-no-match-behavior) |
| `SPRKCNTSCL5012` | BD-13: first()/last() non-determinism | Behavioral difference: `first()`/`last()` non-determinism differs | High | [Link](references/scala/behavioral-differences.md#bd-13-firstlast-non-determinism) |
| `SPRKCNTSCL5013` | BD-14: round() banker's rounding | Behavioral difference: `round()` uses banker's rounding in Snowflake | Medium | [Link](references/scala/behavioral-differences.md#bd-14-round-bankers-rounding) |
| `SPRKCNTSCL5014` | BD-15: explode with null/empty arrays | Behavioral difference: `explode` with null/empty arrays differs | Medium | [Link](references/scala/behavioral-differences.md#bd-15-explode-with-nullempty-arrays) |
| `SPRKCNTSCL5015` | BD-16: String comparison and collation | Behavioral difference: string comparison/collation differs | Medium | [Link](references/scala/behavioral-differences.md#bd-16-string-comparison-and-collation) |
| `SPRKCNTSCL5016` | BD-17: months_between return type | Behavioral difference: `months_between` return type (integer vs double) differs | Medium | [Link](references/scala/behavioral-differences.md#bd-17-months_between-return-type) |
| `SPRKCNTSCL5017` | BD-18: Null-safe equality | Behavioral difference: null-safe equality (`<=>`) differs | Medium | [Link](references/scala/behavioral-differences.md#bd-18-null-safe-equality) |
| `SPRKCNTSCL5018` | BD-19: Aggregation result column naming | Behavioral difference: aggregation result column auto-naming differs | Medium | [Link](references/scala/behavioral-differences.md#bd-19-aggregation-result-column-naming) |
| `SPRKCNTSCL5019` | BD-20: split regex vs literal delimiter | Behavioral difference: `split` regex vs literal delimiter differs | Medium | [Link](references/scala/behavioral-differences.md#bd-20-split-regex-vs-literal-delimiter) |
| `SPRKCNTSCL5020` | BD-21: Integer division result type | Behavioral difference: integer division result type differs | Medium | [Link](references/scala/behavioral-differences.md#bd-21-integer-division-result-type) |
| `SPRKCNTSCL5021` | BD-22: Boolean casting from strings | Behavioral difference: boolean casting from strings differs | Low | [Link](references/scala/behavioral-differences.md#bd-22-boolean-casting-from-strings) |
| `SPRKCNTSCL5022` | BD-23: substring(0) indexing | Behavioral difference: `substring(0)` indexing differs | Low | [Link](references/scala/behavioral-differences.md#bd-23-substring0-indexing) |
| `SPRKCNTSCL5023` | BD-24: groupBy result ordering | Behavioral difference: `groupBy` result ordering differs | Low | [Link](references/scala/behavioral-differences.md#bd-24-groupby-result-ordering) |
| `SPRKCNTSCL5024` | BD-25: Timestamp precision | Behavioral difference: timestamp precision differs | Low | [Link](references/scala/behavioral-differences.md#bd-25-timestamp-precision) |
| `SPRKCNTSCL5025` | BD-26: approx_count_distinct precision | Behavioral difference: `approx_count_distinct` precision differs | Low | [Link](references/scala/behavioral-differences.md#bd-26-approx_count_distinct-precision) |
| `SPRKCNTSCL5026` | BD-27: date_format token differences | Behavioral difference: `date_format` token differences | Medium | [Link](references/scala/behavioral-differences.md#bd-27-date_format-token-differences) |
| `SPRKCNTSCL5027` | BD-28: collect_list/collect_set ordering and nulls | Behavioral difference: `collect_list`/`collect_set` ordering and nulls differ | Medium | [Link](references/scala/behavioral-differences.md#bd-28-collect_listcollect_set-ordering-and-nulls) |
| `SPRKCNTSCL5028` | BD-29: broadcast/repartition/coalesce | Behavioral difference: `broadcast`/`repartition`/`coalesce` semantics (needs investigation) | Low | [Link](references/scala/behavioral-differences.md#bd-29-broadcastrepartitioncoalesce-needs-investigation) |
| `SPRKCNTSCL3600` | AWS Glue entry point | AWS Glue entry point `<element>` (GlueContext / Job / Job.commit) replaced with a Snowpark Connect session | Conversion Error | N/A |
| `SPRKCNTSCL3601` | AWS Glue job arguments | AWS Glue `GlueArgParser.getResolvedOptions` replaced with command-line argument parsing | Warning | N/A |
| `SPRKCNTSCL3602` | AWS Glue Data Catalog read | AWS Glue Data Catalog read `<element>` repointed to a native Snowflake table read (identifier case normalized) | Conversion Error | N/A |
| `SPRKCNTSCL3603` | AWS Glue `ApplyMapping` | AWS Glue `ApplyMapping` `<element>` converted to a select/cast/alias projection | Conversion Error | N/A |
| `SPRKCNTSCL3604` | AWS Glue `Filter.apply` null semantics | AWS Glue `Filter.apply` predicate `<element>` requires a null-safe Column rewrite | Conversion Error | N/A |
| `SPRKCNTSCL3605` | AWS Glue DynamicFrame | AWS Glue DynamicFrame construct `<element>` converted to a native DataFrame operation | Warning | N/A |
| `SPRKCNTSCL3606` | AWS Glue job bookmark | AWS Glue job bookmark `<element>` has no SCOS equivalent; use an external-stage directory table + Stream | Conversion Error | N/A |
| `SPRKCNTSCL3608` | Snowflake connector writeback | Snowflake Spark connector `<element>` (preactions/postactions) requires a staged-table + MERGE rewrite | Conversion Error | N/A |
| `SPRKCNTSCL3609` | AWS Glue Data Catalog write | AWS Glue Data Catalog write `<element>` repointed to a native Snowflake table write | Conversion Error | N/A |
| `SPRKCNTSCL6000` | JDBC / external database access | The element `<element>` uses JDBC or external database access not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl6000) |

> **AWS Glue family (`SPRKCNTSCL36xx`).** Glue Scala workloads are migrated per
> the recipe catalog in [`glue-recipes.md`](glue-recipes.md) (recipes G1–G12).
> `3602` and `3604` are the two parity-critical codes: `3602` because a Glue
> Data Catalog read exposes **lowercase** column names while a native Snowflake
> read returns **UPPERCASE** (case-sensitive downstream logic silently drops
> columns, including primary keys), and `3604` because Glue evaluates a Scala
> `DynamicRecord` predicate per-row while Spark applies SQL three-valued logic
> (a naive port silently drops null rows). Note `3607` (`gluetypes`) is
> Python-only — the Glue Scala SDK uses standard Spark types and has no
> equivalent module. AWS Glue does not support Java ETL jobs, so these codes
> apply to Scala only.

## SCOS Category to EWI Code Mapping

Use this table to determine the correct EWI code when generating `Issues.csv` from SCOS analysis findings.

### By SCOS Analysis Category

| SCOS Category | Scala Code | SMA Category |
|---------------|------------|--------------|
| RDD operation (`has_rdd_usage`) | `SPRKCNTSCL1500` | Conversion Error |
| Unsupported Module: `org.apache.spark.ml` | `SPRKCNTSCL2500` | Conversion Error |
| Unsupported Module: `org.apache.spark.streaming` | `SPRKCNTSCL2000` | Conversion Error |
| Unsupported Module: `org.apache.spark.mllib` | `SPRKCNTSCL3000` | Conversion Error |
| Unsupported Module: `org.apache.spark.graphx` | `SPRKCNTSCL1000` | Conversion Error |
| SparkSession creation / replacement | `SPRKCNTSCL3500` | Conversion Error |
| SparkContext element | `SPRKCNTSCL1500` | Conversion Error |
| Unsupported Format (avro, orc, delta, binary) | `SPRKCNTSCL1000` | Conversion Error |
| Wildcard/Glob File Read (`*.json`, `*.csv`, etc.) | `SPRKCNTSCL1000` | Conversion Error |
| Unsupported Save Mode | `SPRKCNTSCL1000` | Conversion Error |
| Unsupported Option | `SPRKCNTSCL1000` | Warning |
| No-Op API (hint, repartition, coalesce) | `SPRKCNTSCL1000` | Warning |
| No-Op Config | `SPRKCNTSCL1000` | Warning |
| UDF Serialization | `SPRKCNTSCL1000` | Warning |
| Performance Optimization | `SPRKCNTSCL1000` | Information |
| Recommended Improvement (SF Connector pushdown) | `SPRKCNTSCL1000` | Information |
| Map Column Subscript with Column Key | `SPRKCNTSCL1000` | Conversion Error |
| AWS Glue entry point (GlueContext, Job, Job.commit) | `SPRKCNTSCL3600` | Conversion Error |
| AWS Glue job arguments (GlueArgParser.getResolvedOptions) | `SPRKCNTSCL3601` | Warning |
| AWS Glue Data Catalog read (getCatalogSource / getDynamicFrame) | `SPRKCNTSCL3602` | Conversion Error |
| AWS Glue ApplyMapping projection | `SPRKCNTSCL3603` | Conversion Error |
| AWS Glue Filter.apply row-wise predicate | `SPRKCNTSCL3604` | Conversion Error |
| AWS Glue DynamicFrame transform (ResolveChoice, DropFields, SelectFields, RenameField, toDF, fromDF) | `SPRKCNTSCL3605` | Warning |
| AWS Glue job bookmark (transformationContext) | `SPRKCNTSCL3606` | Conversion Error |
| Snowflake Spark connector preactions/postactions writeback | `SPRKCNTSCL3608` | Conversion Error |
| AWS Glue Data Catalog write (getSinkWithFormat / writeDynamicFrame) | `SPRKCNTSCL3609` | Conversion Error |
| Generic / unclassified | `SPRKCNTSCL1000` | Conversion Error |

### By SCOS Comment Prefix

| Comment Pattern | Scala Code | SMA Category |
|-----------------|------------|--------------|
| `// SCOS: TODO -` | `SPRKCNTSCL1000` | Conversion Error |
| `// SCOS: Performance tip -` | `SPRKCNTSCL1000` | Information |
| `// SCOS:` (fix applied/reviewed) | `SPRKCNTSCL1000` | Warning |

### Keyword-Based Code Refinement

When the generic `*1000` code would be assigned, refine it by checking the `root_cause` field from `analysis.json`:

| Keyword in root_cause | Scala Code |
|-----------------------|------------|
| `rdd`, `parallelize`, `sparkContext`, `SparkContext` | `SPRKCNTSCL1500` |
| `longAccumulator`, `doubleAccumulator`, `collectionAccumulator`, `LongAccumulator`, `DoubleAccumulator`, `CollectionAccumulator`, `AccumulatorV2` (accumulators are CONVERTIBLE → DataFrame `agg`, not a blanket TODO; no `sc.accumulator`/`AccumulatorParam` in Scala) | `SPRKCNTSCL1500` |
| `treeAggregate`, `treeReduce`, `collectAsMap`, `countApprox`, `countApproxDistinct`, `meanApprox`, `sumApprox`, `repartitionAndSortWithinPartitions`, `toDebugString`, `saveAsObjectFile` (RDD-exclusive aggregate/§10 ops → DataFrame rewrites, see `rdd-conversion.md`) | `SPRKCNTSCL1500` |
| `org.apache.spark.ml`, `spark.ml` | `SPRKCNTSCL2500` |
| `streaming`, `DStream`, `StreamingContext` | `SPRKCNTSCL2000` |
| `org.apache.spark.mllib`, `spark.mllib` | `SPRKCNTSCL3000` |
| `SparkSession`, `getOrCreate`, `builder` | `SPRKCNTSCL3500` |
| `wildcard`, `glob`, `*.json`, `*.csv`, `*.parquet` | `SPRKCNTSCL1000` |
| `create_map`, `map`, `apply`, `element_at`, `Column subscript`, `UNSUPPORTED_DATA_TYPE` | `SPRKCNTSCL1000` |
| `GlueContext`, `com.amazonaws.services.glue.GlueContext`, `Job.init`, `Job.commit` | `SPRKCNTSCL3600` |
| `GlueArgParser`, `getResolvedOptions`, `com.amazonaws.services.glue.util.GlueArgParser` | `SPRKCNTSCL3601` |
| `getCatalogSource`, `getDynamicFrame`, `from_catalog` | `SPRKCNTSCL3602` |
| `ApplyMapping`, `com.amazonaws.services.glue.transforms.ApplyMapping` | `SPRKCNTSCL3603` |
| `Filter.apply`, `com.amazonaws.services.glue.transforms.Filter` | `SPRKCNTSCL3604` |
| `DynamicFrame`, `DynamicFrameCollection`, `ResolveChoice`, `DropFields`, `SelectFields`, `RenameField` | `SPRKCNTSCL3605` |
| `transformationContext`, `job bookmark`, `Job.commit`, `Job.init` | `SPRKCNTSCL3606` |
| `preactions`, `postactions`, `net.snowflake.spark.snowflake` | `SPRKCNTSCL3608` |
| `getSinkWithFormat`, `writeDynamicFrame`, `write_dynamic_frame` | `SPRKCNTSCL3609` |
| `checkpoint`, `localCheckpoint`, `randomSplit`, `sortWithinPartitions`, `isEmpty`, `toLocalIterator` | `SPRKCNTSCL1000` |
| `withWatermark`, `writeStream`, `dropDuplicatesWithinWatermark` | `SPRKCNTSCL2000` |
| `javaRDD`, `toJavaRDD`, `toJSON`, `reduce`, `queryExecution`, `sqlContext` | `SPRKCNTSCL1000` |
| `com.databricks`, `dbutils`, `Databricks` | `SPRKCNTSCL1100` |
| `hadoopConfiguration`, `fs.s3a`, `FileSystem`, `s3://`, `abfss://`, `wasbs://` | `SPRKCNTSCL3200` |
| `JDBC`, `jdbc`, `DataFrameReader.jdbc`, `Connection`, `DriverManager` | `SPRKCNTSCL6000` |
| `datediff`, `date_format`, `months_between`, `union`, `element_at`, `concat_ws`, `greatest`, `least`, `regexp_replace`, `regexp_extract`, `first`, `last`, `round`, `split`, `substring`, `collect_list`, `collect_set`, `approx_count_distinct` | `SPRKCNTSCL5000`–`SPRKCNTSCL5028` (see `behavioral-differences.md` for the exact code per BD) |
| `fallback`, `partial migration`, `not fully processed` | `SPRKCNTSCL0099` |

## External Documentation

- [Issue Codes by Source (index)](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/README)
- [Snowpark Connect Scala Codes](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala)
- [Spark-Scala Issue Codes (SPRKSCL)](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/README)
- [SQL Issue Codes (SSC-EWI)](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/sql/README)
