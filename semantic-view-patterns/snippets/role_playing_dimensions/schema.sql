-- Role-Playing Dimensions: Schema Setup
--
-- One physical date dimension table (DIM_DATE) aliased twice in the SV:
--   order_date_dim  → joined on ORDERS.ORDER_DATE
--   ship_date_dim   → joined on ORDERS.SHIP_DATE
--
-- This gives each date role its own named dimensions (ORDER_YEAR, SHIP_YEAR, etc.)
-- without any USING clause or metric-level disambiguation.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- DIMENSION TABLE
-- ============================================================

-- One row per calendar date.
-- In a real DW this table may have hundreds of columns; here we use five.
CREATE OR REPLACE TABLE DIM_DATE (
    date_key    DATE        NOT NULL,
    month_num   INTEGER     NOT NULL,
    month_name  VARCHAR(10) NOT NULL,
    quarter     VARCHAR(2)  NOT NULL,
    year        INTEGER     NOT NULL,
    CONSTRAINT pk_dim_date PRIMARY KEY (date_key)
);

-- ============================================================
-- FACT TABLE
-- ============================================================

-- ORDERS: each row is one order.
-- Two date FKs — both reference the same physical DIM_DATE.
CREATE OR REPLACE TABLE ORDERS (
    order_id       INTEGER      NOT NULL,
    customer_name  VARCHAR(50)  NOT NULL,
    order_date     DATE         NOT NULL,
    ship_date      DATE         NOT NULL,
    amount         NUMBER(10,2) NOT NULL,
    CONSTRAINT pk_orders PRIMARY KEY (order_id)
);
