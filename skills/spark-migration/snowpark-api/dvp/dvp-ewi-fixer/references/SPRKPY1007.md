# SPRKPY1007

pyspark.sql.context.SQLContext

> This Issue Code has been **deprecated** since [Spark Conversion Core Version 4.8.0](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.8.0)

Message: pyspark.sql.context.SQLContext is not required

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.context.SQLContext](https://downloads.apache.org/spark/docs/1.6.1/api/python/pyspark.sql.html), which is not required.

## Scenario

**Input**

Here we have an example with different SparkContext overloads.

```python
from pyspark import SQLContext
​
my_sc1 = SQLContext(myMaster, myAppName, mySparkHome, myPyFiles, myEnvironment, myBatctSize, mySerializer, my_conf1)
my_sc2 = SQLContext(conf=my_conf2)
my_sc3 = SQLContext()
```

**Output**

The output code has commented the line for pyspark.SQLContext, and replaces the scenarios with a reference to a configuration. Note that the variables my_sc1 and my_sc2 that contains Spark properties may be not required or it will to be adapted to fix the code.

```python
#EWI: SPRKPY1007 => pyspark.sql.context.SQLContext is not required
#from pyspark import SQLContext
pass

#EWI: SPRKPY1007 => pyspark.sql.context.SQLContext is not required
sql_context1 = my_sc1
#EWI: SPRKPY1007 => pyspark.sql.context.SQLContext is not required
sql_context2 = my_sc2
```
## Additional recommendations

- This is an unnecessary parameter being and is removed with a warning comment inserted into the source code. There should be no action from the user.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
