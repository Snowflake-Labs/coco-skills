# SPRKSCL1114

org.apache.spark.sql.functions.repeat

Message: org.apache.spark.sql.functions.repeat has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.repeat](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#repeat(str:org.apache.spark.sql.Column,n:Int):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.repeat` function that generates this EWI.

```scala
val df = Seq("Hello", "World").toDF("word")
val result = df.withColumn("repeated_word", repeat(col("word"), 3))
```

**Output**

The SMA adds the EWI `SPRKSCL1114` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq("Hello", "World").toDF("word")
/*EWI: SPRKSCL1114 => org.apache.spark.sql.functions.repeat has a workaround, see documentation for more info*/
val result = df.withColumn("repeated_word", repeat(col("word"), 3))
```

**Recommended fix**

As a workaround, you can convert the second argument into a column object using the [com.snowflake.snowpark.functions.lit](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#lit(literal:Any):com.snowflake.snowpark.Column) function.

```scala
val df = Seq("Hello", "World").toDF("word")
val result = df.withColumn("repeated_word", repeat(col("word"), lit(3)))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
