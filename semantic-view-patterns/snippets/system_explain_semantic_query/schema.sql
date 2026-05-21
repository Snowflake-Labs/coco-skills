-- SYSTEM$EXPLAIN_SEMANTIC_QUERY: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE TABLE customers (
    customer_id    INTEGER       NOT NULL,
    customer_name  VARCHAR(50)   NOT NULL,
    tier           VARCHAR(20)   NOT NULL,  -- 'enterprise', 'mid-market', 'smb'
    CONSTRAINT pk_customers_explain PRIMARY KEY (customer_id)
);

CREATE OR REPLACE TABLE support_tickets (
    ticket_id    INTEGER       NOT NULL,
    customer_id  INTEGER       NOT NULL,
    opened_date  DATE          NOT NULL,
    priority     VARCHAR(10)   NOT NULL,  -- 'P1', 'P2', 'P3'
    amount       NUMBER(10,2)  NOT NULL,  -- contract value at time of ticket
    CONSTRAINT pk_tickets PRIMARY KEY (ticket_id)
);
