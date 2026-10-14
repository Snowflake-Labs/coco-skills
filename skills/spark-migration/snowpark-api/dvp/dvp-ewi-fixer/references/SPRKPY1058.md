# SPRKPY1058

Method is not supported with a Platform specific key

Message: < method > with < key > Platform specific key is not supported.

Category: ConversionError

## Description

The `get` and `set` methods from [pyspark.sql.conf.RuntimeConfig](https://spark.apache.org/docs/3.5.3/api/python/reference/pyspark.sql/api/pyspark.sql.conf.RuntimeConfig.html#pyspark.sql.conf.RuntimeConfig) are not supported with a Platform specific key.

## Scenarios

Not all usages of `get` or `set` methods are going to have an EWI in the output code.
This EWI appears when the tool detects the usage of these methods with a Platform specific key which is not supported.

### Scenario 1

**Input**

Below is an example of the `get` or `set` methods with supported keys in Snowpark.

```python
session.conf.set("use_constant_subquery_alias", False)
spark.conf.set("sql_simplifier_enabled", True)

session.conf.get("use_constant_subquery_alias")
session.conf.get("use_constant_subquery_alias")
```

**Output**

Since the keys are supported in Snowpark the tool does not add the EWI on the output code.

```python
session.conf.set("use_constant_subquery_alias", True)
session.conf.set("sql_simplifier_enabled", False)

session.conf.get("use_constant_subquery_alias")
session.conf.get("sql_simplifier_enabled")
```

**Recommended fix**

There is no recommended fix for this scenario.

### Scenario 2

**Input**

Below is an example using not supported keys.

```python
data =
    [
      ("John", 30, "New York"),
      ("Jane", 25, "San Francisco")
    ]

session.conf.set("spark.sql.shuffle.partitions", "50")
spark.conf.set("spark.yarn.am.memory", "1g")

session.conf.get("spark.sql.shuffle.partitions")
session = spark.conf.get("spark.yarn.am.memory")

df = spark.createDataFrame(data, schema=["Name", "Age", "City"])
```

**Output**

The tool adds this EWI `SPRKPY1058` on the output code to let you know that these methods are not supported with a Platform specific key.

```python
data =
    [
      ("John", 30, "New York"),
      ("Jane", 25, "San Francisco")
    ]

#EWI: SPRKPY1058 => pyspark.sql.conf.RuntimeConfig.set method with this "spark.sql.shuffle.partitions" Platform specific key is not supported.
spark.conf.set("spark.sql.shuffle.partitions", "50")
#EWI: SPRKPY1058 => pyspark.sql.conf.RuntimeConfig.set method with this "spark.yarn.am.memory" Platform specific key is not supported.
spark.conf.set("spark.yarn.am.memory", "1g")

#EWI: SPRKPY1058 => pyspark.sql.conf.RuntimeConfig.get method with this "spark.sql.shuffle.partitions" Platform specific key is not supported.
spark.conf.get("spark.sql.shuffle.partitions")
#EWI: SPRKPY1058 => pyspark.sql.conf.RuntimeConfig.get method with this "spark.yarn.am.memory" Platform specific key is not supported.
spark.conf.get("spark.yarn.am.memory")

df = spark.createDataFrame(data, schema=["Name", "Age", "City"])
```

**Recommended fix**

The recommended fix is to remove these methods.

```python
data =
    [
      ("John", 30, "New York"),
      ("Jane", 25, "San Francisco")
    ]

df = spark.createDataFrame(data, schema=["Name", "Age", "City"])
```


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).

