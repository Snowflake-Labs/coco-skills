"""Tests for scos_state.py batch-orchestration additions:
prepare-batches, consolidate, scope-entrypoints, and worktree helpers.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import scos_state as s  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_analysis(tmp_path: Path, eps: list[dict] | None = None) -> Path:
    """Write a minimal analysis.json with given entrypoints."""
    data = {
        "entrypoints": eps or [],
        "entrypoint_candidates": eps or [],
    }
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps(data))
    return tmp_path


def _make_state(tmp_path: Path, **kwargs: Any) -> dict:
    """Write a minimal state.json."""
    state = {
        "schema_version": s.SCHEMA_VERSION, "run_id": "abc123",
        "phase": "init",
        "config": {"connection_name": "c", "project_slug": "tst", "database": "DB"},
        "milestones": {m: False for m in s.CANONICAL_MILESTONES},
        "trials": {}, "phase_a": {"iter": 0}, "phase_b": {"iter": 0},
        "synth_warnings": [],
        "git": {"original_branch": "main", "validation_branch": "validation/abc123", "harvested": False},
        "snowflake": {"database": "DB", "schema": "TST_ABC123", "provisioned": False,
                      "provisioned_tables": [], "stage": "DB.TST_ABC123.SCOS_TEST_STAGE", "stage_prefix": "abc123"},
        "paths": {"skill_dir": "", "original_source": str(tmp_path / "src"), "conv_root": str(tmp_path)},
    }
    state.update(kwargs)
    s.save_state(tmp_path, state)
    return state


def _fake_git_ok(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# _ensure_worktree_skeleton
# ---------------------------------------------------------------------------


def test_ensure_worktree_skeleton_creates_dirs(tmp_path):
    s._ensure_worktree_skeleton(tmp_path)
    for d in s._WORKTREE_VALIDATION_SUBDIRS:
        assert (tmp_path / "Validation" / d).is_dir(), f"Missing dir: {d}"


def test_ensure_worktree_skeleton_idempotent(tmp_path):
    s._ensure_worktree_skeleton(tmp_path)
    s._ensure_worktree_skeleton(tmp_path)  # no error on second call


# ---------------------------------------------------------------------------
# _exclude_worktrees_from_gitignore
# ---------------------------------------------------------------------------


def test_exclude_worktrees_adds_entry(tmp_path):
    s._exclude_worktrees_from_gitignore(tmp_path)
    assert "Validation/worktrees/" in (tmp_path / ".gitignore").read_text()


def test_exclude_worktrees_idempotent(tmp_path):
    s._exclude_worktrees_from_gitignore(tmp_path)
    s._exclude_worktrees_from_gitignore(tmp_path)
    content = (tmp_path / ".gitignore").read_text()
    assert content.count("Validation/worktrees/") == 1


def test_exclude_worktrees_preserves_existing(tmp_path):
    (tmp_path / ".gitignore").write_text("target/\n")
    s._exclude_worktrees_from_gitignore(tmp_path)
    content = (tmp_path / ".gitignore").read_text()
    assert "target/" in content
    assert "Validation/worktrees/" in content


def test_exclude_worktrees_never_raises_on_readonly(tmp_path, monkeypatch):
    # Even if the write fails, it should swallow the exception
    monkeypatch.setattr(Path, "write_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("no write")))
    # Should not raise
    s._exclude_worktrees_from_gitignore(tmp_path)


# ---------------------------------------------------------------------------
# _select_eps_for_worktree
# ---------------------------------------------------------------------------


def test_select_eps_for_worktree_scopes_analysis_and_registers_trials(tmp_path):
    eps = [{"id": "ep1", "name": "EP1"}, {"id": "ep2", "name": "EP2"}, {"id": "ep3", "name": "EP3"}]
    primary_analysis = {"entrypoints": eps, "entrypoint_candidates": eps, "meta": "x"}
    s._ensure_worktree_skeleton(tmp_path)
    _make_state(tmp_path)

    s._select_eps_for_worktree(tmp_path, primary_analysis, ["ep1", "ep3"])

    analysis = json.loads((s.analysis_path(tmp_path)).read_text())
    assert {e["id"] for e in analysis["entrypoints"]} == {"ep1", "ep3"}
    assert {e["id"] for e in analysis["entrypoint_candidates"]} == {"ep1", "ep3"}
    assert analysis["meta"] == "x"  # other keys preserved

    state = s.load_state(tmp_path)
    assert set(state["trials"].keys()) == {"ep1", "ep3"}
    assert state["milestones"]["entrypoints_selected"] is True


def test_select_eps_for_worktree_removes_stale_trials(tmp_path):
    eps = [{"id": "ep1"}, {"id": "ep2"}]
    s._ensure_worktree_skeleton(tmp_path)
    state = _make_state(tmp_path)
    state["trials"] = {"ep1": {"status": "passed"}, "stale": {"status": "pending"}}
    s.save_state(tmp_path, state)

    s._select_eps_for_worktree(tmp_path, {"entrypoints": eps, "entrypoint_candidates": eps}, ["ep1"])

    state = s.load_state(tmp_path)
    assert "stale" not in state["trials"]
    assert "ep1" in state["trials"]


# ---------------------------------------------------------------------------
# _cmd_scope_entrypoints
# ---------------------------------------------------------------------------


def test_scope_entrypoints_filters_analysis(tmp_path):
    eps = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    _make_analysis(tmp_path, eps)
    args = SimpleNamespace(conv_root=str(tmp_path), ids="a,c")
    rc = s._cmd_scope_entrypoints(args)
    assert rc == 0
    analysis = s.load_analysis(tmp_path)
    assert {e["id"] for e in analysis["entrypoints"]} == {"a", "c"}
    assert {e["id"] for e in analysis["entrypoint_candidates"]} == {"a", "c"}


def test_scope_entrypoints_rejects_unknown_ids(tmp_path):
    _make_analysis(tmp_path, [{"id": "a"}])
    args = SimpleNamespace(conv_root=str(tmp_path), ids="a,z")
    rc = s._cmd_scope_entrypoints(args)
    assert rc == 2
    # analysis unchanged
    assert {e["id"] for e in s.load_analysis(tmp_path)["entrypoints"]} == {"a"}


def test_scope_entrypoints_empty_ids_fails(tmp_path):
    _make_analysis(tmp_path, [{"id": "a"}])
    rc = s._cmd_scope_entrypoints(SimpleNamespace(conv_root=str(tmp_path), ids=""))
    assert rc == 2


def test_scope_entrypoints_no_candidates_fails(tmp_path):
    _make_analysis(tmp_path, [])
    rc = s._cmd_scope_entrypoints(SimpleNamespace(conv_root=str(tmp_path), ids="a"))
    assert rc == 2


# ---------------------------------------------------------------------------
# _cmd_prepare_batches (mocked git + batch.py)
# ---------------------------------------------------------------------------


def _make_sections(path: Path, sections: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sections))


def _make_orig_source(tmp_path: Path) -> Path:
    orig = tmp_path / "orig_src"
    orig.mkdir()
    (orig / "Main.scala").write_text("object Main { def main(args: Array[String]): Unit = {} }")
    return orig


def test_prepare_batches_creates_batches_prepared_json(tmp_path, monkeypatch):
    """prepare-batches plans 2 batches, creates worktrees, writes batches_prepared.json."""
    orig = _make_orig_source(tmp_path)
    conv = tmp_path / "conv"
    conv.mkdir()
    (conv / "Output").mkdir()
    (conv / "Output" / "Main.scala").write_text("object Main {}")
    eps = [{"id": "ep1", "weight": 5}, {"id": "ep2", "weight": 5}]
    _make_analysis(conv, eps)

    sections_path = conv / "Validation" / "shared" / "sections.json"
    _make_sections(sections_path, [
        {"section_id": "s1", "section_name": "S1", "ep_ids": ["ep1"]},
        {"section_id": "s2", "section_name": "S2", "ep_ids": ["ep2"]},
    ])

    # Mock _run_git: worktree add succeeds, branch/commit ops succeed
    def fake_run_git(cwd, *args):
        stdout = ""
        if "rev-parse" in args:
            stdout = "main"
        elif "branch" in args and "--list" in args:
            stdout = ""
        return _fake_git_ok(0, stdout)

    monkeypatch.setattr(s, "_run_git", fake_run_git)
    # Mock _git_commit_paths to avoid actual git commits
    monkeypatch.setattr(s, "_git_commit_paths", lambda *a, **kw: "deadbeef")
    monkeypatch.setattr(s, "_ensure_gitignore", lambda *a: None)

    args = SimpleNamespace(
        conv_root=str(conv), sections=str(sections_path),
        original_source=str(orig), connection="c", database="DB",
        project_slug="tst", base_sha="abc1234",
        max_entrypoints=1, max_weight=40, force=False,  # max_entrypoints=1 forces 2 batches
    )
    rc = s._cmd_prepare_batches(args)
    assert rc == 0

    bp_path = conv / "Validation" / "shared" / "batches_prepared.json"
    assert bp_path.is_file()
    bp = json.loads(bp_path.read_text())
    assert bp["base_sha"] == "abc1234"
    assert len(bp["batches"]) == 2
    for b in bp["batches"]:
        assert b["error"] is None
        assert b["worktree"].endswith(b["batch_id"])


def test_prepare_batches_coverage_check_fails_on_missing_ep(tmp_path, monkeypatch):
    """Coverage check should fail (exit 3) when sections don't cover all eps."""
    orig = _make_orig_source(tmp_path)
    conv = tmp_path / "conv"
    conv.mkdir()
    (conv / "Output").mkdir()
    (conv / "Output" / "Main.scala").write_text("")
    eps = [{"id": "ep1"}, {"id": "ep2"}]
    _make_analysis(conv, eps)

    sections_path = conv / "Validation" / "shared" / "sections.json"
    _make_sections(sections_path, [
        {"section_id": "s1", "section_name": "S1", "ep_ids": ["ep1"]},
        # ep2 missing from sections
    ])

    args = SimpleNamespace(
        conv_root=str(conv), sections=str(sections_path),
        original_source=str(orig), connection="c", database="DB",
        project_slug=None, base_sha="abc", max_entrypoints=8, max_weight=40, force=False,
    )
    rc = s._cmd_prepare_batches(args)
    assert rc == 3  # coverage check failure


def test_prepare_batches_missing_sections_file_fails(tmp_path):
    args = SimpleNamespace(
        conv_root=str(tmp_path), sections=str(tmp_path / "nope.json"),
        original_source=str(tmp_path), connection="c", database="DB",
        project_slug=None, base_sha="abc", max_entrypoints=8, max_weight=40, force=False,
    )
    rc = s._cmd_prepare_batches(args)
    assert rc == 2


def test_prepare_batches_no_entrypoints_fails(tmp_path):
    _make_analysis(tmp_path, [])
    sections_path = tmp_path / "sec.json"
    sections_path.write_text("[]")
    args = SimpleNamespace(
        conv_root=str(tmp_path), sections=str(sections_path),
        original_source=str(tmp_path), connection="c", database="DB",
        project_slug=None, base_sha="abc", max_entrypoints=8, max_weight=40, force=False,
    )
    rc = s._cmd_prepare_batches(args)
    assert rc == 2


# ---------------------------------------------------------------------------
# _cmd_consolidate
# ---------------------------------------------------------------------------


def _git_cherry_pick_responses(scenario: str):
    """Return a fake _run_git for consolidate tests."""
    def fake(cwd, *args):
        cmd = list(args)
        if "cherry" == cmd[1]:  # git cherry HEAD branch base
            # Return "+" for every SHA in the branch (not yet applied)
            return _fake_git_ok(0, "+ deadbeef\n+ cafecafe\n")
        if "log" in cmd:
            return _fake_git_ok(0, "deadbeef\ncafecafe\n")
        if "cherry-pick" in cmd and scenario == "clean":
            return _fake_git_ok(0)
        if "cherry-pick" in cmd and scenario == "conflict":
            return _fake_git_ok(1, "", "CONFLICT")
        if "cherry-pick" in cmd and scenario == "locked":
            return _fake_git_ok(128, "", "fatal: index.lock exists")
        if "cherry-pick" in cmd and "--abort" in cmd:
            return _fake_git_ok(0)
        if "branch" in cmd and "--list" in cmd:
            return _fake_git_ok(0, "  validation/abc1\n")
        return _fake_git_ok(0)
    return fake


def test_consolidate_abort(tmp_path, monkeypatch):
    calls = []
    def fake_git(cwd, *args):
        calls.append(list(args))
        return _fake_git_ok(0)
    monkeypatch.setattr(s, "_run_git", fake_git)
    args = SimpleNamespace(conv_root=str(tmp_path), abort=True, continue_=False,
                            base_sha="base", branches=None)
    rc = s._cmd_consolidate(args)
    assert rc == 0
    assert any("--abort" in c for c in calls)


def test_consolidate_nothing_to_pick(tmp_path, monkeypatch):
    def fake_git(cwd, *args):
        if "log" in args:
            return _fake_git_ok(0, "")  # no migration-fix commits
        if "cherry" == args[1]:
            return _fake_git_ok(0, "")
        if "branch" in args and "--list" in args:
            return _fake_git_ok(0, "  validation/abc1\n")
        return _fake_git_ok(0)
    monkeypatch.setattr(s, "_run_git", fake_git)
    monkeypatch.setattr(s, "_assert_fix_commits_clean", lambda *a: None)
    args = SimpleNamespace(conv_root=str(tmp_path), abort=False, continue_=False,
                            base_sha="base", branches=None)
    rc = s._cmd_consolidate(args)
    assert rc == 0


def test_consolidate_clean_pick(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_run_git", _git_cherry_pick_responses("clean"))
    monkeypatch.setattr(s, "_assert_fix_commits_clean", lambda *a: None)
    monkeypatch.setattr(s, "_advance_cherry_pick", lambda *a: True)
    args = SimpleNamespace(conv_root=str(tmp_path), abort=False, continue_=False,
                            base_sha="base", branches="validation/abc1")
    rc = s._cmd_consolidate(args)
    assert rc == 0


def test_consolidate_git_busy_returns_exit6(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_run_git", _git_cherry_pick_responses("locked"))
    monkeypatch.setattr(s, "_assert_fix_commits_clean", lambda *a: None)
    args = SimpleNamespace(conv_root=str(tmp_path), abort=False, continue_=False,
                            base_sha="base", branches="validation/abc1")
    rc = s._cmd_consolidate(args)
    assert rc == 6


def test_consolidate_conflict_returns_exit5(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_run_git", _git_cherry_pick_responses("conflict"))
    monkeypatch.setattr(s, "_assert_fix_commits_clean", lambda *a: None)
    monkeypatch.setattr(s, "_advance_cherry_pick", lambda *a: False)
    monkeypatch.setattr(s, "_cherry_pick_in_progress", lambda *a: True)
    monkeypatch.setattr(s, "_print_harvest_conflicts", lambda *a: None)
    args = SimpleNamespace(conv_root=str(tmp_path), abort=False, continue_=False,
                            base_sha="base", branches="validation/abc1")
    rc = s._cmd_consolidate(args)
    assert rc == 5


def test_consolidate_continue_when_no_cherry_pick_in_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_cherry_pick_in_progress", lambda *a: False)
    monkeypatch.setattr(s, "_run_git", lambda *a: _fake_git_ok(0))
    args = SimpleNamespace(conv_root=str(tmp_path), abort=False, continue_=True,
                            base_sha="base", branches=None)
    rc = s._cmd_consolidate(args)
    assert rc == 0


def test_consolidate_explicit_branches_only(tmp_path, monkeypatch):
    """--branches should only collect from specified branches, not all validation/*."""
    seen_log_calls: list[str] = []
    def fake_git(cwd, *args):
        if "log" in args:
            seen_log_calls.append(str(args))
            return _fake_git_ok(0, "")
        if "cherry" == args[1]:
            return _fake_git_ok(0, "")
        return _fake_git_ok(0)
    monkeypatch.setattr(s, "_run_git", fake_git)
    monkeypatch.setattr(s, "_assert_fix_commits_clean", lambda *a: None)
    args = SimpleNamespace(conv_root=str(tmp_path), abort=False, continue_=False,
                            base_sha="base", branches="validation/x1,validation/x2")
    rc = s._cmd_consolidate(args)
    assert rc == 0
    assert len(seen_log_calls) == 2  # exactly 2 branches queried


# ---------------------------------------------------------------------------
# CLI round-trip: scope-entrypoints
# ---------------------------------------------------------------------------


def test_cli_scope_entrypoints_roundtrip(tmp_path):
    eps = [{"id": "a"}, {"id": "b"}]
    _make_analysis(tmp_path, eps)
    rc = s.main(["scope-entrypoints", "--conv-root", str(tmp_path), "--ids", "a"])
    assert rc == 0
    assert len(s.load_analysis(tmp_path)["entrypoints"]) == 1
    assert s.load_analysis(tmp_path)["entrypoints"][0]["id"] == "a"


# ---------------------------------------------------------------------------
# _derive_phase parity: batch.py's _derive_phase reads Scala state.json
# ---------------------------------------------------------------------------


def test_derive_phase_reads_scala_state_json(tmp_path, monkeypatch):
    """batch.py._derive_phase must not crash on Scala state.json and must handle terminal phases."""
    pyspark_scripts = (
        Path(__file__).resolve().parents[3]
        / "validate-pyspark-to-snowpark-connect" / "scripts"
    )
    if not pyspark_scripts.is_dir():
        pytest.skip("PySpark validator scripts not found; skipping derive_phase parity test")

    if str(pyspark_scripts) not in sys.path:
        sys.path.insert(0, str(pyspark_scripts))
    import batch  # type: ignore[import]

    state_path = tmp_path / "Validation" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    # Verify _derive_phase returns a string for every valid Scala phase (no crash)
    for phase in ["init", "phase_a_done", "phase_b_done"]:
        state = {"phase": phase, "trials": {}}
        derived = batch._derive_phase(state)
        assert isinstance(derived, str), f"_derive_phase returned non-string for phase={phase!r}"

    # Verify terminal: phase_b_done with all-passed trials → something distinct from pre-B phases
    state_init = {"phase": "init", "trials": {}}
    state_done = {
        "phase": "phase_b_done",
        "trials": {"ep1": {"status": "passed", "phase_a_iters": [], "phase_b_iters": []}},
    }
    derived_init = batch._derive_phase(state_init)
    derived_done = batch._derive_phase(state_done)
    # The two phases must map to different labels (batch.py can distinguish them)
    assert derived_init != derived_done, (
        f"_derive_phase cannot distinguish init ({derived_init!r}) from "
        f"phase_b_done ({derived_done!r}) — Scala state.json schema may have drifted"
    )

    # Pool labels use shared PySpark milestones only (batch.py unchanged).
    # Scala-specific readiness flags (venv_prewarmed / tests_authored /
    # snowflake_provisioned) are ignored by _derive_phase; scos_state flips
    # phase_a_complete / phase_b_complete so pool labels stay accurate.
    assert batch._derive_phase({
        "phase": "init",
        "milestones": {"venv_prewarmed": True, "tests_authored": True},
        "trials": {},
    }) == "starting"  # Scala-only milestones are not in batch.py's table
    assert batch._derive_phase({
        "phase": "init",
        "milestones": {"patches_authored": True},
        "trials": {},
    }) == "Phase A"
    assert batch._derive_phase({
        "phase": "phase_a_done",
        "milestones": {"phase_a_complete": True},
        "trials": {"ep1": {"status": "pending", "phase_a_iters": [{"iter": 1}]}},
    }).startswith("Phase B")
    assert batch._derive_phase({
        "phase": "init",
        "milestones": {"phase_b_complete": True},
        "trials": {},
    }) == "Phase B complete"
