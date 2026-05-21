-- Semi-Additive Metric Example: Schema
-- Target: SNIPPETS.PUBLIC (replace with your database/schema)
--
-- Scenario: Daily account balance snapshots for a small portfolio of accounts.
-- Each row represents the balance at end-of-day for one account on one date.

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE TABLE ACCOUNT_BALANCES (
    BALANCE_ID   INTEGER        NOT NULL,   -- surrogate key
    ACCOUNT_ID   VARCHAR(10)    NOT NULL,
    ACCOUNT_NAME VARCHAR(50)    NOT NULL,
    BALANCE_DATE DATE           NOT NULL,
    BALANCE_USD  NUMBER(12, 2)  NOT NULL    -- end-of-day balance
);
