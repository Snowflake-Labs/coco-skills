# SPRKPY1037

pyspark.sql.functions.sort_array

> This issue code has been **deprecated**

Message: pyspark.sql.functions.sort_array has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.functions.sort_array](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.sort_array.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.functions.sort_array` function that generates this EWI. In this example, the `sort_array` function is used to sort the **numbers** array in ascending and descending order.

```python
df = spark.createDataFrame([(1, [3, 1, 2]), (2, [10, 5, 8]), (3, [6, 4, 7])], ["id", "numbers"])
df_sorted_asc = df.withColumn("sorted_numbers_asc", sort_array("numbers", asc=True))
df_sorted_desc = df.withColumn("sorted_numbers_desc", sort_array("numbers", asc=False))
```

**Output**

The SMA adds the EWI `SPRKPY1037` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
df = spark.createDataFrame([(1, [3, 1, 2]), (2, [10, 5, 8]), (3, [6, 4, 7])], ["id", "numbers"])
#EWI: SPRKPY1037 => pyspark.sql.functions.sort_array has a workaround, see documentation for more info
df_sorted_asc = df.withColumn("sorted_numbers_asc", sort_array("numbers", asc=True))
#EWI: SPRKPY1037 => pyspark.sql.functions.sort_array has a workaround, see documentation for more info
df_sorted_desc = df.withColumn("sorted_numbers_desc", sort_array("numbers", asc=False))
```

**Recommended fix**

As a workaround, you can import the [snowpark_extensions](https://pypi.org/project/snowpark-extensions/) package which provides an extension for the `sort_array` function.

```python
import snowpark_extensions

df = spark.createDataFrame([(1, [3, 1, 2]), (2, [10, 5, 8]), (3, [6, 4, 7])], ["id", "numbers"])
df_sorted_asc = df.withColumn("sorted_numbers_asc", sort_array("numbers", asc=True))
df_sorted_desc = df.withColumn("sorted_numbers_desc", sort_array("numbers", asc=False))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
