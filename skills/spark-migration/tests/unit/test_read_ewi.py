"""Tests for EWI read tools."""

import sma_api
from sma_api import BLOCKER_EWI_CODES


# ---------------------------------------------------------------------------
# get_migration_summary
# ---------------------------------------------------------------------------

class TestGetMigrationSummary:
    def test_returns_correct_counts(self, seeded_db):
        result = sma_api.get_migration_summary(seeded_db)
        # 2 files in fixture: pipeline.py and loader.py
        assert result["total_files"] == 2
        # 3 unique EWI codes: SPRKPY1001, SPRKPY1038, SPRKPY2000
        assert result["total_ewis"] == 3
        # 5 total occurrences
        assert result["total_occurrences"] == 5

    def test_blockers_detected(self, seeded_db):
        result = sma_api.get_migration_summary(seeded_db)
        blocker_codes = {b["code"] for b in result["blockers"]}
        # SPRKPY1001 and SPRKPY1038 are in BLOCKER_EWI_CODES
        assert "SPRKPY1001" in blocker_codes
        assert "SPRKPY1038" in blocker_codes

    def test_migration_readiness_present(self, seeded_db):
        result = sma_api.get_migration_summary(seeded_db)
        mr = result["migration_readiness"]
        assert "ready" in mr
        assert "review" in mr
        assert "blocked" in mr
        # pipeline.py has blockers -> blocked; loader.py has blockers -> blocked
        assert mr["blocked"] == 2

    def test_status_summary_all_pending(self, seeded_db):
        result = sma_api.get_migration_summary(seeded_db)
        summary = result["status_summary"]
        assert summary["pending"] == 3  # 3 unique EWI codes, all pending

    def test_empty_db(self, workload_path):
        """An initialized DB with no issues returns zeros."""
        # Create an empty Issues table
        import sqlite3, os
        db_path = os.path.join(workload_path, "sma_storage.sqlite3")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE Issues (id INTEGER PRIMARY KEY, Code TEXT, Description TEXT, "
            "Category TEXT, FileId TEXT, Line TEXT, Column TEXT, Url TEXT, "
            "status TEXT DEFAULT 'pending', notes TEXT DEFAULT '')"
        )
        conn.commit()
        conn.close()
        result = sma_api.get_migration_summary(workload_path)
        assert result["total_files"] == 0
        assert result["total_ewis"] == 0


# ---------------------------------------------------------------------------
# list_ewis
# ---------------------------------------------------------------------------

class TestListEwis:
    def test_returns_all_ewis(self, seeded_db):
        result = sma_api.list_ewis(seeded_db)
        codes = {e["code"] for e in result}
        assert "SPRKPY1001" in codes
        assert "SPRKPY1038" in codes
        assert "SPRKPY2000" in codes

    def test_filter_by_category(self, seeded_db):
        result = sma_api.list_ewis(seeded_db, category="ConversionError")
        codes = {e["code"] for e in result}
        assert "SPRKPY2000" not in codes  # it's a Warning
        assert "SPRKPY1001" in codes

    def test_filter_by_status(self, seeded_db):
        # All start as pending
        result = sma_api.list_ewis(seeded_db, status="pending")
        assert len(result) == 3
        result = sma_api.list_ewis(seeded_db, status="auto_resolved")
        assert len(result) == 0

    def test_limit(self, seeded_db):
        result = sma_api.list_ewis(seeded_db, limit=1)
        assert len(result) == 1

    def test_is_blocker_flag(self, seeded_db):
        result = sma_api.list_ewis(seeded_db)
        for e in result:
            expected = e["code"] in BLOCKER_EWI_CODES
            assert e["is_blocker"] == expected


# ---------------------------------------------------------------------------
# get_blockers
# ---------------------------------------------------------------------------

class TestGetBlockers:
    def test_returns_only_blockers(self, seeded_db):
        result = sma_api.get_blockers(seeded_db)
        assert all(e["is_blocker"] for e in result)
        codes = {e["code"] for e in result}
        assert "SPRKPY2000" not in codes  # not a blocker

    def test_count(self, seeded_db):
        result = sma_api.get_blockers(seeded_db)
        assert len(result) == 2  # SPRKPY1001 and SPRKPY1038


# ---------------------------------------------------------------------------
# get_pending_ewi_codes
# ---------------------------------------------------------------------------

class TestGetPendingEwiCodes:
    def test_returns_pending_codes(self, seeded_db):
        result = sma_api.get_pending_ewi_codes(seeded_db)
        assert len(result) == 3  # all 3 codes are pending
        codes = {r.get("Code") for r in result}
        assert "SPRKPY1001" in codes

    def test_resolved_codes_excluded(self, seeded_db):
        sma_api.update_ewi_status(seeded_db, "SPRKPY2000", "auto_resolved")
        result = sma_api.get_pending_ewi_codes(seeded_db)
        codes = {r.get("Code") for r in result}
        assert "SPRKPY2000" not in codes


# ---------------------------------------------------------------------------
# get_ewis_by_code
# ---------------------------------------------------------------------------

class TestGetEwisByCode:
    def test_returns_occurrences(self, seeded_db):
        result = sma_api.get_ewis_by_code(seeded_db, "SPRKPY1001")
        assert len(result) == 3  # 3 occurrences in fixture

    def test_filters_by_status(self, seeded_db):
        sma_api.update_ewi_status(seeded_db, "SPRKPY1001", "auto_resolved")
        result = sma_api.get_ewis_by_code(seeded_db, "SPRKPY1001", status="auto_resolved")
        assert len(result) == 3
        result = sma_api.get_ewis_by_code(seeded_db, "SPRKPY1001", status="pending")
        assert len(result) == 0

    def test_nonexistent_code(self, seeded_db):
        result = sma_api.get_ewis_by_code(seeded_db, "NONEXISTENT")
        assert len(result) == 0


# ---------------------------------------------------------------------------
# get_ewis_by_file
# ---------------------------------------------------------------------------

class TestGetEwisByFile:
    def test_returns_file_ewis(self, seeded_db):
        result = sma_api.get_ewis_by_file(seeded_db, "src/etl/pipeline.py")
        assert len(result) == 4  # 2x SPRKPY1001 + 1x SPRKPY1038 + 1x SPRKPY2000

    def test_nonexistent_file(self, seeded_db):
        result = sma_api.get_ewis_by_file(seeded_db, "nonexistent.py")
        assert len(result) == 0


# ---------------------------------------------------------------------------
# get_summary_stats
# ---------------------------------------------------------------------------

class TestGetSummaryStats:
    def test_all_pending_initially(self, seeded_db):
        result = sma_api.get_summary_stats(seeded_db)
        assert result["pending"] == 5
        assert result["total"] == 5
        assert result["auto_resolved"] == 0

    def test_after_update(self, seeded_db):
        sma_api.update_ewi_status(seeded_db, "SPRKPY2000", "manual_resolved")
        result = sma_api.get_summary_stats(seeded_db)
        assert result["manual_resolved"] == 1
        assert result["pending"] == 4


# ---------------------------------------------------------------------------
# get_ewi_code_stats
# ---------------------------------------------------------------------------

class TestGetEwiCodeStats:
    def test_returns_per_code_stats(self, seeded_db):
        result = sma_api.get_ewi_code_stats(seeded_db)
        stats_map = {r["Code"]: r for r in result}
        assert stats_map["SPRKPY1001"]["total_count"] == 3
        assert stats_map["SPRKPY1001"]["pending_count"] == 3
        assert stats_map["SPRKPY2000"]["total_count"] == 1

    def test_after_resolve(self, seeded_db):
        sma_api.update_ewi_status(seeded_db, "SPRKPY1001", "auto_resolved")
        result = sma_api.get_ewi_code_stats(seeded_db)
        stats_map = {r["Code"]: r for r in result}
        assert stats_map["SPRKPY1001"]["auto_resolved_count"] == 3
        assert stats_map["SPRKPY1001"]["pending_count"] == 0
