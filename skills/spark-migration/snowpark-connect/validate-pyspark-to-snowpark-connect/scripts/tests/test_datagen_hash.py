"""Tests for datagen.py deferred hash-write behaviour (friction log item 53).

Uses mocked materialize() to avoid pyarrow/pandas dependency — only tests
hash-file gating behaviour, not mock-data content.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import datagen  # noqa: E402


def _ep(ep_id="ep1"):
    """Minimal entrypoint with one relational table."""
    return {
        "id": ep_id,
        "path": "main.py",
        "run_mode": "script",
        "import_roots": ["."],
        "entrypoint_kwargs": {},
        "source_runtime": "spark",
        "tables": {
            "events": {
                "relational": True,
                "category": "table",
                "access": "read",
                "original_path": "events",
                "format": "parquet",
                "mock_file": "events.parquet",
                "columns": [
                    {"name": "id", "type": "string", "nullable": False},
                    {"name": "value", "type": "integer", "nullable": True},
                ],
            }
        },
    }


def _hashes_path(tmp_path, ep_id="ep1"):
    return tmp_path / ep_id / "_hashes.json"


def _seed(eps, out_dir, *, defer_hash_write=False, force_all=False):
    """Call seed_workload with materialize mocked to write a stub file."""
    def _fake_materialize(rows, cols, path, fmt, opts=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stub")

    with patch("datagen.materialize", side_effect=_fake_materialize):
        return datagen.seed_workload(
            eps, str(out_dir),
            defer_hash_write=defer_hash_write,
            force_all=force_all,
        )


# ---------------------------------------------------------------------------
# Default behaviour: backward-compatible (hash written immediately)
# ---------------------------------------------------------------------------

def test_default_writes_hashes_immediately(tmp_path):
    """Without defer_hash_write, _hashes.json is present after seed_workload."""
    result = _seed([_ep()], tmp_path)
    assert _hashes_path(tmp_path).is_file(), "_hashes.json should be written by default"
    assert "pending_hashes" not in result


# ---------------------------------------------------------------------------
# Deferred mode: hash NOT written until commit_mock_hashes is called
# ---------------------------------------------------------------------------

def test_defer_suppresses_hash_file(tmp_path):
    """With defer_hash_write=True, _hashes.json must NOT exist after seed_workload."""
    _seed([_ep()], tmp_path, defer_hash_write=True)
    assert not _hashes_path(tmp_path).exists(), (
        "_hashes.json must not be written before upload confirmation"
    )


def test_defer_returns_pending_hashes(tmp_path):
    """With defer_hash_write=True, result contains pending_hashes for the ep."""
    result = _seed([_ep()], tmp_path, defer_hash_write=True)
    assert "pending_hashes" in result
    assert "ep1" in result["pending_hashes"]
    # key is the base name (no extension) — "events", not "events.parquet"
    assert "events" in result["pending_hashes"]["ep1"]


def test_commit_writes_hash_file(tmp_path):
    """commit_mock_hashes writes _hashes.json with the expected content."""
    result = _seed([_ep()], tmp_path, defer_hash_write=True)
    assert not _hashes_path(tmp_path).exists()

    # Simulate: upload succeeded → commit hashes
    datagen.commit_mock_hashes(str(tmp_path), result["pending_hashes"])

    assert _hashes_path(tmp_path).is_file(), "_hashes.json must exist after commit"
    on_disk = json.loads(_hashes_path(tmp_path).read_text())
    assert "events" in on_disk


def test_upload_failure_leaves_no_hash_file(tmp_path):
    """If caller never calls commit_mock_hashes (upload failed), file stays absent."""
    _seed([_ep()], tmp_path, defer_hash_write=True)
    # Simulate: upload failed — caller does NOT call commit_mock_hashes
    assert not _hashes_path(tmp_path).exists(), (
        "hash file must remain absent when upload is never confirmed"
    )


def test_deferred_hash_prevents_stale_skip_on_retry(tmp_path):
    """Without a committed hash, a subsequent seed_workload re-generates the mock.

    Core regression: if the hash were written eagerly after mock generation,
    the second call would see a hash match and skip regeneration — even though
    the upload never happened and Snowflake still has stale data.
    """
    # First run with deferred write — upload fails, no commit
    _seed([_ep()], tmp_path, defer_hash_write=True)
    assert not _hashes_path(tmp_path).exists()

    # Second run: no stored hash → table must be regenerated (in "seeded"), not skipped
    result2 = _seed([_ep()], tmp_path, defer_hash_write=True)
    assert "table:events" in result2.get("seeded", {}), (
        "table must appear in 'seeded' (regenerated), not 'skipped', "
        "because no hash was committed after the failed upload"
    )


def test_commit_enables_skip_on_second_run(tmp_path):
    """After a successful commit, the next seed_workload skips unchanged tables."""
    result = _seed([_ep()], tmp_path, defer_hash_write=True)
    datagen.commit_mock_hashes(str(tmp_path), result["pending_hashes"])

    # Second run: hash matches → table is skipped (gkey "table:events" in skipped)
    result2 = _seed([_ep()], tmp_path, defer_hash_write=True)
    assert "table:events" not in result2.get("seeded", {}), (
        "table should be skipped after committed hash"
    )
    assert "table:events" in result2.get("skipped", {}), (
        "table should appear in 'skipped'"
    )
