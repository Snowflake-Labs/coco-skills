# SPRKPY1025

pyspark.sql.functions.ntile

> This issue code has been **deprecated**

Message: pyspark.sql.functions.ntile has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.functions.ntile](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.ntile.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.functions.ntile` function that generates this EWI. In this example, the `ntile` function is used to divide the rows into 3 buckets.

```python
df = spark.createDataFrame([("Alice", 50), ("Bob", 30), ("Charlie", 60), ("David", 90), ("Eve", 70), ("Frank", 40)], ["name", "score"])
windowSpec = Window.orderBy("score")
df_with_ntile = df.withColumn("bucket", ntile(3).over(windowSpec))
```

**Output**

The SMA adds the EWI `SPRKPY1025` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
df = spark.createDataFrame([("Alice", 50), ("Bob", 30), ("Charlie", 60), ("David", 90), ("Eve", 70), ("Frank", 40)], ["name", "score"])
windowSpec = Window.orderBy("score")
#EWI: SPRKPY1025 => pyspark.sql.functions.ntile has a workaround, see documentation for more info
df_with_ntile = df.withColumn("bucket", ntile(3).over(windowSpec))
```

**Recommended fix**

Snowpark has an equivalent [ntile](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.ntile) function, however, the argument pass to it should be a column. As a workaround, you can convert the literal argument into a column using the [snowflake.snowpark.functions.lit](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.lit) function.

```python
df = spark.createDataFrame([("Alice", 50), ("Bob", 30), ("Charlie", 60), ("David", 90), ("Eve", 70), ("Frank", 40)], ["name", "score"])
windowSpec = Window.orderBy("score")
df_with_ntile = df.withColumn("bucket", ntile(lit(3)).over(windowSpec))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
