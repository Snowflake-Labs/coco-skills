-- Range Join Example: Schema
-- Target: SNIPPETS.PUBLIC (replace with your database/schema)
--
-- Scenario: E-commerce orders joined to the customer subscription tier
--           that was active at the time of each order.

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- Customer subscription tier history (SCD Type 2)
-- valid_to uses EXCLUSIVE semantics: the tier is active through (valid_to - 1 day)
-- Current records use the sentinel value 9999-12-31 for valid_to
CREATE OR REPLACE TABLE CUSTOMER_SEGMENTS (
    SEGMENT_ID     INTEGER       NOT NULL,   -- surrogate key
    CUSTOMER_ID    VARCHAR(10)   NOT NULL,
    SEGMENT        VARCHAR(20)   NOT NULL,   -- Free | Growth | Enterprise
    VALID_FROM     DATE          NOT NULL,
    VALID_TO       DATE          NOT NULL    -- exclusive end date; 9999-12-31 = current
);

-- Orders fact table
CREATE OR REPLACE TABLE ORDERS (
    ORDER_ID       INTEGER       NOT NULL,
    CUSTOMER_ID    VARCHAR(10)   NOT NULL,
    ORDER_DATE     DATE          NOT NULL,
    ORDER_AMOUNT   NUMBER(10,2)  NOT NULL
);
