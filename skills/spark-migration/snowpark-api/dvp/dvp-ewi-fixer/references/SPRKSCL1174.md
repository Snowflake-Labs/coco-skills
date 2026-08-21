# SPRKSCL1174

org.apache.spark.sql.functions.udf

Message: The single-parameter `udf` function is supported in Snowpark but it might require manual intervention. Please check the documentation to learn how to manually modify the code to make it work in Snowpark.

Category: Warning.

## Description

This issue appears when the SMA detects an use of the single-parameter [org.apache.spark.sql.functions.udf](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#udf(f:org.apache.spark.sql.api.java.UDF10[_,_,_,_,_,_,_,_,_,_,_],returnType:org.apache.spark.sql.types.DataType):org.apache.spark.sql.expressions.UserDefinedFunction) function in the code. Then, it might require a manual intervention.

The Snowpark API provides an equivalent [com.snowflake.snowpark.functions.udf](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/functions$.html) function that allows you to create a user-defined function from a lambda or function in Scala, however, there are some caveats about creating `udf` in Snowpark that might require you to make some manual changes to your code in order to make it work properly.

## Scenarios

The Snowpark `udf` function should work as intended for a wide range of cases without requiring manual intervention. However, there are some scenarios that would requiere you to manually modify your code in order to get it work in Snowpark. Some of those scenarios are listed below:

### Scenario 1

**Input**

Below is an example of creating UDFs in an object with the App Trait.

The Scala's `App` trait simplifies creating executable programs by providing a `main` method that automatically runs the code within the object definition. Extending `App` delays the initialization of the fields until the `main` method is executed, which can affect the UDFs definitions if they rely on initialized fields. This means that if an object extends `App` and the `udf` references an object field, the `udf` definition uploaded to Snowflake will not include the initialized value of the field. This can result in `null` values being returned by the `udf`.

For example, in the following code the variable myValue will resolve to `null` in the `udf` definition:

```scala
object Main extends App {
  ...
  val myValue = 10
  val myUdf = udf((x: Int) => x + myValue) // myValue in the `udf` definition will resolve to null
  ...
}
```

**Output**

The SMA adds the EWI `SPRKSCL1174` to the output code to let you know that the single-parameter `udf` function is supported in Snowpark but it requires manual intervention.


```scala
object Main extends App {
  ...
  val myValue = 10
  /*EWI: SPRKSCL1174 => The single-parameter udf function is supported in Snowpark but it might require manual intervention. Please check the documentation to learn how to manually modify the code to make it work in Snowpark.*/
  val myUdf = udf((x: Int) => x + myValue) // myValue in the `udf` definition will resolve to null
  ...
}
```

**Recommended fix**

To avoid this issue, it is recommended to not extend `App` and implement a separate `main` method for your code. This ensure that object fields are initialized before `udf` definitions are created and uploaded to Snowflake.

```scala
object Main {
  ...
  def main(args: Array[String]): Unit = {
    val myValue = 10
    val myUdf = udf((x: Int) => x + myValue)
  }
  ...
}
```

For more details about this topic, see [Caveat About Creating UDFs in an Object With the App Trait](https://docs.snowflake.com/en/developer-guide/snowpark/scala/creating-udfs#caveat-about-creating-udfs-in-an-object-with-the-app-trait).

### Scenario 2

**Input**

Below is an example of creating UDFs in Jupyter Notebooks.

```scala
def myFunc(s: String): String = {
  ...
}

val myFuncUdf = udf((x: String) => myFunc(x))
df1.select(myFuncUdf(col("name"))).show()
```

**Output**

The SMA adds the EWI `SPRKSCL1174` to the output code to let you know that the single-parameter `udf` function is supported in Snowpark but it requires manual intervention.

```scala
def myFunc(s: String): String = {
  ...
}

/*EWI: SPRKSCL1174 => The single-parameter udf function is supported in Snowpark but it might require manual intervention. Please check the documentation to learn how to manually modify the code to make it work in Snowpark.*/
val myFuncUdf = udf((x: String) => myFunc(x))
df1.select(myFuncUdf(col("name"))).show()
```

**Recommended fix**

To create a `udf` in a Jupyter Notebook, you should define the implementation of your function in a class that extends `Serializable`. For example, you should manually convert it into this:

```scala
object ConvertedUdfFuncs extends Serializable {
  def myFunc(s: String): String = {
    ...
  }

  val myFuncAsLambda = ((x: String) => ConvertedUdfFuncs.myFunc(x))
}

val myFuncUdf = udf(ConvertedUdfFuncs.myFuncAsLambda)
df1.select(myFuncUdf(col("name"))).show()
```

For more details about how to create UDFs in Jupyter Notebooks, see [Creating UDFs in Jupyter Notebooks](https://docs.snowflake.com/en/developer-guide/snowpark/scala/creating-udfs#creating-udfs-in-jupyter-notebooks).

## Additional recommendations

- To learn more about how to create user-defined functions in Snowpark, please refer to the following documentation: [Creating User-Defined Functions (UDFs) for DataFrames in Scala](https://docs.snowflake.com/en/developer-guide/snowpark/scala/creating-udfs)

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
