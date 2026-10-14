"""Tests for ``validate_migration_state.py`` required phase coverage."""

from __future__ import annotations

from validate_migration_state import validate, _required_phases_for_language


def _base_state() -> dict:
    return {
        "orchestrator_coverage_verified": True,
        "compilation_reverted_count": 0,
        "phases_completed": {
            "0_5_preprocess": {"status": "passed"},
            "1_analysis": {"status": "passed"},
            "1a_assessment_report": {"status": "passed"},
            "2_fixes": {"status": "passed"},
            "3_imports": {"status": "passed"},
            "4_reports": {"status": "passed"},
        },
    }


def test_validate_requires_2c_verification():
    report = validate(_base_state(), "dummy-state.json")
    missing = {r.key: r for r in report.results if r.is_failure}
    assert "2c_verification" in missing


def test_validate_requires_1a_assessment_report():
    state = _base_state()
    state["phases_completed"]["2c_verification"] = {"status": "passed"}
    del state["phases_completed"]["1a_assessment_report"]
    report = validate(state, "dummy-state.json")
    missing = {r.key: r for r in report.results if r.is_failure}
    assert "1a_assessment_report" in missing


def test_validate_passes_when_2c_verification_present():
    state = _base_state()
    state["phases_completed"]["2c_verification"] = {
        "status": "passed",
        "disagreements": 0,
        "not_attempted": 0,
        "needs_human_action": [],
        "verified_human_action_count": 0,
        "recorded_migrated_count": 0,
    }
    report = validate(state, "dummy-state.json")
    assert not report.has_failures


def test_validation_phase_recognized_under_new_4a_key():
    state = _base_state()
    state["phases_completed"]["4a_validation"] = {"status": "passed"}
    report = validate(state, "dummy-state.json")
    optional = {r.key: r for r in report.optional_results}
    assert "4a_validation" in optional
    assert optional["4a_validation"].status == "ok"
    # canonical key present -> not flagged as unrecognized
    assert "4a_validation" not in report.extra_keys


def test_legacy_4b_validation_key_still_accepted_as_alias():
    # Older migration_state.json files recorded the validation self-attestation
    # under 4b_validation. It must still satisfy the renamed 4a_validation phase
    # and must NOT be reported as an unrecognized extra key.
    state = _base_state()
    state["phases_completed"]["2c_verification"] = {"status": "passed"}
    state["phases_completed"]["4b_validation"] = {"status": "passed"}
    report = validate(state, "dummy-state.json")
    optional = {r.key: r for r in report.optional_results}
    assert "4a_validation" in optional
    assert optional["4a_validation"].status == "ok"
    assert "via legacy key '4b_validation'" in optional["4a_validation"].detail
    assert "4b_validation" not in report.extra_keys
    assert not report.has_failures


# --- Phase 0.6 conditional requirement (standalone .sql present) -------------

def test_phase_0_6_required_when_standalone_sql_present(tmp_path):
    out = tmp_path / "Output"
    out.mkdir()
    (out / "q.sql").write_text("SELECT 1 QUALIFY ROW_NUMBER() OVER (ORDER BY x) = 1")
    state = _base_state()
    state["migrated_dir"] = str(out)  # 0_6_sql_rewrite NOT recorded
    report = validate(state, "dummy-state.json")
    failures = {r.key for r in report.results if r.is_failure}
    assert "0_6_sql_rewrite" in failures


def test_phase_0_6_satisfied_when_recorded(tmp_path):
    out = tmp_path / "Output"
    out.mkdir()
    (out / "q.sql").write_text("SELECT 1 QUALIFY ROW_NUMBER() OVER (ORDER BY x) = 1")
    state = _base_state()
    state["migrated_dir"] = str(out)
    state["phases_completed"]["0_6_sql_rewrite"] = {"status": "passed"}
    report = validate(state, "dummy-state.json")
    failures = {r.key for r in report.results if r.is_failure}
    assert "0_6_sql_rewrite" not in failures


def test_phase_0_6_optional_when_no_standalone_sql(tmp_path):
    out = tmp_path / "Output"
    out.mkdir()
    (out / "job.py").write_text("print('no sql here')")
    state = _base_state()
    state["migrated_dir"] = str(out)  # 0_6 not recorded, but no .sql
    report = validate(state, "dummy-state.json")
    failures = {r.key for r in report.results if r.is_failure}
    assert "0_6_sql_rewrite" not in failures


def test_databricks_json_sql_notebook_does_not_require_0_6(tmp_path):
    out = tmp_path / "Output"
    out.mkdir()
    (out / "nb.sql").write_text('{"cells": []}')  # native-JSON notebook, not standalone
    state = _base_state()
    state["migrated_dir"] = str(out)
    report = validate(state, "dummy-state.json")
    failures = {r.key for r in report.results if r.is_failure}
    assert "0_6_sql_rewrite" not in failures


# --- Java required phase set ----------------------------------------------- #

def test_java_required_phase_set():
    java_phases = _required_phases_for_language("java")
    keys = {p[0] for p in java_phases}
    assert "0_5c_javaparser" in keys, "Java phases must include 0_5c_javaparser"
    assert "1_analysis" in keys, "Java phases must include 1_analysis"
    assert "2a_fallback" in keys, "Java phases must include 2a_fallback (like Scala)"
    assert "4_reports" in keys, "Java phases must include 4_reports"
    assert "0_5_preprocess" not in keys, "Java phases must NOT include 0_5_preprocess"
    assert "0_5b_scalafix" not in keys, "Java phases must NOT include 0_5b_scalafix"
    assert "2a_coverage" not in keys, "Java phases must NOT include 2a_coverage (Python-only)"
