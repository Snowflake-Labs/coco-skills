-- Inline SV: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- Tables shared by both inline SV patterns
CREATE OR REPLACE TABLE inline_orders (
    order_id    INTEGER       NOT NULL,
    customer_id INTEGER       NOT NULL,
    amount      NUMBER(10,2)  NOT NULL,
    status      VARCHAR(20)   NOT NULL
);

CREATE OR REPLACE TABLE inline_customers (
    customer_id   INTEGER      NOT NULL,
    customer_name VARCHAR(50)  NOT NULL,
    tier          VARCHAR(20)  NOT NULL,
    CONSTRAINT pk_inline_customers PRIMARY KEY (customer_id)
);
