"""Unit tests for ``dataframe_partition_noop_strip_rewrite``.

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_partition_noop_strip_recipe.py
"""
from __future__ import annotations

from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "dataframe_partition_noop_strip_rewrite"


def _apply(source: str):
    return _common.load_recipe_module(str(_RECIPES_DIR / _NAME)).apply(source, file="t.py")


def _code(source: str) -> str:
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


def test_strip_coalesce_in_write_chain():
    res = _apply("shared_df.coalesce(1).write.parquet(p)\n")
    code = _code(res.source)
    assert "shared_df.write.parquet(p)" in code
    assert "coalesce" not in code
    assert len(res.edits) == 1


def test_strip_repartition_with_cols():
    res = _apply('df.repartition(10, "k").write.mode("overwrite").csv(p)\n')
    code = _code(res.source)
    assert "df.write.mode" in code
    assert "repartition" not in code


def test_strip_repartition_assignment():
    res = _apply("out = df.repartition(c)\n")
    assert "out = df" in _code(res.source)


def test_strip_repartition_by_range():
    res = _apply("x = df.repartitionByRange(8, col).write.parquet(p)\n")
    assert "repartitionByRange" not in _code(res.source)


def test_keep_F_coalesce_column_function():
    src = "df2 = df.withColumn('a', F.coalesce('a', F.lit('x')))\n"
    res = _apply(src)
    assert "F.coalesce('a', F.lit('x'))" in res.source  # untouched
    assert len(res.edits) == 0


def test_keep_lowercase_f_coalesce():
    src = "c = f.coalesce('x', 'y')\n"
    res = _apply(src)
    assert "f.coalesce('x', 'y')" in res.source
    assert len(res.edits) == 0


def test_mixed_df_coalesce_and_F_coalesce():
    # Outer DataFrame.coalesce stripped; inner F.coalesce kept.
    src = "out = df.select(F.coalesce('a', 'b')).coalesce(1)\n"
    res = _apply(src)
    code = _code(res.source)
    assert "F.coalesce('a', 'b')" in code  # column fn kept
    assert "out = df.select(F.coalesce('a', 'b'))" in code  # df.coalesce(1) stripped


def test_idempotent():
    first = _apply("df.coalesce(1).write.parquet(p)\n").source
    second = _apply(first).source
    assert first == second
