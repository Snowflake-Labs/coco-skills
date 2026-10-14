# SPRKPY1048

pyspark.sql.session.SparkSession.conf

> This issue code has been **deprecated** since [Spark Conversion Core Version 2.4.0](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-2.4.0)

Message: pyspark.sql.session.SparkSession.conf has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.session.SparkSession.conf](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.SparkSession.conf.html) which has a workaround.

## Scenario

**Input**

Below is an example of how to set a configuration into the property `conf` .

```python
spark.conf.set("spark.sql.crossJoin.enabled", "true")
```

**Output**

The tool adds the EWI `SPRKPY1048` indicating that a workaround can be implemented.

```python
#EWI: SPRKPY1048 => pyspark.sql.session.SparkSession.conf has a workaround, see documentation for more info
spark.conf.set("spark.sql.crossJoin.enabled", "true")
```

**Recommended fix**

SparkSession.conf is used to pass some specific settings only used by Pyspark and doesn't apply to Snowpark. You can remove or comment on the code

```python
#spark.conf.set("spark.sql.crossJoin.enabled", "true")
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
