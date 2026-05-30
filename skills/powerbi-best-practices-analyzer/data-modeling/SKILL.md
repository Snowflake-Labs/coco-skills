---
name: pbi-bpa-data-modeling
description: Data modeling and relationships sub-skill for the Power BI Best Practices Analyzer.
parent_skill: powerbi-best-practices-analyzer
---

# Data Modeling & Relationships Checks

Run all rules below. For each violation, append to findings[]:
`{id, severity, affected: [table/column names], description, fix_summary, sql_ddl}`

---

## Category A: Data Modeling (Snowflake-First)

**A1 — CRITICAL: Logic in Power Query Instead of Snowflake**

*Check:* M expression contains any of: `Table.AddColumn`, `Table.SelectRows`, `Table.Group`, `Table.Join`, `Table.NestedJoin`, `Table.TransformColumns`, or complex string/date functions.

*Violation:* Data transformation logic that should live in Snowflake is embedded in the Power BI model.

*Fix:* Create a Snowflake VIEW or DYNAMIC TABLE implementing the same logic:
```sql
CREATE OR REPLACE VIEW DB.SCHEMA.V_TABLE AS
SELECT *, COL_A || COL_B AS FK_KEY  -- replaces Table.AddColumn
FROM DB.SCHEMA.SOURCE_TABLE
WHERE STATUS = 'Active';            -- replaces Table.SelectRows
```

---

**A2 — HIGH: Calculated Tables (DAX) Should Be Physical Snowflake Tables**

*Check:* `partitions[].source.type == "calculated"` on any table.

*Violation:* DAX-computed tables consume memory and slow model refresh.

*Fix for date/calendar tables:*
```sql
CREATE OR REPLACE TABLE DB.SCHEMA.DIM_DATE AS
SELECT
    DATEADD(DAY, SEQ4(), '2015-01-01'::DATE) AS DATE,
    YEAR(DATEADD(DAY, SEQ4(), '2015-01-01'::DATE)) AS YEAR,
    MONTH(DATEADD(DAY, SEQ4(), '2015-01-01'::DATE)) AS MONTH_NUM,
    TO_CHAR(DATEADD(DAY, SEQ4(), '2015-01-01'::DATE), 'MMMM') AS MONTH_NAME,
    QUARTER(DATEADD(DAY, SEQ4(), '2015-01-01'::DATE)) AS QUARTER_NUM,
    YEAR(DATEADD(DAY, SEQ4(), '2015-01-01'::DATE)) * 100 + QUARTER(DATEADD(DAY, SEQ4(), '2015-01-01'::DATE)) AS YEAR_QUARTER,
    CASE WHEN DAYOFWEEK(DATEADD(DAY, SEQ4(), '2015-01-01'::DATE)) IN (1,7) THEN TRUE ELSE FALSE END AS WEEKEND_FLAG,
    YEAR(DATEADD(DAY, SEQ4(), '2015-01-01'::DATE)) - YEAR(CURRENT_DATE()) AS RELATIVE_YEAR,
    MONTH(DATEADD(DAY, SEQ4(), '2015-01-01'::DATE)) - MONTH(CURRENT_DATE()) AS RELATIVE_MONTH
FROM TABLE(GENERATOR(ROWCOUNT => 7305))
WHERE DATEADD(DAY, SEQ4(), '2015-01-01'::DATE) <= DATE_FROM_PARTS(YEAR(CURRENT_DATE())+10,12,31);
```
Note: `RELATIVE_YEAR` / `RELATIVE_MONTH` columns replace the need for Power Query relative date filtering.

---

**A3 — HIGH: Calculated Columns (DAX) Should Live in Snowflake**

*Check:* Any column with a non-empty `expression` field in DataModelSchema.

*Violation:* DAX calculated columns are rebuilt on every refresh and compress less efficiently than source columns.

*Fix:* Translate the DAX expression to SQL in a Snowflake VIEW. Common translations:
- `YEAR([Date])` → `YEAR(date_col)`
- `FORMAT([Date], "YYYY-MM")` → `TO_CHAR(date_col, 'YYYY-MM')`
- `IF([Qty]>0,"Y","N")` → `CASE WHEN qty > 0 THEN 'Y' ELSE 'N' END`
- `RELATED(Dim[Name])` → JOIN in view
- `DATEDIFF("day",[Start],[End])` → `DATEDIFF('day', start_col, end_col)`
- `CONCATENATE([A],[B])` → `A || B`
- `LEFT([Col],3)` → `LEFT(col, 3)`

---

**A4 — MEDIUM: Wide Flat Tables (Not Star Schema)**

*Check:* Fact tables with >30 columns, or tables mixing dimensional attributes with numeric measures.

*Violation:* Wide tables miss Snowflake's columnar compression advantages and slow Power BI's VertiPaq engine.

*Fix:* Decompose into star schema in Snowflake. Create dimension views:
```sql
CREATE OR REPLACE VIEW DB.SCHEMA.DIM_PRODUCT AS
SELECT DISTINCT PRODUCT_ID, PRODUCT_NAME, CATEGORY, SUBCATEGORY FROM DB.SCHEMA.FLAT_FACT;

CREATE OR REPLACE VIEW DB.SCHEMA.FACT_SALES AS
SELECT ORDER_ID, PRODUCT_ID, CUSTOMER_ID, DATE_ID, AMOUNT, QUANTITY FROM DB.SCHEMA.FLAT_FACT;
```

---

**A5 — MEDIUM: Unnecessary Columns Loaded**

*Check:* Columns not referenced in any relationship, measure expression, or visible hierarchy.

*Violation:* Every extra column occupies memory in the VertiPaq engine; unused columns provide no analytical value.

*Fix:* Either remove from the Power BI model, or create a Snowflake view that excludes them:
```sql
CREATE OR REPLACE VIEW DB.SCHEMA.V_SLIM_TABLE AS
SELECT NEEDED_COL1, NEEDED_COL2, NEEDED_COL3  -- only include necessary columns
FROM DB.SCHEMA.FULL_TABLE;
```

---

**A6 — LOW: No Primary Keys Defined**

*Check:* Tables missing surrogate key columns (`*_ID`, `*_KEY`, `*_SK`).

*Violation:* Relationships without PK constraints allow duplicates and produce incorrect aggregations.

*Fix:*
```sql
ALTER TABLE DB.SCHEMA.DIM_CUSTOMER ADD PRIMARY KEY (CUSTOMER_ID);
-- Or ensure uniqueness with a UNIQUE constraint
ALTER TABLE DB.SCHEMA.DIM_CUSTOMER ADD CONSTRAINT uq_customer UNIQUE (CUSTOMER_ID);
```

---

**A7 — MEDIUM: Columns with Excessive Numeric Precision**

*Check:* Columns with `dataType == "double"` — inspect name for type hints:
- Currency (`amount`,`price`,`cost`,`revenue`,`total`,`value`,`balance`) → flag, recommend `NUMBER(18,2)`
- Integer-like (`id`,`count`,`qty`,`num`,`flag`,`year`,`month`,`day`,`age`,`rank`) → flag, recommend `INTEGER`
- Percentage (`pct`,`percent`,`ratio`,`rate`,`share`) → flag, recommend `NUMBER(10,4)`

*Violation:* `double` (64-bit float) wastes memory and causes floating-point precision issues in aggregations.

*Fix:*
```sql
CREATE OR REPLACE VIEW DB.SCHEMA.V_ORDERS AS
SELECT
    ORDER_ID::INTEGER           AS ORDER_ID,
    CUSTOMER_ID::INTEGER        AS CUSTOMER_ID,
    ROUND(UNIT_PRICE,2)::NUMBER(18,2) AS UNIT_PRICE,
    QUANTITY::INTEGER           AS QUANTITY
FROM DB.SCHEMA.ORDERS;
-- Also consider: ODBC_TREAT_DECIMAL_AS_INT = TRUE for integer-valued DECIMAL columns
```

---

**A8 — HIGH: Auto Date/Time Tables Hidden in Model**

*Check:* Calculated tables whose name matches pattern `DateTableTemplate_*` or `LocalDateTable_*`, OR any calculated table expression containing `CALENDAR(` or `CALENDARAUTO(` and no corresponding physical date table exists.

*Violation:* Power BI auto date/time creates a hidden calculated table for every date column — can double model size.

*Fix:* Disable Auto date/time in Power BI Desktop (File → Options → Data Load → uncheck "Auto date/time"). Create a single physical date table in Snowflake (see A2 DDL above).

---

**A9 — MEDIUM: High-Cardinality Text Column Used as Relationship Key**

*Check:* Relationship `fromColumn` or `toColumn` where the column `dataType == "string"` and column name suggests a code or identifier (`code`, `key`, `num`, `id`, `sku`, `ref`).

*Violation:* String-type join keys prevent hash encoding optimization and generate larger, slower queries.

*Fix:* Add an integer surrogate key in Snowflake:
```sql
ALTER TABLE DB.SCHEMA.DIM_PRODUCT ADD COLUMN PRODUCT_SK INTEGER AUTOINCREMENT;
-- Or use HASH to create a stable integer key
ALTER TABLE DB.SCHEMA.DIM_PRODUCT ADD COLUMN PRODUCT_SK INTEGER
    DEFAULT HASH(PRODUCT_CODE)::INTEGER;
```

---

**A10 — MEDIUM: GUID/UUID Columns in Relationships**

*Check:* Relationship columns where name contains `guid`, `uuid`, or column name is exactly one of the relationship keys and `dataType == "string"` with 36-char format patterns.

*Violation:* Power BI generates `CAST` operations on GUID joins — causes poor DirectQuery performance.

*Fix:* Materialize an integer surrogate key in Snowflake and use that as the join key instead.

---

**A11 — LOW: Primary Key Columns Not Hidden**

*Check:* The one-side (PK) column of each relationship is visible (no `isHidden: true` flag).

*Violation:* ID/key columns surfaced to report authors cause confusion and misuse (e.g., summing IDs).

*Fix:* In Power BI, hide PK columns on dimension tables. Set `summarizeBy = "none"` on all ID columns.

---

**A12 — LOW: Missing Table and Column Descriptions**

*Check:* Tables or columns with empty/missing `description` field.

*Violation:* Undescribed fields reduce discoverability and lead to misuse.

*Fix:* Add descriptions in Power BI. Mirror documentation in Snowflake:
```sql
COMMENT ON COLUMN DB.SCHEMA.TABLE.COLUMN IS 'Description of what this column represents';
COMMENT ON TABLE DB.SCHEMA.TABLE IS 'Description of this table';
```

---

## Category B: Relationships

**B1 — HIGH: Bi-Directional Relationships**

*Check:* `crossFilteringBehavior == "bothDirections"`.

*Violation:* Each bi-directional filter doubles the number of SQL queries generated per visual interaction.

*Fix:* Switch to single-direction. If many-to-many is the root cause, resolve with a Snowflake bridge table:
```sql
CREATE OR REPLACE TABLE DB.SCHEMA.BRIDGE_PRODUCT_CATEGORY AS
SELECT DISTINCT PRODUCT_ID, CATEGORY_ID FROM DB.SCHEMA.FACT_SALES;
```

---

**B2 — HIGH: Many-to-Many Relationships**

*Check:* Relationship cardinality is not one-to-many (infer: FK column has duplicates relative to PK).

*Violation:* Many-to-many causes row duplication (fan-out) and incorrect aggregations.

*Fix:* Resolve in Snowflake with a bridge/junction table or by deduplicating the dimension.

---

**B3 — MEDIUM: Inactive Relationships**

*Check:* `isActive == false`.

*Violation:* Every inactive relationship requires `USERELATIONSHIP()` in each relevant measure — complexity debt and extra SQL queries.

*Fix:* Consider materializing a separate Snowflake view per relationship path, or redesign to eliminate ambiguity.

---

**B4 — LOW: Referential Integrity Not Set**

*Check:* DirectQuery model detected (all M sources are Snowflake). Note as general recommendation.

*Fix:* Enable "Assume Referential Integrity" on all DirectQuery relationships where Snowflake FK integrity is enforced. Forces `INNER JOIN` instead of `LEFT OUTER JOIN` — faster queries.

---

**B5 — MEDIUM: Relationships on Calculated/Derived Columns**

*Check:* Relationship `fromColumn` or `toColumn` matches a column that has an `expression` field (calculated column).

*Violation:* Per Microsoft guidance: relationships on calculated columns embed expressions into every source query and prevent index usage.

*Fix:* Materialize the concatenated/derived key in Snowflake as a physical column, then join on that.

## Output

Return `findings[]` array to the router (SKILL.md Step 3).
