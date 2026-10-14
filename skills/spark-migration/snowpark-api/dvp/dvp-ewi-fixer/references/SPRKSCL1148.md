# SPRKSCL1148

org.apache.spark.sql.functions.toDegrees

Message: org.apache.spark.sql.functions.toDegrees has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.toDegrees](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#toDegrees(columnName:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.toDegrees` function, first used with a column name as an argument and then with a column object.

```scala
val df = Seq(Math.PI, Math.PI / 2, Math.PI / 4).toDF("angle_in_radians")
val result1 = df.withColumn("angle_in_degrees", toDegrees("angle_in_radians"))
val result2 = df.withColumn("angle_in_degrees", toDegrees(col("angle_in_radians")))
```

**Output**

The SMA adds the EWI `SPRKSCL1148` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(Math.PI, Math.PI / 2, Math.PI / 4).toDF("angle_in_radians")
/*EWI: SPRKSCL1148 => org.apache.spark.sql.functions.toDegrees has a workaround, see documentation for more info*/
val result1 = df.withColumn("angle_in_degrees", toDegrees("angle_in_radians"))
/*EWI: SPRKSCL1148 => org.apache.spark.sql.functions.toDegrees has a workaround, see documentation for more info*/
val result2 = df.withColumn("angle_in_degrees", toDegrees(col("angle_in_radians")))
```

**Recommended fix**

As a workaround, you can use the [degrees](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#degrees(e:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function. For the Spark overload that receives a string argument, you additionally have to convert the string into a column object using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function.

```scala
val df = Seq(Math.PI, Math.PI / 2, Math.PI / 4).toDF("angle_in_radians")
val result1 = df.withColumn("angle_in_degrees", degrees(col("angle_in_radians")))
val result2 = df.withColumn("angle_in_degrees", degrees(col("angle_in_radians")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
