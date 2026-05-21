-- Caller Rights: Semantic View DDL + Access Grants

-- SV_CREATOR has SELECT on base tables and CREATE SEMANTIC VIEW on SV_CALLER_TEST.SV
USE ROLE SV_CREATOR;
USE WAREHOUSE SV_CALLER_TEST;
USE SCHEMA SV_CALLER_TEST.SV;

CREATE OR REPLACE SEMANTIC VIEW SV_CALLER_TEST.SV.CUSTOMER_ORDERS_VIEW

  TABLES (
    -- Alias the fully-qualified DATA schema tables into the SV namespace
    customer_address AS SV_CALLER_TEST.DATA.CUSTOMER_ADDRESS
        UNIQUE (ca_cust_id, ca_start_date),
    customer         AS SV_CALLER_TEST.DATA.CUSTOMER
        UNIQUE (c_cust_id),
    orders           AS SV_CALLER_TEST.DATA.ORDERS
        UNIQUE (o_ord_id)
  )

  RELATIONSHIPS (
    customer_address(ca_cust_id) REFERENCES customer,

    -- ASOF join: each order resolves to the address active at order time
    orders(o_cust_id, o_ord_date)
        REFERENCES customer_address(ca_cust_id, ASOF ca_start_date)
  )

  FACTS (
    customer_address.f_zipcode AS ca_zipcode
  )

  DIMENSIONS (
    -- Zip code resolved via ASOF — the address the customer had on the order date
    orders.f_cust_zipcode  AS customer_address.f_zipcode,
    orders.dim_year_month  AS DATE_TRUNC('month', o_ord_date)
  )

  METRICS (
    orders.m_order_amount AS SUM(o_amount)
  )

  COMMENT = 'Customer orders attributed to the address active at time of order (ASOF join). Used to demonstrate caller-rights access control: users must have SELECT on both this SV and the DATA schema base tables.';

-- The future-grant in schema.sql set SV_OWNER as the owner.
-- SV_OWNER now grants SELECT to both user roles.
USE ROLE SV_OWNER;

GRANT SELECT ON SEMANTIC VIEW SV_CALLER_TEST.SV.CUSTOMER_ORDERS_VIEW TO ROLE SV_USER;
GRANT SELECT ON SEMANTIC VIEW SV_CALLER_TEST.SV.CUSTOMER_ORDERS_VIEW TO ROLE SV_USER_NO_BASE_SELECT;
