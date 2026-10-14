"""Regression tests for validate.py hard_stuck gates and summary recovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import validate  # noqa: E402


def _trial(status="pending", **kw):
    return {"status": status, "phase_a_iters": [], "phase_b_iters": [], **kw}


def test_advance_phase_sets_phase_a_complete_milestone():
    st = {"phase": "init", "trials": {
        "a": {"status": "passed", "phase_a_iters": [{"iter": 1}]},
    }}
    validate._advance_phase(st)
    assert st["phase"] == "phase_a_done"
    assert st["milestones"]["phase_a_complete"] is True
    assert not st["milestones"].get("phase_b_complete")


def test_advance_phase_sets_both_milestones_on_phase_b():
    st = {"phase": "phase_a_done", "milestones": {"phase_a_complete": True}, "trials": {
        "a": {"status": "passed", "phase_a_iters": [{"iter": 1}], "phase_b_iters": [{"iter": 1}]},
    }}
    validate._advance_phase(st)
    assert st["phase"] == "phase_b_done"
    assert st["milestones"]["phase_a_complete"] is True
    assert st["milestones"]["phase_b_complete"] is True


def test_advance_phase_noop_when_not_all_terminal():
    st = {"phase": "init", "trials": {"a": {"status": "pending", "phase_a_iters": []}}}
    validate._advance_phase(st)
    assert st["phase"] == "init"
    assert not st.get("milestones")


def test_advance_phase_emits_milestone_event(tmp_path):
    (tmp_path / "Validation").mkdir(parents=True)
    st = {"phase": "init", "trials": {"a": {"status": "passed", "phase_a_iters": [{"iter": 1}]}}}
    validate._advance_phase(st, tmp_path)
    events_path = tmp_path / "Validation" / "events.jsonl"
    assert events_path.is_file()
    kinds = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
    assert any(e.get("kind") == "milestone_completed" and e.get("milestone") == "phase_a_complete"
               for e in kinds)


def _state(**trials):
    return {"schema_version": validate.SCHEMA_VERSION, "phase": "init", "trials": trials,
            "fixer_dispatches": []}


def _save(tmp_path: Path, state: dict) -> None:
    val = tmp_path / "Validation"
    val.mkdir(parents=True, exist_ok=True)
    (val / "state.json").write_text(json.dumps(state) + "\n")


def _record_status(tmp_path: Path, **kwargs):
    args = SimpleNamespace(
        conv_root=str(tmp_path),
        trial_id=kwargs.get("trial_id", "a"),
        status=kwargs.get("status", "hard_stuck"),
        final_iter=kwargs.get("final_iter"),
        reason=kwargs.get("reason", "stuck"),
        analysis_repair_exhausted=kwargs.get("analysis_repair_exhausted", False),
        harness_repair_exhausted=kwargs.get("harness_repair_exhausted", False),
        patch_repair_exhausted=kwargs.get("patch_repair_exhausted", False),
        phase=None,
    )
    try:
        validate.cmd_record_trial_status(args)
    except SystemExit as exc:
        return exc.code
    return 0


# --- summary auto-recovery -------------------------------------------------


def test_recover_pending_promotes_passes_only():
    st = {"trials": {
        "ok": _trial(
            "pending",
            phase_a_iters=[{"iter": 1, "passing": 1, "failing": 0}],
            phase_b_iters=[{"iter": 1, "passing": 2, "failing": 0}],
        ),
        "bad": _trial("pending", phase_b_iters=[{"iter": 1, "passing": 0, "failing": 1}]),
    }}
    n = validate._recover_pending_trials(st)
    assert n == 1
    assert st["trials"]["ok"]["status"] == "passed"
    assert st["trials"]["bad"]["status"] == "pending"


def test_recover_pending_no_baseline_when_no_phase_a():
    st = {"trials": {
        "ep": _trial("pending", phase_b_iters=[{"iter": 1, "passing": 1, "failing": 0}]),
    }}
    validate._recover_pending_trials(st)
    assert st["trials"]["ep"]["status"] == "passed_no_baseline"


def test_recover_pending_no_baseline_when_phase_a_failed():
    """Phase A iters exist but none passed — still passed_no_baseline, not passed."""
    st = {"trials": {
        "ep": _trial(
            "pending",
            phase_a_iters=[{"iter": 1, "passing": 0, "failing": 1}],
            phase_b_iters=[{"iter": 1, "passing": 1, "failing": 0}],
        ),
    }}
    validate._recover_pending_trials(st)
    assert st["trials"]["ep"]["status"] == "passed_no_baseline"


def test_recover_pending_promotes_phase_a_skipped():
    st = {"trials": {
        "ep": _trial(
            "phase_a_skipped",
            phase_b_iters=[{"iter": 2, "passing": 1, "failing": 0}],
        ),
    }}
    n = validate._recover_pending_trials(st)
    assert n == 1
    assert st["trials"]["ep"]["status"] == "passed_no_baseline"


# --- record-iter fix_category tagging --------------------------------------


def test_record_iter_tags_existing_iter(tmp_path):
    _save(tmp_path, _state(a=_trial(phase_b_iters=[{"iter": 2, "passing": 0, "failing": 1}])))
    validate._record_iter_impl(
        tmp_path, "a", "B", 2, 0, 1, fix_category="analysis_repair",
    )
    st = validate._load_state(tmp_path)
    assert st["trials"]["a"]["phase_b_iters"][0]["fix_category"] == "analysis_repair"


def test_record_iter_tag_idempotent(tmp_path, capsys):
    _save(tmp_path, _state(a=_trial(
        phase_b_iters=[{"iter": 1, "passing": 0, "failing": 1, "fix_category": "patch_failure"}],
    )))
    validate._record_iter_impl(tmp_path, "a", "B", 1, 0, 1, fix_category="patch_failure")
    out = capsys.readouterr().out
    assert "no-op" in out
    st = validate._load_state(tmp_path)
    assert len(st["trials"]["a"]["phase_b_iters"]) == 1


def test_record_iter_persists_fix_category_on_new_iter(tmp_path):
    """A freshly recorded iter round-trips fix_category into state.json."""
    _save(tmp_path, _state(a=_trial()))
    validate._record_iter_impl(tmp_path, "a", "B", 1, 3, 0, fix_category="schema_gap")
    st = validate._load_state(tmp_path)
    rec = st["trials"]["a"]["phase_b_iters"][0]
    assert rec == {"iter": 1, "passing": 3, "failing": 0, "fix_category": "schema_gap"}


# --- document-divergence current-iter auto-fill ----------------------------


def _write_manifest(tmp_path: Path, manifest: dict | None = None) -> None:
    sd = tmp_path / "Validation" / "shared" / "schemas"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "manifest.json").write_text(json.dumps(manifest or {}) + "\n")


def _div_args(tmp_path: Path, **kw):
    return SimpleNamespace(
        conv_root=str(tmp_path),
        trial_id=kw.get("trial_id", "a"),
        sink_id=kw.get("sink_id", "write_001"),
        column=kw.get("column", "col_x"),
        reason=kw.get("reason", "cosmetic"),
        baseline_sample=kw.get("baseline_sample", ""),
        shadow_sample=kw.get("shadow_sample", ""),
        iter=kw.get("iter", None),
    )


def test_document_divergence_auto_fills_current_iter(tmp_path):
    """--iter omitted -> latest recorded iter (Phase B preferred over Phase A)."""
    _save(tmp_path, _state(a=_trial(
        phase_a_iters=[{"iter": 2}, {"iter": 5}],
        phase_b_iters=[{"iter": 1}, {"iter": 3}],
    )))
    _write_manifest(tmp_path)
    validate.cmd_document_divergence(_div_args(tmp_path))
    st = validate._load_state(tmp_path)
    div = st["trials"]["a"]["documented_divergences"][0]
    assert div["documented_at_iter"] == 3


def test_document_divergence_explicit_iter_wins(tmp_path):
    _save(tmp_path, _state(a=_trial(phase_b_iters=[{"iter": 3}])))
    _write_manifest(tmp_path)
    validate.cmd_document_divergence(_div_args(tmp_path, iter=7))
    st = validate._load_state(tmp_path)
    assert st["trials"]["a"]["documented_divergences"][0]["documented_at_iter"] == 7


def test_document_divergence_defaults_zero_without_iters(tmp_path):
    _save(tmp_path, _state(a=_trial()))
    _write_manifest(tmp_path)
    validate.cmd_document_divergence(_div_args(tmp_path))
    st = validate._load_state(tmp_path)
    assert st["trials"]["a"]["documented_divergences"][0]["documented_at_iter"] == 0



# --- hard_stuck gate -------------------------------------------------------


def test_hard_stuck_without_any_exhaustion_rejected(tmp_path):
    _save(tmp_path, _state(a=_trial()))
    assert _record_status(tmp_path) == 2


def test_hard_stuck_analysis_repair_requires_recorded_attempt(tmp_path):
    _save(tmp_path, _state(a=_trial()))
    assert _record_status(tmp_path, analysis_repair_exhausted=True, reason="gap") == 2


def test_hard_stuck_analysis_repair_single_attempt_allowed(tmp_path):
    _save(tmp_path, _state(a=_trial(
        phase_b_iters=[{"iter": 1, "fix_category": "analysis_repair"}],
    )))
    assert _record_status(tmp_path, analysis_repair_exhausted=True, reason="gap") == 0
    st = validate._load_state(tmp_path)
    assert st["trials"]["a"]["status"] == "hard_stuck"


def test_hard_stuck_harness_repair_single_attempt_allowed(tmp_path):
    _save(tmp_path, _state(a=_trial(
        phase_b_iters=[{"iter": 1, "fix_category": "harness_failure"}],
    )))
    assert _record_status(tmp_path, harness_repair_exhausted=True, reason="kit bug") == 0


def test_hard_stuck_patch_repair_single_attempt_allowed(tmp_path):
    _save(tmp_path, _state(a=_trial(
        phase_b_iters=[{"iter": 1, "fix_category": "patch_failure"}],
    )))
    assert _record_status(tmp_path, patch_repair_exhausted=True, reason="no patch") == 0


def test_hard_stuck_with_fixer_dispatch_allowed(tmp_path):
    st = _state(a=_trial())
    st["fixer_dispatches"] = [{"trials_affected": ["a"]}]
    _save(tmp_path, st)
    assert _record_status(tmp_path, reason="no progress") == 0


def test_hard_stuck_single_no_change_dispatch_rejected(tmp_path):
    # A lone fixer dispatch that changed nothing must NOT unlock hard_stuck — this
    # was the exact hole behind premature hard_stuck / false "infra blocker"
    # (a repeated SCOS error is almost always still patchable).
    st = _state(a=_trial())
    st["fixer_dispatches"] = [{"trials_affected": ["a"], "outcome": "no_change"}]
    _save(tmp_path, st)
    assert _record_status(tmp_path, reason="no progress") == 2


def test_hard_stuck_real_attempt_dispatch_allowed(tmp_path):
    # A dispatch that actually attempted a change (partial/success) is credible.
    st = _state(a=_trial())
    st["fixer_dispatches"] = [{"trials_affected": ["a"], "outcome": "partial"}]
    _save(tmp_path, st)
    assert _record_status(tmp_path, reason="tried, still stuck") == 0


def test_hard_stuck_two_no_change_dispatches_allowed(tmp_path):
    # Repeated dispatches show persistence even if each changed nothing.
    st = _state(a=_trial())
    st["fixer_dispatches"] = [
        {"trials_affected": ["a"], "outcome": "no_change"},
        {"trials_affected": ["a"], "outcome": "no_change"},
    ]
    _save(tmp_path, st)
    assert _record_status(tmp_path, reason="two tries, stuck") == 0


# --- last-resort --reason requirement + surfacing -------------------------


def test_phase_a_skipped_requires_reason(tmp_path):
    _save(tmp_path, _state(a=_trial("pending")))
    assert _record_status(tmp_path, trial_id="a", status="phase_a_skipped", reason=None) == 2
    # blank/whitespace is also rejected
    assert _record_status(tmp_path, trial_id="a", status="phase_a_skipped", reason="  ") == 2


def test_hard_stuck_requires_reason_even_with_dispatch(tmp_path):
    st = _state(a=_trial())
    st["fixer_dispatches"] = [{"trials_affected": ["a"]}]
    _save(tmp_path, st)
    # gate is satisfied (dispatch), but a blank reason is still rejected
    assert _record_status(tmp_path, trial_id="a", status="hard_stuck", reason=None) == 2


def test_phase_a_skipped_stores_dedicated_reason(tmp_path):
    _save(tmp_path, _state(a=_trial("pending")))
    assert _record_status(
        tmp_path, trial_id="a", status="phase_a_skipped",
        reason="QUALIFY clause unsupported in local PySpark",
    ) == 0
    st = validate._load_state(tmp_path)
    # stored in the dedicated field, NOT hard_stuck_reason
    assert st["trials"]["a"]["phase_a_skip_reason"] == "QUALIFY clause unsupported in local PySpark"
    assert "hard_stuck_reason" not in st["trials"]["a"]


def test_phase_a_skip_reason_preserved_on_promotion(tmp_path):
    # a phase_a_skipped trial with a skip reason, promoted to pnb, keeps the reason
    st = {"trials": {
        "ep": _trial(
            "phase_a_skipped",
            phase_a_skip_reason="MERGE INTO unsupported in local PySpark",
            phase_b_iters=[{"iter": 1, "passing": 1, "failing": 0}],
        ),
    }}
    validate._recover_pending_trials(st)
    assert st["trials"]["ep"]["status"] == "passed_no_baseline"
    assert st["trials"]["ep"]["phase_a_skip_reason"] == "MERGE INTO unsupported in local PySpark"


# --- baseline_produced label (requires a PASSING Phase A iter) ------------


def test_phase_a_baseline_produced_requires_passing_iter():
    passed = {"phase_a_iters": [{"iter": 1, "passing": 1, "failing": 0}]}
    failed = {"phase_a_iters": [{"iter": 1, "passing": 0, "failing": 1}]}
    empty = {"phase_a_iters": []}
    assert validate._phase_a_baseline_produced(passed) is True
    # a Phase A that only ever FAILED is NOT a produced baseline (was mislabeled before)
    assert validate._phase_a_baseline_produced(failed) is False
    assert validate._phase_a_baseline_produced(empty) is False


# --- passed_no_baseline is derived, never set directly --------------------


def test_record_status_rejects_direct_passed_no_baseline(tmp_path):
    # The model must mark phase_a_skipped; Phase B auto-promotes to pnb. Marking
    # pnb directly is rejected so a no-baseline verdict always carries a reason.
    _save(tmp_path, _state(a=_trial(
        phase_a_iters=[{"iter": 1, "passing": 1, "failing": 0}],
    )))
    assert _record_status(tmp_path, status="passed_no_baseline", reason="x") == 2


def test_infer_pass_status_skip_reason_beats_empty_baseline():
    # Phase A recorded a passing (but empty/unusable) capture AND the trial was
    # explicitly skipped -> must promote to pnb, not passed.
    t = {
        "status": "phase_a_skipped",
        "phase_a_skip_reason": "connector read returned 0 rows locally",
        "phase_a_iters": [{"iter": 1, "passing": 1, "failing": 0}],
        "phase_b_iters": [{"iter": 1, "passing": 1, "failing": 0}],
    }
    assert validate._infer_pass_status(t) == "passed_no_baseline"


def test_skip_reason_surfaced_in_verdict_after_promotion():
    # After phase_a_skipped -> pnb promotion, the report verdict.reason must carry
    # the model-provided skip reason (this is what was silently dropped before).
    trial = {
        "status": "phase_a_skipped",
        "phase_a_skip_reason": "JDBC src1 returned 0 rows in local PySpark",
        "phase_b_iters": [{"iter": 1, "passing": 1, "failing": 0}],
    }
    assert validate._recover_pending_trials({"trials": {"ep": trial}}) == 1
    assert trial["status"] == "passed_no_baseline"
    # exercise the real report verdict-reason builder (used by cmd_build_index)
    assert validate._verdict_reason(trial) == "JDBC src1 returned 0 rows in local PySpark"


def test_verdict_reason_variants():
    # hard_stuck and passed each surface their own reason; pending is blank
    assert validate._verdict_reason(
        {"status": "hard_stuck", "hard_stuck_reason": "no workaround"}
    ) == "no workaround"
    assert validate._verdict_reason({"status": "passed"}) == "matched baseline"
    assert validate._verdict_reason({"status": "pending"}) == ""


def test_trial_lacks_baseline_matches_infer_pass_status():
    # A promoted passed_no_baseline trial that recorded a passing Phase A iter must
    # still report as having NO baseline (Phase A verdict + has_baseline), consistent
    # with _infer_pass_status — not mislabeled baseline_produced.
    promoted = {
        "status": "passed_no_baseline",
        "phase_a_skip_reason": "connector read returned 0 rows locally",
        "phase_a_iters": [{"iter": 1, "passing": 1, "failing": 0}],
    }
    assert validate._phase_a_baseline_produced(promoted) is True
    assert validate._trial_lacks_baseline(promoted) is True
    assert validate._trial_lacks_baseline({"status": "phase_a_skipped"}) is True
    # a genuine passed trial with a real baseline is NOT lacking one
    real = {"status": "passed", "phase_a_iters": [{"iter": 1, "passing": 1, "failing": 0}]}
    assert validate._trial_lacks_baseline(real) is False


# --- B2 record-milestone gates -----------------------------------------------


def test_record_milestone_phase_a_complete_requires_phase_a_iters_or_skipped(tmp_path):
    """phase_a_complete milestone blocked when trial lacks phase_a_iters and is not skipped."""
    _save(tmp_path, _state(
        a=_trial("pending"),  # no phase_a_iters, not skipped
    ))
    args = SimpleNamespace(
        conv_root=str(tmp_path),
        milestone="phase_a_complete",
    )
    try:
        validate.cmd_record_milestone(args)
        assert False, "should have died"
    except SystemExit as exc:
        assert exc.code == 1


def test_record_milestone_phase_a_complete_allows_with_phase_a_iters(tmp_path):
    """phase_a_complete milestone succeeds when all trials have phase_a_iters."""
    _save(tmp_path, _state(
        a=_trial("passed", phase_a_iters=[{"iter": 1}]),
        b=_trial("passed", phase_a_iters=[{"iter": 1}]),
    ))
    args = SimpleNamespace(
        conv_root=str(tmp_path),
        milestone="phase_a_complete",
    )
    validate.cmd_record_milestone(args)
    st = validate._load_state(tmp_path)
    assert st["milestones"]["phase_a_complete"] is True


def test_record_milestone_phase_a_complete_allows_with_phase_a_skipped(tmp_path):
    """phase_a_complete milestone succeeds when trial is phase_a_skipped."""
    _save(tmp_path, _state(
        a=_trial("phase_a_skipped"),
    ))
    args = SimpleNamespace(
        conv_root=str(tmp_path),
        milestone="phase_a_complete",
    )
    validate.cmd_record_milestone(args)
    st = validate._load_state(tmp_path)
    assert st["milestones"]["phase_a_complete"] is True


def test_record_milestone_phase_b_complete_requires_terminal(tmp_path):
    """phase_b_complete milestone blocked when trial is non-terminal."""
    _save(tmp_path, _state(
        a=_trial("pending"),
    ))
    args = SimpleNamespace(
        conv_root=str(tmp_path),
        milestone="phase_b_complete",
    )
    try:
        validate.cmd_record_milestone(args)
        assert False, "should have died"
    except SystemExit as exc:
        assert exc.code == 1


def test_record_milestone_phase_b_complete_allows_all_terminal(tmp_path):
    """phase_b_complete milestone succeeds when all trials are terminal."""
    _save(tmp_path, _state(
        a=_trial("passed", phase_b_iters=[{"iter": 1}]),
        b=_trial("hard_stuck", phase_b_iters=[{"iter": 1}]),
        c=_trial("passed_no_baseline", phase_b_iters=[{"iter": 1}]),
    ))
    args = SimpleNamespace(
        conv_root=str(tmp_path),
        milestone="phase_b_complete",
    )
    validate.cmd_record_milestone(args)
    st = validate._load_state(tmp_path)
    assert st["milestones"]["phase_b_complete"] is True


def test_record_milestone_other_milestones_unchanged(tmp_path):
    """Non-phase milestones are recorded without gating."""
    _save(tmp_path, _state(
        a=_trial("pending"),  # no gates for non-phase milestones
    ))
    for milestone in ["entrypoints_selected", "synth_deep", "patches_authored"]:
        args = SimpleNamespace(
            conv_root=str(tmp_path),
            milestone=milestone,
        )
        validate.cmd_record_milestone(args)
    st = validate._load_state(tmp_path)
    assert st["milestones"]["entrypoints_selected"] is True
    assert st["milestones"]["synth_deep"] is True
    assert st["milestones"]["patches_authored"] is True


# --- B14 status --json -------------------------------------------------------


def test_status_json_output_shape(tmp_path):
    """status --json outputs JSON with per-trial status, phase_a_pass, phase_b_pass."""
    _save(tmp_path, _state(
        a=_trial("passed", phase_a_iters=[{"iter": 1, "passing": 1, "failing": 0}],
                           phase_b_iters=[{"iter": 1, "passing": 1, "failing": 0}]),
        b=_trial("pending"),
        c=_trial("hard_stuck", phase_b_iters=[{"iter": 1, "passing": 0, "failing": 1}]),
    ))
    args = SimpleNamespace(
        conv_root=str(tmp_path),
        verbose=False,
        phase="all",
        json=True,
    )
    import io
    import contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            validate.cmd_status(args)
        except SystemExit:
            pass  # cmd_status calls sys.exit, that's expected
    output = out.getvalue()
    data = json.loads(output)
    assert "a" in data and "b" in data and "c" in data
    # Trial a passed with passing iters in both phases
    assert data["a"]["status"] == "passed"
    assert data["a"]["phase_a_pass"] is True
    assert data["a"]["phase_b_pass"] is True
    # Trial b pending with no iters
    assert data["b"]["status"] == "pending"
    assert data["b"]["phase_a_pass"] is False
    assert data["b"]["phase_b_pass"] is False
    # Trial c hard_stuck with failing Phase B iter
    assert data["c"]["status"] == "hard_stuck"
    assert data["c"]["phase_a_pass"] is False
    assert data["c"]["phase_b_pass"] is False


def test_status_json_phase_pass_logic(tmp_path):
    """phase_a_pass and phase_b_pass are True only if passing > 0 in at least one iter."""
    _save(tmp_path, _state(
        only_a_pass=_trial("passed",
            phase_a_iters=[{"iter": 1, "passing": 1, "failing": 0}]),
        only_a_fail=_trial("passed",
            phase_a_iters=[{"iter": 1, "passing": 0, "failing": 1}]),
        multiple_iters=_trial("passed",
            phase_a_iters=[
                {"iter": 1, "passing": 0, "failing": 1},
                {"iter": 2, "passing": 1, "failing": 0},
            ]),
    ))
    args = SimpleNamespace(
        conv_root=str(tmp_path),
        verbose=False,
        phase="all",
        json=True,
    )
    import io
    import contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            validate.cmd_status(args)
        except SystemExit:
            pass
    output = out.getvalue()
    data = json.loads(output)
    assert data["only_a_pass"]["phase_a_pass"] is True
    assert data["only_a_fail"]["phase_a_pass"] is False
    assert data["multiple_iters"]["phase_a_pass"] is True  # second iter passed


def test_status_json_phase_pass_mixed_results():
    """phase_a_pass is False when an iter has both passing and failing (mixed case)."""
    state = _state(
        mixed=_trial("passed",
            phase_a_iters=[{"iter": 1, "passing": 3, "failing": 7}]),
    )
    assert state["trials"]["mixed"]["phase_a_iters"][0]["passing"] == 3
    assert state["trials"]["mixed"]["phase_a_iters"][0]["failing"] == 7
    tmp_path = Path("/tmp/test_phase_pass_mixed")
    tmp_path.mkdir(exist_ok=True)
    _save(tmp_path, state)

    args = SimpleNamespace(
        conv_root=str(tmp_path),
        verbose=False,
        phase="all",
        json=True,
    )
    import io
    import contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            validate.cmd_status(args)
        except SystemExit:
            pass
    output = out.getvalue()
    data = json.loads(output)
    assert data["mixed"]["phase_a_pass"] is False


def test_emit_collapse_hints_fires_for_new_signatures():
    """_emit_collapse_hints fires when 2+ entries share same signature not in blueprint."""
    import io
    import contextlib
    entries = [
        {"id": "p1", "relative_file": "a.py", "search": "x = 1", "replace": "x = 10"},
        {"id": "p2", "relative_file": "b.py", "search": "x = 1", "replace": "x = 10"},
    ]
    existing_sigs = set()

    stderr_capture = io.StringIO()
    with contextlib.redirect_stderr(stderr_capture):
        validate._emit_collapse_hints(entries, existing_sigs)
    stderr_output = stderr_capture.getvalue()
    assert "prefer ONE glob entry" in stderr_output


def test_emit_collapse_hints_not_fired_for_committed_signatures():
    """_emit_collapse_hints does NOT fire for signatures already in blueprint."""
    import io
    import contextlib
    entries = [
        {"id": "p1", "relative_file": "c.py", "search": "x = 1", "replace": "x = 10"},
    ]
    existing_sig = (False, False, "x = 1", "x = 10")
    existing_sigs = {existing_sig}

    stderr_capture = io.StringIO()
    with contextlib.redirect_stderr(stderr_capture):
        validate._emit_collapse_hints(entries, existing_sigs)
    stderr_output = stderr_capture.getvalue()
    assert "prefer ONE glob entry" not in stderr_output
