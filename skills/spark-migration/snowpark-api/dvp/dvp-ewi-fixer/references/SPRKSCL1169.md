# SPRKSCL1169

Spark element is missing on the method chaining.

Message: ***Spark element*** is missing on the method chaining.

Category: Warning.

## Description

This issue appears when the SMA detects that a Spark element call is missing on the method chaining. SMA needs to know that Spark element to analyze the statement.

## Scenario

**Input**

Below is an example where load function call is missing on the method chaining.

```scala
val reader = spark.read.format("json")
val df = reader.load(path)
```

**Output**

The SMA adds the EWI `SPRKSCL1169` to the output code to let you know that load function call is missing on the method chaining and SMA can not analyze the statement.

```scala
/*EWI: SPRKSCL1169 => Function 'org.apache.spark.sql.DataFrameReader.load' is missing on the method chaining*/
val reader = spark.read.format("json")
val df = reader.load(path)
```

**Recommended fix**

Make sure that all function calls of the method chaining are in the same statement.

```scala
val reader = spark.read.format("json").load(path)
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
