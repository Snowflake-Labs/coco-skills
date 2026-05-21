-- ASOF Join Example: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES
-- ============================================================

-- 1. Revenue by zip code (historically resolved address at order time)
--    Expected:
--      90001  $300   (Ord100 + Ord101, Jan address)
--      90002  $700   (Ord102 + Ord103, Apr address)
--      90003  $500   (Ord104, Jul address)
--      90010  $600   (Ord105, Cust002)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_BY_ADDRESS
    DIMENSIONS Customer_address.zip
    METRICS Orders.total_revenue
)
ORDER BY zip;


-- 2. Revenue by customer name and zip — shows address transitions clearly
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_BY_ADDRESS
    DIMENSIONS Customer_name.name, Customer_address.zip
    METRICS Orders.total_revenue
)
ORDER BY name, zip;


-- 3. Monthly revenue with zip — shows Mary moving between addresses each month
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ORDERS_BY_ADDRESS
    DIMENSIONS Customer_name.name, Orders.year_month, Customer_address.zip
    METRICS Orders.total_revenue
)
ORDER BY year_month;


-- ============================================================
-- THE MISTAKE THIS PATTERN PREVENTS
-- ============================================================

-- WRONG: Join on customer ID only (ignores address history)
-- Returns one "current" address per customer — all of Mary's orders
-- get attributed to her most recent zip (90003), which is wrong for
-- orders placed before she moved.
SELECT
    o.o_custid,
    a.ca_zipcode AS current_zip,   -- always most recent
    SUM(o.o_amount) AS wrong_revenue
FROM SNIPPETS.PUBLIC.Orders o
JOIN (
    SELECT ca_custid, ca_zipcode,
           ROW_NUMBER() OVER (PARTITION BY ca_custid ORDER BY ca_start_date DESC) AS rn
    FROM SNIPPETS.PUBLIC.Customer_address
) a ON o.o_custid = a.ca_custid AND a.rn = 1
GROUP BY 1, 2
ORDER BY 1;
-- Mary's $300 (zip 90001) + $700 (90002) all attributed to 90003
