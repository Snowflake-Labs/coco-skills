---
name: pbi-bpa-power-query
description: Power Query (M), connector, and storage mode sub-skill for the Power BI Best Practices Analyzer.
parent_skill: powerbi-best-practices-analyzer
---

# Power Query, Connector & Storage Mode Checks

Run all rules below against M expressions and connection settings. Append violations to `findings[]`.

---

## Category D: Power Query, Connector & Storage

**D1 — CRITICAL: Data Transformation Logic in Power Query**

*Check:* M expressions containing any transformation beyond a simple table reference:
- `Table.AddColumn(` — computed columns
- `Table.SelectRows(` or `Table.SelectColumns(` — row/column filtering
- `Table.Group(` — aggregations
- `Table.Join(` or `Table.NestedJoin(` — joins
- `Table.RenameColumns(`, `Table.TransformColumnTypes(` — structural changes
- `Text.`, `Date.`, `Number.` function calls — value transformations

*Violation:* Per Snowflake and Microsoft guidance, all data preparation should happen upstream in Snowflake. Logic in Power Query is brittle, unmaintainable, and prevents query folding in DirectQuery mode.

*Fix:* For each transform detected, generate the Snowflake equivalent:

| M Pattern | Snowflake Fix |
|-----------|---------------|
| `Table.AddColumn(..., each [A] & [B])` | `SELECT *, A \|\| B AS NEW_COL FROM ...` |
| `Table.SelectRows(..., each [Status] = "Active")` | `CREATE VIEW ... AS SELECT * WHERE STATUS = 'Active'` |
| `Table.Group(..., {"Col"}, {{"Total", each List.Sum([Amt])}})` | `CREATE DYNAMIC TABLE ... AS SELECT COL, SUM(AMT) FROM ... GROUP BY COL` |
| `Table.Join(A, "ID", B, "ID")` | Implement join in a Snowflake VIEW |

```sql
-- Example: replace complex M transforms with a clean Snowflake view
CREATE OR REPLACE VIEW DB.SCHEMA.V_TRANSFORMED AS
SELECT
    ORDER_ID::INTEGER AS ORDER_ID,
    CUSTOMER_ID || '-' || SOURCE AS FK_KEY,   -- replaces AddColumn concat
    UPPER(TRIM(PRODUCT_NAME)) AS PRODUCT_NAME, -- replaces Text transforms
    ROUND(AMOUNT, 2) AS AMOUNT
FROM DB.SCHEMA.RAW_TABLE
WHERE STATUS = 'Active';                        -- replaces SelectRows
```

---

**D2 — HIGH: Relative Date Filtering in Power Query**

*Check:* M expressions containing `Date.IsInCurrentYear(`, `Date.IsInPreviousYear(`, `Date.IsInCurrentMonth(`, `Date.IsInCurrentQuarter(`, `DateTime.LocalNow()`, `#duration(`, or `Date.From(DateTime.LocalNow())`.

*Violation:* Per Microsoft DirectQuery guidance, Power Query relative date filters translate to inefficient native queries using `CONVERT(datetime2, ...)` with hard-coded date literals that are not index-friendly.

*Fix:* Add relative time columns to the Snowflake date table (replace hard-coded date filtering):
```sql
-- Add to DIM_DATE table or view:
ALTER TABLE DB.SCHEMA.DIM_DATE ADD COLUMN RELATIVE_YEAR INTEGER;
ALTER TABLE DB.SCHEMA.DIM_DATE ADD COLUMN RELATIVE_MONTH INTEGER;
ALTER TABLE DB.SCHEMA.DIM_DATE ADD COLUMN IS_CURRENT_YEAR BOOLEAN;
ALTER TABLE DB.SCHEMA.DIM_DATE ADD COLUMN IS_CURRENT_MONTH BOOLEAN;
ALTER TABLE DB.SCHEMA.DIM_DATE ADD COLUMN IS_CURRENT_QTR BOOLEAN;

UPDATE DB.SCHEMA.DIM_DATE SET
    RELATIVE_YEAR = YEAR(DATE) - YEAR(CURRENT_DATE()),
    RELATIVE_MONTH = DATEDIFF('month', CURRENT_DATE(), DATE),
    IS_CURRENT_YEAR = (YEAR(DATE) = YEAR(CURRENT_DATE())),
    IS_CURRENT_MONTH = (DATE_TRUNC('month', DATE) = DATE_TRUNC('month', CURRENT_DATE())),
    IS_CURRENT_QTR = (DATE_TRUNC('quarter', DATE) = DATE_TRUNC('quarter', CURRENT_DATE()));
```
Then filter in Power BI using these columns with DAX: `CALCULATE([Sales], DimDate[IS_CURRENT_YEAR] = TRUE)`.

---

**D3 — HIGH: Custom SQL Hard-Coded in Power Query**

*Check:* M expressions containing `Value.NativeQuery(`, `Query = "SELECT`, or `Sql.Database(` with a `Query` parameter.

*Violation:* Custom SQL embeds logic inside the dataset definition — brittle, duplicates logic, hard to maintain, prevents query folding.

*Fix:* Move SQL into a Snowflake VIEW or DYNAMIC TABLE:
```sql
-- Move this out of Power BI:
-- Query = "SELECT o.*, c.NAME FROM ORDERS o JOIN CUSTOMERS c ON o.CUST_ID = c.ID WHERE o.STATUS = 'Shipped'"
-- Into Snowflake:
CREATE OR REPLACE VIEW DB.SCHEMA.V_SHIPPED_ORDERS AS
SELECT o.*, c.NAME AS CUSTOMER_NAME
FROM DB.SCHEMA.ORDERS o
JOIN DB.SCHEMA.CUSTOMERS c ON o.CUST_ID = c.ID
WHERE o.STATUS = 'Shipped';
-- Power BI connects directly to V_SHIPPED_ORDERS
```

---

**D4 — MEDIUM: Aggregation (Group By) in Power Query**

*Check:* M expressions containing `Table.Group(`.

*Violation:* Power Query aggregations are computed in Power BI memory on every refresh. For large tables this is slow and wasteful — Snowflake can do this more efficiently at source.

*Fix:* Use a Snowflake DYNAMIC TABLE for automated refresh, or a MATERIALIZED VIEW for static aggregation:
```sql
CREATE OR REPLACE DYNAMIC TABLE DB.SCHEMA.AGG_DAILY_SALES
  TARGET_LAG = '1 hour'
  WAREHOUSE = REPORTING_WH
AS
SELECT
    DATE_TRUNC('day', ORDER_DATE) AS ORDER_DAY,
    PRODUCT_ID,
    REGION,
    SUM(AMOUNT) AS TOTAL_SALES,
    COUNT(*) AS ORDER_COUNT,
    SUM(QUANTITY) AS TOTAL_QTY
FROM DB.SCHEMA.FACT_ORDERS
GROUP BY 1, 2, 3;
```

---

**D5 — MEDIUM: Generic ODBC Connector Instead of Native Snowflake**

*Check:* M expressions containing `Odbc.DataSource(` or `Odbc.Query(`.

*Violation:* Generic ODBC bypasses Snowflake-specific connector optimizations: Arrow/ADBC fast transfers, query folding, Snowflake query tags, and the new ADBC connector improvements.

*Fix:* Replace with native Snowflake connector:
```
// Replace:
Odbc.DataSource("dsn=MySnowflakeDSN", ...)
// With:
Snowflake.Databases("account.snowflakecomputing.com", "MY_WH", [Role="MY_ROLE"])
```

---

**D6 — LOW: Non-Snowflake Data Sources Mixed In**

*Check:* M expressions NOT containing `Snowflake.Databases(` (Excel, SharePoint, SQL Server, OData, etc.).

*Violation:* Mixed sources prevent full DirectQuery optimization, complicate governance, and fragment data lineage.

*Fix:* Land non-Snowflake data into Snowflake first using:
- Snowflake connectors (e.g., Fivetran, dbt, Snowpipe)
- External stages for file-based sources
- Snowflake's native Excel/CSV ingestion
Then connect Power BI to a single unified Snowflake source.

---

**D7 — LOW: Include Relationship Columns Enabled**

*Check:* Detect Snowflake connections and note as a general recommendation (not directly in DataModelSchema).

*Violation:* "Include relationship columns" in the Snowflake connector advanced options causes excessive metadata queries, slowing initial model load and refresh.

*Fix:* Uncheck "Include relationship columns" in Power Query → Snowflake connector → Advanced Options. This setting is especially impactful for accounts with many objects.

---

**D8 — MEDIUM: Large Tables Fully Loaded in Import Mode**

*Check:* Fact tables (identified by having >5 numeric columns and being referenced by multiple relationships) present in Import-mode model without visible row filtering or aggregation in M.

*Violation:* Per Microsoft guidance, large fact tables should use DirectQuery storage mode (or be pre-aggregated). Full Import of large fact tables causes slow refresh and high memory usage.

*Fix:* Switch large fact tables to DirectQuery, keep dimension tables in Import mode (Composite Model pattern). Or pre-summarize in Snowflake:
```sql
CREATE OR REPLACE DYNAMIC TABLE DB.SCHEMA.AGG_FACT_MONTHLY
  TARGET_LAG = 'downstream'
  WAREHOUSE = MY_WH
AS
SELECT DATE_TRUNC('month', ORDER_DATE) AS MONTH, PRODUCT_ID, REGION,
       SUM(AMOUNT) AS SALES, COUNT(*) AS ORDERS
FROM DB.SCHEMA.FACT_SALES
GROUP BY 1, 2, 3;
```

## Output

Return `findings[]` to the router (SKILL.md Step 3).
