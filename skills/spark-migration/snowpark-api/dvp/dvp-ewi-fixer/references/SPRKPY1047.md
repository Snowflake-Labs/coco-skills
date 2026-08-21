# SPRKPY1047

pyspark.context.SparkContext.setLogLevel

> This issue code has been **deprecated** since [Spark Conversion Core Version 4.6.0](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.6.0)

Message: pyspark.context.SparkContext.setLogLevel has workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.context.SparkContext.setLogLevel](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.SparkContext.setLogLevel.html) which has a workaround.

## Scenario

**Input**

Below is an example of the usage of the method `setLogLevel`.

```python
sparkSession.sparkContext.setLogLevel("WARN")
```

**Output**

The tool adds the EWI `SPRKPY1047` indicating that a workaround can be implemented.

```python
#EWI: SPRKPY1047 => pyspark.context.SparkContext.setLogLevel has a workaround, see documentation for more info
sparkSession.sparkContext.setLogLevel("WARN")
```

**Recommended fix**

Replace the `setLogLevel` function usage with `logging.basicConfig` that provides a set of convenience functions for simple logging usage. In order to use it, we need to import two modules, "logging" and "sys", and the level constant should be replaced using the "Level equivalent table":

```python
import logging
import sys
logging.basicConfig(stream=sys.stdout, level=logging.WARNING)
```

- Level equivalent table

|Level source parameter|Level target parameter|
|---|---|
|"ALL"|This has no equivalent|
|"DEBUG"|logging.DEBUG|
|"ERROR"|logging.ERROR|
|"FATAL"|logging.CRITICAL|
|"INFO"|logging.INFO|
|"OFF"|logging.NOTSET|
|"TRACE"|This has no equivalent|
|"WARN"|logging.WARNING|

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
