"""Integration test: run-tests against a realistic Validation workspace layout.

Exercises the full run-tests path (real pytest + JSON report + state writes)
without SCOS/Snowflake — trial tests are minimal pass/fail stubs in the
installed harness directory.

Run: uv run pytest -m slow scripts/tests/test_validate_run_tests_integration.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
import validate  # noqa: E402

SCHEMA_VERSION = validate.SCHEMA_VERSION


def _write_trial_test(tests_dir: Path, trial_id: str, *, passing: bool) -> None:
    body = "    assert True\n" if passing else "    assert False, 'simulated workload failure'\n"
    (tests_dir / f"test_{trial_id}.py").write_text(
        f"def test_main_entrypoint():\n{body}",
        encoding="utf-8",
    )


def _write_state(conv_root: Path, trials: dict) -> None:
    val = conv_root / "Validation"
    val.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": SCHEMA_VERSION,
        "phase": "init",
        "trials": trials,
    }
    (val / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _ensure_scos_venv(conv_root: Path) -> Path:
    """Real venv with pytest tooling — mirrors seed-venv output path."""
    venv_dir = conv_root / "Validation" / "shared" / ".venv-scos"
    venv_python = venv_dir / "bin" / "python"
    if venv_python.is_file():
        return venv_python

    subprocess.run(
        ["uv", "venv", "--seed", str(venv_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "uv", "pip", "install", "--python", str(venv_python),
            "pytest>=8", "pytest-json-report>=1.5", "pytest-xdist>=3",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return venv_python


def _build_workspace(tmp_path: Path) -> Path:
    conv_root = tmp_path / "conversion"
    conv_root.mkdir()

    validate.cmd_install_kit(SimpleNamespace(conv_root=str(conv_root)))

    tests_dir = conv_root / "Validation" / "tests"
    _write_trial_test(tests_dir, "ep_baseline", passing=True)
    _write_trial_test(tests_dir, "ep_skipped", passing=True)
    _write_trial_test(tests_dir, "ep_failing", passing=False)

    _write_state(conv_root, {
        "ep_baseline": {
            "status": "pending",
            "phase_a_iters": [{"iter": 1, "passing": 1, "failing": 0}],
            "phase_b_iters": [],
        },
        "ep_skipped": {
            "status": "phase_a_skipped",
            "phase_b_iters": [],
        },
        "ep_failing": {
            "status": "pending",
            "phase_b_iters": [],
        },
        "ep_done": {
            "status": "passed",
            "phase_a_iters": [{"iter": 1, "passing": 1, "failing": 0}],
            "phase_b_iters": [{"iter": 1, "passing": 1, "failing": 0}],
        },
    })

    _ensure_scos_venv(conv_root)
    return conv_root


def _load_state(conv_root: Path) -> dict:
    return json.loads((conv_root / "Validation" / "state.json").read_text(encoding="utf-8"))


def _load_events(conv_root: Path) -> list[dict]:
    path = conv_root / "Validation" / "events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.slow
def test_run_tests_realistic_workspace_auto_promotes_and_records(tmp_path):
    """Full run-tests path on a harness-shaped workspace."""
    conv_root = _build_workspace(tmp_path)
    tests_dir = conv_root / "Validation" / "tests"
    report_path = conv_root / "Validation" / "results" / "phase_b" / "pytest_2.json"

    with pytest.raises(SystemExit) as exc:
        validate.cmd_run_tests(SimpleNamespace(
            conv_root=str(conv_root),
            phase="b",
            iter=2,
            verify_all=False,
        ))
    # pytest exits 1 because ep_failing fails
    assert exc.value.code == 1

    assert report_path.is_file(), "pytest JSON report should be written"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    outcomes = {
        Path(t["nodeid"].split("::")[0]).stem.removeprefix("test_"): t["outcome"]
        for t in report.get("tests", [])
    }
    assert outcomes["ep_baseline"] == "passed"
    assert outcomes["ep_skipped"] == "passed"
    assert outcomes["ep_failing"] == "failed"
    assert "ep_done" not in outcomes, "terminal trial should be deselected"

    state = _load_state(conv_root)

    assert state["trials"]["ep_baseline"]["status"] == "passed"
    assert state["trials"]["ep_baseline"]["final_iter"] == 2
    assert state["trials"]["ep_baseline"]["phase_b_iters"] == [
        {"iter": 2, "passing": 1, "failing": 0},
    ]

    assert state["trials"]["ep_skipped"]["status"] == "passed_no_baseline"
    assert state["trials"]["ep_skipped"]["final_iter"] == 2

    assert state["trials"]["ep_failing"]["status"] == "pending"
    assert state["trials"]["ep_failing"]["phase_b_iters"] == [
        {"iter": 2, "passing": 0, "failing": 1},
    ]

    assert state["trials"]["ep_done"]["status"] == "passed"

    auto_marks = [
        e for e in _load_events(conv_root)
        if e.get("kind") == "trial_marked" and e.get("auto")
    ]
    assert {e["trial_id"] for e in auto_marks} == {"ep_baseline", "ep_skipped"}
    assert {e["status"] for e in auto_marks} == {"passed", "passed_no_baseline"}

    # Phase cannot advance while ep_failing is still pending
    assert state.get("phase") != "phase_b_done"


@pytest.mark.slow
def test_run_tests_summary_recovery_after_raw_pytest_gap(tmp_path):
    """summary recovery still works when a trial passed but was never auto-promoted."""
    conv_root = _build_workspace(tmp_path)

    # Simulate raw pytest: iter recorded manually, no auto-promote
    validate._record_iter_impl(
        conv_root, "ep_baseline", "B", 3, 1, 0,
    )
    st = _load_state(conv_root)
    assert st["trials"]["ep_baseline"]["status"] == "pending"

    recovered = validate._recover_pending_trials(st)
    assert recovered == 1
    assert st["trials"]["ep_baseline"]["status"] == "passed"
    assert st["trials"]["ep_baseline"]["final_iter"] == 3
