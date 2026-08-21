# SPRKPY1013

pyspark.sql.functions.acosh

> This issue code has been **deprecated** since [Spark Conversion Core 7.2.0](https://docs.snowconvert.com/sma/general/release-notes#application-and-cli-version-2.6.2)

Message: pyspark.sql.functions.acosh has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.functions.acosh](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.acosh.html) which has a workaround.

## Scenario

**Input**

On this example pyspark calculates the acosh for a dataframe by using [pyspark.sql.functions.acosh](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.acosh.html)

```python
from pyspark.sql import SparkSession
from pyspark.sql.functions import acosh
spark = SparkSession.builder.getOrCreate()
data = [['V1', 30],
        ['V2', 60],
        ['V3', 50],
        ['V4', 13]]

columns = ['Paremeter', 'value']
df = spark.createDataFrame(data, columns)
df_with_acosh = df.withColumn("acosh_value", acosh(df["value"]))
```

**Output**

SMA returns the EWI SPRKPY1013 over the line where acosh is used, so you can use to identify where to fix.

```python
from snowflake.snowpark import Session

spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
data = [['V1', 30],
        ['V2', 60],
        ['V3', 50],
        ['V4', 13]]

columns = ['Paremeter', 'value']
df = spark.createDataFrame(data, columns)
#EWI: SPRKPY1013 => pyspark.sql.functions.acosh has a workaround, see documentation for more info
df_with_acosh = df.withColumn("acosh_value", acosh(df["value"]))
```

**Recommended fix**

There is no direct "acosh" implementation but "[call_function](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.3.0/api/snowflake.snowpark.functions.call_function)" can be used instead, using "acosh" as the first parameter, and colName as the second one.
```python
import snowflake.snowpark as snowpark
from snowflake.snowpark import Session
from snowflake.snowpark.functions import call_function, col

spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
data = [['V1', 30],
        ['V2', 60],
        ['V3', 50],
        ['V4', 13]]

columns = ['Paremeter', 'value']
df = spark.createDataFrame(data, columns)
df_with_acosh = df.select(call_function('ACOSH', col('value')))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
