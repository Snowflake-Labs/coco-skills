# SPRKPY1011

pyspark.sql.dataframe.DataFrameStatFunctions.approxQuantile

Message: pyspark.sql.dataframe.DataFrameStatFunctions.approxQuantile
has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.dataframe.DataFrameStatFunctions.approxQuantile](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameStatFunctions.approxQuantile.html#pyspark.sql.DataFrameStatFunctions.approxQuantile) which has a workaround.

## Scenario

**Input**

It's important understand that Pyspark uses two different approxQuantile functions, here we use the [DataFrameStatFunctions approxQuantile](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameStatFunctions.approxQuantile.html#pyspark.sql.DataFrameStatFunctions.approxQuantile) version.

```python
import tempfile
from pyspark.sql import SparkSession, DataFrameStatFunctions
spark = SparkSession.builder.getOrCreate()
data = [['Q1', 300000],
        ['Q2', 60000],
        ['Q3', 500002],
        ['Q4', 130000]]

columns = ['Quarter', 'Gain']
df = spark.createDataFrame(data, columns)
aprox_quantille = DataFrameStatFunctions(df).approxQuantile('Gain', [0.25, 0.5, 0.75], 0)
print(aprox_quantille)
```

**Output**

SMA returns the EWI SPRKPY1011 over the line where approxQuantile is used, so you can use to identify where to fix.

```python
import tempfile
from snowflake.snowpark import Session, DataFrameStatFunctions
spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
data = [['Q1', 300000],
        ['Q2', 60000],
        ['Q3', 500002],
        ['Q4', 130000]]

columns = ['Quarter', 'Gain']
df = spark.createDataFrame(data, columns)
#EWI: SPRKPY1011 => pyspark.sql.dataframe.DataFrameStatFunctions.approxQuantile has a workaround, see documentation for more info
aprox_quantille = DataFrameStatFunctions(df).approxQuantile('Gain', [0.25, 0.5, 0.75], 0)
```

**Recommended fix**

You can use [Snowpark approxQuantile](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrame.approxQuantile) method. Some parameters don't match so they require some manual adjustments.
for the output code's example a recommended fix could be:

```python
from snowflake.snowpark import Session # remove DataFrameStatFunctions because is not required
...
df = spark.createDataFrame(data, columns)

aprox_quantille = df.stat.approx_quantile('Ammount', [0.25, 0.5, 0.75])
```
pyspark.sql.dataframe.DataFrame.approxQuantile's relativeError parameter does't exist in SnowPark.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
