# SPRKSPSQL1001

Unrecognized token.

Message: Unrecognized token on line < ***line number*** > column < ***column number*** > of the source code starting at < ***character value*** >.

Category: Error.

## Description

This error message appears when a statement cannot be recognized by the migrator.

## Scenario

**Input**

Below is an example of a statement that cannot be recognized by the migrator.

```SQL
// Intentional Parsing Error
CREATE TABLE MY_TABLE (COL1 INT ;
```

**Output**

The SMA adds the EWI `SPRKSPSQL1001` to the output code to let you know that there is an unrecognized token.

```SQL
-- EWI: SPRKSPSQL1001 => unrecognized token on line 1 column 1 of the source code starting at '/'
--CREATE TABLE MY_TABLE (COL1 INT
;
```

**Recommended fix**

- Verify if the statement compiles in the source's database engine.
- Correct the syntax by ensuring that all the code follows the correct format.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
