"""Unit tests for the SAFE-ALWAYS autofix recipes that drop unsupported args.

Covers:
- approx_count_distinct_drop_rsd_rewrite
- unpersist_drop_blocking_arg_rewrite
- toLocalIterator_drop_prefetch_arg_rewrite

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_safe_always_arg_drop_recipes.py
"""
from __future__ import annotations

from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"


def _apply(name: str, source: str):
    return _common.load_recipe_module(str(_RECIPES_DIR / name)).apply(source, file="t.py")


def _code(source: str) -> str:
    """Drop comment-only lines for cleaner assertions."""
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


def _assert_idempotent(name: str, source: str) -> str:
    first = _apply(name, source).source
    second = _apply(name, first).source
    assert second == first, f"{name} not idempotent"
    return first


# --------------------------------------------------------------------------- #
# approx_count_distinct_drop_rsd_rewrite
# --------------------------------------------------------------------------- #
_ACD_NAME = "approx_count_distinct_drop_rsd_rewrite"


def test_acd_drops_positional_rsd():
    src = "result = F.approx_count_distinct(col('id'), 0.05)\n"
    res = _apply(_ACD_NAME, src)
    code = _code(res.source)
    assert "F.approx_count_distinct(col('id'))" in code
    assert "0.05" not in code
    assert len(res.edits) == 1


def test_acd_drops_keyword_rsd():
    src = "result = F.approx_count_distinct(col('id'), rsd=0.01)\n"
    res = _apply(_ACD_NAME, src)
    code = _code(res.source)
    assert "F.approx_count_distinct(col('id'))" in code
    assert "rsd" not in code
    assert len(res.edits) == 1


def test_acd_drops_from_approxCountDistinct():
    src = "x = approxCountDistinct(df.col, 0.05)\n"
    res = _apply(_ACD_NAME, src)
    code = _code(res.source)
    assert "approxCountDistinct(df.col)" in code
    assert "0.05" not in code
    assert len(res.edits) == 1


def test_acd_no_match_single_arg():
    src = "result = F.approx_count_distinct(col('id'))\n"
    res = _apply(_ACD_NAME, src)
    assert res.source == src
    assert len(res.edits) == 0


def test_acd_no_match_different_function():
    src = "result = F.count_distinct(col('a'), col('b'))\n"
    res = _apply(_ACD_NAME, src)
    assert res.source == src
    assert len(res.edits) == 0


def test_acd_idempotent():
    _assert_idempotent(_ACD_NAME, "result = F.approx_count_distinct(col('id'), 0.05)\n")


# --------------------------------------------------------------------------- #
# unpersist_drop_blocking_arg_rewrite
# --------------------------------------------------------------------------- #
_UNPERSIST_NAME = "unpersist_drop_blocking_arg_rewrite"


def test_unpersist_drops_keyword_blocking():
    src = "df.unpersist(blocking=True)\n"
    res = _apply(_UNPERSIST_NAME, src)
    code = _code(res.source)
    assert "df.unpersist()" in code
    assert "blocking" not in code
    assert len(res.edits) == 1


def test_unpersist_drops_positional_true():
    src = "df.unpersist(True)\n"
    res = _apply(_UNPERSIST_NAME, src)
    code = _code(res.source)
    assert "df.unpersist()" in code
    assert "True" not in code
    assert len(res.edits) == 1


def test_unpersist_drops_positional_false():
    src = "cached_df.unpersist(False)\n"
    res = _apply(_UNPERSIST_NAME, src)
    code = _code(res.source)
    assert "cached_df.unpersist()" in code
    assert len(res.edits) == 1


def test_unpersist_no_match_zero_args():
    src = "df.unpersist()\n"
    res = _apply(_UNPERSIST_NAME, src)
    assert res.source == src
    assert len(res.edits) == 0


def test_unpersist_no_match_different_method():
    src = "df.persist(StorageLevel.MEMORY_AND_DISK)\n"
    res = _apply(_UNPERSIST_NAME, src)
    assert res.source == src
    assert len(res.edits) == 0


def test_unpersist_idempotent():
    _assert_idempotent(_UNPERSIST_NAME, "df.unpersist(blocking=True)\n")


# --------------------------------------------------------------------------- #
# toLocalIterator_drop_prefetch_arg_rewrite
# --------------------------------------------------------------------------- #
_TLI_NAME = "toLocalIterator_drop_prefetch_arg_rewrite"


def test_tli_drops_keyword_prefetch():
    src = "it = df.toLocalIterator(prefetchPartitions=True)\n"
    res = _apply(_TLI_NAME, src)
    code = _code(res.source)
    assert "df.toLocalIterator()" in code
    assert "prefetchPartitions" not in code
    assert len(res.edits) == 1


def test_tli_drops_positional_true():
    src = "it = df.toLocalIterator(True)\n"
    res = _apply(_TLI_NAME, src)
    code = _code(res.source)
    assert "df.toLocalIterator()" in code
    assert "True" not in code
    assert len(res.edits) == 1


def test_tli_drops_positional_false():
    src = "rows = df.toLocalIterator(False)\n"
    res = _apply(_TLI_NAME, src)
    code = _code(res.source)
    assert "df.toLocalIterator()" in code
    assert len(res.edits) == 1


def test_tli_no_match_zero_args():
    src = "it = df.toLocalIterator()\n"
    res = _apply(_TLI_NAME, src)
    assert res.source == src
    assert len(res.edits) == 0


def test_tli_no_match_different_method():
    src = "it = df.collect()\n"
    res = _apply(_TLI_NAME, src)
    assert res.source == src
    assert len(res.edits) == 0


def test_tli_idempotent():
    _assert_idempotent(_TLI_NAME, "it = df.toLocalIterator(prefetchPartitions=True)\n")
