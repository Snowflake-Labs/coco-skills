-- Introspection: Queries
-- Prerequisites: deploy multi_fact_table/semantic_view.sql first

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- DESCRIBE — full DDL round-trip view
-- ============================================================

-- Returns all tables, relationships, dimensions, metrics, facts, VQRs, and AI metadata
DESCRIBE SEMANTIC VIEW SNIPPETS.PUBLIC.MULTI_CHANNEL_SV;


-- ============================================================
-- SHOW SEMANTIC VIEWS
-- ============================================================

-- List all SVs in a schema
SHOW SEMANTIC VIEWS IN SNIPPETS.PUBLIC;

-- List SVs matching a pattern
SHOW SEMANTIC VIEWS LIKE '%CHANNEL%' IN SNIPPETS.PUBLIC;


-- ============================================================
-- SHOW SEMANTIC METRICS — discover what metrics are available
-- ============================================================

-- List all metrics in a SV (name, synonyms, expression, tags)
SHOW SEMANTIC METRICS IN SNIPPETS.PUBLIC.MULTI_CHANNEL_SV;

-- You can also use this output in downstream tooling:
-- the result includes metric logical names usable in SEMANTIC_VIEW() queries.


-- ============================================================
-- SHOW SEMANTIC DIMENSIONS FOR METRIC — dimension compatibility
-- ============================================================

-- Which dimensions can be used with store_revenue?
SHOW SEMANTIC DIMENSIONS IN SNIPPETS.PUBLIC.MULTI_CHANNEL_SV
FOR METRIC CHANNEL_STORE_SALES.STORE_REVENUE;

-- Which dimensions can be used with web_revenue?
SHOW SEMANTIC DIMENSIONS IN SNIPPETS.PUBLIC.MULTI_CHANNEL_SV
FOR METRIC CHANNEL_WEB_SALES.WEB_REVENUE;

-- Key insight: metrics from different fact tables may have different
-- dimension compatibility. SHOW SEMANTIC DIMENSIONS tells you exactly
-- which dimensions are reachable for each metric — useful when debugging
-- "dimension not available for this metric" errors.


-- ============================================================
-- LINEAGE — upstream and downstream dependencies
-- ============================================================

-- What tables does this SV depend on? (upstream)
SELECT SOURCE_OBJECT_NAME, TARGET_OBJECT_NAME, SOURCE_OBJECT_DOMAIN,
       TARGET_OBJECT_DOMAIN, DISTANCE
FROM TABLE(
    SNOWFLAKE.CORE.GET_LINEAGE(
        'SNIPPETS.PUBLIC.MULTI_CHANNEL_SV',
        'SEMANTIC_VIEW',
        'UPSTREAM',
        5
    )
)
ORDER BY DISTANCE, SOURCE_OBJECT_NAME;


-- What depends on this SV? (downstream — reports, pipelines, agents)
SELECT SOURCE_OBJECT_NAME, TARGET_OBJECT_NAME, SOURCE_OBJECT_DOMAIN,
       TARGET_OBJECT_DOMAIN, DISTANCE
FROM TABLE(
    SNOWFLAKE.CORE.GET_LINEAGE(
        'SNIPPETS.PUBLIC.MULTI_CHANNEL_SV',
        'SEMANTIC_VIEW',
        'DOWNSTREAM',
        5
    )
)
ORDER BY DISTANCE, TARGET_OBJECT_NAME;
