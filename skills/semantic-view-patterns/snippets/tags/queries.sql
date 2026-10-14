-- Tags: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES ON THE SV
-- ============================================================

-- 1. Channel revenue by month (standard query — tags don't affect query behavior)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CHANNEL_SALES_TAGGED_SV
    DIMENSIONS tag_dim_date.month
    METRICS tag_store_sales.store_revenue, tag_web_sales.web_revenue,
            total_channel_revenue
)
ORDER BY month;


-- ============================================================
-- QUERYING TAGS (via tag_references)
-- ============================================================

-- 2. All tags for a specific metric
--    Note the special syntax: 'database.schema.view_name!entity.metric_name'
SELECT OBJECT_NAME, TAG_NAME, TAG_VALUE
FROM TABLE(SNIPPETS.INFORMATION_SCHEMA.TAG_REFERENCES(
    'SNIPPETS.PUBLIC.CHANNEL_SALES_TAGGED_SV!TAG_STORE_SALES.STORE_REVENUE',
    'semantic metric'
));


-- 3. All certified metrics in the SV
--    (query tag_references for each metric, filter by status='certified')
SELECT OBJECT_NAME, TAG_NAME, TAG_VALUE
FROM TABLE(SNIPPETS.INFORMATION_SCHEMA.TAG_REFERENCES(
    'SNIPPETS.PUBLIC.CHANNEL_SALES_TAGGED_SV',
    'semantic view'
))
WHERE TAG_NAME = 'METRIC_STATUS' AND TAG_VALUE = 'certified';


-- 4. Discovery: find all metrics owned by a specific team
--    Use SHOW SEMANTIC METRICS to get the list, then join to tag_references
SHOW SEMANTIC METRICS IN SNIPPETS.PUBLIC.CHANNEL_SALES_TAGGED_SV;


-- ============================================================
-- TAG SYNTAX NOTES
-- ============================================================

-- In DDL:
--   WITH TAG (tag_name = 'value', tag_name2 = 'value2')
--   Tag names refer to TAG objects already created via CREATE TAG

-- In tag_references():
--   Object reference format: 'DB.SCHEMA.VIEW_NAME!ENTITY.METRIC_LOGICAL_NAME'
--   Object type: 'semantic metric'

-- Tags are Snowflake governance objects — they can be queried via
-- INFORMATION_SCHEMA.TAG_REFERENCES and governed via roles/policies.
