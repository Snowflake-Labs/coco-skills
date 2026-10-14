# SPRKSCL1145

org.apache.spark.sql.functions.sumDistinct

Message: org.apache.spark.sql.functions.sumDistinct has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.sumDistinct](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#sumDistinct(columnName:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.sumDistinct` function, first used with a column name as an argument and then with a column object.

```scala
val df = Seq(
  ("Alice", 10),
  ("Bob", 15),
  ("Alice", 10),
  ("Alice", 20),
  ("Bob", 15)
).toDF("name", "value")

val result1 = df.groupBy("name").agg(sumDistinct("value"))
val result2 = df.groupBy("name").agg(sumDistinct(col("value")))
```

**Output**

The SMA adds the EWI `SPRKSCL1145` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(
  ("Alice", 10),
  ("Bob", 15),
  ("Alice", 10),
  ("Alice", 20),
  ("Bob", 15)
).toDF("name", "value")

/*EWI: SPRKSCL1145 => org.apache.spark.sql.functions.sumDistinct has a workaround, see documentation for more info*/
val result1 = df.groupBy("name").agg(sumDistinct("value"))
/*EWI: SPRKSCL1145 => org.apache.spark.sql.functions.sumDistinct has a workaround, see documentation for more info*/
val result2 = df.groupBy("name").agg(sumDistinct(col("value")))
```

**Recommended fix**

As a workaround, you can use the [sum_distinct](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#sum_distinct(e:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function. For the Spark overload that receives a string argument, you additionally have to convert the string into a column object using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function.

```scala
val df = Seq(
  ("Alice", 10),
  ("Bob", 15),
  ("Alice", 10),
  ("Alice", 20),
  ("Bob", 15)
).toDF("name", "value")

val result1 = df.groupBy("name").agg(sum_distinct(col("value")))
val result2 = df.groupBy("name").agg(sum_distinct(col("value")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
