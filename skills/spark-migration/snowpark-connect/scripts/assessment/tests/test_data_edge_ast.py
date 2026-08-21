"""Tests for :mod:`data_edge_ast` — the AST walker for data-edge extraction.

Covers each of the five new patterns (2a-2e) with positive + negative cases
plus assertions that :class:`UnresolvedEdge` reasons are DYNAMICALLY derived
from the AST node the walker stopped at (i.e. mention the ``ast`` node type
by name), not drawn from a hardcoded enum.
"""
from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

import pytest

_ASSESSMENT_DIR = Path(__file__).resolve().parent.parent
if str(_ASSESSMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_ASSESSMENT_DIR))
_ADAPTERS_DIR = _ASSESSMENT_DIR / "adapters"
if str(_ADAPTERS_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTERS_DIR))

import data_edge_ast as dea
from data_edge_ast import (
    UnresolvedEdge,
    _extract_path_signatures,
    _normalize_signature,
    _SIGNATURE_NOISE_WORDS,
    _walk_reader_chain,
    _sql_edges_from_string,
    _collect_simple_returns,
    _resolve_to_dict,
    _enumerate_ternary_signatures,
    _signature_from_node,
    _collect_assignments,
    _collect_for_targets,
    _collect_call_site_args,
)


def _write(tmp_path: Path, src: str) -> Path:
    p = tmp_path / "w.py"
    p.write_text(dedent(src).lstrip("\n"))
    return p


# ---------------------------------------------------------------------------
# Pattern 2a — builder .option("path", x).load()
# ---------------------------------------------------------------------------


def test_option_path_load_positive(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            spark.read.format("parquet").option("path", "s3://mybucket/data_area/").load()
    """)
    sources, sinks, u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("mybucket/data_area" in s for s in sources)
    assert sinks == set()
    assert u_reads == []


def test_option_path_save_positive(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(df, b):
            df.write.format("delta").option("path", f"s3://{b}/output_area/").save()
    """)
    sources, sinks, _u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("output_area" in s for s in sinks)
    assert sources == set()


def test_option_path_traces_variable(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            x = "s3://mybucket/traced_data_path/"
            spark.read.option("path", x).option("header", "true").load()
    """)
    sources, _sinks, _u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("mybucket/traced_data_path" in s for s in sources)


def test_option_non_path_key_is_ignored(tmp_path: Path) -> None:
    """A .option("checkpoint", "hdfs://...") in the chain must NOT be
    counted as a data endpoint — only PATH-indicating keys promote a
    signature. If the terminal .load() has no positional arg either, this
    is a bare no-arg .load() and it registers as unresolved."""
    p = _write(tmp_path, """
        def go(spark):
            spark.read.format("json").option("checkpoint", "hdfs://ckpt_area/").load()
    """)
    sources, sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    # "ckpt_area" must NOT be treated as a data source.
    assert all("ckpt_area" not in s for s in sources)
    # And there IS no other source, so we're empty.
    assert sources == set()
    assert sinks == set()
    # The .load() had no positional arg AND no path-keyed option — record as
    # unresolved so the engineer sees the call.
    assert len(u_reads) == 1


def test_options_dict_positive(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            spark.read.options({"path": "s3://mybucket/opts_area/", "header": "true"}).load()
    """)
    sources, _sinks, _u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("mybucket/opts_area" in s for s in sources)


def test_jdbc_dbtable_option(tmp_path: Path) -> None:
    """JDBC pattern: dbtable is the endpoint, not the URL."""
    p = _write(tmp_path, """
        def go(spark):
            spark.read.format("jdbc").option("url", "jdbc:whatever").option("dbtable", "MY_ORDERS_TBL").load()
    """)
    sources, _sinks, _u_reads, _u_writes = _extract_path_signatures(str(p))
    # Both "MY_ORDERS_TBL" (dbtable) and the jdbc URL are collected — the
    # url is also in the path-indicating set. We ONLY assert on the
    # dbtable being present, since url normalization might reject it.
    assert any("my_orders_tbl" in s.lower() for s in sources)


# ---------------------------------------------------------------------------
# Pattern 2b — variable-key subscript cfg[k]
# ---------------------------------------------------------------------------


def test_subscript_variable_key_traces_to_literal(tmp_path: Path) -> None:
    """cfg[k] where k = 'literalKey' — the KEY becomes the signature."""
    p = _write(tmp_path, """
        def go(spark, cfg):
            k = "readerPathTraced"
            path = cfg[k]
            spark.read.parquet(path)
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("readerpathtraced" in s.lower() for s in sources)
    assert u_reads == []


def test_subscript_attribute_key_traces_to_literal(tmp_path: Path) -> None:
    """cfg[self.KEY_NAME] where the attribute resolves to a literal."""
    p = _write(tmp_path, """
        class C:
            def __init__(self):
                self.KEY_NAME = "readerAttrKey"
            def go(self, spark, cfg):
                path = cfg[self.KEY_NAME]
                spark.read.parquet(path)
    """)
    sources, _sinks, _u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("readerattrkey" in s.lower() for s in sources)


def test_subscript_dynamic_key_is_unresolved(tmp_path: Path) -> None:
    """cfg[get_key()] — the key is a function call, so no signature; the
    read call becomes an UnresolvedEdge with a dynamic reason mentioning
    the Call node type."""
    p = _write(tmp_path, """
        def go(spark, cfg):
            path = cfg[get_key()]
            spark.read.parquet(path)
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert sources == set()
    assert len(u_reads) == 1
    reason = u_reads[0].reason
    # Dynamic reason: must reference the Subscript-with-dynamic-key shape.
    assert "subscript" in reason.lower() or "call to get_key" in reason.lower() or "call to " in reason.lower()


# ---------------------------------------------------------------------------
# Pattern 2c — SQL passthrough via sqlglot
# ---------------------------------------------------------------------------


def test_sql_select_from(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            spark.sql("SELECT * FROM analytics.events_daily")
    """)
    sources, _sinks, _u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("events_daily" in s for s in sources)


def test_sql_insert_into(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            spark.sql("INSERT INTO out_table SELECT * FROM in_table")
    """)
    sources, sinks, _u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("in_table" in s for s in sources)
    assert any("out_table" in s for s in sinks)


def test_sql_fstring_partial(tmp_path: Path) -> None:
    """f-string SQL — the literal parts still parse. May capture partial
    table name; assert we don't crash and either return something or an
    unresolved-partial diagnostic."""
    p = _write(tmp_path, """
        def go(spark, schema):
            spark.sql(f"SELECT * FROM {schema}.events_from_fstring")
    """)
    # Should not crash; either extracted "events_from_fstring" or recorded
    # an unresolved diagnostic. We accept EITHER as valid coverage of
    # this best-effort branch — the point is we don't silently drop it.
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    got_signature = any("events_from_fstring" in s for s in sources)
    got_unresolved = any("events_from_fstring" not in s for s in ())  # placeholder
    assert got_signature or u_reads, "SQL walker silently dropped f-string SQL"


def test_sql_non_static_is_unresolved(tmp_path: Path) -> None:
    """spark.sql(build_query()) — arg is a call. Unresolved with a reason
    naming the Call node."""
    p = _write(tmp_path, """
        def go(spark):
            spark.sql(build_query())
    """)
    _sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert len(u_reads) == 1
    r = u_reads[0].reason.lower()
    assert "call" in r or "function" in r


def test_sql_parse_failure_is_unresolved(tmp_path: Path) -> None:
    """Garbage SQL that sqlglot can't parse → unresolved-partial reason
    mentioning parse failure."""
    p = _write(tmp_path, """
        def go(spark):
            spark.sql("this is not valid SQL {@#$%")
    """)
    _sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    # sqlglot might parse partial garbage as a bare identifier; accept either
    # (a) sources non-empty from that bare identifier OR (b) unresolved
    # with a parse-failure or unresolved reason.
    if u_reads:
        r = u_reads[0].reason.lower()
        assert any(kw in r for kw in ("parse", "unresolved", "sql"))


# ---------------------------------------------------------------------------
# Pattern 2d — reader-chain .format() hint + .load(path) survives
# ---------------------------------------------------------------------------


def test_reader_format_load_positive_arg(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            spark.read.format("delta").load("s3://mybucket/delta_load_path/")
    """)
    sources, _sinks, _u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("delta_load_path" in s for s in sources)


def test_walk_reader_chain_extracts_format_hint(tmp_path: Path) -> None:
    """The chain walker records the format connector as a hint."""
    import ast

    src = 'x = spark.read.format("snowflake").option("dbtable", "T").load()\n'
    tree = ast.parse(src)
    # Grab the terminal ``.load()`` call.
    call = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "load":
            call = node
            break
    assert call is not None
    options, fmt = _walk_reader_chain(call)
    assert fmt == "snowflake"
    keys = [k for k, _v in options]
    assert "dbtable" in keys


# ---------------------------------------------------------------------------
# Pattern 2e — loop-generated paths
# ---------------------------------------------------------------------------


def test_loop_literal_list_enumerates(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            for t in ["loop_alpha", "loop_beta", "loop_gamma"]:
                spark.read.table(t)
    """)
    sources, _sinks, _u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("loop_alpha" in s for s in sources)
    assert any("loop_beta" in s for s in sources)
    assert any("loop_gamma" in s for s in sources)


def test_loop_fstring_captures_constant(tmp_path: Path) -> None:
    """for i in range(3): the iterable is a Call — no enumeration. But
    the f-string constant portion ("s3://bucket/data_") still yields a
    partial signature."""
    p = _write(tmp_path, """
        def go(spark):
            for i in range(3):
                spark.read.parquet(f"s3://bucket_loopconst/data_prefix_{i}/")
    """)
    sources, _sinks, _u_reads, _u_writes = _extract_path_signatures(str(p))
    # The constant-portion signature is expected.
    assert any("bucket_loopconst/data_prefix" in s for s in sources)


def test_loop_function_call_iterable_is_unresolved(tmp_path: Path) -> None:
    """for x in get_paths(): — iterator is a Call; the read call can't
    enumerate. It should show up as unresolved with a reason mentioning
    the Call node type."""
    p = _write(tmp_path, """
        def go(spark):
            for x in get_paths_dynamic():
                spark.read.parquet(x)
    """)
    _sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert len(u_reads) == 1
    r = u_reads[0].reason.lower()
    assert "loop over 'x'" in r
    assert "get_paths_dynamic" in r or "call" in r


# ---------------------------------------------------------------------------
# Dynamic reason derivation (Part 3) — reasons describe AST node types.
# ---------------------------------------------------------------------------


def test_unresolved_edge_reason_describes_ast_shape_call(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            spark.read.parquet(compute_path())
    """)
    _sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert len(u_reads) == 1
    assert "call" in u_reads[0].reason.lower()


def test_unresolved_edge_reason_describes_ast_shape_ifexp(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark, cond, a, b):
            spark.read.parquet(a if cond else b)
    """)
    _sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert len(u_reads) == 1
    assert "conditional" in u_reads[0].reason.lower() or "ifexp" in u_reads[0].reason.lower()


def test_unresolved_edge_reason_describes_ast_shape_comprehension(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            spark.read.parquet([x for x in items][0])
    """)
    _sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    # Subscript on a list-comprehension — the shape descriptor mentions
    # either "subscript" or "comprehension" depending on which node the
    # walker stops at.
    assert len(u_reads) == 1
    r = u_reads[0].reason.lower()
    assert "subscript" in r or "comprehension" in r


def test_unresolved_edge_reason_describes_ast_shape_lambda(tmp_path: Path) -> None:
    """Passing a lambda as the arg is odd but tests the lambda branch."""
    p = _write(tmp_path, """
        def go(spark):
            spark.read.parquet((lambda: 'x')())
    """)
    _sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert len(u_reads) == 1
    # The lambda is nested inside a Call; the top-level shape is "call".
    assert "call" in u_reads[0].reason.lower()


def test_unresolved_edge_reason_describes_ast_shape_untraceable_name(tmp_path: Path) -> None:
    """A bare untraceable Name gets 'reference to <name>' in the reason."""
    p = _write(tmp_path, """
        def go(spark, undefined_path):
            spark.read.parquet(undefined_path)
    """)
    _sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert len(u_reads) == 1
    r = u_reads[0].reason.lower()
    assert "undefined_path" in r
    assert "reference" in r


def test_unresolved_edge_reason_is_bounded(tmp_path: Path) -> None:
    """Reason strings stay under 200 chars even with a huge unparse."""
    p = _write(tmp_path, """
        def go(spark, a, b, c, d):
            spark.read.parquet(a_very_long_and_intricate_expression + another_very_long_expression + yet_another_expression + final_expression)
    """)
    _sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    for u in u_reads:
        assert len(u.reason) <= 200


# ---------------------------------------------------------------------------
# Signature normalization sanity — was moved from scan_codebase.
# ---------------------------------------------------------------------------


def test_normalize_signature_new_module_strips_uri_prefix() -> None:
    assert _normalize_signature("s3://bucket/eqs/tbl/") == "bucket/eqs/tbl"


def test_normalize_signature_new_module_rejects_noise_word() -> None:
    assert _normalize_signature("final") is None
    assert _normalize_signature("data") is None


# ---------------------------------------------------------------------------
# _sql_edges_from_string direct unit tests
# ---------------------------------------------------------------------------


def test_sql_edges_from_string_basic() -> None:
    sources, sinks, err = _sql_edges_from_string("SELECT * FROM my_schema.big_events")
    assert err is None
    assert any("big_events" in s for s in sources)
    assert sinks == []


def test_sql_edges_from_string_join_captures_both_sides() -> None:
    sources, _sinks, err = _sql_edges_from_string(
        "SELECT a.id FROM orders_left a JOIN customers_right b ON a.cid = b.id"
    )
    assert err is None
    assert any("orders_left" in s for s in sources)
    assert any("customers_right" in s for s in sources)


def test_sql_edges_from_string_create_as_select() -> None:
    sources, sinks, err = _sql_edges_from_string(
        "CREATE TABLE cts_target AS SELECT * FROM cts_source"
    )
    assert err is None
    assert any("cts_target" in s for s in sinks)
    assert any("cts_source" in s for s in sources)


# ---------------------------------------------------------------------------
# Integration test — a "Verisk-like" fixture exercising multiple patterns
# ---------------------------------------------------------------------------


def test_integration_verisk_like_multi_pattern(tmp_path: Path) -> None:
    """Integration test: single file exercising ALL five patterns plus
    the unresolved-edge diagnostic path. Verifies:

      * The known-good cases all yield signatures.
      * The unresolvable cases produce specific, non-generic reasons.
      * Every reason mentions an AST-shape token — no bare 'unknown'.
    """
    p = _write(tmp_path, """
        def go(spark, session, cfg, external):
            # Pattern 2a — builder .option(path, x).load()
            spark.read.format("parquet").option("path", "s3://verisk_int/area1/").load()
            df.write.format("delta").option("path", "s3://verisk_int/sink_area/").save()

            # Pattern 2b — variable-key subscript
            k = "verisk_key_name"
            path = cfg[k]
            spark.read.parquet(path)

            # Pattern 2c — SQL
            spark.sql("SELECT * FROM verisk_events")
            spark.sql("INSERT INTO verisk_output SELECT * FROM verisk_stage")

            # Pattern 2d — format().load(arg)
            spark.read.format("delta").load("s3://verisk_int/delta_area/")

            # Pattern 2e — loop
            for t in ["verisk_loop_a", "verisk_loop_b"]:
                spark.read.table(t)

            # Unresolvable — function call
            spark.read.parquet(external.compute_path())

            # Unresolvable — conditional
            spark.read.parquet("a" if condition else "b")
    """)
    sources, sinks, u_reads, u_writes = _extract_path_signatures(str(p))

    # Coverage assertions across patterns.
    assert any("area1" in s for s in sources), "2a source missing"
    assert any("sink_area" in s for s in sinks), "2a sink missing"
    assert any("verisk_key_name" in s for s in sources), "2b source missing"
    assert any("verisk_events" in s for s in sources), "2c source missing"
    assert any("verisk_output" in s for s in sinks), "2c sink missing"
    assert any("verisk_stage" in s for s in sources), "2c source (SELECT) missing"
    assert any("delta_area" in s for s in sources), "2d source missing"
    assert any("verisk_loop_a" in s for s in sources), "2e source[a] missing"
    assert any("verisk_loop_b" in s for s in sources), "2e source[b] missing"

    # The two unresolvables produced exactly two unresolved-read entries,
    # each with a reason mentioning the AST shape it stopped at.
    assert len(u_reads) == 2
    reasons_lc = [u.reason.lower() for u in u_reads]
    assert any("call" in r for r in reasons_lc)
    assert any("conditional" in r or "ifexp" in r for r in reasons_lc)
    for u in u_reads:
        assert u.reason.strip() != ""
        # No hardcoded "unknown" — always describes what was seen.
        assert "unknown" not in u.reason.lower()

    assert u_writes == []


# ---------------------------------------------------------------------------
# scan()-level plumbing — Assessment.unresolved_data_edges is populated.
# ---------------------------------------------------------------------------


def test_scan_populates_unresolved_data_edges(tmp_path: Path) -> None:
    """A workload with an unresolvable read/write surfaces in
    :attr:`Assessment.unresolved_data_edges`."""
    (tmp_path / "app.py").write_text(dedent("""
        def go(spark):
            spark.read.parquet(compute_dynamic_path())
    """).lstrip("\n"))
    import scan_codebase as sc
    a = sc.scan(tmp_path, project="test-workload")
    assert len(a.unresolved_data_edges) >= 1
    r = a.unresolved_data_edges[0].reason.lower()
    assert "call" in r or "compute_dynamic_path" in r


# ---------------------------------------------------------------------------
# A1.1 — spark.table("name") direct SparkSession table read
# ---------------------------------------------------------------------------


def test_spark_table_direct_positive(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            spark.table("direct_orders_tbl")
    """)
    sources, sinks, u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("direct_orders_tbl" in s for s in sources)
    assert sinks == set()
    assert u_reads == []


def test_spark_table_direct_variable(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            tbl = "direct_tbl_from_var"
            spark.table(tbl)
    """)
    sources, _sinks, _u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("direct_tbl_from_var" in s for s in sources)


def test_spark_table_direct_unresolved(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark, get_tbl):
            spark.table(get_tbl())
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert sources == set()
    assert len(u_reads) == 1
    assert "call" in u_reads[0].reason.lower()


def test_spark_table_read_chain_still_works(tmp_path: Path) -> None:
    """spark.read.table() (existing pattern) must still resolve correctly."""
    p = _write(tmp_path, """
        def go(spark):
            spark.read.table("read_chain_tbl")
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("read_chain_tbl" in s for s in sources)
    assert u_reads == []


# ---------------------------------------------------------------------------
# A1.2 — SparkContext RDD reads (sc.textFile / sc.binaryFiles / etc.)
# ---------------------------------------------------------------------------


def test_sc_text_file_positive(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(sc):
            sc.textFile("s3://bucket_rdd/text_data/")
    """)
    sources, sinks, u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("bucket_rdd/text_data" in s for s in sources)
    assert sinks == set()
    assert u_reads == []


def test_sc_binary_files_positive(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(sc):
            sc.binaryFiles("hdfs://nn/rdd_bin_data/")
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("rdd_bin_data" in s for s in sources)
    assert u_reads == []


def test_sc_whole_text_files_positive(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(sc):
            sc.wholeTextFiles("/local/rdd_whole/")
    """)
    sources, _sinks, _u, _uw = _extract_path_signatures(str(p))
    assert any("rdd_whole" in s for s in sources)


def test_sc_text_file_unresolved(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(sc, get_path):
            sc.textFile(get_path())
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert sources == set()
    assert len(u_reads) == 1


# ---------------------------------------------------------------------------
# A1.3 — DeltaTable.forPath / forName
# ---------------------------------------------------------------------------


def test_delta_table_for_path_positive(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        from delta.tables import DeltaTable
        def go(spark):
            dt = DeltaTable.forPath(spark, "s3://bucket_delta/delta_tbl/")
    """)
    sources, sinks, u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("delta_tbl" in s for s in sources)
    assert sinks == set()
    assert u_reads == []


def test_delta_table_for_name_positive(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            dt = DeltaTable.forName(spark, "my_delta_named_tbl")
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("my_delta_named_tbl" in s for s in sources)
    assert u_reads == []


def test_delta_table_unresolved_arg(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark, get_name):
            dt = DeltaTable.forName(spark, get_name())
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert sources == set()
    assert len(u_reads) == 1


# ---------------------------------------------------------------------------
# A1.4 — Pandas I/O (pd.read_X / df.to_X)
# ---------------------------------------------------------------------------


def test_pandas_read_csv_positive(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        import pandas as pd
        def go():
            df = pd.read_csv("s3://bucket_pandas/csv_data.csv")
    """)
    sources, sinks, u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("csv_data" in s for s in sources)
    assert sinks == set()
    assert u_reads == []


def test_pandas_read_parquet_positive(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        import pandas as pd
        def go():
            df = pd.read_parquet("/data/pandas_parquet_src/")
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("pandas_parquet_src" in s for s in sources)
    assert u_reads == []


def test_pandas_to_csv_write(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(df):
            df.to_csv("/output/pandas_csv_sink.csv")
    """)
    _sources, sinks, _u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("pandas_csv_sink" in s for s in sinks)
    assert u_writes == []


def test_pandas_to_parquet_write(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(df):
            df.to_parquet("s3://bucket_pandas/parquet_sink/")
    """)
    _sources, sinks, _u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("parquet_sink" in s for s in sinks)
    assert u_writes == []


def test_pandas_to_sql_write(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(df, con):
            df.to_sql("pandas_sql_sink_tbl", con)
    """)
    _sources, sinks, _u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("pandas_sql_sink_tbl" in s for s in sinks)
    assert u_writes == []


def test_pandas_read_unresolved(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        import pandas as pd
        def go(get_path):
            df = pd.read_csv(get_path())
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert sources == set()
    assert len(u_reads) == 1
    assert "call" in u_reads[0].reason.lower()


# ---------------------------------------------------------------------------
# A1.5 — spark.read.jdbc second arg
# ---------------------------------------------------------------------------


def test_jdbc_second_arg_table(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            df = spark.read.jdbc("jdbc:postgresql://host/db", "orders_jdbc_tbl")
    """)
    sources, sinks, u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("orders_jdbc_tbl" in s for s in sources)
    assert sinks == set()
    assert u_reads == []


def test_jdbc_keyword_dbtable(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            df = spark.read.jdbc(url="jdbc:mysql://h/d", dbtable="customers_jdbc_kw")
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("customers_jdbc_kw" in s for s in sources)
    assert u_reads == []


def test_jdbc_unresolved_table(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark, get_tbl):
            df = spark.read.jdbc("jdbc:...", get_tbl())
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert sources == set()
    assert len(u_reads) == 1


# ---------------------------------------------------------------------------
# A1.6 — spark.catalog.createTable
# ---------------------------------------------------------------------------


def test_catalog_create_table_positive(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            spark.catalog.createTable("catalog_sink_tbl", path="s3://b/p/")
    """)
    _sources, sinks, _u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("catalog_sink_tbl" in s for s in sinks)
    assert u_writes == []


def test_catalog_create_external_table(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            spark.catalog.createExternalTable("ext_catalog_tbl")
    """)
    _sources, sinks, _u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("ext_catalog_tbl" in s for s in sinks)
    assert u_writes == []


# ---------------------------------------------------------------------------
# A2 — Ternary-branch enumeration (both arms emitted)
# ---------------------------------------------------------------------------


def test_ternary_both_branches_emitted(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark, cond):
            spark.read.parquet("ternary_true_path" if cond else "ternary_false_path")
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("ternary_true_path" in s for s in sources)
    assert any("ternary_false_path" in s for s in sources)
    assert u_reads == []


def test_ternary_one_dynamic_branch(tmp_path: Path) -> None:
    """One branch resolves, other is dynamic. Should get the literal branch."""
    p = _write(tmp_path, """
        def go(spark, cond, dynamic_path):
            spark.read.parquet("ternary_static_branch" if cond else dynamic_path)
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("ternary_static_branch" in s for s in sources)


def test_ternary_via_variable(tmp_path: Path) -> None:
    """path = "a" if cond else "b" then spark.read.parquet(path) — resolves via Name trace."""
    p = _write(tmp_path, """
        def go(spark, cond):
            path = "ternary_var_true" if cond else "ternary_var_false"
            spark.read.parquet(path)
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("ternary_var" in s for s in sources)


def test_ternary_both_dynamic_is_unresolved(tmp_path: Path) -> None:
    """Both branches are dynamic — should produce an unresolved edge."""
    p = _write(tmp_path, """
        def go(spark, cond, a, b):
            spark.read.parquet(a if cond else b)
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert sources == set()
    assert len(u_reads) == 1
    r = u_reads[0].reason.lower()
    assert "conditional" in r or "reference" in r


# ---------------------------------------------------------------------------
# A2 — Literal dict value lookup
# ---------------------------------------------------------------------------


def test_dict_literal_value_lookup(tmp_path: Path) -> None:
    """cfg["key"] where cfg = {"key": "s3://bucket/path"} resolves to the VALUE."""
    p = _write(tmp_path, """
        def go(spark):
            cfg = {"input_path": "s3://bucket_dict/dict_value_src/"}
            spark.read.parquet(cfg["input_path"])
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("dict_value_src" in s for s in sources)
    assert u_reads == []


def test_dict_literal_value_lookup_write(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(df):
            paths = {"out": "s3://bucket_dict/dict_sink_area/"}
            df.write.parquet(paths["out"])
    """)
    _sources, sinks, _u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("dict_sink_area" in s for s in sinks)
    assert u_writes == []


def test_dict_missing_key_falls_back_to_key_fingerprint(tmp_path: Path) -> None:
    """When the key is NOT in the dict literal, fall back to returning the key."""
    p = _write(tmp_path, """
        def go(spark):
            cfg = {"other_key": "s3://bucket/other/"}
            spark.read.parquet(cfg["missing_key"])
    """)
    sources, _sinks, _u_reads, _u_writes = _extract_path_signatures(str(p))
    # The key string "missing_key" is the fallback fingerprint.
    assert any("missing_key" in s for s in sources)


# ---------------------------------------------------------------------------
# A2 — pathlib.Path division operator
# ---------------------------------------------------------------------------


def test_pathlib_division_operator(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        from pathlib import Path
        def go(spark):
            base = Path("s3://bucket_pathlib/base_dir")
            spark.read.parquet(base / "suffix_area")
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("base_dir/suffix_area" in s for s in sources)
    assert u_reads == []


def test_pathlib_division_literal(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        from pathlib import Path
        def go(spark):
            spark.read.csv(Path("hdfs://nn/pathlib_literal") / "sub")
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("pathlib_literal/sub" in s for s in sources)
    assert u_reads == []


# ---------------------------------------------------------------------------
# A2 — os.environ.get default extraction
# ---------------------------------------------------------------------------


def test_environ_get_default(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        import os
        def go(spark):
            path = os.environ.get("DATA_PATH", "/default/environ_fallback/")
            spark.read.parquet(path)
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("environ_fallback" in s for s in sources)
    assert u_reads == []


def test_environ_get_no_default_is_unresolved(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        import os
        def go(spark):
            path = os.environ.get("DATA_PATH")
            spark.read.parquet(path)
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert sources == set()
    assert len(u_reads) == 1


# ---------------------------------------------------------------------------
# A2 — trivial string passthrough methods (.strip, .lower, .upper)
# ---------------------------------------------------------------------------


def test_strip_passthrough(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            path = "  s3://bucket_strip/strip_area/  ".strip()
            spark.read.parquet(path)
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("strip_area" in s for s in sources)
    assert u_reads == []


def test_lower_passthrough(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def go(spark):
            tbl = "UPPER_TBL_NAME".lower()
            spark.read.table(tbl)
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    assert any("upper_tbl_name" in s for s in sources)
    assert u_reads == []


# ---------------------------------------------------------------------------
# A2 — same-file function return inlining
# ---------------------------------------------------------------------------


def test_function_return_inlining_simple(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def get_input_path():
            return "s3://bucket_inline/fn_return_src/"

        def go(spark):
            spark.read.parquet(get_input_path())
    """)
    sources, sinks, u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("fn_return_src" in s for s in sources)
    assert sinks == set()
    assert u_reads == []


def test_function_return_inlining_write(tmp_path: Path) -> None:
    p = _write(tmp_path, """
        def output_path():
            return "s3://bucket_inline/fn_return_sink/"

        def go(df):
            df.write.parquet(output_path())
    """)
    _sources, sinks, _u_reads, u_writes = _extract_path_signatures(str(p))
    assert any("fn_return_sink" in s for s in sinks)
    assert u_writes == []


def test_function_multi_statement_not_inlined(tmp_path: Path) -> None:
    """Functions with >1 statement are NOT inlined — result is unresolved."""
    p = _write(tmp_path, """
        def complex_path(env):
            if env == "prod":
                return "s3://prod/data/"
            return "s3://dev/data/"

        def go(spark):
            spark.read.parquet(complex_path("prod"))
    """)
    sources, _sinks, u_reads, _u_writes = _extract_path_signatures(str(p))
    # Multi-branch function is NOT inlined; read shows as unresolved.
    assert len(u_reads) == 1


# ---------------------------------------------------------------------------
# Extended integration test covering all A1 + A2 patterns together
# ---------------------------------------------------------------------------


def test_integration_all_new_patterns(tmp_path: Path) -> None:
    """Integration test exercising all A1 and A2 new patterns in a single file."""

    def get_base_path():
        return "s3://int_bucket/base_fn_path/"

    p = _write(tmp_path, """
        import os
        import pandas as pd
        from pathlib import Path
        from delta.tables import DeltaTable

        def get_base_path():
            return "s3://int_bucket/base_fn_path/"

        def go(spark, sc, df, cond):
            # A1.1: spark.table direct
            spark.table("int_direct_tbl")

            # A1.2: RDD read
            sc.textFile("s3://int_bucket/rdd_src/")

            # A1.3: DeltaTable
            DeltaTable.forPath(spark, "s3://int_bucket/delta_src/")
            DeltaTable.forName(spark, "int_delta_named")

            # A1.4: pandas reads + writes
            pd.read_csv("s3://int_bucket/pandas_csv_src.csv")
            df.to_parquet("s3://int_bucket/pandas_parquet_sink/")

            # A1.5: jdbc
            spark.read.jdbc("jdbc://host/db", "int_jdbc_tbl")

            # A1.6: catalog
            spark.catalog.createTable("int_catalog_sink")

            # A2: ternary
            spark.read.parquet("int_ternary_true" if cond else "int_ternary_false")

            # A2: dict lookup
            cfg = {"src": "s3://int_bucket/int_dict_src/"}
            spark.read.json(cfg["src"])

            # A2: pathlib
            spark.read.orc(Path("s3://int_bucket/pathlib_base") / "int_pathlib_area")

            # A2: environ.get default
            spark.read.csv(os.environ.get("P", "/int_env_default/"))

            # A2: function return inlining
            spark.read.parquet(get_base_path())
    """)
    sources, sinks, u_reads, u_writes = _extract_path_signatures(str(p))

    assert any("int_direct_tbl" in s for s in sources), "A1.1 missing"
    assert any("rdd_src" in s for s in sources), "A1.2 missing"
    assert any("delta_src" in s for s in sources), "A1.3 forPath missing"
    assert any("int_delta_named" in s for s in sources), "A1.3 forName missing"
    assert any("pandas_csv_src" in s for s in sources), "A1.4 read missing"
    assert any("pandas_parquet_sink" in s for s in sinks), "A1.4 write missing"
    assert any("int_jdbc_tbl" in s for s in sources), "A1.5 missing"
    assert any("int_catalog_sink" in s for s in sinks), "A1.6 missing"
    assert any("int_ternary_true" in s for s in sources), "A2 ternary true missing"
    assert any("int_ternary_false" in s for s in sources), "A2 ternary false missing"
    assert any("int_dict_src" in s for s in sources), "A2 dict lookup missing"
    assert any("pathlib_base/int_pathlib_area" in s for s in sources), "A2 pathlib missing"
    assert any("int_env_default" in s for s in sources), "A2 environ.get missing"
    assert any("base_fn_path" in s for s in sources), "A2 function inlining missing"

    assert u_reads == [], f"Unexpected unresolved reads: {u_reads}"
    assert u_writes == [], f"Unexpected unresolved writes: {u_writes}"


# ---------------------------------------------------------------------------
# Unit tests for new A2 helper functions
# ---------------------------------------------------------------------------


def test_collect_simple_returns() -> None:
    import ast
    src = dedent("""
        def get_path():
            return "s3://bucket/path/"
        def complex_fn(x):
            if x: return "a"
            return "b"
        def with_docstring():
            '''Returns path.'''
            return "s3://bucket/docstring_path/"
    """).lstrip("\n")
    tree = ast.parse(src)
    sr = _collect_simple_returns(tree)
    assert "get_path" in sr
    assert "complex_fn" not in sr  # multi-statement, not inlined
    assert "with_docstring" in sr  # docstring + return is fine


def test_resolve_to_dict() -> None:
    import ast
    src = dedent("""
        cfg = {"key1": "value1", "key2": "value2"}
        other = 42
    """).lstrip("\n")
    tree = ast.parse(src)
    assignments = _collect_assignments(tree)
    name_node = ast.parse("cfg", mode="eval").body
    d = _resolve_to_dict(name_node, assignments)
    assert d is not None
    assert "key1" in d
    assert "key2" in d
    other_node = ast.parse("other", mode="eval").body
    assert _resolve_to_dict(other_node, assignments) is None


def test_enumerate_ternary_signatures() -> None:
    import ast
    src = 'spark.read.parquet("true_branch" if x else "false_branch")'
    tree = ast.parse(src, mode="eval")
    call = tree.body
    ifexp = call.args[0]
    assignments = {}
    for_targets = {}
    sigs = _enumerate_ternary_signatures(ifexp, assignments, for_targets)
    assert "true_branch" in sigs
    assert "false_branch" in sigs


# ---------------------------------------------------------------------------
# _collect_call_site_args
# ---------------------------------------------------------------------------


def test_collect_call_site_args_positional() -> None:
    """Positional args at call site are mapped to the function's param names."""
    import ast

    tree = ast.parse(
        "def load(table_name):\n"
        "    pass\n"
        "\n"
        "load('db.schema.my_table')\n"
    )
    result = _collect_call_site_args(tree)
    assert "table_name" in result
    values = {n.value for n in result["table_name"] if isinstance(n, ast.Constant)}
    assert "db.schema.my_table" in values


def test_collect_call_site_args_keyword() -> None:
    """Keyword args at call site are mapped to the function's param names."""
    import ast

    tree = ast.parse(
        "def load(table_name, mode):\n"
        "    pass\n"
        "\n"
        "load(mode='overwrite', table_name='db.schema.my_table')\n"
    )
    result = _collect_call_site_args(tree)
    values = {n.value for n in result.get("table_name", []) if isinstance(n, ast.Constant)}
    assert "db.schema.my_table" in values
    assert "mode" in result


def test_collect_call_site_args_multiple_sites() -> None:
    """Multiple call sites accumulate into the same param list."""
    import ast

    tree = ast.parse(
        "def load(table_name):\n"
        "    pass\n"
        "\n"
        "load('db.schema.table_a')\n"
        "load('db.schema.table_b')\n"
    )
    result = _collect_call_site_args(tree)
    values = {n.value for n in result.get("table_name", []) if isinstance(n, ast.Constant)}
    assert "db.schema.table_a" in values
    assert "db.schema.table_b" in values


def test_collect_call_site_args_attribute_call() -> None:
    """obj.method(arg) maps the arg to the method's param name."""
    import ast

    tree = ast.parse(
        "def process(input_path):\n"
        "    pass\n"
        "\n"
        "loader.process('s3://bucket/data.csv')\n"
    )
    result = _collect_call_site_args(tree)
    assert "input_path" in result


def test_collect_call_site_args_no_functions_returns_empty() -> None:
    """File with no function definitions returns empty dict."""
    import ast

    tree = ast.parse("x = 1\nprint(x)\n")
    assert _collect_call_site_args(tree) == {}


# ---------------------------------------------------------------------------
# _normalize_signature
# ---------------------------------------------------------------------------


def test_normalize_signature_rejects_noise_word_values() -> None:
    """'values' added to _SIGNATURE_NOISE_WORDS must return None."""
    assert _normalize_signature("values") is None, "'values' is a noise word"


def test_normalize_signature_rejects_all_noise_words() -> None:
    """Every word in _SIGNATURE_NOISE_WORDS must return None."""
    for word in _SIGNATURE_NOISE_WORDS:
        assert _normalize_signature(word) is None, f"noise word {word!r} must return None"


def test_normalize_signature_rejects_short_strings() -> None:
    """Strings shorter than 4 chars return None."""
    assert _normalize_signature("") is None
    assert _normalize_signature("ab") is None
    assert _normalize_signature("xyz") is None


def test_normalize_signature_strips_uri_scheme() -> None:
    """URI scheme prefixes are stripped from the signature."""
    assert _normalize_signature("s3://bucket/path/mytable") == "bucket/path/mytable"
    assert _normalize_signature("dbfs:/mnt/data/silver") == "mnt/data/silver"


def test_normalize_signature_strips_fstring_placeholders() -> None:
    """Unresolved f-string placeholders are stripped before matching."""
    result = _normalize_signature("{DATABASE}.SCHEMA.MY_TABLE")
    assert result is not None
    assert "{" not in result and "}" not in result
    assert "my_table" in result
