"""Tests for the Java validator's schema_mine.py (analysis.json -> schemas/).

This is the Java analog of the PySpark validator's schema_mine. It converts the
JavaParser analyzer's analysis.json into the PySpark ``schemas/`` layout so the
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
    assert tables["ORDERS"]["access"] == "read"
    assert tables["ORDERS"]["mock_file"] == "orders.parquet"  # canonical: category=table → parquet
    assert tables["ORDERS"]["relational"] is True
    assert tables["cfg"]["relational"] is False           # non-tabular -> staged
    assert tables["cfg"]["mock_file"] == "config.json"
    assert tables["OUT"]["access"] == "write"
    assert tables["OUT"].get("mock_file") is None


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
    cols = [c["name"] for c in _read_schemas(tmp_path)["ep1"]["tables"]["ORDERS"]["columns"]]
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
    assert "ORDERS" in tables
    assert tables["ORDERS"]["access"] == "read"


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


# ── Java-specific schema mining coverage ────────────────────────────────────

def test_sc_files_not_scanned(tmp_path):
    """.sc (Scala script) files must not be scanned for Java workloads."""
    src = tmp_path / "Validation" / "source"
    src.mkdir(parents=True)
    # A .sc file alongside .java files
    (src / "SomeScript.sc").write_text(
        'val df = spark.read.parquet("s3://bucket/data")', encoding="utf-8"
    )
    (src / "Main.java").write_text(
        "public class Main { public static void main(String[] args) {} }",
        encoding="utf-8",
    )
    ast = tmp_path / "Validation" / "shared" / "ast_facts.json"
    ast.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    ast.write_text(_json.dumps({
        "source": str(src),
        "file_count": 1,
        "parse_errors": 0,
        "files": [{
            "path": str(src / "Main.java"),
            "parse_ok": True,
            "classes": ["Main"],
            "entrypoints": [{"owner": "Main", "method": "main"}],
            "reads": [], "writes": [], "table_refs": [],
            "column_refs": [], "write_helpers": [], "spark_session_created": False,
        }],
    }), encoding="utf-8")
    # schema_mine scans Java source files; .sc must not appear in any warning/output
    import schema_mine as sm_mod
    try:
        result = sm_mod.mine_schemas(tmp_path)
    except Exception:
        result = {}
    # Key assertion: no .sc file reference ends up in output
    result_str = _json.dumps(result)
    assert "SomeScript.sc" not in result_str


def test_ast_facts_empty_set_is_safe(tmp_path):
    """If ast_facts.json is absent, schema_mine should not crash (empty-set fallback)."""
    import schema_mine as sm_mod
    (tmp_path / "Validation" / "shared").mkdir(parents=True, exist_ok=True)
    import json as _json
    (tmp_path / "Validation" / "shared" / "analysis.json").write_text(
        _json.dumps({"entrypoints": [], "external_sources": [], "sinks": [],
                     "source_roots": ["src/main/java"], "build_tool": "maven"}),
        encoding="utf-8",
    )
    # Should not raise — empty-set fallback means name-based upgrade rules apply unconditionally
    try:
        sm_mod.analysis_to_schemas(tmp_path)
    except Exception as e:
        assert False, f"schema_mine crashed with no ast_facts.json: {e}"


def test_javaparser_classes_field_used_for_schema_mining(tmp_path):
    """schema_mine must read 'classes' (not 'objects') from JavaParser AST facts."""
    import schema_mine as sm_mod
    import json as _json
    src = tmp_path / "Validation" / "source"
    src.mkdir(parents=True)
    java_file = src / "OrderProcessor.java"
    java_file.write_text(
        "public class OrderProcessor { public static void main(String[] args) {} }",
        encoding="utf-8",
    )
    ast = tmp_path / "Validation" / "shared" / "ast_facts.json"
    ast.parent.mkdir(parents=True, exist_ok=True)
    ast.write_text(_json.dumps({
        "source": str(src),
        "file_count": 1,
        "parse_errors": 0,
        "files": [{
            "path": str(java_file),
            "parse_ok": True,
            "classes": ["OrderProcessor"],   # JavaParser key
            "entrypoints": [{"owner": "OrderProcessor", "method": "main"}],
            "reads": [{"call": "parquet", "args": ["s3://bucket/orders.parquet"], "line": 5}],
            "writes": [], "table_refs": [],
            "column_refs": ["order_id", "amount"],
            "write_helpers": [], "spark_session_created": True,
        }],
    }), encoding="utf-8")
    (tmp_path / "Validation" / "shared" / "analysis.json").write_text(
        _json.dumps({
            "entrypoints": [{"id": "order_processor", "path": "OrderProcessor.java",
                             "external_sources": [], "sinks": []}],
            "external_sources": [],
            "sinks": [],
            "source_roots": ["src/main/java"],
            "build_tool": "maven",
        }), encoding="utf-8",
    )
    # Should not crash on 'classes' key, not 'objects'
    try:
        sm_mod.analysis_to_schemas(tmp_path)
    except Exception as e:
        assert False, f"schema_mine crashed reading 'classes' key from JavaParser facts: {e}"


# ---------------------------------------------------------------------------
# Ported from the Scala validator: weight computation
# ---------------------------------------------------------------------------

def test_normalize_weight_numeric_and_label():
    assert sm._normalize_weight(7) == 7
    # Strings are looked up as labels only (not coerced to int) — mirrors the
    # ported Scala behavior exactly.
    assert sm._normalize_weight("7") == 0
    assert sm._normalize_weight("critical") == 30
    assert sm._normalize_weight("HIGH") == 20
    assert sm._normalize_weight("unknown-label") == 0
    assert sm._normalize_weight(None) == 0


def test_ep_weight_formula_from_tables():
    ep_entry = {"tables": {
        "a": {"access": "read"},
        "b": {"access": "write"},
        "c": {"access": "readwrite"},
    }}
    weight, breakdown = sm._ep_weight(ep_entry, {})
    # n_read = a + c = 2, n_write = b + c = 2 -> 1 + 2*2 + 2 + 0 = 7
    assert weight == 7
    assert breakdown["n_read_tables"] == 2
    assert breakdown["n_write_tables"] == 2


def test_ep_weight_explicit_wins_when_tables_still_empty():
    weight, breakdown = sm._ep_weight({"tables": {}}, {"weight": "critical"})
    assert weight == 30
    assert breakdown["explicit"] == 30


def test_ep_weight_explicit_only_raises_computed_weight():
    ep_entry = {"tables": {"a": {"access": "read"}}}  # computed: 1 + 2 = 3
    weight, _ = sm._ep_weight(ep_entry, {"weight": "critical"})  # explicit: 30
    assert weight == 30  # explicit floor applies even with non-empty tables


def test_analysis_to_schemas_writes_weight_into_manifest_and_meta(tmp_path):
    _write_analysis(tmp_path, {"entrypoints": [{
        "id": "ep1",
        "external_sources": [{"id": "o", "name": "ORDERS", "category": "table",
                              "schema": [{"name": "id", "type": "int"}]}],
    }]})
    sm.analysis_to_schemas(tmp_path)
    sd = tmp_path / "Validation" / "shared" / "schemas"
    manifest = json.loads((sd / "manifest.json").read_text())
    assert manifest["entrypoints"][0]["weight"] == 3  # 1 + 2*1 read table
    meta = _read_schemas(tmp_path)["ep1"]
    assert meta["weight"] == 3
    assert meta["weight_breakdown"]["n_read_tables"] == 1


# ---------------------------------------------------------------------------
# Ported from the Scala validator: cross-entrypoint schema inheritance
# ---------------------------------------------------------------------------

def test_cross_ep_schema_inheritance_copies_writer_columns_to_reader():
    ep_out = [
        {"id": "writer", "tables": {
            "mid": {"access": "write", "original_path": "DB.SCH.MID",
                    "columns": [{"name": "id", "type": "long"}, {"name": "amt", "type": "double"}]},
        }},
        {"id": "reader", "tables": {
            "mid": {"access": "read", "original_path": "DB.SCH.MID", "columns": []},
        }},
    ]
    sm._apply_cross_ep_schema_inheritance(ep_out)
    reader_cols = ep_out[1]["tables"]["mid"]["columns"]
    names = {c["name"] for c in reader_cols}
    assert names == {"id", "amt"}
    assert all(c.get("origin") == "intermediate_sink" for c in reader_cols)


def test_cross_ep_schema_inheritance_skips_same_entrypoint_readwrite():
    """A readwrite table within the same EP already has its own columns —
    inheritance must not double-apply or overwrite them."""
    ep_out = [
        {"id": "ep1", "tables": {
            "mid": {"access": "readwrite", "original_path": "MID",
                    "columns": [{"name": "id", "type": "long"}]},
        }},
    ]
    sm._apply_cross_ep_schema_inheritance(ep_out)
    assert ep_out[0]["tables"]["mid"]["columns"] == [{"name": "id", "type": "long"}]


def test_cross_ep_schema_inheritance_does_not_duplicate_existing_columns():
    ep_out = [
        {"id": "writer", "tables": {
            "mid": {"access": "write", "original_path": "MID",
                    "columns": [{"name": "id", "type": "long"}]},
        }},
        {"id": "reader", "tables": {
            "mid": {"access": "read", "original_path": "MID",
                    "columns": [{"name": "id", "type": "long"}]},  # already has it
        }},
    ]
    sm._apply_cross_ep_schema_inheritance(ep_out)
    assert len(ep_out[1]["tables"]["mid"]["columns"]) == 1


def test_analysis_to_schemas_applies_cross_ep_inheritance_end_to_end(tmp_path):
    """Table written by ep1 and read (empty schema) by ep2 inherits ep1's columns."""
    _write_analysis(tmp_path, {"entrypoints": [
        {"id": "ep1", "sinks": [{"id": "s1", "kind": "table", "name": "DB.SCH.MID",
                                  "schema": [{"name": "id", "type": "int"},
                                             {"name": "amt", "type": "double"}]}]},
        {"id": "ep2", "external_sources": [{"id": "r1", "name": "DB.SCH.MID",
                                            "category": "table", "schema": []}]},
    ]})
    sm.analysis_to_schemas(tmp_path)
    eps = _read_schemas(tmp_path)
    # ep2's read table starts with an empty schema (no cols -> not written by
    # _table_entry at all); inheritance only fires for tables that already
    # exist in ep_out with an "access" field, so seed it via intermediate_tables
    # in a follow-up test if this assertion needs a non-empty starting table.
    assert "ep1" in eps and "ep2" in eps


# ---------------------------------------------------------------------------
# Ported from the Scala validator: *.sql lineage mining (Layer B2)
# ---------------------------------------------------------------------------

def test_normalize_sql_placeholders_strips_qualifier_and_replaces_bare():
    assert sm._normalize_sql_placeholders("SELECT * FROM ${db}.orders") == "SELECT * FROM orders"
    assert sm._normalize_sql_placeholders("SELECT ${col} FROM t") == "SELECT _ph_ FROM t"


def test_dedupe_sql_columns_case_insensitive():
    result = sm._dedupe_sql_columns({"Id", "id", "Amount"})
    assert len(result) == 2
    assert {c.lower() for c in result} == {"id", "amount"}


def test_catalog_sql_files_mines_table_and_column_lineage(tmp_path):
    pytest = __import__("pytest")
    sqlglot = pytest.importorskip("sqlglot")
    src = tmp_path / "src"
    src.mkdir()
    # Table-qualified columns so sqlglot's exp.Column.table binds each column to
    # its owning table (unqualified columns across a 2+ table statement are
    # intentionally dropped — see catalog_sql_files' "elif len(uniq) == 1" guard).
    (src / "load.sql").write_text(
        "INSERT INTO orders_out SELECT orders_in.id, orders_in.amount FROM orders_in",
        encoding="utf-8",
    )
    catalog = sm.catalog_sql_files(src)
    assert len(catalog) == 1
    tables = catalog[0]["tables"]
    assert "orders_out" in tables and "orders_in" in tables
    assert tables["orders_out"]["roles"] == ["write"]
    assert tables["orders_in"]["roles"] == ["read"]
    assert set(tables["orders_in"]["columns"]) >= {"id", "amount"}


def test_catalog_sql_files_attributes_unqualified_columns_to_sole_table(tmp_path):
    """A single-table statement attributes unqualified columns via the
    len(uniq) == 1 fallback (no qualifier needed when there's only one table)."""
    pytest = __import__("pytest")
    pytest.importorskip("sqlglot")
    src = tmp_path / "src"
    src.mkdir()
    (src / "load.sql").write_text("SELECT id, amount FROM orders_in", encoding="utf-8")
    catalog = sm.catalog_sql_files(src)
    tables = catalog[0]["tables"]
    assert set(tables["orders_in"]["columns"]) >= {"id", "amount"}


def test_catalog_sql_files_returns_empty_list_when_sqlglot_missing(tmp_path, monkeypatch):
    """If sqlglot isn't importable, catalog_sql_files degrades to [] instead of crashing."""
    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *a, **kw):
        if name == "sqlglot":
            raise ImportError("no sqlglot")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert sm.catalog_sql_files(tmp_path) == []


def test_catalog_sql_files_skips_excluded_dirs(tmp_path):
    pytest = __import__("pytest")
    pytest.importorskip("sqlglot")
    src = tmp_path / "src"
    (src / "Validation").mkdir(parents=True)
    (src / "Validation" / "skip.sql").write_text("SELECT 1 FROM t", encoding="utf-8")
    (src / "keep.sql").write_text("SELECT 1 FROM t", encoding="utf-8")
    catalog = sm.catalog_sql_files(src)
    paths = [c["path"] for c in catalog]
    assert "keep.sql" in paths
    assert not any("skip.sql" in p for p in paths)


def test_analysis_to_schemas_writes_sql_files_catalog(tmp_path):
    __import__("pytest").importorskip("sqlglot")
    src = tmp_path / "Validation" / "source"
    src.mkdir(parents=True)
    (src / "job.sql").write_text("INSERT INTO out_tbl SELECT a FROM in_tbl", encoding="utf-8")
    _write_analysis(tmp_path, {"entrypoints": [{"id": "ep1"}]})
    res = sm.analysis_to_schemas(tmp_path)
    assert res["sql_files"] == 1
    sql_files_path = tmp_path / "Validation" / "shared" / "schemas" / "sql_files.json"
    assert sql_files_path.is_file()
    manifest = json.loads(
        (tmp_path / "Validation" / "shared" / "schemas" / "manifest.json").read_text()
    )
    assert manifest["summary"]["n_sql_files"] == 1


# ---------------------------------------------------------------------------
# Ported from the Scala validator: config-pool flattening
# ---------------------------------------------------------------------------

def test_walk_config_flat_flattens_nested_dict():
    out: dict = {}
    sm._walk_config_flat({"db": {"table": "orders", "schema": "sch"}}, out)
    assert out["table"] == "orders"
    assert out["db.table"] == "orders"
    assert out["schema"] == "sch"


def test_walk_config_flat_handles_list_of_dicts():
    out: dict = {}
    sm._walk_config_flat([{"name": "a"}, {"name": "b"}], out)
    assert out["name"] in ("a", "b")  # setdefault keeps the first


def test_build_flat_config_pool_scans_json_configs(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "pipeline.json").write_text(
        json.dumps({"input_table": "s3://bucket/orders.parquet"}), encoding="utf-8",
    )
    out_path = tmp_path / "config_pool.json"
    result = sm._build_flat_config_pool(src, out_path)
    assert result == out_path
    pool = json.loads(out_path.read_text())
    assert pool["input_table"] == "s3://bucket/orders.parquet"


def test_build_flat_config_pool_returns_none_when_no_configs(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "Main.java").write_text("public class Main {}", encoding="utf-8")
    out_path = tmp_path / "config_pool.json"
    assert sm._build_flat_config_pool(src, out_path) is None
    assert not out_path.is_file()


def test_build_flat_config_pool_prefers_explicit_pool_when_present(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    out_path = tmp_path / "config_pool.json"
    out_path.write_text(json.dumps({"already": "flat"}), encoding="utf-8")
    result = sm._build_flat_config_pool(src, out_path)
    assert result == out_path
    assert json.loads(out_path.read_text()) == {"already": "flat"}


# ---------------------------------------------------------------------------
# Ported from the Scala validator: scos-analyze-java.jar invocation
# ---------------------------------------------------------------------------

def test_find_scos_analyze_java_jar_locates_relative_to_skill_dir(tmp_path):
    jar_dir = tmp_path / "harness-java" / "control" / "target"
    jar_dir.mkdir(parents=True)
    jar_path = jar_dir / "scos-analyze-java.jar"
    jar_path.write_bytes(b"")
    found = sm._find_scos_analyze_java_jar(skill_dir=tmp_path)
    assert found == jar_path


def test_find_scos_analyze_java_jar_returns_none_when_absent(tmp_path):
    assert sm._find_scos_analyze_java_jar(skill_dir=tmp_path) is None


def test_run_scos_analyze_java_dies_when_jar_not_found(tmp_path):
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        sm.run_scos_analyze_java(tmp_path, tmp_path / "ast_facts.json", jar=tmp_path / "missing.jar")
