-- Scoped Dataset: Semantic View DDL
-- Two SVs from one source table, each scoped to a different LOB

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- PATTERN 1: Two separate SVs, each scoped to one LOB
-- ============================================================

-- Enterprise SV — only sees transactions where lob = 'Enterprise'
-- The alias 'ent_orders' is REQUIRED when using AS (...)
CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.ENTERPRISE_ORDERS_SV
TABLES (
    ent_orders AS (
        -- This filter is embedded permanently in the SV.
        -- Enterprise SV users never see SMB or Retail rows.
        SELECT
            t.transaction_id,
            t.customer_id,
            t.order_date,
            t.amount,
            t.region,
            c.customer_name,
            c.tier
        FROM sales_transactions t
        JOIN lob_customers c ON t.customer_id = c.customer_id
        WHERE t.lob = 'Enterprise'
    ) PRIMARY KEY (transaction_id)
)
DIMENSIONS (
    ent_orders.region AS region
        WITH SYNONYMS ('region', 'geo'),
    ent_orders.customer_name AS customer_name
        WITH SYNONYMS ('customer', 'account'),
    ent_orders.order_month AS DATE_TRUNC('month', ent_orders.order_date)
        WITH SYNONYMS ('month', 'order month')
)
METRICS (
    ent_orders.total_revenue AS SUM(amount)
        WITH SYNONYMS ('revenue', 'enterprise revenue'),
    ent_orders.deal_count AS COUNT(transaction_id)
        WITH SYNONYMS ('deals', 'transactions')
)
COMMENT = 'Enterprise LOB only. Inline SQL filter (lob=''Enterprise'') + join to customer table embedded in TABLES clause. Pre-scoped — no WHERE needed in queries.';


-- Retail SV — only sees transactions where lob = 'Retail'
-- Notice: different entity alias (retail_orders), different synonyms
CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.RETAIL_ORDERS_SV
TABLES (
    retail_orders AS (
        SELECT * FROM sales_transactions
        WHERE lob = 'Retail'
    ) PRIMARY KEY (transaction_id)
)
DIMENSIONS (
    retail_orders.region AS region
        WITH SYNONYMS ('region', 'store region'),
    retail_orders.order_month AS DATE_TRUNC('month', retail_orders.order_date)
        WITH SYNONYMS ('month')
)
METRICS (
    retail_orders.total_revenue AS SUM(amount)
        WITH SYNONYMS ('revenue', 'retail revenue', 'store revenue'),
    retail_orders.transaction_count AS COUNT(transaction_id)
        WITH SYNONYMS ('transactions', 'purchases')
)
COMMENT = 'Retail LOB only. Filtered via inline SQL in TABLES clause.';


-- ============================================================
-- PATTERN 2: Pre-join two tables into one logical entity
-- The SV consumer sees 'customer_info' as a single flat entity
-- combining both customer and address columns.
-- ============================================================
-- CREATE OR REPLACE SEMANTIC VIEW my_sv
-- TABLES (
--     customer_info AS (
--         SELECT c.*, a.zipcode, a.street_addr
--         FROM customers c
--         JOIN addresses a ON c.customer_id = a.customer_id
--     ) PRIMARY KEY (customer_id) WITH SYNONYMS ('customer')
-- )
-- DIMENSIONS (
--     customer_info.customer_name AS customer_name,
--     customer_info.zipcode AS zip
-- )
-- METRICS (
--     ...
-- );
