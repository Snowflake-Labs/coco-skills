"""
Tests for dvp-notebook-to-script.

Covers:
- Databricks source format detection (is_databricks_source)
- Databricks source parsing (parse_databricks_source, _classify_dbx_cell)
- %run target scanning (scan_run_targets, _extract_run_refs)
- Flat vs wrapped script generation (_generate_script)
- Code cell processing (_process_code_cell: %run, magics, shell)
- SQL cell detection and processing
- Notebook discovery (find_notebooks)
- Full conversion pipeline (convert_all with two-pass scan)
"""

import json
import textwrap
from pathlib import Path

import pytest

from notebook_to_script import (
    is_databricks_source,
    parse_databricks_source,
    _classify_dbx_cell,
    NotebookToScriptConverter,
    ConversionResult,
    DBX_HEADER,
    DBX_SEPARATOR,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_dbx(path: Path, cells: list[str]):
    """Write a Databricks source notebook from a list of cell strings."""
    content = DBX_HEADER + "\n"
    for i, cell in enumerate(cells):
        if i > 0:
            content += f"\n{DBX_SEPARATOR}\n\n"
        content += cell
    path.write_text(content, encoding="utf-8")


def _write_ipynb(path: Path, cells: list[dict]):
    """Write a minimal .ipynb notebook."""
    nb = {
        "cells": cells,
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(nb), encoding="utf-8")


def _make_code_cell(source: str) -> dict:
    return {"cell_type": "code", "source": source, "metadata": {}}


def _make_md_cell(source: str) -> dict:
    return {"cell_type": "markdown", "source": source, "metadata": {}}


# ===========================================================================
# 1. Databricks source format detection
# ===========================================================================

class TestIsDatabricksSource:

    def test_valid_dbx_file(self, tmp_path):
        f = tmp_path / "config.py"
        f.write_text(f"{DBX_HEADER}\nimport os\n")
        assert is_databricks_source(f) is True

    def test_regular_python_file(self, tmp_path):
        f = tmp_path / "utils.py"
        f.write_text("import os\nprint('hello')\n")
        assert is_databricks_source(f) is False

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        assert is_databricks_source(f) is False

    def test_already_converted_dbx(self, tmp_path):
        f = tmp_path / "config.dbx.py"
        f.write_text('"""Auto-generated"""\nimport sys\n')
        assert is_databricks_source(f) is False

    def test_nonexistent_file(self, tmp_path):
        f = tmp_path / "nope.py"
        assert is_databricks_source(f) is False


# ===========================================================================
# 2. Databricks source parsing
# ===========================================================================

class TestParseDatabricksSource:

    def test_simple_code_cells(self, tmp_path):
        f = tmp_path / "nb.py"
        _write_dbx(f, [
            'x = 1\nprint(x)',
            'y = 2\nprint(y)',
        ])
        cells = parse_databricks_source(f)
        assert len(cells) == 2
        assert cells[0]["cell_type"] == "code"
        assert "x = 1" in cells[0]["source"]
        assert cells[1]["cell_type"] == "code"
        assert "y = 2" in cells[1]["source"]

    def test_markdown_cell(self, tmp_path):
        f = tmp_path / "nb.py"
        _write_dbx(f, [
            "# MAGIC %md\n# MAGIC # Title\n# MAGIC Description here",
        ])
        cells = parse_databricks_source(f)
        assert len(cells) == 1
        assert cells[0]["cell_type"] == "markdown"
        assert "# Title" in cells[0]["source"]
        assert "Description here" in cells[0]["source"]

    def test_run_magic(self, tmp_path):
        f = tmp_path / "nb.py"
        _write_dbx(f, [
            '# MAGIC %run ./config $brand="acme"',
        ])
        cells = parse_databricks_source(f)
        assert len(cells) == 1
        assert cells[0]["cell_type"] == "code"
        assert "%run" in cells[0]["source"]

    def test_sql_magic(self, tmp_path):
        f = tmp_path / "nb.py"
        _write_dbx(f, [
            "# MAGIC %sql\n# MAGIC SELECT * FROM users",
        ])
        cells = parse_databricks_source(f)
        assert len(cells) == 1
        assert cells[0]["cell_type"] == "code"
        assert "SELECT * FROM users" in cells[0]["source"]

    def test_mixed_cells(self, tmp_path):
        f = tmp_path / "nb.py"
        _write_dbx(f, [
            "# MAGIC %md\n# MAGIC # Setup",
            "import os",
            '# MAGIC %run ./config',
            "# MAGIC %sql\n# MAGIC TRUNCATE TABLE tmp",
        ])
        cells = parse_databricks_source(f)
        assert len(cells) == 4
        assert cells[0]["cell_type"] == "markdown"
        assert cells[1]["cell_type"] == "code"
        assert cells[2]["cell_type"] == "code"
        assert cells[3]["cell_type"] == "code"

    def test_empty_cells_skipped(self, tmp_path):
        f = tmp_path / "nb.py"
        _write_dbx(f, [
            "x = 1",
            "",
            "y = 2",
        ])
        cells = parse_databricks_source(f)
        assert len(cells) == 2


# ===========================================================================
# 3. _classify_dbx_cell
# ===========================================================================

class TestClassifyDbxCell:

    def test_plain_code(self):
        cell_type, source = _classify_dbx_cell(["x = 1", "print(x)"])
        assert cell_type == "code"
        assert "x = 1" in source

    def test_markdown(self):
        cell_type, source = _classify_dbx_cell([
            "# MAGIC %md",
            "# MAGIC # Title",
            "# MAGIC Some text",
        ])
        assert cell_type == "markdown"
        assert "# Title" in source
        assert "Some text" in source

    def test_run(self):
        cell_type, source = _classify_dbx_cell([
            '# MAGIC %run ../config $brand="plk"',
        ])
        assert cell_type == "code"
        assert "%run" in source

    def test_sql(self):
        cell_type, source = _classify_dbx_cell([
            "# MAGIC %sql",
            "# MAGIC SELECT 1",
        ])
        assert cell_type == "code"
        assert "SELECT 1" in source

    def test_bare_magic_line(self):
        """A line that is just '# MAGIC' with no content."""
        cell_type, source = _classify_dbx_cell([
            "# MAGIC %md",
            "# MAGIC",
            "# MAGIC text",
        ])
        assert cell_type == "markdown"


# ===========================================================================
# 4. %run target scanning
# ===========================================================================

class TestRunTargetScanning:

    def test_scan_dbx_notebooks(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        source.mkdir(parents=True)

        _write_dbx(source / "pipeline.py", [
            '# MAGIC %run ./config $brand="acme"',
            "print('done')",
        ])
        _write_dbx(source / "config.py", [
            "ENV = 'dev'",
        ])

        converter = NotebookToScriptConverter(source)
        notebooks = converter.find_notebooks()
        targets = converter.scan_run_targets(notebooks)

        assert "config" in targets

    def test_scan_ipynb_notebooks(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        source.mkdir(parents=True)

        _write_ipynb(source / "main.ipynb", [
            _make_code_cell("%run ./shared_utils"),
            _make_code_cell("x = 1"),
        ])
        _write_ipynb(source / "shared_utils.ipynb", [
            _make_code_cell("def helper(): pass"),
        ])

        converter = NotebookToScriptConverter(source)
        notebooks = converter.find_notebooks()
        targets = converter.scan_run_targets(notebooks)

        assert "shared_utils" in targets

    def test_no_run_targets(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        source.mkdir(parents=True)
        _write_dbx(source / "standalone.py", ["print('hello')"])

        converter = NotebookToScriptConverter(source)
        notebooks = converter.find_notebooks()
        targets = converter.scan_run_targets(notebooks)

        assert targets == set()

    def test_extract_run_refs_from_dbx(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        source.mkdir(parents=True)
        f = source / "nb.py"
        _write_dbx(f, [
            '# MAGIC %run ./config',
            '# MAGIC %run ../shared/utils $x="1"',
        ])

        converter = NotebookToScriptConverter(source)
        refs = converter._extract_run_refs(f)

        assert "./config" in refs
        assert "../shared/utils" in refs

    def test_extract_run_refs_from_ipynb(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        source.mkdir(parents=True)
        f = source / "nb.ipynb"
        _write_ipynb(f, [
            _make_code_cell("%run ./helpers\nprint('done')"),
        ])

        converter = NotebookToScriptConverter(source)
        refs = converter._extract_run_refs(f)

        assert "./helpers" in refs


# ===========================================================================
# 5. Flat vs wrapped script generation
# ===========================================================================

class TestScriptGeneration:

    def _converter(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        source.mkdir(parents=True)
        return NotebookToScriptConverter(source)

    def test_wrapped_script_has_def_run(self, tmp_path):
        converter = self._converter(tmp_path)
        cells = [{"cell_type": "code", "source": "x = 1"}]
        script = converter._generate_script("test.py", cells, flat=False)

        assert "def run():" in script
        assert 'if __name__ == "__main__":' in script
        assert "# DVP:FLAT" not in script

    def test_flat_script_has_no_def_run(self, tmp_path):
        converter = self._converter(tmp_path)
        cells = [{"cell_type": "code", "source": "x = 1"}]
        script = converter._generate_script("config.py", cells, flat=True)

        assert "def run():" not in script
        assert 'if __name__ == "__main__":' not in script
        assert "# DVP:FLAT" in script

    def test_flat_script_code_at_module_level(self, tmp_path):
        converter = self._converter(tmp_path)
        cells = [{"cell_type": "code", "source": "MY_VAR = 42"}]
        script = converter._generate_script("config.py", cells, flat=True)

        lines = script.split("\n")
        var_lines = [l for l in lines if "MY_VAR = 42" in l]
        assert len(var_lines) == 1
        assert not var_lines[0].startswith("    ")

    def test_wrapped_script_code_indented(self, tmp_path):
        converter = self._converter(tmp_path)
        cells = [{"cell_type": "code", "source": "MY_VAR = 42"}]
        script = converter._generate_script("main.py", cells, flat=False)

        lines = script.split("\n")
        var_lines = [l for l in lines if "MY_VAR = 42" in l]
        assert len(var_lines) == 1
        assert var_lines[0].startswith("        ")

    def test_markdown_cells_as_comments(self, tmp_path):
        converter = self._converter(tmp_path)
        cells = [{"cell_type": "markdown", "source": "# Title\nSome description"}]
        script = converter._generate_script("nb.py", cells, flat=True)

        assert "# # Title" in script
        assert "# Some description" in script

    def test_cell_tracking_calls(self, tmp_path):
        converter = self._converter(tmp_path)
        cells = [
            {"cell_type": "code", "source": "x = 1"},
            {"cell_type": "code", "source": "y = 2"},
        ]
        script = converter._generate_script("nb.py", cells, flat=True)

        assert 'nb.cell("001")' in script
        assert 'nb.cell("002")' in script

    def test_notebook_constructor_has_file(self, tmp_path):
        converter = self._converter(tmp_path)
        cells = [{"cell_type": "code", "source": "x = 1"}]

        for flat in (True, False):
            script = converter._generate_script("nb.py", cells, flat=flat)
            assert 'Notebook("nb.py", __file__)' in script

    def test_flat_script_has_finish_call(self, tmp_path):
        converter = self._converter(tmp_path)
        cells = [{"cell_type": "code", "source": "x = 1"}]
        script = converter._generate_script("nb.py", cells, flat=True)

        assert "nb.finish()" in script

    def test_wrapped_script_has_error_handling(self, tmp_path):
        converter = self._converter(tmp_path)
        cells = [{"cell_type": "code", "source": "x = 1"}]
        script = converter._generate_script("nb.py", cells, flat=False)

        assert "except Exception as _e:" in script
        assert "nb.report_error(_e)" in script
        assert "raise" in script


# ===========================================================================
# 6. Code cell processing (%run, magics, shell)
# ===========================================================================

class TestProcessCodeCell:

    def _converter(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        source.mkdir(parents=True)
        return NotebookToScriptConverter(source)

    def test_run_with_params(self, tmp_path):
        converter = self._converter(tmp_path)
        lines = converter._process_code_cell('%run ./config $brand="acme"')
        output = "\n".join(lines)
        assert 'nb.run("./config"' in output
        assert '$brand' in output

    def test_run_without_params(self, tmp_path):
        converter = self._converter(tmp_path)
        lines = converter._process_code_cell("%run ./config")
        output = "\n".join(lines)
        assert 'nb.run("./config")' in output

    def test_line_magic(self, tmp_path):
        converter = self._converter(tmp_path)
        lines = converter._process_code_cell("%pip install pandas")
        output = "\n".join(lines)
        assert 'nb.magic("%pip"' in output
        assert "install pandas" in output

    def test_cell_magic(self, tmp_path):
        converter = self._converter(tmp_path)
        lines = converter._process_code_cell("%%time\nresult = expensive()")
        assert 'nb.magic("%%time"' in lines[0]
        assert "result = expensive()" in lines[1]

    def test_shell_command(self, tmp_path):
        converter = self._converter(tmp_path)
        lines = converter._process_code_cell("!pip install numpy")
        output = "\n".join(lines)
        assert 'nb.magic("!"' in output
        assert "pip install numpy" in output

    def test_regular_code_passthrough(self, tmp_path):
        converter = self._converter(tmp_path)
        code = "x = 1\ny = x + 2\nprint(y)"
        lines = converter._process_code_cell(code)
        assert lines == code.split("\n")


# ===========================================================================
# 7. SQL cell detection and processing
# ===========================================================================

class TestSqlCells:

    def _converter(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        source.mkdir(parents=True)
        return NotebookToScriptConverter(source)

    def test_select_detected(self, tmp_path):
        converter = self._converter(tmp_path)
        assert converter._is_sql_cell("SELECT * FROM users") is True

    def test_insert_detected(self, tmp_path):
        converter = self._converter(tmp_path)
        assert converter._is_sql_cell("INSERT INTO t VALUES (1)") is True

    def test_python_not_detected(self, tmp_path):
        converter = self._converter(tmp_path)
        assert converter._is_sql_cell("x = spark.sql('SELECT 1')") is False

    def test_sql_cell_generates_spark_sql(self, tmp_path):
        converter = self._converter(tmp_path)
        lines = converter._process_sql_cell("SELECT * FROM users", "_sql_result_001")
        output = "\n".join(lines)
        assert "spark.sql(_sql)" in output
        assert "_sql_result_001" in output
        assert "SELECT * FROM users" in output

    def test_sql_comment_converted(self, tmp_path):
        converter = self._converter(tmp_path)
        lines = converter._process_sql_cell("-- This is a comment\nSELECT 1", "_r")
        output = "\n".join(lines)
        assert "# This is a comment" in output


# ===========================================================================
# 8. Notebook discovery
# ===========================================================================

class TestFindNotebooks:

    def test_finds_ipynb(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        source.mkdir(parents=True)
        _write_ipynb(source / "nb.ipynb", [_make_code_cell("x = 1")])

        converter = NotebookToScriptConverter(source)
        notebooks = converter.find_notebooks()
        assert any(n.name == "nb.ipynb" for n in notebooks)

    def test_finds_dbx_py(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        source.mkdir(parents=True)
        _write_dbx(source / "config.py", ["ENV = 'dev'"])

        converter = NotebookToScriptConverter(source)
        notebooks = converter.find_notebooks()
        assert any(n.name == "config.py" for n in notebooks)

    def test_skips_converted_files(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        source.mkdir(parents=True)
        (source / "nb.dbx.py").write_text("# converted")
        (source / "nb.ipynb.py").write_text("# converted")

        converter = NotebookToScriptConverter(source)
        notebooks = converter.find_notebooks()
        names = [n.name for n in notebooks]
        assert "nb.dbx.py" not in names
        assert "nb.ipynb.py" not in names

    def test_skips_regular_py(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        source.mkdir(parents=True)
        (source / "utils.py").write_text("def helper(): pass\n")

        converter = NotebookToScriptConverter(source)
        notebooks = converter.find_notebooks()
        assert len(notebooks) == 0

    def test_nonexistent_path(self, tmp_path):
        converter = NotebookToScriptConverter(tmp_path / "nope")
        notebooks = converter.find_notebooks()
        assert notebooks == []


# ===========================================================================
# 9. Full conversion pipeline (two-pass)
# ===========================================================================

class TestConvertAll:

    def _setup_dvp(self, tmp_path):
        source = tmp_path / "dvp" / "01-source"
        tests = tmp_path / "dvp" / "03-tests"
        source.mkdir(parents=True)
        tests.mkdir(parents=True)
        return source

    def test_run_target_generated_flat(self, tmp_path):
        source = self._setup_dvp(tmp_path)
        _write_dbx(source / "pipeline.py", [
            '# MAGIC %run ./config',
            "print('pipeline')",
        ])
        _write_dbx(source / "config.py", [
            "ENV = 'dev'",
        ])

        converter = NotebookToScriptConverter(source)
        results = converter.convert_all()

        assert len(results) == 2
        config_result = next(r for r in results if "config" in r.source_path.name)
        assert config_result.success

        config_output = source / "config.dbx.py"
        assert config_output.exists()
        content = config_output.read_text()
        assert "# DVP:FLAT" in content
        assert "def run():" not in content

    def test_entrypoint_generated_wrapped(self, tmp_path):
        source = self._setup_dvp(tmp_path)
        _write_dbx(source / "pipeline.py", [
            '# MAGIC %run ./config',
            "print('pipeline')",
        ])
        _write_dbx(source / "config.py", [
            "ENV = 'dev'",
        ])

        converter = NotebookToScriptConverter(source)
        converter.convert_all()

        pipeline_output = source / "pipeline.dbx.py"
        assert pipeline_output.exists()
        content = pipeline_output.read_text()
        assert "# DVP:FLAT" not in content
        assert "def run():" in content

    def test_ipynb_conversion(self, tmp_path):
        source = self._setup_dvp(tmp_path)
        _write_ipynb(source / "analysis.ipynb", [
            _make_md_cell("# Analysis"),
            _make_code_cell("import pandas as pd"),
            _make_code_cell("df = pd.read_csv('data.csv')"),
        ])

        converter = NotebookToScriptConverter(source)
        results = converter.convert_all()

        assert len(results) == 1
        assert results[0].success
        assert results[0].source_format == "ipynb"

        output = source / "analysis.ipynb.py"
        assert output.exists()
        content = output.read_text()
        assert "def run():" in content
        assert "# # Analysis" in content

    def test_mixed_formats(self, tmp_path):
        source = self._setup_dvp(tmp_path)
        _write_ipynb(source / "nb.ipynb", [_make_code_cell("x = 1")])
        _write_dbx(source / "config.py", ["ENV = 'dev'"])

        converter = NotebookToScriptConverter(source)
        results = converter.convert_all()

        assert len(results) == 2
        formats = {r.source_format for r in results}
        assert formats == {"ipynb", "databricks"}

    def test_invalid_ipynb_reports_error(self, tmp_path):
        source = self._setup_dvp(tmp_path)
        (source / "bad.ipynb").write_text("not json{{{")

        converter = NotebookToScriptConverter(source)
        results = converter.convert_all()

        assert len(results) == 1
        assert results[0].success is False
        assert "Invalid JSON" in results[0].error

    def test_helper_copied_to_tests_dir(self, tmp_path):
        source = self._setup_dvp(tmp_path)
        _write_dbx(source / "nb.py", ["x = 1"])

        converter = NotebookToScriptConverter(source)
        converter.convert_all()

        helper = tmp_path / "dvp" / "03-tests" / "dvp_notebook_helper.py"
        assert helper.exists()

    def test_not_inside_dvp_aborts(self, tmp_path):
        source = tmp_path / "random_dir"
        source.mkdir()
        _write_dbx(source / "nb.py", ["x = 1"])

        converter = NotebookToScriptConverter(source)
        results = converter.convert_all()
        assert results == []

    def test_run_in_ipynb_makes_target_flat(self, tmp_path):
        """An .ipynb with %run should cause the target to be flat."""
        source = self._setup_dvp(tmp_path)
        _write_ipynb(source / "main.ipynb", [
            _make_code_cell("%run ./shared"),
            _make_code_cell("print(SHARED_VAR)"),
        ])
        _write_dbx(source / "shared.py", [
            "SHARED_VAR = 'hello'",
        ])

        converter = NotebookToScriptConverter(source)
        converter.convert_all()

        shared_output = source / "shared.dbx.py"
        assert shared_output.exists()
        content = shared_output.read_text()
        assert "# DVP:FLAT" in content
