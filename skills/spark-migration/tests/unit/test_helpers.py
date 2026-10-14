"""Tests for private helper functions (pure logic, no DB required)."""

import os
import sqlite3

import pytest

import sma_api
from sma_api import (
    BLOCKER_EWI_CODES,
    VALID_STATUSES,
    _aggregate_ewis,
    _aggregate_files,
    _connect,
    _db_from_workload,
    _extract_ewi_data,
    _normalize_path,
    _normalize_row,
    _parse_rows,
    _validate_status,
)


# ---------------------------------------------------------------------------
# _db_from_workload
# ---------------------------------------------------------------------------

class TestDbFromWorkload:
    def test_joins_workload_and_db_name(self):
        assert _db_from_workload("/tmp/my_workload") == os.path.join(
            "/tmp/my_workload", "sma_storage.sqlite3"
        )

    def test_empty_path(self):
        assert _db_from_workload("") == "sma_storage.sqlite3"


# ---------------------------------------------------------------------------
# _connect
# ---------------------------------------------------------------------------

class TestConnect:
    def test_auto_creates_missing_db(self, tmp_path):
        missing = os.path.join(str(tmp_path), "subdir", "nonexistent.db")
        with _connect(missing) as conn:
            assert conn is not None
        assert os.path.isfile(missing)

    def test_opens_existing_db(self, tmp_path):
        db_path = os.path.join(str(tmp_path), "test.db")
        sqlite3.connect(db_path).close()  # create empty file
        with _connect(db_path) as conn:
            assert conn is not None
            # row_factory should be set
            assert conn.row_factory == sqlite3.Row


# ---------------------------------------------------------------------------
# _normalize_path
# ---------------------------------------------------------------------------

class TestNormalizePath:
    def test_backslash_to_forward(self):
        assert _normalize_path("src\\etl\\file.py") == "src/etl/file.py"

    def test_strips_leading_slash(self):
        assert _normalize_path("/src/etl/file.py") == "src/etl/file.py"

    def test_both(self):
        assert _normalize_path("\\src\\etl\\file.py") == "src/etl/file.py"

    def test_empty(self):
        assert _normalize_path("") == ""

    def test_already_clean(self):
        assert _normalize_path("src/etl/file.py") == "src/etl/file.py"


# ---------------------------------------------------------------------------
# _validate_status
# ---------------------------------------------------------------------------

class TestValidateStatus:
    @pytest.mark.parametrize("status", sorted(VALID_STATUSES))
    def test_valid_status_returns_none(self, status):
        assert _validate_status(status) is None

    def test_invalid_status_returns_error(self):
        err = _validate_status("completed")
        assert err is not None
        assert "Invalid status" in err
        assert "completed" in err

    def test_empty_string_is_invalid(self):
        assert _validate_status("") is not None


# ---------------------------------------------------------------------------
# _normalize_row
# ---------------------------------------------------------------------------

class TestNormalizeRow:
    def test_standard_keys(self):
        row = {
            "Code": "SPRKPY1001",
            "Description": "Some issue",
            "Category": "Error",
            "FileId": "src/file.py",
            "Line": "10",
            "Column": "5",
            "Url": "https://example.com",
        }
        result = _normalize_row(row)
        assert result["code"] == "SPRKPY1001"
        assert result["description"] == "Some issue"
        assert result["category"] == "Error"
        assert result["file_id"] == "src/file.py"
        assert result["line"] == "10"

    def test_lowercase_keys(self):
        row = {"code": "X", "description": "Y", "category": "Z", "fileid": "f.py"}
        result = _normalize_row(row)
        assert result["code"] == "X"
        assert result["file_id"] == "f.py"

    def test_missing_keys_default_empty(self):
        result = _normalize_row({})
        assert result["code"] == ""
        assert result["status"] == ""
        assert result["notes"] == ""

    def test_none_value_mapped_to_empty(self):
        result = _normalize_row({"Code": None})
        assert result["code"] == ""


# ---------------------------------------------------------------------------
# _parse_rows
# ---------------------------------------------------------------------------

class TestParseRows:
    def test_normalizes_rows(self):
        rows = [
            {"Code": "X", "Category": "Error", "FileId": "\\a\\b.py", "Line": "5"},
        ]
        parsed = _parse_rows(rows)
        assert len(parsed) == 1
        assert parsed[0]["code"] == "X"
        assert parsed[0]["file_id"] == "a/b.py"  # normalized
        assert parsed[0]["status"] == "pending"  # default

    def test_empty_category_becomes_none_str(self):
        parsed = _parse_rows([{"Code": "X"}])
        assert parsed[0]["category"] == "None"


# ---------------------------------------------------------------------------
# _aggregate_ewis
# ---------------------------------------------------------------------------

class TestAggregateEwis:
    def test_groups_by_code(self):
        records = [
            {"code": "A", "description": "Desc A", "category": "Error", "file_id": "f1.py", "url": ""},
            {"code": "A", "description": "Desc A", "category": "Error", "file_id": "f2.py", "url": ""},
            {"code": "B", "description": "Desc B", "category": "Warning", "file_id": "f1.py", "url": ""},
        ]
        result = _aggregate_ewis(records)
        assert len(result) == 2
        a = result[0]
        assert a["code"] == "A"
        assert a["occurrences"] == 2
        assert sorted(a["files_affected"]) == ["f1.py", "f2.py"]
        b = result[1]
        assert b["code"] == "B"
        assert b["occurrences"] == 1

    def test_empty_code_skipped(self):
        records = [{"code": "", "description": "", "category": "", "file_id": "", "url": ""}]
        assert _aggregate_ewis(records) == []

    def test_sorted_by_code(self):
        records = [
            {"code": "Z", "description": "", "category": "", "file_id": "f.py", "url": ""},
            {"code": "A", "description": "", "category": "", "file_id": "f.py", "url": ""},
        ]
        result = _aggregate_ewis(records)
        assert [e["code"] for e in result] == ["A", "Z"]


# ---------------------------------------------------------------------------
# _aggregate_files
# ---------------------------------------------------------------------------

class TestAggregateFiles:
    def test_groups_by_file(self):
        records = [
            {"code": "A", "file_id": "f1.py", "line": "10", "status": "pending"},
            {"code": "A", "file_id": "f1.py", "line": "20", "status": "pending"},
            {"code": "B", "file_id": "f2.py", "line": "5", "status": "auto_resolved"},
        ]
        result = _aggregate_files(records)
        assert "f1.py" in result
        assert "f2.py" in result
        assert result["f1.py"]["file_status"] == "pending"
        assert result["f2.py"]["file_status"] == "auto_resolved"

    def test_mixed_status_is_in_progress(self):
        records = [
            {"code": "A", "file_id": "f.py", "line": "1", "status": "pending"},
            {"code": "B", "file_id": "f.py", "line": "2", "status": "auto_resolved"},
        ]
        result = _aggregate_files(records)
        assert result["f.py"]["file_status"] == "in_progress"

    def test_empty_records(self):
        assert _aggregate_files([]) == {}


# ---------------------------------------------------------------------------
# _extract_ewi_data
# ---------------------------------------------------------------------------

class TestExtractEwiData:
    def test_full_pipeline(self):
        rows = [
            {"Code": "A", "Description": "Issue A", "Category": "Error",
             "FileId": "f.py", "Line": "1", "Column": "1", "Url": ""},
        ]
        result = _extract_ewi_data(rows, workload_name="test")
        assert "ewi_data" in result
        assert "file_data" in result
        assert result["ewi_data"]["workload_name"] == "test"
        assert result["ewi_data"]["total_ewis"] == 1
        assert result["file_data"]["total_files"] == 1

    def test_status_summary_counts(self):
        rows = [
            {"Code": "A", "Description": "", "Category": "", "FileId": "f.py",
             "Line": "1", "Column": "", "Url": "", "status": "pending"},
            {"Code": "B", "Description": "", "Category": "", "FileId": "f.py",
             "Line": "2", "Column": "", "Url": "", "status": "auto_resolved"},
        ]
        result = _extract_ewi_data(rows)
        summary = result["ewi_data"]["summary"]
        assert summary["pending"] == 1
        assert summary["auto_resolved"] == 1

    def test_empty_rows(self):
        result = _extract_ewi_data([])
        assert result["ewi_data"]["total_ewis"] == 0
        assert result["file_data"]["total_files"] == 0
