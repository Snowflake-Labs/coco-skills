# SPRKSPSQL1006

The element is not mapped yet.

Message: The element < ***Element Name*** > is not mapped yet.

Category: Conversion Error.

## Description

The EWI appears when the tool detects an element that is not yet in the mappings table.

## Scenario

**Input**

Below is an example of an element that is not yet in the mappings table.

```SQL
create external table if not exists table12
(
product                 string,
date_ac                string,
asofdate                 int
)
partitioned by (asofdate int)
;
```

**Output**

The SMA adds the EWI `SPRKSPSQL1006` to the output code to let you know that the element that is not yet in the mapping table.

```SQL
--EWI: SPRKSPSQL1006 =>  The element SqlCreateExternalTable.SqlCreateExternalTableStart is not mapped yet.
--EWI: SPRKSPSQL1006 =>  The element SqlCreateExternalTable is not mapped yet.
create external table
                      --EWI: SPRKSPSQL1006 =>  The element SqlCreateExternalTable.SqlCreateExternalTableStart.SqlIfNotExists is not mapped yet.
                      if not exists
                                    --EWI: SPRKSPSQL1006 =>  The element SqlCreateExternalTable.SqlName is not mapped yet.
                                    table12
--EWI: SPRKSPSQL1006 =>  The element SqlCreateExternalTable.SqlTableElementList is not mapped yet.
(
   --EWI: SPRKSPSQL1006 =>  The element SqlCreateExternalTable.SqlTableElementList.SqlColumnDefinition.SqlName is not mapped yet.
   --EWI: SPRKSPSQL1006 =>  The element SqlCreateExternalTable.SqlTableElementList.SqlColumnDefinition is not mapped yet.
product                 string,
   --EWI: SPRKSPSQL1006 =>  The element SqlCreateExternalTable.SqlTableElementList.SqlColumnDefinition.SqlName is not mapped yet.
   --EWI: SPRKSPSQL1006 =>  The element SqlCreateExternalTable.SqlTableElementList.SqlColumnDefinition is not mapped yet.
date_ac                string,
   --EWI: SPRKSPSQL1006 =>  The element SqlCreateExternalTable.SqlTableElementList.SqlColumnDefinition.SqlName is not mapped yet.
   --EWI: SPRKSPSQL1006 =>  The element SqlCreateExternalTable.SqlTableElementList.SqlColumnDefinition is not mapped yet.
asofdate                 int
)
--partitioned by (asofdate int)
;
```

**Recommended fix**

Since the element is not yet mapped, you should search for a corresponding feature or syntax in Snowflake and adjust the SQL code accordingly.


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
