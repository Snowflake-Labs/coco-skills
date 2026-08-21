# SPRKPY1009

pyspark.sql.dataframe.DataFrame.approxQuantile

Message: pyspark.sql.dataframe.DataFrame.approxQuantile has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.dataframe.DataFrame.approxQuantile](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.approxQuantile.html) which has a workaround.

## Scenario

**Input**

It's important understand that Pyspark uses two different approxQuantile functions, here we use the [DataFrame approxQuantile](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.approxQuantile.html) version

```python
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
data = [['Sun', 10],
        ['Mon', 64],
        ['Thr', 12],
        ['Wen', 15],
        ['Thu', 68],
        ['Fri', 14],
        ['Sat', 13]]

columns = ['Day', 'Ammount']
df = spark.createDataFrame(data, columns)
df.approxQuantile('Ammount', [0.25, 0.5, 0.75], 0)
```

**Output**

SMA returns the EWI SPRKPY1009 over the line where approxQuantile is used, so you can use to identify where to fix.

```python
from snowflake.snowpark import Session
spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
data = [['Sun', 10],
        ['Mon', 64],
        ['Thr', 12],
        ['Wen', 15],
        ['Thu', 68],
        ['Fri', 14],
        ['Sat', 13]]

columns = ['Day', 'Ammount']
df = spark.createDataFrame(data, columns)
#EWI: SPRKPY1009 => pyspark.sql.dataframe.DataFrame.approxQuantile has a workaround, see documentation for more info
df.approxQuantile('Ammount', [0.25, 0.5, 0.75], 0)
```

**Recommended fix**

Use [Snowpark approxQuantile](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrame.approxQuantile) method. Some parameters don't match so they require some manual adjustments.
for the output code's example a recommended fix could be:

```python
from snowflake.snowpark import Session
...
df = spark.createDataFrame(data, columns)

df.stat.approx_quantile('Ammount', [0.25, 0.5, 0.75])
```

pyspark.sql.dataframe.DataFrame.approxQuantile's relativeError parameter does't exist in SnowPark.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
