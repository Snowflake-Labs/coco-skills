# SPRKPY1069

pyspark.sql.DataFrameWriter.parquet

Message: If partitionBy parameter is a list, Snowpark will throw an error.

Category: Warning

## Description

When there is a usage of [pyspark.sql.readwriter.DataFrameWriter.parquet](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriter.parquet.html) method where it comes to the parameter `partitionBy`, the tool shows the EWI.

This is because in Snowpark the [DataFrameWriter.parquet](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.parquet) only supports a `ColumnOrSqlExpr` as a partitionBy parameter.

## Scenarios

### Scenario 1

**Input code:**

For this scenario the partitionBy parameter is not a list.

```python
df = spark.createDataFrame([(25, "Alice", "150"), (30, "Bob", "350")], schema=["age", "name", "value"])

df.write.parquet(file_path, partitionBy="age")
```

**Output code:**

The tool adds the EWI `SPRKPY1069` to let you know that Snowpark throws an error if parameter is a list.

```python
df = spark.createDataFrame([(25, "Alice", "150"), (30, "Bob", "350")], schema=["age", "name", "value"])

#EWI: SPRKPY1069 => If partitionBy parameter is a list, Snowpark will throw and error.
df.write.parquet(file_path, partition_by = "age", format_type_options = dict(compression = "None"))
```

**Recommended fix**

There is not a recommended fix for this scenario because the tool always adds this EWI just in case the partitionBy parameter is a list.
Remember that in Snowpark, only accepts cloud locations using a [snowflake stage](https://docs.snowflake.com/en/user-guide/data-load-local-file-system-create-stage).

```python
df = spark.createDataFrame([(25, "Alice", "150"), (30, "Bob", "350")], schema=["age", "name", "value"])

stage = f'{Session.get_fully_qualified_current_schema()}.{_generate_prefix("TEMP_STAGE")}'
Session.sql(f'CREATE TEMPORARY STAGE IF NOT EXISTS {stage}').show()
Session.file.put(f"file:///path/to/data/file.parquet", f"@{stage}")

df.write.parquet(stage, partition_by = "age", format_type_options = dict(compression = "None"))
```

### Scenario 2

**Input code:**

For this scenario the partitionBy parameter is a list.

```python
df = spark.createDataFrame([(25, "Alice", "150"), (30, "Bob", "350")], schema=["age", "name", "value"])

df.write.parquet(file_path, partitionBy=["age", "name"])
```

**Output code:**

The tool adds the EWI `SPRKPY1069` to let you know that Snowpark throws an error if parameter is a list.

```python
df = spark.createDataFrame([(25, "Alice", "150"), (30, "Bob", "350")], schema=["age", "name", "value"])

#EWI: SPRKPY1069 => If partitionBy parameter is a list, Snowpark will throw and error.
df.write.parquet(file_path, partition_by = ["age", "name"], format_type_options = dict(compression = "None"))
```

**Recommended fix**

If the value of the parameter is a `list`, then replace it with a `ColumnOrSqlExpr`.

```python
df.write.parquet(file_path, partition_by = sql_expr("age || name"), format_type_options = dict(compression = "None"))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
