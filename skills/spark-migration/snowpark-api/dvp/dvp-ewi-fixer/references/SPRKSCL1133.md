# SPRKSCL1133

org.apache.spark.sql.functions.least

Message: org.apache.spark.sql.functions.least has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.least](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#least(columnName:String,columnNames:String*):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.least` function, first used with multiple column name as arguments and then with column objects.

```scala
val df = Seq((10, 20, 5), (15, 25, 30), (7, 14, 3)).toDF("value1", "value2", "value3")
val result1 = df.withColumn("least", least("value1", "value2", "value3"))
val result2 = df.withColumn("least", least(col("value1"), col("value2"), col("value3")))
```

**Output**

The SMA adds the EWI `SPRKSCL1133` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq((10, 20, 5), (15, 25, 30), (7, 14, 3)).toDF("value1", "value2", "value3")
/*EWI: SPRKSCL1133 => org.apache.spark.sql.functions.least has a workaround, see documentation for more info*/
val result1 = df.withColumn("least", least("value1", "value2", "value3"))
/*EWI: SPRKSCL1133 => org.apache.spark.sql.functions.least has a workaround, see documentation for more info*/
val result2 = df.withColumn("least", least(col("value1"), col("value2"), col("value3")))
```

**Recommended fix**

Snowpark has an equivalent [least](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#least(exprs:com.snowflake.snowpark.Column*):com.snowflake.snowpark.Column) function that receives multiple column objects as arguments. For that reason, the Spark overload that receives multiple column objects as arguments is directly supported by Snowpark and does not require any changes.

For the overload that receives multiple string arguments, you can convert the strings into column objects using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq((10, 20, 5), (15, 25, 30), (7, 14, 3)).toDF("value1", "value2", "value3")
val result1 = df.withColumn("least", least(col("value1"), col("value2"), col("value3")))
val result2 = df.withColumn("least", least(col("value1"), col("value2"), col("value3")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
