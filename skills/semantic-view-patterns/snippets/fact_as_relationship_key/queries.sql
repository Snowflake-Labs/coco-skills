-- Fact as Relationship Key: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES
-- ============================================================

-- 1. Revenue vs budget by fiscal quarter
--    The computed FK (fiscal_qtr_key) silently resolves the join to fiscal_quarters.
--    Expected: Q1-Q4 2023 at 43-44%, declining late year; Q2 2024 best at 53.7%
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.SALES_VS_BUDGET_SV
    METRICS sales.total_revenue, fiscal_quarters.total_budget
    DIMENSIONS fiscal_quarters.quarter_name
)
ORDER BY quarter_name;


-- 2. Revenue vs budget by fiscal year — multi-quarter rollup
--    fiscal_year rolls up all quarters in the year; budget sums their targets
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.SALES_VS_BUDGET_SV
    METRICS sales.total_revenue, fiscal_quarters.total_budget
    DIMENSIONS fiscal_quarters.fiscal_year
)
ORDER BY fiscal_year;


-- 3. Revenue by product category + fiscal quarter
--    Shows how product mix shifts across quarters (Services strong in Q4, Hardware in Q2 2024)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.SALES_VS_BUDGET_SV
    METRICS sales.total_revenue
    DIMENSIONS products.category, fiscal_quarters.quarter_name
)
ORDER BY quarter_name, category;


-- 4. Budget attainment % — computed in the outer query
--    The SV exposes the raw revenue and budget; attainment is a derived ratio
SELECT
    sv.quarter_name,
    sv.total_revenue,
    sv.total_budget,
    ROUND(sv.total_revenue / NULLIF(sv.total_budget, 0) * 100, 1) AS attainment_pct
FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.SALES_VS_BUDGET_SV
    METRICS sales.total_revenue, fiscal_quarters.total_budget
    DIMENSIONS fiscal_quarters.quarter_name
) AS sv
ORDER BY sv.quarter_name;


-- ============================================================
-- WHAT DOESN'T WORK
-- ============================================================

-- ERROR: Cannot query fiscal_qtr_key as a dimension — it is a FACT
-- used only to resolve the join, not a queryable dimension or metric.
--
-- SELECT * FROM SEMANTIC_VIEW(
--     SNIPPETS.PUBLIC.SALES_VS_BUDGET_SV
--     DIMENSIONS sales.fiscal_qtr_key    -- Error: no dimension named fiscal_qtr_key
-- );
--
-- If you want the quarter key to be queryable, add it as a DIMENSION instead:
--   DIMENSIONS (
--       sales.fiscal_qtr_key_dim AS CONCAT(TO_VARCHAR(YEAR(sale_date)), '-Q', TO_VARCHAR(QUARTER(sale_date)))
--   )
-- Note: a column cannot simultaneously be a FACT (for join use) and a DIMENSION.
-- You would define TWO separate entries — the FACT for the relationship and a
-- separate DIMENSION with the same expression for display purposes.


-- HOW COMPUTED FK FACTS WORK:
-- The engine evaluates the FACT expression (e.g. '2024-Q2') per row on the sales
-- table, then uses that value to look up a matching row in fiscal_quarters via its
-- PRIMARY KEY (fiscal_quarter_key). If no match is found, the row is excluded
-- (same semantics as an INNER JOIN). The computed value is never stored.
