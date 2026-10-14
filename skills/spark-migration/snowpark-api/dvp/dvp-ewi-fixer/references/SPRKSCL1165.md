# SPRKSCL1165

The format of the reader on DataFrameReader method chaining is not one of the defined by Snowpark.

Message: Reader format on DataFrameReader method chaining can't be defined

Category: Warning

## Description

This issue appears when the SMA detects that `format` of the reader in DataFrameReader method chaining is not one of the following supported for Snowpark: `avro`, `csv`, `json`, `orc`, `parquet` and `xml`. Therefore, the SMA can not determine if setting options are defined or not.

## Scenario

**Input**

Below is an example of DataFrameReader method chaining where SMA can determine the format of reader.

```scala
spark.read.format("net.snowflake.spark.snowflake")
                 .option("query", s"select * from $tableName")
                 .load()
```

**Output**

The SMA adds the EWI `SPRKSCL1165` to the output code to let you know that `format` of the reader can not be determine in the giving DataFrameReader method chaining.

```scala
/*EWI: SPRKSCL1165 => Reader format on DataFrameReader method chaining can't be defined*/
spark.read.option("query", s"select * from $tableName")
                 .load()
```

**Recommended fix**

Check the Snowpark documentation [here](https://docs.snowflake.com/en/sql-reference/sql/create-file-format#format-type-options-formattypeoptions) to get more information about format of the reader.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
