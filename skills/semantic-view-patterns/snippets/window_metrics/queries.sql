-- Window Metrics: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES
-- ============================================================

-- 1. Daily revenue with 7-day rolling average
--    Shows smoothed trend vs raw daily noise
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.DAILY_SALES_SV
    DIMENSIONS daily_sales.date
    METRICS daily_sales.total_revenue, daily_sales.rolling_7d_avg_revenue
)
ORDER BY date;


-- 2. Period-over-period comparison: today vs 30 days ago
--    revenue_30d_ago is NULL for the first 30 rows (no prior data)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.DAILY_SALES_SV
    DIMENSIONS daily_sales.date
    METRICS daily_sales.total_revenue, daily_sales.revenue_30d_ago
)
ORDER BY date;


-- 3. YTD cumulative revenue by day
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.DAILY_SALES_SV
    DIMENSIONS daily_sales.date
    METRICS daily_sales.total_revenue, daily_sales.ytd_revenue
)
ORDER BY date;


-- 4. All window metrics together — full picture
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.DAILY_SALES_SV
    DIMENSIONS daily_sales.date
    METRICS daily_sales.total_revenue,
            daily_sales.rolling_7d_avg_revenue,
            daily_sales.revenue_30d_ago,
            daily_sales.ytd_revenue
)
ORDER BY date;


-- ============================================================
-- IMPORTANT NOTES
-- ============================================================

-- PARTITION BY EXCLUDING <dim>: The window partitions by all other dimensions
-- requested in the query, EXCLUDING the specified one.
-- This means if you add channel to the query, each channel gets its own window.

-- Window with channel breakdown (each channel gets its own 7-day window):
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.DAILY_SALES_SV
    DIMENSIONS daily_sales.date, daily_sales.channel
    METRICS daily_sales.total_revenue, daily_sales.rolling_7d_avg_revenue
)
ORDER BY channel, date;


-- ============================================================
-- GOTCHAS
-- ============================================================

-- Window metrics require their ORDER BY dimension to be in the SELECT.
-- If you ask for ytd_revenue without also requesting daily_sales.date,
-- the result is ambiguous — include date in DIMENSIONS.
--
-- Also: LAG(n) will be NULL for the first n rows — this is expected behavior.
-- Handle with COALESCE if needed in standard SQL wrapping.
