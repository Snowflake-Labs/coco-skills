"""Tests for the .ipynb execution path in runtimes/_executor.py.

Exercises the real notebook exec surface — ``load_entrypoint_source``,
``_make_nb_run``, ``_load_entrypoint_module`` (the .ipynb branch), and the
``_is_clean_exit`` SystemExit predicate — WITHOUT needing real PySpark. Notebooks
reference a bare ``spark`` global, which the harness resolves via ``builtins.spark``
(set by ``run_and_capture`` before load); the tests emulate that with a FakeSpark.
"""

from __future__ import annotations

import builtins
import contextlib
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_HARNESS_DIR = os.path.join(os.path.dirname(_HERE), "harness")
if _HARNESS_DIR not in sys.path:
    sys.path.insert(0, _HARNESS_DIR)

from runtimes._executor import (  # noqa: E402
    _is_clean_exit,
    _load_entrypoint_module,
    _make_nb_run,
    _resolve_callable,
    load_entrypoint_source,
)


class FakeSpark:
    """Minimal spark stub that records .sql() calls."""

    def __init__(self):
        self.sql_calls: list[str] = []

    def sql(self, query: str):
        self.sql_calls.append(query)
        return self  # chainable dummy


@contextlib.contextmanager
def _bind_spark(spark):
    """Bind ``builtins.spark`` for the duration — mirrors run_and_capture."""
    had = hasattr(builtins, "spark")
    old = getattr(builtins, "spark", None)
    builtins.spark = spark
    try:
        yield
    finally:
        if had:
            builtins.spark = old
        elif hasattr(builtins, "spark"):
            delattr(builtins, "spark")


def _write_nb(path, *code_cells):
    cells = []
    for src in code_cells:
        cells.append({
            "cell_type": "code",
            "metadata": {},
            "outputs": [],
            "execution_count": None,
            "source": src.splitlines(keepends=True),
        })
    nb = {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(nb, f)
    return str(path)


# ---------------------------------------------------------------------------
# _load_entrypoint_module on .ipynb — SQL calls, globals, _nb_run injected
# ---------------------------------------------------------------------------


def test_load_ipynb_executes_sql_and_sets_globals(tmp_path):
    _write_nb(str(tmp_path / "job.ipynb"),
              "result = spark.sql('SELECT 1')",
              "%%sql\nCREATE TABLE t1 (id INT);\nSELECT id FROM t1;")

    fake_spark = FakeSpark()
    with _bind_spark(fake_spark):
        mod = _load_entrypoint_module(str(tmp_path), "job.ipynb")

    assert any("CREATE TABLE" in q for q in fake_spark.sql_calls), fake_spark.sql_calls
    assert any("SELECT id FROM t1" in q for q in fake_spark.sql_calls), fake_spark.sql_calls
    assert any("SELECT 1" in q for q in fake_spark.sql_calls), fake_spark.sql_calls
    assert "result" in mod.__dict__
    assert "_nb_run" in mod.__dict__


def test_load_ipynb_injects_module_globals_before_exec(tmp_path):
    """pre_exec_globals must be visible to the notebook's top-level code."""
    _write_nb(str(tmp_path / "g.ipynb"), "DERIVED = SCHEMA_NAME + '_x'")
    with _bind_spark(FakeSpark()):
        mod = _load_entrypoint_module(
            str(tmp_path), "g.ipynb", module_globals={"SCHEMA_NAME": "sch"}
        )
    assert mod.__dict__["DERIVED"] == "sch_x"


# ---------------------------------------------------------------------------
# .py path (importlib) still works — script + callable resolution
# ---------------------------------------------------------------------------


def test_load_py_script(tmp_path):
    (tmp_path / "script.py").write_text("MY_VAR = 42\nSECOND = MY_VAR + 1\n")
    mod = _load_entrypoint_module(str(tmp_path), "script.py")
    assert mod.MY_VAR == 42
    assert mod.SECOND == 43


def test_resolve_callable_found_and_missing(tmp_path):
    (tmp_path / "module.py").write_text("def go(spark):\n    return 1\n")
    mod = _load_entrypoint_module(str(tmp_path), "module.py")
    assert callable(_resolve_callable(mod, "go"))
    with pytest.raises(AttributeError):
        _resolve_callable(mod, "missing")


# ---------------------------------------------------------------------------
# _nb_run resolution — relative, nested, copy-paste semantics
# ---------------------------------------------------------------------------


def test_nb_run_resolves_relative_and_shares_namespace(tmp_path):
    _write_nb(str(tmp_path / "helpers_nb.ipynb"), "SHARED = 42")
    _write_nb(str(tmp_path / "main.ipynb"),
              "%run ./helpers_nb",
              "COMBINED = SHARED + 1")
    with _bind_spark(FakeSpark()):
        mod = _load_entrypoint_module(str(tmp_path), "main.ipynb")
    assert mod.__dict__.get("SHARED") == 42
    assert mod.__dict__.get("COMBINED") == 43


def test_nb_run_copy_paste_semantics_both_directions(tmp_path):
    """`%run` inlines the target: parent's prior globals are visible to the child,
    and the child's definitions are visible to the parent afterward."""
    _write_nb(str(tmp_path / "child.ipynb"),
              "CHILD_SAW_PARENT = PARENT_VAL + 1", "SHARED = 100")
    _write_nb(str(tmp_path / "main.ipynb"),
              "PARENT_VAL = 41", "%run ./child", "RESULT = SHARED + CHILD_SAW_PARENT")
    with _bind_spark(FakeSpark()):
        mod = _load_entrypoint_module(str(tmp_path), "main.ipynb")
    assert mod.__dict__.get("CHILD_SAW_PARENT") == 42
    assert mod.__dict__.get("SHARED") == 100
    assert mod.__dict__.get("RESULT") == 142


def test_nb_run_nested_subdirectory_resolution(tmp_path):
    sub_dir = tmp_path / "sub"
    sub_dir.mkdir()
    _write_nb(str(sub_dir / "level2.ipynb"), "DEEP_VAR = 99")
    _write_nb(str(sub_dir / "level1.ipynb"), "%run ./level2", "LEVEL1 = DEEP_VAR + 1")
    _write_nb(str(tmp_path / "main.ipynb"), "%run ./sub/level1", "FINAL = LEVEL1 + 1")
    with _bind_spark(FakeSpark()):
        mod = _load_entrypoint_module(str(tmp_path), "main.ipynb")
    assert mod.__dict__.get("DEEP_VAR") == 99
    assert mod.__dict__.get("LEVEL1") == 100
    assert mod.__dict__.get("FINAL") == 101


def test_nb_run_unresolvable_target_raises(tmp_path):
    _write_nb(str(tmp_path / "bad.ipynb"), "%run ./does_not_exist")
    with _bind_spark(FakeSpark()), pytest.raises(RuntimeError, match="cannot resolve target"):
        _load_entrypoint_module(str(tmp_path), "bad.ipynb")


def test_nb_run_circular_dependency_raises(tmp_path):
    _write_nb(str(tmp_path / "nb_a.ipynb"), "%run ./nb_b")
    _write_nb(str(tmp_path / "nb_b.ipynb"), "%run ./nb_a")
    with _bind_spark(FakeSpark()), pytest.raises(RuntimeError, match="circular dependency"):
        _load_entrypoint_module(str(tmp_path), "nb_a.ipynb")


def test_make_nb_run_resolves_against_workload_root(tmp_path):
    """_nb_run falls back to the workload root when the target isn't relative."""
    _write_nb(str(tmp_path / "shared_nb.ipynb"), "ROOT_VAR = 7")
    nb_run = _make_nb_run(str(tmp_path))
    ns: dict = {}
    nb_run("shared_nb", ns)
    assert ns.get("ROOT_VAR") == 7


# ---------------------------------------------------------------------------
# load_entrypoint_source
# ---------------------------------------------------------------------------


def test_load_entrypoint_source_ipynb_is_translated(tmp_path):
    nb_path = _write_nb(str(tmp_path / "nb.ipynb"), "x = 1")
    result = load_entrypoint_source(nb_path)
    assert "x = 1" in result
    assert "cell_type" not in result  # translated Python, not raw JSON


def test_load_entrypoint_source_py_is_verbatim(tmp_path):
    (tmp_path / "script.py").write_text("y = 2\n")
    assert load_entrypoint_source(str(tmp_path / "script.py")) == "y = 2\n"


# ---------------------------------------------------------------------------
# SystemExit handling — _is_clean_exit predicate + propagation contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code,clean", [(None, True), (0, True), (2, False), ("msg", False)])
def test_is_clean_exit(code, clean):
    assert _is_clean_exit(code) is clean


def test_load_ipynb_propagates_systemexit_for_caller_to_classify(tmp_path):
    """_load_entrypoint_module lets SystemExit propagate; run_and_capture then
    treats code 0/None as a clean finish and anything else as a failure."""
    _write_nb(str(tmp_path / "exits.ipynb"), "import sys", "sys.exit(2)")
    with _bind_spark(FakeSpark()), pytest.raises(SystemExit) as exc:
        _load_entrypoint_module(str(tmp_path), "exits.ipynb")
    assert exc.value.code == 2
    assert not _is_clean_exit(exc.value.code)


# ---------------------------------------------------------------------------
# %%sql -r result binding executes end-to-end and resolves downstream
# ---------------------------------------------------------------------------


def test_ipynb_sql_result_var_resolves_downstream(tmp_path):
    _write_nb(str(tmp_path / "bind.ipynb"),
              "%%sql -r my_result\nSELECT NAME FROM refs",
              "captured = my_result\nimplicit = _sqldf")
    fake_spark = FakeSpark()
    with _bind_spark(fake_spark):
        mod = _load_entrypoint_module(str(tmp_path), "bind.ipynb")
    # Both the named var and _sqldf resolve to the spark.sql result.
    assert mod.__dict__.get("my_result") is not None
    assert mod.__dict__.get("captured") is mod.__dict__.get("my_result")
    assert mod.__dict__.get("implicit") is mod.__dict__.get("my_result")
    assert any("SELECT NAME FROM refs" in q for q in fake_spark.sql_calls)


# ---------------------------------------------------------------------------
# %run child that exits cleanly ends only the child; parent continues
# ---------------------------------------------------------------------------


def test_nb_run_child_clean_exit_does_not_abort_parent(tmp_path):
    """A child notebook's clean sys.exit(0) unwinds only the child (Databricks
    dbutils.notebook.run semantics); the parent keeps executing."""
    _write_nb(str(tmp_path / "child.ipynb"),
              "import sys",
              "CHILD_RAN = 1",
              "sys.exit(0)  # was dbutils.notebook.exit('done')",
              "CHILD_AFTER_EXIT = 1")  # must NOT run
    _write_nb(str(tmp_path / "main.ipynb"),
              "%run ./child",
              "PARENT_AFTER = 1")  # must run despite child exit
    with _bind_spark(FakeSpark()):
        mod = _load_entrypoint_module(str(tmp_path), "main.ipynb")
    assert mod.__dict__.get("CHILD_RAN") == 1
    assert "CHILD_AFTER_EXIT" not in mod.__dict__       # child stopped at exit
    assert mod.__dict__.get("PARENT_AFTER") == 1        # parent continued


def test_nb_run_child_nonzero_exit_propagates(tmp_path):
    """A child's non-zero exit is a real failure and propagates to the parent."""
    _write_nb(str(tmp_path / "child.ipynb"), "import sys", "sys.exit(3)")
    _write_nb(str(tmp_path / "main.ipynb"), "%run ./child", "PARENT_AFTER = 1")
    with _bind_spark(FakeSpark()), pytest.raises(SystemExit) as exc:
        _load_entrypoint_module(str(tmp_path), "main.ipynb")
    assert exc.value.code == 3


# ---------------------------------------------------------------------------
# Databricks notebook-source .py entrypoint executes via the same path
# ---------------------------------------------------------------------------


def test_load_dbx_py_executes_magic_sql(tmp_path):
    """A dbx .py entrypoint's # MAGIC %sql cell runs as spark.sql (not a dead comment)."""
    dbx = (
        "# Databricks notebook source\n"
        "spark.sql('CREATE TABLE t (id INT)')\n"
        "# COMMAND ----------\n"
        "# MAGIC %sql\n"
        "# MAGIC SELECT id FROM t\n"
        "# COMMAND ----------\n"
        "done = True\n"
    )
    (tmp_path / "job.py").write_text(dbx, encoding="utf-8")
    fake_spark = FakeSpark()
    with _bind_spark(fake_spark):
        mod = _load_entrypoint_module(str(tmp_path), "job.py")
    assert any("SELECT id FROM t" in q for q in fake_spark.sql_calls), fake_spark.sql_calls
    assert mod.__dict__.get("done") is True
    assert "_sqldf" in mod.__dict__


def test_load_plain_py_still_uses_importlib(tmp_path):
    """A non-dbx .py entrypoint is imported normally (no notebook translation)."""
    (tmp_path / "plain.py").write_text("import os\nRESULT = 3 * 7\n", encoding="utf-8")
    mod = _load_entrypoint_module(str(tmp_path), "plain.py")
    assert mod.RESULT == 21


# ---------------------------------------------------------------------------
# Notebook migration: source .py <-> migrated .py.ipynb filename divergence.
# Phase B addresses Output/<name>.py but the migrated file is <name>.py.ipynb.
# ---------------------------------------------------------------------------


def test_load_entrypoint_resolves_py_ipynb_fallback(tmp_path):
    """entrypoint_path '<name>.py' loads '<name>.py.ipynb' when only that exists."""
    _write_nb(str(tmp_path / "job.py.ipynb"),
              "%%sql -r r\nSELECT 1 AS a",
              "captured = r")
    fake_spark = FakeSpark()
    with _bind_spark(fake_spark):
        # entrypoint_path uses the source-style .py name; Output only has .py.ipynb
        mod = _load_entrypoint_module(str(tmp_path), "job.py")
    assert mod.__dict__.get("captured") is not None
    assert any("SELECT 1 AS a" in q for q in fake_spark.sql_calls)


def test_plain_py_preferred_over_ipynb_when_both_exist(tmp_path):
    """When <name>.py exists it wins; the .py.ipynb fallback is only for absence."""
    (tmp_path / "job.py").write_text("RESULT = 5\n", encoding="utf-8")
    _write_nb(str(tmp_path / "job.py.ipynb"), "RESULT = 999")
    mod = _load_entrypoint_module(str(tmp_path), "job.py")
    assert mod.RESULT == 5


# ---------------------------------------------------------------------------
# %run include mechanism with a realistic COMMON_UTILS (both formats).
# Proves the child's function/constant definitions land in the parent namespace
# and the parent can call them (Databricks %run copy-paste semantics).
# ---------------------------------------------------------------------------

_COMMON_UTILS_BODY = (
    "def get_sf_connection_csmriskaggr():\n"
    "    return (None, {'sfSchema': 'TEST'})\n"
    "def load_snowflake_table(spark, sf_options, query=None):\n"
    "    return spark.sql(query)\n"
    "def trim_convert_to_uppercase(df):\n"
    "    return df\n"
    "COMMON_UTILS_MARKER = 'COMMON_UTILS_LOADED'\n"
)


def test_nb_run_includes_dbx_common_utils(tmp_path):
    """dbx .py: %run \"./COMMON_UTILS\" (quoted, no ext) resolves COMMON_UTILS.py
    and its defs are usable in the parent."""
    (tmp_path / "COMMON_UTILS.py").write_text(
        "# Databricks notebook source\n" + _COMMON_UTILS_BODY, encoding="utf-8"
    )
    (tmp_path / "main.py").write_text(
        "# Databricks notebook source\n"
        '# MAGIC %run "./COMMON_UTILS"\n'
        "# COMMAND ----------\n"
        "conn, opts = get_sf_connection_csmriskaggr()\n"
        "df = load_snowflake_table(spark, opts, query='SELECT 1 AS a')\n"
        "MARKER = COMMON_UTILS_MARKER\n",
        encoding="utf-8",
    )
    fake_spark = FakeSpark()
    with _bind_spark(fake_spark):
        mod = _load_entrypoint_module(str(tmp_path), "main.py")
    assert mod.__dict__.get("MARKER") == "COMMON_UTILS_LOADED"
    assert mod.__dict__.get("conn") is None
    assert any("SELECT 1 AS a" in q for q in fake_spark.sql_calls)


def test_nb_run_includes_ipynb_common_utils(tmp_path):
    """Migrated .ipynb: %run ./COMMON_UTILS.ipynb resolves and its defs are usable."""
    _write_nb(str(tmp_path / "COMMON_UTILS.ipynb"), _COMMON_UTILS_BODY)
    _write_nb(str(tmp_path / "main.ipynb"),
              "%run ./COMMON_UTILS.ipynb",
              "conn, opts = get_sf_connection_csmriskaggr()\n"
              "df = load_snowflake_table(spark, opts, query='SELECT 2 AS b')\n"
              "MARKER = COMMON_UTILS_MARKER")
    fake_spark = FakeSpark()
    with _bind_spark(fake_spark):
        mod = _load_entrypoint_module(str(tmp_path), "main.ipynb")
    assert mod.__dict__.get("MARKER") == "COMMON_UTILS_LOADED"
    assert any("SELECT 2 AS b" in q for q in fake_spark.sql_calls)


def test_nb_run_dbx_common_utils_via_sql_magic_cell(tmp_path):
    """A dbx # MAGIC %sql cell in the parent runs after the %run include —
    exercises %run + %sql translation together."""
    (tmp_path / "COMMON_UTILS.py").write_text(
        "# Databricks notebook source\n" + _COMMON_UTILS_BODY, encoding="utf-8"
    )
    (tmp_path / "main.py").write_text(
        "# Databricks notebook source\n"
        '# MAGIC %run "./COMMON_UTILS"\n'
        "# COMMAND ----------\n"
        "# MAGIC %sql\n"
        "# MAGIC SELECT COMMON_UTILS_MARKER_UNUSED FROM t\n"
        "# COMMAND ----------\n"
        "MARKER = COMMON_UTILS_MARKER\n",
        encoding="utf-8",
    )
    fake_spark = FakeSpark()
    with _bind_spark(fake_spark):
        mod = _load_entrypoint_module(str(tmp_path), "main.py")
    assert mod.__dict__.get("MARKER") == "COMMON_UTILS_LOADED"
    assert any("SELECT COMMON_UTILS_MARKER_UNUSED FROM t" in q for q in fake_spark.sql_calls)
