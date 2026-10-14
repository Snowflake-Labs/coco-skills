# SPRKPY1070

The `mode` parameter in the methods of CSV, JSON and PARQUET is transformed to `overwrite`

Message: The `mode` argument is transformed to `overwrite`, check the variable value and set the corresponding bool value.

Category: Warning

## Description

When there is a usage of:
- [pyspark.sql.readwriter.DataFrameWriter.csv](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriter.csv.html)
- [pyspark.sql.readwriter.DataFrameWriter.json](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriter.json.html)
- [pyspark.sql.readwriter.DataFrameWriter.parquet](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriter.json.html)

The tool analyzes the parameter `mode` to determinate if the value is `overwrite`.

## Scenarios

### Scenario 1

**Input code**

For this scenario the tool detects that the mode parameter can set the corresponding bool value.

```python
df.write.csv(file_path, mode="overwrite")
```

**Output code:**

The SMA tool analyzes the mode parameter, determinate that the value is `overwrite` and set the corresponding bool value

```python
df.write.csv(file_path, format_type_options = dict(compression = "None"), overwrite = True)
```

**Recommended fix**

There is not a recommended fix for this scenario because the tool performed the corresponding transformation.

**Scenario 2:**

**Input code**

In this scenario the tool can not validate the value is `overwrite`.

```python
df.write.csv(file_path, mode=myVal)
```

**Output code:**

The SMA adds an EWI message indicating that the mode parameter was transformed to 'overwrite', but it's also to let you
know that it is better to check the variable value and set the correct bool value.

```python
#EWI: SPRKPY1070 => The 'mode' argument is transformed to 'overwrite', check the variable value and set the corresponding bool value.
df.write.csv(file_path, format_type_options = dict(compression = "None"), overwrite = myVal)
```

**Recommended fix**

Check for the value of the parameter `mode` and add the correct value for the parameter `overwrite`.

```python
df.write.csv(file_path, format_type_options = dict(compression = "None"), overwrite = True)
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
