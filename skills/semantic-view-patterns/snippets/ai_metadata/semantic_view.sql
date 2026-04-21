-- AI Metadata: Semantic View DDL

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.ORDERS_AI_SV

  TABLES (
    ai_orders,
    ai_customers UNIQUE (customer_id)
  )

  RELATIONSHIPS (
    ai_orders(customer_id) REFERENCES ai_customers
  )

  DIMENSIONS (
    ai_customers.customer_name AS customer_name
      WITH SYNONYMS ('customer', 'name', 'who'),
    ai_customers.region AS region
      WITH SYNONYMS ('region', 'area'),
    ai_orders.status AS status
      WITH SYNONYMS ('order status', 'fulfillment status'),
    ai_orders.order_month AS DATE_TRUNC('month', order_date)
      WITH SYNONYMS ('month', 'order month')
  )

  METRICS (
    ai_orders.total_revenue AS SUM(amount)
      WITH SYNONYMS ('revenue', 'total orders', 'sales'),
    ai_orders.order_count AS COUNT(order_id)
      WITH SYNONYMS ('orders', 'number of orders', 'order volume'),
    ai_orders.avg_order_value AS AVG(amount)
      WITH SYNONYMS ('AOV', 'average order', 'average order value')
  )

  -- Steers LLM query generation style — applied to every query on this SV
  AI_SQL_GENERATION 'Always round monetary values to 2 decimal places. When asked about revenue, never include orders with status = ''refunded''. Use customer_name for customer-level breakdowns.'

  -- Steers how the LLM categorizes incoming questions — can reject/redirect
  AI_QUESTION_CATEGORIZATION 'Answer questions about revenue, orders, and customers. Politely decline questions about individual customer PII (e.g. contact details) or internal pricing margins.'

  -- Pre-approved SQL snippets used verbatim when a question closely matches.
  -- SEMANTIC_VIEW() format works in both AUTO (Cortex Analyst) and REQUIRE mode.
  -- Physical SQL format works in AUTO mode only.
  AI_VERIFIED_QUERIES (
    order_count_by_customer AS (
      QUESTION 'How many orders does each customer have?'
      VERIFIED_BY 'jklahr'
      VERIFIED_AT 1750000000
      SQL 'SELECT * FROM SEMANTIC_VIEW(SNIPPETS.PUBLIC.ORDERS_AI_SV METRICS ai_orders.order_count DIMENSIONS ai_customers.customer_name) ORDER BY order_count DESC'
    ),
    revenue_by_region AS (
      QUESTION 'What is the revenue by region?'
      VERIFIED_BY 'jklahr'
      VERIFIED_AT 1750000000
      SQL 'SELECT * FROM SEMANTIC_VIEW(SNIPPETS.PUBLIC.ORDERS_AI_SV METRICS ai_orders.total_revenue DIMENSIONS ai_customers.region) ORDER BY total_revenue DESC'
    )
  )

  COMMENT = 'Order analytics with all AI metadata: AI_SQL_GENERATION (query style hints), AI_QUESTION_CATEGORIZATION (topic steering), and AI_VERIFIED_QUERIES (pre-approved SQL for common questions).';
