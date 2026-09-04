---
name: pbi-bpa-performance-security
description: DirectQuery performance, RLS, and report design sub-skill for the Power BI Best Practices Analyzer.
parent_skill: powerbi-best-practices-analyzer
---

# Performance, Security & Report Design Checks

Run all rules below. Append violations to `findings[]`.

---

## Category E: Performance, Security & Report Design

**E1 — MEDIUM: Row-Level Security Defined in Power BI**

*Check:* `model.roles` array is non-empty.

*Violation:* RLS defined in Power BI duplicates security logic that belongs in Snowflake. With Power BI RLS, data is fetched first and then filtered — Snowflake row access policies filter at the database level, which is more efficient and centralized.

*Fix:* Move RLS to Snowflake:
```sql
-- Row Access Policy (filter by current user's region)
CREATE OR REPLACE ROW ACCESS POLICY DB.SCHEMA.RAP_BY_REGION
AS (REGION VARCHAR) RETURNS BOOLEAN ->
    CURRENT_USER() IN (
        SELECT USERNAME FROM DB.SCHEMA.USER_REGION_MAP WHERE REGION = REGION
    );

ALTER TABLE DB.SCHEMA.FACT_SALES ADD ROW ACCESS POLICY DB.SCHEMA.RAP_BY_REGION ON (REGION);

-- Column Masking Policy (mask PII for non-privileged roles)
CREATE OR REPLACE MASKING POLICY DB.SCHEMA.MASK_EMAIL AS (VAL VARCHAR) RETURNS VARCHAR ->
    CASE WHEN CURRENT_ROLE() = 'ANALYST_ROLE' THEN VAL
         ELSE '***MASKED***' END;

ALTER TABLE DB.SCHEMA.DIM_CUSTOMER MODIFY COLUMN EMAIL SET MASKING POLICY DB.SCHEMA.MASK_EMAIL;
```
Enable SSO between Power BI and Snowflake so Snowflake policies auto-enforce per user.

---

**E2 — HIGH: MEDIAN on Potentially Large Tables**

*Check:* Any measure containing `MEDIAN(` or `MEDIANX(` applied to a fact table (a table with many rows, indicated by being the source of multiple relationship many-sides).

*Violation:* MEDIAN is not pushed to Snowflake — Power BI retrieves ALL detail rows and computes locally. For large tables this causes 1M-row limit failures or extreme memory pressure.

*Fix:* Pre-compute in Snowflake using `PERCENTILE_CONT`:
```sql
CREATE OR REPLACE VIEW DB.SCHEMA.V_MEDIAN_METRICS AS
SELECT
    PRODUCT_ID,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY UNIT_PRICE) AS MEDIAN_UNIT_PRICE,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY QUANTITY)   AS MEDIAN_QUANTITY
FROM DB.SCHEMA.FACT_SALES
GROUP BY PRODUCT_ID;
```
Reference this view from Power BI instead of computing MEDIAN in DAX.

---

**E3 — HIGH: DISTINCTCOUNT on High-Cardinality Columns in DirectQuery**

*Check:* Measures containing `DISTINCTCOUNT(` on columns likely to have high cardinality (column name contains: `id`, `order`, `transaction`, `session`, `user`).

*Violation:* DISTINCTCOUNT is computed in Power BI locally in DirectQuery mode — causes extra query round-trips and high memory usage for large cardinality sets. Visual totals using DISTINCTCOUNT also require additional queries.

*Fix:* Pre-aggregate distinct counts in Snowflake:
```sql
CREATE OR REPLACE DYNAMIC TABLE DB.SCHEMA.AGG_DISTINCT_CUSTOMERS
  TARGET_LAG = '1 hour' WAREHOUSE = MY_WH AS
SELECT DATE_TRUNC('month', ORDER_DATE) AS MONTH,
       REGION,
       COUNT(DISTINCT CUSTOMER_ID) AS DISTINCT_CUSTOMERS
FROM DB.SCHEMA.FACT_SALES
GROUP BY 1, 2;
```

---

**E4 — MEDIUM: Excessive Visuals Pattern (Design Guidance)**

*Check:* Count unique visual containers in the report layer. If the PBIT has report pages, note as general guidance.

*Violation:* Per Microsoft guidance, each visual on a page generates its own DAX/SQL query. Pages with many visuals slow page load significantly in DirectQuery mode.

*Fix recommendations:*
- Limit to 5-8 visuals per report page
- Use drillthrough pages instead of cramming detail visuals on summary pages
- Use bookmarks to toggle between visual states (reduce active visuals)
- Replace multiple card visuals with a single multi-row card
- Use Query Reduction options (File → Options → Query Reduction): add Apply button to slicers, disable cross-highlighting by default

---

**E5 — MEDIUM: Cross-Filtering Not Constrained**

*Check:* Bi-directional relationships detected (from data-modeling sub-skill) combined with DirectQuery source → note here as a report-design amplifier.

*Violation:* Each cross-filter interaction in a DirectQuery report sends additional queries per visual. With many visuals and bi-directional filters, a single slicer click can trigger 10-20 Snowflake queries.

*Fix:*
- Switch off cross-highlighting/filtering for non-essential visual pairs (Edit Interactions in Power BI)
- Enable "Add an Apply button to each slicer" in Query Reduction settings
- Enable "Add a single Apply button to the page to apply all filter changes at once"

---

**E6 — MEDIUM: Visual Totals Enabled with DISTINCTCOUNT or MEDIAN**

*Check:* Measures using `DISTINCTCOUNT(` or `MEDIAN(` / `MEDIANX(` (already flagged in E2/E3). If present, note that visual totals require additional queries.

*Violation:* Tables/matrices display totals by default. For DISTINCTCOUNT/MEDIAN measures, Power BI sends additional queries to compute these totals — can double query count per visual.

*Fix:* Disable visual totals for DISTINCTCOUNT/MEDIAN measures in the Format pane of each visual, or pre-compute totals in Snowflake.

---

**E7 — LOW: No Dedicated Snowflake Warehouse for Power BI**

*Check:* All M expressions share the same `#"Snowflake Warehouse"` parameter. Note as a recommendation.

*Violation:* Using a shared warehouse for Power BI reports creates resource contention with ETL, notebook, and other workloads.

*Fix recommendations:*
- Create a dedicated reporting warehouse for Power BI: `CREATE WAREHOUSE PBI_WH WAREHOUSE_SIZE = 'MEDIUM' AUTO_SUSPEND = 600 AUTO_RESUME = TRUE;`
- Set auto-suspend to 10+ minutes (preserve warehouse cache for BI workloads)
- For high-concurrency: use multi-cluster warehouse with `MIN_CLUSTER_COUNT = 1, MAX_CLUSTER_COUNT = 3`
- For large Import refreshes: use a separate, larger warehouse that auto-suspends immediately after refresh

---

**E8 — LOW: Snowflake Role Not Scoped to Minimum Privilege**

*Check:* M expressions using a wildcard or admin-level Snowflake role (name contains `ADMIN`, `SYSADMIN`, `ACCOUNTADMIN`).

*Violation:* Per Snowflake best practices, Power BI connections should use a role with access only to the required objects. Broad roles trigger excessive metadata queries (SHOW TABLES, SHOW SCHEMAS) that slow initial connection.

*Fix:*
```sql
CREATE ROLE POWER_BI_ROLE;
GRANT USAGE ON DATABASE MY_DB TO ROLE POWER_BI_ROLE;
GRANT USAGE ON SCHEMA MY_DB.ANALYTICS TO ROLE POWER_BI_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA MY_DB.ANALYTICS TO ROLE POWER_BI_ROLE;
GRANT SELECT ON ALL VIEWS IN SCHEMA MY_DB.ANALYTICS TO ROLE POWER_BI_ROLE;
GRANT FUTURE GRANTS ON TABLES IN SCHEMA MY_DB.ANALYTICS TO ROLE POWER_BI_ROLE;
-- Then connect Power BI with Role = "POWER_BI_ROLE"
```

---

**E9 — MEDIUM: Composite Model Not Used for Large DirectQuery Models**

*Check:* All tables in the model use DirectQuery (all M sources are Snowflake, no Import-mode tables). More than 5 tables detected.

*Violation:* Per Microsoft guidance, Composite Models (mixing Import dimension tables with DirectQuery fact tables) significantly improve performance. Dimension tables in Import mode enable fast filtering without hitting Snowflake for every slicer interaction.

*Fix:* Switch dimension tables (Customer, Product, Date, etc.) to Import storage mode in Power BI. Keep large fact tables in DirectQuery. This reduces Snowflake queries for filter/slice operations to near-zero.

---

**E10 — LOW: Authentication Using Username/Password**

*Check:* M expressions not containing `[Role=...]` or where the connection string pattern suggests basic auth.

*Violation:* Snowflake is deprecating single-factor password authentication. Power BI should use OAuth/SSO or key-pair authentication.

*Fix recommendations:*
- Enable Azure AD OAuth for Power BI → Snowflake SSO (Snowflake External OAuth)
- This ensures Power BI queries respect Snowflake row access policies per user identity
- See: [Snowflake OAuth for Power BI](https://docs.snowflake.com/en/user-guide/oauth-powerbi)

## Output

Return `findings[]` to the router (SKILL.md Step 3).
