# SPRKSCL1110

The method on DataFrameReader method chaining is not supported.

> This issue code has been **deprecated**

Message: Reader method not supported ***method name***.

Category: Warning

## Description

This issue appears when the SMA detects a method that is not supported by Snowflake in the DataFrameReader method chaining.
Then, it might affects the migration of the reader statement.

## Scenario

**Input**

Below is an example of a DataFrameReader method chaining where load method is not supported by Snowflake.

```scala
spark.read.
    format("net.snowflake.spark.snowflake").
    option("query", s"select * from $tablename")
    load()
```

**Output**

The SMA adds the EWI `SPRKSCL1110` to the output code to let you know that load method is not supported by Snowpark. Then, it might affects the migration of the reader statement.

```scala
session.sql(s"select * from $tablename")
/*EWI: SPRKSCL1110 => Reader method not supported .load()*/
```

**Recommended fix**

Check the Snowpark documentation for reader [here](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/DataFrameReader.html), in order to know the supported methods by Snowflake.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
