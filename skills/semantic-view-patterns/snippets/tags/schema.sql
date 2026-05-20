-- Tags: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- Create tags that will be applied to metrics in the semantic view
CREATE TAG IF NOT EXISTS metric_owner
  COMMENT = 'Team or person responsible for this metric';

CREATE TAG IF NOT EXISTS metric_status
  COMMENT = 'Development status: certified, in_development, deprecated';

CREATE TAG IF NOT EXISTS metric_domain
  COMMENT = 'Business domain: sales, marketing, finance, ops';

CREATE OR REPLACE TABLE tag_store_sales (
    sale_id    INTEGER       NOT NULL,
    date_id    INTEGER       NOT NULL,
    revenue    NUMBER(10,2)  NOT NULL,
    quantity   INTEGER       NOT NULL
);

CREATE OR REPLACE TABLE tag_web_sales (
    sale_id    INTEGER       NOT NULL,
    date_id    INTEGER       NOT NULL,
    revenue    NUMBER(10,2)  NOT NULL,
    quantity   INTEGER       NOT NULL
);

CREATE OR REPLACE TABLE tag_dim_date (
    date_id   INTEGER NOT NULL,
    full_date DATE    NOT NULL,
    year      INTEGER NOT NULL,
    month     INTEGER NOT NULL,
    CONSTRAINT pk_tag_dim_date PRIMARY KEY (date_id)
);
