# SPRKPY1071

pyspark.rdd.RDD.getNumPartitions

Message: The function pyspark.rdd.RDD.getNumPartitions is not required in Snowpark. So, you should remove all references.

Category: Warning

### Description

This issue appears when the tool finds the use of the [pyspark.rdd.RDD.getNumPartitions](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.RDD.getNumPartitions.html) function. Snowflake uses micro-partitioning mechanism, so the use of this function is not required.

## Scenario

**Input**

The getNumPartitions returns the quantity of partitions on a RDD.

```python
df = spark.createDataFrame([('2015-04-08',), ('5',), [Row(a=1, b="b")]], ['dt', 'num', 'row'])

print(df.getNumPartitions())
```

**Output**


The tool adds this EWI to let you know that getNumPartitions is not required.

```python
df = spark.createDataFrame([('2015-04-08',), ('5',), [Row(a=1, b="b")]], ['dt', 'num', 'row'])
#EWI: SPRKPY1071 => The getNumPartitions are not required in Snowpark. So, you should remove all references.

print(df.getNumPartitions())
```

**Recommended fix**

Remove all uses of this function.

```python
df = spark.createDataFrame([('2015-04-08',), ('5',), [Row(a=1, b="b")]], ['dt', 'num', 'row'])
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
