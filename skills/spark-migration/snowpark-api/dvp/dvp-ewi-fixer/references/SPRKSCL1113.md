# SPRKSCL1113

org.apache.spark.sql.functions.next_day

Message: org.apache.spark.sql.functions.next_day has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.next_day](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#next_day(date:org.apache.spark.sql.Column,dayOfWeek:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.next_day` function, first used with a string as the second argument and then with a column object.

```scala
val df = Seq("2024-11-06", "2024-11-13", "2024-11-20").toDF("date")
val result1 = df.withColumn("next_monday", next_day(col("date"), "Mon"))
val result2 = df.withColumn("next_monday", next_day(col("date"), lit("Mon")))
```

**Output**

The SMA adds the EWI `SPRKSCL1113` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq("2024-11-06", "2024-11-13", "2024-11-20").toDF("date")
/*EWI: SPRKSCL1113 => org.apache.spark.sql.functions.next_day has a workaround, see documentation for more info*/
val result1 = df.withColumn("next_monday", next_day(col("date"), "Mon"))
/*EWI: SPRKSCL1113 => org.apache.spark.sql.functions.next_day has a workaround, see documentation for more info*/
val result2 = df.withColumn("next_monday", next_day(col("date"), lit("Mon")))
```

**Recommended fix**

Snowpark has an equivalent [next_day](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#next_day(date:com.snowflake.snowpark.Column,dayOfWeek:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives two column objects as arguments. For that reason, the Spark overload that receives two column objects as arguments is directly supported by Snowpark and does not require any changes.

For the overload that receives a column object and a string, you can convert the string into a column object using the [com.snowflake.snowpark.functions.lit](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#lit(literal:Any):org.apache.spark.sql.Column) function as a workaround.

```scala
val df = Seq("2024-11-06", "2024-11-13", "2024-11-20").toDF("date")
val result1 = df.withColumn("next_monday", next_day(col("date"), lit("Mon")))
val result2 = df.withColumn("next_monday", next_day(col("date"), lit("Mon")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
