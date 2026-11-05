"""Shared helpers for the finance_hydrator engine tests (stdlib only)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TESTDATA = TESTS_DIR / "testdata"
ENGINE_DIR = TESTS_DIR.parent
SCRIPTS = ENGINE_DIR / "scripts"
SKILL_DIR = ENGINE_DIR.parent
PLUGIN_DIR = SKILL_DIR.parents[1]
CATALOGS = sorted((SKILL_DIR / "catalog").glob("*.json"))

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def catalog_args() -> list[str]:
    args: list[str] = []
    for path in CATALOGS:
        args += ["--catalog", str(path)]
    return args


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        capture_output=True,
        text=True,
        cwd=SKILL_DIR,
    )


def run_json(script: str, *args: str, ok: bool = True) -> dict:
    proc = run(script, *args)
    if ok and proc.returncode != 0:
        raise AssertionError(f"{script} exited {proc.returncode}: {proc.stderr or proc.stdout}")
    return json.loads(proc.stdout)


def write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def by_skill(result: dict) -> dict[str, dict]:
    return {item["skill"]: item for item in result["skills"]}
