-- Caller Rights: Schema Setup
--
-- ⚠️  Requires ACCOUNTADMIN (or SECURITYADMIN + SYSADMIN).
-- Creates dedicated resources: roles, warehouse SV_CALLER_TEST, database SV_CALLER_TEST.
-- Does NOT use the --db / --schema arguments from run_snippet.py.

-- Isolate role privileges for accurate testing
USE SECONDARY ROLES NONE;

-- ============================================================
-- ROLES
-- ============================================================

USE ROLE SECURITYADMIN;

CREATE ROLE IF NOT EXISTS SV_OWNER;
CREATE ROLE IF NOT EXISTS SV_CREATOR;
CREATE ROLE IF NOT EXISTS SV_USER;
CREATE ROLE IF NOT EXISTS SV_USER_NO_BASE_SELECT;

-- Grant to SYSADMIN so all roles are usable via the admin hierarchy
GRANT ROLE SV_OWNER                 TO ROLE SYSADMIN;
GRANT ROLE SV_CREATOR               TO ROLE SYSADMIN;
GRANT ROLE SV_USER                  TO ROLE SYSADMIN;
GRANT ROLE SV_USER_NO_BASE_SELECT   TO ROLE SYSADMIN;

-- ============================================================
-- WAREHOUSE
-- ============================================================

USE ROLE SYSADMIN;

CREATE OR REPLACE WAREHOUSE SV_CALLER_TEST
    WAREHOUSE_SIZE = XSMALL
    AUTO_SUSPEND   = 60
    AUTO_RESUME    = TRUE;

GRANT USAGE, OPERATE ON WAREHOUSE SV_CALLER_TEST TO ROLE SV_OWNER;
GRANT USAGE, OPERATE ON WAREHOUSE SV_CALLER_TEST TO ROLE SV_CREATOR;
GRANT USAGE, OPERATE ON WAREHOUSE SV_CALLER_TEST TO ROLE SV_USER;
GRANT USAGE, OPERATE ON WAREHOUSE SV_CALLER_TEST TO ROLE SV_USER_NO_BASE_SELECT;

-- ============================================================
-- DATABASE & SCHEMAS
-- ============================================================

CREATE DATABASE IF NOT EXISTS SV_CALLER_TEST;

-- Separate schemas enforce the semantic layer / data layer boundary
CREATE OR REPLACE SCHEMA SV_CALLER_TEST.SV;    -- semantic views live here
CREATE OR REPLACE SCHEMA SV_CALLER_TEST.DATA;  -- base tables live here

-- All roles can use the database
GRANT USAGE ON DATABASE SV_CALLER_TEST TO ROLE SV_OWNER;
GRANT USAGE ON DATABASE SV_CALLER_TEST TO ROLE SV_CREATOR;
GRANT USAGE ON DATABASE SV_CALLER_TEST TO ROLE SV_USER;
GRANT USAGE ON DATABASE SV_CALLER_TEST TO ROLE SV_USER_NO_BASE_SELECT;

-- SV schema: all roles can use it; only SV_CREATOR can create SVs
GRANT USAGE                         ON SCHEMA SV_CALLER_TEST.SV TO ROLE SV_OWNER;
GRANT USAGE, CREATE SEMANTIC VIEW   ON SCHEMA SV_CALLER_TEST.SV TO ROLE SV_CREATOR;
GRANT USAGE                         ON SCHEMA SV_CALLER_TEST.SV TO ROLE SV_USER;
GRANT USAGE                         ON SCHEMA SV_CALLER_TEST.SV TO ROLE SV_USER_NO_BASE_SELECT;

-- DATA schema: granted only to SV_CREATOR and SV_USER.
-- SV_OWNER and SV_USER_NO_BASE_SELECT deliberately do NOT get DATA schema access.
GRANT USAGE ON SCHEMA SV_CALLER_TEST.DATA TO ROLE SV_CREATOR;
GRANT USAGE ON SCHEMA SV_CALLER_TEST.DATA TO ROLE SV_USER;

-- Future SVs created in SV_CALLER_TEST.SV are owned by SV_OWNER
GRANT OWNERSHIP ON FUTURE SEMANTIC VIEWS IN SCHEMA SV_CALLER_TEST.SV TO ROLE SV_OWNER;

-- ============================================================
-- BASE TABLES
-- ============================================================

USE SCHEMA SV_CALLER_TEST.DATA;

CREATE OR REPLACE TABLE CUSTOMER (
    c_cust_id       VARCHAR NOT NULL,
    c_first_name    VARCHAR NOT NULL,
    c_last_name     VARCHAR NOT NULL
);

CREATE OR REPLACE TABLE CUSTOMER_ADDRESS (
    ca_cust_id      VARCHAR NOT NULL,
    ca_zipcode      VARCHAR NOT NULL,
    ca_street_addr  VARCHAR NOT NULL,
    ca_start_date   DATE    NOT NULL,
    ca_end_date     DATE            -- NULL = currently active address
);

CREATE OR REPLACE TABLE ORDERS (
    o_ord_id    VARCHAR        NOT NULL,
    o_cust_id   VARCHAR        NOT NULL,
    o_ord_date  DATE           NOT NULL,
    o_amount    NUMBER(10, 2)  NOT NULL
);

-- SV_CREATOR and SV_USER can read the base tables.
-- SV_USER_NO_BASE_SELECT is explicitly NOT granted access here — that's the test.
GRANT SELECT ON TABLE SV_CALLER_TEST.DATA.CUSTOMER         TO ROLE SV_CREATOR;
GRANT SELECT ON TABLE SV_CALLER_TEST.DATA.CUSTOMER         TO ROLE SV_USER;
GRANT SELECT ON TABLE SV_CALLER_TEST.DATA.CUSTOMER_ADDRESS TO ROLE SV_CREATOR;
GRANT SELECT ON TABLE SV_CALLER_TEST.DATA.CUSTOMER_ADDRESS TO ROLE SV_USER;
GRANT SELECT ON TABLE SV_CALLER_TEST.DATA.ORDERS           TO ROLE SV_CREATOR;
GRANT SELECT ON TABLE SV_CALLER_TEST.DATA.ORDERS           TO ROLE SV_USER;
