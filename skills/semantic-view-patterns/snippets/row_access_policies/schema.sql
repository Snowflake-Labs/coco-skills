-- Row Access Policy Example: Schema Setup
--
-- ⚠️  Requires ACCOUNTADMIN (or SECURITYADMIN + SYSADMIN).
-- Creates a dedicated environment: database RAP_TEST and
-- roles REGION_A_ANALYST and REGION_B_ANALYST.
-- Does NOT create a warehouse — grant the analyst roles USAGE on an existing
-- warehouse before running queries.sql (see Tutorial mode instructions).
-- Does NOT use the --db / --schema arguments from run_snippet.py.

USE SECONDARY ROLES NONE;

-- ============================================================
-- ROLES
-- ============================================================

USE ROLE SECURITYADMIN;

CREATE ROLE IF NOT EXISTS REGION_A_ANALYST;
CREATE ROLE IF NOT EXISTS REGION_B_ANALYST;

GRANT ROLE REGION_A_ANALYST TO ROLE SYSADMIN;
GRANT ROLE REGION_B_ANALYST TO ROLE SYSADMIN;

-- ============================================================
-- DATABASE & SCHEMA
-- ============================================================

CREATE DATABASE IF NOT EXISTS RAP_TEST;
CREATE SCHEMA IF NOT EXISTS RAP_TEST.PUBLIC;

GRANT USAGE ON DATABASE RAP_TEST TO ROLE REGION_A_ANALYST;
GRANT USAGE ON DATABASE RAP_TEST TO ROLE REGION_B_ANALYST;
GRANT USAGE ON SCHEMA   RAP_TEST.PUBLIC TO ROLE REGION_A_ANALYST;
GRANT USAGE ON SCHEMA   RAP_TEST.PUBLIC TO ROLE REGION_B_ANALYST;

-- ============================================================
-- TABLES
-- ============================================================

USE DATABASE RAP_TEST;
USE SCHEMA PUBLIC;

-- Dimension table: one row per sales region
CREATE OR REPLACE TABLE RAP_TEST.PUBLIC.SALES_REGIONS (
    region_id           VARCHAR(10)   NOT NULL,
    region_name         VARCHAR(50)   NOT NULL,
    reporting_manager   VARCHAR(50)   NOT NULL,
    CONSTRAINT pk_regions PRIMARY KEY (region_id)
);

-- Fact table: individual orders
CREATE OR REPLACE TABLE RAP_TEST.PUBLIC.ORDERS (
    order_id    INTEGER       NOT NULL,
    region_id   VARCHAR(10)   NOT NULL,
    order_date  DATE          NOT NULL,
    amount      NUMBER(10,2)  NOT NULL,
    CONSTRAINT pk_orders PRIMARY KEY (order_id)
);

-- ============================================================
-- ROW ACCESS POLICY
-- ============================================================

-- REGION_A_ANALYST: Northeast (R001) + Southeast (R002) only.
-- REGION_B_ANALYST: Northwest (R003) + Southwest (R004) only.
-- SYSADMIN / ACCOUNTADMIN: unrestricted access.
CREATE OR REPLACE ROW ACCESS POLICY RAP_TEST.PUBLIC.region_access_policy
    AS (region_id VARCHAR) RETURNS BOOLEAN ->
    CASE
        WHEN CURRENT_ROLE() IN ('SYSADMIN', 'ACCOUNTADMIN') THEN TRUE
        WHEN CURRENT_ROLE() = 'REGION_A_ANALYST' THEN region_id IN ('R001', 'R002')
        WHEN CURRENT_ROLE() = 'REGION_B_ANALYST' THEN region_id IN ('R003', 'R004')
        ELSE FALSE
    END;

-- ANTI-PATTERN SETUP: Apply the RAP to the dimension table only.
-- This is the configuration that causes NULL rows — see queries.sql.
ALTER TABLE RAP_TEST.PUBLIC.SALES_REGIONS
    ADD ROW ACCESS POLICY RAP_TEST.PUBLIC.region_access_policy ON (REGION_ID);

-- ============================================================
-- GRANTS TO ANALYST ROLES
-- ============================================================

GRANT SELECT ON TABLE RAP_TEST.PUBLIC.ORDERS         TO ROLE REGION_A_ANALYST;
GRANT SELECT ON TABLE RAP_TEST.PUBLIC.ORDERS         TO ROLE REGION_B_ANALYST;
GRANT SELECT ON TABLE RAP_TEST.PUBLIC.SALES_REGIONS  TO ROLE REGION_A_ANALYST;
GRANT SELECT ON TABLE RAP_TEST.PUBLIC.SALES_REGIONS  TO ROLE REGION_B_ANALYST;
