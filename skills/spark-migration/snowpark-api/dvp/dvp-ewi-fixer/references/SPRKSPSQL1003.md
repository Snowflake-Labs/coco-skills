# SPRKSPSQL1003

Name expression is not supported.

Message: The name expression < ***Name Expression*** > is currently not supported.

Category: Error.

## Description

This error message appears when a name with backticks contains an invalid expression (e.g., a function call) as a data source.

## Scenario

**Input**

Below is an example of a statement that contains backticks in the name.

```SQL
SELECT * FROM `SomeDataSource()`;
```

**Output**

The SMA adds the EWI `SPRKSPSQL1003` to the output code to let you know that a name with backticks is not supported.

```SQL
SELECT * FROM
  --EWI: SPRKSPSQL1003 => The expression `SomeDataSource()` is not currently supported.
  `SomeDataSource()`;
```

**Recommended fix**

Modify the SQL to avoid using expressions or function calls inside the backticks. Ensure that the name refers to an actual, valid table or view name without dynamic function calls.

## Additional recommendations

- Expressions as tables and view names, are supported.
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
