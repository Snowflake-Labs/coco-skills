# SPRKSCL1168

Spark element with a given argument is not supported.

Message: ***Spark element*** with argument(s) value(s) ***given arguments*** is not supported.

Category: Warning.

## Description

This issue appears when the SMA detects that Spark element with the given parameters is not supported.

## Scenario

**Input**

Below is an example of Spark element which parameter is not supported.

```scala
spark.read.format("text").load(path)
```

**Output**

The SMA adds the EWI `SPRKSCL1168` to the output code to let you know that Spark element with the given parameter is not supported.

```scala
/*EWI: SPRKSCL1168 => org.apache.spark.sql.DataFrameReader.format(scala.String) with argument(s) value(s) (spark.format) is not supported*/
spark.read.format("text").load(path)
```

**Recommended fix**

For this scenario there is no specific fix.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
