-- Variables: Semantic View DDL

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.PRODUCT_PERFORMANCE_SV

  TABLES (product_sales)

  VARIABLES (
    -- Pattern 1: Adjustable scoring weights for performance composite index
    price_weight   NUMBER(3,2) DEFAULT 0.4,
    rating_weight  NUMBER(3,2) DEFAULT 0.6,

    -- Pattern 2: Adjustable tier boundaries for price bucketing
    premium_threshold NUMBER(10,2) DEFAULT 500.00,
    budget_threshold  NUMBER(10,2) DEFAULT 100.00,

    -- Pattern 3: Date window for "recent sales" analysis
    recent_days   INTEGER DEFAULT 90,
    analysis_date DATE    DEFAULT CURRENT_DATE()
  )

  DIMENSIONS (
    product_sales.product_name AS product_name
      WITH SYNONYMS ('product', 'name'),
    product_sales.category AS category
      WITH SYNONYMS ('category', 'product category'),

    -- Dynamic price tier — bucket boundaries come from VARIABLES
    product_sales.price_tier AS (
      CASE
        WHEN product_sales.unit_price >= premium_threshold THEN 'premium'
        WHEN product_sales.unit_price >= budget_threshold  THEN 'mid-range'
        ELSE                                                    'budget'
      END
    )
    WITH SYNONYMS ('tier', 'price tier', 'price segment'),

    -- Dynamic recency flag — uses analysis_date and recent_days variables
    product_sales.is_recent AS (
      product_sales.sale_date >= DATEADD('day', -recent_days, analysis_date)
    )
    WITH SYNONYMS ('recent', 'is recent sale')
  )

  METRICS (
    product_sales.total_sales AS COUNT(sale_id)
      WITH SYNONYMS ('sales count', 'number of sales'),
    product_sales.total_revenue AS SUM(unit_price * quantity)
      WITH SYNONYMS ('revenue', 'total revenue'),
    product_sales.avg_rating AS AVG(customer_rating)
      WITH SYNONYMS ('rating', 'average rating'),

    -- Composite score blending price and rating via weighted VARIABLES
    product_sales.performance_score AS (
        price_weight * AVG(unit_price) / MAX(unit_price)
        + rating_weight * AVG(customer_rating) / 5.0
    )
    WITH SYNONYMS ('score', 'performance', 'composite score')
  )

  COMMENT = 'Product performance analytics with runtime-configurable variables. Scoring weights, price tier boundaries, and recency windows are all adjustable at query time without changing the SV DDL.'

  AI_SQL_GENERATION 'Variables can be overridden at query time using VARIABLES key => value. Default weights: price_weight=0.4, rating_weight=0.6. Default tiers: budget <$100, mid-range $100-$500, premium >$500. Use price_tier dimension to segment by dynamically-defined price buckets. Use is_recent to filter to recent products.';
