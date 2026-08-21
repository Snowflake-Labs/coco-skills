"""Tests for status write tools."""

import sma_api


# ---------------------------------------------------------------------------
# update_ewi_status
# ---------------------------------------------------------------------------

class TestUpdateEwiStatus:
    def test_updates_all_occurrences(self, seeded_db):
        result = sma_api.update_ewi_status(seeded_db, "SPRKPY1001", "auto_resolved")
        assert result["success"] is True
        assert result["rows_updated"] == 3  # 3 occurrences

    def test_with_notes(self, seeded_db):
        result = sma_api.update_ewi_status(
            seeded_db, "SPRKPY1001", "manual_resolved", notes="Fixed manually"
        )
        assert result["success"] is True
        assert result["notes"] == "Fixed manually"

    def test_invalid_status(self, seeded_db):
        result = sma_api.update_ewi_status(seeded_db, "SPRKPY1001", "bad_status")
        assert "error" in result
        assert "Invalid status" in result["error"]

    def test_nonexistent_code(self, seeded_db):
        result = sma_api.update_ewi_status(seeded_db, "NONEXIST", "pending")
        assert "error" in result


# ---------------------------------------------------------------------------
# update_file_status
# ---------------------------------------------------------------------------

class TestUpdateFileStatus:
    def test_updates_file_ewis(self, seeded_db):
        result = sma_api.update_file_status(seeded_db, "src/etl/pipeline.py", "manual_resolved")
        assert result["success"] is True
        assert result["rows_updated"] == 4  # 4 EWIs in pipeline.py

    def test_invalid_status(self, seeded_db):
        result = sma_api.update_file_status(seeded_db, "src/etl/pipeline.py", "nope")
        assert "error" in result

    def test_nonexistent_file(self, seeded_db):
        result = sma_api.update_file_status(seeded_db, "nope.py", "pending")
        assert "error" in result


# ---------------------------------------------------------------------------
# update_line_status
# ---------------------------------------------------------------------------

class TestUpdateLineStatus:
    def test_updates_specific_line(self, seeded_db):
        result = sma_api.update_line_status(
            seeded_db, "src/etl/pipeline.py", "SPRKPY1001", 10, "auto_resolved"
        )
        assert result["success"] is True
        assert result["rows_updated"] == 1

    def test_invalid_status(self, seeded_db):
        result = sma_api.update_line_status(
            seeded_db, "src/etl/pipeline.py", "SPRKPY1001", 10, "xyz"
        )
        assert "error" in result

    def test_nonexistent_line(self, seeded_db):
        result = sma_api.update_line_status(
            seeded_db, "src/etl/pipeline.py", "SPRKPY1001", 9999, "pending"
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# bulk_update_ewi_status
# ---------------------------------------------------------------------------

class TestBulkUpdateEwiStatus:
    def test_updates_multiple_codes(self, seeded_db):
        result = sma_api.bulk_update_ewi_status(
            seeded_db, ["SPRKPY1001", "SPRKPY1038"], "wont_fix"
        )
        assert result["success"] is True
        assert result["total_rows_updated"] == 4  # 3 + 1
        assert len(result["details"]) == 2

    def test_invalid_status(self, seeded_db):
        result = sma_api.bulk_update_ewi_status(seeded_db, ["SPRKPY1001"], "invalid")
        assert "error" in result

    def test_mixed_existing_nonexisting(self, seeded_db):
        result = sma_api.bulk_update_ewi_status(
            seeded_db, ["SPRKPY1001", "NONEXIST"], "auto_resolved"
        )
        assert result["success"] is True
        details = {d["code"]: d["rows_updated"] for d in result["details"]}
        assert details["SPRKPY1001"] == 3
        assert details["NONEXIST"] == 0


# ---------------------------------------------------------------------------
# update_ewi_notes
# ---------------------------------------------------------------------------

class TestUpdateEwiNotes:
    def test_updates_notes(self, seeded_db):
        result = sma_api.update_ewi_notes(seeded_db, "SPRKPY1001", "Some notes")
        assert result["success"] is True
        assert result["rows_updated"] == 3

    def test_nonexistent_code(self, seeded_db):
        result = sma_api.update_ewi_notes(seeded_db, "NONEXIST", "notes")
        assert "error" in result


# ---------------------------------------------------------------------------
# update_ewi_status_single
# ---------------------------------------------------------------------------

class TestUpdateEwiStatusSingle:
    def test_updates_single_occurrence(self, seeded_db):
        result = sma_api.update_ewi_status_single(
            seeded_db, "SPRKPY1001", "src/etl/pipeline.py", 10, "auto_resolved", "Fixed by tool"
        )
        assert result["success"] is True
        assert result["rows_updated"] == 1

    def test_invalid_status(self, seeded_db):
        result = sma_api.update_ewi_status_single(
            seeded_db, "SPRKPY1001", "src/etl/pipeline.py", 10, "bad", "notes"
        )
        assert "error" in result

    def test_no_match(self, seeded_db):
        result = sma_api.update_ewi_status_single(
            seeded_db, "SPRKPY1001", "no.py", 999, "pending", ""
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# update_dependency_status
# ---------------------------------------------------------------------------

class TestUpdateDependencyStatus:
    def test_updates_and_recalculates(self, seeded_db_with_deps):
        result = sma_api.update_dependency_status(
            seeded_db_with_deps, "src/etl/pipeline.py", "src/etl/loader.py", "manual_resolved"
        )
        assert result["success"] is True
        # file_validated should be recalculated
        assert "file_validated" in result

    def test_invalid_status(self, seeded_db_with_deps):
        result = sma_api.update_dependency_status(
            seeded_db_with_deps, "src/etl/pipeline.py", "src/etl/loader.py", "bad"
        )
        assert "error" in result

    def test_all_resolved_sets_validated_2(self, seeded_db_with_deps):
        """When all deps are resolved, file_validated should be 2."""
        # Resolve all deps for pipeline.py
        sma_api.update_dependency_status(
            seeded_db_with_deps, "src/etl/pipeline.py", "src/etl/loader.py", "auto_resolved"
        )
        result = sma_api.update_dependency_status(
            seeded_db_with_deps, "src/etl/pipeline.py", "pandas", "wont_fix"
        )
        assert result["file_validated"] == 2


# ---------------------------------------------------------------------------
# update_file_validation
# ---------------------------------------------------------------------------

class TestUpdateFileValidation:
    def test_updates_validation(self, seeded_db_with_deps):
        result = sma_api.update_file_validation(seeded_db_with_deps, "src/etl/pipeline.py", 2)
        assert result["success"] is True


# ---------------------------------------------------------------------------
# update_recommended_actions
# ---------------------------------------------------------------------------

class TestUpdateRecommendedActions:
    def test_updates_actions(self, seeded_db_with_deps):
        result = sma_api.update_recommended_actions(
            seeded_db_with_deps, "src/etl/pipeline.py", "Migrate manually"
        )
        assert result["success"] is True
