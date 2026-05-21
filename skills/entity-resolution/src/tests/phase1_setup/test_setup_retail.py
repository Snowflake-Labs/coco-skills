"""Phase 1 — Setup retail/CPG source data in Snowflake."""
import pytest

pytestmark = [pytest.mark.setup, pytest.mark.retail]


class TestSetupRetail:
    def test_load_fixture(self, load_fixture):
        load_fixture("retail_data.sql")

    def test_source_table_exists(self, sf_setup):
        sf_setup.assert_table_exists("RETAIL_SOURCE")

    def test_source_row_count(self, sf_setup):
        sf_setup.assert_row_count_between("RETAIL_SOURCE", 12, 12)

    def test_source_columns(self, sf_setup):
        sf_setup.assert_columns_include("RETAIL_SOURCE", {
            "SOURCE_ID", "SOURCE_TABLE", "RAW_NAME", "RAW_ADDRESS",
            "RAW_GTIN", "RAW_GLN", "RAW_SUPPLIER_ID", "RAW_DUNS", "ENTITY_TYPE",
        })
