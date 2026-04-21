-- ASOF Join Example: Semantic View DDL

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.ORDERS_BY_ADDRESS

  TABLES (
    Customer_address UNIQUE (ca_custid, ca_start_date),
    Customer_name    UNIQUE (c_custid),
    Orders           UNIQUE (o_ordid)
  )

  RELATIONSHIPS (
    -- Join address to name (simple 1:1)
    addr_to_name AS Customer_address(ca_custid) REFERENCES Customer_name,

    -- ASOF join: for each order, find the address record with the
    -- largest ca_start_date that is <= o_orddate for the same customer
    orders_to_addr AS Orders(o_custid, o_orddate)
        REFERENCES Customer_address(ca_custid, ASOF ca_start_date)
  )

  DIMENSIONS (
    Customer_name.name     AS CONCAT(c_first_name, ' ', c_last_name)
      COMMENT = 'Full customer name',
    Customer_address.zip   AS ca_zipcode
      WITH SYNONYMS ('zip code', 'postal code', 'delivery zip'),
    Customer_address.street AS ca_street_addr,
    Orders.year_month      AS DATE_TRUNC('month', o_orddate)
      WITH SYNONYMS ('order month', 'month')
  )

  METRICS (
    Orders.total_revenue AS SUM(o_amount)
      WITH SYNONYMS ('revenue', 'order revenue', 'total order value'),
    Orders.order_count AS COUNT(o_ordid)
      WITH SYNONYMS ('number of orders', 'orders')
  )

  COMMENT = 'Orders attributed to the customer address active at time of order via ASOF join.'

  AI_SQL_GENERATION 'Use Customer_address.zip to break down orders by the delivery zip code the customer had at order time. The ASOF relationship resolves the historically-correct address automatically — no date filtering needed.';
