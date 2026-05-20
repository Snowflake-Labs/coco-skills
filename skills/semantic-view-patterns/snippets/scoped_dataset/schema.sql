-- Scoped Dataset: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- Single source table with data across all lines of business
CREATE OR REPLACE TABLE sales_transactions (
    transaction_id  INTEGER       NOT NULL,
    customer_id     INTEGER       NOT NULL,
    order_date      DATE          NOT NULL,
    amount          NUMBER(10,2)  NOT NULL,
    region          VARCHAR(20)   NOT NULL,
    lob             VARCHAR(20)   NOT NULL   -- 'Retail', 'Enterprise', 'SMB'
);

-- Customer table used for the join-inline example
CREATE OR REPLACE TABLE lob_customers (
    customer_id   INTEGER     NOT NULL,
    customer_name VARCHAR(50) NOT NULL,
    tier          VARCHAR(20) NOT NULL,
    lob           VARCHAR(20) NOT NULL,
    CONSTRAINT pk_lob_customers PRIMARY KEY (customer_id)
);
