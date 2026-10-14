# SPRKSCL1171

org.apache.spark.sql.functions.split.

Message: Snowpark does not support split functions with more than two parameters or containing regex pattern. See documentation for more info.

Category: Warning.

## Description

This issue appears when the SMA detects that [org.apache.spark.sql.functions.split](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#split(str:org.apache.spark.sql.Column,pattern:String,limit:Int):org.apache.spark.sql.Column) has more than two parameters or containing regex pattern.

## Scenarios

The `split` function is used to separate the given column around matches of the given pattern. This Spark function has three overloads.

### Scenario 1

**Input**

Below is an example of the `org.apache.spark.sql.functions.split` function that generates this EWI. In this example, the `split` function has two parameters and the second argument is a string, not a regex pattern.

```scala
val df = Seq("Snowflake", "Snowpark", "Snow", "Spark").toDF("words")
val result = df.select(split(col("words"), "Snow"))
```

**Output**

The SMA adds the EWI `SPRKSCL1171` to the output code to let you know that this function is not fully supported by Snowpark.

```scala
val df = Seq("Snowflake", "Snowpark", "Snow", "Spark").toDF("words")
/* EWI: SPRKSCL1171 => Snowpark does not support split functions with more than two parameters or containing regex pattern. See documentation for more info. */
val result = df.select(split(col("words"), "Snow"))
```

**Recommended fix**

Snowpark has an equivalent [split](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#split(str:com.snowflake.snowpark.Column,pattern:com.snowflake.snowpark.Column):com.snowflake.snowpark.Column) function that receives a column object as a second argument. For that reason, the Spark overload that receives a string argument in the second argument, but it is not a regex pattern, can convert the string into a column object using the [com.snowflake.snowpark.functions.lit](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html#lit(literal:Any):com.snowflake.snowpark.Column) function as a workaround.

```scala
val df = Seq("Snowflake", "Snowpark", "Snow", "Spark").toDF("words")
val result = df.select(split(col("words"), lit("Snow")))
```

### Scenario 2

**Input**

Below is an example of the `org.apache.spark.sql.functions.split` function that generates this EWI. In this example, the `split` function has two parameters and the second argument is a regex pattern.

```scala
val df = Seq("Snowflake", "Snowpark", "Snow", "Spark").toDF("words")
val result = df.select(split(col("words"), "^([\\d]+-[\\d]+-[\\d])"))
```

**Output**

The SMA adds the EWI `SPRKSCL1171` to the output code to let you know that this function is not fully supported by Snowpark because regex patterns are not supported by Snowflake.

```scala
val df = Seq("Snowflake", "Snowpark", "Snow", "Spark").toDF("words")
/* EWI: SPRKSCL1171 => Snowpark does not support split functions with more than two parameters or containing regex pattern. See documentation for more info. */
val result = df.select(split(col("words"), "^([\\d]+-[\\d]+-[\\d])"))
```

**Recommended fix**

Since Snowflake does not supported regex patterns, try to replace the pattern by a not regex pattern string.

### Scenario 3

**Input**

Below is an example of the `org.apache.spark.sql.functions.split` function that generates this EWI. In this example, the `split` function has more than two parameters.

```scala
val df = Seq("Snowflake", "Snowpark", "Snow", "Spark").toDF("words")
val result = df.select(split(df("words"), "Snow", 3))
```

**Output**

The SMA adds the EWI `SPRKSCL1171` to the output code to let you know that this function is not fully supported by Snowpark, because Snowflake does not have a split function with more than two parameters.

```scala
val df = Seq("Snowflake", "Snowpark", "Snow", "Spark").toDF("words")
/* EWI: SPRKSCL1171 => Snowpark does not support split functions with more than two parameters or containing regex pattern. See documentation for more info. */
val result3 = df.select(split(df("words"), "Snow", 3))
```

**Recommended fix**

Since Snowflake does not supported split function with more than two parameters, try to use the split function supported by Snowflake.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).




