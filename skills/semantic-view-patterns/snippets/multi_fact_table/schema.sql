-- Multi-Fact Table: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE TABLE dim_product (
    product_id   INTEGER      NOT NULL,
    product_name VARCHAR(50)  NOT NULL,
    category     VARCHAR(30)  NOT NULL,
    brand        VARCHAR(30)  NOT NULL,
    CONSTRAINT pk_dim_product PRIMARY KEY (product_id)
);

CREATE OR REPLACE TABLE channel_dim_date (
    date_id   INTEGER NOT NULL,
    full_date DATE    NOT NULL,
    year      INTEGER NOT NULL,
    quarter   INTEGER NOT NULL,
    month     INTEGER NOT NULL,
    CONSTRAINT pk_channel_dim_date PRIMARY KEY (date_id)
);

CREATE OR REPLACE TABLE channel_store_sales (
    sale_id    INTEGER       NOT NULL,
    date_id    INTEGER       NOT NULL,
    product_id INTEGER       NOT NULL,
    revenue    NUMBER(10,2)  NOT NULL,
    quantity   INTEGER       NOT NULL
);

CREATE OR REPLACE TABLE channel_web_sales (
    sale_id    INTEGER       NOT NULL,
    date_id    INTEGER       NOT NULL,
    product_id INTEGER       NOT NULL,
    revenue    NUMBER(10,2)  NOT NULL,
    quantity   INTEGER       NOT NULL
);

CREATE OR REPLACE TABLE channel_returns (
    return_id  INTEGER       NOT NULL,
    date_id    INTEGER       NOT NULL,
    product_id INTEGER       NOT NULL,
    amount     NUMBER(10,2)  NOT NULL,
    quantity   INTEGER       NOT NULL
);
