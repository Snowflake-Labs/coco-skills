-- AI Metadata: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE TABLE ai_orders (
    order_id    INTEGER       NOT NULL,
    customer_id INTEGER       NOT NULL,
    amount      NUMBER(10,2)  NOT NULL,
    status      VARCHAR(20)   NOT NULL,
    order_date  DATE          NOT NULL
);

CREATE OR REPLACE TABLE ai_customers (
    customer_id   INTEGER      NOT NULL,
    customer_name VARCHAR(50)  NOT NULL,
    region        VARCHAR(30)  NOT NULL,
    CONSTRAINT pk_ai_customers PRIMARY KEY (customer_id)
);
