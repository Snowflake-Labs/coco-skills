"""Unit tests for ``delta_write_to_parquet_rewrite``.

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_delta_write_to_parquet_recipe.py
"""
from __future__ import annotations

from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "delta_write_to_parquet_rewrite"


def _apply(source: str):
    return _common.load_recipe_module(str(_RECIPES_DIR / _NAME)).apply(source, file="t.py")


def _code(source: str) -> str:
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


def test_plain_write_delta_to_parquet():
    res = _apply('df.write.format("delta").mode("overwrite").save(output_path)\n')
    code = _code(res.source)
    assert 'format("parquet")' in code
    assert '"delta"' not in code
    assert len(res.edits) == 1


def test_comment_explains_conversion():
    res = _apply('df.write.format("delta").save(p)\n')
    assert "not supported" in res.source and "Parquet" in res.source


def test_write_delta_after_mode():
    res = _apply('df.write.mode("overwrite").format("delta").save(p)\n')
    assert 'format("parquet")' in _code(res.source)


def test_read_delta_untouched():
    src = 'df = spark.read.format("delta").load(path)\n'
    res = _apply(src)
    assert 'format("delta")' in res.source  # read is NOT converted
    assert len(res.edits) == 0


def test_file_with_deltatable_api_skipped_entirely():
    # Even the plain write is left alone if the file uses the DeltaTable API.
    src = (
        'from delta.tables import DeltaTable\n'
        'df.write.format("delta").save(p)\n'
        'DeltaTable.forPath(spark, p).alias("t").merge(src, "t.id=s.id")\n'
    )
    res = _apply(src)
    assert 'format("delta")' in res.source
    assert 'format("parquet")' not in res.source
    assert len(res.edits) == 0


def test_non_delta_format_untouched():
    res = _apply('df.write.format("orc").save(p)\n')
    assert 'format("orc")' in res.source
    assert len(res.edits) == 0


def test_idempotent():
    first = _apply('df.write.format("delta").save(p)\n').source
    second = _apply(first).source
    assert first == second
