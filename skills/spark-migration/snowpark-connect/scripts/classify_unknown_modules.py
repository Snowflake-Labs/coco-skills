#!/usr/bin/env python3
"""Apply pre-computed module classifications to analysis.json.

The executing agent classifies modules from its own knowledge and passes
the result via --classifications. No Snowflake session or LLM call needed.

spark_related     -> kind promoted to needs_adjudication for Phase 1.1b
not_spark_related -> resolution:safe, adjudicated:true

Usage:
    # Step 1: see which modules need classification
    python3 classify_unknown_modules.py --analysis <analysis.json> --list-modules

    # Step 2: agent classifies, then applies
    python3 classify_unknown_modules.py --analysis <analysis.json> \
        --classifications '{"boto3": "not_spark_related", "mssparkutils": "spark_related"}'
"""

import argparse
import json
import sys
from pathlib import Path


def _load_rows(path: Path) -> tuple[list, dict | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Cannot read analysis.json: {e}") from e
    if isinstance(data, list):
        return data, None
    for k, v in data.items():
        if isinstance(v, list):
            return v, (data, k)
    return [], None


def _apply(rows: list, classifications: dict[str, str]) -> tuple[int, int]:
    classify_spark = classify_not_spark = 0
    for row in rows:
        if row.get("kind") != "needs_classification" or row.get("adjudicated"):
            continue
        verdict = classifications.get(row.get("import_module", ""))
        if verdict == "spark_related":
            row["kind"] = "needs_adjudication"
            row["source"] = "unknown_surface_scan"
            row["detected_by"] = "deferred_to_fixer"
            classify_spark += 1
        elif verdict == "not_spark_related":
            row["resolution"] = "safe"
            row["adjudicated"] = True
            row["resolution_reason"] = "classifier: not Spark-related"
            classify_not_spark += 1
    return classify_spark, classify_not_spark


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", required=True, type=Path)
    ap.add_argument("--list-modules", action="store_true",
                    help="Print unclassified module names and exit")
    ap.add_argument("--classifications", default=None,
                    help='JSON object mapping module names to "spark_related" or "not_spark_related"')
    args = ap.parse_args(argv)

    if not args.analysis.exists():
        print(f"ERROR: analysis.json not found: {args.analysis}", file=sys.stderr)
        return 3

    try:
        rows, container = _load_rows(args.analysis)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    modules = sorted({
        r["import_module"]
        for r in rows
        if r.get("kind") == "needs_classification" and not r.get("adjudicated") and r.get("import_module")
    })

    if args.list_modules:
        print(json.dumps(modules))
        return 0

    if not modules:
        print("CLASSIFICATION_RESULT classify_spark=0 classify_not_spark=0 modules=0")
        return 0

    if not args.classifications:
        print("ERROR: --classifications required", file=sys.stderr)
        return 3

    try:
        classifications = json.loads(args.classifications)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid --classifications JSON: {e}", file=sys.stderr)
        return 3

    missing = [m for m in modules if m not in classifications]
    if missing:
        print(f"ERROR: missing classifications for: {missing}", file=sys.stderr)
        return 3

    spark_count, not_spark_count = _apply(rows, classifications)

    if container is None:
        args.analysis.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    else:
        obj, key = container
        obj[key] = rows
        args.analysis.write_text(json.dumps(obj, indent=2), encoding="utf-8")

    print(f"CLASSIFICATION_RESULT classify_spark={spark_count} classify_not_spark={not_spark_count} modules={len(modules)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
