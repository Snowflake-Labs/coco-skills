"""Tests for Phase 0.5c: JavaParser AST pre-processing driver.

Covers (all without a JVM — subprocess + runner resolution are fully mocked):
  * Hard-fail semantics: exit 1 + status=failed when no runner is resolvable.
  * Idempotency: files with existing javaparser: edits are skipped on re-run.
  * Recipe-edits anchor shape: javaparser:<Rule>:<line>:<8-hex>.
  * All 14 rules are invoked for each eligible file.
  * phases_completed["0_5c_javaparser"] is written with correct fields.
  * --no-build prevents Maven invocation.
  * --runner-jar override is respected.
  * No .java files in manifest → skipped (not failed).

Run from the snowpark-connect/ directory::

    uv run --project . python -m pytest scripts/tests/test_preprocess_javaparser.py -v
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load module under test via importlib (directory path contains hyphens).
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "preprocess_javaparser",
    _SCRIPTS_DIR / "preprocess_javaparser.py",
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

main = _mod.main
PHASE_KEY = _mod.PHASE_KEY
JAVA_RULES = _mod.JAVA_RULES
RULE_PREFIX = _mod.RULE_PREFIX

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_FAKE_JAVA = "/usr/bin/java"

SAMPLE_JAVA = """\
import org.apache.spark.sql.SparkSession;

class Job {
    public static void main(String[] args) {
        SparkSession spark = SparkSession.builder()
            .master("local")
            .getOrCreate();
    }
}
"""

# Rewritten version: SparkSessionBuilderRewrite removed .master() and added marker.
REWRITTEN_JAVA = """\
import org.apache.spark.sql.SparkSession;

class Job {
    public static void main(String[] args) {
        // SCOS-RECIPE-PRESERVED-CONFIG: .master("local")
        SparkSession spark = SparkSession.builder()
            .getOrCreate();
    }
}
"""


def _make_state(tmp_path: Path, java_content: str = SAMPLE_JAVA) -> tuple[Path, Path]:
    """Create minimal migration_state.json with one .java file in manifest."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    java_file = src_dir / "Job.java"
    java_file.write_text(java_content, encoding="utf-8")

    state: dict[str, Any] = {
        "migrated_dir": str(src_dir),
        "manifest": ["Job.java"],
        "recipe_edits": {},
        "phases_completed": {},
    }
    state_file = tmp_path / "migration_state.json"
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state_file, java_file


def _fake_jar(tmp_path: Path, name: str = "runner.jar") -> Path:
    jar = tmp_path / name
    jar.write_bytes(b"PK")
    return jar


def _fake_which_java_only(x: str) -> str | None:
    return _FAKE_JAVA if x == "java" else None


def _identity_rewrite(cmd: list, **kwargs) -> MagicMock:
    """Fake subprocess.run: echo the --source file content back unchanged."""
    r = MagicMock()
    r.returncode = 0
    r.stderr = ""
    if "--source" in cmd:
        src_idx = cmd.index("--source") + 1
        r.stdout = Path(cmd[src_idx]).read_text(encoding="utf-8")
    else:
        r.stdout = ""
    return r


def _safe_main(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0


# ---------------------------------------------------------------------------
# Constant checks
# ---------------------------------------------------------------------------


def test_phase_key():
    assert PHASE_KEY == "0_5c_javaparser"


def test_rule_count():
    # 14 original + 19 ported from Scalafix (SNOW-3715354). Scalafix has 35 rules;
    # ScosSqlContextImplicitsRewrite and ScosScRangeToSparkRange are deliberately
    # not ported (Scala-only language/API features with no Java analogue).
    assert len(JAVA_RULES) == 33


def test_rule_names_include_expected():
    assert "ScosSparkSessionBuilderRewrite" in JAVA_RULES
    assert "ScosTempViewMultiUseCache" in JAVA_RULES
    assert "ScosSystemGetenvRewrite" in JAVA_RULES
    assert "ScosDeltaTableAnnotate" in JAVA_RULES


def test_rule_prefix():
    assert RULE_PREFIX == "javaparser:"


# ---------------------------------------------------------------------------
# Hard-fail: no runner available
# ---------------------------------------------------------------------------


def test_hard_fail_when_java_absent(monkeypatch, tmp_path):
    """Phase exits 1 and records status=failed when java is not on PATH."""
    state_file, _ = _make_state(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda x: None)
    monkeypatch.setattr(_mod, "MAVEN_JAR", tmp_path / "nonexistent.jar")

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 1

    state = json.loads(state_file.read_text())
    phase = state["phases_completed"][PHASE_KEY]
    assert phase["status"] == "failed"
    assert "skip_reason" in phase


def test_hard_fail_no_build_and_jar_absent(monkeypatch, tmp_path):
    """--no-build + absent jar → exit 1 (not a graceful skip)."""
    state_file, _ = _make_state(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", tmp_path / "nonexistent.jar")
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)

    rc = _safe_main(["--state", str(state_file), "--no-build"])
    assert rc == 1

    state = json.loads(state_file.read_text())
    assert state["phases_completed"][PHASE_KEY]["status"] == "failed"


# ---------------------------------------------------------------------------
# No .java files in manifest → skipped (not failed)
# ---------------------------------------------------------------------------


def test_no_java_files_skips(monkeypatch, tmp_path):
    """Manifest with no .java files records status=skipped and exits 0."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    state: dict[str, Any] = {
        "migrated_dir": str(src_dir),
        "manifest": ["Main.scala", "README.md"],
        "recipe_edits": {},
        "phases_completed": {},
    }
    state_file = tmp_path / "migration_state.json"
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0
    state = json.loads(state_file.read_text())
    assert state["phases_completed"][PHASE_KEY]["status"] == "skipped"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotency_skips_already_processed_file(monkeypatch, tmp_path):
    """Files with existing javaparser: recipe_edits are skipped on re-run."""
    state_file, _ = _make_state(tmp_path)

    # Pre-populate state with a javaparser: anchor for Job.java.
    state = json.loads(state_file.read_text())
    state["recipe_edits"]["Job.java"] = [
        {
            "recipe_id": "javaparser:ScosSparkSessionBuilderRewrite",
            "src_line": 5,
            "output_line_anchor": "javaparser:ScosSparkSessionBuilderRewrite:5:abcd1234",
        }
    ]
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)

    rewrite_calls: list[list] = []

    def tracking_run(cmd: list, **kwargs) -> MagicMock:
        rewrite_calls.append(list(cmd))
        return _identity_rewrite(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", tracking_run)

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    actual_rewrites = [c for c in rewrite_calls if _mod.REWRITE_MAIN in c]
    assert actual_rewrites == [], "no rewrite subprocess expected for already-processed file"


def test_second_run_is_noop(monkeypatch, tmp_path):
    """Running preprocess twice produces identical state (idempotent result)."""
    state_file, _ = _make_state(tmp_path)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(subprocess, "run", _identity_rewrite)

    _safe_main(["--state", str(state_file)])
    state_after_first = json.loads(state_file.read_text())

    _safe_main(["--state", str(state_file)])
    state_after_second = json.loads(state_file.read_text())

    # recipe_edits must not grow on second run.
    assert state_after_first["recipe_edits"] == state_after_second["recipe_edits"]


# ---------------------------------------------------------------------------
# Recipe-edits anchor shape
# ---------------------------------------------------------------------------


def test_recipe_edits_anchor_shape(monkeypatch, tmp_path):
    """Anchors have the correct javaparser:<Rule>:<line>:<8-hex> format."""
    state_file, _ = _make_state(tmp_path)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)

    def rewrite_first_rule_only(cmd: list, **kwargs) -> MagicMock:
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        if "--source" in cmd and "--rule" in cmd:
            rule = cmd[cmd.index("--rule") + 1]
            src = Path(cmd[cmd.index("--source") + 1]).read_text(encoding="utf-8")
            r.stdout = REWRITTEN_JAVA if rule == "ScosSparkSessionBuilderRewrite" else src
        else:
            r.stdout = ""
        return r

    monkeypatch.setattr(subprocess, "run", rewrite_first_rule_only)
    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    state = json.loads(state_file.read_text())
    edits = state["recipe_edits"].get("Job.java", [])
    assert edits, "expected at least one recipe_edit after rewrite"

    for edit in edits:
        assert edit["recipe_id"].startswith("javaparser:"), f"bad recipe_id: {edit['recipe_id']}"
        anchor = edit["output_line_anchor"]
        parts = anchor.split(":")
        # Expected: javaparser:<RuleName>:<src_line>:<8-hex>
        assert len(parts) == 4, f"unexpected anchor format: {anchor!r}"
        assert parts[0] == "javaparser"
        assert parts[1] in JAVA_RULES, f"unknown rule in anchor: {parts[1]}"
        assert parts[2].isdigit(), f"src_line is not numeric: {parts[2]}"
        assert len(parts[3]) == 8, f"digest should be 8 hex chars: {parts[3]}"
        # src_line must match the integer field
        assert edit["src_line"] == int(parts[2])


# ---------------------------------------------------------------------------
# All 14 rules are invoked
# ---------------------------------------------------------------------------


def test_all_rules_invoked(monkeypatch, tmp_path):
    """All 14 rules in JAVA_RULES are invoked for each eligible .java file."""
    state_file, _ = _make_state(tmp_path)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)

    invoked_rules: list[str] = []

    def tracking_run(cmd: list, **kwargs) -> MagicMock:
        if "--rule" in cmd:
            invoked_rules.append(cmd[cmd.index("--rule") + 1])
        return _identity_rewrite(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", tracking_run)
    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0
    assert set(invoked_rules) == set(JAVA_RULES), (
        f"missing rules: {set(JAVA_RULES) - set(invoked_rules)}"
    )


# ---------------------------------------------------------------------------
# phases_completed entry
# ---------------------------------------------------------------------------


def test_phase_entry_written_with_correct_fields(monkeypatch, tmp_path):
    """phases_completed[PHASE_KEY] has status=passed and expected fields."""
    state_file, _ = _make_state(tmp_path)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(subprocess, "run", _identity_rewrite)

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    state = json.loads(state_file.read_text())
    phase = state["phases_completed"][PHASE_KEY]
    assert phase["status"] == "passed"
    assert "ran_at" in phase
    assert phase["files_processed"] == 1
    assert set(phase["rules_run"]) == set(JAVA_RULES)
    assert "files_modified" in phase
    assert "total_edits" in phase


# ---------------------------------------------------------------------------
# --no-build prevents Maven invocation
# ---------------------------------------------------------------------------


def test_no_build_flag_prevents_mvn_call(monkeypatch, tmp_path):
    """--no-build must not invoke mvn; exits 1 when prebuilt jar is absent."""
    state_file, _ = _make_state(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", tmp_path / "nonexistent.jar")
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)

    mvn_called = [False]

    def fake_run(cmd: list, **kwargs) -> MagicMock:
        if "mvn" in cmd[0]:
            mvn_called[0] = True
        r = MagicMock()
        r.returncode = 0
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = _safe_main(["--state", str(state_file), "--no-build"])
    assert rc == 1
    assert not mvn_called[0], "mvn must not be called when --no-build is set"


def test_no_build_env_var_prevents_mvn_call(monkeypatch, tmp_path):
    """SCOS_JAVAPARSER_NO_BUILD=1 has the same effect as --no-build."""
    state_file, _ = _make_state(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", tmp_path / "nonexistent.jar")
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setenv("SCOS_JAVAPARSER_NO_BUILD", "1")

    mvn_called = [False]

    def fake_run(cmd: list, **kwargs) -> MagicMock:
        if "mvn" in str(cmd[0]):
            mvn_called[0] = True
        r = MagicMock()
        r.returncode = 0
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = _safe_main(["--state", str(state_file)])
    assert rc == 1
    assert not mvn_called[0]


# ---------------------------------------------------------------------------
# --runner-jar override
# ---------------------------------------------------------------------------


def test_runner_jar_override_used_when_maven_jar_absent(monkeypatch, tmp_path):
    """--runner-jar <path> is used even when MAVEN_JAR does not exist."""
    state_file, _ = _make_state(tmp_path)
    custom_jar = _fake_jar(tmp_path, "custom-runner.jar")
    monkeypatch.setattr(_mod, "MAVEN_JAR", tmp_path / "nonexistent.jar")
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(subprocess, "run", _identity_rewrite)

    rc = _safe_main(["--state", str(state_file), "--runner-jar", str(custom_jar)])
    assert rc == 0


def test_runner_jar_override_nonexistent_fails(monkeypatch, tmp_path):
    """--runner-jar pointing to a missing file → exit 1."""
    state_file, _ = _make_state(tmp_path)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(subprocess, "run", _identity_rewrite)

    rc = _safe_main(["--state", str(state_file), "--runner-jar", str(tmp_path / "missing.jar")])
    assert rc == 1


# ---------------------------------------------------------------------------
# Per-file rule failure handling (partial failures don't stop other files)
# ---------------------------------------------------------------------------


def test_per_rule_failure_does_not_abort_file(monkeypatch, tmp_path):
    """When some rules fail for a file, the rest proceed and output is written."""
    state_file, _ = _make_state(tmp_path)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)

    failed_rules = {"ScosCheckpointToCache", "ScosTempViewMultiUseCache"}

    def selective_fail(cmd: list, **kwargs) -> MagicMock:
        r = MagicMock()
        if "--rule" in cmd:
            rule = cmd[cmd.index("--rule") + 1]
            if rule in failed_rules:
                r.returncode = 1
                r.stdout = ""
                r.stderr = f"simulated failure for {rule}"
                return r
        return _identity_rewrite(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", selective_fail)
    rc = _safe_main(["--state", str(state_file)])
    # File had some rules succeed → should not be in failures → exit 0.
    assert rc == 0

    state = json.loads(state_file.read_text())
    phase = state["phases_completed"][PHASE_KEY]
    assert phase["status"] == "passed"
    # File is NOT in failures (only added when ALL rules fail).
    assert "failures" not in phase or "Job.java" not in phase.get("failures", [])


def test_all_rules_fail_marks_file_as_failure(monkeypatch, tmp_path):
    """When ALL rules fail for a file, status=failed and exit code is 1.

    Reviewer Fix 3: non-empty failures must set status='failed' and return
    non-zero so the hard gate actually rejects the migration state.
    """
    state_file, _ = _make_state(tmp_path)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)

    def always_fail(cmd: list, **kwargs) -> MagicMock:
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "simulated error"
        return r

    monkeypatch.setattr(subprocess, "run", always_fail)
    rc = _safe_main(["--state", str(state_file)])
    assert rc == 1  # per-file failures must cause exit 1 (hard gate)

    state = json.loads(state_file.read_text())
    phase = state["phases_completed"][PHASE_KEY]
    assert phase["status"] == "failed"
    assert "failures" in phase
    assert "Job.java" in phase["failures"]
