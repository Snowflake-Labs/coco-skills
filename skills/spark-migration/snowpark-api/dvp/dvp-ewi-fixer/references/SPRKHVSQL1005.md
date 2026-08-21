# SPRKHVSQL1005

TBLPROPERTIES is not supported in Snowflake.

Message: TblProperty is not fully supported: < ***SQL Expression*** >.

Category: Conversion Error.

## Description

This error message appears when an Alter Table or an Alter View tries to SET or UNSET Table Properties using TBLPROPERTIES.

## Scenarios

The Snowflake platform does not support the use of TBLPROPERTIES in ALTER TABLE or ALTER VIEW statements. While Snowflake provides alternative mechanisms for managing metadata, some use cases involving TBLPROPERTIES require manual intervention during the conversion process. Below are some examples of scenarios where manual modifications are needed to ensure compatibility with Snowflake.

### Scenario 1

**Input**

Below is an example of an Alter Table or an Alter View tries that SETs Table Properties using TBLPROPERTIES.

```SQL
ALTER TABLE DUMMY_TABLE
SET TBLPROPERTIES ('IsNested'= 'True');
```

**Output**

The SMA adds the EWI `SPRKHVSQL1005` to the output code to let you know that TblProperty is not fully supported.

```SQL
---- Alter Table comment 0
----EWI: SPRKHVSQL1005 => TblProperty is not fully supported. ALTER TABLE DUMMY_TABLE SET TBLPROPERTIES ('IsNested' = 'True');
--ALTER TABLE DUMMY_TABLE
--SET TBLPROPERTIES ('IsNested'= 'True');
```

**Recommended fix**

Snowflake does not support the TBLPROPERTIES clause, so you should remove it entirely. If the IsNested property was used to indicate a special table structure or nested data handling, you might need to adjust the table schema or logic accordingly. In Snowflake, you don’t need the TBLPROPERTIES statement for such purposes.

### Scenario 2

**Input**

Below is an example of an Alter Table or an Alter View tries that SETs Table Properties using TBLPROPERTIES with comment.

```SQL
ALTER TABLE DUMMY SET TBLPROPERTIES ("comment" = "test","id" = "id");
```

**Output**

The SMA adds the EWI `SPRKHVSQL1005` to the output code to let you know that TblProperty is not fully supported.

```SQL
--EWI: SPRKHVSQL1005 => TblProperty is not fully supported. ALTER TABLE DUMMY SET TBLPROPERTIES ("comment" = "test", "id" = "id");
ALTER TABLE DUMMY
  SET COMMENT = 'test';
```

**Recommended fix**

Snowflake supports the COMMENT property for tables, so you should use the SET COMMENT syntax to set descriptive metadata like comments. The other TBLPROPERTIES like "id" = "id" do not have direct equivalents in Snowflake, so they should be omitted. If you need to store additional properties, you may consider using custom columns or a metadata table to store them.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
