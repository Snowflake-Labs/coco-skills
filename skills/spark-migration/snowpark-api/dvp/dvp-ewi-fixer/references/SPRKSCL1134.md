# SPRKSCL1134

org.apache.spark.sql.functions.log

Message: org.apache.spark.sql.functions.log has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.log](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#log(base:Double,columnName:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.log` function that generates this EWI.

```scala
val df = Seq(10.0, 20.0, 30.0, 40.0).toDF("value")
val result1 = df.withColumn("log_value", log(10, "value"))
val result2 = df.withColumn("log_value", log(10, col("value")))
val result3 = df.withColumn("log_value", log("value"))
val result4 = df.withColumn("log_value", log(col("value")))
```

**Output**

The SMA adds the EWI `SPRKSCL1134` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(10.0, 20.0, 30.0, 40.0).toDF("value")
/*EWI: SPRKSCL1134 => org.apache.spark.sql.functions.log has a workaround, see documentation for more info*/
val result1 = df.withColumn("log_value", log(10, "value"))
/*EWI: SPRKSCL1134 => org.apache.spark.sql.functions.log has a workaround, see documentation for more info*/
val result2 = df.withColumn("log_value", log(10, col("value")))
/*EWI: SPRKSCL1134 => org.apache.spark.sql.functions.log has a workaround, see documentation for more info*/
val result3 = df.withColumn("log_value", log("value"))
/*EWI: SPRKSCL1134 => org.apache.spark.sql.functions.log has a workaround, see documentation for more info*/
val result4 = df.withColumn("log_value", log(col("value")))
```

**Recommended fix**

Below are the different workarounds for all the overloads of the `log` function.

**1. def log(base: Double, columnName: String): Column**

You can convert the base into a column object using the [com.snowflake.snowpark.functions.lit](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#lit(literal:Any):com.snowflake.snowpark.Column) function and convert the column name into a column object using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function.

```scala
val result1 = df.withColumn("log_value", log(lit(10), col("value")))
```

**2. def log(base: Double, a: Column): Column**

You can convert the base into a column object using the [com.snowflake.snowpark.functions.lit](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#lit(literal:Any):com.snowflake.snowpark.Column) function.

```scala
val result2 = df.withColumn("log_value", log(lit(10), col("value")))
```

**3.def log(columnName: String): Column**

You can pass `lit(Math.E)` as the first argument and convert the column name into a column object using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function and pass it as the second argument.

```scala
val result3 = df.withColumn("log_value", log(lit(Math.E), col("value")))
```

**4. def log(e: Column): Column**

You can pass `lit(Math.E)` as the first argument and the column object as the second argument.

```scala
val result4 = df.withColumn("log_value", log(lit(Math.E), col("value")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
