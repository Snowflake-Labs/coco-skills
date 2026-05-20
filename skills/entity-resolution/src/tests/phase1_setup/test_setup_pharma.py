"""Phase 1 — Setup pharma source data in Snowflake."""
import pytest

pytestmark = [pytest.mark.setup, pytest.mark.pharma]


class TestSetupPharma:
    def test_load_fixture(self, load_fixture):
        load_fixture("pharma_data.sql")

    def test_source_table_exists(self, sf_setup):
        sf_setup.assert_table_exists("PHARMA_SOURCE")

    def test_source_row_count(self, sf_setup):
        sf_setup.assert_row_count_between("PHARMA_SOURCE", 12, 12)

    def test_source_columns(self, sf_setup):
        sf_setup.assert_columns_include("PHARMA_SOURCE", {
            "SOURCE_ID", "SOURCE_TABLE", "RAW_NAME", "RAW_ADDRESS",
            "RAW_NPI", "RAW_DEA", "RAW_NCPDP",
        })
