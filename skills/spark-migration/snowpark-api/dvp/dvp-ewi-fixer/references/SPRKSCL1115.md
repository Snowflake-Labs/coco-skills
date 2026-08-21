# SPRKSCL1115

org.apache.spark.sql.functions.round

> This issue code has been **deprecated** since [Spark Conversion Core Version 4.6.0](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.6.0)

Message: org.apache.spark.sql.functions.round has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.round](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#round(e:org.apache.spark.sql.Column,scale:Int):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.round` function that generates this EWI.

```scala
val df = Seq(3.9876, 5.673, 8.1234).toDF("value")
val result1 = df.withColumn("rounded_value", round(col("value")))
val result2 = df.withColumn("rounded_value", round(col("value"), 2))
```

**Output**

The SMA adds the EWI `SPRKSCL1115` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(3.9876, 5.673, 8.1234).toDF("value")
/*EWI: SPRKSCL1115 => org.apache.spark.sql.functions.round has a workaround, see documentation for more info*/
val result1 = df.withColumn("rounded_value", round(col("value")))
/*EWI: SPRKSCL1115 => org.apache.spark.sql.functions.round has a workaround, see documentation for more info*/
val result2 = df.withColumn("rounded_value", round(col("value"), 2))
```

**Recommended fix**

Snowpark has an equivalent [round](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#round(e:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives a column object as an argument. For that reason, the Spark overload that receives a column object as an argument is directly supported by Snowpark and does not require any changes.

For the overload that receives a column object and a scale, you can convert the scale into a column object using the [com.snowflake.snowpark.functions.lit](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#lit(literal:Any):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq(3.9876, 5.673, 8.1234).toDF("value")
val result1 = df.withColumn("rounded_value", round(col("value")))
val result2 = df.withColumn("rounded_value", round(col("value"), lit(2)))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
