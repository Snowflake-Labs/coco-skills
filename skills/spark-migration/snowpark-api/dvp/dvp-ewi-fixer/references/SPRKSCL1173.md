# SPRKSCL1173

SQL embedded code cannot be processed.

Message: SQL embedded code cannot be processed.

Category: Warning.

## Description

This issue appears when the SMA detects a SQL-embedded code that can not be processed. Then, the SQL-embedded code can not be converted to Snowflake.

## Scenario

**Input**

Below is an example of a SQL-embedded code that can not be processed.

```scala
spark.sql("CREATE VIEW IF EXISTS My View" + "AS Select * From my Table WHERE date < current_date()")
```

**Output**

The SMA adds the EWI `SPRKSCL1173` to the output code to let you know that the SQL-embedded code can not be processed.

```scala
/*EWI: SPRKSCL1173 => SQL embedded code cannot be processed.*/
spark.sql("CREATE VIEW IF EXISTS My View" + "AS Select * From my Table WHERE date < current_date()")
```

**Recommended fix**

Make sure that the SQL-embedded code is a string without interpolations, variables or string concatenations.

## Additional recommendations

- You can find more information about SQL-embedded [here](https://docs.snowconvert.com/sma/translation-reference/sql-embedded-code).

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
