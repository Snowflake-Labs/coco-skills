# SPRKSCL1107

org.apache.spark.sql.DataFrameWriter.save

> This issue code has been **deprecated**

Message: Writer save is not supported.

Category: Conversion error.

## Description

This issue appears when the tool detects, in writer statement, the usage of a writer save method that is not supported by Snowpark.

## Scenario

**Input**

Below is an example of the [org.apache.spark.sql.DataFrameWriter.save](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/DataFrameWriter.html#:~:text=def-,save,-(path%3A)) used to save the DataFrame content.

```scala
df.write.format("net.snowflake.spark.snowflake").save()
```
**Output**

The SMA adds the EWI `SPRKSCL1107` to the output code to let you know that the save method is not supported by Snowpark.

```scala
df.write.saveAsTable(tablename)
/*EWI: SPRKSCL1107 => Writer method is not supported .save()*/
```

**Recommended fix**

There is no recommended fix for this scenario

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
