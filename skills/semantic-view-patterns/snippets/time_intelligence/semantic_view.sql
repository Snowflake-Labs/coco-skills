-- Time Intelligence: Semantic View DDL
-- Run schema.sql and seed_data.sql first.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.TIME_INTELLIGENCE_SV

  TABLES (
    -- Calendar dimension (one row per month)
    calendar AS SNIPPETS.PUBLIC.DIM_CALENDAR
      PRIMARY KEY (MONTH)

    -- Current-period sales fact
    , sales AS SNIPPETS.PUBLIC.FACT_SALES
      PRIMARY KEY (ROW_ID)

    -- Role-playing alias: same physical table, date will be shifted +1 month
    -- When you query by calendar.month = '2024-03', this entity returns Feb 2024 rows
    , sales_lm AS SNIPPETS.PUBLIC.FACT_SALES
      PRIMARY KEY (ROW_ID)
      COMMENT = 'Same-period last month: FACT_SALES with date shifted forward 1 month'

    -- Role-playing alias: same physical table, date will be shifted +1 year
    -- When you query by calendar.month = '2024-03', this entity returns Mar 2023 rows
    , sales_ly AS SNIPPETS.PUBLIC.FACT_SALES
      PRIMARY KEY (ROW_ID)
      COMMENT = 'Same-period last year (SPLY): FACT_SALES with date shifted forward 1 year'
  )

  RELATIONSHIPS (
    -- Current period joins directly on SALE_MONTH
    sales_to_calendar AS sales(SALE_MONTH) REFERENCES calendar(MONTH)

    -- LM alias joins on the COMPUTED fact sale_month_shifted_lm (= SALE_MONTH + 1 month)
    -- This makes "Feb 2024" rows appear under "Mar 2024" in queries
    , sales_lm_to_calendar AS sales_lm(sale_month_shifted_lm) REFERENCES calendar(MONTH)

    -- LY alias joins on the COMPUTED fact sale_month_shifted_ly (= SALE_MONTH + 1 year)
    -- This makes "Mar 2023" rows appear under "Mar 2024" in queries
    , sales_ly_to_calendar AS sales_ly(sale_month_shifted_ly) REFERENCES calendar(MONTH)
  )

  FACTS (
    sales.revenue AS REVENUE
    , sales.units AS UNITS

    -- Computed FK for last-month join: shift the actual date forward by 1 month.
    -- The relationship uses this column to join to calendar(MONTH), so when
    -- calendar.MONTH = '2024-03-01', we need DATEADD('month',1,SALE_MONTH) = '2024-03-01'
    -- → SALE_MONTH = '2024-02-01' → last month's rows appear in March's bucket.
    , sales_lm.sale_month_shifted_lm AS DATEADD('month', 1, SALE_MONTH)
      COMMENT = 'Computed FK: SALE_MONTH + 1 month — shifts LM rows into the current period bucket'

    -- Same idea, 1 year forward: SALE_MONTH = '2023-03-01' → appears in March 2024 bucket.
    , sales_ly.sale_month_shifted_ly AS DATEADD('year', 1, SALE_MONTH)
      COMMENT = 'Computed FK: SALE_MONTH + 1 year — shifts LY rows into the current period bucket'
  )

  DIMENSIONS (
    calendar.month AS MONTH
      WITH SYNONYMS ('period', 'month', 'sale month')
      COMMENT = 'Calendar month (first day of month)'
    , calendar.month_name AS MONTH_NAME
      WITH SYNONYMS ('month name')
    , calendar.quarter AS QUARTER
      WITH SYNONYMS ('quarter', 'qtr')
    , calendar.year AS YEAR
      WITH SYNONYMS ('year')
    , sales.region AS REGION
      WITH SYNONYMS ('region', 'sales region', 'territory')
  )

  METRICS (
    -- ── Current period ──────────────────────────────────────────────────────
    sales.revenue AS SUM(revenue)
      WITH SYNONYMS ('revenue', 'sales', 'net sales', 'total revenue')
      COMMENT = 'Total revenue in the selected period'

    , sales.units AS SUM(units)
      WITH SYNONYMS ('units', 'units sold', 'quantity')
      COMMENT = 'Total units sold in the selected period'

    -- ── Same period last month (SPLM) ────────────────────────────────────────
    , sales_lm.revenue_lm AS SUM(revenue)
      WITH SYNONYMS ('revenue last month', 'LM revenue', 'prior month revenue', 'SPLM')
      COMMENT = 'Revenue for the same period last month'

    -- ── Same period last year (SPLY) ─────────────────────────────────────────
    , sales_ly.revenue_ly AS SUM(revenue)
      WITH SYNONYMS ('revenue last year', 'LY revenue', 'prior year revenue', 'SPLY', 'same period last year')
      COMMENT = 'Revenue for the same period last year'

    -- ── Month-over-month (cross-entity derived metrics) ──────────────────────
    , mom_change AS sales.revenue - sales_lm.revenue_lm
      WITH SYNONYMS ('MoM change', 'month over month change', 'monthly delta')
      COMMENT = 'Revenue change vs prior month (positive = growth)'

    , mom_pct AS DIV0(sales.revenue - sales_lm.revenue_lm, sales_lm.revenue_lm) * 100
      WITH SYNONYMS ('MoM %', 'MoM growth', 'month over month growth rate')
      COMMENT = 'Revenue % change vs prior month'

    -- ── Year-over-year (cross-entity derived metrics) ────────────────────────
    , yoy_change AS sales.revenue - sales_ly.revenue_ly
      WITH SYNONYMS ('YoY change', 'year over year change', 'annual delta')
      COMMENT = 'Revenue change vs same period last year (positive = growth)'

    , yoy_pct AS DIV0(sales.revenue - sales_ly.revenue_ly, sales_ly.revenue_ly) * 100
      WITH SYNONYMS ('YoY %', 'YoY growth', 'year over year growth rate')
      COMMENT = 'Revenue % change vs same period last year'
  )

  COMMENT = 'Monthly sales with time-shifted role-playing aliases for same-period-last-month (SPLM) and same-period-last-year (SPLY) comparisons. Demonstrates the computed-FK pattern for time intelligence without window functions or pre-aggregated ETL views.'

  AI_SQL_GENERATION 'This semantic view demonstrates two time intelligence patterns using role-playing logical table aliases:

1. sales_lm: same physical table as sales, but SALE_MONTH is shifted forward 1 month via a computed FACT (sale_month_shifted_lm). When you group by calendar.month = March 2024, this entity returns February 2024 rows — enabling MoM comparison without window functions.

2. sales_ly: same physical table as sales, but SALE_MONTH is shifted forward 1 year via a computed FACT (sale_month_shifted_ly). When you group by calendar.year = 2024, this entity returns 2023 rows aligned to the same calendar periods — enabling SPLY/YoY comparison.

Cross-entity derived metrics (mom_change, mom_pct, yoy_change, yoy_pct) reference metrics from both the current and prior-period entities.

Always include calendar.year and/or calendar.month in DIMENSIONS when querying period-over-period metrics so results are aligned by period. For YoY queries, grouping by calendar.year returns annual totals; grouping by calendar.year + calendar.month returns month-level comparisons.';
