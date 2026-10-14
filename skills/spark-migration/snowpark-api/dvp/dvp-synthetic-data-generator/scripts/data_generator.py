"""
DVP Synthetic Data Generator — CLI wrapper.

Uses the SyntheticDataGenerator borrowed from warp-suite to generate
constraint-aware, join-aware test data from data_io_schema.json and optionally
the ASG JSON for column constraints and relationships.

Output: one CSV per input entry in data_io_schema.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

# Add shared warp_core (dvp-orchestrator/) and skill warp/ to sys.path for warp-suite imports
_SKILL_LIB = Path(__file__).resolve().parent.parent / "warp"
_SHARED_LIB = Path(__file__).resolve().parent.parent.parent / "dvp-orchestrator"
sys.path.insert(0, str(_SHARED_LIB))
sys.path.insert(0, str(_SKILL_LIB))

from synthetic_data import SyntheticDataGenerator, GenerationStrategy

logger = logging.getLogger("dvp-synthetic-data-generator")

DEFAULT_ROWS = 10


def resolve_asg_path(results_dir: Path) -> Path | None:
    """Look for the ASG file (*_asg.json) in 04-results/."""
    matches = sorted(results_dir.glob("*_asg.json"))
    if matches:
        return matches[0]
    return None


def relocate_csvs(synthetic_dir: Path, target_dir: Path) -> list[Path]:
    """Move CSVs from synthetic_data/ subfolder to the target flat directory.

    The warp-suite generator writes to output_dir/synthetic_data/*.csv,
    but DVP expects them directly in 04-results/synthetic_data/*.csv.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for f in sorted(synthetic_dir.iterdir()):
        if not f.is_file():
            continue
        dest = target_dir / f.name.lower()
        shutil.move(str(f), str(dest))
        moved.append(dest)

    if synthetic_dir.exists() and not list(synthetic_dir.iterdir()):
        synthetic_dir.rmdir()

    return moved


def run(data_io_path: Path, output_dir: Path, rows: int, asg_path: Path | None, use_asg: bool = True) -> int:
    """Main generation logic. Returns 0 on success, 1 on error."""
    if not data_io_path.exists():
        logger.error("data_io_schema.json not found: %s", data_io_path)
        return 1

    if asg_path and not asg_path.exists():
        logger.warning("ASG file not found: %s — proceeding without constraints", asg_path)
        asg_path = None

    if use_asg and not asg_path:
        results_dir = data_io_path.parent
        asg_path = resolve_asg_path(results_dir)
        if asg_path:
            logger.info("Auto-detected ASG: %s", asg_path)

    gen = SyntheticDataGenerator.from_files(
        data_io_path=str(data_io_path),
        asg_path=str(asg_path) if asg_path else None,
    )

    analysis = gen.get_analysis()
    if not analysis["tables"]:
        logger.error("No input tables found in data_io_schema.json")
        return 1

    logger.info("Tables to generate: %s", list(analysis["tables"].keys()))
    if analysis["join_keys"]:
        logger.info("Join keys detected: %s", analysis["join_keys"])
    if analysis["constraints_count"]:
        logger.info("Constraints from ASG: %d", analysis["constraints_count"])
    if analysis["relationships_count"]:
        logger.info("Relationships from ASG: %d", analysis["relationships_count"])

    temp_output = output_dir.parent
    created = gen.write_csv_files(
        output_dir=temp_output,
        strategy=GenerationStrategy.JOIN_AWARE,
        rows_per_table=rows,
    )

    synthetic_subdir = temp_output / "synthetic_data"
    if synthetic_subdir.exists():
        final_files = relocate_csvs(synthetic_subdir, output_dir)
    else:
        final_files = created

    # Print summary
    print("\nSynthetic Data Generation Summary")
    print("=" * 55)

    mode = "ASG-aware (constraints + relationships)" if asg_path else "Schema-only (no ASG)"
    print(f"\nMode: {mode}")

    if asg_path:
        print(f"ASG:  {asg_path}")
    print(f"\nGenerated {len(final_files)} input files in {output_dir}/:\n")

    with open(data_io_path) as f:
        data_io = json.load(f)
    input_entries = {e["name"]: e for e in data_io if e.get("role") == "input"}

    for filepath in final_files:
        with open(filepath) as f:
            line_count = sum(1 for _ in f) - 1
        stem = filepath.stem
        entry_name = next(
            (n for n in input_entries if n.lower().replace(".csv", "") == stem),
            stem,
        )
        num_cols = len(input_entries.get(entry_name, {}).get("columns", []))
        print(f"  {filepath.name:<30s} {line_count:>3d} rows   {num_cols} columns")

    if analysis["join_keys"]:
        print("\nJoin keys (shared value pools):")
        for key in analysis["join_keys"]:
            print(f"  {key}")

    if analysis.get("branches"):
        print("\nBranch values (from ASG constraints):")
        for col, vals in analysis["branches"].items():
            print(f"  {col}: {vals}")

    issues = gen.issues
    if issues.total > 0:
        print(f"\nDiagnostic issues: {issues.total}")
        for sev, count in issues.by_severity.items():
            if count:
                print(f"  {sev}: {count}")

    print()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic test data from data_io_schema.json (+ optional ASG)"
    )
    parser.add_argument(
        "--data-io",
        required=True,
        help="Path to data_io_schema.json",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write CSV files (e.g., dvp/04-results/synthetic_data/)",
    )
    parser.add_argument(
        "--asg",
        default=None,
        help="Path to ASG JSON (optional; auto-detected from data_io_schema.json parent dir)",
    )
    parser.add_argument(
        "--no-asg",
        action="store_true",
        help="Disable ASG usage (skip auto-detect)",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=DEFAULT_ROWS,
        help=f"Rows per table (default: {DEFAULT_ROWS})",
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

    sys.exit(run(
        data_io_path=Path(args.data_io),
        output_dir=Path(args.output_dir),
        rows=args.rows,
        asg_path=Path(args.asg) if args.asg else None,
        use_asg=(not args.no_asg),
    ))


if __name__ == "__main__":
    main()
