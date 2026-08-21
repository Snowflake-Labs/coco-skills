"""Tests for provisioning perf changes: P8 (tables in state), P12 (parallel
entrypoints), P15 (probe -> create_golden_schema db-exists threading), and
scos_runtime P8 (_list_seed_tables reads persisted table list).

All snowflake access is mocked; runs without snowflake-connector-python.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import mock

import pytest

_TESTS_DIR = Path(__file__).parent
_HARNESS_DIR = _TESTS_DIR.parent / "harness"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

# Stub snowflake.connector so `import snowflake.connector` resolves.
_SF_MOD = sys.modules.get("snowflake") or types.ModuleType("snowflake")
_SF_CONN_MOD = sys.modules.get("snowflake.connector") or types.ModuleType("snowflake.connector")
_SF_MOD.connector = _SF_CONN_MOD  # type: ignore[attr-defined]
sys.modules.setdefault("snowflake", _SF_MOD)
sys.modules.setdefault("snowflake.connector", _SF_CONN_MOD)

from runtimes import _scos_provision as prov  # noqa: E402
from runtimes import scos_runtime as scos_rt  # noqa: E402


class _FakeCursor:
    """Minimal Snowflake cursor: records SQL, answers the metadata queries."""
    def __init__(self, log):
        self._log = log

    def execute(self, sql):
        self._log.append(sql)
        return self

    def fetchone(self):
        return ("TESTROLE",)  # CURRENT_ROLE()

    def fetchall(self):
        last = self._log[-1].upper()
        if "SHOW DATABASES" in last:
            return [("SCOS_VALIDATION",)]  # db exists -> probe path
        return []  # SHOW TABLES -> empty schema

    def close(self):
        pass


class _FakeConn:
    def __init__(self, log):
        self._log = log

    def cursor(self):
        return _FakeCursor(self._log)

    def close(self):
        pass


def test_privilege_probe_returns_true_and_create_skips_db_check():
    """P15: probe returns True; _create_golden_schema with database_exists=True
    issues neither SHOW DATABASES nor CREATE DATABASE."""
    log: list = []
    cur = _FakeCursor(log)
    assert prov._privilege_probe(cur) is True

    log2: list = []
    prov._create_golden_schema(_FakeCursor(log2), "MY_SCHEMA", database_exists=True)
    joined = " ".join(log2).upper()
    assert "SHOW DATABASES" not in joined
    assert "CREATE DATABASE" not in joined
    assert any("CREATE SCHEMA IF NOT EXISTS" in s.upper() for s in log2)
    assert any("CREATE STAGE IF NOT EXISTS" in s.upper() for s in log2)


def test_provision_golden_schemas_parallel_returns_tables(tmp_path, monkeypatch):
    """P12 + P8: all entrypoints provision (each on its own connection) and every
    returned ep_info carries a `tables` list."""
    workspace = tmp_path / "ws"
    mock_data_root = workspace / "shared" / "mock_data"
    mock_data_root.mkdir(parents=True)

    connect_calls = {"n": 0}
    shared_log: list = []

    def _fake_connect(**kwargs):
        connect_calls["n"] += 1
        return _FakeConn(shared_log)

    fake_connector = types.SimpleNamespace(connect=_fake_connect)
    monkeypatch.setattr(prov, "_get_connector", lambda: fake_connector)

    entrypoints = [{"id": f"ep{i}", "tables": {}} for i in range(3)]
    probe_conn = _FakeConn(shared_log)

    result = prov.provision_golden_schemas(
        probe_conn, {"connection_name": "c"}, entrypoints,
        mock_data_root, "proj", "run1", "SCOS_VALIDATION",
    )

    assert set(result) == {"ep0", "ep1", "ep2"}
    for ep_id, info in result.items():
        assert "tables" in info and isinstance(info["tables"], list)
        assert info["schema"].endswith("_GOLDEN")
    # One connection opened per entrypoint (probe used the passed-in conn).
    assert connect_calls["n"] == 3


def test_list_seed_tables_prefers_persisted_state_no_connection(monkeypatch):
    """scos_runtime P8: when state carries the golden table list, no Snowflake
    connection is opened."""
    def _boom(**kwargs):
        raise AssertionError("should not open a connection when state has tables")
    monkeypatch.setattr(scos_rt, "_list_seed_tables", scos_rt._list_seed_tables)  # ensure real fn
    import snowflake.connector as _c
    monkeypatch.setattr(_c, "connect", _boom, raising=False)

    state = {
        "snowflake": {"database": "DB", "golden_schemas": {"ep0": {"tables": ["FOO", "Bar"]}}},
        "config": {"connection_name": "c"},
    }
    out = scos_rt._list_seed_tables(state, "CLONE_SCHEMA", "ep0")
    assert out == ["clone_schema.foo", "clone_schema.bar"]


def test_list_seed_tables_falls_back_to_show_tables_without_persisted(monkeypatch):
    """Older state (no `tables`) still works via a live SHOW TABLES."""
    log: list = []

    class _C:
        def cursor(self):
            return _FakeCursor(log)
        def close(self):
            pass

    import snowflake.connector as _c
    monkeypatch.setattr(_c, "connect", lambda **k: _C(), raising=False)
    state = {
        "snowflake": {"database": "DB", "golden_schemas": {}},
        "config": {"connection_name": "c"},
    }
    out = scos_rt._list_seed_tables(state, "CLONE_SCHEMA", "ep0")
    assert out == []  # fake SHOW TABLES returns no rows
    assert any("SHOW TABLES" in s.upper() for s in log)


def test_provision_parallel_aggregates_all_failures(tmp_path, monkeypatch):
    """P12: when several entrypoints fail concurrently, every failure is reported
    (not just the first future to raise)."""
    workspace = tmp_path / "ws"
    (workspace / "shared" / "mock_data").mkdir(parents=True)
    mock_data_root = workspace / "shared" / "mock_data"

    monkeypatch.setattr(prov, "_get_connector",
                        lambda: types.SimpleNamespace(connect=lambda **k: _FakeConn([])))

    def _fake_provision(cur, conn_params, ep, *a, **k):
        if ep["id"] in ("ep1", "ep2"):
            raise RuntimeError(f"boom-{ep['id']}")
        return {"schema": "S", "stage": "st", "stage_prefix": "r", "tables": []}
    monkeypatch.setattr(prov, "_provision_entrypoint", _fake_provision)

    entrypoints = [{"id": f"ep{i}", "tables": {}} for i in range(3)]
    with pytest.raises(RuntimeError) as ei:
        prov.provision_golden_schemas(
            _FakeConn([]), {"connection_name": "c"}, entrypoints,
            mock_data_root, "proj", "run1", "SCOS_VALIDATION",
        )
    msg = str(ei.value)
    assert "ep1" in msg and "ep2" in msg  # both concurrent failures surfaced
    assert "2 entrypoint(s)" in msg
