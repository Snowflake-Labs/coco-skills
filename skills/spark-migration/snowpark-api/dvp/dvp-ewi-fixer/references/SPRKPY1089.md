# SPRKPY1089
pyspark.sql.readwriter.DataFrameWriter.options

Message: The pyspark.sql.readwriter.DataFrameWriter.options values in Snowpark may be different, so required validation might be needed.

Category: Warning

## Description

The [pyspark.sql.readwriter.DataFrameWriter.options](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriter.options.html) values in Snowpark may be different, so validation might be needed to ensure that the behavior is correct.

## Scenarios

There are some scenarios, depending on whether the options are supported or not, or the format used to write the file.

### Scenario 1

**Input**

Below is an example of the usage of the method options, adding the options `sep` and `nullValue`, which are currently `supported`.

```python
df = spark.createDataFrame([(1, "myVal")], [2, "myVal2"], [None, "myVal3" ])

df.write.options(nullValue="myVal", sep=",").csv("some_path")
```
**Output**

The tool adds the EWI `SPRKPY1089` indicating that it is required validation.

```python
df = spark.createDataFrame([(1, "myVal")], [2, "myVal2"], [None, "myVal3" ])
#EWI: SPRKPY1089 => The pyspark.sql.readwriter.DataFrameWriter.options values in Snowpark may be different, so required validation might be needed.
df.write.options(nullValue="myVal", sep=",").csv("some_path")
```

**Recommended fix**

The Snowpark API supports these parameters, so the only action can be to check the behavior after the migration. Please refer to the **Equivalences table** to see the supported parameters.

```python
df = spark.createDataFrame([(1, "myVal")], [2, "myVal2"], [None, "myVal3" ])
#EWI: SPRKPY1089 => The pyspark.sql.readwriter.DataFrameWriter.options values in Snowpark may be different, so required validation might be needed.
df.write.options(nullValue="myVal", sep=",").csv("some_path")
```

### Scenario 2

**Input**

Here the scenario shows the usage of options, but adds a `header` option, which is `not supported`.

```python
df = spark.createDataFrame([(1, "myVal")], [2, "myVal2"], [None, "myVal3" ])

df.write.options(header=True, sep=",").csv("some_path")
```
**Output**

The tool adds the EWI `SPRKPY1089` indicating that it is required validation is needed.

```python
df = spark.createDataFrame([(1, "myVal")], [2, "myVal2"], [None, "myVal3" ])
#EWI: SPRKPY1089 => The pyspark.sql.readwriter.DataFrameWriter.options values in Snowpark may be different, so required validation might be needed.
df.write.options(header=True, sep=",").csv("some_path")
```

**Recommended fix**

For this scenario it is recommended to evaluate the Snowpark [format type options](https://docs.snowflake.com/en/sql-reference/sql/create-file-format#format-type-options-formattypeoptions) to see if it is possible to change it according to your needs. Also, check the behavior after the change.

```python
df = spark.createDataFrame([(1, "myVal")], [2, "myVal2"], [None, "myVal3" ])
#EWI: SPRKPY1089 => The pyspark.sql.readwriter.DataFrameWriter.options values in Snowpark may be different, so required validation might be needed.
df.write.csv("some_path")
```

### Scenario 3

**Input**

This scenario adds a `sep` option, which is `supported` and uses the `JSON` method.

```python
df = spark.createDataFrame([(1, "myVal")], [2, "myVal2"], [None, "myVal3" ])

df.write.options(nullValue="myVal", sep=",").json("some_path")
```
**Output**

The tool adds the EWI `SPRKPY1089` indicating that it is required validation is needed.

- Note: this scenario also applies for `PARQUET`.

```python
df = spark.createDataFrame([(1, "myVal")], [2, "myVal2"], [None, "myVal3" ])
#EWI: SPRKPY1089 => The pyspark.sql.readwriter.DataFrameWriter.options values in Snowpark may be different, so required validation might be needed.
df.write.options(nullValue="myVal", sep=",").json("some_path")
```

**Recommended fix**

The file format `JSON` does not support the parameter `sep`, so it is recommended to evaluate the snowpark [format type options](https://docs.snowflake.com/en/sql-reference/sql/create-file-format#format-type-options-formattypeoptions) to see if it is possible to change it according to your needs. Also, check the behavior after the change.

```python
df = spark.createDataFrame([(1, "myVal")], [2, "myVal2"], [None, "myVal3" ])
#EWI: SPRKPY1089 => The pyspark.sql.readwriter.DataFrameWriter.options values in Snowpark may be different, so required validation might be needed.
df.write.json("some_path")
```

## Additional recommendations

- Since there are some `not supported` parameters, it is recommended to check the `table of equivalences` and check the behavior after the transformation.


- **Equivalences table:**

Snowpark can support a list of **equivalences** for some parameters:

|PySpark Option | SnowFlake Option | Supported File Formats | Description                                                                            |
|---------------|------------------|-----|----------------------------------------------------------------------------------------|
|SEP            |FIELD_DELIMITER   | CSV | One or more single byte or multibyte characters that separate fields in an input file. |
|LINESEP        |RECORD_DELIMITER  | CSV | One or more characters that separate records in an input file.                         |
|QUOTE          |FIELD_OPTIONALLY_ENCLOSED_BY| CSV | Character used to enclose strings.                                                     |
|NULLVALUE      |NULL_IF  | CSV | String used to convert to and from SQL NULL.                                           |
|DATEFORMAT     |DATE_FORMAT  | CSV | String that defines the format of date values in the data files to be loaded.          |
|TIMESTAMPFORMAT|TIMESTAMP_FORMAT  | CSV | String that defines the format of timestamp values in the data files to be loaded.   |

If the parameter used is not in the list, the API throws an error.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
