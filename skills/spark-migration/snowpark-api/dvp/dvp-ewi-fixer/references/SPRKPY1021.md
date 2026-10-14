# SPRKPY1021

pyspark.sql.functions.last

> This issue code has been **deprecated**

Message: pyspark.sql.functions.last has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.functions.last](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.last.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.functions.last` function that generates this EWI. In this example, the `last` function is used to get the last **value** for each name.

```python
df = spark.createDataFrame([("Alice", 1), ("Bob", 2), ("Charlie", 3), ("Alice", 4), ("Bob", 5)], ["name", "value"])
df_grouped = df.groupBy("name").agg(last("value").alias("last_value"))
```

**Output**

The SMA adds the EWI `SPRKPY1021` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
df = spark.createDataFrame([("Alice", 1), ("Bob", 2), ("Charlie", 3), ("Alice", 4), ("Bob", 5)], ["name", "value"])
#EWI: SPRKPY1021 => pyspark.sql.functions.last has a workaround, see documentation for more info
df_grouped = df.groupBy("name").agg(last("value").alias("last_value"))
```

**Recommended fix**

As a workaround, you can use the Snowflake [LAST_VALUE](https://docs.snowflake.com/en/sql-reference/functions/last_value) function. To invoke this function from Snowpark, use the [snowflake.snowpark.functions.call_builtin](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.call_builtin) function and pass the string `last_value` as the first argument and the corresponding column as the second argument. If you were using the name of the column in the `last` function, you should convert it into a column when calling the `call_builtin` function.

```python
df = spark.createDataFrame([("Alice", 1), ("Bob", 2), ("Charlie", 3), ("Alice", 4), ("Bob", 5)], ["name", "value"])
df_grouped = df.groupBy("name").agg(call_builtin("last_value", col("value")).alias("last_value"))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
