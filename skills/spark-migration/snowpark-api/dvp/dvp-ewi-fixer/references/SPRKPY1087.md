# SPRKPY1087

pyspark.sql.dataframe.DataFrame.writeTo

Message: The pyspark.sql.dataframe.DataFrame.writeTo function is not supported, but it has a workaround.

Category: Warning.

## Description

The [pyspark.sql.dataframe.DataFrame.writeTo](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.writeTo.html) function is not supported. The workaround is to use Snowpark DataFrameWriter [SaveAsTable](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.saveAsTable) method instead.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.dataframe.DataFrame.writeTo` function, the dataframe `df` is written into a table name `Personal_info`.

```python
df = spark.createDataFrame([["John", "Berry"], ["Rick", "Berry"], ["Anthony", "Davis"]],
                                 schema=["FIRST_NAME", "LAST_NAME"])

df.writeTo("Personal_info")
```

**Output**

The SMA adds the EWI `SPRKPY1087` to the output code to let you know that this function is not supported, but has a workaround.

```python
df = spark.createDataFrame([["John", "Berry"], ["Rick", "Berry"], ["Anthony", "Davis"]],
                                 schema=["FIRST_NAME", "LAST_NAME"])

#EWI: SPRKPY1087 => pyspark.sql.dataframe.DataFrame.writeTo is not supported, but it has a workaround.
df.writeTo("Personal_info")
```

**Recommended fix**

The workaround is to use Snowpark DataFrameWriter SaveAsTable method instead.

```python
df = spark.createDataFrame([["John", "Berry"], ["Rick", "Berry"], ["Anthony", "Davis"]],
                                 schema=["FIRST_NAME", "LAST_NAME"])

df.write.saveAsTable("Personal_info")
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
