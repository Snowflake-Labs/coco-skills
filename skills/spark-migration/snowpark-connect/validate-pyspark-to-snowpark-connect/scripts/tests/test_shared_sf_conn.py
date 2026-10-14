"""Tests for SharedSnowflakeConn and the ContextVar-based connection sharing.

All tests stub snowflake.connector at sys.modules level so they run without
the real snowflake-connector-python package installed.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_TESTS_DIR = Path(__file__).parent
_SCRIPTS_DIR = _TESTS_DIR.parent
_HARNESS_DIR = _SCRIPTS_DIR / "harness"

if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

# ---------------------------------------------------------------------------
# Stub snowflake.connector in sys.modules BEFORE any imports that trigger it.
# ---------------------------------------------------------------------------
_SF_MOD = sys.modules.get("snowflake") or types.ModuleType("snowflake")
_SF_CONN_MOD = sys.modules.get("snowflake.connector") or types.ModuleType("snowflake.connector")

_SF_CONN_MOD.connect = mock.MagicMock(  # type: ignore[attr-defined]
    side_effect=AssertionError("snowflake.connector.connect called unexpectedly")
)
_SF_MOD.connector = _SF_CONN_MOD  # type: ignore[attr-defined]

sys.modules.setdefault("snowflake", _SF_MOD)
sys.modules.setdefault("snowflake.connector", _SF_CONN_MOD)
sys.modules.setdefault("snowpark_connect", types.ModuleType("snowpark_connect"))
sys.modules.setdefault("snowflake.snowpark_connect", types.ModuleType("snowflake.snowpark_connect"))

# ---------------------------------------------------------------------------
# Now safe to import the primitives under test
# ---------------------------------------------------------------------------
from helpers import _SF_CONN_HOLDER, SharedSnowflakeConn, clone_golden_schema_for_trial  # type: ignore[import-not-found]  # noqa: E402
from runtimes.scos_runtime import ScosRuntime  # noqa: E402
from runtimes import _scos_provision as _scos_provision_mod  # noqa: E402
from runtimes.base import TrialRequest  # noqa: E402
from helpers import schema_hash, _bare_table_name  # type: ignore[import-not-found]  # noqa: E402


# ---------------------------------------------------------------------------
# Shared builder helpers (mirrors test_scos_provision_fast_path.py)
# ---------------------------------------------------------------------------

def _make_mock_data_dir(tmp_path: Path, ep_id: str) -> str:
    d = tmp_path / "shared" / "mock_data" / ep_id
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _make_request(tmp_path: Path, ep: dict, state: dict, trial_id: str = "trial_001") -> TrialRequest:
    return TrialRequest(
        trial_id=trial_id,
        flavor="scos",
        project_root=str(tmp_path),
        entrypoint_path="",
        ep_config=ep,
        mock_data_dir=_make_mock_data_dir(tmp_path, ep["id"]),
        results_dir=str(tmp_path / "results"),
        state_json=state,
        analysis={},
    )


def _read_table() -> dict:
    return {
        "access": "read",
        "columns": [{"name": "id", "type": "int"}, {"name": "name", "type": "string"}],
        "original_path": "db.schema.orders",
    }


def _table_key(tbl_name: str, tbl: dict) -> str:
    return _bare_table_name(tbl.get("original_path", "")) or tbl_name.lower()


def _golden_return(ep_id: str) -> dict:
    return {ep_id: {"schema": f"myproj_abc12345_golden_{ep_id}", "stage": ""}}


def _make_cursor_mock() -> mock.MagicMock:
    cur = mock.MagicMock()
    cur.execute = mock.MagicMock()
    cur.close = mock.MagicMock()
    return cur


# ---------------------------------------------------------------------------
# Case 1 — Reuse: acquire() twice returns the same connection; connect called once
# ---------------------------------------------------------------------------

def test_reuse_same_connection_name():
    holder = SharedSnowflakeConn()
    mock_conn = mock.MagicMock()

    with mock.patch.object(_SF_CONN_MOD, "connect", return_value=mock_conn) as mock_connect:
        result1 = holder.acquire("test_conn")
        result2 = holder.acquire("test_conn")

    assert mock_connect.call_count == 1
    assert result1 is mock_conn
    assert result2 is mock_conn


# ---------------------------------------------------------------------------
# Case 2 — Name-mismatch: second acquire with different name raises RuntimeError
# ---------------------------------------------------------------------------

def test_name_mismatch_raises():
    holder = SharedSnowflakeConn()
    mock_conn = mock.MagicMock()

    with mock.patch.object(_SF_CONN_MOD, "connect", return_value=mock_conn):
        holder.acquire("conn_a")
        with pytest.raises(RuntimeError, match="already opened for"):
            holder.acquire("conn_b")


# ---------------------------------------------------------------------------
# Case 3 — Provision + clone share ONE connection (fast-path does NOT fire)
# ---------------------------------------------------------------------------

def test_provision_and_clone_share_one_connection(tmp_path):
    ep_id = "ep_share"
    tbl = _read_table()
    ep = {"id": ep_id, "tables": {"orders": tbl}}
    # No golden_schemas → provision fast-path will NOT fire
    state: dict = {
        "config": {"connection_name": "test_conn", "project_slug": "myproj"},
        "run_id": "abc12345",
        "snowflake": {"database": "SCOS_VALIDATION"},
    }

    mock_conn = mock.MagicMock()
    mock_conn.cursor.return_value = _make_cursor_mock()

    holder = SharedSnowflakeConn()
    token = _SF_CONN_HOLDER.set(holder)
    try:
        with mock.patch.object(_SF_CONN_MOD, "connect", return_value=mock_conn) as mock_connect, \
             mock.patch.object(
                 _scos_provision_mod, "provision_golden_schemas",
                 return_value=_golden_return(ep_id),
             ):
            request = _make_request(tmp_path, ep, state)
            ScosRuntime().provision(request)

            # After provision(), state now has golden_schemas populated.
            # clone_golden_schema_for_trial should reuse the same connection.
            with clone_golden_schema_for_trial(state, ep_id):
                pass  # we only care that connect was called once

        # connector.connect must have been called exactly once across both calls.
        assert mock_connect.call_count == 1
    finally:
        _SF_CONN_HOLDER.reset(token)
        holder.close()


# ---------------------------------------------------------------------------
# Case 4 — Isolation: two separate contexts get independent holders
# ---------------------------------------------------------------------------

def test_isolation_two_contexts():
    conn_a = mock.MagicMock()
    conn_b = mock.MagicMock()

    with mock.patch.object(_SF_CONN_MOD, "connect", side_effect=[conn_a, conn_b]) as mock_connect:
        # Simulate test function A
        holder_a = SharedSnowflakeConn()
        token_a = _SF_CONN_HOLDER.set(holder_a)
        try:
            result_a = holder_a.acquire("conn_name")
            assert _SF_CONN_HOLDER.get() is holder_a
        finally:
            _SF_CONN_HOLDER.reset(token_a)

        # Simulate test function B (independent context)
        holder_b = SharedSnowflakeConn()
        token_b = _SF_CONN_HOLDER.set(holder_b)
        try:
            result_b = holder_b.acquire("conn_name")
            assert _SF_CONN_HOLDER.get() is holder_b
        finally:
            _SF_CONN_HOLDER.reset(token_b)

    # Each context opened its own connection; they do not cross-share.
    assert mock_connect.call_count == 2
    assert result_a is conn_a
    assert result_b is conn_b
    assert result_a is not result_b
    assert holder_a._conn is not holder_b._conn


# ---------------------------------------------------------------------------
# Case 5 — Fast-path short-circuits: acquire() is never called
# ---------------------------------------------------------------------------

def test_fast_path_does_not_call_acquire(tmp_path):
    import json

    ep_id = "ep_fast"
    tbl = _read_table()
    ep = {"id": ep_id, "tables": {"orders": tbl}}
    state: dict = {
        "config": {"connection_name": "test_conn", "project_slug": "myproj"},
        "run_id": "abc12345",
        "snowflake": {
            "database": "SCOS_VALIDATION",
            "golden_schemas": {ep_id: {"schema": f"myproj_abc12345_golden_{ep_id}"}},
        },
    }

    # Write provision_hashes.json with matching hash so fast-path fires.
    key = _table_key("orders", tbl)
    shared = tmp_path / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "provision_hashes.json").write_text(
        json.dumps({"scos": {ep_id: {key: schema_hash(tbl)}}}), encoding="utf-8"
    )

    request = _make_request(tmp_path, ep, state)

    holder = SharedSnowflakeConn()
    token = _SF_CONN_HOLDER.set(holder)
    try:
        with mock.patch.object(holder, "acquire") as mock_acquire:
            ScosRuntime().provision(request)  # fast-path fires; returns early

        mock_acquire.assert_not_called()
    finally:
        _SF_CONN_HOLDER.reset(token)
        holder.close()
