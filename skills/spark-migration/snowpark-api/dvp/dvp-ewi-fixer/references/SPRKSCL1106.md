# SPRKSCL1106

org.apache.spark.sql.DataFrameWriter.option

> This issue code has been **deprecated**

Message: Writer option is not supported.

Category: Conversion error.

## Description

This issue appears when the tool detects, in writer statement, the usage of an option not supported by Snowpark.

## Scenario

**Input**

Below is an example of the [org.apache.spark.sql.DataFrameWriter.option](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/DataFrameWriter.html#:~:text=def-,option,-(key%3A%20String%2C%20value%3A%20String) used to add options to a writer statement.

```scala
df.write.format("net.snowflake.spark.snowflake").option("dbtable", tablename)
```
**Output**

The SMA adds the EWI `SPRKSCL1106` to the output code to let you know that the option method is not supported by Snowpark.

```scala
df.write.saveAsTable(tablename)
/*EWI: SPRKSCL1106 => Writer option is not supported .option("dbtable", tablename)*/
```

**Recommended fix**

There is no recommended fix for this scenario

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
