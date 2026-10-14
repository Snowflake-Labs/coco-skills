"""Unit tests for the ``dataframe_display_method_to_show_rewrite`` LibCST recipe.

Exercises: positive trigger (rewrite + recorded edit) for the zero-arg
``<df>.display()`` method form, the negative cases that must be left untouched
(global ``display(df)``, method-with-args, kwargs, attribute-only), and
idempotency. No sqlite needed -- ``record_edit`` is a no-op without
``$SCOS_FACTS_DB`` and ``RecipeResult.edits`` is populated in memory regardless.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_dataframe_display_method_to_show_recipe.py
"""
from __future__ import annotations

from pathlib import Path

from recipes import _common  # provided on sys.path via pyproject pythonpath

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "dataframe_display_method_to_show_rewrite"


def _apply(source: str):
    mod = _common.load_recipe_module(str(_RECIPES_DIR / _NAME))
    return mod.apply(source, file="t.py")


def _code_only(source: str) -> str:
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )


def test_rewrites_bare_method_display_of_name():
    res = _apply("df.display()\n")
    code = _code_only(res.source)
    assert "df.show()" in code
    assert ".display()" not in code
    assert len(res.edits) == 1


def test_rewrites_method_display_of_chained_call():
    res = _apply("spark.table('t').filter(c).display()\n")
    code = _code_only(res.source)
    assert "spark.table('t').filter(c).show()" in code
    assert ".display()" not in code


def test_rewrites_method_display_of_attribute_receiver():
    res = _apply("self.df.display()\n")
    code = _code_only(res.source)
    assert "self.df.show()" in code
    assert ".display()" not in code


def test_negative_global_display_untouched():
    # The global helper form is owned by display_to_show_rewrite.
    res = _apply("display(df)\n")
    assert "display(df)" in res.source
    assert len(res.edits) == 0


def test_negative_method_display_with_arg_untouched():
    # The Databricks renderer takes no args; an argument means a different API.
    res = _apply("obj.display(df)\n")
    assert "obj.display(df)" in res.source
    assert len(res.edits) == 0


def test_negative_method_display_with_kwarg_untouched():
    res = _apply("obj.display(streamName='s')\n")
    assert "obj.display(streamName='s')" in res.source
    assert len(res.edits) == 0


def test_negative_attribute_only_untouched():
    res = _apply("handler = obj.display\n")
    assert "obj.display" in res.source
    assert ".show" not in res.source
    assert len(res.edits) == 0


def test_idempotent():
    first = _apply("df.display()\n").source
    second = _apply(first).source
    assert first == second
