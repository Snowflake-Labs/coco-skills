# SPRKPY1079
pyspark.context.SparkContext.setLogLevel

Message: The argument of the pyspark.context.SparkContext.setLogLevel function is not a valid PySpark log level

Category: Warning

## Description

This issue appears when the SMA detects the use of the [pyspark.context.SparkContext.setLogLevel](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.SparkContext.setLogLevel.html) function with an argument that is not a valid log level in PySpark, and therefore an equivalent could not be determined in Snowpark.

## Scenario

**Input**

here the log level uses "INVALID_LOG_LEVEL" that is not a valid Pyspark log level.
```python
sparkSession.sparkContext.setLogLevel("INVALID_LOG_LEVEL")
```

**Output**

SMA can not recognize the log level "INVALID_LOG_LEVEL", even though SMA makes the conversion the EWI SPRKPY1079 is added to indicate a possible problem.
```python
#EWI: SPRKPY1079 => INVALID_LOG_LEVEL is not a valid PySpark log level, therefore an equivalent could not be determined in Snowpark. Valid PySpark log levels are: ALL, DEBUG, ERROR, FATAL, INFO, OFF, TRACE, WARN
logging.basicConfig(stream = sys.stdout, level = logging.INVALID_LOG_LEVEL)
```

**Recommended fix**

Make sure that the log level used in the pyspark.context.SparkContext.setLogLevel function is a valid log level in [PySpark](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.SparkContext.setLogLevel.html) or in [Snowpark](https://docs.snowflake.com/en/developer-guide/snowpark/python/troubleshooting) and try again.
```python
logging.basicConfig(stream = sys.stdout, level = logging.DEBUG)
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
