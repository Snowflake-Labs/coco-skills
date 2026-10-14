# SPRKPY1049

pyspark.sql.session.SparkSession.sparkContext

> This issue code has been **deprecated** since [Spark Conversion Core Version 2.1.9](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-2.1.9)

Message: pyspark.sql.session.SparkSession.sparkContext has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.session.SparkSession.sparkContext](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.SparkSession.sparkContext.html) which has a workaround.

## Scenario

**Input**

Below is an example that creates a spark session and then uses the `SparkContext` property to print the appName.

```python
print("APP Name :"+spark.sparkContext.appName())
```

**Output**

The tool adds the EWI `SPRKPY1049` indicating that a workaround can be implemented.

```python
#EWI: SPRKPY1049 => pyspark.sql.session.SparkSession.sparkContext has a workaround, see documentation for more info
print("APP Name :"+spark.sparkContext.appName())
```

**Recommended fix**

SparkContext is not supported in SnowPark but you can access the methods and properties from SparkContext directly from the Session instance.

```python
# Pyspark
print("APP Name :"+spark.sparkContext.appName())
can be used in SnowPark removing the sparkContext as:
#Manual adjustment in SnowPark
print("APP Name :"+spark.appName());
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
