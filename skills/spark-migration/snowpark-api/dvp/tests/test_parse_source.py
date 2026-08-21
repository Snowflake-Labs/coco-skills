"""Tests for parse_source() and resolve_entrypoint() utilities."""

import json
import sys
import types
from pathlib import Path

_DVP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_DVP_DIR / "dvp-test-setup-generator" / "templates"))

from conftest import parse_source


class TestParseSource:
    """Validate hybrid source format parsing."""

    def test_python_script(self):
        result = parse_source("workload.py:134")
        assert result == {"file": "workload.py", "lineno": 134}

    def test_python_script_in_subfolder(self):
        result = parse_source("jobs/daily_report.py:36")
        assert result == {"file": "jobs/daily_report.py", "lineno": 36}

    def test_python_adapted_with_method(self):
        result = parse_source("workload.py:295::main_entrypoint")
        assert result == {"file": "workload.py", "lineno": 295, "method": "main_entrypoint"}

    def test_python_adapted_existing_function(self):
        result = parse_source("workload.py:163::main")
        assert result == {"file": "workload.py", "lineno": 163, "method": "main"}

    def test_scala_object_method(self):
        result = parse_source("App.scala:5::GlobalTransactions::main")
        assert result == {
            "file": "App.scala",
            "lineno": 5,
            "scope": ["GlobalTransactions"],
            "method": "main",
        }

    def test_notebook_line_1(self):
        result = parse_source("ingest_notebook.py:1")
        assert result == {"file": "ingest_notebook.py", "lineno": 1}

    def test_notebook_adapted_with_run(self):
        result = parse_source("ingest_notebook.dbx.py:15::run")
        assert result == {"file": "ingest_notebook.dbx.py", "lineno": 15, "method": "run"}

    def test_deep_subfolder(self):
        result = parse_source("src/main/scala/com/example/Pipeline.scala:42::Pipeline::execute")
        assert result == {
            "file": "src/main/scala/com/example/Pipeline.scala",
            "lineno": 42,
            "scope": ["Pipeline"],
            "method": "execute",
        }

    def test_collision_suffix(self):
        result = parse_source("workload.py:300::main_entrypoint_02")
        assert result == {"file": "workload.py", "lineno": 300, "method": "main_entrypoint_02"}

    def test_method_present_means_callable(self):
        """If 'method' key exists, the entrypoint has a specific callable."""
        without = parse_source("workload.py:217")
        with_method = parse_source("workload.py:163::main")

        assert "method" not in without
        assert "method" in with_method

    def test_scope_only_present_with_multiple_segments(self):
        """scope is only set when there are 2+ :: segments beyond file:line."""
        python = parse_source("workload.py:163::main")
        scala = parse_source("App.scala:5::MyApp::main")

        assert "scope" not in python
        assert scala["scope"] == ["MyApp"]

    def test_nested_scope(self):
        """Multiple scope segments for nested classes/objects."""
        result = parse_source("file.py:10::Outer::Inner::run")
        assert result == {
            "file": "file.py",
            "lineno": 10,
            "scope": ["Outer", "Inner"],
            "method": "run",
        }

    def test_python_class_method(self):
        """Python class with method — same pattern as Scala."""
        result = parse_source("pipeline.py:25::PipelineRunner::execute")
        assert result == {
            "file": "pipeline.py",
            "lineno": 25,
            "scope": ["PipelineRunner"],
            "method": "execute",
        }
