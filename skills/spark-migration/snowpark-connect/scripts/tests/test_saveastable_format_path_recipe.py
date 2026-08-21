"""Unit tests for the
``saveastable_drop_format_path_kwargs_rewrite`` LibCST recipe.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_saveastable_format_path_recipe.py
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

# Make ``scripts/recipes`` importable so we can load the recipe module
# directly (same pattern preprocess_recipes.py uses).
_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
sys.path.insert(0, str(_RECIPES_DIR))

from _common import load_recipe_module  # noqa: E402

_RECIPE_DIR = _RECIPES_DIR / "saveastable_drop_format_path_kwargs_rewrite"
_recipe = load_recipe_module(_RECIPE_DIR)


def _apply(src: str):
    """Convenience: run the recipe and return ``(new_source, edits)``."""
    src = textwrap.dedent(src).lstrip("\n")
    res = _recipe.apply(src, file="t.py")
    return res.source, res.edits


def _code_only(src: str) -> str:
    """Strip ``# SCOS: ...`` recipe comment lines so kwarg-presence assertions
    don't accidentally match the recipe's leading comment."""
    return "\n".join(
        line for line in src.splitlines() if "# SCOS:" not in line
    )


# --------------------------------------------------------------------------
# Positive cases — recipe must rewrite
# --------------------------------------------------------------------------


def test_drops_format_kwarg_alone() -> None:
    new, edits = _apply(
        """
        df.write.saveAsTable('schema.t', format='parquet')
        """
    )
    code = _code_only(new)
    assert "format=" not in code
    assert "saveAsTable('schema.t')" in code
    assert _recipe.RECIPE_ID in new  # leading comment present
    assert len(edits) == 1


def test_drops_path_kwarg_alone() -> None:
    new, edits = _apply(
        """
        df.write.saveAsTable('schema.t', path='hdfs://nn/wh/schema.t')
        """
    )
    code = _code_only(new)
    assert "path=" not in code
    assert "saveAsTable('schema.t')" in code
    assert len(edits) == 1


def test_drops_both_format_and_path_keeps_mode() -> None:
    new, edits = _apply(
        """
        df.write.saveAsTable(
            'schema.t',
            format='parquet',
            mode='overwrite',
            path='hdfs://nn/wh/schema.t',
        )
        """
    )
    code = _code_only(new)
    assert "format=" not in code
    assert "path=" not in code
    assert "mode='overwrite'" in code  # mode kwarg preserved
    assert "saveAsTable" in code
    assert len(edits) == 1


def test_preserves_partitionby_kwarg() -> None:
    new, _ = _apply(
        """
        df.write.saveAsTable('t', format='parquet', partitionBy=['country', 'year'])
        """
    )
    code = _code_only(new)
    assert "format=" not in code
    assert "partitionBy=['country', 'year']" in code


def test_chained_writer_options_receiver_preserved() -> None:
    new, edits = _apply(
        """
        df.write.option('header', 'true').saveAsTable('t', format='parquet')
        """
    )
    # Receiver chain (``df.write.option(...)``) must be left untouched.
    assert "df.write.option('header', 'true').saveAsTable('t')" in new
    assert len(edits) == 1


# --------------------------------------------------------------------------
# Negative cases — recipe must NOT fire
# --------------------------------------------------------------------------


def test_no_change_when_no_format_or_path() -> None:
    src = textwrap.dedent(
        """
        df.write.saveAsTable('schema.t')
        df.write.saveAsTable('schema.t', mode='overwrite')
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


def test_no_change_for_format_chained_method() -> None:
    src = textwrap.dedent(
        """
        df.write.format('parquet').saveAsTable('t')
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    # ``.format('parquet')`` is a chained writer call, not a saveAsTable
    # kwarg. A separate recipe owns that surface.
    assert new == src
    assert edits == []


def test_no_change_for_unrelated_method() -> None:
    src = textwrap.dedent(
        """
        df.write.save('out', format='parquet', path='/tmp/x')
        rdd.saveAsTextFile('/tmp/x')
        """
    ).lstrip("\n")
    new, edits = _apply(src)
    assert new == src
    assert edits == []


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_idempotent_second_pass_is_noop() -> None:
    new1, edits1 = _apply(
        """
        df.write.saveAsTable('t', format='parquet', path='/tmp/x')
        """
    )
    assert len(edits1) == 1

    new2, edits2 = _apply(new1)
    # Second pass must not change source and must not record another edit
    # (the `format=`/`path=` kwargs are gone, so the trigger no longer matches).
    assert new2 == new1
    assert edits2 == []


def test_two_calls_in_same_module_both_rewritten() -> None:
    new, edits = _apply(
        """
        df_a.write.saveAsTable('a', format='parquet')
        df_b.write.saveAsTable('b', path='/tmp/b')
        """
    )
    code = _code_only(new)
    assert "format=" not in code
    assert "path=" not in code
    assert "saveAsTable('a')" in code
    assert "saveAsTable('b')" in code
    # One edit per *statement*, two statements => two edits.
    assert len(edits) == 2
