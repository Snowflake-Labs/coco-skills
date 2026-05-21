-- Materialization: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE TABLE mat_orders (
    order_id    INTEGER       NOT NULL,
    customer_id INTEGER       NOT NULL,
    order_date  DATE          NOT NULL,
    amount      NUMBER(10,2)  NOT NULL,
    region      VARCHAR(30)   NOT NULL
);

CREATE OR REPLACE TABLE mat_customers (
    customer_id   INTEGER      NOT NULL,
    customer_name VARCHAR(50)  NOT NULL,
    segment       VARCHAR(30)  NOT NULL,
    CONSTRAINT pk_mat_customers PRIMARY KEY (customer_id)
);
