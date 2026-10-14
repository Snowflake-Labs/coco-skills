# SPRKSCL1132

org.apache.spark.sql.functions.grouping_id

Message: org.apache.spark.sql.functions.grouping_id has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.grouping_id](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#grouping_id(colName:String,colNames:String*):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.grouping_id` function, first used with multiple column name as arguments and then with column objects.

```scala
val df = Seq(
  ("Store1", "Product1", 100),
  ("Store1", "Product2", 150),
  ("Store2", "Product1", 200),
  ("Store2", "Product2", 250)
).toDF("store", "product", "amount")

val result1 = df.cube("store", "product").agg(sum("amount"), grouping_id("store", "product"))
val result2 = df.cube("store", "product").agg(sum("amount"), grouping_id(col("store"), col("product")))
```

**Output**

The SMA adds the EWI `SPRKSCL1132` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(
  ("Store1", "Product1", 100),
  ("Store1", "Product2", 150),
  ("Store2", "Product1", 200),
  ("Store2", "Product2", 250)
).toDF("store", "product", "amount")

/*EWI: SPRKSCL1132 => org.apache.spark.sql.functions.grouping_id has a workaround, see documentation for more info*/
val result1 = df.cube("store", "product").agg(sum("amount"), grouping_id("store", "product"))
/*EWI: SPRKSCL1132 => org.apache.spark.sql.functions.grouping_id has a workaround, see documentation for more info*/
val result2 = df.cube("store", "product").agg(sum("amount"), grouping_id(col("store"), col("product")))
```

**Recommended fix**

Snowpark has an equivalent [grouping_id](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#grouping_id(cols:com.snowflake.snowpark.Column*):com.snowflake.snowpark.Column) function that receives multiple column objects as arguments. For that reason, the Spark overload that receives multiple column objects as arguments is directly supported by Snowpark and does not require any changes.

For the overload that receives multiple string arguments, you can convert the strings into column objects using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq(
  ("Store1", "Product1", 100),
  ("Store1", "Product2", 150),
  ("Store2", "Product1", 200),
  ("Store2", "Product2", 250)
).toDF("store", "product", "amount")

val result1 = df.cube("store", "product").agg(sum("amount"), grouping_id(col("store"), col("product")))
val result2 = df.cube("store", "product").agg(sum("amount"), grouping_id(col("store"), col("product")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
