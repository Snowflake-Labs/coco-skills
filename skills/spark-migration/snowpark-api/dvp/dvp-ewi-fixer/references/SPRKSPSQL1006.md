# SPRKSPSQL1005

The element is not mapped yet.

Message: The element < ***Element Name*** > is not mapped yet.

Category: Conversion Error.

## Description

The EWI appears when the tool detects an element that is not yet in the mappings table.

## Scenario

**Input**

Below is an example of an element that is not yet in the mappings table.

```SQL
CREATE FUNCTION blue()
  RETURNS STRING
  COMMENT 'Blue color code'
LANGUAGE SQL
RETURN '0000FF';
```

**Output**

The SMA adds the EWI `SPRKSPSQL1006` to the output code to let you know that the element that is not yet in the mapping table.

```SQL
CREATE FUNCTION blue()
  RETURNS STRING
  COMMENT =
          --EWI: SPRKSPSQL1006 =>  The element SqlCreateFunction.SqlCommentClause.SqlLiteralExpr is not mapped yet.
          'Blue color code'
--EWI: SPRKSPSQL1006 =>  The element SqlCreateFunction.SqlLanguageClause is not mapped yet.
LANGUAGE
         --EWI: SPRKSPSQL1006 =>  The element SqlCreateFunction.SqlLanguageClause.SqlSpecificLanguage is not mapped yet.
         SQL
RETURN
       --EWI: SPRKSPSQL1006 =>  The element SqlReturnStatement.SqlLiteralExpr is not mapped yet.
       '0000FF';
```

**Recommended fix**

Since the element is not yet mapped, you should search for a corresponding feature or syntax in Snowflake and adjust the SQL code accordingly.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
