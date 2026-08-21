# EWI Code Reference for SCOS Migration — Java

Official Snowpark Migration Accelerator (SMA) issue codes used when generating dashboard-compatible reports for Java workloads. Java migrations reuse the `SPRKCNTSCL*` family — SMA treats Java under the same JVM/Scala family.

## Code Prefixes

| Prefix | Language | Source |
|--------|----------|---------|
| `SPRKCNTSCL` | Java (JVM) | Snowpark Connect for Spark (JVM family) |
| `SSC-EWI` | SQL | SnowConvert SQL |

## Snowpark Connect Java/JVM Codes (SPRKCNTSCL)

| Code | Message | Category | Doc URL |
|------|---------|----------|---------|
| `SPRKCNTSCL0099` | The file `<element>` was not fully processed by the LLM migration agent — a deterministic fallback transformation was applied. Manual review required. | Warning | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl0099) |
| `SPRKCNTSCL1000` | The element `<element>` is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl1000) |
| `SPRKCNTSCL1500` | The element `<element>` of the library RDD is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl1500) |
| `SPRKCNTSCL2000` | The element `<element>` of the library Streaming is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl2000) |
| `SPRKCNTSCL2500` | The element `<element>` of the library ML is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl2500) |
| `SPRKCNTSCL3000` | The element `<element>` of the library MLLIB is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl3000) |
| `SPRKCNTSCL3500` | The element `<element>` of the library Spark Session is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala#sprkcntscl3500) |

## SCOS Category to EWI Code Mapping (Java)

Java uses the same EWI code table as Scala (both are JVM/Spark-Java family).

### By SCOS Analysis Category

| SCOS Category | Java Code | SMA Category |
|---------------|-----------|--------------|
| RDD operation (`has_rdd_usage`) | `SPRKCNTSCL1500` | Conversion Error |
| Unsupported Module: `org.apache.spark.ml` | `SPRKCNTSCL2500` | Conversion Error |
| Unsupported Module: `org.apache.spark.streaming` | `SPRKCNTSCL2000` | Conversion Error |
| Unsupported Module: `org.apache.spark.mllib` | `SPRKCNTSCL3000` | Conversion Error |
| Unsupported Module: `org.apache.spark.graphx` | `SPRKCNTSCL1000` | Conversion Error |
| SparkSession creation / replacement | `SPRKCNTSCL3500` | Conversion Error |
| JavaSparkContext / SparkContext element | `SPRKCNTSCL1500` | Conversion Error |
| Unsupported Format (avro, orc, delta, binary) | `SPRKCNTSCL1000` | Conversion Error |
| Wildcard/Glob File Read | `SPRKCNTSCL1000` | Conversion Error |
| Unsupported Save Mode | `SPRKCNTSCL1000` | Conversion Error |
| No-Op API (hint, repartition, coalesce) | `SPRKCNTSCL1000` | Warning |
| UDF Serialization | `SPRKCNTSCL1000` | Warning |
| Generic / unclassified | `SPRKCNTSCL1000` | Conversion Error |

### By SCOS Comment Prefix

| Comment Pattern | Java Code | SMA Category |
|-----------------|-----------|--------------|
| `// SCOS: TODO -` | `SPRKCNTSCL1000` | Conversion Error |
| `// SCOS: Performance tip -` | `SPRKCNTSCL1000` | Information |
| `// SCOS:` (fix applied/reviewed) | `SPRKCNTSCL1000` | Warning |

### Keyword-Based Code Refinement

When the generic `*1000` code would be assigned, refine it by checking the `root_cause` field from `analysis.json`:

| Keyword in root_cause | Java Code |
|-----------------------|-----------|
| `rdd`, `parallelize`, `sparkContext`, `JavaSparkContext` | `SPRKCNTSCL1500` |
| `org.apache.spark.ml`, `spark.ml` | `SPRKCNTSCL2500` |
| `streaming`, `DStream`, `StreamingContext`, `JavaStreamingContext` | `SPRKCNTSCL2000` |
| `org.apache.spark.mllib`, `spark.mllib` | `SPRKCNTSCL3000` |
| `SparkSession`, `getOrCreate`, `builder` | `SPRKCNTSCL3500` |
| `wildcard`, `glob`, `*.json`, `*.csv`, `*.parquet` | `SPRKCNTSCL1000` |
| `checkpoint`, `randomSplit`, `isEmpty`, `toLocalIterator` | `SPRKCNTSCL1000` |
| `writeStream`, `StreamingQuery`, `dropDuplicatesWithinWatermark` | `SPRKCNTSCL2000` |
| `fallback`, `partial migration`, `not fully processed` | `SPRKCNTSCL0099` |

## External Documentation

- [Issue Codes by Source (index)](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/README)
- [Snowpark Connect Scala/JVM Codes](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/spark-scala/snowpark-connect-codes-scala)

## Behavioral Difference Codes (SPRKCNTSCL5xxx)

These codes are assigned for behavioral differences between Spark and Snowflake that affect migration output. Java and Scala share the same `SPRKCNTSCL5xxx` code family — the Java API generates identical logical plans to the Scala API and hits the same Snowflake SQL engine.

| Code | BD # | Description | Severity |
|------|------|-------------|----------|
| `SPRKCNTSCL5000` | BD-1 | Division by zero — Spark returns NULL; Snowflake throws error | Critical |
| `SPRKCNTSCL5001` | BD-2 | Type cast failure — Spark returns NULL; Snowflake throws error | Critical |
| `SPRKCNTSCL5002` | BD-3 | `datediff` parameter order reversed | Critical |
| `SPRKCNTSCL5003` | BD-4 | `union()` is position-based — silent data corruption if column orders differ | Critical |
| `SPRKCNTSCL5004` | BD-5 | `element_at` 1-indexed vs 0-indexed | Critical |
| `SPRKCNTSCL5005` | BD-6 | `concat_ws` NULL handling — Spark skips NULLs; Snowflake returns NULL | High |
| `SPRKCNTSCL5006` | BD-7 | `ORDER BY` null ordering defaults differ | High |
| `SPRKCNTSCL5007` | BD-8 | NaN handling — Snowflake has no NaN; returns NULL instead | High |
| `SPRKCNTSCL5008` | BD-9 | `regexp_replace` regex dialect — Java regex vs POSIX | High |
| `SPRKCNTSCL5009` | BD-10 | `greatest`/`least` null handling | High |
| `SPRKCNTSCL5010` | BD-11 | `concat` null handling — Spark skips NULLs; Snowflake returns NULL | High |
| `SPRKCNTSCL5011` | BD-12 | `regexp_extract` no-match returns `""` vs NULL | High |
| `SPRKCNTSCL5012` | BD-13 | `first()`/`last()` non-determinism without ORDER BY | High |
| `SPRKCNTSCL5013` | BD-14 | `round()` banker's rounding (half-even) vs half-up | Medium |
| `SPRKCNTSCL5014` | BD-15 | `explode` with null/empty arrays behavior | Medium |
| `SPRKCNTSCL5015` | BD-16 | String comparison and collation differences | Medium |
| `SPRKCNTSCL5016` | BD-17 | `months_between` return type — Double vs Integer | Medium |
| `SPRKCNTSCL5017` | BD-18 | Null-safe equality (`<=>` vs `EQUAL_NULL`) | Medium |
| `SPRKCNTSCL5018` | BD-19 | Aggregation result column naming — lowercase vs UPPER | Medium |
| `SPRKCNTSCL5019` | BD-20 | `split` regex vs literal delimiter | Medium |
| `SPRKCNTSCL5020` | BD-21 | Integer division result type — int vs DECIMAL | Medium |
| `SPRKCNTSCL5021` | BD-22 | Boolean casting from strings | Low |
| `SPRKCNTSCL5022` | BD-23 | `substring(0)` indexing — treated as 1 vs returns empty | Low |
| `SPRKCNTSCL5023` | BD-24 | `groupBy` result ordering non-determinism | Low |
| `SPRKCNTSCL5024` | BD-25 | Timestamp precision — microseconds vs nanoseconds | Low |
| `SPRKCNTSCL5025` | BD-26 | `approx_count_distinct` precision not configurable | Low |
| `SPRKCNTSCL5026` | BD-27 | `date_format` token differences (`yyyy`/`dd`/`HH` → `YYYY`/`DD`/`HH24`) | Medium |
| `SPRKCNTSCL5027` | BD-28 | `collect_list`/`collect_set` ordering and null behavior | Medium |
| `SPRKCNTSCL5028` | BD-29 | `broadcast`/`repartition`/`coalesce` hints may be silently ignored | Low |
| `SPRKCNTSCL5029` | BD-30 | Integral type widening — `ByteType`/`ShortType`/`IntegerType` → `LongType` | Medium |
| `SPRKCNTSCL5030` | BD-31 | STRUCT field ordering — Snowflake alphabetizes; Spark preserves insertion order | Medium |
| `SPRKCNTSCL5031` | BD-32 | Timestamp type mapping — `TimestampType` behavior depends on `TIMESTAMP_TYPE_MAPPING` session parameter (see also BD-25/5024 for precision) | Medium |
| `SPRKCNTSCL5032` | BD-33 | Parquet pre-Gregorian rebase — timestamps before 1582-10-15 not rebased; silent corruption risk | Medium |

See `references/java/behavioral-differences.md` for Java-specific fix examples for BD-1, BD-3, BD-4, BD-8, BD-9, BD-12, BD-20, BD-27, BD-30–BD-33.
See `references/scala/behavioral-differences.md` for the full Scala fix list (BD-1 through BD-29).
