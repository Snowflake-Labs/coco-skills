"""Phase 1 — Setup healthcare provider source data in Snowflake."""
import pytest

pytestmark = [pytest.mark.setup, pytest.mark.healthcare]


class TestSetupHealthcare:
    def test_load_fixture(self, load_fixture):
        load_fixture("healthcare_data.sql")

    def test_source_table_exists(self, sf_setup):
        sf_setup.assert_table_exists("HEALTHCARE_SOURCE")

    def test_source_row_count(self, sf_setup):
        sf_setup.assert_row_count_between("HEALTHCARE_SOURCE", 12, 12)

    def test_source_columns(self, sf_setup):
        sf_setup.assert_columns_include("HEALTHCARE_SOURCE", {
            "SOURCE_ID", "SOURCE_TABLE", "RAW_NAME", "RAW_ADDRESS",
            "RAW_NPI", "NPI_TYPE", "RAW_TAXONOMY",
        })
