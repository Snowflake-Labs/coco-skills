-- Row Access Policy Example: Queries
--
-- Demonstrates the NULL-row problem and both workarounds in sequence.
-- Run schema.sql, seed_data.sql, and semantic_view.sql first.
--
-- Roles used:
--   REGION_A_ANALYST  — can see R001 (Northeast) and R002 (Southeast) only
--   REGION_B_ANALYST  — can see R003 (Northwest) and R004 (Southwest) only
--   SYSADMIN          — unrestricted access

-- Queries use whatever warehouse the analyst roles have been granted USAGE on.
-- In Tutorial mode, this is handled by the skill before running queries.
-- If running standalone: GRANT USAGE ON WAREHOUSE <your_wh> TO ROLE REGION_A_ANALYST;
--                        GRANT USAGE ON WAREHOUSE <your_wh> TO ROLE REGION_B_ANALYST;
USE DATABASE RAP_TEST;
USE SCHEMA PUBLIC;


-- ============================================================
-- BASELINE: SYSADMIN sees all four regions correctly
-- ============================================================

USE SECONDARY ROLES NONE;
USE ROLE SYSADMIN;

-- Expected: 4 rows, total $4,600
SELECT * FROM SEMANTIC_VIEW(
    RAP_TEST.PUBLIC.SALES_BY_REGION_SV
    DIMENSIONS regions.region_name
    METRICS orders.total_revenue, orders.order_count
)
ORDER BY total_revenue DESC;
-- Northeast  $1,250  2
-- Southwest  $1,200  2
-- Northwest  $1,400  2
-- Southeast    $750  2


-- ============================================================
-- ANTI-PATTERN: RAP on dimension table only
-- Expected problem: REGION_A sees 2 allowed regions PLUS a NULL row
-- that aggregates all revenue from the filtered-out regions (R003+R004).
-- ============================================================

USE ROLE REGION_A_ANALYST;
USE SECONDARY ROLES NONE;

-- Expected (broken): 3 rows instead of 2
--   Northeast  $1,250  2
--   Southeast    $750  2
--   NULL       $2,600  4   ← R003+R004 orders: fact rows survive, dimension is NULL
SELECT * FROM SEMANTIC_VIEW(
    RAP_TEST.PUBLIC.SALES_BY_REGION_SV
    DIMENSIONS regions.region_name
    METRICS orders.total_revenue, orders.order_count
)
ORDER BY total_revenue DESC NULLS LAST;

-- WHY THIS HAPPENS:
-- The SV engine generates a LEFT JOIN between ORDERS and SALES_REGIONS.
-- The RAP filters SALES_REGIONS rows for R003 and R004, so those region rows
-- are invisible. The LEFT JOIN still includes the ORDERS rows for R003/R004,
-- but produces NULL for every dimension column. Those orphaned orders are
-- grouped together under a single NULL dimension row.
--
-- The NULL row is not just cosmetically wrong — it leaks information:
-- REGION_A_ANALYST can now infer that $2,600 of revenue exists in regions
-- they are not supposed to see at all.


-- ============================================================
-- WORKAROUND 1: Helper view with inner join
-- The ORDERS_FILTERED view inner-joins ORDERS to SALES_REGIONS.
-- When the RAP hides a SALES_REGIONS row, the INNER JOIN also drops
-- the corresponding ORDERS row — no orphaned facts reach the SV.
-- ============================================================

-- Expected (correct): 2 rows, no NULL
--   Northeast  $1,250  2
--   Southeast    $750  2
SELECT * FROM SEMANTIC_VIEW(
    RAP_TEST.PUBLIC.SALES_BY_REGION_VIEW_SV
    DIMENSIONS regions.region_name
    METRICS orders.total_revenue, orders.order_count
)
ORDER BY total_revenue DESC;


-- ============================================================
-- WORKAROUND 2: Apply the RAP directly to the fact table
-- Adding the same RAP to ORDERS means the fact rows themselves are
-- filtered before any join occurs. The original (simpler) SV works
-- correctly without needing an intermediate helper view.
-- ============================================================

USE ROLE SYSADMIN;

ALTER TABLE RAP_TEST.PUBLIC.ORDERS
    ADD ROW ACCESS POLICY RAP_TEST.PUBLIC.region_access_policy ON (REGION_ID);

USE ROLE REGION_A_ANALYST;
USE SECONDARY ROLES NONE;

-- Same SV as the anti-pattern — but now ORDERS itself is also filtered.
-- Expected (correct): 2 rows, no NULL
--   Northeast  $1,250  2
--   Southeast    $750  2
SELECT * FROM SEMANTIC_VIEW(
    RAP_TEST.PUBLIC.SALES_BY_REGION_SV
    DIMENSIONS regions.region_name
    METRICS orders.total_revenue, orders.order_count
)
ORDER BY total_revenue DESC;

-- REGION_B_ANALYST sees their two regions
USE ROLE REGION_B_ANALYST;
USE SECONDARY ROLES NONE;

-- Expected: 2 rows
--   Northwest  $1,400  2
--   Southwest  $1,200  2
SELECT * FROM SEMANTIC_VIEW(
    RAP_TEST.PUBLIC.SALES_BY_REGION_SV
    DIMENSIONS regions.region_name
    METRICS orders.total_revenue, orders.order_count
)
ORDER BY total_revenue DESC;


-- ============================================================
-- HOW THE JOIN DIRECTION MATTERS:
-- RAP on dimension → LEFT JOIN survives, orphaned facts get NULL dims.
-- RAP on fact      → fact rows are filtered first; no orphaned rows exist.
-- RAP on both      → belt-and-suspenders; the fact-table RAP alone is sufficient.
--
-- Helper view approach: the INNER JOIN in the view mimics fact-table filtering
-- without modifying the underlying table. Useful when you cannot or prefer not
-- to alter the physical table (e.g., shared tables used by other SVs or queries
-- that should NOT be filtered).
-- ============================================================


-- ============================================================
-- CLEANUP — run to remove all objects created by this snippet
-- ============================================================

USE ROLE SYSADMIN;
DROP DATABASE  IF EXISTS RAP_TEST;

USE ROLE SECURITYADMIN;
DROP ROLE IF EXISTS REGION_A_ANALYST;
DROP ROLE IF EXISTS REGION_B_ANALYST;
