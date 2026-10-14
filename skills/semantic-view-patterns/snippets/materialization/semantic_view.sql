-- Materialization: Semantic View DDL + Materialization Setup

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- STEP 1: Create the SV with MAX_STALENESS
-- MAX_STALENESS enables the materialization feature.
-- Without it, ADD MATERIALIZATION will fail.
-- Minimum allowed: 120 seconds.
-- ============================================================

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.REVENUE_ANALYSIS_SV

  TABLES (
    mat_orders,
    mat_customers UNIQUE (customer_id)
  )

  RELATIONSHIPS (
    orders_to_customers AS mat_orders(customer_id) REFERENCES mat_customers
  )

  DIMENSIONS (
    mat_customers.customer_name AS customer_name
      WITH SYNONYMS ('customer', 'account name'),
    mat_customers.segment AS segment
      WITH SYNONYMS ('customer segment', 'tier'),
    mat_orders.region AS region
      WITH SYNONYMS ('region', 'geo'),
    mat_orders.order_date AS order_date
      WITH SYNONYMS ('date', 'order date'),
    mat_orders.order_year AS YEAR(order_date)
      WITH SYNONYMS ('year')
  )

  METRICS (
    -- Additive metrics (SUM, COUNT, MIN, MAX) can be reaggregated
    -- from a materialization with MORE dimensions — great for rollup queries
    mat_orders.total_revenue AS SUM(amount)
      WITH SYNONYMS ('revenue', 'total sales'),
    mat_orders.order_count AS COUNT(order_id)
      WITH SYNONYMS ('orders', 'number of orders'),
    mat_orders.min_order AS MIN(amount)
      WITH SYNONYMS ('smallest order'),
    mat_orders.max_order AS MAX(amount)
      WITH SYNONYMS ('largest order'),

    -- Non-additive metrics (AVG, COUNT DISTINCT) cannot be reaggregated
    -- They require the materialization to include ALL the same dimensions as the query
    mat_orders.avg_order AS AVG(amount)
      WITH SYNONYMS ('average order', 'AOV')
  )

  -- MAX_STALENESS: how stale can materialized data be before falling back to base tables?
  -- Minimum: 120 seconds
  MAX_STALENESS = '1 hour'

  COMMENT = 'Revenue analysis SV with materialization support. Demonstrates full-refresh vs incremental-refresh materializations and reaggregation of additive metrics.';


-- ============================================================
-- STEP 2: Grant the materialization privilege (run as ACCOUNTADMIN)
-- ============================================================

-- GRANT ADD SEMANTIC VIEW MATERIALIZATION ON SCHEMA SNIPPETS.PUBLIC TO ROLE <your_role>;


-- ============================================================
-- STEP 3: Add materializations
-- ============================================================

-- Materialization 1: Customer + year revenue rollup
-- No IMMUTABLE WHERE → full refresh each time (expensive for large datasets)
ALTER SEMANTIC VIEW SNIPPETS.PUBLIC.REVENUE_ANALYSIS_SV
  ADD MATERIALIZATION revenue_by_customer_year
  WAREHOUSE = SNOWADHOC
  AS
    DIMENSIONS mat_customers.customer_name, mat_orders.order_year
    METRICS mat_orders.total_revenue, mat_orders.order_count;


-- Materialization 2: Historical data (pre-2024) — IMMUTABLE WHERE limits refresh scope
-- Snowflake strongly recommends IMMUTABLE WHERE to reduce refresh cost.
-- Rows where order_date < '2024-01-01' are computed once and not recomputed
-- unless the materialization is explicitly dropped and re-added.
ALTER SEMANTIC VIEW SNIPPETS.PUBLIC.REVENUE_ANALYSIS_SV
  ADD MATERIALIZATION historical_revenue
  WAREHOUSE = SNOWADHOC
  IMMUTABLE WHERE (order_date < '2024-01-01')
  AS
    DIMENSIONS mat_orders.order_date, mat_customers.segment, mat_orders.region
    METRICS mat_orders.total_revenue, mat_orders.order_count;


-- ============================================================
-- STEP 4: Operational commands
-- ============================================================

-- List all materializations for this SV
-- (shows name, state, stale_by, warehouse, dimensions, metrics, immutable_where)
SHOW MATERIALIZATIONS IN SEMANTIC VIEW SNIPPETS.PUBLIC.REVENUE_ANALYSIS_SV;

-- Manual refresh (uses current session warehouse)
ALTER SEMANTIC VIEW SNIPPETS.PUBLIC.REVENUE_ANALYSIS_SV
  REFRESH MATERIALIZATION revenue_by_customer_year;

-- View refresh history
SELECT * FROM TABLE(SNIPPETS.INFORMATION_SCHEMA.SEMANTIC_VIEW_MATERIALIZATION_REFRESH_HISTORY(
    NAME => 'revenue_by_customer_year'
));

-- Remove a materialization
-- ALTER SEMANTIC VIEW SNIPPETS.PUBLIC.REVENUE_ANALYSIS_SV
--   DROP MATERIALIZATION revenue_by_customer_year;

-- Change MAX_STALENESS (e.g. if refreshes are taking too long and materializations
-- are exceeding the staleness limit and being skipped)
-- ALTER SEMANTIC VIEW SNIPPETS.PUBLIC.REVENUE_ANALYSIS_SV
--   SET MAX_STALENESS = '2 hours';
