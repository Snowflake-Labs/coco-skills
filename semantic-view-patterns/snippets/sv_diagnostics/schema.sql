-- SV Diagnostics: Shared Schema
--
-- One schema supports all four diagnostic scenarios:
--
--   Scenario 1 — Ambiguous path
--     DEALS has two date FKs (created_date, close_date) both pointing to DIM_DATE.
--     Without USING on metrics, any date dimension query errors at runtime.
--
--   Scenario 2 — Fan trap
--     Revenue lives at the DEALS header (one row per deal).
--     DEAL_ITEMS links each deal to one or more products.
--     Routing header-level revenue through DEAL_ITEMS to DIM_PRODUCT fans out rows
--     and produces a query-time error.
--     Fix: move the metric to DEAL_ITEMS.LINE_AMOUNT (line-item grain).
--
--   Scenario 3 — Table with no relationship
--     DIM_REGION is defined in TABLES but never given a RELATIONSHIP to any fact.
--     Deploying succeeds; using its dimensions at query time errors.
--
--   Scenario 4 — Duplicate logical name / ambiguous synonyms
--     Duplicate logical name across entities → deploy-time error (hard stop).
--     Overlapping synonyms across dimensions/metrics → deploys fine, but Cortex
--     Analyst can't disambiguate and refuses to answer.

USE DATABASE SEMANTIC_SKILLS;
USE SCHEMA SNIPPETS;

-- ── Dimensions ────────────────────────────────────────────────────────────────

CREATE OR REPLACE TABLE DIM_REP (
    rep_id      INTEGER      NOT NULL,
    rep_name    VARCHAR(50)  NOT NULL,
    region      VARCHAR(20)  NOT NULL,
    team        VARCHAR(20)  NOT NULL,
    CONSTRAINT pk_dim_rep PRIMARY KEY (rep_id)
);

CREATE OR REPLACE TABLE DIM_PRODUCT (
    product_id    INTEGER      NOT NULL,
    product_name  VARCHAR(50)  NOT NULL,
    category      VARCHAR(30)  NOT NULL,
    CONSTRAINT pk_dim_product PRIMARY KEY (product_id)
);

-- Intentionally orphaned for Scenario 3 — defined in TABLES, no RELATIONSHIP
CREATE OR REPLACE TABLE DIM_REGION (
    region_code VARCHAR(20)  NOT NULL,
    region_name VARCHAR(50)  NOT NULL,
    CONSTRAINT pk_dim_region PRIMARY KEY (region_code)
);

-- ── Facts ─────────────────────────────────────────────────────────────────────

-- DEALS: two date FKs → Scenario 1 (ambiguous path).
-- Revenue (AMOUNT) lives here at header grain → Scenario 2 (fan trap source).
CREATE OR REPLACE TABLE DEALS (
    deal_id       INTEGER       NOT NULL,
    rep_id        INTEGER       NOT NULL,   -- FK → DIM_REP
    created_date  DATE          NOT NULL,   -- FK → DIM_DATE (pipeline entry)
    close_date    DATE,                     -- FK → DIM_DATE (NULL if open)
    amount        NUMBER(10,2)  NOT NULL,
    product       VARCHAR(30)   NOT NULL,
    stage         VARCHAR(20)   NOT NULL,
    CONSTRAINT pk_deals PRIMARY KEY (deal_id)
);

-- DEAL_ITEMS: bridge between DEALS and DIM_PRODUCT.
-- LINE_AMOUNT is the per-product allocation of the deal amount.
-- This is the correct grain for product-level revenue metrics.
CREATE OR REPLACE TABLE DEAL_ITEMS (
    item_id      INTEGER       NOT NULL,
    deal_id      INTEGER       NOT NULL,   -- FK → DEALS
    product_id   INTEGER       NOT NULL,   -- FK → DIM_PRODUCT
    line_amount  NUMBER(10,2),             -- revenue at line-item grain
    CONSTRAINT pk_deal_items PRIMARY KEY (item_id)
);
