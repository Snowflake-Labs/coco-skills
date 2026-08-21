# SPRKSCL1155

org.apache.spark.sql.functions.countDistinct

> This issue code has been **deprecated** since [Spark Conversion Core Version 4.3.2](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.3.2)

Message: org.apache.spark.sql.functions.countDistinct has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.countDistinct](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#countDistinct(columnName:String,columnNames:String*):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.countDistinct` function, first used with column names as arguments and then with column objects.

```scala
val df = Seq(
  ("Alice", 1),
  ("Bob", 2),
  ("Alice", 3),
  ("Bob", 4),
  ("Alice", 1),
  ("Charlie", 5)
).toDF("name", "value")

val result1 = df.select(countDistinct("name", "value"))
val result2 = df.select(countDistinct(col("name"), col("value")))
```

**Output**

The SMA adds the EWI `SPRKSCL1155` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(
  ("Alice", 1),
  ("Bob", 2),
  ("Alice", 3),
  ("Bob", 4),
  ("Alice", 1),
  ("Charlie", 5)
).toDF("name", "value")

/*EWI: SPRKSCL1155 => org.apache.spark.sql.functions.countDistinct has a workaround, see documentation for more info*/
val result1 = df.select(countDistinct("name", "value"))
/*EWI: SPRKSCL1155 => org.apache.spark.sql.functions.countDistinct has a workaround, see documentation for more info*/
val result2 = df.select(countDistinct(col("name"), col("value")))
```

**Recommended fix**

As a workaround, you can use the [count_distinct](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#count_distinct(expr:com.snowflake.snowpark.Column,exprs:com.snowflake.snowpark.Column*):com.snowflake.snowpark.Column) function. For the Spark overload that receives string arguments, you additionally have to convert the strings into column objects using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function.

```scala
val df = Seq(
  ("Alice", 1),
  ("Bob", 2),
  ("Alice", 3),
  ("Bob", 4),
  ("Alice", 1),
  ("Charlie", 5)
).toDF("name", "value")

val result1 = df.select(count_distinct(col("name"), col("value")))
val result2 = df.select(count_distinct(col("name"), col("value")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
