"""Tests for the Scala validator's schema_mine.py (analysis.json -> schemas/).

This is the Scala analog of the PySpark validator's schema_mine. It converts the
Scalameta analyzer's analysis.json into the PySpark ``schemas/`` layout so the
*unchanged* canonical datagen.py / provision.py can consume it. These tests lock
in the mapping: external_sources -> read tables (+mock), non-tabular -> staged
(relational False), sinks -> empty write tables, intermediates -> empty write
tables (seed_sql intentionally NOT applied).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import schema_mine as sm  # noqa: E402


def _write_analysis(tmp_path: Path, doc: dict) -> None:
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps(doc), encoding="utf-8")


def _read_schemas(tmp_path: Path) -> dict:
    sd = tmp_path / "Validation" / "shared" / "schemas"
    manifest = json.loads((sd / "manifest.json").read_text())
    eps = {}
    for ref in manifest["entrypoints"]:
        ep_dir = sd / ref["dir"]
        meta = json.loads((ep_dir / "_meta.json").read_text())
        tables: dict = {}
        tables_dir = ep_dir / "tables"
        if tables_dir.is_dir():
            for tf in sorted(tables_dir.glob("*.json")):
                t = json.loads(tf.read_text())
                key = t.pop("_table_key")
                tables[key] = t
        meta["tables"] = tables
        eps[ref["id"]] = meta
    return eps


def test_sources_sinks_and_file_mapping(tmp_path):
    _write_analysis(tmp_path, {"entrypoints": [{
        "id": "ep1",
        "external_sources": [
            {"id": "orders", "name": "DB.SCH.ORDERS", "category": "table",
             "mock_file": "orders.csv", "schema": [{"name": "id", "type": "int"}]},
            {"id": "cfg", "name": "config.json", "category": "file",
             "mock_file": "config.json", "schema": "not-a-list"},
        ],
        "sinks": [{"id": "out1", "kind": "table", "name": "DB.SCH.OUT",
                   "schema": [{"name": "id", "type": "int"}]}],
    }]})
    sm.analysis_to_schemas(tmp_path)
    tables = _read_schemas(tmp_path)["ep1"]["tables"]
    # Table keys prefer analysis id (not bare FQN) — see _table_entry.
    assert tables["orders"]["access"] == "read"
    # Explicit analysis mock_file is preserved (not rewritten to .parquet).
    assert tables["orders"]["mock_file"] == "orders.csv"
    assert tables["orders"]["relational"] is True
    assert tables["cfg"]["relational"] is False           # non-tabular -> staged
    assert tables["cfg"]["mock_file"] == "config.json"
    assert tables["out1"]["access"] == "write"
    assert tables["out1"].get("mock_file") is None


def test_non_table_sinks_kept_with_allow_empty(tmp_path):
    """File/excel sinks must not be dropped — Phase B stage capture needs them."""
    _write_analysis(tmp_path, {"entrypoints": [{
        "id": "ep1",
        "external_sources": [],
        "sinks": [
            {"id": "xlsx_out", "kind": "excel", "name": "report.xlsx",
             "format": "excel", "allow_empty": "header-only export",
             "schema": [{"name": "id", "type": "int"}]},
            {"id": "pq_out", "kind": "file", "name": "out.parquet",
             "format": "parquet",
             "schema": [{"name": "v", "type": "string"}]},
        ],
    }]})
    sm.analysis_to_schemas(tmp_path)
    tables = _read_schemas(tmp_path)["ep1"]["tables"]
    assert "xlsx_out" in tables
    assert tables["xlsx_out"]["category"] == "excel"
    assert tables["xlsx_out"]["access"] == "write"
    assert tables["xlsx_out"]["allow_empty"] == "header-only export"
    assert "pq_out" in tables
    assert tables["pq_out"]["category"] == "file"
    assert tables["pq_out"]["format"] == "parquet"

def test_intermediate_created_empty_seed_sql_not_applied(tmp_path):
    _write_analysis(tmp_path, {
        "entrypoints": [{"id": "ep1", "external_sources": [], "sinks": []}],
        "intermediate_tables": [{
            "name": "DB.SCH.MID", "writer_entrypoint_id": "ep1",
            "reader_entrypoint_ids": ["ep1"], "schema": [{"name": "k", "type": "string"}],
            "seed_strategy": "from_source_join", "seed_sql": "SELECT 1",
        }],
    })
    sm.analysis_to_schemas(tmp_path)
    mid = _read_schemas(tmp_path)["ep1"]["tables"]["MID"]
    assert mid["access"] == "write"
    assert mid.get("mock_file") is None    # created empty
    assert "seed_sql" not in mid           # seed_sql intentionally not carried


def test_ref_schema_resolved_from_schemas_json(tmp_path):
    _write_analysis(tmp_path, {"entrypoints": [{
        "id": "ep1",
        "external_sources": [{"id": "o", "name": "ORDERS", "category": "table",
                              "mock_file": "o.csv",
                              "schema": {"$ref": "schemas.json#/external_sources/orders"}}],
    }]})
    shared = tmp_path / "Validation" / "shared"
    (shared / "schemas.json").write_text(json.dumps(
        {"external_sources": {"orders": [{"name": "id", "type": "int"},
                                         {"name": "amt", "type": "double"}]}}), encoding="utf-8")
    sm.analysis_to_schemas(tmp_path)
    cols = [c["name"] for c in _read_schemas(tmp_path)["ep1"]["tables"]["o"]["columns"]]
    assert cols == ["id", "amt"]


# ---------------------------------------------------------------------------
# Directory layout: _table_filename, _meta/tables split, manifest "dir" key
# ---------------------------------------------------------------------------

def test_table_filename_sanitization(tmp_path):
    """Unsafe chars in a table key are replaced so the filename is safe."""
    used: set = set()
    result = sm._table_filename("DB TBL", used)
    assert " " not in result
    assert result in used


def test_table_filename_collision(tmp_path):
    """Two keys that sanitize to the same stem get distinct filenames."""
    used: set = set()
    first = sm._table_filename("a/b", used)
    second = sm._table_filename("a\\b", used)
    assert first != second
    assert len(used) == 2


def test_table_filename_empty_key(tmp_path):
    """An empty key falls back to '_table'."""
    used: set = set()
    result = sm._table_filename("", used)
    assert result == "_table"
    assert "_table" in used


def test_table_key_round_trip(tmp_path):
    """The original table key is preserved via _table_key and survives _read_schemas."""
    _write_analysis(tmp_path, {"entrypoints": [{
        "id": "ep1",
        "external_sources": [
            {"id": "o", "name": "DB.SCH.ORDERS", "category": "table",
             "mock_file": "o.csv", "schema": [{"name": "id", "type": "int"}]},
        ],
    }]})
    sm.analysis_to_schemas(tmp_path)
    tables = _read_schemas(tmp_path)["ep1"]["tables"]
    assert "o" in tables
    assert tables["o"]["access"] == "read"


def test_meta_tables_separation(tmp_path):
    """Entrypoint metadata lands in _meta.json; tables are in tables/ subdir."""
    _write_analysis(tmp_path, {"entrypoints": [{
        "id": "ep1",
        "external_sources": [
            {"id": "o", "name": "ORDERS", "category": "table",
             "mock_file": "o.csv", "schema": [{"name": "id", "type": "int"}]},
        ],
    }]})
    sm.analysis_to_schemas(tmp_path)
    sd = tmp_path / "Validation" / "shared" / "schemas"
    manifest = json.loads((sd / "manifest.json").read_text())
    ref = manifest["entrypoints"][0]
    assert "dir" in ref and "file" not in ref
    ep_dir = sd / ref["dir"]
    meta = json.loads((ep_dir / "_meta.json").read_text())
    assert "tables" not in meta
    assert (ep_dir / "tables").is_dir()
    assert len(list((ep_dir / "tables").glob("*.json"))) == 1
