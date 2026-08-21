"""Unit tests for ``verify_migration`` — evidence-based migration status.

These assert that hard, deterministic evidence (real fixer markers vs the
fallback-only signature) drives the status, and that the fuzzy snippet probe
never overrides it. Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_verify_migration.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from verify_migration import (
    FALLBACK_SIGNATURE,
    HUMAN_ACTION_CODE,
    STATUS_MIGRATED,
    STATUS_NOT_ATTEMPTED,
    STATUS_PARTIAL,
    STATUS_TRIVIAL,
    _phase_2c_completion_entry,
    classify_file,
    reconcile,
    verify_migration,
)

FALLBACK_HEADER = f'"""\nSCOS Migration Output\nManual review required — this file was {FALLBACK_SIGNATURE}\n"""\n'
REAL_FIXER_EDIT = "# SCOS: [SPRKCNTPY4002] SparkContext.getOrCreate() removed.\n"
FALLBACK_ANNOTATION = "# SCOS: [SPRKCNTPY0099] PySpark import — review.\n"
SPARK_LINE = "from pyspark.sql import SparkSession\n"


def _write(tmp_path: Path, rel: str, body: str) -> str:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return rel


def _classify(tmp_path, rel, *, done=False, findings=None):
    return classify_file(
        rel,
        recorded_done=done,
        migrated_dir=str(tmp_path),
        findings=findings or [],
    )


# --- hard evidence wins over the fuzzy snippet probe -------------------------


def test_real_fixer_marker_is_migrated_even_with_fallback_header(tmp_path):
    """The main.py case: fallback header + real SPRKCNTPY4002 edits => migrated."""
    rel = _write(tmp_path, "src/main.py", FALLBACK_HEADER + SPARK_LINE + REAL_FIXER_EDIT)
    findings = [{"code": "self.sc = SparkContext.getOrCreate(conf=self.jobConf)", "final_risk": 1.0}]
    row = _classify(tmp_path, rel, done=True, findings=findings)
    assert row["status"] == STATUS_MIGRATED
    assert row["fixer_annotated"] is True
    assert row["fallback_only"] is False
    assert row["disagreement"] is None


def test_fallback_only_annotations_do_not_count_as_real_edit(tmp_path):
    """Only 0099 import annotations => LLM never really processed it."""
    rel = _write(
        tmp_path, "src/t.py", FALLBACK_HEADER + SPARK_LINE + FALLBACK_ANNOTATION
    )
    row = _classify(tmp_path, rel, done=True)
    assert row["status"] == STATUS_PARTIAL
    assert row["fallback_only"] is True
    assert row["disagreement"] is not None  # done-but-fallback-only


# --- status precedence -------------------------------------------------------


def test_fallback_only_trivial_when_no_spark_surface(tmp_path):
    rel = _write(tmp_path, "src/__init__.py", FALLBACK_HEADER + "x = 1\n")
    row = _classify(tmp_path, rel, done=True)
    assert row["status"] == STATUS_TRIVIAL
    assert row["disagreement"] is None


def test_recorded_done_clean_file_is_migrated(tmp_path):
    rel = _write(tmp_path, "src/clean.py", SPARK_LINE + "df = spark.range(1)\n")
    row = _classify(tmp_path, rel, done=True)
    assert row["status"] == STATUS_MIGRATED


def test_missing_file_not_recorded_is_not_attempted(tmp_path):
    row = _classify(tmp_path, "src/ghost.py", done=False)
    assert row["status"] == STATUS_NOT_ATTEMPTED


def test_missing_file_recorded_done_is_disagreement(tmp_path):
    row = _classify(tmp_path, "src/ghost.py", done=True)
    assert row["status"] == STATUS_NOT_ATTEMPTED
    assert row["disagreement"] == "state=done but output file is missing"


def test_not_done_with_real_edits_flags_disagreement(tmp_path):
    rel = _write(tmp_path, "src/orphan.py", SPARK_LINE + REAL_FIXER_EDIT)
    row = _classify(tmp_path, rel, done=False)
    assert row["status"] == STATUS_MIGRATED
    assert row["disagreement"] is not None  # not-done-but-migrated


def test_resolution_in_analysis_counts_as_engagement(tmp_path):
    """A file whose only fixer evidence is a `resolution` in analysis.json (no
    inline # SCOS comment, e.g. an issue judged contextually safe) is still
    counted as migrated rather than partial."""
    rel = _write(tmp_path, "src/safe.py", SPARK_LINE + "df = spark.range(1)\n")
    findings = [{
        "final_risk": 0.95,
        "resolution": "safe",
        "resolution_reason": "window has explicit orderBy('id') -> deterministic",
    }]
    row = _classify(tmp_path, rel, done=True, findings=findings)
    assert row["status"] == STATUS_MIGRATED
    assert row["fixer_annotated"] is False
    assert row["resolved_in_analysis"] is True
    assert row["disagreement"] is None


# --- advisory needs_review ---------------------------------------------------


def test_needs_review_when_high_risk_snippet_survives(tmp_path):
    snippet = "df = spark.sparkContext.broadcast(big_lookup_table_value)"
    rel = _write(tmp_path, "src/r.py", SPARK_LINE + REAL_FIXER_EDIT + snippet + "\n")
    row = _classify(tmp_path, rel, done=True, findings=[{"code": snippet, "final_risk": 1.0}])
    assert row["status"] == STATUS_MIGRATED
    assert row["needs_review"] is True
    assert row["residual_high_risk"] == 1


# --- end-to-end summary ------------------------------------------------------


def test_verify_migration_summary_and_disagreements(tmp_path):
    a = _write(tmp_path, "a.py", FALLBACK_HEADER + SPARK_LINE + REAL_FIXER_EDIT)
    b = _write(tmp_path, "b.py", FALLBACK_HEADER + SPARK_LINE + FALLBACK_ANNOTATION)
    c = _write(tmp_path, "c.py", FALLBACK_HEADER + "y = 2\n")
    state = {"manifest": [a, b, c], "processed_files": [a, b, c]}
    result = verify_migration(state, analysis=[], migrated_dir=str(tmp_path))
    s = result["summary"]
    assert s["total"] == 3
    assert s["by_status"] == {STATUS_MIGRATED: 1, STATUS_PARTIAL: 1, STATUS_TRIVIAL: 1}
    assert s["disagreements"] == 1  # b.py: done but fallback-only
    assert s["not_attempted"] == 0
    assert result["disagreements"][0]["file"] == b


def test_verify_migration_summary_counts_not_attempted(tmp_path):
    state = {"manifest": ["src/ghost.py"], "processed_files": []}
    result = verify_migration(state, analysis=[], migrated_dir=str(tmp_path))
    assert result["summary"]["by_status"] == {STATUS_NOT_ATTEMPTED: 1}
    assert result["summary"]["not_attempted"] == 1
    assert result["not_attempted"][0]["file"] == "src/ghost.py"


# --- closing the loop: reconcile --------------------------------------------


def _kipawa_like(tmp_path):
    a = _write(tmp_path, "a.py", FALLBACK_HEADER + SPARK_LINE + REAL_FIXER_EDIT)
    b = _write(tmp_path, "b.py", FALLBACK_HEADER + SPARK_LINE + FALLBACK_ANNOTATION)
    c = _write(tmp_path, "c.py", FALLBACK_HEADER + "y = 2\n")
    state = {"manifest": [a, b, c], "processed_files": [a, b, c]}
    # 19-style stale noise + a couple of real findings to preserve.
    analysis = [
        {"file": b, "code": "SPRKCNTPY0099", "category": "Partial Migration", "final_risk": 0.9},
        {"file": a, "code": "SPRKCNTPY0099", "category": "Partial Migration", "final_risk": 0.9},
        {"file": c, "code": "SPRKCNTPY0099", "category": "Partial Migration", "final_risk": 0.9},
        {"file": a, "code": "df = spark.range(1)", "final_risk": 0.1},
    ]
    return state, analysis, (a, b, c)


def test_reconcile_drives_disagreements_to_zero(tmp_path):
    state, analysis, (a, b, c) = _kipawa_like(tmp_path)
    before = verify_migration(state, analysis, str(tmp_path))
    assert before["summary"]["disagreements"] == 1

    rec = reconcile(state, analysis, before)
    after = verify_migration(rec["state"], rec["analysis"], str(tmp_path))
    assert after["summary"]["disagreements"] == 0


def test_reconcile_marks_only_verified_partial_in_analysis(tmp_path):
    state, analysis, (a, b, c) = _kipawa_like(tmp_path)
    rec = reconcile(state, analysis, verify_migration(state, analysis, str(tmp_path)))

    partial_entries = [
        f for f in rec["analysis"] if f.get("category") == "Partial Migration"
    ]
    # Exactly one verified human-action entry — for b.py, not all three.
    assert len(partial_entries) == 1
    assert partial_entries[0]["file"] == b
    assert partial_entries[0]["code"] == HUMAN_ACTION_CODE
    assert partial_entries[0]["verified_by"] == "verify_migration"
    # The real low-risk finding on a.py is preserved.
    assert any(f.get("code") == "df = spark.range(1)" for f in rec["analysis"])


def test_reconcile_demotes_partial_from_state_completion(tmp_path):
    state, analysis, (a, b, c) = _kipawa_like(tmp_path)
    rec = reconcile(state, analysis, verify_migration(state, analysis, str(tmp_path)))
    assert b not in rec["state"]["processed_files"]
    assert rec["state"]["needs_human_action"] == [b]
    # migrated / trivial stay recorded done.
    assert a in rec["state"]["processed_files"]
    assert c in rec["state"]["processed_files"]


def test_reconcile_uses_scala_code_for_scala(tmp_path):
    state, analysis, (a, b, c) = _kipawa_like(tmp_path)
    rec = reconcile(
        state, analysis, verify_migration(state, analysis, str(tmp_path)), language="scala"
    )
    partial = [f for f in rec["analysis"] if f.get("category") == "Partial Migration"]
    assert partial[0]["code"] == "SPRKCNTSCL0099"


def test_reconcile_records_unrecorded_migration(tmp_path):
    orphan = _write(tmp_path, "orphan.py", SPARK_LINE + REAL_FIXER_EDIT)
    state = {"manifest": [orphan], "processed_files": []}
    before = verify_migration(state, [], str(tmp_path))
    assert before["summary"]["disagreements"] == 1  # not-done but migrated
    rec = reconcile(state, [], before)
    assert orphan in rec["state"]["processed_files"]
    after = verify_migration(rec["state"], rec["analysis"], str(tmp_path))
    assert after["summary"]["disagreements"] == 0


def test_phase_2c_completion_entry_shape():
    entry = _phase_2c_completion_entry(
        partial=["src/a.py"],
        migrated_unrecorded=["src/b.py"],
    )
    assert entry == {
        "status": "passed",
        "disagreements": 0,
        "not_attempted": 0,
        "needs_human_action": ["src/a.py"],
        "verified_human_action_count": 1,
        "recorded_migrated_count": 1,
    }


def test_verify_migration_separates_duplicate_basenames_by_exact_path(tmp_path):
    foo = _write(
        tmp_path, "src/foo/main.py", FALLBACK_HEADER + SPARK_LINE + REAL_FIXER_EDIT
    )
    bar = _write(
        tmp_path,
        "src/bar/main.py",
        FALLBACK_HEADER + SPARK_LINE + FALLBACK_ANNOTATION,
    )
    state = {"manifest": [foo, bar], "processed_files": [foo, bar]}
    analysis = [
        {"file": foo, "code": "foo-only", "final_risk": 0.9},
        {"file": bar, "code": "SPRKCNTPY0099", "category": "Partial Migration", "final_risk": 0.9},
    ]
    rows = {
        r["file"]: r
        for r in verify_migration(state, analysis, str(tmp_path))["files"]
    }
    assert rows[foo]["status"] == STATUS_MIGRATED
    assert rows[bar]["status"] == STATUS_PARTIAL


def test_verify_migration_uses_unique_basename_fallback_when_safe(tmp_path):
    rel = _write(tmp_path, "src/only/place.py", SPARK_LINE + REAL_FIXER_EDIT)
    state = {"manifest": [rel], "processed_files": ["/abs/Output/src/only/place.py"]}
    analysis = [{"file": "place.py", "code": REAL_FIXER_EDIT, "final_risk": 0.9}]
    row = verify_migration(state, analysis, str(tmp_path))["files"][0]
    assert row["status"] == STATUS_MIGRATED
    assert row["recorded_done"] is True
    assert row["residual_high_risk"] == 1


def test_reconcile_demotes_only_exact_duplicate_basename_match(tmp_path):
    foo = _write(
        tmp_path, "src/foo/main.py", FALLBACK_HEADER + SPARK_LINE + REAL_FIXER_EDIT
    )
    bar = _write(
        tmp_path,
        "src/bar/main.py",
        FALLBACK_HEADER + SPARK_LINE + FALLBACK_ANNOTATION,
    )
    state = {"manifest": [foo, bar], "processed_files": [foo, bar]}
    before = verify_migration(state, [], str(tmp_path))
    rec = reconcile(state, [], before, migrated_dir=str(tmp_path))
    assert foo in rec["state"]["processed_files"]
    assert bar not in rec["state"]["processed_files"]
    assert rec["state"]["needs_human_action"] == [bar]


def test_reconcile_replaces_stale_needs_human_action(tmp_path):
    a = _write(tmp_path, "src/a.py", FALLBACK_HEADER + SPARK_LINE + FALLBACK_ANNOTATION)
    state = {
        "manifest": [a],
        "processed_files": [a],
        "needs_human_action": ["src/stale.py"],
    }
    rec = reconcile(state, [], verify_migration(state, [], str(tmp_path)))
    assert rec["state"]["needs_human_action"] == [a]


def test_verify_migration_cli_fails_when_not_attempted_present(tmp_path):
    state_path = tmp_path / "migration_state.json"
    state_path.write_text(json.dumps({"manifest": ["src/ghost.py"], "processed_files": []}))
    proc = subprocess.run(
        [sys.executable, "scripts/verify_migration.py", "--state", str(state_path)],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "Not attempted" in proc.stdout
    assert "Phase 2 coverage should have caught this" in proc.stderr


def test_verify_migration_cli_reports_invalid_state_json(tmp_path):
    state_path = tmp_path / "migration_state.json"
    state_path.write_text("{not valid json", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "scripts/verify_migration.py", "--state", str(state_path)],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "migration_state.json is not valid JSON" in proc.stderr


def test_verify_migration_cli_reports_invalid_analysis_json(tmp_path):
    state_path = tmp_path / "migration_state.json"
    analysis_path = tmp_path / "analysis.json"
    state_path.write_text(json.dumps({"manifest": [], "processed_files": []}), encoding="utf-8")
    analysis_path.write_text("{not valid json", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/verify_migration.py",
            "--state",
            str(state_path),
            "--analysis",
            str(analysis_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "analysis.json is not valid JSON" in proc.stderr
