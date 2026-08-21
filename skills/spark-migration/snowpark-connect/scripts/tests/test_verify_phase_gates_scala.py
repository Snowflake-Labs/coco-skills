"""Tests for the Phase-2 orchestration gate and notebook-coverage checks added to
``verify_phase.py`` (Row C parity with the PySpark ``scos_gates.py``).
"""
from __future__ import annotations

import json
from pathlib import Path

from verify_phase import run_phase, STATUS_FAIL, STATUS_OK


def _check(report, name):
    for c in report.checks:
        if c.name == name:
            return c
    raise AssertionError(f"check {name!r} not found in {[c.name for c in report.checks]}")


def _has(report, name) -> bool:
    return any(c.name == name for c in report.checks)


def _scala_ipynb(scala_source: str) -> str:
    """Minimal Scala Jupyter notebook (one code cell) as JSON text."""
    nb = {
        "cells": [
            {"cell_type": "code", "metadata": {}, "source": scala_source.splitlines(keepends=True),
             "outputs": [], "execution_count": None}
        ],
        "metadata": {"kernelspec": {"name": "scala", "language": "scala", "display_name": "Scala"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(nb)


def _build(tmp_path: Path, *, scala=None, notebooks=None, analysis=None, state_extra=None) -> Path:
    conv = tmp_path / "Conversion-SCOS-test"
    out = conv / "Output"
    out.mkdir(parents=True)
    for rel, content in (scala or {}).items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    for rel, content in (notebooks or {}).items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    if analysis is not None:
        (conv / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    state = {
        "conversion_root": str(conv),
        "migrated_dir": str(out),
        "manifest": sorted((scala or {}).keys()) + sorted((notebooks or {}).keys()),
    }
    if state_extra:
        state.update(state_extra)
    sp = conv / "migration_state.json"
    sp.write_text(json.dumps(state), encoding="utf-8")
    return sp


# --- Orchestration gate -----------------------------------------------------


def test_orchestration_gate_fails_multifile_without_plan(tmp_path):
    sp = _build(tmp_path, scala={"A.scala": "object A\n", "B.scala": "object B\n"})
    report = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(report, "phase 2 orchestration").status == STATUS_FAIL


def test_orchestration_gate_ok_with_plan(tmp_path):
    sp = _build(
        tmp_path,
        scala={"A.scala": "object A\n", "B.scala": "object B\n"},
        state_extra={"max_parallel_fixers": 6, "phase2_chunks": {"chunks": [["A.scala"], ["B.scala"]]}},
    )
    report = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(report, "phase 2 orchestration").status == STATUS_OK


def test_orchestration_gate_ok_single_file_without_plan(tmp_path):
    # A single-file workload never needs the parallel pool.
    sp = _build(tmp_path, scala={"A.scala": "object A\n"})
    report = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(report, "phase 2 orchestration").status == STATUS_OK


def test_orchestration_gate_counts_notebooks_toward_multifile(tmp_path):
    # 1 scala file + 1 scala notebook == 2 code units -> plan required.
    sp = _build(
        tmp_path,
        scala={"A.scala": "object A\n"},
        notebooks={"nb.ipynb": _scala_ipynb('val df = spark.range(1)\n')},
    )
    report = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(report, "phase 2 orchestration").status == STATUS_FAIL


# --- Notebook coverage ------------------------------------------------------


def test_notebook_clean_passes_coverage(tmp_path):
    sp = _build(
        tmp_path,
        scala={"A.scala": "object A\n"},
        notebooks={"nb.ipynb": _scala_ipynb('val df = spark.range(1)\n')},
        state_extra={"max_parallel_fixers": 4, "phase2_chunks": {"chunks": []}},
    )
    report = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(report, "notebook validity").status == STATUS_OK
    assert _check(report, "notebook syntax artifacts").status == STATUS_OK


def test_notebook_with_import_artifact_fails(tmp_path):
    bad = 'import org.apache.spark.sql._ — removed in SCOS\nval df = spark.range(1)\n'
    sp = _build(
        tmp_path,
        scala={"A.scala": "object A\n"},
        notebooks={"nb.ipynb": _scala_ipynb(bad)},
        state_extra={"max_parallel_fixers": 4, "phase2_chunks": {"chunks": []}},
    )
    report = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(report, "notebook syntax artifacts").status == STATUS_FAIL


def test_notebook_high_risk_without_marker_fails(tmp_path):
    nb_src = 'val r = df.checkpoint()\n'  # high-risk, no // SCOS: marker
    sp = _build(
        tmp_path,
        scala={"A.scala": "object A\n"},
        notebooks={"nb.ipynb": _scala_ipynb(nb_src)},
        analysis=[{"file": "nb.ipynb", "final_risk": 0.9, "lines": "2-2", "root_cause": "checkpoint unsupported"}],
        state_extra={"max_parallel_fixers": 4, "phase2_chunks": {"chunks": []}},
    )
    report = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(report, "notebook high-risk coverage").status == STATUS_FAIL


def test_notebook_high_risk_with_marker_passes(tmp_path):
    nb_src = '// SCOS: checkpoint unsupported — annotate\nval r = df.checkpoint()\n'
    sp = _build(
        tmp_path,
        scala={"A.scala": "object A\n"},
        notebooks={"nb.ipynb": _scala_ipynb(nb_src)},
        analysis=[{"file": "nb.ipynb", "final_risk": 0.9, "lines": "3-3", "root_cause": "checkpoint unsupported"}],
        state_extra={"max_parallel_fixers": 4, "phase2_chunks": {"chunks": []}},
    )
    report = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(report, "notebook high-risk coverage").status == STATUS_OK


def test_no_notebook_checks_when_no_notebooks(tmp_path):
    # Pure .scala workload: notebook checks are not emitted (no noise).
    sp = _build(tmp_path, scala={"A.scala": "object A\n"})
    report = run_phase(2, json.loads(sp.read_text()), sp)
    assert not _has(report, "notebook validity")
