"""Tests for reset and overview tools."""

import json
import os
import sqlite3

import sma_api


# ---------------------------------------------------------------------------
# reset_not_resolved_to_pending
# ---------------------------------------------------------------------------

class TestResetNotResolvedToPending:
    def test_resets_not_auto_resolved(self, seeded_db):
        # Set some to not_auto_resolved
        sma_api.update_ewi_status(seeded_db, "SPRKPY1001", "not_auto_resolved")
        stats = sma_api.get_summary_stats(seeded_db)
        assert stats["not_auto_resolved"] == 3

        result = sma_api.reset_not_resolved_to_pending(seeded_db)
        assert result["success"] is True
        assert result["reset_count"] == 3

        stats = sma_api.get_summary_stats(seeded_db)
        assert stats["not_auto_resolved"] == 0
        assert stats["pending"] == 5

    def test_does_not_touch_other_statuses(self, seeded_db):
        sma_api.update_ewi_status(seeded_db, "SPRKPY1001", "auto_resolved")
        sma_api.update_ewi_status(seeded_db, "SPRKPY2000", "not_auto_resolved")

        sma_api.reset_not_resolved_to_pending(seeded_db)
        stats = sma_api.get_summary_stats(seeded_db)
        assert stats["auto_resolved"] == 3  # untouched
        assert stats["pending"] == 2  # 1 reset + 1 original SPRKPY1038


# ---------------------------------------------------------------------------
# reset_all_to_pending
# ---------------------------------------------------------------------------

class TestResetAllToPending:
    def test_resets_everything(self, seeded_db):
        sma_api.update_ewi_status(seeded_db, "SPRKPY1001", "auto_resolved")
        sma_api.update_ewi_status(seeded_db, "SPRKPY1038", "manual_resolved")
        sma_api.update_ewi_status(seeded_db, "SPRKPY2000", "wont_fix")

        result = sma_api.reset_all_to_pending(seeded_db)
        assert result["success"] is True
        assert result["reset_count"] == 5  # all 5 rows

        stats = sma_api.get_summary_stats(seeded_db)
        assert stats["pending"] == 5
        assert stats["auto_resolved"] == 0
        assert stats["manual_resolved"] == 0

    def test_no_op_when_all_pending(self, seeded_db):
        result = sma_api.reset_all_to_pending(seeded_db)
        assert result["reset_count"] == 0


# ---------------------------------------------------------------------------
# save_overview_stats
# ---------------------------------------------------------------------------

class TestSaveOverviewStats:
    def test_creates_and_saves(self, seeded_db):
        overview = {
            "total_files": 10,
            "total_ewis": 25,
            "ewi_occurrences": 100,
            "total_islands": 3,
            "island_files": 8,
            "ready_files": 5,
            "migration_readiness": {"ready": 5, "review": 3, "blocked": 2},
            "file_complexity": {"simple": 4, "moderate": 3, "complex": 3},
            "blockers": [{"code": "SPRKPY1001", "count": 5}],
            "ewi_categories": {"Error": 15, "Warning": 10},
            "file_types": {".py": 8, ".sql": 2},
            "readiness_files": {"ready": ["a.py"], "blocked": ["b.py"]},
        }
        result = sma_api.save_overview_stats(seeded_db, overview)
        assert result["success"] is True

        # Verify persisted
        db_path = sma_api._db_from_workload(seeded_db)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM overview_stats").fetchone()
        conn.close()
        assert row is not None
        assert row["total_files"] == 10
        assert row["readiness_ready"] == 5
        assert row["readiness_blocked"] == 2
        blockers = json.loads(row["blockers_json"])
        assert len(blockers) == 1

    def test_replaces_previous(self, seeded_db):
        """Second call replaces the first (only 1 row kept)."""
        sma_api.save_overview_stats(seeded_db, {"total_files": 1})
        sma_api.save_overview_stats(seeded_db, {"total_files": 2})
        db_path = sma_api._db_from_workload(seeded_db)
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM overview_stats").fetchone()[0]
        row = conn.execute("SELECT total_files FROM overview_stats").fetchone()
        conn.close()
        assert count == 1
        assert row[0] == 2
