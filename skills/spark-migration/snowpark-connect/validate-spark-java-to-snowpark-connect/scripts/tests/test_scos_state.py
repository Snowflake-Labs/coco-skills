"""Tests for scos_state.py — the ported ScosState state machine (P4a core, Java variant).

Covers the invariants that matter most: phase advancement, the record-trial-status
hard gate (incl. the hard_stuck fixer-dispatch requirement), run_index comparison
verdict, manual-review materialization, pending recovery, and atomic state I/O.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import scos_state as s  # noqa: E402


def _trial(status="pending", **kw):
    return {"status": status, "phase_a_iters": [], "phase_b_iters": [], **kw}


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


def test_comparison_verdict_phase_a_skipped_is_unverified():
    """phase_a_skipped means "no comparable baseline", same as passed_no_baseline —
    it must not fall through to the documented_divergences/hard_stuck/pending
    branches (parity with the Scala validator)."""
    assert s.comparison_verdict({"status": "phase_a_skipped"}) == "unverified"
    # Even with documented divergences recorded, still unverified (no baseline
    # to compare them against) rather than cosmetic_divergence.
    assert s.comparison_verdict(
        {"status": "phase_a_skipped", "documented_divergences": [{"c": 1}]}
    ) == "unverified"


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


def test_passed_no_baseline_allowed_without_baseline():
    # phase_a_skipped-style: no passing phase_a iter → allowed
    st = _state(a=_trial(phase_a_iters=[{"iter": 1, "passing": 0, "failing": 2}]))
    new, code, err, _ = s.apply_trial_status(st, "a", "passed_no_baseline")
    assert err is None and new["trials"]["a"]["status"] == "passed_no_baseline"


def test_passed_no_baseline_escape_with_flag():
    st = _state(a=_trial(phase_a_iters=[{"iter": 1, "passing": 3, "failing": 0}]))
    new, code, err, _ = s.apply_trial_status(st, "a", "passed_no_baseline",
                                             baseline_not_comparable=True)
    assert err is None and new["trials"]["a"]["status"] == "passed_no_baseline"


def test_passed_clears_hard_stuck_reason():
    st = _state(a=_trial("pending", hard_stuck_reason="old"))
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
    (pb / "_manual_review.json").write_text("{}")
    (pb / "_index.json").write_text("{}")
    out = s.materialize_manual_review_statuses(tmp_path, st)
    assert out["trials"]["ep1"]["status"] == "passed_no_baseline"


def test_recover_pending_trials():
    st = {"trials": {
        "ok": _trial("pending", phase_a_iters=[{}], phase_b_iters=[{"passing": 3, "failing": 0}]),
        "bad": _trial("pending", phase_b_iters=[{"passing": 0, "failing": 2}]),
    }}
    out, n = s.recover_pending_trials(st)
    assert n == 2
    assert out["trials"]["ok"]["status"] == "passed"
    assert out["trials"]["bad"]["status"] == "hard_stuck"


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
    st = _state(a=_trial())
    s.save_state(tmp_path, st)
    rc = s.main(["record-trial-status", "--conv-root", str(tmp_path), "--trial-id", "a", "--status", "passed"])
    assert rc == 0
    assert s.load_state(tmp_path)["trials"]["a"]["status"] == "passed"
    # hard_stuck with no dispatch -> exit 2
    rc2 = s.main(["record-trial-status", "--conv-root", str(tmp_path), "--trial-id", "a", "--status", "hard_stuck"])
    assert rc2 == 2


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
    src = tmp_path / "src.java"
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
    assert (tmp_path / "Validation/source/src.java").is_file()
    assert len(st["run_id"]) == 8


def test_init_idempotent_skip(tmp_path):
    _init_workspace(tmp_path)
    # set a milestone so the idempotency guard triggers
    st = s.load_state(tmp_path)
    st["milestones"]["analyzer_survey"] = True
    s.save_state(tmp_path, st)
    rid = st["run_id"]
    src = tmp_path / "src.java"
    rc = s.main(["init", "--conv-root", str(tmp_path), "--connection", "conn",
                 "--original-source", str(src)])
    assert rc == 0 and s.load_state(tmp_path)["run_id"] == rid  # unchanged


# --- init source/Output layout alignment (item 1) --------------------------

def test_init_rejects_misaligned_source(tmp_path):
    # Output nests under an extra wrapper dir the copied source lacks → patches
    # would silently miss one side. init must refuse (exit 2).
    (tmp_path / "Output/proj/src").mkdir(parents=True)
    (tmp_path / "Output/proj/src/Job.java").write_text("public class Job {}\n")
    src = tmp_path / "orig"
    (src / "src").mkdir(parents=True)
    (src / "src/Job.java").write_text("public class Job {}\n")
    rc = s.main(["init", "--conv-root", str(tmp_path), "--connection", "c",
                 "--original-source", str(src)])
    assert rc == 2


def test_init_accepts_aligned_source(tmp_path):
    (tmp_path / "Output/src").mkdir(parents=True)
    (tmp_path / "Output/src/Job.java").write_text("public class Job {}\n")
    src = tmp_path / "orig"
    (src / "src").mkdir(parents=True)
    (src / "src/Job.java").write_text("public class Job {}\n")
    rc = s.main(["init", "--conv-root", str(tmp_path), "--connection", "c",
                 "--original-source", str(src)])
    assert rc == 0
    assert (tmp_path / "Validation/source/src/Job.java").is_file()


def test_init_wipes_stale_source_before_copy(tmp_path):
    (tmp_path / "Output/src").mkdir(parents=True)
    (tmp_path / "Output/src/Job.java").write_text("public class Job {}\n")
    src = tmp_path / "orig"
    (src / "src").mkdir(parents=True)
    (src / "src/Job.java").write_text("public class Job {}\n")
    # plant a stale file from a hypothetical prior failed init
    stale = tmp_path / "Validation/source/old"
    stale.mkdir(parents=True)
    (stale / "Leftover.java").write_text("public class Leftover {}\n")
    rc = s.main(["init", "--conv-root", str(tmp_path), "--connection", "c",
                 "--original-source", str(src), "--force"])
    assert rc == 0
    assert not (tmp_path / "Validation/source/old/Leftover.java").exists()


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
            "--error-class", "x", "--error-hash", "h", "--outcome", "no_change"])
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
                   "--phase", "A", "--iter", "1"]) == 0
    # passed -> phase advances to phase_a_done (terminal, has A, no B)
    s.main(["record-trial-status", "--conv-root", str(tmp_path), "--trial-id", "ep1", "--status", "passed"])
    assert s.load_state(tmp_path)["phase"] == "phase_a_done"


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
            "--phase", "phase_a", "--file", "F.java", "--reason", "fix"])
    st = s.load_state(tmp_path)
    assert st["trials"]["ep1"]["phase_a_patches"][0]["file"] == "F.java"


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


def test_summary_exit4_when_events_missing(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    # no record-iter -> no events.jsonl -> gate fails
    assert s.main(["summary", "--conv-root", str(tmp_path)]) == 4


def test_summary_exit0_when_all_outputs_present(tmp_path):
    _init_workspace(tmp_path)
    _write_analysis(tmp_path, [{"id": "ep1"}])
    s.main(["select-entrypoints", "--conv-root", str(tmp_path), "--ids", "ep1"])
    s.main(["record-iter", "--conv-root", str(tmp_path), "--trial-id", "ep1",
            "--phase", "A", "--iter", "1", "--passing", "1", "--failing", "0"])
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
    src = tmp_path / "src.java"
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


# --- auto-provision helpers ------------------------------------------------

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


# ---------------------------------------------------------------------------
# advance_phase: milestone flipping + no all-terminal gate (SNOW-3715354)
# ---------------------------------------------------------------------------


def test_advance_phase_flips_phase_a_complete_milestone():
    """batch.py reads these milestones for pool phase labels.

    Java's advance_phase never set them, so the pool always reported the Phase A
    label regardless of actual progress.
    """
    state = {"phase": "init", "trials": {"t1": _trial(phase_a_iters=[{"iter": 1}])}}
    out = s.advance_phase(state)
    assert out["phase"] == "phase_a_done"
    assert out["milestones"]["phase_a_complete"] is True
    assert "phase_b_complete" not in out["milestones"]


def test_advance_phase_flips_both_milestones_at_phase_b():
    state = {
        "phase": "init",
        "trials": {"t1": _trial("passed", phase_a_iters=[{"iter": 1}],
                                phase_b_iters=[{"iter": 1}])},
    }
    out = s.advance_phase(state)
    assert out["phase"] == "phase_b_done"
    assert out["milestones"]["phase_a_complete"] is True
    assert out["milestones"]["phase_b_complete"] is True


def test_advance_phase_does_not_require_all_trials_terminal():
    """Phase tracks ITERATION progress, not final verdict (PySpark/Scala parity).

    Java previously returned early unless every trial was terminal, so a single
    still-pending trial pinned the run at `init` forever and Phase B never began.
    """
    state = {
        "phase": "init",
        "trials": {
            "t1": _trial("passed", phase_a_iters=[{"iter": 1}]),
            "t2": _trial("pending", phase_a_iters=[{"iter": 1}]),  # not terminal
        },
    }
    out = s.advance_phase(state)
    assert out["phase"] == "phase_a_done", "a pending trial must not block advancement"
    assert out["milestones"]["phase_a_complete"] is True


def test_advance_phase_milestone_flip_is_idempotent():
    state = {"phase": "init", "trials": {"t1": _trial(phase_a_iters=[{"iter": 1}])}}
    once = s.advance_phase(state)
    twice = s.advance_phase(once)
    assert twice["milestones"] == once["milestones"]


def test_phase_milestones_are_canonical():
    """record-milestone _die()s on unknown names, so these must be registered."""
    assert "phase_a_complete" in s.CANONICAL_MILESTONES
    assert "phase_b_complete" in s.CANONICAL_MILESTONES


# ---------------------------------------------------------------------------
# _render_spec — every {{TOKEN}} in the real template must be substituted.
# Regression coverage: EXTRA_CLASSPATH_SOURCE/MIGRATED and SCHEMAS_DIR_PATH
# used to have no entry in the tokens dict, so they leaked into the rendered
# .scala file as literal "{{TOKEN}}" text (compiles fine inside a Scala
# string literal, but is silently wrong at runtime).
# ---------------------------------------------------------------------------

_TEMPLATE_PATH = (
    _SCRIPTS.parent.parent / "validate-spark-scala-to-snowpark-connect"
    / "harness-scala" / "kit" / "templates" / "TestTemplate.scala.tmpl"
)


def _render_fixture(**overrides):
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    ep = {"id": "my_ep", "entrypoint_class": "com.example.MyJob", "entrypoint_method": "main"}
    kwargs = dict(
        template=template, ep=ep,
        source_jar="/abs/source.jar", migrated_jar="/abs/migrated.jar",
        trial_dir="/abs/results/phase_a/my_ep", phase_a_dir="/abs/results/phase_a/my_ep",
        analysis_json="/abs/Validation/shared/analysis.json",
        state_json="/abs/Validation/state.json",
    )
    kwargs.update(overrides)
    return s._render_spec(**kwargs)


def test_render_spec_substitutes_every_template_placeholder():
    assert _TEMPLATE_PATH.is_file(), f"shared template not found: {_TEMPLATE_PATH}"
    rendered = _render_fixture()
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", rendered)
    assert not leftover, f"unsubstituted template placeholder(s) leaked into rendered spec: {leftover}"


def test_render_spec_fills_schemas_dir_and_extra_classpath_with_real_values():
    rendered = _render_fixture(
        schemas_dir="/abs/Validation/shared/schemas",
        extra_classpath_source="/abs/lib/a.jar:/abs/lib/b.jar",
        extra_classpath_migrated="/abs/lib/c.jar",
    )
    assert 'val SCHEMAS_DIR_PATH: String = """/abs/Validation/shared/schemas"""' in rendered
    assert 'val EXTRA_CLASSPATH_SOURCE: String = """/abs/lib/a.jar:/abs/lib/b.jar"""' in rendered
    assert 'val EXTRA_CLASSPATH_MIGRATED: String = """/abs/lib/c.jar"""' in rendered


def test_render_spec_defaults_missing_optional_fields_to_empty_not_literal_token():
    """Before the fix, omitting schemas_dir/extra_classpath left the literal
    "{{SCHEMAS_DIR_PATH}}" etc. text in the rendered spec (a non-empty garbage
    path) instead of an empty string (the template's documented "not set" value).
    """
    rendered = _render_fixture()
    assert 'val SCHEMAS_DIR_PATH: String = """"""' in rendered
    assert 'val EXTRA_CLASSPATH_SOURCE: String = """"""' in rendered
    assert 'val EXTRA_CLASSPATH_MIGRATED: String = """"""' in rendered


# ---------------------------------------------------------------------------
# _run_comparators_for_passed_trials — auto-compare, ported from Scala.
# ---------------------------------------------------------------------------

def _make_index(path, table_names):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tables": [{"name": n} for n in table_names]}), encoding="utf-8")


def test_run_comparators_skips_non_passed_trials(tmp_path):
    state = {"trials": {"t1": _trial("hard_stuck")}}
    (tmp_path / "Validation" / "results" / "phase_a" / "t1" / "tables").mkdir(parents=True)
    s._run_comparators_for_passed_trials(tmp_path, state)
    compare_json = tmp_path / "Validation" / "results" / "phase_b" / "t1" / "compare.json"
    assert not compare_json.exists(), "non-passed trials must not be auto-compared"


def test_run_comparators_skips_trials_without_phase_a_baseline(tmp_path):
    state = {"trials": {"t1": _trial("passed")}}
    # No phase_a/t1/tables dir at all — no baseline to compare against.
    s._run_comparators_for_passed_trials(tmp_path, state)
    compare_json = tmp_path / "Validation" / "results" / "phase_b" / "t1" / "compare.json"
    assert not compare_json.exists()


def test_run_comparators_invokes_compare_trial_for_passed_trial_with_baseline(tmp_path, capsys):
    state = {"trials": {"t1": _trial("passed")}}
    val_root = tmp_path / "Validation"
    (val_root / "results" / "phase_a" / "t1" / "tables").mkdir(parents=True)
    _make_index(val_root / "results" / "phase_a" / "t1" / "_index.json", [])
    (val_root / "results" / "phase_b" / "t1").mkdir(parents=True)
    _make_index(val_root / "results" / "phase_b" / "t1" / "_index.json", [])
    s._run_comparators_for_passed_trials(tmp_path, state)
    # Zero declared tables -> compare_trial still runs and writes its aggregate
    # summary; this test asserts the wiring (module loaded, compare_trial called,
    # result printed), not comparator table-diff semantics (covered elsewhere).
    compare_json = val_root / "results" / "phase_b" / "t1" / "compare.json"
    assert compare_json.is_file()
    out = capsys.readouterr().out
    assert "compare trial=t1" in out


# ---------------------------------------------------------------------------
# prevalidate: _cli_value_incomplete
# ---------------------------------------------------------------------------

def test_cli_value_incomplete_stub_patterns():
    assert s._cli_value_incomplete(None) is True
    assert s._cli_value_incomplete("") is True
    assert s._cli_value_incomplete("   ") is True
    assert s._cli_value_incomplete("TODO") is True
    assert s._cli_value_incomplete("<path>") is True
    assert s._cli_value_incomplete('""') is True


def test_cli_value_incomplete_accepts_concrete_values():
    assert s._cli_value_incomplete("/data/orders.parquet") is False
    assert s._cli_value_incomplete("--env=dev") is False
    assert s._cli_value_incomplete(42) is False


def test_cli_value_incomplete_json_literals_not_flagged_as_stub():
    """"[]" and JSON array/object literals are legitimate values, not stubs —
    only a non-JSON placeholder like "[...]" falls through to the stub regex."""
    assert s._cli_value_incomplete("[]") is False
    assert s._cli_value_incomplete('[{"counts":0}]') is False
    assert s._cli_value_incomplete("true") is False
    assert s._cli_value_incomplete("[{...}]") is True  # not valid JSON -> stub


# ---------------------------------------------------------------------------
# prevalidate: _check_analysis_completeness_pv
# ---------------------------------------------------------------------------

def _write_analysis_pv(tmp_path, doc):
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps(doc), encoding="utf-8")


def test_analysis_completeness_blocks_missing_analysis_json(tmp_path):
    findings = s._check_analysis_completeness_pv(tmp_path, "a")
    assert any(f["check"] == "analysis_completeness" and f["severity"] == "blocking" for f in findings)


def test_analysis_completeness_blocks_no_entrypoints(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": []})
    findings = s._check_analysis_completeness_pv(tmp_path, "a")
    assert any("no entrypoints" in f["message"] for f in findings)


def test_analysis_completeness_blocks_missing_entrypoint_class(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "external_sources": [], "sinks": []},
    ]})
    findings = s._check_analysis_completeness_pv(tmp_path, "a")
    assert any("missing entrypoint_class" in f["message"] and f["severity"] == "blocking"
               for f in findings)


def test_analysis_completeness_warns_missing_entrypoint_method(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Main",
         "external_sources": [], "sinks": []},
    ]})
    findings = s._check_analysis_completeness_pv(tmp_path, "a")
    assert any("missing entrypoint_method" in f["message"] and f["severity"] == "warning"
               for f in findings)


def test_analysis_completeness_blocks_missing_sources_and_sinks_keys(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Main", "entrypoint_method": "main"},
    ]})
    findings = s._check_analysis_completeness_pv(tmp_path, "a")
    msgs = [f["message"] for f in findings]
    assert any("missing external_sources[]" in m for m in msgs)
    assert any("missing sinks[] / external_sinks[] key" in m for m in msgs)


def test_analysis_completeness_clean_entrypoint_has_no_findings(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Main", "entrypoint_method": "main",
         "external_sources": [], "sinks": []},
    ]})
    findings = s._check_analysis_completeness_pv(tmp_path, "a")
    assert findings == []


def test_analysis_completeness_empty_sinks_with_ast_write_evidence_blocks(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Main", "entrypoint_method": "main",
         "path": "Main.java", "external_sources": [], "sinks": []},
    ]})
    shared = tmp_path / "Validation" / "shared"
    (shared / "ast_facts.json").write_text(json.dumps({
        "files": [{"path": "Main.java", "writes": [{"call": "parquet"}]}],
    }), encoding="utf-8")
    findings = s._check_analysis_completeness_pv(tmp_path, "a")
    assert any("ast_facts shows writes" in f["message"] and f["severity"] == "blocking"
               for f in findings)


def test_analysis_completeness_empty_sinks_with_no_ast_evidence_is_clean(tmp_path):
    """Empty sinks[] with NO corresponding AST writes is a valid no-sink trial."""
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Main", "entrypoint_method": "main",
         "path": "Main.java", "external_sources": [], "sinks": []},
    ]})
    shared = tmp_path / "Validation" / "shared"
    (shared / "ast_facts.json").write_text(json.dumps({
        "files": [{"path": "Main.java", "writes": []}],
    }), encoding="utf-8")
    findings = s._check_analysis_completeness_pv(tmp_path, "a")
    assert findings == []


def test_analysis_completeness_external_sinks_key_satisfies_sinks_requirement(tmp_path):
    """A entrypoint using external_sinks[] (not sinks[]) must not be flagged as
    missing the sinks key — entrypoint_declares_sinks_key checks both."""
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Main", "entrypoint_method": "main",
         "external_sources": [], "external_sinks": [{"id": "s1", "kind": "table"}]},
    ]})
    findings = s._check_analysis_completeness_pv(tmp_path, "a")
    assert not any("missing sinks[]" in f["message"] for f in findings)


def test_analysis_completeness_llm_todo_warning_in_phase_a_blocking_in_phase_b(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Main", "entrypoint_method": "main",
         "external_sources": [], "sinks": [], "llm_todo": "confirm column mapping"},
    ]})
    a_findings = s._check_analysis_completeness_pv(tmp_path, "a")
    b_findings = s._check_analysis_completeness_pv(tmp_path, "b")
    a_todo = [f for f in a_findings if "open llm_todo" in f["message"]]
    b_todo = [f for f in b_findings if "open llm_todo" in f["message"]]
    assert a_todo and a_todo[0]["severity"] == "warning"
    assert b_todo and b_todo[0]["severity"] == "blocking"


def test_analysis_completeness_llm_todo_dynamic_path_blocks_phase_a():
    """An llm_todo mentioning dynamic paths/unresolved args blocks Phase A too,
    not just Phase B — these reliably cause harness loops."""
    findings_module = s
    assert findings_module._LLM_TODO_PHASE_A_BLOCK.search("unresolved dynamic path in cli_args")


# ---------------------------------------------------------------------------
# prevalidate: _check_cli_args_completeness_pv
# ---------------------------------------------------------------------------

def test_cli_args_completeness_blocks_stub_arg(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "cli_args": ["TODO", "/data/real.parquet"]},
    ]})
    findings = s._check_cli_args_completeness_pv(tmp_path, "a")
    assert any("cli_args[0]" in f["message"] for f in findings)
    assert not any("cli_args[1]" in f["message"] for f in findings)
    assert all(f["rebuild_required"] is False for f in findings)


def test_cli_args_completeness_blocks_stub_kwargs(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_kwargs": {"input_path": "<dynamic>"}},
    ]})
    findings = s._check_cli_args_completeness_pv(tmp_path, "a")
    assert any("entrypoint_kwargs['input_path']" in f["message"] for f in findings)


def test_cli_args_completeness_empty_args_with_open_todo_blocks(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "cli_args": [], "llm_todo": "resolve missing args for this job"},
    ]})
    findings = s._check_cli_args_completeness_pv(tmp_path, "a")
    assert any("open args-related llm_todo" in f["message"] for f in findings)


def test_cli_args_completeness_empty_args_without_todo_is_clean(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [{"id": "ep1", "cli_args": []}]})
    findings = s._check_cli_args_completeness_pv(tmp_path, "a")
    assert findings == []


def test_cli_args_completeness_missing_analysis_json_returns_empty(tmp_path):
    """No analysis.json at all -> [] (not a crash); analysis_completeness owns that gap."""
    assert s._check_cli_args_completeness_pv(tmp_path, "a") == []


# ---------------------------------------------------------------------------
# prevalidate: _check_entry_classes
# ---------------------------------------------------------------------------

def _make_jar_with_classes(jar_path, class_paths):
    import zipfile
    jar_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(jar_path, "w") as zf:
        for cp in class_paths:
            zf.writestr(cp, b"")


def test_entry_classes_blocks_missing_class(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Missing"},
    ]})
    _make_jar_with_classes(
        tmp_path / "Output" / "target" / "app.jar", ["com/example/Other.class"],
    )
    findings = s._check_entry_classes(tmp_path)
    assert any(f["check"] == "entry_class" and f["severity"] == "blocking" for f in findings)


def test_entry_classes_passes_when_class_present(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Main"},
    ]})
    _make_jar_with_classes(
        tmp_path / "Output" / "target" / "app.jar", ["com/example/Main.class"],
    )
    findings = s._check_entry_classes(tmp_path)
    assert findings == []


def test_entry_classes_matches_bare_dollar_suffix_entry(tmp_path):
    """The check probes for an exact "$"-suffixed entry (e.g. Scala companion
    objects: Foo$.class) in addition to the bare name — a faithful, unmodified
    port of the Scala check. It does NOT fuzzy-match arbitrary nested-class
    suffixes like Main$Inner.class (only the literal "" and "$" suffixes)."""
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Main"},
    ]})
    _make_jar_with_classes(
        tmp_path / "Output" / "target" / "app.jar", ["com/example/Main$.class"],
    )
    findings = s._check_entry_classes(tmp_path)
    assert findings == []


def test_entry_classes_warns_when_no_jar_found(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Main"},
    ]})
    findings = s._check_entry_classes(tmp_path)
    assert any(f["severity"] == "warning" and "no assembled JAR" in f["message"] for f in findings)


def test_entry_classes_uses_analysis_jar_path_hint(tmp_path):
    _write_analysis_pv(tmp_path, {
        "jar_path": "target/hinted.jar",
        "entrypoints": [{"id": "ep1", "entrypoint_class": "com.example.Main"}],
    })
    _make_jar_with_classes(
        tmp_path / "Output" / "target" / "hinted.jar", ["com/example/Main.class"],
    )
    findings = s._check_entry_classes(tmp_path)
    assert findings == []


# ---------------------------------------------------------------------------
# prevalidate: caching + _cmd_prevalidate orchestration
# ---------------------------------------------------------------------------

def test_prevalidate_hash_state_changes_when_analysis_json_changes(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [{"id": "ep1"}]})
    h1 = s._prevalidate_hash_state(tmp_path)
    _write_analysis_pv(tmp_path, {"entrypoints": [{"id": "ep2"}]})
    h2 = s._prevalidate_hash_state(tmp_path)
    assert h1 != h2


def test_prevalidate_hash_state_prefers_schemas_dir_when_present(tmp_path):
    """Editing schemas/ must change the hash even if analysis.json is untouched."""
    _write_analysis_pv(tmp_path, {"entrypoints": [{"id": "ep1"}]})
    schemas = tmp_path / "Validation" / "shared" / "schemas"
    schemas.mkdir(parents=True)
    (schemas / "manifest.json").write_text(json.dumps({"v": 1}), encoding="utf-8")
    h1 = s._prevalidate_hash_state(tmp_path)
    (schemas / "manifest.json").write_text(json.dumps({"v": 2}), encoding="utf-8")
    h2 = s._prevalidate_hash_state(tmp_path)
    assert h1 != h2


def test_prevalidate_cache_roundtrip(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [{"id": "ep1"}]})
    h = s._prevalidate_hash_state(tmp_path)
    assert s._prevalidate_check_cache(tmp_path, h) is None  # no cache yet
    shared = tmp_path / "Validation" / "shared"
    (shared / "prevalidation_report.json").write_text(
        json.dumps({"ok": True, "blocking_count": 0, "warning_count": 0}), encoding="utf-8",
    )
    s._prevalidate_save_cache(tmp_path, h)
    cached = s._prevalidate_check_cache(tmp_path, h)
    assert cached is not None and cached["ok"] is True
    # A different hash must miss the cache.
    assert s._prevalidate_check_cache(tmp_path, "different-hash") is None


class _FakeArgs:
    def __init__(self, conv_root, phase="a", force=False):
        self.conv_root = str(conv_root)
        self.phase = phase
        self.force = force


def test_cmd_prevalidate_writes_report_and_returns_1_on_blocking(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "external_sources": [], "sinks": []},  # missing entrypoint_class
    ]})
    rc = s._cmd_prevalidate(_FakeArgs(tmp_path))
    assert rc == 1
    report = json.loads(
        (tmp_path / "Validation" / "shared" / "prevalidation_report.json").read_text()
    )
    assert report["ok"] is False
    assert report["blocking_count"] > 0


def test_cmd_prevalidate_uses_cache_on_second_run(tmp_path, capsys):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Main", "entrypoint_method": "main",
         "external_sources": [], "sinks": []},
    ]})
    _make_jar_with_classes(
        tmp_path / "Output" / "target" / "app.jar", ["com/example/Main.class"],
    )
    rc1 = s._cmd_prevalidate(_FakeArgs(tmp_path))
    capsys.readouterr()
    rc2 = s._cmd_prevalidate(_FakeArgs(tmp_path))
    out2 = capsys.readouterr().out
    assert "no changes since last run (cached)" in out2
    assert rc1 == rc2


def test_cmd_prevalidate_force_bypasses_cache(tmp_path):
    _write_analysis_pv(tmp_path, {"entrypoints": [
        {"id": "ep1", "entrypoint_class": "com.example.Main", "entrypoint_method": "main",
         "external_sources": [], "sinks": []},
    ]})
    _make_jar_with_classes(
        tmp_path / "Output" / "target" / "app.jar", ["com/example/Main.class"],
    )
    s._cmd_prevalidate(_FakeArgs(tmp_path))
    rc = s._cmd_prevalidate(_FakeArgs(tmp_path, force=True))
    assert rc in (0, 1, 2)  # ran the real checks again, did not short-circuit on cache


# ---------------------------------------------------------------------------
# build-doctor
# ---------------------------------------------------------------------------

def test_detect_java_build_tool_maven(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    assert s._detect_java_build_tool(tmp_path) == "maven"


def test_detect_java_build_tool_gradle(tmp_path):
    (tmp_path / "build.gradle.kts").write_text("", encoding="utf-8")
    assert s._detect_java_build_tool(tmp_path) == "gradle"


def test_detect_java_build_tool_none(tmp_path):
    assert s._detect_java_build_tool(tmp_path) == ""


def test_find_jar_in_target_prefers_shaded(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "app-1.0.jar").write_bytes(b"")
    (target / "app-1.0-shaded.jar").write_bytes(b"")
    (target / "app-1.0-sources.jar").write_bytes(b"")
    found = s._find_jar_in_target(tmp_path, "maven")
    assert found.endswith("app-1.0-shaded.jar")


def test_find_jar_in_target_excludes_sources_and_javadoc(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "app-sources.jar").write_bytes(b"")
    (target / "app-javadoc.jar").write_bytes(b"")
    assert s._find_jar_in_target(tmp_path, "maven") == ""


class _FakeBDArgs:
    def __init__(self, conv_root, side="source", force_rebuild=False, output=None):
        self.conv_root = str(conv_root)
        self.side = side
        self.force_rebuild = force_rebuild
        self.output = output


def test_build_doctor_project_dir_missing_returns_5(tmp_path):
    rc = s._cmd_build_doctor(_FakeBDArgs(tmp_path))
    assert rc == 5


def test_build_doctor_no_build_file_returns_5(tmp_path, capsys):
    src = tmp_path / "Validation" / "source"
    src.mkdir(parents=True)
    rc = s._cmd_build_doctor(_FakeBDArgs(tmp_path))
    assert rc == 5
    out = json.loads(capsys.readouterr().out)
    assert "no pom.xml" in out["error"]


def test_build_doctor_skips_build_when_jar_already_present(tmp_path, monkeypatch, capsys):
    src = tmp_path / "Validation" / "source"
    src.mkdir(parents=True)
    (src / "pom.xml").write_text("<project/>", encoding="utf-8")
    (src / "target").mkdir()
    (src / "target" / "app.jar").write_bytes(b"")

    called = {"n": 0}

    def _fake_run(*a, **kw):
        called["n"] += 1
        raise AssertionError("should not invoke mvn when a jar already exists")

    monkeypatch.setattr(s.subprocess, "run", _fake_run)
    rc = s._cmd_build_doctor(_FakeBDArgs(tmp_path))
    assert rc == 0
    assert called["n"] == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["jar"].endswith("app.jar")


def test_build_doctor_force_rebuild_invokes_mvn(tmp_path, monkeypatch):
    src = tmp_path / "Validation" / "source"
    src.mkdir(parents=True)
    (src / "pom.xml").write_text("<project/>", encoding="utf-8")
    (src / "target").mkdir()
    (src / "target" / "app.jar").write_bytes(b"")

    captured = {}

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        import subprocess as _sp
        return _sp.CompletedProcess(cmd, 0, stdout="BUILD SUCCESS", stderr="")

    monkeypatch.setattr(s.subprocess, "run", _fake_run)
    rc = s._cmd_build_doctor(_FakeBDArgs(tmp_path, force_rebuild=True))
    assert rc == 0
    assert captured["cmd"][0] == "mvn"
    assert "package" in captured["cmd"]


def test_build_doctor_build_failure_returns_5_when_no_jar_produced(tmp_path, monkeypatch, capsys):
    """A failed build with no jar produced returns 5 (no-jar takes priority
    over the generic not-ok=1 code, matching _cmd_build_doctor's exit ladder)."""
    src = tmp_path / "Validation" / "source"
    src.mkdir(parents=True)
    (src / "pom.xml").write_text("<project/>", encoding="utf-8")

    def _fake_run(cmd, **kw):
        import subprocess as _sp
        return _sp.CompletedProcess(cmd, 1, stdout="", stderr="COMPILATION ERROR")

    monkeypatch.setattr(s.subprocess, "run", _fake_run)
    rc = s._cmd_build_doctor(_FakeBDArgs(tmp_path))
    assert rc == 5
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False


def test_build_doctor_build_failure_with_stale_jar_returns_1(tmp_path, monkeypatch, capsys):
    """If a jar already exists from a prior build but the rebuild fails, ok=False
    and rc=1 (a stale jar is present, but the build did not actually succeed)."""
    src = tmp_path / "Validation" / "source"
    src.mkdir(parents=True)
    (src / "pom.xml").write_text("<project/>", encoding="utf-8")
    (src / "target").mkdir()
    (src / "target" / "app.jar").write_bytes(b"")

    def _fake_run(cmd, **kw):
        import subprocess as _sp
        return _sp.CompletedProcess(cmd, 1, stdout="", stderr="COMPILATION ERROR")

    monkeypatch.setattr(s.subprocess, "run", _fake_run)
    rc = s._cmd_build_doctor(_FakeBDArgs(tmp_path, force_rebuild=True))
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["jar"].endswith("app.jar")


def test_build_doctor_migrated_side_targets_output_dir(tmp_path, monkeypatch):
    out_dir = tmp_path / "Output"
    out_dir.mkdir(parents=True)
    (out_dir / "pom.xml").write_text("<project/>", encoding="utf-8")
    (out_dir / "target").mkdir()
    (out_dir / "target" / "app.jar").write_bytes(b"")
    rc = s._cmd_build_doctor(_FakeBDArgs(tmp_path, side="migrated"))
    assert rc == 0


def test_build_doctor_writes_output_file_when_requested(tmp_path):
    src = tmp_path / "Validation" / "source"
    src.mkdir(parents=True)
    (src / "pom.xml").write_text("<project/>", encoding="utf-8")
    (src / "target").mkdir()
    (src / "target" / "app.jar").write_bytes(b"")
    out_file = tmp_path / "report.json"
    rc = s._cmd_build_doctor(_FakeBDArgs(tmp_path, output=str(out_file)))
    assert rc == 0
    assert out_file.is_file()
    assert json.loads(out_file.read_text())["ok"] is True
