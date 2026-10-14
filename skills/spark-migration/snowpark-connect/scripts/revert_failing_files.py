#!/usr/bin/env python3
"""Portable replacement for the bash ``find -print0`` / ``py_compile`` / ``git
show`` revert block used in the Phase 2 compilation gate of the SCOS migration
skill.

Runs identically on macOS, Linux, and Windows under ``uv run``.

Usage::

    uv run --project <SKILL_DIRECTORY> \
        python <SKILL_DIRECTORY>/scripts/revert_failing_files.py \
        --migrated <MIGRATED_DIR> \
        --phase-tag phase-1-complete \
        [--json]

Behaviour — identical to the previous shell block::

    find <MIGRATED> \\( -path '*/__pycache__' -o -path '*/.git' \\) -prune \\
        -o -name "*.py" -type f -print0 \\
        | while IFS= read -r -d '' f; do
            if ! python3 -m py_compile "$f" 2>/dev/null; then
              echo "COMPILE_FAIL: $f"
              FAIL_COUNT=$((FAIL_COUNT + 1))
              git show "phase-1-complete":"$(git ls-files --full-name "$f")" > "$f" 2>/dev/null || true
            fi
          done
    find <MIGRATED> -type d -name '__pycache__' -exec rm -rf {} +

The Python version uses :mod:`pathlib.Path.rglob` (handles filenames with
spaces natively), :mod:`py_compile`, and :mod:`subprocess` for the
``git show``/``git ls-files`` revert step. After the compile sweep it removes
every ``__pycache__`` directory under ``<MIGRATED>`` via
:func:`shutil.rmtree`.

Exit code is the final ``FAIL_COUNT`` capped at 255 (``0`` = all files
compile), which keeps the outer Phase 2 hard gate behaviour unchanged.
"""

from __future__ import annotations

import argparse
import json
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

_PRUNE_DIR_NAMES = {"__pycache__", ".git"}


def _iter_python_files(root: Path):
    """Yield every ``*.py`` file under ``root`` skipping ``__pycache__`` and
    ``.git`` trees. Handles filenames with whitespace natively (unlike
    unquoted ``$(find ...)``).
    """
    for path in root.rglob("*.py"):
        if not path.is_file():
            continue
        if any(part in _PRUNE_DIR_NAMES for part in path.parts):
            continue
        yield path


def _git_revert(migrated: Path, file_path: Path, phase_tag: str) -> bool:
    """Replace ``file_path`` with its blob at ``phase_tag`` using ``git show``.

    Equivalent to::

        git show "<tag>":"$(git ls-files --full-name <file>)" > <file>

    Returns True when the revert succeeded. Errors are swallowed to mirror
    the trailing ``|| true`` in the shell version — downstream Phase 2 logic
    treats the file as still-failing if the revert did not land.
    """
    try:
        full_name = subprocess.run(
            ["git", "ls-files", "--full-name", str(file_path)],
            cwd=str(migrated),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not full_name:
            return False
        show = subprocess.run(
            ["git", "show", f"{phase_tag}:{full_name}"],
            cwd=str(migrated),
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    try:
        file_path.write_bytes(show.stdout)
    except OSError:
        return False
    return True


def _purge_pycache(root: Path) -> int:
    removed = 0
    for cache_dir in root.rglob("__pycache__"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)
            removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migrated",
        required=True,
        help="Path to the <MIGRATED> directory that contains the Phase 2 "
        "code and its git history.",
    )
    parser.add_argument(
        "--phase-tag",
        default="phase-1-complete",
        help="Git ref to revert failing files back to. Default: "
        "phase-1-complete.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary instead of text lines.",
    )
    args = parser.parse_args(argv)

    migrated = Path(args.migrated).expanduser().resolve()
    if not migrated.is_dir():
        print(f"ERROR: --migrated {migrated} is not a directory", file=sys.stderr)
        return 255

    failures: list[str] = []
    reverted: list[str] = []
    for py_file in _iter_python_files(migrated):
        try:
            py_compile.compile(str(py_file), doraise=True)
        except py_compile.PyCompileError:
            rel = str(py_file.relative_to(migrated))
            failures.append(rel)
            if _git_revert(migrated, py_file, args.phase_tag):
                reverted.append(rel)
            if not args.json:
                print(f"COMPILE_FAIL: {py_file}")

    pycache_removed = _purge_pycache(migrated)

    if args.json:
        print(
            json.dumps(
                {
                    "fail_count": len(failures),
                    "failures": failures,
                    "reverted": reverted,
                    "pycache_dirs_removed": pycache_removed,
                },
                indent=2,
            )
        )
    else:
        print(f"FAIL_COUNT={len(failures)}")
        print(f"REVERTED={len(reverted)}")
        print(f"PYCACHE_REMOVED={pycache_removed}")

    return min(len(failures), 255)


if __name__ == "__main__":
    raise SystemExit(main())
