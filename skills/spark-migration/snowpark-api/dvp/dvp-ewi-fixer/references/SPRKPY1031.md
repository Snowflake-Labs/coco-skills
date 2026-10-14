# SPRKPY1031

pyspark.sql.column.Column.contains

> This issue code has been **deprecated** since [Spark Conversion Core 2.7.0](https://docs.snowconvert.com/sma/general/release-notes/old-version-release-notes/sc-spark-python-release-notes#id-2.7.0)

Message: pyspark.sql.column.Column.contains has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.column.Column.contains](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.Column.contains.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.column.Column.contains` function that generates this EWI. In this example, the `contains` function is used to filter the rows where the 'City' column contains the substring 'New'.

```python
df = spark.createDataFrame([("Alice", "New York"), ("Bob", "Los Angeles"), ("Charlie", "Chicago")], ["Name", "City"])
df_filtered = df.filter(col("City").contains("New"))
```

**Output**

The SMA adds the EWI `SPRKPY1031` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
df = spark.createDataFrame([("Alice", "New York"), ("Bob", "Los Angeles"), ("Charlie", "Chicago")], ["Name", "City"])
#EWI: SPRKPY1031 => pyspark.sql.column.Column.contains has a workaround, see documentation for more info
df_filtered = df.filter(col("City").contains("New"))
```

**Recommended fix**

As a workaround, you can use the [snowflake.snowpark.functions.contains](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.contains) function by passing the column as the first argument and the element to search as the second argument. If the element to search is a literal value then it should be converted into a column expression using the `lit` function.

```python
from snowflake.snowpark import functions as f
df = spark.createDataFrame([("Alice", "New York"), ("Bob", "Los Angeles"), ("Charlie", "Chicago")], ["Name", "City"])
df_filtered = df.filter(f.contains(col("City"), f.lit("New")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
