-- Shared Degenerate Dimension: Semantic View DDL
-- Two approaches shown. Use whichever fits your environment.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- APPROACH A: Physical helper view (region_dim)
-- Created in schema.sql as:
--   CREATE VIEW region_dim AS
--     SELECT DISTINCT region FROM store_orders
--     UNION
--     SELECT DISTINCT region FROM web_orders;
--
-- Best for: production SVs, when the helper view is reused across multiple SVs
-- ============================================================

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.CHANNEL_BY_REGION_SV

  TABLES (
    -- Shared dimension: a helper view that unions distinct region values
    -- from both fact tables. Referenced as 'regions' in the SV.
    regions AS region_dim UNIQUE (region),

    store_orders,
    web_orders
  )

  RELATIONSHIPS (
    -- Both fact tables relate to the shared dimension on the 'region' column.
    store_to_region AS store_orders(region) REFERENCES regions,
    web_to_region   AS web_orders(region)   REFERENCES regions
  )

  DIMENSIONS (
    -- The shared dimension: defined once, usable with metrics from either fact.
    regions.region AS region
      WITH SYNONYMS ('region', 'geo', 'geography')
      COMMENT = 'Shared region dimension covering all channels.',

    -- Fact-specific dimensions (not shared)
    store_orders.category AS category
      WITH SYNONYMS ('product category'),
    web_orders.channel AS channel
      WITH SYNONYMS ('device type', 'web channel'),

    store_orders.order_month AS DATE_TRUNC('month', store_orders.order_date)
      WITH SYNONYMS ('store month', 'month'),
    web_orders.order_month AS DATE_TRUNC('month', web_orders.order_date)
      WITH SYNONYMS ('web month')
  )

  METRICS (
    store_orders.store_revenue AS SUM(amount)
      WITH SYNONYMS ('store sales', 'store revenue'),
    web_orders.web_revenue AS SUM(amount)
      WITH SYNONYMS ('web sales', 'online revenue'),

    -- Cross-fact derived metric using the shared region dimension
    total_revenue AS store_orders.store_revenue + web_orders.web_revenue
      WITH SYNONYMS ('total revenue', 'all channel revenue')
  )

  COMMENT = 'Store and web orders with a shared region dimension derived from a UNION helper view. Demonstrates the degenerate dimension pattern.';


-- ============================================================
-- APPROACH B: Inline SQL dataset (no separate physical view)
-- Best for: ad-hoc or when you don't want to create a view object
-- ============================================================

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.CHANNEL_BY_REGION_INLINE_SV

  TABLES (
    -- Same UNION logic, but inline in the TABLES clause.
    -- Alias is required when using AS (...).
    regions AS (
        SELECT DISTINCT region FROM SNIPPETS.PUBLIC.store_orders
        UNION
        SELECT DISTINCT region FROM SNIPPETS.PUBLIC.web_orders
    ) UNIQUE (region),

    store_orders,
    web_orders
  )

  RELATIONSHIPS (
    store_to_region AS store_orders(region) REFERENCES regions,
    web_to_region   AS web_orders(region)   REFERENCES regions
  )

  DIMENSIONS (
    regions.region AS region
      WITH SYNONYMS ('region', 'geo')
  )

  METRICS (
    store_orders.store_revenue AS SUM(amount)
      WITH SYNONYMS ('store revenue'),
    web_orders.web_revenue AS SUM(amount)
      WITH SYNONYMS ('web revenue'),
    total_revenue AS store_orders.store_revenue + web_orders.web_revenue
      WITH SYNONYMS ('total revenue')
  )

  COMMENT = 'Same as CHANNEL_BY_REGION_SV but using an inline SQL UNION instead of a physical helper view.';
