# SPRKSCL1116

org.apache.spark.sql.functions.split

> This issue code has been **deprecated** since [Spark Conversion Core Version 2.40.1](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-2.40.1)

Message: org.apache.spark.sql.functions.split has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.split](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#split(str:org.apache.spark.sql.Column,pattern:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.split` function that generates this EWI.

```scala
val df = Seq("apple,banana,orange", "grape,lemon,lime", "cherry,blueberry,strawberry").toDF("values")
val result1 = df.withColumn("split_values", split(col("values"), ","))
val result2 = df.withColumn("split_values", split(col("values"), ",", 0))
```

**Output**

The SMA adds the EWI `SPRKSCL1116` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq("apple,banana,orange", "grape,lemon,lime", "cherry,blueberry,strawberry").toDF("values")
/*EWI: SPRKSCL1116 => org.apache.spark.sql.functions.split has a workaround, see documentation for more info*/
val result1 = df.withColumn("split_values", split(col("values"), ","))
/*EWI: SPRKSCL1116 => org.apache.spark.sql.functions.split has a workaround, see documentation for more info*/
val result2 = df.withColumn("split_values", split(col("values"), ",", 0))
```

**Recommended fix**

For the Spark overload that receives two arguments, you can convert the second argument into a column object using the [com.snowflake.snowpark.functions.lit](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#lit(literal:Any):com.snowflake.snowpark.Column) function as a workaround.

The overload that receives three arguments is not yet supported by Snowpark and there is no workaround.

```scala
val df = Seq("apple,banana,orange", "grape,lemon,lime", "cherry,blueberry,strawberry").toDF("values")
val result1 = df.withColumn("split_values", split(col("values"), lit(",")))
val result2 = df.withColumn("split_values", split(col("values"), ",", 0)) // This overload is not supported yet
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
