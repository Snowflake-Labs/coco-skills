-- Range Join Example: Semantic View DDL
-- Run schema.sql and seed_data.sql first.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.ORDERS_BY_SEGMENT

  TABLES (
    orders AS SNIPPETS.PUBLIC.ORDERS
      PRIMARY KEY (ORDER_ID),

    customer_segments AS SNIPPETS.PUBLIC.CUSTOMER_SEGMENTS
      PRIMARY KEY (SEGMENT_ID)
      UNIQUE (CUSTOMER_ID, VALID_FROM, VALID_TO)
      CONSTRAINT segment_period DISTINCT RANGE BETWEEN VALID_FROM AND VALID_TO EXCLUSIVE
  )

  RELATIONSHIPS (
    -- Compound key: match on customer_id AND the order_date falling within the segment's valid range
    orders_to_segment AS orders(CUSTOMER_ID, ORDER_DATE)
      REFERENCES customer_segments(CUSTOMER_ID, BETWEEN VALID_FROM AND VALID_TO EXCLUSIVE)
  )

  FACTS (
    orders.order_revenue AS ORDER_AMOUNT
      COMMENT = 'Order amount in USD'
  )

  DIMENSIONS (
    orders.order_id      AS ORDER_ID,
    orders.customer_id   AS CUSTOMER_ID,
    orders.order_date    AS ORDER_DATE,

    customer_segments.segment    AS SEGMENT
      WITH SYNONYMS ('tier', 'subscription tier', 'plan', 'customer plan')
      COMMENT = 'Subscription tier active at time of order (historically resolved via range join)',
    customer_segments.valid_from AS VALID_FROM
      COMMENT = 'Start of this segment period (inclusive)',
    customer_segments.valid_to   AS VALID_TO
      COMMENT = 'End of this segment period (exclusive; 9999-12-31 = current)'
  )

  METRICS (
    orders.total_revenue AS SUM(ORDER_AMOUNT)
      WITH SYNONYMS ('revenue', 'total sales', 'gmv')
      COMMENT = 'Sum of order amounts',

    orders.order_count AS COUNT(ORDER_ID)
      WITH SYNONYMS ('number of orders', 'orders', 'order volume')
      COMMENT = 'Number of orders'
  )

  COMMENT = 'Orders joined to the subscription tier active at time of purchase via SCD2 range relationship.'

  AI_SQL_GENERATION 'Use customer_segments.segment to break down order metrics by the subscription tier the customer was on at the time of each order. The range relationship automatically resolves the historically-correct segment — there is no need to filter on valid_from/valid_to manually. Each order maps to exactly one segment record.';
