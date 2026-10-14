# SPRKPY1081

pyspark.sql.readwriter.DataFrameWriter.partitionBy

> This issue code has been **deprecated** since [Spark Conversion Core 4.12.0](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.12.0)

Message: pyspark.sql.readwriter.DataFrameWriter.partitionBy has a workaround.

Category: Warning

## Description

The [pyspark.sql.readwriter.DataFrameWriter.partitionBy](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriter.partitionBy.html) function is not supported. The workaround is to use [Snowpark's copy_into_location](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.copy_into_location) instead. See the documentation for more info.

## Scenario

**Input**

This code will create a separate directories for each unique value in the `FIRST_NAME` column.
The data is the same, but it's going to be stored in different directories based on the column.
```python
df = session.createDataFrame([["John", "Berry"], ["Rick", "Berry"], ["Anthony", "Davis"]], schema = ["FIRST_NAME", "LAST_NAME"])
df.write.partitionBy("FIRST_NAME").csv("/home/data")
```
This code will create a separate directories for each unique value in the `FIRST_NAME` column.
The data is the same, but it's going to be stored in different directories based on the column.

**Output code**
```python
df = session.createDataFrame([["John", "Berry"], ["Rick", "Berry"], ["Anthony", "Davis"]], schema = ["FIRST_NAME", "LAST_NAME"])
#EWI: SPRKPY1081 => The partitionBy function is not supported, but you can instead use copy_into_location as workaround. See the documentation for more info.
df.write.partitionBy("FIRST_NAME").csv("/home/data", format_type_options = dict(compression = "None"))
```

**Recommended fix**

In Snowpark, [copy_into_location](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.copy_into_location) has a partition_by parameter that you can use instead of the partitionBy function,
but it's going to require some manual adjustments, as shown in the following example:

**Spark code:**

```python
df = session.createDataFrame([["John", "Berry"], ["Rick", "Berry"], ["Anthony", "Davis"]], schema = ["FIRST_NAME", "LAST_NAME"])
df.write.partitionBy("FIRST_NAME").csv("/home/data")
```

**Snowpark code manually adjusted:**

```python
df = session.createDataFrame([["John", "Berry"], ["Rick", "Berry"], ["Anthony", "Davis"]], schema = ["FIRST_NAME", "LAST_NAME"])
df.write.copy_into_location(location=temp_stage, partition_by=col("FIRST_NAME"), file_format_type="csv", format_type_options={"COMPRESSION": "NONE"}, header=True)
```
**copy_into_location** has the following parameters
- _location_: The Snowpark location only accepts cloud locations using an [snowflake stage](https://docs.snowflake.com/en/user-guide/data-load-local-file-system-create-stage).

- _partition_by_: It can be a Column name or a SQL expression, so you will need to converted to a column or a SQL, using col or sql_expr.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
