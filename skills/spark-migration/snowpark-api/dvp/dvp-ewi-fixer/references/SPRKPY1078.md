# SPRKPY1078

pyspark.context.SparkContext.setLogLevel

Message: The argument of the pyspark.context.SparkContext.setLogLevel function is not a literal value and therefore could not be evaluated

Category: Warning

## Description

This issue appears when the SMA detects the use of the [pyspark.context.SparkContext.setLogLevel](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.SparkContext.setLogLevel.html) function with an argument that is not a literal value, for example, when the argument is a variable.

The SMA does a static analysis of your source code and therefore it is not possible to evaluate the content of that argument and determine an equivalent in Snowpark.

## Scenario

**Input**

In this example the logLevel is defined in the variable my_log_level, then my_log_level used as parameter by the setLogLevel method.

```python
my_log_level = "WARN"
sparkSession.sparkContext.setLogLevel(my_log_level)
```

**Output**

SMA is unable to evaluate the argument for the log level parameter, so the EWI SPRKPY1078 is added over the line of the transformed logging:
```python
my_log_level = "WARN"
#EWI: SPRKPY1078 => my_log_level is not a literal value and therefore could not be evaluated. Make sure the value of my_log_level is a valid level in Snowpark. Valid log levels are: logging.CRITICAL, logging.DEBUG, logging.ERROR, logging.INFO, logging.NOTSET, logging.WARNING
logging.basicConfig(stream = sys.stdout, level = my_log_level)
```

**Recommended fix**

Even though the SMA was unable to evaluate the argument, it will transform the `pyspark.context.SparkContext.setLogLevel` function into the Snowpark equivalent. Please make sure the value of the `level` argument in the generated output code is a valid and equivalent log level in Snowpark according to the table below:

| PySpark log level | Snowpark log level equivalent |
  | ----------------- | ----------------------------- |
| ALL               | logging.NOTSET                |
| DEBUG             | logging.DEBUG                 |
| ERROR             | logging.ERROR                 |
| FATAL             | logging.CRITICAL              |
| INFO              | logging.INFO                  |
| OFF               | logging.WARNING               |
| TRACE             | logging.NOTSET                |
| WARN              | logging.WARNING               |

Thus the recommended fix will looks like:

```python
my_log_level = logging.WARNING
logging.basicConfig(stream = sys.stdout, level = my_log_level)
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
