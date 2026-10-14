"""Tests for the Streamlit validation report's data layer (validation_report_data).

The Streamlit app itself is not unit-tested (it needs a live render), but the
data contract it consumes — `load_validation_run` over the Scala validator's
on-disk artifacts — is. This guards the Scala-specific adaptations: analysis is
read from `shared/analysis.json` (not a PySpark schemas/manifest split), and the
Scala milestones + per-trial `migration_fix_commits` surface correctly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPORT = Path(__file__).resolve().parents[1] / "report"
sys.path.insert(0, str(_REPORT))

import validation_report_data as rd  # noqa: E402


def _make_run(root: Path) -> None:
    (root / "results").mkdir(parents=True, exist_ok=True)
    (root / "shared").mkdir(parents=True, exist_ok=True)
    run_index = {
        "run": {"id": "abc123", "status": "partial", "connection": "c",
                "database": "SCOS_VALIDATION", "schema_namespace": "S"},
        "milestones": {"synth_survey": {"status": "done"},
                       "workload_built": {"status": "done"},
                       "patches_authored": {"status": "pending"}},
        "entrypoints": [{
            "id": "ep1",
            "source_path": "src/Job.scala",
            "phase_a": {"verdict": "baseline_produced", "iters": [1]},
            "phase_b": {"verdict": "passed", "iters": [1],
                        "migration_fix_commits": [{"sha": "deadbeef", "subject": "fix abs(date)"}]},
            "comparison": {"verdict": "match", "diffs": [], "documented_divergences": []},
            "trial_dir": "results/phase_b/ep1/",
            "verdict": {"overall": "passed", "reason": "matched baseline"},
        }],
        "artifacts_index": {
            "analysis": "shared/analysis.json",
            "patch_blueprint": "shared/patch_blueprint.json",
            "mock_data": [{"trial_id": "ep1", "files": ["shared/mock_data/ep1/t.parquet"]}],
            "rendered_tests": ["tests/TestEp1Spec.scala"],
        },
        "fixer_dispatches": [{"iter": 1, "error_class": "workload_failure", "outcome": "success"}],
        "documented_divergences": [],
        "warnings": [],
        "parse_errors": [],
    }
    (root / "run_index.json").write_text(json.dumps(run_index))
    (root / "results" / "summary.json").write_text(json.dumps(
        {"decision": {"overall": "partial", "phase_b_passes": 1}, "warnings": []}))
    (root / "results" / "REPORT.md").write_text("# Validation Report\n")
    (root / "shared" / "analysis.json").write_text(json.dumps(
        {"entrypoints": [{"id": "ep1", "external_sources": [{"id": "t", "category": "table"}],
                          "sinks": [{"id": "out"}]}]}))
    (root / "events.jsonl").write_text(
        json.dumps({"ts": "2026-01-01T00:00:00Z", "kind": "milestone_completed", "milestone": "synth_survey"}) + "\n")


def test_load_validation_run_scala(tmp_path):
    root = tmp_path / "Validation"
    _make_run(root)
    data = rd.load_validation_run(root)

    # analysis comes from shared/analysis.json (Scala), not a manifest split
    assert data.analysis is not None
    assert data.analysis["entrypoints"][0]["id"] == "ep1"
    assert "sql_files" not in data.analysis  # PySpark-only field must be absent

    # one entrypoint surfaced, with the P1 migration-fix attribution
    assert len(data.entrypoints) == 1
    assert data.entrypoints[0]["phase_b"]["migration_fix_commits"][0]["sha"] == "deadbeef"

    # Scala milestones present in the milestone model
    names = {m["id"] for m in data.milestones}
    assert {"synth_survey", "workload_built", "patches_authored"} <= names
    assert "shim_curator_done" not in names  # removed: shims are reactive (no curation step)
    assert "adapter_done" not in names  # renamed: adapter -> patch-author, milestone -> workload_built

    # generic builders populated
    assert any(item["group"] == "analysis" for item in data.artifact_inventory)
    assert data.pipeline_steps  # execution pipeline rendered
    assert data.fixer_dispatches and data.run_metrics.get("run_id") == "abc123"


def test_load_validation_run_requires_run_index(tmp_path):
    root = tmp_path / "Validation"
    root.mkdir()
    try:
        rd.load_validation_run(root)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_milestone_descriptions_are_scala():
    # No PySpark-only milestone wording leaked through the vendoring.
    assert "workload_built" in rd.MILESTONE_DESCRIPTIONS
    assert "patches_authored" in rd.MILESTONE_DESCRIPTIONS
    assert "shim_curator_done" not in rd.MILESTONE_ORDER
    joined = " ".join(rd.MILESTONE_DESCRIPTIONS.values()).lower()
    assert "pytest" not in joined and "virtual environment" not in joined
