# EWI Code Reference for Snowpark Connect Migration

Official Snowpark Connect issue codes used when generating dashboard-compatible reports. Consult this reference when classifying issues during Step 3 (Apply Fixes) and when generating Reports/ CSV files.

## Code Prefixes

> **Note:** The code prefixes below (e.g., `SPRKCNTPY`) are the machine-readable identifiers used in reports, doc URLs, and generated CSV files. The "Display Name" column shows a human-readable, dash-separated form for easier scanning.

| Prefix | Display Name | Language | Source |
|--------|--------------|----------|--------|
| `SPRKCNTPY` | `SPRK-CONNECT-PY` | Python | Snowpark Connect for Python |
| `SPRKPY` | `SPRK-PY` | Python | Snowpark API (general Python) |
| `SSC-EWI` | `SSC-EWI` | SQL | SnowConvert SQL |

## Snowpark Connect Python Codes (SPRKCNTPY)

| Code | Message | Category | Doc URL |
|------|---------|----------|---------|
| `SPRKCNTPY1000` | The element `<element>` is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/snowpark-connect-codes-python#sprkcntpy1000) |
| `SPRKCNTPY1001` | SparkSession creation replaced with Snowpark Connect Session | Warning | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/snowpark-connect-codes-python#sprkcntpy1001) |
| `SPRKCNTPY1500` | The element `<element>` of the library RDD is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/snowpark-connect-codes-python#sprkcntpy1500) |
| `SPRKCNTPY2000` | The element `<element>` of the library Streaming is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/snowpark-connect-codes-python#sprkcntpy2000) |
| `SPRKCNTPY2500` | The element `<element>` of the library ML is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/snowpark-connect-codes-python#sprkcntpy2500) |
| `SPRKCNTPY3000` | The element `<element>` of the library MLLIB is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/snowpark-connect-codes-python#sprkcntpy3000) |
| `SPRKCNTPY3500` | The element `<element>` of the library Spark Session is not supported for Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/snowpark-connect-codes-python#sprkcntpy3500) |
| `SPRKCNTPY3501` | AppName method replaced with SetName for Snowpark Connect | Warning | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/snowpark-connect-codes-python#sprkcntpy3501) |
| `SPRKCNTPY3502` | Master method replaced with SetName for Snowpark Connect | Warning | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/snowpark-connect-codes-python#sprkcntpy3502) |
| `SPRKCNTPY4000` | SparkContext element is not supported by Snowpark Connect | Conversion Error | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/snowpark-connect-codes-python#sprkcntpy4000) |
| `SPRKCNTPY4001` | SparkContext instantiation converted to Snowpark Connect session | Warning | [Link](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/snowpark-connect-codes-python#sprkcntpy4001) |
| `SPRKCNTPY4002` | SparkContext property `<property>` replaced with static fallback | Warning | N/A |
| `SPRKCNTPY3100` | JVM-only library `<library>` requires manual replacement with native DataFrame operations | Conversion Error | N/A |
| `SPRKCNTPY3200` | Hadoop filesystem pattern `<pattern>` replaced with cloud-native equivalent | Conversion Error | N/A |
| `SPRKCNTPY3201` | DBFS path `<path>` replaced with Snowflake stage reference | Conversion Error | N/A |
| `SPRKCNTPY3202` | Hadoop credential configuration replaced with Snowflake storage integration | Conversion Error | N/A |
| `SPRKCNTPY3300` | USE DATABASE/SCHEMA statement commented out; table references rewritten to FQN | Conversion Error | N/A |
| `SPRKCNTPY3400` | Delta Lake `<pattern>` converted to Snowflake native equivalent | Conversion Error | N/A |
| `SPRKCNTPY3401` | Delta SQL `<keyword>` (OPTIMIZE/VACUUM/ZORDER) replaced with Snowflake equivalent | Warning | N/A |
| `SPRKCNTPY2501` | ML pipeline `<class>` converted to snowflake.ml equivalent | Conversion Error | N/A |
| `SPRKCNTPY2502` | VectorAssembler removed; feature columns passed directly to estimator | Warning | N/A |
| `SPRKCNTPY2600` | UDTF/UDAF pattern `<pattern>` requires structural conversion to Snowpark handler | Conversion Error | N/A |
| `SPRKCNTPY3600` | AWS Glue entry point `<element>` (GlueContext / Job / job.commit) replaced with a Snowpark Connect session | Conversion Error | N/A |
| `SPRKCNTPY3601` | AWS Glue `getResolvedOptions` replaced with argparse argument parsing | Warning | N/A |
| `SPRKCNTPY3602` | AWS Glue Data Catalog read `<element>` repointed to a native Snowflake table read (identifier case normalized) | Conversion Error | N/A |
| `SPRKCNTPY3603` | AWS Glue `ApplyMapping` converted to a select/cast/alias projection | Conversion Error | N/A |
| `SPRKCNTPY3604` | AWS Glue `Filter.apply` row-wise predicate requires a null-safe Column rewrite | Conversion Error | N/A |
| `SPRKCNTPY3605` | AWS Glue DynamicFrame construct `<element>` converted to a native DataFrame operation | Warning | N/A |
| `SPRKCNTPY3606` | AWS Glue job bookmark `<element>` has no SCOS equivalent; use an external-stage directory table + Stream | Conversion Error | N/A |
| `SPRKCNTPY3607` | AWS Glue `gluetypes` instance comparison converted to a `pyspark.sql.types` isinstance check | Warning | N/A |
| `SPRKCNTPY3608` | Snowflake Spark connector `<element>` (preactions/postactions) requires a staged-table + MERGE rewrite | Conversion Error | N/A |
| `SPRKCNTPY3609` | AWS Glue Data Catalog write `<element>` repointed to a native Snowflake table write | Conversion Error | N/A |

> **AWS Glue family (`SPRKCNTPY36xx`).** Glue workloads are migrated per the recipe
> catalog in [`glue-recipes.md`](glue-recipes.md) (recipes G1–G12). `3602` and `3604`
> are the two parity-critical codes: `3602` because a Glue Data Catalog read exposes
> **lowercase** column names while a native Snowflake read returns **UPPERCASE**
> (case-sensitive downstream logic silently drops columns, including primary keys),
> and `3604` because Glue evaluates a Python predicate row-wise while Spark applies
> SQL three-valued logic (a naive port silently drops null rows).

## Snowpark Connect Category to EWI Code Mapping

Use this table to determine the correct EWI code when generating `Issues.csv` from Snowpark Connect analysis findings.

### By Snowpark Connect Analysis Category

| Snowpark Connect Category | Python Code | SMA Category |
|---------------|-------------|--------------|
| RDD operation (`has_rdd_usage`) | `SPRKCNTPY1500` | Conversion Error |
| Unsupported Module: `pyspark.ml` | `SPRKCNTPY2500` | Conversion Error |
| Unsupported Module: `pyspark.streaming` | `SPRKCNTPY2000` | Conversion Error |
| Unsupported Module: `pyspark.mllib` | `SPRKCNTPY3000` | Conversion Error |
| SparkSession creation / replacement | `SPRKCNTPY1001` | Warning |
| SparkContext element | `SPRKCNTPY4000` | Conversion Error |
| SparkContext property replacement | `SPRKCNTPY4002` | Warning |
| Legacy SQL/Hive entry point (`sqlContext`, `SQLContext`, `HiveContext`) | `SPRKCNTPY3500` | Conversion Error |
| JVM-only library (pydeequ, Deequ, Great Expectations Spark) | `SPRKCNTPY3100` | Conversion Error |
| Hadoop Filesystem (FileSystem.get, hadoop.fs.Path, etc.) | `SPRKCNTPY3200` | Conversion Error |
| DBFS path (dbfs:/, /mnt/) | `SPRKCNTPY3201` | Conversion Error |
| Hadoop credential configuration | `SPRKCNTPY3202` | Conversion Error |
| USE DATABASE/SCHEMA statement | `SPRKCNTPY3300` | Conversion Error |
| Delta Lake API (DeltaTable, format("delta")) | `SPRKCNTPY3400` | Conversion Error |
| Delta SQL (OPTIMIZE, VACUUM, ZORDER) | `SPRKCNTPY3401` | Warning |
| ML Pipeline (estimator, VectorAssembler, Pipeline) | `SPRKCNTPY2501` | Conversion Error |
| VectorAssembler removal | `SPRKCNTPY2502` | Warning |
| UDTF/UDAF pattern | `SPRKCNTPY2600` | Conversion Error |
| Unsupported Format (avro, orc, delta, binary) | `SPRKCNTPY1000` | Conversion Error |
| Wildcard/Glob File Read (`*.json`, `*.csv`, etc.) | `SPRKCNTPY1000` | Conversion Error |
| Unsupported Save Mode | `SPRKCNTPY1000` | Conversion Error |
| Unsupported Option | `SPRKCNTPY1000` | Warning |
| No-Op API (hint, repartition, coalesce) | `SPRKCNTPY1000` | Warning |
| No-Op Config | `SPRKCNTPY1000` | Warning |
| UDF Serialization | `SPRKCNTPY1000` | Warning |
| Performance Optimization | `SPRKCNTPY1000` | Information |
| Recommended Improvement (SF Connector pushdown) | `SPRKCNTPY1000` | Information |
| Map Column Subscript with Column Key | `SPRKCNTPY1000` | Conversion Error |
| AWS Glue entry point (GlueContext, Job, job.commit) | `SPRKCNTPY3600` | Conversion Error |
| AWS Glue job arguments (getResolvedOptions) | `SPRKCNTPY3601` | Warning |
| AWS Glue Data Catalog read (create_dynamic_frame.*) | `SPRKCNTPY3602` | Conversion Error |
| AWS Glue ApplyMapping projection | `SPRKCNTPY3603` | Conversion Error |
| AWS Glue Filter.apply row-wise predicate | `SPRKCNTPY3604` | Conversion Error |
| AWS Glue DynamicFrame transform (ResolveChoice, DropFields, SelectFields, RenameField, fromDF) | `SPRKCNTPY3605` | Warning |
| AWS Glue job bookmark (transformation_ctx) | `SPRKCNTPY3606` | Conversion Error |
| AWS Glue gluetypes comparison | `SPRKCNTPY3607` | Warning |
| Snowflake Spark connector preactions/postactions writeback | `SPRKCNTPY3608` | Conversion Error |
| AWS Glue Data Catalog write (write_dynamic_frame.*) | `SPRKCNTPY3609` | Conversion Error |
| Generic / unclassified | `SPRKCNTPY1000` | Conversion Error |

### By Snowpark Connect Comment Prefix

| Comment Pattern | Python Code | Snowpark Connect Category |
|-----------------|-------------|--------------|
| `# SCOS: TODO -` | `SPRKCNTPY1000` | Conversion Error |
| `# SCOS: Performance tip -` | `SPRKCNTPY1000` | Information |
| `# SCOS:` (fix applied/reviewed) | `SPRKCNTPY1000` | Warning |

### Keyword-Based Code Refinement

When the generic `*1000` code would be assigned, refine it by checking the `root_cause` field from `analysis.json`:

| Keyword in root_cause | Python Code |
|-----------------------|-------------|
| `rdd`, `parallelize`, `sparkContext.` | `SPRKCNTPY1500` |
| `pyspark.ml`, `spark.ml`, `MLlib` | `SPRKCNTPY2500` |
| `streaming`, `DStream` | `SPRKCNTPY2000` |
| `pyspark.mllib`, `spark.mllib` | `SPRKCNTPY3000` |
| `SparkSession`, `getOrCreate` | `SPRKCNTPY1001` |
| `sqlContext`, `SQLContext`, `HiveContext` | `SPRKCNTPY3500` |
| `SparkContext` | `SPRKCNTPY4000` |
| `sparkContext.master`, `sparkContext.applicationId`, `sparkContext.getConf` | `SPRKCNTPY4002` |
| `pydeequ`, `VerificationSuite`, `Deequ`, `CheckLevel` | `SPRKCNTPY3100` |
| `hadoop.fs.FileSystem`, `hadoop.fs.Path`, `hadoopConfiguration` | `SPRKCNTPY3200` |
| `dbfs:/`, `/mnt/`, `DBFS` | `SPRKCNTPY3201` |
| `USE DATABASE`, `USE SCHEMA`, `setCurrentDatabase` | `SPRKCNTPY3300` |
| `DeltaTable`, `delta.tables`, `format("delta")` | `SPRKCNTPY3400` |
| `OPTIMIZE`, `VACUUM`, `ZORDER` | `SPRKCNTPY3401` |
| `LogisticRegression`, `RandomForest`, `Pipeline`, `CrossValidator`, `VectorAssembler` | `SPRKCNTPY2501` |
| `@udtf`, `PandasUDFType.GROUPED_AGG`, `PandasUDFType.SCALAR`, `UDAF` | `SPRKCNTPY2600` |
| `wildcard`, `glob`, `*.json`, `*.csv`, `*.parquet` | `SPRKCNTPY1000` |
| `create_map`, `__getitem__`, `element_at`, `Column subscript`, `UNSUPPORTED_DATA_TYPE` | `SPRKCNTPY1000` |
| `GlueContext`, `awsglue.context`, `awsglue.job`, `job.init`, `job.commit` | `SPRKCNTPY3600` |
| `getResolvedOptions`, `awsglue.utils` | `SPRKCNTPY3601` |
| `create_dynamic_frame`, `from_catalog`, `getSource` | `SPRKCNTPY3602` |
| `ApplyMapping` | `SPRKCNTPY3603` |
| `Filter.apply` | `SPRKCNTPY3604` |
| `DynamicFrame`, `DynamicFrameCollection`, `ResolveChoice`, `DropFields`, `SelectFields`, `RenameField`, `fromDF` | `SPRKCNTPY3605` |
| `transformation_ctx`, `job bookmark` | `SPRKCNTPY3606` |
| `gluetypes`, `awsglue.gluetypes` | `SPRKCNTPY3607` |
| `preactions`, `postactions`, `net.snowflake.spark.snowflake` | `SPRKCNTPY3608` |
| `write_dynamic_frame`, `getSink`, `from_jdbc_conf` | `SPRKCNTPY3609` |

## External Documentation

- [Issue Codes by Source (index)](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/README)
- [Snowpark Connect Python Codes](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/snowpark-connect-codes-python)
- [Python Issue Codes (SPRKPY)](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/python/README)
- [SQL Issue Codes (SSC-EWI)](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/sql/README)
- [Pandas Issue Codes (PNDSPY)](https://docs.snowflake.com/en/migrations/sma-docs/issue-analysis/issue-codes-by-source/pandas/README)
