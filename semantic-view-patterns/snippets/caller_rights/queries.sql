-- Caller Rights: Queries
--
-- Demonstrates that SEMANTIC_VIEW() uses caller rights:
-- the querying user must have SELECT on both the SV AND its base tables.

USE WAREHOUSE SV_CALLER_TEST;

-- ============================================================
-- WORKING QUERY — SV_USER has SELECT on SV + base tables
-- ============================================================

-- SV_USER privileges:
--   ✓ USAGE on SV_CALLER_TEST.SV schema
--   ✓ USAGE on SV_CALLER_TEST.DATA schema
--   ✓ SELECT on CUSTOMER, CUSTOMER_ADDRESS, ORDERS
--   ✓ SELECT on CUSTOMER_ORDERS_VIEW

USE SECONDARY ROLES NONE;
USE ROLE SV_USER;

-- Expected: 12 rows — monthly order totals with historically correct zip codes
SELECT * FROM SEMANTIC_VIEW(
    SV_CALLER_TEST.SV.CUSTOMER_ORDERS_VIEW
    DIMENSIONS orders.dim_year_month, orders.f_cust_zipcode
    METRICS orders.m_order_amount
)
ORDER BY dim_year_month, f_cust_zipcode;


-- ============================================================
-- FAILING QUERY — SV_USER_NO_BASE_SELECT has SV SELECT only
-- ============================================================

-- SV_USER_NO_BASE_SELECT privileges:
--   ✓ USAGE on SV_CALLER_TEST.SV schema
--   ✗ NO USAGE on SV_CALLER_TEST.DATA schema
--   ✗ NO SELECT on CUSTOMER, CUSTOMER_ADDRESS, ORDERS
--   ✓ SELECT on CUSTOMER_ORDERS_VIEW
--
-- Despite having SELECT on the SV, the query fails because the engine
-- resolves the base tables with the CALLER's privileges, not the owner's.

USE ROLE SV_USER_NO_BASE_SELECT;

-- Expected: ERROR — insufficient privileges on the DATA schema / base tables
SELECT * FROM SEMANTIC_VIEW(
    SV_CALLER_TEST.SV.CUSTOMER_ORDERS_VIEW
    DIMENSIONS orders.dim_year_month, orders.f_cust_zipcode
    METRICS orders.m_order_amount
)
ORDER BY dim_year_month, f_cust_zipcode;


-- ============================================================
-- HOW CALLER RIGHTS WORKS:
-- When a SEMANTIC_VIEW() query runs, the engine rewrites it into SQL
-- against the underlying base tables and executes it with the calling
-- user's active role. That role must have:
--   1. USAGE on the database and schema containing the SV
--   2. SELECT on the SV itself
--   3. USAGE on the database and schema containing every base table
--   4. SELECT on every base table referenced by the SV
--
-- This is different from standard Snowflake views, which use OWNER RIGHTS
-- by default: a user with SELECT on a regular view can read data even
-- without SELECT on the underlying tables.
--
-- USE SECONDARY ROLES ALL can alter this test — if a secondary role grants
-- DATA schema access, SV_USER_NO_BASE_SELECT may unexpectedly succeed.
-- Always use USE SECONDARY ROLES NONE when testing access boundaries.


-- ============================================================
-- CLEANUP — run to remove all objects created by this snippet
-- ============================================================

USE ROLE SYSADMIN;
DROP DATABASE   IF EXISTS SV_CALLER_TEST;
DROP WAREHOUSE  IF EXISTS SV_CALLER_TEST;

USE ROLE SECURITYADMIN;
DROP ROLE IF EXISTS SV_OWNER;
DROP ROLE IF EXISTS SV_CREATOR;
DROP ROLE IF EXISTS SV_USER;
DROP ROLE IF EXISTS SV_USER_NO_BASE_SELECT;
