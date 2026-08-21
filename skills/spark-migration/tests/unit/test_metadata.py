"""Tests for workload_metadata functions in sma_api.

Covers: set_metadata, get_metadata, get_all_metadata,
        and auto-detection of conversion_type in initialize_database.
"""

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


# ---------------------------------------------------------------------------
# set_metadata / get_metadata
# ---------------------------------------------------------------------------

class TestSetMetadata:
    def test_basic_set_and_get(self, workload):
        result = sma_api.set_metadata(workload, "conversion_type", "snowpark_api")
        assert result["success"] is True
        assert sma_api.get_metadata(workload, "conversion_type") == "snowpark_api"

    def test_upsert_overwrites(self, workload):
        sma_api.set_metadata(workload, "conversion_type", "snowpark_api")
        sma_api.set_metadata(workload, "conversion_type", "snowpark_connect")
        assert sma_api.get_metadata(workload, "conversion_type") == "snowpark_connect"

    def test_empty_key_returns_error(self, workload):
        result = sma_api.set_metadata(workload, "", "value")
        assert "error" in result

    def test_uses_db_path_kwarg(self, db_path):
        sma_api.set_metadata(key="mykey", value="myval", db_path=db_path)
        assert sma_api.get_metadata(key="mykey", db_path=db_path) == "myval"

    def test_multiple_keys(self, workload):
        sma_api.set_metadata(workload, "key_a", "val_a")
        sma_api.set_metadata(workload, "key_b", "val_b")
        assert sma_api.get_metadata(workload, "key_a") == "val_a"
        assert sma_api.get_metadata(workload, "key_b") == "val_b"


class TestGetMetadata:
    def test_returns_none_for_missing_key(self, workload):
        assert sma_api.get_metadata(workload, "nonexistent") is None

    def test_empty_key_returns_none(self, workload):
        assert sma_api.get_metadata(workload, "") is None

    def test_creates_table_if_missing(self, db_path):
        # get_metadata on a fresh DB should not crash
        assert sma_api.get_metadata(key="foo", db_path=db_path) is None
        # table should exist now
        with sqlite3.connect(db_path) as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "workload_metadata" in tables


# ---------------------------------------------------------------------------
# get_all_metadata
# ---------------------------------------------------------------------------

class TestGetAllMetadata:
    def test_empty_on_fresh_db(self, workload):
        result = sma_api.get_all_metadata(workload)
        assert result == {}

    def test_returns_all_pairs(self, workload):
        sma_api.set_metadata(workload, "key_a", "val_a")
        sma_api.set_metadata(workload, "key_b", "val_b")
        result = sma_api.get_all_metadata(workload)
        assert result == {"key_a": "val_a", "key_b": "val_b"}

    def test_uses_db_path_kwarg(self, db_path):
        sma_api.set_metadata(key="k", value="v", db_path=db_path)
        result = sma_api.get_all_metadata(db_path=db_path)
        assert result == {"k": "v"}


# ---------------------------------------------------------------------------
# Auto-detection in initialize_database
# ---------------------------------------------------------------------------

ISSUES_CSV_HEADER = "Code,Description,Category,FileId,Line,Column,Url\n"
ISSUES_CSV_ROW = "SPRKPY1001,Test,Error,file.py,1,1,http://example.com\n"
ISSUES_CSV_ROW_SCOS_PY = "SPRKCNTPY1001,Test,Error,file.py,1,1,http://example.com\n"
ISSUES_CSV_ROW_SCOS_SCL = "SPRKCNTSCL1001,Test,Error,file.scala,1,1,http://example.com\n"


class TestConversionTypeAutoDetection:
    def test_snowpark_api_from_issues_csv(self, tmp_path):
        reports = tmp_path / "Reports"
        reports.mkdir()
        (reports / "Issues.csv").write_text(ISSUES_CSV_HEADER + ISSUES_CSV_ROW)
        wp = str(tmp_path)
        sma_api.initialize_database(wp)
        assert sma_api.get_metadata(wp, "conversion_type") == "snowpark_api"

    def test_snowpark_connect_from_issues_connect_csv(self, tmp_path):
        reports = tmp_path / "Reports"
        reports.mkdir()
        (reports / "IssuesConnect.csv").write_text(ISSUES_CSV_HEADER + ISSUES_CSV_ROW)
        wp = str(tmp_path)
        sma_api.initialize_database(wp)
        assert sma_api.get_metadata(wp, "conversion_type") == "snowpark_connect"

    def test_issues_csv_preferred_over_connect(self, tmp_path):
        """When both CSVs exist, Issues.csv wins (iteration order)."""
        reports = tmp_path / "Reports"
        reports.mkdir()
        (reports / "Issues.csv").write_text(ISSUES_CSV_HEADER + ISSUES_CSV_ROW)
        (reports / "IssuesConnect.csv").write_text(ISSUES_CSV_HEADER + ISSUES_CSV_ROW)
        wp = str(tmp_path)
        sma_api.initialize_database(wp)
        assert sma_api.get_metadata(wp, "conversion_type") == "snowpark_api"

    def test_metadata_persists_after_reinit(self, tmp_path):
        """Re-calling initialize_database doesn't overwrite metadata (DB already exists)."""
        reports = tmp_path / "Reports"
        reports.mkdir()
        (reports / "Issues.csv").write_text(ISSUES_CSV_HEADER + ISSUES_CSV_ROW)
        wp = str(tmp_path)
        sma_api.initialize_database(wp)
        # Manually change to verify it's not overwritten on re-init
        sma_api.set_metadata(wp, "conversion_type", "manual_override")
        sma_api.initialize_database(wp)  # Should skip init since table exists
        assert sma_api.get_metadata(wp, "conversion_type") == "manual_override"

    def test_scos_python_detected_from_ewi_codes(self, tmp_path):
        """Issues.csv with SPRKCNTPY* codes should be detected as snowpark_connect."""
        reports = tmp_path / "Reports"
        reports.mkdir()
        (reports / "Issues.csv").write_text(ISSUES_CSV_HEADER + ISSUES_CSV_ROW_SCOS_PY)
        wp = str(tmp_path)
        sma_api.initialize_database(wp)
        assert sma_api.get_metadata(wp, "conversion_type") == "snowpark_connect"

    def test_scos_scala_detected_from_ewi_codes(self, tmp_path):
        """Issues.csv with SPRKCNTSCL* codes should be detected as snowpark_connect."""
        reports = tmp_path / "Reports"
        reports.mkdir()
        (reports / "Issues.csv").write_text(ISSUES_CSV_HEADER + ISSUES_CSV_ROW_SCOS_SCL)
        wp = str(tmp_path)
        sma_api.initialize_database(wp)
        assert sma_api.get_metadata(wp, "conversion_type") == "snowpark_connect"
