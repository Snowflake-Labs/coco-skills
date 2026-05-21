-- Range Join Example: Queries
-- Run schema.sql, seed_data.sql, and semantic_view.sql first.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES
-- ============================================================

-- 1. Revenue by subscription tier (historically resolved)
--    Each order is matched to the tier the customer was on at time of purchase.
--
--    Expected:
--      Enterprise   $998.00
--      Growth       $298.00
--      Free         $196.00
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_BY_SEGMENT
    DIMENSIONS customer_segments.segment
    METRICS orders.total_revenue
)
ORDER BY total_revenue DESC;


-- 2. Order count by tier
--
--    Expected:
--      Free         4
--      Enterprise   2
--      Growth       2
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_BY_SEGMENT
    DIMENSIONS customer_segments.segment
    METRICS orders.order_count
)
ORDER BY order_count DESC;


-- 3. Revenue by customer and tier (shows the historical transitions clearly)
--
--    Expected:
--      C001  Free         $49
--      C001  Growth       $149
--      C001  Enterprise   $499
--      C002  Growth       $149
--      C002  Enterprise   $499
--      C003  Free         $147
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_BY_SEGMENT
    DIMENSIONS orders.customer_id, customer_segments.segment
    METRICS orders.total_revenue
)
ORDER BY customer_id, total_revenue;


-- 4. Revenue for Enterprise tier only
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_BY_SEGMENT
    DIMENSIONS customer_segments.segment
    METRICS orders.total_revenue
    WHERE customer_segments.segment = 'Enterprise'
);


-- ============================================================
-- THE MISTAKE THIS PATTERN PREVENTS
-- ============================================================

-- WRONG: Naive SQL join without temporal constraint
-- This joins ALL segment records for a customer to ALL their orders,
-- causing each order to appear once per segment history record (fan-out).
--
-- Result: C001 has 3 segment records → each of C001's 3 orders appears 3 times.
-- Total "revenue" = $1,492 × 3 for C001 = massively overcounted.
--
-- (Run this to see the incorrect output)
SELECT
    o.customer_id,
    cs.segment,
    SUM(o.order_amount) AS wrong_revenue
FROM SNIPPETS.PUBLIC.ORDERS o
JOIN SNIPPETS.PUBLIC.CUSTOMER_SEGMENTS cs ON o.customer_id = cs.customer_id
GROUP BY 1, 2
ORDER BY 1, 3 DESC;
-- C001 shows revenue in ALL THREE tiers, even for orders placed before they upgraded.


-- ALSO WRONG: Current-only join (loses history)
-- Joining only on is_current = true / max(valid_from) assigns today's tier to all orders.
-- C001's January order (correctly "Free") gets credited to "Enterprise" (their current tier).
SELECT
    o.customer_id,
    cs.segment AS current_segment,
    SUM(o.order_amount) AS wrong_revenue
FROM SNIPPETS.PUBLIC.ORDERS o
JOIN SNIPPETS.PUBLIC.CUSTOMER_SEGMENTS cs
    ON o.customer_id = cs.customer_id
    AND cs.valid_to = '9999-12-31'   -- "current record only"
GROUP BY 1, 2
ORDER BY 1;
-- All of C001's revenue ($697) attributed to Enterprise, none to Free or Growth.


-- ============================================================
-- WHAT DOESN'T WORK IN SEMANTIC_VIEW()
-- ============================================================

-- ERROR: Querying customer_segments dimensions without the orders metrics
-- (The range-joined entity doesn't have its own metrics defined)
--
-- SELECT * FROM SEMANTIC_VIEW(
--     SNIPPETS.PUBLIC.ORDERS_BY_SEGMENT
--     DIMENSIONS customer_segments.segment
--     METRICS customer_segments.some_metric    -- no metrics defined on this entity
-- );

-- NOTE: If you add a second fact table (e.g., support_tickets) to this SV that is
-- NOT directly related to customer_segments, you cannot break down support_tickets
-- metrics by customer_segments.segment. The segment dimension is only reachable
-- from entities that join to customer_segments directly.
-- Error you'd see: "The dimension entity 'CUSTOMER_SEGMENTS' must be related to
-- the base metric entity 'SUPPORT_TICKETS'"
