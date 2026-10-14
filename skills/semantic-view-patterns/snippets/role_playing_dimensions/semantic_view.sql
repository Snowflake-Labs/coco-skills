-- Role-Playing Dimensions: Semantic View DDL
--
-- Pattern: alias the same physical DIM_DATE table twice under different names.
-- Each alias gets its own dedicated logical dimension names — no USING clause needed.
--
-- Syntax reminder: entity.logical_name AS physical_column

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.ORDERS_RPD_SV

  TABLES (
    orders AS SNIPPETS.PUBLIC.ORDERS
      PRIMARY KEY (ORDER_ID)

    -- The same physical DIM_DATE table aliased under two logical names.
    -- 'order_date_dim' will be joined on ORDER_DATE.
    -- 'ship_date_dim'  will be joined on SHIP_DATE.
    -- The SV engine treats them as completely separate entities.
    , order_date_dim AS SNIPPETS.PUBLIC.DIM_DATE
        PRIMARY KEY (DATE_KEY)
        COMMENT = 'Date dimension for when the order was placed'

    , ship_date_dim AS SNIPPETS.PUBLIC.DIM_DATE
        PRIMARY KEY (DATE_KEY)
        COMMENT = 'Date dimension for when the order was shipped'
  )

  RELATIONSHIPS (
    -- orders.ORDER_DATE → the order-date role of DIM_DATE
    orders_to_order_date AS orders(ORDER_DATE)
      REFERENCES order_date_dim(DATE_KEY)

    -- orders.SHIP_DATE → the ship-date role of DIM_DATE
    -- Identical physical table; different logical role; no conflict.
    , orders_to_ship_date AS orders(SHIP_DATE)
      REFERENCES ship_date_dim(DATE_KEY)
  )

  FACTS (
    -- logical: revenue → physical column: AMOUNT
    orders.revenue AS AMOUNT
  )

  DIMENSIONS (
    -- logical: customer_name → physical: CUSTOMER_NAME
    orders.customer_name AS CUSTOMER_NAME
      WITH SYNONYMS ('customer', 'buyer', 'account')

    -- ORDER DATE role — logical names are unique per role; physical columns come from DIM_DATE
    , order_date_dim.order_year       AS YEAR
        WITH SYNONYMS ('order year', 'year ordered', 'placed year')
    , order_date_dim.order_quarter    AS QUARTER
        WITH SYNONYMS ('order quarter', 'quarter ordered')
    , order_date_dim.order_month_num  AS MONTH_NUM
        WITH SYNONYMS ('order month number', 'month number ordered')
    , order_date_dim.order_month_name AS MONTH_NAME
        WITH SYNONYMS ('order month', 'order month name', 'month ordered')

    -- SHIP DATE role — same physical columns (YEAR, QUARTER, etc.), unique logical names
    , ship_date_dim.ship_year         AS YEAR
        WITH SYNONYMS ('ship year', 'shipped year', 'fulfillment year')
    , ship_date_dim.ship_quarter      AS QUARTER
        WITH SYNONYMS ('ship quarter', 'shipped quarter')
    , ship_date_dim.ship_month_num    AS MONTH_NUM
        WITH SYNONYMS ('ship month number', 'month number shipped')
    , ship_date_dim.ship_month_name   AS MONTH_NAME
        WITH SYNONYMS ('ship month', 'ship month name', 'month shipped')
  )

  METRICS (
    -- logical: total_revenue → physical: SUM(AMOUNT)
    orders.total_revenue AS SUM(AMOUNT)
      WITH SYNONYMS ('revenue', 'sales', 'total sales')
      COMMENT = 'Sum of order amounts'

    -- logical: order_count → physical: COUNT(ORDER_ID)
    , orders.order_count AS COUNT(ORDER_ID)
      WITH SYNONYMS ('orders', 'order count', 'number of orders')
      COMMENT = 'Number of orders'
  )

  COMMENT = 'Orders with two independent date roles — order date and ship date — both backed by the same physical DIM_DATE table. Demonstrates role-playing dimensions: aliasing a dimension table multiple times so each role gets its own logical dimension names without any USING clause.'

  AI_SQL_GENERATION 'This SV uses two aliases of the same physical DIM_DATE table to model order date and ship date as independent roles.

ORDER DATE dimensions: order_date_dim.order_year, order_date_dim.order_quarter, order_date_dim.order_month_num, order_date_dim.order_month_name
SHIP DATE dimensions:  ship_date_dim.ship_year, ship_date_dim.ship_quarter, ship_date_dim.ship_month_num, ship_date_dim.ship_month_name

Use ORDER date dimensions when the question is about when orders were placed.
Use SHIP date dimensions when the question is about when orders shipped or were fulfilled.

You can combine both in one query (e.g. order_date_dim.order_month_name + ship_date_dim.ship_month_name) to see the cross-tab of order date vs ship date — useful for lead-time or fulfillment lag analysis.';
