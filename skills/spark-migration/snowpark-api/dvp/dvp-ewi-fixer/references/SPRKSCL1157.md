# SPRKSCL1157

org.apache.spark.sql.functions.kurtosis

Message: org.apache.spark.sql.functions.kurtosis has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.kurtosis](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#kurtosis(columnName:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.kurtosis` function that generates this EWI. In this example, the `kurtosis` function is used to calculate the kurtosis of selected column.

```scala
val df = Seq("1", "2", "3").toDF("elements")
val result1 = kurtosis(col("elements"))
val result2 = kurtosis("elements")
```

**Output**

The SMA adds the EWI `SPRKSCL1157` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq("1", "2", "3").toDF("elements")
/*EWI: SPRKSCL1157 => org.apache.spark.sql.functions.kurtosis has a workaround, see documentation for more info*/
val result1 = kurtosis(col("elements"))
/*EWI: SPRKSCL1157 => org.apache.spark.sql.functions.kurtosis has a workaround, see documentation for more info*/
val result2 = kurtosis("elements")
```

**Recommended fix**

Snowpark has an equivalent [kurtosis](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#kurtosis(e:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives a column object as an argument. For that reason, the Spark overload that receives a column object as an argument is directly supported by Snowpark and does not require any changes.

For the overload that receives a string argument, you can convert the string into a column object using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq("1", "2", "3").toDF("elements")
val result1 = kurtosis(col("elements"))
val result2 = kurtosis(col("elements"))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
