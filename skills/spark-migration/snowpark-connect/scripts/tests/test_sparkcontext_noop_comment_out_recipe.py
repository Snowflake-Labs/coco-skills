"""Unit tests for ``sparkcontext_noop_comment_out_rewrite`` LibCST recipe.

Tests that sparkContext.setCheckpointDir() and sparkContext.setLogLevel()
standalone expression statements are commented out with a SCOS note.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_sparkcontext_noop_comment_out_recipe.py
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
sys.path.insert(0, str(_RECIPES_DIR))

from _common import load_recipe_module  # noqa: E402

_RECIPE_DIR = _RECIPES_DIR / "sparkcontext_noop_comment_out_rewrite"
_recipe = load_recipe_module(_RECIPE_DIR)

_MARKER = "sparkcontext_noop_comment_out_rewrite"


def _apply(src: str):
    src = textwrap.dedent(src).lstrip("\n")
    res = _recipe.apply(src, file="t.py")
    return res.source, res.edits


# --------------------------------------------------------------------------
# Positive cases — setCheckpointDir
# --------------------------------------------------------------------------


def test_comments_out_sparkcontext_setcheckpointdir() -> None:
    new, edits = _apply(
        """
        spark.sparkContext.setCheckpointDir("dbfs:/tmp/checkpoints")
        """
    )
    assert _MARKER in new
    assert "setCheckpointDir" in new
    # Original code is preserved as a comment
    assert '# spark.sparkContext.setCheckpointDir("dbfs:/tmp/checkpoints")' in new
    # SCOS explanation is present
    assert "no SCOS equivalent" in new
    assert "Snowflake manages checkpointing" in new
    # A `pass` replaces the statement body
    assert "pass" in new
    assert len(edits) == 1


def test_comments_out_sc_setcheckpointdir() -> None:
    new, edits = _apply(
        """
        sc.setCheckpointDir("/tmp/spark_checkpoints")
        """
    )
    assert _MARKER in new
    assert '# sc.setCheckpointDir("/tmp/spark_checkpoints")' in new
    assert "pass" in new
    assert len(edits) == 1


def test_comments_out_setcheckpointdir_variable_arg() -> None:
    new, edits = _apply(
        """
        spark.sparkContext.setCheckpointDir(checkpoint_path)
        """
    )
    assert _MARKER in new
    assert "# spark.sparkContext.setCheckpointDir(checkpoint_path)" in new
    assert len(edits) == 1


# --------------------------------------------------------------------------
# Positive cases — setLogLevel
# --------------------------------------------------------------------------


def test_comments_out_sparkcontext_setloglevel() -> None:
    new, edits = _apply(
        """
        spark.sparkContext.setLogLevel("WARN")
        """
    )
    assert _MARKER in new
    assert "setLogLevel" in new
    assert '# spark.sparkContext.setLogLevel("WARN")' in new
    assert "log verbosity" in new
    assert "pass" in new
    assert len(edits) == 1


def test_comments_out_sc_setloglevel() -> None:
    new, edits = _apply(
        """
        sc.setLogLevel("ERROR")
        """
    )
    assert _MARKER in new
    assert '# sc.setLogLevel("ERROR")' in new
    assert "pass" in new
    assert len(edits) == 1


def test_comments_out_setloglevel_variable_arg() -> None:
    new, edits = _apply(
        """
        sc.setLogLevel(log_level_var)
        """
    )
    assert _MARKER in new
    assert "# sc.setLogLevel(log_level_var)" in new
    assert len(edits) == 1


# --------------------------------------------------------------------------
# Both in the same file
# --------------------------------------------------------------------------


def test_both_calls_commented_out_in_same_file() -> None:
    new, edits = _apply(
        """
        spark.sparkContext.setCheckpointDir("dbfs:/tmp/cp")
        spark.sparkContext.setLogLevel("WARN")
        x = 1
        """
    )
    assert new.count("pass") == 2
    assert "setCheckpointDir" in new
    assert "setLogLevel" in new
    # Surrounding code is untouched
    assert "x = 1" in new
    assert len(edits) == 2


# --------------------------------------------------------------------------
# Negative cases — must NOT match
# --------------------------------------------------------------------------


def test_no_match_unrelated_receiver_setloglevel() -> None:
    src = textwrap.dedent(
        """
        logger.setLogLevel("DEBUG")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_match_unrelated_receiver_setcheckpointdir() -> None:
    src = textwrap.dedent(
        """
        storage.setCheckpointDir("/my/path")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_match_assignment_form() -> None:
    """Assignment (return value captured) is NOT a standalone expr — skip."""
    src = textwrap.dedent(
        """
        result = sc.setCheckpointDir("/tmp")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_match_different_method_on_sc() -> None:
    """Other sc methods are NOT targeted."""
    src = textwrap.dedent(
        """
        sc.parallelize([1, 2, 3])
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_match_similar_name_on_dataframe() -> None:
    """A hypothetical .setLogLevel on a non-SC object must not trigger."""
    src = textwrap.dedent(
        """
        df.setLogLevel("INFO")
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_idempotent_setcheckpointdir() -> None:
    new1, edits1 = _apply(
        """
        spark.sparkContext.setCheckpointDir("dbfs:/tmp/checkpoints")
        """
    )
    assert len(edits1) == 1
    new2, edits2 = _apply(new1)
    assert new2 == new1
    assert edits2 == []


def test_idempotent_setloglevel() -> None:
    new1, edits1 = _apply(
        """
        sc.setLogLevel("WARN")
        """
    )
    assert len(edits1) == 1
    new2, edits2 = _apply(new1)
    assert new2 == new1
    assert edits2 == []


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------


def test_preserves_indentation_in_function() -> None:
    new, edits = _apply(
        """
        def setup():
            spark.sparkContext.setLogLevel("INFO")
            return spark
        """
    )
    assert _MARKER in new
    assert "pass" in new
    assert "return spark" in new
    assert len(edits) == 1


def test_self_sc_receiver() -> None:
    """``self.sc.setLogLevel(...)`` — self.sc is recognized as SC."""
    new, edits = _apply(
        """
        self.sc.setLogLevel("ERROR")
        """
    )
    assert _MARKER in new
    assert "pass" in new
    assert len(edits) == 1
