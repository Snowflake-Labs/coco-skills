---
name: pbi-bpa-dax-measures
description: DAX measures sub-skill for the Power BI Best Practices Analyzer.
parent_skill: powerbi-best-practices-analyzer
---

# DAX Measures Checks

Run all rules below against all measures across all tables. Append violations to `findings[]`.

---

## Category C: DAX Measures

**C1 — HIGH: Repeated Aggregation Logic Without Base Measures**

*Check:* Extract all `SUM(`, `COUNT(`, `AVERAGE(`, `MIN(`, `MAX(`, `DISTINCTCOUNT(` calls from all measure expressions. Find any raw aggregation sub-expression appearing in 3+ separate measures without a dedicated base measure that encapsulates it.

*Algorithm:*
```python
import re
agg_pattern = re.compile(r"(SUM|COUNT|AVERAGE|MIN|MAX|DISTINCTCOUNT)\s*\([^\)]+\)")
from collections import Counter
all_aggs = []
for t in tables:
    for m in t.get('measures', []):
        expr = ''.join(m.get('expression', []) if isinstance(m.get('expression'), list) else [m.get('expression', '')])
        all_aggs.extend(agg_pattern.findall(expr))
duplicates = [agg for agg, count in Counter(all_aggs).items() if count >= 3]
```

*Violation:* Repeated raw aggregations across measures with no base measure. Every change requires updating N measures.

*Fix:* Create dedicated base measures:
```
-- Base measure (create first):
Total Sales = SUM(Sales[Amount])

-- Then reference it everywhere:
Total Sales YTD = TOTALYTD([Total Sales], Date[Date])
Total Sales LY = CALCULATE([Total Sales], SAMEPERIODLASTYEAR(Date[Date]))
Sales Growth % = DIVIDE([Total Sales] - [Total Sales LY], [Total Sales LY])
-- NOT: Sales Growth % = DIVIDE(SUM(Sales[Amount]) - CALCULATE(SUM(Sales[Amount]),...), CALCULATE(SUM(Sales[Amount]),...))
```

---

**C2 — MEDIUM: IF/SWITCH Without .EAGER Variant**

*Check:* Measure expressions containing `IF(` or `SWITCH(` (case-insensitive, not already using `IF.EAGER` or `SWITCH.EAGER`).

*Violation:* Standard `IF`/`SWITCH` in DirectQuery mode generates one SQL query per branch. `IF.EAGER`/`SWITCH.EAGER` evaluates all branches in a single query.

*Fix:* Replace `IF(condition, a, b)` → `IF.EAGER(condition, a, b)` and `SWITCH(expr, v1,r1, v2,r2, else)` → `SWITCH.EAGER(expr, v1,r1, v2,r2, else)`.

Note: `.EAGER` variants may slightly increase query cost in Import mode but significantly reduce query count in DirectQuery mode.

---

**C3 — HIGH: Complex DAX Computed in Memory (Pre-aggregate in Snowflake)**

*Check:* Measures containing `CALCULATETABLE(`, `SUMMARIZE(`, `ADDCOLUMNS(`, `CROSSJOIN(`, `TOPN(`, or `GENERATE(`.

*Violation:* These functions force Power BI to pull large intermediate result sets into memory and process them locally — especially costly in DirectQuery mode.

*Fix:* Pre-aggregate in Snowflake using a DYNAMIC TABLE or MATERIALIZED VIEW:
```sql
-- Instead of SUMMARIZE(Sales, Customer[Region], "Total", SUM(Sales[Amount]))
CREATE OR REPLACE DYNAMIC TABLE DB.SCHEMA.AGG_SALES_BY_REGION
  TARGET_LAG = '1 hour'
  WAREHOUSE = MY_WH
AS
SELECT REGION, SUM(AMOUNT) AS TOTAL_SALES, COUNT(*) AS ORDER_COUNT
FROM DB.SCHEMA.FACT_SALES
GROUP BY REGION;
```

---

**C4 — MEDIUM: CALCULATE Used Excessively**

*Check:* Count the number of measures containing `CALCULATE(`. If >60% of all measures use `CALCULATE(`, flag.

*Violation:* Per Microsoft DirectQuery guidance, heavy use of `CALCULATE` generates expensive native queries that don't perform well. Signals that filter context manipulation could instead be handled in Snowflake views.

*Fix:* For common filter patterns, create pre-filtered Snowflake views:
```sql
-- Instead of CALCULATE([Sales], Region = "West")
CREATE OR REPLACE VIEW DB.SCHEMA.V_SALES_WEST AS
SELECT * FROM DB.SCHEMA.FACT_SALES WHERE REGION = 'West';
-- Power BI measure becomes simply: SUM(V_SALES_WEST[AMOUNT])
```

---

**C5 — MEDIUM: Measures Without Format Strings**

*Check:* Measures with empty or missing `formatString` field.

*Violation:* Unformatted measures display raw numbers — inconsistent UX, prone to misinterpretation.

*Fix:* Assign format strings based on measure name patterns:
- `amount`, `sales`, `revenue`, `cost`, `price` → `"$#,##0.00"`
- `pct`, `percent`, `rate`, `ratio` → `"0.00%"`
- `count`, `qty`, `quantity`, `num` → `"#,##0"`
- Generic numeric → `"#,##0.00"`

---

**C6 — MEDIUM: No Dedicated Measures Table**

*Check:* Measures are scattered across multiple dimension and fact tables (each with >1 table having measures).

*Violation:* Scattered measures reduce discoverability and make the model harder to maintain.

*Fix:* Create a hidden `_Measures` table in Power BI and move all measures there. In Snowflake, document this convention with a semantic view.

---

**C7 — MEDIUM: Time Intelligence in Power Query Instead of DAX**

*Check:* M expressions containing `Date.IsInCurrentYear(`, `Date.IsInPreviousYear(`, `Date.IsInCurrentMonth(`, `DateTime.LocalNow()`, or `#duration(`.

*Violation:* Per Microsoft DirectQuery guidance, Power Query relative date filters generate inefficient `CONVERT(datetime2, ...)` native queries instead of using the date table's relative columns.

*Fix:* Add `RELATIVE_YEAR` and `RELATIVE_MONTH` columns to the Snowflake date table (included in A2 DDL above). Then use DAX time intelligence:
```
-- Use DAX time intelligence against the date table, not M filters:
Sales YTD = TOTALYTD([Total Sales], Date[Date])
Sales LY = CALCULATE([Total Sales], SAMEPERIODLASTYEAR(Date[Date]))
```

---

**C8 — LOW: Implicit Measures on Numeric Columns**

*Check:* Numeric columns in fact tables that don't have a corresponding explicit measure. Infer by checking if `summarizeBy` is not `"none"` for numeric columns (ID columns, flag columns).

*Violation:* Auto-aggregation of numeric columns (e.g., summing an `ORDER_ID`) produces meaningless results and confuses report authors.

*Fix:* For fact columns that should be aggregated, create explicit measures. For ID/flag/code columns, set `summarizeBy = "none"` in Power BI (or recommend adding to the model with this property). Alternatively, in Snowflake ensure ID columns use `INTEGER` type (non-summarizable by convention).

---

**C9 — LOW: Measures Without Descriptions**

*Check:* Measures with empty or missing `description` field.

*Violation:* Undocumented measures lead to misuse and duplication.

*Fix:* Add descriptions to all measures. Mirror business logic documentation in Snowflake:
```sql
COMMENT ON COLUMN DB.SCHEMA.SEMANTIC_VIEW.TOTAL_SALES IS 'Sum of all invoice amounts. Excludes cancelled orders.';
```

---

**C10 — LOW: MEDIAN on Potentially High-Cardinality Data**

*Check:* Measures containing `MEDIAN(` or `MEDIANX(`.

*Violation:* Power BI does not push MEDIAN to Snowflake — it retrieves all detail rows and computes locally. For large tables this hits the 1M row limit and causes failures.

*Fix:* Pre-compute median in Snowflake:
```sql
-- Replace MEDIAN(Sales[Price]) with a Snowflake pre-computed value
CREATE OR REPLACE VIEW DB.SCHEMA.V_MEDIAN_PRICE AS
SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY PRICE) AS MEDIAN_PRICE
FROM DB.SCHEMA.FACT_SALES;
```

## Output

Return `findings[]` to the router (SKILL.md Step 3).
