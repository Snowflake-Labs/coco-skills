# SPRKPY1067

pyspark.sql.functions.split

Message: The pyspark.sql.functions.split has parameters that are not supported in Snowpark.

Category: Warning

### Description

This issue appears when the tool detects the usage of [pyspark.sql.functions.split](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.split.html) with more than two parameters or a regex pattern as a parameter; both cases are not supported.

## Scenarios

### Scenario 1

**Input code**

In this example the split function has more than two parameters.

```python
df.select(split(columnName, ",", 5))
```

**Output code**

The tool adds this EWI on the output code to let you know that this function is not supported when it has more than two parameters.

```python
#EWI: SPRKPY1067 => Snowpark does not support split functions with more than two parameters or containing regex pattern. See documentation for more info.
df.select(split(columnName, ",", 5))
```

**Recommended fix**

Keep the split function with only two parameters.

```python
df.select(split(columnName, ","))
```

### Scenario 2

**Input code**

In this example the split function has a regex pattern as a parameter.

```python
df.select(split(columnName, "^([\d]+-[\d]+-[\d])"))
```

**Output code**

The tool adds this EWI on the output code to let you know that this function is not supported when it has a regex pattern as a parameter.

```python
#EWI: SPRKPY1067 => Snowpark does not support split functions with more than two parameters or containing regex pattern. See documentation for more info.
df.select(split(columnName, "^([\d]+-[\d]+-[\d])"))
```

**Recommended fix**

The spark signature for this method `functions.split(str: ColumnOrName, pattern: str, limit: int = - 1)`
not exactly match with the method in Snowpark `functions.split(str: Union[Column, str], pattern: Union[Column, str])`
so for now the scenario using regular expression does not have a recommended fix.


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
