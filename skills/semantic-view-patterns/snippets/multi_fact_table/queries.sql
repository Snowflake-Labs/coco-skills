-- Multi-Fact Table: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES
-- ============================================================

-- 1. Store vs web revenue by category (cross-fact comparison)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.MULTI_CHANNEL_SV
    DIMENSIONS dim_product.category
    METRICS channel_store_sales.store_revenue, channel_web_sales.web_revenue, total_gross_revenue
)
ORDER BY total_gross_revenue DESC;


-- 2. Gross vs net revenue by month (returns subtracted)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.MULTI_CHANNEL_SV
    DIMENSIONS channel_dim_date.month
    METRICS total_gross_revenue, channel_returns.total_returns, net_revenue
)
ORDER BY month;


-- 3. Return rate by product
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.MULTI_CHANNEL_SV
    DIMENSIONS dim_product.product_name
    METRICS channel_store_sales.store_quantity, channel_returns.return_quantity
)
ORDER BY product_name;


-- 4. Brand quarterly performance
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.MULTI_CHANNEL_SV
    DIMENSIONS dim_product.brand, channel_dim_date.quarter
    METRICS net_revenue, store_share
)
ORDER BY quarter, brand;


-- ============================================================
-- MULTI-FACT KEY CONCEPTS
-- ============================================================

-- Each fact table is independent — a query requesting only store_revenue
-- will only involve channel_store_sales in the generated SQL. The web and
-- returns tables are NOT joined unless their metrics or dimensions are requested.

-- Cross-fact derived metrics (total_gross_revenue, net_revenue) will trigger
-- a join/union across the relevant fact tables when queried.

-- Shared dimensions (dim_product, channel_dim_date) work as a fan-out:
-- the SV resolves aggregates per-fact then joins them on the shared dim keys.
-- This is semantically equivalent to a fanout query pattern in standard SQL.
