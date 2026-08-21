# SPRKPY1026

pyspark.sql.readwriter.DataFrameReader.csv

> This issue code has been **deprecated** since [Spark Conversion Core 4.3.2](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.3.2)

Message: pyspark.sql.readwriter.DataFrameReader.csv has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.readwriter.DataFrameReader.csv](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameReader.csv.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.readwriter.DataFrameReader.csv` function that generates this EWI. In this example, the `csv` function is used to read multiple `.csv` files with a given schema and uses some extra options such as **encoding**, **header** and **sep** to fine-tune the behavior of reading the files.

```python
file_paths = [
  "path/to/your/file1.csv",
  "path/to/your/file2.csv",
  "path/to/your/file3.csv",
]

df = session.read.csv(
  file_paths,
  schema=my_schema,
  encoding="UTF-8",
  header=True,
  sep=","
)
```

**Output**

The SMA adds the EWI `SPRKPY1026` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
file_paths = [
  "path/to/your/file1.csv",
  "path/to/your/file2.csv",
  "path/to/your/file3.csv",
]

#EWI: SPRKPY1026 => pyspark.sql.readwriter.DataFrameReader.csv has a workaround, see documentation for more info
df = session.read.csv(
  file_paths,
  schema=my_schema,
  encoding="UTF-8",
  header=True,
  sep=","
)
```

**Recommended fix**

In this section, we explain how to configure the `path` parameter, the `schema` parameter and some `options` to make them work in Snowpark.

**1. path parameter**

Snowpark requires the **path** parameter to be a stage location so, as a workaround, you can create a temporary stage and add each `.csv` file to that stage using the prefix `file://`.

**2. schema parameter**

Snowpark does not allow defining the **schema** as a parameter of the `csv` function. As a workaround, you can use the [snowflake.snowpark.DataFrameReader.schema](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameReader.schema) function.

**3. options parameters**

Snowpark does not allow defining the **extra options** as parameters of the `csv` function. As a workaround, for many of them you can use the [snowflake.snowpark.DataFrameReader.option](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameReader.option) function to specify those parameters as options of the DataFrameReader.

> The following options are **not supported** by Snowpark:
> - columnNameOfCorruptRecord
> - emptyValue
> - enforceSchema
> - header
> - ignoreLeadingWhiteSpace
> - ignoreTrailingWhiteSpace
> - inferSchema
> - locale
> - maxCharsPerColumn
> - maxColumns
> - mode
> - multiLine
> - nanValue
> - negativeInf
> - nullValue
> - positiveInf
> - quoteAll
> - samplingRatio
> - timestampNTZFormat
> - unescapedQuoteHandling

Below is the full example of how the input code should look like after applying the suggestions mentioned above to make it work in Snowpark:

```python
stage = f'{session.get_fully_qualified_current_schema()}.{_generate_prefix("TEMP_STAGE")}'
session.sql(f'CREATE TEMPORARY STAGE IF NOT EXISTS {stage}')

session.file.put(f"file:///path/to/your/file1.csv", f"@{stage}")
session.file.put(f"file:///path/to/your/file2.csv", f"@{stage}")
session.file.put(f"file:///path/to/your/file3.csv", f"@{stage}")

df = session.read.schema(my_schema).option("encoding", "UTF-8").option("sep", ",").csv(stage)
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
