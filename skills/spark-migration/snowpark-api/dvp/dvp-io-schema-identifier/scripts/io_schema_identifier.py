"""
DVP IO Schema Identifier — CLI wrapper.

Uses the DataIODetector borrowed from warp-suite to extract data inputs/outputs
with schema inference from the ASG JSON file.

Output: data_io_schema.json with IO metadata + inferred column schemas.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_SKILL_LIB = Path(__file__).resolve().parent.parent / "warp"
_SHARED_LIB = Path(__file__).resolve().parent.parent.parent / "dvp-orchestrator"
sys.path.insert(0, str(_SHARED_LIB))
sys.path.insert(0, str(_SKILL_LIB))

from data_io import DataIODetector

logger = logging.getLogger("dvp-io-schema-identifier")


def resolve_asg_path(results_dir: Path) -> Path | None:
    """Look for the ASG file (*_asg.json) in 04-results/."""
    matches = sorted(results_dir.glob("*_asg.json"))
    if matches:
        return matches[0]
    return None


def run(asg_path: Path, output_path: Path) -> int:
    """Main detection logic. Returns 0 on success, 1 on error."""
    if not asg_path.exists():
        logger.error("ASG file not found: %s", asg_path)
        return 1

    detector = DataIODetector()
    results = detector.detect_from_file(str(asg_path))

    if not results:
        logger.warning("No data I/O entries detected from ASG")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = detector.to_list()
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    inputs = [d for d in data if d.get("role") == "input"]
    outputs = [d for d in data if d.get("role") == "output"]

    print("\nIO Schema Identification Summary")
    print("=" * 55)
    print(f"\nASG: {asg_path}")
    print(f"\nInputs ({len(inputs)}):")
    for entry in inputs:
        cols = entry.get("columns", [])
        print(f"  {entry['name']:<30s} {entry['type']:<8s} {len(cols)} columns")

    print(f"\nOutputs ({len(outputs)}):")
    for entry in outputs:
        cols = entry.get("columns", [])
        print(f"  {entry['name']:<30s} {entry['type']:<8s} {len(cols)} columns")

    schema_count = sum(1 for d in data if d.get("columns"))
    print(f"\nSchema coverage: {schema_count}/{len(data)} entries have columns defined")

    issues = detector.issues
    if issues.total > 0:
        print(f"\nDiagnostic issues: {issues.total}")
        for sev, count in issues.by_severity.items():
            if count:
                print(f"  {sev}: {count}")

    print(f"\nSaved to {output_path}\n")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Extract data I/O with schema from ASG JSON"
    )
    parser.add_argument(
        "--asg",
        type=Path,
        default=None,
        help="Path to ASG JSON (XX_asg.json). Auto-detected from --results-dir if omitted.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Path to dvp/04-results/ directory (for auto-detecting ASG)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write data_io_schema.json",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(name)s %(levelname)s: %(message)s",
    )

    asg_path = args.asg
    if not asg_path and args.results_dir:
        asg_path = resolve_asg_path(args.results_dir)
        if asg_path:
            logger.info("Auto-detected ASG: %s", asg_path)

    if not asg_path:
        logger.error("No ASG file specified or found. Use --asg or --results-dir.")
        sys.exit(1)

    sys.exit(run(asg_path=asg_path, output_path=args.output))


if __name__ == "__main__":
    main()
