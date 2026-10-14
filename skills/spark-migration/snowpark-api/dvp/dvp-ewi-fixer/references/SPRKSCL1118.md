# SPRKSCL1118

org.apache.spark.sql.functions.trunc

Message: org.apache.spark.sql.functions.trunc has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.trunc](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#trunc(date:org.apache.spark.sql.Column,format:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.trunc` function that generates this EWI.

```scala
val df = Seq(
  Date.valueOf("2024-10-28"),
  Date.valueOf("2023-05-15"),
  Date.valueOf("2022-11-20"),
).toDF("date")

val result = df.withColumn("truncated", trunc(col("date"), "month"))
```

**Output**

The SMA adds the EWI `SPRKSCL1118` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(
  Date.valueOf("2024-10-28"),
  Date.valueOf("2023-05-15"),
  Date.valueOf("2022-11-20"),
).toDF("date")

/*EWI: SPRKSCL1118 => org.apache.spark.sql.functions.trunc has a workaround, see documentation for more info*/
val result = df.withColumn("truncated", trunc(col("date"), "month"))
```

**Recommended fix**

As a workaround, you can convert the second argument into a column object using the [com.snowflake.snowpark.functions.lit](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#lit(literal:Any):com.snowflake.snowpark.Column) function.

```scala
val df = Seq(
  Date.valueOf("2024-10-28"),
  Date.valueOf("2023-05-15"),
  Date.valueOf("2022-11-20"),
).toDF("date")

val result = df.withColumn("truncated", trunc(col("date"), lit("month")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
