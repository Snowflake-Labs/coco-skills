# SPRKPY1010

pyspark.sql.dataframe.DataFrame.checkpoint

Message: pyspark.sql.dataframe.DataFrame.checkpoint has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.dataframe.DataFrame.checkpoint](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.checkpoint.html) which has a workaround.

## Scenario

**Input**

In PySpark Checkpoints are used to truncate the logical plan of a dataframe, this to avoid the growing of a logical plan.

```python
import tempfile
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
data = [['Q1', 300000],
        ['Q2', 60000],
        ['Q3', 500002],
        ['Q4', 130000]]

columns = ['Quarter', 'Score']
df = spark.createDataFrame(data, columns)
with tempfile.TemporaryDirectory() as d:
    spark.sparkContext.setCheckpointDir("/tmp/bb")
    df.checkpoint(False)
```

**Output**

SMA returns the EWI SPRKPY1010 over the line where approxQuantile is used, so you can use to identify where to fix. Note that also marks the [setCheckpointDir](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.SparkContext.setCheckpointDir.html) as unsupported, but a checpointed directory is not required for the fix.

```python
import tempfile
from snowflake.snowpark import Session
spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
data = [['Q1', 300000],
        ['Q2', 60000],
        ['Q3', 500002],
        ['Q4', 130000]]

columns = ['Quarter', 'Score']
df = spark.createDataFrame(data, columns)
with tempfile.TemporaryDirectory() as d:
    #EWI: SPRKPY1002 => pyspark.context.SparkContext.setCheckpointDir is not supported
    spark.setCheckpointDir("/tmp/bb")
    #EWI: SPRKPY1010 => pyspark.sql.dataframe.DataFrame.checkpoint has a workaround, see documentation for more info
    df.checkpoint(False)
```

**Recommended fix**

Snowpark eliminates the need for explicit checkpoints: this because Snowpark works with SQL-based operations that are optimized by Snowflake query optimization engine eliminating the need for unrequited computations or logical plans that grow out of control.

However there could be scenarios where you would require persist the result of a computation on a dataframe. In this scenarios you can save materialize the results by writing the dataframe on a [Snowflake Table or in a Snowflake Temporary Table](https://docs.snowflake.com/en/user-guide/tables-temp-transient).

- By the use of a permanent table or the computed result can be accessed in any moment even after the session end.
```python
from snowflake.snowpark import Session
spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
data = [['Q1', 300000],
        ['Q2', 60000],
        ['Q3', 500002],
        ['Q4', 130000]]

columns = ['Quarter', 'Score']
df = spark.createDataFrame(data, columns)
df.write.save_as_table("my_table", table_type="temporary") # Save the dataframe into Snowflake table "my_table".
df2 = Session.table("my_table") # Now I can access the stored result quering the table "my_table"
```
- An alternative fix, the use of a Temporary table have the advantage that the table is deleted after the session ends:

```python
from snowflake.snowpark import Session
spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
data = [['Q1', 300000],
        ['Q2', 60000],
        ['Q3', 500002],
        ['Q4', 130000]]

columns = ['Quarter', 'Score']
df = spark.createDataFrame(data, columns)
df.write.save_as_table("my_temp_table", table_type="temporary") # Save the dataframe into Snowflake table "my_temp_table".
df2 = Session.table("my_temp_table") # Now I can access the stored result quering the table "my_temp_table"
```
## Additional recommendations
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
