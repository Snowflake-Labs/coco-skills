#!/usr/bin/env python3
"""Run the JavaParser AST fact extractor (ScosJavaFacts) for the migrate analyzer.

This is the Python side of the Java facts job: it resolves the JVM toolchain via
the prebuilt fat-jar under ``scripts/javaparser_maven/target/``, or builds it once
via Maven, then runs the extractor over a file or directory and returns the parsed
facts.

Design: **best-effort with graceful degradation.** If the JVM/Maven toolchain is
unavailable (or anything fails), ``extract_facts`` returns ``None`` and the
analyzer falls back to its in-process regex detectors — so the migrate flow never
hard-requires a live build toolchain in this module.  When the toolchain IS present,
the analyzer gets AST-precise, line-tagged facts (no comment/string false positives,
multi-line chains handled) — the same precision the Scala path gets from Scalameta.

The runner is cached per process: the extractor is invoked ONCE over the whole
migrated directory (ScosJavaFacts walks the tree), not per file.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_MAVEN_JAR = _SCRIPT_DIR / "javaparser_maven" / "target" / "scos-javaparser-runner.jar"
_MAVEN_POM = _SCRIPT_DIR / "javaparser_maven" / "pom.xml"
_FACTS_MAIN = "com.snowflake.scos.javaparser.ScosJavaFacts"

# Cached (jar_path, java_exe) once resolved; ("", "") means "resolution failed —
# don't retry this process".
_RESOLVED: tuple[str, str] | None = None


def _resolve_runner(*, timeout: int = 600) -> tuple[str, str] | None:
    """Return ``(jar_path, java_exe)`` or None if unavailable.

    Resolution order:
    1. Prebuilt fat-jar at ``_MAVEN_JAR`` — used directly when present.
    2. Maven auto-build — ``mvn -q -f <pom.xml> package -DskipTests`` when
       ``mvn`` + ``java`` are on PATH and ``_MAVEN_POM`` exists.
    3. Any failure → cached as ("", "") so we don't re-attempt.
    """
    global _RESOLVED
    if _RESOLVED is not None:
        return _RESOLVED if _RESOLVED != ("", "") else None

    java = shutil.which("java")
    if java is None:
        _RESOLVED = ("", "")
        return None

    # 1. Prebuilt fat-jar.
    if _MAVEN_JAR.exists():
        _RESOLVED = (str(_MAVEN_JAR), java)
        return _RESOLVED

    # 2. Maven auto-build.
    mvn = shutil.which("mvn")
    if mvn is None or not _MAVEN_POM.exists():
        _RESOLVED = ("", "")
        return None

    try:
        result = subprocess.run(
            [mvn, "-q", "-f", str(_MAVEN_POM), "package", "-DskipTests"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        _RESOLVED = ("", "")
        return None

    if result.returncode != 0 or not _MAVEN_JAR.exists():
        _RESOLVED = ("", "")
        return None

    _RESOLVED = (str(_MAVEN_JAR), java)
    return _RESOLVED


def extract_facts(source_path: str | Path, *, timeout: int = 300) -> dict | None:
    """Return AST facts for ``source_path`` (file or directory), or None.

    Result shape (on success)::

        {"<abs file path>": {parse_ok, imports, calls, selects, new_types,
                             spark_sql, infix, interpolations, session_created}, ...}

    Returns None when the toolchain is unavailable or extraction fails — callers
    MUST treat None as "fall back to regex detection".
    """
    resolved = _resolve_runner()
    if resolved is None:
        return None
    jar, java = resolved

    src = Path(source_path).resolve()
    if not src.exists():
        return None

    try:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "facts.json"
            proc = subprocess.run(
                [java, "-cp", jar, _FACTS_MAIN,
                 "--source", str(src), "--output", str(out)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if proc.returncode != 0 or not out.exists():
                return None
            data = json.loads(out.read_text(encoding="utf-8"))
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return None

    by_path: dict[str, dict] = {}
    for f in data.get("files", []):
        p = f.get("path")
        if p:
            by_path[str(Path(p).resolve())] = f
    return by_path


def facts_available() -> bool:
    """True when the JVM/Maven toolchain can be resolved (without running extraction)."""
    return _resolve_runner() is not None
