-- Materialization: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- QUERIES — the SV is queried identically with or without materialization.
-- When a suitable materialization exists and is fresh, Snowflake rewrites
-- the query to read from the materialized result instead of the base tables.
-- No change to query syntax is needed.
-- ============================================================

-- 1. Revenue by customer and year
--    → WILL use the revenue_by_customer_year materialization (exact match)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.REVENUE_ANALYSIS_SV
    DIMENSIONS mat_customers.customer_name, mat_orders.order_year
    METRICS mat_orders.total_revenue
)
ORDER BY customer_name, order_year;


-- 2. Revenue by customer only (no year)
--    → WILL use revenue_by_customer_year: reaggregates by summing across order_year.
--      Reaggregation works because total_revenue is SUM (additive).
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.REVENUE_ANALYSIS_SV
    DIMENSIONS mat_customers.customer_name
    METRICS mat_orders.total_revenue
)
ORDER BY total_revenue DESC;


-- 3. Revenue by year only
--    → WILL use revenue_by_customer_year: reaggregates by summing across customer_name.
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.REVENUE_ANALYSIS_SV
    DIMENSIONS mat_orders.order_year
    METRICS mat_orders.total_revenue
);


-- 4. Revenue by date, segment, region (pre-2024 data)
--    → WILL use historical_revenue materialization (IMMUTABLE WHERE order_date < 2024)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.REVENUE_ANALYSIS_SV
    DIMENSIONS mat_orders.order_date, mat_customers.segment, mat_orders.region
    METRICS mat_orders.total_revenue
    WHERE mat_orders.order_date < '2024-01-01'
)
ORDER BY order_date;


-- 5. Average order value (AVG = non-additive)
--    → CANNOT use materialization for reaggregation — falls back to base tables.
--      To use a materialization for AVG queries, the materialization must include
--      the EXACT same set of dimensions as the query.
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.REVENUE_ANALYSIS_SV
    DIMENSIONS mat_customers.segment
    METRICS mat_orders.avg_order
);


-- ============================================================
-- REAGGREGATION RULES SUMMARY
-- ============================================================
-- Additive (can reaggregate from finer-grained materialization):
--   SUM ✓  COUNT ✓  MIN ✓  MAX ✓

-- Non-additive (cannot reaggregate — materialization must be exact match):
--   AVG ✗  COUNT(DISTINCT) ✗  MEDIAN ✗  PERCENTILE ✗

-- Cannot be materialized at all:
--   Window function metrics (LAG, rolling AVG, YTD) ✗
--   Semi-additive metrics (NON ADDITIVE BY) ✗
--   Metrics with USING clause ✗


-- ============================================================
-- WHEN MATERIALIZATION IS SKIPPED (fallback to base tables)
-- ============================================================
-- • No materialization covers the requested dimensions/metrics
-- • Materialization is older than MAX_STALENESS
-- • A masking policy or row access policy exists on base tables
-- • Non-additive metric requires reaggregation (dimensions don't match exactly)
