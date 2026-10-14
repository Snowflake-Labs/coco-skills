"""Shared pytest helpers for the validation skill test suite."""
from __future__ import annotations


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: git/worktree integration tests (deselect with '-m \"not slow\"')",
    )
