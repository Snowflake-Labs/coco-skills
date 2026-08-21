# SPRKSCL1147

org.apache.spark.sql.functions.tanh

Message: org.apache.spark.sql.functions.tanh has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.tanh](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#tanh(columnName:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.tanh` function, first used with a column name as an argument and then with a column object.

```scala
val df = Seq(-1.0, 0.5, 1.0, 2.0).toDF("value")
val result1 = df.withColumn("tanh_value", tanh("value"))
val result2 = df.withColumn("tanh_value", tanh(col("value")))
```

**Output**

The SMA adds the EWI `SPRKSCL1147` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(-1.0, 0.5, 1.0, 2.0).toDF("value")
/*EWI: SPRKSCL1147 => org.apache.spark.sql.functions.tanh has a workaround, see documentation for more info*/
val result1 = df.withColumn("tanh_value", tanh("value"))
/*EWI: SPRKSCL1147 => org.apache.spark.sql.functions.tanh has a workaround, see documentation for more info*/
val result2 = df.withColumn("tanh_value", tanh(col("value")))
```

**Recommended fix**

Snowpark has an equivalent [tanh](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#tanh(e:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives a column object as an argument. For that reason, the Spark overload that receives a column object as an argument is directly supported by Snowpark and does not require any changes.

For the overload that receives a string argument, you can convert the string into a column object using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq(-1.0, 0.5, 1.0, 2.0).toDF("value")
val result1 = df.withColumn("tanh_value", tanh(col("value")))
val result2 = df.withColumn("tanh_value", tanh(col("value")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
