# SPRKSCL1136

org.apache.spark.sql.functions.min

> This issue code is **deprecated** since [Spark Conversion Core 4.3.2](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.3.2)

Message: org.apache.spark.sql.functions.min has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.min](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#min(columnName:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.min` function, first used with a column name as an argument and then with a column object.

```scala
val df = Seq(1, 3, 10, 1, 3).toDF("value")
val result1 = df.select(min("value"))
val result2 = df.select(min(col("value")))
```

**Output**

The SMA adds the EWI `SPRKSCL1136` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(1, 3, 10, 1, 3).toDF("value")
/*EWI: SPRKSCL1136 => org.apache.spark.sql.functions.min has a workaround, see documentation for more info*/
val result1 = df.select(min("value"))
/*EWI: SPRKSCL1136 => org.apache.spark.sql.functions.min has a workaround, see documentation for more info*/
val result2 = df.select(min(col("value")))
```

**Recommended fix**

Snowpark has an equivalent [min](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#min(e:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives a column object as an argument. For that reason, the Spark overload that receives a column object as an argument is directly supported by Snowpark and does not require any changes.

For the overload that takes a string argument, you can convert the string into a column object using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq(1, 3, 10, 1, 3).toDF("value")
val result1 = df.select(min(col("value")))
val result2 = df.select(min(col("value")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
