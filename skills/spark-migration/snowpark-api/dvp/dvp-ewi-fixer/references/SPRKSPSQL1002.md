# SPRKSPSQL1002

SQL statement is not supported.

Message: < ***Element Name*** > is not supported.

Category: Error.

## Description

This error message appears when a statement is not supported in Snowflake.

## Scenario

**Input**

Below is an example of an element that is not supported in Snowflake.

```SQL
ANALYZE TABLE students COMPUTE STATISTICS;
```

**Output**

The SMA adds the EWI `SPRKSPSQL1002` to the output code to let you know that the element is not supported in Snowflake.

```SQL
----EWI: SPRKSPSQL1002 => SparkAnalyzeTable is not supported
--ANALYZE TABLE
------EWI: SPRKSPSQL1002 => SparkAnalyzeTable.SqlName is not supported
--students
------EWI: SPRKSPSQL1002 => SparkAnalyzeTable.SparkComputeStatistics is not supported
--COMPUTE STATISTICS
;
```

**Recommended fix**

Check if the source code has the correct syntax. (You can use the issues.csv file to determine where the conversion error(s) are occurring.) If the syntax is correct, report that you encountered a conversion error on a particular element using the Report an Issue option in the SMA. Include the line of code that was causing the error in the description when you file this issue.

## Additional recommendations
- Even though the option or the element on the message is not supported, this does not mean that a solution cannot be found. It only means that the tool itself cannot find the solution.
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
