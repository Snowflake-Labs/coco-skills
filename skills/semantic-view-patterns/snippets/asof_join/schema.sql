-- ASOF Join Example: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE TABLE Customer_address (
    ca_custid      VARCHAR(10)  NOT NULL,
    ca_zipcode     INTEGER      NOT NULL,
    ca_street_addr VARCHAR(50)  NOT NULL,
    ca_start_date  DATE         NOT NULL
);

CREATE OR REPLACE TABLE Customer_name (
    c_custid    VARCHAR(10)  NOT NULL,
    c_first_name VARCHAR(20) NOT NULL,
    c_last_name  VARCHAR(20) NOT NULL
);

CREATE OR REPLACE TABLE Orders (
    o_ordid    VARCHAR(10)   NOT NULL,
    o_custid   VARCHAR(10)   NOT NULL,
    o_orddate  DATE          NOT NULL,
    o_amount   NUMBER(10,2)  NOT NULL
);
