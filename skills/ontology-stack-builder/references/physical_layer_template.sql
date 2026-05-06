-- ============================================================================
-- Physical Layer Template (KG Path Only)
-- Creates KG_NODE and KG_EDGE tables for Knowledge Graph storage
-- Replace {DATABASE}, {SCHEMA} with actual values
-- ============================================================================

USE SCHEMA {DATABASE}.{SCHEMA};

-- KG_NODE: Universal node table for all entity types
CREATE OR REPLACE TABLE KG_NODE (
    NODE_ID         STRING NOT NULL,
    NODE_TYPE       STRING NOT NULL,
    NAME            STRING,
    PROPS           VARIANT,
    TS_INGESTED     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_KG_NODE PRIMARY KEY (NODE_ID)
) CLUSTER BY (NODE_TYPE);

-- KG_EDGE: Universal edge table for all relationship types
CREATE OR REPLACE TABLE KG_EDGE (
    EDGE_ID         STRING NOT NULL,
    SRC_ID          STRING NOT NULL,
    DST_ID          STRING NOT NULL,
    EDGE_TYPE       STRING NOT NULL,
    WEIGHT          FLOAT DEFAULT 1.0,
    PROPS           VARIANT,
    EFFECTIVE_START DATE,
    EFFECTIVE_END   DATE,
    TS_INGESTED     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_KG_EDGE PRIMARY KEY (EDGE_ID),
    CONSTRAINT FK_EDGE_SRC FOREIGN KEY (SRC_ID) REFERENCES KG_NODE(NODE_ID),
    CONSTRAINT FK_EDGE_DST FOREIGN KEY (DST_ID) REFERENCES KG_NODE(NODE_ID)
) CLUSTER BY (EDGE_TYPE, SRC_ID, DST_ID);

-- Load pattern for nodes (one INSERT per source table):
-- INSERT INTO KG_NODE (NODE_ID, NODE_TYPE, NAME, PROPS)
-- SELECT
--     {ID_EXPR}::STRING AS NODE_ID,
--     '{CLASS_NAME}' AS NODE_TYPE,
--     {NAME_EXPR}::STRING AS NAME,
--     OBJECT_CONSTRUCT(
--         'col1', COL1,
--         'col2', COL2
--     ) AS PROPS
-- FROM {SOURCE_TABLE}
-- WHERE {FILTER_CONDITION};  -- omit if no filter

-- Load pattern for edges (one INSERT per relationship):
-- INSERT INTO KG_EDGE (EDGE_ID, SRC_ID, DST_ID, EDGE_TYPE, WEIGHT, PROPS)
-- SELECT
--     UUID_STRING() AS EDGE_ID,
--     {SRC_ID_EXPR}::STRING AS SRC_ID,
--     {DST_ID_EXPR}::STRING AS DST_ID,
--     '{REL_NAME}' AS EDGE_TYPE,
--     {WEIGHT_EXPR} AS WEIGHT,
--     {PROPS_EXPR} AS PROPS
-- FROM {SOURCE_TABLE}
-- WHERE {FILTER_CONDITION};  -- omit if no filter
