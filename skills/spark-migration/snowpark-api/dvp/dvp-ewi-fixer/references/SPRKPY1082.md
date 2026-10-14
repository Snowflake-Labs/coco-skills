# SPRKPY1082

pyspark.sql.readwriter.DataFrameReader.load

Message: The pyspark.sql.readwriter.DataFrameReader.load function is not supported. A workaround is to use Snowpark DataFrameReader format specific method instead (avro csv, json, orc, parquet). The path parameter should be a stage location.

Category: Warning

## Description

The [pyspark.sql.readwriter.DataFrameReader.load](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameReader.load.html) function is not supported. The workaround is to use Snowpark DataFrameReader methods instead.

## Scenarios

The spark signature for this method `DataFrameReader.load(path, format, schema, **options)` does not exist in Snowpark.
Therefore, any usage of the load function is going to have an EWI in the output code.

### Scenario 1

**Input**

Below is an example that tries to load data from a `CSV` source.

```python
path_csv_file = "/path/to/file.csv"

schemaParam = StructType([
        StructField("Name", StringType(), True),
        StructField("Superhero", StringType(), True)
    ])

my_session.read.load(path_csv_file, "csv").show()
my_session.read.load(path_csv_file, "csv", schema=schemaParam).show()
my_session.read.load(path_csv_file, "csv", schema=schemaParam, lineSep="\r\n", dateFormat="YYYY/MM/DD").show()
```

**Output**

The SMA adds the EWI `SPRKPY1082` to let you know that this function is not supported by Snowpark, but it has a workaround.

```python
path_csv_file = "/path/to/file.csv"

schemaParam = StructType([
        StructField("Name", StringType(), True),
        StructField("Superhero", StringType(), True)
    ])
#EWI: SPRKPY1082 => The pyspark.sql.readwriter.DataFrameReader.load function is not supported. A workaround is to use Snowpark DataFrameReader format specific method instead (avro csv, json, orc, parquet). The path parameter should be a stage location.

my_session.read.load(path_csv_file, "csv").show()
#EWI: SPRKPY1082 => The pyspark.sql.readwriter.DataFrameReader.load function is not supported. A workaround is to use Snowpark DataFrameReader format specific method instead (avro csv, json, orc, parquet). The path parameter should be a stage location.
my_session.read.load(path_csv_file, "csv", schema=schemaParam).show()
#EWI: SPRKPY1082 => The pyspark.sql.readwriter.DataFrameReader.load function is not supported. A workaround is to use Snowpark DataFrameReader format specific method instead (avro csv, json, orc, parquet). The path parameter should be a stage location.
my_session.read.load(path_csv_file, "csv", schema=schemaParam, lineSep="\r\n", dateFormat="YYYY/MM/DD").show()
```

**Recommended fix**

As a workaround, you can use [Snowpark DataFrameReader](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameReader) methods instead.

- Fixing `path` and `format` parameters:

  - Replace the `load` method with `csv` method.
  - The first parameter `path` must be in a stage to make an equivalence with [Snowpark](https://docs.snowflake.com/en/user-guide/data-load-local-file-system-create-stage).

Below is an example that creates a temporal stage and puts the file into it, then calls the `CSV` method.

```python
path_csv_file = "/path/to/file.csv"

# Stage creation

temp_stage = f'{Session.get_fully_qualified_current_schema()}.{_generate_prefix("TEMP_STAGE")}'
my_session.sql(f'CREATE TEMPORARY STAGE IF NOT EXISTS {temp_stage}').show()
my_session.file.put(f"file:///path/to/file.csv", f"@{temp_stage}")
stage_file_path = f"{temp_stage}file.csv"

schemaParam = StructType([
        StructField("Name", StringType(), True),
        StructField("Superhero", StringType(), True)
    ])

my_session.read.csv(stage_file_path).show()
```

- Fixing `schema` parameter:
  - The schema can be set by using the [schema](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameReader.schema) function as follows:

```python
schemaParam = StructType([
        StructField("name", StringType(), True),
        StructField("city", StringType(), True)
    ])

df = my_session.read.schema(schemaParam).csv(temp_stage)
```

- Fixing `options` parameter:

The [options](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameReader.options) between spark and snowpark are not the same, in this case `lineSep` and `dateFormat` are replaced with `RECORD_DELIMITER` and `DATE_FORMAT`,
the **Additional recommendations** section has a table with all the Equivalences.

Below is an example that creates a dictionary with `RECORD_DELIMITER` and `DATE_FORMAT`, and calls the `options` method with that dictionary.

```python
optionsParam = {"RECORD_DELIMITER": "\r\n", "DATE_FORMAT": "YYYY/MM/DD"}
df = my_session.read.options(optionsParam).csv(stage)
```

### Scenario 2

**Input**

Below is an example that tries to load data from a `JSON` source.

```python
path_json_file = "/path/to/file.json"

schemaParam = StructType([
        StructField("Name", StringType(), True),
        StructField("Superhero", StringType(), True)
    ])

my_session.read.load(path_json_file, "json").show()
my_session.read.load(path_json_file, "json", schema=schemaParam).show()
my_session.read.load(path_json_file, "json", schema=schemaParam, dateFormat="YYYY/MM/DD", timestampFormat="YYYY-MM-DD HH24:MI:SS.FF3").show()
```

**Output**

The SMA adds the EWI `SPRKPY1082` to let you know that this function is not supported by Snowpark, but it has a workaround.

```python
path_json_file = "/path/to/file.json"

schemaParam = StructType([
        StructField("Name", StringType(), True),
        StructField("Superhero", StringType(), True)
    ])
#EWI: SPRKPY1082 => The pyspark.sql.readwriter.DataFrameReader.load function is not supported. A workaround is to use Snowpark DataFrameReader format specific method instead (avro csv, json, orc, parquet). The path parameter should be a stage location.

my_session.read.load(path_json_file, "json").show()
#EWI: SPRKPY1082 => The pyspark.sql.readwriter.DataFrameReader.load function is not supported. A workaround is to use Snowpark DataFrameReader format specific method instead (avro csv, json, orc, parquet). The path parameter should be a stage location.
my_session.read.load(path_json_file, "json", schema=schemaParam).show()
#EWI: SPRKPY1082 => The pyspark.sql.readwriter.DataFrameReader.load function is not supported. A workaround is to use Snowpark DataFrameReader format specific method instead (avro csv, json, orc, parquet). The path parameter should be a stage location.
my_session.read.load(path_json_file, "json", schema=schemaParam, dateFormat="YYYY/MM/DD", timestampFormat="YYYY-MM-DD HH24:MI:SS.FF3").show()
```

**Recommended fix**

As a workaround, you can use [Snowpark DataFrameReader](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameReader) methods instead.

- Fixing `path` and `format` parameters:

  - Replace the `load` method with `json` method
  - The first parameter `path` must be in a stage to make an equivalence with [Snowpark](https://docs.snowflake.com/en/user-guide/data-load-local-file-system-create-stage).

Below is an example that creates a temporal stage and puts the file into it, then calls the `JSON` method.

```python
path_json_file = "/path/to/file.json"

# Stage creation

temp_stage = f'{Session.get_fully_qualified_current_schema()}.{_generate_prefix("TEMP_STAGE")}'
my_session.sql(f'CREATE TEMPORARY STAGE IF NOT EXISTS {temp_stage}').show()
my_session.file.put(f"file:///path/to/file.json", f"@{temp_stage}")
stage_file_path = f"{temp_stage}file.json"

schemaParam = StructType([
        StructField("Name", StringType(), True),
        StructField("Superhero", StringType(), True)
    ])

my_session.read.json(stage_file_path).show()
```

- Fixing `schema` parameter:
  - The schema can be set by using the [schema](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameReader.schema) function as follows:

```python
schemaParam = StructType([
        StructField("name", StringType(), True),
        StructField("city", StringType(), True)
    ])

df = my_session.read.schema(schemaParam).json(temp_stage)
```

- Fixing `options` parameter:

The [options](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameReader.options) between Spark and snowpark are not the same, in this case `dateFormat` and `timestampFormat` are replaced with `DATE_FORMAT` and `TIMESTAMP_FORMAT`,
the **Additional recommendations** section has a table with all the Equivalences.

Below is an example that creates a dictionary with `DATE_FORMAT` and `TIMESTAMP_FORMAT`, and calls the `options` method with that dictionary.
```python
optionsParam = {"DATE_FORMAT": "YYYY/MM/DD", "TIMESTAMP_FORMAT": "YYYY-MM-DD HH24:MI:SS.FF3"}
df = Session.read.options(optionsParam).json(stage)
```

### Scenario 3

**Input**

Below is an example that tries to load data from a `PARQUET` source.

```python
path_parquet_file = "/path/to/file.parquet"

schemaParam = StructType([
        StructField("Name", StringType(), True),
        StructField("Superhero", StringType(), True)
    ])

my_session.read.load(path_parquet_file, "parquet").show()
my_session.read.load(path_parquet_file, "parquet", schema=schemaParam).show()
my_session.read.load(path_parquet_file, "parquet", schema=schemaParam, pathGlobFilter="*.parquet").show()
```

**Output**

The SMA adds the EWI `SPRKPY1082` to let you know that this function is not supported by Snowpark, but it has a workaround.

```python
path_parquet_file = "/path/to/file.parquet"

schemaParam = StructType([
        StructField("Name", StringType(), True),
        StructField("Superhero", StringType(), True)
    ])
#EWI: SPRKPY1082 => The pyspark.sql.readwriter.DataFrameReader.load function is not supported. A workaround is to use Snowpark DataFrameReader format specific method instead (avro csv, json, orc, parquet). The path parameter should be a stage location.

my_session.read.load(path_parquet_file, "parquet").show()
#EWI: SPRKPY1082 => The pyspark.sql.readwriter.DataFrameReader.load function is not supported. A workaround is to use Snowpark DataFrameReader format specific method instead (avro csv, json, orc, parquet). The path parameter should be a stage location.
my_session.read.load(path_parquet_file, "parquet", schema=schemaParam).show()
#EWI: SPRKPY1082 => The pyspark.sql.readwriter.DataFrameReader.load function is not supported. A workaround is to use Snowpark DataFrameReader format specific method instead (avro csv, json, orc, parquet). The path parameter should be a stage location.
my_session.read.load(path_parquet_file, "parquet", schema=schemaParam, pathGlobFilter="*.parquet").show()
```

**Recommended fix**

As a workaround, you can use [Snowpark DataFrameReader](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameReader) methods instead.

- Fixing `path` and `format` parameters:

  - Replace the `load` method with `parquet` method
  - The first parameter `path` must be in a stage to make an equivalence with [Snowpark](https://docs.snowflake.com/en/user-guide/data-load-local-file-system-create-stage).

Below is an example that creates a temporal stage and puts the file into it, then calls the `PARQUET` method.

```python
path_parquet_file = "/path/to/file.parquet"

# Stage creation

temp_stage = f'{Session.get_fully_qualified_current_schema()}.{_generate_prefix("TEMP_STAGE")}'
my_session.sql(f'CREATE TEMPORARY STAGE IF NOT EXISTS {temp_stage}').show()
my_session.file.put(f"file:///path/to/file.parquet", f"@{temp_stage}")
stage_file_path = f"{temp_stage}file.parquet"

schemaParam = StructType([
        StructField("Name", StringType(), True),
        StructField("Superhero", StringType(), True)
    ])

my_session.read.parquet(stage_file_path).show()
```

- Fixing `schema` parameter:
  - The schema can be set by using the [schema](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameReader.schema) function as follows:

```python
schemaParam = StructType([
        StructField("name", StringType(), True),
        StructField("city", StringType(), True)
    ])

df = my_session.read.schema(schemaParam).parquet(temp_stage)
```

- Fixing `options` parameter:

The [options](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrameReader.options) between Spark and snowpark are not the same, in this case `pathGlobFilter` is replaced with `PATTERN`,
the **Additional recommendations** section has a table with all the Equivalences.

Below is an example that creates a dictionary with `PATTERN`, and calls the `options` method with that dictionary.

```python
optionsParam = {"PATTERN": "*.parquet"}
df = Session.read.options(optionsParam).parquet(stage)
```

## Additional recommendations

- Take into account that the options between spark and snowpark are not the same, but they can be mapped:

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

- For **modifiedBefore / modifiedAfter** option you can achieve the same result in Snowflake by using the metadata columns and then adding a filter like: `df.filter(METADATA_FILE_LAST_MODIFIED > ‘some_date’)`.
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
