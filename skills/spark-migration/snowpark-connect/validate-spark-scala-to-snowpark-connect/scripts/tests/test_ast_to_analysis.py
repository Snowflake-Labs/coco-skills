"""Tests for ast_to_analysis.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import ast_to_analysis as ata  # noqa: E402


def _write_ast(tmp_path: Path, files: list[dict]) -> None:
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "ast_facts.json").write_text(json.dumps({
        "source": str(tmp_path / "Validation" / "source"),
        "file_count": len(files),
        "parse_errors": 0,
        "files": files,
    }), encoding="utf-8")


def _write_source(tmp_path: Path, rel: str, body: str = "") -> Path:
    src = tmp_path / "Validation" / "source" / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(body or "object Main { def main(args: Array[String]): Unit = () }", encoding="utf-8")
    return src


def test_survey_builds_candidates(tmp_path):
    rel = "src/main/scala/Jobs.scala"
    src = _write_source(tmp_path, rel)
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "objects": ["Main"],
        "entrypoints": [{"owner": "Main", "method": "main"}],
        "reads": [{"call": "table", "args": ["ORDERS"]}],
        "writes": [],
        "table_refs": ["ORDERS"],
        "column_refs": ["order_id"],
        "write_helpers": [],
        "spark_session_created": True,
    }])
    result = ata.run(tmp_path, mode="survey")
    assert len(result["entrypoint_candidates"]) == 1
    assert result["entrypoint_candidates"][0]["id"] == "jobs"
    assert result["build_tool"] in {"sbt", "unknown"}


def test_deep_builds_sources_sinks_with_llm_todo(tmp_path):
    rel = "src/main/scala/Jobs.scala"
    src = _write_source(tmp_path, rel)
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{"id": "jobs", "path": rel}],
        "source_roots": ["src/main/scala"],
        "build_tool": "sbt",
    }), encoding="utf-8")
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "objects": ["Main"],
        "entrypoints": [{"owner": "Main", "method": "main"}],
        "reads": [{"call": "table", "args": ["DB.SCH.ORDERS"]}],
        "writes": [{"call": "saveAsTable", "args": ["DB.SCH.OUT"]}],
        "table_refs": ["DB.SCH.ORDERS", "DB.SCH.OUT"],
        "column_refs": ["order_id", "amount"],
        "write_helpers": ["writeOut"],
        "spark_session_created": True,
    }])
    result = ata.run(tmp_path, mode="deep")
    ep = result["entrypoints"][0]
    assert ep["external_sources"]
    assert ep["sinks"]
    assert result["external_sources"][0]["schema"]
    assert result["external_sources"][0].get("llm_todo")
    assert result["sinks"][0].get("natural_keys") == []
    assert result["complete"] is False
    assert result["llm_todos"]


# ── unresolved edge consumption (ScosAnalyze data-edge parity) ─────────────


def _write_ast_unresolved(tmp_path: Path, unresolved_reads, unresolved_writes) -> None:
    rel = "src/main/scala/Job.scala"
    src = _write_source(tmp_path, rel)
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{"id": "job", "path": rel}],
        "source_roots": ["src/main/scala"],
        "build_tool": "sbt",
    }), encoding="utf-8")
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "objects": ["Job"],
        "entrypoints": [{"owner": "Job", "method": "main"}],
        "reads": [],
        "writes": [],
        "unresolved_reads": unresolved_reads,
        "unresolved_writes": unresolved_writes,
        "table_refs": [],
        "column_refs": ["id"],
        "write_helpers": [],
        "spark_session_created": True,
    }])


def test_unresolved_read_creates_source_with_llm_todo(tmp_path):
    """An unresolved read (dynamic path) must create a source with a dynamic-path llm_todo."""
    _write_ast_unresolved(tmp_path, unresolved_reads=[
        {"call": "parquet", "arg_expr": "configPath", "line": 10},
    ], unresolved_writes=[])
    result = ata.run(tmp_path, mode="deep")
    sources = result.get("external_sources") or []
    assert sources, "expected at least one source from unresolved read"
    src = sources[0]
    assert "dynamic" in src["llm_todo"].lower() or "path" in src["llm_todo"].lower()
    assert "configPath" in src["llm_todo"] or "configPath" in src.get("original_path", "")
    assert src.get("reader_method") == "parquet"


def test_unresolved_write_creates_sink_with_llm_todo(tmp_path):
    """An unresolved write (dynamic target) must create a sink with an llm_todo."""
    _write_ast_unresolved(tmp_path, unresolved_reads=[], unresolved_writes=[
        {"call": "saveAsTable", "arg_expr": "outputTable", "line": 15},
    ])
    result = ata.run(tmp_path, mode="deep")
    sinks = result.get("sinks") or []
    assert sinks, "expected at least one sink from unresolved write"
    sink = sinks[0]
    assert sink.get("llm_todo") or sink.get("method") == "saveastable"


def test_resolved_and_unresolved_reads_together(tmp_path):
    """Mix of resolved + unresolved reads: all become sources, unresolved flagged."""
    rel = "src/main/scala/MixedJob.scala"
    src = _write_source(tmp_path, rel)
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{"id": "mixedjob", "path": rel}],
        "source_roots": ["src/main/scala"],
        "build_tool": "sbt",
    }), encoding="utf-8")
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "objects": ["MixedJob"],
        "entrypoints": [{"owner": "MixedJob", "method": "main"}],
        "reads": [{"call": "parquet", "args": ["s3://bucket/static.parquet"], "line": 5}],
        "writes": [{"call": "saveAsTable", "args": ["out_table"], "line": 20}],
        "unresolved_reads": [
            {"call": "csv", "arg_expr": "dynamicCsvPath", "line": 8},
        ],
        "unresolved_writes": [],
        "table_refs": [],
        "column_refs": ["id", "value"],
        "write_helpers": [],
        "spark_session_created": True,
    }])
    result = ata.run(tmp_path, mode="deep")
    sources = result.get("external_sources") or []
    # Should have both the static read AND the unresolved one
    assert len(sources) == 2, f"expected 2 sources (static + unresolved), got {len(sources)}"
    methods = {s["reader_method"] for s in sources}
    assert "parquet" in methods
    assert "csv" in methods
    # The unresolved one must have a dynamic-path llm_todo
    unresolved_src = next(s for s in sources if s["reader_method"] == "csv")
    assert "dynamic" in unresolved_src.get("llm_todo", "").lower() or \
           "path" in unresolved_src.get("llm_todo", "").lower()


def test_unresolved_edges_deduplication(tmp_path):
    """Duplicate unresolved reads (same call+arg_expr) must be deduplicated."""
    _write_ast_unresolved(tmp_path, unresolved_reads=[
        {"call": "parquet", "arg_expr": "samePath", "line": 5},
        {"call": "parquet", "arg_expr": "samePath", "line": 5},  # duplicate
    ], unresolved_writes=[])
    result = ata.run(tmp_path, mode="deep")
    sources = result.get("external_sources") or []
    assert len(sources) == 1, "duplicate unresolved reads must be deduplicated"


def test_line_field_on_resolved_read(tmp_path):
    """Reads now carry a line field; ast_to_analysis must not break when it's present."""
    rel = "src/main/scala/LineJob.scala"
    src = _write_source(tmp_path, rel)
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{"id": "linejob", "path": rel}],
        "source_roots": ["src/main/scala"],
        "build_tool": "sbt",
    }), encoding="utf-8")
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "objects": ["LineJob"],
        "entrypoints": [{"owner": "LineJob", "method": "main"}],
        "reads": [{"call": "parquet", "args": ["s3://b/f.parquet"], "line": 42}],
        "writes": [],
        "unresolved_reads": [],
        "unresolved_writes": [],
        "table_refs": [],
        "column_refs": ["id"],
        "write_helpers": [],
        "spark_session_created": True,
    }])
    result = ata.run(tmp_path, mode="deep")
    sources = result.get("external_sources") or []
    assert sources, "source should be created for read with line field"
    assert sources[0]["reader_method"] == "parquet"


# ── Fix 3: filter / join data-contract enrichment ───────────────────────────


def _write_deep_fixture(tmp_path: Path, rel: str, body: str, ast_file: dict) -> None:
    src = _write_source(tmp_path, rel, body)
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{"id": Path(rel).stem.lower(), "path": rel}],
        "source_roots": ["src/main/scala"],
        "build_tool": "sbt",
    }), encoding="utf-8")
    ast_file = dict(ast_file)
    ast_file["path"] = str(src)
    _write_ast(tmp_path, [ast_file])


def test_regex_enriches_filter_values_and_join_keys(tmp_path):
    """Regex hedge: === / isin / SQL filter / Seq join → values + join_key."""
    rel = "src/main/scala/FilterJoinJob.scala"
    body = '''
object FilterJoinJob {
  def main(args: Array[String]): Unit = {
    val orders = spark.read.table("ORDERS")
    val items = spark.read.table("ITEMS")
    val filtered = orders
      .filter(col("status") === "ACTIVE")
      .filter(col("region").isin("US", "CA"))
      .filter("code = '610A'")
    val joined = filtered.join(items, Seq("order_id"))
    joined.write.saveAsTable("OUT")
  }
}
'''
    _write_deep_fixture(tmp_path, rel, body, {
        "parse_ok": True,
        "objects": ["FilterJoinJob"],
        "entrypoints": [{"owner": "FilterJoinJob", "method": "main"}],
        "reads": [
            {"call": "table", "args": ["ORDERS"]},
            {"call": "table", "args": ["ITEMS"]},
        ],
        "writes": [{"call": "saveAsTable", "args": ["OUT"]}],
        "table_refs": ["ORDERS", "ITEMS", "OUT"],
        "column_refs": ["status", "region", "code", "order_id"],
        "write_helpers": [],
        "spark_session_created": True,
        # No Scalameta filters/joins — force regex path
    })
    result = ata.run(tmp_path, mode="deep")
    sources = {s["name"]: s for s in result["external_sources"]}
    assert "ORDERS" in sources or any("orders" in s["id"] for s in result["external_sources"])

    # Find columns across sources
    all_cols = {}
    for s in result["external_sources"]:
        for c in s.get("schema") or []:
            all_cols.setdefault(c["name"], c)

    assert "ACTIVE" in (all_cols["status"].get("values") or [])
    assert set(all_cols["region"].get("values") or []) >= {"US", "CA"}
    assert "610A" in (all_cols["code"].get("values") or [])
    assert all_cols["order_id"].get("join_key") is True

    ep = result["entrypoints"][0]
    assert ep.get("joins"), "expected joins edges for shared order_id"
    assert any("order_id" in (e.get("left", "") + e.get("right", "")) for e in ep["joins"])


def test_scalameta_filters_preferred_over_regex(tmp_path):
    """When Scalameta filters[] present, those values win; regex only fills gaps."""
    rel = "src/main/scala/MetaJob.scala"
    body = '''
object MetaJob {
  def main(args: Array[String]): Unit = {
    val df = spark.read.table("T")
    // regex would see STALE; Scalameta says LIVE
    df.filter(col("status") === "STALE")
    df.write.saveAsTable("OUT")
  }
}
'''
    _write_deep_fixture(tmp_path, rel, body, {
        "parse_ok": True,
        "objects": ["MetaJob"],
        "entrypoints": [{"owner": "MetaJob", "method": "main"}],
        "reads": [{"call": "table", "args": ["T"]}],
        "writes": [{"call": "saveAsTable", "args": ["OUT"]}],
        "table_refs": ["T", "OUT"],
        "column_refs": ["status", "tier"],
        "write_helpers": [],
        "spark_session_created": True,
        "filters": [
            {"col": "status", "op": "===", "values": ["LIVE"], "line": 5},
        ],
        "joins": [],
    })
    result = ata.run(tmp_path, mode="deep")
    cols = {
        c["name"]: c
        for s in result["external_sources"]
        for c in (s.get("schema") or [])
    }
    # Scalameta value present; regex must NOT append the conflicting STALE literal
    assert cols["status"].get("values") == ["LIVE"]
    # Column with only regex evidence (not in Scalameta) still gets filled:
    # body has no tier filter — ensure we didn't invent values
    assert "tier" not in cols or not cols["tier"].get("values")


def test_scalameta_joins_set_join_key(tmp_path):
    rel = "src/main/scala/JoinMeta.scala"
    body = "object JoinMeta { def main(args: Array[String]): Unit = () }"
    _write_deep_fixture(tmp_path, rel, body, {
        "parse_ok": True,
        "objects": ["JoinMeta"],
        "entrypoints": [{"owner": "JoinMeta", "method": "main"}],
        "reads": [
            {"call": "table", "args": ["LEFT"]},
            {"call": "table", "args": ["RIGHT"]},
        ],
        "writes": [],
        "table_refs": ["LEFT", "RIGHT"],
        "column_refs": ["cust_id", "amount"],
        "write_helpers": [],
        "spark_session_created": True,
        "filters": [],
        "joins": [{"join_keys": ["cust_id"], "line": 10}],
    })
    result = ata.run(tmp_path, mode="deep")
    for s in result["external_sources"]:
        by_name = {c["name"]: c for c in s.get("schema") or []}
        if "cust_id" in by_name:
            assert by_name["cust_id"].get("join_key") is True
    ep = result["entrypoints"][0]
    assert any(
        e.get("left", "").endswith(".cust_id") and e.get("right", "").endswith(".cust_id")
        for e in (ep.get("joins") or [])
    )


def test_mine_filter_join_regex_unit():
    text = '''
      .filter(col("status") === "ACTIVE")
      .filter($"region" === "US")
      .filter(col("code").isin("A", "B"))
      .filter("flag = 'Y'")
      .join(other, Seq("order_id", "line_id"))
      .join(dim, "cust_id")
      .join(x, col("a_id") === col("b_id"))
      .filter(!col("bad").isin("X"))
    '''
    vals, keys = ata._mine_filter_join_regex(text)
    assert vals["status"] == ["ACTIVE"]
    assert vals["region"] == ["US"]
    assert vals["code"] == ["A", "B"]
    assert vals["flag"] == ["Y"]
    assert "bad" not in vals  # negated isin skipped
    assert keys == {"order_id", "line_id", "cust_id", "a_id", "b_id"}


def test_complex_predicate_not_invented(tmp_path):
    """Complex filter expressions must not invent values."""
    rel = "src/main/scala/Complex.scala"
    body = '''
object Complex {
  def main(args: Array[String]): Unit = {
    val df = spark.read.table("T")
    df.filter(col("a") > col("b") && col("c").isNotNull)
    df.write.saveAsTable("OUT")
  }
}
'''
    _write_deep_fixture(tmp_path, rel, body, {
        "parse_ok": True,
        "objects": ["Complex"],
        "entrypoints": [{"owner": "Complex", "method": "main"}],
        "reads": [{"call": "table", "args": ["T"]}],
        "writes": [{"call": "saveAsTable", "args": ["OUT"]}],
        "table_refs": ["T"],
        "column_refs": ["a", "b", "c"],
        "write_helpers": [],
        "spark_session_created": True,
    })
    result = ata.run(tmp_path, mode="deep")
    for s in result["external_sources"]:
        for c in s.get("schema") or []:
            assert not c.get("values"), f"invented values on {c['name']}: {c.get('values')}"


def test_deep_surfaces_unsupported_constructs_and_sql_risks(tmp_path):
    """AST risk fields (sql_calls/udfs/rdd/external_io) become unsupported_constructs + llm_todos."""
    rel = "src/main/scala/Risky.scala"
    src = _write_source(tmp_path, rel)
    shared = tmp_path / "Validation" / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "analysis.json").write_text(json.dumps({
        "entrypoints": [{"id": "risky", "path": rel}],
        "source_roots": ["src/main/scala"],
        "build_tool": "sbt",
    }), encoding="utf-8")
    _write_ast(tmp_path, [{
        "path": str(src),
        "parse_ok": True,
        "objects": ["Risky"],
        "entrypoints": [{"owner": "Risky", "method": "main"}],
        "reads": [{"call": "table", "args": ["T"]}],
        "writes": [{"call": "saveAsTable", "args": ["OUT"]}],
        "table_refs": ["T", "OUT"],
        "column_refs": ["id"],
        "write_helpers": [],
        "spark_session_created": True,
        "sql_calls": [{"sql": "SELECT * FROM t WHERE d = current_date()", "line": 10,
                       "has_current_date": True, "has_qualify": False}],
        "udfs": [{"name": "my_udf", "line": 12}],
        "rdd_ops": [{"call": "sc.parallelize", "line": 14}],
        "streaming": False,
        "external_io": [{"kind": "jdbc", "import_or_call": "DriverManager.getConnection", "line": 16}],
        "reflection_usage": True,
        "unsupported_constructs": [
            {"kind": "rdd_op", "detail": "sc.parallelize", "line": 14, "phase_b_blocking": True},
            {"kind": "udf", "detail": "my_udf", "line": 12, "phase_b_blocking": False},
            {"kind": "external_io", "detail": "jdbc: DriverManager.getConnection", "line": 16,
             "phase_b_blocking": True},
        ],
    }])
    result = ata.run(tmp_path, mode="deep")
    ep = result["entrypoints"][0]
    assert ep.get("unsupported_constructs")
    kinds = {u["kind"] for u in ep["unsupported_constructs"]}
    assert "rdd_op" in kinds and "udf" in kinds and "external_io" in kinds
    assert ep.get("complete") is False
    todo = (ep.get("llm_todo") or "") + " " + " ".join(result.get("llm_todos") or [])
    assert "CURRENT_DATE" in todo or "current_date" in todo.lower() or "UDF" in todo or "udf" in todo.lower()
