-- Window Metrics: Semantic View DDL

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.DAILY_SALES_SV

  TABLES (
    daily_sales
  )

  FACTS (
    daily_sales.sale_date_fact AS sale_date
  )

  DIMENSIONS (
    daily_sales.date AS sale_date
      WITH SYNONYMS ('date', 'day', 'sale date'),
    daily_sales.channel AS channel
      WITH SYNONYMS ('channel', 'sales channel'),
    daily_sales.year AS YEAR(sale_date)
      WITH SYNONYMS ('year'),
    daily_sales.month AS MONTH(sale_date)
      WITH SYNONYMS ('month')
  )

  METRICS (
    -- Base metric: daily total
    -- NOTE: 'revenue' is a bare physical column name (no entity prefix).
    -- Do NOT declare revenue in FACTS \u2014 FACTS columns are "row-level" and
    -- PARTITION BY EXCLUDING will fail on any metric that references them.
    -- Always use entity prefix on the metric name (daily_sales.total_revenue)
    -- when the SV includes window metrics.
    daily_sales.total_revenue AS SUM(revenue)
      WITH SYNONYMS ('revenue', 'daily revenue'),

    daily_sales.total_quantity AS SUM(quantity)
      WITH SYNONYMS ('quantity', 'units sold'),

    -- Rolling 7-day average:
    --   PARTITION BY EXCLUDING daily_sales.date → group by everything else (e.g. channel)
    --   ORDER BY date → march forward in time
    --   RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW → 7-day window
    daily_sales.rolling_7d_avg_revenue AS
      AVG(total_revenue)
      OVER (PARTITION BY EXCLUDING daily_sales.date
            ORDER BY daily_sales.date
            RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW)
      WITH SYNONYMS ('7 day rolling average', '7-day avg', 'weekly rolling average'),

    -- LAG — value 30 rows (days) ago in the same partition
    -- Used to compare current period vs the same period last month
    daily_sales.revenue_30d_ago AS
      LAG(total_revenue, 30)
      OVER (PARTITION BY EXCLUDING daily_sales.date
            ORDER BY daily_sales.date)
      WITH SYNONYMS ('revenue 30 days ago', 'prior month revenue', 'last month revenue'),

    -- YTD: running total within each year
    --   PARTITION BY daily_sales.year → reset at year boundary
    --   ORDER BY date → accumulate forward
    --   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW → include all prior rows
    daily_sales.ytd_revenue AS
      SUM(total_revenue)
      OVER (PARTITION BY daily_sales.year
            ORDER BY daily_sales.date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      WITH SYNONYMS ('YTD revenue', 'year to date revenue', 'cumulative revenue')
  )

  COMMENT = 'Daily sales metrics demonstrating three window function patterns: rolling 7-day average, LAG for period-over-period comparison, and YTD cumulative sum.'

  AI_SQL_GENERATION 'Use rolling_7d_avg_revenue for smoothed trend analysis. Use revenue_30d_ago alongside total_revenue to compare current vs prior period (month-over-month). Use ytd_revenue for cumulative year-to-date totals. Window metrics require daily_sales.date in the DIMENSIONS clause to show day-level results. PARTITION BY EXCLUDING means "partition by all other dimensions in the query except date".';
