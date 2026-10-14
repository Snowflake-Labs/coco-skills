# SPRKPY1052

pyspark.sql.session.SparkSession.Builder.enableHiveSupport

> This issue code has been **deprecated** since [Spark Conversion Core Version 2.8.0](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-2.8.0)

Message: pyspark.sql.session.SparkSession.Builder.enableHiveSupport has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of  [pyspark.sql.session.SparkSession.Builder.enableHiveSupport](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.SparkSession.builder.enableHiveSupport.html) which has a workaround.

## Scenario

**Input**

Below is an example that configures the SparkSession and enables the hive support using the method `enableHiveSupport`.

```python
spark = Session.builder.appName("Merge_target_table")\
        .config("spark.port.maxRetries","100") \
        .enableHiveSupport().getOrCreate()
```

**Output**

The tool adds the EWI `SPRKPY1052` indicating that a workaround can be implemented.

```python
#EWI: SPRKPY1052 => pyspark.sql.session.SparkSession.Builder.enableHiveSupport has a workaround, see documentation for more info
spark = Session.builder.appName("Merge_target_table")\
        .config("spark.port.maxRetries","100") \
        .enableHiveSupport().getOrCreate()
```

**Recommended fix**

Remove the use of `enableHiveSupport` function because it is not needed in Snowpark.

```python
spark = Session.builder.appName("Merge_target_table")\
        .config("spark.port.maxRetries","100") \
        .getOrCreate()
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).