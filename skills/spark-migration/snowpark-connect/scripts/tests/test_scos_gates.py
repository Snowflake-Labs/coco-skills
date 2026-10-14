"""Tests for the deterministic analyzer gate in scos_gates.py."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scos_gates import (
    EXIT_FAIL,
    EXIT_PASS,
    run_analyzer_gate,
    run_fixer_gate,
    run_imports_gate,
    run_reports_gate,
)

HEADER = '"""\nSCOS Migration Output\n"""\n'


def _conv(tmp_path: Path):
    conv = tmp_path / "Conversion-SCOS-TEST"
    output = conv / "Output"
    reports = conv / "Reports"
    output.mkdir(parents=True)
    reports.mkdir(parents=True)
    return conv, output, reports


def _state(conv: Path, output: Path, manifest: list[str], analysis=None) -> Path:
    if analysis is not None:
        (conv / "analysis.json").write_text(json.dumps(analysis))
    (conv / "migration_state.json").write_text(json.dumps(
        {"conversion_root": str(conv), "migrated_dir": str(output), "manifest": manifest}))
    return conv / "migration_state.json"


def _setup(tmp_path: Path, source: str, analysis: object) -> Path:
    """Create a minimal conversion folder and return the state path."""
    conv = tmp_path / "Conversion-SCOS-TEST"
    output = conv / "Output"
    output.mkdir(parents=True)
    (output / "workload.py").write_text(source)
    (conv / "analysis.json").write_text(json.dumps(analysis))
    state = {
        "conversion_root": str(conv),
        "migrated_dir": str(output),
        "manifest": ["workload.py"],
    }
    state_path = conv / "migration_state.json"
    state_path.write_text(json.dumps(state))
    return state_path


def test_pass_when_blindspot_is_covered(tmp_path: Path):
    source = (
        "from pyspark.sql import SparkSession\n"
        "@udf\n"
        "def my_udf(x):\n"
        "    return x\n"
    )
    # analysis covers the @udf decorator on line 2.
    analysis = [{"file": "/somewhere/workload.py", "lines": "2-2", "final_risk": 0.9}]
    res = run_analyzer_gate(_setup(tmp_path, source, analysis))
    assert res.verdict == "PASS"
    assert res.exit_code == EXIT_PASS


def test_fail_when_critical_blindspot_uncovered(tmp_path: Path):
    source = (
        "from pyspark.sql import SparkSession\n"
        "@udf\n"
        "def my_udf(x):\n"
        "    return x\n"
    )
    # analysis points at an unrelated line, leaving the @udf uncovered.
    analysis = [{"file": "/somewhere/workload.py", "lines": "10-10", "final_risk": 0.3}]
    res = run_analyzer_gate(_setup(tmp_path, source, analysis))
    assert res.verdict == "FAIL"
    assert res.exit_code == EXIT_FAIL
    assert any(f.code == "blindspot:udf_decorator" for f in res.findings)
    # FAIL findings are surfaced as gaps for re-scan.
    assert any(g.severity == "CRITICAL" for g in res.findings)


def test_jvm_attr_is_critical(tmp_path: Path):
    source = "df2 = df._jdf.schema()\n"
    res = run_analyzer_gate(_setup(tmp_path, source, []))
    assert res.verdict == "FAIL"
    assert any(f.code == "blindspot:jvm_attr" for f in res.findings)


def test_commented_and_annotated_lines_are_ignored(tmp_path: Path):
    source = (
        "# @udf this is just a comment\n"
        "x = df.checkpoint()  # SCOS: [SPRKCNTPY0099] already flagged\n"
    )
    res = run_analyzer_gate(_setup(tmp_path, source, []))
    # No pyspark import, comment + annotated line skipped -> clean PASS.
    assert res.verdict == "PASS"
    assert res.exit_code == EXIT_PASS


def test_empty_analysis_with_pyspark_fails(tmp_path: Path):
    source = "import pyspark\ndf = spark.range(10)\n"
    res = run_analyzer_gate(_setup(tmp_path, source, []))
    assert res.verdict == "FAIL"
    assert any(f.code == "empty_analysis_with_pyspark" for f in res.findings)


def test_invalid_analysis_json_fails(tmp_path: Path):
    state_path = _setup(tmp_path, "x = 1\n", [])
    (state_path.parent / "analysis.json").write_text("{not valid json")
    res = run_analyzer_gate(state_path)
    assert res.verdict == "FAIL"
    assert any(f.code == "analysis_invalid_json" for f in res.findings)


def test_malformed_analysis_entries_do_not_crash(tmp_path: Path):
    # LLM-generated analysis.json may contain non-dict items or a non-numeric
    # final_risk; the gate must degrade gracefully, never raise.
    source = "import pyspark\nx = df.rdd.map(lambda r: r)\n"
    analysis = [
        "a bare string, not an object",
        42,
        {"file": "/x/workload.py", "lines": "1-1", "final_risk": "high"},
        {"file": "/x/workload.py", "lines": "bogus", "final_risk": None},
    ]
    res = run_analyzer_gate(_setup(tmp_path, source, analysis))
    # Should produce a clean verdict (not a traceback / exit 1).
    assert res.verdict in {"PASS", "PASS_WITH_GAPS", "FAIL"}
    assert res.exit_code in {EXIT_PASS, EXIT_FAIL}


def test_warn_only_blindspot_passes(tmp_path: Path):
    # ml_pipeline (Pipeline) is WARN-tier -> advisory gap, still exit 0.
    # Non-empty analysis avoids the empty-analysis-with-pyspark CRITICAL.
    source = "from pyspark.ml import Pipeline\npipe = Pipeline(stages=[])\n"
    analysis = [{"file": "/x/workload.py", "lines": "99-99", "final_risk": 0.5}]
    res = run_analyzer_gate(_setup(tmp_path, source, analysis))
    assert res.verdict == "PASS_WITH_GAPS"
    assert res.exit_code == EXIT_PASS
    assert any(f.code == "blindspot:ml_pipeline" for f in res.findings)


# --- imports gate -------------------------------------------------------------

def test_imports_pass(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    (output / "main.py").write_text(
        HEADER + "from snowflake import snowpark_connect\n"
        "spark = snowpark_connect.init_spark_session()\n")
    res = run_imports_gate(_state(conv, output, ["main.py"]))
    assert res.verdict == "PASS"
    assert res.exit_code == EXIT_PASS


def test_imports_fail_missing_header(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    (output / "main.py").write_text(
        "from snowflake import snowpark_connect\nx = 1\n")
    res = run_imports_gate(_state(conv, output, ["main.py"]))
    assert res.verdict == "FAIL"
    assert any(f.code == "missing_header" for f in res.findings)


def test_imports_fail_builder_in_code(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    (output / "main.py").write_text(
        HEADER + "from snowflake import snowpark_connect\n"
        "spark = SparkSession.builder.getOrCreate()\n")
    res = run_imports_gate(_state(conv, output, ["main.py"]))
    assert res.verdict == "FAIL"
    assert any(f.code == "spark_builder_in_code" for f in res.findings)


def test_imports_builder_in_comment_is_ok(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    (output / "main.py").write_text(
        HEADER + "from snowflake import snowpark_connect\n"
        "# spark = SparkSession.builder.getOrCreate()\n")
    res = run_imports_gate(_state(conv, output, ["main.py"]))
    assert res.verdict == "PASS"


def test_imports_fail_unsupported_import(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    (output / "main.py").write_text(
        HEADER + "from snowflake import snowpark_connect\n"
        "from delta.tables import DeltaTable\n")
    res = run_imports_gate(_state(conv, output, ["main.py"]))
    assert res.verdict == "FAIL"
    assert any(f.code == "unsupported_import" for f in res.findings)


def test_imports_fail_no_snowpark_connect(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    (output / "main.py").write_text(HEADER + "x = 1\n")
    res = run_imports_gate(_state(conv, output, ["main.py"]))
    assert res.verdict == "FAIL"
    assert any(f.code == "missing_snowpark_connect" for f in res.findings)


STUB_HEADER = (
    '"""\nSCOS Migration Output\n'
    "=====================\n"
    "Source File: main.py\n"
    "Migrated on: 2026-01-01\n"
    "\nChanges Overview:\n"
    "- Deterministic header added by report generator.\n"
    "\nKnown Limitations:\n"
    "- None\n"
    '"""\n'
)


def test_imports_fail_stub_header(tmp_path: Path):
    """A placeholder header (Phase 3 skipped) must be rejected, not accepted."""
    conv, output, _ = _conv(tmp_path)
    (output / "main.py").write_text(
        STUB_HEADER + "from snowflake import snowpark_connect\n"
        "spark = snowpark_connect.init_spark_session()\n")
    res = run_imports_gate(_state(conv, output, ["main.py"]))
    assert res.verdict == "FAIL"
    assert any(f.code == "stub_header" for f in res.findings)
    # The stub still carries the marker, so missing_header must NOT also fire.
    assert not any(f.code == "missing_header" for f in res.findings)


# --- reports gate -------------------------------------------------------------

def test_reports_assessment_pass(tmp_path: Path):
    conv, output, reports = _conv(tmp_path)
    (reports / "MigrationReadinessReport.html").write_text("<html>ok</html>")
    (reports / "AssessmentIR.json").write_text("{}")
    res = run_reports_gate(_state(conv, output, []), "assessment")
    assert res.verdict == "PASS"


def test_reports_assessment_fail_missing_and_jinja(tmp_path: Path):
    conv, output, reports = _conv(tmp_path)
    (reports / "MigrationReadinessReport.html").write_text("<html>{{ leftover }}</html>")
    res = run_reports_gate(_state(conv, output, []), "assessment")
    assert res.verdict == "FAIL"
    codes = {f.code for f in res.findings}
    assert "missing_ir" in codes and "unrendered_jinja" in codes


def test_reports_csvs_pass(tmp_path: Path):
    conv, output, reports = _conv(tmp_path)
    (reports / "Issues.csv").write_text("EWI_Code,File,Line\nSPRKCNTPY0099,a.py,1\n")
    (reports / "InputFilesInventory.csv").write_text("File\na.py\n")
    (reports / "ArtifactDependencyInventory.csv").write_text("Import\npyspark\n")
    res = run_reports_gate(_state(conv, output, []), "csvs")
    assert res.verdict == "PASS"


def test_reports_csvs_fail_empty_issues(tmp_path: Path):
    conv, output, reports = _conv(tmp_path)
    (reports / "Issues.csv").write_text("EWI_Code,File,Line\n")
    (reports / "InputFilesInventory.csv").write_text("File\na.py\n")
    (reports / "ArtifactDependencyInventory.csv").write_text("Import\npyspark\n")
    res = run_reports_gate(_state(conv, output, []), "csvs")
    assert res.verdict == "FAIL"
    assert any(f.code == "issues_no_data" for f in res.findings)


# --- fixer gate ---------------------------------------------------------------

def test_fixer_pass(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    (output / "workload.py").write_text(
        HEADER + "from snowflake import snowpark_connect\n"
        "x = udf(f)  # SCOS: [SPRKCNTPY0099]\n")
    analysis = [{"file": "/x/workload.py", "lines": "5-5", "final_risk": 0.9}]
    res = run_fixer_gate(_state(conv, output, ["workload.py"], analysis))
    assert res.verdict == "PASS"


def test_fixer_fail_syntax_error(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    (output / "bad.py").write_text("def f(:\n    pass\n")
    res = run_fixer_gate(_state(conv, output, ["bad.py"], []))
    assert res.verdict == "FAIL"
    assert any(f.code == "syntax_error" for f in res.findings)


def test_fixer_fail_empty_file(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    (output / "empty.py").write_text("")
    res = run_fixer_gate(_state(conv, output, ["empty.py"], []))
    assert res.verdict == "FAIL"
    assert any(f.code == "empty_file" for f in res.findings)


def _state_extra(conv: Path, output: Path, manifest: list[str], **extra) -> Path:
    """Like _state but merges arbitrary extra top-level fields into state."""
    state = {"conversion_root": str(conv), "migrated_dir": str(output),
             "manifest": manifest}
    state.update(extra)
    (conv / "migration_state.json").write_text(json.dumps(state))
    return conv / "migration_state.json"


def test_fixer_fail_when_not_orchestrated(tmp_path: Path):
    """Multi-file workload with no orchestrator plan in state must FAIL so the
    coordinator is forced to run orchestrate_phases.py (not fix inline)."""
    conv, output, _ = _conv(tmp_path)
    for name in ("a.py", "b.py"):
        (output / name).write_text(
            HEADER + "from snowflake import snowpark_connect\nx = 1\n")
    res = run_fixer_gate(_state(conv, output, ["a.py", "b.py"], []))
    assert res.verdict == "FAIL"
    assert any(f.code == "phase2_not_orchestrated" for f in res.findings)


def test_fixer_pass_when_orchestrated(tmp_path: Path):
    """Same multi-file workload, but state carries the orchestrator plan."""
    conv, output, _ = _conv(tmp_path)
    for name in ("a.py", "b.py"):
        (output / name).write_text(
            HEADER + "from snowflake import snowpark_connect\nx = 1\n")
    state = _state_extra(conv, output, ["a.py", "b.py"],
                         max_parallel_fixers=2,
                         phase2_chunks=[["a.py"], ["b.py"]])
    res = run_fixer_gate(state)
    assert not any(f.code == "phase2_not_orchestrated" for f in res.findings)


def test_fixer_single_file_skips_orchestration_check(tmp_path: Path):
    """A 1-file workload never needs the parallel pool, so the orchestration
    check must not fire even without max_parallel_fixers in state."""
    conv, output, _ = _conv(tmp_path)
    (output / "a.py").write_text(
        HEADER + "from snowflake import snowpark_connect\nx = 1\n")
    res = run_fixer_gate(_state(conv, output, ["a.py"], []))
    assert not any(f.code == "phase2_not_orchestrated" for f in res.findings)


def test_fixer_fail_high_risk_unmarked(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    (output / "workload.py").write_text(
        HEADER + "from snowflake import snowpark_connect\nx = udf(f)\n")
    analysis = [{"file": "/x/workload.py", "lines": "5-5", "final_risk": 0.95}]
    res = run_fixer_gate(_state(conv, output, ["workload.py"], analysis))
    assert res.verdict == "FAIL"
    assert any(f.code == "high_risk_unmarked" for f in res.findings)


def test_fixer_pass_high_risk_resolution_safe(tmp_path: Path):
    """A high-risk issue the fixer judged safe (with a reason) recorded in
    analysis.json satisfies the gate WITHOUT an inline # SCOS comment."""
    conv, output, _ = _conv(tmp_path)
    (output / "workload.py").write_text(
        HEADER + "from snowflake import snowpark_connect\nx = udf(f)\n")
    analysis = [{
        "file": "/x/workload.py", "lines": "5-5", "final_risk": 0.95,
        "resolution": "safe",
        "resolution_reason": "window has explicit orderBy('id') -> deterministic",
    }]
    res = run_fixer_gate(_state(conv, output, ["workload.py"], analysis))
    assert res.verdict == "PASS"
    assert not any(f.code == "high_risk_unmarked" for f in res.findings)


def test_fixer_fail_safe_without_reason(tmp_path: Path):
    """resolution='safe' with no reason is not a free pass on a high-risk issue."""
    conv, output, _ = _conv(tmp_path)
    (output / "workload.py").write_text(
        HEADER + "from snowflake import snowpark_connect\nx = udf(f)\n")
    analysis = [{
        "file": "/x/workload.py", "lines": "5-5", "final_risk": 0.95,
        "resolution": "safe", "resolution_reason": "  ",
    }]
    res = run_fixer_gate(_state(conv, output, ["workload.py"], analysis))
    assert res.verdict == "FAIL"
    assert any(f.code == "safe_without_reason" for f in res.findings)
    # The specific guardrail fires; the generic unmarked check must not double-report.
    assert not any(f.code == "high_risk_unmarked" for f in res.findings)


def test_fixer_pass_resolution_fixed_without_inline_marker(tmp_path: Path):
    """A non-'safe' resolution (fixed/todo/perf) also satisfies the coverage check."""
    conv, output, _ = _conv(tmp_path)
    (output / "workload.py").write_text(
        HEADER + "from snowflake import snowpark_connect\nx = udf(f)\n")
    analysis = [{
        "file": "/x/workload.py", "lines": "5-5", "final_risk": 0.95,
        "resolution": "todo",
    }]
    res = run_fixer_gate(_state(conv, output, ["workload.py"], analysis))
    assert res.verdict == "PASS"
    assert not any(f.code == "high_risk_unmarked" for f in res.findings)


# --- --revert-failing safety net (folded-in former Phase 2b) ----------------

def _git(args: list[str], cwd: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", *args], cwd=str(cwd), check=True, env=env,
                   capture_output=True, text=True)


def test_fixer_revert_failing_reverts_broken_fix_to_baseline(tmp_path: Path):
    """A non-compiling fix is reverted to phase-1-complete and reported as an
    advisory fix_reverted WARN (not a blocking syntax_error)."""
    conv, output, _ = _conv(tmp_path)
    good = HEADER + "from snowflake import snowpark_connect\nx = 1\n"
    f = output / "workload.py"
    f.write_text(good)
    state_path = _state(conv, output, ["workload.py"], [])

    # Baseline: commit the compiling version and tag it phase-1-complete.
    _git(["init", "-q"], conv)
    _git(["add", "-A"], conv)
    _git(["commit", "-q", "-m", "phase 1"], conv)
    _git(["tag", "phase-1-complete"], conv)

    # Simulate a fix that broke syntax.
    f.write_text("def broken(:\n    pass\n")

    res = run_fixer_gate(state_path, revert_failing=True)

    assert res.reverted == ["workload.py"]
    assert any(fd.code == "fix_reverted" for fd in res.findings)
    assert not any(fd.code == "syntax_error" for fd in res.findings)
    assert res.verdict == "PASS_WITH_GAPS"   # advisory only, not blocking
    assert "x = 1" in f.read_text()          # original restored


def test_fixer_revert_failing_keeps_critical_when_no_baseline(tmp_path: Path):
    """If the revert cannot land (no git repo / tag), the file stays a blocking
    syntax_error so broken code is never silently accepted."""
    conv, output, _ = _conv(tmp_path)
    (output / "bad.py").write_text("def f(:\n    pass\n")
    res = run_fixer_gate(_state(conv, output, ["bad.py"], []), revert_failing=True)
    assert res.verdict == "FAIL"
    assert any(fd.code == "syntax_error" for fd in res.findings)
    assert res.reverted == []


# ---------------------------------------------------------------------------
# Notebook coverage (.ipynb)
# ---------------------------------------------------------------------------
#
# All four gates must apply the same checks to .ipynb / Databricks-native
# .python notebooks that they apply to .py files. Below we build minimal
# .ipynb files on disk and exercise each gate.


def _ipynb(cells_source: list[str], language: str = "python") -> str:
    """Return a minimal .ipynb JSON string with one code cell per source."""
    cells = []
    for src in cells_source:
        cells.append({
            "cell_type": "code",
            "metadata": {},
            "source": src.splitlines(keepends=True) if src else [],
            "execution_count": None,
            "outputs": [],
        })
    return json.dumps({
        "cells": cells,
        "metadata": {"kernelspec": {"language": language, "name": language}},
        "nbformat": 4,
        "nbformat_minor": 5,
    })


def _state_with_nb_index(conv: Path, output: Path, manifest: list[str],
                         nb_paths: list[Path], analysis=None) -> Path:
    """State with a notebook_index entry per .ipynb path (mirrors what
    orchestrate_phases.py writes)."""
    if analysis is not None:
        (conv / "analysis.json").write_text(json.dumps(analysis))
    nb_index = {
        str(p): {
            "format": "ipynb",
            "language": "python",
            "rel_path": str(p.relative_to(output)),
            "code_cells_by_language": {"python": 1},
        }
        for p in nb_paths
    }
    (conv / "migration_state.json").write_text(json.dumps({
        "conversion_root": str(conv),
        "migrated_dir": str(output),
        "manifest": manifest,
        "notebook_index": nb_index,
    }))
    return conv / "migration_state.json"


def test_imports_pass_for_ipynb_notebook(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    nb_path = output / "workload.ipynb"
    nb_path.write_text(_ipynb([
        '"""SCOS Migration Output\nMigrated."""\n',
        "from snowflake import snowpark_connect\nspark = snowpark_connect.init_spark_session()\n",
        "df = spark.range(10)\n",
    ]))
    state = _state_with_nb_index(conv, output, ["workload.ipynb"], [nb_path])
    res = run_imports_gate(state)
    assert res.verdict == "PASS", [f.message for f in res.findings]


def test_imports_fail_ipynb_missing_header(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    nb_path = output / "workload.ipynb"
    nb_path.write_text(_ipynb([
        "import pyspark\n",
        "from snowflake import snowpark_connect\n",
    ]))
    state = _state_with_nb_index(conv, output, ["workload.ipynb"], [nb_path])
    res = run_imports_gate(state)
    assert res.verdict == "FAIL"
    assert any(f.code == "missing_header" for f in res.findings)


def test_imports_fail_ipynb_builder_in_cell(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    nb_path = output / "workload.ipynb"
    nb_path.write_text(_ipynb([
        '"""SCOS Migration Output\n"""\n',
        "from snowflake import snowpark_connect\n",
        "spark = SparkSession.builder.appName('x').getOrCreate()\n",
    ]))
    state = _state_with_nb_index(conv, output, ["workload.ipynb"], [nb_path])
    res = run_imports_gate(state)
    assert res.verdict == "FAIL"
    assert any(f.code == "spark_builder_in_code" for f in res.findings)


def test_imports_fail_ipynb_unsupported_import(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    nb_path = output / "workload.ipynb"
    nb_path.write_text(_ipynb([
        '"""SCOS Migration Output\n"""\n',
        "from databricks.sdk.runtime import dbutils\n",
        "from snowflake import snowpark_connect\n",
    ]))
    state = _state_with_nb_index(conv, output, ["workload.ipynb"], [nb_path])
    res = run_imports_gate(state)
    assert res.verdict == "FAIL"
    assert any(f.code == "unsupported_import" for f in res.findings)


def test_imports_fail_ipynb_no_snowpark_connect(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    nb_path = output / "workload.ipynb"
    nb_path.write_text(_ipynb([
        '"""SCOS Migration Output\n"""\n',
        "import pyspark\n",
    ]))
    state = _state_with_nb_index(conv, output, ["workload.ipynb"], [nb_path])
    res = run_imports_gate(state)
    assert res.verdict == "FAIL"
    assert any(f.code == "missing_snowpark_connect" for f in res.findings)


def test_imports_fail_ipynb_manifest_file_missing(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    # Manifest claims the notebook but it doesn't exist on disk.
    state = _state_with_nb_index(conv, output, ["workload.ipynb", "lib.py"],
                                 [output / "workload.ipynb"])
    # write a stub .py so the gate has SOMETHING to scan
    (output / "lib.py").write_text(
        HEADER + "from snowflake import snowpark_connect\n")
    res = run_imports_gate(state)
    assert res.verdict == "FAIL"
    assert any(f.code == "manifest_file_missing"
               and f.file == "workload.ipynb" for f in res.findings)


def test_fixer_pass_for_ipynb_notebook(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    nb_path = output / "workload.ipynb"
    nb_path.write_text(_ipynb([
        '"""SCOS Migration Output\n"""\n',
        "from snowflake import snowpark_connect\nspark = snowpark_connect.init_spark_session()\n",
        "df = spark.range(10)\ndf.show()\n",
    ]))
    state = _state_with_nb_index(conv, output, ["workload.ipynb"], [nb_path])
    res = run_fixer_gate(state)
    assert res.verdict == "PASS", [f.message for f in res.findings]


def test_fixer_fail_ipynb_cell_syntax_error(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    nb_path = output / "workload.ipynb"
    nb_path.write_text(_ipynb([
        '"""SCOS Migration Output\n"""\n',
        "from snowflake import snowpark_connect\n",
        # Broken Python cell — missing close paren.
        "df = spark.range(10\n",
    ]))
    state = _state_with_nb_index(conv, output, ["workload.ipynb"], [nb_path])
    res = run_fixer_gate(state)
    assert res.verdict == "FAIL"
    assert any(f.code == "notebook_cell_syntax_error" for f in res.findings)


def test_analyzer_pass_for_ipynb_with_covered_blindspot(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    nb_path = output / "workload.ipynb"
    # Cell-1 imports pyspark; cell-2 uses sparkContext (a CRITICAL blind-
    # spot). When concatenated with `\n\n` joiner, the sparkContext line
    # ends up around line 4 of the flattened text, so the analysis.json
    # line range covers it.
    nb_path.write_text(_ipynb([
        "import pyspark\n",
        "x = spark.sparkContext.broadcast({})\n",
    ]))
    analysis = [{"file": "/x/workload.ipynb", "lines": "1-10",
                 "final_risk": 0.95, "kind": "standard"}]
    state = _state_with_nb_index(conv, output, ["workload.ipynb"],
                                 [nb_path], analysis=analysis)
    res = run_analyzer_gate(state)
    # No critical blind-spot finding — sparkContext is covered by the
    # analysis.json line range.
    assert not any(f.severity == "CRITICAL" and f.code.startswith("blindspot:")
                   for f in res.findings)


def test_analyzer_fail_ipynb_uncovered_blindspot(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    nb_path = output / "workload.ipynb"
    nb_path.write_text(_ipynb([
        "import pyspark\n",
        "x = spark.sparkContext.broadcast({})\n",
    ]))
    # analysis.json points at a different file → blind-spot uncovered.
    analysis = [{"file": "/x/other.py", "lines": "1-10", "final_risk": 0.5}]
    state = _state_with_nb_index(conv, output, ["workload.ipynb"],
                                 [nb_path], analysis=analysis)
    res = run_analyzer_gate(state)
    assert any(f.code.startswith("blindspot:") for f in res.findings)


# --- mechanical SQL rewrite enforcement (fixer gate check 6) ------------------

def _codes(res):
    return [f.code for f in res.findings]


def test_fixer_fail_sql_mechanical_only_annotated(tmp_path: Path):
    """A standalone .sql with a MECHANICAL gap that was only annotated (not
    rewritten) must FAIL — an annotation is not a fix for a mechanical case."""
    conv, output, _ = _conv(tmp_path)
    (output / "q.sql").write_text(
        "-- SCOS: [detector:explain_ddl_rejected] EXPLAIN over DDL is rejected\n"
        "EXPLAIN CREATE TABLE t AS SELECT 1;\n")
    res = run_fixer_gate(_state(conv, output, [], []))
    assert res.verdict == "FAIL"
    assert "sql_mechanical_not_rewritten" in _codes(res)


def test_fixer_pass_sql_mechanical_rewritten(tmp_path: Path):
    """The same gap, actually rewritten (EXPLAIN dropped), must not trip the
    mechanical-rewrite check."""
    conv, output, _ = _conv(tmp_path)
    (output / "q.sql").write_text(
        "CREATE TABLE t AS SELECT 1;\n")
    res = run_fixer_gate(_state(conv, output, [], []))
    assert "sql_mechanical_not_rewritten" not in _codes(res)


def test_fixer_window_without_order_by_does_not_trip_mechanical(tmp_path: Path):
    """Window-without-ORDER-BY is a JUDGMENT gap (no safe syntactic fix — see
    sql_ast.MECHANICAL_RULE_IDS), so leaving it annotated must NOT raise the
    mechanical-rewrite check. This guards against re-classifying it as mechanical
    and forcing the unsafe partition-key ORDER BY rewrite."""
    conv, output, _ = _conv(tmp_path)
    (output / "q.sql").write_text(
        "-- SCOS: TODO - [detector:window_without_order_by] add a real ORDER BY\n"
        "SELECT ROW_NUMBER() OVER (PARTITION BY x) AS rn FROM t;\n")
    res = run_fixer_gate(_state(conv, output, [], []))
    assert "sql_mechanical_not_rewritten" not in _codes(res)


def test_fixer_multicolumn_not_in_does_not_trip_mechanical(tmp_path: Path):
    """Multi-column NOT IN is a JUDGMENT gap (NOT EXISTS is not NULL-equivalent),
    so an annotated occurrence must NOT raise the mechanical-rewrite check."""
    conv, output, _ = _conv(tmp_path)
    (output / "q.sql").write_text(
        "-- SCOS: TODO - [detector:multicolumn_not_in] preserve NULL semantics\n"
        "SELECT * FROM t WHERE (a, b) NOT IN (SELECT a, b FROM u);\n")
    res = run_fixer_gate(_state(conv, output, [], []))
    assert "sql_mechanical_not_rewritten" not in _codes(res)


def test_fixer_sql_judgment_gap_does_not_trip_mechanical(tmp_path: Path):
    """A judgment-heavy gap (LCA collision) left annotated is NOT a mechanical
    case — it must not raise sql_mechanical_not_rewritten."""
    conv, output, _ = _conv(tmp_path)
    (output / "q.sql").write_text(
        "-- SCOS: TODO - [detector:lca_alias_collision] rename alias\n"
        "SELECT SUM(v) AS k FROM t GROUP BY k;\n")
    res = run_fixer_gate(_state(conv, output, [], []))
    assert "sql_mechanical_not_rewritten" not in _codes(res)


def test_fixer_fail_embedded_sql_mechanical_not_rewritten(tmp_path: Path):
    conv, output, _ = _conv(tmp_path)
    (output / "w.py").write_text(
        HEADER + "from snowflake import snowpark_connect\n"
        'df = spark.sql("EXPLAIN CREATE TABLE t AS SELECT 1")\n')
    res = run_fixer_gate(_state(conv, output, ["w.py"], []))
    assert res.verdict == "FAIL"
    assert "sql_mechanical_not_rewritten" in _codes(res)


def test_fixer_embedded_dynamic_sql_is_not_checked(tmp_path: Path):
    """Dynamic (f-string) embedded SQL can't be statically rewritten, so it must
    not be flagged by the mechanical-rewrite check."""
    conv, output, _ = _conv(tmp_path)
    (output / "w.py").write_text(
        HEADER + "from snowflake import snowpark_connect\n"
        "tbl = 't'\n"
        'df = spark.sql(f"EXPLAIN CREATE TABLE {tbl} AS SELECT 1")\n')
    res = run_fixer_gate(_state(conv, output, ["w.py"], []))
    assert "sql_mechanical_not_rewritten" not in _codes(res)
