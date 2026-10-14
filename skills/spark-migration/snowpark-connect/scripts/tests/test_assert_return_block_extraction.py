"""Regression tests for the ``PySparkExtractor`` blind spot on ``assert`` and
``return`` statements.

Before ``visit_Assert``/``visit_Return`` existed, ``ast.Assert`` and
``ast.Return`` had no dedicated visitor, so a PySpark method chain wrapped in
either (e.g. ``assert df.select(...).collect() == [...]`` or
``return df.select(...)``) was invisible to block extraction entirely:
``visit_Call`` only recognises the narrow ``.sql()``/``expr()``/
``.selectExpr()`` shapes, and ``visit_Expr`` only fires for bare top-level
expression statements. This meant RAG/trigger-KB compatibility checks never
ran on any PySpark call inside an ``assert`` or ``return`` — a real, silently
missed class of code, and an especially damaging one given how common
``assert``-based test/validation suites are in PySpark migration workloads.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_assert_return_block_extraction.py
"""
from __future__ import annotations

import pytest

pytest.importorskip(
    "snowflake.snowpark",
    reason="analyze_pyspark needs the full SCOS dependency stack (CI only)",
)

from analyze_pyspark import extract_code_blocks_from_source  # noqa: E402


def _block_lines(src: str) -> list[tuple[int, int, str]]:
    return [(b.line_start, b.line_end, b.block_type) for b in extract_code_blocks_from_source(src)]


def test_assert_with_method_chain_is_extracted():
    src = (
        "assert df.select(F.array_sort(df.b, lambda x, y: F.abs(x) - F.abs(y))).collect() == [1]\n"
    )
    blocks = extract_code_blocks_from_source(src)
    assert any(b.block_type == "method_chain" for b in blocks), (
        "assert statement wrapping a .select(...) chain must produce a block"
    )
    assert any("array_sort" in b.functions for b in blocks)


def test_assert_without_pyspark_method_produces_no_block():
    src = "assert 1 + 1 == 2\n"
    blocks = extract_code_blocks_from_source(src)
    assert blocks == []


def test_multiline_assert_in_function_is_extracted():
    src = (
        "def test_foo():\n"
        "    df = spark.range(3)\n"
        "    assert _rows(\n"
        "        df.select(F.array_sort('a'))\n"
        "    ) == [1]\n"
    )
    blocks = extract_code_blocks_from_source(src)
    method_chain_blocks = [b for b in blocks if b.block_type == "method_chain"]
    assert method_chain_blocks, "multi-line assert with a select(...) chain must be extracted"
    # Anchored on the assert statement's own line range, not just node.test's.
    assert any(b.line_start == 3 for b in method_chain_blocks)


def test_assert_msg_with_method_chain_is_extracted():
    src = (
        "assert cond, f'got {df.select(F.col(\"x\")).collect()}'\n"
    )
    blocks = extract_code_blocks_from_source(src)
    assert any(b.block_type == "method_chain" for b in blocks), (
        "a method chain inside the assert message (2nd arg) must also be picked up"
    )


def test_return_with_method_chain_is_extracted():
    src = (
        "def build(df):\n"
        "    return df.select(F.array_sort('a')).where(F.col('a').isNotNull())\n"
    )
    blocks = extract_code_blocks_from_source(src)
    assert any(b.block_type == "method_chain" for b in blocks), (
        "return statement wrapping a .select(...) chain must produce a block"
    )


def test_return_none_does_not_crash():
    src = (
        "def f():\n"
        "    if True:\n"
        "        return\n"
    )
    # Bare `return` (node.value is None) must not raise.
    blocks = extract_code_blocks_from_source(src)
    assert blocks == []


def test_nested_sql_call_inside_assert_still_extracted_as_sql_block():
    """Guard against the fix regressing the pre-existing .sql()/.selectExpr()
    detection for calls nested inside an assert - visit_Call still runs via
    generic_visit regardless of the new visit_Assert."""
    src = 'assert spark.sql("SELECT 1").collect() == [1]\n'
    blocks = extract_code_blocks_from_source(src)
    assert any(b.block_type == "sql" for b in blocks)
