"""Tests for the implicit-``spark`` bootstrap recipe.

The recipe injects

    from snowflake import snowpark_connect
    spark = snowpark_connect.init_spark_session()

into any file that uses ``spark`` as an ambient global without binding it
at module scope — covering Databricks notebooks AND plain
``spark-submit``/YARN scripts (the case that previously slipped through
because the recipe was gated to Databricks-shape input only).
"""
from __future__ import annotations

from recipes.implicit_spark_inject_bootstrap import recipe

BOOTSTRAP_IMPORT = "from snowflake import snowpark_connect"
BOOTSTRAP_ASSIGN = "spark = snowpark_connect.init_spark_session()"


def _apply(src: str):
    return recipe.apply(src, file="t.py")


def _injected(result) -> bool:
    return BOOTSTRAP_IMPORT in result.source and BOOTSTRAP_ASSIGN in result.source


# ---------------------------------------------------------------------------
# Positive cases — bootstrap must be injected
# ---------------------------------------------------------------------------


def test_plain_sparksubmit_script_ambient_spark_injected():
    """Plain PySpark spark-submit script: ``spark`` used, builder commented
    out, no module-scope definition. This is the case the old
    Databricks-only gate missed."""
    src = (
        "# job FLOTTE — runs via spark-submit on YARN\n"
        "# spark = SparkSession.builder.appName('flotte').getOrCreate()\n"
        "df = spark.table('raw.events')\n"
        "spark.conf.set('k', 'v')\n"
    )
    result = _apply(src)
    assert _injected(result)
    assert len(result.edits) == 1
    # Bootstrap is the first two statements, before user code.
    assert result.source.index(BOOTSTRAP_ASSIGN) < result.source.index(
        "df = spark.table"
    )


def test_databricks_notebook_still_injected():
    src = (
        "# Databricks notebook source\n"
        "df = spark.read.parquet('/mnt/x')\n"
    )
    assert _injected(_apply(src))


def test_bare_spark_call_triggers():
    assert _injected(_apply("result = spark('expr')\n"))


# ---------------------------------------------------------------------------
# Negative cases — must NOT inject
# ---------------------------------------------------------------------------


def test_live_builder_not_injected():
    """A file with a live builder binds ``spark`` at module scope; the
    builder recipes own that case."""
    src = (
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.appName('x').getOrCreate()\n"
        "df = spark.table('t')\n"
    )
    result = _apply(src)
    assert not _injected(result)
    assert result.edits == []


def test_module_scope_import_of_spark_not_injected():
    """``spark`` imported from a shared helper is already bound — do not
    double-bind it."""
    src = (
        "from project.context import spark\n"
        "df = spark.table('t')\n"
    )
    result = _apply(src)
    assert not _injected(result)
    assert result.edits == []


def test_import_as_spark_not_injected():
    src = (
        "import project.context as spark\n"
        "spark.conf.set('k', 'v')\n"
    )
    assert not _injected(_apply(src))


def test_already_has_snowpark_connect_import_noop():
    src = (
        "from snowflake import snowpark_connect\n"
        "spark = snowpark_connect.init_spark_session()\n"
        "df = spark.table('t')\n"
    )
    result = _apply(src)
    # No second bootstrap appended.
    assert result.source.count(BOOTSTRAP_ASSIGN) == 1
    assert result.edits == []


def test_no_spark_usage_noop():
    src = "import os\nprint(os.getcwd())\n"
    result = _apply(src)
    assert not _injected(result)
    assert result.edits == []


def test_idempotent_on_second_run():
    src = "df = spark.table('t')\n"
    first = _apply(src)
    assert _injected(first)
    second = _apply(first.source)
    assert second.source == first.source
    assert second.edits == []


def test_local_spark_param_does_not_count_as_definition():
    """A function-local ``spark`` (param/assignment) does not satisfy
    module-level references, so the bootstrap is still injected."""
    src = (
        "def run(spark):\n"
        "    return spark.table('t')\n"
        "\n"
        "df = spark.table('top_level')\n"
    )
    assert _injected(_apply(src))


def test_unparseable_source_noop():
    result = _apply("def (:\n  spark.x\n")
    assert result.edits == []


# ---------------------------------------------------------------------------
# SCOS marker — the injection must be visible inline AND in the header
# ---------------------------------------------------------------------------

BOOTSTRAP_MARKER_CODE = "[SPRKCNTPY1001-Fixed]"


def test_injected_bootstrap_carries_scos_marker():
    """The injection is stamped with a ``# SCOS:`` marker directly above the
    import. Without it the bootstrap was invisible to both the inline reader and
    the migration header (built only from ``# SCOS:`` comments), so a file whose
    only change was the injected session reported "No changes required"."""
    result = _apply("df = spark.table('t')\n")
    assert _injected(result)
    lines = result.source.splitlines()
    marker = next((l for l in lines if l.lstrip().startswith("# SCOS:")), None)
    assert marker is not None, "bootstrap injection carries no # SCOS: marker"
    assert BOOTSTRAP_MARKER_CODE in marker
    assert recipe.RECIPE_ID in marker
    # The marker sits immediately above the injected import line.
    assert lines[lines.index(marker) + 1] == BOOTSTRAP_IMPORT


def test_marker_not_duplicated_on_rerun():
    first = _apply("df = spark.table('t')\n")
    second = _apply(first.source)
    assert second.source == first.source
    assert first.source.count("SPRKCNTPY1001") == 1


def test_marker_lands_after_notebook_header_comment():
    """In a Databricks export the ``# Databricks notebook source`` line lives in
    the module header; the SCOS marker must land after it, not before."""
    result = _apply("# Databricks notebook source\ndf = spark.read.parquet('/mnt/x')\n")
    lines = result.source.splitlines()
    assert lines[0] == "# Databricks notebook source"
    assert lines[1].lstrip().startswith("# SCOS:") and BOOTSTRAP_MARKER_CODE in lines[1]
    assert lines[2] == BOOTSTRAP_IMPORT


# ---------------------------------------------------------------------------
# Driver-level: module-scope recipe → notebook cell mapping
#
# The recipe injects the bootstrap *after* the first cell's leading header
# comments (an interior insertion, not a top-of-file prepend). The driver's
# notebook mapping previously only accepted a pure prepend, so it SKIPPED the
# injection on every notebook with a "non-prepend change" warning — the
# bootstrap silently never landed. These tests exercise that driver path
# (`_apply_module_recipe_to_notebook` / `_splice_single_insertion_into_cell`),
# which the recipe-only tests above never reach.
# ---------------------------------------------------------------------------


def _splice(cell_sources, new_concat, sep="\n"):
    import preprocess_recipes as pr

    class _Cell:
        def __init__(self, s):
            self.source = s

    cells = [_Cell(s) for s in cell_sources]
    concat = sep.join(c.source for c in cells)
    ok = pr._splice_single_insertion_into_cell(cells, concat, new_concat, sep)
    return ok, [c.source for c in cells], sep.join(c.source for c in cells)


def test_splice_pure_prepend_into_first_cell():
    ok, cells, rebuilt = _splice(["a=1", "b=2"], "IMPORT\na=1\nb=2")
    assert ok and cells[0] == "IMPORT\na=1" and rebuilt == "IMPORT\na=1\nb=2"


def test_splice_interior_insertion_after_header():
    # This is the regression: an insertion AFTER a leading comment in cell 0.
    ok, cells, rebuilt = _splice(["# hdr\na=1", "b=2"], "# hdr\nBOOT\na=1\nb=2")
    assert ok and cells[0] == "# hdr\nBOOT\na=1" and rebuilt == "# hdr\nBOOT\na=1\nb=2"


def test_splice_insertion_maps_to_second_cell():
    ok, cells, rebuilt = _splice(["a=1", "# h2\nc=3"], "a=1\n# h2\nZZ\nc=3")
    assert ok and cells[1] == "# h2\nZZ\nc=3" and rebuilt == "a=1\n# h2\nZZ\nc=3"


def test_splice_rejects_modification():
    ok, _, _ = _splice(["a=1", "b=2"], "a=9\nb=2")
    assert ok is False


def test_splice_rejects_deletion():
    ok, _, _ = _splice(["a=1", "b=2"], "a=1")
    assert ok is False


def test_module_recipe_injects_bootstrap_into_notebook_cells():
    """End-to-end driver path: a Databricks exported-Python notebook whose only
    binding of ``spark`` is ambient must receive the bootstrap in its first
    Python cell, in ``import`` → ``init`` order, via the driver's cell mapping.
    """
    import os
    import tempfile

    import notebook_io as ni
    import preprocess_recipes as pr
    from recipes.implicit_spark_inject_bootstrap import recipe as boot_recipe

    src = (
        "# Databricks notebook source\n"
        "# MAGIC %md\n"
        "# MAGIC # Header\n"
        "# COMMAND ----------\n"
        "from pyspark.sql import functions as F\n"
        "df = spark.read.parquet('/mnt/x')\n"
        "# COMMAND ----------\n"
        "df2 = spark.table('t')\n"
    )
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "nb.py")
        with open(p, "w") as fh:
            fh.write(src)
        edits, applied, modified = pr.apply_recipes_to_notebook(
            p, "nb.py",
            [("implicit_spark_inject_bootstrap", boot_recipe)],
            dry_run=False, facts_db=None, strict=True,
        )
        assert modified is True
        assert "implicit_spark_inject_bootstrap" in applied
        nb = ni.parse_notebook(p)
        code = "\n".join(
            c.source for c in nb.cells
            if c.cell_type == "code" and c.cell_language == "python"
        )
        assert BOOTSTRAP_IMPORT in code
        assert BOOTSTRAP_ASSIGN in code
        # import must precede the init, and both must precede first spark usage.
        i_imp = code.index(BOOTSTRAP_IMPORT)
        i_ini = code.index(BOOTSTRAP_ASSIGN)
        i_use = code.index("spark.read.parquet")
        assert i_imp < i_ini < i_use


# ---------------------------------------------------------------------------
# notebook_io magic-cell classification
#
# A ``%run`` (Databricks include) or ``%pip``/``%conda`` (install) cell is NOT
# Python. If notebook_io labels it "python", the bare ``%run ...`` line lands in
# the concatenated Python module, breaks ``parse_module``, and silently disables
# EVERY module-scope recipe (including the bootstrap) for that whole notebook —
# observed on 34/45 real customer notebooks. These pin the correct language so
# such cells are excluded from the Python stream.
# ---------------------------------------------------------------------------


def test_infer_cell_language_run_pip_conda_are_not_python():
    import notebook_io as ni

    assert ni._infer_cell_language("%run ../config $brand='plk'", "python") == "run"
    assert ni._infer_cell_language("%pip install torch", "python") == "shell"
    assert ni._infer_cell_language("%conda install numpy", "python") == "shell"
    # sanity: real python and other magics still classify as before
    assert ni._infer_cell_language("df = spark.table('t')", "python") == "python"
    assert ni._infer_cell_language("%sql SELECT 1", "python") == "sql"
    assert ni._infer_cell_language("%md # title", "python") == "markdown"


def test_infer_cell_language_jupyter_cell_magics_and_shell_escapes():
    """Jupyter %%<lang> cell magics govern the whole cell and are not Python;
    a pure ``!`` shell-escape cell is shell. But Python-wrapping cell magics
    (%%time) and mixed !+Python cells must STAY python so real code is kept."""
    import notebook_io as ni

    # %%<lang> cell magics -> whole-cell language (excluded from Python)
    assert ni._infer_cell_language("%%sql\nSELECT 1", "python") == "sql"
    assert ni._infer_cell_language("%%sql SELECT 1", "python") == "sql"
    assert ni._infer_cell_language("%%bash\necho hi", "python") == "shell"
    assert ni._infer_cell_language("%%sh ls", "python") == "shell"
    assert ni._infer_cell_language("%%html\n<b>x</b>", "python") == "markdown"
    assert ni._infer_cell_language("%%r\nx <- 1", "python") == "r"
    assert ni._infer_cell_language("%%scala\nval x = 1", "python") == "scala"

    # pure ``!`` shell-escape cell -> shell
    assert ni._infer_cell_language("!pip install torch", "python") == "shell"
    assert ni._infer_cell_language("!ls\n!pwd", "python") == "shell"

    # MUST stay python: python-wrapping cell magic + mixed !/python + `!=` op
    assert ni._infer_cell_language("%%time\ndf = spark.read.parquet('/x')", "python") == "python"
    assert ni._infer_cell_language("%%capture\nx = 1", "python") == "python"
    assert ni._infer_cell_language("!pip install foo\nimport foo\ndf = foo.load()", "python") == "python"
    assert ni._infer_cell_language("x = a != b", "python") == "python"


def test_notebook_with_run_magic_gets_bootstrap_injected():
    """Regression: a Databricks exported notebook whose second cell is a
    ``# MAGIC %run`` include used to make the concatenated Python module
    unparseable, so the bootstrap was silently skipped. The %run cell must now
    be classified "run" (excluded from Python), letting the bootstrap land.
    """
    import os
    import tempfile

    import notebook_io as ni
    import preprocess_recipes as pr
    from recipes.implicit_spark_inject_bootstrap import recipe as boot_recipe

    src = (
        "# Databricks notebook source\n"
        "from helpers.schema import SCHEMA\n"
        "# COMMAND ----------\n"
        "# MAGIC %run ../config $brand=\"plk\"\n"
        "# COMMAND ----------\n"
        "df = spark.table('t')\n"
    )
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "nb.py")
        with open(p, "w") as fh:
            fh.write(src)
        # The %run cell must be classified "run", not "python".
        nb = ni.parse_notebook(p)
        run_cells = [c for c in nb.cells if c.source.lstrip().startswith("%run")]
        assert run_cells and all(c.cell_language == "run" for c in run_cells)

        edits, applied, modified = pr.apply_recipes_to_notebook(
            p, "nb.py",
            [("implicit_spark_inject_bootstrap", boot_recipe)],
            dry_run=False, facts_db=None, strict=True,
        )
        assert modified is True
        assert "implicit_spark_inject_bootstrap" in applied
        nb2 = ni.parse_notebook(p)
        code = "\n".join(
            c.source for c in nb2.cells
            if c.cell_type == "code" and c.cell_language == "python"
        )
        assert BOOTSTRAP_IMPORT in code and BOOTSTRAP_ASSIGN in code
        # the %run include must be preserved (not dropped or mangled)
        assert any(
            c.source.lstrip().startswith("%run") for c in nb2.cells
        )
