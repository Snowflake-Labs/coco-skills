"""Unit tests for the ``window_no_order_by`` condition in ``static_condition_pass``.

Covers the #3375 forward-port onto the kb_rules architecture: windowed
functions (row_number / lead / first_value) that *require* an ORDER BY in Spark
should only be flagged (met=True) when the window provably lacks an ORDER BY;
a window that demonstrably HAS ``.orderBy(...)`` clears the false positive
(met=False), and an unresolved window variable stays indeterminate (met=None).

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_static_condition_window.py
"""
from __future__ import annotations

import static_condition_pass as sc

_COND = {"row_number": "window_no_order_by"}


def _verdict(src: str, fn: str = "row_number", assignment_src: str | None = None):
    """Return the summarized verdict for ``fn``: 'met' | 'cleared' | 'indeterminate'."""
    occ = sc._scan_python(src, _COND, "t.py", assignment_src=assignment_src)
    mets = [o["met"] for o in occ if o["function"] == fn]
    assert mets, f"no {fn} occurrence detected in:\n{src}"
    if any(m is True for m in mets):
        return "met"
    if any(m is None for m in mets):
        return "indeterminate"
    return "cleared"


# --- catalog wiring --------------------------------------------------------

def test_loader_maps_window_fns_to_window_no_order_by():
    cond = sc._load_conditional_fns()  # bundled kb_rules.json (edited)
    for fn in ("row_number", "lead", "first_value"):
        assert cond.get(fn) == "window_no_order_by", (fn, cond.get(fn))


# --- inline window ---------------------------------------------------------

def test_inline_window_with_orderby_is_cleared():
    src = 'df = df.withColumn("rn", row_number().over(Window.partitionBy("a").orderBy("b")))\n'
    assert _verdict(src) == "cleared"


def test_inline_window_partitionby_only_is_met():
    src = 'df = df.withColumn("rn", row_number().over(Window.partitionBy("a")))\n'
    assert _verdict(src) == "met"


def test_not_windowed_is_cleared():
    # row_number() with no .over(...) cannot hit the divergence.
    src = 'x = row_number()\n'
    assert _verdict(src) == "cleared"


# --- named window variable, same block -------------------------------------

def test_named_window_with_orderby_same_block_cleared():
    src = (
        'w = Window.partitionBy("a").orderBy("b")\n'
        'df = df.withColumn("rn", row_number().over(w))\n'
    )
    assert _verdict(src) == "cleared"


def test_named_window_partitionby_only_same_block_met():
    src = (
        'w = Window.partitionBy("a")\n'
        'df = df.withColumn("rn", row_number().over(w))\n'
    )
    assert _verdict(src) == "met"


# --- cross-block window variable (needs whole-file assignment_src) ---------

def test_named_window_orderby_cross_block_cleared():
    # The usage block does NOT define w; the whole file (assignment_src) does.
    whole_file = (
        'base = Window.partitionBy("a")\n'
        'w = base.orderBy(F.col("b"))\n'
        'def build(df):\n'
        '    return df.withColumn("rn", row_number().over(w))\n'
    )
    block = 'def build(df):\n    return df.withColumn("rn", row_number().over(w))\n'
    # Without whole-file source, w is unresolved -> indeterminate.
    assert _verdict(block) == "indeterminate"
    # With whole-file source, w resolves to an ORDER BY -> cleared.
    assert _verdict(block, assignment_src=whole_file) == "cleared"


def test_named_window_partitionby_cross_block_met():
    whole_file = (
        'w = Window.partitionBy("a")\n'
        'def build(df):\n'
        '    return df.withColumn("rn", row_number().over(w))\n'
    )
    block = 'def build(df):\n    return df.withColumn("rn", row_number().over(w))\n'
    assert _verdict(block, assignment_src=whole_file) == "met"


# --- unresolved variable stays indeterminate -------------------------------

def test_unresolved_window_variable_indeterminate():
    src = 'df = df.withColumn("rn", row_number().over(some_imported_window))\n'
    assert _verdict(src) == "indeterminate"


# --- SQL surface -----------------------------------------------------------

def test_sql_window_with_orderby_cleared():
    q = "SELECT ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) rn FROM t"
    occ = sc._scan_sql(q, _COND, "t.sql")
    assert occ and all(o["met"] is False for o in occ)


def test_sql_window_partitionby_only_met():
    q = "SELECT ROW_NUMBER() OVER (PARTITION BY a) rn FROM t"
    occ = sc._scan_sql(q, _COND, "t.sql")
    assert occ and any(o["met"] is True for o in occ)


# --- summarize clearability ------------------------------------------------

def test_summarize_clearable_when_all_cleared():
    src = 'df = df.withColumn("rn", row_number().over(Window.partitionBy("a").orderBy("b")))\n'
    occ = sc._scan_python(src, _COND, "t.py")
    summary = sc.summarize(occ)
    assert "row_number" in summary["clearable"]
    assert "row_number" not in summary["confirmed"]


def test_summarize_confirmed_blocks_clear():
    # One cleared + one met in the same file -> NOT clearable (met wins).
    src = (
        'df = df.withColumn("rn1", row_number().over(Window.partitionBy("a").orderBy("b")))\n'
        'df = df.withColumn("rn2", row_number().over(Window.partitionBy("a")))\n'
    )
    occ = sc._scan_python(src, _COND, "t.py")
    summary = sc.summarize(occ)
    assert "row_number" in summary["confirmed"]
    assert "row_number" not in summary["clearable"]
