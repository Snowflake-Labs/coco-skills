# SPRKPY1064

The ***Spark element*** does not apply.

Message: The ***Spark element*** does not apply since snowflake uses snowpipe mechanism instead.

Category: Warning

## Description

This issue appears when the tool detects the usage of any element from the pyspark.streaming library:
- [pyspark.streaming.DStream](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.streaming.DStream.html)
- [pyspark.streaming.StreamingContext](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.streaming.StreamingContext.html)
- pyspark.streaming.listener.StreamingListener.

## Scenario

**Input**

Below is an example with one of the elements that trigger this EWI.

```python
from pyspark.streaming.listener import StreamingListener

var = StreamingListener.Java
var.mro()

df = spark.createDataFrame([(25, "Alice", "150"), (30, "Bob", "350")], schema=["age", "name", "value"])
df.show()
```

**Output**

The SMA adds the EWI `SPRKPY1064` on the output code to let you know that this function does not apply.

```python
#EWI: SPRKPY1064 => The element does not apply since snowflake uses snowpipe mechanism instead.

var = StreamingListener.Java
var.mro()

df = spark.createDataFrame([(25, "Alice", "150"), (30, "Bob", "350")], schema=["age", "name", "value"])
df.show()
```

**Recommended fix**

The SMA removes the import statement and adds the issue to the *Issues.csv* inventory, remove any usages of the Spark element.

```python
df = spark.createDataFrame([(25, "Alice", "150"), (30, "Bob", "350")], schema=["age", "name", "value"])
df.show()
```

## Additional recommendations

- Check the documentation for [Snowpipe](https://docs.snowflake.com/en/user-guide/data-load-snowpipe-intro) to see how it fits to the current scenario.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
