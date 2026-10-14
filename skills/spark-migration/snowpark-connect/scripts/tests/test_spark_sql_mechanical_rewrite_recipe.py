"""Unit tests for the ``spark_sql_mechanical_rewrite`` LibCST recipe.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_spark_sql_mechanical_rewrite_recipe.py
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
sys.path.insert(0, str(_RECIPES_DIR))
sys.path.insert(0, str(_RECIPES_DIR.parent))  # scripts/ for `rag`

from _common import load_recipe_module  # noqa: E402

_recipe = load_recipe_module(_RECIPES_DIR / "spark_sql_mechanical_rewrite")


def _apply(src: str):
    src = textwrap.dedent(src).lstrip("\n")
    res = _recipe.apply(src, file="t.py")
    return res.source, res.edits


# --- rewrites ----------------------------------------------------------------

def test_rewrites_grouping_sets_in_spark_sql():
    out, edits = _apply(
        '''
        df = spark.sql("SELECT a, b, SUM(v) FROM t GROUP BY a GROUPING SETS ((b), ())")
        '''
    )
    assert len(edits) == 1
    assert "(A, B)" in out.upper()  # plain GROUP BY col folded into the set
    assert "# SCOS: [SPRKCNTPY5400-Fixed] spark_sql_mechanical_rewrite" in out


def test_triple_quoted_multiline_sql_rewritten():
    out, edits = _apply(
        '''
        q = spark.sql("""
            SELECT a, b, SUM(v) FROM t GROUP BY a GROUPING SETS ((b), ())
        """)
        '''
    )
    assert len(edits) == 1
    assert "(A, B)" in out.upper()


def test_explain_ddl_in_spark_sql_dropped():
    out, edits = _apply(
        '''
        spark.sql("EXPLAIN CREATE TABLE t AS SELECT 1")
        '''
    )
    assert len(edits) == 1
    assert "CREATE TABLE" in out.upper()
    assert "EXPLAIN CREATE" not in out.upper()  # the EXPLAIN prefix is gone from the SQL


# --- dynamic / non-target → no-op -------------------------------------------

def test_fstring_sql_is_skipped():
    src = textwrap.dedent(
        '''
        tbl = "t"
        df = spark.sql(f"SELECT ROW_NUMBER() OVER (PARTITION BY x) FROM {tbl}")
        '''
    ).lstrip("\n")
    out, edits = _apply(src)
    assert edits == []
    assert out == src


def test_concatenated_dynamic_sql_is_skipped():
    src = textwrap.dedent(
        '''
        df = spark.sql("SELECT ROW_NUMBER() OVER (PARTITION BY x) FROM " + tbl)
        '''
    ).lstrip("\n")
    out, edits = _apply(src)
    assert edits == []
    assert out == src


def test_name_arg_sql_is_skipped():
    src = textwrap.dedent(
        '''
        df = spark.sql(query)
        '''
    ).lstrip("\n")
    out, edits = _apply(src)
    assert edits == []
    assert out == src


def test_already_compatible_sql_is_noop():
    src = textwrap.dedent(
        '''
        df = spark.sql("SELECT a, b FROM t WHERE a > 1")
        '''
    ).lstrip("\n")
    out, edits = _apply(src)
    assert edits == []
    assert out == src


def test_non_sql_method_call_is_noop():
    # `.sql` on something whose string isn't SCOS-incompatible SQL → rewriter
    # reports no change → recipe no-op.
    src = textwrap.dedent(
        '''
        x = obj.sql("not really sql here")
        '''
    ).lstrip("\n")
    out, edits = _apply(src)
    assert edits == []
    assert out == src


# --- residual TODO + idempotency --------------------------------------------

def test_residual_emits_todo_comment():
    # A mechanical GROUPING SETS fold (applied) alongside a judgment-heavy
    # window-missing-ORDER-BY (residual, not auto-rewritten) → the recipe both
    # rewrites and emits a TODO.
    out, edits = _apply(
        '''
        spark.sql("SELECT ROW_NUMBER() OVER (PARTITION BY x) AS rn, SUM(v) AS s FROM t GROUP BY a GROUPING SETS ((b), ())")
        '''
    )
    assert len(edits) == 1
    assert "# SCOS-TODO: [SPRKCNTPY5400-Error] spark_sql_mechanical_rewrite" in out
    assert "detector:window_without_order_by" in out


def test_idempotent():
    src = textwrap.dedent(
        '''
        df = spark.sql("SELECT a, b, SUM(v) FROM t GROUP BY a GROUPING SETS ((b), ())")
        '''
    ).lstrip("\n")
    first, e1 = _apply(src)
    assert len(e1) == 1
    second, e2 = _apply(first)
    assert e2 == []
    assert second == first
    assert second.count("# SCOS: [SPRKCNTPY5400-Fixed] spark_sql_mechanical_rewrite") == 1
