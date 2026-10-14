# SPRKPY1028

pyspark.sql.readwriter.DataFrameReader.orc

Message: pyspark.sql.readwriter.DataFrameReader.orc has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.readwriter.DataFrameReader.orc](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameReader.orc.html) function, which has a workaround.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.readwriter.DataFrameReader.orc` function that generates this EWI. In this example, the `orc` function is used to read multiple `.orc` files and uses some extra options such as **mergeSchema** and **recursiveFileLookup** to fine-tune the behavior of reading the files.

```python
file_paths = [
  "path/to/your/file1.orc",
  "path/to/your/file2.orc",
  "path/to/your/file3.orc",
]

df = session.read.orc(
  file_paths,
  mergeSchema="True",
  recursiveFileLookup="True"
)
```

**Output**

The SMA adds the EWI `SPRKPY1028` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```python
file_paths = [
  "path/to/your/file1.orc",
  "path/to/your/file2.orc",
  "path/to/your/file3.orc",
]

#EWI: SPRKPY1028 => pyspark.sql.readwriter.DataFrameReader.orc has a workaround, see documentation for more info
df = session.read.orc(
  file_paths,
  mergeSchema="True",
  recursiveFileLookup="True"
)
```

**Recommended fix**

In this section, we explain how to configure the `path` parameter and the extra `options` to make them work in Snowpark.

**1. path parameter**

Snowpark requires the **path** parameter to be a stage location so, as a workaround, you can create a temporary stage and add each `.orc` file to that stage using the prefix `file://`.

**2. options parameters**

Snowpark does not allow defining the **extra options** as parameters of the `orc` function. As a workaround, for many of them you can use the [snowflake.snowpark.DataFrameReader.option](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameReader.option) function to specify those parameters as options of the DataFrameReader.

> The following options are not supported by Snowpark:
> - compression
> - mergeSchema

Below is the full example of how the input code should look like after applying the suggestions mentioned above to make it work in Snowpark:

```python
stage = f'{session.get_fully_qualified_current_schema()}.{_generate_prefix("TEMP_STAGE")}'
session.sql(f'CREATE TEMPORARY STAGE IF NOT EXISTS {stage}')

session.file.put(f"file:///path/to/your/file1.orc", f"@{stage}")
session.file.put(f"file:///path/to/your/file2.orc", f"@{stage}")
session.file.put(f"file:///path/to/your/file3.orc", f"@{stage}")

df = session.read.option(recursiveFileLookup, "True").orc(stage)
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
