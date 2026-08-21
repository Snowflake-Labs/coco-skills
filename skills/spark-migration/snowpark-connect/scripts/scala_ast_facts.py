#!/usr/bin/env python3
"""Run the Scalameta AST fact extractor (ScosMigrateFacts) for the migrate analyzer.

This is the Python side of Job 2: it resolves the JVM toolchain via the pinned
``scripts/scalafix_sbt`` wrapper (which already compiles
``scripts/scalafix_rules/ScosMigrateFacts.scala`` and resolves Scalameta + circe),
then runs the extractor over a file or directory and returns the parsed facts.

Design: **best-effort with graceful degradation.** If the JVM/sbt toolchain is
unavailable (or anything fails), ``extract_facts`` returns ``None`` and the
analyzer falls back to its in-process regex detectors — so the migrate flow
never hard-requires a JVM. When the toolchain IS present, the analyzer gets
AST-precise, line-tagged facts (no comment/string false positives, multi-line
chains handled) — the same precision PySpark gets from libcst.

The classpath export is cached per process: the extractor is invoked ONCE over
the whole migrated directory (ScosMigrateFacts walks the tree), not per file.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SBT_DIR = _SCRIPT_DIR / "scalafix_sbt"
_FACTS_MAIN = "com.snowflake.scos.scalafix.ScosMigrateFacts"

# Shared on-disk classpath cache (same file written by preprocess_scalafix.py)
_CP_CACHE_PATH = _SBT_DIR / ".classpath_cache.json"
_CP_CACHE_SOURCES = [
    _SBT_DIR / "build.sbt",
    _SCRIPT_DIR / "scalafix_rules" / "SCOSRules.scala",
    _SCRIPT_DIR / "scalafix_rules" / "ScosMigrateFacts.scala",
]

# Cached (classpath, java) once resolved; ("", "") means "resolution failed,
# don't retry this process".
_RESOLVED: tuple[str, str] | None = None


def _cache_key() -> str:
    """SHA-256 over the *contents* of the build sources (stable across git
    checkouts that reset mtimes). Must match preprocess_scalafix._cp_cache_key."""
    h = hashlib.sha256()
    for p in _CP_CACHE_SOURCES:
        h.update(p.name.encode("utf-8"))
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"\x00")
    return h.hexdigest()


def _resolve_classpath(timeout: int = 900) -> tuple[str, str] | None:
    """Resolve ``(classpath, java)`` via the sbt wrapper, or None if unavailable.

    Checks the shared on-disk cache first; falls back to ``sbt export`` only
    when the cache is absent or stale.  Mirrors
    ``preprocess_scalafix._resolve_sbt_invocation`` but returns the raw classpath
    so we can run our OWN main class (ScosMigrateFacts) rather than scalafix.cli.Cli.
    """
    global _RESOLVED
    if _RESOLVED is not None:
        return _RESOLVED if _RESOLVED != ("", "") else None

    sbt = shutil.which("sbt")
    java = shutil.which("java")
    if sbt is None or java is None or not (_SBT_DIR / "build.sbt").exists():
        _RESOLVED = ("", "")
        return None

    # ── Cache hit: skip sbt export ────────────────────────────────────────────
    try:
        data = json.loads(_CP_CACHE_PATH.read_text(encoding="utf-8"))
        if data.get("key") == _cache_key():
            cp = data.get("classpath", "")
            jv = data.get("java", "")
            if cp and jv and (shutil.which(jv) or Path(jv).is_file()):
                # Spot-check that the first few cached JARs still exist.
                jar_paths = [p for p in cp.split(os.pathsep) if p.endswith(".jar")][:5]
                if not any(not Path(j).is_file() for j in jar_paths):
                    _RESOLVED = (cp, jv)
                    return _RESOLVED
    except Exception:  # noqa: BLE001
        pass
    # ─────────────────────────────────────────────────────────────────────────

    try:
        result = subprocess.run(
            [sbt, "--batch", "-Dsbt.log.noformat=true", "-error", "export Compile/fullClasspath"],
            cwd=str(_SBT_DIR), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        _RESOLVED = ("", "")
        return None
    if result.returncode != 0:
        _RESOLVED = ("", "")
        return None
    # `export` prints the classpath as one File.pathSeparator-joined line.
    classpath = None
    for line in reversed(result.stdout.splitlines()):
        cand = line.strip()
        if "scalameta" in cand or "scalafix-cli" in cand:
            if os.pathsep in cand or cand.endswith(".jar"):
                classpath = cand
                break
    if not classpath:
        _RESOLVED = ("", "")
        return None
    _RESOLVED = (classpath, java)
    # ── Persist cache ─────────────────────────────────────────────────────────
    # Only write real classpaths (>=10 JARs) to avoid corrupting the cache
    # with test fixture stubs that have only a few fake entries.
    if len(classpath.split(os.pathsep)) >= 10:
        try:
            _CP_CACHE_PATH.write_text(
                json.dumps({"key": _cache_key(), "classpath": classpath, "java": java}, indent=2),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass
    # ─────────────────────────────────────────────────────────────────────────
    return _RESOLVED


def extract_facts(source_path: str | Path, *, timeout: int = 300) -> dict | None:
    """Return AST facts for ``source_path`` (file or directory), or None.

    Result shape (on success)::

        {"<abs file path>": {parse_ok, imports, calls, selects, new_types,
                             spark_sql, interpolations, session_created}, ...}

    Returns None when the toolchain is unavailable or extraction fails — callers
    MUST treat None as "fall back to regex detection".
    """
    resolved = _resolve_classpath()
    if resolved is None:
        return None
    classpath, java = resolved

    src = Path(source_path).resolve()
    if not src.exists():
        return None
    try:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "facts.json"
            proc = subprocess.run(
                [java, "-cp", classpath, _FACTS_MAIN,
                 "--source", str(src), "--output", str(out)],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
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
    """True when the JVM toolchain can be resolved (without running extraction)."""
    return _resolve_classpath() is not None
