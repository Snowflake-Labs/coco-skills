# SPRKPY1035

pyspark.sql.functions.reverse

> This issue code has been **deprecated**

Message: pyspark.sql.functions.reverse has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.functions.reverse](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.reverse.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.functions.reverse` function that generates this EWI. In this example, the `reverse` function is used to reverse each string of the **word** column.

```python
df = spark.createDataFrame([("hello",), ("world",)], ["word"])
df_reversed = df.withColumn("reversed_word", reverse(df["word"]))
df_reversed = df.withColumn("reversed_word", reverse("word"))
```

**Output**

The SMA adds the EWI `SPRKPY1035` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
df = spark.createDataFrame([("hello",), ("world",)], ["word"])
#EWI: SPRKPY1035 => pyspark.sql.functions.reverse has a workaround, see documentation for more info
df_reversed = df.withColumn("reversed_word", reverse(df["word"]))
#EWI: SPRKPY1035 => pyspark.sql.functions.reverse has a workaround, see documentation for more info
df_reversed = df.withColumn("reversed_word", reverse("word"))
```

**Recommended fix**

As a workaround, you can import the [snowpark_extensions](https://pypi.org/project/snowpark-extensions/) package which provides an extension for the `reverse` function.

```python
import snowpark_extensions

df = spark.createDataFrame([("hello",), ("world",)], ["word"])
df_reversed = df.withColumn("reversed_word", reverse(df["word"]))
df_reversed = df.withColumn("reversed_word", reverse("word"))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
