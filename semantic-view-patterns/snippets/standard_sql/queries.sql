-- Standard SQL: Queries
-- Prerequisites: deploy derived_metrics/semantic_view.sql first
-- Reference SV: SNIPPETS.PUBLIC.CHANNEL_SALES_SV (no SEMANTIC_VIEW() function needed)

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- STANDARD SQL ON A SEMANTIC VIEW
-- Regular SELECT from a SV as if it were a view.
-- ============================================================

-- 1. Monthly revenue across channels using standard SQL (not SEMANTIC_VIEW())
--    IMPORTANT: metrics must be wrapped in ANY_VALUE(), MIN(), or MAX()
--    if other columns are selected; otherwise use them ungrouped.
SELECT
    month,
    ANY_VALUE(store_revenue)   AS store_rev,
    ANY_VALUE(web_revenue)     AS web_rev,
    ANY_VALUE(catalog_revenue) AS catalog_rev,
    ANY_VALUE(total_revenue)   AS total_rev
FROM SNIPPETS.PUBLIC.CHANNEL_SALES_SV
WHERE year = 2024
GROUP BY ALL
ORDER BY month;


-- 2. MIN/MAX pattern for metrics
SELECT
    quarter,
    MIN(total_revenue) AS min_quarterly_rev,
    MAX(total_revenue) AS max_quarterly_rev
FROM SNIPPETS.PUBLIC.CHANNEL_SALES_SV
GROUP BY quarter
ORDER BY quarter;


-- 3. Metric-less dimension query — returns distinct dimension values
--    No GROUP BY needed; the SV engine handles deduplication.
SELECT month
FROM SNIPPETS.PUBLIC.CHANNEL_SALES_SV
WHERE year = 2024;


-- 4. WHERE clause on dimensions + standard aggregation
SELECT
    year,
    quarter,
    ANY_VALUE(store_revenue) AS store_rev,
    ANY_VALUE(total_revenue) AS total_rev
FROM SNIPPETS.PUBLIC.CHANNEL_SALES_SV
WHERE quarter IN (1, 2)
GROUP BY ALL
ORDER BY year, quarter;


-- 5. Combine with standard SQL window functions on top of the SV
SELECT
    month,
    ANY_VALUE(total_revenue) AS monthly_rev,
    SUM(ANY_VALUE(total_revenue)) OVER (ORDER BY month) AS running_total
FROM SNIPPETS.PUBLIC.CHANNEL_SALES_SV
GROUP BY month
ORDER BY month;


-- ============================================================
-- RULES FOR STANDARD SQL ON SVs
-- ============================================================

-- 1. If you SELECT a metric alongside other columns, wrap it in
--    ANY_VALUE(), MIN(), or MAX()
-- 2. If you SELECT only metrics (no other columns), no wrapping needed
-- 3. If you SELECT only dimensions (no metrics), no wrapping needed
--    and GROUP BY is not required — SV returns distinct values
-- 4. Standard WHERE, ORDER BY, LIMIT all work normally
-- 5. You can JOIN a SV to another table or SV using standard SQL syntax

-- ============================================================
-- WHY USE STANDARD SQL OVER SEMANTIC_VIEW()?
-- ============================================================
-- + Familiar SQL syntax for analysts already in SQL tools
-- + Works with BI tools that don't speak SEMANTIC_VIEW() syntax
-- + Enables standard SQL window functions on top of SV output
-- + Can JOIN multiple SVs together
-- - Less explicit about which metrics/dimensions are being requested
-- - No AI routing or VQR matching (Cortex Analyst uses SEMANTIC_VIEW() internally)
