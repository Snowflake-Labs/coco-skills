# SPRKPY1024

pyspark.sql.functions.log2

> This issue code has been **deprecated** since [Spark Conversion Core 7.2.0](https://docs.snowconvert.com/sma/general/release-notes#application-and-cli-version-2.6.2)

Message: pyspark.sql.functions.log2 has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.functions.log2](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.log2.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.functions.log2` function that generates this EWI. In this example, the `log2` function is used to calculate the base-2 logarithm of the **value** column.

```python
df = spark.createDataFrame([(1,), (2,), (4,), (8,), (16,)], ["value"])
df_with_log2 = df.withColumn("log2_value", log2(df["value"]))
```

**Output**

The SMA adds the EWI `SPRKPY1024` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
df = spark.createDataFrame([(1,), (2,), (4,), (8,), (16,)], ["value"])
#EWI: SPRKPY1024 => pyspark.sql.functions.log2 has a workaround, see documentation for more info
df_with_log2 = df.withColumn("log2_value", log2(df["value"]))
```

**Recommended fix**

As a workaround, you can use the [snowflake.snowpark.functions.log](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.log) function by passing the literal value `2` as the base.

```python
df = session.createDataFrame([(1,), (2,), (4,), (8,), (16,)], ["value"])
df_with_log2 = df.withColumn("log2_value", log(2, df["value"]))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
