# SPRKSCL1175

org.apache.spark.sql.functions.udf

Message: The two-parameter`udf`function is not supported in Snowpark. It should be converted into a single-parameter`udf`function. Please check the documentation to learn how to manually modify the code to make it work in Snowpark.

Category: Conversion error.

## Description

This issue appears when the SMA detects an use of the two-parameter [org.apache.spark.sql.functions.udf](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#udf(f:org.apache.spark.sql.api.java.UDF0[_],returnType:org.apache.spark.sql.types.DataType):org.apache.spark.sql.expressions.UserDefinedFunction) function in the source code, because Snowpark does not have an equivalent two-parameter `udf` function, then the output code might not compile.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.udf` function that generates this EWI. In this example, the `udf` function has two parameters.

```scala
val myFuncUdf = udf(new UDF1[String, Integer] {
  override def call(s: String): Integer = s.length()
}, IntegerType)
```

**Output**

The SMA adds the EWI `SPRKSCL1175` to the output code to let you know that the `udf` function is not supported, because it has two parameters.

```scala
/*EWI: SPRKSCL1175 => The two-parameter udf function is not supported in Snowpark. It should be converted into a single-parameter udf function. Please check the documentation to learn how to manually modify the code to make it work in Snowpark.*/
val myFuncUdf = udf(new UDF1[String, Integer] {
  override def call(s: String): Integer = s.length()
}, IntegerType)
```

**Recommended fix**

Snowpark only supports the single-parameter `udf` function (without the return type parameter), so you should convert your two-parameter `udf` function into a single-parameter `udf` function in order to make it work in Snowpark.

For example, for the sample code mentioned above, you would have to manually convert it into this:

```scala
val myFuncUdf = udf((s: String) => s.length())
```

Please note that there are some caveats about creating `udf` in Snowpark that might require you to make some additional manual changes to your code. Please check this other recommendations [here](https://docs.snowconvert.com/sma/issue-analysis/issue-codes-by-source/spark-scala/sprkscl1174#recommendation) related with creating single-parameter `udf` functions in Snowpark for more details.

## Additional recommendations

- To learn more about how to create user-defined functions in Snowpark, please refer to the following documentation: [Creating User-Defined Functions (UDFs) for DataFrames in Scala](https://docs.snowflake.com/en/developer-guide/snowpark/scala/creating-udfs)

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
