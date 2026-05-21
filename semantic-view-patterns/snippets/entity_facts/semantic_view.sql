-- Entity Facts: Semantic View DDL

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.CUSTOMER_ORDERS_SV

  TABLES (
    customers PRIMARY KEY (customer_id),
    orders
  )

  RELATIONSHIPS (
    orders(customer_id) REFERENCES customers
  )

  FACTS (
    -- Per-order fact: accessible in WHERE and as a dimension for row-level filtering
    orders.order_amount AS amount
      WITH SYNONYMS ('order value', 'transaction amount'),

    -- Entity-level aggregated fact: aggregates up to the customer entity.
    -- This creates a single number per customer (their total lifetime spend)
    -- which can then be used in dimension CASE expressions below.
    PRIVATE customers.lifetime_value AS SUM(orders.order_amount)
      WITH SYNONYMS ('customer LTV', 'customer lifetime value', 'total spend')
  )

  DIMENSIONS (
    customers.customer_name AS customer_name
      WITH SYNONYMS ('name', 'customer'),

    -- Calculated dimension: expression evaluated row-by-row
    customers.age AS (YEAR(CURRENT_DATE()) - birth_year)
      WITH SYNONYMS ('customer age', 'age in years'),

    -- Derived dimension from an entity-level aggregated fact:
    -- lifetime_value is PRIVATE (not directly queryable) but drives this bucketing
    customers.value_segment AS (
      CASE
        WHEN customers.lifetime_value < 1000  THEN 'low'
        WHEN customers.lifetime_value <= 3000 THEN 'medium'
        ELSE                                       'high'
      END
    )
    WITH SYNONYMS ('customer tier', 'value tier', 'segment'),

    orders.order_date AS order_date
      WITH SYNONYMS ('date', 'purchase date'),

    orders.order_month AS DATE_TRUNC('month', order_date)
      WITH SYNONYMS ('month', 'order month')
  )

  METRICS (
    orders.total_revenue AS SUM(amount)
      WITH SYNONYMS ('revenue', 'total orders'),
    orders.order_count AS COUNT(order_id)
      WITH SYNONYMS ('orders', 'number of orders'),
    customers.customer_count AS COUNT(customer_id)
      WITH SYNONYMS ('customers', 'number of customers')
  )

  COMMENT = 'Customer order history. Demonstrates PRIVATE entity-level aggregated facts (lifetime_value) used to define a value_segment dimension, and a calculated age dimension using YEAR(CURRENT_DATE()).'

  AI_SQL_GENERATION 'Use customers.value_segment to segment customers by lifetime spend. Use customers.age to filter or bucket by customer age. The lifetime_value fact is PRIVATE — it powers value_segment internally but cannot be queried directly.';
