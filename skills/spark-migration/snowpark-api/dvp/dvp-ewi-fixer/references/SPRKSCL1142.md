# SPRKSCL1142

Element is not defined

Message: ***spark element*** is not defined

Category: Conversion error

## Description

This issue appears when the SMA could not determine an appropriate mapping status for the given element. This means, the SMA doesn't know yet if this element is supported or not by Snowpark. Please note, this is a generic error code used by the SMA for any not defined element.

## Scenario

**Input**

Below is an example of a function for which the SMA could not determine an appropriate mapping status, and therefore it generated this EWI. In this case, you should assume that `notDefinedFunction()` is a valid Spark function and the code runs.

```scala
val df = session.range(10)
val result = df.notDefinedFunction()
````

**Output**

The SMA adds the EWI `SPRKSCL1142` to the output code to let you know that this element is not defined.

```scala
val df = session.range(10)
/*EWI: SPRKSCL1142 => org.apache.spark.sql.DataFrame.notDefinedFunction is not defined*/
val result = df.notDefinedFunction()
```

**Recommended fix**

To try to identify the problem, you can perform the following validations:
  - Check if it is a valid Spark element.
  - Check if the element has the correct syntax and it is spelled correctly.
  - Check if you are using a Spark version supported by the SMA.

If this is a valid Spark element, please report that you encountered a conversion error on that particular element using the [Report an Issue](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue) option of the SMA and include any additional information that you think may be helpful.

Please note that if an element is not defined by the SMA, it does not mean necessarily that it is not supported by Snowpark. You should check the [Snowpark Documentation](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/index.html) to verify if an equivalent element exist.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
