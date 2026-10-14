# SPRKPY1020

pyspark.sql.functions.instr

> This issue code has been **deprecated** since [Spark Conversion Core 7.2.0](https://docs.snowconvert.com/sma/general/release-notes#application-and-cli-version-2.6.2)

Message: pyspark.sql.functions.instr has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.functions.instr](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.instr.html) which has a workaround.

## Scenario

**Input**

Here is a basic example of usage of pyspark instr:
```python
from pyspark.sql import SparkSession
from pyspark.sql.functions import instr
spark = SparkSession.builder.getOrCreate()
df = spark.createDataFrame([('abcd',)], ['test',])
df.select(instr(df.test, 'cd').alias('result')).collect()
```

**Output:**

SMA returns the EWI SPRKPY1020 over the line where instr is used, so you can use to identify where to fix.
```python
from snowflake.snowpark import Session

spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
df = spark.createDataFrame([('abcd',)], ['test',])
#EWI: SPRKPY1020 => pyspark.sql.functions.instr has a workaround, see documentation for more info
df.select(instr(df.test, 'cd').alias('result')).collect()
```

**Recommended fix**

Requires a manual change by using the function [charindex](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.charindex) and changing the order of the first two parameters.
Here is the above output code with the fix:

```python
import snowflake.snowpark as snowpark
from snowflake.snowpark import Session
from snowflake.snowpark.functions import charindex, lit

spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
df = spark.createDataFrame([('abcd',)], ['test',])
df.select(charindex(lit('cd'), df.test).as_('result')).show()
```

## Additional recommendation

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
