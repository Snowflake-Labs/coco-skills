# SPRKPY1050

pyspark.conf.SparkConf.set

Message: pyspark.conf.SparkConf.set has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.conf.SparkConf.set](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.SparkConf.set.html) which has a workaround.

## Scenario

**Input**

Below is an example that sets a variable using `conf.set`.

```python
conf = SparkConf().setAppName('my_app')

conf.set("spark.storage.memoryFraction", "0.5")
```

**Output**

The tool adds the EWI `SPRKPY1050` indicating that a workaround can be implemented.

```python
conf = SparkConf().setAppName('my_app')

#EWI: SPRKPY1050 => pyspark.conf.SparkConf.set has a workaround, see documentation for more info
conf.set("spark.storage.memoryFraction", "0.5")
```

**Recommended fix**

SparkConf.set is used to set a configuration setting only used by Pyspark and doesn't apply to Snowpark. You can remove or comment on the code

```python
#conf.set("spark.storage.memoryFraction", "0.5")
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
