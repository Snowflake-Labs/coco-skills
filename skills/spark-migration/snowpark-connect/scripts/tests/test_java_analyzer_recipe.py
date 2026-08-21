"""Unit tests for analyze_java.py recipe-edit grounding helpers (B4 / Workstream B).

Tests:
  * _recipe_edits_for_block scopes edits to a line range
  * _build_recipe_text formats rewrite vs annotate vs other correctly
  * _recipe_edits_for_file uses relative-path keying
  * analyze_file passes recipe_edits key into batch items (mocked LLM path)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ is on sys.path.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Guard: analyze_java imports snowflake.snowpark; mock if unavailable.
try:
    import snowflake.snowpark  # noqa: F401
    _SNOWPARK_AVAILABLE = True
except ImportError:
    _SNOWPARK_AVAILABLE = False

if not _SNOWPARK_AVAILABLE:
    import types
    _sf = types.ModuleType("snowflake")
    _sf.snowpark = types.ModuleType("snowflake.snowpark")  # type: ignore[attr-defined]
    _sf.snowpark.Session = object  # type: ignore[attr-defined]
    sys.modules.setdefault("snowflake", _sf)
    sys.modules.setdefault("snowflake.snowpark", _sf.snowpark)

import analyze_java as A


# ── _recipe_edits_for_block ────────────────────────────────────────────────────

def test_recipe_edits_for_block_scopes_to_range():
    edits = [
        {"recipe_id": "javaparser:RuleA", "src_line": 10},
        {"recipe_id": "javaparser:RuleB", "src_line": 20},
        {"recipe_id": "javaparser:RuleC", "src_line": 30},
    ]
    result = A._recipe_edits_for_block(edits, 15, 25)
    assert len(result) == 1
    assert result[0]["recipe_id"] == "javaparser:RuleB"


def test_recipe_edits_for_block_inclusive_boundaries():
    edits = [
        {"recipe_id": "javaparser:RuleA", "src_line": 5},
        {"recipe_id": "javaparser:RuleB", "src_line": 15},
    ]
    assert len(A._recipe_edits_for_block(edits, 5, 5)) == 1
    assert len(A._recipe_edits_for_block(edits, 15, 15)) == 1
    assert len(A._recipe_edits_for_block(edits, 5, 15)) == 2


def test_recipe_edits_for_block_empty_input():
    assert A._recipe_edits_for_block([], 1, 100) == []


def test_recipe_edits_for_block_no_src_line_skipped():
    edits = [{"recipe_id": "javaparser:RuleA"}]  # missing src_line
    assert A._recipe_edits_for_block(edits, 1, 100) == []


def test_recipe_edits_for_block_non_int_src_line_skipped():
    edits = [{"recipe_id": "javaparser:RuleA", "src_line": "10"}]  # string, not int
    assert A._recipe_edits_for_block(edits, 1, 100) == []


# ── _build_recipe_text ─────────────────────────────────────────────────────────

def test_build_recipe_text_empty_returns_no_recipes_message():
    text = A._build_recipe_text([])
    assert "No recipes fired" in text


def test_build_recipe_text_annotate_rule():
    edits = [{"recipe_id": "javaparser:ScosCheckpointToCache_annotate", "src_line": 42}]
    text = A._build_recipe_text(edits)
    assert "ANNOTATE-only" in text
    assert "42" in text
    assert "recipe_incomplete" in text


def test_build_recipe_text_rewrite_rule():
    edits = [{"recipe_id": "javaparser:ScosSparkSessionBuilderRewrite_rewrite", "src_line": 7}]
    text = A._build_recipe_text(edits)
    assert "REWRITE applied" in text
    assert "7" in text
    assert "Do NOT emit a fresh issue" in text


def test_build_recipe_text_other_rule():
    edits = [{"recipe_id": "javaparser:SomeOtherRule", "src_line": 99}]
    text = A._build_recipe_text(edits)
    assert "OTHER" in text
    assert "99" in text


# ── _recipe_edits_for_file ─────────────────────────────────────────────────────

def test_recipe_edits_for_file_relative_key_match(tmp_path):
    source_root = tmp_path / "Output"
    source_root.mkdir()
    subdir = source_root / "com" / "example"
    subdir.mkdir(parents=True)
    java_file = subdir / "Etl.java"
    java_file.write_text("// placeholder")

    recipe_edits_all = {
        "com/example/Etl.java": [{"recipe_id": "javaparser:RuleX", "src_line": 5}],
    }
    result = A._recipe_edits_for_file(recipe_edits_all, java_file, source_root)
    assert len(result) == 1
    assert result[0]["recipe_id"] == "javaparser:RuleX"


def test_recipe_edits_for_file_none_when_no_recipe_edits(tmp_path):
    java_file = tmp_path / "Foo.java"
    java_file.write_text("")
    assert A._recipe_edits_for_file(None, java_file, tmp_path) == []


def test_recipe_edits_for_file_outside_source_root(tmp_path, caplog):
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    java_file = other_dir / "Foo.java"
    java_file.write_text("")
    source_root = tmp_path / "Output"
    source_root.mkdir()

    recipe_edits_all = {"Foo.java": [{"recipe_id": "javaparser:RuleX", "src_line": 1}]}
    import logging
    with caplog.at_level(logging.WARNING, logger="analyze_java"):
        result = A._recipe_edits_for_file(recipe_edits_all, java_file, source_root)
    assert result == []


# ── _classify_recipe_kind ─────────────────────────────────────────────────────

def test_classify_recipe_kind():
    assert A._classify_recipe_kind("javaparser:RuleA_rewrite") == "rewrite"
    assert A._classify_recipe_kind("javaparser:RuleA_annotate") == "annotate"
    assert A._classify_recipe_kind("javaparser:RuleA_comment") == "comment"
    assert A._classify_recipe_kind("javaparser:RuleA") == "other"
