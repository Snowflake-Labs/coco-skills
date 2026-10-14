"""Tests for orchestrate_phases.py Phase 2 planning vs fallback separation.

Regression guard: the default (planning) invocation must be side-effect-free —
it computes the dispatch plan but must NOT mutate any source files. The
deterministic fallback only runs under --run-fallback (Phase 2a), after the
fixer sub-agents complete.
"""

from __future__ import annotations

import json
from pathlib import Path

import orchestrate_phases as op


def _make_state(tmp_path: Path, src: str) -> tuple[Path, Path]:
    conv = tmp_path / "Conversion-SCOS-test"
    out = conv / "Output"
    out.mkdir(parents=True)
    scala = out / "M.scala"
    scala.write_text(src, encoding="utf-8")
    state = {
        "manifest": ["M.scala"],
        "migrated_dir": str(out),
        "conversion_root": str(conv),
        # skill_directory intentionally omitted — planning must not need it.
    }
    sp = conv / "migration_state.json"
    sp.write_text(json.dumps(state), encoding="utf-8")
    return sp, scala


def test_phase2_planning_is_side_effect_free(tmp_path):
    src = "object M {\n  val spark = SparkSession.builder().getOrCreate()\n}\n"
    sp, scala = _make_state(tmp_path, src)

    rc = op.orchestrate_phase2(str(sp), budget=80000, language="scala",
                               max_parallel=4, run_fallback_flag=False)

    assert rc == 0
    # The source file must be byte-for-byte unchanged (no header/import injection).
    assert scala.read_text(encoding="utf-8") == src
    # The plan must have been persisted for the coordinator.
    state = json.loads(sp.read_text())
    assert "phase2_chunks" in state and state["phase2_chunks"]
    # Planning must NOT have created fixer-progress/coverage bookkeeping.
    assert "orchestrator_coverage_verified" not in state


def test_phase2_planning_handles_fresh_state_without_full_rewrite(tmp_path):
    # On a fresh state (no files_done / pending_files), planning must still not
    # transform anything — this is the exact scenario that previously caused the
    # fallback to generically rewrite the entire manifest.
    src = "object M\n"
    sp, scala = _make_state(tmp_path, src)
    op.orchestrate_phase2(str(sp), budget=80000, language="scala",
                          max_parallel=4, run_fallback_flag=False)
    assert scala.read_text(encoding="utf-8") == src


# ---------------------------------------------------------------------------
# Java Phase 2a: _run_java_fallback coverage-gate regression tests
# ---------------------------------------------------------------------------


def _make_java_state(tmp_path: Path, java_files: list[str], create_files: list[str]) -> Path:
    """Build a minimal Java migration_state.json for fallback testing.
    java_files: relative paths that belong in the manifest.
    create_files: subset of java_files that actually exist on disk.
    """
    conv = tmp_path / "Conversion-SCOS-java"
    out = conv / "Output"
    out.mkdir(parents=True)

    for rel in create_files:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("class Stub {}", encoding="utf-8")

    state = {
        "manifest": java_files,
        "migrated_dir": str(out),
        "conversion_root": str(conv),
    }
    sp = conv / "migration_state.json"
    sp.write_text(json.dumps(state), encoding="utf-8")
    return sp


def test_java_fallback_coverage_ok_writes_skipped(tmp_path):
    """All manifest files present → status=skipped (no fallback needed)."""
    sp = _make_java_state(tmp_path, ["App.java"], ["App.java"])
    rc = op._run_java_fallback(str(sp))
    assert rc == 0
    state = json.loads(sp.read_text())
    phase = state["phases_completed"]["2a_fallback"]
    assert phase["status"] == "skipped"
    assert phase["coverage_ok"] is True
    assert "missing_files" not in phase


def test_java_fallback_missing_file_writes_failed(tmp_path):
    """Missing manifest file → status=failed so validator rejects the state.

    Regression: previously wrote status='skipped' even on missing files,
    causing validate_migration_state --strict --language java to pass silently.
    """
    sp = _make_java_state(tmp_path, ["App.java", "Missing.java"], ["App.java"])
    rc = op._run_java_fallback(str(sp))
    assert rc == 1
    state = json.loads(sp.read_text())
    phase = state["phases_completed"]["2a_fallback"]
    assert phase["status"] == "failed"
    assert phase["coverage_ok"] is False
    assert "Missing.java" in phase["missing_files"]


def test_java_fallback_return_code_propagated_by_orchestrate_phase2(tmp_path):
    """orchestrate_phase2 --run-fallback must return non-zero when coverage fails.

    Regression: previously swallowed the non-zero fallback_rc with a warning
    and returned 0, making automation believe Phase 2a succeeded.
    """
    # Missing.java is in the manifest but not on disk.
    sp = _make_java_state(tmp_path, ["App.java", "Missing.java"], ["App.java"])
    state = json.loads(sp.read_text())
    # skill_directory must be set for the fallback path to run.
    import os
    skill_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)))
    state["skill_directory"] = skill_dir
    sp.write_text(json.dumps(state))

    rc = op.orchestrate_phase2(str(sp), budget=80000, language="java",
                               max_parallel=4, run_fallback_flag=True)
    assert rc != 0, "orchestrate_phase2 must propagate non-zero when Java coverage fails"
