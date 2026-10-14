# SPRKCNTPY3502
The AppName method of pyspark.sql.session.SparkSession.Builder has been replaced with the SetName function to provide equivalent functionality in Snowpark Connect when creating a session.

Message: The AppName method of pyspark.sql.session.SparkSession.Builder has been replaced with the SetName function to provide equivalent functionality in Snowpark Connect

Category: Warning.

## Description

This issue occurs when the SMA detects the use of `AppName` Spark function while creating a `SparkSession` instance. The tool replaces this function with Snowpark initialization statements to achieve equivalent functionality.

## Scenario

**Input**

Below is an example of a Python AppName Spark function that will be replaced by the SetAppName Snowpark Connect function, and therefore it will add this EWI.

```python
spark = (
  SparkSession
    .builder
    .appName("MyApp")
    .getOrCreate()
)
```

**Output**

The SMA adds the EWI `SPRKCNTPY3501` to the output code to let you know that this element has been transformed.

```python
conf = SparkConf()
conf.setAppName("MyApp")
#EWI: SPRKCNTPY3501 => The AppName method of pyspark.sql.session.SparkSession.Builder has been replaced with the SetAppName function to provide equivalent functionality in Snowpark Connect

spark = ( snowpark_connect.server.init_spark_session(conf = conf)
)
```

**Recommended fix**

Review the output code and ensure that the Snowpark Connect session is configured correctly with the desired application name.

## Additional recommendations

- Please review the Snowpark Connect documentation to understand how to configure and use Snowpark Connect sessions effectively.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
