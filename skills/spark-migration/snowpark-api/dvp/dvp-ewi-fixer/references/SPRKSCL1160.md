# SPRKSCL1160

org.apache.spark.sql.functions.sum

> This issue code is **deprecated** since [Spark Conversion Core 4.1.0](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.1.0)

Message: org.apache.spark.sql.functions.sum has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.sum](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#sum(columnName:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.sum` function that generates this EWI. In this example, the `sum` function is used to calculate the sum of selected column.

```scala
val df = Seq("1", "2", "3", "4", "5").toDF("elements")
val result1 = sum(col("elements"))
val result2 = sum("elements")
```

**Output**

The SMA adds the EWI `SPRKSCL1160` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq("1", "2", "3", "4", "5").toDF("elements")
/*EWI: SPRKSCL1160 => org.apache.spark.sql.functions.sum has a workaround, see documentation for more info*/
val result1 = sum(col("elements"))
/*EWI: SPRKSCL1160 => org.apache.spark.sql.functions.sum has a workaround, see documentation for more info*/
val result2 = sum("elements")
```

**Recommended fix**

Snowpark has an equivalent [sum](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#sum(e:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives a column object as an argument. For that reason, the Spark overload that receives a column object as an argument is directly supported by Snowpark and does not require any changes.

For the overload that receives a string argument, you can convert the string into a column object using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq("1", "2", "3", "4", "5").toDF("elements")
val result1 = sum(col("elements"))
val result2 = sum(col("elements"))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
