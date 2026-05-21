-- Row Access Policy Example: Semantic View DDL
-- Run schema.sql and seed_data.sql first.
--
-- Three objects are created here:
--   1. SALES_BY_REGION_SV      — anti-pattern: RAP on dim only → NULL row for filtered facts
--   2. ORDERS_FILTERED         — helper view (workaround 1): inner join drops unmatched fact rows
--   3. SALES_BY_REGION_VIEW_SV — workaround 1: uses ORDERS_FILTERED as the fact entity
--
-- Workaround 2 (apply RAP directly to the ORDERS fact table) is demonstrated
-- in queries.sql after the anti-pattern behaviour is shown.

USE ROLE SYSADMIN;
USE DATABASE RAP_TEST;
USE SCHEMA PUBLIC;

-- ============================================================
-- ANTI-PATTERN SV
-- The RAP is attached to SALES_REGIONS (the dimension). When the SV
-- engine joins ORDERS to SALES_REGIONS, filtered-out region rows produce
-- NULL dimension values for the orphaned ORDERS rows.
-- ============================================================

CREATE OR REPLACE SEMANTIC VIEW RAP_TEST.PUBLIC.SALES_BY_REGION_SV

  TABLES (
    orders  AS RAP_TEST.PUBLIC.ORDERS
      PRIMARY KEY (ORDER_ID),

    regions AS RAP_TEST.PUBLIC.SALES_REGIONS
      PRIMARY KEY (REGION_ID)
  )

  RELATIONSHIPS (
    orders_to_regions AS orders(REGION_ID) REFERENCES regions
  )

  FACTS (
    orders.revenue AS AMOUNT
      COMMENT = 'Order amount in USD'
  )

  DIMENSIONS (
    orders.order_date           AS ORDER_DATE,
    regions.region_name         AS REGION_NAME,
    regions.reporting_manager   AS REPORTING_MANAGER
  )

  METRICS (
    orders.total_revenue AS SUM(AMOUNT)
      WITH SYNONYMS ('revenue', 'sales', 'total sales')
      COMMENT = 'Sum of order amounts in USD',

    orders.order_count AS COUNT(ORDER_ID)
      WITH SYNONYMS ('orders', 'number of orders', 'order volume')
      COMMENT = 'Number of orders'
  )

  COMMENT = 'Anti-pattern: RAP on dimension only. Query as REGION_A_ANALYST to see the NULL-row problem.';

GRANT SELECT ON SEMANTIC VIEW RAP_TEST.PUBLIC.SALES_BY_REGION_SV TO ROLE REGION_A_ANALYST;
GRANT SELECT ON SEMANTIC VIEW RAP_TEST.PUBLIC.SALES_BY_REGION_SV TO ROLE REGION_B_ANALYST;


-- ============================================================
-- WORKAROUND 1: HELPER VIEW WITH INNER JOIN
-- The view pre-filters ORDERS by inner-joining to SALES_REGIONS.
-- When the RAP hides a region row, the INNER JOIN also drops the
-- corresponding ORDERS rows — no orphaned facts, no NULL dimension rows.
-- ============================================================

CREATE OR REPLACE VIEW RAP_TEST.PUBLIC.ORDERS_FILTERED AS
    SELECT o.order_id, o.region_id, o.order_date, o.amount
    FROM RAP_TEST.PUBLIC.ORDERS o
    INNER JOIN RAP_TEST.PUBLIC.SALES_REGIONS r ON o.region_id = r.region_id;

GRANT SELECT ON VIEW RAP_TEST.PUBLIC.ORDERS_FILTERED TO ROLE REGION_A_ANALYST;
GRANT SELECT ON VIEW RAP_TEST.PUBLIC.ORDERS_FILTERED TO ROLE REGION_B_ANALYST;

-- Structurally identical to SALES_BY_REGION_SV, but the fact entity is
-- ORDERS_FILTERED (the pre-filtered view) instead of raw ORDERS.
CREATE OR REPLACE SEMANTIC VIEW RAP_TEST.PUBLIC.SALES_BY_REGION_VIEW_SV

  TABLES (
    orders  AS RAP_TEST.PUBLIC.ORDERS_FILTERED
      PRIMARY KEY (ORDER_ID),

    regions AS RAP_TEST.PUBLIC.SALES_REGIONS
      PRIMARY KEY (REGION_ID)
  )

  RELATIONSHIPS (
    orders_to_regions AS orders(REGION_ID) REFERENCES regions
  )

  FACTS (
    orders.revenue AS AMOUNT
      COMMENT = 'Order amount in USD'
  )

  DIMENSIONS (
    orders.order_date           AS ORDER_DATE,
    regions.region_name         AS REGION_NAME,
    regions.reporting_manager   AS REPORTING_MANAGER
  )

  METRICS (
    orders.total_revenue AS SUM(AMOUNT)
      WITH SYNONYMS ('revenue', 'sales', 'total sales')
      COMMENT = 'Sum of order amounts in USD',

    orders.order_count AS COUNT(ORDER_ID)
      WITH SYNONYMS ('orders', 'number of orders', 'order volume')
      COMMENT = 'Number of orders'
  )

  COMMENT = 'Workaround 1: fact entity is a helper view that inner-joins to the dimension, eliminating NULL rows.';

GRANT SELECT ON SEMANTIC VIEW RAP_TEST.PUBLIC.SALES_BY_REGION_VIEW_SV TO ROLE REGION_A_ANALYST;
GRANT SELECT ON SEMANTIC VIEW RAP_TEST.PUBLIC.SALES_BY_REGION_VIEW_SV TO ROLE REGION_B_ANALYST;
