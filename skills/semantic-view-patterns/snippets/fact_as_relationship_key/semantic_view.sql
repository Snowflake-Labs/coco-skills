-- Fact as Relationship Key: Semantic View DDL

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.SALES_VS_BUDGET_SV

  TABLES (
    products        PRIMARY KEY (product_id),
    fiscal_quarters PRIMARY KEY (fiscal_quarter_key),
    sales           PRIMARY KEY (sale_id)
  )

  RELATIONSHIPS (
    -- Standard FK: each sale references a product
    sales_to_products AS sales(product_id) REFERENCES products,

    -- Computed FK: sales has no fiscal_quarter_key column, so we derive
    -- it as a FACT below and use that fact as the join key here.
    sales_to_quarters AS sales(sales.fiscal_qtr_key) REFERENCES fiscal_quarters
  )

  FACTS (
    -- Computed FK fact: derives the fiscal quarter key from sale_date.
    -- No physical column on the sales table — the engine evaluates this
    -- expression per row and uses the result to resolve the join above.
    sales.fiscal_qtr_key AS CONCAT(
        TO_VARCHAR(YEAR(sale_date)),
        '-Q',
        TO_VARCHAR(QUARTER(sale_date))
    )
  )

  DIMENSIONS (
    products.category      AS category
      WITH SYNONYMS ('product category', 'category'),
    products.product_name  AS product_name
      WITH SYNONYMS ('product', 'item'),

    fiscal_quarters.quarter_name  AS quarter_name
      WITH SYNONYMS ('quarter', 'fiscal quarter', 'period'),
    fiscal_quarters.fiscal_year   AS fiscal_year
      WITH SYNONYMS ('year', 'fy'),

    sales.sale_date AS sale_date
      WITH SYNONYMS ('date', 'transaction date')
  )

  METRICS (
    sales.total_revenue AS SUM(amount)
      WITH SYNONYMS ('revenue', 'sales', 'total sales'),
    fiscal_quarters.total_budget AS SUM(budget_amount)
      WITH SYNONYMS ('budget', 'target', 'quota')
  )

  COMMENT = 'Sales vs fiscal-quarter budgets. Demonstrates joining a fact table to a dimension using a computed FK fact (CONCAT of YEAR + QUARTER) when no physical FK column exists on the source table.'

  AI_SQL_GENERATION 'Use fiscal_quarters.quarter_name or fiscal_quarters.fiscal_year to break down results by time period. Use products.category to compare Hardware vs Services. To compare revenue against budget, query both sales.total_revenue and fiscal_quarters.total_budget together.';
