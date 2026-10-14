"""Pytest configuration for Snowpark (migrated) tests.

Migrated tests preserve the original IO strategy: some inputs/outputs
come from Snowflake stages (files) and others from tables. This simulates
the real production scenario for the migrated workload.

Provides BaseMigratedWorkloadTest — inherits shared properties, fixtures,
and test methods from BaseWorkloadTest; adds Snowpark-specific read logic.

Shared Snowflake infrastructure (snowpark_session, test_stage, upload,
create/load tables, cleanup) lives in the root conftest.py.
"""

import pytest
try:
    from conftest import BaseWorkloadTest
except (ModuleNotFoundError, ImportError):
    import importlib.util
    from pathlib import Path

    _ROOT_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
    _spec = importlib.util.spec_from_file_location("_dvp_root_conftest", _ROOT_CONFTEST)
    _mod = importlib.util.module_from_spec(_spec)
    assert _spec and _spec.loader
    _spec.loader.exec_module(_mod)

    BaseWorkloadTest = _mod.BaseWorkloadTest


@pytest.fixture(scope="session")
def db_session(snowpark_session):
    """Generic session alias — Snowpark session is used for both infra and workload."""
    return snowpark_session


@pytest.fixture(scope="session")
def workload_session(db_session):
    """Alias used by BaseIOConfig test methods."""
    return db_session


class BaseMigratedWorkloadTest(BaseWorkloadTest):
    """Snowpark-specific base class for migrated workload tests.

    Inherits shared properties, run_workload fixture, and test methods
    from BaseWorkloadTest.  Only _read_output differs (Snowpark API).
    """

    def _read_output(self, session, name, test_stage=None):
        """Read a migrated output — from table or stage depending on type."""
        if name in self.output_table_names:
            return session.table(name)
        return session.read.option("PARSE_HEADER", True).csv(
            f"{test_stage}/{name}"
        )
