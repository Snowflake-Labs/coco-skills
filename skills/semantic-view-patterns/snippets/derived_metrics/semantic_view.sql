-- Derived Metrics: Semantic View DDL

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.CHANNEL_SALES_SV

  TABLES (
    dim_date    PRIMARY KEY (date_id),
    store_sales,
    web_sales,
    catalog_sales
  )

  RELATIONSHIPS (
    store_to_date   AS store_sales(date_id)   REFERENCES dim_date,
    web_to_date     AS web_sales(date_id)     REFERENCES dim_date,
    catalog_to_date AS catalog_sales(date_id) REFERENCES dim_date
  )

  DIMENSIONS (
    dim_date.year    AS year    WITH SYNONYMS ('year'),
    dim_date.quarter AS quarter WITH SYNONYMS ('quarter', 'qtr'),
    dim_date.month   AS month   WITH SYNONYMS ('month')
  )

  METRICS (
    store_sales.store_revenue AS SUM(revenue)
      WITH SYNONYMS ('store sales', 'store revenue', 'brick and mortar revenue'),
    web_sales.web_revenue AS SUM(revenue)
      WITH SYNONYMS ('web sales', 'online revenue', 'e-commerce revenue'),
    catalog_sales.catalog_revenue AS SUM(revenue)
      WITH SYNONYMS ('catalog sales', 'catalog revenue', 'mail order revenue'),

    -- Cross-table derived metric: no table prefix; references per-channel metrics by name
    total_revenue AS store_sales.store_revenue + web_sales.web_revenue + catalog_sales.catalog_revenue
      WITH SYNONYMS ('total sales', 'all channel revenue', 'combined revenue'),

    -- Ratios/% of total — derived metrics using the total above
    store_pct_of_total AS store_sales.store_revenue / total_revenue
      WITH SYNONYMS ('store share', 'store contribution', '% from store'),
    web_pct_of_total AS web_sales.web_revenue / total_revenue
      WITH SYNONYMS ('web share', 'web contribution', '% from web'),
    catalog_pct_of_total AS catalog_sales.catalog_revenue / total_revenue
      WITH SYNONYMS ('catalog share', 'catalog contribution', '% from catalog')
  )

  COMMENT = 'Multi-channel revenue analytics. Demonstrates cross-table derived metrics (total_revenue = sum of three channels) and ratio metrics (% of total per channel).'

  AI_SQL_GENERATION 'Use total_revenue for combined across all channels. Use per-channel metrics (store_revenue, web_revenue, catalog_revenue) for channel comparison. Percent-of-total metrics (store_pct_of_total etc.) show channel mix as decimals — multiply by 100 for percentages. All metrics are combinable with dim_date dimensions for time breakdowns.';
