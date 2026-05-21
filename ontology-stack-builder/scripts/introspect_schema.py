# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyyaml>=6.0",
# ]
# ///
"""
Schema Introspection Script - Analyzes Snowflake tables to propose ontology classes and relations.

This is the schema-first discovery path (no OWL file). It reads table metadata
provided as JSON (pre-fetched via DESCRIBE TABLE / SHOW PRIMARY KEYS / SHOW IMPORTED KEYS)
and proposes ontology classes, relations, and mappings.

Usage:
    uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/introspect_schema.py \
      --metadata-json /tmp/table_metadata.json \
      --questions "Who are the top customers?|What products sell most?" \
      --output-dir /tmp/ontology_parsed

The --metadata-json file is produced by the SKILL.md workflow which calls
DESCRIBE TABLE and SHOW KEYS via snowflake_sql_execute, then assembles the results.

Expected metadata JSON format:
{
  "database": "MYDB",
  "schema": "MYSCHEMA",
  "tables": [
    {
      "name": "CUSTOMERS",
      "columns": [
        {"name": "CUSTOMER_ID", "type": "NUMBER", "nullable": false, "primary_key": true},
        {"name": "NAME", "type": "VARCHAR", "nullable": false},
        {"name": "EMAIL", "type": "VARCHAR", "nullable": true},
        ...
      ],
      "primary_keys": ["CUSTOMER_ID"],
      "foreign_keys": [
        {"column": "REGION_ID", "ref_table": "REGIONS", "ref_column": "REGION_ID"}
      ],
      "row_count": 50000
    },
    ...
  ]
}
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


# Heuristic patterns for identifying column roles
ID_PATTERNS = re.compile(r"^(.*_)?(ID|KEY|CODE|UUID|PK)$", re.IGNORECASE)
NAME_PATTERNS = re.compile(r"^(.*_)?(NAME|LABEL|TITLE|DISPLAY_NAME|FULL_NAME)$", re.IGNORECASE)
DESC_PATTERNS = re.compile(r"^(.*_)?(DESC|DESCRIPTION|NOTES|COMMENT|BIO)$", re.IGNORECASE)
TYPE_PATTERNS = re.compile(r"^(.*_)?(TYPE|CATEGORY|CLASS|STATUS|KIND|GROUP)$", re.IGNORECASE)
DATE_PATTERNS = re.compile(r"^(.*_)?(DATE|TIME|TIMESTAMP|CREATED|UPDATED|MODIFIED|_AT|_ON)$", re.IGNORECASE)
FK_SUFFIX = re.compile(r"^(.+)_(ID|KEY|CODE)$", re.IGNORECASE)


def classify_table(table: dict) -> str:
    """Classify a table as 'entity', 'relationship', or 'lookup' based on structure."""
    cols = table["columns"]
    col_names = [c["name"].upper() for c in cols]
    pks = [pk.upper() for pk in table.get("primary_keys", [])]
    fks = table.get("foreign_keys", [])

    # Junction/bridge table: composite PK with 2+ FKs, few non-FK columns
    if len(pks) >= 2 and len(fks) >= 2:
        non_fk_cols = [c for c in col_names if not any(
            c == fk["column"].upper() for fk in fks
        ) and c not in pks]
        if len(non_fk_cols) <= 3:
            return "relationship"

    # Lookup table: small column count, single PK, has name/desc column
    if len(cols) <= 5 and len(pks) == 1:
        has_name = any(NAME_PATTERNS.match(c) for c in col_names)
        if has_name:
            return "lookup"

    return "entity"


def find_column(columns: list[dict], pattern: re.Pattern) -> str | None:
    """Find the first column matching a pattern."""
    for col in columns:
        if pattern.match(col["name"]):
            return col["name"]
    return None


def infer_class_name(table_name: str) -> str:
    """Convert a table name to an ontology class name.
    
    CUSTOMERS -> Customer, ORDER_ITEMS -> OrderItem, etc.
    """
    # Remove common prefixes
    name = table_name
    for prefix in ("TBL_", "T_", "DIM_", "FACT_", "STG_", "RAW_"):
        if name.upper().startswith(prefix):
            name = name[len(prefix):]
            break

    # Convert to PascalCase singular
    parts = name.lower().split("_")
    # Simple singularization: remove trailing 's' if word is >3 chars
    singular_parts = []
    for p in parts:
        if len(p) > 3 and p.endswith("s") and not p.endswith("ss"):
            singular_parts.append(p[:-1])
        else:
            singular_parts.append(p)
    return "".join(w.capitalize() for w in singular_parts)


def propose_classes(tables: list[dict], database: str, schema: str) -> list[dict]:
    """Propose ontology classes from table metadata."""
    classes = []

    for table in tables:
        tbl_type = classify_table(table)
        if tbl_type == "relationship":
            continue  # Handled in propose_relations

        class_name = infer_class_name(table["name"])
        cols = table["columns"]
        pks = table.get("primary_keys", [])

        # Find key columns
        id_col = pks[0] if pks else find_column(cols, ID_PATTERNS) or cols[0]["name"]
        name_col = find_column(cols, NAME_PATTERNS)
        desc_col = find_column(cols, DESC_PATTERNS)
        type_col = find_column(cols, TYPE_PATTERNS)

        is_abstract = tbl_type == "lookup"  # Lookups often define type hierarchies

        # Collect all columns with type info for downstream property generation
        all_columns = [
            {
                "name": c["name"],
                "data_type": c.get("type", "VARCHAR"),
                "nullable": c.get("nullable", True),
                "is_primary_key": c["name"] in pks,
                "is_foreign_key": any(
                    c["name"].upper() == fk["column"].upper()
                    for fk in table.get("foreign_keys", [])
                ),
            }
            for c in cols
        ]

        classes.append({
            "name": class_name,
            "label": class_name,
            "description": f"Entity from table {table['name']}",
            "parent_name": None,
            "is_abstract": is_abstract,
            "is_deprecated": False,
            "namespace": f"{database}.{schema}",
            "uri": f"urn:{database}:{schema}:{class_name}",
            # Mapping info (carried through for convenience)
            "_source_table": table["name"],
            "_id_column": id_col,
            "_name_column": name_col,
            "_desc_column": desc_col,
            "_type_column": type_col,
            "_table_type": tbl_type,
            "_columns": all_columns,
        })

    return classes


def propose_relations(tables: list[dict], classes: list[dict]) -> list[dict]:
    """Propose ontology relations from FK relationships and junction tables."""
    relations = []
    class_by_table = {c["_source_table"]: c["name"] for c in classes}

    # 1. FK-based relations from entity tables
    for table in tables:
        tbl_type = classify_table(table)
        if tbl_type == "relationship":
            continue

        src_class = class_by_table.get(table["name"])
        if not src_class:
            continue

        for fk in table.get("foreign_keys", []):
            ref_table = fk["ref_table"]
            # Handle fully qualified ref_table names
            ref_simple = ref_table.split(".")[-1] if "." in ref_table else ref_table
            dst_class = class_by_table.get(ref_simple) or class_by_table.get(ref_table)
            if not dst_class:
                continue

            # Derive relation name from FK column
            fk_col = fk["column"]
            match = FK_SUFFIX.match(fk_col)
            if match:
                rel_name = f"has_{match.group(1)}".lower().replace("__", "_")
            else:
                rel_name = f"belongs_to_{dst_class}".lower()

            relations.append({
                "name": rel_name,
                "label": rel_name.replace("_", " ").title(),
                "description": f"FK relationship from {table['name']}.{fk_col} to {ref_table}.{fk['ref_column']}",
                "domain_class": src_class,
                "domain_classes": [src_class],
                "range_class": dst_class,
                "range_classes": [dst_class],
                "is_transitive": False,
                "is_symmetric": False,
                "is_functional": True,
                "is_abstract": False,
                "is_hierarchical": False,
                "parent_name": None,
                "inverse_name": None,
                "cardinality": "N:1",
                "uri": f"urn:rel:{rel_name}",
                "_source_table": table["name"],
                "_src_column": fk["column"],
                "_dst_column": fk["ref_column"],
            })

    # 2. Junction table relations
    for table in tables:
        tbl_type = classify_table(table)
        if tbl_type != "relationship":
            continue

        fks = table.get("foreign_keys", [])
        if len(fks) < 2:
            continue

        # Create a relation between the first two FK targets
        ref1 = fks[0]["ref_table"].split(".")[-1]
        ref2 = fks[1]["ref_table"].split(".")[-1]
        cls1 = class_by_table.get(ref1)
        cls2 = class_by_table.get(ref2)
        if not cls1 or not cls2:
            continue

        rel_name = infer_class_name(table["name"]).lower()

        relations.append({
            "name": rel_name,
            "label": rel_name.replace("_", " ").title(),
            "description": f"Many-to-many relationship via junction table {table['name']}",
            "domain_class": cls1,
            "domain_classes": [cls1],
            "range_class": cls2,
            "range_classes": [cls2],
            "is_transitive": False,
            "is_symmetric": False,
            "is_functional": False,
            "is_abstract": False,
            "is_hierarchical": False,
            "parent_name": None,
            "inverse_name": None,
            "cardinality": "N:N",
            "uri": f"urn:rel:{rel_name}",
            "_source_table": table["name"],
            "_src_column": fks[0]["column"],
            "_dst_column": fks[1]["column"],
        })

    return relations


def propose_mappings(classes: list[dict], database: str, schema: str) -> list[dict]:
    """Build class-to-table mapping entries."""
    mappings = []
    for cls in classes:
        if cls.get("_table_type") == "relationship":
            continue
        mappings.append({
            "class_name": cls["name"],
            "source_table": f"{database}.{schema}.{cls['_source_table']}",
            "filter_condition": None,
            "id_column": cls["_id_column"],
            "name_column": cls.get("_name_column"),
            "description_column": cls.get("_desc_column"),
            "type_column": cls.get("_type_column"),
            "props_column": None,
            "columns": cls.get("_columns", []),
        })
    return mappings


def propose_rel_mappings(relations: list[dict], tables: list[dict], database: str, schema: str) -> list[dict]:
    """Build relation-to-table mapping entries."""
    table_by_name = {t["name"]: t for t in tables}
    rel_mappings = []
    for rel in relations:
        src_table_name = rel["_source_table"]
        table_meta = table_by_name.get(src_table_name, {})
        # Collect non-FK/non-PK columns from relationship tables (e.g. WEIGHT, EFFECTIVE_START)
        pks = set(pk.upper() for pk in table_meta.get("primary_keys", []))
        fk_cols = set(fk["column"].upper() for fk in table_meta.get("foreign_keys", []))
        extra_columns = [
            {
                "name": c["name"],
                "data_type": c.get("type", "VARCHAR"),
                "nullable": c.get("nullable", True),
            }
            for c in table_meta.get("columns", [])
            if c["name"].upper() not in pks
            and c["name"].upper() not in fk_cols
            and c["name"].upper() != rel["_src_column"].upper()
            and c["name"].upper() != rel["_dst_column"].upper()
        ]
        rel_mappings.append({
            "rel_name": rel["name"],
            "source_table": f"{database}.{schema}.{src_table_name}",
            "filter_condition": None,
            "src_column": rel["_src_column"],
            "dst_column": rel["_dst_column"],
            "props_column": None,
            "columns": extra_columns,
        })
    return rel_mappings


def compute_stats(classes: list, relations: list) -> dict:
    """Compute summary statistics about the proposed ontology."""
    children_map = defaultdict(list)
    roots = []
    for cls in classes:
        if cls.get("parent_name"):
            children_map[cls["parent_name"]].append(cls["name"])
        else:
            roots.append(cls["name"])

    return {
        "total_classes": len(classes),
        "abstract_classes": sum(1 for c in classes if c.get("is_abstract")),
        "concrete_classes": sum(1 for c in classes if not c.get("is_abstract")),
        "deprecated_classes": 0,
        "root_classes": len(roots),
        "max_hierarchy_depth": 0,
        "total_relations": len(relations),
        "hierarchical_relations": sum(1 for r in relations if r.get("is_hierarchical")),
        "transitive_relations": sum(1 for r in relations if r.get("is_transitive")),
        "total_individuals": 0,
        "top_namespaces": {},
    }


def clean_for_output(items: list[dict]) -> list[dict]:
    """Remove internal _ prefixed keys from output."""
    cleaned = []
    for item in items:
        cleaned.append({k: v for k, v in item.items() if not k.startswith("_")})
    return cleaned


def main():
    parser = argparse.ArgumentParser(description="Introspect Snowflake schema and propose ontology")
    parser.add_argument("--metadata-json", required=True, help="Path to table metadata JSON")
    parser.add_argument("--questions", default="", help="Pipe-separated business questions")
    parser.add_argument("--output-dir", required=True, help="Directory for output JSON files")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load metadata
    with open(args.metadata_json) as f:
        metadata = json.load(f)

    database = metadata["database"]
    schema = metadata["schema"]
    tables = metadata["tables"]

    print(f"Introspecting {len(tables)} tables in {database}.{schema}")

    # Propose ontology
    classes = propose_classes(tables, database, schema)
    relations = propose_relations(tables, classes)
    class_mappings = propose_mappings(classes, database, schema)
    rel_mappings = propose_rel_mappings(relations, tables, database, schema)
    stats = compute_stats(classes, relations)

    # Questions (stored for downstream use in semantic model verified queries)
    questions = [q.strip() for q in args.questions.split("|") if q.strip()]

    print(f"  Proposed {len(classes)} classes, {len(relations)} relations")
    print(f"  {len(class_mappings)} class mappings, {len(rel_mappings)} relation mappings")

    # Write outputs (same format as parse_owl.py for downstream compatibility)
    for name, data in [
        ("classes", clean_for_output(classes)),
        ("relations", clean_for_output(relations)),
        ("stats", stats),
        ("individuals", []),  # No individuals in schema-first path
    ]:
        out_path = output_dir / f"{name}.json"
        with open(out_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  Wrote {out_path}")

    # Write mappings (additional output not in parse_owl.py)
    mappings_data = {
        "database": database,
        "schema": schema,
        "class_mappings": class_mappings,
        "relation_mappings": rel_mappings,
        "questions": questions,
    }
    mappings_path = output_dir / "mappings.json"
    with open(mappings_path, "w") as f:
        json.dump(mappings_data, f, indent=2)
    print(f"  Wrote {mappings_path}")

    # Print summary
    print(f"\n=== Proposed Ontology ===")
    print(f"  Classes: {len(classes)}")
    for cls in classes:
        ttype = "abstract" if cls.get("is_abstract") else "concrete"
        print(f"    - {cls['name']} ({ttype}) <- {cls['_source_table']}")
    print(f"  Relations: {len(relations)}")
    for rel in relations:
        print(f"    - {rel['domain_class']} --[{rel['name']}]--> {rel['range_class']}")


if __name__ == "__main__":
    main()
