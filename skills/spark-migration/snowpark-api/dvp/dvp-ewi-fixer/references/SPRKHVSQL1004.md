# SPRKHVSQL1004

Underlying data can not be recovered.

Message: Information from underlying data files can not be recovered.

Category: Warning.

## Description

This error message appears when DROP TABLE does not contain the purge clause, because there can be slightly differences between the functionality of this clause in Hive and Snowflake.

## Scenario

**Input**

Below is an example of a DROP TABLE that does not contain the purge clause.

```SQL
DROP TABLE table1;
```

**Output**

The SMA adds the EWI `SPRKHVSQL1004` to the output code to let you know that the underlying data files can not be recovered.

```SQL
--EWI: SPRKHVSQL1004 => Information from underlying data files can not be recovered. ***/
DROP TABLE table1;
```

**Recommended fix**

If you do not want to permanently delete your data and prefer to keep it for potential recovery or future use, you can omit the `PURGE` clause in the `DROP TABLE` statement.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
