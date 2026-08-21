# SPRKPY1060

Message: The authentication mechanism is connection.json (template provided).

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.conf.SparkConf](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.SparkConf.html). 

## Scenario

**Input**

Since the authentication mechanism is different in Snowpark, the tool removes the usages and creates a **connection configuration file (connection.json)** instead.

```python
from pyspark import SparkConf

my_conf = SparkConf(loadDefaults=True)
```

**Output**

The tool adds the EWI `SPRKPY1060` indicating that the authentication mechanism is different.

```python
#EWI: SPRKPY1002 => pyspark.conf.SparkConf is not supported
#EWI: SPRKPY1060 => The authentication mechanism is connection.json (template provided).
#my_conf = Session.builder.configs(connection_parameter).getOrCreate()

my_conf = None
```

**Recommended fix**

To create a connection it is necessary that you fill in the information in the `connection.json` file.

```python
{
  "user": "<USER>",
  "password": "<PASSWORD>",
  "account": "<ACCOUNT>",
  "role": "<ROLE>",
  "warehouse": "<WAREHOUSE>",
  "database": "<DATABASE>",
  "schema": "<SCHEMA>"
}
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).

