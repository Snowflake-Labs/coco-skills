-- Tags: Semantic View DDL

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.CHANNEL_SALES_TAGGED_SV

  TABLES (
    tag_dim_date    PRIMARY KEY (date_id),
    tag_store_sales,
    tag_web_sales
  )

  RELATIONSHIPS (
    store_to_date AS tag_store_sales(date_id) REFERENCES tag_dim_date,
    web_to_date   AS tag_web_sales(date_id)   REFERENCES tag_dim_date
  )

  DIMENSIONS (
    tag_dim_date.year  AS year  WITH SYNONYMS ('year'),
    tag_dim_date.month AS month WITH SYNONYMS ('month')
  )

  METRICS (
    tag_store_sales.store_revenue AS SUM(revenue)
      WITH SYNONYMS ('store revenue', 'store sales')
      WITH TAG (metric_owner = 'finance_team', metric_status = 'certified', metric_domain = 'sales'),

    tag_store_sales.store_quantity AS SUM(quantity)
      WITH SYNONYMS ('store units')
      WITH TAG (metric_owner = 'finance_team', metric_status = 'certified', metric_domain = 'sales'),

    tag_web_sales.web_revenue AS SUM(revenue)
      WITH SYNONYMS ('web revenue', 'online sales')
      WITH TAG (metric_owner = 'growth_team', metric_status = 'in_development', metric_domain = 'sales'),

    tag_web_sales.web_quantity AS SUM(quantity)
      WITH SYNONYMS ('web units', 'online units')
      WITH TAG (metric_owner = 'growth_team', metric_status = 'in_development', metric_domain = 'sales'),

    -- Cross-channel derived metric — no entity prefix; tagged as well
    total_channel_revenue AS tag_store_sales.store_revenue + tag_web_sales.web_revenue
      WITH SYNONYMS ('total revenue', 'all channel revenue')
      WITH TAG (metric_owner = 'finance_team', metric_status = 'certified', metric_domain = 'sales')
  )

  COMMENT = 'Demonstrates WITH TAG on metrics. Tags are queryable via tag_references() to discover metric ownership, certification status, and business domain.';
