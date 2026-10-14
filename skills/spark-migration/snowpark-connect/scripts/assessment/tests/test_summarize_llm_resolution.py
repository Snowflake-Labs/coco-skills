"""Tests for summarize_llm_resolution.py — groups by LLM-assigned severity."""
from __future__ import annotations

import json

from summarize_llm_resolution import main, summarize


def _ir(**llm) -> dict:
    """AssessmentIR with an llm_resolved_data_edges block (and optional baselines)."""
    baseline_edges = llm.pop("_baseline_edges", 0)
    baseline_imports = llm.pop("_baseline_imports", 0)
    return {
        "unresolved_data_edges": [{}] * baseline_edges,
        "unresolved_dynamic_imports": [{}] * baseline_imports,
        "llm_resolved_data_edges": llm,
    }


def _unres(severity: str, why: str = "reason", file: str = "a.py", line: int = 1) -> dict:
    return {"file": file, "line": line, "kind": "read",
            "why_unresolvable": why, "severity": severity}


class TestCounts:
    def test_resolved_and_discovered_counts(self):
        s = summarize(_ir(
            _baseline_edges=3, _baseline_imports=2,
            edges=[{"source": "resolved_unresolved"},
                   {"source": "resolved_unresolved"},
                   {"source": "newly_discovered"}],
        ))
        assert s["resolved"] == 2 and s["newly_discovered"] == 1
        assert s["baseline_unresolved_edges"] == 3
        assert s["baseline_unresolved_imports"] == 2

    def test_empty_ir_is_clean(self):
        s = summarize({})
        assert s["clean"] is True
        assert s["critical"] == [] and s["informational"] == [] and s["benign_count"] == 0


class TestSeverityGrouping:
    def test_critical_edge_grouped_and_not_clean(self):
        s = summarize(_ir(unresolvable_edges=[
            _unres("critical", "caller absent from export", file="x.py", line=9)]))
        assert s["clean"] is False
        assert s["critical"] == [{"file": "x.py", "line": 9, "why": "caller absent from export"}]

    def test_benign_is_counted_not_listed_and_stays_clean(self):
        s = summarize(_ir(unresolvable_edges=[_unres("benign", "in-memory to_json")]))
        assert s["clean"] is True
        assert s["benign_count"] == 1
        assert s["critical"] == [] and s["informational"] == []

    def test_informational_surfaced_but_clean(self):
        s = summarize(_ir(unresolvable_edges=[_unres("informational", "config-driven S3 path")]))
        assert s["clean"] is True  # informational is a blind spot, not a blocker
        assert len(s["informational"]) == 1

    def test_mixed_severities(self):
        s = summarize(_ir(unresolvable_edges=[
            _unres("critical", "missing upstream table"),
            _unres("informational", "runtime path"),
            _unres("benign", "dead code"),
            _unres("benign", "scanner misclassification"),
        ]))
        assert len(s["critical"]) == 1
        assert len(s["informational"]) == 1
        assert s["benign_count"] == 2
        assert s["clean"] is False

    def test_unresolvable_import_severity_grouped(self):
        s = summarize(_ir(resolved_imports=[
            {"file": "d.py", "line": 3, "resolution_type": "unresolvable",
             "why_unresolvable": "module not in export", "severity": "critical"},
            {"file": "d.py", "line": 4, "resolution_type": "literal_found"},  # ignored
        ]))
        assert s["critical"] == [{"file": "d.py", "line": 3, "why": "module not in export"}]

    def test_unclassified_import_falls_to_informational(self):
        # An unresolvable import with no severity must be surfaced, never dropped,
        # but not raised as a blocker.
        s = summarize(_ir(resolved_imports=[
            {"file": "d.py", "line": 3, "resolution_type": "unresolvable",
             "why_unresolvable": "runtime dispatch"}]))
        assert s["clean"] is True
        assert len(s["informational"]) == 1

    def test_insights_passed_through(self):
        s = summarize(_ir(llm_insights=["a", "b"]))
        assert s["insights"] == ["a", "b"]


class TestCli:
    def test_main_prints_summary_json(self, tmp_path, capsys):
        p = tmp_path / "AssessmentIR.json"
        p.write_text(json.dumps(_ir(unresolvable_edges=[_unres("critical")])))
        assert main([str(p)]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["clean"] is False and len(out["critical"]) == 1

    def test_main_missing_arg_errors(self):
        assert main([]) == 3

    def test_run_bad_path_errors(self, tmp_path):
        assert main([str(tmp_path / "nope.json")]) == 3
