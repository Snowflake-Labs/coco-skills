-- Entity Facts: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE TABLE customers (
    customer_id   INTEGER       NOT NULL,
    customer_name VARCHAR(50)   NOT NULL,
    birth_year    INTEGER       NOT NULL,
    CONSTRAINT pk_customers PRIMARY KEY (customer_id)
);

CREATE OR REPLACE TABLE orders (
    order_id    INTEGER       NOT NULL,
    customer_id INTEGER       NOT NULL,
    order_date  DATE          NOT NULL,
    amount      NUMBER(10,2)  NOT NULL
);
