# SPRKPY1030

pyspark.sql.session.SparkSession.Builder.appName

> This issue code has been **deprecated**

Message: pyspark.sql.session.SparkSession.Builder.appName has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.session.SparkSession.Builder.appName](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.SparkSession.builder.appName.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.session.SparkSession.Builder.appName` function that generates this EWI. In this example, the `appName` function is used to set **MyApp** as the name of the application.

```python
session = SparkSession.builder.appName("MyApp").getOrCreate()
```

**Output**

The SMA adds the EWI `SPRKPY1030` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
#EWI: SPRKPY1030 => pyspark.sql.session.SparkSession.Builder.appName has a workaround, see documentation for more info
session = Session.builder.appName("MyApp").getOrCreate()
```

**Recommended fix**

As a workaround, you can import the [snowpark_extensions](https://pypi.org/project/snowpark-extensions/) package which provides an extension for the `appName` function.

```python
import snowpark_extensions
session = SessionBuilder.appName("MyApp").getOrCreate()
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
