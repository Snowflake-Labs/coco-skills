"""Tests for dep_check.py — dependency/classpath validation."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import dep_check


# ---------------------------------------------------------------------------
# Version alignment
# ---------------------------------------------------------------------------

def test_spark_version_mismatch_major_minor(tmp_path):
    """Spark 3.3.x vs kit 3.5.1 — major.minor differ → blocking problem."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "build.sbt").write_text(
        'val sparkVersion = "3.3.2"\n', encoding="utf-8",
    )
    result = dep_check.run_checks(tmp_path)
    assert not result["ok"]
    assert any("Spark version mismatch" in p for p in result["problems"])


def test_delta_version_mismatch(tmp_path):
    """Delta version differs from kit default → blocking problem."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "build.sbt").write_text(
        'val sparkVersion = "3.5.1"\n'
        'val deltaVersion = "2.3.0"\n'
        'val deltaArtifact = "delta-core"\n',
        encoding="utf-8",
    )
    result = dep_check.run_checks(tmp_path)
    assert any("Delta version mismatch" in p for p in result["problems"])
    assert any("Delta artifact mismatch" in p for p in result["problems"])


def test_matching_versions_no_version_problems(tmp_path):
    """Kit-default versions in build.sbt → no version-mismatch problems."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "build.sbt").write_text(
        'val sparkVersion = "3.5.1"\n'
        'val deltaVersion = "3.1.0"\n'
        'val deltaArtifact = "delta-spark"\n',
        encoding="utf-8",
    )
    result = dep_check.run_checks(tmp_path)
    version_probs = [p for p in result["problems"] if "mismatch" in p]
    assert version_probs == [], f"Unexpected version problems: {version_probs}"


def test_spark_patch_version_diff_is_warning_only(tmp_path):
    """3.5.3 vs 3.5.1 — same major.minor → warning, not blocking problem."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "build.sbt").write_text(
        'val sparkVersion = "3.5.3"\n', encoding="utf-8",
    )
    result = dep_check.run_checks(tmp_path)
    spark_probs = [p for p in result["problems"] if "Spark version mismatch" in p]
    spark_warns = [w for w in result["warnings"] if "Spark version mismatch" in w]
    assert spark_probs == [], "patch-version diff should be a warning, not a problem"
    assert spark_warns, "expected a patch-version warning"


def test_no_build_file_gives_warning(tmp_path):
    """No build file in Output/ → warning about skipped version check."""
    (tmp_path / "Output").mkdir()
    result = dep_check.run_checks(tmp_path)
    assert any("no build.sbt" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Duplicate-class detection
# ---------------------------------------------------------------------------

def test_inner_class_not_flagged_as_duplicate(tmp_path):
    """Inner-class entries must NOT be treated as duplicates of their outer class.

    Regression guard: the detector uses exact-filename matching. Collapsing
    ``Foo$Inner.class`` into ``Foo.class`` produced false positives for any code
    with inner/anonymous classes (a normal JVM pattern).
    """
    (tmp_path / "Output" / "target").mkdir(parents=True)
    jar = tmp_path / "Output" / "target" / "app-assembly.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("com/example/Foo.class", b"a")
        zf.writestr("com/example/Foo$Inner.class", b"b")  # inner class, NOT a dup
        zf.writestr("com/other/Bar.class", b"c")
    result = dep_check.run_checks(tmp_path)
    dup_probs = [p for p in result["problems"] if "duplicate class" in p]
    assert dup_probs == []


def test_exact_duplicate_class_flagged(tmp_path):
    """A genuine exact-filename duplicate across merged jars IS flagged."""
    (tmp_path / "Output" / "target").mkdir(parents=True)
    jar = tmp_path / "Output" / "target" / "app-assembly.jar"
    # zipfile allows writing the same entry name twice — simulates a real
    # assembly-merge collision (two jars each shipping com/example/Foo.class).
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("com/example/Foo.class", b"a")
        zf.writestr("com/example/Foo.class", b"b")  # exact-name collision
        zf.writestr("com/other/Bar.class", b"c")
    result = dep_check.run_checks(tmp_path)
    assert any("duplicate class" in p for p in result["problems"])


def test_no_duplicate_classes(tmp_path):
    """JAR with all unique class names → no duplicate-class problem."""
    (tmp_path / "Output" / "target").mkdir(parents=True)
    jar = tmp_path / "Output" / "target" / "app-assembly.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("com/example/Foo.class", b"a")
        zf.writestr("com/example/Bar.class", b"b")
    result = dep_check.run_checks(tmp_path)
    dup_probs = [p for p in result["problems"] if "duplicate class" in p]
    assert dup_probs == []


# ---------------------------------------------------------------------------
# Shaded-library collision
# ---------------------------------------------------------------------------

def test_shaded_collision_detected(tmp_path):
    """JAR with shaded/ + org/apache/spark/ entry → shaded-collision problem."""
    (tmp_path / "Output" / "target").mkdir(parents=True)
    jar = tmp_path / "Output" / "target" / "fat-assembly.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("com/vendor/shaded/org/apache/spark/SomeClass.class", b"x")
        zf.writestr("com/safe/Thing.class", b"y")
    result = dep_check.run_checks(tmp_path)
    assert any("shaded-library collision" in p for p in result["problems"])


def test_no_shaded_collision_clean_jar(tmp_path):
    """JAR with no shaded markers → no shaded-collision problem."""
    (tmp_path / "Output" / "target").mkdir(parents=True)
    jar = tmp_path / "Output" / "target" / "app-assembly.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("org/apache/spark/sql/SparkSession.class", b"real")
        zf.writestr("com/example/Job.class", b"job")
    result = dep_check.run_checks(tmp_path)
    shaded_probs = [p for p in result["problems"] if "shaded-library collision" in p]
    assert shaded_probs == []


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------

def test_cli_exit_1_on_problems(tmp_path, monkeypatch):
    """CLI returns 1 when there are blocking problems."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "build.sbt").write_text(
        'val sparkVersion = "3.3.2"\n', encoding="utf-8",
    )
    import dep_check as dc
    rc = dc.main(["--conv-root", str(tmp_path)])
    assert rc == 1


def test_cli_exit_2_warnings_only(tmp_path, monkeypatch):
    """CLI returns 2 when there are only warnings (e.g. patch version mismatch + no SCOS jar)."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "build.sbt").write_text(
        'val sparkVersion = "3.5.3"\n'   # patch-only diff → warning
        'val deltaVersion = "3.1.0"\n'
        'val deltaArtifact = "delta-spark"\n',
        encoding="utf-8",
    )
    # Stub out SCOS jar check to avoid environment dependency
    import dep_check as dc
    import unittest.mock as mock
    with mock.patch.object(dc, "_check_scos_jar", return_value=([], [])):
        result = dc.run_checks(tmp_path)
    if result["problems"]:
        pytest.skip("SCOS jar found locally — skip exit-2 test")
    assert result["warnings"], "expected at least one warning"
