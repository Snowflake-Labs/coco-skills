# SPRKSCL1112

Element is not supported

Message: ***spark element*** is not supported

Category: Conversion error

## Description

This issue appears when the SMA detects the use of a Spark element that is not supported by Snowpark, and it does not have its own error code associated with it. This is a generic error code used by the SMA for any unsupported Spark element.

## Scenario

**Input**

Below is an example of a Spark element that is not supported by Snowpark, and therefore it generates this EWI.

```scala
val df = session.range(10)
val result = df.isLocal
```

**Output**

The SMA adds the EWI `SPRKSCL1112` to the output code to let you know that this element is not supported by Snowpark.

```scala
val df = session.range(10)
/*EWI: SPRKSCL1112 => org.apache.spark.sql.Dataset.isLocal is not supported*/
val result = df.isLocal
```

**Recommended fix**

Since this is a generic error code that applies to a range of unsupported functions, there is not a single and specific fix. The appropriate action will depend on the particular element in use.

Please note that even though the element is not supported, it does not necessarily mean that a solution or workaround cannot be found. It only means that the SMA itself cannot find the solution.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
