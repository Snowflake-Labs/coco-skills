-- Inline SV: Queries / Semantic View DDL
-- This snippet has two distinct patterns — both are shown here.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- PATTERN 1: SQL SUBQUERY AS TABLE DEFINITION
-- A SQL query used as the source of a table in the TABLES clause.
-- Useful for: filtering source data, combining tables at load time,
-- exposing only certain rows to the SV without an intermediate view.
-- ============================================================

-- SV with inline subquery filter:
-- Only "premium" customers are exposed to the SV consumer.
CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.PREMIUM_ORDERS_SV
TABLES (
    inline_orders,
    inline_customers AS (
        SELECT * FROM inline_customers
        WHERE tier = 'premium'
    ) UNIQUE (customer_id)
)
RELATIONSHIPS (
    inline_orders(customer_id) REFERENCES inline_customers
)
DIMENSIONS (
    inline_customers.customer_name AS customer_name
)
METRICS (
    inline_orders.total_revenue AS SUM(amount),
    inline_orders.order_count   AS COUNT(order_id)
);

-- Query it — only premium customers are included
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.PREMIUM_ORDERS_SV
    DIMENSIONS inline_customers.customer_name
    METRICS inline_orders.total_revenue
);


-- ============================================================
-- PATTERN 2: INLINE / AD-HOC SEMANTIC VIEW (SV CTE)
-- Define and query a SV in one statement — no CREATE needed.
-- The SV exists only for the duration of the query.
-- Useful for: testing SV DDL before committing,
-- dbt unit testing, ad-hoc exploration.
-- ============================================================

-- Inline SV using WITH ... AS SEMANTIC VIEW:
WITH adhoc_sv AS SEMANTIC VIEW
TABLES (
    inline_orders,
    inline_customers UNIQUE (customer_id)
)
RELATIONSHIPS (
    inline_orders(customer_id) REFERENCES inline_customers
)
DIMENSIONS (
    inline_customers.customer_name AS customer_name,
    inline_customers.tier AS tier
)
METRICS (
    inline_orders.total_revenue AS SUM(amount),
    inline_orders.order_count   AS COUNT(order_id)
)
SELECT * FROM SEMANTIC_VIEW(
    adhoc_sv
    DIMENSIONS inline_customers.customer_name, inline_customers.tier
    METRICS inline_orders.total_revenue
)
ORDER BY total_revenue DESC;


-- Another inline SV — test a filter before committing to the DDL:
WITH test_sv AS SEMANTIC VIEW
TABLES (
    inline_orders,
    inline_customers UNIQUE (customer_id)
)
RELATIONSHIPS (
    inline_orders(customer_id) REFERENCES inline_customers
)
DIMENSIONS (
    inline_customers.customer_name AS customer_name
)
METRICS (
    inline_orders.completed_revenue AS SUM(amount)
)
-- Metric-only query (no dimension needed):
SELECT * FROM SEMANTIC_VIEW(
    test_sv
    METRICS inline_orders.completed_revenue
);


-- ============================================================
-- RULES AND GOTCHAS
-- ============================================================

-- Pattern 1 (subquery in TABLES clause):
--   - SQL query must include the unique/primary key columns
--   - Subquery syntax: table_alias AS (SELECT ...) UNIQUE (key_col)
--   - Changes to underlying tables NOT visible until SV is replaced

-- Pattern 2 (WITH ... AS SEMANTIC VIEW):
--   - Does NOT create a persistent SV — exists only for the query
--   - Cannot be referenced by Cortex Analyst (no saved SV to target)
--   - Great for dbt model testing and iterative DDL development
--   - The SEMANTIC_VIEW() call immediately follows the WITH block (no SELECT needed)
