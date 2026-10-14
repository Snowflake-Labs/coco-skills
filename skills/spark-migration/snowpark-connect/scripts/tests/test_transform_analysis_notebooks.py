"""Tests for notebook coordinate resolution in ``transform_analysis``.

Covers the new functionality introduced to handle the cell-relative vs
file-absolute line number distinction between Databricks exported ``.py``
notebooks and ``.ipynb`` (pure) notebooks:

* ``_notebook_cell_code_start_line`` — format detector + cell start resolver
* ``_cell_relative_to_absolute`` — arithmetic conversion helper
* ``_rebase_findings`` — routing logic:
    - exported .py  → cell-relative → file-absolute → difflib
    - .ipynb/plain  → skip difflib entirely, keep cell-relative
* ``transform()``  — display format:
    - rebased (exported .py)  → plain file-absolute string
    - not rebased (.ipynb)    → "cell N: L" string
    - no cell_id (plain .py)  → raw lines string
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from transform_analysis import (
    _cell_relative_to_absolute,
    _notebook_cell_code_start_line,
    _rebase_findings,
    transform,
)


# ---------------------------------------------------------------------------
# _notebook_cell_code_start_line: format detector
# ---------------------------------------------------------------------------

_EXPORTED_PY = dedent("""\
    # Databricks notebook source
    # COMMAND ----------

    import pyspark
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()

    # COMMAND ----------

    def process(df):
        return df.filter("id > 0")

    # COMMAND ----------

    result = process(spark.range(100))
    result.show()
""")

_EXPORTED_PY_DBTITLE = dedent("""\
    # Databricks notebook source
    # COMMAND ----------
    # DBTITLE 1,Setup

    import pyspark

    # COMMAND ----------
    # DBTITLE 1,Process

    def process(df):
        return df.filter("id > 0")
""")


def test_cell_start_plain_file_returns_none() -> None:
    """A plain (non-notebook) Python file returns None — acts as format detector."""
    plain = "import pyspark\nspark = SparkSession.builder.getOrCreate()\n"
    assert _notebook_cell_code_start_line(plain, cell_id=0) is None


def test_cell_start_cell0() -> None:
    """Cell 0 starts after the header line; blank lines after header are skipped."""
    # In _EXPORTED_PY: line 1 = header, line 2 = COMMAND (cell 0 has no code before
    # first COMMAND, so cell 0 is the block after the header and before the first sep)
    # Wait: cell 0 is lines 1-2 of _EXPORTED_PY (blank line then import).
    # Header is index 0 (line 1), so i starts at 1; line 1 is blank (""), skip it;
    # line 2 (0-indexed) is "# COMMAND ----------" — but that's the separator, not code.
    # Actually the structure is:
    #   line 1: # Databricks notebook source   (index 0)
    #   line 2: # COMMAND ----------            (index 1)  <- separator for cell 0 boundary
    #   line 3: (blank)                         (index 2)
    #   line 4: import pyspark                  (index 3)  <- cell 1 code starts here
    #
    # So cell 0 is the empty cell before the first COMMAND and cell 1 starts at line 4.
    # cell_id=0: i=1 (after header). lines[1] = "# COMMAND ----------" — not blank, not DBTITLE.
    # → returns 2 (1-based). That's the separator line itself, which is code-empty.
    # For the real test, cell_id=1 (first real code cell) should return line 4.
    result = _notebook_cell_code_start_line(_EXPORTED_PY, cell_id=1)
    # sep_indices[0] is index 1 (the first "# COMMAND ----------")
    # i = 1 + 1 = 2 (index 2, blank line)
    # skip blank → i = 3 (index 3, "import pyspark")
    # → return i + 1 = 4 (1-based)
    assert result == 4, f"expected cell 1 to start at line 4, got {result}"


def test_cell_start_second_cell() -> None:
    """Cell 2 starts after the second COMMAND separator."""
    result = _notebook_cell_code_start_line(_EXPORTED_PY, cell_id=2)
    # _EXPORTED_PY lines (1-based):
    #  1: # Databricks notebook source
    #  2: # COMMAND ----------       <- sep_indices[0] = index 1
    #  3: (blank)
    #  4: import pyspark
    #  5: from pyspark.sql ...
    #  6: spark = ...
    #  7: (blank)
    #  8: # COMMAND ----------       <- sep_indices[1] = index 7
    #  9: (blank)
    # 10: def process(df):
    # cell_id=2: i = sep_indices[1] + 1 = 8 (index 8, blank line)
    # skip blank → i = 9 (index 9, "def process(df):")
    # → return i + 1 = 10
    assert result == 10, f"expected cell 2 to start at line 10, got {result}"


def test_cell_start_out_of_range_returns_none() -> None:
    """cell_id beyond the number of COMMAND separators returns None."""
    result = _notebook_cell_code_start_line(_EXPORTED_PY, cell_id=999)
    assert result is None


def test_cell_start_dbtitle_skipped() -> None:
    """A leading DBTITLE line (stripped by notebook_io) is skipped when
    computing the cell's code start line."""
    result = _notebook_cell_code_start_line(_EXPORTED_PY_DBTITLE, cell_id=1)
    # _EXPORTED_PY_DBTITLE:
    #  1: # Databricks notebook source
    #  2: # COMMAND ----------
    #  3: # DBTITLE 1,Setup
    #  4: (blank)
    #  5: import pyspark
    # sep_indices[0] = index 1; i = 2; lines[2] = "# DBTITLE 1,Setup" → skip
    # i = 3 (blank) → skip → i = 4 ("import pyspark") → return 5
    assert result == 5, f"expected DBTITLE-skipped cell to start at line 5, got {result}"


def test_cell_start_empty_text_returns_none() -> None:
    assert _notebook_cell_code_start_line("", cell_id=0) is None


# ---------------------------------------------------------------------------
# _cell_relative_to_absolute: arithmetic conversion
# ---------------------------------------------------------------------------


def test_cell_relative_single_line() -> None:
    assert _cell_relative_to_absolute("1", cell_start_line=218) == "218"
    assert _cell_relative_to_absolute("1", cell_start_line=269) == "269"
    assert _cell_relative_to_absolute("57", cell_start_line=294) == "350"


def test_cell_relative_range() -> None:
    assert _cell_relative_to_absolute("2-8", cell_start_line=218) == "219-225"
    assert _cell_relative_to_absolute("1-1", cell_start_line=269) == "269-269"


def test_cell_relative_empty_string_unchanged() -> None:
    assert _cell_relative_to_absolute("", cell_start_line=10) == ""


def test_cell_relative_invalid_unchanged() -> None:
    assert _cell_relative_to_absolute("not-a-number", cell_start_line=10) == "not-a-number"


# ---------------------------------------------------------------------------
# _rebase_findings: routing logic for notebook cell_id entries
# ---------------------------------------------------------------------------


@pytest.fixture
def exported_py_sources(tmp_path: Path) -> tuple[Path, Path]:
    """Synthetic Databricks exported .py in both original and post-recipe trees.

    The post-recipe file has an extra comment line inserted at the top (after
    the header), so file-absolute line numbers differ by 1 between the two
    trees.

    Post-recipe structure (cell 1 code starts at file-absolute line 7):
        1: # Databricks notebook source
        2: # COMMAND ----------
        3: (blank)
        4: # SCOS-WARN: recipe applied
        5: import pyspark
        6: from pyspark.sql import SparkSession
        7: spark = SparkSession.builder.getOrCreate()  <- cell 1 code line 3
        8: # COMMAND ----------
        9: (blank)
       10: rdd = spark.sparkContext.parallelize([1])   <- cell 2 code line 1

    Original structure (same code, no recipe comment; cell 1 code starts at line 5):
        1: # Databricks notebook source
        2: # COMMAND ----------
        3: (blank)
        4: import pyspark
        5: from pyspark.sql import SparkSession
        6: spark = SparkSession.builder.getOrCreate()  <- cell 1 code line 3
        7: # COMMAND ----------
        8: (blank)
        9: rdd = spark.sparkContext.parallelize([1])   <- cell 2 code line 1
    """
    filename = "notebook_sample.py"
    post_text = dedent("""\
        # Databricks notebook source
        # COMMAND ----------

        # SCOS-WARN: recipe applied
        import pyspark
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
        # COMMAND ----------

        rdd = spark.sparkContext.parallelize([1])
    """)
    orig_text = dedent("""\
        # Databricks notebook source
        # COMMAND ----------

        import pyspark
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
        # COMMAND ----------

        rdd = spark.sparkContext.parallelize([1])
    """)
    orig_dir = tmp_path / "orig"
    post_dir = tmp_path / "post"
    orig_dir.mkdir()
    post_dir.mkdir()
    (orig_dir / filename).write_text(orig_text)
    (post_dir / filename).write_text(post_text)
    return orig_dir, post_dir


def test_rebase_exported_py_notebook_converts_cell_relative(
    exported_py_sources: tuple[Path, Path],
) -> None:
    """For a Databricks exported .py, a finding with cell_id and cell-relative
    lines should be converted to file-absolute and then difflib-mapped to the
    original source."""
    orig_dir, post_dir = exported_py_sources
    # cell_id=1, cell-relative line 3 = "spark = ..." in the cell.
    # Post-recipe cell 1 starts at file-absolute line 5 (after header, COMMAND,
    # blank, SCOS-WARN comment, then import/from lines).
    # Cell-relative line 3 → file-absolute in post = 5 + (3-1) = 7.
    # In original, line 7 is the COMMAND separator; actual spark= is at line 6.
    # difflib maps post line 7 → original line 6.
    findings = [
        {
            "file": "notebook_sample.py",
            "lines": "3",
            "cell_id": 1,
            "code": "spark = SparkSession.builder.getOrCreate()",
            "final_risk": 0.7,
        }
    ]
    rebased = _rebase_findings(
        findings,
        original_source_dir=orig_dir,
        post_recipe_source_dir=post_dir,
    )
    assert len(rebased) == 1
    r = rebased[0]
    # Must have been converted from cell-relative ("3") to file-absolute, then
    # difflib-mapped to the original source. The exact line may vary by the
    # synthetic content, but it must NOT remain as "3" (cell-relative).
    assert r["lines"] != "3", (
        "exported .py finding should not keep cell-relative line '3'; "
        f"got {r['lines']!r}"
    )
    # _notebook_lines_rebased flag must be set
    assert r.get("_notebook_lines_rebased") is True


def test_rebase_ipynb_finding_skips_difflib(tmp_path: Path) -> None:
    """For a .ipynb-style finding (header check returns None), difflib is
    skipped and the finding is kept with its original cell-relative lines."""
    filename = "notebook.ipynb.py"  # no Databricks header → format detector → None
    plain_text = dedent("""\
        import pyspark
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
        rdd = spark.sparkContext.parallelize([1, 2])
    """)
    orig_dir = tmp_path / "orig"
    post_dir = tmp_path / "post"
    orig_dir.mkdir()
    post_dir.mkdir()
    (orig_dir / filename).write_text(plain_text)
    (post_dir / filename).write_text(plain_text)

    findings = [
        {
            "file": filename,
            "lines": "2-3",
            "cell_id": 5,
            "code": "some cell code",
            "final_risk": 0.6,
        }
    ]
    rebased = _rebase_findings(
        findings,
        original_source_dir=orig_dir,
        post_recipe_source_dir=post_dir,
    )
    assert len(rebased) == 1
    r = rebased[0]
    # Cell-relative lines must be preserved exactly — no difflib mapping applied.
    assert r["lines"] == "2-3", (
        f".ipynb finding should keep cell-relative lines '2-3'; got {r['lines']!r}"
    )
    # _notebook_lines_rebased must NOT be set
    assert not r.get("_notebook_lines_rebased"), (
        "_notebook_lines_rebased must not be set for .ipynb findings"
    )


# ---------------------------------------------------------------------------
# transform(): display format for notebook findings
# ---------------------------------------------------------------------------


def test_transform_plain_file_finding_displays_raw_lines() -> None:
    """A finding without cell_id displays its lines field as-is."""
    findings = [
        {
            "file": "src/main.py",
            "lines": "17-27",
            "code": "rdd.collect()",
            "final_risk": 0.8,
            "language": "python",
            "root_cause": "RDD not supported",
            "confidence": "HIGH",
        }
    ]
    ir = transform(findings, project="test")
    assert ir.detailed_findings[0].lines == "17-27"


def test_transform_ipynb_finding_displays_cell_prefix() -> None:
    """A finding with cell_id but without _notebook_lines_rebased is displayed
    as 'cell N: L' so the user knows the coordinate is within-cell."""
    findings = [
        {
            "file": "notebook.ipynb",
            "lines": "2-8",
            "cell_id": 12,
            "code": "df.join(...)",
            "final_risk": 0.75,
            "language": "python",
            "root_cause": "join semantics differ",
            "confidence": "HIGH",
        }
    ]
    ir = transform(findings, project="test")
    assert ir.detailed_findings[0].lines == "cell 12: 2-8", (
        f"expected 'cell 12: 2-8', got {ir.detailed_findings[0].lines!r}"
    )


def test_transform_exported_py_finding_displays_file_absolute() -> None:
    """A finding that went through notebook rebasing (_notebook_lines_rebased=True)
    displays its resolved file-absolute lines without any 'cell N:' prefix."""
    findings = [
        {
            "file": "RAD_notebook.py",
            "lines": "219-225",
            "cell_id": 12,
            "_notebook_lines_rebased": True,
            "code": "def generateClashLocationDataFrame(...):",
            "final_risk": 0.75,
            "language": "python",
            "root_cause": "custom join method",
            "confidence": "HIGH",
        }
    ]
    ir = transform(findings, project="test")
    assert ir.detailed_findings[0].lines == "219-225", (
        f"expected '219-225', got {ir.detailed_findings[0].lines!r}"
    )


def test_transform_cell_id_no_lines_displays_cell_only() -> None:
    """Edge case: cell_id present but lines is empty → display as 'cell N'."""
    findings = [
        {
            "file": "notebook.ipynb",
            "lines": "",
            "cell_id": 3,
            "code": "x = 1",
            "final_risk": 0.5,
            "language": "python",
            "root_cause": "some issue",
            "confidence": "MEDIUM",
        }
    ]
    ir = transform(findings, project="test")
    assert ir.detailed_findings[0].lines == "cell 3", (
        f"expected 'cell 3', got {ir.detailed_findings[0].lines!r}"
    )
