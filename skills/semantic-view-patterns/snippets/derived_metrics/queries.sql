-- Derived Metrics: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES
-- ============================================================

-- 1. Total revenue and per-channel breakdown by month
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CHANNEL_SALES_SV
    DIMENSIONS dim_date.month
    METRICS store_sales.store_revenue, web_sales.web_revenue,
            catalog_sales.catalog_revenue, total_revenue
)
ORDER BY month;


-- 2. Channel mix — % of total per month
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CHANNEL_SALES_SV
    DIMENSIONS dim_date.month
    METRICS store_pct_of_total, web_pct_of_total, catalog_pct_of_total
)
ORDER BY month;


-- 3. Q1 vs Q2 channel revenue
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CHANNEL_SALES_SV
    DIMENSIONS dim_date.quarter
    METRICS store_sales.store_revenue, web_sales.web_revenue,
            catalog_sales.catalog_revenue, total_revenue
)
ORDER BY quarter;


-- 4. Full year summary (no time dimension needed)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CHANNEL_SALES_SV
    METRICS total_revenue, store_pct_of_total, web_pct_of_total, catalog_pct_of_total
);


-- ============================================================
-- GOTCHAS
-- ============================================================

-- NOTE: Derived metric names have NO table prefix in the DDL:
--   CORRECT:   total_revenue AS store_sales.store_revenue + ...
--   INCORRECT: store_sales.total_revenue AS ...    (would scope it to store entity)

-- NOTE: Ratio metrics return decimals (0.0 - 1.0).
-- To display as percent, use standard SQL on top of the SV:
SELECT
    month,
    ROUND(store_pct_of_total * 100, 1) AS store_pct,
    ROUND(web_pct_of_total * 100, 1)   AS web_pct,
    ROUND(catalog_pct_of_total * 100, 1) AS catalog_pct
FROM SNIPPETS.PUBLIC.CHANNEL_SALES_SV
WHERE year = 2024
GROUP BY ALL
ORDER BY month;
