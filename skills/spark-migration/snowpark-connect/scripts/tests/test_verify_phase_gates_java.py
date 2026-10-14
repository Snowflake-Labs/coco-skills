"""Tests for Phase-2 orchestration gate and Java notebook-coverage checks added
to ``verify_phase.py`` (Phase 2 parity with the Scala/PySpark gates).

Covers:
  - verify_phase_2_java: orchestration gate (multi-file without plan → FAIL)
  - verify_phase_2_java: notebook check (Java ipynb) added as Check 9
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_SCRIPTS.parent))

if "verify_phase" in sys.modules:
    del sys.modules["verify_phase"]

import verify_phase as VP  # noqa: E402


def _check(report, name):
    for c in report.checks:
        if c.name == name:
            return c
    raise AssertionError(f"check {name!r} not found; available: {[c.name for c in report.checks]}")


def _has(report, name) -> bool:
    return any(c.name == name for c in report.checks)


def _java_ipynb(java_source: str) -> str:
    """Minimal Java Jupyter notebook (one code cell) as JSON text."""
    nb = {
        "cells": [
            {"cell_type": "code", "metadata": {},
             "source": java_source.splitlines(keepends=True),
             "outputs": [], "execution_count": None}
        ],
        "metadata": {"kernelspec": {"name": "java", "language": "java"}},
        "nbformat": 4, "nbformat_minor": 5,
    }
    return json.dumps(nb)


def _build(tmp_path: Path, *, java=None, notebooks=None, analysis=None,
           state_extra=None) -> Path:
    conv = tmp_path / "Conversion-SCOS-test"
    out = conv / "Output"
    out.mkdir(parents=True)

    for name, content in (java or {}).items():
        f = out / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")

    for name, content in (notebooks or {}).items():
        f = out / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")

    state: dict = {
        "schema_version": 1,
        "migration_dir": str(out),
        "original_source_path": str(out),
        "phases_completed": {},
    }
    if state_extra:
        state.update(state_extra)

    if analysis is not None:
        (conv / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")

    return conv


# ---------------------------------------------------------------------------
# verify_phase_2_java: orchestration gate
# ---------------------------------------------------------------------------


def test_java_phase2_orchestration_passes_single_file(tmp_path):
    """A single-file workload is allowed without an orchestration plan."""
    conv = _build(tmp_path, java={"Job.java": "class Job {}\n"})
    report = VP.run_phase(2, {
        "schema_version": 1,
        "migration_dir": str(conv / "Output"),
        "original_source_path": str(conv / "Output"),
        "phases_completed": {},
    }, conv / "migration_state.json", language="java")
    orch = _check(report, "phase 2 orchestration")
    assert orch.status == VP.STATUS_OK


def test_java_phase2_orchestration_fails_multifile_without_plan(tmp_path):
    """Multi-file without orchestrator plan must FAIL (prevents inline-agent bypass)."""
    conv = _build(tmp_path, java={
        "Job1.java": "class Job1 {}\n",
        "Job2.java": "class Job2 {}\n",
    })
    report = VP.run_phase(2, {
        "schema_version": 1,
        "migration_dir": str(conv / "Output"),
        "original_source_path": str(conv / "Output"),
        "phases_completed": {},
    }, conv / "migration_state.json", language="java")
    orch = _check(report, "phase 2 orchestration")
    assert orch.status == VP.STATUS_FAIL, orch.detail


def test_java_phase2_orchestration_passes_multifile_with_plan(tmp_path):
    """Multi-file WITH an orchestration plan must pass the gate."""
    conv = _build(tmp_path, java={
        "Job1.java": "class Job1 {}\n",
        "Job2.java": "class Job2 {}\n",
    })
    report = VP.run_phase(2, {
        "schema_version": 1,
        "migration_dir": str(conv / "Output"),
        "original_source_path": str(conv / "Output"),
        "phases_completed": {},
        "max_parallel_fixers": 2,
        "phase2_chunks": [["Job1.java"], ["Job2.java"]],
    }, conv / "migration_state.json", language="java")
    orch = _check(report, "phase 2 orchestration")
    assert orch.status == VP.STATUS_OK, orch.detail


# ---------------------------------------------------------------------------
# verify_phase_2_java: notebook coverage (Check 9)
# ---------------------------------------------------------------------------


def test_java_phase2_no_notebook_checks_when_no_notebooks(tmp_path):
    """No notebook-validity check should appear when there are no notebooks."""
    conv = _build(tmp_path, java={"Job.java": "class Job {}\n"})
    report = VP.run_phase(2, {
        "schema_version": 1,
        "migration_dir": str(conv / "Output"),
        "original_source_path": str(conv / "Output"),
        "phases_completed": {},
    }, conv / "migration_state.json", language="java")
    # notebook validity check must be absent — no noise for non-notebook workloads.
    assert not _has(report, "notebook validity"), (
        "notebook validity check must be absent when there are no notebooks"
    )


def test_java_phase2_notebook_validity_passes_for_valid_notebook(tmp_path):
    conv = _build(tmp_path,
                  java={"Job.java": "class Job {}\n"},
                  notebooks={"Notebook.ipynb": _java_ipynb('df.select(col("x"));')})
    report = VP.run_phase(2, {
        "schema_version": 1,
        "migration_dir": str(conv / "Output"),
        "original_source_path": str(conv / "Output"),
        "phases_completed": {},
    }, conv / "migration_state.json", language="java")
    if _has(report, "notebook validity"):
        nv = _check(report, "notebook validity")
        assert nv.status == VP.STATUS_OK, nv.detail


def test_java_iter_java_files_excludes_ipynb(tmp_path):
    """ipynb files must not be returned by iter_java_files; they go to _jvm_notebook_units.

    This prevents double-counting in the orchestration gate: a Java ipynb notebook
    is counted once (by the notebook coverage path) not twice.
    """
    flat = tmp_path / "Job.java"
    flat.write_text("class Job {}\n", encoding="utf-8")
    nb = tmp_path / "Nb.ipynb"
    nb.write_text(_java_ipynb("df.show();"), encoding="utf-8")
    found = {p.name for p in VP.iter_java_files(tmp_path)}
    assert "Job.java" in found
    assert "Nb.ipynb" not in found, "ipynb must not appear in iter_java_files output"


def test_jvm_notebook_units_are_language_scoped(tmp_path):
    """_jvm_notebook_units must return Java notebooks for language='java' only."""
    scala_nb = tmp_path / "S.ipynb"
    java_nb = tmp_path / "J.ipynb"
    scala_nb.write_text(json.dumps({
        "cells": [{"cell_type": "code", "metadata": {}, "source": ["df.show()"],
                   "outputs": [], "execution_count": None}],
        "metadata": {"kernelspec": {"language": "scala"}},
        "nbformat": 4, "nbformat_minor": 5,
    }), encoding="utf-8")
    java_nb.write_text(_java_ipynb("df.show();"), encoding="utf-8")

    java_units = {u["display"] for u in VP._jvm_notebook_units(tmp_path, {}, "java")}
    scala_units = {u["display"] for u in VP._jvm_notebook_units(tmp_path, {}, "scala")}
    assert java_units == {"J.ipynb"}
    assert scala_units == {"S.ipynb"}
