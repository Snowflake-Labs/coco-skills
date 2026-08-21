# SPRKPY1083

pyspark.sql.readwriter.DataFrameWriter.save

Message: The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.

Category: Warning

## Description

The [pyspark.sql.readwriter.DataFrameWriter.save](https://spark.apache.org/docs/3.5.3/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriter.save.html) function is not supported. The workaround is to use Snowpark DataFrameWriter methods instead.

## Scenarios

The spark signature for this method `DataFrameWriter.save(path, format, mode, partitionBy, **options)` does not exists in Snowpark.
Therefore, any usage of the load function it's going to have an EWI in the output code.

### Scenario 1

**Input**

Below is an example that tries to save data with `CSV` format.

```python
path_csv_file = "/path/to/file.csv"

data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]

df = my_session.createDataFrame(data, schema=["Name", "Age", "City"])

df.write.save(path_csv_file, format="csv")
df.write.save(path_csv_file, format="csv", mode="overwrite")
df.write.save(path_csv_file, format="csv", mode="overwrite", lineSep="\r\n", dateFormat="YYYY/MM/DD")
df.write.save(path_csv_file, format="csv", mode="overwrite", partitionBy="City", lineSep="\r\n", dateFormat="YYYY/MM/DD")
```

**Output**

The tool adds this EWI `SPRKPY1083` on the output code to let you know that this function is not supported by Snowpark, but it has a workaround.

```python
path_csv_file = "/path/to/file.csv"

data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]

df = my_session.createDataFrame(data, schema=["Name", "Age", "City"])

#EWI: SPRKPY1083 => The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.
df.write.save(path_csv_file, format="csv")
#EWI: SPRKPY1083 => The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.
df.write.save(path_csv_file, format="csv", mode="overwrite")
#EWI: SPRKPY1083 => The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.
df.write.save(path_csv_file, format="csv", mode="overwrite", lineSep="\r\n", dateFormat="YYYY/MM/DD")
#EWI: SPRKPY1083 => The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.
df.write.save(path_csv_file, format="csv", mode="overwrite", partitionBy="City", lineSep="\r\n", dateFormat="YYYY/MM/DD")
```

**Recommended fix**

As a workaround you can use [Snowpark DataFrameWriter](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter) methods instead.

- Fixing `path` and `format` parameters:

  - Replace the `load` method with [csv](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameWriter.csv) or [copy_into_location](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameWriter.copy_into_location) method.
  - If you are using `copy_into_location` method, you need to specify the format with the `file_format_type parameter`.
  - The first parameter `path` must be in a stage to make an equivalence with [Snowpark](https://docs.snowflake.com/en/user-guide/data-load-local-file-system-create-stage).

Below is an example that creates a temporal stage and put the file into it, then calls one the methods mentioned above.

```python
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]
df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

# Stage creation

temp_stage = f'{Session.get_fully_qualified_current_schema()}.{_generate_prefix("TEMP_STAGE")}'
my_session.sql(f'CREATE TEMPORARY STAGE IF NOT EXISTS {temp_stage}').show()
my_session.file.put(f"file:///path/to/file.csv", f"@{temp_stage}")
stage_file_path = f"{temp_stage}file.csv"

# Using csv method
df.write.csv(stage_file_path)

# Using copy_into_location method
df.write.copy_into_location(stage_file_path, file_format_type="csv")
```

- Fixing `mode` parameter:
  - Use the [mode](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.mode) function from [Snowpark DataFrameWriter](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter), as follows:

Below is an example that adds into the daisy chain the `mode` method with `overwrite` as a parameter.

```python
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]
df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

# Using csv method
df.write.mode("overwrite").csv(temp_stage)

# Using copy_into_location method
df.write.mode("overwrite").copy_into_location(temp_stage, file_format_type="csv")
```

- Fixing `partitionBy` parameter:
  - Use the [partition_by](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.csv) parameter from the `CSV` method, as follows:

Below is an example that used the `partition_by` parameter from the `CSV` method.

```python
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]
df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

# Using csv method
df.write.csv(temp_stage, partition_by="City")

# Using copy_into_location method
df.write.copy_into_location(temp_stage, file_format_type="csv", partition_by="City")
```

- Fixing `options` parameter:
  - Use the [format_type_options](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.csv) parameter from the `CSV` method, as follows:

The options between spark and snowpark are not the same, in this case `lineSep` and `dateFormat` are replaced with `RECORD_DELIMITER` and `DATE_FORMAT`,
the **Additional recommendations** section has table with all the Equivalences.

Below is an example that creates a dictionary with `RECORD_DELIMITER` and `DATE_FORMAT`, and calls the `options` method with that dictionary.

```python
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]
df = spark.createDataFrame(data, schema=["Name", "Age", "City"])
optionsParam = {"RECORD_DELIMITER": "\r\n", "DATE_FORMAT": "YYYY/MM/DD"}

# Using csv method
df.write.csv(stage, format_type_options=optionsParam)

# Using copy_into_location method
df.write.csv(stage, file_format_type="csv", format_type_options=optionsParam)
```

### Scenario 2

**Input code**

Below is an example that tries to save data with `JSON` format.

```python
path_json_file = "/path/to/file.json"

data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]

df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

df.write.save(path_json_file, format="json")
df.write.save(path_json_file, format="json", mode="overwrite")
df.write.save(path_json_file, format="json", mode="overwrite", dateFormat="YYYY/MM/DD", timestampFormat="YYYY-MM-DD HH24:MI:SS.FF3")
df.write.save(path_json_file, format="json", mode="overwrite", partitionBy="City", dateFormat="YYYY/MM/DD", timestampFormat="YYYY-MM-DD HH24:MI:SS.FF3")
```

**Output code**

The tool adds this EWI `SPRKPY1083` on the output code to let you know that this function is not supported by Snowpark, but it has a workaround.

```python
path_json_file = "/path/to/file.json"

data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]

df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

#EWI: SPRKPY1083 => The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.
df.write.save(path_json_file, format="json")
#EWI: SPRKPY1083 => The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.
df.write.save(path_json_file, format="json", mode="overwrite")
#EWI: SPRKPY1083 => The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.
df.write.save(path_json_file, format="json", mode="overwrite", dateFormat="YYYY/MM/DD", timestampFormat="YYYY-MM-DD HH24:MI:SS.FF3")
#EWI: SPRKPY1083 => The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.
df.write.save(path_json_file, format="json", mode="overwrite", partitionBy="City", dateFormat="YYYY/MM/DD", timestampFormat="YYYY-MM-DD HH24:MI:SS.FF3")
```

**Recommended fix**

As a workaround you can use [Snowpark DataFrameReader](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameReader) methods instead.

- Fixing `path` and `format` parameters:

  - Replace the `load` method with [json](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameWriter.json) or [copy_into_location](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameWriter.copy_into_location) method
  - If you are using `copy_into_location` method, you need to specify the format with the `file_format_type parameter`.
  - The first parameter `path` must be in a stage to make an equivalence with [Snowpark](https://docs.snowflake.com/en/user-guide/data-load-local-file-system-create-stage).

Below is an example that creates a temporal stage and put the file into it, then calls one the methods mentioned above.

```python
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]
df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

# Stage creation

temp_stage = f'{Session.get_fully_qualified_current_schema()}.{_generate_prefix("TEMP_STAGE")}'
my_session.sql(f'CREATE TEMPORARY STAGE IF NOT EXISTS {temp_stage}').show()
my_session.file.put(f"file:///path/to/file.json", f"@{temp_stage}")
stage_file_path = f"{temp_stage}file.json"

# Using json method
df.write.json(stage_file_path)

# Using copy_into_location method
df.write.copy_into_location(stage_file_path, file_format_type="json")
```

- Fixing `mode` parameter:
  - Use the [mode](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.mode) function from [Snowpark DataFrameWriter](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter), as follows:

Below is an example that adds into the daisy chain the `mode` method with `overwrite` as a parameter.

```python
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]
df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

# Using json method
df.write.mode("overwrite").json(temp_stage)

# Using copy_into_location method
df.write.mode("overwrite").copy_into_location(temp_stage, file_format_type="json")
```

- Fixing `partitionBy` parameter:
  - Use the [partition_by](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.csv) parameter from the `CSV` method, as follows:

Below is an example that used the `partition_by` parameter from the `CSV` method.

```python
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]
df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

# Using json method
df.write.json(temp_stage, partition_by="City")

# Using copy_into_location method
df.write.copy_into_location(temp_stage, file_format_type="json", partition_by="City")
```

- Fixing `options` parameter:
  - Use the [format_type_options](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.csv) parameter from the `CSV` method, as follows:

The options between spark and snowpark are not the same, in this case `dateFormat` and `timestampFormat` are replaced with `DATE_FORMAT` and `TIMESTAMP_FORMAT`,
the **Additional recommendations** section has table with all the Equivalences.

Below is an example that creates a dictionary with `DATE_FORMAT` and `TIMESTAMP_FORMAT`, and calls the `options` method with that dictionary.

```python
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]
df = spark.createDataFrame(data, schema=["Name", "Age", "City"])
optionsParam = {"DATE_FORMAT": "YYYY/MM/DD", "TIMESTAMP_FORMAT": "YYYY-MM-DD HH24:MI:SS.FF3"}

# Using json method
df.write.json(stage, format_type_options=optionsParam)

# Using copy_into_location method
df.write.copy_into_location(stage, file_format_type="json", format_type_options=optionsParam)
```

### Scenario 3

**Input code**

Below is an example that tries to save data with `PARQUET` format.

```python
path_parquet_file = "/path/to/file.parquet"

data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]

df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

df.write.save(path_parquet_file, format="parquet")
df.write.save(path_parquet_file, format="parquet", mode="overwrite")
df.write.save(path_parquet_file, format="parquet", mode="overwrite", pathGlobFilter="*.parquet")
df.write.save(path_parquet_file, format="parquet", mode="overwrite", partitionBy="City", pathGlobFilter="*.parquet")
```

**Output code**

The tool adds this EWI `SPRKPY1083` on the output code to let you know that this function is not supported by Snowpark, but it has a workaround.

```python
path_parquet_file = "/path/to/file.parquet"

data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]

df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

#EWI: SPRKPY1083 => The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.
df.write.save(path_parquet_file, format="parquet")
#EWI: SPRKPY1083 => The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.
df.write.save(path_parquet_file, format="parquet", mode="overwrite")
#EWI: SPRKPY1083 => The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.
df.write.save(path_parquet_file, format="parquet", mode="overwrite", pathGlobFilter="*.parquet")
#EWI: SPRKPY1083 => The pyspark.sql.readwriter.DataFrameWriter.save function is not supported. A workaround is to use Snowpark DataFrameWriter copy_into_location method instead.
df.write.save(path_parquet_file, format="parquet", mode="overwrite", partitionBy="City", pathGlobFilter="*.parquet")
```

**Recommended fix**

As a workaround you can use [Snowpark DataFrameReader](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameReader) methods instead.

- Fixing `path` and `format` parameters:

  - Replace the `load` method with [parquet](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameWriter.parquet) or [copy_into_location](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameWriter.copy_into_location) method.
  -  If you are using `copy_into_location` method, you need to specify the format with the `file_format_type parameter`.
  - The first parameter `path` must be in a stage to make an equivalence with [Snowpark](https://docs.snowflake.com/en/user-guide/data-load-local-file-system-create-stage).

Below is an example that creates a temporal stage and put the file into it, then calls one the methods mentioned above.

```python
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]
df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

# Stage creation

temp_stage = f'{Session.get_fully_qualified_current_schema()}.{_generate_prefix("TEMP_STAGE")}'
my_session.sql(f'CREATE TEMPORARY STAGE IF NOT EXISTS {temp_stage}').show()
my_session.file.put(f"file:///path/to/file.parquet", f"@{temp_stage}")
stage_file_path = f"{temp_stage}file.parquet"

# Using parquet method
df.write.parquet(stage_file_path)

# Using copy_into_location method
df.write.copy_into_location(stage, file_format_type="parquet")
```

- Fixing `mode` parameter:
  - Use the [mode](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.mode) function from [Snowpark DataFrameWriter](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter), as follows:

Below is an example that adds into the daisy chain the `mode` method with `overwrite` as a parameter.

```python
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]
df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

# Using parquet method
df.write.mode("overwrite").parquet(temp_stage)

# Using copy_into_location method
df.write.mode("overwrite").copy_into_location(stage, file_format_type="parquet")
```

- Fixing `partitionBy` parameter:
  - Use the [partition_by](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.csv) parameter from the `CSV` method, as follows:

Below is an example that used the `partition_by` parameter from the `parquet` method.

```python
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]
df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

# Using parquet method
df.write.parquet(temp_stage, partition_by="City")

# Using copy_into_location method
df.write.copy_into_location(stage, file_format_type="parquet", partition_by="City")
```

- Fixing `options` parameter:
  - Use the [format_type_options](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameWriter.csv) parameter from the `CSV` method, as follows:

The options between spark and snowpark are not the same, in this case `pathGlobFilter` is replaced with `PATTERN`,
the **Additional recommendations** section has table with all the Equivalences.

Below is an example that creates a dictionary with `PATTERN`, and calls the `options` method with that dictionary.

```python
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]
df = spark.createDataFrame(data, schema=["Name", "Age", "City"])
optionsParam = {"PATTERN": "*.parquet"}

# Using parquet method
df.write.parquet(stage, format_type_options=optionsParam)

# Using copy_into_location method
df.write.copy_into_location(stage, file_format_type="parquet", format_type_options=optionsParam)
```


## Additional recommendations

- Take into account the options between spark and snowpark are not the same, but they can be mapped:

| Spark Options        | Possible value                             | Snowpark equivalent               | Description                                                                                                                                                                                                             |
|----------------------|--------------------------------------------|-----------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| header               | True or False                              | SKIP_HEADER = 1 / SKIP_HEADER = 0 | To use the first line of a file as names of columns.                                                                                                                                                                    |
| delimiter            | Any single/multi character field separator | FIELD_DELIMITER                   | To specify single / multiple character(s) as a separator for each column/field.                                                                                                                                         |
| sep                  | Any single character field separator       | FIELD_DELIMITER                   | To specify a single character as a separator for each column/field.                                                                                                                                                     |
| encoding             | UTF-8, UTF-16, etc...                      | ENCODING                          | To decode the CSV files by the given encoding type. Default encoding is UTF-8                                                                                                                                           |
| lineSep              | Any single character line separator        | RECORD_DELIMITER                  | To define the line separator that should be used for file parsing.                                                                                                                                                      |
| pathGlobFilter       | File pattern                               | PATTERN                           | To define a pattern to read files only with filenames matching the pattern.                                                                                                                                             |
| recursiveFileLookup  | True or False                              | N/A                               | To recursively scan a directory to read files. Default value of this option is False.                                                                                                                                   |
| quote                | Single character to be quoted              | FIELD_OPTIONALLY_ENCLOSED_BY      | To quote fields/columns containing fields where the delimiter / separator can be part of the value. This character To quote all fields when used with quoteAll option. Default value of this option is double quote("). |
| nullValue            | String to replace null                     | NULL_IF                           | To replace null values with the string while reading and writing dataframe.                                                                                                                                             |
| dateFormat           | Valid date format                          | DATE_FORMAT                       | To define a string that indicates a date format. Default format is yyyy-MM-dd.                                                                                                                                          |
| timestampFormat      | Valid timestamp format                     | TIMESTAMP_FORMAT                  | To define a string that indicates a timestamp format. Default format is yyyy-MM-dd 'T'HH:mm:ss.                                                                                                                         |
| escape               | Any single character                       | ESCAPE                            | To set a single character as escaping character to override default escape character(\\).                                                                                                                               |
| inferSchema          | True or False                              | INFER_SCHEMA                      | Automatically detects the file schema                                                                                                                                                                                   |
| mergeSchema          | True or False                              | N/A                               | Not needed in snowflake as this happens whenever the infer_schema determines the parquet file structure                                                                                                                 |

- For **modifiedBefore / modifiedAfter** option you can achieve the same result in Snowflake by using the metadata columns and then add a filter like: `df.filter(METADATA_FILE_LAST_MODIFIED > ‘some_date’)`.
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
