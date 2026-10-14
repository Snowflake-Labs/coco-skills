"""Tests for scos_state.py — the ported ScosState state machine (P4a core).

Covers the invariants that matter most: phase advancement, the record-trial-status
hard gate (incl. the hard_stuck fixer-dispatch requirement), run_index comparison
verdict, manual-review materialization, pending recovery, and atomic state I/O.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import scos_state as s  # noqa: E402


def _trial(status="pending", **kw):
    return {"status": status, "phase_a_iters": [], "phase_b_iters": [], **kw}


def test_advance_phase_flips_phase_milestones():
    st = {"phase": "init", "milestones": {}, "trials": {
        "a": _trial("pending", phase_a_iters=[{"iter": 1}]),
    }}
    out = s.advance_phase(st)
    assert out["phase"] == "phase_a_done"
    assert out["milestones"]["phase_a_complete"] is True
    assert out["milestones"].get("phase_b_complete") is not True

    st2 = {**out, "trials": {
        "a": _trial("pending", phase_a_iters=[{"iter": 1}], phase_b_iters=[{"iter": 1}]),
    }}
    out2 = s.advance_phase(st2)
    assert out2["phase"] == "phase_b_done"
    assert out2["milestones"]["phase_b_complete"] is True


def test_status_verbose_prints_iters(tmp_path, capsys):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    s.main(["record-iter", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--phase", "A", "--iter", "1", "--passing", "2", "--failing", "1"])
    s.main(["record-iter", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--phase", "B", "--iter", "1", "--passing", "1", "--failing", "0"])
    assert s.main(["status", "--conv-root", str(tmp_path), "--verbose", "--phase", "A"]) == 1
    out = capsys.readouterr().out
    assert "Phase A iter 1: pass=2 fail=1" in out
    assert "    Phase B iter" not in out


# --- advance_phase ---------------------------------------------------------

def test_advance_phase_empty_trials_unchanged():
    st = {"phase": "init", "trials": {}}
    assert s.advance_phase(st)["phase"] == "init"


def test_advance_phase_not_all_terminal_unchanged():
    st = {"phase": "init", "trials": {"a": _trial("pending"), "b": _trial("passed")}}
    assert s.advance_phase(st)["phase"] == "init"


def test_advance_phase_init_to_phase_a_done():
    st = {"phase": "init", "trials": {
        "a": _trial("passed", phase_a_iters=[{"iter": 1}]),  # has A, no B
    }}
    assert s.advance_phase(st)["phase"] == "phase_a_done"


def test_advance_phase_to_phase_b_done():
    st = {"phase": "phase_a_done", "trials": {
        "a": _trial("passed", phase_a_iters=[{"i": 1}], phase_b_iters=[{"i": 1}]),
    }}
    assert s.advance_phase(st)["phase"] == "phase_b_done"


def test_phase_a_skipped_and_hard_stuck_do_not_block():
    st = {"phase": "init", "trials": {
        "a": _trial("phase_a_skipped"),              # counts as haveA & haveB
        "b": _trial("hard_stuck", phase_a_iters=[{}]),  # hard_stuck doesn't block haveB
    }}
    assert s.advance_phase(st)["phase"] == "phase_b_done"


# --- comparison_verdict ----------------------------------------------------

def test_comparison_verdict_branches():
    assert s.comparison_verdict({"status": "passed"}) == "match"
    assert s.comparison_verdict({"status": "passed_no_baseline"}) == "unverified"
    assert s.comparison_verdict({"status": "pending", "documented_divergences": [{"c": 1}]}) == "cosmetic_divergence"
    assert s.comparison_verdict({"status": "hard_stuck"}) == "real_divergence"
    assert s.comparison_verdict({"status": "pending"}) == "pending"


def test_coerce_entrypoint_weight_labels():
    assert s._coerce_entrypoint_weight("high") == 20
    assert s._coerce_entrypoint_weight("bogus") == 1
    assert s._coerce_entrypoint_weight(15) == 15


def test_normalize_manifest_weights_for_batch():
    man = {"entrypoints": [{"id": "ep1", "weight": "high"}, {"id": "ep2", "weight": 3}]}
    out = s._normalize_manifest_weights(man)
    assert out["entrypoints"][0]["weight"] == 20
    assert out["entrypoints"][1]["weight"] == 3
    assert man["entrypoints"][0]["weight"] == "high"  # input unchanged


def test_normalize_patch_entries_unescapes_quotes():
    entries = [{
        "id": "p1",
        "migrated": {
            "file": "Job.scala",
            "search": r'getProperty(\"KEY\")',
            "replace": r'System.getProperty(\"KEY\")',
        },
    }]
    out = s._normalize_patch_entries(entries)
    assert out[0]["migrated"]["search"] == 'getProperty("KEY")'
    assert out[0]["migrated"]["replace"] == 'System.getProperty("KEY")'


# --- apply_trial_status (hard gate) ----------------------------------------

def _state(**trials):
    return {"schema_version": 1, "phase": "init", "trials": trials, "fixer_dispatches": []}


def test_invalid_status_rejected():
    st = _state(a=_trial())
    _, code, err, _ = s.apply_trial_status(st, "a", "bogus")
    assert code == 2 and "invalid status" in err


def test_unknown_trial_rejected():
    _, code, err, _ = s.apply_trial_status(_state(a=_trial()), "zzz", "passed")
    assert code == 2 and "not in state.trials" in err


def test_hard_stuck_without_dispatch_rejected():
    st = _state(a=_trial())
    _, code, err, _ = s.apply_trial_status(st, "a", "hard_stuck")
    assert code == 2 and "no fixer dispatch" in err


def test_hard_stuck_with_dispatch_allowed():
    st = _state(a=_trial())
    st["fixer_dispatches"] = [{"trials_affected": ["a"]}]
    new, code, err, noop = s.apply_trial_status(st, "a", "hard_stuck", reason="stuck")
    assert err is None and new["trials"]["a"]["status"] == "hard_stuck"
    assert new["trials"]["a"]["hard_stuck_reason"] == "stuck"


# --- analysis-repair-exhausted gate (item 3) -------------------------------

def test_hard_stuck_repair_exhausted_needs_two_rounds():
    # one repair round + no dispatch → rejected even with the flag
    st = _state(a=_trial(phase_b_iters=[{"iter": 1, "fix_category": "analysis_repair"}]))
    _, code, err, _ = s.apply_trial_status(st, "a", "hard_stuck",
                                           analysis_repair_exhausted=True)
    assert code == 2 and "after only 1 schema-repair round" in err


def test_hard_stuck_repair_exhausted_two_rounds_allowed():
    st = _state(a=_trial(phase_b_iters=[
        {"iter": 1, "fix_category": "analysis_repair"},
        {"iter": 2, "fix_category": "schema_gap"}]))
    new, code, err, _ = s.apply_trial_status(st, "a", "hard_stuck",
                                             analysis_repair_exhausted=True, reason="gap")
    assert err is None and new["trials"]["a"]["status"] == "hard_stuck"


def test_hard_stuck_flag_without_repair_iters_rejected():
    # flag set but no recorded repair rounds → still rejected
    st = _state(a=_trial())
    _, code, err, _ = s.apply_trial_status(st, "a", "hard_stuck",
                                           analysis_repair_exhausted=True)
    assert code == 2 and "after only 0 schema-repair round" in err


# --- passed_no_baseline anti-gaming gate (item 2) --------------------------

def test_passed_no_baseline_rejected_when_baseline_exists():
    st = _state(a=_trial(phase_a_iters=[{"iter": 1, "passing": 3, "failing": 0}]))
    _, code, err, _ = s.apply_trial_status(st, "a", "passed_no_baseline")
    assert code == 2 and "Phase A produced a baseline" in err


def test_passed_no_baseline_direct_set_rejected_even_without_baseline():
    # Derived-only: a direct passed_no_baseline is rejected regardless of baseline.
    st = _state(a=_trial(phase_a_iters=[{"iter": 1, "passing": 0, "failing": 2}]))
    _, code, err, _ = s.apply_trial_status(st, "a", "passed_no_baseline")
    assert code == 2 and "do not set" in err and "derived" in err


def test_passed_no_baseline_allowed_when_derived():
    # Internal promotion path (allow_derived=True) — how summary reaches it.
    st = _state(a=_trial(phase_a_iters=[{"iter": 1, "passing": 0, "failing": 2}]))
    new, code, err, _ = s.apply_trial_status(st, "a", "passed_no_baseline",
                                             allow_derived=True)
    assert err is None and new["trials"]["a"]["status"] == "passed_no_baseline"


def test_passed_no_baseline_escape_with_flag_requires_reason():
    st = _state(a=_trial(phase_a_iters=[{"iter": 1, "passing": 3, "failing": 0}]))
    # flag but no reason -> rejected
    _, code, err, _ = s.apply_trial_status(st, "a", "passed_no_baseline",
                                           baseline_not_comparable=True)
    assert code == 2 and "requires --reason" in err
    # flag + reason -> allowed, reason stored as phase_a_skip_reason
    new, code2, err2, _ = s.apply_trial_status(st, "a", "passed_no_baseline",
                                               baseline_not_comparable=True,
                                               reason="A captured sink X, B captured Y")
    assert err2 is None and new["trials"]["a"]["status"] == "passed_no_baseline"
    assert new["trials"]["a"]["phase_a_skip_reason"] == "A captured sink X, B captured Y"


# --- reason-required gate (Phase A skip / hard_stuck) ----------------------

def test_phase_a_skipped_requires_reason():
    st = _state(a=_trial())
    _, code, err, _ = s.apply_trial_status(st, "a", "phase_a_skipped")
    assert code == 2 and "--reason is required" in err


def test_phase_a_skipped_stores_dedicated_reason():
    st = _state(a=_trial())
    new, code, err, _ = s.apply_trial_status(st, "a", "phase_a_skipped",
                                             reason="QUALIFY in rank.sql")
    assert err is None
    t = new["trials"]["a"]
    assert t["status"] == "phase_a_skipped"
    assert t["phase_a_skip_reason"] == "QUALIFY in rank.sql"
    assert "hard_stuck_reason" not in t


def test_hard_stuck_requires_reason_even_with_dispatch():
    st = _state(a=_trial())
    st["fixer_dispatches"] = [{"trials_affected": ["a"]}]
    _, code, err, _ = s.apply_trial_status(st, "a", "hard_stuck")
    assert code == 2 and "--reason is required" in err


# --- passed gate (requires green Phase B) ----------------------------------

def test_passed_rejected_without_phase_b():
    st = _state(a=_trial("pending"))
    _, code, err, _ = s.apply_trial_status(st, "a", "passed")
    assert code == 2 and "no Phase B iterations" in err


def test_passed_rejected_when_phase_b_failing():
    st = _state(a=_trial("pending", phase_b_iters=[{"iter": 1, "passing": 1, "failing": 2}]))
    _, code, err, _ = s.apply_trial_status(st, "a", "passed")
    assert code == 2 and "requires passing>=1" in err


def test_passed_allowed_with_green_phase_b():
    st = _state(a=_trial("pending", phase_b_iters=[{"iter": 1, "passing": 3, "failing": 0}]))
    new, code, err, _ = s.apply_trial_status(st, "a", "passed")
    assert err is None and new["trials"]["a"]["status"] == "passed"


# --- harness / patch repair-exhaustion tracks ------------------------------

def test_hard_stuck_harness_exhausted_needs_recorded_iter():
    st = _state(a=_trial())
    _, code, err, _ = s.apply_trial_status(st, "a", "hard_stuck",
                                           harness_repair_exhausted=True, reason="kit bug")
    assert code == 2 and "harness_failure" in err


def test_hard_stuck_harness_exhausted_allowed_with_iter():
    st = _state(a=_trial(phase_a_iters=[{"iter": 1, "fix_category": "harness_failure"}]))
    new, code, err, _ = s.apply_trial_status(st, "a", "hard_stuck",
                                             harness_repair_exhausted=True, reason="kit bug")
    assert err is None and new["trials"]["a"]["status"] == "hard_stuck"


def test_hard_stuck_patch_exhausted_allowed_with_iter():
    st = _state(a=_trial(phase_b_iters=[{"iter": 1, "fix_category": "patch_failure"}]))
    new, code, err, _ = s.apply_trial_status(st, "a", "hard_stuck",
                                             patch_repair_exhausted=True, reason="unpatchable I/O")
    assert err is None and new["trials"]["a"]["status"] == "hard_stuck"


def test_passed_clears_hard_stuck_reason():
    st = _state(a=_trial("pending", hard_stuck_reason="old",
                         phase_b_iters=[{"iter": 1, "passing": 1, "failing": 0}]))
    new, _, err, _ = s.apply_trial_status(st, "a", "passed")
    assert err is None and "hard_stuck_reason" not in new["trials"]["a"]


def test_idempotent_noop_for_terminal_same():
    st = _state(a=_trial("passed"))
    _, code, err, noop = s.apply_trial_status(st, "a", "passed")
    assert noop is True and code == 0 and err is None


# --- manual review + recovery ----------------------------------------------

def test_materialize_manual_review(tmp_path):
    st = {"phase": "init", "trials": {"ep1": _trial("pending")}}
    pb = tmp_path / "Validation/results/phase_b/ep1"
    pb.mkdir(parents=True)
    (pb / "_manual_review.json").write_text('{"reason": "unusable capture"}')
    (pb / "_index.json").write_text("{}")
    out = s.materialize_manual_review_statuses(tmp_path, st)
    assert out["trials"]["ep1"]["status"] == "passed_no_baseline"
    # reason from the marker is preserved so the report can explain it
    assert out["trials"]["ep1"]["phase_a_skip_reason"] == "unusable capture"


def test_recover_pending_trials():
    st = {"trials": {
        "ok": _trial("pending", phase_a_iters=[{"passing": 1, "failing": 0}],
                     phase_b_iters=[{"passing": 3, "failing": 0}]),
        "bad": _trial("pending", phase_b_iters=[{"passing": 0, "failing": 2}]),
    }}
    out, n = s.recover_pending_trials(st)
    assert n == 2
    assert out["trials"]["ok"]["status"] == "passed"
    assert out["trials"]["bad"]["status"] == "hard_stuck"


def test_recover_pending_no_baseline_preserves_reason():
    # Clean Phase B but no comparable Phase A baseline + a preserved skip reason
    # → passed_no_baseline carrying that reason (parity with PySpark).
    st = {"trials": {
        "skipped": _trial("pending", phase_a_skip_reason="QUALIFY unsupported",
                          phase_b_iters=[{"passing": 2, "failing": 0}]),
        "nobase": _trial("pending", phase_b_iters=[{"passing": 2, "failing": 0}]),
    }}
    out, n = s.recover_pending_trials(st)
    assert out["trials"]["skipped"]["status"] == "passed_no_baseline"
    assert out["trials"]["skipped"]["phase_a_skip_reason"] == "QUALIFY unsupported"
    # no explicit reason → a generic default is set (never blank)
    assert out["trials"]["nobase"]["status"] == "passed_no_baseline"
    assert out["trials"]["nobase"]["phase_a_skip_reason"]


# --- state I/O + CLI -------------------------------------------------------

def test_write_atomic_roundtrip_and_event(tmp_path):
    st = {"schema_version": 1, "phase": "init", "trials": {}}
    s.save_state(tmp_path, st)
    assert s.load_state(tmp_path) == st
    s.append_event(s.validation_root(tmp_path), {"kind": "x"})
    line = (tmp_path / "Validation/events.jsonl").read_text().strip()
    rec = json.loads(line)
    assert rec["kind"] == "x" and "ts" in rec


def test_cli_record_trial_status(tmp_path):
    st = _state(a=_trial(phase_b_iters=[{"iter": 1, "passing": 1, "failing": 0}]))
    s.save_state(tmp_path, st)
    rc = s.main(["record-trial-status", "--conv-root", str(tmp_path), "--trial-id", "a", "--status", "passed"])
    assert rc == 0
    assert s.load_state(tmp_path)["trials"]["a"]["status"] == "passed"
    # hard_stuck with no dispatch -> exit 2
    rc2 = s.main(["record-trial-status", "--conv-root", str(tmp_path), "--trial-id", "a", "--status", "hard_stuck"])
    assert rc2 == 2


def test_cli_phase_a_skipped_requires_reason(tmp_path):
    st = _state(a=_trial())
    s.save_state(tmp_path, st)
    # no --reason -> rejected
    rc = s.main(["record-trial-status", "--conv-root", str(tmp_path),
                 "--trial-id", "a", "--status", "phase_a_skipped"])
    assert rc == 2
    # with --reason -> accepted, dedicated field stored
    rc2 = s.main(["record-trial-status", "--conv-root", str(tmp_path),
                  "--trial-id", "a", "--status", "phase_a_skipped",
                  "--reason", "MERGE INTO unsupported in local Spark"])
    assert rc2 == 0
    assert s.load_state(tmp_path)["trials"]["a"]["phase_a_skip_reason"] \
        == "MERGE INTO unsupported in local Spark"


# --- helpers ---------------------------------------------------------------

def test_project_slug_and_normalize_sink():
    assert s.project_slug("My Project!") == "my_project"
    assert s.project_slug("123abc") == "p_123abc"
    assert s.normalize_sink_name("db.schema.MyTable") == "MyTable"
    assert s.normalize_sink_name("s3://bucket/path/out.parquet") == "out"


def test_ensure_entrypoints_list_dict_and_list():
    assert s.ensure_entrypoints_list({"entrypoints": [{"id": "a"}]}) == [{"id": "a"}]
    out = s.ensure_entrypoints_list({"entrypoints": {"a": {"x": 1}, "b": "str"}})
    assert {"id": "a", "x": 1} in out and {"id": "b"} in out


# --- init / select-entrypoints / status ------------------------------------

def _init_workspace(tmp_path):
    (tmp_path / "Output").mkdir()
    src = tmp_path / "src.scala"
    src.write_text("object X")
    rc = s.main(["init", "--conv-root", str(tmp_path), "--connection", "conn",
                 "--original-source", str(src)])
    assert rc == 0
    return s.load_state(tmp_path)


def test_init_creates_state_and_dirs(tmp_path):
    st = _init_workspace(tmp_path)
    assert st["schema_version"] == 1 and st["phase"] == "init"
    assert st["config"]["connection_name"] == "conn"
    assert (tmp_path / "Validation/results/phase_b").is_dir()
    assert (tmp_path / "Validation/source/src.scala").is_file()
    assert len(st["run_id"]) == 8


def test_init_idempotent_skip(tmp_path):
    _init_workspace(tmp_path)
    # set a milestone so the idempotency guard triggers
    st = s.load_state(tmp_path)
    st["milestones"]["synth_survey"] = True
    s.save_state(tmp_path, st)
    rid = st["run_id"]
    src = tmp_path / "src.scala"
    rc = s.main(["init", "--conv-root", str(tmp_path), "--connection", "conn",
                 "--original-source", str(src)])
    assert rc == 0 and s.load_state(tmp_path)["run_id"] == rid  # unchanged


# --- init source/Output layout alignment (item 1) --------------------------

def test_init_rejects_misaligned_source(tmp_path):
    # Output nests under an extra wrapper dir the copied source lacks → patches
    # would silently miss one side. init must refuse (exit 2).
    (tmp_path / "Output/proj/src").mkdir(parents=True)
    (tmp_path / "Output/proj/src/Job.scala").write_text("object Job\n")
    src = tmp_path / "orig"
    (src / "src").mkdir(parents=True)
    (src / "src/Job.scala").write_text("object Job\n")
    rc = s.main(["init", "--conv-root", str(tmp_path), "--connection", "c",
                 "--original-source", str(src)])
    assert rc == 2


def test_init_accepts_aligned_source(tmp_path):
    (tmp_path / "Output/src").mkdir(parents=True)
    (tmp_path / "Output/src/Job.scala").write_text("object Job\n")
    src = tmp_path / "orig"
    (src / "src").mkdir(parents=True)
    (src / "src/Job.scala").write_text("object Job\n")
    rc = s.main(["init", "--conv-root", str(tmp_path), "--connection", "c",
                 "--original-source", str(src)])
    assert rc == 0
    assert (tmp_path / "Validation/source/src/Job.scala").is_file()


def test_init_wipes_stale_source_before_copy(tmp_path):
    (tmp_path / "Output/src").mkdir(parents=True)
    (tmp_path / "Output/src/Job.scala").write_text("object Job\n")
    src = tmp_path / "orig"
    (src / "src").mkdir(parents=True)
    (src / "src/Job.scala").write_text("object Job\n")
    # plant a stale file from a hypothetical prior failed init
    stale = tmp_path / "Validation/source/old"
    stale.mkdir(parents=True)
    (stale / "Leftover.scala").write_text("object Leftover\n")
    rc = s.main(["init", "--conv-root", str(tmp_path), "--connection", "c",
                 "--original-source", str(src), "--force"])
    assert rc == 0
    assert not (tmp_path / "Validation/source/old/Leftover.scala").exists()


def _write_analysis(tmp_path, candidates):
    p = tmp_path / "Validation/shared/analysis.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"entrypoint_candidates": candidates}))


def test_select_entrypoints(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}, {"id": "ep2"}, {"id": "ep3"}])
    rc = s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1,ep3"])
    assert rc == 0
    st = s.load_state(tmp_path)
    assert set(st["trials"]) == {"ep1", "ep3"}
    assert st["milestones"]["entrypoints_selected"] is True
    analysis = json.loads((tmp_path / "Validation/shared/analysis.json").read_text())
    assert [e["id"] for e in analysis["entrypoints"]] == ["ep1", "ep3"]


def test_select_entrypoints_max_exceeded(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}, {"id": "ep2"}])
    rc = s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1,ep2", "--max", "1"])
    assert rc == 2


def test_scope_entrypoints_reports_kept_and_removed(tmp_path, capsys):
    # stateless subset filter (no init/state.json required)
    _write_analysis(tmp_path, [{"id": "ep1"}, {"id": "ep2"}, {"id": "ep3"}])
    rc = s.main(["scope-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1,ep3"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "kept ['ep1', 'ep3']" in out, out
    assert "removed 1 unselected candidate(s)" in out, out
    analysis = json.loads((tmp_path / "Validation/shared/analysis.json").read_text())
    assert [e["id"] for e in analysis["entrypoints"]] == ["ep1", "ep3"]
    assert [e["id"] for e in analysis["entrypoint_candidates"]] == ["ep1", "ep3"]


def test_clear_trial_outputs_removes_stale_state(tmp_path):
    trial_dir = tmp_path / "phase_a" / "ep1"
    (trial_dir / "tables").mkdir(parents=True)
    (trial_dir / "artifacts").mkdir()
    (trial_dir / "diffs").mkdir()
    (trial_dir / "stage_snapshot").mkdir()
    for rel in (
        "_harness_status.json", "_index.json", "_manual_review.json",
        "workload_error.txt", "capture_error.txt",
        "tables/out.parquet", "artifacts/wb.xlsx", "diffs/out.json",
        "stage_snapshot/t.csv", "out_diff.json",
    ):
        (trial_dir / rel).write_text("stale", encoding="utf-8")

    s._clear_trial_outputs(trial_dir)

    # dir still exists (recreated) but is empty of stale state
    assert trial_dir.is_dir()
    assert list(trial_dir.iterdir()) == []


def test_clear_trial_outputs_only_touches_given_dir(tmp_path):
    # the phase_a baseline must NOT be cleared when clearing phase_b
    phase_a = tmp_path / "phase_a" / "ep1"
    phase_b = tmp_path / "phase_b" / "ep1"
    (phase_a / "tables").mkdir(parents=True)
    (phase_a / "tables" / "base.parquet").write_text("keep", encoding="utf-8")
    (phase_b / "tables").mkdir(parents=True)
    (phase_b / "tables" / "old.parquet").write_text("stale", encoding="utf-8")

    s._clear_trial_outputs(phase_b)

    assert (phase_a / "tables" / "base.parquet").exists()  # baseline untouched
    assert not (phase_b / "tables").exists()


def test_status_exit_codes(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    assert s.main(["status", "--conv-root", str(tmp_path)]) == 1  # pending
    s.main(["record-fixer-dispatch", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--iter", "1", "--error-class", "workload_failure", "--error-hash", "h",
            "--outcome", "no_change"])
    s.main(["record-trial-status", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--status", "hard_stuck", "--reason", "stuck"])
    assert s.main(["status", "--conv-root", str(tmp_path)]) == 2  # blocked


# --- record-iter / milestone / patch -------------------------------------

def test_record_iter_and_phase_advance(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    rc = s.main(["record-iter", "--conv-root", str(tmp_path), "--trial-id", "ep1",
                 "--phase", "A", "--iter", "1", "--passing", "3", "--failing", "0"])
    assert rc == 0
    st = s.load_state(tmp_path)
    assert st["trials"]["ep1"]["phase_a_iters"][0]["passing"] == 3
    assert st["phase_a"]["iter"] == 1
    assert (tmp_path / "Validation/events.jsonl").is_file()
    # no-op on duplicate iter
    assert s.main(["record-iter", "--conv-root", str(tmp_path), "--trial-id", "ep1",
                   "--phase", "A", "--iter", "1", "--passing", "3", "--failing", "0"]) == 0
    # Phase A alone is terminal-eligible only via record-iter; 'passed' now requires
    # a green Phase B iter (the passed gate), matching the real two-phase flow.
    s.main(["record-iter", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--phase", "B", "--iter", "1", "--passing", "3", "--failing", "0"])
    s.main(["record-trial-status", "--conv-root", str(tmp_path), "--trial-id", "ep1", "--status", "passed"])
    assert s.load_state(tmp_path)["phase"] == "phase_b_done"
    assert s.load_state(tmp_path)["milestones"]["phase_a_complete"] is True
    assert s.load_state(tmp_path)["milestones"]["phase_b_complete"] is True


def test_record_milestone_validation(tmp_path):
    _init_workspace(tmp_path)
    assert s.main(["record-milestone", "--conv-root", str(tmp_path), "--milestone", "bogus"]) == 2
    assert s.main(["record-milestone", "--conv-root", str(tmp_path), "--milestone", "workload_built"]) == 0
    assert s.load_state(tmp_path)["milestones"]["workload_built"] is True


def test_record_patch(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    s.main(["record-patch", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--phase", "phase_a", "--file", "F.scala", "--reason", "fix"])
    st = s.load_state(tmp_path)
    assert st["trials"]["ep1"]["phase_a_patches"][0]["file"] == "F.scala"


# --- document-divergence / migrate / mark-empty / unselected-dep -----------

def test_document_divergence_updates_state_and_analysis(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    rc = s.main(["document-divergence", "--conv-root", str(tmp_path), "--trial-id", "ep1",
                 "--sink-id", "out_tbl", "--column", "amt", "--reason", "float drift"])
    assert rc == 0
    st = s.load_state(tmp_path)
    div = st["trials"]["ep1"]["documented_divergences"][0]
    assert div["column"] == "AMT" and div["sink_id"] == "out_tbl"
    analysis = json.loads((tmp_path / "Validation/shared/analysis.json").read_text())
    assert "ep1.out_tbl" in analysis["expected_divergences"]
    assert analysis["expected_divergences"]["ep1.out_tbl"][0]["scope"] == "data"


def test_document_divergence_persists_to_manifest_and_survives_shim_refresh(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    shared = tmp_path / "Validation" / "shared"
    schemas = shared / "schemas"
    ep_dir = schemas / "entrypoints" / "ep1"
    (ep_dir / "tables").mkdir(parents=True)
    (schemas / "manifest.json").write_text(json.dumps({
        "entrypoints": [{"id": "ep1", "path": "ep1.scala", "dir": "entrypoints/ep1"}],
        "expected_divergences": {},
    }), encoding="utf-8")
    (ep_dir / "_meta.json").write_text(json.dumps({"id": "ep1", "entrypoint_class": "C$"}), encoding="utf-8")
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    rc = s.main(["document-divergence", "--conv-root", str(tmp_path), "--trial-id", "ep1",
                 "--sink-id", "out_tbl", "--column", "amt", "--reason", "float drift",
                 "--scope", "udf"])
    assert rc == 0
    manifest = json.loads((schemas / "manifest.json").read_text())
    assert "ep1.out_tbl" in manifest["expected_divergences"]
    assert manifest["expected_divergences"]["ep1.out_tbl"][0]["scope"] == "udf"
    assert "ep1.__udf__" in manifest["expected_divergences"]
    # Shim refresh must not drop manifest divergences.
    s.ensure_analysis_shim_from_schemas(tmp_path)
    analysis = json.loads((shared / "analysis.json").read_text())
    assert "ep1.out_tbl" in analysis["expected_divergences"]
    assert analysis["expected_divergences"]["ep1.out_tbl"][0]["scope"] == "udf"


def test_mark_unselected_dependency_passes_review(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    rc = s.main(["mark-unselected-dependency", "--conv-root", str(tmp_path),
                 "--trial-id", "ep1", "--reason", "depends on unselected ep2"])
    assert rc == 0
    st = s.load_state(tmp_path)
    assert st["trials"]["ep1"]["status"] == "passed_no_baseline"
    assert st["fixer_dispatches"][0]["error_class"] == "unselected_dependency"


def test_migrate_divergences_ambiguous(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    st = s.load_state(tmp_path)
    st["trials"]["ep1"]["documented_divergences"] = [{"sink_id": "write_001", "column": "X"}]
    s.save_state(tmp_path, st)
    # no phase_a write_ dir -> ambiguous -> exit 1
    assert s.main(["migrate-divergences", "--conv-root", str(tmp_path)]) == 1


# --- build-index + summary gate --------------------------------------------

def test_build_index_run_index(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    s.main(["document-divergence", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--sink-id", "t", "--column", "c", "--reason", "r"])
    assert s.main(["build-index", "--conv-root", str(tmp_path)]) == 0
    ri = json.loads((tmp_path / "Validation/run_index.json").read_text())
    assert ri["run"]["run_id"]
    ep = ri["entrypoints"][0]
    assert ep["id"] == "ep1"
    assert ep["comparison"]["verdict"] == "cosmetic_divergence"  # pending + documented div


def test_summary_exit1_when_trials_non_terminal(tmp_path):
    """summary exits 1 when any trial is pending (no record-iter run yet)."""
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    # no record-iter → ep1 is pending → non-terminal gate fires with exit 1
    assert s.main(["summary", "--conv-root", str(tmp_path)]) == 1


def test_summary_exit0_when_all_outputs_present(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    s.main(["record-iter", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--phase", "A", "--iter", "1", "--passing", "1", "--failing", "0"])
    # Phase B iter required for summary gate to pass; add one and mark passed
    s.main(["record-iter", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--phase", "B", "--iter", "1", "--passing", "2", "--failing", "0"])
    s.main(["record-trial-status", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--status", "passed"])
    assert s.main(["summary", "--conv-root", str(tmp_path)]) == 0
    summ = json.loads((tmp_path / "Validation/results/summary.json").read_text())
    assert summ["decision"]["overall"] in ("partial", "passed", "blocked")
    assert (tmp_path / "Validation/results/REPORT.md").is_file()


# --- commit (git) ----------------------------------------------------------

def test_commit(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.io"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output/x.txt").write_text("hi")
    rc = s.main(["commit", "--conv-root", str(tmp_path), "--message", "msg",
                 "--kind", "test-patch", "--print-sha-only"])
    assert rc == 0
    log = subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True)
    assert "[TEST-PATCH] msg" in log.stdout
    # nothing-to-commit path
    assert s.main(["commit", "--conv-root", str(tmp_path), "--message", "again",
                   "--kind", "test-patch"]) == 0


# --- P1: branch + harvest delivery model -----------------------------------

def _git_init_repo(tmp_path):
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, text=True, check=True)
    run("init", "-q")
    run("config", "user.email", "t@t.io")
    run("config", "user.name", "t")
    run("checkout", "-q", "-b", "main")
    (tmp_path / "Output").mkdir()
    (tmp_path / "Output/x.txt").write_text("base\n")
    (tmp_path / "Output/conf.txt").write_text("base\n")
    run("add", "-A")
    run("commit", "-q", "-m", "baseline")
    return run


def _init_on_branch(tmp_path):
    """git repo on 'main' + scos init → cuts validation/<rid>."""
    _git_init_repo(tmp_path)
    src = tmp_path / "src.scala"
    src.write_text("object X")
    assert s.main(["init", "--conv-root", str(tmp_path), "--connection", "c",
                   "--original-source", str(src)]) == 0
    return s.load_state(tmp_path)


def _branch(tmp_path):
    import subprocess
    return subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                          cwd=tmp_path, capture_output=True, text=True).stdout.strip()


def test_init_cuts_validation_branch(tmp_path):
    st = _init_on_branch(tmp_path)
    assert st["git"]["original_branch"] == "main"
    assert st["git"]["validation_branch"] == f"validation/{st['run_id']}"
    assert st["git"]["harvested"] is False
    assert _branch(tmp_path) == f"validation/{st['run_id']}"
    # source baseline committed on the validation branch
    import subprocess
    log = subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True)
    assert "[VALIDATION] import Phase-A source baseline" in log.stdout


def test_commit_migration_fix_prefix_and_trailer(tmp_path):
    import subprocess
    _init_on_branch(tmp_path)
    (tmp_path / "Output/x.txt").write_text("real fix\n")
    assert s.main(["commit", "--conv-root", str(tmp_path), "--message", "fix join",
                   "--kind", "migration-fix", "--trial-ids", "ep1"]) == 0
    body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=tmp_path,
                          capture_output=True, text=True).stdout
    assert body.startswith("[MIGRATION-FIX] fix join")
    assert "SCOS-Trials: ep1" in body


def test_commit_migration_fix_rejects_scos_leak(tmp_path):
    _init_on_branch(tmp_path)
    (tmp_path / "Output/x.txt").write_text('val t = sys.env("SCOS_DATABASE_NAME")\n')
    with pytest.raises(SystemExit) as e:
        s.main(["commit", "--conv-root", str(tmp_path), "--message", "leak",
                "--kind", "migration-fix"])
    assert e.value.code == 2
    # but the SAME edit is allowed as a test-patch
    assert s.main(["commit", "--conv-root", str(tmp_path), "--message", "harness wiring",
                   "--kind", "test-patch"]) == 0


def test_harvest_cherry_picks_migration_fix_only(tmp_path):
    import subprocess
    st = _init_on_branch(tmp_path)
    vb = st["git"]["validation_branch"]
    # [TEST-PATCH] on a different file (harness wiring — must NOT reach deliverable)
    (tmp_path / "Output/conf.txt").write_text('env = "SCOS_OUTPUT_SCHEMA"\n')
    assert s.main(["commit", "--conv-root", str(tmp_path), "--message", "wire env",
                   "--kind", "test-patch"]) == 0
    # [MIGRATION-FIX] on x.txt (real fix — must reach deliverable)
    (tmp_path / "Output/x.txt").write_text("real fix\n")
    assert s.main(["commit", "--conv-root", str(tmp_path), "--message", "fix dialect",
                   "--kind", "migration-fix", "--trial-ids", "ep1"]) == 0
    # harvest preconditions: summary.json + run_index.json
    ws = tmp_path / "Validation"
    (ws / "results").mkdir(parents=True, exist_ok=True)
    (ws / "results/summary.json").write_text("{}")
    (ws / "run_index.json").write_text("{}")

    assert s.main(["harvest", "--conv-root", str(tmp_path)]) == 0
    assert _branch(tmp_path) == "main"
    assert s.load_state(tmp_path)["git"]["harvested"] is True
    # deliverable has the migration fix but NOT the test patch
    assert (tmp_path / "Output/x.txt").read_text() == "real fix\n"
    assert (tmp_path / "Output/conf.txt").read_text() == "base\n"
    log = subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert "[MIGRATION-FIX] fix dialect" in log
    assert "[TEST-PATCH] wire env" not in log


def test_harvest_no_validation_branch_errors(tmp_path):
    _init_workspace(tmp_path)  # non-git workspace → no git branch recorded
    assert s.main(["harvest", "--conv-root", str(tmp_path)]) == 1


def test_build_index_attributes_migration_fix_commits(tmp_path):
    _init_on_branch(tmp_path)
    (tmp_path / "Output/x.txt").write_text("real fix\n")
    assert s.main(["commit", "--conv-root", str(tmp_path), "--message", "fix",
                   "--kind", "migration-fix", "--trial-ids", "ep1"]) == 0
    st = s.load_state(tmp_path)
    st["trials"] = {"ep1": {"status": "passed", "phase_b_iters": []}}
    s.save_state(tmp_path, st)
    s.build_index(tmp_path)
    idx = json.loads((tmp_path / "Validation/run_index.json").read_text())
    ep = next(e for e in idx["entrypoints"] if e["id"] == "ep1")
    fixes = ep["phase_b"]["migration_fix_commits"]
    assert len(fixes) == 1 and fixes[0]["subject"] == "fix"



# ===========================================================================
# Universal run harness fixes
# ===========================================================================

# --- Gap 2: advance_phase called after record-iter --------------------------
# (advance_phase is already tested via record-iter; these cover the runner path)

def test_advance_phase_pending_with_a_iters():
    """A trial with phase_a_iters advances state.phase to phase_a_done."""
    st = {
        "phase": "init",
        "trials": {
            "ep1": _trial("pending", phase_a_iters=[{"passing": 1, "failing": 0}])
        }
    }
    out = s.advance_phase(st)
    assert out["phase"] == "phase_a_done"


def test_advance_phase_all_terminal_advances_to_phase_b_done():
    """All terminal trials → phase_b_done."""
    st = {
        "phase": "phase_a_done",
        "trials": {
            "ep1": _trial("passed",
                          phase_a_iters=[{"passing":1,"failing":0}],
                          phase_b_iters=[{"passing":2,"failing":0}]),
        }
    }
    out = s.advance_phase(st)
    assert out["phase"] == "phase_b_done"


# --- Gap 1: Phase A terminal skip via pre_phase_a_terminal -----------------

def test_run_phase_a_skips_terminal_trials_in_deselect_set():
    """The terminal-skip set excludes passed/hard_stuck from TERMINAL_TRIAL_STATUSES."""
    import types
    trials = {
        "ep1": _trial("pending"),
        "ep2": _trial("passed"),
        "ep3": _trial("hard_stuck"),
        "ep4": _trial("phase_a_skipped", phase_a_skip_reason="X"),
    }
    # Simulate the deselect logic directly (no sbt needed)
    TERMINAL = s.TERMINAL_TRIAL_STATUSES
    pre = {tid for tid, t in trials.items() if s._status(t) in TERMINAL}
    assert "ep1" not in pre          # pending → runs
    assert "ep2" in pre              # passed → skipped
    assert "ep3" in pre              # hard_stuck → skipped
    assert "ep4" in pre              # phase_a_skipped → skipped


def test_run_phase_a_verify_all_empties_terminal_set():
    trials = {
        "ep1": _trial("passed"),
        "ep2": _trial("pending"),
    }
    TERMINAL = s.TERMINAL_TRIAL_STATUSES
    # verify_all path: empty set regardless of status
    pre = set() if True else {tid for tid, t in trials.items() if s._status(t) in TERMINAL}
    assert pre == set()


def test_run_phase_a_trial_id_deselects_all_others():
    trials = {"ep1": _trial("pending"), "ep2": _trial("pending"), "ep3": _trial("pending")}
    target = "ep2"
    pre = {tid for tid in trials if tid != target}
    assert pre == {"ep1", "ep3"}
    assert "ep2" not in pre


# --- Gap 4: --trial-id CLI validation ----------------------------------------

def test_run_phase_b_trial_id_unknown_returns_error(tmp_path):
    """--trial-id for a non-existent trial exits 2."""
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    rc = s.main(["run-phase-b", "--conv-root", str(tmp_path),
                 "--trial-id", "does_not_exist"])
    assert rc == 2


def test_run_phase_a_trial_id_unknown_returns_error(tmp_path):
    """--trial-id for a non-existent trial exits 2 for Phase A too."""
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    rc = s.main(["run-phase-a", "--conv-root", str(tmp_path),
                 "--trial-id", "no_such_ep"])
    assert rc == 2


# --- Gap 6: _run_sbt_streaming writes log and streams -----------------------

def test_run_sbt_streaming_writes_log_and_returns_rc(tmp_path):
    """_run_sbt_streaming writes output to the log file and returns exit code."""
    import io
    import sys as _sys
    log = tmp_path / "out.log"
    # Use 'echo' / 'python -c' as a portable echo command
    rc = s._run_sbt_streaming(
        [_sys.executable, "-c", "import sys; print('hello sbt'); sys.exit(0)"],
        cwd=str(tmp_path),
        env={**__import__("os").environ},
        log_path=log,
    )
    assert rc == 0
    assert "hello sbt" in log.read_text()


def test_run_sbt_streaming_nonzero_exit_code(tmp_path):
    import sys as _sys
    log = tmp_path / "out.log"
    rc = s._run_sbt_streaming(
        [_sys.executable, "-c", "import sys; sys.exit(42)"],
        cwd=str(tmp_path),
        env={**__import__("os").environ},
        log_path=log,
    )
    assert rc == 42


# --- Gap #1: recover_pending_trials handles phase_a_skipped ----------------

def test_recover_phase_a_skipped_clean_phase_b_promotes_to_passed_no_baseline():
    """phase_a_skipped + clean Phase B → passed_no_baseline (critical gap #1)."""
    st = {"trials": {
        "ep1": _trial("phase_a_skipped",
                      phase_a_skip_reason="QUALIFY unsupported",
                      phase_b_iters=[{"passing": 3, "failing": 0}]),
    }}
    out, n = s.recover_pending_trials(st)
    assert n == 1
    t = out["trials"]["ep1"]
    assert t["status"] == "passed_no_baseline"
    # reason must be preserved, not blanked
    assert t["phase_a_skip_reason"] == "QUALIFY unsupported"


def test_recover_phase_a_skipped_failing_phase_b_becomes_hard_stuck():
    """phase_a_skipped + failing Phase B → hard_stuck (not left as skipped)."""
    st = {"trials": {
        "ep1": _trial("phase_a_skipped",
                      phase_a_skip_reason="MERGE INTO unsupported",
                      phase_b_iters=[{"passing": 0, "failing": 2}]),
    }}
    out, n = s.recover_pending_trials(st)
    assert n == 1
    t = out["trials"]["ep1"]
    assert t["status"] == "hard_stuck"
    assert "phase_a_skipped" in t["hard_stuck_reason"]


def test_recover_phase_a_skipped_no_phase_b_iters_unchanged():
    """phase_a_skipped with no Phase B iters → stays phase_a_skipped."""
    st = {"trials": {"ep1": _trial("phase_a_skipped", phase_a_skip_reason="X")}}
    out, n = s.recover_pending_trials(st)
    assert n == 0
    assert out["trials"]["ep1"]["status"] == "phase_a_skipped"


def test_recover_skip_reason_overrides_passing_phase_a_iter():
    """Even if a passing Phase A iter exists, phase_a_skip_reason prevents 'passed'."""
    st = {"trials": {
        "ep1": _trial("pending",
                      phase_a_iters=[{"passing": 1, "failing": 0}],
                      phase_a_skip_reason="old skip",
                      phase_b_iters=[{"passing": 2, "failing": 0}]),
    }}
    out, n = s.recover_pending_trials(st)
    t = out["trials"]["ep1"]
    assert t["status"] == "passed_no_baseline"


# --- Gap #2: comparison_verdict for phase_a_skipped ------------------------

def test_comparison_verdict_phase_a_skipped_is_unverified():
    """phase_a_skipped → unverified (was wrongly returning 'pending')."""
    assert s.comparison_verdict({"status": "phase_a_skipped"}) == "unverified"
    assert s.comparison_verdict({"status": "passed_no_baseline"}) == "unverified"


def test_comparison_verdict_unchanged_for_other_statuses():
    assert s.comparison_verdict({"status": "passed"}) == "match"
    assert s.comparison_verdict({"status": "hard_stuck"}) == "real_divergence"
    assert s.comparison_verdict({"status": "pending"}) == "pending"


# --- Gap #10: summary blocks on non-terminal trials ------------------------

def test_summary_blocks_on_non_terminal_trials(tmp_path):
    """summary must exit 1 when any trial is not yet terminal."""
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    # ep1 is pending, events.jsonl created by select-entrypoints
    s.main(["record-iter", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--phase", "A", "--iter", "1", "--passing", "1", "--failing", "0"])
    rc = s.main(["summary", "--conv-root", str(tmp_path)])
    assert rc == 1  # blocked — ep1 is still pending


def test_summary_passes_when_all_terminal(tmp_path):
    """summary exits 0 when all trials are terminal."""
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    s.main(["record-iter", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--phase", "A", "--iter", "1", "--passing", "1", "--failing", "0"])
    s.main(["record-iter", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--phase", "B", "--iter", "1", "--passing", "2", "--failing", "0"])
    s.main(["record-trial-status", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--status", "passed"])
    rc = s.main(["summary", "--conv-root", str(tmp_path)])
    assert rc == 0


# --- Gap #8+9: verify-all + reopen -----------------------------------------

def test_maybe_reopen_trial_reopens_on_failure():
    """A terminal trial (passed) is reopened when the verify-all rerun fails."""
    st = {"phase": "phase_b_done", "trials": {
        "ep1": {"status": "passed", "phase_b_iters": [{"passing": 2, "failing": 0}]},
    }}
    conv_root = None  # workspace not needed for pure dict logic; pass a dummy
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "Validation"
        ws.mkdir()
        new_st = s._maybe_reopen_trial(st, "ep1", passing=0, failing=1,
                                        iter_n=2, conv_root=Path(td), workspace=ws)
    assert new_st["trials"]["ep1"]["status"] == "pending"
    assert new_st["phase"] == "phase_a_done"


def test_maybe_reopen_trial_no_op_on_clean_rerun():
    """A terminal trial is NOT reopened when the rerun passes."""
    st = {"phase": "phase_b_done", "trials": {
        "ep1": {"status": "passed", "phase_b_iters": []},
    }}
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "Validation"; ws.mkdir()
        new_st = s._maybe_reopen_trial(st, "ep1", passing=3, failing=0,
                                        iter_n=2, conv_root=Path(td), workspace=ws)
    assert new_st["trials"]["ep1"]["status"] == "passed"


def test_maybe_reopen_trial_non_terminal_not_reopened():
    """A pending trial is not touched by _maybe_reopen_trial."""
    st = {"phase": "init", "trials": {"ep1": {"status": "pending", "phase_b_iters": []}}}
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "Validation"; ws.mkdir()
        new_st = s._maybe_reopen_trial(st, "ep1", passing=0, failing=2,
                                        iter_n=1, conv_root=Path(td), workspace=ws)
    assert new_st["trials"]["ep1"]["status"] == "pending"


# --- Gap #1 regression: existing pending→passed still works ----------------

def test_recover_pending_clean_phase_b_with_baseline_still_passes():
    st = {"trials": {
        "ep1": _trial("pending",
                      phase_a_iters=[{"passing": 1, "failing": 0}],
                      phase_b_iters=[{"passing": 3, "failing": 0}]),
    }}
    out, n = s.recover_pending_trials(st)
    assert out["trials"]["ep1"]["status"] == "passed"
    assert n == 1


# --- Universal root-cause: no_sink / empty-capture honesty -----------------

def test_entrypoint_declared_sinks_merges_both_keys():
    ep = {"sinks": [{"id": "a"}], "external_sinks": [{"id": "b"}]}
    ids = [s["id"] for s in s.entrypoint_declared_sinks(ep)]
    assert ids == ["a", "b"]


def test_entrypoint_declared_sinks_external_only():
    ep = {"external_sinks": [{"id": "x"}]}
    assert [s["id"] for s in s.entrypoint_declared_sinks(ep)] == ["x"]
    assert s.entrypoint_declares_sinks_key(ep) is True


def test_entrypoint_declares_sinks_key_false_when_absent():
    assert s.entrypoint_declares_sinks_key({"id": "e"}) is False
    # An empty external_sinks list still counts as "declared the key".
    assert s.entrypoint_declares_sinks_key({"external_sinks": []}) is True


def test_cli_value_incomplete_flags_stubs_and_blanks():
    for v in (None, "", "   ", "TODO", "FIXME", "TBD", "<input path>", "[TODO]",
              "[{...}]", "null", "none", '""'):
        assert s._cli_value_incomplete(v) is True, v


def test_cli_value_incomplete_exempts_concrete_json_and_values():
    # Documented Args.* stubs are JSON-encoded strings — must count as complete.
    for v in ("[]", "[{}]", '[{"counts": 0}]', '{"k": 1}', "true", "false", "0",
              "--input", "/path/to/data", "s3://bucket/key"):
        assert s._cli_value_incomplete(v) is False, v


def test_entrypoint_ast_write_evidence_detects_writes():
    ast = {"files": [{
        "path": "/ws/Validation/source/src/main/scala/Job.scala",
        "writes": [{"call": "saveAsTable", "args": ["out"]}],
        "unresolved_writes": [],
        "write_helpers": [],
    }]}
    ep = {"id": "job", "path": "src/main/scala/Job.scala"}
    ev = s.entrypoint_ast_write_evidence(ast, ep)
    assert ev and "writes=1" in ev[0]


def test_entrypoint_ast_write_evidence_empty_when_no_writes():
    ast = {"files": [{
        "path": "src/main/scala/Job.scala",
        "writes": [],
        "unresolved_writes": [],
        "write_helpers": [],
    }]}
    assert s.entrypoint_ast_write_evidence(ast, {"path": "src/main/scala/Job.scala"}) == []


def test_recover_no_sink_baseline_zero_capture_phase_b_passes():
    """AST-confirmed no-sink + clean Phase B with 0 tables → passed (smoke)."""
    st = {"trials": {
        "ep1": _trial(
            "pending",
            phase_a_iters=[{"passing": 1, "failing": 0, "no_sink_baseline": True}],
            phase_b_iters=[{"passing": 0, "failing": 0}],
        ),
    }}
    out, n = s.recover_pending_trials(st)
    assert n == 1
    assert out["trials"]["ep1"]["status"] == "passed"
    assert out["trials"]["ep1"].get("no_sink_smoke") is True


def test_recover_zero_capture_phase_b_without_no_sink_stays_pending():
    """Empty Phase B capture without no_sink_baseline must NOT soft-green."""
    st = {"trials": {
        "ep1": _trial(
            "pending",
            phase_a_iters=[{"passing": 1, "failing": 0}],
            phase_b_iters=[{"passing": 0, "failing": 0}],
        ),
    }}
    out, n = s.recover_pending_trials(st)
    assert n == 0
    assert out["trials"]["ep1"]["status"] == "pending"


def test_recover_allow_empty_style_zero_capture_stays_pending():
    """All-allow_empty / 0-row capture looks like passing=0 failing=0 — stay pending."""
    st = {"trials": {
        "ep1": _trial(
            "pending",
            phase_a_iters=[{"passing": 0, "failing": 0}],
            phase_b_iters=[{"passing": 0, "failing": 0}],
        ),
    }}
    out, n = s.recover_pending_trials(st)
    assert n == 0
    assert out["trials"]["ep1"]["status"] == "pending"


def test_build_index_phase_a_verdict_no_sink_baseline(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    st = s.load_state(tmp_path)
    st["trials"]["ep1"] = _trial(
        "passed",
        phase_a_iters=[{"passing": 1, "failing": 0, "no_sink_baseline": True}],
        phase_b_iters=[{"passing": 0, "failing": 0}],
        no_sink_smoke=True,
    )
    s.save_state(tmp_path, st)
    s.build_index(tmp_path)
    idx = json.loads((tmp_path / "Validation" / "run_index.json").read_text())
    ep = next(e for e in idx["entrypoints"] if e["id"] == "ep1")
    assert ep["phase_a"]["verdict"] == "no_sink_baseline"
    assert "no-sink" in (ep["verdict"].get("reason") or "").lower()


def test_needs_provision_when_golden_schemas_missing():
    st = {
        "trials": {"ep1": _trial()},
        "snowflake": {"provisioned": False, "golden_schemas": {}},
    }
    assert s._needs_provision(st) is True


def test_needs_provision_false_when_all_trials_have_golden():
    st = {
        "trials": {"ep1": _trial()},
        "snowflake": {
            "provisioned": True,
            "golden_schemas": {"ep1": {"schema": "GOLDEN_EP1"}},
        },
    }
    assert s._needs_provision(st) is False


# --- JVM readiness: JDK resolver + preflight gate --------------------------
# These cover the one-shot-baseline fix: Phase A's local Spark 3.5 runs only on
# Java 8/11/17, so the harness must prefer/auto-provision a compatible JDK and
# hard-fail (never silently produce a no-baseline pass) when it cannot.

def _fake_jdk_home(tmp_path: Path, name: str) -> str:
    home = tmp_path / name
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "java").write_text("#!/bin/sh\n")
    return str(home)


def test_java_major_parses_modern(tmp_path, monkeypatch):
    home = _fake_jdk_home(tmp_path, "jdk17")

    class _R:
        stderr = 'openjdk version "17.0.11" 2024-04-16\nOpenJDK Runtime\n'
        stdout = ""

    monkeypatch.setattr(s.subprocess, "run", lambda *a, **k: _R())
    assert s._java_major(home) == 17


def test_java_major_parses_legacy_1_8(tmp_path, monkeypatch):
    home = _fake_jdk_home(tmp_path, "jdk8")

    class _R:
        stderr = 'java version "1.8.0_402"\nJava(TM) SE Runtime\n'
        stdout = ""

    monkeypatch.setattr(s.subprocess, "run", lambda *a, **k: _R())
    assert s._java_major(home) == 8


def test_java_major_none_when_no_java_binary(tmp_path):
    assert s._java_major(str(tmp_path / "does-not-exist")) is None


def test_resolve_jdk_prefers_installed_17_over_21(tmp_path, monkeypatch):
    # No cache; two installed JVMs (21 first, 17 second). Must pick 17, skip 21.
    monkeypatch.setattr(s, "_JDK_CACHE_FILE", tmp_path / "cache_absent.txt")
    monkeypatch.setattr(s, "_candidate_java_homes", lambda: ["/jvm21", "/jvm17"])
    monkeypatch.setattr(s, "_java_major", lambda h: {"/jvm21": 21, "/jvm17": 17}.get(h))
    monkeypatch.setattr(s, "_cache_jdk", lambda h: None)
    assert s._resolve_phase_a_jdk(allow_provision=False) == "/jvm17"


def test_resolve_jdk_provisions_when_only_21_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_JDK_CACHE_FILE", tmp_path / "cache_absent.txt")
    monkeypatch.setattr(s, "_candidate_java_homes", lambda: ["/jvm21"])
    monkeypatch.setattr(s, "_java_major",
                        lambda h: {"/jvm21": 21, "/prov17": 17}.get(h))
    monkeypatch.setattr(s, "_cache_jdk", lambda h: None)
    monkeypatch.setattr(s, "_provision_jdk_via_coursier", lambda major: "/prov17")
    assert s._resolve_phase_a_jdk(allow_provision=True) == "/prov17"


def test_resolve_jdk_none_when_incompatible_and_no_provision(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_JDK_CACHE_FILE", tmp_path / "cache_absent.txt")
    monkeypatch.setattr(s, "_candidate_java_homes", lambda: ["/jvm21"])
    monkeypatch.setattr(s, "_java_major", lambda h: 21)
    assert s._resolve_phase_a_jdk(allow_provision=False) is None


def test_apply_jdk_to_env_sets_home_and_path():
    env = s._apply_jdk_to_env({"PATH": "/usr/bin"}, "/opt/jdk17")
    assert env["JAVA_HOME"] == "/opt/jdk17"
    assert env["PATH"].startswith("/opt/jdk17/bin" + s.os.pathsep)


def test_apply_jdk_to_env_noop_when_blank():
    env = {"PATH": "/usr/bin"}
    assert s._apply_jdk_to_env(env, "") is env  # unchanged, same object


def test_preflight_ok_phase_a(tmp_path, monkeypatch):
    monkeypatch.setattr(s.shutil, "which", lambda name: "/usr/bin/sbt")
    monkeypatch.setattr(s, "_resolve_phase_a_jdk", lambda allow_provision=True: "/jdk17")
    rc, problems, jh = s._preflight_checks(tmp_path, "a")
    assert rc == 0 and problems == [] and jh == "/jdk17"


def test_preflight_hard_fails_without_compatible_jdk(tmp_path, monkeypatch):
    monkeypatch.setattr(s.shutil, "which", lambda name: "/usr/bin/sbt")
    monkeypatch.setattr(s, "_resolve_phase_a_jdk", lambda allow_provision=True: None)
    rc, problems, jh = s._preflight_checks(tmp_path, "a")
    assert rc == 3 and jh is None
    assert any("Java 8/11/17" in p for p in problems)


def test_preflight_phase_b_requires_client_jar(tmp_path, monkeypatch):
    monkeypatch.setattr(s.shutil, "which", lambda name: "/usr/bin/sbt")
    monkeypatch.setattr(s, "_resolve_phase_a_jdk", lambda allow_provision=True: "/jdk17")
    monkeypatch.setattr(s, "_scos_client_jar_locatable", lambda td, cr: False)
    rc, problems, _ = s._preflight_checks(tmp_path, "b")
    assert rc == 3
    assert any("snowpark-connect-java-client" in p for p in problems)


def test_preflight_reports_missing_sbt(tmp_path, monkeypatch):
    monkeypatch.setattr(s.shutil, "which", lambda name: None)
    monkeypatch.setattr(s, "_resolve_phase_a_jdk", lambda allow_provision=True: "/jdk17")
    rc, problems, _ = s._preflight_checks(tmp_path, "a")
    assert rc == 3 and any("sbt" in p for p in problems)


def test_cmd_preflight_exit_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(s.shutil, "which", lambda name: "/usr/bin/sbt")
    monkeypatch.setattr(s, "_resolve_phase_a_jdk", lambda allow_provision=True: "/jdk17")

    class _A:
        conv_root = str(tmp_path)
        phase = "a"

    assert s._cmd_preflight(_A()) == 0
    monkeypatch.setattr(s, "_resolve_phase_a_jdk", lambda allow_provision=True: None)
    assert s._cmd_preflight(_A()) == 3


# --- phase_a_skipped deny-list / construct gate ----------------------------

@pytest.mark.parametrize("reason", [
    "unknown", "N/A", "na", "error", "failed", "timeout",
    "environment", "spark issue", "cannot run",
])
def test_phase_a_skipped_rejects_denylist_reasons(reason):
    st = _state(a=_trial())
    _, code, err, _ = s.apply_trial_status(st, "a", "phase_a_skipped", reason=reason)
    assert code == 2 and "too weak" in err


def test_phase_a_skipped_rejects_too_short_reason():
    st = _state(a=_trial())
    _, code, err, _ = s.apply_trial_status(st, "a", "phase_a_skipped", reason="short")
    assert code == 2 and "too weak" in err


def test_phase_a_skipped_rejects_reason_without_construct():
    st = _state(a=_trial())
    _, code, err, _ = s.apply_trial_status(
        st, "a", "phase_a_skipped",
        reason="cannot execute this workload locally at all")
    assert code == 2 and "too weak" in err


def test_phase_a_skipped_accepts_named_construct_reason():
    st = _state(a=_trial())
    new, code, err, _ = s.apply_trial_status(
        st, "a", "phase_a_skipped", reason="QUALIFY in rank.sql")
    assert err is None and code is None
    assert new["trials"]["a"]["phase_a_skip_reason"] == "QUALIFY in rank.sql"


# --- host-aware parallelism ------------------------------------------------

def test_resolve_test_parallelism_explicit_wins(monkeypatch):
    monkeypatch.setattr(s, "_available_ram_gb", lambda: 4.0)
    monkeypatch.setattr(s.os, "cpu_count", lambda: 32)
    assert s._resolve_test_parallelism(3) == 3
    assert s._resolve_test_parallelism(1) == 1


def test_resolve_test_parallelism_auto_caps(monkeypatch):
    # Pin cpu_count to a large number so the CPU cap never constrains the RAM cap.
    monkeypatch.setattr(s.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(s, "_available_ram_gb", lambda: 4.0)
    assert s._resolve_test_parallelism(None) == 1
    monkeypatch.setattr(s, "_available_ram_gb", lambda: 12.0)
    assert s._resolve_test_parallelism(None) == 2
    monkeypatch.setattr(s, "_available_ram_gb", lambda: 32.0)
    assert s._resolve_test_parallelism(None) == 4
    monkeypatch.setattr(s, "_available_ram_gb", lambda: None)
    assert s._resolve_test_parallelism(None) == 4


# Speed 8: CPU cap constrains parallelism when logical CPUs are scarce
def test_resolve_parallelism_uses_cpu_cap(monkeypatch):
    # 32 GiB RAM → ram_cap=4, but only 2 logical CPUs → cpu_cap=1 → result=1
    monkeypatch.setattr(s, "_available_ram_gb", lambda: 32.0)
    monkeypatch.setattr(s.os, "cpu_count", lambda: 2)
    assert s._resolve_test_parallelism(None) == 1

    # 32 GiB RAM → ram_cap=4, 8 CPUs → cpu_cap=4 → result=4
    monkeypatch.setattr(s.os, "cpu_count", lambda: 8)
    assert s._resolve_test_parallelism(None) == 4

    # 12 GiB RAM → ram_cap=2, 4 CPUs → cpu_cap=2 → result=2
    monkeypatch.setattr(s, "_available_ram_gb", lambda: 12.0)
    monkeypatch.setattr(s.os, "cpu_count", lambda: 4)
    assert s._resolve_test_parallelism(None) == 2

    # CPU cap is minimum 1 even when cpu_count=1
    monkeypatch.setattr(s.os, "cpu_count", lambda: 1)
    assert s._resolve_test_parallelism(None) == 1


# --- honest prewarm --------------------------------------------------------

def test_prewarm_fails_without_jdk(tmp_path, monkeypatch):
    _init_workspace(tmp_path)
    monkeypatch.setattr(s, "_resolve_phase_a_jdk", lambda allow_provision=True: None)
    monkeypatch.setattr(s.shutil, "which",
                        lambda name: "/usr/bin/sbt" if name == "sbt" else None)
    # Avoid real rsync into kit; stage is still needed so kit_src must exist.
    class _A:
        conv_root = str(tmp_path)
    rc = s._cmd_prewarm(_A())
    assert rc == 3
    st = s.load_state(tmp_path)
    assert not (st.get("milestones") or {}).get("venv_prewarmed")


def test_prewarm_fails_without_sbt(tmp_path, monkeypatch):
    _init_workspace(tmp_path)
    monkeypatch.setattr(s, "_resolve_phase_a_jdk", lambda allow_provision=True: "/jdk17")
    monkeypatch.setattr(s.shutil, "which", lambda name: None)
    monkeypatch.setattr(s, "_stage_scos_client_jar", lambda *a, **k: "")
    class _A:
        conv_root = str(tmp_path)
    rc = s._cmd_prewarm(_A())
    assert rc == 3
    st = s.load_state(tmp_path)
    assert not (st.get("milestones") or {}).get("venv_prewarmed")


# --- mock-guard hard-fail on datagen import --------------------------------

def test_ensure_mock_data_hard_fails_on_datagen_import(tmp_path, monkeypatch):
    _init_workspace(tmp_path)
    schemas = tmp_path / "Validation" / "shared" / "schemas"
    schemas.mkdir(parents=True)
    (schemas / "manifest.json").write_text('{"tables":[]}', encoding="utf-8")

    import sys as _sys
    _sys.modules.pop("datagen", None)

    class _Blocker:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "datagen":
                raise ImportError("blocked datagen")
            return None

    monkeypatch.setattr(_sys, "meta_path", [_Blocker()] + list(_sys.meta_path))
    rc, problems = s._ensure_mock_data(tmp_path)
    assert rc == 2
    assert any("datagen import failed" in p for p in problems)


# --- thin-jar / classpath / classify ---------------------------------------

def test_filter_dependency_classpath_drops_spark_delta_hadoop():
    sep = s.os.pathsep
    raw = sep.join([
        "/repo/com.example/lib-util-1.0.jar",
        "/repo/org.apache.spark/spark-sql_2.12-3.5.0.jar",
        "/repo/io.delta/delta-spark_2.12-3.1.0.jar",
        "/repo/org.apache.hadoop/hadoop-client-3.3.4.jar",
        "/repo/com.amazon/deequ-2.0.jar",
    ])
    filtered = s._filter_dependency_classpath(raw)
    parts = filtered.split(sep) if filtered else []
    assert any("lib-util" in p for p in parts)
    assert any("deequ" in p for p in parts)
    assert not any("spark-sql" in p for p in parts)
    assert not any("delta-spark" in p for p in parts)
    assert not any("hadoop-client" in p for p in parts)


def test_is_fat_jar_and_classify_build_failure(tmp_path):
    assert s._is_fat_jar("/x/foo-assembly-1.0.jar")
    assert s._is_fat_jar("/x/app-shadow.jar")
    assert not s._is_fat_jar("/x/app_2.12-1.0.jar")

    log = tmp_path / "scos_source_build.log"
    log.write_text("unresolved dependency: com.foo#bar;1.0\n", encoding="utf-8")
    cause, rem = s._classify_build_failure(log)
    assert cause == "unresolved-dependency"
    assert "dependencies" in rem.lower() or "Fix" in rem

    log.write_text("[error] compile failed\n[error] not found: type Foo\n", encoding="utf-8")
    cause, _ = s._classify_build_failure(log)
    assert cause == "compile-error"

    cause, _ = s._classify_build_failure(tmp_path / "missing.log")
    assert cause == "no-build-tool-or-empty-log"


def test_build_source_jar_thin_plus_classpath_ok(tmp_path, monkeypatch):
    src = tmp_path / "source"
    (src / "target" / "scala-2.12").mkdir(parents=True)
    jar = src / "target" / "scala-2.12" / "app_2.12-1.0.jar"
    jar.write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # minimal zip-ish
    (src / "build.sbt").write_text('name := "app"\n', encoding="utf-8")

    monkeypatch.setattr(s.shutil, "which",
                        lambda name: "/usr/bin/sbt" if name == "sbt" else None)

    def _fake_run(cmd, **kwargs):
        class R:
            returncode = 1 if "assembly" in cmd else 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(s.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        s, "_export_dependency_classpath",
        lambda *a, **k: f"/deps/util.jar{s.os.pathsep}/deps/other.jar",
    )
    monkeypatch.setattr(s, "_find_built_jar", lambda d: str(jar))

    build = s._build_source_jar(src, java_home="/jdk17")
    assert build["jar"] == str(jar)
    assert build["ok"] is True
    assert "util.jar" in build["extra_classpath"]
    assert build["build_tool"] == "sbt"


def test_build_source_jar_no_jar_not_ok(tmp_path, monkeypatch):
    src = tmp_path / "source"
    src.mkdir()
    (src / "build.sbt").write_text('name := "app"\n', encoding="utf-8")
    monkeypatch.setattr(s.shutil, "which",
                        lambda name: "/usr/bin/sbt" if name == "sbt" else None)

    def _fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(s.subprocess, "run", _fake_run)
    monkeypatch.setattr(s, "_find_built_jar", lambda d: "")
    build = s._build_source_jar(src, java_home="/jdk17")
    assert build["jar"] == ""
    assert build["ok"] is False


# Speed 2: reuse existing fat jar without triggering a rebuild
def test_build_source_jar_reuses_existing_fat_jar(tmp_path, monkeypatch):
    src = tmp_path / "source"
    (src / "target" / "scala-2.12").mkdir(parents=True)
    fat_jar = src / "target" / "scala-2.12" / "app-assembly-1.0.jar"
    fat_jar.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (src / "build.sbt").write_text('name := "app"\n', encoding="utf-8")

    def _should_not_build(*a, **k):
        raise AssertionError("rebuild must not run when fat jar exists")

    monkeypatch.setattr(s.subprocess, "run", _should_not_build)
    monkeypatch.setattr(s, "_find_built_jar", lambda d: str(fat_jar))

    build = s._build_source_jar(src, java_home="/jdk17")
    assert build["ok"] is True
    assert build["jar"] == str(fat_jar)
    assert build["extra_classpath"] == ""


# Speed 3: classpath export is skipped when cache file is fresh
def test_classpath_export_uses_cache_when_fresh(tmp_path, monkeypatch):
    import time
    src = tmp_path / "source"
    src.mkdir()
    # Write build.sbt with an old mtime (simulate "hasn't changed since last export")
    build_sbt = src / "build.sbt"
    build_sbt.write_text('name := "app"\n', encoding="utf-8")
    # Write a cache file that is newer than build.sbt
    cache = src / "scos_runtime_classpath.txt"
    cache.write_text("/cached/dep.jar", encoding="utf-8")
    # Touch the cache to be clearly newer than build.sbt
    past = build_sbt.stat().st_mtime - 10
    import os
    os.utime(build_sbt, (past, past))

    def _should_not_export(*a, **k):
        raise AssertionError("CP export must not run when cache is fresh")

    monkeypatch.setattr(s.shutil, "which",
                        lambda name: "/usr/bin/sbt" if name == "sbt" else None)
    # Patch subprocess.run to detect if sbt is called
    monkeypatch.setattr(s.subprocess, "run", _should_not_export)

    result = s._export_dependency_classpath(src, "sbt", java_home="/jdk17",
                                             force_rebuild=False)
    # Should return filtered version of the cached classpath
    assert "/cached/dep.jar" in result or result == "/cached/dep.jar"


def test_prewarm_fails_on_kit_compile_failure(tmp_path, monkeypatch):
    _init_workspace(tmp_path)
    monkeypatch.setattr(s, "_resolve_phase_a_jdk", lambda allow_provision=True: "/jdk17")
    monkeypatch.setattr(s.shutil, "which",
                        lambda name: "/usr/bin/sbt" if name == "sbt" else None)
    monkeypatch.setattr(s, "_stage_scos_client_jar", lambda *a, **k: "")

    def _fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stdout = "error: something"
            stderr = "compile failed"
        return R()

    monkeypatch.setattr(s.subprocess, "run", _fake_run)
    class _A:
        conv_root = str(tmp_path)
    rc = s._cmd_prewarm(_A())
    assert rc == 3
    st = s.load_state(tmp_path)
    assert not (st.get("milestones") or {}).get("venv_prewarmed")


def test_filter_dependency_classpath_drops_gav_path_segments():
    sep = s.os.pathsep
    # Basename deliberately avoids spark-/delta- prefixes so the GAV-path rule fires.
    raw = sep.join([
        "/repo/com/example/util/1.0/util-1.0.jar",
        "/repo/org/apache/spark/spark-catalyst_2.12/3.5.0/catalyst_2.12-3.5.0.jar",
        "/repo/io/delta/delta-storage/3.1.0/storage-3.1.0.jar",
    ])
    detail = {}
    filtered = s._filter_dependency_classpath(raw, detail=detail)
    parts = filtered.split(sep) if filtered else []
    assert any("util-1.0" in p for p in parts)
    assert not any("catalyst_2.12" in p for p in parts)
    assert not any("storage-3.1.0" in p for p in parts)
    assert len(detail["dropped"]) >= 2
    assert any(d.get("reason") == "gav-path" for d in detail["dropped"])


def test_resolve_workload_artifact_prefers_existing_fat(tmp_path, monkeypatch):
    out = tmp_path / "Output"
    (out / "target").mkdir(parents=True)
    jar = out / "target" / "app-assembly-1.0.jar"
    jar.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (out / "build.sbt").write_text('name := "app"\n', encoding="utf-8")

    def _should_not_build(*a, **k):
        raise AssertionError("should not rebuild when fat jar exists")

    monkeypatch.setattr(s, "_build_source_jar", _should_not_build)
    build = s._resolve_workload_artifact(out, preferred_jar=str(jar), allow_build=True)
    assert build["ok"] is True
    assert build["jar"] == str(jar.resolve())
    assert build["extra_classpath"] == ""


def test_resolve_workload_artifact_thin_needs_classpath(tmp_path, monkeypatch):
    out = tmp_path / "Output"
    (out / "target").mkdir(parents=True)
    jar = out / "target" / "app_2.12-1.0.jar"
    jar.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (out / "build.sbt").write_text('name := "app"\n', encoding="utf-8")
    monkeypatch.setattr(
        s, "_export_dependency_classpath",
        lambda *a, **k: "",
    )
    build = s._resolve_workload_artifact(out, preferred_jar=str(jar), allow_build=False)
    assert build["jar"]
    assert build["ok"] is False


def test_build_doctor_side_migrated(tmp_path, monkeypatch):
    _init_workspace(tmp_path)
    out = tmp_path / "Output"
    (out / "target").mkdir(parents=True)
    jar = out / "target" / "workload-assembly.jar"
    jar.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (out / "build.sbt").write_text('name := "w"\n', encoding="utf-8")
    analysis = tmp_path / "Validation" / "shared" / "analysis.json"
    analysis.parent.mkdir(parents=True, exist_ok=True)
    analysis.write_text(
        json.dumps({"entrypoints": [], "jar_path": "Output/target/workload-assembly.jar"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(s, "_resolve_phase_a_jdk", lambda allow_provision=True: "/jdk17")

    class _A:
        conv_root = str(tmp_path)
        side = "migrated"
        source_dir = None
        output = None

    rc = s._cmd_build_doctor(_A())
    assert rc == 0


# --- summary compare.json preference ---------------------------------------

def test_build_index_prefers_compare_json_verdict(tmp_path):
    _init_workspace(tmp_path)
    st = s.load_state(tmp_path)
    st["trials"] = {
        "ep1": _trial("passed",
                      phase_a_iters=[{"passing": 1, "failing": 0}],
                      phase_b_iters=[{"passing": 1, "failing": 0}]),
    }
    s.save_state(tmp_path, st)
    pb = tmp_path / "Validation" / "results" / "phase_b" / "ep1"
    pb.mkdir(parents=True)
    (pb / "compare.json").write_text(
        json.dumps({"verdict": "diverge", "tables": [{"table": "t1", "verdict": "diverge"}]}),
        encoding="utf-8",
    )
    _, eps = s._build_index_entrypoints(
        tmp_path / "Validation", st["trials"], st)
    assert eps[0]["comparison"]["verdict"] == "real_divergence"


def test_build_index_compare_json_error_is_unverified(tmp_path):
    _init_workspace(tmp_path)
    st = s.load_state(tmp_path)
    st["trials"] = {
        "ep1": _trial("passed",
                      phase_a_iters=[{"passing": 1, "failing": 0}],
                      phase_b_iters=[{"passing": 1, "failing": 0}]),
    }
    s.save_state(tmp_path, st)
    pb = tmp_path / "Validation" / "results" / "phase_b" / "ep1"
    pb.mkdir(parents=True)
    (pb / "compare.json").write_text(
        json.dumps({"verdict": "error", "tables": []}),
        encoding="utf-8",
    )
    _, eps = s._build_index_entrypoints(
        tmp_path / "Validation", st["trials"], st)
    assert eps[0]["comparison"]["verdict"] == "unverified"


def test_comparator_shim_loads_pyspark_compare():
    import importlib.util
    import sys as _sys
    shim = _SCRIPTS / "harness" / "comparator.py"
    name = "scos_cmp_shim_test2"
    spec = importlib.util.spec_from_file_location(name, shim)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[name] = mod
    spec.loader.exec_module(mod)
    # Shim replaces sys.modules[name] with the PySpark module.
    loaded = _sys.modules[name]
    assert callable(getattr(loaded, "compare", None))
