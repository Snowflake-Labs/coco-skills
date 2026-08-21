"""Tests for scos_state.py prevalidate command — aggregation, caching, phase-scoped blocking."""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

# Import scos_state by path to avoid collision with any sibling scos_state module.
_spec = importlib.util.spec_from_file_location("scos_state", _SCRIPTS / "scos_state.py")
_scos_state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scos_state)  # type: ignore[union-attr]

_cmd_prevalidate = _scos_state._cmd_prevalidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(conv_root, phase="a", force=False):
    return SimpleNamespace(conv_root=str(conv_root), phase=phase, force=force)


def _complete_ep(eid="job1", **kwargs):
    """Minimal analysis.json entrypoint that passes analysis_completeness."""
    base = {
        "id": eid,
        "entrypoint_class": "com.example.Job$",
        "entrypoint_method": "main",
        "external_sources": [],
        "sinks": [],
    }
    base.update(kwargs)
    return base


def _write_analysis(tmp_path, extra=None):
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    data = {"jar_path": "", "entrypoints": [_complete_ep()]}
    if extra:
        data.update(extra)
    (shared / "analysis.json").write_text(json.dumps(data), encoding="utf-8")
    return shared


def _read_report(tmp_path):
    return json.loads((tmp_path / "Validation" / "shared" / "prevalidation_report.json").read_text())


# ---------------------------------------------------------------------------
# Fixture A: datagen 2 problems + compile 1 error → all 3 in report, exit 1
# ---------------------------------------------------------------------------

def test_all_problems_aggregated_no_early_abort(tmp_path):
    """datagen reports 2 problems AND sbt compile has 1 error → all 3 in report; exit 1."""
    _write_analysis(tmp_path, extra={"entrypoints": []})  # also triggers analysis_completeness
    # Create Validation/source with a build.sbt so sbt compile path is taken
    source_dir = tmp_path / "Validation" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "build.sbt").write_text("", encoding="utf-8")

    def _mock_subprocess_run(cmd, **kwargs):
        r = MagicMock()
        cmd_str = " ".join(str(c) for c in cmd)
        if "column_check" in cmd_str:
            r.stdout = json.dumps({"ok": True, "problems": [], "warnings": []})
            r.stderr = ""
            r.returncode = 0
        elif "dep_check" in cmd_str:
            r.stdout = json.dumps({"ok": True, "problems": [], "warnings": []})
            r.stderr = ""
            r.returncode = 0
        elif "sbt" in cmd_str:
            r.stdout = "[error] Compilation failed\n"
            r.stderr = ""
            r.returncode = 1
        else:
            r.stdout = ""
            r.stderr = ""
            r.returncode = 0
        return r

    with (
        patch.object(_scos_state, "_preflight_checks", return_value=(0, [], "/usr/lib/jvm/java-17")),
        patch.object(_scos_state, "_ensure_mock_data", return_value=(1, ["datagen problem 1", "datagen problem 2"])),
        patch.object(_scos_state, "subprocess") as mock_sp,
        patch.object(_scos_state.shutil, "which", return_value="/usr/bin/sbt"),
    ):
        mock_sp.run.side_effect = _mock_subprocess_run
        mock_sp.TimeoutExpired = TimeoutError
        rc = _cmd_prevalidate(_make_args(tmp_path, phase="a", force=True))

    assert rc == 1, f"Expected exit 1 but got {rc}"
    report = _read_report(tmp_path)

    mock_data_findings = [f for f in report["findings"] if f["check"] == "mock_data"]
    sbt_findings = [f for f in report["findings"] if f["check"] == "sbt_compile"]
    assert len(mock_data_findings) == 2, f"Expected 2 mock_data findings: {mock_data_findings}"
    assert len(sbt_findings) >= 1, f"Expected >=1 sbt_compile finding: {sbt_findings}"
    # No early abort — all issue types present (mock + compile + analysis completeness)
    assert report["blocking_count"] >= 3, f"Expected >=3 blocking: {report['blocking_count']}"
    assert report.get("rebuild_required") is True
    assert any(f["check"] == "analysis_completeness" for f in report["findings"])


# ---------------------------------------------------------------------------
# Fixture B: all clean → ok:true, report written, cache stored
# ---------------------------------------------------------------------------

def test_all_clean_writes_report_and_cache(tmp_path):
    """All checks pass → ok:true, report and cache files written, exit 0."""
    _write_analysis(tmp_path)

    def _mock_subprocess_run(cmd, **kwargs):
        r = MagicMock()
        r.stdout = json.dumps({"ok": True, "problems": [], "warnings": []})
        r.stderr = ""
        r.returncode = 0
        return r

    with (
        patch.object(_scos_state, "_preflight_checks", return_value=(0, [], "/usr/lib/jvm/java-17")),
        patch.object(_scos_state, "_ensure_mock_data", return_value=(0, [])),
        patch.object(_scos_state, "_check_entry_classes", return_value=[]),
        patch.object(_scos_state, "_check_unsupported_constructs_pv", return_value=[]),
        patch.object(_scos_state, "_run_sbt_compile_pv", return_value=[]),
        patch.object(_scos_state, "subprocess") as mock_sp,
    ):
        mock_sp.run.side_effect = _mock_subprocess_run
        mock_sp.TimeoutExpired = TimeoutError
        rc = _cmd_prevalidate(_make_args(tmp_path, phase="a", force=True))

    assert rc == 0, f"Expected exit 0 but got {rc}"

    report = _read_report(tmp_path)
    assert report["ok"] is True
    assert report["blocking_count"] == 0

    cache_path = tmp_path / "Validation" / "shared" / ".prevalidate_cache.json"
    assert cache_path.exists(), "cache file not written"
    cache = json.loads(cache_path.read_text())
    assert "hash" in cache and len(cache["hash"]) == 64, "cache should contain a SHA-256 hash"


# ---------------------------------------------------------------------------
# Fixture C: re-run with same hash → cache hit, no subprocesses invoked
# ---------------------------------------------------------------------------

def test_cache_hit_skips_all_checks(tmp_path):
    """Second run with identical files → cached message, no subprocess calls."""
    _write_analysis(tmp_path)

    def _mock_subprocess_run(cmd, **kwargs):
        r = MagicMock()
        r.stdout = json.dumps({"ok": True, "problems": [], "warnings": []})
        r.stderr = ""
        r.returncode = 0
        return r

    with (
        patch.object(_scos_state, "_preflight_checks", return_value=(0, [], "/usr/lib/jvm/java-17")),
        patch.object(_scos_state, "_ensure_mock_data", return_value=(0, [])),
        patch.object(_scos_state, "_check_entry_classes", return_value=[]),
        patch.object(_scos_state, "_check_unsupported_constructs_pv", return_value=[]),
        patch.object(_scos_state, "_run_sbt_compile_pv", return_value=[]),
        patch.object(_scos_state, "subprocess") as mock_sp,
    ):
        mock_sp.run.side_effect = _mock_subprocess_run
        mock_sp.TimeoutExpired = TimeoutError
        # First run to populate cache
        _cmd_prevalidate(_make_args(tmp_path, phase="a", force=True))
        call_count_after_first = mock_sp.run.call_count

    # Second run — no force → should hit cache
    with (
        patch.object(_scos_state, "_preflight_checks") as mock_pf,
        patch.object(_scos_state, "_ensure_mock_data") as mock_mock,
        patch.object(_scos_state, "subprocess") as mock_sp2,
    ):
        mock_sp2.run.side_effect = _mock_subprocess_run
        mock_sp2.TimeoutExpired = TimeoutError
        rc = _cmd_prevalidate(_make_args(tmp_path, phase="a", force=False))

    # Cache hit: none of the heavy checks should have been invoked
    mock_pf.assert_not_called()
    mock_mock.assert_not_called()
    mock_sp2.run.assert_not_called()
    # Exit code should reflect cached report's outcome
    assert rc in (0, 1, 2)


# ---------------------------------------------------------------------------
# Fixture D: --phase b with unpatched S3 read in Output/ → blocking io_completeness
# ---------------------------------------------------------------------------

def test_phase_b_io_completeness_blocking(tmp_path):
    """Phase B with unpatched cloud URI in Output/src → blocking io_completeness finding."""
    _write_analysis(tmp_path)
    # Create Output/src with unpatched S3 read
    scala_dir = tmp_path / "Output" / "src" / "main" / "scala"
    scala_dir.mkdir(parents=True)
    (scala_dir / "Job.scala").write_text(
        'val df = spark.read.parquet("s3://my-bucket/input-data")\n',
        encoding="utf-8",
    )

    def _mock_subprocess_run(cmd, **kwargs):
        r = MagicMock()
        r.stdout = json.dumps({"ok": True, "problems": [], "warnings": []})
        r.stderr = ""
        r.returncode = 0
        return r

    with (
        patch.object(_scos_state, "_preflight_checks", return_value=(0, [], "/usr/lib/jvm/java-17")),
        patch.object(_scos_state, "_ensure_mock_data", return_value=(0, [])),
        patch.object(_scos_state, "_check_entry_classes", return_value=[]),
        patch.object(_scos_state, "_check_unsupported_constructs_pv", return_value=[]),
        patch.object(_scos_state, "_check_scos_venv_pv", return_value=[]),
        patch.object(_scos_state, "subprocess") as mock_sp,
    ):
        mock_sp.run.side_effect = _mock_subprocess_run
        mock_sp.TimeoutExpired = TimeoutError
        rc = _cmd_prevalidate(_make_args(tmp_path, phase="b", force=True))

    assert rc == 1, f"Expected exit 1 for Phase B with unpatched S3 read, got {rc}"
    report = _read_report(tmp_path)
    io_findings = [f for f in report["findings"] if f["check"] == "io_completeness"]
    assert io_findings, "Expected at least one io_completeness finding"
    assert all(f["severity"] == "blocking" for f in io_findings)
    assert any("cloud_uri_literal" in f["message"] for f in io_findings)


# ---------------------------------------------------------------------------
# Phase-scoped unsupported-construct severity
# ---------------------------------------------------------------------------

def test_unsupported_construct_phase_a_is_warning(tmp_path):
    """rdd_op with phase_b_blocking=True → warning in Phase A."""
    _write_analysis(tmp_path, extra={
        "entrypoints": [_complete_ep(
            unsupported_constructs=[
                {"kind": "rdd_op", "detail": "sc.parallelize", "line": 5, "phase_b_blocking": True},
            ],
        )],
    })

    def _clean_subprocess(cmd, **kwargs):
        r = MagicMock()
        r.stdout = json.dumps({"ok": True, "problems": [], "warnings": []})
        r.stderr = ""
        r.returncode = 0
        return r

    with (
        patch.object(_scos_state, "_preflight_checks", return_value=(0, [], "/usr/lib/jvm/java-17")),
        patch.object(_scos_state, "_ensure_mock_data", return_value=(0, [])),
        patch.object(_scos_state, "_check_entry_classes", return_value=[]),
        patch.object(_scos_state, "subprocess") as mock_sp,
    ):
        mock_sp.run.side_effect = _clean_subprocess
        mock_sp.TimeoutExpired = TimeoutError
        rc_a = _cmd_prevalidate(_make_args(tmp_path, phase="a", force=True))

    report_a = _read_report(tmp_path)
    uc_a = [f for f in report_a["findings"] if f["check"] == "unsupported_construct"]
    assert uc_a, "Expected unsupported_construct finding in Phase A"
    assert all(f["severity"] == "warning" for f in uc_a), f"Phase A should be warning: {uc_a}"
    assert rc_a == 2, f"Phase A with only warnings should exit 2, got {rc_a}"


def test_unsupported_construct_phase_b_is_blocking(tmp_path):
    """rdd_op with phase_b_blocking=True → blocking in Phase B."""
    _write_analysis(tmp_path, extra={
        "entrypoints": [_complete_ep(
            unsupported_constructs=[
                {"kind": "rdd_op", "detail": "sc.parallelize", "line": 5, "phase_b_blocking": True},
            ],
        )],
    })
    scala_dir = tmp_path / "Output" / "src" / "main" / "scala"
    scala_dir.mkdir(parents=True)
    (scala_dir / "Job.scala").write_text("// clean code\n", encoding="utf-8")

    def _clean_subprocess(cmd, **kwargs):
        r = MagicMock()
        r.stdout = json.dumps({"ok": True, "problems": [], "warnings": []})
        r.stderr = ""
        r.returncode = 0
        return r

    with (
        patch.object(_scos_state, "_preflight_checks", return_value=(0, [], "/usr/lib/jvm/java-17")),
        patch.object(_scos_state, "_ensure_mock_data", return_value=(0, [])),
        patch.object(_scos_state, "_check_entry_classes", return_value=[]),
        patch.object(_scos_state, "_check_scos_venv_pv", return_value=[]),
        patch.object(_scos_state, "subprocess") as mock_sp,
    ):
        mock_sp.run.side_effect = _clean_subprocess
        mock_sp.TimeoutExpired = TimeoutError
        rc_b = _cmd_prevalidate(_make_args(tmp_path, phase="b", force=True))

    report_b = _read_report(tmp_path)
    uc_b = [f for f in report_b["findings"] if f["check"] == "unsupported_construct"]
    assert uc_b, "Expected unsupported_construct finding in Phase B"
    assert any(f["severity"] == "blocking" for f in uc_b), f"Phase B should be blocking: {uc_b}"
    assert rc_b == 1, f"Phase B with blocking should exit 1, got {rc_b}"


def test_udf_construct_is_warning_even_in_phase_b(tmp_path):
    """UDF risks stay warnings in Phase B (softened; not hard-blocking)."""
    _write_analysis(tmp_path, extra={
        "entrypoints": [_complete_ep(
            unsupported_constructs=[
                {"kind": "udf", "detail": "my_udf", "line": 9, "phase_b_blocking": True},
            ],
        )],
    })
    scala_dir = tmp_path / "Output" / "src" / "main" / "scala"
    scala_dir.mkdir(parents=True)
    (scala_dir / "Job.scala").write_text("// clean\n", encoding="utf-8")

    def _clean_subprocess(cmd, **kwargs):
        r = MagicMock()
        r.stdout = json.dumps({"ok": True, "problems": [], "warnings": []})
        r.stderr = ""
        r.returncode = 0
        return r

    with (
        patch.object(_scos_state, "_preflight_checks", return_value=(0, [], "/usr/lib/jvm/java-17")),
        patch.object(_scos_state, "_ensure_mock_data", return_value=(0, [])),
        patch.object(_scos_state, "_check_entry_classes", return_value=[]),
        patch.object(_scos_state, "_check_scos_venv_pv", return_value=[]),
        patch.object(_scos_state, "subprocess") as mock_sp,
    ):
        mock_sp.run.side_effect = _clean_subprocess
        mock_sp.TimeoutExpired = TimeoutError
        rc = _cmd_prevalidate(_make_args(tmp_path, phase="b", force=True))

    report = _read_report(tmp_path)
    uc = [f for f in report["findings"] if f["check"] == "unsupported_construct"]
    assert uc and all(f["severity"] == "warning" for f in uc)
    assert rc == 2


def test_analysis_completeness_missing_entrypoint_class(tmp_path):
    """Missing entrypoint_class is a blocking analysis_completeness finding."""
    _write_analysis(tmp_path, extra={
        "entrypoints": [{
            "id": "job1",
            "external_sources": [],
            "sinks": [],
        }],
    })

    def _clean_subprocess(cmd, **kwargs):
        r = MagicMock()
        r.stdout = json.dumps({"ok": True, "problems": [], "warnings": []})
        r.stderr = ""
        r.returncode = 0
        return r

    with (
        patch.object(_scos_state, "_preflight_checks", return_value=(0, [], "/usr/lib/jvm/java-17")),
        patch.object(_scos_state, "_ensure_mock_data", return_value=(0, [])),
        patch.object(_scos_state, "_check_entry_classes", return_value=[]),
        patch.object(_scos_state, "_check_unsupported_constructs_pv", return_value=[]),
        patch.object(_scos_state, "_run_sbt_compile_pv", return_value=[]),
        patch.object(_scos_state, "subprocess") as mock_sp,
    ):
        mock_sp.run.side_effect = _clean_subprocess
        mock_sp.TimeoutExpired = TimeoutError
        rc = _cmd_prevalidate(_make_args(tmp_path, phase="a", force=True))

    assert rc == 1
    report = _read_report(tmp_path)
    ac = [f for f in report["findings"] if f["check"] == "analysis_completeness"]
    assert any("entrypoint_class" in f["message"] for f in ac)


def test_phase_spec_dirs_isolate_a_and_b(tmp_path):
    """Rendering Phase A clears phase_b specs; Phase B keeps only phase_b specs."""
    tests = tmp_path / "Validation" / "tests"
    phase_a = _scos_state._phase_spec_dir(tests, "a")
    phase_b = _scos_state._phase_spec_dir(tests, "b")
    phase_a.mkdir(parents=True)
    phase_b.mkdir(parents=True)
    (phase_a / "TestASpec.scala").write_text("// a\n", encoding="utf-8")
    (phase_b / "TestBSpec.scala").write_text("// b\n", encoding="utf-8")
    flat = tests / "src" / "test" / "scala"
    (flat / "LegacySpec.scala").write_text("// legacy\n", encoding="utf-8")

    cleared = _scos_state._clear_rendered_specs(tests, phases=["b"])
    assert (phase_a / "TestASpec.scala").is_file()
    assert not (phase_b / "TestBSpec.scala").exists()
    assert not (flat / "LegacySpec.scala").exists()
    assert cleared

    phase_b.mkdir(parents=True, exist_ok=True)
    (phase_b / "TestBSpec.scala").write_text("// b2\n", encoding="utf-8")
    cleared_a = _scos_state._clear_rendered_specs(tests, phases=["a"])
    assert not (phase_a / "TestASpec.scala").exists()
    assert (phase_b / "TestBSpec.scala").is_file()
    assert cleared_a


def _clean_subprocess(cmd, **kwargs):
    r = MagicMock()
    r.stdout = json.dumps({"ok": True, "problems": [], "warnings": []})
    r.stderr = ""
    r.returncode = 0
    return r


def _run_prevalidate_clean(tmp_path, phase="a"):
    with (
        patch.object(_scos_state, "_preflight_checks", return_value=(0, [], "/usr/lib/jvm/java-17")),
        patch.object(_scos_state, "_ensure_mock_data", return_value=(0, [])),
        patch.object(_scos_state, "_check_entry_classes", return_value=[]),
        patch.object(_scos_state, "_check_unsupported_constructs_pv", return_value=[]),
        patch.object(_scos_state, "_run_sbt_compile_pv", return_value=[]),
        patch.object(_scos_state, "_check_scos_venv_pv", return_value=[]),
        patch.object(_scos_state, "subprocess") as mock_sp,
    ):
        mock_sp.run.side_effect = _clean_subprocess
        mock_sp.TimeoutExpired = TimeoutError
        return _cmd_prevalidate(_make_args(tmp_path, phase=phase, force=True))


def test_cli_args_stub_is_blocking_no_rebuild(tmp_path):
    """Incomplete cli_args stubs block Phase A without requiring a JAR rebuild."""
    _write_analysis(tmp_path, extra={
        "entrypoints": [_complete_ep(cli_args=["--input", "<TODO>", "--out", "ok"])],
    })
    rc = _run_prevalidate_clean(tmp_path, phase="a")
    assert rc == 1
    report = _read_report(tmp_path)
    cli = [f for f in report["findings"] if f["check"] == "cli_args"]
    assert cli and all(f["severity"] == "blocking" for f in cli)
    assert all(f.get("rebuild_required") is False for f in cli)


def test_dynamic_path_llm_todo_blocks_phase_a(tmp_path):
    """Open llm_todo about dynamic paths is Phase-A-blocking."""
    _write_analysis(tmp_path, extra={
        "entrypoints": [_complete_ep(llm_todo="unresolved dynamic path for banners")],
    })
    rc = _run_prevalidate_clean(tmp_path, phase="a")
    assert rc == 1
    report = _read_report(tmp_path)
    ac = [f for f in report["findings"] if f["check"] == "analysis_completeness"]
    assert any("llm_todo" in f["message"] and f["severity"] == "blocking" for f in ac)


def test_empty_sinks_with_ast_writes_blocks(tmp_path):
    """sinks=[] while ast_facts shows writes is an analysis gap, not no-sink smoke."""
    ep = _complete_ep(sinks=[], path="src/main/scala/Job.scala")
    _write_analysis(tmp_path, extra={"entrypoints": [ep]})
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "ast_facts.json").write_text(json.dumps({
        "files": [{
            "path": str(tmp_path / "Validation" / "source" / "src" / "main" / "scala" / "Job.scala"),
            "writes": [{"call": "saveAsTable", "args": ["OUT"]}],
            "unresolved_writes": [],
            "write_helpers": [],
        }],
    }), encoding="utf-8")
    rc = _run_prevalidate_clean(tmp_path, phase="a")
    assert rc == 1
    report = _read_report(tmp_path)
    ac = [f for f in report["findings"] if f["check"] == "analysis_completeness"]
    assert any("sinks=[]" in f["message"] and "ast_facts" in f["message"] for f in ac)


def test_external_sinks_key_satisfies_completeness(tmp_path):
    """Synthesizer key `external_sinks` counts as declaring sinks (no false block)."""
    ep = {
        "id": "job1",
        "entrypoint_class": "com.example.Job$",
        "entrypoint_method": "main",
        "external_sources": [],
        "external_sinks": [{"id": "out", "kind": "table",
                            "schema": [{"name": "c", "type": "string"}]}],
        # NOTE: no legacy "sinks" key at all
    }
    _write_analysis(tmp_path, extra={"entrypoints": [ep]})
    _run_prevalidate_clean(tmp_path, phase="a")
    report = _read_report(tmp_path)
    ac = [f for f in report["findings"] if f["check"] == "analysis_completeness"]
    # Must NOT complain about missing sinks key, nor claim sinks=[] with AST writes.
    assert not any("missing sinks" in f["message"] for f in ac)
    assert not any("sinks=[]" in f["message"] for f in ac)


def test_external_sinks_present_not_blocked_by_ast_writes(tmp_path):
    """sinks=[] but external_sinks populated + AST writes → not an analysis gap."""
    ep = {
        "id": "job1",
        "entrypoint_class": "com.example.Job$",
        "entrypoint_method": "main",
        "external_sources": [],
        "sinks": [],
        "external_sinks": [{"id": "out", "kind": "table",
                            "schema": [{"name": "c", "type": "string"}]}],
        "path": "src/main/scala/Job.scala",
    }
    _write_analysis(tmp_path, extra={"entrypoints": [ep]})
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "ast_facts.json").write_text(json.dumps({
        "files": [{
            "path": str(tmp_path / "Validation" / "source" / "src" / "main" / "scala" / "Job.scala"),
            "writes": [{"call": "saveAsTable", "args": ["OUT"]}],
            "unresolved_writes": [],
            "write_helpers": [],
        }],
    }), encoding="utf-8")
    _run_prevalidate_clean(tmp_path, phase="a")
    report = _read_report(tmp_path)
    ac = [f for f in report["findings"] if f["check"] == "analysis_completeness"]
    assert not any("sinks=[]" in f["message"] and "ast_facts" in f["message"] for f in ac)


def test_empty_sinks_without_ast_writes_ok(tmp_path):
    """Confirmed no-sink (AST has no writes) does not block analysis_completeness."""
    ep = _complete_ep(sinks=[], path="src/main/scala/Job.scala")
    _write_analysis(tmp_path, extra={"entrypoints": [ep]})
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "ast_facts.json").write_text(json.dumps({
        "files": [{
            "path": "src/main/scala/Job.scala",
            "writes": [],
            "unresolved_writes": [],
            "write_helpers": [],
        }],
    }), encoding="utf-8")
    # May still fail other checks; assert no sinks=[]/ast_facts gap finding.
    _run_prevalidate_clean(tmp_path, phase="a")
    report = _read_report(tmp_path)
    ac = [f for f in report["findings"] if f["check"] == "analysis_completeness"]
    assert not any("sinks=[]" in f["message"] and "ast_facts" in f["message"] for f in ac)


def test_intermediate_missing_schema_blocks(tmp_path):
    _write_analysis(tmp_path, extra={
        "entrypoints": [_complete_ep()],
        "intermediate_tables": [{"name": "stage_1_meta", "schema": []}],
    })
    rc = _run_prevalidate_clean(tmp_path, phase="a")
    assert rc == 1
    report = _read_report(tmp_path)
    mid = [f for f in report["findings"] if f["check"] == "intermediate_tables"]
    assert mid and any("missing typed schema" in f["message"] for f in mid)


def test_cores_zero_blocks_phase_a(tmp_path):
    _write_analysis(tmp_path)
    src = tmp_path / "Validation" / "source" / "src" / "main" / "scala"
    src.mkdir(parents=True)
    (src / "Job.scala").write_text(
        'object Job { def main(args: Array[String]): Unit = spark.conf.set("spark.master", "local[0]") }\n',
        encoding="utf-8",
    )
    rc = _run_prevalidate_clean(tmp_path, phase="a")
    assert rc == 1
    report = _read_report(tmp_path)
    cz = [f for f in report["findings"] if f["check"] == "cores_zero"]
    assert cz and any("local[0]" in f["message"] for f in cz)


def test_io_completeness_flags_excel_in_phase_b(tmp_path):
    _write_analysis(tmp_path)
    out = tmp_path / "Output" / "src" / "main" / "scala"
    out.mkdir(parents=True)
    (out / "Job.scala").write_text(
        'df.write.format("com.crealytics.spark.excel").save("report.xlsx")\n',
        encoding="utf-8",
    )
    findings = _scos_state._check_io_completeness(tmp_path)
    assert any(f["severity"] == "blocking" and "excel_io" in f["message"] for f in findings)


def test_sink_strategy_blocks_excel_without_allow_empty(tmp_path):
    _write_analysis(tmp_path, extra={
        "entrypoints": [_complete_ep(sinks=[
            {"id": "x", "kind": "excel", "name": "r.xlsx", "format": "excel"},
        ])],
        "sinks": [
            {"id": "x", "kind": "excel", "name": "r.xlsx", "format": "excel"},
        ],
    })
    findings = _scos_state._check_sink_strategy_pv(tmp_path, "b")
    assert findings and findings[0]["severity"] == "blocking"
    assert findings[0].get("rebuild_required") is True


def test_sink_strategy_warns_when_allow_empty(tmp_path):
    _write_analysis(tmp_path, extra={
        "entrypoints": [_complete_ep(sinks=["x"])],
        "sinks": [
            {"id": "x", "kind": "excel", "name": "r.xlsx", "format": "excel",
             "allow_empty": "legacy header-only"},
        ],
    })
    findings = _scos_state._check_sink_strategy_pv(tmp_path, "b")
    assert findings and findings[0]["severity"] == "warning"
    assert findings[0].get("rebuild_required") is False
