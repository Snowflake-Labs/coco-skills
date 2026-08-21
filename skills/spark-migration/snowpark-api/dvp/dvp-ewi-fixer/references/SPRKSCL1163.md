# SPRKSCL1163

The current processing element is not a literal.

Message: The element is not a literal and can't be evaluated.

Category: Conversion error.

## Description

This issue occurs when the current processing element is not a literal, then it can not be evaluated by SMA.

## Scenario

**Input**

Below is an example when element to process is not a literal and it can not be evaluated by SMA.

```scala
val format_type = "csv"
spark.read.format(format_type).load(path)
```

**Output**

The SMA adds the EWI `SPRKSCL1163` to the output code to let you know that `format_type` parameter is not a literal and it can not be evaluated by the SMA.

```scala
/*EWI: SPRKSCL1163 => format_type is not a literal and can't be evaluated*/
val format_type = "csv"
spark.read.format(format_type).load(path)
```

**Recommended fix**

- Make sure that a value of the variable is a valid one in order to avoid unexpected behaviors.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
