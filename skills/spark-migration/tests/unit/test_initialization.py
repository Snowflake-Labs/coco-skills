"""Tests for initialization tools: initialize_database, create_artifact_dependency_tables, create_input_files_table."""

import os
import sqlite3

import pytest

import sma_api


# ---------------------------------------------------------------------------
# initialize_database
# ---------------------------------------------------------------------------

class TestInitializeDatabase:
    def test_creates_db_from_csv(self, workload_with_csv):
        result = sma_api.initialize_database(workload_with_csv)
        assert result["success"] is True
        assert result["initialized_from_csv"] is True
        assert result["fix_id"]
        db = os.path.join(workload_with_csv, "sma_storage.sqlite3")
        assert os.path.exists(db)

    def test_issues_table_has_rows(self, seeded_db):
        db_path = sma_api._db_from_workload(seeded_db)
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM Issues").fetchone()[0]
        conn.close()
        assert count == 5  # 5 rows in ISSUES_CSV fixture

    def test_issues_table_has_status_column(self, seeded_db):
        db_path = sma_api._db_from_workload(seeded_db)
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(Issues)").fetchall()}
        conn.close()
        assert "status" in cols
        assert "notes" in cols

    def test_ewi_fixer_tables_created(self, seeded_db):
        db_path = sma_api._db_from_workload(seeded_db)
        conn = sqlite3.connect(db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "ewi_fixer_results" in tables
        assert "ewi_fixer_summary" in tables

    def test_idempotent_second_call(self, seeded_db):
        result = sma_api.initialize_database(seeded_db)
        assert result["success"] is True
        assert result["initialized_from_csv"] is False  # already exists

    def test_missing_csv_returns_error(self, workload_path):
        """No Reports/Issues.csv → error dict."""
        result = sma_api.initialize_database(workload_path)
        assert "error" in result

    def test_fix_id_inserted_into_summary(self, seeded_db):
        db_path = sma_api._db_from_workload(seeded_db)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM ewi_fixer_summary").fetchall()
        conn.close()
        assert len(rows) >= 1
        assert rows[0]["fix_id"] is not None

    def test_file_paths_normalized(self, tmp_path):
        """FileId paths with backslashes get normalized to forward slashes."""
        reports = tmp_path / "Reports"
        reports.mkdir()
        csv_content = "Code,Description,Category,FileId,Line,Column,Url\nX,d,c,\\src\\file.py,1,1,\n"
        (reports / "Issues.csv").write_text(csv_content, encoding="utf-8")
        result = sma_api.initialize_database(str(tmp_path))
        assert result["success"] is True
        db_path = sma_api._db_from_workload(str(tmp_path))
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT FileId FROM Issues").fetchone()
        conn.close()
        assert "\\" not in row[0]


# ---------------------------------------------------------------------------
# create_artifact_dependency_tables
# ---------------------------------------------------------------------------

class TestCreateArtifactDependencyTables:
    def test_creates_tables(self, seeded_db, dependency_csv):
        result = sma_api.create_artifact_dependency_tables(seeded_db, dependency_csv)
        assert result["success"] is True
        assert result["summary_rows"] > 0
        assert result["islands"] >= 1

    def test_inventory_rows_imported(self, seeded_db, dependency_csv):
        sma_api.create_artifact_dependency_tables(seeded_db, dependency_csv)
        db_path = sma_api._db_from_workload(seeded_db)
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM artifact_dependency_inventory").fetchone()[0]
        conn.close()
        assert count == 4  # 4 rows in DEPENDENCY_CSV

    def test_graph_table_created(self, seeded_db, dependency_csv):
        sma_api.create_artifact_dependency_tables(seeded_db, dependency_csv)
        db_path = sma_api._db_from_workload(seeded_db)
        conn = sqlite3.connect(db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "artifact_dependency_graph" in tables

    def test_summary_has_recommended_actions(self, seeded_db, dependency_csv):
        sma_api.create_artifact_dependency_tables(seeded_db, dependency_csv)
        db_path = sma_api._db_from_workload(seeded_db)
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT file_id, recommended_actions FROM artifact_dependency_summary"
        ).fetchall()
        conn.close()
        # loader.py has unknown libraries, so it should have recommended actions
        loader_rows = [r for r in rows if "loader" in r[0]]
        assert len(loader_rows) > 0
        assert loader_rows[0][1]  # non-empty

    def test_idempotent_refreshes_summary(self, seeded_db, dependency_csv):
        """Second call skips inventory import but refreshes summary."""
        sma_api.create_artifact_dependency_tables(seeded_db, dependency_csv)
        result = sma_api.create_artifact_dependency_tables(seeded_db, dependency_csv)
        assert result["success"] is True

    def test_empty_csv_returns_error(self, seeded_db, tmp_path):
        empty_csv = tmp_path / "empty.csv"
        empty_csv.write_text("ExecutionId,FileId,Dependency,Type,Success,StatusDetail,Arguments,Location,IndirectDependencies,TotalIndirectDependencies,DirectParents,TotalDirectParents,IndirectParents,TotalIndirectParents\n")
        result = sma_api.create_artifact_dependency_tables(seeded_db, str(empty_csv))
        assert "error" in result


# ---------------------------------------------------------------------------
# create_input_files_table
# ---------------------------------------------------------------------------

class TestCreateInputFilesTable:
    def test_creates_and_imports(self, seeded_db, input_files_csv):
        result = sma_api.create_input_files_table(seeded_db, input_files_csv)
        assert result["success"] is True
        assert result["rows_imported"] == 3

    def test_skips_if_exists(self, seeded_db, input_files_csv):
        sma_api.create_input_files_table(seeded_db, input_files_csv)
        result = sma_api.create_input_files_table(seeded_db, input_files_csv)
        assert result["skipped"] is True
        assert result["existing_rows"] == 3

    def test_empty_csv_returns_error(self, seeded_db, tmp_path):
        empty_csv = tmp_path / "empty.csv"
        empty_csv.write_text("Element,ProjectId,FileId,Count,SessionId,Extension,Technology,Bytes,CharacterLength,LinesOfCode,ParseResult,Ignored,OriginFilePath\n")
        result = sma_api.create_input_files_table(seeded_db, str(empty_csv))
        assert "error" in result
