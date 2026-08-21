# SPRKPY1040

pyspark.sql.functions.explode

> This issue code has been **deprecated**

Message: pyspark.sql.functions.explode has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.functions.explode](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.explode.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.functions.explode` function that generates this EWI. In this example, the `explode` function is used to generate one row per array item for the **numbers** column.

```python
df = spark.createDataFrame([("Alice", [1, 2, 3]), ("Bob", [4, 5]), ("Charlie", [6, 7, 8, 9])], ["name", "numbers"])
exploded_df = df.select("name", explode(df.numbers).alias("number"))
```

**Output**

The SMA adds the EWI `SPRKPY1040` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
df = spark.createDataFrame([("Alice", [1, 2, 3]), ("Bob", [4, 5]), ("Charlie", [6, 7, 8, 9])], ["name", "numbers"])
#EWI: SPRKPY1040 => pyspark.sql.functions.explode has a workaround, see documentation for more info
exploded_df = df.select("name", explode(df.numbers).alias("number"))
```

**Recommended fix**

As a workaround, you can import the [snowpark_extensions](https://pypi.org/project/snowpark-extensions/) package which provides an extension for the `explode` function.

```python
import snowpark_extensions

df = spark.createDataFrame([("Alice", [1, 2, 3]), ("Bob", [4, 5]), ("Charlie", [6, 7, 8, 9])], ["name", "numbers"])
exploded_df = df.select("name", explode(df.numbers).alias("number"))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
