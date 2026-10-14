"""Unit tests for the ``display_to_show_rewrite`` LibCST recipe.

Exercises: positive trigger (rewrite + recorded edit), the negative cases that
must be left untouched, and idempotency. No sqlite needed -- ``record_edit`` is a
no-op without ``$SCOS_FACTS_DB`` and ``RecipeResult.edits`` is populated in
memory regardless.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_display_to_show_recipe.py
"""
from __future__ import annotations

from pathlib import Path

from recipes import _common  # provided on sys.path via pyproject pythonpath

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "display_to_show_rewrite"


def _apply(source: str):
    mod = _common.load_recipe_module(str(_RECIPES_DIR / _NAME))
    return mod.apply(source, file="t.py")


def _code_only(source: str) -> str:
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )


def test_rewrites_bare_display_of_name():
    res = _apply("display(df)\n")
    code = _code_only(res.source)
    assert "df.show()" in code
    assert "display(" not in code
    assert len(res.edits) == 1


def test_rewrites_display_of_chained_call():
    res = _apply("display(spark.table('t').filter(c))\n")
    code = _code_only(res.source)
    assert "spark.table('t').filter(c).show()" in code
    assert "display(" not in code


def test_negative_method_display_untouched():
    src = "obj.display(df)\n"
    res = _apply(src)
    assert "obj.display(df)" in res.source
    assert len(res.edits) == 0


def test_negative_no_arg_untouched():
    res = _apply("display()\n")
    assert "display()" in res.source
    assert len(res.edits) == 0


def test_negative_multi_arg_untouched():
    res = _apply("display(df1, df2)\n")
    assert "display(df1, df2)" in res.source
    assert len(res.edits) == 0


def test_negative_kwarg_untouched():
    res = _apply("display(df, streamName='s')\n")
    assert "display(df, streamName='s')" in res.source
    assert len(res.edits) == 0


def test_negative_non_trailerable_arg_untouched():
    # An operator expression would need parens before ``.show()``; skip it.
    res = _apply("display(a + b)\n")
    assert "display(a + b)" in res.source
    assert len(res.edits) == 0


def test_idempotent():
    first = _apply("display(df)\n").source
    second = _apply(first).source
    assert first == second
