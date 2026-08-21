"""Tests for register_tests.py — DVP test registration CLI wrapper.

Covers: format normalization for entrypoints.json, test file matching,
        workload root file creation, dashboard manifest update, and CLI entry.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure the script under test can be imported
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "dvp" / "dvp-test-setup-generator" / "scripts"
_SMA_API_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))
sys.path.insert(0, str(_SMA_API_DIR))

import register_tests  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_workload(tmp_path, entrypoints, create_test_files=True):
    """Build a minimal workload directory with entrypoints.json and test files."""
    wp = tmp_path / "workload"
    wp.mkdir()

    # dvp/04-results/entrypoints.json
    results_dir = wp / "dvp" / "04-results"
    results_dir.mkdir(parents=True)
    (results_dir / "entrypoints.json").write_text(
        json.dumps(entrypoints), encoding="utf-8"
    )

    if create_test_files:
        # dvp/03-tests/source/
        source_dir = wp / "dvp" / "03-tests" / "source"
        source_dir.mkdir(parents=True)
        # Create test files that match entrypoint names
        names = entrypoints if isinstance(entrypoints, list) else entrypoints.get("entrypoints", [])
        for ep in names:
            name = ep.get("name") or Path(ep.get("file", "unknown")).stem
            # Test files use underscores (matching real dvp-test-setup-generator output)
            file_name = name.replace("-", "_")
            (source_dir / f"test_{file_name}.py").write_text(
                f"# test for {name}\n", encoding="utf-8"
            )

    return wp


@pytest.fixture
def canonical_workload(tmp_path):
    """Workload with canonical flat-list entrypoints.json format."""
    entrypoints = [
        {"name": "pipeline_etl", "source": "pipeline_etl.py:1", "type": "pipeline", "status": "detected"},
        {"name": "loader", "source": "loader.py:1", "type": "pipeline", "status": "detected"},
    ]
    return _make_workload(tmp_path, entrypoints)


@pytest.fixture
def dict_format_workload(tmp_path):
    """Workload with agent-produced dict-wrapped entrypoints.json format."""
    entrypoints = {
        "entrypoints": [
            {"file": "pipeline_etl.py", "source_path": "/src/pipeline_etl.py", "type": "pipeline"},
            {"file": "loader.py", "source_path": "/src/loader.py", "type": "pipeline"},
        ]
    }
    return _make_workload(tmp_path, entrypoints)


# ---------------------------------------------------------------------------
# Format normalization
# ---------------------------------------------------------------------------

class TestFormatNormalization:
    def test_canonical_format_accepted(self, canonical_workload):
        rc = register_tests.run(canonical_workload)
        assert rc == 0

    def test_dict_wrapped_format_accepted(self, dict_format_workload):
        rc = register_tests.run(dict_format_workload)
        assert rc == 0

    def test_unrecognized_format_returns_error(self, tmp_path):
        wp = tmp_path / "workload"
        wp.mkdir()
        results_dir = wp / "dvp" / "04-results"
        results_dir.mkdir(parents=True)
        (results_dir / "entrypoints.json").write_text(
            json.dumps({"unexpected_key": "value"}), encoding="utf-8"
        )
        rc = register_tests.run(wp)
        assert rc == 1

    def test_dict_format_derives_name_from_file(self, dict_format_workload):
        """When format is {"entrypoints": [{file: ...}]}, name = stem of file."""
        rc = register_tests.run(dict_format_workload)
        assert rc == 0
        import sma_api
        result = sma_api.get_tests(str(dict_format_workload))
        names = {t["entrypoint_name"] for t in result["tests"]}
        assert "pipeline_etl" in names
        assert "loader" in names

    def test_dict_format_derives_source_from_source_path(self, dict_format_workload):
        rc = register_tests.run(dict_format_workload)
        assert rc == 0
        import sma_api
        result = sma_api.get_tests(str(dict_format_workload))
        sources = {t["entrypoint_source"] for t in result["tests"]}
        assert "pipeline_etl.py:1" in sources

    def test_dict_format_defaults_status_to_detected(self, dict_format_workload):
        """Entries without 'status' should default to 'detected' and be registered."""
        rc = register_tests.run(dict_format_workload)
        assert rc == 0
        import sma_api
        result = sma_api.get_tests(str(dict_format_workload))
        assert len(result["tests"]) == 2  # both should be registered


# ---------------------------------------------------------------------------
# Test file matching
# ---------------------------------------------------------------------------

class TestMatchTestToEntrypoint:
    def test_matches_by_name(self):
        test_file = Path("/dvp/03-tests/source/test_pipeline_etl.py")
        eps = [{"name": "pipeline_etl"}, {"name": "loader"}]
        match = register_tests._match_test_to_entrypoint(test_file, eps)
        assert match is not None
        assert match["name"] == "pipeline_etl"

    def test_case_insensitive_match(self):
        test_file = Path("/dvp/03-tests/source/test_Pipeline_ETL.py")
        eps = [{"name": "pipeline_etl"}]
        match = register_tests._match_test_to_entrypoint(test_file, eps)
        assert match is not None

    def test_hyphen_underscore_match(self):
        """Entrypoint 'pyspark-add-month' should match test_pyspark_add_month.py."""
        test_file = Path("/dvp/03-tests/source/test_pyspark_add_month.py")
        eps = [{"name": "pyspark-add-month"}]
        match = register_tests._match_test_to_entrypoint(test_file, eps)
        assert match is not None
        assert match["name"] == "pyspark-add-month"

    def test_no_match_returns_none(self):
        test_file = Path("/dvp/03-tests/source/test_unknown.py")
        eps = [{"name": "pipeline_etl"}]
        match = register_tests._match_test_to_entrypoint(test_file, eps)
        assert match is None

    def test_camel_case_to_snake_case_match(self):
        """Entrypoint 'MyFile' should match test_my_file.py."""
        test_file = Path("/dvp/03-tests/source/test_my_file.py")
        eps = [{"name": "MyFile"}]
        match = register_tests._match_test_to_entrypoint(test_file, eps)
        assert match is not None
        assert match["name"] == "MyFile"


# ---------------------------------------------------------------------------
# _detect_migrated_suite
# ---------------------------------------------------------------------------

class TestDetectMigratedSuite:
    def test_detects_migrated(self, tmp_path):
        (tmp_path / "migrated").mkdir()
        assert register_tests._detect_migrated_suite(tmp_path) == "migrated"

    def test_detects_migrated_scos(self, tmp_path):
        (tmp_path / "migrated_scos").mkdir()
        assert register_tests._detect_migrated_suite(tmp_path) == "migrated_scos"

    def test_prefers_migrated_over_scos(self, tmp_path):
        (tmp_path / "migrated").mkdir()
        (tmp_path / "migrated_scos").mkdir()
        assert register_tests._detect_migrated_suite(tmp_path) == "migrated"

    def test_returns_none_when_neither(self, tmp_path):
        assert register_tests._detect_migrated_suite(tmp_path) is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_missing_entrypoints_json(self, tmp_path):
        wp = tmp_path / "workload"
        wp.mkdir()
        rc = register_tests.run(wp)
        assert rc == 1

    def test_no_detected_entrypoints(self, tmp_path):
        entrypoints = [
            {"name": "done", "source": "done.py:1", "status": "completed"},
        ]
        wp = _make_workload(tmp_path, entrypoints)
        rc = register_tests.run(wp)
        assert rc == 0  # exits gracefully

    def test_no_test_files_found(self, tmp_path):
        entrypoints = [
            {"name": "pipeline_etl", "source": "pipeline_etl.py:1", "status": "detected"},
        ]
        wp = _make_workload(tmp_path, entrypoints, create_test_files=False)
        # Create the tests dir but no files
        (wp / "dvp" / "03-tests" / "source").mkdir(parents=True)
        rc = register_tests.run(wp)
        assert rc == 0  # exits gracefully

    def test_no_tests_dir(self, tmp_path):
        entrypoints = [
            {"name": "pipeline_etl", "source": "pipeline_etl.py:1", "status": "detected"},
        ]
        wp = _make_workload(tmp_path, entrypoints, create_test_files=False)
        rc = register_tests.run(wp)
        assert rc == 1


# ---------------------------------------------------------------------------
# Dashboard manifest update
# ---------------------------------------------------------------------------

class TestDashboardManifest:
    def test_updates_manifest_has_data(self, canonical_workload):
        manifest_dir = canonical_workload / "sma-dashboard"
        manifest_dir.mkdir()
        manifest = {
            "modules": {
                "test_tracker": {"has_data": False}
            }
        }
        (manifest_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        register_tests.run(canonical_workload)
        updated = json.loads((manifest_dir / "manifest.json").read_text())
        assert updated["modules"]["test_tracker"]["has_data"] is True

    def test_no_manifest_no_error(self, canonical_workload):
        """When no manifest exists, run still succeeds."""
        rc = register_tests.run(canonical_workload)
        assert rc == 0


# ---------------------------------------------------------------------------
# Workload root files
# ---------------------------------------------------------------------------

class TestWorkloadRootFiles:
    def test_creates_gitignore(self, canonical_workload):
        register_tests.run(canonical_workload)
        gitignore = canonical_workload / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert "metastore_db/" in content
        assert ".pytest_cache/" in content

    def test_merges_gitignore_entries(self, canonical_workload):
        existing = "# existing\nnode_modules/\n"
        (canonical_workload / ".gitignore").write_text(existing)
        register_tests.run(canonical_workload)
        content = (canonical_workload / ".gitignore").read_text()
        assert "node_modules/" in content
        assert "metastore_db/" in content


# ---------------------------------------------------------------------------
# Hyphen/underscore normalization (end-to-end)
# ---------------------------------------------------------------------------

class TestHyphenUnderscoreRegistration:
    def test_hyphenated_entrypoints_register_all(self, tmp_path):
        """Entrypoints with hyphens should match test files with underscores."""
        entrypoints = [
            {"name": "pyspark-add-month", "source": "pyspark_add_month.py:1", "status": "detected"},
            {"name": "pyspark-filter", "source": "pyspark_filter.py:1", "status": "detected"},
            {"name": "simple_job", "source": "simple_job.py:1", "status": "detected"},
        ]
        wp = _make_workload(tmp_path, entrypoints)
        rc = register_tests.run(wp)
        assert rc == 0
        import sma_api
        result = sma_api.get_tests(str(wp))
        assert len(result["tests"]) == 3
