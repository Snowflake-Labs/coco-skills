# SPRKPY1066

The Spark element does not apply.

Message: The Spark element does not apply since snowflake use micro-partitioning mechanism are created automatically.

Category: Warning

## Description

This issue appears when the tool detects the usage of elements related to partitions:
- [pyspark.sql.catalog.Catalog.recoverPartitions](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.Catalog.recoverPartitions.html)
- [pyspark.sql.dataframe.DataFrame.foreachPartition](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.foreachPartition.html)
- [pyspark.sql.dataframe.DataFrame.sortWithinPartitions](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.sortWithinPartitions.html)
- [pyspark.sql.functions.spark_partition_id](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.spark_partition_id.html)


Those elements do not apply due the use of [micro-partitions](https://docs.snowflake.com/en/user-guide/tables-clustering-micropartitions) of Snowflake.

**Input code**

In this example [sortWithinPartitions](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.sortWithinPartitions.html) it's used to create a partition in a DataFrame sorted by the specified column.

```python
df = spark.createDataFrame([(2, "Alice"), (5, "Bob")], schema=["age", "name"])
df.sortWithinPartitions("age", ascending=False)
```

**Output code**

The SMA adds an EWI message indicating that Spark element is not required.

```python
df = spark.createDataFrame([(2, "Alice"), (5, "Bob")], schema=["age", "name"])
#EWI: SPRKPY1066 => The element does not apply since snowflake use micro-partitioning mechanism are created automatically.
df.sortWithinPartitions("age", ascending=False)
```

**Recommended fix**

Remove the usage of the element.

```python
df = spark.createDataFrame([(2, "Alice"), (5, "Bob")], schema=["age", "name"])
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
