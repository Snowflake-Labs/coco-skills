"""Phase 1 — Setup financial services source data in Snowflake."""
import pytest

pytestmark = [pytest.mark.setup, pytest.mark.financial]


class TestSetupFinancial:
    def test_load_fixture(self, load_fixture):
        load_fixture("financial_data.sql")

    def test_source_table_exists(self, sf_setup):
        sf_setup.assert_table_exists("FINANCIAL_SOURCE")

    def test_source_row_count(self, sf_setup):
        sf_setup.assert_row_count_between("FINANCIAL_SOURCE", 12, 12)

    def test_source_columns(self, sf_setup):
        sf_setup.assert_columns_include("FINANCIAL_SOURCE", {
            "SOURCE_ID", "SOURCE_TABLE", "RAW_NAME", "RAW_ADDRESS",
            "RAW_LEI", "RAW_DUNS", "RAW_TAX_ID", "RAW_CRD", "RAW_SWIFT",
        })
