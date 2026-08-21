#!/usr/bin/env python3
"""
validate_notebook.py — Validate a converted Snowflake Workspace notebook.

Checks that the output notebook meets migration quality criteria:
  - Valid nbformat 4.x JSON structure
  - All SQL cells have resultVariableName metadata
  - No orphaned _sqldf references
  - All %run paths end in .ipynb
  - %run paths match expected collision-safe names (when --run-targets provided)
  - Migration summary cell is present
  - Cell count matches expected (original cell count if provided)

With --finalize, also validates the naming convention and deletes the original
non-.ipynb source file after successful validation.

Usage:
    python validate_notebook.py <notebook.ipynb>
    python validate_notebook.py <notebook.ipynb> --expected-cells 15
    python validate_notebook.py <notebook.ipynb> --run-targets ./config.py.ipynb --run-targets ./utils.py.ipynb
    python validate_notebook.py <notebook.ipynb> --finalize <original_source_file>

Exit code 0 = all checks pass, 1 = validation failures found.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detect_and_parse_notebook import convert_filename


def load_notebook(path: str) -> dict:
    """Load and return the notebook JSON. Raises on invalid JSON."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_nbformat(nb: dict) -> list[str]:
    """Verify the notebook has valid nbformat 4.x structure."""
    errors = []
    version = nb.get("nbformat")
    if version is None:
        errors.append("Missing 'nbformat' key")
    elif version < 4:
        errors.append(f"nbformat version {version} is too old (expected 4+)")

    if "cells" not in nb:
        errors.append("Missing 'cells' array")
    elif not isinstance(nb["cells"], list):
        errors.append("'cells' is not a list")

    metadata = nb.get("metadata", {})
    if not isinstance(metadata, dict):
        errors.append("'metadata' is not a dict")

    return errors


def check_sql_cells(nb: dict) -> list[str]:
    """Verify all SQL cells have resultVariableName metadata."""
    errors = []
    for i, cell in enumerate(nb.get("cells", [])):
        source = _get_source(cell)
        cell_meta = cell.get("metadata", {})
        is_sql = (
            cell.get("cell_type") == "code"
            and cell_meta.get("language") == "sql"
        ) or source.lstrip().startswith("%%sql")

        if is_sql and not cell_meta.get("resultVariableName"):
            errors.append(f"Cell {i}: SQL cell missing resultVariableName metadata")
    return errors


def check_sqldf_references(nb: dict) -> list[str]:
    """Check for orphaned _sqldf references in Python cells."""
    errors = []
    for i, cell in enumerate(nb.get("cells", [])):
        source = _get_source(cell)
        cell_meta = cell.get("metadata", {})
        is_sql = cell_meta.get("language") == "sql"
        if is_sql:
            continue
        if cell.get("cell_type") == "markdown":
            continue
        if re.search(r'\b_sqldf\b', source):
            errors.append(f"Cell {i}: contains orphaned '_sqldf' reference")
    return errors


def check_run_paths(nb: dict, expected_run_targets: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Verify all %run paths end in .ipynb.

    If expected_run_targets is provided, also warn about %run paths that
    are not in the expected set (post-conversion collision-safe filenames).
    Returns (errors, warnings).
    """
    errors = []
    warnings = []
    expected_normalized = None
    if expected_run_targets is not None:
        expected_normalized = {t.removeprefix("./") for t in expected_run_targets}
    for i, cell in enumerate(nb.get("cells", [])):
        if cell.get("cell_type") == "markdown":
            continue
        source = _get_source(cell)
        for line in source.split("\n"):
            stripped = line.strip()
            if not stripped.startswith("%run "):
                continue
            path_part = stripped[5:].strip()
            if not path_part.endswith(".ipynb"):
                errors.append(
                    f"Cell {i}: %run path does not end in .ipynb: '{path_part}'"
                )
            elif expected_normalized is not None:
                normalized = path_part.removeprefix("./")
                if normalized not in expected_normalized:
                    warnings.append(
                        f"Cell {i}: %run path '{path_part}' is not in expected "
                        f"run targets — may not follow collision-safe naming convention"
                    )
    return errors, warnings


def check_migration_summary(nb: dict) -> list[str]:
    """Check that a migration summary markdown cell exists at the end."""
    cells = nb.get("cells", [])
    if not cells:
        return ["Notebook has no cells"]

    for cell in reversed(cells[-3:]):
        source = _get_source(cell)
        if cell.get("cell_type") == "markdown" and "migration" in source.lower():
            return []

    return ["No migration summary markdown cell found near the end of the notebook"]


def check_cell_count(nb: dict, expected: int) -> list[str]:
    """Verify cell count meets expectation (original cells + setup + summary)."""
    actual = len(nb.get("cells", []))
    if actual < expected:
        return [
            f"Cell count {actual} is less than expected minimum {expected} "
            f"(original cells may be missing)"
        ]
    return []


def _get_source(cell: dict) -> str:
    """Extract source string from a cell (handles list or string format)."""
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(source)
    return source


def _finalize_original(
    path: str, finalize: str, errors: list[str], is_valid: bool
) -> dict:
    """Check naming convention and delete original source file.

    Uses guard clauses to keep the logic flat. Appends to ``errors``
    in-place when the naming convention is violated or deletion fails.
    """
    info: dict = {"original": finalize, "deleted": False}

    if not is_valid:
        info["skipped"] = "validation failed — original not deleted"
        return info

    if finalize.endswith(".ipynb"):
        info["skipped"] = "original is already .ipynb — no deletion needed"
        return info

    expected_name = convert_filename(os.path.basename(finalize))
    actual_name = os.path.basename(path)
    if actual_name != expected_name:
        errors.append(
            f"Naming convention violation: expected '{expected_name}' "
            f"but got '{actual_name}'"
        )
        return info

    if not os.path.isfile(finalize):
        info["skipped"] = "original already removed"
        return info

    try:
        os.remove(finalize)
        info["deleted"] = True
    except OSError as e:
        errors.append(f"Failed to delete original: {e}")

    return info


def validate(
    path: str,
    expected_cells: int | None = None,
    run_targets: list[str] | None = None,
    finalize: str | None = None,
) -> dict:
    """Run all validation checks and return results.

    If finalize is provided (path to the original source file), also:
    - Verify the output filename follows the collision-safe naming convention
    - Delete the original non-.ipynb source file after successful validation
    """
    try:
        nb = load_notebook(path)
    except json.JSONDecodeError as e:
        return {
            "valid": False,
            "file": path,
            "errors": [f"Invalid JSON: {e}"],
            "warnings": [],
        }
    except OSError as e:
        return {
            "valid": False,
            "file": path,
            "errors": [f"Cannot read file: {e}"],
            "warnings": [],
        }

    errors = []
    warnings = []

    errors.extend(check_nbformat(nb))
    if errors:
        return {"valid": False, "file": path, "errors": errors, "warnings": warnings}

    errors.extend(check_sql_cells(nb))
    errors.extend(check_sqldf_references(nb))
    run_errors, run_warnings = check_run_paths(nb, run_targets)
    errors.extend(run_errors)
    warnings.extend(run_warnings)

    summary_issues = check_migration_summary(nb)
    warnings.extend(summary_issues)

    if expected_cells is not None:
        errors.extend(check_cell_count(nb, expected_cells))

    cells = nb.get("cells", [])
    cell_types = Counter(cell.get("cell_type", "unknown") for cell in cells)

    result = {
        "valid": len(errors) == 0,
        "file": path,
        "cell_count": len(cells),
        "cell_types": dict(cell_types),
        "errors": errors,
        "warnings": warnings,
    }

    if finalize is not None:
        result["finalize"] = _finalize_original(path, finalize, errors, result["valid"])
        result["valid"] = len(errors) == 0

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Validate a converted Snowflake Workspace notebook"
    )
    parser.add_argument("notebook", help="Path to the .ipynb file to validate")
    parser.add_argument(
        "--expected-cells",
        type=int,
        default=None,
        help="Minimum expected cell count (original notebook cell count)",
    )
    parser.add_argument(
        "--run-targets",
        action="append",
        dest="run_targets",
        default=None,
        metavar="PATH",
        help=(
            "Expected %%run target path (post-conversion, e.g. ./config.py.ipynb). "
            "Can be repeated. When provided, %%run paths not in this list are warned."
        ),
    )
    parser.add_argument(
        "--finalize",
        default=None,
        metavar="ORIGINAL",
        help=(
            "Path to the original source file. If validation passes, checks the "
            "naming convention and deletes the original non-.ipynb file. "
            "E.g. --finalize my_notebook.python"
        ),
    )
    args = parser.parse_args()

    result = validate(args.notebook, args.expected_cells, args.run_targets, args.finalize)
    print(json.dumps(result, indent=2))

    if not result["valid"]:
        print(f"\nVALIDATION FAILED: {len(result['errors'])} error(s)", file=sys.stderr)
        for err in result["errors"]:
            print(f"  ERROR: {err}", file=sys.stderr)
        for warn in result["warnings"]:
            print(f"  WARNING: {warn}", file=sys.stderr)
        sys.exit(1)
    else:
        warn_count = len(result.get("warnings", []))
        msg = "VALIDATION PASSED"
        if warn_count:
            msg += f" with {warn_count} warning(s)"
        print(f"\n{msg}", file=sys.stderr)
        for warn in result["warnings"]:
            print(f"  WARNING: {warn}", file=sys.stderr)


if __name__ == "__main__":
    main()
