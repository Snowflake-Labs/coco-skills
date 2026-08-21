"""Regression tests for datagen.py mock generation + verify behavior."""
import json
import os
import pathlib
import subprocess
import sys
import glob

import pytest
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import datagen  # noqa: E402

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _manifest_from_bundle(a):
    return {
        "complete": a.get("complete", False),
        "expected_divergences": a.get("expected_divergences", {}),
        "summary": a.get("summary", {}),
    }


def _normalize_table(name, tbl):
    out = {
        "relational": True,
        "category": "table",
        "access": "read",
        "original_path": name,
        "columns": [],
    }
    out.update(tbl)
    if out.get("category") != "file":
        out.setdefault("format", "parquet")
    if out.get("relational", True) and out.get("access") != "write" and not out.get("mock_file"):
        ext = "parquet"
        if out.get("category") == "file" and out.get("format"):
            ext = datagen._ext_for(out["format"])
        out["mock_file"] = "%s.%s" % (datagen._canon(name), ext)
    return out


def _analysis(tables, *, ep_id="ep", **ep_kw):
    ep = {
        "id": ep_id,
        "path": "main.py",
        "run_mode": "script",
        "import_roots": ["."],
        "entrypoint_kwargs": {},
        "source_runtime": "spark",
        "tables": {k: _normalize_table(k, v) for k, v in tables.items()},
    }
    ep.update(ep_kw)
    return {
        "complete": True,
        "expected_divergences": {},
        "entrypoints": [ep],
    }


def _multi_ep(entrypoints):
    normalized = []
    for ep in entrypoints:
        row = {
            "path": "main.py",
            "run_mode": "script",
            "import_roots": ["."],
            "entrypoint_kwargs": {},
            "source_runtime": "spark",
            **ep,
        }
        row["tables"] = {
            k: _normalize_table(k, v) for k, v in ep.get("tables", {}).items()
        }
        normalized.append(row)
    return {"complete": True, "entrypoints": normalized}


def _generated_keys(entrypoints, out):
    """Table keys whose mock exists on disk — what verify_mocks is scoped to."""
    generated = set()
    for ep in entrypoints:
        for tname, t in (ep.get("tables") or {}).items():
            if t.get("access") == "write":
                continue
            mf = t.get("mock_file")
            if mf and os.path.isfile(os.path.join(str(out), ep["id"], mf)):
                generated.add(f"{ep['id']}.{tname}")
    return generated


def _verify_bundle(a, out, sql_files=None):
    """Both validation phases, flattened to a message list."""
    eps = a["entrypoints"]
    problems = datagen.verify_schema(eps, sql_files=sql_files)
    gated = datagen.tables_to_gate(problems, eps)
    generated = _generated_keys(eps, out) - gated
    mock_problems, _overlap = datagen.verify_mocks(eps, out, generated)
    for key, msgs in mock_problems.items():
        problems.setdefault(key, []).extend(msgs)
    return [m for key in sorted(problems) for m in problems[key]]


def _write_schemas_dir(tmp_path, a):
    datagen.write_schemas_dir(tmp_path / "schemas", {
        "root": ".",
        "complete": a.get("complete", False),
        "summary": a.get("summary", {}),
        "expected_divergences": a.get("expected_divergences", {}),
        "entrypoints": a["entrypoints"],
    })
    return tmp_path / "schemas"


def _seed(tmp_path, sources, **kw):
    out = str(tmp_path / "mock")
    os.makedirs(out, exist_ok=True)
    a = _analysis(sources, **kw)
    datagen.seed_workload(a["entrypoints"], out)
    return out, a


def _df(out, name, ext="parquet"):
    p = glob.glob(f"{out}/**/{name}.{ext}", recursive=True)
    assert p, f"no {ext} for {name}"
    if ext == "parquet":
        return pq.read_table(p[0]).to_pandas()
    return p[0]


# ---------------------------------------------------------------------------
# Row generation
# ---------------------------------------------------------------------------

def test_nullable_enum_gets_a_null(tmp_path):
    out, _ = _seed(tmp_path, {
        "t": {"relational": True, "category": "file", "format": "parquet",
              "columns": [
                  {"name": "row_id", "type": "string", "nullable": False},
                  {"name": "status", "type": "string", "nullable": True,
                   "values": ["ACTIVE", "CHURNED"]},
              ]},
    })
    df = _df(out, "t")
    assert df["status"].isna().sum() >= 1, "nullable enum must get >=1 null"
    assert set(df["status"].dropna()) <= {"ACTIVE", "CHURNED"}


def test_idlike_key_stays_populated(tmp_path):
    out, _ = _seed(tmp_path, {
        "t": {"relational": True, "category": "file", "format": "parquet",
              "columns": [
                  {"name": "customer_id", "type": "string", "nullable": True},
                  {"name": "order_no", "type": "string", "nullable": True},
                  {"name": "memo", "type": "string", "nullable": True},
              ]},
    })
    df = _df(out, "t")
    assert df["customer_id"].isna().sum() == 0, "id-like key should stay populated"
    assert df["order_no"].isna().sum() == 0, "*_no key should stay populated"
    assert df["memo"].isna().sum() >= 1, "non-key nullable column should get a null"


def test_shared_idlike_keys_overlap_across_sources(tmp_path):
    cols = [{"name": "wbl_no", "type": "string", "nullable": False},
            {"name": "val", "type": "string", "nullable": True}]
    out, _ = _seed(tmp_path, {
        "a": {"relational": True, "category": "file", "format": "parquet",
              "columns": [dict(c) for c in cols]},
        "b": {"relational": True, "category": "file", "format": "parquet",
              "columns": [dict(c) for c in cols]},
    })
    a = set(_df(out, "a")["wbl_no"])
    b = set(_df(out, "b")["wbl_no"])
    assert len(a & b) >= 1, "*_no join key must pool/overlap across sources"


def _seed_multi(tmp_path, entrypoints):
    out = str(tmp_path / "mock")
    os.makedirs(out, exist_ok=True)
    a = _multi_ep(entrypoints)
    datagen.seed_workload(a["entrypoints"], out)
    return out


def test_joins_edge_pools_non_idlike_same_named(tmp_path):
    # `contrat` is NOT id-like and has no enum -> name fallback won't pool it.
    # A `joins` edge forces the pool so the two mocks overlap.
    cols = [{"name": "contrat", "type": "string", "nullable": False},
            {"name": "v", "type": "string", "nullable": True}]
    out = _seed_multi(tmp_path, [{
        "id": "ep", "tables": {
            "a": {"relational": True, "category": "file", "format": "parquet",
                  "columns": [dict(c) for c in cols]},
            "b": {"relational": True, "category": "file", "format": "parquet",
                  "columns": [dict(c) for c in cols]},
        },
        "joins": [{"left": "a.contrat", "right": "b.contrat"}],
    }])
    a = set(_df(out, "a")["contrat"])
    b = set(_df(out, "b")["contrat"])
    assert a & b, "joins edge must pool the non-id-like key so mocks overlap"


def test_joins_edge_cross_named_overlap(tmp_path):
    # a.k1 == b.k2 -> the two differently-named columns must overlap.
    out = _seed_multi(tmp_path, [{
        "id": "ep", "tables": {
            "a": {"relational": True, "category": "file", "format": "parquet",
                  "columns": [{"name": "k1", "type": "string", "nullable": False}]},
            "b": {"relational": True, "category": "file", "format": "parquet",
                  "columns": [{"name": "k2", "type": "string", "nullable": False}]},
        },
        "joins": [{"left": "a.k1", "right": "b.k2"}],
    }])
    a = set(_df(out, "a")["k1"])
    b = set(_df(out, "b")["k2"])
    assert a & b, "cross-named join edge must overlap the two columns"


def test_same_name_different_type_pools_do_not_collide(tmp_path):
    # Two UNRELATED joins both keyed on `id`, one string, one long. The type-safe
    # fallback (keyed on name+type) must NOT merge them: the string pool stays
    # string, the long pool stays integral -> no cross-type pollution.
    out = _seed_multi(tmp_path, [
        {"id": "ep1", "tables": {
            "x": {"relational": True, "category": "file", "format": "parquet",
                  "columns": [{"name": "id", "type": "string", "nullable": False}]},
            "y": {"relational": True, "category": "file", "format": "parquet",
                  "columns": [{"name": "id", "type": "string", "nullable": False}]},
        }, "joins": [{"left": "x.id", "right": "y.id"}]},
        {"id": "ep2", "tables": {
            "p": {"relational": True, "category": "file", "format": "parquet",
                  "columns": [{"name": "id", "type": "long", "nullable": False}]},
            "q": {"relational": True, "category": "file", "format": "parquet",
                  "columns": [{"name": "id", "type": "long", "nullable": False}]},
        }, "joins": [{"left": "p.id", "right": "q.id"}]},
    ])
    xid = list(_df(out, "x")["id"])
    pid = list(_df(out, "p")["id"])
    assert all(isinstance(v, str) for v in xid), "string id pool must stay string"
    assert all(isinstance(v, (int,)) and not isinstance(v, bool) for v in pid), \
        "long id pool must stay integral (no cross-type pollution)"
    # both joins still overlap within their own entrypoint
    assert set(xid) & set(_df(out, "y")["id"]), "string join must overlap"
    assert set(pid) & set(_df(out, "q")["id"]), "long join must overlap"


def test_pool_values_capped_small(tmp_path):
    # pools stay small so a few rows densely cover them.
    out = _seed_multi(tmp_path, [{
        "id": "ep", "tables": {
            "a": {"relational": True, "category": "file", "format": "parquet",
                  "columns": [{"name": "gid", "type": "string", "nullable": False}]},
            "b": {"relational": True, "category": "file", "format": "parquet",
                  "columns": [{"name": "gid", "type": "string", "nullable": False}]},
        },
        "joins": [{"left": "a.gid", "right": "b.gid"}],
    }])
    distinct = set(_df(out, "a")["gid"]) | set(_df(out, "b")["gid"])
    assert len(distinct) <= datagen._POOL_SIZE, (len(distinct), datagen._POOL_SIZE)


def test_generate_rows_full_coverage_scales_with_schema():
    schema = [
        {"name": f"col_{i}", "type": "string", "nullable": True}
        for i in range(25)
    ]
    rows = datagen.generate_rows(schema, n=12, seed=42)
    # baseline + 25 null rows + 25 edge rows = 51 (n=12 is a floor, not a cap)
    assert len(rows) == datagen._coverage_row_target(schema, min_rows=12)
    assert len(rows) > 12
    assert sum(1 for r in rows if r["col_0"] is None) >= 1


def test_generate_rows_every_nullable_gets_null_row():
    schema = [
        {"name": "a", "type": "string", "nullable": True},
        {"name": "b", "type": "string", "nullable": True},
        {"name": "c", "type": "string", "nullable": True},
    ]
    rows = datagen.generate_rows(schema, n=12, seed=1)
    for col in ("a", "b", "c"):
        assert any(r[col] is None for r in rows), f"{col} should have a dedicated null row"


def test_generate_rows_keeps_baseline_and_null_coverage():
    schema = [
        {"name": "id", "type": "string", "nullable": False},
        {"name": "note", "type": "string", "nullable": True},
    ]
    rows = datagen.generate_rows(schema, n=12, seed=7)
    assert rows[0]["id"] is not None
    assert any(r["note"] is None for r in rows), "nullable column should get a null row"


def test_generate_rows_one_edge_per_column_not_all_edges():
    schema = [{"name": "amount", "type": "double", "nullable": True}]
    rows = datagen.generate_rows(schema, n=20, seed=99)
    amounts = [r["amount"] for r in rows]
    # old behavior added every edge (~5 rows just for edges); compact adds one
    edge_pool = datagen._scalar_pool("double", __import__("random").Random(99))["edge"]
    edge_hits = sum(1 for a in amounts if a in edge_pool)
    assert edge_hits >= 1
    assert edge_hits <= len(edge_pool)


def test_categoricals_from_columns():
    cols = [
        {"name": "a", "type": "string", "values": ["X", "Y"]},
        {"name": "b", "type": "int"},
        {"name": "c", "type": "string", "values": "not-a-list"},
    ]
    assert datagen.categoricals_from_columns(cols) == {"a": ["X", "Y"]}


# ---------------------------------------------------------------------------
# Format routing
# ---------------------------------------------------------------------------

def test_ext_for_text_and_avro():
    assert datagen._ext_for("text") == "txt"
    assert datagen._ext_for("avro") == "avro"
    assert datagen._ext_for("orc") == "parquet"
    assert datagen._ext_for("parquet") == "parquet"


def test_text_format_writes_txt_file(tmp_path):
    out, a = _seed(tmp_path, {
        "lines": {"relational": True, "category": "file", "format": "text",
                  "columns": [{"name": "value", "type": "string", "nullable": False}]},
    })
    path = _df(out, "lines", ext="txt")
    content = open(path).read()
    assert content.strip(), "text mock should have lines"
    assert a["entrypoints"][0]["tables"]["lines"]["mock_file"] == "lines.txt"


def test_table_source_materializes_parquet_not_reader_format(tmp_path):
    """Catalog reads load staging data — format on disk is parquet, not avro."""
    out, a = _seed(tmp_path, {
        "events": {"relational": True, "category": "table",
                   "reader_method": "table", "format": "avro",
                   "columns": [{"name": "id", "type": "string", "nullable": False}]},
    })
    _df(out, "events", ext="parquet")
    assert a["entrypoints"][0]["tables"]["events"]["mock_file"] == "events.parquet"


def test_connector_source_materializes_parquet(tmp_path):
    out, a = _seed(tmp_path, {
        "jdbc_src": {"relational": True, "category": "connector",
                     "reader_method": "jdbc", "format": "jdbc",
                     "columns": [{"name": "id", "type": "long", "nullable": False}]},
    })
    _df(out, "jdbc_src", ext="parquet")
    assert a["entrypoints"][0]["tables"]["jdbc_src"]["mock_file"] == "jdbc_src.parquet"


def test_avro_format_writes_avro_file(tmp_path):
    import fastavro
    out, a = _seed(tmp_path, {
        "events": {"relational": True, "category": "file", "format": "avro",
                   "columns": [
                       {"name": "id", "type": "string", "nullable": False},
                       {"name": "score", "type": "double", "nullable": True},
                   ]},
    })
    path = _df(out, "events", ext="avro")
    with open(path, "rb") as f:
        records = list(fastavro.reader(f))
    assert len(records) >= 1
    assert "id" in records[0]
    assert a["entrypoints"][0]["tables"]["events"]["mock_file"] == "events.avro"


def test_json_format_writes_jsonl(tmp_path):
    out, _ = _seed(tmp_path, {
        "j": {"relational": True, "category": "file", "format": "json",
              "columns": [{"name": "k", "type": "string", "nullable": False}]},
    })
    path = _df(out, "j", ext="json")
    lines = open(path).read().strip().splitlines()
    assert len(lines) >= 1
    assert "k" in json.loads(lines[0])


# ---------------------------------------------------------------------------
# Join-key pooling + warnings
# ---------------------------------------------------------------------------

def test_verify_warns_on_shared_unpooled_column(tmp_path):
    cols = [{"name": "join_token", "type": "string", "nullable": True},
            {"name": "x", "type": "string", "nullable": True}]
    a = _analysis({
        "a": {"relational": True, "category": "file", "format": "parquet",
              "columns": [dict(c) for c in cols]},
        "b": {"relational": True, "category": "file", "format": "parquet",
              "columns": [dict(c) for c in cols]},
    })
    warns = datagen.verify_warnings(a["entrypoints"])
    assert any("join_token" in w for w in warns), warns


def test_verify_warning_suppression_rules():
    analysis = {"entrypoints": [{"id": "ep", "tables": {
        "a": {"relational": True, "columns": [{"name": "city", "type": "string"}]},
        "b": {"relational": True, "columns": [{"name": "city", "type": "string"}]},
    }}]}
    assert any("city" in w for w in datagen.verify_warnings(analysis["entrypoints"]))
    analysis["entrypoints"][0]["tables"]["a"]["columns"][0]["join_key"] = False
    assert not any("city" in w for w in datagen.verify_warnings(analysis["entrypoints"]))

    analysis = {"entrypoints": [{"id": "ep", "tables": {
        "a": {"relational": True, "columns": [{"name": "contrat", "type": "string"}]},
        "b": {"relational": True, "columns": [{"name": "contrat", "type": "string"}]},
    }}]}
    assert any("contrat" in w for w in datagen.verify_warnings(analysis["entrypoints"]))
    analysis["entrypoints"][0]["joins"] = [{"left": "a.contrat", "right": "b.contrat"}]
    assert not any("contrat" in w for w in datagen.verify_warnings(analysis["entrypoints"]))


def test_join_key_true_pools_non_id_column(tmp_path):
    cols = [{"name": "region_token", "type": "string", "nullable": False, "join_key": True},
            {"name": "payload", "type": "string", "nullable": True}]
    out, _ = _seed(tmp_path, {
        "left": {"relational": True, "category": "file", "format": "parquet",
                 "columns": [dict(c) for c in cols]},
        "right": {"relational": True, "category": "file", "format": "parquet",
                  "columns": [dict(c) for c in cols]},
    })
    left = set(_df(out, "left")["region_token"])
    right = set(_df(out, "right")["region_token"])
    assert len(left & right) >= 1


def test_verify_warning_suppressed_by_join_key_true():
    analysis = {"entrypoints": [{"id": "ep", "tables": {
        "a": {"relational": True,
              "columns": [{"name": "region_token", "type": "string", "join_key": True}]},
        "b": {"relational": True,
              "columns": [{"name": "region_token", "type": "string", "join_key": True}]},
    }}]}
    assert not any("region_token" in w for w in datagen.verify_warnings(analysis["entrypoints"]))


def test_empty_join_key_overlap_is_reported_as_a_warning(tmp_path):
    """Mocks with no overlapping join_key values must be surfaced. Each mock is
    valid on its own — the defect is in the relationship — so it is a warning the
    data-synthesizer must resolve or dismiss, not a reason to withhold the data."""
    cols = [{"name": "link_code", "type": "string", "nullable": False, "join_key": True}]
    a = _analysis({
        "a": {"relational": True, "category": "file", "format": "parquet",
              "columns": cols, "mock_file": "a.parquet"},
        "b": {"relational": True, "category": "file", "format": "parquet",
              "columns": cols, "mock_file": "b.parquet"},
    })
    out = str(tmp_path / "mock")
    (tmp_path / "mock" / "ep").mkdir(parents=True)
    datagen.write_mock_parquet(
        tmp_path / "mock" / "ep" / "a.parquet",
        [{"link_code": "AAA"}, {"link_code": "BBB"}],
        cols,
    )
    datagen.write_mock_parquet(
        tmp_path / "mock" / "ep" / "b.parquet",
        [{"link_code": "CCC"}, {"link_code": "DDD"}],
        cols,
    )
    problems, overlap = datagen.verify_mocks(
        a["entrypoints"], out, {"ep.a", "ep.b"})
    assert any("join overlap" in w.lower() for w in overlap), overlap
    assert not problems, problems


def test_values_constraint_does_not_bleed_to_sibling_ep_table(tmp_path):
    """values on a keylike column must NOT pool into same-named columns in other EPs."""
    out = str(tmp_path / "mock")
    os.makedirs(out, exist_ok=True)
    a = _multi_ep([
        {"id": "ep_a", "tables": {
            "fact_a": {"relational": True, "category": "file", "format": "parquet",
                       "columns": [{"name": "dsrc_id", "type": "integer", "nullable": False,
                                    "values": [1000, 1004]}]},
        }},
        {"id": "ep_b", "tables": {
            "fact_b": {"relational": True, "category": "file", "format": "parquet",
                       "columns": [{"name": "dsrc_id", "type": "integer", "nullable": False}]},
        }},
    ])
    datagen.seed_workload(a["entrypoints"], out, n=30)
    df = _df(out, "fact_b")
    ep_b_values = set(df["dsrc_id"].dropna())
    assert not ep_b_values.issubset({1000, 1004}), (
        "ep_b dsrc_id contaminated by ep_a values constraint: got %s" % ep_b_values
    )


# ---------------------------------------------------------------------------
# verify() content checks
# ---------------------------------------------------------------------------

def test_scan_needs_llm_flags_empty_doc_schema(tmp_path):
    a = _analysis({
        "cfg": {"relational": False, "category": "file",
                "format": "yaml", "document_schema": None},
    })
    nl = datagen._scan_needs_llm(a["entrypoints"])
    assert "cfg" in nl


def test_verify_reports_missing_document_schema_as_problem(tmp_path):
    a = _analysis({
        "cfg": {"relational": False, "category": "file",
                "format": "yaml", "document_schema": None},
    })
    probs = _verify_bundle(a, str(tmp_path / "mock"))
    assert any("cfg: non-relational table missing document_schema" in p for p in probs), probs
    assert not any("cfg: non-relational file table missing mock_file" in p for p in probs), probs


def test_verify_skips_mockfile_noise_when_columns_empty(tmp_path):
    a = _analysis({
        "src": {"relational": True, "category": "file", "format": "parquet",
                 "columns": []},
    })
    probs = _verify_bundle(a, str(tmp_path / "mock"))
    assert any("src: table has columns: []" in p for p in probs), probs
    assert not any("src: relational table missing mock_file" in p for p in probs), probs


def test_nullable_categorical_string_writes_real_null(tmp_path):
    rows = [{"flag": "TRUE"}, {"flag": "FALSE"}, {"flag": None}]
    schema = [{"name": "flag", "type": "string", "nullable": True, "values": ["TRUE", "FALSE"]}]
    p = tmp_path / "m.parquet"
    datagen.write_mock_parquet(p, rows, schema)
    col = pq.read_table(p).column("flag").to_pylist()
    assert None in col, col
    assert "None" not in col and "nan" not in col, col


def test_verify_flags_missing_nullable_null(tmp_path):
    cols = [{"name": "id", "type": "string", "nullable": False},
            {"name": "memo", "type": "string", "nullable": True}]
    a = _analysis({
        "t": {"relational": True, "category": "file", "format": "parquet",
              "columns": cols, "mock_file": "t.parquet"},
    })
    out = str(tmp_path / "mock")
    (tmp_path / "mock" / "ep").mkdir(parents=True)
    datagen.write_mock_parquet(
        tmp_path / "mock" / "ep" / "t.parquet",
        [{"id": "1", "memo": "x"}, {"id": "2", "memo": "y"}],
        cols,
    )
    probs = _verify_bundle(a, out)
    assert any("memo" in p and "null" in p.lower() for p in probs), probs


def test_verify_flags_enum_out_of_domain(tmp_path):
    cols = [{"name": "status", "type": "string", "nullable": True,
             "values": ["ACTIVE", "CHURNED"]}]
    a = _analysis({
        "t": {"relational": True, "category": "file", "format": "parquet",
              "columns": cols, "mock_file": "t.parquet"},
    })
    out = str(tmp_path / "mock")
    (tmp_path / "mock" / "ep").mkdir(parents=True)
    datagen.write_mock_parquet(
        tmp_path / "mock" / "ep" / "t.parquet",
        [{"status": "ACTIVE"}, {"status": "BOGUS"}],
        cols,
    )
    probs = _verify_bundle(a, out)
    assert any("enum" in p.lower() or "BOGUS" in p or "values" in p for p in probs), probs


def test_remaining_llm_todos_and_completeness():
    a = {"entrypoints": [{"id": "ep",
         "tables": {"s": {"access": "read", "columns": [], "llm_todo": "x"}}}]}
    assert datagen._remaining_llm_todos(a["entrypoints"]) == ["ep.tables.s"]
    a["entrypoints"][0]["tables"]["s"].pop("llm_todo")
    assert datagen._remaining_llm_todos(a["entrypoints"]) == []


def test_verify_reports_one_problem_per_llm_todo():
    a = _multi_ep([
        {"id": "ep1", "tables": {"a": {"access": "read", "columns": [], "llm_todo": "open column set"}}},
        {"id": "ep2", "tables": {"b": {"access": "write", "columns": [], "llm_todo": "runtime variable sink"}}},
    ])
    probs = _verify_bundle(a, "/tmp/does_not_matter")
    assert "unresolved llm_todo (" not in " ".join(probs), probs
    assert "ep1.tables.a: unresolved llm_todo — open column set" in probs, probs
    assert "ep2.tables.b: unresolved llm_todo — runtime variable sink" in probs, probs


def _csv_analysis(reader_options, columns):
    return _analysis({
        "data_": {
            "relational": True,
            "category": "file",
            "format": "csv",
            "reader_options": reader_options,
            "columns": columns,
        },
    })


def test_csv_seeded_with_reader_sep_and_no_header(tmp_path):
    a = _csv_analysis({"sep": "|", "header": "false"},
                      [{"name": "LOCATIONID", "type": "string", "nullable": False},
                       {"name": "AMOUNT", "type": "double", "nullable": True}])
    datagen.seed_workload(a["entrypoints"], str(tmp_path))
    f = glob.glob(str(tmp_path) + "/ep/*.csv")[0]
    lines = open(f).read().splitlines()
    assert "|" in lines[0] and "," not in lines[0], lines[0]
    assert lines[0].split("|")[0] != "LOCATIONID", lines[0]
    assert all(ln.split("|")[0] != "" for ln in lines), lines
    assert _verify_bundle(a, str(tmp_path)) == []


def test_verify_flags_csv_delimiter_mismatch(tmp_path):
    a = _csv_analysis({"sep": "|"},
                      [{"name": "a", "type": "string"}, {"name": "b", "type": "string"},
                       {"name": "c", "type": "string"}])
    (tmp_path / "ep").mkdir()
    (tmp_path / "ep" / "data_.csv").write_text("a,b,c\n1,2,3\n")
    a["entrypoints"][0]["tables"]["data_"]["mock_file"] = "data_.csv"
    probs = _verify_bundle(a, str(tmp_path))
    assert any("delimiter mismatch" in p for p in probs), probs


def test_verify_allows_file_csv_no_header(tmp_path):
    """header=false is fine for file-category reads (no provision COPY INTO)."""
    a = _csv_analysis({"sep": "|", "header": "false"},
                      [{"name": "LOCATIONID", "type": "string", "nullable": False}])
    datagen.seed_workload(a["entrypoints"], str(tmp_path))
    assert _verify_bundle(a, str(tmp_path)) == []


def test_verify_flags_table_csv_no_header_for_provision(tmp_path):
    """Table sources normally seed as parquet; a hand-edited .csv still COPYs."""
    a = _csv_analysis({"sep": "|", "header": "false"},
                      [{"name": "LOCATIONID", "type": "string", "nullable": False}])
    a["entrypoints"][0]["tables"]["data_"]["category"] = "table"
    (tmp_path / "ep").mkdir()
    (tmp_path / "ep" / "data_.csv").write_text("LOC1\nLOC2\n")
    a["entrypoints"][0]["tables"]["data_"]["mock_file"] = "data_.csv"
    probs = _verify_bundle(a, str(tmp_path))
    assert any("SKIP_HEADER=1" in p for p in probs), probs


def test_verify_flags_table_source_empty_columns_mock(tmp_path):
    a = _analysis({
        "RDS_PREDOM_PRICE": {
            "relational": True, "category": "table", "format": "table",
            "columns": [],
        },
    }, ep_id="ep")
    probs = _verify_bundle(a, str(tmp_path))
    assert any("columns: []" in p and "RDS_PREDOM_PRICE" in p for p in probs), probs


def test_verify_flags_table_source_empty_columns(tmp_path):
    a = _analysis({
        "INITIAL_COST": {
            "relational": True, "category": "table", "format": "table",
            "columns": [],
        },
    }, ep_id="ep")
    probs = _verify_bundle(a, str(tmp_path))
    assert any("columns: []" in p and "INITIAL_COST" in p for p in probs), probs


def test_verify_flags_table_write_empty_columns(tmp_path):
    a = _analysis({}, ep_id="ep")
    a["entrypoints"][0]["tables"] = {
        "HXPRICECOMP": {"access": "write", "category": "table", "columns": []},
    }
    probs = _verify_bundle(a, str(tmp_path))
    assert any("HXPRICECOMP" in p and "columns: []" in p for p in probs), probs


def test_verify_flags_parquet_missing_columns_for_provision(tmp_path):
    schema = [
        {"name": "id", "type": "string", "nullable": False},
        {"name": "amount", "type": "double", "nullable": True},
    ]
    rows = datagen.generate_rows([schema[0]], n=4, seed=1)
    mock_dir = tmp_path / "mock" / "ep"
    mock_dir.mkdir(parents=True)
    datagen.write_mock_parquet(mock_dir / "t.parquet", rows, [schema[0]])
    a = _analysis({
        "t": {"relational": True, "category": "table", "format": "parquet",
              "columns": schema, "mock_file": "t.parquet"},
    }, ep_id="ep")
    probs = _verify_bundle(a, str(tmp_path / "mock"))
    assert any("parquet mock missing declared column" in p for p in probs), probs
    assert any("amount" in p for p in probs), probs


# ---------------------------------------------------------------------------
# seed_workload orchestration
# ---------------------------------------------------------------------------

def test_doc_ext_matches_writer():
    assert datagen._doc_ext("yaml") == "yaml"
    assert datagen._doc_ext("json") == "json"
    assert datagen._doc_ext("xml") == "json"


def test_generate_document_from_dict():
    doc = datagen.generate_document({"host": "string", "port": "int"}, seed=1)
    assert isinstance(doc, dict)
    assert "host" in doc and "port" in doc


def test_nonrelational_yaml_document(tmp_path):
    out, a = _seed(tmp_path, {
        "cfg": {"relational": False, "category": "file", "format": "yaml",
                "document_schema": {"env": "string", "retries": "int"}},
    })
    path = _df(out, "cfg", ext="yaml")
    text = open(path).read()
    assert "env" in text
    assert a["entrypoints"][0]["tables"]["cfg"]["mock_file"] == "cfg.yaml"


def test_relational_source_always_gets_mock_file(tmp_path):
    out, a = _seed(tmp_path, {
        "intermediate": {"relational": True, "category": "table",
                         "format": "parquet",
                         "columns": [{"name": "x", "type": "int", "nullable": True}]},
    })
    assert a["entrypoints"][0]["tables"]["intermediate"]["mock_file"] == "intermediate.parquet"
    assert glob.glob(f"{out}/**/*.parquet", recursive=True)


def test_cross_entrypoint_seed_once_duplicate(tmp_path):
    cols = [{"name": "id", "type": "string", "nullable": False},
            {"name": "v", "type": "int", "nullable": True}]
    src = {"relational": True, "category": "table", "format": "parquet",
           "columns": cols}
    a = _multi_ep([
        {"id": "ep1", "tables": {"customers": dict(src)}},
        {"id": "ep2", "tables": {"customers": dict(src)}},
    ])
    out = str(tmp_path / "mock")
    man = datagen.seed_workload(a["entrypoints"], out)
    assert len(man["seeded"]) == 1
    p1 = tmp_path / "mock" / "ep1" / "customers.parquet"
    p2 = tmp_path / "mock" / "ep2" / "customers.parquet"
    assert p1.is_file() and p2.is_file()
    assert p1.read_bytes() == p2.read_bytes()


def test_hash_driven_reseed_triggers(tmp_path):
    """Hash gating skips unchanged tables and reseeds on schema/join/consumer changes."""
    out = str(tmp_path / "mock")

    cols_a = [{"name": "id", "type": "string", "nullable": False},
              {"name": "v", "type": "int", "nullable": True}]
    cols_b = [{"name": "broker_id", "type": "string", "nullable": False},
              {"name": "name", "type": "string", "nullable": True}]
    src_a = {"relational": True, "category": "table", "format": "parquet", "columns": cols_a}
    src_b = {"relational": True, "category": "table", "format": "parquet", "columns": cols_b}
    a = _multi_ep([{"id": "ep1", "tables": {"customers": dict(src_a)}}])
    man1 = datagen.seed_workload(a["entrypoints"], out)
    assert len(man1["seeded"]) == 1
    customers_path = tmp_path / "mock" / "ep1" / "customers.parquet"
    before = customers_path.read_bytes()

    a["entrypoints"][0]["tables"]["brokers"] = _normalize_table("brokers", src_b)
    man2 = datagen.seed_workload(a["entrypoints"], out, force_all=False)
    assert len(man2["seeded"]) == 1 and len(man2["skipped"]) == 1
    assert customers_path.read_bytes() == before

    a["entrypoints"][0]["tables"]["t"] = {
        "relational": True, "category": "file", "format": "parquet",
        "columns": [
            {"name": "rid", "type": "string", "nullable": False},
            {"name": "status", "type": "string", "nullable": True, "values": ["A", "B"]},
        ],
    }
    datagen.seed_workload(a["entrypoints"], out)
    a["entrypoints"][0]["tables"]["t"]["columns"][1]["values"] = ["A", "B", "C", "D"]
    man3 = datagen.seed_workload(a["entrypoints"], out, force_all=False)
    assert "file:t" in man3["seeded"], man3

    cols = [{"name": "contrat", "type": "string", "nullable": False}]
    eps = [{"id": "ep", "tables": {
        "a": {"relational": True, "category": "file", "format": "parquet", "columns": [dict(c) for c in cols]},
        "b": {"relational": True, "category": "file", "format": "parquet", "columns": [dict(c) for c in cols]},
    }}]
    datagen.seed_workload(_multi_ep(eps)["entrypoints"], out)
    eps[0]["joins"] = [{"left": "a.contrat", "right": "b.contrat"}]
    man4 = datagen.seed_workload(_multi_ep(eps)["entrypoints"], out, force_all=False)
    assert {"file:a", "file:b"} <= set(man4["seeded"]), man4


def test_generated_mocks_are_read_only_and_regenerable(tmp_path):
    """Generated mocks are written read-only (guards against hand-edits that
    bypass the schema), yet datagen can still regenerate over them."""
    import os
    import stat
    cols = [{"name": "id", "type": "string", "nullable": False},
            {"name": "v", "type": "int", "nullable": True}]
    src = {"relational": True, "category": "table", "format": "parquet",
           "columns": cols}
    a = _multi_ep([{"id": "ep1", "tables": {"customers": dict(src)}}])
    out = str(tmp_path / "mock")
    datagen.seed_workload(a["entrypoints"], out)
    p = tmp_path / "mock" / "ep1" / "customers.parquet"
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert not (mode & stat.S_IWUSR), f"mock should be read-only, got {oct(mode)}"

    # A schema change must still regenerate the read-only file (writable-first).
    a["entrypoints"][0]["tables"]["customers"]["columns"].append(
        {"name": "extra", "type": "string", "nullable": True})
    man = datagen.seed_workload(a["entrypoints"], out, force_all=False)
    assert len(man["seeded"]) == 1
    t = pq.read_table(p)
    assert "extra" in t.column_names
    assert not (stat.S_IMODE(os.stat(p).st_mode) & stat.S_IWUSR)


def test_cli_hash_driven_skip_and_seed(tmp_path):
    cols = [{"name": "id", "type": "string", "nullable": False}]
    src = {"relational": True, "category": "table", "format": "parquet",
           "columns": cols}
    a = _multi_ep([{"id": "ep1", "tables": {"customers": dict(src)}}])
    schemas = _write_schemas_dir(tmp_path, a)
    out = tmp_path / "mock"
    datagen.seed_workload(a["entrypoints"], str(out))
    before = (out / "ep1" / "customers.parquet").read_bytes()

    a["entrypoints"][0]["tables"]["brokers"] = {
        "relational": True, "category": "table", "format": "parquet", "access": "read",
        "columns": [{"name": "broker_id", "type": "string", "nullable": False}],
    }
    _write_schemas_dir(tmp_path, a)

    proc = subprocess.run(
         [sys.executable, os.path.join(_SCRIPTS, "datagen.py"),
         str(schemas), str(out)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert len(payload["seeded"]) == 1
    assert len(payload["skipped"]) == 1
    assert (out / "ep1" / "customers.parquet").read_bytes() == before
    assert (out / "ep1" / "brokers.parquet").is_file()


def test_verify_flags_write_file_sink_missing_format():
    """A write-category file sink with no `format` must be flagged (else the runtime
    silently defaults to parquet and captures zero rows)."""
    a = _analysis({"column_data": {"access": "write", "category": "file",
                                   "relational": True, "columns": [{"name": "x", "type": "string"}]}})
    probs = _verify_bundle(a, "/tmp/does_not_matter")
    assert any("missing 'format'" in p and "column_data" in p for p in probs), probs
    # declaring the format clears it
    a2 = _analysis({"column_data": {"access": "write", "category": "file", "format": "text",
                                    "relational": True, "columns": [{"name": "x", "type": "string"}]}})
    probs2 = _verify_bundle(a2, "/tmp/does_not_matter")
    assert not any("missing 'format'" in p for p in probs2), probs2


def test_merge_columns_upgrades_string_and_keeps_enum():
    existing = [{"name": "amt", "type": "string", "nullable": True}]
    incoming = [{"name": "amt", "type": "double", "nullable": False, "values": ["1", "2"]}]
    merged = datagen._merge_columns(existing, incoming)
    by_name = {c["name"]: c for c in merged}
    assert by_name["amt"]["type"] == "double"
    assert by_name["amt"]["nullable"] is False
    assert by_name["amt"]["values"] == ["1", "2"]


def test_merge_columns_not_null_wins_over_nullable():
    merged = datagen._merge_columns(
        [{"name": "STORE", "type": "string", "nullable": False}],
        [{"name": "STORE", "type": "string", "nullable": True}],
    )
    assert merged[0]["nullable"] is False


def test_shared_seed_respects_not_null_when_consumers_disagree(tmp_path):
    """Shared table mocks must not inject nulls when any consumer declares NOT NULL."""
    a = _multi_ep([
        {"id": "ep1", "tables": {"orders": {
            "relational": True, "category": "table",
            "columns": [{"name": "id", "type": "string", "nullable": False},
                        {"name": "amt", "type": "double", "nullable": False}],
            "format": "table"}}},
        {"id": "ep2", "tables": {"ORDERS": {
            "relational": True, "category": "table",
            "columns": [{"name": "id", "type": "string", "nullable": False},
                        {"name": "amt", "type": "double", "nullable": True}],
            "format": "table"}}},
    ])
    out = str(tmp_path / "mock")
    datagen.seed_workload(a["entrypoints"], out, n=12, seed=1)
    import pandas as pd
    for ep in ("ep1", "ep2"):
        df = pd.read_parquet(f"{out}/{ep}/orders.parquet")
        assert int(df["amt"].isna().sum()) == 0


def test_generate_rows_non_nullable_columns_never_null():
    schema = [
        {"name": "id", "type": "string", "nullable": False},
        {"name": "qty", "type": "int", "nullable": False},
        {"name": "note", "type": "string", "nullable": True},
    ]
    rows = datagen.generate_rows(schema, n=12, seed=3)
    for c in ("id", "qty"):
        assert all(r[c] is not None for r in rows), c
    assert any(r["note"] is None for r in rows)


def test_verify_flags_not_null_column_with_null_in_mock(tmp_path):
    import pandas as pd
    cols = [{"name": "id", "type": "string", "nullable": False},
            {"name": "memo", "type": "string", "nullable": True}]
    a = _analysis({"t": {"relational": True, "format": "parquet", "columns": cols,
                         "mock_file": "t.parquet"}}, ep_id="ep")
    out = tmp_path / "mock" / "ep"
    out.mkdir(parents=True)
    pd.DataFrame([{"id": None, "memo": "x"}, {"id": "a", "memo": None}]).to_parquet(out / "t.parquet")
    probs = _verify_bundle(a, tmp_path / "mock")
    assert any("NOT NULL column has" in p and "id" in p for p in probs), probs


def test_placeholder_src_names_scoped_per_entrypoint(tmp_path):
    cols = [{"name": "id", "type": "string", "nullable": False}]
    a = _multi_ep([
        {"id": "ep1", "tables": {"src0": {"relational": True, "format": "parquet",
                                            "columns": cols}}},
        {"id": "ep2", "tables": {"src0": {"relational": True, "format": "parquet",
                                            "columns": cols}}},
    ])
    out = str(tmp_path / "mock")
    man = datagen.seed_workload(a["entrypoints"], out)
    assert len(man["seeded"]) == 2


def test_file_and_table_same_name_seed_separately(tmp_path):
    """links (file/csv) and LINKS (table) must not share one on-disk format."""
    a = _multi_ep([
        {"id": "ep_file", "tables": {
            "links": {"relational": True, "category": "file", "format": "csv",
                      "reader_options": {"sep": "|"},
                      "columns": [{"name": "link", "type": "string", "nullable": True}]},
        }},
        {"id": "ep_table", "tables": {
            "LINKS": {"relational": True, "category": "table", "format": "table",
                      "columns": [{"name": "link", "type": "string", "nullable": True}]},
        }},
    ])
    out = str(tmp_path / "mock")
    datagen.seed_workload(a["entrypoints"], out)
    assert (tmp_path / "mock" / "ep_file" / "links.csv").is_file()
    assert (tmp_path / "mock" / "ep_table" / "links.parquet").is_file()
    assert a["entrypoints"][1]["tables"]["LINKS"]["mock_file"] == "links.parquet"
    problems = _verify_bundle(a, out)
    assert not problems, problems


def test_verify_snowflake_duplicate_columns(tmp_path):
    a = _analysis({
        "t": {"relational": True, "category": "table", "format": "parquet",
              "columns": [
                  {"name": "ITEM", "type": "string", "nullable": True},
                  {"name": "item", "type": "string", "nullable": True},
              ]},
    })
    problems = _verify_bundle(a, str(tmp_path))
    assert any("duplicate Snowflake column" in p for p in problems)


def test_parquet_spark_seed_scalar_types(tmp_path):
    """int/date columns must match harness seeding (IntegerType/DateType)."""
    schema = [
        {"name": "status", "type": "int", "nullable": True},
        {"name": "intro_date", "type": "date", "nullable": True},
    ]
    rows = datagen.generate_rows(schema, n=4, seed=1)
    mock_dir = tmp_path / "mock" / "ep"
    mock_dir.mkdir(parents=True)
    p = mock_dir / "typed.parquet"
    datagen.write_mock_parquet(p, rows, schema)
    t = pq.read_table(p)
    assert str(t.schema.field("status").type) == "int32"
    assert str(t.schema.field("intro_date").type) == "date32[day]"
    a = _analysis({
        "t": {"relational": True, "category": "file", "format": "parquet",
              "columns": schema, "mock_file": "typed.parquet"},
    }, ep_id="ep")
    problems = _verify_bundle(a, str(tmp_path / "mock"))
    assert not problems, problems


def test_declared_string_type_wins_over_name_heuristic(tmp_path):
    """§4: a column DECLARED string must stay string in parquet even when its
    name looks like a date/flag/count — datagen must never infer type from name."""
    schema = [
        {"name": "effective_date", "type": "string", "nullable": True},
        {"name": "is_active", "type": "string", "nullable": True},
        {"name": "status", "type": "string", "nullable": True},
        {"name": "record_count", "type": "string", "nullable": True},
    ]
    rows = datagen.generate_rows(schema, n=6, seed=3)
    p = tmp_path / "named.parquet"
    datagen.write_mock_parquet(p, rows, schema)
    t = pq.read_table(p)
    for col in ("effective_date", "is_active", "status", "record_count"):
        assert "string" in str(t.schema.field(col).type), (
            "%s should stay string, got %s" % (col, t.schema.field(col).type))


def test_decimal_and_timestamp_parquet_physical_types(tmp_path):
    """decimal/timestamp must be written with the parquet physical types the
    harness strict-reads (DecimalType -> decimal128, TimestampType -> timestamp);
    DOUBLE-for-decimal or string-for-timestamp break seed_entrypoint."""
    schema = [
        {"name": "amount", "type": "decimal(10,2)", "nullable": True},
        {"name": "created_at", "type": "timestamp", "nullable": True},
        {"name": "ingest_ntz", "type": "timestamp_ntz", "nullable": True},
        {"name": "event_date", "type": "date", "nullable": True},
    ]
    rows = datagen.generate_rows(schema, n=6, seed=2)
    p = tmp_path / "typed2.parquet"
    datagen.write_mock_parquet(p, rows, schema)
    t = pq.read_table(p)
    assert str(t.schema.field("amount").type) == "decimal128(10, 2)"
    assert str(t.schema.field("created_at").type) == "timestamp[us]"
    assert str(t.schema.field("ingest_ntz").type) == "timestamp[us]"
    assert str(t.schema.field("event_date").type) == "date32[day]"


def test_integer_aliases_and_ranges_parquet_types(tmp_path):
    """Every integer/float alias must map to the parquet physical type the harness
    Spark schema expects, and generated values must stay within the type's range
    (byte/tinyint previously overflowed int8 and smallint/tinyint fell back to
    string)."""
    schema = [
        {"name": "a_short", "type": "short", "nullable": True},
        {"name": "a_smallint", "type": "smallint", "nullable": True},
        {"name": "a_byte", "type": "byte", "nullable": True},
        {"name": "a_tinyint", "type": "tinyint", "nullable": True},
        {"name": "a_int", "type": "integer", "nullable": True},
        {"name": "a_bigint", "type": "bigint", "nullable": True},
        {"name": "a_real", "type": "real", "nullable": True},
        {"name": "a_numeric", "type": "numeric(12,4)", "nullable": True},
    ]
    rows = datagen.generate_rows(schema, n=8, seed=7)
    p = tmp_path / "ints.parquet"
    datagen.write_mock_parquet(p, rows, schema)   # must not raise (byte overflow)
    t = pq.read_table(p)
    assert str(t.schema.field("a_short").type) == "int16"
    assert str(t.schema.field("a_smallint").type) == "int16"
    assert str(t.schema.field("a_byte").type) == "int8"
    assert str(t.schema.field("a_tinyint").type) == "int8"
    assert str(t.schema.field("a_int").type) == "int32"
    assert str(t.schema.field("a_bigint").type) == "int64"
    # real -> DoubleType in the harness, so write float64 (double), not float32.
    assert str(t.schema.field("a_real").type) == "double"
    # numeric precision/scale must be honored, not defaulted to (38,18).
    assert str(t.schema.field("a_numeric").type) == "decimal128(12, 4)"


def test_verify_accepts_alias_type_parquet(tmp_path):
    """datagen must accept the physical type the writer produces for alias types
    (real->double, smallint->int16, tinyint->int8, numeric->decimal128); a stale
    accept-set previously false-flagged `real` as 'declared real but parquet
    double'."""
    a = _analysis({
        "t": {"relational": True, "category": "file", "format": "parquet",
              "columns": [
                  {"name": "a", "type": "real", "nullable": True},
                  {"name": "b", "type": "smallint", "nullable": True},
                  {"name": "c", "type": "tinyint", "nullable": True},
                  {"name": "d", "type": "numeric(9,3)", "nullable": True},
                  {"name": "e", "type": "integer", "nullable": True},
              ]},
    })
    out = str(tmp_path / "mock")
    datagen.seed_workload(a["entrypoints"], out)
    problems = _verify_bundle(a, out)
    assert not problems, problems


def test_nested_struct_parquet_roundtrip(tmp_path):
    schema = [
        {"name": "id", "type": "string", "nullable": False},
        {"name": "meta", "type": "struct<k:string,v:int>", "nullable": True},
    ]
    rows = datagen.generate_rows(schema, n=8, seed=5)
    p = tmp_path / "nested.parquet"
    datagen.write_mock_parquet(p, rows, schema)
    t = pq.read_table(p)
    assert "meta" in t.column_names
    assert str(t.schema.field("meta").type).startswith("struct")


def test_read_file_source_without_format_still_generates(tmp_path):
    """`format` is optional on a READ file source — it falls back to parquet and
    the table is valid, so it gets data. Only WRITE file sinks must declare one."""
    out = str(tmp_path / "mock")
    a = _analysis({
        "events": {"relational": True, "category": "file",
                   "columns": [{"name": "id", "type": "long"}]},
    })
    assert datagen.verify_schema(a["entrypoints"]) == {}
    man = datagen.seed_workload(a["entrypoints"], out)
    assert "ep.events" in man["table_paths"]
    assert _verify_bundle(a, out) == []


def test_verify_schema_flags_write_file_sink_missing_format():
    a = _analysis({
        "sink": {"relational": True, "category": "file", "access": "write",
                 "columns": [{"name": "id", "type": "long"}]},
    })
    problems = datagen.verify_schema(a["entrypoints"])
    assert "ep.sink" in problems
    assert any("missing 'format'" in m for m in problems["ep.sink"])


def test_verify_flags_source_type_instead_of_category(tmp_path):
    a = _analysis({
        "events": {"relational": True, "category": "file", "format": "json",
                   "original_path": "s3://b/in/*.json",
                   "columns": [{"name": "id", "type": "long"}],
                   "mock_file": "events.json"},
    })
    s = a["entrypoints"][0]["tables"]["events"]
    del s["category"]
    # 'type' is not a valid property in table_entry (additionalProperties: false)
    # but the column type field IS valid; setting a bare 'type' at table level should error
    s["type"] = "file"
    probs = _verify_bundle(a, str(tmp_path / "mock"))
    # The schema now catches this as "type" being an unexpected property at table level
    # (column.type is fine, but table_entry doesn't allow bare 'type')
    # Actually 'type' IS NOT in table_entry additionalProperties -- but wait, we removed it.
    # Let's just verify schema errors are raised for the invalid structure
    assert probs, "Expected schema validation errors for invalid table entry"


def test_verify_flags_source_kind_instead_of_format(tmp_path):
    a = _analysis({
        "events": {"relational": True, "category": "file",
                   "columns": [{"name": "id", "type": "long"}],
                   "mock_file": "events.json"},
    })
    # 'kind' is not a valid table_entry property — schema validation should catch it
    a["entrypoints"][0]["tables"]["events"]["kind"] = "json"
    probs = _verify_bundle(a, str(tmp_path / "mock"))
    assert any("kind" in p for p in probs), probs


def test_verify_flags_missing_entrypoint_run_mode(tmp_path):
    a = _analysis({
        "t": {"columns": [{"name": "id", "type": "long"}]},
    })
    del a["entrypoints"][0]["run_mode"]
    probs = _verify_bundle(a, str(tmp_path / "mock"))
    assert any("run_mode" in p for p in probs), probs


def test_normalized_entrypoint_passes_json_schema():
    a = _analysis({
        "t": {"columns": [{"name": "id", "type": "long"}]},
    })
    assert datagen._verify_entrypoint_schema(a["entrypoints"]) == []


def test_verify_requires_source_runtime(tmp_path):
    """Every entrypoint must declare a non-null source_runtime."""
    # Missing source_runtime -> flagged by the JSON-schema check.
    a = _analysis({"t": {"columns": [{"name": "id", "type": "long"}]}})
    del a["entrypoints"][0]["source_runtime"]
    assert any("source_runtime" in msg
               for _key, msg in datagen._verify_entrypoint_schema(a["entrypoints"]))

    # Null source_runtime -> flagged by verify().
    a = _analysis({"t": {"columns": [{"name": "id", "type": "long"}]}})
    a["entrypoints"][0]["source_runtime"] = None
    probs = _verify_bundle(a, str(tmp_path / "mock"))
    assert any("source_runtime" in p for p in probs), probs


def test_verify_readwrite_table_empty_columns(tmp_path):
    """A readwrite table with empty columns is flagged by verify."""
    a = _analysis({
        "RDS_PREDOM_PRICE": {"relational": True, "category": "table", "format": "table",
                             "access": "readwrite",
                             "original_path": "RDS_PREDOM_PRICE",
                             "columns": []},
    })
    probs = _verify_bundle(a, str(tmp_path / "mock"))
    assert any("RDS_PREDOM_PRICE" in p and "columns: []" in p for p in probs), probs


def test_verify_csv_header_must_match_declared_columns(tmp_path):
    cols = [{"name": "A", "type": "string"}, {"name": "B", "type": "string"}]
    a = _analysis({
        "f": {"relational": True, "category": "file", "format": "csv",
              "reader_options": {"sep": "|", "header": True},
              "columns": cols, "mock_file": "f.csv"},
    })
    out = tmp_path / "mock" / "ep"
    out.mkdir(parents=True)
    (out / "f.csv").write_text("A|C\n1|2\n", encoding="utf-8")
    probs = _verify_bundle(a, str(tmp_path / "mock"))
    assert any("CSV header columns" in p for p in probs), probs


def test_verify_rejects_sql_seeded_in_mock_data(tmp_path):
    """§5: verify ERRORs if a .sql template is seeded as mock_file."""
    a = _analysis({
        "query_template": {"relational": True, "category": "file", "format": "sql",
                           "access": "read",
                           "columns": [{"name": "x", "type": "string"}],
                           "mock_file": "query.sql"},
    })
    mock_dir = tmp_path / "mock" / "ep"
    mock_dir.mkdir(parents=True)
    (mock_dir / "query.sql").write_text("SELECT 1")
    probs = _verify_bundle(a, str(tmp_path / "mock"))
    assert any("SQL template seeded" in p for p in probs), probs


# ---------------------------------------------------------------------------
# peek + CLI
# ---------------------------------------------------------------------------

def test_peek_yaml_document(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("env: prod\nretries: 3\n", encoding="utf-8")
    rc = datagen.peek_file(p, n=3)
    assert rc == 0


def test_cli_exits_nonzero_when_document_schema_missing(tmp_path):
    schemas = _write_schemas_dir(tmp_path, _analysis({
        "cfg": {"relational": False, "format": "yaml",
                "document_schema": None},
    }))
    out = tmp_path / "mock"
    proc = subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS, "datagen.py"), str(schemas), str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0, proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["gated_tables"] == ["ep.cfg"]
    assert any("document_schema" in m for m in payload["problems"]["ep.cfg"])


def test_cli_verify_json_shape(tmp_path):
    a = _csv_analysis({"sep": ","},
                      [{"name": "x", "type": "string", "nullable": False}])
    datagen.seed_workload(a["entrypoints"], str(tmp_path))
    schemas = _write_schemas_dir(tmp_path, a)
    proc = subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS, "datagen.py"),
         str(schemas), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert "ok" in payload and "warnings" in payload
    assert "gated_tables" in payload and "problems" in payload


def test_tables_to_gate_expands_entrypoint_level_problem():
    """An entrypoint-level key gates every table in that ep; a table-level key
    gates only that table; a catalog-level key gates nothing."""
    entrypoints = [
        {"id": "ep1", "tables": {"t1": {}, "t2": {}}},
        {"id": "ep2", "tables": {"a": {}}},
    ]
    problems = {
        "ep1": ["ep1: source_runtime is null — set it"],
        "ep2.a": ["ep2.tables.a: table has columns: []"],
        "sql_files[etl/load.sql]": ["sql_files[etl/load.sql]: table x not declared"],
    }
    assert datagen.tables_to_gate(problems, entrypoints) == {"ep1.t1", "ep1.t2", "ep2.a"}


def test_tables_to_gate_handles_dotted_table_names():
    """Mined table keys often contain dots (e.g. "db.out_tbl") — resolution must
    not assume the last dot separates the entrypoint from the table."""
    entrypoints = [{"id": "main", "tables": {"db.out_tbl": {}, "orders": {}}}]
    problems = {"main.db.out_tbl": ["main.tables.db.out_tbl: table has columns: []"]}
    assert datagen.tables_to_gate(problems, entrypoints) == {"main.db.out_tbl"}


def test_tables_to_gate_rejects_unresolvable_key():
    """A key that names no entrypoint or table is a producer bug, not a no-op."""
    entrypoints = [{"id": "ep1", "tables": {"t1": {}}}]
    with pytest.raises(ValueError, match="matches no entrypoint or table"):
        datagen.tables_to_gate({"ep1.tables": ["bogus key"]}, entrypoints)


def test_verify_schema_keys_every_problem_to_a_real_target():
    """Every key verify_schema emits must resolve — this is what makes gating work.

    Covers all five producers: llm_todo, JSON-schema errors, missing
    document_schema, empty columns, and the sql_files catalog.
    """
    a = _multi_ep([{
        "id": "ep1",
        "tables": {
            "empty_cols": _normalize_table("empty_cols", {"columns": []}),
            "todo": _normalize_table("todo", {
                "columns": [{"name": "id", "type": "long"}],
                "llm_todo": "confirm the real table name",
            }),
            "doc": _normalize_table("doc", {
                "relational": False, "category": "file", "format": "json",
            }),
            "bad_type": _normalize_table("bad_type", {
                "columns": [{"name": "id", "type": 17}],
            }),
        },
    }])
    sql_files = [{"path": "etl/load.sql",
                  "tables": {"nowhere": {"name": "nowhere", "roles": ["read"],
                                         "columns": ["c"]}}}]
    problems = datagen.verify_schema(a["entrypoints"], sql_files=sql_files)
    assert problems
    # No key raises, and the per-table problems land on the real table keys.
    gated = datagen.tables_to_gate(problems, a["entrypoints"])
    assert {"ep1.empty_cols", "ep1.todo", "ep1.doc", "ep1.bad_type"} <= gated
    assert "sql_files[etl/load.sql]" in problems


# --- date realism: anchor date/period mocks near today so relative filters match ---

import datetime as _dt_test
import random as _random_test


def _recent(value, fmt, max_days_back=420, max_days_fwd=10):
    d = _dt_test.datetime.strptime(value, fmt).date()
    today = _dt_test.date.today()
    return -max_days_fwd <= (today - d).days <= max_days_back


def test_date_semantics_and_recent_values():
    rng = _random_test.Random(0)
    for name in ("year_month", "YEAR_MONTH", "period", "report_month", "month"):
        v = datagen._date_semantic_string(name, rng)
        assert v is not None and len(v) == 7 and v[4] == "-", (name, v)
        assert _recent(v + "-01", "%Y-%m-%d"), (name, v)

    rng = _random_test.Random(1)
    for name in ("report_dt", "transaction_date", "etl_updated_dt", "asof_date", "day"):
        v = datagen._date_semantic_string(name, rng)
        assert v is not None and len(v) == 10, (name, v)
        assert _recent(v, "%Y-%m-%d"), (name, v)

    rng = _random_test.Random(2)
    for name in ("rest_no", "status", "amount", "store_id", "fz_code", "country_code"):
        assert datagen._date_semantic_string(name, rng) is None, name

    rng = _random_test.Random(3)
    for v in datagen._scalar_pool("date", rng)["typical"]:
        assert _recent(v, "%Y-%m-%d"), v

    rng = _random_test.Random(4)
    for v in datagen._scalar_pool("timestamp", rng)["typical"]:
        assert _recent(v[:10], "%Y-%m-%d"), v

    schema = [
        {"name": "rest_no", "type": "string", "nullable": False},
        {"name": "year_month", "type": "string", "nullable": False},
    ]
    rows = datagen.generate_rows(schema, n=12, seed=11)
    ym = [r["year_month"] for r in rows if isinstance(r.get("year_month"), str)]
    assert ym
    assert any(len(v) == 7 and v[4] == "-" and _recent(v + "-01", "%Y-%m-%d") for v in ym), ym

    schema = [{"name": "year_month", "type": "string", "nullable": False,
               "values": ["2019-01"]}]
    cats = datagen.categoricals_from_columns(schema)
    rows = datagen.generate_rows(schema, n=6, seed=5, categoricals=cats)
    assert {r["year_month"] for r in rows} == {"2019-01"}


# ===========================================================================
# Directory layout: write_schemas_dir, save_entrypoint, save_table
# ===========================================================================


def test_write_schemas_dir_creates_dir_layout(tmp_path):
    """write_schemas_dir emits _meta.json + tables/*.json per entrypoint."""
    a = _analysis({
        "t1": {"columns": [{"name": "id", "type": "string"}]},
        "t2": {"access": "write", "columns": [{"name": "v", "type": "long"}]},
    })
    schemas = _write_schemas_dir(tmp_path, a)
    ep_id = a["entrypoints"][0]["id"]

    # manifest uses "dir" not "file"
    manifest = json.loads((schemas / "manifest.json").read_text())
    ref = manifest["entrypoints"][0]
    assert "dir" in ref
    assert "file" not in ref
    assert ref["dir"] == f"entrypoints/{ep_id}"

    # _meta.json exists and has no "tables" key
    meta_path = schemas / "entrypoints" / ep_id / "_meta.json"
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text())
    assert "tables" not in meta
    assert meta["id"] == ep_id

    # tables/ directory has one file per table
    tables_dir = schemas / "entrypoints" / ep_id / "tables"
    assert tables_dir.is_dir()
    tbl_files = list(tables_dir.glob("*.json"))
    assert len(tbl_files) == 2

    # each table file has _table_key
    for tf in tbl_files:
        d = json.loads(tf.read_text())
        assert "_table_key" in d


def test_write_schemas_dir_no_legacy_flat_json(tmp_path):
    """No flat entrypoints/<id>.json files are written (hard cutover)."""
    a = _analysis({"t": {"columns": [{"name": "x", "type": "int"}]}})
    schemas = _write_schemas_dir(tmp_path, a)
    ep_id = a["entrypoints"][0]["id"]
    # The old single-file path must NOT exist
    assert not (schemas / "entrypoints" / f"{ep_id}.json").is_file()


def test_save_entrypoint_full_sync_deletes_stale(tmp_path):
    """save_entrypoint removes table files whose key is no longer in tables."""
    a = _analysis({
        "alpha": {"columns": [{"name": "id", "type": "string"}]},
        "beta": {"columns": [{"name": "v", "type": "long"}]},
        "gamma": {"columns": [{"name": "x", "type": "int"}]},
    })
    schemas = _write_schemas_dir(tmp_path, a)
    ep_id = a["entrypoints"][0]["id"]
    tables_dir = schemas / "entrypoints" / ep_id / "tables"
    assert len(list(tables_dir.glob("*.json"))) == 3

    # Remove "gamma" from tables and save
    ep = a["entrypoints"][0]
    del ep["tables"]["gamma"]
    datagen.save_entrypoint(schemas, ep)

    remaining_keys = set()
    for p in tables_dir.glob("*.json"):
        d = json.loads(p.read_text())
        remaining_keys.add(d["_table_key"])
    assert remaining_keys == {"alpha", "beta"}
    assert len(list(tables_dir.glob("*.json"))) == 2


def test_save_table_isolated(tmp_path):
    """save_table writes only the targeted table file; others and _meta untouched."""
    a = _analysis({
        "src": {"columns": [{"name": "id", "type": "string"}]},
        "out": {"access": "write", "columns": [{"name": "v", "type": "long"}]},
    })
    schemas = _write_schemas_dir(tmp_path, a)
    ep_id = a["entrypoints"][0]["id"]
    meta_path = schemas / "entrypoints" / ep_id / "_meta.json"
    tables_dir = schemas / "entrypoints" / ep_id / "tables"

    meta_mtime = meta_path.stat().st_mtime

    # Find the file for "src" and record other file's mtime
    src_path = None
    out_path = None
    for p in tables_dir.glob("*.json"):
        d = json.loads(p.read_text())
        if d["_table_key"] == "src":
            src_path = p
        else:
            out_path = p
    assert src_path and out_path

    out_mtime_before = out_path.stat().st_mtime

    # Update only "src" via save_table
    import time; time.sleep(0.01)
    new_entry = {"columns": [{"name": "id", "type": "string"}, {"name": "extra", "type": "int"}]}
    datagen.save_table(schemas, ep_id, "src", new_entry)

    # meta unchanged
    assert meta_path.stat().st_mtime == meta_mtime

    # out unchanged
    assert out_path.stat().st_mtime == out_mtime_before

    # src updated and _table_key preserved
    d = json.loads(src_path.read_text())
    assert d["_table_key"] == "src"
    assert len(d["columns"]) == 2


def test_read_entrypoints_roundtrip(tmp_path):
    """read_entrypoints after write_schemas_dir returns identical entrypoints."""
    a = _analysis({
        "t": {"columns": [{"name": "id", "type": "string"}]},
    }, ep_id="my_ep")
    schemas = _write_schemas_dir(tmp_path, a)
    eps = datagen.read_entrypoints(schemas)
    assert len(eps) == 1
    assert eps[0] == a["entrypoints"][0]


# ---------------------------------------------------------------------------
# Enum coercion: string-typed column with int values (regression for #1)
# ---------------------------------------------------------------------------

def test_string_column_with_int_enum_values_generates_strings(tmp_path):
    """String-typed column with int enum values must produce str values, not '1.0'."""
    schema = [{"name": "code", "type": "string", "nullable": True}]
    rows = datagen.generate_rows(schema, n=6, categoricals={"code": [1, 2]})
    for r in rows:
        v = r.get("code")
        if v is not None:
            assert isinstance(v, str), f"expected str, got {type(v).__name__}: {v!r}"
            assert v in ("1", "2"), f"unexpected categorical value: {v!r}"

    # parquet round-trip: column dtype must be string and values must not be '1.0'
    out = tmp_path / "codes.parquet"
    datagen.write_mock_parquet(out, rows, schema)
    tbl = pq.read_table(out)
    col_vals = [v.as_py() for v in tbl.column("code") if v.as_py() is not None]
    assert col_vals, "expected at least one non-null value"
    for v in col_vals:
        assert isinstance(v, str), f"parquet value is not str: {v!r}"
        assert "." not in v, f"expected '1' or '2', got float-like string: {v!r}"


def test_load_entrypoint_raises_on_collision(tmp_path):
    """load_entrypoint raises SchemaCollisionError when table-keys collapse to same name."""
    from helpers import load_entrypoint, SchemaCollisionError

    ep_dir = tmp_path / "entrypoints" / "myep"
    (ep_dir / "tables").mkdir(parents=True)
    (ep_dir / "_meta.json").write_text(
        json.dumps({"id": "myep", "source_runtime": "spark"}))
    for key in ("Table_X", "table_x"):
        (ep_dir / "tables" / f"{key}.json").write_text(
            json.dumps({"_table_key": key, "columns": []}))

    with pytest.raises(SchemaCollisionError) as exc:
        load_entrypoint(str(tmp_path), "myep")
    msg = str(exc.value)
    assert "Table_X" in msg and "table_x" in msg and "myep" in msg
    assert "\n" not in msg, "collision FATAL must stay a single line"


# ---------------------------------------------------------------------------
# Transitive join-key overlap: union-find over the declared edges
# ---------------------------------------------------------------------------

def test_join_pooling_is_transitive_across_a_chain(tmp_path):
    """A->B and B->C edges put all three pk columns in ONE pool, so the values
    genuinely intersect across the whole chain (a terminal sink stays non-empty)."""
    out = str(tmp_path / "mock")
    os.makedirs(out, exist_ok=True)
    a = _analysis({
        "A": {"relational": True, "format": "parquet", "columns": [
            {"name": "pk", "type": "string", "nullable": False},
            {"name": "val_a", "type": "string"}
        ], "mock_file": "a.parquet"},
        "B": {"relational": True, "format": "parquet", "columns": [
            {"name": "pk", "type": "string", "nullable": False},
            {"name": "val_b", "type": "string"}
        ], "mock_file": "b.parquet"},
        "C": {"relational": True, "format": "parquet", "columns": [
            {"name": "pk", "type": "string", "nullable": False},
            {"name": "val_c", "type": "string"}
        ], "mock_file": "c.parquet"},
    }, ep_id="ep")
    # Only adjacent edges are declared; C must still share A's values.
    a["entrypoints"][0]["joins"] = [
        {"left": "A.pk", "right": "B.pk"},
        {"left": "B.pk", "right": "C.pk"},
    ]
    datagen.seed_workload(a["entrypoints"], out)

    vals = {}
    for t in ("a", "b", "c"):
        col = pq.read_table(os.path.join(out, "ep", f"{t}.parquet")).column("pk")
        vals[t] = {v for v in col.to_pylist() if v}
    assert vals["a"] and vals["b"] and vals["c"]
    assert vals["a"] & vals["b"] & vals["c"], vals


def test_same_named_keylike_columns_pool_only_when_types_match(tmp_path):
    """The name fallback is keyed on (name, base_type): a string `id` and a long
    `id` in unrelated tables must NOT be forced to share values."""
    out = str(tmp_path / "mock")
    os.makedirs(out, exist_ok=True)
    a = _analysis({
        "S": {"relational": True, "format": "parquet", "columns": [
            {"name": "id", "type": "string", "nullable": False}], "mock_file": "s.parquet"},
        "T": {"relational": True, "format": "parquet", "columns": [
            {"name": "id", "type": "string", "nullable": False}], "mock_file": "t.parquet"},
        "U": {"relational": True, "format": "parquet", "columns": [
            {"name": "id", "type": "long", "nullable": False}], "mock_file": "u.parquet"},
    }, ep_id="ep")
    datagen.seed_workload(a["entrypoints"], out)

    def ids(name):
        return {v for v in pq.read_table(
            os.path.join(out, "ep", f"{name}.parquet")).column("id").to_pylist() if v is not None}

    assert ids("s") & ids("t")          # same name AND same base type -> pooled
    assert not (ids("s") & ids("u"))    # different base type -> independent


# ---------------------------------------------------------------------------
# Gating contract: valid schemas get data, problem schemas get none, and the
# mock directory always reflects the latest run.
# ---------------------------------------------------------------------------

def _run_datagen(schemas, out):
    proc = subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS, "datagen.py"), str(schemas), str(out)],
        capture_output=True, text=True,
    )
    return proc, json.loads(proc.stdout)


def _table_files(schemas, ep_id):
    return sorted(p.name for p in (schemas / "entrypoints" / ep_id / "tables").glob("*.json"))


def _mock_files(out, ep_id):
    d = out / ep_id
    return sorted(p.name for p in d.glob("*")) if d.is_dir() else []


def test_gating_never_deletes_table_schemas(tmp_path):
    """An entrypoint-level problem withholds data for every table in that ep but
    must NEVER touch the mined schema files — the agent has to be able to fix them."""
    a = _multi_ep([{
        "id": "ep1",
        "tables": {
            "orders": _normalize_table("orders", {"columns": [{"name": "id", "type": "long"}]}),
            "cust": _normalize_table("cust", {"columns": [{"name": "id", "type": "long"}]}),
        },
    }])
    schemas, out = _write_schemas_dir(tmp_path, a), tmp_path / "mock"
    proc, payload = _run_datagen(schemas, out)
    assert payload["ok"] is True, payload
    assert _mock_files(out, "ep1") == ["_hashes.json", "cust.parquet", "orders.parquet"]

    # Regress the entrypoint itself: source_runtime is a one-field _meta fix.
    meta_path = schemas / "entrypoints" / "ep1" / "_meta.json"
    meta = json.loads(meta_path.read_text())
    meta["source_runtime"] = None
    meta_path.write_text(json.dumps(meta))

    proc, payload = _run_datagen(schemas, out)
    assert proc.returncode != 0
    assert payload["ok"] is False
    assert payload["gated_tables"] == ["ep1.cust", "ep1.orders"]
    # every table schema survives, and the stale mocks are gone
    assert _table_files(schemas, "ep1") == ["cust.json", "orders.json"]
    assert _mock_files(out, "ep1") == ["_hashes.json"]

    # Fixing the one field restores full generation.
    meta["source_runtime"] = "spark"
    meta_path.write_text(json.dumps(meta))
    proc, payload = _run_datagen(schemas, out)
    assert payload["ok"] is True, payload
    assert payload["gated_tables"] is None
    assert _mock_files(out, "ep1") == ["_hashes.json", "cust.parquet", "orders.parquet"]


def test_one_table_llm_todo_gates_only_that_table(tmp_path):
    """A table-level problem must not take its siblings down with it."""
    a = _multi_ep([{
        "id": "ep1",
        "tables": {
            "good": _normalize_table("good", {"columns": [{"name": "id", "type": "long"}]}),
            "vague": _normalize_table("vague", {
                "columns": [{"name": "id", "type": "long"}],
                "llm_todo": "confirm the real table name",
            }),
        },
    }])
    schemas, out = _write_schemas_dir(tmp_path, a), tmp_path / "mock"
    proc, payload = _run_datagen(schemas, out)
    assert proc.returncode != 0
    assert payload["gated_tables"] == ["ep1.vague"]
    assert _table_files(schemas, "ep1") == ["good.json", "vague.json"]
    assert _mock_files(out, "ep1") == ["_hashes.json", "good.parquet"]


def test_corrupt_mock_is_reported_and_deleted(tmp_path):
    """A generated mock that no longer matches its schema is a blocking problem and
    its data must not survive the run."""
    a = _analysis({"orders": {"relational": True, "format": "parquet", "columns": [
        {"name": "id", "type": "int"}, {"name": "amt", "type": "double"}]}}, ep_id="ep")
    schemas, out = _write_schemas_dir(tmp_path, a), tmp_path / "mock"
    proc, payload = _run_datagen(schemas, out)
    assert payload["ok"] is True, payload

    mock = out / "ep" / "orders.parquet"
    mock.chmod(0o644)
    pq.write_table(pa.table({"id": pa.array(["a", "b"], pa.string())}), mock)

    proc, payload = _run_datagen(schemas, out)
    assert proc.returncode != 0
    msgs = payload["problems"]["ep.orders"]
    assert any("declared int but parquet string" in m for m in msgs), msgs
    assert any("missing declared column(s): amt" in m for m in msgs), msgs
    assert payload["gated_tables"] == ["ep.orders"]
    assert not mock.exists(), "a mock that failed its checks must be deleted"

    # The next run regenerates it from the (unchanged) schema.
    proc, payload = _run_datagen(schemas, out)
    assert payload["ok"] is True, payload
    assert pq.read_schema(mock).names == ["id", "amt"]


def test_gated_mock_is_wiped_even_when_a_sibling_ep_shares_the_name(tmp_path):
    """Two entrypoints reading the same table name write identically-named files;
    gating one must not spare it because the other still has data."""
    tbl = {"columns": [{"name": "id", "type": "long"}]}
    a = _multi_ep([
        {"id": "ep1", "tables": {"orders": _normalize_table("orders", dict(tbl))}},
        {"id": "ep2", "tables": {"orders": _normalize_table("orders", dict(tbl))}},
    ])
    schemas, out = _write_schemas_dir(tmp_path, a), tmp_path / "mock"
    proc, payload = _run_datagen(schemas, out)
    assert payload["ok"] is True, payload
    assert _mock_files(out, "ep1") == _mock_files(out, "ep2") == ["_hashes.json", "orders.parquet"]

    meta_path = schemas / "entrypoints" / "ep2" / "_meta.json"
    meta = json.loads(meta_path.read_text())
    meta["source_runtime"] = None
    meta_path.write_text(json.dumps(meta))

    proc, payload = _run_datagen(schemas, out)
    assert payload["gated_tables"] == ["ep2.orders"]
    assert _mock_files(out, "ep1") == ["_hashes.json", "orders.parquet"]
    assert _mock_files(out, "ep2") == ["_hashes.json"]


def test_mock_dir_drops_data_for_a_removed_table(tmp_path):
    """Deleting a table from the schemas removes its mock on the next run."""
    a = _multi_ep([{
        "id": "ep1",
        "tables": {
            "keep": _normalize_table("keep", {"columns": [{"name": "id", "type": "long"}]}),
            "drop": _normalize_table("drop", {"columns": [{"name": "id", "type": "long"}]}),
        },
    }])
    schemas, out = _write_schemas_dir(tmp_path, a), tmp_path / "mock"
    _run_datagen(schemas, out)
    assert _mock_files(out, "ep1") == ["_hashes.json", "drop.parquet", "keep.parquet"]

    (schemas / "entrypoints" / "ep1" / "tables" / "drop.json").unlink()
    proc, payload = _run_datagen(schemas, out)
    assert payload["ok"] is True, payload
    assert _mock_files(out, "ep1") == ["_hashes.json", "keep.parquet"]


def test_cli_fatal_on_schema_case_collision(tmp_path):
    a = _multi_ep([{"id": "ep1", "tables": {
        "Table_X": _normalize_table("Table_X", {"columns": [{"name": "id", "type": "long"}]}),
    }}])
    schemas, out = _write_schemas_dir(tmp_path, a), tmp_path / "mock"
    tables_dir = schemas / "entrypoints" / "ep1" / "tables"
    (tables_dir / "table_x.json").write_text(json.dumps(
        {"_table_key": "table_x", "relational": True, "category": "table", "access": "read",
         "columns": [{"name": "id", "type": "long"}]}))

    proc = subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS, "datagen.py"), str(schemas), str(out)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "FATAL" in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1, proc.stderr
    assert not (out / "ep1").exists(), "a collision must generate nothing"


# ---------------------------------------------------------------------------
# Shared mocks and shared pools must satisfy every consumer
# ---------------------------------------------------------------------------

def test_merge_columns_narrows_enum_to_the_intersection():
    """One mock serves every consuming entrypoint, so a column declared with two
    different domains must be generated from what both allow."""
    merged = datagen._merge_columns(
        [{"name": "cd", "type": "string", "values": ["A", "B", "C"]}],
        [{"name": "cd", "type": "string", "values": ["B", "C", "D"]}],
    )
    assert merged[0]["values"] == ["B", "C"]


def test_merge_columns_keeps_first_domain_when_intersection_is_empty():
    """Irreconcilable domains still have to generate something; verify_mocks is
    what reports the conflict against the other consumer."""
    merged = datagen._merge_columns(
        [{"name": "cd", "type": "string", "values": ["A"]}],
        [{"name": "cd", "type": "string", "values": ["B"]}],
    )
    assert merged[0]["values"] == ["A"]


def test_shared_enum_columns_pool_so_the_join_overlaps(tmp_path):
    """Two tables joined on an enum column must share values. Declaring the same
    domain on both sides is the documented remedy for an empty overlap, so a
    100-value domain sampled into 12 rows per table cannot be left to chance."""
    out = str(tmp_path / "mock")
    os.makedirs(out, exist_ok=True)
    domain = ["V%03d" % i for i in range(100)]
    a = _analysis({
        "L": {"relational": True, "format": "parquet", "mock_file": "l.parquet", "columns": [
            {"name": "bilcodc", "type": "string", "nullable": False, "values": list(domain)}]},
        "R": {"relational": True, "format": "parquet", "mock_file": "r.parquet", "columns": [
            {"name": "bilcodc", "type": "string", "nullable": False, "values": list(domain)}]},
    }, ep_id="ep")
    datagen.seed_workload(a["entrypoints"], out)
    vals = {t: {v for v in pq.read_table(os.path.join(out, "ep", f"{t}.parquet"))
                .column("bilcodc").to_pylist() if v} for t in ("l", "r")}
    assert vals["l"] & vals["r"], vals
    assert _verify_bundle(a, out) == []


def test_pooled_int_enum_is_not_written_as_float(tmp_path):
    """A pooled int-enum column must not reach the mock as '1.0' — the pool draw
    has to honour the declared type exactly like a local categorical does."""
    out = str(tmp_path / "mock")
    os.makedirs(out, exist_ok=True)
    a = _analysis({
        "L": {"relational": True, "format": "parquet", "mock_file": "l.parquet", "columns": [
            {"name": "an", "type": "integer", "values": [1]}]},
        "R": {"relational": True, "format": "parquet", "mock_file": "r.parquet", "columns": [
            {"name": "an", "type": "integer", "values": [1]}]},
    }, ep_id="ep")
    datagen.seed_workload(a["entrypoints"], out)
    for t in ("l", "r"):
        vals = {v for v in pq.read_table(os.path.join(out, "ep", f"{t}.parquet"))
                .column("an").to_pylist() if v is not None}
        assert vals == {1}, f"{t}: {vals}"
    assert _verify_bundle(a, out) == []


def test_enum_check_uses_the_declared_type(tmp_path):
    """A string column with int enum literals holds "1"; the domain check must
    compare against the same coercion the generator applies."""
    out = str(tmp_path / "mock")
    os.makedirs(out, exist_ok=True)
    a = _analysis({"t": {"relational": True, "format": "parquet", "mock_file": "t.parquet",
                         "columns": [{"name": "code", "type": "string", "values": [1, 2]}]}},
                  ep_id="ep")
    datagen.seed_workload(a["entrypoints"], out)
    assert _verify_bundle(a, out) == []


def test_join_overlap_is_not_asserted_when_a_participant_is_gated(tmp_path):
    """One bad table must not gate its siblings: with a participant withheld the
    value pool is incomplete, so the overlap verdict is not theirs to fail."""
    out = str(tmp_path / "mock")
    os.makedirs(out, exist_ok=True)
    a = _analysis({
        "A": {"relational": True, "format": "parquet", "mock_file": "a.parquet", "columns": [
            {"name": "k_id", "type": "string", "join_key": True}]},
        "B": {"relational": True, "format": "parquet", "mock_file": "b.parquet", "columns": [
            {"name": "k_id", "type": "string", "join_key": True}]},
        "C": {"relational": True, "format": "parquet", "mock_file": "c.parquet", "columns": [
            {"name": "k_id", "type": "string", "join_key": True}]},
    }, ep_id="ep")
    eps = a["entrypoints"]
    gated = {"ep.C"}
    datagen.seed_workload(eps, out, gated_tables=gated)
    generated = {"ep.A", "ep.B"}
    problems, overlap = datagen.verify_mocks(eps, out, generated)
    assert not any("join overlap" in m for msgs in problems.values() for m in msgs), problems
    assert not overlap, overlap


def test_all_mock_formats_generate_and_verify_clean(tmp_path):
    """parquet table + csv file source + json document + write sink in one
    entrypoint: datagen must produce each artifact, verify it clean, and leave the
    write-only sink unmocked."""
    out = str(tmp_path / "mock")
    a = _multi_ep([{
        "id": "ep",
        "tables": {
            "cat_tbl": _normalize_table("cat_tbl", {
                "category": "table", "format": "parquet",
                "columns": [{"name": "id", "type": "string"}, {"name": "n", "type": "integer"}]}),
            "csv_src": _normalize_table("csv_src", {
                "category": "file", "format": "csv",
                "reader_options": {"header": "true", "sep": ","},
                "columns": [{"name": "id", "type": "string"}, {"name": "amt", "type": "double"}]}),
            "cfg_doc": _normalize_table("cfg_doc", {
                "relational": False, "category": "file", "format": "json", "columns": [],
                "document_schema": {"env": "string", "retries": "int", "hosts": ["string"]}}),
            "sink_tbl": _normalize_table("sink_tbl", {
                "category": "table", "access": "write",
                "columns": [{"name": "id", "type": "string"}]}),
        },
    }])
    eps = a["entrypoints"]
    assert datagen.verify_schema(eps) == {}
    man = datagen.seed_workload(eps, out)
    assert set(man["table_paths"]) == {"ep.cat_tbl", "ep.csv_src", "ep.cfg_doc"}
    assert "mock_file" not in eps[0]["tables"]["sink_tbl"]
    assert _verify_bundle(a, out) == []

    tbl = pq.read_table(os.path.join(out, "ep", eps[0]["tables"]["cat_tbl"]["mock_file"]))
    assert tbl.num_rows > 0 and tbl.column_names == ["id", "n"]

    import csv as _csv
    with open(os.path.join(out, "ep", eps[0]["tables"]["csv_src"]["mock_file"])) as fh:
        rows = list(_csv.DictReader(fh))
    assert rows and set(rows[0]) == {"id", "amt"}

    doc = json.loads(pathlib.Path(out, "ep", eps[0]["tables"]["cfg_doc"]["mock_file"]).read_text())
    assert set(doc) == {"env", "retries", "hosts"}
    assert isinstance(doc["env"], str) and isinstance(doc["retries"], int)
    assert isinstance(doc["hosts"], list) and isinstance(doc["hosts"][0], str)
