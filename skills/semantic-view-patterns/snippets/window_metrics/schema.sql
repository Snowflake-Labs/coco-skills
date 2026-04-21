-- Window Metrics: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE TABLE daily_sales (
    sale_id      INTEGER       NOT NULL,
    sale_date    DATE          NOT NULL,
    channel      VARCHAR(20)   NOT NULL,
    revenue      NUMBER(10,2)  NOT NULL,
    quantity     INTEGER       NOT NULL
);
