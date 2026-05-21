-- Accumulating Snapshot: Schema Setup
--
-- Kimball's "Accumulating Snapshot Fact Table" pattern:
-- one row per business entity (loan application), updated as it moves
-- through pipeline stages. Each milestone gets its own date column.
--
-- Four milestone FKs all reference the same DIM_DATE table.
-- In the Semantic View, each stage metric uses USING to route through
-- the correct date relationship — no ambiguity, no dedicated date alias per stage.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- DIMENSION TABLE
-- ============================================================

CREATE OR REPLACE TABLE DIM_DATE (
    date_key    DATE        NOT NULL,
    month_num   INTEGER     NOT NULL,
    month_name  VARCHAR(10) NOT NULL,
    quarter     VARCHAR(2)  NOT NULL,
    year        INTEGER     NOT NULL,
    CONSTRAINT pk_dim_date PRIMARY KEY (date_key)
);

-- ============================================================
-- FACT TABLE — Accumulating Snapshot
-- ============================================================

-- One row per loan application. Milestone columns are NULL until
-- the application reaches that stage. FUNDED_AMOUNT is NULL for
-- denied, withdrawn, or in-progress applications.
CREATE OR REPLACE TABLE LOAN_APPLICATIONS (
    application_id    INTEGER       NOT NULL,
    loan_product      VARCHAR(20)   NOT NULL,   -- Personal Loan | Student Refi | Home Equity
    state             VARCHAR(2)    NOT NULL,
    channel           VARCHAR(20)   NOT NULL,   -- Organic | Paid Search | Referral | Direct Mail
    -- Milestone timestamps — FK to DIM_DATE, NULL until stage reached
    application_date  DATE          NOT NULL,   -- always set at row creation
    review_date       DATE,                     -- set when underwriting starts
    decision_date     DATE,                     -- set when approved or denied
    funding_date      DATE,                     -- set only for approved + funded loans
    -- Measures
    requested_amount  NUMBER(10,2)  NOT NULL,
    funded_amount     NUMBER(10,2),             -- NULL until funded
    CONSTRAINT pk_loan_app PRIMARY KEY (application_id)
);
