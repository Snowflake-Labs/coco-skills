# SPRKPY1023

pyspark.sql.functions.log1p

> This issue code has been **deprecated** since [Spark Conversion Core 7.2.0](https://docs.snowconvert.com/sma/general/release-notes#application-and-cli-version-2.6.2)

Message: pyspark.sql.functions.log1p has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.functions.log1p](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.log1p.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.functions.log1p` function that generates this EWI. In this example, the `log1p` function is used to calculate the natural logarithm of the **value** column.

```python
df = spark.createDataFrame([(0,), (1,), (10,), (100,)], ["value"])
df_with_log1p = df.withColumn("log1p_value", log1p(df["value"]))
```

**Output**

The SMA adds the EWI `SPRKPY1023` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
df = spark.createDataFrame([(0,), (1,), (10,), (100,)], ["value"])
#EWI: SPRKPY1023 => pyspark.sql.functions.log1p has a workaround, see documentation for more info
df_with_log1p = df.withColumn("log1p_value", log1p(df["value"]))
```

**Recommended fix**

As a workaround, you can use the [call_function](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.call_function) function by passing the string `ln` as the first argument and by adding `1` to the second argument.

```python
df = spark.createDataFrame([(0,), (1,), (10,), (100,)], ["value"])
df_with_log1p = df.withColumn("log1p_value", call_function("ln", lit(1) + df["value"]))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
