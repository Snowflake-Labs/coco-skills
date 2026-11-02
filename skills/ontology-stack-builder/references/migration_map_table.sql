-- ONT_BON_MIGRATION_MAP: one row per source object of an ontology stack, recording
-- what it became in Snowflake Business Ontology. Created and maintained by
-- scripts/bon_reconcile.py (Path M). Keeps the full original source row, so the
-- migration is lossless even for objects BON cannot represent.
--
-- STATUS: MIGRATED (read back from BON), MISSING (expected but not found),
--         NOT_MIGRATED (no BON home by design; see migration/MAPPING.md)
-- SOURCE_KIND: CLASS | SUBCLASS | RELATION | BINDING | PROPERTY | DERIVED | SHARED

CREATE TABLE IF NOT EXISTS {DATABASE}.{SCHEMA}.ONT_BON_MIGRATION_MAP (
    ONTOLOGY_NAME     STRING        NOT NULL,
    SOURCE_KIND       STRING        NOT NULL,
    SOURCE_KEY        STRING        NOT NULL,
    BON_DOMAIN        STRING,
    BON_NAME          STRING,
    BON_KIND          STRING,       -- ENTITY | METRIC | TERM | DIMENSION_CONCEPT | <relationship type> | <refType>
    BON_TERM_ID       STRING,       -- BON termId (decimal string) for node rows
    BON_DETAIL        VARIANT,      -- edge or assetRefJson as written
    STATUS            STRING        NOT NULL,
    SOURCE_CHECKSUM   STRING,       -- set only when MIGRATED; drives delta re-runs
    SOURCE_ROW        VARIANT,      -- the original ONT_* row
    NOTE              STRING,
    MIGRATED_AT       TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (ONTOLOGY_NAME, SOURCE_KIND, SOURCE_KEY)
)
COMMENT = 'ontology-stack-builder Path M: stack object -> Business Ontology mapping';

-- Useful checks
-- SELECT SOURCE_KIND, STATUS, COUNT(*) FROM {DATABASE}.{SCHEMA}.ONT_BON_MIGRATION_MAP GROUP BY 1, 2 ORDER BY 1, 2;
-- SELECT * FROM {DATABASE}.{SCHEMA}.ONT_BON_MIGRATION_MAP WHERE STATUS = 'MISSING';
