#!/usr/bin/env python3
"""
assess_complexity.py - Parse source DDL and score migration complexity.

Usage:
    uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/assess_complexity.py \
        --input <ddl_file.sql> --platform <oracle|teradata|redshift|sqlserver> \
        --output <report.json>
"""

import argparse
import json
import re
import sys
from collections import defaultdict


COMPLEXITY_RULES = {
    "oracle": {
        "trivial": [
            (r"CREATE\s+TABLE", "table"),
            (r"CREATE\s+SEQUENCE", "sequence"),
            (r"CREATE\s+(UNIQUE\s+)?INDEX", "index"),
        ],
        "simple": [
            (r"CREATE\s+(OR\s+REPLACE\s+)?VIEW", "view"),
            (r"ALTER\s+TABLE.*ADD\s+CONSTRAINT", "constraint"),
        ],
        "moderate": [
            (r"CREATE\s+MATERIALIZED\s+VIEW", "materialized_view"),
            (r"CREATE\s+(OR\s+REPLACE\s+)?FUNCTION", "function"),
        ],
        "complex": [
            (r"CREATE\s+(OR\s+REPLACE\s+)?PROCEDURE", "procedure"),
            (r"CREATE\s+(OR\s+REPLACE\s+)?TRIGGER", "trigger"),
            (r"CONNECT\s+BY", "hierarchical_query"),
            (r"DBMS_\w+", "dbms_package_call"),
        ],
        "critical": [
            (r"CREATE\s+(OR\s+REPLACE\s+)?PACKAGE", "package"),
            (r"DB_?LINK|DATABASE\s+LINK", "db_link"),
            (r"PRAGMA\s+AUTONOMOUS_TRANSACTION", "autonomous_txn"),
            (r"TYPE\s+\w+\s+(IS|AS)\s+(OBJECT|TABLE|RECORD)", "user_defined_type"),
        ],
    },
    "teradata": {
        "trivial": [
            (r"CREATE\s+(MULTISET|SET)?\s*TABLE", "table"),
            (r"CREATE\s+(UNIQUE\s+)?INDEX", "index"),
        ],
        "simple": [
            (r"CREATE\s+(OR\s+REPLACE\s+)?VIEW", "view"),
            (r"COLLECT\s+STATISTICS", "collect_stats"),
        ],
        "moderate": [
            (r"CREATE\s+JOIN\s+INDEX", "join_index"),
            (r"PERIOD\s*\(", "temporal_period"),
            (r"CREATE\s+MACRO", "macro"),
        ],
        "complex": [
            (r"CREATE\s+PROCEDURE", "procedure"),
            (r"CREATE\s+TRIGGER", "trigger"),
            (r"NORMALIZE", "normalize_query"),
        ],
        "critical": [
            (r"\.EXPORT", "bteq_export"),
            (r"\.IMPORT", "bteq_import"),
            (r"\.LOGON", "bteq_logon"),
        ],
    },
    "redshift": {
        "trivial": [
            (r"CREATE\s+TABLE", "table"),
        ],
        "simple": [
            (r"CREATE\s+(OR\s+REPLACE\s+)?VIEW", "view"),
            (r"DISTSTYLE|DISTKEY|SORTKEY", "distribution_hint"),
        ],
        "moderate": [
            (r"CREATE\s+MATERIALIZED\s+VIEW", "materialized_view"),
            (r"CREATE\s+(OR\s+REPLACE\s+)?FUNCTION", "udf"),
            (r"CREATE\s+EXTERNAL\s+TABLE", "spectrum_table"),
        ],
        "complex": [
            (r"CREATE\s+(OR\s+REPLACE\s+)?PROCEDURE", "procedure"),
            (r"CREATE\s+EXTERNAL\s+SCHEMA", "external_schema"),
        ],
        "critical": [
            (r"CREATE\s+LIBRARY", "custom_library"),
            (r"LAMBDA", "lambda_udf"),
        ],
    },
    "sqlserver": {
        "trivial": [
            (r"CREATE\s+TABLE", "table"),
            (r"CREATE\s+(UNIQUE\s+)?(CLUSTERED\s+|NONCLUSTERED\s+)?INDEX", "index"),
            (r"CREATE\s+SEQUENCE", "sequence"),
        ],
        "simple": [
            (r"CREATE\s+(OR\s+ALTER\s+)?VIEW", "view"),
            (r"ALTER\s+TABLE.*ADD\s+CONSTRAINT", "constraint"),
        ],
        "moderate": [
            (r"CREATE\s+(OR\s+ALTER\s+)?FUNCTION", "function"),
            (r"CREATE\s+(OR\s+ALTER\s+)?TRIGGER", "trigger"),
        ],
        "complex": [
            (r"CREATE\s+(OR\s+ALTER\s+)?PROC(EDURE)?", "procedure"),
            (r"EXEC(UTE)?\s+sp_executesql", "dynamic_sql"),
            (r"CROSS\s+APPLY|OUTER\s+APPLY", "apply_join"),
            (r"FOR\s+XML\s+PATH", "xml_aggregation"),
        ],
        "critical": [
            (r"WITH\s+EXTERNAL_ACCESS|CLR", "clr_procedure"),
            (r"OPENROWSET|OPENQUERY|OPENDATASOURCE", "linked_server_query"),
            (r"CREATE\s+ASSEMBLY", "clr_assembly"),
            (r"SERVICE\s+BROKER", "service_broker"),
        ],
    },
}

SCORE_MAP = {"trivial": 1, "simple": 2, "moderate": 3, "complex": 4, "critical": 5}


def assess_ddl(ddl_text: str, platform: str) -> dict:
    rules = COMPLEXITY_RULES.get(platform)
    if not rules:
        return {"error": f"Unknown platform: {platform}"}

    findings = defaultdict(list)
    object_counts = defaultdict(int)
    total_score = 0
    total_count = 0

    for level, patterns in rules.items():
        for pattern, obj_type in patterns:
            matches = re.findall(pattern, ddl_text, re.IGNORECASE)
            if matches:
                count = len(matches)
                score = SCORE_MAP[level]
                object_counts[obj_type] = count
                total_score += count * score
                total_count += count
                findings[level].append(
                    {"object_type": obj_type, "count": count, "score": score}
                )

    weighted_avg = round(total_score / total_count, 2) if total_count > 0 else 0

    if weighted_avg <= 1.5:
        readiness = "Ready"
    elif weighted_avg <= 3.0:
        readiness = "Ready with caveats"
    else:
        readiness = "Needs redesign"

    return {
        "platform": platform,
        "total_objects": total_count,
        "weighted_complexity": weighted_avg,
        "readiness": readiness,
        "findings_by_level": dict(findings),
        "object_counts": dict(object_counts),
        "critical_items": findings.get("critical", []),
    }


def main():
    parser = argparse.ArgumentParser(description="Assess DDL migration complexity")
    parser.add_argument("--input", required=True, help="Path to source DDL file")
    parser.add_argument(
        "--platform",
        required=True,
        choices=["oracle", "teradata", "redshift", "sqlserver"],
        help="Source database platform",
    )
    parser.add_argument("--output", help="Output JSON file (default: stdout)")
    args = parser.parse_args()

    try:
        with open(args.input, "r") as f:
            ddl_text = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    result = assess_ddl(ddl_text, args.platform)

    output = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Report written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
