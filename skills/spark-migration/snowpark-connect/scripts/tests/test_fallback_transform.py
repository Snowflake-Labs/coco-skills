"""Unit tests for ``fallback_transform.find_unprocessed_files``.

Regression coverage for the state-key mismatch where the fallback gate
treated LLM-migrated files as unprocessed and stamped them with a bogus
``SPRKCNTPY0099`` "Partial Migration" finding.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_fallback_transform.py
"""

from __future__ import annotations

from fallback_transform import _collect_done_files, find_unprocessed_files


def _manifest() -> list[str]:
    return [
        "src/__init__.py",
        "src/main.py",
        "src/transformers/geohash_encoder.py",
    ]


# --- the original bug --------------------------------------------------------


def test_processed_files_counts_as_done():
    """Files recorded only in ``processed_files`` must NOT be re-flagged.

    This is the exact Kipawa shape: the fixer wrote ``processed_files`` but
    no top-level ``2_fixes.files_done`` exists. The old code ignored
    ``processed_files`` and returned the whole manifest.
    """
    state = {
        "manifest": _manifest(),
        "processed_files": _manifest(),
    }
    assert find_unprocessed_files(state) == []


def test_phases_completed_files_done_counts_as_done():
    """Completion recorded under ``phases_completed.2_fixes.files_done``."""
    state = {
        "manifest": _manifest(),
        "phases_completed": {"2_fixes": {"files_done": _manifest()}},
    }
    assert find_unprocessed_files(state) == []


def test_top_level_2_fixes_files_done_still_honored():
    """The original public-skill contract keeps working."""
    state = {
        "manifest": _manifest(),
        "2_fixes": {"files_done": _manifest()},
    }
    assert find_unprocessed_files(state) == []


# --- partial coverage --------------------------------------------------------


def test_only_genuinely_unprocessed_files_returned():
    state = {
        "manifest": _manifest(),
        "processed_files": ["src/__init__.py", "src/main.py"],
    }
    assert find_unprocessed_files(state) == ["src/transformers/geohash_encoder.py"]


def test_basename_match_bridges_abs_and_relative_paths():
    """A done record by absolute path covers the relative manifest entry."""
    state = {
        "manifest": ["src/main.py"],
        "processed_files": ["/abs/Output/src/main.py"],
    }
    assert find_unprocessed_files(state) == []


# --- pending_files interaction ----------------------------------------------


def test_pending_files_authoritative_but_subtracts_done():
    """A stale pending entry that is also recorded done must not re-trigger."""
    state = {
        "manifest": _manifest(),
        "pending_files": ["src/main.py"],
        "processed_files": ["src/main.py"],
    }
    assert find_unprocessed_files(state) == []


def test_pending_files_returns_genuinely_pending():
    state = {
        "manifest": _manifest(),
        "pending_files": ["src/transformers/geohash_encoder.py"],
        "processed_files": ["src/__init__.py", "src/main.py"],
    }
    assert find_unprocessed_files(state) == ["src/transformers/geohash_encoder.py"]


# --- no signal anywhere ------------------------------------------------------


def test_no_completion_signal_returns_full_manifest():
    state = {"manifest": _manifest()}
    assert find_unprocessed_files(state) == _manifest()


def test_collect_done_unions_all_sources():
    state = {
        "processed_files": ["a.py"],
        "2_fixes": {"files_done": ["b.py"]},
        "phases_completed": {"2_fixes": {"files_done": ["c.py"]}},
    }
    assert _collect_done_files(state) == {"a.py", "b.py", "c.py"}
