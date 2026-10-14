# SPRKSCL1129

org.apache.spark.sql.functions.floor

Message: org.apache.spark.sql.functions.floor has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.floor](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#floor(columnName:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.floor` function, first used with a column name as an argument, then with a column object and finally with two column objects.

```scala
val df = Seq(4.75, 6.22, 9.99).toDF("value")
val result1 = df.withColumn("floor_value", floor("value"))
val result2 = df.withColumn("floor_value", floor(col("value")))
val result3 = df.withColumn("floor_value", floor(col("value"), lit(1)))
```

**Output**

The SMA adds the EWI `SPRKSCL1129` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(4.75, 6.22, 9.99).toDF("value")
/*EWI: SPRKSCL1129 => org.apache.spark.sql.functions.floor has a workaround, see documentation for more info*/
val result1 = df.withColumn("floor_value", floor("value"))
/*EWI: SPRKSCL1129 => org.apache.spark.sql.functions.floor has a workaround, see documentation for more info*/
val result2 = df.withColumn("floor_value", floor(col("value")))
/*EWI: SPRKSCL1129 => org.apache.spark.sql.functions.floor has a workaround, see documentation for more info*/
val result3 = df.withColumn("floor_value", floor(col("value"), lit(1)))
```

**Recommended fix**

Snowpark has an equivalent [floor](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#floor(e:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives a column object as an argument. For that reason, the Spark overload that receives a column object as an argument is directly supported by Snowpark and does not require any changes.

For the overload that receives a string argument, you can convert the string into a column object using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function as a workaround.

For the overload that receives a column object and a scale, you can use the [callBuiltin](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#callBuiltin(functionName:String,args:Any*):com.snowflake.snowpark.Column) function to invoke the Snowflake builtin [FLOOR](https://docs.snowflake.com/en/sql-reference/functions/floor) function. To use it, you should pass the string **"floor"** as the first argument, the column as the second argument and the scale as the third argument.

```scala
val df = Seq(4.75, 6.22, 9.99).toDF("value")
val result1 = df.withColumn("floor_value", floor(col("value")))
val result2 = df.withColumn("floor_value", floor(col("value")))
val result3 = df.withColumn("floor_value", callBuiltin("floor", col("value"), lit(1)))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
