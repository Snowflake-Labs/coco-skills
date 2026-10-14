"""Tests for the Java AST fact extractor: ScosJavaFacts + java_ast_facts.py runner.

Two layers (mirroring test_scala_ast_facts.py):
  * Static guards (always run): runner API exists, required functions present.
  * Mocked unit tests that cover resolution paths, caching, graceful failure,
    and the happy-path facts extraction — all without requiring a JVM.

The integration test (requires SCOS_RUN_JAVAPARSER_IT=1 + java + mvn) is
gated and skipped in normal CI.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import java_ast_facts

_SCRIPTS = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Reset per-process cache before/after every test so tests are independent.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_resolved():
    java_ast_facts._RESOLVED = None
    yield
    java_ast_facts._RESOLVED = None


# ---------------------------------------------------------------------------
# Static guards
# ---------------------------------------------------------------------------


def test_runner_api():
    assert callable(java_ast_facts.extract_facts)
    assert callable(java_ast_facts.facts_available)
    assert callable(java_ast_facts._resolve_runner)


def test_returns_none_for_nonexistent_source(tmp_path):
    """Non-existent source path → None regardless of toolchain state."""
    # Pre-seed a fake resolved runner so resolution succeeds.
    java_ast_facts._RESOLVED = ("/fake/runner.jar", "/usr/bin/java")
    assert java_ast_facts.extract_facts(tmp_path / "nope.java") is None


# ---------------------------------------------------------------------------
# Resolution failure paths → None / facts_available False
# ---------------------------------------------------------------------------


def test_returns_none_when_java_absent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda x: None)
    assert java_ast_facts.extract_facts(Path("/any/file.java")) is None
    assert java_ast_facts.facts_available() is False


def test_returns_none_when_jar_absent_and_mvn_absent(monkeypatch, tmp_path):
    # java present, jar absent, mvn absent → None
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/java" if x == "java" else None)
    monkeypatch.setattr(java_ast_facts, "_MAVEN_JAR", tmp_path / "nonexistent.jar")
    assert java_ast_facts._resolve_runner() is None
    assert java_ast_facts.facts_available() is False


def test_returns_none_on_subprocess_failure(monkeypatch, tmp_path):
    """Subprocess returns non-zero exit → extract_facts returns None."""
    java_ast_facts._RESOLVED = ("/fake/runner.jar", "/usr/bin/java")
    src = tmp_path / "Job.java"
    src.write_text("class Job {}", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "parse error"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert java_ast_facts.extract_facts(src) is None


def test_returns_none_when_output_file_not_created(monkeypatch, tmp_path):
    """Subprocess exits 0 but output file is not written → None."""
    java_ast_facts._RESOLVED = ("/fake/runner.jar", "/usr/bin/java")
    src = tmp_path / "Job.java"
    src.write_text("class Job {}", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        # Do NOT write the output file.
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert java_ast_facts.extract_facts(src) is None


def test_returns_none_on_timeout(monkeypatch, tmp_path):
    """Subprocess timeout → extract_facts returns None (no exception propagated)."""
    java_ast_facts._RESOLVED = ("/fake/runner.jar", "/usr/bin/java")
    src = tmp_path / "Job.java"
    src.write_text("class Job {}", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 300)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert java_ast_facts.extract_facts(src) is None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def _write_output_and_succeed(fake_facts: dict):
    """Return a fake_run function that writes facts JSON to --output path."""
    def fake_run(cmd, **kwargs):
        out_idx = cmd.index("--output") + 1
        Path(cmd[out_idx]).write_text(json.dumps(fake_facts), encoding="utf-8")
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r
    return fake_run


def test_returns_facts_on_success(monkeypatch, tmp_path):
    """Happy path: subprocess writes facts JSON → extract_facts returns keyed dict."""
    java_ast_facts._RESOLVED = ("/fake/runner.jar", "/usr/bin/java")
    src = tmp_path / "Job.java"
    src.write_text(
        "import org.apache.spark.sql.SparkSession;\nclass Job {}",
        encoding="utf-8",
    )

    fake_facts = {
        "source": str(src),
        "file_count": 1,
        "parse_errors": 0,
        "files": [
            {
                "path": str(src),
                "parse_ok": True,
                "imports": [{"ref": "org.apache.spark.sql.SparkSession", "line": 1}],
                "calls": [],
                "selects": [],
                "new_types": [],
                "spark_sql": [],
                "infix": [],
                "interpolations": [],
                "session_created": False,
            }
        ],
    }

    monkeypatch.setattr(subprocess, "run", _write_output_and_succeed(fake_facts))
    result = java_ast_facts.extract_facts(src)

    assert result is not None
    key = str(src.resolve())
    assert key in result
    fobj = result[key]
    assert fobj["parse_ok"] is True
    assert any(i["ref"] == "org.apache.spark.sql.SparkSession" for i in fobj["imports"])


def test_facts_available_with_prebuilt_jar(monkeypatch, tmp_path):
    """facts_available() returns True when the prebuilt fat-jar exists."""
    fake_jar = tmp_path / "scos-javaparser-runner.jar"
    fake_jar.write_bytes(b"PK")  # minimal placeholder
    monkeypatch.setattr(java_ast_facts, "_MAVEN_JAR", fake_jar)
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/java" if x == "java" else None)
    assert java_ast_facts.facts_available() is True


# ---------------------------------------------------------------------------
# Caching behaviour
# ---------------------------------------------------------------------------


def test_caching_does_not_retry_after_failure(monkeypatch):
    """After resolution failure _RESOLVED is set to ('','') — no retry on next call."""
    call_count = [0]

    def fake_which(x):
        call_count[0] += 1
        return None  # java absent → resolution fails

    monkeypatch.setattr(shutil, "which", fake_which)
    java_ast_facts._resolve_runner()
    calls_after_first = call_count[0]

    # Second call must NOT invoke shutil.which again.
    java_ast_facts._resolve_runner()
    assert call_count[0] == calls_after_first
    assert java_ast_facts._RESOLVED == ("", "")


def test_caching_reuses_resolved_runner(monkeypatch, tmp_path):
    """After a successful resolution _resolve_runner returns the cached result."""
    fake_jar = tmp_path / "runner.jar"
    fake_jar.write_bytes(b"PK")
    monkeypatch.setattr(java_ast_facts, "_MAVEN_JAR", fake_jar)
    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/java" if x == "java" else None)

    r1 = java_ast_facts._resolve_runner()
    r2 = java_ast_facts._resolve_runner()
    assert r1 is r2  # same tuple object (cached)


# ---------------------------------------------------------------------------
# Maven auto-build
# ---------------------------------------------------------------------------


def test_maven_build_invoked_when_jar_absent(monkeypatch, tmp_path):
    """When jar is absent but mvn + java are present, Maven build is attempted."""
    fake_jar = tmp_path / "scos-javaparser-runner.jar"
    fake_pom = tmp_path / "pom.xml"
    fake_pom.write_text("<project/>", encoding="utf-8")
    monkeypatch.setattr(java_ast_facts, "_MAVEN_JAR", fake_jar)
    monkeypatch.setattr(java_ast_facts, "_MAVEN_POM", fake_pom)
    monkeypatch.setattr(
        shutil, "which", lambda x: f"/usr/bin/{x}" if x in ("java", "mvn") else None
    )

    def fake_run(cmd, **kwargs):
        fake_jar.write_bytes(b"PK")  # simulate Maven creating the jar
        r = MagicMock()
        r.returncode = 0
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = java_ast_facts._resolve_runner()
    assert result is not None
    assert result[0] == str(fake_jar)


def test_maven_build_failure_returns_none(monkeypatch, tmp_path):
    """If Maven exits non-zero, _resolve_runner returns None."""
    fake_jar = tmp_path / "scos-javaparser-runner.jar"
    fake_pom = tmp_path / "pom.xml"
    fake_pom.write_text("<project/>", encoding="utf-8")
    monkeypatch.setattr(java_ast_facts, "_MAVEN_JAR", fake_jar)
    monkeypatch.setattr(java_ast_facts, "_MAVEN_POM", fake_pom)
    monkeypatch.setattr(
        shutil, "which", lambda x: f"/usr/bin/{x}" if x in ("java", "mvn") else None
    )

    def fake_run(cmd, **kwargs):
        # Do NOT create the jar → simulate build failure
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "BUILD FAILURE"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert java_ast_facts._resolve_runner() is None


# ---------------------------------------------------------------------------
# Toolchain-gated integration test (requires real JVM + Maven)
# ---------------------------------------------------------------------------

_IT = (
    os.environ.get("SCOS_RUN_JAVAPARSER_IT") == "1"
    and shutil.which("java") is not None
    and shutil.which("mvn") is not None
)


@pytest.mark.skipif(
    not _IT,
    reason="set SCOS_RUN_JAVAPARSER_IT=1 and have java + mvn on PATH",
)
def test_extract_facts_integration(tmp_path):
    f = tmp_path / "Job.java"
    f.write_text(
        "import org.apache.spark.sql.SparkSession;\n"
        "class Job {\n"
        "  public static void main(String[] args) {\n"
        '    SparkSession spark = SparkSession.builder().appName("t").getOrCreate();\n'
        '    spark.sql("SELECT 1");\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    facts = java_ast_facts.extract_facts(f)
    assert facts is not None, "extractor returned None despite toolchain present"
    fobj = next(iter(facts.values()))
    assert fobj["parse_ok"]
    assert any(i["ref"] == "org.apache.spark.sql.SparkSession" for i in fobj["imports"])
    assert any("SELECT 1" in s.get("text", "") for s in fobj["spark_sql"])
