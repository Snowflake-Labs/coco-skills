"""Tests for revert_failing_java_files.py — the Phase 2b compilation gate.

Covers the tokenizer fallback (`_check_with_fallback`), the javac cmd builder,
the RDD quarantine marker check, and the batch-first sweep control flow
(`_run_sweep`). Sweep tests monkeypatch javac and git helpers so they run
without a JVM or a git repo, asserting:
  - batch pass  -> no per-file checks, no reverts
  - batch fail  -> per-file attribution + revert of exactly the failing files
  - no javac    -> tokenizer fallback path
  - SPRKCNTSCL1500 marker -> quarantine, NOT counted as failure
"""

from __future__ import annotations

from pathlib import Path

import pytest

import revert_failing_java_files as rv


# --- tokenizer fallback (pure, no JVM) ----------------------------------------


@pytest.mark.parametrize("src", [
    "class M { void f() {} }",
    'String s = "a string with { unbalanced brace";',    # brace inside string
    "// a comment with ) paren\nclass M {}",              # paren in line comment
    '/* block } comment */ class M { int x = 1; }',      # brace in block comment
    'class M { int[] arr = {1, 2, 3}; }',                # balanced braces
    "class M { Object o = null; }",
])
def test_fallback_accepts_balanced(src):
    assert rv._check_with_fallback(src) is True


@pytest.mark.parametrize("src", [
    "class M { void f() {",              # missing closing brace
    "void f(int x ",                     # missing closing paren
    "int[] arr = new int[10",            # missing closing bracket
    'String s = "unterminated string',   # unclosed string
    "/* unterminated block comment",     # unclosed block comment
])
def test_fallback_rejects_unbalanced(src):
    assert rv._check_with_fallback(src) is False


# --- _javac_cmd construction ---------------------------------------------------


def test_javac_cmd_includes_proc_none():
    cmd = rv._javac_cmd([Path("A.java"), Path("B.java")], "/tmp/out")
    assert "-proc:none" in cmd
    assert "-d" in cmd and "/tmp/out" in cmd
    assert "A.java" in cmd[-2] and "B.java" in cmd[-1]


def test_javac_cmd_single_file():
    cmd = rv._javac_cmd([Path("Job.java")], "/tmp/out")
    assert "javac" in cmd[0]
    assert "Job.java" in cmd[-1]


# --- _has_rdd_scos_marker (quarantine detection) --------------------------------


def test_rdd_marker_detected(tmp_path):
    f = tmp_path / "RddJob.java"
    f.write_text(
        "// SCOS: [SPRKCNTSCL1500] RDD usage cannot be migrated automatically\n"
        "class RddJob {}",
        encoding="utf-8",
    )
    assert rv._has_rdd_scos_marker(f) is True


def test_rdd_marker_absent(tmp_path):
    f = tmp_path / "Clean.java"
    f.write_text("class Clean {}", encoding="utf-8")
    assert rv._has_rdd_scos_marker(f) is False


# --- _run_sweep control flow ---------------------------------------------------


def _make_tree(tmp_path: Path, names: list[str]) -> Path:
    migrated = tmp_path / "Output"
    migrated.mkdir()
    for n in names:
        (migrated / n).write_text("class M {}\n", encoding="utf-8")
    return migrated


def test_sweep_batch_pass_skips_per_file(tmp_path, monkeypatch):
    migrated = _make_tree(tmp_path, ["A.java", "B.java"])
    monkeypatch.setattr(rv, "_batch_javac_passes", lambda files: True)
    monkeypatch.setattr(rv, "_check_with_javac",
                        lambda *a, **k: pytest.fail("per-file check should be skipped"))
    monkeypatch.setattr(rv, "_git_revert", lambda *a, **k: pytest.fail("no revert expected"))

    failures, reverted, strategy, quarantined, diagnostics, revert_failures = rv._run_sweep(
        migrated, True, "phase-1-complete", False, no_revert=False
    )
    assert failures == [] and reverted == []
    assert strategy == "batch"


def test_sweep_batch_fail_attributes_per_file(tmp_path, monkeypatch):
    migrated = _make_tree(tmp_path, ["Good.java", "Bad.java"])
    monkeypatch.setattr(rv, "_batch_javac_passes", lambda files: False)
    monkeypatch.setattr(
        rv, "_check_with_javac",
        lambda fp: (fp.name != "Bad.java", "error: ';' expected"),
    )
    reverts: list[str] = []

    def _fake_revert(mig, fp, tag):
        reverts.append(fp.name)
        return True

    monkeypatch.setattr(rv, "_git_revert", _fake_revert)

    failures, reverted, strategy, quarantined, diagnostics, revert_failures = rv._run_sweep(
        migrated, True, "phase-1-complete", False, no_revert=False
    )
    assert failures == ["Bad.java"]
    assert reverted == ["Bad.java"]
    assert strategy == "per_file"
    assert "Bad.java" in diagnostics


def test_sweep_no_javac_uses_tokenizer(tmp_path, monkeypatch):
    migrated = _make_tree(tmp_path, ["A.java"])
    (migrated / "Broken.java").write_text("class Broken {", encoding="utf-8")
    monkeypatch.setattr(rv, "_git_revert", lambda *a, **k: True)

    failures, reverted, strategy, quarantined, diagnostics, revert_failures = rv._run_sweep(
        migrated, False, "phase-1-complete", False, no_revert=False
    )
    assert "Broken.java" in failures
    assert strategy == "per_file"


def test_sweep_quarantines_rdd_marker_files(tmp_path, monkeypatch):
    migrated = tmp_path / "Output"
    migrated.mkdir()
    bad = migrated / "RddJob.java"
    bad.write_text(
        "// SCOS: [SPRKCNTSCL1500] RDD usage\nclass RddJob {",
        encoding="utf-8",
    )
    monkeypatch.setattr(rv, "_batch_javac_passes", lambda files: False)
    monkeypatch.setattr(rv, "_check_with_javac", lambda fp: (False, "unresolved"))
    monkeypatch.setattr(rv, "_git_revert", lambda *a, **k: pytest.fail("quarantined should not be reverted"))

    failures, reverted, strategy, quarantined, diagnostics, revert_failures = rv._run_sweep(
        migrated, True, "phase-1-complete", False, no_revert=False
    )
    assert failures == []
    assert "RddJob.java" in quarantined
    assert reverted == []


def test_sweep_no_revert_flag_skips_revert(tmp_path, monkeypatch):
    migrated = _make_tree(tmp_path, ["Bad.java"])
    monkeypatch.setattr(rv, "_batch_javac_passes", lambda files: False)
    monkeypatch.setattr(rv, "_check_with_javac", lambda fp: (False, "error"))
    monkeypatch.setattr(rv, "_git_revert", lambda *a, **k: pytest.fail("revert must not run in no_revert mode"))

    failures, reverted, strategy, quarantined, diagnostics, revert_failures = rv._run_sweep(
        migrated, True, "phase-1-complete", False, no_revert=True
    )
    assert "Bad.java" in failures
    assert reverted == []


def test_sweep_empty_directory(tmp_path, monkeypatch):
    migrated = tmp_path / "Empty"
    migrated.mkdir()
    failures, reverted, strategy, quarantined, diagnostics, revert_failures = rv._run_sweep(
        migrated, True, "phase-1-complete", False
    )
    assert failures == [] and reverted == [] and strategy == "none"


# --- _iter_java_files pruning --------------------------------------------------


def test_iter_skips_target_gradle_idea(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "Main.java").write_text("class Main {}", encoding="utf-8")
    target = root / "target"
    target.mkdir()
    (target / "Generated.java").write_text("class G {}", encoding="utf-8")
    gradle = root / ".gradle"
    gradle.mkdir()
    (gradle / "Cache.java").write_text("class C {}", encoding="utf-8")

    files = list(rv._iter_java_files(root))
    names = {f.name for f in files}
    assert "Main.java" in names
    assert "Generated.java" not in names
    assert "Cache.java" not in names


# ---------------------------------------------------------------------------
# Security: CWE-22 path-confinement tests
# ---------------------------------------------------------------------------


def test_iter_java_files_skips_symlinks(tmp_path):
    """A .java symlink inside the root must not be yielded (CWE-22 fix)."""
    migrated = tmp_path / "migrated"
    migrated.mkdir()
    outside = tmp_path / "secret.java"
    outside.write_text("class Secret {}", encoding="utf-8")

    link = migrated / "Link.java"
    link.symlink_to(outside)

    files = list(rv._iter_java_files(migrated))
    assert link not in files, "symlink pointing outside root must be excluded"
    assert outside not in files


def test_iter_java_files_excludes_path_outside_root(tmp_path):
    """Resolved path outside the root is excluded (defense-in-depth)."""
    migrated = tmp_path / "migrated"
    migrated.mkdir()
    real_file = migrated / "App.java"
    real_file.write_text("class App {}", encoding="utf-8")

    files = list(rv._iter_java_files(migrated))
    # Real file inside root is included
    assert real_file in files
    # All yielded files must resolve inside the root
    resolved_root = migrated.resolve()
    for f in files:
        assert f.resolve().is_relative_to(resolved_root), (
            f"{f} resolves outside the migrated root"
        )


def test_git_revert_refuses_path_outside_root(tmp_path, monkeypatch):
    """_git_revert must return False without writing when file_path resolves
    outside the migrated root, even if git show would succeed (CWE-22 fix)."""
    migrated = tmp_path / "migrated"
    migrated.mkdir()

    outside = tmp_path / "outside.java"
    outside.write_text("original", encoding="utf-8")

    # Patch subprocess so git show appears to succeed with attacker bytes
    import subprocess
    class _FakeResult:
        stdout = b"attacker-bytes"
        returncode = 0

    def _fake_run(cmd, **kw):
        if "ls-files" in cmd:
            r = _FakeResult()
            r.stdout = "outside.java\n"
            return r
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = rv._git_revert(migrated, outside, "phase-1-complete")

    assert result is False, "_git_revert must refuse to write outside migrated root"
    assert outside.read_text() == "original", "file outside root must not be overwritten"