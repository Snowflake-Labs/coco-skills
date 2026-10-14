# SPRKSCL1119

org.apache.spark.sql.Column.endsWith

Message: org.apache.spark.sql.Column.endsWith has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.Column.endsWith](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/Column.html#endsWith(literal:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.Column.endsWith` function, first used with a literal string argument and then with a column object argument.

```scala
val df1 = Seq(
  ("Alice", "alice@example.com"),
  ("Bob", "bob@example.org"),
  ("David", "david@example.com")
).toDF("name", "email")
val result1 = df1.filter(col("email").endsWith(".com"))

val df2 = Seq(
  ("Alice", "alice@example.com", ".com"),
  ("Bob", "bob@example.org", ".org"),
  ("David", "david@example.org", ".com")
).toDF("name", "email", "suffix")
val result2 = df2.filter(col("email").endsWith(col("suffix")))
```

**Output**

The SMA adds the EWI `SPRKSCL1119` to the output code to let you know that this function is not directly supported by Snowpark, but it has a workaround.

```scala
val df1 = Seq(
  ("Alice", "alice@example.com"),
  ("Bob", "bob@example.org"),
  ("David", "david@example.com")
).toDF("name", "email")
/*EWI: SPRKSCL1119 => org.apache.spark.sql.Column.endsWith has a workaround, see documentation for more info*/
val result1 = df1.filter(col("email").endsWith(".com"))

val df2 = Seq(
  ("Alice", "alice@example.com", ".com"),
  ("Bob", "bob@example.org", ".org"),
  ("David", "david@example.org", ".com")
).toDF("name", "email", "suffix")
/*EWI: SPRKSCL1119 => org.apache.spark.sql.Column.endsWith has a workaround, see documentation for more info*/
val result2 = df2.filter(col("email").endsWith(col("suffix")))
```

**Recommended fix**

As a workaround, you can use the [com.snowflake.snowpark.functions.endswith](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#endswith(expr:com.snowflake.snowpark.Column,str:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function, where the first argument would be the column whose values will be checked and the second argument the suffix to check against the column values. Please note that if the argument of the Spark's `endswith` function is a literal string, you should convert it into a column object using the [com.snowflake.snowpark.functions.lit](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#lit(literal:Any):com.snowflake.snowpark.Column) function.

```scala
val df1 = Seq(
  ("Alice", "alice@example.com"),
  ("Bob", "bob@example.org"),
  ("David", "david@example.com")
).toDF("name", "email")
val result1 = df1.filter(endswith(col("email"), lit(".com")))

val df2 = Seq(
  ("Alice", "alice@example.com", ".com"),
  ("Bob", "bob@example.org", ".org"),
  ("David", "david@example.org", ".com")
).toDF("name", "email", "suffix")
val result2 = df2.filter(endswith(col("email"), col("suffix")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
