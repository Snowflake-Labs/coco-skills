# SPRKPY1029

pyspark.sql.readwriter.DataFrameReader.parquet

Message: This issue appears when the tool detects the usage of pyspark.sql.readwriter.DataFrameReader.parquet. This function is supported, but some of the differences between Snowpark and the Spark API might require making some manual changes.

Category: Warning

## Description

This issue appears when the SMA detects a use of the [pyspark.sql.readwriter.DataFrameReader.parquet](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameReader.parquet.html) function. This function is supported by Snowpark, however, there are some differences that would require some manual changes.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.readwriter.DataFrameReader.parquet` function that generates this EWI.

```python
file_paths = [
  "path/to/your/file1.parquet",
  "path/to/your/file2.parquet",
  "path/to/your/file3.parquet",
]

df = session.read.parquet(
  *file_paths,
  mergeSchema="true",
  pathGlobFilter="*file*",
  recursiveFileLookup="true",
  modifiedBefore="2024-12-31T00:00:00",
  modifiedAfter="2023-12-31T00:00:00"
)
```

**Output**

The SMA adds the EWI `SPRKPY1029` to the output code to let you know that this function is supported by Snowpark, but it requires some manual adjustments. Please note that the options supported by Snowpark are transformed into `option` function calls and those that are not supported are removed. This is explained in more detail in the next sections.

```python
file_paths = [
  "path/to/your/file1.parquet",
  "path/to/your/file2.parquet",
  "path/to/your/file3.parquet"
]

#EWI: SPRKPY1076 => Some of the included parameters are not supported in the parquet function, the supported ones will be added into a option method.
#EWI: SPRKPY1029 => This issue appears when the tool detects the usage of pyspark.sql.readwriter.DataFrameReader.parquet. This function is supported, but some of the differences between Snowpark and the Spark API might require making some manual changes.
df = session.read.option("PATTERN", "*file*").parquet(
  *file_paths
)
```

**Recommended fix**

In this section, we explain how to configure the `paths` and `options` parameters to make them work in Snowpark.

**1. paths parameter**

In Spark, this parameter can be a local or cloud location. Snowpark only accepts cloud locations using a [snowflake stage](https://docs.snowflake.com/en/user-guide/data-load-local-file-system-create-stage). So, you can create a temporal stage and add each file into it using the prefix `file://`.

**2. options parameter**

Snowpark does not allow defining the different **options** as parameters of the `parquet` function. As a workaround, you can use the [option](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameReader.option) or [options](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameReader.options) functions to specify those parameters as extra options of the DataFrameReader.

Please note that the Snowpark **options** are not exactly the same as the PySpark **options** so some manual changes might be needed. Below is a more detailed explanation of how to configure the most common PySpark options in Snowpark.

**2.1 mergeSchema option**

Parquet supports schema evolution, allowing users to start with a simple schema and gradually add more columns as needed. This can result in multiple parquet files with different but compatible schemas.
In Snowflake, thanks to the [infer_schema](https://docs.snowflake.com/en/sql-reference/functions/infer_schema) capabilities you don't need to do that and therefore the `mergeSchema` option can just be removed.

**2.2 pathGlobFilter option**

If you want to load only a subset of files from the stage, you can use the `pattern` option to specify a
regular expression that matches the files you want to load. The SMA already automates this as you can see in the output of this scenario.

**2.3 recursiveFileLookupstr option**

This option is not supported by Snowpark. The best recommendation is to use a regular expression like with the `pathGlobFilter` option to achieve something similar.

**2.4 modifiedBefore / modifiedAfter option**

You can achieve the same result in Snowflake by using the `metadata` columns.

> The following options are not supported by Snowpark:
> - compression
> - datetimeRebaseMode
> - int96RebaseMode
> - mergeSchema

Below is the full example of how the input code should be transformed in order to make it work in Snowpark:

```python
from snowflake.snowpark.column import METADATA_FILE_LAST_MODIFIED, METADATA_FILENAME

temp_stage = f'{session.get_fully_qualified_current_schema()}.{_generate_prefix("TEMP_STAGE")}'
session.sql(f'CREATE TEMPORARY STAGE IF NOT EXISTS {temp_stage}')

session.file.put(f"file:///path/to/your/file1.parquet", f"@{temp_stage}")
session.file.put(f"file:///path/to/your/file2.parquet", f"@{temp_stage}")
session.file.put(f"file:///path/to/your/file3.parquet", f"@{temp_stage}")

df = session.read \
  .option("PATTERN", ".*file.*") \
  .with_metadata(METADATA_FILENAME, METADATA_FILE_LAST_MODIFIED) \
  .parquet(temp_stage) \
  .where(METADATA_FILE_LAST_MODIFIED < '2024-12-31T00:00:00') \
  .where(METADATA_FILE_LAST_MODIFIED > '2023-12-31T00:00:00')
```

## Additional recommendations

- In Snowflake, you can leverage other approaches for parquet data ingestion, such as:
  - Leveraging [native parquet ingestion capabilities](https://docs.snowflake.com/en/user-guide/tutorials/script-data-load-transform-parquet). Consider also [autoingest with snowpipe.](https://docs.snowflake.com/en/user-guide/data-load-snowpipe-auto-s3)
  - Parquet [external tables](https://docs.snowflake.com/en/user-guide/tables-external-intro) which can be pointed directly to cloud file locations.
  - Using [Iceberg tables](https://docs.snowflake.com/LIMITEDACCESS/iceberg/tables-iceberg-parquet-source).
- When doing a migration is a good practice to leverage the SMA reports to try to build an inventory of files and determine after modernization to which stages/tables will the data be mapped.
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
