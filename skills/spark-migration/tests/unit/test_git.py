"""Unit tests for sma_api git tools."""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

import sma_api  # noqa: E402


@pytest.fixture
def workdir(tmp_path):
    (tmp_path / "file.txt").write_text("hello")
    return str(tmp_path)


@pytest.fixture
def git_repo(workdir):
    subprocess.run(["git", "init"], cwd=workdir, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=workdir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=workdir,
        capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    subprocess.run(["git", "branch", "-M", "main"], cwd=workdir, capture_output=True)
    return workdir


class TestGitIsRepo:
    def test_not_a_repo(self, workdir):
        assert sma_api.git_is_repo(workdir) is False

    def test_is_a_repo(self, git_repo):
        assert sma_api.git_is_repo(git_repo) is True


class TestGitStatus:
    def test_not_a_repo(self, workdir):
        s = sma_api.git_status(workdir)
        assert s["is_repo"] is False
        assert s["current_branch"] is None

    def test_repo_clean(self, git_repo):
        s = sma_api.git_status(git_repo)
        assert s["is_repo"] is True
        assert s["is_clean"] is True
        assert s["current_branch"] == "main"
        assert s["migration_branch_exists"] is False

    def test_repo_dirty(self, git_repo):
        with open(os.path.join(git_repo, "new.txt"), "w") as f:
            f.write("dirty")
        s = sma_api.git_status(git_repo)
        assert s["is_clean"] is False


class TestGitInitIfNeeded:
    def test_init_fresh(self, workdir):
        result = sma_api.git_init_if_needed(workdir)
        assert result["success"] is True
        assert result["action"] == "initialized"
        assert sma_api.git_is_repo(workdir) is True
        assert sma_api.git_current_branch(workdir) == "main"

    def test_init_already_repo(self, git_repo):
        result = sma_api.git_init_if_needed(git_repo)
        assert result["success"] is True
        assert result["action"] == "already_initialized"


class TestGitEnsureBranch:
    def test_create_new_branch(self, git_repo):
        result = sma_api.git_ensure_branch(git_repo)
        assert result["success"] is True
        assert result["action"] == "created"
        assert sma_api.git_current_branch(git_repo) == sma_api.MIGRATION_BRANCH

    def test_switch_existing_branch(self, git_repo):
        sma_api.git_ensure_branch(git_repo)
        subprocess.run(["git", "checkout", "main"], cwd=git_repo, capture_output=True)
        result = sma_api.git_ensure_branch(git_repo)
        assert result["success"] is True
        assert result["action"] == "switched"

    def test_already_on_branch(self, git_repo):
        sma_api.git_ensure_branch(git_repo)
        result = sma_api.git_ensure_branch(git_repo)
        assert result["success"] is True
        assert result["action"] == "already_on_branch"


class TestGitStash:
    def test_stash_dirty(self, git_repo):
        with open(os.path.join(git_repo, "new.txt"), "w") as f:
            f.write("dirty")
        result = sma_api.git_stash(git_repo)
        assert result["success"] is True
        assert result["stashed"] is True
        assert sma_api.git_is_clean(git_repo) is True

    def test_stash_clean(self, git_repo):
        result = sma_api.git_stash(git_repo)
        assert result["success"] is True
        assert result["stashed"] is False


class TestGitCommit:
    def test_commit_changes(self, git_repo):
        with open(os.path.join(git_repo, "new.txt"), "w") as f:
            f.write("content")
        result = sma_api.git_commit(git_repo, "test commit")
        assert result["success"] is True
        assert result["action"] == "committed"
        assert result["commit_hash"] is not None

    def test_nothing_to_commit(self, git_repo):
        result = sma_api.git_commit(git_repo, "empty")
        assert result["success"] is True
        assert result["action"] == "nothing_to_commit"


class TestGitVerifyBranches:
    def test_only_main(self, git_repo):
        result = sma_api.git_verify_branches(git_repo)
        assert result["has_main"] is True
        assert result["has_migration"] is False
        assert result["success"] is False

    def test_both_branches(self, git_repo):
        sma_api.git_ensure_branch(git_repo)
        result = sma_api.git_verify_branches(git_repo)
        assert result["success"] is True
        assert result["has_main"] is True
        assert result["has_migration"] is True


class TestGitEnsureReady:
    def test_fresh_directory(self, workdir):
        result = sma_api.git_ensure_ready(workdir)
        assert result["success"] is True
        assert sma_api.git_current_branch(workdir) == sma_api.MIGRATION_BRANCH
        v = sma_api.git_verify_branches(workdir)
        assert v["has_main"] is True
        assert v["has_migration"] is True

    def test_existing_clean_repo(self, git_repo):
        result = sma_api.git_ensure_ready(git_repo)
        assert result["success"] is True
        assert sma_api.git_current_branch(git_repo) == sma_api.MIGRATION_BRANCH

    def test_existing_dirty_repo(self, git_repo):
        with open(os.path.join(git_repo, "dirty.txt"), "w") as f:
            f.write("dirty")
        result = sma_api.git_ensure_ready(git_repo)
        assert result["success"] is True
        assert sma_api.git_is_clean(git_repo) is True
        assert sma_api.git_current_branch(git_repo) == sma_api.MIGRATION_BRANCH
