"""Tests for ScosRuntime.provision() fast-path and conn kwarg.

The fast-path skips snowflake.connector.connect() when:
  - state["snowflake"]["golden_schemas"][ep_id] is populated (prior run succeeded)
  - all readable table hashes in provision_hashes.json match the current ep config

All tests mock snowflake/snowpark at sys.modules level so this suite runs without
the snowflake-connector-python or snowpark-connect packages installed.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap — must happen before importing runtimes
# ---------------------------------------------------------------------------
_TESTS_DIR = Path(__file__).parent
_SCRIPTS_DIR = _TESTS_DIR.parent
_HARNESS_DIR = _SCRIPTS_DIR / "harness"

if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

# ---------------------------------------------------------------------------
# Stub snowflake/snowpark in sys.modules BEFORE importing scos_runtime.
# The module-level import in scos_runtime.py is `from helpers import ...`
# (stdlib-only at module level); snowflake.connector is lazy inside methods.
# We pre-populate sys.modules so that `import snowflake.connector` inside
# provision() resolves to our stub without installing the real package.
# ---------------------------------------------------------------------------
_SF_MOD = sys.modules.get("snowflake") or types.ModuleType("snowflake")
_SF_CONN_MOD = sys.modules.get("snowflake.connector") or types.ModuleType("snowflake.connector")

# Default: raise if connect() is called unexpectedly.
_SF_CONN_MOD.connect = mock.MagicMock(  # type: ignore[attr-defined]
    side_effect=AssertionError("snowflake.connector.connect called unexpectedly")
)
_SF_MOD.connector = _SF_CONN_MOD  # type: ignore[attr-defined]

sys.modules.setdefault("snowflake", _SF_MOD)
sys.modules.setdefault("snowflake.connector", _SF_CONN_MOD)
sys.modules.setdefault("snowpark_connect", types.ModuleType("snowpark_connect"))
sys.modules.setdefault(
    "snowflake.snowpark_connect", types.ModuleType("snowflake.snowpark_connect")
)

# ---------------------------------------------------------------------------
# Now safe to import the runtime and its helpers
# ---------------------------------------------------------------------------
from runtimes.scos_runtime import ScosRuntime  # noqa: E402
from runtimes import _scos_provision as _scos_provision_mod  # noqa: E402
from runtimes.base import TrialRequest  # noqa: E402
from helpers import schema_hash, _bare_table_name  # type: ignore[import-not-found]  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture / builder helpers
# ---------------------------------------------------------------------------


def _make_mock_data_dir(tmp_path: Path, ep_id: str) -> str:
    """Return mock_data_dir path with the directory structure that yields
    workspace_root == tmp_path.

    Layout:
        tmp_path/
          shared/
            mock_data/
              <ep_id>/   ← mock_data_dir
    so that:
        mock_data_root = tmp_path/shared/mock_data
        workspace_root = mock_data_root.resolve().parents[1] = tmp_path
    """
    d = tmp_path / "shared" / "mock_data" / ep_id
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _write_provision_hashes(
    workspace_root: Path,
    ep_id: str,
    table_hashes: dict,
    flavor: str = "scos",
) -> None:
    """Write provision_hashes.json with given {table_key: hash} for the ep."""
    shared = workspace_root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    data = {flavor: {ep_id: table_hashes}}
    (shared / "provision_hashes.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def _make_request(
    tmp_path: Path,
    ep: dict,
    state: dict,
    trial_id: str = "trial_001",
) -> TrialRequest:
    ep_id = ep["id"]
    mock_data_dir = _make_mock_data_dir(tmp_path, ep_id)
    return TrialRequest(
        trial_id=trial_id,
        flavor="scos",
        project_root=str(tmp_path),
        entrypoint_path="",
        ep_config=ep,
        mock_data_dir=mock_data_dir,
        results_dir=str(tmp_path / "results"),
        state_json=state,
        analysis={},
    )


def _read_table() -> dict:
    """Minimal readable table entry."""
    return {
        "access": "read",
        "columns": [
            {"name": "id", "type": "int"},
            {"name": "name", "type": "string"},
        ],
        "original_path": "db.schema.orders",
    }


def _write_table() -> dict:
    """Minimal write-only table entry."""
    return {
        "access": "write",
        "columns": [{"name": "result_id", "type": "int"}],
        "original_path": "",
    }


def _table_key(tbl_name: str, tbl: dict) -> str:
    """Same derivation as _scos_provision._provision_entrypoint."""
    return _bare_table_name(tbl.get("original_path", "")) or tbl_name.lower()


def _base_state(ep_id: str, with_golden: bool = True) -> dict:
    state: dict = {
        "config": {
            "connection_name": "test_conn",
            "project_slug": "myproj",
        },
        "run_id": "abc12345",
        "snowflake": {"database": "SCOS_VALIDATION"},
    }
    if with_golden:
        state["snowflake"]["golden_schemas"] = {
            ep_id: {"schema": f"myproj_abc12345_golden_{ep_id}"}
        }
    return state


# Reusable mock return value for provision_golden_schemas in the slow path.
def _golden_return(ep_id: str) -> dict:
    return {ep_id: {"schema": f"myproj_abc12345_golden_{ep_id}", "stage": ""}}


# ---------------------------------------------------------------------------
# Case A — fast path fires: state present + hashes match → connect not called
# ---------------------------------------------------------------------------


def test_case_a_fast_path_fires(tmp_path):
    ep_id = "ep_foo"
    tbl = _read_table()
    ep = {"id": ep_id, "tables": {"orders": tbl}}
    state = _base_state(ep_id, with_golden=True)

    # Write provision_hashes.json with the correct hash for this table.
    key = _table_key("orders", tbl)
    _write_provision_hashes(tmp_path, ep_id, {key: schema_hash(tbl)})

    request = _make_request(tmp_path, ep, state)
    runtime = ScosRuntime()

    # connect() must not be called — side_effect=AssertionError enforces this.
    with mock.patch.object(
        _SF_CONN_MOD, "connect",
        side_effect=AssertionError("connect must not be called in fast path"),
    ):
        runtime.provision(request)  # should not raise


# ---------------------------------------------------------------------------
# Case B — missing state entry: hashes fine, state absent → connect IS called
# ---------------------------------------------------------------------------


def test_case_b_missing_state_falls_through(tmp_path):
    ep_id = "ep_bar"
    tbl = _read_table()
    ep = {"id": ep_id, "tables": {"orders": tbl}}
    # No golden_schemas entry for this ep
    state = _base_state(ep_id, with_golden=False)

    key = _table_key("orders", tbl)
    _write_provision_hashes(tmp_path, ep_id, {key: schema_hash(tbl)})

    request = _make_request(tmp_path, ep, state)
    runtime = ScosRuntime()

    mock_conn = mock.MagicMock()
    mock_conn.close = mock.MagicMock()
    with mock.patch.object(_SF_CONN_MOD, "connect", return_value=mock_conn) as mock_connect, \
         mock.patch.object(
             _scos_provision_mod, "provision_golden_schemas",
             return_value=_golden_return(ep_id),
         ):
        runtime.provision(request)

    mock_connect.assert_called_once()
    mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Case C — stale hash: state present, one hash mismatches → connect IS called
# ---------------------------------------------------------------------------


def test_case_c_stale_hash_falls_through(tmp_path):
    ep_id = "ep_baz"
    tbl = _read_table()
    ep = {"id": ep_id, "tables": {"orders": tbl}}
    state = _base_state(ep_id, with_golden=True)

    # Write a deliberately wrong hash.
    key = _table_key("orders", tbl)
    _write_provision_hashes(tmp_path, ep_id, {key: "deadbeef" * 8})

    request = _make_request(tmp_path, ep, state)
    runtime = ScosRuntime()

    mock_conn = mock.MagicMock()
    mock_conn.close = mock.MagicMock()
    with mock.patch.object(_SF_CONN_MOD, "connect", return_value=mock_conn) as mock_connect, \
         mock.patch.object(
             _scos_provision_mod, "provision_golden_schemas",
             return_value=_golden_return(ep_id),
         ):
        runtime.provision(request)

    mock_connect.assert_called_once()


# ---------------------------------------------------------------------------
# Case D — write-only tables only: no readable tables → conservative fall-through
# ---------------------------------------------------------------------------


def test_case_d_write_only_tables_falls_through(tmp_path):
    ep_id = "ep_qux"
    tbl = _write_table()
    ep = {"id": ep_id, "tables": {"sink_tbl": tbl}}
    state = _base_state(ep_id, with_golden=True)

    # Even if provision_hashes.json were perfectly up-to-date, with zero readable
    # tables has_readable=False, so the fast-path must NOT fire (conservative).
    _write_provision_hashes(tmp_path, ep_id, {})

    request = _make_request(tmp_path, ep, state)
    runtime = ScosRuntime()

    mock_conn = mock.MagicMock()
    mock_conn.close = mock.MagicMock()
    with mock.patch.object(_SF_CONN_MOD, "connect", return_value=mock_conn) as mock_connect, \
         mock.patch.object(
             _scos_provision_mod, "provision_golden_schemas",
             return_value=_golden_return(ep_id),
         ):
        runtime.provision(request)

    mock_connect.assert_called_once()


# ---------------------------------------------------------------------------
# Case E — conn kwarg: passed-in connection is used; connector.connect not called
# ---------------------------------------------------------------------------


def test_case_e_conn_kwarg_used_directly(tmp_path):
    ep_id = "ep_ext"
    tbl = _read_table()
    ep = {"id": ep_id, "tables": {"orders": tbl}}
    # No state entry → would normally need a connection → but conn is passed in.
    state = _base_state(ep_id, with_golden=False)

    request = _make_request(tmp_path, ep, state)
    runtime = ScosRuntime()

    external_conn = mock.MagicMock()
    external_conn.close = mock.MagicMock()

    with mock.patch.object(
        _SF_CONN_MOD, "connect",
        side_effect=AssertionError("connector.connect must not be called when conn is passed"),
    ), mock.patch.object(
        _scos_provision_mod, "provision_golden_schemas",
        return_value=_golden_return(ep_id),
    ) as mock_pgs:
        runtime.provision(request, conn=external_conn)

    # The external conn was passed through, not closed.
    external_conn.close.assert_not_called()
    # provision_golden_schemas was called with our external conn.
    assert mock_pgs.call_args[0][0] is external_conn
