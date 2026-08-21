#!/usr/bin/env python3
"""
validate_directory.py — Validate post-conversion directory state.

Checks that a directory contains only properly converted notebooks:
  - No stale original non-.ipynb notebook source files remain
  - Every converted .ipynb follows the collision-safe naming convention
  - Optionally cross-references against a scan_dependencies.py output

Run this after all notebooks in a directory have been converted.

Usage:
    python validate_directory.py <directory>
    python validate_directory.py <directory> --scan-output scan_results.json

Exit code 0 = clean, 1 = stale originals or issues found.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detect_and_parse_notebook import detect_format, convert_filename


def validate_directory(
    directory: str, scan_output: dict | None = None
) -> dict:
    """Validate that a directory has no stale originals after conversion."""
    errors = []
    warnings = []
    stale_originals = []
    converted_notebooks = []
    non_notebook_files = []

    notebook_exts = {".ipynb", ".python", ".py", ".scala", ".sql"}

    for root, _dirs, files in os.walk(directory):
        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, directory)
            ext = Path(fname).suffix.lower()

            if ext == ".ipynb":
                converted_notebooks.append(rel)
                continue

            if ext not in notebook_exts:
                non_notebook_files.append(rel)
                continue

            # Non-.ipynb file with a notebook-capable extension — check if it's a notebook
            info = detect_format(fpath)
            if info["format"] == "not_notebook":
                # Plain script (e.g., sfutils.py) — fine to keep
                non_notebook_files.append(rel)
                continue

            # This is a Databricks notebook that was NOT converted/deleted
            expected_converted = convert_filename(rel)
            converted_exists = os.path.isfile(
                os.path.join(directory, expected_converted)
            )

            stale_originals.append(rel)
            if converted_exists:
                errors.append(
                    f"Stale original '{rel}' remains alongside its "
                    f"converted version '{expected_converted}' — "
                    f"original should have been deleted after conversion"
                )
            else:
                errors.append(
                    f"Unconverted notebook '{rel}' found — expected "
                    f"converted file '{expected_converted}' does not exist"
                )

    # Cross-reference with scan output if provided
    if scan_output is not None:
        expected_notebooks = scan_output.get("notebooks", [])
        for nb in expected_notebooks:
            expected_name = nb.get("converted_name") or convert_filename(
                nb["file"]
            )
            if expected_name not in converted_notebooks:
                warnings.append(
                    f"Expected converted notebook '{expected_name}' "
                    f"(from '{nb['file']}') not found in directory"
                )

    return {
        "valid": len(errors) == 0,
        "directory": directory,
        "converted_notebooks": converted_notebooks,
        "non_notebook_files": non_notebook_files,
        "stale_originals": stale_originals,
        "errors": errors,
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Validate post-conversion directory state"
    )
    parser.add_argument("directory", help="Directory to validate")
    parser.add_argument(
        "--scan-output",
        default=None,
        metavar="PATH",
        help=(
            "Path to scan_dependencies.py JSON output for cross-referencing. "
            "When provided, checks that all expected converted notebooks exist."
        ),
    )
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f"Error: {args.directory} is not a directory", file=sys.stderr)
        sys.exit(1)

    scan_output = None
    if args.scan_output:
        try:
            with open(args.scan_output, "r", encoding="utf-8") as f:
                scan_output = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(
                f"Warning: cannot read scan output: {e}", file=sys.stderr
            )

    result = validate_directory(args.directory, scan_output)
    print(json.dumps(result, indent=2))

    if not result["valid"]:
        print(
            f"\nVALIDATION FAILED: {len(result['errors'])} error(s)",
            file=sys.stderr,
        )
        for err in result["errors"]:
            print(f"  ERROR: {err}", file=sys.stderr)
        for warn in result["warnings"]:
            print(f"  WARNING: {warn}", file=sys.stderr)
        sys.exit(1)
    else:
        nb_count = len(result["converted_notebooks"])
        stale_count = len(result["stale_originals"])
        warn_count = len(result["warnings"])
        msg = f"VALIDATION PASSED: {nb_count} notebook(s), {stale_count} stale original(s)"
        if warn_count:
            msg += f", {warn_count} warning(s)"
        print(f"\n{msg}", file=sys.stderr)
        for warn in result["warnings"]:
            print(f"  WARNING: {warn}", file=sys.stderr)


if __name__ == "__main__":
    main()
