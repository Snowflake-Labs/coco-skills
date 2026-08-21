"""Tests for the `consolidate` subcommand of validate.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

VALIDATE_PY = Path(__file__).parent.parent / "validate.py"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True
    )


def _setup_repo(tmp_path: Path) -> Path:
    """Init a repo with identity configured (needed for commits)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "branch", "-M", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    return repo


def _commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content)
    _git(repo, "add", filename)
    r = _git(repo, "commit", "-m", message)
    assert r.returncode == 0, r.stderr
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _run_consolidate(repo: Path, base_sha: str, branches: str, extra: list | None = None) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable, str(VALIDATE_PY), "consolidate",
        "--conv-root", str(repo),
        "--base-sha", base_sha,
        *(["--branches", branches] if branches else []),
        *(extra or []),
    ]
    return subprocess.run(cmd, capture_output=False, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


# ---------------------------------------------------------------------------
# Happy-path: two branches, fix commits cherry-picked, noise excluded
# ---------------------------------------------------------------------------

def test_consolidate_two_branches(tmp_path):
    repo = _setup_repo(tmp_path)

    # Base commit on main
    base_sha = _commit(repo, "deliverable.txt", "base", "initial commit")

    # v1: one fix commit + one noise commit
    _git(repo, "checkout", "-b", "v1")
    _commit(repo, "fileA.txt", "fix from v1", "[MIGRATION-FIX] fix A")
    _commit(repo, "noise.txt", "not a fix", "[TEST-PATCH] noise")

    # v2: one fix commit
    _git(repo, "checkout", "main")
    _git(repo, "checkout", "-b", "v2")
    _commit(repo, "fileB.txt", "fix from v2", "[MIGRATION-FIX] fix B")

    # Back to main (the deliverable)
    _git(repo, "checkout", "main")

    result = _run_consolidate(repo, base_sha, "v1,v2")

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "RESULT=ok" in result.stdout

    # Both fix files present on main
    assert (repo / "fileA.txt").read_text() == "fix from v1"
    assert (repo / "fileB.txt").read_text() == "fix from v2"

    # Noise commit NOT cherry-picked (noise.txt must not exist)
    assert not (repo / "noise.txt").exists(), "noise.txt should not be present on main"


# ---------------------------------------------------------------------------
# Conflict case: two branches both modify the same line
# ---------------------------------------------------------------------------

def test_consolidate_conflict(tmp_path):
    repo = _setup_repo(tmp_path)

    # Base: shared.txt exists so both branches modify an existing file
    base_sha = _commit(repo, "shared.txt", "original\n", "initial commit")

    _git(repo, "checkout", "-b", "v1")
    (repo / "shared.txt").write_text("v1 change\n")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-m", "[MIGRATION-FIX] v1 edit shared")

    _git(repo, "checkout", "main")
    _git(repo, "checkout", "-b", "v2")
    (repo / "shared.txt").write_text("v2 change\n")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-m", "[MIGRATION-FIX] v2 edit shared")

    _git(repo, "checkout", "main")

    result = _run_consolidate(repo, base_sha, "v1,v2")

    assert result.returncode == 5, f"expected exit 5, got {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "RESULT=conflict" in result.stdout

    # Abort so the repo is clean for any teardown
    _git(repo, "cherry-pick", "--abort")


# ---------------------------------------------------------------------------
# Empty case: no [MIGRATION-FIX] commits → exit 0
# ---------------------------------------------------------------------------

def test_consolidate_no_fix_commits(tmp_path):
    repo = _setup_repo(tmp_path)
    base_sha = _commit(repo, "deliverable.txt", "base", "initial commit")

    _git(repo, "checkout", "-b", "v1")
    _commit(repo, "noise.txt", "something", "[TEST-PATCH] only noise")

    _git(repo, "checkout", "main")

    result = _run_consolidate(repo, base_sha, "v1")

    assert result.returncode == 0, result.stderr
    assert "RESULT=ok" in result.stdout
    assert "no [MIGRATION-FIX] commits" in result.stdout


# ---------------------------------------------------------------------------
# Dedup: same sha reachable from two branches is applied only once
# ---------------------------------------------------------------------------

def test_consolidate_dedup(tmp_path):
    repo = _setup_repo(tmp_path)
    base_sha = _commit(repo, "deliverable.txt", "base", "initial commit")

    # v1 has one fix
    _git(repo, "checkout", "-b", "v1")
    fix_sha = _commit(repo, "fileA.txt", "fix", "[MIGRATION-FIX] fix A")

    # v2 is v1 itself (same branch tip — same sha would appear in both logs)
    # Simulate by creating v2 as a branch pointer at the same commit
    _git(repo, "checkout", "main")
    _git(repo, "branch", "v2", "v1")

    result = _run_consolidate(repo, base_sha, "v1,v2")

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "RESULT=ok" in result.stdout
    # fileA.txt applied exactly once
    assert (repo / "fileA.txt").read_text() == "fix"
    # Only one extra commit on main beyond the base
    log = _git(repo, "log", "--format=%H", f"{base_sha}..HEAD")
    shas = [s for s in log.stdout.splitlines() if s.strip()]
    assert len(shas) == 1, f"expected 1 cherry-picked commit, got {len(shas)}: {shas}"


# ---------------------------------------------------------------------------
# Auto-discovery: --branches omitted → finds all validation/* branches
# ---------------------------------------------------------------------------

def test_consolidate_auto_discover_branches(tmp_path):
    repo = _setup_repo(tmp_path)

    # Base commit on main
    base_sha = _commit(repo, "deliverable.txt", "base", "initial commit")

    # Create two validation/* branches, each with a [MIGRATION-FIX] commit
    _git(repo, "checkout", "-b", "validation/aaa")
    _commit(repo, "fix_aaa.txt", "fix from aaa", "[MIGRATION-FIX] fix AAA")

    _git(repo, "checkout", "main")
    _git(repo, "checkout", "-b", "validation/bbb")
    _commit(repo, "fix_bbb.txt", "fix from bbb", "[MIGRATION-FIX] fix BBB")

    # A non-validation branch with a fix that should NOT be auto-discovered
    _git(repo, "checkout", "main")
    _git(repo, "checkout", "-b", "other/branch")
    _commit(repo, "fix_other.txt", "should not appear", "[MIGRATION-FIX] fix OTHER")

    # Back to main (the deliverable) — run consolidate WITHOUT --branches
    _git(repo, "checkout", "main")
    result = _run_consolidate(repo, base_sha, "")  # empty string → omit --branches

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "RESULT=ok" in result.stdout

    # Both validation/* fixes applied
    assert (repo / "fix_aaa.txt").read_text() == "fix from aaa"
    assert (repo / "fix_bbb.txt").read_text() == "fix from bbb"

    # Non-validation branch fix NOT applied
    assert not (repo / "fix_other.txt").exists(), \
        "fix_other.txt must not be applied — other/branch is not validation/*"


def test_consolidate_idempotent_on_reinvocation(tmp_path):
    """Re-running consolidate over an already-harvested branch is a clean no-op.
    The fix is cherry-picked onto main under a NEW sha; `git cherry` (patch-id)
    recognizes it as already present, so it is not re-selected and cannot
    conflict on a second invocation (crash re-dispatch / manual retry)."""
    repo = _setup_repo(tmp_path)
    base_sha = _commit(repo, "deliverable.txt", "base", "initial commit")
    _git(repo, "checkout", "-b", "v1")
    _commit(repo, "fileA.txt", "fix from v1", "[MIGRATION-FIX] fix A")
    _git(repo, "checkout", "main")

    # First harvest applies the fix.
    r1 = _run_consolidate(repo, base_sha, "v1")
    assert r1.returncode == 0, f"stdout={r1.stdout}\nstderr={r1.stderr}"
    assert (repo / "fileA.txt").read_text() == "fix from v1"

    # Second harvest over the same branch: already applied → filtered out, clean.
    r2 = _run_consolidate(repo, base_sha, "v1")
    assert r2.returncode == 0, f"stdout={r2.stdout}\nstderr={r2.stderr}"
    assert "RESULT=ok" in r2.stdout
    assert "no [MIGRATION-FIX] commits" in r2.stdout
