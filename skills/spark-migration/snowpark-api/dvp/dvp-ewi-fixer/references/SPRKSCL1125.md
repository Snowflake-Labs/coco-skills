# SPRKSCL1125

org.apache.spark.sql.functions.count

> This issue code is **deprecated** since [Spark Conversion Core 2.9.0](https://docs.snowconvert.com/sma/general/release-notes/old-version-release-notes/sc-spark-scala-release-notes#id-2.09.0)

Message: org.apache.spark.sql.functions.count has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.count](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#count(columnName:String):org.apache.spark.sql.TypedColumn[Any,Long]) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.count` function, first used with a column name as an argument and then with a column object.

```scala
val df = Seq(
  ("Alice", "Math"),
  ("Bob", "Science"),
  ("Alice", "Science"),
  ("Bob", null)
).toDF("name", "subject")

val result1 = df.groupBy("name").agg(count("subject").as("subject_count"))
val result2 = df.groupBy("name").agg(count(col("subject")).as("subject_count"))
```

**Output**

The SMA adds the EWI `SPRKSCL1125` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(
  ("Alice", "Math"),
  ("Bob", "Science"),
  ("Alice", "Science"),
  ("Bob", null)
).toDF("name", "subject")

/*EWI: SPRKSCL1125 => org.apache.spark.sql.functions.count has a workaround, see documentation for more info*/
val result1 = df.groupBy("name").agg(count("subject").as("subject_count"))
/*EWI: SPRKSCL1125 => org.apache.spark.sql.functions.count has a workaround, see documentation for more info*/
val result2 = df.groupBy("name").agg(count(col("subject")).as("subject_count"))
```

**Recommended fix**

Snowpark has an equivalent [count](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#count(e:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives a column object as an argument. For that reason, the Spark overload that receives a column object as an argument is directly supported by Snowpark and does not require any changes.

For the overload that receives a string argument, you can convert the string into a column object using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq(
  ("Alice", "Math"),
  ("Bob", "Science"),
  ("Alice", "Science"),
  ("Bob", null)
).toDF("name", "subject")

val result1 = df.groupBy("name").agg(count(col("subject")).as("subject_count"))
val result2 = df.groupBy("name").agg(count(col("subject")).as("subject_count"))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
