-- Variables: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE TABLE product_sales (
    sale_id         INTEGER        NOT NULL,
    product_id      INTEGER        NOT NULL,
    product_name    VARCHAR(50)    NOT NULL,
    category        VARCHAR(30)    NOT NULL,
    sale_date       DATE           NOT NULL,
    quantity        INTEGER        NOT NULL,
    unit_price      NUMBER(10,2)   NOT NULL,
    customer_rating NUMBER(3,2)    NOT NULL
);
