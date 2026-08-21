# SPRKPY1039

pyspark.sql.column.Column.getItem

> This issue code has been **deprecated**

Message: pyspark.sql.column.Column.getItem has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.column.Column.getItem](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.Column.getItem.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.column.Column.getItem` function that generates this EWI. In this example, the `getItem` function is used to get an item by position and by key.

```python
df = spark.createDataFrame([(1, ["apple", "banana", "orange"]), (2, ["carrot", "avocado", "banana"])], ["id", "fruits"])
df.withColumn("first_fruit", col("fruits").getItem(0))

df = spark.createDataFrame([(1, {"apple": 10, "banana": 20}), (2, {"carrot": 15, "grape": 25}), (3, {"pear": 30, "apple": 35})], ["id", "fruit_quantities"])
df.withColumn("apple_quantity", col("fruit_quantities").getItem("apple"))
```

**Output**

The SMA adds the EWI `SPRKPY1039` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
df = spark.createDataFrame([(1, ["apple", "banana", "orange"]), (2, ["carrot", "avocado", "banana"])], ["id", "fruits"])
#EWI: SPRKPY1039 => pyspark.sql.column.Column.getItem has a workaround, see documentation for more info
df.withColumn("first_fruit", col("fruits").getItem(0))

df = spark.createDataFrame([(1, {"apple": 10, "banana": 20}), (2, {"carrot": 15, "grape": 25}), (3, {"pear": 30, "apple": 35})], ["id", "fruit_quantities"])
#EWI: SPRKPY1039 => pyspark.sql.column.Column.getItem has a workaround, see documentation for more info
df.withColumn("apple_quantity", col("fruit_quantities").getItem("apple"))
```

**Recommended fix**

As a workaround, you can use the **Snowpark column indexer operator** with the name or position of the field as the index.

```python
df = spark.createDataFrame([(1, ["apple", "banana", "orange"]), (2, ["carrot", "avocado", "banana"])], ["id", "fruits"])
df.withColumn("first_fruit", col("fruits")[0])

df = spark.createDataFrame([(1, {"apple": 10, "banana": 20}), (2, {"carrot": 15, "grape": 25}), (3, {"pear": 30, "apple": 35})], ["id", "fruit_quantities"])
df.withColumn("apple_quantity", col("fruit_quantities")["apple"])
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
