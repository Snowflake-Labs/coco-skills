"""Phase 1 — Setup generic (name+address) source data in Snowflake."""
import pytest

pytestmark = [pytest.mark.setup, pytest.mark.generic]


class TestSetupGeneric:
    def test_load_fixture(self, load_fixture):
        load_fixture("generic_data.sql")

    def test_source_table_exists(self, sf_setup):
        sf_setup.assert_table_exists("GENERIC_SOURCE")

    def test_source_row_count(self, sf_setup):
        sf_setup.assert_row_count_between("GENERIC_SOURCE", 10, 10)

    def test_source_columns(self, sf_setup):
        sf_setup.assert_columns_include("GENERIC_SOURCE", {
            "SOURCE_ID", "SOURCE_TABLE", "RAW_NAME", "RAW_ADDRESS",
            "RAW_PHONE", "RAW_EMAIL",
        })
