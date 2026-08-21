"""
Tests for dvp_notebook_helper.

Covers:
- Notebook initialization (name, caller_dir, logging)
- Cell tracking (cell method)
- Magic command handling (magic method)
- %run resolution (_resolve_notebook)
- %run execution (exec into caller globals, flat-marker warning)
- Finish / error reporting
"""

import logging
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from dvp_notebook_helper import Notebook


# ===========================================================================
# 1. Notebook initialization
# ===========================================================================

class TestNotebookInit:

    def test_name_stored(self):
        nb = Notebook("test.ipynb")
        assert nb.name == "test.ipynb"

    def test_caller_dir_from_file(self, tmp_path):
        fake_file = tmp_path / "scripts" / "main.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.touch()

        nb = Notebook("main.py", str(fake_file))
        assert nb._caller_dir == fake_file.resolve().parent

    def test_caller_dir_none_when_omitted(self):
        nb = Notebook("nb.py")
        assert nb._caller_dir is None

    def test_logger_created(self):
        nb = Notebook("test_log.py")
        assert nb._logger.name == "dvp.notebook.test_log.py"

    def test_start_time_set(self):
        nb = Notebook("nb.py")
        assert nb._start_time is not None


# ===========================================================================
# 2. Cell tracking
# ===========================================================================

class TestCellTracking:

    def test_cell_sets_current_cell(self):
        nb = Notebook("nb.py")
        nb.cell("001")
        assert nb._current_cell == "001"

    def test_cell_sets_type(self):
        nb = Notebook("nb.py")
        nb.cell("002", "markdown")
        assert nb._cell_type == "markdown"

    def test_cell_defaults_to_code(self):
        nb = Notebook("nb.py")
        nb.cell("003")
        assert nb._cell_type == "code"

    def test_cell_progression(self):
        nb = Notebook("nb.py")
        nb.cell("001")
        nb.cell("002")
        nb.cell("003")
        assert nb._current_cell == "003"


# ===========================================================================
# 3. Magic command handling
# ===========================================================================

class TestMagic:

    def test_magic_logs(self, caplog):
        nb = Notebook("nb.py")
        with caplog.at_level(logging.INFO, logger="dvp.notebook"):
            nb.magic("%pip", "install pandas")
        assert "MAGIC [%pip]: install pandas" in caplog.text

    def test_magic_shell(self, caplog):
        nb = Notebook("nb.py")
        with caplog.at_level(logging.INFO, logger="dvp.notebook"):
            nb.magic("!", "ls -la")
        assert "MAGIC [!]: ls -la" in caplog.text


# ===========================================================================
# 4. %run resolution (_resolve_notebook)
# ===========================================================================

class TestResolveNotebook:

    def test_resolve_dbx_extension(self, tmp_path):
        target = tmp_path / "config.dbx.py"
        target.write_text("# DVP:FLAT\nx = 1\n")

        nb = Notebook("main.py", str(tmp_path / "main.py"))
        resolved = nb._resolve_notebook("./config")
        assert resolved == target

    def test_resolve_ipynb_extension(self, tmp_path):
        target = tmp_path / "utils.ipynb.py"
        target.write_text("# DVP:FLAT\ny = 2\n")

        nb = Notebook("main.py", str(tmp_path / "main.py"))
        resolved = nb._resolve_notebook("./utils")
        assert resolved == target

    def test_dbx_preferred_over_ipynb(self, tmp_path):
        dbx = tmp_path / "config.dbx.py"
        ipynb = tmp_path / "config.ipynb.py"
        dbx.write_text("# dbx\n")
        ipynb.write_text("# ipynb\n")

        nb = Notebook("main.py", str(tmp_path / "main.py"))
        resolved = nb._resolve_notebook("./config")
        assert resolved == dbx

    def test_resolve_with_py_suffix(self, tmp_path):
        target = tmp_path / "config.dbx.py"
        target.write_text("# content\n")

        nb = Notebook("main.py", str(tmp_path / "main.py"))
        resolved = nb._resolve_notebook("./config.py")
        assert resolved == target

    def test_resolve_relative_parent(self, tmp_path):
        subdir = tmp_path / "jobs"
        subdir.mkdir()
        target = tmp_path / "config.dbx.py"
        target.write_text("# content\n")

        nb = Notebook("job.py", str(subdir / "job.py"))
        resolved = nb._resolve_notebook("../config")
        assert resolved.resolve() == target.resolve()

    def test_resolve_returns_none_for_missing(self, tmp_path):
        nb = Notebook("main.py", str(tmp_path / "main.py"))
        resolved = nb._resolve_notebook("./nonexistent")
        assert resolved is None

    def test_resolve_returns_none_without_caller(self):
        nb = Notebook("main.py")
        resolved = nb._resolve_notebook("./config")
        assert resolved is None


# ===========================================================================
# 5. %run execution (shared namespace via exec)
# ===========================================================================

class TestRunExecution:

    def _make_flat_script(self, path: Path, code: str):
        """Write a flat (DVP:FLAT) script."""
        content = textwrap.dedent(f"""\
        # DVP:FLAT — this script is a %run target (no def run wrapper)
        {code}
        """)
        path.write_text(content)

    def test_run_flat_script_shares_globals(self, tmp_path):
        target = tmp_path / "config.dbx.py"
        self._make_flat_script(target, "SHARED_VAR = 42")

        nb = Notebook("main.py", str(tmp_path / "main.py"))
        nb.run("./config")

        assert globals().get("SHARED_VAR") == 42
        # Cleanup
        globals().pop("SHARED_VAR", None)

    def test_run_missing_target_logs_warning(self, tmp_path, caplog):
        nb = Notebook("main.py", str(tmp_path / "main.py"))
        with caplog.at_level(logging.WARNING, logger="dvp.notebook"):
            nb.run("./nonexistent")
        assert "not found" in caplog.text

    def test_run_non_flat_warns(self, tmp_path, caplog):
        target = tmp_path / "config.dbx.py"
        target.write_text("x = 1\n")

        nb = Notebook("main.py", str(tmp_path / "main.py"))
        with caplog.at_level(logging.WARNING, logger="dvp.notebook"):
            nb.run("./config")

        assert "NOT a flat script" in caplog.text

    def test_run_with_params_logs(self, tmp_path, caplog):
        target = tmp_path / "config.dbx.py"
        self._make_flat_script(target, "BRAND = 'default'")

        nb = Notebook("main.py", str(tmp_path / "main.py"))
        with caplog.at_level(logging.INFO, logger="dvp.notebook"):
            nb.run("./config", '$brand="acme"')

        assert '$brand="acme"' in caplog.text
        globals().pop("BRAND", None)


# ===========================================================================
# 6. Finish and error reporting
# ===========================================================================

class TestFinishAndErrors:

    def test_finish_logs_success(self, caplog):
        nb = Notebook("nb.py")
        nb.cell("001")
        with caplog.at_level(logging.INFO, logger="dvp.notebook"):
            nb.finish()
        assert "finished successfully" in caplog.text

    def test_report_error_logs_cell(self, caplog):
        nb = Notebook("nb.py")
        nb.cell("005")
        with caplog.at_level(logging.ERROR, logger="dvp.notebook"):
            nb.report_error(ValueError("test error"))
        assert "Cell 005" in caplog.text
        assert "ERROR" in caplog.text

    def test_report_error_includes_traceback(self, caplog):
        nb = Notebook("nb.py")
        nb.cell("001")
        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            with caplog.at_level(logging.ERROR, logger="dvp.notebook"):
                nb.report_error(e)
        assert "boom" in caplog.text
