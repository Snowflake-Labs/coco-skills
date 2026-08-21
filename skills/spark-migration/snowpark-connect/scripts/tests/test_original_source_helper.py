"""Tier-B unit tests for ``_original_source.materialize_original_source``.

Exercises the git-tag round-trip on a freshly-initialized tmp repo:
``git init`` + commit a tracked tree + tag + materialize → extracted dir
contains the original content even after the live repo has been mutated.
Also covers the failure mode (missing tag → ``OriginalSourceUnavailable``).

These tests shell out to ``git`` directly so they validate the actual
subprocess contract; if git isn't on PATH they're skipped.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from _original_source import (
    OriginalSourceUnavailable,
    materialize_original_source,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git binary required for materialize_original_source round-trip",
)


def _git(*args: str, cwd: Path) -> None:
    """Helper: run a git command in ``cwd``, fail loudly on non-zero."""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed in {cwd}: rc={proc.returncode}, "
            f"stderr={proc.stderr.strip()}"
        )


def _init_repo(repo_root: Path, files: dict[str, str], tag: str) -> None:
    """Initialise a git repo at ``repo_root``, commit ``files``, tag it."""
    repo_root.mkdir(parents=True, exist_ok=True)
    _git("init", "--quiet", "-b", "main", cwd=repo_root)
    _git("config", "user.email", "test@example.com", cwd=repo_root)
    _git("config", "user.name", "Test User", cwd=repo_root)
    _git("config", "commit.gpgsign", "false", cwd=repo_root)
    for rel, content in files.items():
        p = repo_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git("add", ".", cwd=repo_root)
    _git(
        "-c", "user.email=test@example.com",
        "-c", "user.name=Test User",
        "commit", "--quiet", "-m", "Initial commit",
        cwd=repo_root,
    )
    _git("tag", tag, cwd=repo_root)


def test_materialize_round_trip_returns_tagged_content(tmp_path: Path) -> None:
    """The tagged tree survives even when the live work-tree is mutated."""
    repo = tmp_path / "conversion"
    original_files = {
        "Output/workload.py": "import pyspark\nprint('original')\n",
        "Output/sub/util.py": "def helper():\n    return 1\n",
        "migration_state.json": '{"phase": 0}\n',
    }
    _init_repo(repo, original_files, tag="phase-0-source")

    # Mutate the live work-tree AFTER the tag — simulate Phase 0.5 recipes.
    (repo / "Output" / "workload.py").write_text(
        "import pyspark\n# SCOS-WARN: recipe applied\nprint('post-recipe')\n",
        encoding="utf-8",
    )

    with materialize_original_source(repo) as extracted:
        # The extracted root should hold the tagged content.
        wp = extracted / "Output" / "workload.py"
        assert wp.is_file(), f"workload.py missing from {extracted}"
        text = wp.read_text(encoding="utf-8")
        assert text == "import pyspark\nprint('original')\n", (
            "extracted file does not match the tagged original — got:\n" + text
        )

        # Subdirectories preserved.
        up = extracted / "Output" / "sub" / "util.py"
        assert up.is_file()
        assert "def helper" in up.read_text(encoding="utf-8")

        # Non-source files (migration_state.json) also captured because the
        # initial commit added everything in <CONVERSION>/.
        ms = extracted / "migration_state.json"
        assert ms.is_file()


def test_materialize_cleans_up_temp_dir_on_exit(tmp_path: Path) -> None:
    """The temp directory is deleted when the context manager exits."""
    repo = tmp_path / "conversion"
    _init_repo(
        repo,
        {"Output/a.py": "x = 1\n"},
        tag="phase-0-source",
    )

    with materialize_original_source(repo) as extracted:
        assert extracted.is_dir()
        captured = extracted
    assert not captured.exists(), (
        f"materialize_original_source must clean up its tmp dir; "
        f"{captured} still exists after context exit"
    )


def test_materialize_raises_when_tag_missing(tmp_path: Path) -> None:
    """Missing tag → OriginalSourceUnavailable (caller falls back gracefully)."""
    repo = tmp_path / "conversion"
    _init_repo(
        repo,
        {"Output/a.py": "x = 1\n"},
        tag="some-other-tag",
    )
    with pytest.raises(OriginalSourceUnavailable) as excinfo:
        with materialize_original_source(repo, tag="phase-0-source"):
            pass  # pragma: no cover — we expect the enter to raise
    assert "phase-0-source" in str(excinfo.value)


def test_materialize_raises_when_not_a_repo(tmp_path: Path) -> None:
    """A non-repo dir produces OriginalSourceUnavailable, not a cryptic crash."""
    repo = tmp_path / "not-a-repo"
    repo.mkdir()
    with pytest.raises(OriginalSourceUnavailable):
        with materialize_original_source(repo):
            pass  # pragma: no cover
