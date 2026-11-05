#!/usr/bin/env python3
"""Regenerate ENGINE_MANIFEST.json and TEMPLATES_MANIFEST.json. Stdlib only.

Each manifest maps a POSIX path (relative to the manifest's root directory) to
the SHA-256 of the file's bytes. ``engine_version`` in the engine manifest is
taken from ``engine/ENGINE_VERSION``. Skipped: the manifests themselves,
``__pycache__``/``*.pyc``, hidden files, and the dev-only ``engine/tests/``.

Run from anywhere:  python3 engine/scripts/update_manifests.py [--check]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1]
SKILL_DIR = ENGINE_DIR.parent
TEMPLATES_DIR = SKILL_DIR / "templates"
ENGINE_MANIFEST = ENGINE_DIR / "ENGINE_MANIFEST.json"
TEMPLATES_MANIFEST = TEMPLATES_DIR / "TEMPLATES_MANIFEST.json"
EXCLUDED_TOP_DIRS = {ENGINE_DIR: {"tests"}, TEMPLATES_DIR: set()}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_files(root: Path, manifest: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    excluded = EXCLUDED_TOP_DIRS.get(root, set())
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == manifest:
            continue
        rel = path.relative_to(root)
        if rel.parts[0] in excluded or "__pycache__" in rel.parts or path.suffix == ".pyc":
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        files[rel.as_posix()] = sha256(path)
    return files


def build_manifests() -> dict[Path, dict]:
    engine_version = (ENGINE_DIR / "ENGINE_VERSION").read_text(encoding="utf-8").strip()
    return {
        ENGINE_MANIFEST: {
            "engine_version": engine_version,
            "files": manifest_files(ENGINE_DIR, ENGINE_MANIFEST),
        },
        TEMPLATES_MANIFEST: {"files": manifest_files(TEMPLATES_DIR, TEMPLATES_MANIFEST)},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Report drift; write nothing.")
    args = parser.parse_args()

    drift = False
    for path, data in build_manifests().items():
        text = json.dumps(data, indent=2) + "\n"
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current == text:
            print(f"up to date: {path.relative_to(SKILL_DIR)}")
            continue
        drift = True
        if args.check:
            print(f"out of date: {path.relative_to(SKILL_DIR)}")
        else:
            path.write_text(text, encoding="utf-8")
            print(f"wrote: {path.relative_to(SKILL_DIR)} ({len(data['files'])} files)")
    return 1 if (args.check and drift) else 0


if __name__ == "__main__":
    raise SystemExit(main())
