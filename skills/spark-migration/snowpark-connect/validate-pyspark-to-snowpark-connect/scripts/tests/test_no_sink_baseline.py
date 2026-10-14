"""Tests for the no-sink smoke-baseline predicate.

A pure DDL/config entrypoint declares no write/display sink, so a clean run
(no error) is itself a valid baseline. `declares_any_sink` is the single signal
the driver + executor use to decide whether captured tables are required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import json

_HARNESS = Path(__file__).resolve().parents[1] / "harness"
sys.path.insert(0, str(_HARNESS))

import helpers  # noqa: E402
from runtimes import driver  # noqa: E402


def test_declares_any_sink_true_for_write_table():
    cfg = {"tables": {"out": {"access": "write", "category": "table"}}}
    assert helpers.declares_any_sink(cfg) is True


def test_declares_any_sink_true_for_readwrite():
    cfg = {"tables": {"t": {"access": "readwrite"}}}
    assert helpers.declares_any_sink(cfg) is True


def test_declares_any_sink_true_for_write_file_or_display():
    # file-category write sinks (incl. synthesized display sinks) also count
    cfg = {"tables": {"display_0": {"access": "write", "category": "file"}}}
    assert helpers.declares_any_sink(cfg) is True


def test_declares_any_sink_false_for_read_only():
    # pure DDL/config: reads only, no sink -> clean run is the baseline
    cfg = {"tables": {"src": {"access": "read", "category": "table"}}}
    assert helpers.declares_any_sink(cfg) is False


def test_declares_any_sink_false_for_no_tables():
    assert helpers.declares_any_sink({}) is False
    assert helpers.declares_any_sink({"tables": {}}) is False
    assert helpers.declares_any_sink({"tables": None}) is False


def test_declares_any_sink_default_access_is_read():
    # a table entry with no explicit access defaults to read -> not a sink
    cfg = {"tables": {"t": {"category": "table"}}}
    assert helpers.declares_any_sink(cfg) is False


def _phase_a(tmp_path, *, ok, tables):
    """Build a fake Phase A dir with an optional ok-status and parquet tables."""
    d = tmp_path / "phase_a"
    (d / "tables").mkdir(parents=True)
    for t in tables:
        (d / "tables" / f"{t}.parquet").write_text("", encoding="utf-8")
    (d / "_index.json").write_text(
        json.dumps(
            {
                "tables": [{"name": t, "path": f"tables/{t}.parquet"} for t in tables],
            }
        ),
        encoding="utf-8",
    )
    if ok is not None:
        (d / "_harness_status.json").write_text(json.dumps({"ok": ok}), encoding="utf-8")
    return str(d)


def _branch(declares_sinks, phase_a_dir):
    # Mirrors the scos post-capture decision in driver.run_validation_trial.
    if driver._has_phase_a_baseline(phase_a_dir):
        return "compare"
    if not declares_sinks and driver._phase_a_ran_clean(phase_a_dir):
        return "smoke_match"
    return "manual_review"


def test_no_sink_clean_run_takes_smoke_match_branch(tmp_path):
    # No declared sink + clean Phase A (ok:true, zero tables) -> smoke match, NOT a
    # manual-review/no-baseline marker.
    d = _phase_a(tmp_path, ok=True, tables=[])
    assert driver._has_phase_a_baseline(d) is False
    assert driver._phase_a_ran_clean(d) is True
    assert _branch(declares_sinks=False, phase_a_dir=d) == "smoke_match"


def test_sink_with_baseline_takes_compare_branch(tmp_path):
    d = _phase_a(tmp_path, ok=True, tables=["out"])
    assert driver._has_phase_a_baseline(d) is True
    assert _branch(declares_sinks=True, phase_a_dir=d) == "compare"


def test_declares_sink_but_zero_tables_is_manual_review(tmp_path):
    # A sink-declaring trial that captured zero tables must NOT be treated as a
    # smoke match — it falls through to manual review.
    d = _phase_a(tmp_path, ok=True, tables=[])
    assert driver._has_phase_a_baseline(d) is False
    assert _branch(declares_sinks=True, phase_a_dir=d) == "manual_review"


def test_no_sink_failed_phase_a_is_manual_review(tmp_path):
    # No sink but Phase A did not run clean (no ok status) -> manual review, not smoke.
    d = _phase_a(tmp_path, ok=False, tables=[])
    assert driver._phase_a_ran_clean(d) is False
    assert _branch(declares_sinks=False, phase_a_dir=d) == "manual_review"
