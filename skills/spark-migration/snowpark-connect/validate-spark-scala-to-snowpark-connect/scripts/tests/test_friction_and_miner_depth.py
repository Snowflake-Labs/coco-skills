"""Tests for friction + miner-depth follow-ups (schemas-first gates, sqlglot, cross-EP)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import ast_to_analysis as a2a  # noqa: E402
import column_check as cc  # noqa: E402
import schema_mine as sm  # noqa: E402

_spec = importlib.util.spec_from_file_location("scos_state", _SCRIPTS / "scos_state.py")
_scos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scos)  # type: ignore[union-attr]


def _write_schemas_layout(tmp_path: Path, eps: list[dict]) -> Path:
    """Write a minimal schemas/ tree + return shared dir."""
    shared = tmp_path / "Validation" / "shared"
    schemas = shared / "schemas"
    schemas.mkdir(parents=True, exist_ok=True)
    refs = []
    for ep in eps:
        eid = ep["id"]
        ep_dir = schemas / "entrypoints" / eid
        (ep_dir / "tables").mkdir(parents=True, exist_ok=True)
        tables = ep.pop("tables", {})
        meta = {k: v for k, v in ep.items()}
        (ep_dir / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        for tkey, tbl in tables.items():
            payload = dict(tbl)
            payload["_table_key"] = tkey
            (ep_dir / "tables" / f"{tkey}.json").write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
        refs.append({"id": eid, "path": ep.get("path") or eid, "dir": f"entrypoints/{eid}"})
    (schemas / "manifest.json").write_text(
        json.dumps({"entrypoints": refs, "complete": True, "summary": {}}, indent=2),
        encoding="utf-8",
    )
    return shared


# ---------------------------------------------------------------------------
# Cross-EP inheritance
# ---------------------------------------------------------------------------

def test_cross_ep_schema_inheritance(tmp_path):
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True)
    analysis = {
        "entrypoints": [
            {
                "id": "producer",
                "external_sources": [],
                "sinks": [{
                    "id": "staging_foo",
                    "kind": "table",
                    "name": "db.sch.staging_foo",
                    "original_target": "db.sch.staging_foo",
                    "schema": [
                        {"name": "id", "type": "long"},
                        {"name": "amount", "type": "double"},
                    ],
                }],
            },
            {
                "id": "consumer",
                "external_sources": [{
                    "id": "staging_foo",
                    "category": "table",
                    "name": "db.sch.staging_foo",
                    "original_path": "db.sch.staging_foo",
                    "schema": [{"name": "id", "type": "string"}],  # incomplete
                }],
                "sinks": [],
            },
        ],
    }
    (shared / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    sm.analysis_to_schemas(tmp_path)
    consumer_tables = {}
    tdir = tmp_path / "Validation" / "shared" / "schemas" / "entrypoints" / "consumer" / "tables"
    for tf in tdir.glob("*.json"):
        t = json.loads(tf.read_text())
        key = t.pop("_table_key", tf.stem)
        consumer_tables[key] = t
    cols = {c["name"]: c for c in consumer_tables["staging_foo"]["columns"]}
    assert "id" in cols
    assert "amount" in cols
    assert cols["amount"].get("origin") == "intermediate_sink"


# ---------------------------------------------------------------------------
# sqlglot lineage
# ---------------------------------------------------------------------------

def test_sql_lineage_extracts_tables_and_columns():
    pytest.importorskip("sqlglot")
    lineage = a2a._sql_lineage([
        "SELECT a.id, a.amt FROM orders a WHERE a.status = 'OPEN'"
    ])
    assert "orders" in lineage
    assert set(lineage["orders"]["columns"]) >= {"id", "amt", "status"}
    assert "OPEN" in (lineage.get("__col_values__") or {}).get("status", [])


def test_merge_sql_lineage_adds_missing_source():
    pytest.importorskip("sqlglot")
    sources = []
    lineage = a2a._sql_lineage(["SELECT x, y FROM dim_store"])
    out = a2a._merge_sql_lineage_into_sources(sources, lineage)
    assert len(out) == 1
    assert out[0]["name"].lower() in ("dim_store", "store") or "dim_store" in out[0]["id"]
    names = {c["name"] for c in out[0]["schema"]}
    assert names >= {"x", "y"}


# ---------------------------------------------------------------------------
# Config pool builder
# ---------------------------------------------------------------------------

def test_build_flat_config_pool_from_json(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.json").write_text(json.dumps({
        "s3SinkDirectory": "s3://bucket/out/",
        "nested": {"DATABASE": "ANALYTICS"},
    }), encoding="utf-8")
    out = tmp_path / "config_pool.json"
    path = sm._build_flat_config_pool(src, out)
    assert path == out
    pool = json.loads(out.read_text())
    assert pool.get("s3SinkDirectory") == "s3://bucket/out/"
    assert pool.get("DATABASE") == "ANALYTICS"


# ---------------------------------------------------------------------------
# Prevalidate hash prefers schemas/
# ---------------------------------------------------------------------------

def test_prevalidate_hash_changes_when_schemas_edited(tmp_path):
    shared = _write_schemas_layout(tmp_path, [{
        "id": "ep1",
        "path": "Job.scala",
        "entrypoint_class": "com.example.Job$",
        "entrypoint_method": "main",
        "tables": {
            "orders": {
                "access": "read", "category": "table", "relational": True,
                "columns": [{"name": "id", "type": "long"}],
            },
        },
    }])
    # Seed a shim analysis.json as well
    (shared / "analysis.json").write_text(json.dumps({"entrypoints": []}), encoding="utf-8")
    h1 = _scos._prevalidate_hash_state(tmp_path)
    # Edit a table — hash must change
    tpath = shared / "schemas" / "entrypoints" / "ep1" / "tables" / "orders.json"
    tbl = json.loads(tpath.read_text())
    tbl["columns"].append({"name": "amount", "type": "double"})
    tpath.write_text(json.dumps(tbl), encoding="utf-8")
    h2 = _scos._prevalidate_hash_state(tmp_path)
    assert h1 != h2


def test_load_analysis_prefer_schemas_refreshes_shim(tmp_path):
    shared = _write_schemas_layout(tmp_path, [{
        "id": "ep1",
        "path": "Job.scala",
        "entrypoint_class": "com.example.Job$",
        "entrypoint_method": "main",
        "tables": {
            "orders": {
                "access": "read", "category": "table", "relational": True,
                "columns": [{"name": "id", "type": "long"}],
                "original_path": "db.sch.orders",
            },
        },
    }])
    # Stale analysis with wrong entrypoint_class
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{"id": "ep1", "entrypoint_class": "WRONG"}],
    }), encoding="utf-8")
    analysis = _scos.load_analysis_prefer_schemas(tmp_path)
    eps = {e["id"]: e for e in analysis["entrypoints"]}
    assert eps["ep1"]["entrypoint_class"] == "com.example.Job$"
    assert eps["ep1"]["external_sources"]


# ---------------------------------------------------------------------------
# column_check refreshes from schemas
# ---------------------------------------------------------------------------

def test_column_check_refresh_from_schemas(tmp_path):
    source = tmp_path / "Validation" / "source"
    source.mkdir(parents=True)
    scala = source / "Job.scala"
    scala.write_text("object Job { def main(args: Array[String]) = () }", encoding="utf-8")
    shared = _write_schemas_layout(tmp_path, [{
        "id": "job",
        "path": "Job.scala",
        "entrypoint_class": "Job$",
        "entrypoint_method": "main",
        "tables": {
            "orders": {
                "access": "read", "category": "table", "relational": True,
                "columns": [
                    {"name": "order_id", "type": "string"},
                    {"name": "amount", "type": "double"},
                ],
                "original_path": "orders",
            },
        },
    }])
    # Stale analysis missing amount — refresh must pull amount from schemas
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{
            "id": "job",
            "path": "Job.scala",
            "external_sources": [{
                "id": "orders",
                "category": "table",
                "schema": [{"name": "order_id", "type": "string"}],
            }],
            "sinks": [],
        }],
        "external_sources": [{
            "id": "orders",
            "category": "table",
            "schema": [{"name": "order_id", "type": "string"}],
        }],
    }), encoding="utf-8")
    (shared / "ast_facts.json").write_text(json.dumps({
        "files": [{
            "path": str(scala),
            "parse_ok": True,
            "column_refs": ["order_id", "amount"],
            "write_helpers": [],
        }],
    }), encoding="utf-8")

    refreshed = cc._refresh_analysis_from_schemas(tmp_path)
    assert refreshed is not None
    analysis = json.loads(refreshed.read_text())
    ast_facts = json.loads((shared / "ast_facts.json").read_text())
    probs = cc.check_columns(ast_facts, analysis, source_root=source)
    assert probs == [], probs


def test_render_spec_injects_schemas_dir_path():
    out = _scos._render_spec(
        "schemas={{SCHEMAS_DIR_PATH}};analysis={{ANALYSIS_JSON_PATH}}",
        {"id": "ep1", "entrypoint_class": "C$", "entrypoint_method": "main"},
        "/j/a.jar",
        "/j/b.jar",
        "/t",
        "/a",
        "/conv/Validation/shared/analysis.json",
        "/conv/Validation/state.json",
    )
    assert "schemas=/conv/Validation/shared/schemas" in out
    assert "analysis=/conv/Validation/shared/analysis.json" in out
