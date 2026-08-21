# SPRKPY1006

pyspark.context.SparkContext

> This Issue Code has been **deprecated** since [Spark Conversion Core Version 4.8.0 ](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.8.0)

Message: pyspark.context.SparkContext is not required

Category: Warning.

## Description
This issue appears when the tool detects the usage of [pyspark.context.SparkContext](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.SparkContext.html), which is not required in Snowflake.

## Scenario
**Input**

In this example there are two context to create a connections to an Spark Cluster
```python
from pyspark import SparkContext

sql_context1 = SparkContext(my_sc1)
sql_context2 = SparkContext(sparkContext=my_sc2)
```

**Output**

Because there are no clusters on Snowflake the Context is not required, note that the variables my_sc1 and my_sc2 that contains Spark properties may be not required or it will to be adapted to fix the code.

```python
from snowflake.snowpark import Session
#EWI: SPRKPY1006 => pyspark.sql.context.SparkContext is not required
sql_context1 = my_sc1
#EWI: SPRKPY1006 => pyspark.sql.context.SparkContext is not required

sql_context2 = my_sc2
```

## Additional recommendations

- This is an unnecessary parameter being removed with a warning comment being inserted. There should be no action from the user.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
