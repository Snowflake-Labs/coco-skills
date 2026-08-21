# SPRKSCL1109

org.apache.spark.sql.DataFrameReader.option

> This issue code has been **deprecated**

Message: The parameter is not defined for org.apache.spark.sql.DataFrameReader.option

Category: Warning

## Description

This issue appears when the SMA detects that giving parameter of [org.apache.spark.sql.DataFrameReader.option](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/DataFrameReader.html#option(key:String,value:Double):org.apache.spark.sql.DataFrameReader) is not defined.

## Scenario

**Input**

Below is an example of undefined parameter for `org.apache.spark.sql.DataFrameReader.option` function.

```scala
spark.read.option("header", True).json(path)
```

**Output**

The SMA adds the EWI `SPRKSCL1109` to the output code to let you know that giving parameter to the org.apache.spark.sql.DataFrameReader.option function is not defined.

```scala
/*EWI: SPRKSCL1109 => The parameter header=True is not supported for org.apache.spark.sql.DataFrameReader.option*/
spark.read.option("header", True).json(path)
```

**Recommended fix**

Check the Snowpark documentation for reader format option [here](https://docs.snowflake.com/en/sql-reference/sql/create-file-format#format-type-options-formattypeoptions), in order to identify the defined options.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
