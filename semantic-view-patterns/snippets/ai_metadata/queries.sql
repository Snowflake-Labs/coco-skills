-- AI Metadata: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES (standard SEMANTIC_VIEW queries)
-- ============================================================

-- 1. Order count by customer name (matches the VQR exactly)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_AI_SV
    DIMENSIONS ai_customers.customer_name
    METRICS ai_orders.order_count
)
ORDER BY order_count DESC;


-- 2. Revenue by region
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_AI_SV
    DIMENSIONS ai_customers.region
    METRICS ai_orders.total_revenue
)
ORDER BY total_revenue DESC;


-- 3. Monthly revenue trend
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_AI_SV
    DIMENSIONS ai_orders.order_month
    METRICS ai_orders.total_revenue
)
ORDER BY order_month;


-- ============================================================
-- AI METADATA IN ACTION
-- ============================================================

-- The AI_SQL_GENERATION instructions steer the LLM's query construction:
--   - "Round amounts to 2 decimal places" → AI will use ROUND(amount, 2)
--   - "Never include refunded orders" → AI adds WHERE status != 'refunded'

-- The AI_QUESTION_CATEGORIZATION instructions enable pre-query steering:
--   - "Reject questions about internal cost structure" → AI responds with
--     a refusal or redirection instead of generating SQL

-- AI_VERIFIED_QUERIES gives the engine pre-approved SQL to use verbatim
-- when a question closely matches — bypassing AI SQL generation entirely.

-- To retrieve the VQRs from the DDL:
SHOW SEMANTIC VIEWS LIKE 'ORDERS_AI_SV' IN SNIPPETS.PUBLIC;
DESCRIBE SEMANTIC VIEW SNIPPETS.PUBLIC.ORDERS_AI_SV;


-- ============================================================
-- PHYSICAL SQL VQR vs SEMANTIC_VIEW() VQR
-- ============================================================

-- Physical SQL VQR (in the DDL comments):
-- Works in AUTO mode only (Cortex Analyst backend).
-- SELECT ai_customers.customer_name, COUNT(ai_orders.order_id)...

-- SEMANTIC_VIEW() SQL VQR (in the DDL above):
-- Works in both AUTO mode (Cortex Analyst) and REQUIRE mode (direct SEMANTIC_VIEW() invocation).
-- SELECT * FROM SEMANTIC_VIEW(ORDERS_AI_SV METRICS ... DIMENSIONS ...)

-- Use SEMANTIC_VIEW() format in VQRs when you want them to work across both modes.
