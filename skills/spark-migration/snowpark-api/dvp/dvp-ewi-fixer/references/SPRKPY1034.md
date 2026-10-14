# SPRKPY1034

pyspark.sql.functions.desc

> This issue code has been **deprecated**

Message: pyspark.sql.functions.desc has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.functions.desc](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.desc.html) function, which has a workaround.

## Scenarios

The `pyspark.sql.functions.desc` function takes either a column object or the name of the column as a string as its parameter. Both scenarios are not supported by Snowpark so this EWI is generated.

### Scenario 1

**Input**

Below is an example of a use of the `pyspark.sql.functions.desc` function that takes a column object as parameter.

```python
df.orderBy(desc(col))
```

**Output**

The SMA adds the EWI `SPRKPY1034` to the output code to let you know that the `desc` function with a column object parameter is not directly supported by Snowpark, but it has a workaround.

```python
#EWI: SPRKPY1034 => pyspark.sql.functions.desc has a workaround, see documentation for more info
df.orderBy(desc(col))
```

**Recommended fix**

As a workaround, you can call the [snowflake.snowpark.Column.desc](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.Column.desc) function from the column parameter.

```python
df.orderBy(col.desc())
```

### Scenario 2

**Input**

Below is an example of a use of the `pyspark.sql.functions.desc` function that takes the name of the column as parameter.

```python
df.orderBy(desc("colName"))
```

**Output**

The SMA adds the EWI `SPRKPY1034` to the output code to let you know that the `desc` function with a column name parameter is not directly supported by Snowpark, but it has a workaround.

```python
#EWI: SPRKPY1034 => pyspark.sql.functions.desc has a workaround, see documentation for more info
df.orderBy(desc("colName"))
```

**Recommended fix**

As a workaround, you can convert the string parameter into a column object using the [snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.col) function and then call the [snowflake.snowpark.Column.desc](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.Column.desc) function.

```python
df.orderBy(col("colName").desc())
````

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
