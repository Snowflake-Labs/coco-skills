# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyyaml>=6.0",
# ]
# ///
"""
Generate Ontology SQL - Produces SQL scripts for Layers 1-3 of the Ontology-on-Snowflake stack.

Generates:
  - 01_physical_layer.sql (KG path only): KG_NODE/KG_EDGE tables + data load
  - 02_concrete_views.sql: V_{CLASS} typed entity views, V_{REL} relationship views
  - 03_metadata_tables.sql: ONT_CLASS, ONT_RELATION_DEF, ONT_CLASS_MAP, ONT_REL_MAP + expanded tables + inserts
  - 04_abstract_views.sql: VW_ONT_* views, REL_RESOLVED, VW_ONT_ALL_ENTITIES
  - 05_view_generator_sp.sql: SP_GENERATE_ONTOLOGY_VIEWS() stored procedure

Usage:
    uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/generate_ontology_sql.py \
      --classes-json /tmp/ontology_parsed/classes.json \
      --relations-json /tmp/ontology_parsed/relations.json \
      --mappings-json /tmp/ontology_parsed/mappings.json \
      --database MYDB --schema MYSCHEMA --ontology-name DOMAIN \
      --kg-path true \
      --output-dir /tmp/generated
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def sql_escape(val: str | None) -> str:
    if val is None:
        return "NULL"
    return "'" + str(val).replace("'", "''") + "'"


def generate_physical_layer_sql(
    classes: list[dict],
    relations: list[dict],
    mappings: dict,
    database: str,
    schema: str,
) -> str:
    """Generate KG_NODE/KG_EDGE DDL and data load INSERTs."""
    fqn = f"{database}.{schema}"
    lines = []
    lines.append(f"-- {'='*76}")
    lines.append(f"-- Layer 1: Physical KG Tables")
    lines.append(f"-- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"-- {'='*76}\n")
    lines.append(f"USE SCHEMA {fqn};\n")

    # KG_NODE
    lines.append("CREATE OR REPLACE TABLE KG_NODE (")
    lines.append("    NODE_ID         STRING NOT NULL,")
    lines.append("    NODE_TYPE       STRING NOT NULL,")
    lines.append("    NAME            STRING,")
    lines.append("    PROPS           VARIANT,")
    lines.append("    TS_INGESTED     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),")
    lines.append("    CONSTRAINT PK_KG_NODE PRIMARY KEY (NODE_ID)")
    lines.append(") CLUSTER BY (NODE_TYPE);\n")

    # KG_EDGE
    lines.append("CREATE OR REPLACE TABLE KG_EDGE (")
    lines.append("    EDGE_ID         STRING NOT NULL,")
    lines.append("    SRC_ID          STRING NOT NULL,")
    lines.append("    DST_ID          STRING NOT NULL,")
    lines.append("    EDGE_TYPE       STRING NOT NULL,")
    lines.append("    WEIGHT          FLOAT DEFAULT 1.0,")
    lines.append("    PROPS           VARIANT,")
    lines.append("    EFFECTIVE_START DATE,")
    lines.append("    EFFECTIVE_END   DATE,")
    lines.append("    TS_INGESTED     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),")
    lines.append("    CONSTRAINT PK_KG_EDGE PRIMARY KEY (EDGE_ID)")
    lines.append(") CLUSTER BY (EDGE_TYPE, SRC_ID, DST_ID);\n")

    # Node load inserts
    class_maps = mappings.get("class_mappings", [])
    for cm in class_maps:
        cls_name = cm["class_name"]
        src_table = cm["source_table"]
        id_col = cm["id_column"]
        name_col = cm.get("name_column") or id_col

        lines.append(f"-- Load {cls_name} nodes from {src_table}")
        lines.append(f"INSERT INTO KG_NODE (NODE_ID, NODE_TYPE, NAME, PROPS)")
        lines.append(f"SELECT")
        lines.append(f"    {id_col}::STRING AS NODE_ID,")
        lines.append(f"    '{cls_name}' AS NODE_TYPE,")
        lines.append(f"    {name_col}::STRING AS NAME,")
        lines.append(f"    OBJECT_CONSTRUCT(*) AS PROPS")
        lines.append(f"FROM {src_table};")
        lines.append("")

    # Edge load inserts
    rel_maps = mappings.get("relation_mappings", [])
    for rm in rel_maps:
        rel_name = rm["rel_name"]
        src_table = rm["source_table"]
        src_col = rm.get("src_column") or rm.get("src_id_column", "SRC_ID")
        dst_col = rm.get("dst_column") or rm.get("dst_id_column", "DST_ID")

        lines.append(f"-- Load {rel_name} edges from {src_table}")
        lines.append(f"INSERT INTO KG_EDGE (EDGE_ID, SRC_ID, DST_ID, EDGE_TYPE)")
        lines.append(f"SELECT")
        lines.append(f"    UUID_STRING() AS EDGE_ID,")
        lines.append(f"    {src_col}::STRING AS SRC_ID,")
        lines.append(f"    {dst_col}::STRING AS DST_ID,")
        lines.append(f"    '{rel_name}' AS EDGE_TYPE")
        lines.append(f"FROM {src_table};")
        lines.append("")

    return "\n".join(lines)


# Mapping from Snowflake column types to PROPS extraction cast types
SNOWFLAKE_TYPE_MAP = {
    "NUMBER": "NUMBER",
    "DECIMAL": "NUMBER",
    "NUMERIC": "NUMBER",
    "INT": "INTEGER",
    "INTEGER": "INTEGER",
    "BIGINT": "INTEGER",
    "SMALLINT": "INTEGER",
    "TINYINT": "INTEGER",
    "BYTEINT": "INTEGER",
    "FLOAT": "FLOAT",
    "FLOAT4": "FLOAT",
    "FLOAT8": "FLOAT",
    "DOUBLE": "FLOAT",
    "DOUBLE PRECISION": "FLOAT",
    "REAL": "FLOAT",
    "VARCHAR": "STRING",
    "CHAR": "STRING",
    "CHARACTER": "STRING",
    "STRING": "STRING",
    "TEXT": "STRING",
    "BINARY": "STRING",
    "VARBINARY": "STRING",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "DATETIME": "TIMESTAMP_NTZ",
    "TIME": "TIME",
    "TIMESTAMP": "TIMESTAMP_NTZ",
    "TIMESTAMP_LTZ": "TIMESTAMP_LTZ",
    "TIMESTAMP_NTZ": "TIMESTAMP_NTZ",
    "TIMESTAMP_TZ": "TIMESTAMP_TZ",
    "VARIANT": "VARIANT",
    "OBJECT": "VARIANT",
    "ARRAY": "VARIANT",
}


def _sf_cast_type(col_type: str) -> str:
    """Map a Snowflake column type to a PROPS extraction cast type."""
    # Strip precision/scale like NUMBER(10,2) -> NUMBER
    base = col_type.upper().split("(")[0].strip()
    return SNOWFLAKE_TYPE_MAP.get(base, "STRING")


def generate_concrete_views_sql(
    mappings: dict,
    database: str,
    schema: str,
    kg_path: bool,
) -> str:
    """Generate concrete typed entity views (V_{CLASS}) and relationship views.

    For KG path: V_{CLASS} extracts PROPS fields into typed columns from KG_NODE.
    For direct path: V_{CLASS} is a thin wrapper over the source table with typed columns.
    """
    fqn = f"{database}.{schema}"
    lines = []
    lines.append(f"-- {'='*76}")
    lines.append(f"-- Concrete Entity & Relationship Views")
    lines.append(f"-- Typed views over KG_NODE/KG_EDGE (KG path) or source tables (direct path)")
    lines.append(f"-- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"-- {'='*76}\n")
    lines.append(f"USE SCHEMA {fqn};\n")

    class_maps = mappings.get("class_mappings", [])

    # Per-class concrete entity views
    for cm in class_maps:
        cls_name = cm["class_name"]
        view_name = f"V_{cls_name.upper()}"
        columns = cm.get("columns", [])
        id_col = cm["id_column"]
        name_col = cm.get("name_column")

        if kg_path:
            # Extract typed properties from KG_NODE PROPS variant
            lines.append(f"-- {view_name}: Typed view for {cls_name} (KG path)")
            lines.append(f"CREATE OR REPLACE VIEW {view_name} AS")
            lines.append(f"SELECT")
            lines.append(f"    NODE_ID,")
            lines.append(f"    NAME,")

            # Project each non-PK, non-FK column as PROPS:col_name::TYPE
            prop_cols = [
                c for c in columns
                if not c.get("is_primary_key") and not c.get("is_foreign_key")
                and c["name"].upper() not in ("NODE_ID", "NODE_TYPE", "NAME", "PROPS", "TS_INGESTED")
            ]
            for col in prop_cols:
                cast_type = _sf_cast_type(col["data_type"])
                col_lower = col["name"].lower()
                lines.append(f"    PROPS:{col_lower}::{cast_type} AS {col['name']},")

            lines.append(f"    PROPS")
            lines.append(f"FROM KG_NODE")
            lines.append(f"WHERE NODE_TYPE = '{cls_name}';\n")
        else:
            # Direct table path: select all typed columns from source table
            src = cm["source_table"]
            lines.append(f"-- {view_name}: Typed view for {cls_name} (direct table path)")
            lines.append(f"CREATE OR REPLACE VIEW {view_name} AS")
            lines.append(f"SELECT")

            select_cols = []
            for col in columns:
                select_cols.append(f"    {col['name']}")

            if not select_cols:
                # Fallback: SELECT *
                lines.append(f"    *")
            else:
                lines.append(",\n".join(select_cols))

            lines.append(f"FROM {src};\n")

    # Per-relation concrete relationship views (KG path only — direct path uses source tables directly)
    if kg_path:
        rel_maps = mappings.get("relation_mappings", [])
        for rm in rel_maps:
            rel_name = rm["rel_name"]
            view_name = f"V_{rel_name.upper()}"
            extra_cols = rm.get("columns", [])

            lines.append(f"-- {view_name}: Relationship view for {rel_name} (KG path)")
            lines.append(f"CREATE OR REPLACE VIEW {view_name} AS")
            lines.append(f"SELECT")
            lines.append(f"    SRC_ID,")
            lines.append(f"    DST_ID,")
            lines.append(f"    EDGE_TYPE,")
            lines.append(f"    PROPS,")
            lines.append(f"    WEIGHT,")
            lines.append(f"    EFFECTIVE_START,")
            lines.append(f"    EFFECTIVE_END,")

            # Project extra columns from PROPS if available
            for col in extra_cols:
                cast_type = _sf_cast_type(col["data_type"])
                col_lower = col["name"].lower()
                lines.append(f"    PROPS:{col_lower}::{cast_type} AS {col['name']},")

            # Remove trailing comma from last PROPS extraction line if any extra cols were added
            if extra_cols:
                lines[-1] = lines[-1].rstrip(",")
            else:
                # Remove trailing comma from EFFECTIVE_END line
                lines[-1] = lines[-1].rstrip(",")

            lines.append(f"FROM KG_EDGE")
            lines.append(f"WHERE EDGE_TYPE = '{rel_name}';\n")

    return "\n".join(lines)


def generate_metadata_sql(
    classes: list[dict],
    relations: list[dict],
    mappings: dict,
    database: str,
    schema: str,
    ontology_name: str,
    kg_path: bool,
) -> str:
    """Generate metadata tables DDL and INSERT statements."""
    fqn = f"{database}.{schema}"
    lines = []
    lines.append(f"-- {'='*76}")
    lines.append(f"-- Layer 2: Ontology Metadata Tables for {ontology_name}")
    lines.append(f"-- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"-- {'='*76}\n")
    lines.append(f"USE SCHEMA {fqn};\n")

    # ONT_CLASS
    lines.append("CREATE TABLE IF NOT EXISTS ONT_CLASS (")
    lines.append("    CLASS_NAME          STRING NOT NULL PRIMARY KEY,")
    lines.append("    PARENT_CLASS_NAME   STRING,")
    lines.append("    IS_ABSTRACT         BOOLEAN DEFAULT FALSE,")
    lines.append("    DESCRIPTION         STRING,")
    lines.append(f"    ONTOLOGY_NAME       STRING DEFAULT {sql_escape(ontology_name)},")
    lines.append("    TYPE_CLASS          STRING DEFAULT 'ANALYTICAL',")
    lines.append("    CREATED_AT          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()")
    lines.append(");\n")

    if classes:
        lines.append("INSERT INTO ONT_CLASS (CLASS_NAME, PARENT_CLASS_NAME, IS_ABSTRACT, DESCRIPTION, TYPE_CLASS)")
        lines.append("SELECT * FROM VALUES")
        rows = []
        for cls in classes:
            if cls.get("is_deprecated"):
                continue
            name = cls.get("class_name") or cls.get("name")
            parent = cls.get("parent_class_name") or cls.get("parent_name")
            is_abs = cls.get("is_abstract", False)
            desc = (cls.get("description") or cls.get("label") or "")[:500]
            type_class = "ANALYTICAL" if is_abs else "OPERATIONAL"
            rows.append(
                f"    ({sql_escape(name)}, {sql_escape(parent)}, {str(is_abs).upper()}, "
                f"{sql_escape(desc)}, {sql_escape(type_class)})"
            )
        lines.append(",\n".join(rows))
        lines.append("AS t(CLASS_NAME, PARENT_CLASS_NAME, IS_ABSTRACT, DESCRIPTION, TYPE_CLASS);")
    lines.append("")

    # ONT_RELATION_DEF
    lines.append("CREATE TABLE IF NOT EXISTS ONT_RELATION_DEF (")
    lines.append("    REL_NAME            STRING NOT NULL PRIMARY KEY,")
    lines.append("    DOMAIN_CLASS        STRING NOT NULL,")
    lines.append("    RANGE_CLASS         STRING NOT NULL,")
    lines.append("    CARDINALITY         STRING DEFAULT 'N:N',")
    lines.append("    IS_HIERARCHICAL     BOOLEAN DEFAULT FALSE,")
    lines.append("    IS_TRANSITIVE       BOOLEAN DEFAULT FALSE,")
    lines.append("    INVERSE_REL_NAME    STRING,")
    lines.append("    DESCRIPTION         STRING,")
    lines.append(f"    ONTOLOGY_NAME       STRING DEFAULT {sql_escape(ontology_name)}")
    lines.append(");\n")

    if relations:
        lines.append("INSERT INTO ONT_RELATION_DEF (REL_NAME, DOMAIN_CLASS, RANGE_CLASS, CARDINALITY, IS_HIERARCHICAL, IS_TRANSITIVE, INVERSE_REL_NAME, DESCRIPTION)")
        lines.append("SELECT * FROM VALUES")
        rows = []
        for rel in relations:
            rn = rel.get("rel_name") or rel.get("name")
            rows.append(
                f"    ({sql_escape(rn)}, {sql_escape(rel.get('domain_class', 'Thing'))}, "
                f"{sql_escape(rel.get('range_class', 'Thing'))}, {sql_escape(rel.get('cardinality', 'N:N'))}, "
                f"{str(rel.get('is_hierarchical', False)).upper()}, {str(rel.get('is_transitive', False)).upper()}, "
                f"{sql_escape(rel.get('inverse_name'))}, {sql_escape((rel.get('description') or '')[:500])})"
            )
        lines.append(",\n".join(rows))
        lines.append("AS t(REL_NAME, DOMAIN_CLASS, RANGE_CLASS, CARDINALITY, IS_HIERARCHICAL, IS_TRANSITIVE, INVERSE_REL_NAME, DESCRIPTION);")
    lines.append("")

    # ONT_CLASS_MAP
    lines.append("CREATE TABLE IF NOT EXISTS ONT_CLASS_MAP (")
    lines.append("    MAP_ID              STRING DEFAULT UUID_STRING() PRIMARY KEY,")
    lines.append("    CLASS_NAME          STRING NOT NULL,")
    lines.append("    SOURCE_DATABASE     STRING NOT NULL,")
    lines.append("    SOURCE_SCHEMA       STRING NOT NULL,")
    lines.append("    SOURCE_TABLE        STRING NOT NULL,")
    lines.append("    FILTER_COL          STRING,")
    lines.append("    FILTER_VAL          STRING,")
    lines.append("    ID_EXPR             STRING NOT NULL,")
    lines.append("    NAME_EXPR           STRING,")
    lines.append("    SUBTYPE_EXPR        STRING,")
    lines.append(f"    ONTOLOGY_NAME       STRING DEFAULT {sql_escape(ontology_name)}")
    lines.append(");\n")

    class_maps = mappings.get("class_mappings", [])
    if class_maps:
        lines.append("INSERT INTO ONT_CLASS_MAP (CLASS_NAME, SOURCE_DATABASE, SOURCE_SCHEMA, SOURCE_TABLE, FILTER_COL, FILTER_VAL, ID_EXPR, NAME_EXPR)")
        lines.append("SELECT * FROM VALUES")
        rows = []
        for cm in class_maps:
            # Parse source_table which may be fully qualified
            src = cm["source_table"]
            parts = src.split(".")
            if len(parts) == 3:
                src_db, src_schema, src_tbl = parts
            else:
                src_db, src_schema, src_tbl = database, schema, src

            if kg_path:
                # In KG path, source is KG_NODE with filter
                rows.append(
                    f"    ({sql_escape(cm['class_name'])}, {sql_escape(database)}, {sql_escape(schema)}, "
                    f"'KG_NODE', 'NODE_TYPE', {sql_escape(cm['class_name'])}, "
                    f"'NODE_ID', 'NAME')"
                )
            else:
                rows.append(
                    f"    ({sql_escape(cm['class_name'])}, {sql_escape(src_db)}, {sql_escape(src_schema)}, "
                    f"{sql_escape(src_tbl)}, {sql_escape(cm.get('filter_condition'))}, NULL, "
                    f"{sql_escape(cm['id_column'])}, {sql_escape(cm.get('name_column'))})"
                )
        lines.append(",\n".join(rows))
        lines.append("AS t(CLASS_NAME, SOURCE_DATABASE, SOURCE_SCHEMA, SOURCE_TABLE, FILTER_COL, FILTER_VAL, ID_EXPR, NAME_EXPR);")
    lines.append("")

    # ONT_REL_MAP
    lines.append("CREATE TABLE IF NOT EXISTS ONT_REL_MAP (")
    lines.append("    MAP_ID              STRING DEFAULT UUID_STRING() PRIMARY KEY,")
    lines.append("    REL_NAME            STRING NOT NULL,")
    lines.append("    SOURCE_DATABASE     STRING NOT NULL,")
    lines.append("    SOURCE_SCHEMA       STRING NOT NULL,")
    lines.append("    SOURCE_TABLE        STRING NOT NULL,")
    lines.append("    SRC_ID_EXPR         STRING NOT NULL,")
    lines.append("    DST_ID_EXPR         STRING NOT NULL,")
    lines.append("    FILTER_COL          STRING,")
    lines.append("    FILTER_VAL          STRING,")
    lines.append(f"    ONTOLOGY_NAME       STRING DEFAULT {sql_escape(ontology_name)}")
    lines.append(");\n")

    rel_maps = mappings.get("relation_mappings", [])
    if rel_maps:
        lines.append("INSERT INTO ONT_REL_MAP (REL_NAME, SOURCE_DATABASE, SOURCE_SCHEMA, SOURCE_TABLE, SRC_ID_EXPR, DST_ID_EXPR, FILTER_COL, FILTER_VAL)")
        lines.append("SELECT * FROM VALUES")
        rows = []
        for rm in rel_maps:
            src = rm["source_table"]
            parts = src.split(".")
            if len(parts) == 3:
                src_db, src_schema, src_tbl = parts
            else:
                src_db, src_schema, src_tbl = database, schema, src

            if kg_path:
                rows.append(
                    f"    ({sql_escape(rm['rel_name'])}, {sql_escape(database)}, {sql_escape(schema)}, "
                    f"'KG_EDGE', 'SRC_ID', 'DST_ID', 'EDGE_TYPE', {sql_escape(rm['rel_name'])})"
                )
            else:
                src_col_val = rm.get("src_column") or rm.get("src_id_column", "SRC_ID")
                dst_col_val = rm.get("dst_column") or rm.get("dst_id_column", "DST_ID")
                rows.append(
                    f"    ({sql_escape(rm['rel_name'])}, {sql_escape(src_db)}, {sql_escape(src_schema)}, "
                    f"{sql_escape(src_tbl)}, {sql_escape(src_col_val)}, {sql_escape(dst_col_val)}, "
                    f"NULL, NULL)"
                )
        lines.append(",\n".join(rows))
        lines.append("AS t(REL_NAME, SOURCE_DATABASE, SOURCE_SCHEMA, SOURCE_TABLE, SRC_ID_EXPR, DST_ID_EXPR, FILTER_COL, FILTER_VAL);")
    lines.append("")

    # ── ONT_ONTOLOGY ──
    lines.append("-- Ontology Registry")
    lines.append("CREATE TABLE IF NOT EXISTS ONT_ONTOLOGY (")
    lines.append("    ONTOLOGY_NAME       STRING PRIMARY KEY,")
    lines.append("    DESCRIPTION         STRING,")
    lines.append("    VERSION             STRING,")
    lines.append("    DEFAULT_SCHEMA      STRING,")
    lines.append("    CREATED_BY          STRING,")
    lines.append("    CREATED_AT          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),")
    lines.append("    IS_ACTIVE           BOOLEAN DEFAULT TRUE")
    lines.append(");\n")
    lines.append(f"INSERT INTO ONT_ONTOLOGY (ONTOLOGY_NAME, VERSION, DESCRIPTION, DEFAULT_SCHEMA, CREATED_BY, IS_ACTIVE)")
    lines.append(f"VALUES ({sql_escape(ontology_name)}, '1.0.0', {sql_escape(ontology_name + ' Ontology')}, {sql_escape(schema)}, 'SYSTEM', TRUE);\n")

    # ── ONT_OBJECT_SOURCE ──
    lines.append("-- Object Source Mappings")
    lines.append("CREATE TABLE IF NOT EXISTS ONT_OBJECT_SOURCE (")
    lines.append("    ONTOLOGY_NAME       STRING,")
    lines.append("    OBJ_TYPE            STRING,")
    lines.append("    SOURCE_TABLE        STRING,")
    lines.append("    FILTER_SQL          STRING,")
    lines.append("    MAPPING             VARIANT,")
    lines.append("    PRIMARY KEY (ONTOLOGY_NAME, OBJ_TYPE, SOURCE_TABLE)")
    lines.append(");\n")

    if class_maps:
        for cm in class_maps:
            cls_name = cm["class_name"]
            if kg_path:
                src_tbl = "KG_NODE"
                filter_sql = f"NODE_TYPE = ''{cls_name.upper()}''"
                # Build mapping JSON from columns
                mapping_pairs = ['"NODE_ID": "id"', '"NAME": "name"']
                for col in cm.get("columns", []):
                    if not col.get("is_primary_key") and not col.get("is_foreign_key"):
                        col_lower = col["name"].lower()
                        if col_lower not in ("node_id", "node_type", "name", "props", "ts_ingested"):
                            mapping_pairs.append(f'"PROPS:{col["name"]}": "{col_lower}"')
                mapping_json = "{" + ", ".join(mapping_pairs) + "}"
            else:
                src = cm["source_table"]
                parts = src.split(".")
                src_tbl = parts[-1] if parts else src
                filter_sql = ""
                mapping_pairs = []
                for col in cm.get("columns", []):
                    mapping_pairs.append(f'"{col["name"]}": "{col["name"].lower()}"')
                mapping_json = "{" + ", ".join(mapping_pairs) + "}"
            lines.append(f"INSERT INTO ONT_OBJECT_SOURCE (ONTOLOGY_NAME, OBJ_TYPE, SOURCE_TABLE, FILTER_SQL, MAPPING)")
            lines.append(f"SELECT {sql_escape(ontology_name)}, {sql_escape(cls_name)}, {sql_escape(src_tbl)}, {sql_escape(filter_sql)}, PARSE_JSON('{mapping_json}');")
        lines.append("")

    # ── ONT_LINK_SOURCE ──
    lines.append("-- Link Source Mappings")
    lines.append("CREATE TABLE IF NOT EXISTS ONT_LINK_SOURCE (")
    lines.append("    ONTOLOGY_NAME       STRING,")
    lines.append("    LINK_TYPE           STRING,")
    lines.append("    SOURCE_TABLE        STRING,")
    lines.append("    FILTER_SQL          STRING,")
    lines.append("    MAPPING             VARIANT,")
    lines.append("    PRIMARY KEY (ONTOLOGY_NAME, LINK_TYPE, SOURCE_TABLE)")
    lines.append(");\n")

    if rel_maps:
        for rm in rel_maps:
            rel_name = rm["rel_name"]
            if kg_path:
                src_tbl = "KG_EDGE"
                filter_sql = f"EDGE_TYPE = ''{rel_name}''"
                mapping_json = '{"SRC_ID": "src_id", "DST_ID": "dst_id"}'
            else:
                src = rm["source_table"]
                parts = src.split(".")
                src_tbl = parts[-1] if parts else src
                filter_sql = ""
                src_col_val = rm.get("src_column") or rm.get("src_id_column", "SRC_ID")
                dst_col_val = rm.get("dst_column") or rm.get("dst_id_column", "DST_ID")
                mapping_json = '{' + f'"SRC_ID": "{src_col_val}", "DST_ID": "{dst_col_val}"' + '}'
            lines.append(f"INSERT INTO ONT_LINK_SOURCE (ONTOLOGY_NAME, LINK_TYPE, SOURCE_TABLE, FILTER_SQL, MAPPING)")
            lines.append(f"SELECT {sql_escape(ontology_name)}, {sql_escape(rel_name)}, {sql_escape(src_tbl)}, {sql_escape(filter_sql)}, PARSE_JSON('{mapping_json}');")
        lines.append("")

    # ── ONT_SHARED_PROPERTY ──
    lines.append("-- Shared Properties")
    lines.append("CREATE TABLE IF NOT EXISTS ONT_SHARED_PROPERTY (")
    lines.append("    SHARED_PROP_NAME    STRING PRIMARY KEY,")
    lines.append("    BASE_TYPE           STRING,")
    lines.append("    DESCRIPTION         STRING,")
    lines.append("    DEFAULT_FORMAT      STRING")
    lines.append(");\n")

    # Auto-detect shared properties: columns that appear in 2+ classes with the same name
    col_counts: dict[str, dict] = {}  # col_name -> {type, count}
    for cm in class_maps:
        for col in cm.get("columns", []):
            if col.get("is_primary_key") or col.get("is_foreign_key"):
                continue
            cn = col["name"].upper()
            if cn not in col_counts:
                col_counts[cn] = {"type": col["data_type"], "count": 0}
            col_counts[cn]["count"] += 1
    shared_props = {k: v for k, v in col_counts.items() if v["count"] >= 2}
    if shared_props:
        lines.append("INSERT INTO ONT_SHARED_PROPERTY (SHARED_PROP_NAME, BASE_TYPE, DESCRIPTION) VALUES")
        rows = []
        for sp_name, sp_info in shared_props.items():
            cast_t = _sf_cast_type(sp_info["type"])
            rows.append(f"    ({sql_escape(sp_name.lower())}, {sql_escape(cast_t)}, {sql_escape(f'Shared property {sp_name.lower()}')})")
        lines.append(",\n".join(rows) + ";\n")

    # ── ONT_PROPERTY ──
    lines.append("-- Property Definitions")
    lines.append("CREATE TABLE IF NOT EXISTS ONT_PROPERTY (")
    lines.append("    CLASS_NAME          STRING,")
    lines.append("    PROP_NAME           STRING,")
    lines.append("    DATA_TYPE           STRING,")
    lines.append("    SHARED_PROP_NAME    STRING,")
    lines.append("    IS_REQUIRED         BOOLEAN DEFAULT FALSE,")
    lines.append("    IS_INDEXED          BOOLEAN DEFAULT FALSE,")
    lines.append("    DESCRIPTION         STRING,")
    lines.append("    PRIMARY KEY (CLASS_NAME, PROP_NAME)")
    lines.append(");\n")

    # Auto-populate ONT_PROPERTY from column metadata
    prop_rows = []
    for cm in class_maps:
        cls_name = cm["class_name"]
        for col in cm.get("columns", []):
            if col.get("is_primary_key") or col.get("is_foreign_key"):
                continue
            cn = col["name"].upper()
            if cn in ("NODE_ID", "NODE_TYPE", "NAME", "PROPS", "TS_INGESTED"):
                continue
            cast_t = _sf_cast_type(col["data_type"])
            sp = col["name"].lower() if cn in shared_props else None
            is_req = not col.get("nullable", True)
            col_name = col["name"]
            prop_rows.append(
                f"    ({sql_escape(cls_name)}, {sql_escape(col['name'].lower())}, "
                f"{sql_escape(cast_t)}, {sql_escape(sp)}, "
                f"{str(is_req).upper()}, FALSE, {sql_escape(col_name + ' of ' + cls_name)})"
            )
    if prop_rows:
        lines.append("INSERT INTO ONT_PROPERTY (CLASS_NAME, PROP_NAME, DATA_TYPE, SHARED_PROP_NAME, IS_REQUIRED, IS_INDEXED, DESCRIPTION) VALUES")
        lines.append(",\n".join(prop_rows) + ";\n")

    # ── ONT_DERIVED_PROPERTY ──
    lines.append("-- Derived Properties")
    lines.append("CREATE TABLE IF NOT EXISTS ONT_DERIVED_PROPERTY (")
    lines.append("    CLASS_NAME          STRING,")
    lines.append("    PROP_NAME           STRING,")
    lines.append("    DEFINITION_KIND     STRING,")
    lines.append("    SQL_EXPR            STRING,")
    lines.append("    FUNCTION_NAME       STRING,")
    lines.append("    DESCRIPTION         STRING,")
    lines.append("    PRIMARY KEY (CLASS_NAME, PROP_NAME)")
    lines.append(");\n")

    # ── ONT_INTERFACE ──
    lines.append("-- Interfaces (Polymorphism)")
    lines.append("CREATE TABLE IF NOT EXISTS ONT_INTERFACE (")
    lines.append("    INTERFACE_NAME      STRING PRIMARY KEY,")
    lines.append("    DESCRIPTION         STRING")
    lines.append(");\n")

    # ── ONT_INTERFACE_PROPERTY ──
    lines.append("CREATE TABLE IF NOT EXISTS ONT_INTERFACE_PROPERTY (")
    lines.append("    INTERFACE_NAME      STRING,")
    lines.append("    PROP_NAME           STRING,")
    lines.append("    SHARED_PROP_NAME    STRING,")
    lines.append("    PRIMARY KEY (INTERFACE_NAME, PROP_NAME)")
    lines.append(");\n")

    # ── ONT_INTERFACE_IMPL ──
    lines.append("CREATE TABLE IF NOT EXISTS ONT_INTERFACE_IMPL (")
    lines.append("    INTERFACE_NAME      STRING,")
    lines.append("    CLASS_NAME          STRING,")
    lines.append("    PRIMARY KEY (INTERFACE_NAME, CLASS_NAME)")
    lines.append(");\n")

    # Auto-populate interfaces from shared properties
    if shared_props:
        # Create one interface per shared property pattern (Named, Temporal, etc.)
        # Group classes by shared property membership
        interface_rows = []
        impl_rows = []
        prop_iface_rows = []
        for sp_name in shared_props:
            iface_name = f"Has{sp_name.replace('_',' ').title().replace(' ','')}"
            interface_rows.append(f"    ({sql_escape(iface_name)}, {sql_escape(f'Entities with {sp_name.lower()} property')})")
            prop_iface_rows.append(f"    ({sql_escape(iface_name)}, {sql_escape(sp_name.lower())}, {sql_escape(sp_name.lower())})")
            for cm in class_maps:
                for col in cm.get("columns", []):
                    if col["name"].upper() == sp_name:
                        impl_rows.append(f"    ({sql_escape(iface_name)}, {sql_escape(cm['class_name'])})")
                        break
        if interface_rows:
            lines.append("INSERT INTO ONT_INTERFACE (INTERFACE_NAME, DESCRIPTION) VALUES")
            lines.append(",\n".join(interface_rows) + ";\n")
        if prop_iface_rows:
            lines.append("INSERT INTO ONT_INTERFACE_PROPERTY (INTERFACE_NAME, PROP_NAME, SHARED_PROP_NAME) VALUES")
            lines.append(",\n".join(prop_iface_rows) + ";\n")
        if impl_rows:
            lines.append("INSERT INTO ONT_INTERFACE_IMPL (INTERFACE_NAME, CLASS_NAME) VALUES")
            lines.append(",\n".join(impl_rows) + ";\n")

    # ── ONT_RULE ──
    lines.append("-- Inference Rules")
    lines.append("CREATE TABLE IF NOT EXISTS ONT_RULE (")
    lines.append("    RULE_ID             STRING PRIMARY KEY,")
    lines.append("    RULE_KIND           STRING,")
    lines.append("    TARGET_REL          STRING,")
    lines.append("    SOURCE_REL_1        STRING,")
    lines.append("    SOURCE_REL_2        STRING,")
    lines.append("    INVERSE_OF          STRING,")
    lines.append("    DESCRIPTION         STRING,")
    lines.append("    IS_ENABLED          BOOLEAN DEFAULT TRUE,")
    lines.append("    TS_CREATED          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()")
    lines.append(");\n")

    # Auto-populate inverse rules from relation definitions that have inverse_name
    rule_rows = []
    rule_idx = 0
    for rel in relations:
        inv = rel.get("inverse_name")
        if inv:
            rule_idx += 1
            rel_name_val = rel.get("rel_name") or rel.get("name")
            rule_rows.append(
                f"    ({sql_escape(f'RULE_INV_{rule_idx:03d}')}, 'INVERSE', {sql_escape(inv)}, "
                f"NULL, NULL, {sql_escape(rel_name_val)}, TRUE, "
                f"{sql_escape('Infer ' + inv + ' from ' + rel_name_val)})"
            )
    if rule_rows:
        lines.append("INSERT INTO ONT_RULE (RULE_ID, RULE_KIND, TARGET_REL, SOURCE_REL_1, SOURCE_REL_2, INVERSE_OF, IS_ENABLED, DESCRIPTION) VALUES")
        lines.append(",\n".join(rule_rows) + ";\n")

    # ── REL_EDGE_INFERRED ──
    lines.append("-- Inferred Edges")
    lines.append("CREATE TABLE IF NOT EXISTS REL_EDGE_INFERRED (")
    lines.append("    REL_NAME            STRING NOT NULL,")
    lines.append("    SRC_ID              STRING NOT NULL,")
    lines.append("    DST_ID              STRING NOT NULL,")
    lines.append("    INFERENCE_KIND      STRING,")
    lines.append("    RULE_ID             STRING,")
    lines.append("    WEIGHT              FLOAT DEFAULT 1.0,")
    lines.append("    EFFECTIVE_START     DATE,")
    lines.append("    EFFECTIVE_END       DATE,")
    lines.append("    COMPUTED_AT         TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),")
    lines.append("    PRIMARY KEY (REL_NAME, SRC_ID, DST_ID, RULE_ID)")
    lines.append(");\n")

    # ── ONT_CONSTRAINT_VIOLATION ──
    lines.append("-- Data Quality Constraints")
    lines.append("CREATE TABLE IF NOT EXISTS ONT_CONSTRAINT_VIOLATION (")
    lines.append("    VIOLATION_ID        STRING DEFAULT UUID_STRING(),")
    lines.append("    CHECK_NAME          STRING,")
    lines.append("    SCOPE               STRING,")
    lines.append("    REL_OR_CLASS        STRING,")
    lines.append("    SRC_ID              STRING,")
    lines.append("    DST_ID              STRING,")
    lines.append("    DETAILS             STRING,")
    lines.append("    OBSERVED_AT         TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),")
    lines.append("    PRIMARY KEY (VIOLATION_ID)")
    lines.append(");\n")

    # ── ACT_TYPE ──
    lines.append("-- Action Types")
    lines.append("CREATE TABLE IF NOT EXISTS ACT_TYPE (")
    lines.append("    ACTION_TYPE_ID      STRING PRIMARY KEY,")
    lines.append("    ACTION_NAME         STRING NOT NULL,")
    lines.append("    DESCRIPTION         STRING,")
    lines.append("    ONTOLOGY_NAME       STRING,")
    lines.append("    TARGET_CLASS        STRING,")
    lines.append("    HANDLER_PROC        STRING,")
    lines.append("    IS_ENABLED          BOOLEAN DEFAULT TRUE,")
    lines.append("    TS_CREATED          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()")
    lines.append(");\n")

    # ── ACT_DEF ──
    lines.append("-- Action Parameter Definitions")
    lines.append("CREATE TABLE IF NOT EXISTS ACT_DEF (")
    lines.append("    ACTION_TYPE_ID      STRING,")
    lines.append("    PARAM_NAME          STRING,")
    lines.append("    PARAM_TYPE          STRING,")
    lines.append("    IS_REQUIRED         BOOLEAN DEFAULT FALSE,")
    lines.append("    DESCRIPTION         STRING,")
    lines.append("    PRIMARY KEY (ACTION_TYPE_ID, PARAM_NAME)")
    lines.append(");\n")

    # ── ACT_INVOCATION ──
    lines.append("-- Action Invocation Log")
    lines.append("CREATE TABLE IF NOT EXISTS ACT_INVOCATION (")
    lines.append("    INVOCATION_ID       STRING PRIMARY KEY,")
    lines.append("    ACTION_TYPE_ID      STRING NOT NULL,")
    lines.append("    TARGET_OBJECT_ID    STRING,")
    lines.append("    PARAMS              VARIANT,")
    lines.append("    STATUS              STRING,")
    lines.append("    RESULT_MSG          STRING,")
    lines.append("    INVOKED_BY          STRING,")
    lines.append("    INVOKED_AT          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),")
    lines.append("    COMPLETED_AT        TIMESTAMP_NTZ")
    lines.append(");\n")

    # ── ONT_FUNCTION ──
    lines.append("-- Function Catalog")
    lines.append("CREATE TABLE IF NOT EXISTS ONT_FUNCTION (")
    lines.append("    FUNCTION_NAME       STRING,")
    lines.append("    VERSION             STRING,")
    lines.append("    LANGUAGE            STRING,")
    lines.append("    SNOWFLAKE_REF       STRING,")
    lines.append("    DESCRIPTION         STRING,")
    lines.append("    INPUT_SCHEMA        VARIANT,")
    lines.append("    OUTPUT_SCHEMA       VARIANT,")
    lines.append("    ONTOLOGY_NAME       STRING,")
    lines.append("    PRIMARY KEY (ONTOLOGY_NAME, FUNCTION_NAME, VERSION)")
    lines.append(");\n")

    # ── ONT_FUNCTION_BINDING ──
    lines.append("-- Function Bindings")
    lines.append("CREATE TABLE IF NOT EXISTS ONT_FUNCTION_BINDING (")
    lines.append("    ONTOLOGY_NAME       STRING,")
    lines.append("    FUNCTION_NAME       STRING,")
    lines.append("    VERSION             STRING,")
    lines.append("    BOUND_TO_KIND       STRING,")
    lines.append("    BOUND_TO_NAME       STRING")
    lines.append(");\n")

    # ── OBJ_VIEW_DEF ──
    lines.append("-- Object View Definitions (UI/Governance)")
    lines.append("CREATE TABLE IF NOT EXISTS OBJ_VIEW_DEF (")
    lines.append("    OBJ_TYPE            STRING,")
    lines.append("    VIEW_NAME           STRING,")
    lines.append("    CREATED_BY          STRING,")
    lines.append("    DESCRIPTION         STRING,")
    lines.append("    DISPLAY_COLS        VARIANT,")
    lines.append("    VERSION             STRING DEFAULT '1.0',")
    lines.append("    STATUS              STRING DEFAULT 'ACTIVE',")
    lines.append("    TS_CREATED          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),")
    lines.append("    PRIMARY KEY (OBJ_TYPE, VIEW_NAME)")
    lines.append(");\n")

    # Auto-populate OBJ_VIEW_DEF from class mappings
    for cm in class_maps:
        cls_name = cm["class_name"]
        view_name = f"V_{cls_name.upper()}"
        display_cols = [
            c["name"].upper() for c in cm.get("columns", [])
            if not c.get("is_primary_key") and not c.get("is_foreign_key")
            and c["name"].upper() not in ("NODE_ID", "NODE_TYPE", "PROPS", "TS_INGESTED")
        ][:5]  # Limit to 5 display columns
        cols_array = ", ".join(f"'{c}'" for c in display_cols)
        lines.append(f"INSERT INTO OBJ_VIEW_DEF (OBJ_TYPE, VIEW_NAME, CREATED_BY, DESCRIPTION, DISPLAY_COLS)")
        lines.append(f"SELECT {sql_escape(cls_name)}, {sql_escape(view_name)}, 'SYSTEM', {sql_escape(f'Standard {cls_name} view')}, ARRAY_CONSTRUCT({cols_array});")
    lines.append("")

    # ── OBJ_VIEW_FIELD ──
    lines.append("-- Object View Fields")
    lines.append("CREATE TABLE IF NOT EXISTS OBJ_VIEW_FIELD (")
    lines.append("    OBJ_TYPE            STRING,")
    lines.append("    VIEW_NAME           STRING,")
    lines.append("    VERSION             STRING DEFAULT '1.0',")
    lines.append("    FIELD_ORDER         NUMBER,")
    lines.append("    PROP_NAME           STRING,")
    lines.append("    RENDER_HINT         STRING,")
    lines.append("    PRIMARY KEY (OBJ_TYPE, VIEW_NAME, VERSION, PROP_NAME)")
    lines.append(");\n")

    # ── ONT_ROLE ──
    lines.append("-- Roles and Permissions")
    lines.append("CREATE TABLE IF NOT EXISTS ONT_ROLE (")
    lines.append("    ONTOLOGY_NAME       STRING,")
    lines.append("    ONT_ROLE_NAME       STRING,")
    lines.append("    DESCRIPTION         STRING,")
    lines.append("    PRIMARY KEY (ONTOLOGY_NAME, ONT_ROLE_NAME)")
    lines.append(");\n")

    # Default roles
    lines.append(f"INSERT INTO ONT_ROLE (ONTOLOGY_NAME, ONT_ROLE_NAME, DESCRIPTION) VALUES")
    lines.append(f"    ({sql_escape(ontology_name)}, 'viewer', 'Read-only access to all entities'),")
    lines.append(f"    ({sql_escape(ontology_name)}, 'analyst', 'Read access plus analytics functions'),")
    lines.append(f"    ({sql_escape(ontology_name)}, 'editor', 'Read and write access to entities'),")
    lines.append(f"    ({sql_escape(ontology_name)}, 'admin', 'Full administrative access');\n")

    # ── ONT_ROLE_BINDING ──
    lines.append("CREATE TABLE IF NOT EXISTS ONT_ROLE_BINDING (")
    lines.append("    ONTOLOGY_NAME       STRING,")
    lines.append("    ONT_ROLE_NAME       STRING,")
    lines.append("    SNOWFLAKE_ROLE      STRING,")
    lines.append("    PRIMARY KEY (ONTOLOGY_NAME, ONT_ROLE_NAME, SNOWFLAKE_ROLE)")
    lines.append(");\n")

    # ── ONT_PERMISSION ──
    lines.append("CREATE TABLE IF NOT EXISTS ONT_PERMISSION (")
    lines.append("    ONTOLOGY_NAME       STRING,")
    lines.append("    SUBJECT_KIND        STRING,")
    lines.append("    SUBJECT_NAME        STRING,")
    lines.append("    ONT_ROLE_NAME       STRING,")
    lines.append("    PRIVILEGE           STRING,")
    lines.append("    PRIMARY KEY (ONTOLOGY_NAME, SUBJECT_KIND, SUBJECT_NAME, ONT_ROLE_NAME, PRIVILEGE)")
    lines.append(");\n")

    # Auto-populate viewer READ permissions for all classes
    perm_rows = []
    for cm in class_maps:
        cls_name = cm["class_name"]
        perm_rows.append(
            f"    ({sql_escape(ontology_name)}, 'OBJECT_TYPE', {sql_escape(cls_name)}, 'viewer', 'READ')"
        )
        perm_rows.append(
            f"    ({sql_escape(ontology_name)}, 'OBJECT_TYPE', {sql_escape(cls_name)}, 'admin', 'ADMIN')"
        )
    if perm_rows:
        lines.append("INSERT INTO ONT_PERMISSION (ONTOLOGY_NAME, SUBJECT_KIND, SUBJECT_NAME, ONT_ROLE_NAME, PRIVILEGE) VALUES")
        lines.append(",\n".join(perm_rows) + ";\n")

    return "\n".join(lines)


def generate_views_sql(
    classes: list[dict],
    mappings: dict,
    database: str,
    schema: str,
    ontology_name: str,
    kg_path: bool,
) -> str:
    """Generate abstract ontology views SQL."""
    fqn = f"{database}.{schema}"
    lines = []
    lines.append(f"-- {'='*76}")
    lines.append(f"-- Layer 3: Abstract Ontology Views for {ontology_name}")
    lines.append(f"-- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"-- {'='*76}\n")
    lines.append(f"USE SCHEMA {fqn};\n")

    class_maps = mappings.get("class_mappings", [])

    # Per-class entity views
    view_names = []
    for cm in class_maps:
        cls_name = cm["class_name"]
        view_name = f"VW_ONT_{cls_name.upper()}"
        view_names.append(view_name)

        if kg_path:
            lines.append(f"-- {view_name}: Entity view for {cls_name} (KG path)")
            lines.append(f"CREATE OR REPLACE VIEW {view_name} AS")
            lines.append(f"SELECT")
            lines.append(f"    NODE_ID AS ENTITY_ID,")
            lines.append(f"    NODE_TYPE AS ENTITY_TYPE,")
            lines.append(f"    NAME AS ENTITY_NAME,")
            lines.append(f"    PROPS")
            lines.append(f"FROM KG_NODE")
            lines.append(f"WHERE NODE_TYPE = '{cls_name}';")
        else:
            src = cm["source_table"]
            parts = src.split(".")
            src_tbl = parts[-1] if "." in src else src
            id_col = cm["id_column"]
            name_col = cm.get("name_column") or id_col
            filt = cm.get("filter_condition")

            lines.append(f"-- {view_name}: Entity view for {cls_name} (direct table path)")
            lines.append(f"CREATE OR REPLACE VIEW {view_name} AS")
            lines.append(f"SELECT")
            lines.append(f"    {id_col}::STRING AS ENTITY_ID,")
            lines.append(f"    '{cls_name}' AS ENTITY_TYPE,")
            lines.append(f"    {name_col}::STRING AS ENTITY_NAME,")
            lines.append(f"    OBJECT_CONSTRUCT(*) AS PROPS")
            lines.append(f"FROM {src}")
            if filt:
                lines.append(f"WHERE {filt}")
            lines.append(f";")
        lines.append("")

    # Unified entity view
    if len(view_names) > 1:
        lines.append("-- VW_ONT_ALL_ENTITIES: Unified view of all entity types")
        lines.append("CREATE OR REPLACE VIEW VW_ONT_ALL_ENTITIES AS")
        for i, vn in enumerate(view_names):
            if i > 0:
                lines.append("UNION ALL")
            lines.append(f"SELECT ENTITY_ID, ENTITY_TYPE, ENTITY_NAME, PROPS FROM {vn}")
        lines.append(";\n")

    # Resolved relationships view
    if kg_path:
        lines.append("-- REL_RESOLVED: Resolved relationships with node names")
        lines.append("CREATE OR REPLACE VIEW REL_RESOLVED AS")
        lines.append("SELECT")
        lines.append("    e.EDGE_TYPE AS REL_NAME,")
        lines.append("    e.SRC_ID,")
        lines.append("    src.NAME AS SRC_NAME,")
        lines.append("    src.NODE_TYPE AS SRC_TYPE,")
        lines.append("    e.DST_ID,")
        lines.append("    dst.NAME AS DST_NAME,")
        lines.append("    dst.NODE_TYPE AS DST_TYPE,")
        lines.append("    e.WEIGHT,")
        lines.append("    e.EFFECTIVE_START,")
        lines.append("    e.EFFECTIVE_END")
        lines.append("FROM KG_EDGE e")
        lines.append("LEFT JOIN KG_NODE src ON e.SRC_ID = src.NODE_ID")
        lines.append("LEFT JOIN KG_NODE dst ON e.DST_ID = dst.NODE_ID;\n")
    else:
        # For direct-table path, create REL_RESOLVED from relation mappings
        rel_maps = mappings.get("relation_mappings", [])
        if rel_maps:
            lines.append("-- REL_RESOLVED: Resolved relationships (direct table path)")
            lines.append("CREATE OR REPLACE VIEW REL_RESOLVED AS")
            for i, rm in enumerate(rel_maps):
                if i > 0:
                    lines.append("UNION ALL")
                src_tbl = rm["source_table"]
                lines.append(f"SELECT")
                lines.append(f"    '{rm['rel_name']}' AS REL_NAME,")
                lines.append(f"    {rm['src_column']}::STRING AS SRC_ID,")
                lines.append(f"    NULL AS SRC_NAME,")
                lines.append(f"    NULL AS SRC_TYPE,")
                lines.append(f"    {rm['dst_column']}::STRING AS DST_ID,")
                lines.append(f"    NULL AS DST_NAME,")
                lines.append(f"    NULL AS DST_TYPE,")
                lines.append(f"    1.0 AS WEIGHT,")
                lines.append(f"    NULL AS EFFECTIVE_START,")
                lines.append(f"    NULL AS EFFECTIVE_END")
                lines.append(f"FROM {src_tbl}")
            lines.append(";\n")

    return "\n".join(lines)


def generate_view_generator_sp(database: str, schema: str, ontology_name: str) -> str:
    """Generate the SP_GENERATE_ONTOLOGY_VIEWS stored procedure."""
    fqn = f"{database}.{schema}"
    return f"""-- ============================================================================
-- View Generator Stored Procedure
-- Reads ONT_CLASS_MAP and regenerates VW_ONT_* views dynamically
-- ============================================================================

USE SCHEMA {fqn};

CREATE OR REPLACE PROCEDURE SP_GENERATE_ONTOLOGY_VIEWS()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'generate_views'
AS
$$
def generate_views(session):
    import json

    # Read class mappings
    maps_df = session.sql("SELECT * FROM ONT_CLASS_MAP").collect()
    
    views_created = []
    union_parts = []
    
    for row in maps_df:
        cls_name = row['CLASS_NAME']
        src_db = row['SOURCE_DATABASE']
        src_schema = row['SOURCE_SCHEMA']
        src_table = row['SOURCE_TABLE']
        filter_col = row.get('FILTER_COL')
        filter_val = row.get('FILTER_VAL')
        id_expr = row['ID_EXPR']
        name_expr = row.get('NAME_EXPR') or id_expr
        
        view_name = f"VW_ONT_{{cls_name.upper()}}"
        fqn_src = f"{{src_db}}.{{src_schema}}.{{src_table}}"
        
        where_clause = ""
        if filter_col and filter_val:
            where_clause = f"WHERE {{filter_col}} = '{{filter_val}}'"
        
        view_sql = f\"\"\"
CREATE OR REPLACE VIEW {{view_name}} AS
SELECT
    {{id_expr}}::STRING AS ENTITY_ID,
    '{{cls_name}}' AS ENTITY_TYPE,
    {{name_expr}}::STRING AS ENTITY_NAME,
    OBJECT_CONSTRUCT(*) AS PROPS
FROM {{fqn_src}}
{{where_clause}}
\"\"\"
        session.sql(view_sql.strip()).collect()
        views_created.append(view_name)
        
        union_parts.append(
            f"SELECT ENTITY_ID, ENTITY_TYPE, ENTITY_NAME, PROPS FROM {{view_name}}"
        )
    
    # Create unified view
    if len(union_parts) > 1:
        union_sql = "CREATE OR REPLACE VIEW VW_ONT_ALL_ENTITIES AS\\n" + "\\nUNION ALL\\n".join(union_parts)
        session.sql(union_sql).collect()
        views_created.append("VW_ONT_ALL_ENTITIES")
    
    return json.dumps({{"views_created": views_created, "count": len(views_created)}})
$$;

-- Run it once to generate initial views
CALL SP_GENERATE_ONTOLOGY_VIEWS();
"""


def generate_inference_sql(database: str, schema: str) -> str:
    """Generate optional inference engine stored procedures (KG path only).

    Produces 06_inference_engine.sql with:
    - SP_INFER_TRANSITIVE: recursive transitive closure
    - SP_INFER_INVERSE: inverse relationship materialisation
    - SP_RUN_ONTOLOGY_INFERENCE: master runner for all enabled rules
    - SP_CHECK_CARDINALITY_SINGLE: cardinality constraint checker
    - SP_CHECK_REFERENTIAL: referential integrity checker
    """
    return f"""-- =============================================================
-- INFERENCE ENGINE & CONSTRAINT PROCEDURES
-- Optional stored procedures for ontology inference and data quality
-- Generated by ontology-stack-builder
-- =============================================================

USE DATABASE {database};
USE SCHEMA {schema};

-- =============================================================
-- SP_INFER_TRANSITIVE
-- Computes transitive closure for a relationship type.
-- If A->B and B->C via the same rel, infers A->C.
-- =============================================================
CREATE OR REPLACE PROCEDURE SP_INFER_TRANSITIVE(TARGET_REL STRING, RULE_ID STRING)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'infer_transitive'
AS
$$
def infer_transitive(session, target_rel, rule_id):
    session.sql(f\"\"\"
        DELETE FROM REL_EDGE_INFERRED WHERE RULE_ID = '{{rule_id}}'
    \"\"\").collect()

    infer_sql = f\"\"\"
        INSERT INTO REL_EDGE_INFERRED (REL_NAME, SRC_ID, DST_ID, INFERENCE_KIND, RULE_ID, WEIGHT)
        WITH RECURSIVE transitive(src, dst, depth) AS (
            SELECT SRC_ID, DST_ID, 1
            FROM KG_EDGE
            WHERE EDGE_TYPE = '{{target_rel}}'
            UNION ALL
            SELECT t.src, e.DST_ID, t.depth + 1
            FROM transitive t
            JOIN KG_EDGE e ON t.dst = e.SRC_ID AND e.EDGE_TYPE = '{{target_rel}}'
            WHERE t.depth < 5 AND t.src != e.DST_ID
        )
        SELECT DISTINCT
            '{{target_rel}}', src, dst, 'TRANSITIVE', '{{rule_id}}', 1.0 / depth
        FROM transitive
        WHERE (src, dst) NOT IN (
            SELECT SRC_ID, DST_ID FROM KG_EDGE WHERE EDGE_TYPE = '{{target_rel}}'
        )
    \"\"\"
    session.sql(infer_sql).collect()
    count = session.sql(f\"\"\"
        SELECT COUNT(*) AS cnt FROM REL_EDGE_INFERRED WHERE RULE_ID = '{{rule_id}}'
    \"\"\").collect()[0]['CNT']
    return f"Inferred {{count}} transitive edges for {{target_rel}}"
$$;

-- =============================================================
-- SP_INFER_INVERSE
-- Creates inverse relationships based on ONT_RELATION_DEF.INVERSE_REL_NAME
-- =============================================================
CREATE OR REPLACE PROCEDURE SP_INFER_INVERSE(RULE_ID STRING)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'infer_inverse'
AS
$$
def infer_inverse(session, rule_id):
    session.sql(f\"\"\"
        DELETE FROM REL_EDGE_INFERRED WHERE RULE_ID = '{{rule_id}}'
    \"\"\").collect()

    rels = session.sql(\"\"\"
        SELECT REL_NAME, INVERSE_REL_NAME
        FROM ONT_RELATION_DEF
        WHERE INVERSE_REL_NAME IS NOT NULL AND STATUS = 'ACTIVE'
    \"\"\").collect()

    total = 0
    for rel in rels:
        rel_name = rel['REL_NAME']
        inverse_name = rel['INVERSE_REL_NAME']
        session.sql(f\"\"\"
            INSERT INTO REL_EDGE_INFERRED
                (REL_NAME, SRC_ID, DST_ID, INFERENCE_KIND, RULE_ID, WEIGHT, EFFECTIVE_START, EFFECTIVE_END)
            SELECT
                '{{inverse_name}}', DST_ID, SRC_ID, 'INVERSE', '{{rule_id}}',
                WEIGHT, EFFECTIVE_START, EFFECTIVE_END
            FROM KG_EDGE
            WHERE EDGE_TYPE = '{{rel_name}}'
        \"\"\").collect()
        cnt = session.sql(f\"\"\"
            SELECT COUNT(*) AS cnt FROM REL_EDGE_INFERRED
            WHERE RULE_ID = '{{rule_id}}' AND REL_NAME = '{{inverse_name}}'
        \"\"\").collect()[0]['CNT']
        total += cnt

    return f"Inferred {{total}} inverse edges"
$$;

-- =============================================================
-- SP_RUN_ONTOLOGY_INFERENCE
-- Master procedure — runs all enabled inference rules in order
-- =============================================================
CREATE OR REPLACE PROCEDURE SP_RUN_ONTOLOGY_INFERENCE()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'run_inference'
AS
$$
def run_inference(session):
    rules = session.sql(\"\"\"
        SELECT RULE_ID, RULE_KIND, TARGET_REL
        FROM ONT_RULE
        WHERE IS_ENABLED = TRUE
        ORDER BY CASE RULE_KIND
            WHEN 'INVERSE' THEN 1
            WHEN 'TRANSITIVE' THEN 2
            WHEN 'PROPERTY_CHAIN' THEN 3
        END
    \"\"\").collect()

    results = []
    for rule in rules:
        rule_id = rule['RULE_ID']
        kind = rule['RULE_KIND']
        try:
            if kind == 'INVERSE':
                r = session.call('SP_INFER_INVERSE', rule_id)
            elif kind == 'TRANSITIVE':
                r = session.call('SP_INFER_TRANSITIVE', rule['TARGET_REL'], rule_id)
            else:
                r = f"Unsupported rule kind: {{kind}}"
            results.append(f"{{rule_id}}: {{r}}")
        except Exception as e:
            results.append(f"{{rule_id}}: ERROR - {{str(e)}}")
    return "\\n".join(results)
$$;

-- =============================================================
-- SP_CHECK_CARDINALITY_SINGLE
-- Checks that a 1:1 or N:1 relationship has at most one edge per source
-- =============================================================
CREATE OR REPLACE PROCEDURE SP_CHECK_CARDINALITY_SINGLE(REL STRING, CHECK_NAME STRING)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'check_cardinality'
AS
$$
def check_cardinality(session, rel, check_name):
    session.sql(f\"\"\"
        INSERT INTO ONT_CONSTRAINT_VIOLATION (CHECK_NAME, SCOPE, REL_OR_CLASS, SRC_ID, DETAILS)
        SELECT '{{check_name}}', 'RELATION', '{{rel}}', SRC_ID,
               'Multiple edges from same source: ' || COUNT(*) || ' edges'
        FROM KG_EDGE
        WHERE EDGE_TYPE = '{{rel}}'
          AND (EFFECTIVE_END IS NULL OR EFFECTIVE_END >= CURRENT_DATE())
        GROUP BY SRC_ID HAVING COUNT(*) > 1
    \"\"\").collect()
    cnt = session.sql(f\"\"\"
        SELECT COUNT(*) AS cnt FROM ONT_CONSTRAINT_VIOLATION WHERE CHECK_NAME = '{{check_name}}'
    \"\"\").collect()[0]['CNT']
    return f"Found {{cnt}} cardinality violations for {{rel}}"
$$;

-- =============================================================
-- SP_CHECK_REFERENTIAL
-- Checks that all edge endpoints reference existing nodes
-- =============================================================
CREATE OR REPLACE PROCEDURE SP_CHECK_REFERENTIAL(REL STRING, CHECK_NAME STRING)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'check_referential'
AS
$$
def check_referential(session, rel, check_name):
    session.sql(f\"\"\"
        INSERT INTO ONT_CONSTRAINT_VIOLATION (CHECK_NAME, SCOPE, REL_OR_CLASS, SRC_ID, DETAILS)
        SELECT '{{check_name}}', 'RELATION', '{{rel}}', e.SRC_ID, 'Source node not found'
        FROM KG_EDGE e LEFT JOIN KG_NODE n ON e.SRC_ID = n.NODE_ID
        WHERE e.EDGE_TYPE = '{{rel}}' AND n.NODE_ID IS NULL
    \"\"\").collect()
    session.sql(f\"\"\"
        INSERT INTO ONT_CONSTRAINT_VIOLATION (CHECK_NAME, SCOPE, REL_OR_CLASS, DST_ID, DETAILS)
        SELECT '{{check_name}}', 'RELATION', '{{rel}}', e.DST_ID, 'Destination node not found'
        FROM KG_EDGE e LEFT JOIN KG_NODE n ON e.DST_ID = n.NODE_ID
        WHERE e.EDGE_TYPE = '{{rel}}' AND n.NODE_ID IS NULL
    \"\"\").collect()
    cnt = session.sql(f\"\"\"
        SELECT COUNT(*) AS cnt FROM ONT_CONSTRAINT_VIOLATION WHERE CHECK_NAME = '{{check_name}}'
    \"\"\").collect()[0]['CNT']
    return f"Found {{cnt}} referential integrity violations for {{rel}}"
$$;

COMMENT ON PROCEDURE SP_INFER_TRANSITIVE(STRING, STRING) IS 'Computes transitive closure for a relationship type';
COMMENT ON PROCEDURE SP_INFER_INVERSE(STRING) IS 'Creates inverse relationships based on ontology definitions';
COMMENT ON PROCEDURE SP_RUN_ONTOLOGY_INFERENCE() IS 'Master procedure to run all enabled inference rules';
COMMENT ON PROCEDURE SP_CHECK_CARDINALITY_SINGLE(STRING, STRING) IS 'Checks cardinality constraints for relationships';
COMMENT ON PROCEDURE SP_CHECK_REFERENTIAL(STRING, STRING) IS 'Checks referential integrity for edges';
"""


def generate_graph_traversal_sql(database: str, schema: str) -> str:
    """Generate generic SQL UDF graph traversal tools for KG_NODE/KG_EDGE.

    Produces 07_graph_traversal_tools.sql with 4 parameterised UDFs:
    - EXPAND_DESCENDANTS_TOOL: recursive downward traversal
    - GET_ANCESTORS_TOOL: recursive upward traversal
    - GET_HIERARCHY_PATH_TOOL: path between two concepts
    - GET_DIRECT_CHILDREN_TOOL: single-hop children

    These operate purely on KG_NODE/KG_EDGE and work for any ontology.
    """
    fqn = f"{database}.{schema}"
    return f"""-- ============================================================================
-- 07_graph_traversal_tools.sql
-- Generic SQL UDF Graph Traversal Tools for Cortex Agent
-- Generated by ontology-stack-builder
-- ============================================================================
-- These UDFs operate on KG_NODE / KG_EDGE and work with any ontology.
-- They are registered as "generic" tools in the Cortex Agent, enabling
-- hierarchy traversal queries that semantic views cannot handle well
-- (e.g., recursive descendant expansion with a runtime root concept).
-- ============================================================================

USE SCHEMA {fqn};

-- ============================================================================
-- EXPAND_DESCENDANTS_TOOL: Get all descendants of a root concept
-- ============================================================================
-- Given a concept name, recursively walks subClassOf edges downward to
-- return all descendant concepts with depth and path.
--
-- Example: SELECT * FROM TABLE(EXPAND_DESCENDANTS_TOOL('Vehicle'));
-- ============================================================================

CREATE OR REPLACE FUNCTION EXPAND_DESCENDANTS_TOOL(ROOT_CONCEPT VARCHAR)
RETURNS TABLE (
    NODE_ID VARCHAR,
    NODE_NAME VARCHAR,
    DEPTH NUMBER,
    PATH VARCHAR
)
LANGUAGE SQL
AS
$$
WITH RECURSIVE
root_node AS (
    SELECT NODE_ID, NAME
    FROM {fqn}.KG_NODE
    WHERE LOWER(NAME) = LOWER(ROOT_CONCEPT)
    LIMIT 1
),
descendants AS (
    SELECT
        e.SRC_ID AS NODE_ID,
        n.NAME AS NODE_NAME,
        1 AS DEPTH,
        r.NAME || ' -> ' || n.NAME AS PATH
    FROM {fqn}.KG_EDGE e
    JOIN root_node r ON e.DST_ID = r.NODE_ID
    JOIN {fqn}.KG_NODE n ON e.SRC_ID = n.NODE_ID
    WHERE e.EDGE_TYPE = 'subClassOf'

    UNION ALL

    SELECT
        e.SRC_ID AS NODE_ID,
        n.NAME AS NODE_NAME,
        d.DEPTH + 1 AS DEPTH,
        d.PATH || ' -> ' || n.NAME AS PATH
    FROM descendants d
    JOIN {fqn}.KG_EDGE e ON d.NODE_ID = e.DST_ID
    JOIN {fqn}.KG_NODE n ON e.SRC_ID = n.NODE_ID
    WHERE e.EDGE_TYPE = 'subClassOf'
    AND d.DEPTH < 15
)
SELECT NODE_ID, NAME AS NODE_NAME, 0 AS DEPTH, NAME AS PATH
FROM root_node
UNION ALL
SELECT NODE_ID, NODE_NAME, DEPTH, PATH
FROM descendants
ORDER BY DEPTH, NODE_NAME
$$;

COMMENT ON FUNCTION EXPAND_DESCENDANTS_TOOL(VARCHAR) IS
'Returns all descendants of a root concept in the ontology hierarchy.
Use for cohort expansion queries like "all subtypes of X".
Returns NODE_ID, NODE_NAME, DEPTH (distance from root), and PATH.';


-- ============================================================================
-- GET_ANCESTORS_TOOL: Get all ancestors of a concept
-- ============================================================================
-- Given a concept name, recursively walks subClassOf edges upward to
-- return all ancestor concepts with depth (shortest path).
--
-- Example: SELECT * FROM TABLE(GET_ANCESTORS_TOOL('Sedan'));
-- ============================================================================

CREATE OR REPLACE FUNCTION GET_ANCESTORS_TOOL(CONCEPT VARCHAR)
RETURNS TABLE (
    ANCESTOR_ID VARCHAR,
    ANCESTOR_NAME VARCHAR,
    DEPTH NUMBER
)
LANGUAGE SQL
AS
$$
WITH RECURSIVE
start_node AS (
    SELECT NODE_ID, NAME
    FROM {fqn}.KG_NODE
    WHERE LOWER(NAME) = LOWER(CONCEPT)
    LIMIT 1
),
ancestors AS (
    SELECT
        e.DST_ID AS ANCESTOR_ID,
        n.NAME AS ANCESTOR_NAME,
        1 AS DEPTH
    FROM start_node s
    JOIN {fqn}.KG_EDGE e ON s.NODE_ID = e.SRC_ID
    JOIN {fqn}.KG_NODE n ON e.DST_ID = n.NODE_ID
    WHERE e.EDGE_TYPE = 'subClassOf'

    UNION ALL

    SELECT
        e.DST_ID AS ANCESTOR_ID,
        n.NAME AS ANCESTOR_NAME,
        a.DEPTH + 1 AS DEPTH
    FROM ancestors a
    JOIN {fqn}.KG_EDGE e ON a.ANCESTOR_ID = e.SRC_ID
    JOIN {fqn}.KG_NODE n ON e.DST_ID = n.NODE_ID
    WHERE e.EDGE_TYPE = 'subClassOf'
    AND a.DEPTH < 20
)
SELECT DISTINCT ANCESTOR_ID, ANCESTOR_NAME, MIN(DEPTH) AS DEPTH
FROM ancestors
GROUP BY ANCESTOR_ID, ANCESTOR_NAME
ORDER BY DEPTH, ANCESTOR_NAME
$$;

COMMENT ON FUNCTION GET_ANCESTORS_TOOL(VARCHAR) IS
'Returns all ancestors of a concept. Use for understanding where a concept sits in the hierarchy.';


-- ============================================================================
-- GET_HIERARCHY_PATH_TOOL: Find path between two concepts
-- ============================================================================
-- Given a start and end concept, finds the shortest path through the
-- subClassOf hierarchy (traversing upward from start toward end).
--
-- Example: SELECT * FROM TABLE(GET_HIERARCHY_PATH_TOOL('Sedan', 'Vehicle'));
-- ============================================================================

CREATE OR REPLACE FUNCTION GET_HIERARCHY_PATH_TOOL(START_CONCEPT VARCHAR, END_CONCEPT VARCHAR)
RETURNS TABLE (
    STEP NUMBER,
    NODE_ID VARCHAR,
    NODE_NAME VARCHAR,
    RELATIONSHIP VARCHAR
)
LANGUAGE SQL
AS
$$
WITH RECURSIVE
start_node AS (
    SELECT NODE_ID, NAME
    FROM {fqn}.KG_NODE
    WHERE LOWER(NAME) = LOWER(START_CONCEPT)
    LIMIT 1
),
end_node AS (
    SELECT NODE_ID, NAME
    FROM {fqn}.KG_NODE
    WHERE LOWER(NAME) = LOWER(END_CONCEPT)
    LIMIT 1
),
path_up AS (
    SELECT
        s.NODE_ID,
        s.NAME AS NODE_NAME,
        0 AS STEP,
        ARRAY_CONSTRUCT(s.NODE_ID) AS VISITED
    FROM start_node s

    UNION ALL

    SELECT
        e.DST_ID AS NODE_ID,
        n.NAME AS NODE_NAME,
        p.STEP + 1 AS STEP,
        ARRAY_APPEND(p.VISITED, e.DST_ID) AS VISITED
    FROM path_up p
    JOIN {fqn}.KG_EDGE e ON p.NODE_ID = e.SRC_ID
    JOIN {fqn}.KG_NODE n ON e.DST_ID = n.NODE_ID
    CROSS JOIN end_node en
    WHERE e.EDGE_TYPE = 'subClassOf'
    AND NOT ARRAY_CONTAINS(e.DST_ID::VARIANT, p.VISITED)
    AND p.STEP < 20
)
SELECT
    STEP,
    NODE_ID,
    NODE_NAME,
    CASE WHEN STEP = 0 THEN 'START' ELSE 'subClassOf' END AS RELATIONSHIP
FROM path_up
WHERE NODE_ID = (SELECT NODE_ID FROM end_node)
   OR STEP <= (
       SELECT MIN(STEP) FROM path_up WHERE NODE_ID = (SELECT NODE_ID FROM end_node)
   )
QUALIFY ROW_NUMBER() OVER (PARTITION BY NODE_ID ORDER BY STEP) = 1
ORDER BY STEP
$$;

COMMENT ON FUNCTION GET_HIERARCHY_PATH_TOOL(VARCHAR, VARCHAR) IS
'Returns the path between two concepts in the ontology hierarchy.
Use for lineage queries like "path from X to Y".
Returns STEP number, NODE_ID, NODE_NAME, and RELATIONSHIP type.';


-- ============================================================================
-- GET_DIRECT_CHILDREN_TOOL: Get immediate children of a concept
-- ============================================================================
-- Simple non-recursive single-hop query for direct children.
--
-- Example: SELECT * FROM TABLE(GET_DIRECT_CHILDREN_TOOL('Vehicle'));
-- ============================================================================

CREATE OR REPLACE FUNCTION GET_DIRECT_CHILDREN_TOOL(PARENT_CONCEPT VARCHAR)
RETURNS TABLE (
    CHILD_ID VARCHAR,
    CHILD_NAME VARCHAR,
    CHILD_TYPE VARCHAR
)
LANGUAGE SQL
AS
$$
SELECT
    n.NODE_ID AS CHILD_ID,
    n.NAME AS CHILD_NAME,
    n.NODE_TYPE AS CHILD_TYPE
FROM {fqn}.KG_NODE parent
JOIN {fqn}.KG_EDGE e ON parent.NODE_ID = e.DST_ID
JOIN {fqn}.KG_NODE n ON e.SRC_ID = n.NODE_ID
WHERE LOWER(parent.NAME) = LOWER(PARENT_CONCEPT)
AND e.EDGE_TYPE = 'subClassOf'
ORDER BY n.NAME
$$;

COMMENT ON FUNCTION GET_DIRECT_CHILDREN_TOOL(VARCHAR) IS
'Returns direct children (depth=1) of a concept. Use for incremental hierarchy exploration.';


-- ============================================================================
-- Verification queries (uncomment to test after deployment):
-- ============================================================================
-- SELECT * FROM TABLE(EXPAND_DESCENDANTS_TOOL('YourRootConcept')) LIMIT 20;
-- SELECT * FROM TABLE(GET_DIRECT_CHILDREN_TOOL('YourRootConcept'));
-- SELECT * FROM TABLE(GET_ANCESTORS_TOOL('YourLeafConcept'));
-- SELECT * FROM TABLE(GET_HIERARCHY_PATH_TOOL('YourLeafConcept', 'YourRootConcept'));
"""


def main():
    parser = argparse.ArgumentParser(description="Generate Ontology SQL (Layers 1-3)")
    parser.add_argument("--classes-json", required=True)
    parser.add_argument("--relations-json", required=True)
    parser.add_argument("--mappings-json", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--ontology-name", required=True)
    parser.add_argument("--kg-path", default="false", help="'true' for KG path, 'false' for direct table path")
    parser.add_argument("--include-inference", default="false", help="'true' to generate inference engine SPs (KG path only)")
    parser.add_argument("--include-graph-tools", default="false", help="'true' to generate SQL UDF graph traversal tools (KG path only)")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    kg_path = args.kg_path.lower() in ("true", "1", "yes")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(args.classes_json) as f:
        classes = json.load(f)
    with open(args.relations_json) as f:
        relations = json.load(f)
    with open(args.mappings_json) as f:
        mappings = json.load(f)

    database = args.database
    schema = args.schema
    ontology_name = args.ontology_name

    print(f"Generating SQL for {ontology_name} ({'KG' if kg_path else 'direct'} path)")
    print(f"  {len(classes)} classes, {len(relations)} relations")

    files_written = []

    # 01: Physical layer (KG path only)
    if kg_path:
        sql = generate_physical_layer_sql(classes, relations, mappings, database, schema)
        path = output_dir / "01_physical_layer.sql"
        path.write_text(sql)
        files_written.append(str(path))
        print(f"  Wrote {path}")

    # 02: Concrete entity & relationship views (V_{CLASS}, V_{REL})
    sql = generate_concrete_views_sql(mappings, database, schema, kg_path)
    path = output_dir / "02_concrete_views.sql"
    path.write_text(sql)
    files_written.append(str(path))
    print(f"  Wrote {path}")

    # 03: Metadata tables
    sql = generate_metadata_sql(classes, relations, mappings, database, schema, ontology_name, kg_path)
    path = output_dir / "03_metadata_tables.sql"
    path.write_text(sql)
    files_written.append(str(path))
    print(f"  Wrote {path}")

    # 04: Abstract views
    sql = generate_views_sql(classes, mappings, database, schema, ontology_name, kg_path)
    path = output_dir / "04_abstract_views.sql"
    path.write_text(sql)
    files_written.append(str(path))
    print(f"  Wrote {path}")

    # 05: View generator SP
    sql = generate_view_generator_sp(database, schema, ontology_name)
    path = output_dir / "05_view_generator_sp.sql"
    path.write_text(sql)
    files_written.append(str(path))
    print(f"  Wrote {path}")

    # 06: Inference engine (optional, KG path only)
    include_inference = args.include_inference.lower() in ("true", "1", "yes")
    if kg_path and include_inference:
        sql = generate_inference_sql(database, schema)
        path = output_dir / "06_inference_engine.sql"
        path.write_text(sql)
        files_written.append(str(path))
        print(f"  Wrote {path}")

    # 07: Graph traversal tools (optional, KG path only)
    include_graph_tools = args.include_graph_tools.lower() in ("true", "1", "yes")
    if kg_path and include_graph_tools:
        sql = generate_graph_traversal_sql(database, schema)
        path = output_dir / "07_graph_traversal_tools.sql"
        path.write_text(sql)
        files_written.append(str(path))
        print(f"  Wrote {path}")

    print(f"\nGenerated {len(files_written)} SQL files in {output_dir}")


if __name__ == "__main__":
    main()
