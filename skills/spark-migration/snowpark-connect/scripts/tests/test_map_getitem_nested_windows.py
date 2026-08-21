"""Tests for map getItem -> element_at, nested-window annotate, ARRAY map keys.

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_map_getitem_nested_windows.py
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"


def _apply(name: str, src: str) -> str:
    src = textwrap.dedent(src).lstrip("\n")
    return _common.load_recipe_module(str(_RECIPES_DIR / name)).apply(
        src, file="t.py"
    ).source


def test_getitem_to_element_at():
    src = """\
        from pyspark.sql import functions as F
        x = F.create_map(F.lit("a"), F.lit(1)).getItem(F.col("k"))
    """
    out = _apply("map_column_subscript_colkey_to_element_at_rewrite", src)
    assert "getItem" not in out
    assert "element_at(" in out
    assert "from pyspark.sql.functions import element_at" in out
    assert _apply("map_column_subscript_colkey_to_element_at_rewrite", out) == out


def test_array_getitem_not_rewritten():
    src = """\
        from pyspark.sql import functions as F
        x = F.col("arr").getItem(F.col("i"))
    """
    out = _apply("map_column_subscript_colkey_to_element_at_rewrite", src)
    assert "getItem" in out
    assert "element_at(" not in out


def test_literal_getitem_not_rewritten():
    src = """\
        from pyspark.sql import functions as F
        x = F.create_map(F.lit("a"), F.lit(1)).getItem("a")
    """
    out = _apply("map_column_subscript_colkey_to_element_at_rewrite", src)
    assert 'getItem("a")' in out
    assert "element_at(" not in out


def test_nested_window_flagged():
    src = """\
        from pyspark.sql import functions as F, Window
        w = Window.partitionBy("k").orderBy("t")
        x = F.lag(F.last("v").over(w), 1).over(w)
    """
    out = _apply("nested_window_functions_annotate", src)
    assert "SPRKCNTPY5300" in out
    assert "nested_window_functions_annotate" in out
    assert _apply("nested_window_functions_annotate", out) == out


def test_plain_window_not_flagged():
    src = """\
        from pyspark.sql import functions as F, Window
        w = Window.partitionBy("k").orderBy("t")
        x = F.sum("v").over(w)
    """
    out = _apply("nested_window_functions_annotate", src)
    assert "SCOS-TODO" not in out


def test_create_map_collect_list_flagged():
    src = """\
        from pyspark.sql import functions as F
        m = F.create_map(F.collect_list("k"), F.collect_list("v"))
    """
    out = _apply("create_map_array_keys_annotate", src)
    assert "SPRKCNTPY1000" in out
    assert "create_map_array_keys_annotate" in out
    assert _apply("create_map_array_keys_annotate", out) == out


def test_map_from_arrays_not_flagged():
    src = """\
        from pyspark.sql import functions as F
        m = F.map_from_arrays(F.collect_list("k"), F.collect_list("v"))
    """
    out = _apply("create_map_array_keys_annotate", src)
    assert "SCOS-TODO" not in out
    assert out == textwrap.dedent(src).lstrip("\n")
