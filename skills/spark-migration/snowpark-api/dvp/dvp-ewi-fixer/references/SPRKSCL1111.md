# SPRKSCL1111

org.apache.spark.sql.types.DataTypes.CreateDecimalType

> This issue code has been **deprecated**

Message: CreateDecimalType is not supported.

Category: Conversion error.

## Description

This issue appears when the SMA detects a usage [org.apache.spark.sql.types.DataTypes.CreateDecimalType](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/types/DecimalType.html) function.

## Scenario

**Input**

Below is an example of usage of org.apache.spark.sql.types.DataTypes.CreateDecimalType function.

```scala
var result = DataTypes.createDecimalType(18, 8)
```

**Output**

The SMA adds the EWI `SPRKSCL1111` to the output code to let you know that CreateDecimalType function is not supported by Snowpark.

```scala
/*EWI: SPRKSCL1111 => CreateDecimalType is not supported*/
var result = createDecimalType(18, 8)
```

**Recommended fix**

There is not a recommended fix yet.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
