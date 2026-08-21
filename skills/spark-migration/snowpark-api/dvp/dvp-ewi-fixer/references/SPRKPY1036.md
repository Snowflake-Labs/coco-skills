# SPRKPY1036

pyspark.sql.column.Column.getField

> This issue code has been **deprecated**

Message: pyspark.sql.column.Column.getField has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.column.Column.getField](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.Column.getField.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.column.Column.getField` function that generates this EWI. In this example, the `getField` function is used to extract the **name** from the **info** column.

```python
df = spark.createDataFrame([(1, {"name": "John", "age": 30}), (2, {"name": "Jane", "age": 25})], ["id", "info"])
df_with_name = df.withColumn("name", col("info").getField("name"))
```

**Output**

The SMA adds the EWI `SPRKPY1036` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
df = spark.createDataFrame([(1, {"name": "John", "age": 30}), (2, {"name": "Jane", "age": 25})], ["id", "info"])
#EWI: SPRKPY1036 => pyspark.sql.column.Column.getField has a workaround, see documentation for more info
df_with_name = df.withColumn("name", col("info").getField("name"))
```

**Recommended fix**

As a workaround, you can use the [Snowpark column indexer operator](https://docs.snowflake.com/ko/developer-guide/snowpark/reference/python/api/snowflake.snowpark.Column.html#snowflake.snowpark.Column) with the name of the field as the index.

```python
df = spark.createDataFrame([(1, {"name": "John", "age": 30}), (2, {"name": "Jane", "age": 25})], ["id", "info"])
df_with_name = df.withColumn("name", col("info")["name"])
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
