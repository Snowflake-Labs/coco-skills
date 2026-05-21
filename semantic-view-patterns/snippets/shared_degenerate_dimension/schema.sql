-- Shared Degenerate Dimension: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- Two fact tables — each has a 'region' column but there is no dedicated region dim table.
-- Region is a "degenerate dimension": a dimension stored on the fact table itself.

CREATE OR REPLACE TABLE store_orders (
    order_id    INTEGER       NOT NULL,
    order_date  DATE          NOT NULL,
    region      VARCHAR(20)   NOT NULL,
    category    VARCHAR(30)   NOT NULL,
    amount      NUMBER(10,2)  NOT NULL
);

CREATE OR REPLACE TABLE web_orders (
    order_id    INTEGER       NOT NULL,
    order_date  DATE          NOT NULL,
    region      VARCHAR(20)   NOT NULL,
    channel     VARCHAR(20)   NOT NULL,
    amount      NUMBER(10,2)  NOT NULL
);

-- Helper view: unions the distinct region values from both fact tables.
-- This becomes the shared dimension entity in the semantic view.
-- Alternative: use inline SQL in the TABLES clause (shown in semantic_view.sql).
CREATE OR REPLACE VIEW region_dim AS
    SELECT DISTINCT region FROM store_orders
    UNION
    SELECT DISTINCT region FROM web_orders;
