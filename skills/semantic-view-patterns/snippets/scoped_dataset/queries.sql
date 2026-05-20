-- Scoped Dataset: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- QUERIES ON ENTERPRISE SV (scoped by lob='Enterprise')
-- Only the 4 Enterprise transactions are visible.
-- ============================================================

-- 1. Total revenue for Enterprise — the lob filter is baked in
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ENTERPRISE_ORDERS_SV
    METRICS ent_orders.total_revenue
);
-- Expected: 5000 + 3200 + 7500 + 4100 = $19,800


-- 2. Revenue by region (Enterprise only)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ENTERPRISE_ORDERS_SV
    DIMENSIONS ent_orders.region
    METRICS ent_orders.total_revenue
)
ORDER BY total_revenue DESC;
-- Expected: East=$11,600, West=$8,200


-- 3. Customer name + revenue (uses the joined-in customer data)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ENTERPRISE_ORDERS_SV
    DIMENSIONS ent_orders.customer_name
    METRICS ent_orders.total_revenue
)
ORDER BY total_revenue DESC;


-- ============================================================
-- QUERIES ON RETAIL SV (scoped by lob='Retail')
-- Only 4 Retail transactions are visible.
-- ============================================================

-- 4. Retail revenue total
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.RETAIL_ORDERS_SV
    METRICS retail_orders.total_revenue
);
-- Expected: 120 + 95 + 210 + 175 = $600


-- 5. Retail revenue by region
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.RETAIL_ORDERS_SV
    DIMENSIONS retail_orders.region
    METRICS retail_orders.total_revenue
)
ORDER BY region;


-- ============================================================
-- VERIFY THE INLINE FILTER VIA DESCRIBE
-- ============================================================

-- DESCRIBE shows the DEFINITION property (the inline SQL) instead of
-- BASE_TABLE_NAME — confirming the filter is embedded in the SV DDL.
DESCRIBE SEMANTIC VIEW SNIPPETS.PUBLIC.ENTERPRISE_ORDERS_SV;
-- Look for: object_kind=TABLE, property=DEFINITION,
--           property_value=SELECT * FROM sales_transactions WHERE lob = 'Enterprise'

DESCRIBE SEMANTIC VIEW SNIPPETS.PUBLIC.RETAIL_ORDERS_SV;


-- ============================================================
-- THE ALTERNATIVE WITHOUT THIS PATTERN (for comparison)
-- ============================================================
-- Without inline dataset, you'd need to:
-- 1. CREATE VIEW enterprise_sales AS SELECT * FROM sales_transactions WHERE lob = 'Enterprise';
-- 2. Reference enterprise_sales in the SV's TABLES clause
--
-- The inline approach avoids creating an intermediate view object,
-- keeps the filter co-located with the SV definition, and simplifies governance.
