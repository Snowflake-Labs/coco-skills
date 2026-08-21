#!/usr/bin/env python3
# SNOW-3347465: Cross-platform wrapper that ensures report generation always
# runs, even if the migration agent is interrupted mid-workflow.
#
# This script wraps the SCOS migration skill execution and guarantees that
# generate_scos_reports.py runs as a final step whenever analysis.json exists.
# It is the portable replacement for the previous run_scos_migration.sh and
# works identically on macOS, Linux, and Windows.
#
# Usage (via `uv run` so no activation / chmod / shebang handling is required):
#
#     uv run --project <SKILL_DIRECTORY> \
#         python <SKILL_DIRECTORY>/scripts/run_scos_migration.py \
#         --analysis <path> --source-dir <path> --output-dir <path> \
#         [--migrated-dir <path>] [--project-name <name>] [--email <email>] \
#         [--company <company>] [--language <lang>]
#
# Environment variables (optional — resolved from the script location when unset):
#     SKILL_DIR — Path to the snowpark-connect skill directory (contains
#                 pyproject.toml). Default: the parent directory of this script.
#
# Exit codes:
#     0 — Reports generated successfully.
#     1 — No analysis.json found (nothing to generate).
#     2 — Report generation failed.
"""Cross-platform orchestrator for the SCOS migration reporter."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _resolve_skill_dir() -> Path:
    """Return the skill directory, preferring SKILL_DIR env var when set.

    Falls back to the parent of this script's directory (i.e. the
    ``snowpark-connect`` skill root that owns ``pyproject.toml``).
    """
    override = os.environ.get("SKILL_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def _parse_args(argv: list[str]) -> tuple[str, list[str]]:
    """Extract --analysis (for the existence check) and keep all other args
    intact to pass through to ``generate_scos_reports.py``.

    Returns ``(analysis_path, forwarded_args)`` where ``forwarded_args``
    already includes the resolved ``--analysis <path>`` pair so the child
    script always receives an explicit value.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--analysis", default=None)
    known, extras = parser.parse_known_args(argv)

    analysis = known.analysis or "analysis.json"
    forwarded: list[str] = ["--analysis", analysis, *extras]
    return analysis, forwarded


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    analysis_str, forwarded = _parse_args(argv)

    analysis_path = Path(analysis_str).expanduser()
    if not analysis_path.is_file():
        print(
            f"WARNING: No analysis.json found at '{analysis_path}'. "
            "Reports cannot be generated.",
            file=sys.stderr,
        )
        return 1

    skill_dir = _resolve_skill_dir()
    reporter = skill_dir / "scripts" / "generate_scos_reports.py"

    print(f"Generating reports from {analysis_path}...")
    completed = subprocess.run(
        [sys.executable, str(reporter), *forwarded],
        check=False,
    )
    if completed.returncode == 0:
        print("Reports generated successfully.")
        return 0

    print(
        f"WARNING: Report generation failed with exit code {completed.returncode}.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
