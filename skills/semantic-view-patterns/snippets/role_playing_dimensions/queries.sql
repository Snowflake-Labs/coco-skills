-- Role-Playing Dimensions: Queries
--
-- Demonstrates how the same DIM_DATE table, aliased under two logical names,
-- produces completely independent date dimensions for order date and ship date.
--
-- In SEMANTIC_VIEW() queries, dimensions are referenced as entity_alias.logical_name.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;


-- ============================================================
-- WORKING QUERIES
-- ============================================================

-- 1. Revenue by ORDER month — when were orders placed?
--
--    Expected (4 rows):
--      November  2024   $1,300   2 orders   (orders 1 + 2)
--      December  2024   $1,500   2 orders   (orders 3 + 4)
--      January   2025   $1,350   2 orders   (orders 5 + 6)
--      February  2025   $1,750   2 orders   (orders 7 + 8)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_RPD_SV
    DIMENSIONS order_date_dim.order_year,
               order_date_dim.order_month_num,
               order_date_dim.order_month_name
    METRICS    orders.total_revenue, orders.order_count
)
ORDER BY order_year, order_month_num;


-- 2. Revenue by SHIP month — when did revenue actually leave the warehouse?
--
--    Expected (5 rows — order 4 crosses a year boundary):
--      November  2024     $500   1 order    (order 1)
--      December  2024   $1,100   2 orders   (orders 2 + 3)
--      January   2025   $1,650   2 orders   (orders 4 + 5)  ← Dec order shows up here
--      February  2025   $1,550   2 orders   (orders 6 + 7)
--      March     2025   $1,100   1 order    (order 8)
--
--    Note: total is still $5,900 — same revenue, different monthly distribution.
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_RPD_SV
    DIMENSIONS ship_date_dim.ship_year,
               ship_date_dim.ship_month_num,
               ship_date_dim.ship_month_name
    METRICS    orders.total_revenue, orders.order_count
)
ORDER BY ship_year, ship_month_num;


-- 3. Fulfillment lag — order_month_name and ship_month_name in the same query
--
--    Because order_date_dim and ship_date_dim are independent entities,
--    you can combine them freely. The result is a cross-tab:
--    each row shows (order_month, ship_month, revenue) for orders
--    where those two dates occur.
--
--    Expected (8 rows — one per order):
--      November 2024  → November 2024   $500    (order 1, same month)
--      November 2024  → December 2024   $800    (order 2, 1-month lag)
--      December 2024  → December 2024   $300    (order 3, same month)
--      December 2024  → January  2025   $1,200  (order 4, crosses year!)
--      January  2025  → January  2025   $450    (order 5, same month)
--      January  2025  → February 2025   $900    (order 6, 1-month lag)
--      February 2025  → February 2025   $650    (order 7, same month)
--      February 2025  → March    2025   $1,100  (order 8, 1-month lag)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_RPD_SV
    DIMENSIONS order_date_dim.order_year,
               order_date_dim.order_month_num,
               order_date_dim.order_month_name,
               ship_date_dim.ship_year,
               ship_date_dim.ship_month_num,
               ship_date_dim.ship_month_name
    METRICS    orders.total_revenue, orders.order_count
)
ORDER BY order_year, order_month_num, ship_year, ship_month_num;


-- 4. Revenue per customer broken down by both order_year and ship_year
--
--    Delta Co has one order placed in Dec 2024 but shipped in Jan 2025 —
--    watch for the (2024, 2025) cross-year row.
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_RPD_SV
    DIMENSIONS orders.customer_name,
               order_date_dim.order_year,
               ship_date_dim.ship_year
    METRICS    orders.total_revenue, orders.order_count
)
ORDER BY customer_name, order_year;


-- ============================================================
-- HOW ROLE-PLAYING DIMENSIONS WORK
-- ============================================================

-- The SV engine generates one JOIN per alias:
--
--   SELECT ...
--   FROM   ORDERS o
--   LEFT JOIN DIM_DATE odate ON o.ORDER_DATE = odate.DATE_KEY    ← order_date_dim path
--   LEFT JOIN DIM_DATE sdate ON o.SHIP_DATE  = sdate.DATE_KEY    ← ship_date_dim path
--
-- odate.YEAR  → logical name order_year   (independent GROUP BY)
-- sdate.YEAR  → logical name ship_year    (independent GROUP BY)
--
-- No USING clause needed because there is no ambiguity — each alias
-- is bound to exactly one relationship.


-- ============================================================
-- GOTCHAS
-- ============================================================

-- Using order_month_name and ship_month_name together produces a cross-tab, not a list.
-- Query 3 above returns 8 rows (one per order) rather than 4–5 rows (one per month).
-- This is correct behavior — it shows the joint distribution of (order month, ship month).
-- If you only want one date perspective, use EITHER set of dimensions, not both.

-- The multi_path_metrics approach (single alias + USING) does NOT work here.
-- That pattern requires a single dimension column shared across both paths.
-- Role-playing dimensions gives each path its own logical names — use it whenever you
-- want to independently slice by each date role.

-- NULL month names appear when an order_date or ship_date has no matching row in DIM_DATE.
-- Always ensure DIM_DATE is fully populated for all dates in the fact table.
-- A sparse DIM_DATE will cause silent NULLs in dimension columns (LEFT JOIN semantics).


-- ============================================================
-- CLEANUP — run to remove objects created by this snippet
-- ============================================================

DROP TABLE        IF EXISTS SNIPPETS.PUBLIC.ORDERS;
DROP TABLE        IF EXISTS SNIPPETS.PUBLIC.DIM_DATE;
DROP SEMANTIC VIEW IF EXISTS SNIPPETS.PUBLIC.ORDERS_RPD_SV;
