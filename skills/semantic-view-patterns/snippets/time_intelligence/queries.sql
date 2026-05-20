-- Time Intelligence: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES
-- ============================================================

-- 1. Monthly revenue vs same period last year (SPLY)
--    For 2024 months: revenue_ly shows the aligned 2023 value
--    yoy_pct shows % growth — positive means 2024 > 2023
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.TIME_INTELLIGENCE_SV
    DIMENSIONS calendar.year, calendar.month, calendar.month_name
    METRICS sales.revenue, sales_ly.revenue_ly, yoy_change, yoy_pct
)
ORDER BY YEAR ASC, MONTH ASC;


-- 2. Month-over-month revenue change
--    revenue_lm for January 2024 = December 2023 (the prior month)
--    First month in dataset (Jan 2023) shows NULL for revenue_lm — no prior month
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.TIME_INTELLIGENCE_SV
    DIMENSIONS calendar.year, calendar.month, calendar.month_name
    METRICS sales.revenue, sales_lm.revenue_lm, mom_change, mom_pct
)
ORDER BY YEAR ASC, MONTH ASC;


-- 3. Annual totals with YoY growth
--    Grouping by year collapses all months — shift arithmetic still aligns correctly
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.TIME_INTELLIGENCE_SV
    DIMENSIONS calendar.year
    METRICS sales.revenue, sales_ly.revenue_ly, yoy_change, yoy_pct
)
ORDER BY YEAR ASC;


-- 4. YoY growth by region
--    Both East and West show their own YoY numbers
--    Works because region lives on the current-period entity (sales)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.TIME_INTELLIGENCE_SV
    DIMENSIONS calendar.year, sales.region
    METRICS sales.revenue, sales_ly.revenue_ly, yoy_pct
)
ORDER BY YEAR ASC, REGION ASC;


-- 5. Full dashboard row: current, SPLM, SPLY, MoM%, YoY%
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.TIME_INTELLIGENCE_SV
    DIMENSIONS calendar.year, calendar.month, calendar.month_name
    METRICS sales.revenue,
            sales_lm.revenue_lm,
            sales_ly.revenue_ly,
            mom_pct,
            yoy_pct
)
ORDER BY YEAR ASC, MONTH ASC;


-- ============================================================
-- HOW THE DATE SHIFT WORKS
-- ============================================================

-- The sales_ly entity has a computed FACT:
--   sales_ly.sale_month_shifted_ly AS DATEADD('year', 1, SALE_MONTH)
--
-- The relationship joins on that computed fact:
--   sales_ly(sale_month_shifted_ly) REFERENCES calendar(MONTH)
--
-- So when the query filters calendar.MONTH = '2024-03-01':
--   → sales rows where SALE_MONTH = '2024-03-01' (current period)
--   → sales_ly rows where DATEADD('year',1, SALE_MONTH) = '2024-03-01'
--                      = rows where SALE_MONTH = '2023-03-01' (last year) ✓
--
-- No ETL, no UNION ALL view, no window function needed.


-- ============================================================
-- GOTCHAS
-- ============================================================

-- NULL for the first/last period:
--   revenue_lm is NULL for Jan 2023 (no prior month in the dataset).
--   revenue_ly is NULL for all 2023 months (no 2022 data to shift from).
--   Handle with COALESCE(revenue_ly, 0) in standard SQL wrapping if needed.

-- YTD / QTD / MTD are NOT supported by this pattern.
--   The time-shift pattern gives you point-in-time period comparisons.
--   For cumulative running totals (YTD, QTD), use window metrics with
--   SUM(total_revenue) OVER (PARTITION BY year ORDER BY date ROWS UNBOUNDED PRECEDING).
--   See the window_metrics/ snippet.

-- Cross-period region breakdown:
--   Query 4 uses sales.region (current period) with revenue_ly.
--   The SV automatically applies region to both entities because they share
--   the same physical table (FACT_SALES). If you add a separate entity for
--   the LY role-play that joins through a different path, you may need to
--   define region on sales_ly explicitly.
