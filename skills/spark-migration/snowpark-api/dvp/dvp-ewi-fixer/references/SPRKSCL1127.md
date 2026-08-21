# SPRKSCL1127

org.apache.spark.sql.functions.covar_samp

Message: org.apache.spark.sql.functions.covar_samp has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.covar_samp](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#covar_samp(columnName1:String,columnName2:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.covar_samp` function, first used with column names as the arguments and then with column objects.

```scala
val df = Seq(
  (10.0, 20.0),
  (15.0, 25.0),
  (20.0, 30.0),
  (25.0, 35.0),
  (30.0, 40.0)
).toDF("value1", "value2")

val result1 = df.select(covar_samp("value1", "value2").as("sample_covariance"))
val result2 = df.select(covar_samp(col("value1"), col("value2")).as("sample_covariance"))
```

**Output**

The SMA adds the EWI `SPRKSCL1127` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(
  (10.0, 20.0),
  (15.0, 25.0),
  (20.0, 30.0),
  (25.0, 35.0),
  (30.0, 40.0)
).toDF("value1", "value2")

/*EWI: SPRKSCL1127 => org.apache.spark.sql.functions.covar_samp has a workaround, see documentation for more info*/
val result1 = df.select(covar_samp("value1", "value2").as("sample_covariance"))
/*EWI: SPRKSCL1127 => org.apache.spark.sql.functions.covar_samp has a workaround, see documentation for more info*/
val result2 = df.select(covar_samp(col("value1"), col("value2")).as("sample_covariance"))
```

**Recommended fix**

Snowpark has an equivalent [covar_samp](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#covar_samp(column1:com.snowflake.snowpark.Column,column2:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives two column objects as arguments. For that reason, the Spark overload that receives two column objects as arguments is directly supported by Snowpark and does not require any changes.

For the overload that receives two string arguments, you can convert the strings into column objects using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq(
  (10.0, 20.0),
  (15.0, 25.0),
  (20.0, 30.0),
  (25.0, 35.0),
  (30.0, 40.0)
).toDF("value1", "value2")

val result1 = df.select(covar_samp(col("value1"), col("value2")).as("sample_covariance"))
val result2 = df.select(covar_samp(col("value1"), col("value2")).as("sample_covariance"))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
