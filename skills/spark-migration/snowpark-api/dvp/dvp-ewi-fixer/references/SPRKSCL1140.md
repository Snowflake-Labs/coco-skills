# SPRKSCL1140

org.apache.spark.sql.functions.stddev

Message: org.apache.spark.sql.functions.stddev has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.stddev](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#stddev(columnName:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.stddev` function, first used with a column name as an argument and then with a column object.

```scala
val df = Seq(
  ("Alice", 10),
  ("Bob", 15),
  ("Charlie", 20),
  ("David", 25),
).toDF("name", "score")

val result1 = df.select(stddev("score"))
val result2 = df.select(stddev(col("score")))
```

**Output**

The SMA adds the EWI `SPRKSCL1140` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(
  ("Alice", 10),
  ("Bob", 15),
  ("Charlie", 20),
  ("David", 25),
).toDF("name", "score")

/*EWI: SPRKSCL1140 => org.apache.spark.sql.functions.stddev has a workaround, see documentation for more info*/
val result1 = df.select(stddev("score"))
/*EWI: SPRKSCL1140 => org.apache.spark.sql.functions.stddev has a workaround, see documentation for more info*/
val result2 = df.select(stddev(col("score")))
```

**Recommended fix**

Snowpark has an equivalent [stddev](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#stddev(e:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives a column object as an argument. For that reason, the Spark overload that receives a column object as an argument is directly supported by Snowpark and does not require any changes.

For the overload that receives a string argument, you can convert the string into a column object using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq(
  ("Alice", 10),
  ("Bob", 15),
  ("Charlie", 20),
  ("David", 25),
).toDF("name", "score")

val result1 = df.select(stddev(col("score")))
val result2 = df.select(stddev(col("score")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
