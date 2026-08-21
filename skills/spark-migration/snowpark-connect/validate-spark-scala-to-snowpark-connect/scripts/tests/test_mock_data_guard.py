"""The Scala validator seeds mocks through the PySpark skill's datagen. That is a
cross-skill API coupling, so it needs a test: a datagen refactor that breaks it
otherwise surfaces only as a hard-failing mock-data guard at runtime.
"""
import json
import os
import sys

import pytest

_SCALA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PYSPARK = os.path.join(
    os.path.dirname(os.path.dirname(_SCALA)),
    "validate-pyspark-to-snowpark-connect", "scripts")
for _p in (_SCALA, _PYSPARK, os.path.join(_PYSPARK, "harness")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import datagen  # noqa: E402
import scos_state  # noqa: E402


def _conv_root(tmp_path, tables):
    cr = tmp_path / "cr"
    schemas = cr / "Validation" / "shared" / "schemas"
    schemas.parent.mkdir(parents=True)
    ep = {"id": "ep", "path": "Main.scala", "run_mode": "script",
          "import_roots": ["."], "entrypoint_kwargs": {},
          "source_runtime": "spark", "tables": tables}
    datagen.write_schemas_dir(schemas, {
        "root": ".", "complete": True, "summary": {},
        "expected_divergences": {}, "entrypoints": [ep]})
    return cr, schemas, cr / "Validation" / "shared" / "mock_data"


def _read_table(cols):
    return {"relational": True, "category": "table", "access": "read", "columns": cols}


def test_guard_seeds_valid_tables_clean(tmp_path):
    cr, _s, mock = _conv_root(tmp_path, {
        "orders": _read_table([{"name": "id", "type": "string"},
                               {"name": "amt", "type": "double"}]),
        "cust": _read_table([{"name": "id", "type": "string"}]),
    })
    rc, problems = scos_state._ensure_mock_data(cr)
    assert (rc, problems) == (0, [])
    assert {p.name for p in (mock / "ep").glob("*.parquet")} == {
        "orders.parquet", "cust.parquet"}


def test_guard_wipes_a_stale_mock(tmp_path):
    cr, _s, mock = _conv_root(tmp_path, {
        "orders": _read_table([{"name": "id", "type": "string"}]),
    })
    assert scos_state._ensure_mock_data(cr)[0] == 0
    ghost = mock / "ep" / "ghost.parquet"
    ghost.write_bytes(b"stale")
    rc, problems = scos_state._ensure_mock_data(cr)
    assert (rc, problems) == (0, [])
    assert not ghost.exists(), "mock_data must reflect only the latest run"


def test_guard_reports_a_bad_table_and_withholds_only_its_data(tmp_path):
    cr, schemas, mock = _conv_root(tmp_path, {
        "orders": _read_table([{"name": "id", "type": "string"}]),
        "cust": _read_table([{"name": "id", "type": "string"}]),
    })
    assert scos_state._ensure_mock_data(cr)[0] == 0

    tdir = schemas / "entrypoints" / "ep" / "tables"
    bad = next(p for p in tdir.glob("*.json")
               if json.loads(p.read_text())["_table_key"] == "orders")
    entry = json.loads(bad.read_text())
    entry["columns"] = []
    bad.write_text(json.dumps(entry))

    rc, problems = scos_state._ensure_mock_data(cr)
    assert rc == 1 and problems
    assert any("orders" in p for p in problems)
    assert not (mock / "ep" / "orders.parquet").exists()
    assert (mock / "ep" / "cust.parquet").exists(), "sibling must keep its data"
    assert bad.is_file(), "the guard must never delete a table schema"
