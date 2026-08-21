"""Tests for scripts/harness/notebook_source.py — notebook-to-Python translator."""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile

# Ensure harness dir is importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
_HARNESS_DIR = os.path.join(os.path.dirname(_HERE), "harness")
if _HARNESS_DIR not in sys.path:
    sys.path.insert(0, _HARNESS_DIR)

import notebook_source  # noqa: E402


def _make_nb(*code_cells, markdown_cells=None):
    """Build a minimal notebook dict."""
    cells = []
    for src in code_cells:
        cells.append({
            "cell_type": "code",
            "metadata": {},
            "outputs": [],
            "execution_count": None,
            "source": src.splitlines(keepends=True),
        })
    if markdown_cells:
        for md in markdown_cells:
            cells.append({
                "cell_type": "markdown",
                "metadata": {},
                "source": md.splitlines(keepends=True),
            })
    return {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}


def _write_nb(path, *code_cells, **kwargs):
    """Write a notebook to disk."""
    nb = _make_nb(*code_cells, **kwargs)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(nb, f)
    return path


# ---------------------------------------------------------------------------
# Code cell concatenation
# ---------------------------------------------------------------------------


def test_code_cell_concatenation_order():
    nb = _make_nb("x = 1", "y = 2", "z = x + y")
    result = notebook_source.notebook_dict_to_python(nb)
    lines = result.split("\n")
    assert "x = 1" in result
    assert "y = 2" in result
    assert "z = x + y" in result
    # Order: x=1 before y=2 before z=...
    assert result.index("x = 1") < result.index("y = 2") < result.index("z = x + y")


def test_non_code_cells_skipped():
    nb = _make_nb("x = 1")
    nb["cells"].insert(0, {
        "cell_type": "markdown",
        "metadata": {},
        "source": ["# This is markdown\n", "Some text\n"],
    })
    nb["cells"].append({
        "cell_type": "raw",
        "metadata": {},
        "source": ["raw content\n"],
    })
    result = notebook_source.notebook_dict_to_python(nb)
    assert "x = 1" in result
    assert "markdown" not in result.lower() or "notebook-magic" in result
    assert "raw content" not in result


# ---------------------------------------------------------------------------
# %%sql cell magic
# ---------------------------------------------------------------------------


def test_sql_cell_magic_single_statement():
    nb = _make_nb("%%sql\nSELECT * FROM orders")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "spark.sql(" in result
    assert "SELECT * FROM orders" in result
    ast.parse(result)


def test_sql_cell_magic_multi_statement():
    nb = _make_nb("%%sql\nCREATE TABLE t1 (id INT);\nINSERT INTO t1 VALUES (1);")
    result = notebook_source.notebook_dict_to_python(nb)
    assert result.count("spark.sql(") == 2
    assert "CREATE TABLE t1 (id INT)" in result
    assert "INSERT INTO t1 VALUES (1)" in result
    ast.parse(result)


def test_sql_cell_magic_empty_trailing_semicolons():
    nb = _make_nb("%%sql\nSELECT 1;\n;\n")
    result = notebook_source.notebook_dict_to_python(nb)
    # Only one real statement.
    assert result.count("spark.sql(") == 1
    ast.parse(result)


def test_sql_cell_magic_semicolon_in_string_literal_not_split():
    """A `;` inside a single-quoted SQL string literal must NOT split statements."""
    nb = _make_nb(
        "%%sql\n"
        "SELECT order_id,\n"
        "  CASE WHEN status = 'shipped; delivered' THEN 'done' ELSE status END AS s\n"
        "FROM orders"
    )
    result = notebook_source.notebook_dict_to_python(nb)
    # One statement only — the embedded ';' is part of the string, not a separator.
    assert result.count("spark.sql(") == 1
    assert "shipped; delivered" in result
    ast.parse(result)


def test_sql_cell_magic_multi_statement_with_embedded_semicolon():
    """Real separators split; semicolons inside literals do not."""
    nb = _make_nb(
        "%%sql\n"
        "CREATE TEMP VIEW v AS SELECT * FROM t WHERE label = 'a;b';\n"
        "SELECT count(*) FROM v"
    )
    result = notebook_source.notebook_dict_to_python(nb)
    assert result.count("spark.sql(") == 2
    assert "'a;b'" in result
    ast.parse(result)


def test_split_sql_statements_helper():
    """Direct unit test of the quote-aware splitter."""
    split = notebook_source._split_sql_statements
    assert split("SELECT 1; SELECT 2") == ["SELECT 1", "SELECT 2"]
    assert split("SELECT 'x;y'") == ["SELECT 'x;y'"]
    assert split("A WHERE c = 'p;q'; B") == ["A WHERE c = 'p;q'", "B"]
    # Escaped quote inside a literal ('') does not close the span early.
    assert split("SELECT 'it''s; fine'") == ["SELECT 'it''s; fine'"]
    # Trailing/empty statements dropped.
    assert split("SELECT 1;;") == ["SELECT 1"]



def test_sql_cell_magic_with_args():
    """%%sql -d mydb style cell magics with trailing args are still recognized."""
    nb = _make_nb("%%sql -d mydb\nSELECT 1")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "spark.sql(" in result
    assert "SELECT 1" in result
    ast.parse(result)


def test_sql_cell_magic_with_long_args():
    """%%sql --database=prod --timeout=60 is still recognized as SQL."""
    nb = _make_nb("%%sql --database=prod --timeout=60\nSELECT name FROM users")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "spark.sql(" in result
    assert "SELECT name FROM users" in result
    ast.parse(result)


# ---------------------------------------------------------------------------
# %sql line magic
# ---------------------------------------------------------------------------


def test_sql_line_magic():
    nb = _make_nb("%sql SELECT * FROM users WHERE active = 1")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "spark.sql(" in result
    assert "SELECT * FROM users WHERE active = 1" in result
    ast.parse(result)


def test_sql_line_magic_multiline_body():
    nb = _make_nb("%sql\nSELECT *\nFROM orders\nWHERE amount > 100;")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "spark.sql(" in result
    assert "orders" in result
    ast.parse(result)


# ---------------------------------------------------------------------------
# %run
# ---------------------------------------------------------------------------


def test_run_magic():
    nb = _make_nb("%run ./common/config")
    result = notebook_source.notebook_dict_to_python(nb)
    assert '_nb_run("./common/config", globals())' in result
    ast.parse(result)


def test_run_magic_with_quotes():
    nb = _make_nb('%run "./utils/helpers"')
    result = notebook_source.notebook_dict_to_python(nb)
    assert "_nb_run(" in result
    assert "utils/helpers" in result
    ast.parse(result)


def test_run_magic_drops_widget_args_and_flags():
    # Databricks %run passes widget args after the path; _nb_run can't forward
    # them, so only the path becomes the target and the args are flagged.
    nb = _make_nb('%run ./shared_utils $env="prod"')
    result = notebook_source.notebook_dict_to_python(nb)
    assert '_nb_run("./shared_utils", globals())' in result
    assert "NEEDS-REVIEW" in result
    assert '$env="prod"' in result
    ast.parse(result)


def test_run_magic_quoted_path_with_trailing_args():
    nb = _make_nb('%run "./My Notebook" x=1')
    result = notebook_source.notebook_dict_to_python(nb)
    assert '_nb_run("./My Notebook", globals())' in result
    assert "NEEDS-REVIEW" in result
    ast.parse(result)


# ---------------------------------------------------------------------------
# dbutils.notebook.run
# ---------------------------------------------------------------------------


def test_dbutils_notebook_run():
    nb = _make_nb('result = dbutils.notebook.run("etl/load_data", 0)')
    result = notebook_source.notebook_dict_to_python(nb)
    assert '_nb_run("etl/load_data", globals())' in result
    ast.parse(result)


def test_dbutils_run_variable_first_arg_passed_through():
    # First arg is a variable (not a string literal): pass the expression through
    # so _nb_run resolves the path at runtime — do NOT grab a later kwarg string.
    nb = _make_nb('nb_path = "child"\ndbutils.notebook.run(nb_path, 60, {"k": "v"})')
    result = notebook_source.notebook_dict_to_python(nb)
    assert "_nb_run(nb_path, globals())" in result
    assert '_nb_run("v"' not in result
    assert '_nb_run("k"' not in result
    ast.parse(result)


def test_dbutils_run_string_first_arg_ignores_kwarg_strings():
    # A string literal in a kwargs dict must not be mistaken for the target.
    nb = _make_nb('dbutils.notebook.run("child_nb", 60, {"other": "notafile"})')
    result = notebook_source.notebook_dict_to_python(nb)
    assert '_nb_run("child_nb", globals())' in result
    assert "notafile" not in result
    ast.parse(result)


# ---------------------------------------------------------------------------
# Other magics → pass  # notebook-magic
# ---------------------------------------------------------------------------


def test_pip_magic():
    nb = _make_nb("%pip install pandas\nimport pandas as pd")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "pass  # notebook-magic" in result
    assert "import pandas as pd" in result
    assert "%pip" not in result
    ast.parse(result)


def test_shell_escape():
    nb = _make_nb("!ls -la")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "pass  # notebook-magic" in result
    assert "!ls" not in result
    ast.parse(result)


def test_md_magic():
    nb = _make_nb("%md # Section Header")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "pass  # notebook-magic" in result
    ast.parse(result)


def test_md_mid_cell_neutralizes_rest_of_cell():
    # A %md appearing after code must neutralize the remaining markdown lines,
    # otherwise they would leak through as bare Python and fail to compile.
    nb = _make_nb("x = 1\n%md\n# Heading\nSome *markdown* prose, not python!")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "x = 1" in result
    assert "pass  # notebook-magic" in result
    # The prose survives only as a comment, never as bare (uncompilable) Python.
    assert "# Some *markdown* prose, not python!" in result
    ast.parse(result)


def test_sh_magic_data_emits_needs_review():
    nb = _make_nb("%sh aws s3 cp s3://bucket/data.csv /tmp/")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "pass  # notebook-magic" in result
    assert "# NEEDS-REVIEW:" in result
    ast.parse(result)


def test_fs_magic_data_emits_needs_review():
    nb = _make_nb("%fs cp /mnt/data /tmp/local")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "pass  # notebook-magic" in result
    assert "# NEEDS-REVIEW:" in result
    ast.parse(result)


def test_bash_cell_magic_data_emits_needs_review():
    nb = _make_nb("%%bash\naws s3 cp s3://bucket/file .")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "pass  # notebook-magic" in result
    assert "# NEEDS-REVIEW:" in result
    ast.parse(result)


# ---------------------------------------------------------------------------
# Output is always ast.parse-able
# ---------------------------------------------------------------------------


def test_mixed_notebook_parses():
    nb = _make_nb(
        "%pip install foo",
        "%%sql\nCREATE TABLE t (id INT);\nSELECT * FROM t;",
        "%run ../setup",
        "import os\ndf = spark.read.parquet('/data')",
        "!echo hello",
        "%python\nx = 42",
    )
    result = notebook_source.notebook_dict_to_python(nb)
    ast.parse(result)


def test_empty_notebook_returns_empty():
    assert notebook_source.notebook_dict_to_python({}) == ""
    assert notebook_source.notebook_dict_to_python({"cells": []}) == ""


def test_malformed_notebook_file():
    with tempfile.NamedTemporaryFile(suffix=".ipynb", mode="w", delete=False) as f:
        f.write("not json at all {{{")
        path = f.name
    try:
        result = notebook_source.to_python(path)
        assert result == ""
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# notebook_dict_to_python works on in-memory dict
# ---------------------------------------------------------------------------


def test_notebook_dict_to_python_in_memory():
    nb = _make_nb("a = 1", "b = a + 1")
    result = notebook_source.notebook_dict_to_python(nb)
    assert "a = 1" in result
    assert "b = a + 1" in result
    ast.parse(result)


# ---------------------------------------------------------------------------
# to_python file round-trip
# ---------------------------------------------------------------------------


def test_to_python_file_roundtrip():
    with tempfile.NamedTemporaryFile(suffix=".ipynb", mode="w", delete=False) as f:
        nb = _make_nb("x = 42\nprint(x)")
        json.dump(nb, f)
        path = f.name
    try:
        result = notebook_source.to_python(path)
        assert "x = 42" in result
        assert "print(x)" in result
        ast.parse(result)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# SQL result binding — Snowflake `%%sql -r <name>` and Databricks `_sqldf`
# ---------------------------------------------------------------------------


def test_sql_cell_result_var_binding():
    """%%sql -r <name> binds the result to <name> AND _sqldf; downstream resolves."""
    nb = _make_nb(
        "%%sql -r my_result\nSELECT C.NAME FROM SOURCE_TABLE C",
        "my_list = my_result",
    )
    out = notebook_source.notebook_dict_to_python(nb)
    ast.parse(out)
    assert "my_result = _sqldf = spark.sql(" in out
    assert "SELECT C.NAME FROM SOURCE_TABLE C" in out


def test_sql_cell_result_var_double_dash_result():
    nb = _make_nb("%%sql --result my_df\nSELECT 1 AS x")
    out = notebook_source.notebook_dict_to_python(nb)
    assert "my_df = _sqldf = spark.sql(" in out


def test_sql_cell_implicit_sqldf_binding():
    """A Databricks %sql / %%sql cell (no -r) implicitly binds _sqldf."""
    nb = _make_nb("%%sql\nSELECT 1 AS x")
    out = notebook_source.notebook_dict_to_python(nb)
    ast.parse(out)
    assert "_sqldf = spark.sql(" in out


def test_sql_line_magic_implicit_sqldf():
    nb = _make_nb("%sql SELECT 42 AS answer")
    out = notebook_source.notebook_dict_to_python(nb)
    assert "_sqldf = spark.sql(" in out
    assert "SELECT 42 AS answer" in out


def test_sql_database_flag_is_not_a_result_var():
    """`%%sql -d db` / `--database` must NOT be parsed as a result binding."""
    nb = _make_nb("%%sql -d mydb\nSELECT 1")
    out = notebook_source.notebook_dict_to_python(nb)
    # Only _sqldf is bound; no stray identifier from the -d flag.
    assert "_sqldf = spark.sql(" in out
    assert "mydb = " not in out


def test_sql_multi_statement_binds_only_last():
    nb = _make_nb("%%sql -r r\nCREATE TABLE t (id INT); INSERT INTO t VALUES (1); SELECT * FROM t")
    out = notebook_source.notebook_dict_to_python(nb)
    ast.parse(out)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    # First two run for side effects (bare spark.sql), last is bound.
    assert lines[0].startswith("spark.sql(")
    assert lines[1].startswith("spark.sql(")
    assert lines[2].startswith("r = _sqldf = spark.sql(")
    assert "SELECT * FROM t" in lines[2]


# ---------------------------------------------------------------------------
# Comment-aware statement splitting
# ---------------------------------------------------------------------------


def test_split_skips_line_comment_semicolon():
    stmts = notebook_source._split_sql_statements("SELECT 1 -- a; b\n; SELECT 2")
    assert stmts == ["SELECT 1 -- a; b", "SELECT 2"]


def test_split_skips_block_comment_semicolon():
    stmts = notebook_source._split_sql_statements("SELECT 1 /* x; y */ ; SELECT 2")
    assert len(stmts) == 2
    assert "SELECT 1" in stmts[0]
    assert stmts[1] == "SELECT 2"


def test_split_skips_string_literal_semicolon():
    stmts = notebook_source._split_sql_statements("SELECT 'a; b' AS c; SELECT 2")
    assert stmts == ["SELECT 'a; b' AS c", "SELECT 2"]


# ---------------------------------------------------------------------------
# Trailing double-quote escape (compile-gate safety)
# ---------------------------------------------------------------------------


def test_sql_trailing_double_quote_is_parseable():
    """A SQL statement ending in a double-quote must still translate to valid Python."""
    nb = _make_nb('%%sql\nSELECT col AS "Name"')
    out = notebook_source.notebook_dict_to_python(nb)
    ast.parse(out)  # would raise on the old """...""""  four-quote bug


def test_sql_double_quote_value_preserved():
    """Escaping double quotes must not change the SQL string passed to spark.sql."""
    code = notebook_source._sql_to_spark_calls('SELECT "col"')
    ns = {"_sqldf": None, "spark": type("S", (), {"sql": staticmethod(lambda q: q)})()}
    exec(compile(code, "<t>", "exec"), ns)
    assert ns["_sqldf"] == 'SELECT "col"'


# ---------------------------------------------------------------------------
# Unresolved notebook parameters -> NEEDS-REVIEW
# ---------------------------------------------------------------------------


def test_sql_jinja_param_emits_needs_review():
    nb = _make_nb("%%sql -r r\nSELECT * FROM t WHERE id = {{paramLoadID}}")
    out = notebook_source.notebook_dict_to_python(nb)
    assert "NEEDS-REVIEW" in out
    assert "{{" in out  # the SQL itself is preserved for the patch-author


# ---------------------------------------------------------------------------
# dbutils.notebook.run — return-value NEEDS-REVIEW + multi-line
# ---------------------------------------------------------------------------


def test_dbutils_run_assignment_flags_return_value():
    nb = _make_nb('result = dbutils.notebook.run("etl/load", 300)')
    out = notebook_source.notebook_dict_to_python(nb)
    ast.parse(out)
    assert '_nb_run("etl/load", globals())' in out
    assert "NEEDS-REVIEW" in out  # return value consumed


def test_dbutils_run_bare_call_has_no_marker():
    nb = _make_nb('dbutils.notebook.run("etl/load", 300)')
    out = notebook_source.notebook_dict_to_python(nb)
    ast.parse(out)
    assert '_nb_run("etl/load", globals())' in out
    assert "NEEDS-REVIEW" not in out  # bare statement, return value discarded


def test_dbutils_run_multiline_call_translated():
    nb = _make_nb('v = dbutils.notebook.run(\n    "child",\n    300,\n)')
    out = notebook_source.notebook_dict_to_python(nb)
    ast.parse(out)
    assert '_nb_run("child", globals())' in out
    # The executable code (before the NEEDS-REVIEW comment) is fully neutralized.
    code_only = out.split("# NEEDS-REVIEW")[0]
    assert "dbutils.notebook.run" not in code_only


# ---------------------------------------------------------------------------
# Databricks notebook-source .py format (# MAGIC / # COMMAND cells)
# ---------------------------------------------------------------------------

_DBX_SAMPLE = '''# Databricks notebook source
# MAGIC %md
# MAGIC # Title
# MAGIC some prose that is not python

# COMMAND ----------

# DBTITLE 1,Change Catalog
spark.sql("USE CATALOG foo")

# COMMAND ----------

# DBTITLE 1,Run Common Utils
# MAGIC %run "./COMMON_UTILS"

# COMMAND ----------

# DBTITLE 1,GET rows
# MAGIC %sql
# MAGIC SELECT C.NAME
# MAGIC FROM SOURCE_TABLE C
# MAGIC WHERE C.ID = 1

# COMMAND ----------

x = 1
dbutils.notebook.exit("Success")
'''


def test_is_dbx_notebook_py_detection():
    assert notebook_source.is_dbx_notebook_py(_DBX_SAMPLE)
    assert not notebook_source.is_dbx_notebook_py("import os\nx = 1\n")
    # leading blank lines still detected
    assert notebook_source.is_dbx_notebook_py("\n\n# Databricks notebook source\nx=1")


def test_dbx_py_translates_and_parses():
    out = notebook_source.dbx_py_to_python(_DBX_SAMPLE)
    ast.parse(out)  # must be valid Python
    # %md prose neutralized (not leaked as bare text)
    assert "some prose that is not python" not in out
    # plain python preserved
    assert 'spark.sql("USE CATALOG foo")' in out
    # %run -> _nb_run
    assert '_nb_run("./COMMON_UTILS", globals())' in out
    # # MAGIC %sql -> spark.sql with the SELECT (bound to _sqldf)
    assert "spark.sql(" in out
    assert "SELECT C.NAME" in out
    assert "_sqldf = spark.sql(" in out
    # dbutils.notebook.exit preserved for the exit->sys.exit(0) patch to rewrite
    assert "dbutils.notebook.exit" in out or "_nb_run" in out


def test_dbx_md_cell_does_not_leak_markdown():
    """A # MAGIC %md cell must neutralize to pass, never emit bare markdown."""
    dbx = "# Databricks notebook source\n# MAGIC %md\n# MAGIC This is prose. Not code!\n"
    out = notebook_source.dbx_py_to_python(dbx)
    ast.parse(out)
    assert "This is prose" not in out


def test_dbx_source_to_python_dispatch(tmp_path):
    """source_to_python translates a dbx .py but returns a plain .py verbatim."""
    dbx_path = tmp_path / "nb.py"
    dbx_path.write_text(_DBX_SAMPLE, encoding="utf-8")
    out = notebook_source.source_to_python(str(dbx_path))
    ast.parse(out)
    assert "_sqldf = spark.sql(" in out

    plain = tmp_path / "mod.py"
    plain.write_text("import os\nVALUE = 1\n", encoding="utf-8")
    assert notebook_source.source_to_python(str(plain)) == "import os\nVALUE = 1\n"


# ---------------------------------------------------------------------------
# Review round 2: nested-paren dbutils, backslash SQL split, %sql -r next-line,
# trailing-statement preservation, cell-magic fallback.
# ---------------------------------------------------------------------------


def test_dbutils_run_nested_paren_args_matched_whole():
    """A call with nested parens / dict args is matched whole and stays parseable."""
    src = 'x = dbutils.notebook.run("child", 60, arguments={"date": str(dt)})\n'
    out = notebook_source._translate_dbutils_in_python(src)
    ast.parse(out)
    assert '_nb_run("child", globals())' in out
    # no dangling "})" leaked into the code
    assert "})" not in out.split("#")[0]


def test_dbutils_run_multiline_nested_paren():
    src = 'v = dbutils.notebook.run(\n  "c",\n  arguments={"k": f(g(1))},\n)\n'
    out = notebook_source._translate_dbutils_in_python(src)
    ast.parse(out)
    assert '_nb_run("c", globals())' in out
    assert "dbutils.notebook.run" not in out.split("# NEEDS-REVIEW")[0]


def test_dbutils_run_preserves_trailing_statement():
    """Only the call span is replaced; trailing code on the line survives."""
    src = 'dbutils.notebook.run("x"); do_more()\n'
    out = notebook_source._translate_dbutils_in_python(src)
    ast.parse(out)
    assert "do_more()" in out
    assert '_nb_run("x", globals())' in out
    # a bare (unconsumed) call gets no inline NEEDS-REVIEW that would eat do_more()
    assert "NEEDS-REVIEW" not in out


def test_split_handles_backslash_escaped_quote():
    stmts = notebook_source._split_sql_statements(r"SELECT 'a\'b; c' AS x; SELECT 2")
    assert stmts == [r"SELECT 'a\'b; c' AS x", "SELECT 2"]


def test_sql_line_magic_result_flag_with_query_on_next_line():
    """`%sql --result out` with the query on the NEXT line still binds `out`."""
    nb = _make_nb("%sql --result out\nSELECT 1 AS a")
    out = notebook_source.notebook_dict_to_python(nb)
    ast.parse(out)
    assert "out = _sqldf = spark.sql(" in out
    assert "--result out" not in out  # flag consumed, not left as SQL text


def test_sql_line_magic_dash_r_next_line():
    nb = _make_nb("%sql -r res\nSELECT col FROM t")
    out = notebook_source.notebook_dict_to_python(nb)
    ast.parse(out)
    assert "res = _sqldf = spark.sql(" in out
    assert "SELECT col FROM t" in out


def test_source_to_python_is_cached_by_path_mtime(tmp_path):
    """P6: source_to_python memoizes translation per (abspath, mtime) so a shared
    %run target isn't retranslated on every call."""
    notebook_source._TRANSLATION_CACHE.clear()
    p = tmp_path / "mod.py"
    p.write_text("x = 1\n")
    r1 = notebook_source.source_to_python(str(p))
    assert r1 == "x = 1\n"
    key = (os.path.abspath(str(p)), os.path.getmtime(str(p)))
    assert key in notebook_source._TRANSLATION_CACHE
    # Second call returns the cached object (identity), not a re-read.
    r2 = notebook_source.source_to_python(str(p))
    assert r2 is r1


def test_source_to_python_still_translates_dbx_and_plain(tmp_path):
    """Cache wrapper must not change translation behavior."""
    plain = tmp_path / "plain.py"
    plain.write_text("import os\nprint(os.getcwd())\n")
    assert notebook_source.source_to_python(str(plain)) == "import os\nprint(os.getcwd())\n"
    dbx = tmp_path / "nb.py"
    dbx.write_text("# Databricks notebook source\n# COMMAND ----------\nprint('hi')\n")
    out = notebook_source.source_to_python(str(dbx))
    ast.parse(out)  # translated output is valid Python
    assert "hi" in out
