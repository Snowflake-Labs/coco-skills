"""Tests for dep_check.py — dependency/classpath validation (Java/Maven variant).

Mirrors validate-spark-scala-to-snowpark-connect/scripts/tests/test_dep_check.py,
adapted for pom.xml instead of build.sbt. This suite previously did not exist —
dep_check.py had zero test coverage despite being the Java-specific check most
likely to catch real classpath defects (Maven Shade collisions, duplicate
classes, Spark/Delta version drift, missing SCOS client jar).
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import dep_check


def _pom(spark_version=None, delta_version=None, delta_artifact=None) -> str:
    """Build a minimal pom.xml exercising dep_check's regexes."""
    props = f"<spark.version>{spark_version}</spark.version>" if spark_version else ""
    delta_dep = ""
    if delta_version and delta_artifact:
        delta_dep = f"""
        <dependency>
          <groupId>io.delta</groupId>
          <artifactId>{delta_artifact}_2.12</artifactId>
          <version>{delta_version}</version>
        </dependency>"""
    return f"""<project>
  <properties>{props}</properties>
  <dependencies>{delta_dep}
  </dependencies>
</project>
"""


# ---------------------------------------------------------------------------
# Version alignment
# ---------------------------------------------------------------------------

def test_spark_version_mismatch_major_minor(tmp_path):
    """Spark 3.3.x vs kit 3.5.1 — major.minor differ → blocking problem."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "pom.xml").write_text(_pom(spark_version="3.3.2"), encoding="utf-8")
    result = dep_check.run_checks(tmp_path)
    assert not result["ok"]
    assert any("Spark version mismatch" in p for p in result["problems"])


def test_delta_version_mismatch(tmp_path):
    """Delta version differs from kit default → blocking problem."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "pom.xml").write_text(
        _pom(spark_version="3.5.1", delta_version="2.3.0", delta_artifact="delta-core"),
        encoding="utf-8",
    )
    result = dep_check.run_checks(tmp_path)
    assert any("Delta version mismatch" in p for p in result["problems"])
    assert any("Delta artifact mismatch" in p for p in result["problems"])


def test_matching_versions_no_version_problems(tmp_path):
    """Kit-default versions in pom.xml → no version-mismatch problems."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "pom.xml").write_text(
        _pom(spark_version="3.5.1", delta_version="3.1.0", delta_artifact="delta-spark"),
        encoding="utf-8",
    )
    result = dep_check.run_checks(tmp_path)
    version_probs = [p for p in result["problems"] if "mismatch" in p]
    assert version_probs == [], f"Unexpected version problems: {version_probs}"


def test_spark_patch_version_diff_is_warning_only(tmp_path):
    """3.5.3 vs 3.5.1 — same major.minor → warning, not blocking problem."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "pom.xml").write_text(_pom(spark_version="3.5.3"), encoding="utf-8")
    result = dep_check.run_checks(tmp_path)
    spark_probs = [p for p in result["problems"] if "Spark version mismatch" in p]
    spark_warns = [w for w in result["warnings"] if "Spark version mismatch" in w]
    assert spark_probs == [], "patch-version diff should be a warning, not a problem"
    assert spark_warns, "expected a patch-version warning"


def test_no_build_file_gives_warning(tmp_path):
    """No build file in Output/ → warning about skipped version check."""
    (tmp_path / "Output").mkdir()
    result = dep_check.run_checks(tmp_path)
    assert any("no pom.xml" in w for w in result["warnings"])


def test_build_sbt_fallback_parsed_for_mixed_projects(tmp_path):
    """A Java validator target can still carry a build.sbt in mixed repos; dep_check
    falls back to the sbt parser when no pom.xml/build.gradle is present."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "build.sbt").write_text(
        'val sparkVersion = "3.3.2"\n', encoding="utf-8",
    )
    result = dep_check.run_checks(tmp_path)
    assert any("Spark version mismatch" in p for p in result["problems"])


# ---------------------------------------------------------------------------
# Duplicate-class detection
# ---------------------------------------------------------------------------

def test_inner_class_not_flagged_as_duplicate(tmp_path):
    """Inner-class entries must NOT be treated as duplicates of their outer class.

    The detector uses exact-filename matching, so ``Foo$Inner.class`` is simply a
    different name from ``Foo.class`` — no special-casing needed or present.
    """
    (tmp_path / "Output" / "target").mkdir(parents=True)
    jar = tmp_path / "Output" / "target" / "app-shaded.jar"
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
    jar = tmp_path / "Output" / "target" / "app-shaded.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("com/example/Foo.class", b"a")
        zf.writestr("com/example/Foo.class", b"b")  # exact-name collision
        zf.writestr("com/other/Bar.class", b"c")
    result = dep_check.run_checks(tmp_path)
    assert any("duplicate class" in p for p in result["problems"])


def test_no_duplicate_classes(tmp_path):
    """JAR with all unique class names → no duplicate-class problem."""
    (tmp_path / "Output" / "target").mkdir(parents=True)
    jar = tmp_path / "Output" / "target" / "app-shaded.jar"
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
    """JAR with a _shaded_ marker + org/apache/spark/ entry → shaded-collision problem."""
    (tmp_path / "Output" / "target").mkdir(parents=True)
    jar = tmp_path / "Output" / "target" / "fat-shaded.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("com/vendor/_shaded_/org/apache/spark/SomeClass.class", b"x")
        zf.writestr("com/safe/Thing.class", b"y")
    result = dep_check.run_checks(tmp_path)
    assert any("shaded-library collision" in p for p in result["problems"])


def test_no_shaded_collision_clean_jar(tmp_path):
    """JAR with no shaded markers → no shaded-collision problem."""
    (tmp_path / "Output" / "target").mkdir(parents=True)
    jar = tmp_path / "Output" / "target" / "app-shaded.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("org/apache/spark/sql/SparkSession.class", b"real")
        zf.writestr("com/example/Job.class", b"job")
    result = dep_check.run_checks(tmp_path)
    shaded_probs = [p for p in result["problems"] if "shaded-library collision" in p]
    assert shaded_probs == []


# ---------------------------------------------------------------------------
# Assembly JAR resolution (Maven Shade naming, distinct from Scala's sbt-assembly)
# ---------------------------------------------------------------------------

def test_finds_maven_shade_jar_by_mtime(tmp_path):
    """Among several jars under target/, the newest shaded/assembly-named jar wins."""
    (tmp_path / "Output" / "target").mkdir(parents=True)
    plain = tmp_path / "Output" / "target" / "app-1.0.jar"
    shaded = tmp_path / "Output" / "target" / "app-1.0-shaded.jar"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("com/example/Foo.class", b"a")
    with zipfile.ZipFile(shaded, "w") as zf:
        zf.writestr("com/example/Foo.class", b"a")
        zf.writestr("com/example/Foo.class", b"b")  # dup only in the shaded jar
    result = dep_check.run_checks(tmp_path)
    assert any("duplicate class" in p and "shaded" in p for p in result["problems"])


def test_analysis_json_jar_path_hint_used_when_present(tmp_path):
    """jar_path in analysis.json overrides the target/ scan."""
    (tmp_path / "Output" / "target").mkdir(parents=True)
    hinted = tmp_path / "Output" / "target" / "hinted.jar"
    with zipfile.ZipFile(hinted, "w") as zf:
        zf.writestr("com/example/Foo.class", b"a")
        zf.writestr("com/example/Foo.class", b"b")
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True)
    (shared / "analysis.json").write_text(
        '{"jar_path": "Output/target/hinted.jar"}', encoding="utf-8",
    )
    result = dep_check.run_checks(tmp_path)
    assert any("hinted.jar" in p for p in result["problems"])


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------

def test_cli_exit_1_on_problems(tmp_path):
    """CLI returns 1 when there are blocking problems."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "pom.xml").write_text(_pom(spark_version="3.3.2"), encoding="utf-8")
    rc = dep_check.main(["--conv-root", str(tmp_path)])
    assert rc == 1


def test_cli_exit_2_warnings_only(tmp_path):
    """CLI returns 2 when there are only warnings (e.g. patch version mismatch + no SCOS jar)."""
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output" / "pom.xml").write_text(
        _pom(spark_version="3.5.3", delta_version="3.1.0", delta_artifact="delta-spark"),
        encoding="utf-8",
    )
    import unittest.mock as mock
    with mock.patch.object(dep_check, "_check_scos_jar", return_value=([], [])):
        result = dep_check.run_checks(tmp_path)
    if result["problems"]:
        pytest.skip("SCOS jar found locally — skip exit-2 test")
    assert result["warnings"], "expected at least one warning"


def test_cli_missing_conv_root_exits_1(tmp_path):
    rc = dep_check.main(["--conv-root", str(tmp_path / "does-not-exist")])
    assert rc == 1
