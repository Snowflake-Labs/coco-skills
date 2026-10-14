# SPRKSCL1117

org.apache.spark.sql.functions.translate

> This issue code is **deprecated** since [Spark Conversion Core 2.40.1](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-2.40.1)

Message: org.apache.spark.sql.functions.translate has a workaround, see documentation for more info

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.translate](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#translate(src:org.apache.spark.sql.Column,matchingString:String,replaceString:String):org.apache.spark.sql.Column) function, which has a workaround.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.translate` function that generates this EWI. In this example, the `translate` function is used to replace the characters **'a'**, **'e'** and **'o'** in each word with **'1'**, **'2'** and **'3'**, respectively.

```scala
val df = Seq("hello", "world", "scala").toDF("word")
val result = df.withColumn("translated_word", translate(col("word"), "aeo", "123"))
```

**Output**

The SMA adds the EWI `SPRKSCL1117` to the output code to let you know that this function is not fully supported by Snowpark, but it has a workaround.

```scala
val df = Seq("hello", "world", "scala").toDF("word")
/*EWI: SPRKSCL1117 => org.apache.spark.sql.functions.translate has a workaround, see documentation for more info*/
val result = df.withColumn("translated_word", translate(col("word"), "aeo", "123"))
```

**Recommended fix**

As a workaround, you can convert the second and third argument into a column object using the [com.snowflake.snowpark.functions.lit](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#lit(literal:Any):com.snowflake.snowpark.Column) function.

```scala
val df = Seq("hello", "world", "scala").toDF("word")
val result = df.withColumn("translated_word", translate(col("word"), lit("aeo"), lit("123")))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
