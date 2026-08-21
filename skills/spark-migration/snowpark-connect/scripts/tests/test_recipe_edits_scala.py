"""Tests for analyze_scala.py Phase 0.5 recipe-edits threading.

Covers: _recipe_path_key, _recipe_edits_for_file, _recipe_edits_for_block,
and the recipe-aware _partition_decidable_blocks_scala defer guard (recipe-
touched blocks are always deferred as ``needs_adjudication``, never bypassed
as decidable).
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from analyze_scala import (
    ScalaCodeBlock,
    _RECIPE_LINKAGE_STATS,
    _RECIPE_LINKAGE_FAILED_PATHS,
    _block_is_fully_decidable_scala,
    _partition_decidable_blocks_scala,
    _recipe_edits_for_block,
    _recipe_edits_for_file,
    _recipe_path_key,
)


# ---------------------------------------------------------------------------
# _recipe_path_key
# ---------------------------------------------------------------------------


def test_recipe_path_key_relative(tmp_path):
    src = tmp_path / "Output" / "src" / "main" / "scala" / "Etl.scala"
    src.parent.mkdir(parents=True)
    src.touch()
    key = _recipe_path_key(src, tmp_path / "Output")
    assert key == str(Path("src") / "main" / "scala" / "Etl.scala")


def test_recipe_path_key_no_source_root():
    assert _recipe_path_key(Path("/some/file.scala"), None) is None


def test_recipe_path_key_outside_root(tmp_path):
    other = tmp_path / "other" / "file.scala"
    root = tmp_path / "Output"
    root.mkdir()
    assert _recipe_path_key(other, root) is None


# ---------------------------------------------------------------------------
# _recipe_edits_for_file
# ---------------------------------------------------------------------------


def test_recipe_edits_for_file_match(tmp_path):
    src = tmp_path / "Output" / "Etl.scala"
    src.parent.mkdir(parents=True)
    src.touch()
    edits = [{"recipe_id": "scalafix:ScosRddPersistToCache", "src_line": 10}]
    recipe_all = {"Etl.scala": edits}
    result = _recipe_edits_for_file(recipe_all, src, tmp_path / "Output")
    assert result == edits


def test_recipe_edits_for_file_no_match(tmp_path):
    src = tmp_path / "Output" / "Other.scala"
    src.parent.mkdir(parents=True)
    src.touch()
    edits = [{"recipe_id": "scalafix:ScosRddPersistToCache", "src_line": 5}]
    recipe_all = {"Etl.scala": edits}
    result = _recipe_edits_for_file(recipe_all, src, tmp_path / "Output")
    assert result == []


def test_recipe_edits_for_file_none():
    result = _recipe_edits_for_file(None, Path("/x.scala"), None)
    assert result == []


def test_recipe_edits_for_file_empty_dict():
    result = _recipe_edits_for_file({}, Path("/x.scala"), Path("/"))
    assert result == []


# ---------------------------------------------------------------------------
# _recipe_edits_for_block
# ---------------------------------------------------------------------------


def test_recipe_edits_for_block_output_line_in_range():
    edits = [
        {"recipe_id": "scalafix:ScosWildcardReadAnnotate", "src_line": 5, "output_line": 12}
    ]
    assert _recipe_edits_for_block(edits, 10, 15) == edits


def test_recipe_edits_for_block_src_line_fallback():
    edits = [{"recipe_id": "scalafix:ScosRddPersistToCache", "src_line": 20}]
    assert _recipe_edits_for_block(edits, 18, 22) == edits


def test_recipe_edits_for_block_outside_range():
    edits = [{"recipe_id": "scalafix:ScosRddPersistToCache", "src_line": 5}]
    assert _recipe_edits_for_block(edits, 10, 20) == []


def test_recipe_edits_for_block_empty():
    assert _recipe_edits_for_block([], 1, 100) == []


def test_recipe_edits_for_block_prefers_output_line():
    # output_line=50 is outside [10, 15], but src_line=12 is inside.
    # Should use output_line and NOT match.
    edits = [{"recipe_id": "scalafix:ScosRddPersistToCache", "src_line": 12, "output_line": 50}]
    assert _recipe_edits_for_block(edits, 10, 15) == []


# ---------------------------------------------------------------------------
# _partition_decidable_blocks_scala (recipe-defer guard)
# ---------------------------------------------------------------------------


def _make_decidable_item(line_start=10, line_end=12):
    """Build a minimal blocks_to_analyze item that is fully decidable."""
    block = ScalaCodeBlock(
        code="import org.apache.spark.ml.classification.LogisticRegression",
        line_start=line_start,
        line_end=line_end,
        block_type="import",
    )
    return {
        "block": block,
        "scos_issues": [{"api": "spark.ml", "risk": 1.0, "reason": "ML not supported",
                         "decidable": True, "category": "Unsupported Module"}],
        "matching_patterns": [],  # no fuzzy evidence
        "preliminary_risk": 1.0,
        "scos_risk": 1.0,
        "failure_likelihood": 0.0,
    }


class _DummyFile:
    """Minimal file stub so _build_decidable_result_scala can call file_path.read_text."""


def test_partition_decidable_no_recipe():
    """Fully decidable block with no recipe touch → emitted deterministically."""
    item = _make_decidable_item()
    decided, remaining = _partition_decidable_blocks_scala(
        [item], Path("Etl.scala"), risk_threshold=0.1, file_recipe_edits=[]
    )
    assert len(decided) == 1
    assert decided[0]["source"] == "trigger_decidable"
    assert remaining == []


def test_partition_decidable_recipe_touch_deferred():
    """Decidable block that was touched by a Scalafix rule → deferred as
    needs_adjudication (never bypassed as decidable), never sent to an LLM."""
    item = _make_decidable_item(line_start=10, line_end=12)
    file_recipe_edits = [
        {"recipe_id": "scalafix:ScosRddPersistToCache", "src_line": 11}
    ]
    decided, remaining = _partition_decidable_blocks_scala(
        [item], Path("Etl.scala"), risk_threshold=0.1, file_recipe_edits=file_recipe_edits
    )
    # Recipe-touched → deferred for the Phase 1.1 adjudicator, not bypassed.
    assert len(decided) == 1
    assert decided[0]["kind"] == "needs_adjudication"
    assert decided[0]["source"] == "deferred_adjudication"
    assert remaining == []


def test_partition_decidable_recipe_outside_range():
    """Recipe edit outside the block range → block is still bypassed normally."""
    item = _make_decidable_item(line_start=10, line_end=12)
    file_recipe_edits = [
        {"recipe_id": "scalafix:ScosRddPersistToCache", "src_line": 50}
    ]
    decided, remaining = _partition_decidable_blocks_scala(
        [item], Path("Etl.scala"), risk_threshold=0.1, file_recipe_edits=file_recipe_edits
    )
    assert len(decided) == 1
    assert decided[0]["source"] == "trigger_decidable"
    assert remaining == []


def test_partition_none_recipe_edits():
    """Passing file_recipe_edits=None is treated as empty — no crash."""
    item = _make_decidable_item()
    decided, remaining = _partition_decidable_blocks_scala(
        [item], Path("Etl.scala"), risk_threshold=0.1, file_recipe_edits=None
    )
    assert len(decided) == 1
    assert remaining == []


def test_partition_non_decidable_block_deferred():
    """A non-decidable block (fuzzy RAG match) is deferred, not dropped or LLM'd."""
    block = ScalaCodeBlock(code="x", line_start=1, line_end=1, block_type="expr")
    item = {
        "block": block,
        "scos_issues": [],
        "matching_patterns": [{"score": 0.9, "root_cause": "fuzzy match"}],
        "preliminary_risk": 0.6,
        "scos_risk": 0.0,
        "failure_likelihood": 0.9,
    }
    decided, remaining = _partition_decidable_blocks_scala(
        [item], Path("Etl.scala"), risk_threshold=0.1, file_recipe_edits=[]
    )
    assert len(decided) == 1
    assert decided[0]["kind"] == "needs_adjudication"
    assert decided[0]["adjudicated"] is False
    assert decided[0]["root_cause"] == "fuzzy match"
    assert remaining == []

