-- Entity Facts: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES
-- ============================================================

-- 1. Revenue by customer value segment
--    (derived dimension from PRIVATE aggregated fact)
--    Expected: high=$4200, medium=$1700, low=$600+$200=$800
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CUSTOMER_ORDERS_SV
    DIMENSIONS customers.value_segment
    METRICS orders.total_revenue, customers.customer_count
)
ORDER BY value_segment;


-- 2. Number of customers by segment and age bucket
--    (calculated dimension customers.age)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CUSTOMER_ORDERS_SV
    DIMENSIONS customers.value_segment, customers.age
    METRICS customers.customer_count
)
ORDER BY value_segment, age;


-- 3. Monthly revenue by segment — show transitions over time
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CUSTOMER_ORDERS_SV
    DIMENSIONS customers.value_segment, orders.order_month
    METRICS orders.total_revenue
)
ORDER BY order_month, value_segment;


-- 4. Filter using the per-order fact in WHERE clause
--    (row-level filtering on orders.order_amount)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CUSTOMER_ORDERS_SV
    DIMENSIONS customers.customer_name
    METRICS orders.total_revenue, orders.order_count
    WHERE orders.order_amount > 500
);


-- ============================================================
-- WHAT DOESN'T WORK
-- ============================================================

-- ERROR: Cannot directly query a PRIVATE fact as a dimension or metric.
-- customers.lifetime_value is PRIVATE — it only exists to power value_segment.
--
-- SELECT * FROM SEMANTIC_VIEW(
--     SNIPPETS.PUBLIC.CUSTOMER_ORDERS_SV
--     DIMENSIONS customers.lifetime_value    -- Error: no dimension named lifetime_value
-- );
--
-- Remove PRIVATE from the DDL if you want it directly queryable.
