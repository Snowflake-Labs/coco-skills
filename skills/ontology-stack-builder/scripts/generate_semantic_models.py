# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyyaml>=6.0",
# ]
# ///
"""
DEPRECATED: This script is no longer used by the ontology-stack-builder workflow.

Semantic view generation is now handled by the native Cortex Code `semantic-view` skill,
which uses Snowflake's FastGen system function (SYSTEM$CORTEX_ANALYST_FAST_GENERATION)
to auto-discover columns, infer primary keys, generate dimensions/measures/metrics/
relationships, and create verified queries directly from Snowflake metadata.

This file is retained for reference only. Do not invoke it from the skill workflow.
See SKILL.md Phase 5 for the current workflow.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


# Mapping from Snowflake types to Cortex Analyst semantic model data types
_ANALYST_TYPE_MAP = {
    "NUMBER": "NUMBER", "DECIMAL": "NUMBER", "NUMERIC": "NUMBER",
    "INT": "NUMBER", "INTEGER": "NUMBER", "BIGINT": "NUMBER",
    "SMALLINT": "NUMBER", "TINYINT": "NUMBER", "BYTEINT": "NUMBER",
    "FLOAT": "NUMBER", "FLOAT4": "NUMBER", "FLOAT8": "NUMBER",
    "DOUBLE": "NUMBER", "DOUBLE PRECISION": "NUMBER", "REAL": "NUMBER",
    "VARCHAR": "STRING", "CHAR": "STRING", "CHARACTER": "STRING",
    "STRING": "STRING", "TEXT": "STRING",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE", "DATETIME": "TIMESTAMP", "TIME": "TIME",
    "TIMESTAMP": "TIMESTAMP", "TIMESTAMP_LTZ": "TIMESTAMP",
    "TIMESTAMP_NTZ": "TIMESTAMP", "TIMESTAMP_TZ": "TIMESTAMP",
    "VARIANT": "VARIANT", "OBJECT": "VARIANT", "ARRAY": "VARIANT",
}


def _analyst_type(col_type: str) -> str:
    """Map a Snowflake column type to a Cortex Analyst data type."""
    base = col_type.upper().split("(")[0].strip()
    return _ANALYST_TYPE_MAP.get(base, "STRING")


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def generate_kg_model(
    classes: list[dict],
    relations: list[dict],
    mappings: dict,
    database: str,
    schema: str,
    ontology_name: str,
    questions: list[str],
) -> dict:
    """Generate the KG Semantic Model — concrete V_{CLASS} views for fast direct queries."""
    model = {
        "name": f"{ontology_name}_KG_MODEL",
        "description": (
            f"Knowledge Graph model for {ontology_name}. Contains concrete entity views "
            f"(V_* per type) and relationship views. Use for SPECIFIC entity lookups, "
            f"aggregations, named entity queries, and direct data access. "
            f"Best for: 'Who scored most goals?', 'Which customer spent most?', entity-specific questions."
        ),
        "tables": [],
        "relationships": [],
        "verified_queries": [],
    }

    class_maps = mappings.get("class_mappings", [])
    rel_maps = mappings.get("relation_mappings", [])

    # Build class name -> class_map lookup for relationship resolution
    cls_by_name = {cm["class_name"]: cm for cm in class_maps}

    # ── Per-class concrete entity views (V_{CLASS}) ──
    for cm in class_maps:
        cls_name = cm["class_name"]
        view_name = f"V_{cls_name.upper()}"
        columns = cm.get("columns", [])
        id_col = cm["id_column"]
        name_col = cm.get("name_column")

        table_def = {
            "name": view_name,
            "base_table": {"database": database, "schema": schema, "table": view_name},
            "description": f"Concrete view for {cls_name} entities with typed properties",
            "primary_key": {"columns": ["NODE_ID"] if id_col.upper() == "NODE_ID" else [id_col]},
            "dimensions": [],
        }

        # Add NODE_ID / primary key dimension
        pk_name = "NODE_ID" if id_col.upper() in ("NODE_ID", id_col.upper()) else id_col
        table_def["dimensions"].append({
            "name": pk_name, "expr": pk_name, "data_type": "STRING",
            "description": f"{cls_name} identifier",
        })

        # Add NAME dimension
        if name_col:
            table_def["dimensions"].append({
                "name": "NAME", "expr": "NAME", "data_type": "STRING",
                "description": f"Name of the {cls_name.lower()}",
            })

        # Add typed property dimensions from column metadata
        facts_list = []
        for col in columns:
            if col.get("is_primary_key") or col.get("is_foreign_key"):
                continue
            cn = col["name"].upper()
            if cn in ("NODE_ID", "NODE_TYPE", "NAME", "PROPS", "TS_INGESTED"):
                continue
            at = _analyst_type(col["data_type"])
            dim = {
                "name": col["name"].upper(),
                "expr": col["name"].upper(),
                "data_type": at,
                "description": f"{col['name']} of {cls_name}",
            }
            if at == "NUMBER":
                facts_list.append(dim)
            else:
                table_def["dimensions"].append(dim)

        # Add PROPS variant dimension
        table_def["dimensions"].append({
            "name": "PROPS", "expr": "PROPS", "data_type": "VARIANT",
            "description": f"Additional {cls_name.lower()} properties",
        })

        if facts_list:
            table_def["facts"] = facts_list

        model["tables"].append(table_def)

    # ── Per-relation concrete relationship views (V_{REL}) — KG path ──
    for rm in rel_maps:
        rel_name = rm["rel_name"]
        view_name = f"V_{rel_name.upper()}"
        extra_cols = rm.get("columns", [])
        domain = rm.get("domain_class", "")
        range_cls = rm.get("range_class", "")

        table_def = {
            "name": view_name,
            "base_table": {"database": database, "schema": schema, "table": view_name},
            "description": f"Relationship view for {rel_name} ({domain} → {range_cls})",
            "dimensions": [
                {"name": "SRC_ID", "expr": "SRC_ID", "data_type": "STRING",
                 "description": f"Source entity ID ({domain})"},
                {"name": "DST_ID", "expr": "DST_ID", "data_type": "STRING",
                 "description": f"Target entity ID ({range_cls})"},
                {"name": "EDGE_TYPE", "expr": "EDGE_TYPE", "data_type": "STRING",
                 "description": "Relationship type"},
                {"name": "PROPS", "expr": "PROPS", "data_type": "VARIANT",
                 "description": "Additional relationship properties"},
            ],
            "time_dimensions": [
                {"name": "EFFECTIVE_START", "expr": "EFFECTIVE_START", "data_type": "DATE",
                 "description": "Start date of relationship"},
                {"name": "EFFECTIVE_END", "expr": "EFFECTIVE_END", "data_type": "DATE",
                 "description": "End date of relationship"},
            ],
            "facts": [
                {"name": "WEIGHT", "expr": "WEIGHT", "data_type": "NUMBER",
                 "description": "Relationship weight/strength"},
            ],
        }

        # Add extra typed columns from relationship metadata
        for col in extra_cols:
            at = _analyst_type(col["data_type"])
            dim = {
                "name": col["name"].upper(),
                "expr": col["name"].upper(),
                "data_type": at,
                "description": f"{col['name']} of {rel_name} relationship",
            }
            if at == "NUMBER":
                table_def["facts"].append(dim)
            else:
                table_def["dimensions"].append(dim)

        model["tables"].append(table_def)

        # ── Add relationships between rel view and entity views ──
        # SRC_ID → source entity view
        if domain and domain in cls_by_name:
            src_view = f"V_{domain.upper()}"
            src_pk = "NODE_ID"
            model["relationships"].append({
                "name": f"{rel_name.lower()}_to_{domain.lower()}",
                "left_table": view_name,
                "right_table": src_view,
                "relationship_columns": [{"left_column": "SRC_ID", "right_column": src_pk}],
                "join_type": "left_outer",
                "relationship_type": "many_to_one",
            })
        # DST_ID → target entity view
        if range_cls and range_cls in cls_by_name:
            dst_view = f"V_{range_cls.upper()}"
            dst_pk = "NODE_ID"
            model["relationships"].append({
                "name": f"{rel_name.lower()}_to_{range_cls.lower()}",
                "left_table": view_name,
                "right_table": dst_view,
                "relationship_columns": [{"left_column": "DST_ID", "right_column": dst_pk}],
                "join_type": "left_outer",
                "relationship_type": "many_to_one",
            })

    # ── Verified queries ──
    ts = now_ts()
    if class_maps:
        primary = class_maps[0]
        cls_name = primary["class_name"]
        view_name = f"V_{cls_name.upper()}"
        model["verified_queries"].append({
            "name": f"list_{cls_name.lower()}",
            "question": f"Show all {cls_name.lower()} entities",
            "sql": f"SELECT NODE_ID, NAME FROM {view_name} ORDER BY NAME LIMIT 50",
            "verified_at": ts,
            "verified_by": "ontology-stack-builder",
        })

    if rel_maps:
        first_rel = rel_maps[0]
        rv = f"V_{first_rel['rel_name'].upper()}"
        model["verified_queries"].append({
            "name": "relationship_sample",
            "question": f"Show sample {first_rel['rel_name']} relationships",
            "sql": f"SELECT SRC_ID, DST_ID, EDGE_TYPE, EFFECTIVE_START FROM {rv} LIMIT 20",
            "verified_at": ts,
            "verified_by": "ontology-stack-builder",
        })

    return model


def generate_ontology_model(
    classes: list[dict],
    relations: list[dict],
    mappings: dict,
    database: str,
    schema: str,
    ontology_name: str,
    questions: list[str],
) -> dict:
    """Generate the Ontology Semantic Model — abstract views for cross-type reasoning."""
    model = {
        "name": f"{ontology_name}_ONTOLOGY_MODEL",
        "description": (
            f"Ontology model for {ontology_name}. Contains abstract views (VW_ONT_*) "
            f"that unify entity types and enable cross-type queries. Use for ABSTRACT reasoning: "
            f"'What types of entities exist?', 'Show all people', hierarchy traversal, "
            f"entity unification, polymorphic queries across types."
        ),
        "tables": [],
        "verified_queries": [],
    }

    class_maps = mappings.get("class_mappings", [])

    # Add abstract VW_ONT_* views
    for cm in class_maps:
        cls_name = cm["class_name"]
        view_name = f"VW_ONT_{cls_name.upper()}"

        table_def = {
            "name": view_name,
            "base_table": {"database": database, "schema": schema, "table": view_name},
            "description": f"Abstract ontology view for {cls_name} — unified entity interface",
            "primary_key": {"columns": ["ENTITY_ID"]},
            "dimensions": [
                {"name": "ENTITY_ID", "expr": "ENTITY_ID", "data_type": "VARCHAR",
                 "description": f"Unique identifier for {cls_name}"},
                {"name": "ENTITY_TYPE", "expr": "ENTITY_TYPE", "data_type": "VARCHAR",
                 "description": "Ontology class type"},
                {"name": "ENTITY_NAME", "expr": "ENTITY_NAME", "data_type": "VARCHAR",
                 "description": f"Display name for {cls_name}"},
            ],
        }
        model["tables"].append(table_def)

    # Unified entity view
    if len(class_maps) > 1:
        model["tables"].append({
            "name": "VW_ONT_ALL_ENTITIES",
            "base_table": {"database": database, "schema": schema, "table": "VW_ONT_ALL_ENTITIES"},
            "description": "Unified view of ALL entity types. Use for cross-type queries.",
            "primary_key": {"columns": ["ENTITY_ID"]},
            "dimensions": [
                {"name": "ENTITY_ID", "expr": "ENTITY_ID", "data_type": "VARCHAR"},
                {"name": "ENTITY_TYPE", "expr": "ENTITY_TYPE", "data_type": "VARCHAR",
                 "description": "The ontology class this entity belongs to"},
                {"name": "ENTITY_NAME", "expr": "ENTITY_NAME", "data_type": "VARCHAR"},
            ],
        })

    # REL_RESOLVED for cross-type relationship queries
    model["tables"].append({
        "name": "REL_RESOLVED",
        "base_table": {"database": database, "schema": schema, "table": "REL_RESOLVED"},
        "description": "Resolved relationships for cross-type reasoning",
        "dimensions": [
            {"name": "REL_NAME", "expr": "REL_NAME", "data_type": "VARCHAR"},
            {"name": "SRC_ID", "expr": "SRC_ID", "data_type": "VARCHAR"},
            {"name": "SRC_NAME", "expr": "SRC_NAME", "data_type": "VARCHAR"},
            {"name": "SRC_TYPE", "expr": "SRC_TYPE", "data_type": "VARCHAR"},
            {"name": "DST_ID", "expr": "DST_ID", "data_type": "VARCHAR"},
            {"name": "DST_NAME", "expr": "DST_NAME", "data_type": "VARCHAR"},
            {"name": "DST_TYPE", "expr": "DST_TYPE", "data_type": "VARCHAR"},
        ],
        "facts": [
            {"name": "WEIGHT", "expr": "WEIGHT", "data_type": "FLOAT"},
        ],
    })

    # Verified queries
    ts = now_ts()
    model["verified_queries"].extend([
        {
            "name": "entity_type_counts",
            "question": "How many entities of each type exist?",
            "sql": "SELECT ENTITY_TYPE, COUNT(*) AS cnt FROM VW_ONT_ALL_ENTITIES GROUP BY ENTITY_TYPE ORDER BY cnt DESC",
            "verified_at": ts,
            "verified_by": "ontology-stack-builder",
        },
        {
            "name": "search_entities",
            "question": "Find all entities matching a name pattern",
            "sql": "SELECT ENTITY_ID, ENTITY_TYPE, ENTITY_NAME FROM VW_ONT_ALL_ENTITIES WHERE ENTITY_NAME ILIKE '%{search_term}%' ORDER BY ENTITY_TYPE, ENTITY_NAME LIMIT 50",
            "verified_at": ts,
            "verified_by": "ontology-stack-builder",
        },
        {
            "name": "cross_type_relationships",
            "question": "What relationships connect different entity types?",
            "sql": "SELECT SRC_TYPE, REL_NAME, DST_TYPE, COUNT(*) AS cnt FROM REL_RESOLVED GROUP BY SRC_TYPE, REL_NAME, DST_TYPE ORDER BY cnt DESC LIMIT 50",
            "verified_at": ts,
            "verified_by": "ontology-stack-builder",
        },
    ])

    return model


def generate_metadata_model(
    database: str,
    schema: str,
    ontology_name: str,
) -> dict:
    """Generate the Metadata & Governance Model — all ~23 introspection tables."""

    def _tbl(name: str) -> dict:
        return {"database": database, "schema": schema, "table": name}

    model = {
        "name": f"{ontology_name}_METADATA_MODEL",
        "description": (
            f"Metadata and governance model for {ontology_name}. Contains ontology structure "
            f"definitions: classes, relations, properties, interfaces, rules, actions, roles, "
            f"permissions, functions, and data quality. Use for INTROSPECTION: "
            f"'What classes are defined?', 'What properties does X have?', 'Who has access?', "
            f"schema governance, data catalog questions."
        ),
        "tables": [
            # ONT_ONTOLOGY
            {
                "name": "ONT_ONTOLOGY",
                "base_table": _tbl("ONT_ONTOLOGY"),
                "description": "Ontology registry with versioning",
                "primary_key": {"columns": ["ONTOLOGY_NAME"]},
                "dimensions": [
                    {"name": "ONTOLOGY_NAME", "expr": "ONTOLOGY_NAME", "data_type": "STRING", "description": "Unique ontology identifier"},
                    {"name": "VERSION", "expr": "VERSION", "data_type": "STRING", "description": "Ontology version"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                    {"name": "DEFAULT_SCHEMA", "expr": "DEFAULT_SCHEMA", "data_type": "STRING"},
                    {"name": "CREATED_BY", "expr": "CREATED_BY", "data_type": "STRING"},
                    {"name": "IS_ACTIVE", "expr": "IS_ACTIVE", "data_type": "BOOLEAN"},
                ],
                "time_dimensions": [
                    {"name": "CREATED_AT", "expr": "CREATED_AT", "data_type": "TIMESTAMP"},
                ],
            },
            # ONT_OBJECT_SOURCE
            {
                "name": "ONT_OBJECT_SOURCE",
                "base_table": _tbl("ONT_OBJECT_SOURCE"),
                "description": "Maps object types to source tables with column mappings",
                "primary_key": {"columns": ["ONTOLOGY_NAME", "OBJ_TYPE", "SOURCE_TABLE"]},
                "dimensions": [
                    {"name": "ONTOLOGY_NAME", "expr": "ONTOLOGY_NAME", "data_type": "STRING"},
                    {"name": "OBJ_TYPE", "expr": "OBJ_TYPE", "data_type": "STRING", "description": "Object type name"},
                    {"name": "SOURCE_TABLE", "expr": "SOURCE_TABLE", "data_type": "STRING"},
                    {"name": "FILTER_SQL", "expr": "FILTER_SQL", "data_type": "STRING"},
                    {"name": "MAPPING", "expr": "MAPPING", "data_type": "VARIANT", "description": "JSON column mapping"},
                ],
            },
            # ONT_LINK_SOURCE
            {
                "name": "ONT_LINK_SOURCE",
                "base_table": _tbl("ONT_LINK_SOURCE"),
                "description": "Maps link types to source tables with column mappings",
                "primary_key": {"columns": ["ONTOLOGY_NAME", "LINK_TYPE", "SOURCE_TABLE"]},
                "dimensions": [
                    {"name": "ONTOLOGY_NAME", "expr": "ONTOLOGY_NAME", "data_type": "STRING"},
                    {"name": "LINK_TYPE", "expr": "LINK_TYPE", "data_type": "STRING", "description": "Link type name"},
                    {"name": "SOURCE_TABLE", "expr": "SOURCE_TABLE", "data_type": "STRING"},
                    {"name": "FILTER_SQL", "expr": "FILTER_SQL", "data_type": "STRING"},
                    {"name": "MAPPING", "expr": "MAPPING", "data_type": "VARIANT"},
                ],
            },
            # ONT_CLASS
            {
                "name": "ONT_CLASS",
                "base_table": _tbl("ONT_CLASS"),
                "description": "Object type definitions with class hierarchy",
                "primary_key": {"columns": ["CLASS_NAME"]},
                "dimensions": [
                    {"name": "CLASS_NAME", "expr": "CLASS_NAME", "data_type": "STRING", "description": "Unique class name"},
                    {"name": "PARENT_CLASS_NAME", "expr": "PARENT_CLASS_NAME", "data_type": "STRING", "description": "Parent class for hierarchy"},
                    {"name": "IS_ABSTRACT", "expr": "IS_ABSTRACT", "data_type": "BOOLEAN"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                    {"name": "ONTOLOGY_NAME", "expr": "ONTOLOGY_NAME", "data_type": "STRING"},
                    {"name": "TYPE_CLASS", "expr": "TYPE_CLASS", "data_type": "STRING", "description": "ANALYTICAL or OPERATIONAL"},
                ],
            },
            # ONT_RELATION_DEF
            {
                "name": "ONT_RELATION_DEF",
                "base_table": _tbl("ONT_RELATION_DEF"),
                "description": "Relationship type definitions with cardinality",
                "primary_key": {"columns": ["REL_NAME"]},
                "dimensions": [
                    {"name": "REL_NAME", "expr": "REL_NAME", "data_type": "STRING", "description": "Relationship type name"},
                    {"name": "DOMAIN_CLASS", "expr": "DOMAIN_CLASS", "data_type": "STRING", "description": "Source class"},
                    {"name": "RANGE_CLASS", "expr": "RANGE_CLASS", "data_type": "STRING", "description": "Target class"},
                    {"name": "CARDINALITY", "expr": "CARDINALITY", "data_type": "STRING"},
                    {"name": "IS_HIERARCHICAL", "expr": "IS_HIERARCHICAL", "data_type": "BOOLEAN"},
                    {"name": "INVERSE_REL_NAME", "expr": "INVERSE_REL_NAME", "data_type": "STRING"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                    {"name": "ONTOLOGY_NAME", "expr": "ONTOLOGY_NAME", "data_type": "STRING"},
                ],
            },
            # ONT_SHARED_PROPERTY
            {
                "name": "ONT_SHARED_PROPERTY",
                "base_table": _tbl("ONT_SHARED_PROPERTY"),
                "description": "Shared properties reusable across object types",
                "primary_key": {"columns": ["SHARED_PROP_NAME"]},
                "dimensions": [
                    {"name": "SHARED_PROP_NAME", "expr": "SHARED_PROP_NAME", "data_type": "STRING"},
                    {"name": "BASE_TYPE", "expr": "BASE_TYPE", "data_type": "STRING"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                    {"name": "DEFAULT_FORMAT", "expr": "DEFAULT_FORMAT", "data_type": "STRING"},
                ],
            },
            # ONT_PROPERTY
            {
                "name": "ONT_PROPERTY",
                "base_table": _tbl("ONT_PROPERTY"),
                "description": "Properties for each object type",
                "primary_key": {"columns": ["CLASS_NAME", "PROP_NAME"]},
                "dimensions": [
                    {"name": "CLASS_NAME", "expr": "CLASS_NAME", "data_type": "STRING"},
                    {"name": "PROP_NAME", "expr": "PROP_NAME", "data_type": "STRING"},
                    {"name": "DATA_TYPE", "expr": "DATA_TYPE", "data_type": "STRING"},
                    {"name": "SHARED_PROP_NAME", "expr": "SHARED_PROP_NAME", "data_type": "STRING"},
                    {"name": "IS_REQUIRED", "expr": "IS_REQUIRED", "data_type": "BOOLEAN"},
                    {"name": "IS_INDEXED", "expr": "IS_INDEXED", "data_type": "BOOLEAN"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                ],
            },
            # ONT_DERIVED_PROPERTY
            {
                "name": "ONT_DERIVED_PROPERTY",
                "base_table": _tbl("ONT_DERIVED_PROPERTY"),
                "description": "Computed/derived properties with definitions",
                "primary_key": {"columns": ["CLASS_NAME", "PROP_NAME"]},
                "dimensions": [
                    {"name": "CLASS_NAME", "expr": "CLASS_NAME", "data_type": "STRING"},
                    {"name": "PROP_NAME", "expr": "PROP_NAME", "data_type": "STRING"},
                    {"name": "DEFINITION_KIND", "expr": "DEFINITION_KIND", "data_type": "STRING", "description": "SQL or FUNCTION"},
                    {"name": "SQL_EXPR", "expr": "SQL_EXPR", "data_type": "STRING"},
                    {"name": "FUNCTION_NAME", "expr": "FUNCTION_NAME", "data_type": "STRING"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                ],
            },
            # ONT_INTERFACE
            {
                "name": "ONT_INTERFACE",
                "base_table": _tbl("ONT_INTERFACE"),
                "description": "Interface definitions for polymorphism",
                "primary_key": {"columns": ["INTERFACE_NAME"]},
                "dimensions": [
                    {"name": "INTERFACE_NAME", "expr": "INTERFACE_NAME", "data_type": "STRING"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                ],
            },
            # ONT_INTERFACE_PROPERTY
            {
                "name": "ONT_INTERFACE_PROPERTY",
                "base_table": _tbl("ONT_INTERFACE_PROPERTY"),
                "description": "Properties required by each interface",
                "primary_key": {"columns": ["INTERFACE_NAME", "PROP_NAME"]},
                "dimensions": [
                    {"name": "INTERFACE_NAME", "expr": "INTERFACE_NAME", "data_type": "STRING"},
                    {"name": "PROP_NAME", "expr": "PROP_NAME", "data_type": "STRING"},
                    {"name": "SHARED_PROP_NAME", "expr": "SHARED_PROP_NAME", "data_type": "STRING"},
                ],
            },
            # ONT_INTERFACE_IMPL
            {
                "name": "ONT_INTERFACE_IMPL",
                "base_table": _tbl("ONT_INTERFACE_IMPL"),
                "description": "Maps classes to interfaces they implement",
                "primary_key": {"columns": ["INTERFACE_NAME", "CLASS_NAME"]},
                "dimensions": [
                    {"name": "INTERFACE_NAME", "expr": "INTERFACE_NAME", "data_type": "STRING"},
                    {"name": "CLASS_NAME", "expr": "CLASS_NAME", "data_type": "STRING"},
                ],
            },
            # ONT_CLASS_MAP
            {
                "name": "ONT_CLASS_MAP",
                "base_table": _tbl("ONT_CLASS_MAP"),
                "description": "Maps ontology classes to physical tables",
                "dimensions": [
                    {"name": "CLASS_NAME", "expr": "CLASS_NAME", "data_type": "STRING"},
                    {"name": "SOURCE_DATABASE", "expr": "SOURCE_DATABASE", "data_type": "STRING"},
                    {"name": "SOURCE_SCHEMA", "expr": "SOURCE_SCHEMA", "data_type": "STRING"},
                    {"name": "SOURCE_TABLE", "expr": "SOURCE_TABLE", "data_type": "STRING"},
                    {"name": "ID_EXPR", "expr": "ID_EXPR", "data_type": "STRING"},
                    {"name": "NAME_EXPR", "expr": "NAME_EXPR", "data_type": "STRING"},
                ],
            },
            # ONT_REL_MAP
            {
                "name": "ONT_REL_MAP",
                "base_table": _tbl("ONT_REL_MAP"),
                "description": "Maps ontology relationships to physical tables",
                "dimensions": [
                    {"name": "REL_NAME", "expr": "REL_NAME", "data_type": "STRING"},
                    {"name": "SOURCE_DATABASE", "expr": "SOURCE_DATABASE", "data_type": "STRING"},
                    {"name": "SOURCE_SCHEMA", "expr": "SOURCE_SCHEMA", "data_type": "STRING"},
                    {"name": "SOURCE_TABLE", "expr": "SOURCE_TABLE", "data_type": "STRING"},
                    {"name": "SRC_ID_EXPR", "expr": "SRC_ID_EXPR", "data_type": "STRING"},
                    {"name": "DST_ID_EXPR", "expr": "DST_ID_EXPR", "data_type": "STRING"},
                ],
            },
            # ONT_RULE
            {
                "name": "ONT_RULE",
                "base_table": _tbl("ONT_RULE"),
                "description": "Inference rule registry",
                "primary_key": {"columns": ["RULE_ID"]},
                "dimensions": [
                    {"name": "RULE_ID", "expr": "RULE_ID", "data_type": "STRING"},
                    {"name": "RULE_KIND", "expr": "RULE_KIND", "data_type": "STRING", "description": "TRANSITIVE, PROPERTY_CHAIN, or INVERSE"},
                    {"name": "TARGET_REL", "expr": "TARGET_REL", "data_type": "STRING"},
                    {"name": "IS_ENABLED", "expr": "IS_ENABLED", "data_type": "BOOLEAN"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                ],
                "time_dimensions": [
                    {"name": "TS_CREATED", "expr": "TS_CREATED", "data_type": "TIMESTAMP"},
                ],
            },
            # REL_EDGE_INFERRED
            {
                "name": "REL_EDGE_INFERRED",
                "base_table": _tbl("REL_EDGE_INFERRED"),
                "description": "Inferred relationships from rules",
                "primary_key": {"columns": ["REL_NAME", "SRC_ID", "DST_ID", "RULE_ID"]},
                "dimensions": [
                    {"name": "REL_NAME", "expr": "REL_NAME", "data_type": "STRING"},
                    {"name": "SRC_ID", "expr": "SRC_ID", "data_type": "STRING"},
                    {"name": "DST_ID", "expr": "DST_ID", "data_type": "STRING"},
                    {"name": "INFERENCE_KIND", "expr": "INFERENCE_KIND", "data_type": "STRING"},
                    {"name": "RULE_ID", "expr": "RULE_ID", "data_type": "STRING"},
                ],
                "time_dimensions": [
                    {"name": "COMPUTED_AT", "expr": "COMPUTED_AT", "data_type": "TIMESTAMP"},
                ],
                "facts": [
                    {"name": "WEIGHT", "expr": "WEIGHT", "data_type": "NUMBER"},
                ],
            },
            # ONT_CONSTRAINT_VIOLATION
            {
                "name": "ONT_CONSTRAINT_VIOLATION",
                "base_table": _tbl("ONT_CONSTRAINT_VIOLATION"),
                "description": "Data quality constraint violations",
                "primary_key": {"columns": ["VIOLATION_ID"]},
                "dimensions": [
                    {"name": "VIOLATION_ID", "expr": "VIOLATION_ID", "data_type": "STRING"},
                    {"name": "CHECK_NAME", "expr": "CHECK_NAME", "data_type": "STRING"},
                    {"name": "SCOPE", "expr": "SCOPE", "data_type": "STRING"},
                    {"name": "REL_OR_CLASS", "expr": "REL_OR_CLASS", "data_type": "STRING"},
                    {"name": "DETAILS", "expr": "DETAILS", "data_type": "STRING"},
                ],
                "time_dimensions": [
                    {"name": "OBSERVED_AT", "expr": "OBSERVED_AT", "data_type": "TIMESTAMP"},
                ],
            },
            # ACT_TYPE
            {
                "name": "ACT_TYPE",
                "base_table": _tbl("ACT_TYPE"),
                "description": "Action type definitions",
                "primary_key": {"columns": ["ACTION_TYPE_ID"]},
                "dimensions": [
                    {"name": "ACTION_TYPE_ID", "expr": "ACTION_TYPE_ID", "data_type": "STRING"},
                    {"name": "ACTION_NAME", "expr": "ACTION_NAME", "data_type": "STRING"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                    {"name": "ONTOLOGY_NAME", "expr": "ONTOLOGY_NAME", "data_type": "STRING"},
                    {"name": "TARGET_CLASS", "expr": "TARGET_CLASS", "data_type": "STRING"},
                    {"name": "HANDLER_PROC", "expr": "HANDLER_PROC", "data_type": "STRING"},
                    {"name": "IS_ENABLED", "expr": "IS_ENABLED", "data_type": "BOOLEAN"},
                ],
                "time_dimensions": [
                    {"name": "TS_CREATED", "expr": "TS_CREATED", "data_type": "TIMESTAMP"},
                ],
            },
            # ACT_DEF
            {
                "name": "ACT_DEF",
                "base_table": _tbl("ACT_DEF"),
                "description": "Action parameter definitions",
                "primary_key": {"columns": ["ACTION_TYPE_ID", "PARAM_NAME"]},
                "dimensions": [
                    {"name": "ACTION_TYPE_ID", "expr": "ACTION_TYPE_ID", "data_type": "STRING"},
                    {"name": "PARAM_NAME", "expr": "PARAM_NAME", "data_type": "STRING"},
                    {"name": "PARAM_TYPE", "expr": "PARAM_TYPE", "data_type": "STRING"},
                    {"name": "IS_REQUIRED", "expr": "IS_REQUIRED", "data_type": "BOOLEAN"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                ],
            },
            # ONT_FUNCTION
            {
                "name": "ONT_FUNCTION",
                "base_table": _tbl("ONT_FUNCTION"),
                "description": "Function catalog — versioned code artifacts",
                "primary_key": {"columns": ["ONTOLOGY_NAME", "FUNCTION_NAME", "VERSION"]},
                "dimensions": [
                    {"name": "FUNCTION_NAME", "expr": "FUNCTION_NAME", "data_type": "STRING"},
                    {"name": "VERSION", "expr": "VERSION", "data_type": "STRING"},
                    {"name": "LANGUAGE", "expr": "LANGUAGE", "data_type": "STRING", "description": "SQL, PYTHON, JS, or EXTERNAL"},
                    {"name": "SNOWFLAKE_REF", "expr": "SNOWFLAKE_REF", "data_type": "STRING"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                    {"name": "ONTOLOGY_NAME", "expr": "ONTOLOGY_NAME", "data_type": "STRING"},
                ],
            },
            # ONT_FUNCTION_BINDING
            {
                "name": "ONT_FUNCTION_BINDING",
                "base_table": _tbl("ONT_FUNCTION_BINDING"),
                "description": "Binds functions to object types, link types, or actions",
                "dimensions": [
                    {"name": "ONTOLOGY_NAME", "expr": "ONTOLOGY_NAME", "data_type": "STRING"},
                    {"name": "FUNCTION_NAME", "expr": "FUNCTION_NAME", "data_type": "STRING"},
                    {"name": "VERSION", "expr": "VERSION", "data_type": "STRING"},
                    {"name": "BOUND_TO_KIND", "expr": "BOUND_TO_KIND", "data_type": "STRING", "description": "OBJECT_TYPE, LINK_TYPE, or ACTION_TYPE"},
                    {"name": "BOUND_TO_NAME", "expr": "BOUND_TO_NAME", "data_type": "STRING"},
                ],
            },
            # OBJ_VIEW_DEF
            {
                "name": "OBJ_VIEW_DEF",
                "base_table": _tbl("OBJ_VIEW_DEF"),
                "description": "Object view definitions for UI and governance",
                "primary_key": {"columns": ["OBJ_TYPE", "VIEW_NAME"]},
                "dimensions": [
                    {"name": "OBJ_TYPE", "expr": "OBJ_TYPE", "data_type": "STRING"},
                    {"name": "VIEW_NAME", "expr": "VIEW_NAME", "data_type": "STRING"},
                    {"name": "CREATED_BY", "expr": "CREATED_BY", "data_type": "STRING"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                    {"name": "DISPLAY_COLS", "expr": "DISPLAY_COLS", "data_type": "VARIANT"},
                    {"name": "VERSION", "expr": "VERSION", "data_type": "STRING"},
                    {"name": "STATUS", "expr": "STATUS", "data_type": "STRING"},
                ],
                "time_dimensions": [
                    {"name": "TS_CREATED", "expr": "TS_CREATED", "data_type": "TIMESTAMP"},
                ],
            },
            # OBJ_VIEW_FIELD
            {
                "name": "OBJ_VIEW_FIELD",
                "base_table": _tbl("OBJ_VIEW_FIELD"),
                "description": "Field configuration for object views",
                "primary_key": {"columns": ["OBJ_TYPE", "VIEW_NAME", "VERSION", "PROP_NAME"]},
                "dimensions": [
                    {"name": "OBJ_TYPE", "expr": "OBJ_TYPE", "data_type": "STRING"},
                    {"name": "VIEW_NAME", "expr": "VIEW_NAME", "data_type": "STRING"},
                    {"name": "VERSION", "expr": "VERSION", "data_type": "STRING"},
                    {"name": "PROP_NAME", "expr": "PROP_NAME", "data_type": "STRING"},
                    {"name": "RENDER_HINT", "expr": "RENDER_HINT", "data_type": "STRING"},
                ],
                "facts": [
                    {"name": "FIELD_ORDER", "expr": "FIELD_ORDER", "data_type": "NUMBER"},
                ],
            },
            # ONT_ROLE
            {
                "name": "ONT_ROLE",
                "base_table": _tbl("ONT_ROLE"),
                "description": "Ontology-specific roles for access control",
                "primary_key": {"columns": ["ONTOLOGY_NAME", "ONT_ROLE_NAME"]},
                "dimensions": [
                    {"name": "ONTOLOGY_NAME", "expr": "ONTOLOGY_NAME", "data_type": "STRING"},
                    {"name": "ONT_ROLE_NAME", "expr": "ONT_ROLE_NAME", "data_type": "STRING"},
                    {"name": "DESCRIPTION", "expr": "DESCRIPTION", "data_type": "STRING"},
                ],
            },
            # ONT_ROLE_BINDING
            {
                "name": "ONT_ROLE_BINDING",
                "base_table": _tbl("ONT_ROLE_BINDING"),
                "description": "Maps ontology roles to Snowflake roles",
                "primary_key": {"columns": ["ONTOLOGY_NAME", "ONT_ROLE_NAME", "SNOWFLAKE_ROLE"]},
                "dimensions": [
                    {"name": "ONTOLOGY_NAME", "expr": "ONTOLOGY_NAME", "data_type": "STRING"},
                    {"name": "ONT_ROLE_NAME", "expr": "ONT_ROLE_NAME", "data_type": "STRING"},
                    {"name": "SNOWFLAKE_ROLE", "expr": "SNOWFLAKE_ROLE", "data_type": "STRING"},
                ],
            },
            # ONT_PERMISSION
            {
                "name": "ONT_PERMISSION",
                "base_table": _tbl("ONT_PERMISSION"),
                "description": "Granular permissions for object types, link types, and actions",
                "primary_key": {"columns": ["ONTOLOGY_NAME", "SUBJECT_KIND", "SUBJECT_NAME", "ONT_ROLE_NAME", "PRIVILEGE"]},
                "dimensions": [
                    {"name": "ONTOLOGY_NAME", "expr": "ONTOLOGY_NAME", "data_type": "STRING"},
                    {"name": "SUBJECT_KIND", "expr": "SUBJECT_KIND", "data_type": "STRING", "description": "OBJECT_TYPE, LINK_TYPE, or ACTION_TYPE"},
                    {"name": "SUBJECT_NAME", "expr": "SUBJECT_NAME", "data_type": "STRING"},
                    {"name": "ONT_ROLE_NAME", "expr": "ONT_ROLE_NAME", "data_type": "STRING"},
                    {"name": "PRIVILEGE", "expr": "PRIVILEGE", "data_type": "STRING", "description": "READ, WRITE, EXECUTE, or ADMIN"},
                ],
            },
        ],
        "verified_queries": [],
    }

    ts = now_ts()
    model["verified_queries"].extend([
        {
            "name": "list_all_classes",
            "question": "What ontology classes are defined?",
            "sql": "SELECT CLASS_NAME, PARENT_CLASS_NAME, IS_ABSTRACT, DESCRIPTION FROM ONT_CLASS ORDER BY CLASS_NAME",
            "verified_at": ts,
            "verified_by": "ontology-stack-builder",
        },
        {
            "name": "list_all_relations",
            "question": "What relationship types exist in the ontology?",
            "sql": "SELECT REL_NAME, DOMAIN_CLASS, RANGE_CLASS, CARDINALITY FROM ONT_RELATION_DEF ORDER BY REL_NAME",
            "verified_at": ts,
            "verified_by": "ontology-stack-builder",
        },
        {
            "name": "class_properties",
            "question": "What properties does a given class have?",
            "sql": "SELECT CLASS_NAME, PROP_NAME, DATA_TYPE, IS_REQUIRED, DESCRIPTION FROM ONT_PROPERTY ORDER BY CLASS_NAME, PROP_NAME",
            "verified_at": ts,
            "verified_by": "ontology-stack-builder",
        },
        {
            "name": "roles_and_permissions",
            "question": "What roles and permissions are defined?",
            "sql": "SELECT r.ONT_ROLE_NAME, r.DESCRIPTION, COUNT(p.PRIVILEGE) AS perm_count FROM ONT_ROLE r LEFT JOIN ONT_PERMISSION p ON r.ONTOLOGY_NAME = p.ONTOLOGY_NAME AND r.ONT_ROLE_NAME = p.ONT_ROLE_NAME GROUP BY r.ONT_ROLE_NAME, r.DESCRIPTION ORDER BY r.ONT_ROLE_NAME",
            "verified_at": ts,
            "verified_by": "ontology-stack-builder",
        },
        {
            "name": "inference_rules",
            "question": "What inference rules are defined?",
            "sql": "SELECT RULE_ID, RULE_KIND, TARGET_REL, IS_ENABLED, DESCRIPTION FROM ONT_RULE ORDER BY RULE_ID",
            "verified_at": ts,
            "verified_by": "ontology-stack-builder",
        },
        {
            "name": "data_quality_violations",
            "question": "Are there any data quality violations?",
            "sql": "SELECT CHECK_NAME, SCOPE, REL_OR_CLASS, COUNT(*) AS violation_count FROM ONT_CONSTRAINT_VIOLATION GROUP BY CHECK_NAME, SCOPE, REL_OR_CLASS ORDER BY violation_count DESC",
            "verified_at": ts,
            "verified_by": "ontology-stack-builder",
        },
    ])

    return model


def main():
    parser = argparse.ArgumentParser(description="Generate Semantic Model YAMLs")
    parser.add_argument("--classes-json", required=True)
    parser.add_argument("--relations-json", required=True)
    parser.add_argument("--mappings-json", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--ontology-name", required=True)
    parser.add_argument("--models", required=True, help="Comma-separated: kg,ontology,metadata")
    parser.add_argument("--questions", default="", help="Pipe-separated business questions")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(args.classes_json) as f:
        classes = json.load(f)
    with open(args.relations_json) as f:
        relations = json.load(f)
    with open(args.mappings_json) as f:
        mappings = json.load(f)

    questions = [q.strip() for q in args.questions.split("|") if q.strip()]
    selected_models = [m.strip().lower() for m in args.models.split(",")]

    database = args.database
    schema = args.schema
    ontology_name = args.ontology_name

    print(f"Generating semantic models: {', '.join(selected_models)}")
    files_written = []

    if "kg" in selected_models:
        model = generate_kg_model(classes, relations, mappings, database, schema, ontology_name, questions)
        path = output_dir / "kg_semantic_model.yaml"
        path.write_text(yaml.dump(model, default_flow_style=False, sort_keys=False, allow_unicode=True, width=120))
        files_written.append(str(path))
        print(f"  Wrote {path} ({len(model['tables'])} tables, {len(model['verified_queries'])} verified queries)")

    if "ontology" in selected_models:
        model = generate_ontology_model(classes, relations, mappings, database, schema, ontology_name, questions)
        path = output_dir / "ontology_semantic_model.yaml"
        path.write_text(yaml.dump(model, default_flow_style=False, sort_keys=False, allow_unicode=True, width=120))
        files_written.append(str(path))
        print(f"  Wrote {path} ({len(model['tables'])} tables, {len(model['verified_queries'])} verified queries)")

    if "metadata" in selected_models:
        model = generate_metadata_model(database, schema, ontology_name)
        path = output_dir / "metadata_governance_model.yaml"
        path.write_text(yaml.dump(model, default_flow_style=False, sort_keys=False, allow_unicode=True, width=120))
        files_written.append(str(path))
        print(f"  Wrote {path} ({len(model['tables'])} tables, {len(model['verified_queries'])} verified queries)")

    print(f"\nGenerated {len(files_written)} semantic model files")


if __name__ == "__main__":
    main()
