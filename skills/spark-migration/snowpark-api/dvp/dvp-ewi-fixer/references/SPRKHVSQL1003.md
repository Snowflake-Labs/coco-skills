# SPRKHVSQL1003

Unsupported SET statement in Snowflake.

Message: Snowflake does not support the following command.

Category: Warning.

## Description

This error message appears when there is an invalid SET declaration in Snowflake.

## Scenario

**Input**

Below is an example of an invalid SET declaration.

```SQL
SET hive.groupby.orderby.position.alias = true;
```

**Output**

The SMA adds the EWI `SPRKHVSQL1003` to the output code to let you know that the SET statement is invalid in Snowflake.

```SQL
--EWI: SPRKHVSQL1003 => Snowflake does not support the following command
SET hive.groupby.orderby.position.alias = true;
```

### **Recommended Fix**

- Snowflake generally handles query behavior (such as ordering or aliasing) implicitly, without requiring explicit configuration. Therefore, the `SET` statement may not be necessary.
- Since Snowflake does not support this `SET` statement, you can safely remove it.
- If the original `SET` statement was intended to control query behavior (like ordering or aliasing), replicate this functionality directly in your query (e.g., using `ORDER BY`, `GROUP BY`, or explicit aliasing within the query).

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
