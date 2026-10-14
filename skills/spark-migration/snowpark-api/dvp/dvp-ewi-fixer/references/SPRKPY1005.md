# SPRKPY1005

pyspark.conf.SparkConf

> This Issue Code has been **deprecated** since [Spark Conversion Core Version 4.8.0 ](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.8.0)

Message: pyspark.conf.SparkConf is not required

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.conf.SparkConf](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.SparkConf.html) which is not required.

## Scenario

**Input**

SparkConf could be called without parameters or with loadDefaults.

```python
from pyspark import SparkConf

my_conf = SparkConf(loadDefaults=True)
```

**Output**

For both cases (with or without parameters) SMA creates a [Snowpark Session.builder](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.Session.SessionBuilder.configs) object:

```python
#EWI: SPRKPY1005 => pyspark.conf.SparkConf is not required
#from pyspark import SparkConf
pass

#EWI: SPRKPY1005 => pyspark.conf.SparkConf is not required
my_conf = Session.builder.configs({"user" : "my_user", "password" : "my_password", "account" : "my_account", "role" : "my_role", "warehouse" : "my_warehouse", "database" : "my_database", "schema" : "my_schema"}).create()
```

## Additional recommendations

- This is an unnecessary parameter being removed with a warning comment being inserted. There should be no additional action from the user.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
