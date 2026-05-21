-- Multi-Fact Table: Semantic View DDL

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.MULTI_CHANNEL_SV

  TABLES (
    dim_product     PRIMARY KEY (product_id),
    channel_dim_date PRIMARY KEY (date_id),
    channel_store_sales,
    channel_web_sales,
    channel_returns
  )

  RELATIONSHIPS (
    -- Store sales joins to both shared dimensions
    store_to_date    AS channel_store_sales(date_id)    REFERENCES channel_dim_date,
    store_to_product AS channel_store_sales(product_id) REFERENCES dim_product,

    -- Web sales joins to both shared dimensions
    web_to_date      AS channel_web_sales(date_id)      REFERENCES channel_dim_date,
    web_to_product   AS channel_web_sales(product_id)   REFERENCES dim_product,

    -- Returns joins to both shared dimensions
    returns_to_date    AS channel_returns(date_id)    REFERENCES channel_dim_date,
    returns_to_product AS channel_returns(product_id) REFERENCES dim_product
  )

  DIMENSIONS (
    dim_product.category    AS category WITH SYNONYMS ('category', 'product category'),
    dim_product.brand       AS brand    WITH SYNONYMS ('brand'),
    dim_product.product_name AS product_name WITH SYNONYMS ('product'),
    channel_dim_date.year   AS year     WITH SYNONYMS ('year'),
    channel_dim_date.quarter AS quarter WITH SYNONYMS ('quarter', 'qtr'),
    channel_dim_date.month  AS month    WITH SYNONYMS ('month')
  )

  METRICS (
    channel_store_sales.store_revenue AS SUM(revenue)
      WITH SYNONYMS ('store sales', 'store revenue'),
    channel_store_sales.store_quantity AS SUM(quantity)
      WITH SYNONYMS ('store units', 'units sold in store'),

    channel_web_sales.web_revenue AS SUM(revenue)
      WITH SYNONYMS ('web sales', 'online revenue'),
    channel_web_sales.web_quantity AS SUM(quantity)
      WITH SYNONYMS ('web units', 'units sold online'),

    channel_returns.total_returns AS SUM(amount)
      WITH SYNONYMS ('returns', 'total returned amount'),
    channel_returns.return_quantity AS SUM(quantity)
      WITH SYNONYMS ('returned units', 'return volume'),

    -- Cross-fact derived metric: combines revenue from both channel fact tables
    total_gross_revenue AS channel_store_sales.store_revenue + channel_web_sales.web_revenue
      WITH SYNONYMS ('gross revenue', 'combined revenue', 'all channels revenue'),

    -- Net revenue = gross minus returns
    net_revenue AS total_gross_revenue - channel_returns.total_returns
      WITH SYNONYMS ('net revenue', 'revenue after returns'),

    -- Store share of total channel revenue
    store_share AS channel_store_sales.store_revenue / total_gross_revenue
      WITH SYNONYMS ('store contribution', 'store share')
  )

  COMMENT = 'Multi-fact table SV: store sales, web sales, and returns as three independent fact tables sharing a product and date dimension. Demonstrates cross-fact derived metrics (total_gross_revenue, net_revenue).'

  AI_SQL_GENERATION 'This SV has three fact tables: channel_store_sales, channel_web_sales, and channel_returns. Use total_gross_revenue for combined channel sales. Use net_revenue for returns-adjusted revenue. Use store_share for channel mix. All metrics can be broken down by dim_product and channel_dim_date dimensions.';
