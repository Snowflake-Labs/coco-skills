-- ============================================================================
-- Abstract Ontology Views Template
-- Replace {DATABASE}, {SCHEMA} with actual values
-- ============================================================================

USE SCHEMA {DATABASE}.{SCHEMA};

-- Per-class entity view pattern (one per concrete class):
-- CREATE OR REPLACE VIEW VW_ONT_{CLASS_NAME} AS
-- SELECT
--     {ID_EXPR} AS ENTITY_ID,
--     '{CLASS_NAME}' AS ENTITY_TYPE,
--     {NAME_EXPR} AS ENTITY_NAME,
--     {SUBTYPE_EXPR} AS SUBTYPE,
--     {PROPS_EXPR} AS PROPS
-- FROM {SOURCE_TABLE}
-- WHERE {FILTER_COL} = '{FILTER_VAL}';   -- omit WHERE if no filter

-- Unified entity view (UNION ALL of all concrete class views):
-- CREATE OR REPLACE VIEW VW_ONT_ALL_ENTITIES AS
-- SELECT ENTITY_ID, ENTITY_TYPE, ENTITY_NAME, SUBTYPE, PROPS FROM VW_ONT_{CLASS1}
-- UNION ALL
-- SELECT ENTITY_ID, ENTITY_TYPE, ENTITY_NAME, SUBTYPE, PROPS FROM VW_ONT_{CLASS2}
-- ...;

-- Resolved relationships view:
-- CREATE OR REPLACE VIEW REL_RESOLVED AS
-- SELECT
--     r.REL_NAME,
--     r.SRC_ID_EXPR AS SRC_ID,
--     src.ENTITY_NAME AS SRC_NAME,
--     src.ENTITY_TYPE AS SRC_TYPE,
--     r.DST_ID_EXPR AS DST_ID,
--     dst.ENTITY_NAME AS DST_NAME,
--     dst.ENTITY_TYPE AS DST_TYPE,
--     r.WEIGHT_EXPR AS WEIGHT
-- FROM {EDGE_SOURCE} r
-- LEFT JOIN VW_ONT_ALL_ENTITIES src ON r.SRC_ID = src.ENTITY_ID
-- LEFT JOIN VW_ONT_ALL_ENTITIES dst ON r.DST_ID = dst.ENTITY_ID;

-- Hierarchy stats view:
-- CREATE OR REPLACE VIEW VW_ONT_HIERARCHY_STATS AS
-- SELECT
--     e.ENTITY_ID,
--     e.ENTITY_NAME,
--     e.ENTITY_TYPE,
--     COUNT(DISTINCT child_rel.DST_ID) AS DIRECT_CHILDREN,
--     COUNT(DISTINCT parent_rel.SRC_ID) AS DIRECT_PARENTS
-- FROM VW_ONT_ALL_ENTITIES e
-- LEFT JOIN REL_RESOLVED child_rel ON e.ENTITY_ID = child_rel.SRC_ID AND child_rel.REL_NAME = 'subClassOf'
-- LEFT JOIN REL_RESOLVED parent_rel ON e.ENTITY_ID = parent_rel.DST_ID AND parent_rel.REL_NAME = 'subClassOf'
-- GROUP BY e.ENTITY_ID, e.ENTITY_NAME, e.ENTITY_TYPE;
