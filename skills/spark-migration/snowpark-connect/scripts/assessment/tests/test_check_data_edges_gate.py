"""Tests for check_data_edges_gate.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from check_data_edges_gate import run


def _write_ir(tmp_path: Path, ir: dict) -> Path:
    p = tmp_path / "AssessmentIR.json"
    p.write_text(json.dumps(ir))
    return p


def _workload(tmp_path: Path, files: list[str]) -> Path:
    """Create a fake workload directory with the given relative paths."""
    wdir = tmp_path / "Output"
    for rel in files:
        full = wdir / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("# stub")
    return wdir


# ---------------------------------------------------------------------------
# Exit 0 — full coverage
# ---------------------------------------------------------------------------

def _edge(file: str, kind: str = "read", sig: str = "tbl", src: str = "newly_discovered") -> dict:
    return {"file": file, "line": 1, "kind": kind, "source": src,
            "resolved_signature": sig, "resolution_type": "traced"}


class TestExit0:
    def test_all_files_analyzed(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["etl/load.py", "etl/transform.py"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["etl/load.py", "etl/transform.py"],
                "excluded_files": [],
                "edges": [_edge("etl/load.py"), _edge("etl/transform.py")],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "pass"
        assert out["total_files"] == 2

    def test_mix_analyzed_and_excluded(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["etl/load.py", "utils/__init__.py"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["etl/load.py"],
                "excluded_files": ["utils/__init__.py"],
                "edges": [_edge("etl/load.py")],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 0

    def test_sql_and_ipynb_files_covered(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["queries.sql", "notebook.ipynb", "main.py"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["queries.sql", "notebook.ipynb", "main.py"],
                "excluded_files": [],
                "edges": [_edge("queries.sql"), _edge("notebook.ipynb"), _edge("main.py")],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 0

    def test_non_data_extensions_ignored(self, tmp_path, capsys):
        """txt, md, yaml files in workload are not required to be covered."""
        wdir = _workload(tmp_path, ["etl/load.py", "README.md", "config.yaml"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["etl/load.py"],
                "excluded_files": [],
                "edges": [_edge("etl/load.py")],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 0

    def test_counts_in_pass_output(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["a.py"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["a.py"],
                "excluded_files": [],
                "edges": [
                    {"file": "a.py", "line": 1, "kind": "read",
                     "source": "resolved_unresolved", "resolved_signature": "s3://x", "resolution_type": "traced"},
                    {"file": "a.py", "line": 2, "kind": "write",
                     "source": "newly_discovered", "resolved_signature": "dynamo://t", "resolution_type": "traced"},
                ],
                "unresolvable_edges": [
                    {"file": "a.py", "line": 3, "kind": "read",
                     "why_unresolvable": "argparse", "severity": "benign"},
                ],
            },
        })
        assert run(ir, wdir) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["resolved_count"] == 1
        assert out["newly_discovered_count"] == 1
        assert out["unresolvable_count"] == 1

    def test_no_workload_dir_passes_when_lists_present(self, tmp_path, capsys):
        """Without workload_dir the gate only verifies structural validity."""
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["a.py"],
                "excluded_files": ["b.py"],
                "edges": [],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, None) == 0
        out = json.loads(capsys.readouterr().out)
        assert "coverage check skipped" in out.get("note", "")


# ---------------------------------------------------------------------------
# Exit 2 — coverage gaps
# ---------------------------------------------------------------------------

class TestExit2:
    def test_single_uncovered_file(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["etl/load.py", "etl/transform.py"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["etl/load.py"],
                "excluded_files": [],
                "edges": [],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "fail"
        assert out["gap_count"] == 1
        assert "etl/transform.py" in out["gaps"]

    def test_multiple_uncovered_files(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["a.py", "b.py", "c.sql"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["a.py"],
                "excluded_files": [],
                "edges": [],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["gap_count"] == 2
        assert set(out["gaps"]) == {"b.py", "c.sql"}

    def test_empty_analyzed_and_excluded(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["load.py"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": [],
                "excluded_files": [],
                "edges": [],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["gap_count"] == 1

    def test_gaps_list_is_sorted(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["z.py", "a.py", "m.sql"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": [],
                "excluded_files": [],
                "edges": [],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["gaps"] == sorted(out["gaps"])

    def test_analyzed_file_with_zero_edges_fails(self, tmp_path, capsys):
        """A file in analyzed_files with no edges or unresolvable entries is an edge gap."""
        wdir = _workload(tmp_path, ["etl/load.py", "sql/queries.sql"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["etl/load.py", "sql/queries.sql"],
                "excluded_files": [],
                "edges": [
                    {"file": "etl/load.py", "line": 1, "kind": "read",
                     "source": "newly_discovered", "resolved_signature": "s3://x", "resolution_type": "traced"},
                ],
                # sql/queries.sql has no edges and no unresolvable entries
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "fail"
        assert out["edge_gap_count"] == 1
        assert "sql/queries.sql" in out["edge_gaps"]

    def test_analyzed_file_with_only_unresolvable_passes_edge_check(self, tmp_path, capsys):
        """A file in analyzed_files with only unresolvable entries (no resolved edges) satisfies the edge check."""
        wdir = _workload(tmp_path, ["etl/load.py"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["etl/load.py"],
                "excluded_files": [],
                "edges": [],
                "unresolvable_edges": [
                    {"file": "etl/load.py", "line": 5, "kind": "write",
                     "why_unresolvable": "argparse-driven path", "severity": "informational"},
                ],
            },
        })
        assert run(ir, wdir) == 0

    def test_edge_gaps_and_coverage_gaps_both_reported(self, tmp_path, capsys):
        """Both missing files and zero-edge analyzed files appear in the exit-2 output."""
        wdir = _workload(tmp_path, ["a.py", "b.sql", "c.py"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["a.py", "b.sql"],  # c.py uncovered
                "excluded_files": [],
                "edges": [
                    {"file": "a.py", "line": 1, "kind": "read",
                     "source": "newly_discovered", "resolved_signature": "tbl", "resolution_type": "traced"},
                ],
                # b.sql analyzed but zero edges
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "fail"
        assert "c.py" in out["gaps"]
        assert "b.sql" in out["edge_gaps"]


# ---------------------------------------------------------------------------
# Exit 3 — IO / parse errors
# ---------------------------------------------------------------------------

class TestExit3:
    def test_ir_not_found(self, tmp_path, capsys):
        wdir = _workload(tmp_path, [])
        assert run(tmp_path / "nonexistent.json", wdir) == 3
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "error"
        assert "not found" in out["message"]

    def test_ir_malformed_json(self, tmp_path, capsys):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        assert run(p, None) == 3
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "error"

    def test_no_llm_data_in_ir(self, tmp_path, capsys):
        ir = _write_ir(tmp_path, {"unresolved_data_edges": []})
        wdir = _workload(tmp_path, ["a.py"])
        assert run(ir, wdir) == 3
        out = json.loads(capsys.readouterr().out)
        assert "llm_resolved_data_edges" in out["message"]

    def test_empty_ir_object(self, tmp_path, capsys):
        ir = _write_ir(tmp_path, {})
        assert run(ir, None) == 3

    def test_workload_dir_not_found(self, tmp_path, capsys):
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": [],
                "excluded_files": [],
                "edges": [],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, tmp_path / "nonexistent_dir") == 3
        out = json.loads(capsys.readouterr().out)
        assert "workload_dir" in out["message"]

    def test_no_workload_dir_and_empty_lists_is_error(self, tmp_path, capsys):
        """Without workload_dir AND without any coverage lists, the gate can't check anything."""
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "edges": [],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, None) == 3
        out = json.loads(capsys.readouterr().out)
        assert "analyzed_files" in out["message"]


# ---------------------------------------------------------------------------
# Exit 2 — reconciliation leaks (data edges + dynamic imports)
# ---------------------------------------------------------------------------

class TestReconciliation:
    def test_data_edge_leak_fails(self, tmp_path, capsys):
        """An unresolved_data_edges entry neither resolved nor confirmed
        unresolvable is a leak → exit 2 with data_leaks."""
        wdir = _workload(tmp_path, ["etl/load.py"])
        ir = _write_ir(tmp_path, {
            "unresolved_data_edges": [
                {"file": "etl/load.py", "line": 10, "kind": "read",
                 "call_expr": "spark.sql", "arg_expr": "q", "reason": "ast.Name"},
            ],
            "llm_resolved_data_edges": {
                "analyzed_files": ["etl/load.py"],
                "excluded_files": [],
                "edges": [_edge("etl/load.py")],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "fail"
        assert out["data_leak_count"] == 1
        assert out["data_leaks"][0]["line"] == 10
        assert out["data_edges_accounted"] == "0/1"

    def test_data_edge_resolved_passes(self, tmp_path, capsys):
        """Same edge, now resolved with matching (file,line,kind) → pass."""
        wdir = _workload(tmp_path, ["etl/load.py"])
        ir = _write_ir(tmp_path, {
            "unresolved_data_edges": [
                {"file": "etl/load.py", "line": 10, "kind": "read",
                 "call_expr": "spark.sql", "arg_expr": "q", "reason": "ast.Name"},
            ],
            "llm_resolved_data_edges": {
                "analyzed_files": ["etl/load.py"],
                "excluded_files": [],
                "edges": [{"file": "etl/load.py", "line": 10, "kind": "read",
                           "source": "resolved_unresolved", "resolved_signature": "t",
                           "resolution_type": "traced"}],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["data_edges_accounted"] == "1/1"

    def test_data_edge_confirmed_unresolvable_passes(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["etl/load.py"])
        ir = _write_ir(tmp_path, {
            "unresolved_data_edges": [
                {"file": "etl/load.py", "line": 10, "kind": "read",
                 "call_expr": "spark.sql", "arg_expr": "q", "reason": "ast.Name"},
            ],
            "llm_resolved_data_edges": {
                "analyzed_files": ["etl/load.py"],
                "excluded_files": [],
                "edges": [_edge("etl/load.py")],
                "unresolvable_edges": [{"file": "etl/load.py", "line": 10,
                                        "kind": "read", "why_unresolvable": "runtime",
                                        "severity": "benign"}],
            },
        })
        assert run(ir, wdir) == 0

    def test_line_mismatch_still_leaks(self, tmp_path, capsys):
        """Resolving with a different line number does NOT reconcile."""
        wdir = _workload(tmp_path, ["etl/load.py"])
        ir = _write_ir(tmp_path, {
            "unresolved_data_edges": [
                {"file": "etl/load.py", "line": 10, "kind": "read",
                 "call_expr": "spark.sql", "arg_expr": "q", "reason": "ast.Name"},
            ],
            "llm_resolved_data_edges": {
                "analyzed_files": ["etl/load.py"],
                "excluded_files": [],
                "edges": [{"file": "etl/load.py", "line": 99, "kind": "read",
                           "source": "resolved_unresolved", "resolved_signature": "t",
                           "resolution_type": "traced"}],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["data_leak_count"] == 1

    def test_import_leak_fails(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["orch.py"])
        ir = _write_ir(tmp_path, {
            "unresolved_dynamic_imports": [
                {"file": "orch.py", "line": 20, "kind": "spec_from_file",
                 "reason": "path not resolvable", "raw_expr": "spec(p)"},
            ],
            "llm_resolved_data_edges": {
                "analyzed_files": ["orch.py"],
                "excluded_files": [],
                "edges": [_edge("orch.py")],
                "unresolvable_edges": [],
                "resolved_imports": [],
            },
        })
        assert run(ir, wdir) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["import_leak_count"] == 1
        assert out["imports_accounted"] == "0/1"

    def test_import_resolved_passes(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["orch.py", "task.py"])
        ir = _write_ir(tmp_path, {
            "unresolved_dynamic_imports": [
                {"file": "orch.py", "line": 20, "kind": "spec_from_file",
                 "reason": "path not resolvable", "raw_expr": "spec(p)"},
            ],
            "llm_resolved_data_edges": {
                "analyzed_files": ["orch.py", "task.py"],
                "excluded_files": [],
                "edges": [_edge("orch.py"), _edge("task.py")],
                "unresolvable_edges": [],
                "resolved_imports": [{"file": "orch.py", "line": 20,
                                      "kind": "spec_from_file",
                                      "resolved_targets": ["task.py"],
                                      "resolution_type": "traced"}],
            },
        })
        assert run(ir, wdir) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["imports_resolved_count"] == 1
        assert out["imports_accounted"] == "1/1"

    def test_import_confirmed_unresolvable_passes(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["orch.py"])
        ir = _write_ir(tmp_path, {
            "unresolved_dynamic_imports": [
                {"file": "orch.py", "line": 20, "kind": "spec_from_file",
                 "reason": "path not resolvable", "raw_expr": "spec(p)"},
            ],
            "llm_resolved_data_edges": {
                "analyzed_files": ["orch.py"],
                "excluded_files": [],
                "edges": [_edge("orch.py")],
                "unresolvable_edges": [],
                "resolved_imports": [{"file": "orch.py", "line": 20,
                                      "kind": "spec_from_file",
                                      "resolved_targets": [],
                                      "resolution_type": "unresolvable",
                                      "why_unresolvable": "runtime path from argv"}],
            },
        })
        assert run(ir, wdir) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["imports_unresolvable_count"] == 1

    def test_reconciliation_enforced_without_workload_dir(self, tmp_path, capsys):
        """Reconciliation is IR-only, so leaks fail even in the no-workload path."""
        ir = _write_ir(tmp_path, {
            "unresolved_data_edges": [
                {"file": "etl/load.py", "line": 10, "kind": "read"},
            ],
            "llm_resolved_data_edges": {
                "analyzed_files": ["etl/load.py"],
                "excluded_files": [],
                "edges": [],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, None) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["data_leak_count"] == 1


# ---------------------------------------------------------------------------
# Exit 2 — JSON Schema validation of the llm block
# ---------------------------------------------------------------------------

class TestSchemaValidation:
    def test_bad_enum_fails_with_schema_errors(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["a.py"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["a.py"], "excluded_files": [],
                # source is a bad enum value → schema violation
                "edges": [{"file": "a.py", "line": 1, "kind": "read",
                           "resolved_signature": "t", "resolution_type": "traced",
                           "source": "bogus"}],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "fail"
        assert out["schema_error_count"] >= 1

    def test_missing_required_field_fails(self, tmp_path, capsys):
        wdir = _workload(tmp_path, ["a.py"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["a.py"], "excluded_files": [],
                # missing resolved_signature + source
                "edges": [{"file": "a.py", "line": 1, "kind": "read",
                           "resolution_type": "traced"}],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["schema_error_count"] >= 1

    def test_schema_valid_block_passes_through(self, tmp_path, capsys):
        # A schema-valid, fully-covered, reconciled block reaches exit 0.
        wdir = _workload(tmp_path, ["a.py"])
        ir = _write_ir(tmp_path, {
            "llm_resolved_data_edges": {
                "analyzed_files": ["a.py"], "excluded_files": [],
                "edges": [_edge("a.py")],
                "unresolvable_edges": [],
            },
        })
        assert run(ir, wdir) == 0
