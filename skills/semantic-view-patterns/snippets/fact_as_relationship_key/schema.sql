-- Fact as Relationship Key: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE TABLE products (
    product_id    INTEGER       NOT NULL,
    product_name  VARCHAR(50)   NOT NULL,
    category      VARCHAR(30)   NOT NULL,
    CONSTRAINT pk_products PRIMARY KEY (product_id)
);

CREATE OR REPLACE TABLE fiscal_quarters (
    fiscal_quarter_key  VARCHAR(10)    NOT NULL,  -- e.g. '2024-Q2'
    quarter_name        VARCHAR(20)    NOT NULL,  -- e.g. 'Q2 FY2024'
    fiscal_year         INTEGER        NOT NULL,
    budget_amount       NUMBER(12,2)   NOT NULL,
    CONSTRAINT pk_fiscal_quarters PRIMARY KEY (fiscal_quarter_key)
);

CREATE OR REPLACE TABLE sales (
    sale_id     INTEGER       NOT NULL,
    sale_date   DATE          NOT NULL,
    product_id  INTEGER       NOT NULL,
    amount      NUMBER(10,2)  NOT NULL,
    CONSTRAINT pk_sales PRIMARY KEY (sale_id)
);
