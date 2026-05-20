-- Derived Metrics: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE TABLE dim_date (
    date_id  INTEGER NOT NULL,
    full_date DATE   NOT NULL,
    year     INTEGER NOT NULL,
    quarter  INTEGER NOT NULL,
    month    INTEGER NOT NULL,
    CONSTRAINT pk_dim_date PRIMARY KEY (date_id)
);

CREATE OR REPLACE TABLE store_sales (
    sale_id  INTEGER       NOT NULL,
    date_id  INTEGER       NOT NULL,
    revenue  NUMBER(10,2)  NOT NULL,
    quantity INTEGER       NOT NULL
);

CREATE OR REPLACE TABLE web_sales (
    sale_id  INTEGER       NOT NULL,
    date_id  INTEGER       NOT NULL,
    revenue  NUMBER(10,2)  NOT NULL,
    quantity INTEGER       NOT NULL
);

CREATE OR REPLACE TABLE catalog_sales (
    sale_id  INTEGER       NOT NULL,
    date_id  INTEGER       NOT NULL,
    revenue  NUMBER(10,2)  NOT NULL,
    quantity INTEGER       NOT NULL
);
