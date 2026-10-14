# SPRKPY1061

pyspark.sql.functions.unix_timestamp

Message: Snowpark does not support unix_timestamp functions with no parameters.

Category: Warning

## Description

In Snowpark, the first parameter is mandatory; the issue appears when the tool detects the usage of [pyspark.sql.functions.unix_timestamp](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.unix_timestamp.html) with no parameters.

## Scenario

**Input**

Below an example that calls the `unix_timestamp` method without parameters.

```python
data = [["2015-04-08", "10"],["2015-04-10", "15"]]

df = spark.createDataFrame(data, ['dt', 'val'])
df.select(unix_timestamp()).show()
```

**Output**

The Snowpark signature for this function `unix_timestamp(e: ColumnOrName, fmt: Optional["Column"] = None)`, as you can
notice the first parameter it's required.

The tool adds this EWI `SPRKPY1061` to let you know that function unix_timestamp with no parameters it's not supported in Snowpark.

```python
data = [["2015-04-08", "10"],["2015-04-10", "15"]]

df = spark.createDataFrame(data, ['dt', 'val'])
#EWI: SPRKPY1061 => Snowpark does not support unix_timestamp functions with no parameters. See documentation for more info.
df.select(unix_timestamp()).show()
```

**Recommended fix**

As a workaround, you can add at least the name or column of the timestamp string.

```python
data = [["2015-04-08", "10"],["2015-04-10", "15"]]

df = spark.createDataFrame(data, ["dt", "val"])
df.select(unix_timestamp("dt")).show()
```

## Additional recommendations

- You also can add the [current_timestamp()](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.current_timestamp) as the first parameter.
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
