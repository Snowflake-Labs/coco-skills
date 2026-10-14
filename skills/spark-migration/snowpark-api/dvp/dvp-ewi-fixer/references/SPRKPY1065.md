# SPRKPY1065

pyspark.context.SparkContext.broadcast

Message: The pyspark.context.SparkContext.broadcast does not apply since snowflake use data-clustering mechanism to compute the data.

Category: Warning

## Description

This issue appears when the tool detects the usage of element [pyspark.context.SparkContext.broadcast](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.SparkContext.broadcast.html), which is not necessary due to the use of [data-clustering](https://docs.snowflake.com/en/user-guide/tables-clustering-micropartitions) of Snowflake.

**Input code**

In this example a broadcast variable is created, these variables allows data to be share more
efficiently through all nodes.

```python
sc = SparkContext(conf=conf_spark)

mapping = {1: 10001, 2: 10002}

bc = sc.broadcast(mapping)
```

**Output code**

The SMA adds an EWI message indicating that the broadcast it's not required.

```python
sc = conf_spark

mapping = {1: 10001, 2: 10002}
#EWI: SPRKPY1065 => The element does not apply since snowflake use data-clustering mechanism to compute the data.

bc = sc.broadcast(mapping)
```

**Recommended fix**

Remove any usages of pyspark.context.SparkContext.broadcast.

```python
sc = conf_spark

mapping = {1: 10001, 2: 10002}
```


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
