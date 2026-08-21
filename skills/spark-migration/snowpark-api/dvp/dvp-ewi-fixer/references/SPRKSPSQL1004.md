# SPRKSPSQL1004

Binary expression has no equivalence.

Message: Pending functional equivalence for binary expression.

Category: Warning.

## Description

Currently there is no functional equivalence for a binary expression.

## Scenario

**Input**

Below is an example of a statement that uses a binary expression.

```SQL
SELECT 10 % 2;
```

**Output**

The SMA adds the EWI `SPRKSPSQL1004` to the output code to let you know that the binary expression is not supported.

```SQL
SELECT
--EWI: SPRKSPSQL1004 => Pending functional equivalence for binary expression
10 % 2;
```

**Recommended fix**

- If the binary operation is unsupported, you should look for equivalent logic in Snowflake's SQL functions or rewrite the logic using supported operations.
- Consider if the specific binary operation is essential or if it can be replaced with another logic in Snowflake SQL

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
