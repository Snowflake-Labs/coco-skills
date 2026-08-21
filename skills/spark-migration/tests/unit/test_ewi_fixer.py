"""Tests for EWI fixer tools."""

import sma_api


# ---------------------------------------------------------------------------
# generate_fix_id
# ---------------------------------------------------------------------------

class TestGenerateFixId:
    def test_returns_uuid(self, workload_path):
        result = sma_api.generate_fix_id(workload_path)
        assert "fix_id" in result
        # UUID v4 format: 8-4-4-4-12
        parts = result["fix_id"].split("-")
        assert len(parts) == 5

    def test_successive_calls_differ(self, workload_path):
        r1 = sma_api.generate_fix_id(workload_path)
        r2 = sma_api.generate_fix_id(workload_path)
        assert r1["fix_id"] != r2["fix_id"]


# ---------------------------------------------------------------------------
# insert_fix_result
# ---------------------------------------------------------------------------

class TestInsertFixResult:
    def test_inserts_row(self, seeded_db):
        sma_api.generate_fix_id(seeded_db)
        result = sma_api.insert_fix_result(
            seeded_db, "SPRKPY1001", "Replaced API call", "src/etl/pipeline.py", "10,25", "success"
        )
        assert result["success"] is True
        assert result["fix_id"]

    def test_without_prior_fix_id(self, seeded_db):
        """If no fix_id set, it auto-generates one."""
        sma_api._fix_id = None
        result = sma_api.insert_fix_result(
            seeded_db, "SPRKPY1001", "desc", "file.py", "1", "failed"
        )
        assert result["success"] is True
        assert result["fix_id"]


# ---------------------------------------------------------------------------
# batch_insert_fix_results
# ---------------------------------------------------------------------------

class TestBatchInsertFixResults:
    def test_inserts_multiple(self, seeded_db):
        sma_api.generate_fix_id(seeded_db)
        results = [
            {"ewi_code": "SPRKPY1001", "fix_description": "Fix 1",
             "affected_file": "a.py", "affected_lines": "1", "status": "success"},
            {"ewi_code": "SPRKPY1038", "fix_description": "Fix 2",
             "affected_file": "b.py", "affected_lines": "2,3", "status": "failed"},
        ]
        result = sma_api.batch_insert_fix_results(seeded_db, results)
        assert result["success"] is True
        assert result["inserted"] == 2

    def test_empty_list(self, seeded_db):
        result = sma_api.batch_insert_fix_results(seeded_db, [])
        assert result["success"] is True
        assert result["inserted"] == 0


# ---------------------------------------------------------------------------
# get_fix_results
# ---------------------------------------------------------------------------

class TestGetFixResults:
    def test_returns_inserted_results(self, seeded_db):
        fix = sma_api.generate_fix_id(seeded_db)
        sma_api.insert_fix_result(
            seeded_db, "SPRKPY1001", "desc", "f.py", "1", "success"
        )
        results = sma_api.get_fix_results(seeded_db, fix_id=fix["fix_id"])
        assert len(results) == 1
        assert results[0]["ewi_code"] == "SPRKPY1001"

    def test_filter_by_fix_id(self, seeded_db):
        fix1 = sma_api.generate_fix_id(seeded_db)
        sma_api.insert_fix_result(seeded_db, "A", "d1", "f.py", "1", "success")
        fix2 = sma_api.generate_fix_id(seeded_db)
        sma_api.insert_fix_result(seeded_db, "B", "d2", "g.py", "2", "failed")
        r1 = sma_api.get_fix_results(seeded_db, fix_id=fix1["fix_id"])
        r2 = sma_api.get_fix_results(seeded_db, fix_id=fix2["fix_id"])
        assert len(r1) == 1
        assert r1[0]["ewi_code"] == "A"
        assert len(r2) == 1
        assert r2[0]["ewi_code"] == "B"


# ---------------------------------------------------------------------------
# get_fix_results_stats
# ---------------------------------------------------------------------------

class TestGetFixResultsStats:
    def test_counts(self, seeded_db):
        sma_api.generate_fix_id(seeded_db)
        sma_api.insert_fix_result(seeded_db, "A", "d", "f.py", "1", "success")
        sma_api.insert_fix_result(seeded_db, "B", "d", "g.py", "2", "success")
        sma_api.insert_fix_result(seeded_db, "C", "d", "h.py", "3", "failed")
        stats = sma_api.get_fix_results_stats(seeded_db)
        assert stats["success"] == 2
        assert stats["failed"] == 1
        assert stats["total"] == 3


# ---------------------------------------------------------------------------
# insert_summary_start
# ---------------------------------------------------------------------------

class TestInsertSummaryStart:
    def test_creates_summary_record(self, seeded_db):
        sma_api._fix_id = None
        result = sma_api.insert_summary_start(seeded_db)
        assert result["success"] is True
        assert result["fix_id"]
        # Verify record exists
        summary = sma_api.get_fix_summary(seeded_db, fix_id=result["fix_id"])
        assert summary["fix_id"] == result["fix_id"]


# ---------------------------------------------------------------------------
# update_summary_end
# ---------------------------------------------------------------------------

class TestUpdateSummaryEnd:
    def test_updates_summary(self, seeded_db):
        start = sma_api.insert_summary_start(seeded_db)
        result = sma_api.update_summary_end(
            seeded_db,
            total_ewis=10, auto_resolved_ewis=7,
            not_auto_resolved_ewis=3, total_files_fixed=5,
            total_not_auto_resolved_files=2, compilation_errors_fixed=1,
        )
        assert result["success"] is True
        summary = sma_api.get_fix_summary(seeded_db, fix_id=start["fix_id"])
        assert summary["total_ewis"] == 10
        assert summary["auto_resolved_ewis"] == 7
        assert summary["compilation_errors_fixed"] == 1
        assert summary["end_time"] is not None

    def test_no_active_session(self, seeded_db):
        sma_api._fix_id = None
        result = sma_api.update_summary_end(
            seeded_db, total_ewis=0, auto_resolved_ewis=0,
            not_auto_resolved_ewis=0, total_files_fixed=0,
            total_not_auto_resolved_files=0,
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# get_fix_summary
# ---------------------------------------------------------------------------

class TestGetFixSummary:
    def test_returns_summary(self, seeded_db):
        start = sma_api.insert_summary_start(seeded_db)
        result = sma_api.get_fix_summary(seeded_db, fix_id=start["fix_id"])
        assert result["fix_id"] == start["fix_id"]

    def test_nonexistent_fix_id(self, seeded_db):
        result = sma_api.get_fix_summary(seeded_db, fix_id="nonexistent-id")
        assert "error" in result

    def test_no_fix_id_and_no_session(self, seeded_db):
        sma_api._fix_id = None
        result = sma_api.get_fix_summary(seeded_db)
        assert "error" in result
