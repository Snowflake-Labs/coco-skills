#!/usr/bin/env python3
"""Portable replacement for the ``date`` + ``mkdir -p`` + ``cp -r`` + ``.dbc``
unpack shell block in the SCOS migration SKILL.md files.

Runs identically on macOS, Linux, and Windows under ``uv run``.

Usage:

    uv run --project <SKILL_DIRECTORY> \
        python <SKILL_DIRECTORY>/scripts/prepare_conversion_dirs.py \
        --output-root <OUTPUT_ROOT> \
        --source <SOURCE_FILE_OR_DIR> \
        [--unpack-dbc] [--json]

Creates ``<OUTPUT_ROOT>/Conversion-SCOS-<TIMESTAMP>/{Output,Reports,Logs}`` and
copies the source tree (or single file) into ``.../Output/``. Optionally
extracts any ``*.dbc`` archives found under ``Output/`` into sibling
``_unpacked/`` directories using ``zipfile`` (no shell loop required).

Prints the resolved paths as ``KEY=value`` lines (or JSON with ``--json``) so
callers can wire them into ``migration_state.json``::

    CONVERSION=/abs/path/to/Conversion-SCOS-05-04-2026T14-22-08
    OUTPUT_DIR=/abs/path/to/Conversion-SCOS-.../Output
    REPORTS_DIR=/abs/path/to/Conversion-SCOS-.../Reports
    LOGS_DIR=/abs/path/to/Conversion-SCOS-.../Logs
    TIMESTAMP=05-04-2026T14-22-08
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path


def _timestamp(fmt: str = "%m-%d-%YT%H-%M-%S") -> str:
    return datetime.now().strftime(fmt)


# Directories never worth copying into the conversion: version control, IDE
# metadata, and build output. Pruned at every depth during the source copy so
# the manifest and reports reflect the workload, not artifacts.
_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn",            # VCS
    ".idea", ".vscode", ".metals", ".bloop", ".bsp",  # IDE / tooling
    "target", "build", "out",         # JVM build output
    ".gradle", "__pycache__", "node_modules",
}


def _make_ignore_excluded(extra: set[str] | None = None):
    """Return a ``shutil.copytree`` ignore callback that prunes excluded names."""
    combined = _EXCLUDE_DIRS | (extra or set())

    def _ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in combined}

    return _ignore


def _ignore_excluded(_dir: str, names: list[str]) -> set[str]:
    """``shutil.copytree`` ignore callback: prune excluded dir names at any depth."""
    return {n for n in names if n in _EXCLUDE_DIRS}


def _copy_source(src: Path, dst_output_dir: Path, extra_excludes: set[str] | None = None) -> None:
    """Copy ``src`` (file or directory) into ``dst_output_dir`` portably.

    Mirrors the behaviour of the previous ``cp -r $SRC/* <OUTPUT>/`` and
    ``cp $SRC <OUTPUT>/`` shell commands but works on Windows without a
    POSIX ``cp``. VCS / IDE / build-output directories (``.git``, ``.idea``,
    ``target`` …) are pruned at every depth so the conversion contains the
    workload, not build artifacts and editor metadata.

    ``extra_excludes`` is an optional set of top-level directory names to skip
    in addition to the built-in ``_EXCLUDE_DIRS`` (useful when the output root
    lives inside the source tree and must not be copied into itself).
    """
    combined_exclude = _EXCLUDE_DIRS | (extra_excludes or set())
    ignore_fn = _make_ignore_excluded(extra_excludes)
    if src.is_dir():
        # Copy every top-level entry of src into dst_output_dir, merging into
        # any existing tree. Matches ``cp -r src/* dst/`` semantics.
        for entry in src.iterdir():
            if entry.is_dir() and entry.name in combined_exclude:
                continue
            target = dst_output_dir / entry.name
            if entry.is_dir():
                shutil.copytree(
                    entry, target, dirs_exist_ok=True, ignore=ignore_fn
                )
            else:
                shutil.copy2(entry, target)
    elif src.is_file():
        shutil.copy2(src, dst_output_dir / src.name)
    else:
        raise FileNotFoundError(f"Source path not found: {src}")


def _unpack_dbc_archives(output_dir: Path) -> list[Path]:
    """Extract every ``*.dbc`` archive under ``output_dir`` into a sibling
    ``<name>_unpacked/`` directory using the standard ``zipfile`` module.
    Returns the list of unpacked directories.
    """
    unpacked: list[Path] = []
    for archive in output_dir.rglob("*.dbc"):
        if not archive.is_file():
            continue
        target = archive.with_name(archive.stem + "_unpacked")
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(target)
        unpacked.append(target)
    return unpacked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        required=True,
        help="Parent directory that will hold the timestamped "
        "`Conversion-SCOS-<timestamp>/` folder.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Optional. File or directory whose contents are copied into "
        "`<CONVERSION>/Output/`. Matches `cp -r $SRC/* dst/` for dirs.",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="Optional explicit timestamp string. Defaults to now in "
        "`%%m-%%d-%%YT%%H-%%M-%%S` format.",
    )
    parser.add_argument(
        "--unpack-dbc",
        action="store_true",
        help="Extract every *.dbc archive found under Output/ into sibling "
        "<name>_unpacked/ directories.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the resolved paths as a JSON object instead of `KEY=val`.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="DIR_NAME",
        help="Top-level directory name(s) inside --source to skip during the "
        "copy. May be repeated. Useful when the output root lives inside the "
        "source tree and must not be copied into itself.",
    )
    args = parser.parse_args(argv)

    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    ts = args.timestamp or _timestamp()
    conversion = output_root / f"Conversion-SCOS-{ts}"
    output_dir = conversion / "Output"
    reports_dir = conversion / "Reports"
    logs_dir = conversion / "Logs"
    for p in (output_dir, reports_dir, logs_dir):
        p.mkdir(parents=True, exist_ok=True)

    if args.source:
        src = Path(args.source).expanduser().resolve()
        extra_excludes = set(args.exclude) if args.exclude else None
        # Auto-detect: if the output root is a direct child of src, exclude it
        # to prevent the copy from recursing into its own destination.
        if output_root.parent == src:
            extra_excludes = (extra_excludes or set()) | {output_root.name}
        _copy_source(src, output_dir, extra_excludes=extra_excludes)

    unpacked: list[Path] = []
    if args.unpack_dbc:
        unpacked = _unpack_dbc_archives(output_dir)

    payload = {
        "CONVERSION": str(conversion),
        "OUTPUT_DIR": str(output_dir),
        "REPORTS_DIR": str(reports_dir),
        "LOGS_DIR": str(logs_dir),
        "TIMESTAMP": ts,
        "UNPACKED_DBC_DIRS": [str(p) for p in unpacked],
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for key, value in payload.items():
            if isinstance(value, list):
                print(f"{key}={','.join(value)}")
            else:
                print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
