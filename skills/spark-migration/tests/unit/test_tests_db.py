"""Tests for database-touching test functions in sma_api.

Covers: create_tests_table, register_tests, insert_test_run,
        get_tests, get_test_runs, has_tests, update_test_status,
        export_test_results.
"""

import csv
import os
import sqlite3

import pytest

import sma_api


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workload(tmp_path):
    """Return a workload path string (bare directory, no DB yet)."""
    return str(tmp_path)


@pytest.fixture
def db_path(tmp_path):
    """Return a DB file path inside tmp_path (file does not exist yet)."""
    return str(tmp_path / "sma_storage.sqlite3")


@pytest.fixture
def sample_tests():
    """Minimal list of test dicts suitable for register_tests()."""
    return [
        {
            "entrypoint_name": "pipeline_etl",
            "entrypoint_source": "pipeline_etl.py:1",
            "test_file": "dvp/03-tests/source/test_pipeline_etl.py",
            "test_type": "source",
        },
        {
            "entrypoint_name": "pipeline_etl",
            "entrypoint_source": "pipeline_etl.py:1",
            "test_file": "dvp/03-tests/migrated/test_pipeline_etl.py",
            "test_type": "migrated",
        },
        {
            "entrypoint_name": "loader",
            "entrypoint_source": "loader.py:1",
            "test_file": "dvp/03-tests/source/test_loader.py",
            "test_type": "source",
        },
    ]


@pytest.fixture
def seeded_tests(workload, sample_tests):
    """Register sample_tests and return (workload_path, result)."""
    result = sma_api.register_tests(workload, sample_tests)
    assert result["success"] is True
    return workload, result


# ---------------------------------------------------------------------------
# create_tests_table
# ---------------------------------------------------------------------------

class TestCreateTestsTable:
    def test_creates_tables_via_workload_path(self, workload):
        result = sma_api.create_tests_table(workload)
        assert result == {"success": True}
        db = os.path.join(workload, "sma_storage.sqlite3")
        assert os.path.isfile(db)
        with sqlite3.connect(db) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "entrypoint_tests" in tables
        assert "entrypoint_test_runs" in tables

    def test_creates_tables_via_db_path(self, db_path):
        result = sma_api.create_tests_table(db_path=db_path)
        assert result["success"] is True
        assert os.path.isfile(db_path)

    def test_idempotent(self, workload):
        sma_api.create_tests_table(workload)
        result = sma_api.create_tests_table(workload)
        assert result["success"] is True

    def test_entrypoint_tests_schema(self, db_path):
        sma_api.create_tests_table(db_path=db_path)
        with sqlite3.connect(db_path) as conn:
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(entrypoint_tests)")
            }
        expected = {
            "id", "entrypoint_name", "entrypoint_source",
            "test_file", "test_type", "status", "created_at",
        }
        assert expected == cols

    def test_entrypoint_test_runs_schema(self, db_path):
        sma_api.create_tests_table(db_path=db_path)
        with sqlite3.connect(db_path) as conn:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(entrypoint_test_runs)")
            }
        expected = {
            "id", "test_id", "test_method", "status",
            "error_message", "duration_seconds", "executed_at",
        }
        assert expected == cols


# ---------------------------------------------------------------------------
# register_tests
# ---------------------------------------------------------------------------

class TestRegisterTests:
    def test_inserts_all(self, workload, sample_tests):
        result = sma_api.register_tests(workload, sample_tests)
        assert result["success"] is True
        assert result["inserted"] == 3

    def test_deduplication_by_name_and_type(self, workload, sample_tests):
        sma_api.register_tests(workload, sample_tests)
        result = sma_api.register_tests(workload, sample_tests)
        assert result["inserted"] == 0

    def test_partial_deduplication(self, workload, sample_tests):
        sma_api.register_tests(workload, sample_tests[:1])
        result = sma_api.register_tests(workload, sample_tests)
        # First entry already exists, second and third are new
        assert result["inserted"] == 2

    def test_defaults_test_type_to_source(self, workload):
        tests = [{"entrypoint_name": "ep1", "test_file": "test_ep1.py"}]
        sma_api.register_tests(workload, tests)
        result = sma_api.get_tests(workload)
        assert result["tests"][0]["test_type"] == "source"

    def test_defaults_status_to_pending(self, workload):
        tests = [{"entrypoint_name": "ep1", "test_file": "test_ep1.py"}]
        sma_api.register_tests(workload, tests)
        result = sma_api.get_tests(workload)
        assert result["tests"][0]["status"] == "pending"

    def test_stores_entrypoint_source(self, workload, sample_tests):
        sma_api.register_tests(workload, sample_tests)
        result = sma_api.get_tests(workload)
        sources = {t["entrypoint_source"] for t in result["tests"]}
        assert "pipeline_etl.py:1" in sources
        assert "loader.py:1" in sources

    def test_empty_list_inserts_nothing(self, workload):
        result = sma_api.register_tests(workload, [])
        assert result["success"] is True
        assert result["inserted"] == 0


# ---------------------------------------------------------------------------
# insert_test_run
# ---------------------------------------------------------------------------

class TestInsertTestRun:
    def test_insert_passed_run(self, seeded_tests):
        wp, _ = seeded_tests
        result = sma_api.insert_test_run(wp, test_id=1, status="passed", duration_seconds=1.5)
        assert result["success"] is True
        assert "run_id" in result

    def test_insert_failed_run_with_error(self, seeded_tests):
        wp, _ = seeded_tests
        result = sma_api.insert_test_run(
            wp, test_id=1, status="failed",
            error_message="AssertionError: values differ",
            duration_seconds=0.3,
        )
        assert result["success"] is True
        runs = sma_api.get_test_runs(wp, test_id=1)
        assert runs["runs"][0]["error_message"] == "AssertionError: values differ"

    def test_insert_run_with_test_method(self, seeded_tests):
        wp, _ = seeded_tests
        sma_api.insert_test_run(
            wp, test_id=1, status="passed",
            test_method="test_row_count",
        )
        runs = sma_api.get_test_runs(wp, test_id=1)
        assert runs["runs"][0]["test_method"] == "test_row_count"

    def test_updates_parent_test_status(self, seeded_tests):
        wp, _ = seeded_tests
        sma_api.insert_test_run(wp, test_id=1, status="passed")
        tests = sma_api.get_tests(wp)
        test_1 = next(t for t in tests["tests"] if t["id"] == 1)
        assert test_1["status"] == "passed"

    def test_invalid_status_returns_error(self, seeded_tests):
        wp, _ = seeded_tests
        result = sma_api.insert_test_run(wp, test_id=1, status="completed")
        assert "error" in result
        assert "Invalid status" in result["error"]

    @pytest.mark.parametrize("status", sorted(sma_api.VALID_TEST_STATUSES))
    def test_all_valid_statuses_accepted(self, seeded_tests, status):
        wp, _ = seeded_tests
        result = sma_api.insert_test_run(wp, test_id=1, status=status)
        assert result["success"] is True

    def test_multiple_runs_for_same_test(self, seeded_tests):
        wp, _ = seeded_tests
        sma_api.insert_test_run(wp, test_id=1, status="failed")
        sma_api.insert_test_run(wp, test_id=1, status="passed")
        runs = sma_api.get_test_runs(wp, test_id=1)
        assert len(runs["runs"]) == 2


# ---------------------------------------------------------------------------
# get_tests / get_test_runs
# ---------------------------------------------------------------------------

class TestGetTests:
    def test_returns_all_registered(self, seeded_tests):
        wp, _ = seeded_tests
        result = sma_api.get_tests(wp)
        assert result["success"] is True
        assert len(result["tests"]) == 3

    def test_empty_when_no_tests(self, workload):
        result = sma_api.get_tests(workload)
        assert result["tests"] == []

    def test_island_is_null_without_deps(self, seeded_tests):
        wp, _ = seeded_tests
        result = sma_api.get_tests(wp)
        for t in result["tests"]:
            assert t["island"] is None


class TestGetTestRuns:
    def test_returns_runs_for_test_id(self, seeded_tests):
        wp, _ = seeded_tests
        sma_api.insert_test_run(wp, test_id=1, status="passed")
        sma_api.insert_test_run(wp, test_id=2, status="failed")
        runs = sma_api.get_test_runs(wp, test_id=1)
        assert len(runs["runs"]) == 1
        assert runs["runs"][0]["status"] == "passed"

    def test_returns_all_runs_when_no_filter(self, seeded_tests):
        wp, _ = seeded_tests
        sma_api.insert_test_run(wp, test_id=1, status="passed")
        sma_api.insert_test_run(wp, test_id=2, status="failed")
        runs = sma_api.get_test_runs(wp)
        assert len(runs["runs"]) == 2

    def test_empty_when_no_runs(self, seeded_tests):
        wp, _ = seeded_tests
        runs = sma_api.get_test_runs(wp, test_id=1)
        assert runs["runs"] == []


# ---------------------------------------------------------------------------
# has_tests
# ---------------------------------------------------------------------------

class TestHasTests:
    def test_false_when_no_db(self, tmp_path):
        assert sma_api.has_tests(str(tmp_path)) is False

    def test_false_when_table_empty(self, workload):
        sma_api.create_tests_table(workload)
        assert sma_api.has_tests(workload) is False

    def test_true_when_tests_registered(self, seeded_tests):
        wp, _ = seeded_tests
        assert sma_api.has_tests(wp) is True


# ---------------------------------------------------------------------------
# update_test_status
# ---------------------------------------------------------------------------

class TestUpdateTestStatus:
    def test_updates_status(self, seeded_tests):
        wp, _ = seeded_tests
        result = sma_api.update_test_status(wp, test_id=1, status="passed")
        assert result["success"] is True
        tests = sma_api.get_tests(wp)
        test_1 = next(t for t in tests["tests"] if t["id"] == 1)
        assert test_1["status"] == "passed"

    def test_invalid_status_returns_error(self, seeded_tests):
        wp, _ = seeded_tests
        result = sma_api.update_test_status(wp, test_id=1, status="done")
        assert "error" in result


# ---------------------------------------------------------------------------
# export_test_results
# ---------------------------------------------------------------------------

class TestExportTestResults:
    def test_creates_csv_file(self, seeded_tests):
        wp, _ = seeded_tests
        result = sma_api.export_test_results(wp)
        assert result["success"] is True
        filepath = os.path.join(result["path"], result["file"])
        assert os.path.isfile(filepath)

    def test_csv_contains_headers(self, seeded_tests):
        wp, _ = seeded_tests
        result = sma_api.export_test_results(wp)
        filepath = os.path.join(result["path"], result["file"])
        with open(filepath) as f:
            reader = csv.reader(f)
            headers = next(reader)
        expected = [
            "entrypoint_name", "entrypoint_source", "island", "test_file",
            "test_type", "test_status", "created_at", "test_method",
            "run_status", "error_message", "duration_seconds", "executed_at",
        ]
        assert headers == expected

    def test_csv_row_count_matches_tests(self, seeded_tests):
        wp, _ = seeded_tests
        result = sma_api.export_test_results(wp)
        assert result["rows"] == 3  # 3 tests, no runs yet

    def test_csv_includes_run_data(self, seeded_tests):
        wp, _ = seeded_tests
        sma_api.insert_test_run(
            wp, test_id=1, status="passed",
            test_method="test_row_count", duration_seconds=1.2,
        )
        result = sma_api.export_test_results(wp)
        filepath = os.path.join(result["path"], result["file"])
        with open(filepath) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        passed_rows = [r for r in rows if r["run_status"] == "passed"]
        assert len(passed_rows) == 1
        assert passed_rows[0]["test_method"] == "test_row_count"

    def test_filename_contains_timestamp(self, seeded_tests):
        wp, _ = seeded_tests
        result = sma_api.export_test_results(wp)
        assert result["file"].startswith("test_results_")
        assert result["file"].endswith(".csv")

    def test_empty_export(self, workload):
        result = sma_api.export_test_results(workload)
        assert result["success"] is True
        assert result["rows"] == 0
