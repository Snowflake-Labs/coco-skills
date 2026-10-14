# SPRKSCL1141

org.apache.spark.sql.functions.stddev_pop

Message: org.apache.spark.sql.functions.stddev_pop has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.stddev_pop](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#stddev_pop(columnName:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

Below is an example of the `org.apache.spark.sql.functions.stddev_pop` function, first used with a column name as an argument and then with a column object.

**Input**

```scala
val df = Seq(
  ("Alice", 23),
  ("Bob", 30),
  ("Carol", 27),
  ("David", 25),
).toDF("name", "age")

val result1 = df.select(stddev_pop("age"))
val result2 = df.select(stddev_pop(col("age")))
```

**Output**

The SMA adds the EWI `SPRKSCL1141` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(
  ("Alice", 23),
  ("Bob", 30),
  ("Carol", 27),
  ("David", 25),
).toDF("name", "age")

/*EWI: SPRKSCL1141 => org.apache.spark.sql.functions.stddev_pop has a workaround, see documentation for more info*/
val result1 = df.select(stddev_pop("age"))
/*EWI: SPRKSCL1141 => org.apache.spark.sql.functions.stddev_pop has a workaround, see documentation for more info*/
val result2 = df.select(stddev_pop(col("age")))
```

**Recommended fix**

Snowpark has an equivalent [stddev_pop](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#stddev_pop(e:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives a column object as an argument. For that reason, the Spark overload that receives a column object as an argument is directly supported by Snowpark and does not require any changes.

For the overload that receives a string argument, you can convert the string into a column object using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq(
  ("Alice", 23),
  ("Bob", 30),
  ("Carol", 27),
  ("David", 25),
).toDF("name", "age")

val result1 = df.select(stddev_pop(col("age")))
val result2 = df.select(stddev_pop(col("age")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
