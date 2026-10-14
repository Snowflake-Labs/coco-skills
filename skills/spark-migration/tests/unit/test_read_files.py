"""Tests for file read tools: list_files, get_file_details, get_ewi_descriptions."""

import sma_api


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------

class TestListFiles:
    def test_returns_files(self, seeded_db):
        result = sma_api.list_files(seeded_db)
        paths = {f["file_path"] for f in result}
        assert "src/etl/pipeline.py" in paths
        assert "src/etl/loader.py" in paths

    def test_file_ewi_counts(self, seeded_db):
        result = sma_api.list_files(seeded_db)
        by_path = {f["file_path"]: f for f in result}
        # pipeline.py has SPRKPY1001, SPRKPY1038, SPRKPY2000 -> 3 unique codes
        assert by_path["src/etl/pipeline.py"]["total_ewis"] == 3
        # loader.py has SPRKPY1001 -> 1 unique code
        assert by_path["src/etl/loader.py"]["total_ewis"] == 1

    def test_filter_by_status(self, seeded_db):
        # All files are pending at start
        result = sma_api.list_files(seeded_db, status="pending")
        assert len(result) == 2
        result = sma_api.list_files(seeded_db, status="auto_resolved")
        assert len(result) == 0

    def test_limit(self, seeded_db):
        result = sma_api.list_files(seeded_db, limit=1)
        assert len(result) == 1

    def test_ewi_codes_listed(self, seeded_db):
        result = sma_api.list_files(seeded_db)
        by_path = {f["file_path"]: f for f in result}
        codes = by_path["src/etl/pipeline.py"]["ewi_codes"]
        assert "SPRKPY1001" in codes
        assert "SPRKPY1038" in codes
        assert "SPRKPY2000" in codes


# ---------------------------------------------------------------------------
# get_file_details
# ---------------------------------------------------------------------------

class TestGetFileDetails:
    def test_found(self, seeded_db):
        result = sma_api.get_file_details(seeded_db, "src/etl/pipeline.py")
        assert result["file_path"] == "src/etl/pipeline.py"
        assert "ewis" in result
        assert result["total_ewis"] == 3

    def test_not_found(self, seeded_db):
        result = sma_api.get_file_details(seeded_db, "nonexistent.py")
        assert "error" in result

    def test_backslash_path_normalized(self, seeded_db):
        result = sma_api.get_file_details(seeded_db, "src\\etl\\pipeline.py")
        assert result["file_path"] == "src/etl/pipeline.py"


# ---------------------------------------------------------------------------
# get_ewi_descriptions
# ---------------------------------------------------------------------------

class TestGetEwiDescriptions:
    def test_returns_mapping(self, seeded_db):
        result = sma_api.get_ewi_descriptions(seeded_db)
        assert isinstance(result, dict)
        assert "SPRKPY1001" in result
        assert result["SPRKPY1001"] == "Unsupported API call"
        assert "SPRKPY2000" in result
