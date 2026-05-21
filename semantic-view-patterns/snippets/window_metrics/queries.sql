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

-- 1. Window metrics require their ORDER BY dimension to be in the SELECT.
--    If you ask for ytd_revenue without also requesting daily_sales.date,
--    the result is ambiguous — include date in DIMENSIONS.

-- 2. LAG(n) will be NULL for the first n rows — expected behavior.
--    Handle with COALESCE if needed in standard SQL wrapping.

-- 3. Do NOT declare measure columns in FACTS if you want to use them in
--    window metrics. FACTS columns are treated as "row-level expressions";
--    PARTITION BY EXCLUDING will fail with:
--      "PARTITION BY EXCLUDING is not allowed when the window function
--       operates over a row-level expression."
--    Fix: omit measure columns from FACTS and reference them by bare
--    physical column name in the base metric: SUM(revenue) not SUM(entity.revenue).

-- 4. Always include PARTITION BY EXCLUDING (or explicit PARTITION BY) in
--    window metrics. Bare ORDER BY without PARTITION BY is unsupported:
--      SUM(total_revenue) OVER (ORDER BY date ROWS BETWEEN 4 PRECEDING ...)
--    will fail with "Unsupported expression in the definition of derived metric".

-- 5. Use entity prefix on ALL metric names when the SV includes window metrics:
--      daily_sales.total_revenue AS SUM(revenue)       -- correct
--      total_revenue AS SUM(revenue)                   -- may fail in window context
