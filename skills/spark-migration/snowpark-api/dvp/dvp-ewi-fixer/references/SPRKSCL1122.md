# SPRKSCL1122

org.apache.spark.sql.functions.corr

Message: org.apache.spark.sql.functions.corr has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.corr](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#corr(columnName1:String,columnName2:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.corr` function, first used with column names as the arguments and then with column objects.

```scala
val df = Seq(
  (10.0, 20.0),
  (20.0, 40.0),
  (30.0, 60.0)
).toDF("col1", "col2")

val result1 = df.select(corr("col1", "col2"))
val result2 = df.select(corr(col("col1"), col("col2")))
```

**Output**

The SMA adds the EWI `SPRKSCL1122` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq(
  (10.0, 20.0),
  (20.0, 40.0),
  (30.0, 60.0)
).toDF("col1", "col2")

/*EWI: SPRKSCL1122 => org.apache.spark.sql.functions.corr has a workaround, see documentation for more info*/
val result1 = df.select(corr("col1", "col2"))
/*EWI: SPRKSCL1122 => org.apache.spark.sql.functions.corr has a workaround, see documentation for more info*/
val result2 = df.select(corr(col("col1"), col("col2")))
```

**Recommended fix**

Snowpark has an equivalent [corr](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#corr(column1:com.snowflake.snowpark.Column,column2:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives two column objects as arguments. For that reason, the Spark overload that receives column objects as arguments is directly supported by Snowpark and does not require any changes.

For the overload that receives two string arguments, you can convert the strings into column objects using the [com.snowflake.snowpark.functions.col](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#col(colName:String):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq(
  (10.0, 20.0),
  (20.0, 40.0),
  (30.0, 60.0)
).toDF("col1", "col2")

val result1 = df.select(corr(col("col1"), col("col2")))
val result2 = df.select(corr(col("col1"), col("col2")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
