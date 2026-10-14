"""Tests for Scala miner depth: StructType, attribution, sql_files, transitive depth."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import ast_to_analysis as a2a  # noqa: E402
import schema_mine as sm  # noqa: E402


# ---------------------------------------------------------------------------
# StructType Python fallback + jar schema_fields
# ---------------------------------------------------------------------------

def test_extract_struct_schemas_from_source():
    text = '''
    val ordersSchema = StructType(Seq(
      StructField("order_id", LongType, nullable = false),
      StructField("amount", DecimalType(10, 2)),
      StructField("status", StringType)
    ))
    '''
    schemas = a2a._extract_struct_schemas_from_source(text)
    assert len(schemas) == 1
    assert schemas[0]["name"] == "ordersSchema"
    by_name = {f["name"]: f for f in schemas[0]["fields"]}
    assert by_name["order_id"]["type"] == "long"
    assert by_name["amount"]["type"] == "decimal(10,2)"
    assert by_name["status"]["type"] == "string"
    assert by_name["order_id"]["nullable"] is False


def test_schema_for_read_prefers_schema_fields():
    rd = {
        "arg": "s3://bucket/orders",
        "schema_fields": [
            {"name": "id", "type": "long", "nullable": False},
            {"name": "amt", "type": "double"},
        ],
    }
    schema, origin, todo = a2a._schema_for_read(
        rd, ["noise_col"], {}, multi_source=True,
    )
    assert origin == "structtype"
    assert todo == ""
    assert {c["name"] for c in schema} == {"id", "amt"}
    assert schema[0]["type"] == "long"


def test_schema_for_read_named_struct_match():
    named = {
        "ordersSchema": [
            {"name": "order_id", "type": "long"},
            {"name": "qty", "type": "int"},
        ],
    }
    rd = {"arg": "db.sch.orders"}
    schema, origin, todo = a2a._schema_for_read(
        rd, ["other"], named, multi_source=True,
    )
    assert origin == "structtype_named"
    assert todo == ""
    assert {c["name"] for c in schema} == {"order_id", "qty"}


def test_build_source_catalog_structtype_skips_attribution_todo():
    reads = [
        {
            "call": "parquet",
            "arg": "/data/orders",
            "category": "file",
            "reader_method": "parquet",
            "schema_fields": [{"name": "id", "type": "long"}],
        },
        {
            "call": "parquet",
            "arg": "/data/items",
            "category": "file",
            "reader_method": "parquet",
        },
    ]
    sources, todos = a2a._build_source_catalog(
        reads, ["id", "sku", "price"],
    )
    by_path = {s["original_path"]: s for s in sources}
    assert by_path["/data/orders"]["schema_origin"] == "structtype"
    assert "llm_todo" not in by_path["/data/orders"]
    # Second source still has shared column_refs attribution todo
    assert by_path["/data/items"].get("schema_origin") == "column_refs_shared"
    assert "llm_todo" in by_path["/data/items"]


# ---------------------------------------------------------------------------
# Per-read column attribution via deep_analysis-style merge
# ---------------------------------------------------------------------------

def test_per_read_column_refs_from_helpers():
    reads = [
        {"call": "table", "arg": "orders", "category": "table", "reader_method": "table"},
        {"call": "table", "arg": "items", "category": "table", "reader_method": "table"},
    ]
    per = {
        0: ["order_id", "customer_id"],
        1: ["sku", "price"],
    }
    sources, _ = a2a._build_source_catalog(
        reads, ["order_id", "customer_id", "sku", "price"],
        per_read_column_refs=per,
    )
    by_name = {s["name"]: s for s in sources}
    order_cols = {c["name"] for c in by_name["orders"]["schema"]}
    item_cols = {c["name"] for c in by_name["items"]["schema"]}
    # With per-read refs, each source gets only its file's columns — but
    # multi_source still sets column_refs_shared origin when no StructType.
    # The schema itself should still be built from local_cols.
    assert order_cols == {"order_id", "customer_id"}
    assert item_cols == {"sku", "price"}


# ---------------------------------------------------------------------------
# sql_files catalog
# ---------------------------------------------------------------------------

def test_catalog_sql_files(tmp_path):
    pytest.importorskip("sqlglot")
    (tmp_path / "sql").mkdir()
    (tmp_path / "sql" / "load_orders.sql").write_text(
        "INSERT INTO tgt_orders SELECT id, amount FROM src_orders WHERE status = 'OPEN';\n",
        encoding="utf-8",
    )
    catalog = sm.catalog_sql_files(tmp_path)
    assert len(catalog) == 1
    assert catalog[0]["path"] == "sql/load_orders.sql"
    tables = catalog[0]["tables"]
    assert "src_orders" in tables or "src_orders" in {t.lower() for t in tables}
    # roles include read for source and write for insert target
    roles = {k: set(v["roles"]) for k, v in tables.items()}
    assert any("read" in r for r in roles.values())
    assert any("write" in r for r in roles.values())


def test_analysis_to_schemas_writes_sql_files(tmp_path):
    pytest.importorskip("sqlglot")
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True)
    source = tmp_path / "Validation" / "source"
    source.mkdir(parents=True)
    (source / "q.sql").write_text(
        "SELECT a, b FROM dim_store;\n", encoding="utf-8",
    )
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{
            "id": "ep1",
            "external_sources": [],
            "sinks": [],
        }],
        "complete": True,
    }), encoding="utf-8")
    res = sm.analysis_to_schemas(tmp_path)
    assert res.get("sql_files", 0) >= 1
    sql_path = shared / "schemas" / "sql_files.json"
    assert sql_path.is_file()
    catalog = json.loads(sql_path.read_text())
    assert catalog[0]["path"] == "q.sql"
    assert "dim_store" in catalog[0]["tables"]


# ---------------------------------------------------------------------------
# Transitive depth default
# ---------------------------------------------------------------------------

def test_collect_transitive_default_depth_is_four():
    import inspect
    sig = inspect.signature(a2a._collect_transitive_facts)
    assert sig.parameters["max_depth"].default == 4


def test_reads_forward_schema_fields():
    facts = {
        "reads": [{
            "call": "parquet",
            "args": ["/data/x"],
            "line": 10,
            "schema_fields": [{"name": "id", "type": "long"}],
        }],
        "unresolved_reads": [],
        "table_refs": [],
    }
    reads = a2a._reads_for_entrypoint(facts)
    assert len(reads) == 1
    assert reads[0]["schema_fields"][0]["name"] == "id"


def test_df_attribution_prefers_source_columns():
    reads = [
        {"call": "parquet", "arg": "/data/orders", "category": "file", "reader_method": "parquet"},
        {"call": "parquet", "arg": "/data/items", "category": "file", "reader_method": "parquet"},
    ]
    sources, _ = a2a._build_source_catalog(
        reads,
        ["order_id", "sku", "price", "noise"],
        source_columns={
            "/data/orders": ["order_id"],
            "/data/items": ["sku", "price"],
        },
    )
    by = {s["original_path"]: s for s in sources}
    assert {c["name"] for c in by["/data/orders"]["schema"]} == {"order_id"}
    assert {c["name"] for c in by["/data/items"]["schema"]} == {"sku", "price"}
    assert by["/data/orders"]["schema_origin"] == "df_attribution"


def test_validate_with_sqlframe_skipped_without_sql():
    assert a2a._validate_with_sqlframe([], [])["status"] == "skipped"


def test_apply_sqlframe_validation_adds_missing_col():
    sources = [{
        "id": "src_t",
        "name": "t",
        "schema": [{"name": "a", "type": "string"}],
    }]
    todos: list = []
    validation = {
        "status": "ran",
        "missing_columns": [{"column": "b", "error": "unknown"}],
    }
    out, todos2 = a2a._apply_sqlframe_validation(sources, todos, validation)
    names = {c["name"] for c in out[0]["schema"]}
    assert "b" in names
    assert any("sqlframe" in t for t in todos2)
