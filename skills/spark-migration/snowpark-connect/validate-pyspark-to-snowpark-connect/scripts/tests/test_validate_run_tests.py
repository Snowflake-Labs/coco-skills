"""Tests for validate.py run-tests subcommand.

Run: uv run --project <skill>/.. python -m pytest scripts/tests/ -q
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import validate  # noqa: E402

SCHEMA_VERSION = validate.SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(trials: dict) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": "init",
        "trials": trials,
    }


def _write_state(conv_root: Path, state: dict) -> None:
    val = conv_root / "Validation"
    val.mkdir(parents=True, exist_ok=True)
    (val / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _make_venv(conv_root: Path, phase: str) -> Path:
    venv_name = ".venv-source" if phase == "a" else ".venv-scos"
    venv_python = conv_root / "Validation" / "shared" / venv_name / "bin" / "python"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("#!/bin/sh\nexec python3 \"$@\"\n")
    return venv_python


def _fake_report(tests_dir: Path, trial_outcomes: dict) -> dict:
    """Build a minimal pytest-json-report dict for the given {trial_id: outcome} map."""
    tests = []
    for tid, outcome in trial_outcomes.items():
        tests.append({"nodeid": f"{tests_dir}/test_{tid}.py::test_main", "outcome": outcome})
    return {"tests": tests}


def _run_cmd(conv_root, phase, iter_n, verify_all=False, trial_id=None):
    return validate.cmd_run_tests(SimpleNamespace(
        conv_root=str(conv_root),
        phase=phase,
        iter=iter_n,
        verify_all=verify_all,
        trial_id=trial_id,
    ))


# ---------------------------------------------------------------------------
# Test 1: deselect set excludes terminal trials in Phase B
# ---------------------------------------------------------------------------

def test_deselect_terminal_trials_phase_b(tmp_path):
    """Phase B: passed + hard_stuck trials are deselected; pending trial runs."""
    state = _make_state({
        "trial_a": {"status": "passed"},
        "trial_b": {"status": "hard_stuck"},
        "trial_c": {"status": "pending"},
    })
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")

    results_dir = tmp_path / "Validation" / "results" / "phase_b"
    results_dir.mkdir(parents=True, exist_ok=True)

    report = _fake_report(tmp_path / "Validation" / "tests", {"trial_c": "passed"})
    report_path = results_dir / "pytest_3.json"

    captured_cmd = {}

    def fake_run(cmd, env=None, **kwargs):
        captured_cmd["cmd"] = cmd
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            _run_cmd(tmp_path, "b", 3)
    assert exc.value.code == 0

    cmd = captured_cmd["cmd"]
    k_idx = cmd.index("-k")
    k_expr = cmd[k_idx + 1]
    # Both terminal trials must be excluded
    assert "test_trial_a" in k_expr
    assert "test_trial_b" in k_expr
    # Pending trial must NOT be excluded
    assert "test_trial_c" not in k_expr


# ---------------------------------------------------------------------------
# Test 2: --verify-all skips deselect
# ---------------------------------------------------------------------------

def test_verify_all_skips_deselect(tmp_path):
    """--verify-all: no -k flag in pytest command, all trials run."""
    state = _make_state({
        "trial_passed": {"status": "passed"},
        "trial_pending": {"status": "pending"},
    })
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "a")

    results_dir = tmp_path / "Validation" / "results" / "phase_a"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / "pytest_1.json"

    report = _fake_report(
        tmp_path / "Validation" / "tests",
        {"trial_passed": "passed", "trial_pending": "passed"},
    )

    captured_cmd = {}

    def fake_run(cmd, env=None, **kwargs):
        captured_cmd["cmd"] = cmd
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            _run_cmd(tmp_path, "a", 1, verify_all=True)
    assert exc.value.code == 0

    assert "-k" not in captured_cmd["cmd"]


# ---------------------------------------------------------------------------
# Test 3: record-iter emitted for pending trials only
# ---------------------------------------------------------------------------

def test_record_iter_emitted_for_ran_trials(tmp_path):
    """record-iter is called for trials that ran (not deselected)."""
    state = _make_state({
        "ep_ok": {"status": "passed"},
        "ep_run": {"status": "pending"},
    })
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")

    results_dir = tmp_path / "Validation" / "results" / "phase_b"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / "pytest_2.json"

    report = _fake_report(
        tmp_path / "Validation" / "tests",
        {"ep_run": "passed"},  # ep_ok was deselected, not in report
    )

    def fake_run(cmd, env=None, **kwargs):
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return MagicMock(returncode=0)

    recorded = []

    def fake_record_iter_impl(conv_root, trial_id, phase, iter_n, passing, failing,
                              fix_category=None, _extra_entry=None):
        recorded.append({"trial_id": trial_id, "passing": passing, "failing": failing})

    with patch("subprocess.run", side_effect=fake_run):
        with patch.object(validate, "_record_iter_impl", side_effect=fake_record_iter_impl):
            with pytest.raises(SystemExit) as exc:
                _run_cmd(tmp_path, "b", 2)
    assert exc.value.code == 0

    assert len(recorded) == 1
    assert recorded[0]["trial_id"] == "ep_run"
    assert recorded[0]["passing"] == 1
    assert recorded[0]["failing"] == 0


# ---------------------------------------------------------------------------
# Test 4: phase_a_skipped deselected in Phase A but NOT in Phase B
# ---------------------------------------------------------------------------

def test_phase_a_skipped_deselect_semantics(tmp_path):
    """phase_a_skipped is terminal for Phase A but not for Phase B."""
    state = _make_state({
        "skipped_ep": {"status": "phase_a_skipped"},
        "pending_ep": {"status": "pending"},
    })
    _write_state(tmp_path, state)

    captured = {}

    def fake_run(cmd, env=None, **kwargs):
        captured["cmd"] = cmd
        # Write minimal report so no record-iter is attempted
        report_path.write_text(json.dumps({"tests": []}), encoding="utf-8")
        return MagicMock(returncode=0)

    # --- Phase A: phase_a_skipped should be deselected ---
    _make_venv(tmp_path, "a")
    results_dir_a = tmp_path / "Validation" / "results" / "phase_a"
    results_dir_a.mkdir(parents=True, exist_ok=True)
    report_path = results_dir_a / "pytest_1.json"

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit):
            _run_cmd(tmp_path, "a", 1)

    k_idx = captured["cmd"].index("-k")
    k_expr = captured["cmd"][k_idx + 1]
    assert "test_skipped_ep" in k_expr, "phase_a_skipped should be deselected in Phase A"

    # --- Phase B: phase_a_skipped should NOT be deselected ---
    captured.clear()
    _make_venv(tmp_path, "b")
    results_dir_b = tmp_path / "Validation" / "results" / "phase_b"
    results_dir_b.mkdir(parents=True, exist_ok=True)
    report_path = results_dir_b / "pytest_1.json"

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit):
            _run_cmd(tmp_path, "b", 1)

    assert "-k" not in captured["cmd"], (
        "phase_a_skipped must NOT be deselected in Phase B"
    )


# ---------------------------------------------------------------------------
# Test 5: --trial-id runs only the selected trial, even if terminal
# ---------------------------------------------------------------------------

def test_trial_id_runs_only_selected_trial_even_if_terminal(tmp_path):
    state = _make_state({
        "target_ep": {"status": "passed"},
        "other_ep": {"status": "pending"},
    })
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")

    results_dir = tmp_path / "Validation" / "results" / "phase_b"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / "pytest_4.json"
    report = _fake_report(tmp_path / "Validation" / "tests", {"target_ep": "passed"})

    captured_cmd = {}

    def fake_run(cmd, env=None, **kwargs):
        captured_cmd["cmd"] = cmd
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            _run_cmd(tmp_path, "b", 4, trial_id="target_ep")
    assert exc.value.code == 0

    cmd = captured_cmd["cmd"]
    k_idx = cmd.index("-k")
    k_expr = cmd[k_idx + 1]
    assert "test_other_ep" in k_expr
    assert "test_target_ep" not in k_expr


# ---------------------------------------------------------------------------
# Test 6: verify-all failure reopens a previously passed trial
# ---------------------------------------------------------------------------

def test_verify_all_failure_reopens_passed_trial(tmp_path):
    state = _make_state({
        "ep": {
            "status": "passed",
            "phase_a_iters": [{"iter": 1, "passing": 1, "failing": 0}],
            "phase_b_iters": [{"iter": 1, "passing": 1, "failing": 0}],
            "final_iter": 1,
        },
    })
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")

    results_dir = tmp_path / "Validation" / "results" / "phase_b"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / "pytest_5.json"
    report = _fake_report(tmp_path / "Validation" / "tests", {"ep": "failed"})

    def fake_run(cmd, env=None, **kwargs):
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return MagicMock(returncode=1)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            _run_cmd(tmp_path, "b", 5, verify_all=True)
    assert exc.value.code == 1

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    assert st["trials"]["ep"]["status"] == "pending"
    assert "final_iter" not in st["trials"]["ep"]


def test_trial_id_pass_refreshes_hard_stuck_trial(tmp_path):
    state = _make_state({
        "ep": {
            "status": "hard_stuck",
            "phase_a_iters": [{"iter": 1, "passing": 1, "failing": 0}],
            "phase_b_iters": [{"iter": 2, "passing": 0, "failing": 1}],
            "hard_stuck_reason": "old reason",
            "final_iter": 2,
        },
        "other_ep": {"status": "pending"},
    })
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")

    results_dir = tmp_path / "Validation" / "results" / "phase_b"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / "pytest_6.json"
    report = _fake_report(tmp_path / "Validation" / "tests", {"ep": "passed"})

    def fake_run(cmd, env=None, **kwargs):
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            _run_cmd(tmp_path, "b", 6, trial_id="ep")
    assert exc.value.code == 0

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    assert st["trials"]["ep"]["status"] == "passed"
    assert st["trials"]["ep"]["final_iter"] == 6
    assert "hard_stuck_reason" not in st["trials"]["ep"]


# ---------------------------------------------------------------------------
# Test 8: missing venv → clear error, non-zero exit
# ---------------------------------------------------------------------------

def test_missing_venv_exits_nonzero(tmp_path):
    """If the venv doesn't exist, die with exit code 2."""
    state = _make_state({"some_trial": {"status": "pending"}})
    _write_state(tmp_path, state)
    # deliberately do NOT create the venv

    with pytest.raises(SystemExit) as exc:
        _run_cmd(tmp_path, "b", 1)
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Test 9: Phase B pass auto-promotes trial status
# ---------------------------------------------------------------------------

def test_run_tests_auto_promotes_passed(tmp_path):
    state = _make_state({
        "ep": {
            "status": "pending",
            "phase_a_iters": [{"iter": 1, "passing": 1, "failing": 0}],
            "phase_b_iters": [],
        },
    })
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")

    results_dir = tmp_path / "Validation" / "results" / "phase_b"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / "pytest_3.json"
    report = _fake_report(tmp_path / "Validation" / "tests", {"ep": "passed"})

    def fake_run(cmd, env=None, **kwargs):
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            _run_cmd(tmp_path, "b", 3)
    assert exc.value.code == 0

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    assert st["trials"]["ep"]["status"] == "passed"
    assert st["trials"]["ep"]["final_iter"] == 3


def test_trial_id_unknown_exits_nonzero(tmp_path):
    state = _make_state({"ep": {"status": "pending"}})
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")

    with pytest.raises(SystemExit) as exc:
        _run_cmd(tmp_path, "b", 1, trial_id="missing_ep")
    assert exc.value.code == 2


def test_run_tests_auto_promotes_passed_no_baseline(tmp_path):
    state = _make_state({
        "ep": {"status": "phase_a_skipped", "phase_b_iters": []},
    })
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")

    results_dir = tmp_path / "Validation" / "results" / "phase_b"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / "pytest_1.json"
    report = _fake_report(tmp_path / "Validation" / "tests", {"ep": "passed"})

    def fake_run(cmd, env=None, **kwargs):
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            _run_cmd(tmp_path, "b", 1)
    assert exc.value.code == 0

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    assert st["trials"]["ep"]["status"] == "passed_no_baseline"


def test_run_tests_does_not_auto_promote_failures(tmp_path):
    state = _make_state({"ep": {"status": "pending", "phase_b_iters": []}})
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")

    results_dir = tmp_path / "Validation" / "results" / "phase_b"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / "pytest_2.json"
    report = _fake_report(tmp_path / "Validation" / "tests", {"ep": "failed"})

    def fake_run(cmd, env=None, **kwargs):
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return MagicMock(returncode=1)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            _run_cmd(tmp_path, "b", 2)
    assert exc.value.code == 1

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    assert st["trials"]["ep"]["status"] == "pending"


# ---------------------------------------------------------------------------
# No-progress detector
# ---------------------------------------------------------------------------


def _fake_report_with_msg(tests_dir: Path, trial_id: str, message: str,
                          outcome: str = "failed") -> dict:
    """pytest-json-report dict with a crash message for one failing trial."""
    return {"tests": [{
        "nodeid": f"{tests_dir}/test_{trial_id}.py::test_main",
        "outcome": outcome,
        "call": {"outcome": outcome, "crash": {"message": message}},
    }]}


def _load_events(conv_root: Path) -> list:
    p = conv_root / "Validation" / "events.jsonl"
    if not p.is_file():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


@pytest.mark.parametrize("message,expected", [
    # SCOS banner with no Spark error class -> code only (back-compat)
    ("======\nSNOWPARK CONNECT ERROR CODE: 5001\n======\ntraceback...", "SCOS_ERR_5001"),
    ("SNOWPARK CONNECT ERROR CODE: 0100", "SCOS_ERR_0100"),
    # Same SCOS code, DIFFERENT Spark error class / object -> distinct signatures
    # (so fixing one 5001 and hitting a different 5001 counts as PROGRESS).
    ("SNOWPARK CONNECT ERROR CODE: 5001 org.apache.spark.sql.catalyst.parser."
     "ParseException: [PARSE_SYNTAX_ERROR] Syntax error at or near end of input",
     "SCOS_ERR_5001:PARSE_SYNTAX_ERROR"),
    ("SNOWPARK CONNECT ERROR CODE: 5001 [TABLE_OR_VIEW_NOT_FOUND] The table or view "
     "cannot be found. cams_glue_catalog.l0.corprd_nhe_mtr_org_m",
     "SCOS_ERR_5001:TABLE_OR_VIEW_NOT_FOUND:cams_glue_catalog.l0.corprd_nhe_mtr_org_m"),
    ("ModuleNotFoundError: No module named 'pyspark.sql.connect'", "NoModule:pyspark.sql.connect"),
    ("No module named foo", "NoModule:foo"),
    ("NameError: name 'spark' is not defined", "NameUndef:spark"),
    ("AssertionError: sink produced 0 rows for table X", "EmptySink"),
    ("AssertionError: result set was empty (empty sink)", "EmptySink"),
    ("StopIteration", "StopIteration"),
    ("E   StopIteration", "StopIteration"),
    ("SystemExit: 1", "SystemExit:1"),
    ("AssertionError: assert 1 == 2", "AssertionError:assert 1 == 2"),
    ("builtins.TimeoutError: timed out", "TimeoutError:timed out"),
    ("", "Unknown"),
    (None, "Unknown"),
])
def test_normalize_failure_signature(message, expected):
    assert validate._normalize_failure_signature(message) == expected


def test_normalize_failure_signature_scrubs_volatile_bits():
    # Two failures identical apart from a uuid / hex addr / line number must
    # normalize to the SAME signature (so an identical failure is seen as identical).
    a = "RuntimeError: query 12345678-1234-1234-1234-1234567890ab failed at line 42 @ 0xdeadbeef"
    b = "RuntimeError: query abcdef01-abcd-abcd-abcd-abcdef012345 failed at line 99 @ 0xfeedface"
    sig_a = validate._normalize_failure_signature(a)
    sig_b = validate._normalize_failure_signature(b)
    assert sig_a == sig_b
    assert sig_a.startswith("RuntimeError:")


def test_scos_same_code_different_error_is_distinct_signature():
    # The regression driver: code-only signatures conflated a PATCHABLE parse error
    # and a table-not-found under one "SCOS_ERR_5001", so fixing one and hitting a
    # different one looked like "no progress". Enriched signatures must separate them.
    f = validate._normalize_failure_signature
    parse = "SNOWPARK CONNECT ERROR CODE: 5001 [PARSE_SYNTAX_ERROR] Syntax error"
    tbl_a = "SNOWPARK CONNECT ERROR CODE: 5001 [TABLE_OR_VIEW_NOT_FOUND] found. cams.l0.tbl_a"
    tbl_b = "SNOWPARK CONNECT ERROR CODE: 5001 [TABLE_OR_VIEW_NOT_FOUND] found. cams.l0.tbl_b"
    assert len({f(parse), f(tbl_a), f(tbl_b)}) == 3
    # but an identical failure still collapses to the same signature (real no-progress)
    assert f(tbl_a) == f(tbl_a.replace("5001", "5001"))


def test_next_no_progress_run_counts_and_resets():
    fn = validate._next_no_progress_run
    # identical signature repeats extend the run
    sig, count = fn(None, 0, "SCOS_ERR_5001")
    assert (sig, count) == ("SCOS_ERR_5001", 1)
    sig, count = fn(sig, count, "SCOS_ERR_5001")
    assert (sig, count) == ("SCOS_ERR_5001", 2)
    sig, count = fn(sig, count, "SCOS_ERR_5001")
    assert (sig, count) == ("SCOS_ERR_5001", 3)
    # signature CHANGE resets the run to 1 (that is progress)
    sig, count = fn(sig, count, "NoModule:foo")
    assert (sig, count) == ("NoModule:foo", 1)
    # a passing iter (None) resets to 0
    sig, count = fn(sig, count, None)
    assert (sig, count) == (None, 0)


def test_default_plateau_does_not_change_terminal_status(tmp_path, monkeypatch):
    """PASS-RATE-PRESERVATION GUARANTEE.

    With DEFAULT settings, an identical failure repeated far past the signal
    threshold must NOT change the trial's terminal outcome — the trial stays
    pending (still able to eventually pass), and the ONLY effect is the
    no_progress_detected marker event.
    """
    # Ensure defaults: signal at K=4, hard stop OFF.
    monkeypatch.delenv("SCOS_NO_PROGRESS_SIGNAL_K", raising=False)
    monkeypatch.delenv("SCOS_NO_PROGRESS_HARD_STOP_K", raising=False)

    state = _make_state({"ep": {"status": "pending", "phase_b_iters": []}})
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")
    results_dir = tmp_path / "Validation" / "results" / "phase_b"
    results_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = tmp_path / "Validation" / "tests"

    # 10 consecutive iterations, all the SAME failure signature (well past the
    # observed real-pass plateaus this detector must never truncate).
    for it in range(1, 11):
        report_path = results_dir / f"pytest_{it}.json"
        report = _fake_report_with_msg(
            tests_dir, "ep", "SNOWPARK CONNECT ERROR CODE: 5001\ntraceback...")

        def fake_run(cmd, env=None, _rp=report_path, _rep=report, **kwargs):
            _rp.write_text(json.dumps(_rep), encoding="utf-8")
            return MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(SystemExit) as exc:
                _run_cmd(tmp_path, "b", it)
        assert exc.value.code == 1

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    # Terminal outcome is IDENTICAL to today: still pending, never abandoned.
    assert st["trials"]["ep"]["status"] == "pending"
    # The detector DID track the plateau and emitted the escalation signal.
    assert st["trials"]["ep"]["no_progress"]["signature"] == "SCOS_ERR_5001"
    assert st["trials"]["ep"]["no_progress"]["count"] == 10

    events = _load_events(tmp_path)
    np_events = [e for e in events if e.get("kind") == "no_progress_detected"]
    # Signal starts at K=4 -> iters 4..10 inclusive = 7 events; none change status.
    assert len(np_events) == 7
    assert np_events[0]["signature"] == "SCOS_ERR_5001"
    assert np_events[0]["count"] == 4
    assert all(e["kind"] == "no_progress_detected" for e in np_events)
    # No trial_marked (terminal) event was emitted by the plateau.
    assert not [e for e in events
                if e.get("kind") == "trial_marked" and e.get("no_progress")]


def test_signature_change_resets_counter_and_clears_tracker(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOS_NO_PROGRESS_SIGNAL_K", raising=False)
    monkeypatch.delenv("SCOS_NO_PROGRESS_HARD_STOP_K", raising=False)

    state = _make_state({"ep": {"status": "pending", "phase_b_iters": []}})
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")
    results_dir = tmp_path / "Validation" / "results" / "phase_b"
    results_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = tmp_path / "Validation" / "tests"

    plan = [
        (1, "SNOWPARK CONNECT ERROR CODE: 5001"),
        (2, "SNOWPARK CONNECT ERROR CODE: 5001"),
        (3, "NameError: name 'spark' is not defined"),  # signature CHANGED
    ]
    for it, msg in plan:
        report_path = results_dir / f"pytest_{it}.json"
        report = _fake_report_with_msg(tests_dir, "ep", msg)

        def fake_run(cmd, env=None, _rp=report_path, _rep=report, **kwargs):
            _rp.write_text(json.dumps(_rep), encoding="utf-8")
            return MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(SystemExit):
                _run_cmd(tmp_path, "b", it)

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    # Counter reset on the changed signature: new run is at 1, not 3.
    assert st["trials"]["ep"]["no_progress"]["signature"] == "NameUndef:spark"
    assert st["trials"]["ep"]["no_progress"]["count"] == 1


def test_pass_clears_no_progress_tracker(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOS_NO_PROGRESS_SIGNAL_K", raising=False)
    monkeypatch.delenv("SCOS_NO_PROGRESS_HARD_STOP_K", raising=False)

    state = _make_state({
        "ep": {
            "status": "pending",
            "phase_a_iters": [{"iter": 1, "passing": 1, "failing": 0}],
            "phase_b_iters": [],
            "no_progress": {"signature": "SCOS_ERR_5001", "count": 3, "iter": 1},
        },
    })
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")
    results_dir = tmp_path / "Validation" / "results" / "phase_b"
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / "pytest_2.json"
    report = _fake_report(tmp_path / "Validation" / "tests", {"ep": "passed"})

    def fake_run(cmd, env=None, **kwargs):
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            _run_cmd(tmp_path, "b", 2)
    assert exc.value.code == 0

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    assert st["trials"]["ep"]["status"] == "passed"  # promotion unaffected
    assert "no_progress" not in st["trials"]["ep"]  # tracker cleared on progress


def test_optin_hard_stop_off_by_default_no_status_change(tmp_path, monkeypatch):
    """Even with a fixer dispatch on record, a plateau does NOT hard-stop unless the
    opt-in env var is set (default OFF)."""
    monkeypatch.delenv("SCOS_NO_PROGRESS_HARD_STOP_K", raising=False)
    monkeypatch.setenv("SCOS_NO_PROGRESS_SIGNAL_K", "2")

    state = _make_state({"ep": {"status": "pending", "phase_b_iters": []}})
    state["fixer_dispatches"] = [{"trials_affected": ["ep"]}]
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "b")
    results_dir = tmp_path / "Validation" / "results" / "phase_b"
    results_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = tmp_path / "Validation" / "tests"

    for it in range(1, 6):
        report_path = results_dir / f"pytest_{it}.json"
        report = _fake_report_with_msg(tests_dir, "ep", "StopIteration")

        def fake_run(cmd, env=None, _rp=report_path, _rep=report, **kwargs):
            _rp.write_text(json.dumps(_rep), encoding="utf-8")
            return MagicMock(returncode=1)

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(SystemExit):
                _run_cmd(tmp_path, "b", it)

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    assert st["trials"]["ep"]["status"] == "pending"  # hard stop OFF -> unchanged


def test_optin_hard_stop_gated_requires_fixer_dispatch(tmp_path, monkeypatch):
    """Opt-in hard stop enabled: it fires only when the hard_stuck gate is satisfied
    (a fixer dispatch is on record), never on a raw plateau — protecting real passes."""
    monkeypatch.setenv("SCOS_NO_PROGRESS_HARD_STOP_K", "3")
    monkeypatch.setenv("SCOS_NO_PROGRESS_SIGNAL_K", "2")

    def _drive(conv_root, with_dispatch: bool):
        st = _make_state({"ep": {"status": "pending", "phase_b_iters": []}})
        if with_dispatch:
            st["fixer_dispatches"] = [{"trials_affected": ["ep"]}]
        _write_state(conv_root, st)
        _make_venv(conv_root, "b")
        results_dir = conv_root / "Validation" / "results" / "phase_b"
        results_dir.mkdir(parents=True, exist_ok=True)
        tests_dir = conv_root / "Validation" / "tests"
        for it in range(1, 5):
            report_path = results_dir / f"pytest_{it}.json"
            report = _fake_report_with_msg(tests_dir, "ep", "StopIteration")

            def fake_run(cmd, env=None, _rp=report_path, _rep=report, **kwargs):
                _rp.write_text(json.dumps(_rep), encoding="utf-8")
                return MagicMock(returncode=1)

            with patch("subprocess.run", side_effect=fake_run):
                with pytest.raises(SystemExit):
                    _run_cmd(conv_root, "b", it)
        return json.loads((conv_root / "Validation" / "state.json").read_text())

    # No fixer dispatch -> gate blocks -> stays pending even with hard stop enabled.
    ungated = _drive(tmp_path / "ungated", with_dispatch=False)
    assert ungated["trials"]["ep"]["status"] == "pending"

    # Fixer dispatch on record -> gate allows -> opt-in hard stop fires.
    gated = _drive(tmp_path / "gated", with_dispatch=True)
    assert gated["trials"]["ep"]["status"] == "hard_stuck"
    assert "no-progress hard stop" in gated["trials"]["ep"]["hard_stuck_reason"]


def test_reopen_clears_phase_a_skip_reason(tmp_path):
    # Reopening a passed_no_baseline trial must drop its stale phase_a_skip_reason so
    # that a fresh Phase A yielding a real baseline promotes to passed, not
    # passed_no_baseline.
    state = _make_state({
        "ep": {
            "status": "passed_no_baseline",
            "phase_a_skip_reason": "connector read returned 0 rows locally",
            "phase_a_iters": [{"iter": 1, "passing": 1, "failing": 0}],
            "phase_b_iters": [{"iter": 1, "passing": 1, "failing": 0}],
            "final_iter": 1,
        },
    })
    _write_state(tmp_path, state)
    reopened = validate._maybe_reopen_trial_after_phase_b_failure(
        tmp_path, "ep", phase="B", iter_n=2, passing=0, failing=1,
        allow_terminal_refresh=True,
    )
    assert reopened is True
    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    assert st["trials"]["ep"]["status"] == "pending"
    assert "phase_a_skip_reason" not in st["trials"]["ep"]


# ---------------------------------------------------------------------------
# Iteration counter is owned by run-tests (no agent-supplied --iter)
# ---------------------------------------------------------------------------

def test_run_tests_auto_increments_iter_when_omitted(tmp_path):
    """Two run-tests calls without --iter advance the counter: 1, then 2."""
    state = _make_state({"ep": {"status": "pending", "phase_a_iters": []}})
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "a")
    results_dir = tmp_path / "Validation" / "results" / "phase_a"
    results_dir.mkdir(parents=True, exist_ok=True)

    def fake_run(cmd, env=None, **kwargs):
        report_file = next(
            a.split("=", 1)[1] for a in cmd if a.startswith("--json-report-file=")
        )
        Path(report_file).write_text(
            json.dumps(_fake_report(tmp_path / "Validation" / "tests", {"ep": "failed"})),
            encoding="utf-8",
        )
        return MagicMock(returncode=1)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit):
            _run_cmd(tmp_path, "a", None)  # iter omitted → auto 1
        with pytest.raises(SystemExit):
            _run_cmd(tmp_path, "a", None)  # iter omitted → auto 2

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    iters = [it["iter"] for it in st["trials"]["ep"]["phase_a_iters"]]
    assert iters == [1, 2], iters
    assert st["phase_a"]["iter"] == 2


def test_record_iter_defaults_to_current_counter(tmp_path):
    """record-iter with no --iter attaches to the phase's current counter."""
    state = _make_state({"ep": {"status": "pending", "phase_a_iters": []}})
    state["phase_a"] = {"iter": 3}
    _write_state(tmp_path, state)

    validate.cmd_record_iter(SimpleNamespace(
        conv_root=str(tmp_path), trial_id="ep", phase="A", iter=None,
        passing=0, failing=1, issues=None, patches_extended=None,
        fix_commit=None, fix_category="analysis_repair",
    ))

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    assert st["trials"]["ep"]["phase_a_iters"][-1]["iter"] == 3


def test_record_fixer_dispatch_defaults_to_phase_b_iter(tmp_path):
    """record-fixer-dispatch with no --iter uses the current Phase B counter."""
    state = _make_state({"ep": {"status": "pending"}})
    state["phase_b"] = {"iter": 5}
    _write_state(tmp_path, state)

    validate.cmd_record_fixer_dispatch(SimpleNamespace(
        conv_root=str(tmp_path), iter=None, error_class="patch_failure",
        error_hash="abc", trial_ids="ep", outcome="no_change",
    ))

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    assert st["fixer_dispatches"][-1]["iter"] == 5


def test_run_tests_advances_iter_even_when_no_results_are_recorded(tmp_path):
    """The counter must advance from run-tests itself, not as a side effect of
    recording a trial result. If pytest dies before writing its JSON report, a
    reused iter number would silently no-op the next run's record-iter."""
    state = _make_state({"ep": {"status": "pending", "phase_a_iters": []}})
    _write_state(tmp_path, state)
    _make_venv(tmp_path, "a")
    (tmp_path / "Validation" / "results" / "phase_a").mkdir(parents=True, exist_ok=True)

    def fake_run_no_report(cmd, env=None, **kwargs):
        return MagicMock(returncode=2)      # pytest crashed; no report written

    with patch("subprocess.run", side_effect=fake_run_no_report):
        for _ in range(2):
            with pytest.raises(SystemExit):
                _run_cmd(tmp_path, "a", None)

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    assert st["phase_a"]["iter"] == 2, st
    assert st["trials"]["ep"]["phase_a_iters"] == []


def test_record_patch_defaults_to_the_iter_that_just_ran(tmp_path):
    """record-patch with no --iter attaches to run-tests' current counter."""
    state = _make_state({"ep": {"status": "pending", "phase_a_iters": []}})
    state["phase_a"] = {"iter": 4}
    _write_state(tmp_path, state)

    validate.cmd_record_patch(SimpleNamespace(
        conv_root=str(tmp_path), trial_id="ep", phase="phase_a", iter=None,
        file="Output/job.py", reason="stage path", diff_path=None,
    ))

    st = json.loads((tmp_path / "Validation" / "state.json").read_text())
    assert st["trials"]["ep"]["patches"][-1]["iter"] == 4
