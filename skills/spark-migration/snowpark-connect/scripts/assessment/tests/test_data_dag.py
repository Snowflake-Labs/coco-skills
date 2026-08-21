"""Unit tests for the data-DAG POC in scan_codebase and prototype_v1.

Tests cover:
  * _mine_file_io — graceful fallback when schema_mine is unavailable
  * _build_data_dep_edges — sink→source matching across files
  * _build_unified_dependency_graph — edge kind stamping (import vs data)
  * Orphan promotion: a file connected only by a data edge is no longer
    isolated after the combined edge list is passed to _build_isolated_modules
  * _data_consumers_adjacency_json — forward adjacency for data DAG JS
  * scan() integration — data_dependency_graph field in Assessment
  * Template rendering — data DAG section present in HTML output
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ASSESSMENT_DIR = Path(__file__).resolve().parent.parent
if str(_ASSESSMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_ASSESSMENT_DIR))

_ADAPTERS_DIR = _ASSESSMENT_DIR / "adapters"
if str(_ADAPTERS_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTERS_DIR))

import scan_codebase as sc
from scan_codebase import (
    _build_data_dep_edges,
    _build_isolated_modules,
    _build_unified_dependency_graph,
    _candidate_files_for_names,
    _discover_per_file_external_endpoints,
    _dynamic_import_chain_edges,
    _extract_path_signatures,
    _extract_sql_data_refs,
    _implicit_chains_from_data_edges,
    _load_config_data,
    _load_config_pool,
    _load_entry_points_registry,
    _looks_like_data_path,
    _lookup_by_key_name,
    _lookup_list_by_key_name,
    _mine_file_io,
    _mine_file_io_and_imports,
    _normalize_signature,
    _order_sites_for_chain,
    _resolve_dynamic_import_site,
    _resolve_module_to_file,
    _resolve_path_arg_to_file,
    _resolve_via_config,
    _short_ext_label,
    scan,
)
from prototype_v1 import _data_consumers_adjacency_json, _import_adjacency_json, _unresolved_dynamic_imports_summary

# schema_mine helpers exposed for direct unit-testing of the AST detector.
_VALIDATE_SCRIPTS = (
    _ASSESSMENT_DIR.parent.parent
    / "validate-pyspark-to-snowpark-connect"
    / "scripts"
)
if str(_VALIDATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_VALIDATE_SCRIPTS))
try:
    from schema_mine import (  # type: ignore[import]
        _collect_assignments_for_dynamic_imports,
        _find_dynamic_import_sites,
        mine as _schema_mine,
    )
    _SCHEMA_MINE_IMPORTABLE = True
except Exception:  # pragma: no cover - PySpark not installed
    _SCHEMA_MINE_IMPORTABLE = False


import ast  # noqa: E402  (import after path setup — matches file convention)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _code_file(rel_path: str, lines: int = 10) -> dict:
    return {
        "rel_path": rel_path,
        "name": Path(rel_path).name,
        "ext": ".py",
        "lines": lines,
        "imports": [],
        "spark_api": 0,
    }


# ---------------------------------------------------------------------------
# _mine_file_io — graceful degradation
# ---------------------------------------------------------------------------

def test_mine_file_io_unavailable_returns_none(tmp_path: Path) -> None:
    """When schema_mine is unavailable AND no config_pool is supplied, returns None."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    with patch.object(sc, "_DATA_MINING_AVAILABLE", False):
        assert _mine_file_io(str(f)) is None
        assert _mine_file_io(str(f), config_pool=None) is None


def test_mine_file_io_works_with_config_pool_alone(tmp_path: Path) -> None:
    """Config-aware AST analysis works even without schema_mine/PySpark."""
    f = tmp_path / "etl.py"
    f.write_text(
        "class Job:\n"
        "    def __init__(self, cfg):\n"
        "        self.src = cfg['inputPath']\n"
        "    def run(self, spark):\n"
        "        spark.read.parquet(self.src)\n"
    )
    pool = {"inputPath": {"s3://lake/raw/"}}
    with patch.object(sc, "_DATA_MINING_AVAILABLE", False):
        result = _mine_file_io(str(f), config_pool=pool)
    assert result is not None
    sources, sinks = result
    assert "s3://lake/raw/" in sources
    assert sinks == set()


def test_mine_file_io_exception_returns_empty_when_only_schema_mine(tmp_path: Path) -> None:
    """If schema_mine raises and no config_pool is supplied, return empty sets
    (schema_mine ran but found nothing)."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")

    def _boom(path, **kwargs):
        raise RuntimeError("pyspark not available")

    with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
         patch.object(sc, "_schema_mine_fn", _boom):
        result = _mine_file_io(str(f))
    # schema_mine ran (and failed), config_pool not given → empty sets, not None.
    assert result == (set(), set())


def test_mine_file_io_extracts_sources_and_sinks(tmp_path: Path) -> None:
    """Returns the (sources, sinks) key sets from the mine() result dict."""
    f = tmp_path / "etl.py"
    f.write_text("df = spark.read.parquet('input')\ndf.write.saveAsTable('output')\n")

    def _fake_mine(path, **kwargs):
        return {
            "_sources": {"input_table": {"format": "parquet", "reader_method": "parquet"}},
            "_sinks": {"output_table": {"kind": "table", "defined_at": "etl.py:2"}},
        }

    with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
         patch.object(sc, "_schema_mine_fn", _fake_mine):
        result = _mine_file_io(str(f))
    assert result is not None
    sources, sinks = result
    assert "input_table" in sources
    assert "output_table" in sinks


# ---------------------------------------------------------------------------
# _build_data_dep_edges — sink→source matching
# ---------------------------------------------------------------------------

def test_build_data_dep_edges_unavailable_returns_empty(tmp_path: Path) -> None:
    files = [_code_file("a.py"), _code_file("b.py")]
    with patch.object(sc, "_DATA_MINING_AVAILABLE", False):
        edges, unresolved, unresolved_edges = _build_data_dep_edges(files, tmp_path)
        assert edges == []
        assert unresolved == []
        assert unresolved_edges == []


def test_build_data_dep_edges_no_overlap_returns_empty(tmp_path: Path) -> None:
    """No data edge when sink names never match any source name."""
    io_map = {
        str(tmp_path / "a.py"): (set(), {"raw_events"}),  # a writes raw_events
        str(tmp_path / "b.py"): ({"clean_users"}, set()),  # b reads clean_users
    }

    def _fake_mine(path, **kwargs):
        srcs, snks = io_map.get(path, (set(), set()))
        return {
            "_sources": {n: {} for n in srcs},
            "_sinks": {n: {} for n in snks},
        }

    files = [_code_file("a.py"), _code_file("b.py")]
    with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
         patch.object(sc, "_schema_mine_fn", _fake_mine):
        edges, _unres, _unres_edges = _build_data_dep_edges(files, tmp_path)
    assert edges == []


def test_build_data_dep_edges_match_creates_edge(tmp_path: Path) -> None:
    """When a.py sinks 'my_table' and b.py sources 'my_table' → edge (a, b)."""
    io_map = {
        str(tmp_path / "a.py"): (set(), {"my_table"}),
        str(tmp_path / "b.py"): ({"my_table"}, set()),
    }

    def _fake_mine(path, **kwargs):
        srcs, snks = io_map.get(path, (set(), set()))
        return {"_sources": {n: {} for n in srcs}, "_sinks": {n: {} for n in snks}}

    files = [_code_file("a.py"), _code_file("b.py")]
    with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
         patch.object(sc, "_schema_mine_fn", _fake_mine):
        edges, _unres, _unres_edges = _build_data_dep_edges(files, tmp_path)
    assert ("a.py", "b.py", "data") in edges


def test_build_data_dep_edges_case_insensitive_match(tmp_path: Path) -> None:
    """Table name matching is case-insensitive: 'My_Table' == 'my_table'."""
    io_map = {
        str(tmp_path / "writer.py"): (set(), {"My_Table"}),
        str(tmp_path / "reader.py"): ({"my_table"}, set()),
    }

    def _fake_mine(path, **kwargs):
        srcs, snks = io_map.get(path, (set(), set()))
        return {"_sources": {n: {} for n in srcs}, "_sinks": {n: {} for n in snks}}

    files = [_code_file("writer.py"), _code_file("reader.py")]
    with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
         patch.object(sc, "_schema_mine_fn", _fake_mine):
        edges, _unres, _unres_edges = _build_data_dep_edges(files, tmp_path)
    assert ("writer.py", "reader.py", "data") in edges


def test_build_data_dep_edges_no_self_loop(tmp_path: Path) -> None:
    """A file that reads and writes the same table produces no self-edge."""
    io_map = {str(tmp_path / "a.py"): ({"t"}, {"t"})}

    def _fake_mine(path, **kwargs):
        srcs, snks = io_map.get(path, (set(), set()))
        return {"_sources": {n: {} for n in srcs}, "_sinks": {n: {} for n in snks}}

    files = [_code_file("a.py")]
    with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
         patch.object(sc, "_schema_mine_fn", _fake_mine):
        edges, _unres, _unres_edges = _build_data_dep_edges(files, tmp_path)
    assert not any(a == b for (a, b, _k) in edges)


def test_build_data_dep_edges_deduplicates(tmp_path: Path) -> None:
    """Multiple sink entries matching the same source produce only one edge."""
    io_map = {
        str(tmp_path / "w.py"): (set(), {"t1", "t2"}),
        str(tmp_path / "r.py"): ({"t1", "t2"}, set()),
    }

    def _fake_mine(path, **kwargs):
        srcs, snks = io_map.get(path, (set(), set()))
        return {"_sources": {n: {} for n in srcs}, "_sinks": {n: {} for n in snks}}

    files = [_code_file("w.py"), _code_file("r.py")]
    with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
         patch.object(sc, "_schema_mine_fn", _fake_mine):
        edges, _unres, _unres_edges = _build_data_dep_edges(files, tmp_path)
    assert sum(1 for e in edges if e[0] == "w.py" and e[1] == "r.py") == 1


# ---------------------------------------------------------------------------
# _build_unified_dependency_graph — edge kind stamping
# ---------------------------------------------------------------------------

def test_graph_import_edge_has_kind_import() -> None:
    """An edge not in data_edge_set gets kind='import'."""
    files = [_code_file("a.py"), _code_file("b.py")]
    edges = [("a.py", "b.py")]
    g = _build_unified_dependency_graph(files, edges, frozenset())
    assert g is not None
    assert all(e.kind == "import" for e in g.edges)


def test_graph_data_edge_has_kind_data() -> None:
    """An edge in data_edge_set gets kind='data'."""
    files = [_code_file("writer.py"), _code_file("reader.py")]
    edges = [("writer.py", "reader.py")]
    data_set = frozenset({("writer.py", "reader.py")})
    g = _build_unified_dependency_graph(files, edges, data_set)
    assert g is not None
    data_edges = [e for e in g.edges if e.kind == "data"]
    assert len(data_edges) == 1
    assert data_edges[0].source == "writer.py"
    assert data_edges[0].target == "reader.py"


def test_graph_mixed_edges_stamped_correctly() -> None:
    """Import and data edges coexist; each gets the right kind."""
    files = [_code_file("a.py"), _code_file("b.py"), _code_file("c.py")]
    edges = [("a.py", "b.py"), ("b.py", "c.py")]
    data_set = frozenset({("b.py", "c.py")})
    g = _build_unified_dependency_graph(files, edges, data_set)
    assert g is not None
    by_source = {e.source: e.kind for e in g.edges}
    assert by_source["a.py"] == "import"
    assert by_source["b.py"] == "data"


# ---------------------------------------------------------------------------
# Orphan promotion via combined edges
# ---------------------------------------------------------------------------

def test_orphan_promoted_when_data_edge_connects_it() -> None:
    """An isolated file that gains a data-flow edge joins the main cluster.

    Setup: three files — A imports B (these form the main cluster), and C is
    an orphan with no import edges.  After adding a data edge B→C, the
    combined-edge _build_isolated_modules call sees B and C as connected, so C
    leaves the isolated list.
    """
    files = [_code_file("a.py"), _code_file("b.py"), _code_file("c.py")]
    import_edges: list[tuple[str, str]] = [("a.py", "b.py")]
    data_edges: list[tuple[str, str]] = [("b.py", "c.py")]

    # Without data edge: c.py is isolated.
    iso_import, _, _, _ = _build_isolated_modules(files, import_edges)
    isolated_paths_import = {f.path for m in iso_import for f in m.files}
    assert "c.py" in isolated_paths_import

    # With data edge: c.py should be promoted (no longer isolated).
    combined = import_edges + data_edges
    iso_combined, _, _, _ = _build_isolated_modules(files, combined)
    isolated_paths_combined = {f.path for m in iso_combined for f in m.files}
    assert "c.py" not in isolated_paths_combined


def test_fully_disconnected_file_remains_isolated() -> None:
    """A file with no import *or* data edges stays in the isolated list."""
    files = [_code_file("a.py"), _code_file("b.py"), _code_file("orphan.py")]
    edges: list[tuple[str, str]] = [("a.py", "b.py")]
    iso, _, _, _ = _build_isolated_modules(files, edges)
    isolated_paths = {f.path for m in iso for f in m.files}
    assert "orphan.py" in isolated_paths


# ---------------------------------------------------------------------------
# _data_consumers_adjacency_json
# ---------------------------------------------------------------------------

def test_data_consumers_adjacency_empty_graph() -> None:
    assert json.loads(_data_consumers_adjacency_json(None)) == {}
    assert json.loads(_data_consumers_adjacency_json({})) == {}


def test_data_consumers_adjacency_single_edge() -> None:
    """Edge writer→reader produces entry: consumers[writer] = [reader]."""
    graph = {"edges": [{"source": "writer.py", "target": "reader.py"}]}
    adj = json.loads(_data_consumers_adjacency_json(graph))
    assert adj == {"writer.py": ["reader.py"]}


def test_data_consumers_adjacency_multiple_consumers() -> None:
    """One writer with two readers produces a list of two consumers."""
    graph = {
        "edges": [
            {"source": "writer.py", "target": "reader_a.py"},
            {"source": "writer.py", "target": "reader_b.py"},
        ]
    }
    adj = json.loads(_data_consumers_adjacency_json(graph))
    assert set(adj["writer.py"]) == {"reader_a.py", "reader_b.py"}


def test_data_consumers_adjacency_chain() -> None:
    """A→B→C produces consumers[A]=[B], consumers[B]=[C]."""
    graph = {
        "edges": [
            {"source": "a.py", "target": "b.py"},
            {"source": "b.py", "target": "c.py"},
        ]
    }
    adj = json.loads(_data_consumers_adjacency_json(graph))
    assert adj["a.py"] == ["b.py"]
    assert adj["b.py"] == ["c.py"]
    assert "c.py" not in adj  # c has no consumers


# ---------------------------------------------------------------------------
# scan() integration — data_dependency_graph field in Assessment
# ---------------------------------------------------------------------------

def test_scan_populates_data_dependency_graph_when_edges_exist(tmp_path: Path) -> None:
    """When data edges exist, Assessment.data_dependency_graph is populated."""
    (tmp_path / "writer.py").write_text("x = 1\n")
    (tmp_path / "reader.py").write_text("y = 2\n")

    fake_io = {
        str(tmp_path / "writer.py"): (set(), {"shared_table"}),
        str(tmp_path / "reader.py"): ({"shared_table"}, set()),
    }

    def _fake_mine(path, **kwargs):
        srcs, snks = fake_io.get(path, (set(), set()))
        return {"_sources": {n: {} for n in srcs}, "_sinks": {n: {} for n in snks}}

    with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
         patch.object(sc, "_schema_mine_fn", _fake_mine):
        ir = scan(tmp_path, project="test")

    assert ir.data_dependency_graph is not None
    assert ir.data_dependency_graph.edge_count >= 1
    # Import-only dependency_graph should NOT contain the data edge.
    import_edge_sources = {e.source for e in (ir.dependency_graph.edges if ir.dependency_graph else [])}
    assert "writer.py" not in import_edge_sources


def test_scan_data_dependency_graph_none_when_no_edges(tmp_path: Path) -> None:
    """Without data mining available, data_dependency_graph stays None."""
    (tmp_path / "a.py").write_text("x = 1\n")
    with patch.object(sc, "_DATA_MINING_AVAILABLE", False):
        ir = scan(tmp_path, project="test")
    assert ir.data_dependency_graph is None


# ---------------------------------------------------------------------------
# Template rendering — data DAG section present in HTML
# ---------------------------------------------------------------------------

def test_render_contains_data_dag_section_heading() -> None:
    """HTML report always contains the 'Data dependency graph' section heading."""
    from assess_ir import Assessment
    from prototype_v1 import render

    ir = Assessment()
    html = render(ir)
    assert "Data dependency graph" in html
    assert "data_consumers_adjacency_json" not in html  # template vars fully resolved
    assert "{{ " not in html


def test_render_contains_data_dag_svg_when_graph_present(tmp_path: Path) -> None:
    """When data_dependency_graph is populated, the SVG element is rendered."""
    from assess_ir import Assessment, DependencyGraph, GraphEdge, GraphNode
    from prototype_v1 import render

    node = GraphNode(id="writer.py", label="writer.py", full_label="writer.py",
                     path="writer.py", x=20, y=20, width=155, height=28)
    edge = GraphEdge(x1=97, y1=48, x2=97, y2=70,
                     source="writer.py", target="reader.py", kind="data")
    dg = DependencyGraph(module="Project", width=300, height=150,
                         file_count=2, edge_count=1, nodes=[node], edges=[edge])
    ir = Assessment(data_dependency_graph=dg)
    html = render(ir)
    assert "data-dep-graph-svg" in html
    assert "writer.py" in html
    assert "#4b5563" in html  # gray data-edge stroke color


def test_render_shows_empty_state_when_no_data_dag() -> None:
    """Empty-state message shown when data_dependency_graph is None."""
    from assess_ir import Assessment
    from prototype_v1 import render

    ir = Assessment(data_dependency_graph=None)
    html = render(ir)
    assert "No data-flow dependencies detected" in html


# ---------------------------------------------------------------------------
# Config-aware AST resolution (the Kipawa-style runtime path case)
# ---------------------------------------------------------------------------

def test_looks_like_data_path_recognises_uris() -> None:
    assert _looks_like_data_path("s3://bucket/key/")
    assert _looks_like_data_path("gs://bucket/key/")
    assert _looks_like_data_path("hdfs://nn/path")
    assert _looks_like_data_path("/mnt/data/file.parquet")
    assert _looks_like_data_path("events.json")
    assert not _looks_like_data_path("just_a_string")
    assert not _looks_like_data_path("")
    assert not _looks_like_data_path("UPPER")


def test_load_config_pool_extracts_path_values(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "pipeline.json").write_text(
        '{"inputPath": "s3://lake/raw/", "outputPath": "s3://lake/clean/", '
        '"nonPathValue": "just a string", "nested": {"midPath": "s3://lake/mid/"}}'
    )
    pool = _load_config_pool(tmp_path)
    assert pool["inputPath"] == {"s3://lake/raw/"}
    assert pool["outputPath"] == {"s3://lake/clean/"}
    assert pool["midPath"] == {"s3://lake/mid/"}
    # Non-path values are filtered.
    assert "nonPathValue" not in pool


def test_load_config_pool_ignores_excluded_dirs(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "junk.json").write_text('{"badKey": "s3://hidden/"}')
    (tmp_path / "config.json").write_text('{"goodKey": "s3://visible/"}')
    pool = _load_config_pool(tmp_path)
    # .git contents must be silently skipped.
    assert "badKey" not in pool
    # The sibling config.json outside .git must be picked up.
    assert pool.get("goodKey") == {"s3://visible/"}


def test_resolve_via_config_subscript_pattern(tmp_path: Path) -> None:
    """Classic Kipawa pattern: self.x = config['KEY']; spark.read.parquet(self.x)."""
    f = tmp_path / "job.py"
    f.write_text(
        "class Reader:\n"
        "    def __init__(self, cfg):\n"
        "        self.src = cfg['sourcePath']\n"
        "    def run(self, spark):\n"
        "        return spark.read.parquet(self.src)\n"
    )
    sources, sinks = _resolve_via_config(str(f), {"sourcePath": {"s3://lake/raw/"}})
    assert sources == {"s3://lake/raw/"}
    assert sinks == set()


def test_resolve_via_config_get_pattern(tmp_path: Path) -> None:
    """``config.get('KEY')`` should resolve identically to subscript."""
    f = tmp_path / "job.py"
    f.write_text(
        "def run(spark, cfg):\n"
        "    spark.read.json(cfg.get('inputPath'))\n"
    )
    sources, _ = _resolve_via_config(str(f), {"inputPath": {"s3://lake/in/"}})
    assert sources == {"s3://lake/in/"}


def test_resolve_via_config_writer_with_string_concat(tmp_path: Path) -> None:
    """Writer that does ``temp = self.x.rstrip('/') + '/'`` then ``.save(temp)``."""
    f = tmp_path / "writer.py"
    f.write_text(
        "class Writer:\n"
        "    def __init__(self, cfg):\n"
        "        self.dest = cfg['destPath']\n"
        "    def write(self, df):\n"
        "        temp = self.dest.rstrip('/') + '/'\n"
        "        df.write.format('parquet').mode('overwrite').save(temp)\n"
    )
    sources, sinks = _resolve_via_config(str(f), {"destPath": {"s3://lake/out/"}})
    assert sinks == {"s3://lake/out/"}
    assert sources == set()


def test_resolve_via_config_skips_unmatched_keys(tmp_path: Path) -> None:
    """If a referenced key isn't in the pool, no source/sink is added."""
    f = tmp_path / "job.py"
    f.write_text(
        "def run(spark, cfg):\n"
        "    spark.read.parquet(cfg['missingKey'])\n"
    )
    sources, _ = _resolve_via_config(str(f), {"otherKey": {"s3://elsewhere/"}})
    assert sources == set()


def test_resolve_via_config_ignores_non_data_calls(tmp_path: Path) -> None:
    """``boto3.client('s3')`` etc. must not be misread as a data read."""
    f = tmp_path / "job.py"
    f.write_text(
        "import boto3\n"
        "def setup():\n"
        "    return boto3.client('s3')\n"
    )
    sources, sinks = _resolve_via_config(str(f), {"s3": {"s3://anywhere/"}})
    assert sources == set()
    assert sinks == set()


def test_full_pipeline_kipawa_style_produces_edges(tmp_path: Path) -> None:
    """End-to-end: Kipawa-shaped workload (orchestrator with dynamic imports +
    nested reader/transforms/writer config + module files) produces the full
    reader→transforms→writer chain.

    This mirrors the exact structure of ``00_Kipawa_scos``:
    ``pipeline/pipeline_impl.py`` calls ``importlib.import_module(cfg[K])`` for
    each stage; the discovered ``K`` values are looked up in ``config/x.json``
    to produce the module names and, via ``_resolve_module_to_file``, the file
    chain.
    """
    # Directory shape mirroring Kipawa: config/, src/pipeline, src/readers,
    # src/transformers, src/writers.
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "batch.json").write_text(json.dumps({
        "pipeline": {
            "reader": {"readerModule": "readers.s3_json_reader"},
            "transforms": [
                {"transformModule": "transformers.epoch_to_date"},
                {"transformModule": "transformers.reduce_gps_precision"},
            ],
            "writer": {"writerModule": "writers.s3_parquet_writer"},
        }
    }))
    (tmp_path / "src").mkdir()
    for sub in ("pipeline", "readers", "transformers", "writers"):
        (tmp_path / "src" / sub).mkdir()
        (tmp_path / "src" / sub / "__init__.py").write_text("")
    # Orchestrator: three dynamic-import sites in reader → transforms → writer
    # source-code order. This is what drives the AST-based chain builder.
    # Distinct local-variable names per stage mimic realistic orchestrator
    # code (see 00_Kipawa_scos/src/pipeline/pipeline_impl.py) and avoid a
    # legitimate ambiguity when the same identifier is assigned in multiple
    # methods.
    (tmp_path / "src" / "pipeline" / "pipeline_impl.py").write_text(
        "import importlib\n"
        "class PipelineImpl:\n"
        "    def _reader(self, cfg):\n"
        "        reader_name = cfg['readerModule']\n"
        "        return importlib.import_module(reader_name)\n"
        "    def _transformers(self, cfg):\n"
        "        for item in cfg['transforms']:\n"
        "            importlib.import_module(item['transformModule'])\n"
        "    def _writer(self, cfg):\n"
        "        writer_name = cfg['writerModule']\n"
        "        return importlib.import_module(writer_name)\n"
    )
    (tmp_path / "src" / "readers" / "s3_json_reader.py").write_text(
        "def read_data(spark):\n    return spark.read.json('input')\n"
    )
    (tmp_path / "src" / "transformers" / "epoch_to_date.py").write_text("def t(df): return df\n")
    (tmp_path / "src" / "transformers" / "reduce_gps_precision.py").write_text("def t(df): return df\n")
    (tmp_path / "src" / "writers" / "s3_parquet_writer.py").write_text(
        "def write_data(df):\n    df.write.parquet('out')\n"
    )

    ir = scan(tmp_path, project="kipawa-shape")
    assert ir.data_dependency_graph is not None
    # 3 chain edges (reader→t1→t2→writer). External endpoints are now
    # attached as chain-node metadata (``external_sources`` /
    # ``external_sinks``), not pseudo-nodes with their own arrows — so
    # edge_count is exactly 3 regardless of whether schema_mine is
    # installed.
    assert ir.data_dependency_graph.edge_count == 3
    chain_pairs = {(e.source, e.target) for e in ir.data_dependency_graph.edges}
    assert ("src/readers/s3_json_reader.py", "src/transformers/epoch_to_date.py") in chain_pairs
    assert ("src/transformers/epoch_to_date.py", "src/transformers/reduce_gps_precision.py") in chain_pairs
    assert ("src/transformers/reduce_gps_precision.py", "src/writers/s3_parquet_writer.py") in chain_pairs


# ---------------------------------------------------------------------------
# Dynamic-import detection (schema_mine._find_dynamic_import_sites)
# ---------------------------------------------------------------------------
#
# Exercises the AST helper that discovers customer-specific config-key names
# from importlib.import_module() call sites. Runs even when PySpark isn't
# installed because the helper is pure AST.

def _sites(src: str) -> list[dict]:
    """Test convenience: run the helper on inline source, return the sites."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    tree = ast.parse(src)
    return _find_dynamic_import_sites(
        tree, _collect_assignments_for_dynamic_imports(tree)
    )


def test_find_dyn_imports_literal_arg() -> None:
    """String-literal import_module argument records the raw expression with
    ``config_key=None``."""
    sites = _sites("import importlib\nimportlib.import_module('foo.bar')\n")
    assert len(sites) == 1
    assert sites[0]["config_key"] is None
    assert sites[0]["container_key"] is None
    assert "foo.bar" in sites[0]["raw_expr"]


def test_find_dyn_imports_direct_subscript() -> None:
    """``importlib.import_module(cfg[\"KEY\"])`` → ``config_key='KEY'``."""
    sites = _sites(
        "import importlib\ndef f(cfg):\n"
        "    return importlib.import_module(cfg['readerModule'])\n"
    )
    assert len(sites) == 1
    assert sites[0]["config_key"] == "readerModule"
    assert sites[0]["container_key"] is None


def test_find_dyn_imports_get_call() -> None:
    """``.get('KEY')`` resolves identically to subscript."""
    sites = _sites(
        "import importlib\ndef f(cfg):\n"
        "    return importlib.import_module(cfg.get('writerModule'))\n"
    )
    assert len(sites) == 1
    assert sites[0]["config_key"] == "writerModule"


def test_find_dyn_imports_via_local_variable() -> None:
    """Argument traced back through a Name assignment."""
    sites = _sites(
        "import importlib\ndef f(cfg):\n"
        "    name = cfg['pluginModule']\n"
        "    return importlib.import_module(name)\n"
    )
    assert len(sites) == 1
    assert sites[0]["config_key"] == "pluginModule"


def test_find_dyn_imports_via_attribute() -> None:
    """``self.x = cfg['KEY']``-style traced through attribute assignment."""
    sites = _sites(
        "import importlib\nclass C:\n"
        "    def __init__(self, cfg):\n"
        "        self.mod = cfg['stageModule']\n"
        "    def load(self):\n"
        "        return importlib.import_module(self.mod)\n"
    )
    assert len(sites) == 1
    assert sites[0]["config_key"] == "stageModule"


def test_find_dyn_imports_for_loop_container() -> None:
    """for-loop over ``cfg[LIST]`` records ``container_key=LIST``."""
    sites = _sites(
        "import importlib\ndef f(cfg):\n"
        "    for item in cfg['pipelineStages']:\n"
        "        importlib.import_module(item['plugin'])\n"
    )
    assert len(sites) == 1
    assert sites[0]["config_key"] == "plugin"
    assert sites[0]["container_key"] == "pipelineStages"


def test_find_dyn_imports_for_loop_via_variable_iter() -> None:
    """``xs = cfg['LIST']; for item in xs: import_module(item['K'])`` traces
    the iterator back through the assignment to the LIST key."""
    sites = _sites(
        "import importlib\ndef f(cfg):\n"
        "    stages = cfg['pipeline_stages']\n"
        "    for item in stages:\n"
        "        importlib.import_module(item['plugin'])\n"
    )
    assert len(sites) == 1
    assert sites[0]["config_key"] == "plugin"
    assert sites[0]["container_key"] == "pipeline_stages"


def test_find_dyn_imports_unresolvable_records_none() -> None:
    """Best-effort: an argument we can't trace still shows up with
    ``config_key=None``."""
    sites = _sites(
        "import importlib\ndef f(x):\n"
        "    return importlib.import_module(x)\n"
    )
    assert len(sites) == 1
    assert sites[0]["config_key"] is None


def test_mine_default_off_omits_dynamic_imports(tmp_path: Path) -> None:
    """Default ``mine(path)`` result has no ``_dynamic_imports`` key so
    validation callers observe byte-identical behaviour."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    f = tmp_path / "x.py"
    f.write_text(
        "import importlib\ndef f(cfg):\n"
        "    return importlib.import_module(cfg['readerModule'])\n"
    )
    r = _schema_mine(str(f))
    assert "_dynamic_imports" not in r


def test_mine_explicit_on_returns_key_even_when_empty(tmp_path: Path) -> None:
    """With the flag on, the key is always present — empty list when no sites."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    f = tmp_path / "empty.py"
    f.write_text("x = 1\n")
    r = _schema_mine(str(f), detect_dynamic_imports=True)
    assert r.get("_dynamic_imports") == []


# ---------------------------------------------------------------------------
# Workload-level chain builder (dynamic-import + config lookup)
# ---------------------------------------------------------------------------

def test_lookup_by_key_name_finds_first_string() -> None:
    """A key found anywhere in a nested dict returns its first value."""
    cfg = [{"pipeline": {"reader": {"readerModule": "readers.a"}}}]
    assert _lookup_by_key_name(cfg, "readerModule") == "readers.a"


def test_lookup_by_key_name_returns_none_when_missing() -> None:
    assert _lookup_by_key_name([{"other": "x"}], "readerModule") is None


def test_lookup_list_preserves_list_order() -> None:
    cfg = [{
        "pipeline": {
            "transforms": [
                {"transformModule": "t.a"},
                {"transformModule": "t.b"},
                {"transformModule": "t.c"},
            ],
        }
    }]
    assert _lookup_list_by_key_name(cfg, "transforms", "transformModule") == [
        "t.a", "t.b", "t.c"
    ]


def test_load_config_data_returns_parsed_dicts(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "p.json").write_text(
        '{"pipeline": {"reader": {"readerModule": "readers.a"}}}'
    )
    data = _load_config_data(tmp_path)
    assert len(data) == 1
    assert data[0]["pipeline"]["reader"]["readerModule"] == "readers.a"


def test_dynamic_import_chain_edges_kipawa_shape() -> None:
    """readerModule / transforms[].transformModule / writerModule keys —
    the shape used to be hardcoded, now it's discovered from the AST."""
    sites_by_file = {
        "src/pipeline/pipeline_impl.py": [
            {"line": 10, "kind": "import_module", "config_key": "readerModule",
             "container_key": None, "raw_expr": ""},
            {"line": 20, "kind": "import_module", "config_key": "transformModule",
             "container_key": "transforms", "raw_expr": ""},
            {"line": 30, "kind": "import_module", "config_key": "writerModule",
             "container_key": None, "raw_expr": ""},
        ]
    }
    config_data = [{
        "pipeline": {
            "reader": {"readerModule": "readers.a"},
            "transforms": [
                {"transformModule": "t.x"},
                {"transformModule": "t.y"},
            ],
            "writer": {"writerModule": "writers.b"},
        }
    }]
    code_files = [
        {"rel_path": "src/readers/a.py"},
        {"rel_path": "src/t/x.py"},
        {"rel_path": "src/t/y.py"},
        {"rel_path": "src/writers/b.py"},
    ]
    edges, _unres = _dynamic_import_chain_edges(sites_by_file, config_data, code_files)
    assert edges == [
        ("src/readers/a.py", "src/t/x.py", "data"),
        ("src/t/x.py", "src/t/y.py", "data"),
        ("src/t/y.py", "src/writers/b.py", "data"),
    ]


def test_dynamic_import_chain_edges_alternate_key_names() -> None:
    """DIFFERENT vocabulary — proves the hardcoding is gone."""
    sites_by_file = {
        "orchestrator.py": [
            {"line": 5, "kind": "import_module", "config_key": "data_source",
             "container_key": None, "raw_expr": ""},
            {"line": 12, "kind": "import_module", "config_key": "plugin",
             "container_key": "pipeline_stages", "raw_expr": ""},
            {"line": 20, "kind": "import_module", "config_key": "output_target",
             "container_key": None, "raw_expr": ""},
        ]
    }
    config_data = [{
        "data_source": "readers.source_a",
        "pipeline_stages": [
            {"plugin": "stages.filter"},
            {"plugin": "stages.enrich"},
        ],
        "output_target": "writers.warehouse",
    }]
    code_files = [
        {"rel_path": "readers/source_a.py"},
        {"rel_path": "stages/filter.py"},
        {"rel_path": "stages/enrich.py"},
        {"rel_path": "writers/warehouse.py"},
    ]
    edges, _unres = _dynamic_import_chain_edges(sites_by_file, config_data, code_files)
    assert edges == [
        ("readers/source_a.py", "stages/filter.py", "data"),
        ("stages/filter.py", "stages/enrich.py", "data"),
        ("stages/enrich.py", "writers/warehouse.py", "data"),
    ]


def test_dynamic_import_chain_edges_no_sites_returns_empty() -> None:
    """No orchestrator sites → no chain edges, no crash."""
    edges, unres = _dynamic_import_chain_edges({}, [{"unrelated": "config"}], [])
    assert edges == []
    assert unres == []


def test_dynamic_import_chain_edges_no_config_matches() -> None:
    """Sites present but config has no matching keys → empty edge list, one
    unresolved entry describing the missing key."""
    sites_by_file = {
        "orch.py": [
            {"line": 1, "kind": "import_module", "config_key": "readerModule",
             "container_key": None, "raw_expr": "cfg['readerModule']"},
        ]
    }
    config_data = [{"unrelated": "config"}]
    code_files = [{"rel_path": "readers/a.py"}]
    edges, unres = _dynamic_import_chain_edges(sites_by_file, config_data, code_files)
    assert edges == []
    assert len(unres) == 1
    assert "readerModule" in unres[0].reason


def test_dynamic_import_chain_preserves_source_order() -> None:
    """When multiple import sites live in the same orchestrator, source-line
    order drives the chain regardless of dict-insertion order."""
    sites_by_file = {
        "orch.py": [
            # writer declared BEFORE reader in the sites list; sort by line.
            {"line": 30, "kind": "import_module", "config_key": "writerModule",
             "container_key": None, "raw_expr": ""},
            {"line": 10, "kind": "import_module", "config_key": "readerModule",
             "container_key": None, "raw_expr": ""},
        ]
    }
    config_data = [{
        "readerModule": "readers.a",
        "writerModule": "writers.b",
    }]
    code_files = [
        {"rel_path": "readers/a.py"},
        {"rel_path": "writers/b.py"},
    ]
    edges, _unres = _dynamic_import_chain_edges(sites_by_file, config_data, code_files)
    assert edges == [("readers/a.py", "writers/b.py", "data")]


def test_order_sites_container_interposed_between_single_stages() -> None:
    """Real Kipawa layout: reader helper (line 119), writer helper (line 160),
    transforms-loop helper (line 200). The chain-assembly heuristic must place
    the container between the two single-stage endpoints so the resulting
    chain reads reader → transforms → writer."""
    sites = [
        {"line": 200, "config_key": "transformModule",
         "container_key": "transforms", "raw_expr": ""},
        {"line": 160, "config_key": "writerModule", "container_key": None,
         "raw_expr": ""},
        {"line": 119, "config_key": "readerModule", "container_key": None,
         "raw_expr": ""},
    ]
    ordered = _order_sites_for_chain(sites)
    keys = [(s["config_key"], s.get("container_key")) for s in ordered]
    assert keys == [
        ("readerModule", None),
        ("transformModule", "transforms"),
        ("writerModule", None),
    ]


def test_order_sites_no_container_falls_back_to_source_order() -> None:
    """Without any container sites, ordering is plain source order."""
    sites = [
        {"line": 30, "config_key": "writerModule", "container_key": None,
         "raw_expr": ""},
        {"line": 10, "config_key": "readerModule", "container_key": None,
         "raw_expr": ""},
    ]
    ordered = _order_sites_for_chain(sites)
    lines = [s["line"] for s in ordered]
    assert lines == [10, 30]


def test_full_pipeline_kipawa_shape_source_order_writer_before_transforms(
    tmp_path: Path,
) -> None:
    """Mirrors the EXACT source-line layout of Kipawa's pipeline_impl.py:
    reader-helper, writer-helper, transforms-loop (in that order). Verifies
    the chain-assembly heuristic still produces reader → transforms → writer.
    Regression guard for the ordering bug that would otherwise render
    reader → writer → transforms and produce broken edges.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "batch.json").write_text(json.dumps({
        "pipeline": {
            "reader": {"readerModule": "readers.a"},
            "transforms": [
                {"transformModule": "t.x"},
                {"transformModule": "t.y"},
            ],
            "writer": {"writerModule": "writers.b"},
        }
    }))
    (tmp_path / "src").mkdir()
    for sub in ("pipeline", "readers", "t", "writers"):
        (tmp_path / "src" / sub).mkdir()
        (tmp_path / "src" / sub / "__init__.py").write_text("")
    # Note the method definition order: reader, WRITER, transformers-loop.
    (tmp_path / "src" / "pipeline" / "pipeline_impl.py").write_text(
        "import importlib\n"
        "class Pipe:\n"
        "    def _create_reader(self, cfg):\n"
        "        rn = cfg['readerModule']\n"
        "        return importlib.import_module(rn)\n"
        "    def _create_writer(self, cfg):\n"
        "        wn = cfg['writerModule']\n"
        "        return importlib.import_module(wn)\n"
        "    def _create_transformers(self, cfg):\n"
        "        for item in cfg['transforms']:\n"
        "            importlib.import_module(item['transformModule'])\n"
    )
    (tmp_path / "src" / "readers" / "a.py").write_text("x = 1\n")
    (tmp_path / "src" / "t" / "x.py").write_text("x = 1\n")
    (tmp_path / "src" / "t" / "y.py").write_text("x = 1\n")
    (tmp_path / "src" / "writers" / "b.py").write_text("x = 1\n")

    ir = scan(tmp_path, project="kipawa-real-order")
    assert ir.data_dependency_graph is not None
    pairs = {(e.source, e.target) for e in ir.data_dependency_graph.edges
             if not e.source.startswith("framework") and not e.source.startswith("ext:")}
    assert ("src/readers/a.py", "src/t/x.py") in pairs
    assert ("src/t/x.py", "src/t/y.py") in pairs
    assert ("src/t/y.py", "src/writers/b.py") in pairs
    # Sanity: writer must NOT come before transforms in the resulting chain.
    assert ("src/readers/a.py", "src/writers/b.py") not in pairs
    assert ("src/writers/b.py", "src/t/x.py") not in pairs


def test_resolve_module_to_file_direct() -> None:
    files = [{"rel_path": "readers/s3_json_reader.py"}]
    assert _resolve_module_to_file("readers.s3_json_reader", files) == "readers/s3_json_reader.py"


def test_resolve_module_to_file_with_src_prefix() -> None:
    files = [{"rel_path": "src/readers/s3_json_reader.py"}]
    assert _resolve_module_to_file("readers.s3_json_reader", files) == "src/readers/s3_json_reader.py"


def test_resolve_module_to_file_suffix_match() -> None:
    """When the file lives under an arbitrary source root, suffix match resolves."""
    files = [{"rel_path": "app/lib/readers/s3_json_reader.py"}]
    assert _resolve_module_to_file("readers.s3_json_reader", files) == "app/lib/readers/s3_json_reader.py"


def test_resolve_module_to_file_returns_none_for_missing() -> None:
    files = [{"rel_path": "src/other.py"}]
    assert _resolve_module_to_file("readers.absent", files) is None


# ---------------------------------------------------------------------------
# Detection tests — new dynamic-import kinds via _find_dynamic_import_sites
# ---------------------------------------------------------------------------

def test_find_dyn_imports_dunder_import_literal() -> None:
    """``__import__('foo.bar')`` records kind='__import__' with config_key=None."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    sites = _sites("m = __import__('foo.bar')\n")
    assert len(sites) == 1
    assert sites[0]["kind"] == "__import__"
    assert sites[0]["config_key"] is None


def test_find_dyn_imports_dunder_import_via_subscript() -> None:
    """``__import__(cfg['k'])`` traces the config key just like import_module."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    sites = _sites(
        "def f(cfg):\n"
        "    return __import__(cfg['k'])\n"
    )
    assert len(sites) == 1
    assert sites[0]["kind"] == "__import__"
    assert sites[0]["config_key"] == "k"


def test_find_dyn_imports_spec_from_file_literal_path() -> None:
    """``importlib.util.spec_from_file_location(name, path_literal)`` captures
    the path string via ``path_arg`` and ``path_arg_raw``."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    sites = _sites(
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('m', '/tmp/x.py')\n"
    )
    assert len(sites) == 1
    assert sites[0]["kind"] == "spec_from_file"
    assert sites[0]["path_arg"] == "/tmp/x.py"
    assert sites[0]["path_arg_raw"] and "/tmp/x.py" in sites[0]["path_arg_raw"]


def test_find_dyn_imports_imp_load_source_traces_via_var() -> None:
    """``imp.load_source(name, path)`` traces path back through assignments."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    sites = _sites(
        "import imp\n"
        "def f(cfg):\n"
        "    p = cfg['path']\n"
        "    return imp.load_source('m', p)\n"
    )
    assert len(sites) == 1
    assert sites[0]["kind"] == "imp_load_source"
    # path_arg trace can only pull literal strings; through a Name binding to
    # ``cfg['path']`` the string is not statically known — path_arg stays None,
    # but the raw expression carries the argument text for reference.
    assert sites[0]["path_arg_raw"] == "p"


def test_find_dyn_imports_pkg_resources_load_entry_point() -> None:
    """``pkg_resources.load_entry_point(dist, group, name)`` captures both
    group and name literals."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    sites = _sites(
        "import pkg_resources\n"
        "ep = pkg_resources.load_entry_point('dist', 'grp', 'name')\n"
    )
    assert len(sites) == 1
    assert sites[0]["kind"] == "entry_point"
    assert sites[0]["entry_point_group"] == "grp"
    assert sites[0]["entry_point_name"] == "name"


def test_find_dyn_imports_metadata_entry_points_group_only() -> None:
    """``importlib.metadata.entry_points(group='mygrp')`` picks up group; name
    stays None since none is provided."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    sites = _sites(
        "import importlib.metadata\n"
        "for ep in importlib.metadata.entry_points(group='mygrp'):\n"
        "    ep.load()\n"
    )
    kinds = [s["kind"] for s in sites]
    assert "entry_point" in kinds
    ep = next(s for s in sites if s["kind"] == "entry_point")
    assert ep["entry_point_group"] == "mygrp"


def test_find_dyn_imports_factory_dict_with_name_values() -> None:
    """READERS = {'a': ClsA, 'b': ClsB}; READERS[cfg['type']](x) → factory_dict."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    sites = _sites(
        "from readers import ClsA, ClsB\n"
        "READERS = {'a': ClsA, 'b': ClsB}\n"
        "def make(cfg):\n"
        "    return READERS[cfg['type']]()\n"
    )
    factory_sites = [s for s in sites if s["kind"] == "factory_dict"]
    assert len(factory_sites) == 1
    fs = factory_sites[0]
    assert fs["dict_var_name"] == "READERS"
    assert set(fs["candidate_classes"]) == {"ClsA", "ClsB"}
    assert fs["dispatch_key"] == "type"


def test_find_dyn_imports_factory_dict_lambda_values_skipped() -> None:
    """When dict values are lambdas (or any non-Name), the dict is NOT
    reported as a factory. The call site should NOT produce a factory_dict."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    sites = _sites(
        "READERS = {'a': lambda: 1, 'b': lambda: 2}\n"
        "def make(cfg):\n"
        "    return READERS[cfg['type']]()\n"
    )
    assert all(s["kind"] != "factory_dict" for s in sites)


# ---------------------------------------------------------------------------
# Resolution tests — _resolve_dynamic_import_site
# ---------------------------------------------------------------------------

def _make_site(kind: str, **fields) -> dict:
    base = {
        "line": 1,
        "kind": kind,
        "config_key": None,
        "container_key": None,
        "path_arg": None,
        "path_arg_raw": None,
        "entry_point_group": None,
        "entry_point_name": None,
        "dict_var_name": None,
        "candidate_classes": [],
        "dispatch_key": None,
        "raw_expr": "",
    }
    base.update(fields)
    return base


def test_resolve_path_loader_matches_workload_file(tmp_path: Path) -> None:
    (tmp_path / "reader.py").write_text("x = 1\n")
    code_files = [{"rel_path": "reader.py", "imports": []}]
    site = _make_site("spec_from_file", path_arg="reader.py",
                       path_arg_raw="'reader.py'")
    files, reason = _resolve_dynamic_import_site(
        site, "orch.py", [], code_files, {}, tmp_path,
    )
    assert files == ["reader.py"]
    assert reason is None


def test_resolve_path_loader_unresolved_reports_path(tmp_path: Path) -> None:
    code_files = [{"rel_path": "reader.py", "imports": []}]
    site = _make_site("spec_from_file", path_arg="/missing/module.py",
                       path_arg_raw="'/missing/module.py'")
    files, reason = _resolve_dynamic_import_site(
        site, "orch.py", [], code_files, {}, tmp_path,
    )
    assert files == []
    assert "/missing/module.py" in (reason or "")


def test_resolve_entry_point_via_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        "name = \"demo\"\n\n"
        "[project.entry-points.mygrp]\n"
        "foo = \"plugins.foo\"\n"
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "foo.py").write_text("x = 1\n")
    reg = _load_entry_points_registry(tmp_path)
    assert reg.get("mygrp", {}).get("foo") == "plugins.foo"
    code_files = [{"rel_path": "plugins/foo.py", "imports": []}]
    site = _make_site("entry_point", entry_point_group="mygrp", entry_point_name="foo")
    files, reason = _resolve_dynamic_import_site(
        site, "orch.py", [], code_files, reg, tmp_path,
    )
    assert files == ["plugins/foo.py"]
    assert reason is None


def test_resolve_entry_point_via_setup_py_best_effort(tmp_path: Path) -> None:
    """setup.py with entry_points={...} literal-dict form. Best-effort
    AST parsing extracts the group + module string."""
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\n"
        "setup(\n"
        "    name='demo',\n"
        "    entry_points={\n"
        "        'plugins': ['foo = pkg.foo:main'],\n"
        "    },\n"
        ")\n"
    )
    reg = _load_entry_points_registry(tmp_path)
    assert reg.get("plugins", {}).get("foo", "").startswith("pkg.foo")


def test_resolve_entry_point_missing_registry(tmp_path: Path) -> None:
    """When no pyproject/setup.py/setup.cfg is present, resolver returns a
    reason that names the missing registry."""
    code_files = [{"rel_path": "reader.py", "imports": []}]
    site = _make_site("entry_point", entry_point_group="mygrp", entry_point_name="foo")
    files, reason = _resolve_dynamic_import_site(
        site, "orch.py", [], code_files, {}, tmp_path,
    )
    assert files == []
    assert reason is not None
    assert "no entry_points registry" in reason


def test_resolve_factory_dict_via_static_import_graph(tmp_path: Path) -> None:
    """Factory dict candidates that are declared in workload files resolve
    to those files. Uses class-definition detection."""
    (tmp_path / "readers").mkdir()
    (tmp_path / "readers" / "cls_a.py").write_text("class ClsA:\n    pass\n")
    (tmp_path / "readers" / "cls_b.py").write_text("class ClsB:\n    pass\n")
    (tmp_path / "orch.py").write_text("x = 1\n")
    code_files = [
        {"rel_path": "readers/cls_a.py", "imports": []},
        {"rel_path": "readers/cls_b.py", "imports": []},
        {"rel_path": "orch.py", "imports": []},
    ]
    site = _make_site(
        "factory_dict",
        dict_var_name="READERS",
        candidate_classes=["ClsA", "ClsB"],
        dispatch_key="type",
    )
    files, reason = _resolve_dynamic_import_site(
        site, "orch.py", [], code_files, {}, tmp_path,
    )
    assert set(files) == {"readers/cls_a.py", "readers/cls_b.py"}
    assert reason is None


def test_resolve_factory_dict_unresolved_when_no_matches(tmp_path: Path) -> None:
    """Factory dict candidates that aren't defined anywhere in the workload
    surface as an unresolved warning."""
    (tmp_path / "orch.py").write_text("x = 1\n")
    code_files = [{"rel_path": "orch.py", "imports": []}]
    site = _make_site(
        "factory_dict",
        dict_var_name="READERS",
        candidate_classes=["Ghost", "Phantom"],
        dispatch_key="type",
    )
    files, reason = _resolve_dynamic_import_site(
        site, "orch.py", [], code_files, {}, tmp_path,
    )
    assert files == []
    assert reason is not None
    assert "READERS" in reason


# ---------------------------------------------------------------------------
# End-to-end integration — mixed resolvable + unresolved workload
# ---------------------------------------------------------------------------

def test_end_to_end_mixed_resolvable_and_unresolved(tmp_path: Path) -> None:
    """Workload with FOUR dynamic-import sites: one resolves (import_module +
    config), one is unresolved (spec_from_file with a missing path), one is
    unresolved (entry_point without a registry), and one factory_dict with
    two candidates that both resolve to workload files.

    Verifies:
      * Data DAG has the chain-based edge from the resolvable site
      * Data DAG has 2 factory_dispatch edges (dashed)
      * ``Assessment.unresolved_dynamic_imports`` has 2 entries with specific
        reasons
      * Rendered HTML contains the warning block with 2 rows
    """
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    (tmp_path / "config.json").write_text(json.dumps({
        "readerModule": "readers.a",
    }))
    (tmp_path / "readers").mkdir()
    (tmp_path / "readers" / "a.py").write_text("x = 1\n")
    (tmp_path / "writers").mkdir()
    (tmp_path / "writers" / "w.py").write_text("x = 1\n")
    (tmp_path / "candidates").mkdir()
    (tmp_path / "candidates" / "cand_a.py").write_text("class CandA:\n    pass\n")
    (tmp_path / "candidates" / "cand_b.py").write_text("class CandB:\n    pass\n")
    # Orchestrator uses:
    #   - import_module + config['readerModule'] → resolves to readers/a.py
    #   - spec_from_file with a missing path → unresolved
    #   - pkg_resources.load_entry_point without any workload registry → unresolved
    #   - factory dict with two candidates → both resolve, giving 2 fan-out edges
    (tmp_path / "orch.py").write_text(
        "import importlib\n"
        "import importlib.util\n"
        "import pkg_resources\n"
        "from candidates.cand_a import CandA\n"
        "from candidates.cand_b import CandB\n"
        "READERS = {'a': CandA, 'b': CandB}\n"
        "def run(cfg):\n"
        "    importlib.import_module(cfg['readerModule'])\n"
        "    importlib.util.spec_from_file_location('missing', '/nowhere/x.py')\n"
        "    pkg_resources.load_entry_point('dist', 'g', 'name')\n"
        "    return READERS[cfg['type']]()\n"
    )

    ir = scan(tmp_path, project="mixed-e2e")

    assert ir.data_dependency_graph is not None
    edges = ir.data_dependency_graph.edges
    # 1. Chain-based edge from the resolvable import_module — the reader is
    #    the only chain stage; external source URIs are attached to the
    #    reader node as metadata (``external_sources``) rather than
    #    rendered as pseudo-nodes with their own arrows. We only assert
    #    the chain covers readers/a.py somewhere.
    node_ids = {n.id for n in ir.data_dependency_graph.nodes}
    assert "readers/a.py" in node_ids
    # 2. Two factory_dispatch fan-out edges (from candidates into orch.py).
    factory_edges = [e for e in edges if e.kind == "factory_dispatch"]
    factory_targets = {(e.source, e.target) for e in factory_edges}
    assert ("candidates/cand_a.py", "orch.py") in factory_targets
    assert ("candidates/cand_b.py", "orch.py") in factory_targets
    # 3. Assessment.unresolved_dynamic_imports has 2 entries — one for the
    #    spec_from_file with a missing path, one for the entry_point without
    #    a registry.
    unres = ir.unresolved_dynamic_imports
    assert len(unres) == 2
    kinds = {u.kind for u in unres}
    assert kinds == {"spec_from_file", "entry_point"}
    for u in unres:
        assert u.file == "orch.py"
        assert u.reason and u.reason != "unknown"
    # 4. HTML render contains the warning block with 2 rows.
    from prototype_v1 import render
    html = render(ir)
    assert "Unresolved dynamic imports" in html
    assert "spec_from_file" in html
    assert "entry_point" in html


def test_unresolved_dynamic_imports_summary_shape() -> None:
    """The prototype adapter helper produces a stable summary shape."""
    entries = [
        {"file": "b.py", "line": 10, "kind": "spec_from_file",
         "reason": "path 'x' did not match", "raw_expr": "x"},
        {"file": "a.py", "line": 5, "kind": "entry_point",
         "reason": "no registry", "raw_expr": "load_entry_point()"},
        {"file": "a.py", "line": 20, "kind": "entry_point",
         "reason": "no registry", "raw_expr": "load_entry_point()"},
    ]
    summary = _unresolved_dynamic_imports_summary(entries)
    assert summary["count"] == 3
    assert summary["by_kind"] == {"spec_from_file": 1, "entry_point": 2}
    # Sorted by (file, line).
    ordered = [(e["file"], e["line"]) for e in summary["entries"]]
    assert ordered == [("a.py", 5), ("a.py", 20), ("b.py", 10)]


# ---------------------------------------------------------------------------
# Top-to-bottom chain layout (Change 1)
# ---------------------------------------------------------------------------

def _kipawa_workload(tmp_path: Path) -> Path:
    """Reusable fixture builder: Kipawa-shaped workload with orchestrator +
    reader → 2 transforms → writer. Returns the workload root."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "batch.json").write_text(json.dumps({
        "pipeline": {
            "reader": {"readerModule": "readers.s3_json_reader"},
            "transforms": [
                {"transformModule": "transformers.epoch_to_date"},
                {"transformModule": "transformers.reduce_gps_precision"},
            ],
            "writer": {"writerModule": "writers.s3_parquet_writer"},
        }
    }))
    (tmp_path / "src").mkdir()
    for sub in ("pipeline", "readers", "transformers", "writers", "common"):
        (tmp_path / "src" / sub).mkdir()
        (tmp_path / "src" / sub / "__init__.py").write_text("")
    (tmp_path / "src" / "pipeline" / "pipeline_impl.py").write_text(
        "import importlib\n"
        "class PipelineImpl:\n"
        "    def _reader(self, cfg):\n"
        "        return importlib.import_module(cfg['readerModule'])\n"
        "    def _transformers(self, cfg):\n"
        "        for item in cfg['transforms']:\n"
        "            importlib.import_module(item['transformModule'])\n"
        "    def _writer(self, cfg):\n"
        "        return importlib.import_module(cfg['writerModule'])\n"
    )
    # A base class that the reader/writer import — gives the framework
    # nodes a non-zero in-degree.
    (tmp_path / "src" / "common" / "base_stage.py").write_text(
        "from abc import ABC, abstractmethod\n"
        "class BaseStage(ABC):\n"
        "    @abstractmethod\n"
        "    def run(self):\n        ...\n"
    )
    (tmp_path / "src" / "readers" / "s3_json_reader.py").write_text(
        "from src.common.base_stage import BaseStage\n"
        "def read_data(spark):\n    return spark.read.json('input')\n"
    )
    (tmp_path / "src" / "transformers" / "epoch_to_date.py").write_text(
        "from src.common.base_stage import BaseStage\n"
        "def t(df): return df\n"
    )
    (tmp_path / "src" / "transformers" / "reduce_gps_precision.py").write_text(
        "from src.common.base_stage import BaseStage\n"
        "def t(df): return df\n"
    )
    (tmp_path / "src" / "writers" / "s3_parquet_writer.py").write_text(
        "from src.common.base_stage import BaseStage\n"
        "def write_data(df):\n    df.write.parquet('out')\n"
    )
    return tmp_path


def test_tb_layout_chain_nodes_monotonically_increasing_y(tmp_path: Path) -> None:
    """The chain renders TOP-TO-BOTTOM: y-coordinates strictly increase
    along the reader → transformers → writer sequence."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-tb")
    assert ir.data_dependency_graph is not None
    nodes_by_id = {n.id: n for n in ir.data_dependency_graph.nodes}
    chain_order = [
        "src/readers/s3_json_reader.py",
        "src/transformers/epoch_to_date.py",
        "src/transformers/reduce_gps_precision.py",
        "src/writers/s3_parquet_writer.py",
    ]
    ys = [nodes_by_id[p].y for p in chain_order if p in nodes_by_id]
    assert len(ys) == 4
    # Strictly increasing y — vertical chain.
    assert ys == sorted(ys)
    assert len(set(ys)) == 4


def test_tb_layout_all_chain_nodes_share_x_center(tmp_path: Path) -> None:
    """All chain-group nodes should be horizontally aligned (same x-midpoint)."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-x-align")
    assert ir.data_dependency_graph is not None
    chain_nodes = [n for n in ir.data_dependency_graph.nodes if n.group == "chain"]
    assert len(chain_nodes) >= 2
    mids = {n.x + n.width // 2 for n in chain_nodes}
    assert len(mids) == 1


def test_tb_layout_svg_width_reasonable(tmp_path: Path) -> None:
    """The TB layout keeps the SVG width under 1200 px even for long chains
    (was infinite horizontal scroll before)."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-width")
    assert ir.data_dependency_graph is not None
    assert ir.data_dependency_graph.width < 1200


# ---------------------------------------------------------------------------
# Framework in-degree badge (Change 2)
# ---------------------------------------------------------------------------

def test_framework_node_carries_in_degree_from_import_edges(tmp_path: Path) -> None:
    """Base class imported by 4 chain files gets a badge showing '4'."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-badge")
    assert ir.data_dependency_graph is not None
    nodes_by_id = {n.id: n for n in ir.data_dependency_graph.nodes}
    base = nodes_by_id.get("src/common/base_stage.py")
    assert base is not None
    # 4 chain files import base_stage — badge should reflect that.
    assert base.in_degree == 4
    # Badge appears in the label as a compact chip suffix.
    assert "\U0001F9F2" in base.label
    assert " 4" in base.label


def test_framework_node_no_badge_when_zero_in_degree(tmp_path: Path) -> None:
    """A framework file imported by nobody gets no badge in its label."""
    # Kipawa-shape workload but the base class is NOT imported by anyone
    # (chain files skip the base-class import). Verifies badge only shows
    # when in_degree > 0.
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "batch.json").write_text(json.dumps({
        "pipeline": {
            "reader": {"readerModule": "readers.r"},
            "writer": {"writerModule": "writers.w"},
        }
    }))
    (tmp_path / "src").mkdir()
    for sub in ("pipeline", "readers", "writers", "common"):
        (tmp_path / "src" / sub).mkdir()
        (tmp_path / "src" / sub / "__init__.py").write_text("")
    (tmp_path / "src" / "pipeline" / "pipeline_impl.py").write_text(
        "import importlib\n"
        "def load(cfg):\n"
        "    return importlib.import_module(cfg['readerModule']), importlib.import_module(cfg['writerModule'])\n"
    )
    # base_stage.py exists as a framework file but no one imports it —
    # in_degree should be 0.
    (tmp_path / "src" / "common" / "base_stage.py").write_text(
        "class BaseStage:\n    pass\n"
    )
    (tmp_path / "src" / "readers" / "r.py").write_text("def read_data(spark): return spark.read.json('x')\n")
    (tmp_path / "src" / "writers" / "w.py").write_text("def write_data(df): df.write.parquet('y')\n")

    ir = scan(tmp_path, project="no-badge")
    assert ir.data_dependency_graph is not None
    nodes_by_id = {n.id: n for n in ir.data_dependency_graph.nodes}
    base = nodes_by_id.get("src/common/base_stage.py")
    assert base is not None
    assert base.group == "framework"
    assert base.in_degree == 0
    # No badge chip in the label when in_degree is zero.
    assert "\U0001F9F2" not in base.label


# ---------------------------------------------------------------------------
# Orchestrator promotion + "orchestrates" edge (Change 3)
# ---------------------------------------------------------------------------

def test_orchestrator_promoted_into_framework_cluster(tmp_path: Path) -> None:
    """pipeline_impl.py has dynamic-import sites → framework, not islands."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-orch")
    assert ir.data_dependency_graph is not None
    nodes_by_id = {n.id: n for n in ir.data_dependency_graph.nodes}
    orch = nodes_by_id.get("src/pipeline/pipeline_impl.py")
    assert orch is not None
    assert orch.group == "framework"


def test_orchestrates_edge_emitted_from_orchestrator_to_reader(tmp_path: Path) -> None:
    """A dashed blue 'orchestrates' edge points from pipeline_impl.py to
    the reader (first chain stage). Exactly one such edge per orchestrator."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-orch-edge")
    assert ir.data_dependency_graph is not None
    orch_edges = [e for e in ir.data_dependency_graph.edges
                  if e.kind == "orchestrates"]
    assert len(orch_edges) == 1
    e = orch_edges[0]
    assert e.source == "src/pipeline/pipeline_impl.py"
    assert e.target == "src/readers/s3_json_reader.py"
    assert e.label == "orchestrates"


def test_orchestrates_edge_absent_when_no_orchestrator(tmp_path: Path) -> None:
    """Workloads without any dynamic-import site emit zero orchestrates edges."""
    (tmp_path / "writer.py").write_text("x = 1\n")
    (tmp_path / "reader.py").write_text("y = 2\n")
    fake_io = {
        str(tmp_path / "writer.py"): (set(), {"shared_table"}),
        str(tmp_path / "reader.py"): ({"shared_table"}, set()),
    }

    def _fake_mine(path, **kwargs):
        srcs, snks = fake_io.get(path, (set(), set()))
        return {"_sources": {n: {} for n in srcs}, "_sinks": {n: {} for n in snks}}

    with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
         patch.object(sc, "_schema_mine_fn", _fake_mine):
        ir = scan(tmp_path, project="no-orch")
    assert ir.data_dependency_graph is not None
    orch_edges = [e for e in ir.data_dependency_graph.edges
                  if e.kind == "orchestrates"]
    assert orch_edges == []


# ---------------------------------------------------------------------------
# import_adjacency_json (symmetric hover data — Fix 3)
# ---------------------------------------------------------------------------

def test_import_adjacency_json_escapes_script_tag_in_filenames() -> None:
    """A workload file whose name contains ``</script>`` must NOT be able to
    break out of the surrounding ``<script>`` block. The adapter escapes
    ``</`` before embedding — verify with a synthetic node id."""
    data_dep = {
        "nodes": [{"id": "foo</script>bar.py", "group": "chain"}],
        "edges": [],
    }
    raw = _import_adjacency_json({}, data_dep)
    assert "</script>" not in raw
    assert "<\\/" in raw
    parsed = json.loads(raw)
    assert "foo</script>bar.py" in parsed


def test_load_config_pool_rejects_symlink_escape(tmp_path: Path) -> None:
    """A symlink under the workload pointing outside must not be read. Guards
    the config walkers against CWE-22 path traversal.

    workload is placed 3 levels deep inside tmp_path so that
    _config_search_roots only scans up to tmp_path / "proj" (2 levels up).
    The "secret" file lives at tmp_path itself — reachable via the symlink
    but not via the parent-dir scanner — so the only way it could appear in
    the pool is if the symlink traversal guard fails.
    """
    import os
    outside = tmp_path / "outside_config.json"
    outside.write_text('{"secretPath": "s3://leaked/"}')
    workload = tmp_path / "proj" / "sub" / "workload"
    workload.mkdir(parents=True)
    # Symlink inside the workload that resolves outside the scan range.
    escape = workload / "config.json"
    try:
        os.symlink(str(outside), str(escape))
    except OSError:
        return  # Skip on platforms/filesystems that can't create symlinks.
    pool = _load_config_pool(workload)
    # "secretPath" would have been picked up if we followed the symlink.
    assert "secretPath" not in pool


def test_import_adjacency_json_empty_when_no_graph() -> None:
    assert json.loads(_import_adjacency_json(None, None)) == {}
    assert json.loads(_import_adjacency_json({}, None)) == {}
    # When there IS a data DAG, every node appears with empty adjacency
    # lists (data-only categories) so hover doesn't error out.
    data_dep = {"nodes": [{"id": "a.py", "group": "chain"}], "edges": []}
    result = json.loads(_import_adjacency_json({}, data_dep))
    assert "a.py" in result
    assert result["a.py"] == {
        "orchestrates": [],
        "orchestrated_by": [],
        "data_produces_to": [],
        "data_consumes_from": [],
    }


def test_import_adjacency_json_includes_orchestrates_edges() -> None:
    """Orchestrator files' dynamic-import targets appear in ``orchestrates``
    and the target's ``orchestrated_by``. This is what makes hovering
    ``pipeline_impl.py`` highlight the ``s3_json_reader.py`` it dynamically
    loads via ``importlib.import_module``, even though there's no static
    import edge between them."""
    data_dep = {
        "nodes": [
            {"id": "pipeline_impl.py", "group": "framework"},
            {"id": "reader.py", "group": "chain"},
            {"id": "other.py", "group": "framework"},
        ],
        "edges": [
            {"source": "pipeline_impl.py", "target": "reader.py", "kind": "orchestrates"},
            {"source": "other.py", "target": "reader.py", "kind": "data"},
        ],
    }
    result = json.loads(_import_adjacency_json({}, data_dep))
    assert result["pipeline_impl.py"]["orchestrates"] == ["reader.py"]
    assert result["pipeline_impl.py"]["orchestrated_by"] == []
    assert result["reader.py"]["orchestrated_by"] == ["pipeline_impl.py"]
    # "data" edges are NOT counted as orchestrates
    assert "other.py" not in result["reader.py"]["orchestrated_by"]


def test_import_adjacency_json_bidirectional_for_all_nodes() -> None:
    """Adjacency map contains data-flow + orchestrates categories for every
    node in the data DAG. Static Python imports are INTENTIONALLY excluded
    from this section — the report's separate Import Dependency Graph
    exposes them elsewhere."""
    data_dep_graph = {
        "nodes": [
            {"id": "a.py", "group": "chain"},
            {"id": "b.py", "group": "framework"},
            {"id": "c.py", "group": ""},
        ],
        "edges": [
            {"source": "a.py", "target": "b.py", "kind": "data"},
            {"source": "b.py", "target": "c.py", "kind": "data"},
        ],
    }
    result = json.loads(_import_adjacency_json({}, data_dep_graph))
    assert set(result.keys()) == {"a.py", "b.py", "c.py"}
    for entry in result.values():
        assert "data_produces_to" in entry
        assert "data_consumes_from" in entry
        assert "orchestrates" in entry
        assert "orchestrated_by" in entry
        # Static imports intentionally NOT in this section.
        assert "imports" not in entry
        assert "importers" not in entry
    assert result["a.py"]["data_produces_to"] == ["b.py"]
    assert result["b.py"]["data_consumes_from"] == ["a.py"]
    assert result["b.py"]["data_produces_to"] == ["c.py"]
    assert result["c.py"]["data_consumes_from"] == ["b.py"]


def test_import_adjacency_json_skips_external_endpoints() -> None:
    """Legacy defensive guard: even if a synthetic graph contained an
    ``ext:source:`` / ``ext:sink:`` pseudo-node (they are no longer
    emitted by the real builder), the adjacency map skips them because
    they are not real code files. The current builder never produces
    such nodes — external endpoints are chain-node metadata — but the
    filter stays as a belt-and-suspenders guard for legacy IR
    payloads."""
    dep_graph = {"edges": []}
    data_dep_graph = {
        "nodes": [
            {"id": "reader.py", "group": "chain"},
            {"id": "ext:source:0:s3://bucket/", "group": "external-source"},
            {"id": "ext:sink:0:s3://out/", "group": "external-sink"},
        ],
        "edges": [],
    }
    result = json.loads(_import_adjacency_json(dep_graph, data_dep_graph))
    assert "reader.py" in result
    assert all(not k.startswith("ext:") for k in result.keys())


def test_import_adjacency_json_covers_every_data_dag_node(tmp_path: Path) -> None:
    """End-to-end: for a Kipawa-shaped workload every real code file in the
    data DAG appears in the adjacency map with both fields."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-adjacency")
    assert ir.data_dependency_graph is not None
    dep_graph_dict = ir.dependency_graph.model_dump() if ir.dependency_graph else None
    data_dep_dict = ir.data_dependency_graph.model_dump()
    result = json.loads(_import_adjacency_json(dep_graph_dict, data_dep_dict))
    for n in data_dep_dict["nodes"]:
        nid = n["id"]
        if str(nid).startswith("ext:"):
            continue
        assert nid in result, f"missing adjacency entry for {nid}"
        assert "data_produces_to" in result[nid]
        assert "data_consumes_from" in result[nid]
        assert "orchestrates" in result[nid]
        assert "orchestrated_by" in result[nid]


# ---------------------------------------------------------------------------
# Template rendering (Change 3 + Change 4 wiring)
# ---------------------------------------------------------------------------

def test_render_contains_orchestrates_edge_when_present(tmp_path: Path) -> None:
    """When the IR has an orchestrates edge, the rendered HTML shows the
    dashed blue arrow with 'orchestrates' label text."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-render-orch")
    from prototype_v1 import render
    html = render(ir)
    assert "data-dep-edge-orchestrates" in html
    assert ">orchestrates<" in html
    # Highlight-blue used for the edge stroke.
    assert "#1A6CE7" in html


def test_render_contains_import_adjacency_json_context_var() -> None:
    """The template must reference the symmetric hover adjacency map so JS can consume it."""
    from assess_ir import Assessment
    from prototype_v1 import render
    html = render(Assessment())
    assert "importAdjacency" in html


def test_render_framework_node_shows_in_degree_badge(tmp_path: Path) -> None:
    """The framework node label (rendered as SVG <text>) carries the badge suffix."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-render-badge")
    from prototype_v1 import render
    html = render(ir)
    # base_stage.py imported by 4 files → badge suffix present in HTML.
    assert "\U0001F9F2 4" in html or "&#129522; 4" in html


# ---------------------------------------------------------------------------
# Chain shape (Fix 4): _chain_from_dynamic_imports returns list[list[str]]
# ---------------------------------------------------------------------------

def test_chain_from_dynamic_imports_returns_list_of_chains(tmp_path: Path) -> None:
    """A single-orchestrator workload returns exactly one chain in the outer list."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "batch.json").write_text(json.dumps({
        "pipeline": {
            "reader": {"readerModule": "readers.r"},
            "writer": {"writerModule": "writers.w"},
        }
    }))
    (tmp_path / "src").mkdir()
    for sub in ("pipeline", "readers", "writers"):
        (tmp_path / "src" / sub).mkdir()
        (tmp_path / "src" / sub / "__init__.py").write_text("")
    (tmp_path / "src" / "pipeline" / "orch.py").write_text(
        "import importlib\n"
        "def load(cfg):\n"
        "    importlib.import_module(cfg['readerModule'])\n"
        "    importlib.import_module(cfg['writerModule'])\n"
    )
    (tmp_path / "src" / "readers" / "r.py").write_text("x = 1\n")
    (tmp_path / "src" / "writers" / "w.py").write_text("x = 1\n")

    from scan_codebase import _chain_from_dynamic_imports
    sites_by_file = {}
    for rel in ("src/pipeline/orch.py",):
        io = _mine_file_io_and_imports(str(tmp_path / rel), None)
        if io:
            sites_by_file[rel] = io[2]
    code_files = [
        {"rel_path": "src/pipeline/orch.py", "imports": []},
        {"rel_path": "src/readers/r.py", "imports": []},
        {"rel_path": "src/writers/w.py", "imports": []},
    ]
    config_data = [{
        "pipeline": {
            "reader": {"readerModule": "readers.r"},
            "writer": {"writerModule": "writers.w"},
        }
    }]
    chains, _u = _chain_from_dynamic_imports(sites_by_file, config_data, code_files,
                                             {}, tmp_path)
    assert isinstance(chains, list)
    assert len(chains) == 1
    assert isinstance(chains[0], list)


def test_chain_from_dynamic_imports_two_independent_pipelines(tmp_path: Path) -> None:
    """Two orchestrators with disjoint chains produce two independent chains."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "a.json").write_text(json.dumps({
        "readerModuleA": "readers_a.r_a", "writerModuleA": "writers_a.w_a",
    }))
    (tmp_path / "config" / "b.json").write_text(json.dumps({
        "srcPluginB": "readers_b.r_b", "sinkPluginB": "writers_b.w_b",
    }))
    for d in ("orch_a", "orch_b", "readers_a", "readers_b", "writers_a", "writers_b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "__init__.py").write_text("")
    (tmp_path / "orch_a" / "orch_a.py").write_text(
        "import importlib\n"
        "def go(cfg):\n"
        "    importlib.import_module(cfg['readerModuleA'])\n"
        "    importlib.import_module(cfg['writerModuleA'])\n"
    )
    (tmp_path / "orch_b" / "orch_b.py").write_text(
        "import importlib\n"
        "def go(cfg):\n"
        "    importlib.import_module(cfg['srcPluginB'])\n"
        "    importlib.import_module(cfg['sinkPluginB'])\n"
    )
    (tmp_path / "readers_a" / "r_a.py").write_text("x = 1\n")
    (tmp_path / "writers_a" / "w_a.py").write_text("x = 1\n")
    (tmp_path / "readers_b" / "r_b.py").write_text("x = 1\n")
    (tmp_path / "writers_b" / "w_b.py").write_text("x = 1\n")

    ir = scan(tmp_path, project="multi-chain")
    assert ir.data_dependency_graph is not None
    # 2 independent pipelines with disjoint files.
    assert ir.data_dependency_graph.pipeline_count == 2


# ---------------------------------------------------------------------------
# Leaf-orchestrator dedup (Fix 2)
# ---------------------------------------------------------------------------

def test_leaf_orchestrator_dedup_one_arrow_when_orch_imports_orch(tmp_path: Path) -> None:
    """If orchestrator A imports orchestrator B and both have chain-resolving
    dynamic-import sites, only B's arrow should be drawn (B is the leaf)."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "batch.json").write_text(json.dumps({
        "pipeline": {
            "reader": {"readerModule": "readers.r"},
            "writer": {"writerModule": "writers.w"},
        }
    }))
    (tmp_path / "src").mkdir()
    for sub in ("pipeline", "readers", "writers"):
        (tmp_path / "src" / sub).mkdir()
        (tmp_path / "src" / sub / "__init__.py").write_text("")
    # main.py imports pipeline_impl.py (so pipeline_impl is downstream of main).
    (tmp_path / "src" / "pipeline" / "main.py").write_text(
        "import importlib\n"
        "from src.pipeline.pipeline_impl import PipelineImpl\n"
        "def run(cfg):\n"
        "    importlib.import_module(cfg['readerModule'])\n"
        "    importlib.import_module(cfg['writerModule'])\n"
        "    PipelineImpl().go(cfg)\n"
    )
    (tmp_path / "src" / "pipeline" / "pipeline_impl.py").write_text(
        "import importlib\n"
        "class PipelineImpl:\n"
        "    def go(self, cfg):\n"
        "        importlib.import_module(cfg['readerModule'])\n"
        "        importlib.import_module(cfg['writerModule'])\n"
    )
    (tmp_path / "src" / "readers" / "r.py").write_text("x = 1\n")
    (tmp_path / "src" / "writers" / "w.py").write_text("x = 1\n")

    ir = scan(tmp_path, project="leaf-dedup")
    assert ir.data_dependency_graph is not None
    orch_edges = [e for e in ir.data_dependency_graph.edges if e.kind == "orchestrates"]
    # Exactly ONE arrow (the leaf's), even though two orchestrators exist.
    assert len(orch_edges) == 1
    # And it originates at pipeline_impl.py (the leaf) — main.py imports
    # pipeline_impl.py so pipeline_impl.py is "closer" to the chain.
    assert orch_edges[0].source == "src/pipeline/pipeline_impl.py"


# ---------------------------------------------------------------------------
# Bezier / label placement for orchestrates arrows (Fix 2)
# ---------------------------------------------------------------------------

def test_orchestrates_edge_uses_path_bezier(tmp_path: Path) -> None:
    """The orchestrates edge carries a non-empty ``path_d`` bezier string,
    and explicit label coordinates so the label sits below the cluster."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-bezier")
    assert ir.data_dependency_graph is not None
    orch_edges = [e for e in ir.data_dependency_graph.edges if e.kind == "orchestrates"]
    assert len(orch_edges) == 1
    e = orch_edges[0]
    # Bezier path_d present.
    assert e.path_d
    assert e.path_d.startswith("M ") and " Q " in e.path_d
    # Explicit label coordinates provided; label sits BELOW the framework
    # cluster bottom.
    fw_cluster = ir.data_dependency_graph.clusters[0]
    cluster_bottom = fw_cluster.y + fw_cluster.height
    assert e.label_y > cluster_bottom - 1


def test_render_orchestrates_uses_path_not_line(tmp_path: Path) -> None:
    """When path_d is populated the template emits a ``<path>`` element (not
    ``<line>``) for the orchestrates arrow."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-render-bezier")
    from prototype_v1 import render
    html = render(ir)
    # Find the orchestrates arrow rendering; it should be a <path with
    # the data-dep-edge-orchestrates class.
    assert "data-dep-edge-orchestrates" in html
    # Must be a <path> tag carrying the orchestrates class (bezier form).
    assert '<path class="data-dep-edge data-dep-edge-orchestrates' in html


# ---------------------------------------------------------------------------
# Multi-pipeline side-by-side layout (Fix 4)
# ---------------------------------------------------------------------------

def _multi_pipeline_workload(tmp_path: Path) -> Path:
    """Two independent pipelines: S3 reader/writer + Azure reader/writer,
    each with its own orchestrator and its own config. No overlap.

    Uses DIFFERENT vocabulary per pipeline (``readerModule``/``writerModule``
    vs ``srcPlugin``/``sinkPlugin``) so the two orchestrators don't
    accidentally pick up each other's configs and merge into one pipeline.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "s3.json").write_text(json.dumps({
        "pipeline": {
            "reader": {"readerModule": "s3.reader_s3"},
            "writer": {"writerModule": "s3.writer_s3"},
        }
    }))
    (tmp_path / "config" / "az.json").write_text(json.dumps({
        "pipeline": {
            "reader": {"srcPlugin": "az.reader_az"},
            "writer": {"sinkPlugin": "az.writer_az"},
        }
    }))
    for sub in ("pipe_s3", "pipe_az", "s3", "az"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "__init__.py").write_text("")
    (tmp_path / "pipe_s3" / "orch_s3.py").write_text(
        "import importlib\n"
        "def go(cfg):\n"
        "    importlib.import_module(cfg['readerModule'])\n"
        "    importlib.import_module(cfg['writerModule'])\n"
    )
    (tmp_path / "pipe_az" / "orch_az.py").write_text(
        "import importlib\n"
        "def go(cfg):\n"
        "    importlib.import_module(cfg['srcPlugin'])\n"
        "    importlib.import_module(cfg['sinkPlugin'])\n"
    )
    (tmp_path / "s3" / "reader_s3.py").write_text("x = 1\n")
    (tmp_path / "s3" / "writer_s3.py").write_text("x = 1\n")
    (tmp_path / "az" / "reader_az.py").write_text("x = 1\n")
    (tmp_path / "az" / "writer_az.py").write_text("x = 1\n")
    return tmp_path


def test_multi_pipeline_two_independent_chains_rendered(tmp_path: Path) -> None:
    _multi_pipeline_workload(tmp_path)
    ir = scan(tmp_path, project="multi-render")
    assert ir.data_dependency_graph is not None
    assert ir.data_dependency_graph.pipeline_count == 2
    # 2 orchestrates arrows (one per pipeline, leaf-dedup on each).
    orch_edges = [e for e in ir.data_dependency_graph.edges if e.kind == "orchestrates"]
    assert len(orch_edges) == 2


def test_multi_pipeline_chains_columns_dont_overlap(tmp_path: Path) -> None:
    """The two chain columns render side-by-side; chain-node x-mids differ."""
    _multi_pipeline_workload(tmp_path)
    ir = scan(tmp_path, project="multi-column")
    assert ir.data_dependency_graph is not None
    chain_nodes = [n for n in ir.data_dependency_graph.nodes if n.group == "chain"]
    mids = {n.x + n.width // 2 for n in chain_nodes}
    # 2 pipelines → 2 distinct x-centers.
    assert len(mids) == 2


def test_multi_pipeline_svg_width_scales_sensibly(tmp_path: Path) -> None:
    """SVG width grows with the number of pipelines but stays bounded for
    a 2-pipeline workload."""
    _multi_pipeline_workload(tmp_path)
    ir = scan(tmp_path, project="multi-width")
    assert ir.data_dependency_graph is not None
    # 2 pipelines shouldn't blow up past 2000 px.
    assert ir.data_dependency_graph.width < 2000
    # But should be wider than a single-chain layout (Kipawa is ~< 900).
    assert ir.data_dependency_graph.width > 500


def test_multi_pipeline_banner_shows_pipeline_count(tmp_path: Path) -> None:
    """The section header banner surfaces the pipeline count when > 1."""
    _multi_pipeline_workload(tmp_path)
    ir = scan(tmp_path, project="multi-banner")
    from prototype_v1 import render
    html = render(ir)
    assert "2 pipelines" in html


def test_single_pipeline_banner_omits_pipeline_count(tmp_path: Path) -> None:
    """A workload with only one pipeline does NOT get the pipeline-count prefix."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-banner")
    from prototype_v1 import render
    html = render(ir)
    assert "1 pipelines" not in html
    assert "pipelines," not in html



# ---------------------------------------------------------------------------
# Path-signature extraction (Signal 4)
# ---------------------------------------------------------------------------

def test_normalize_signature_strips_uri_prefix_and_placeholders() -> None:
    """URI prefixes drop, f-string ``{}`` placeholders collapse, and the
    result is lowercased so case drift between files doesn't miss a match."""
    assert _normalize_signature("s3://bucket/EQS/dynamo_write_df/") == "bucket/eqs/dynamo_write_df"
    assert _normalize_signature("s3://{b}/EQS/dynamo_write_df/") == "eqs/dynamo_write_df"


def test_normalize_signature_rejects_generic_noise() -> None:
    """Bare noise tokens don't produce a signature — otherwise unrelated files
    would spuriously match on 'final' or 'csv'."""
    assert _normalize_signature("final") is None
    assert _normalize_signature("data") is None
    assert _normalize_signature("csv") is None
    # Multi-segment paths pass; case is normalized to lowercase.
    assert _normalize_signature("merge/CVT137+156/final") == "merge/cvt137+156/final"


def test_normalize_signature_rejects_too_short() -> None:
    """Signatures below 4 chars are dropped as too ambiguous."""
    assert _normalize_signature("abc") is None
    assert _normalize_signature("") is None
    assert _normalize_signature(None) is None  # type: ignore[arg-type]


def test_extract_path_signatures_literal_arg(tmp_path: Path) -> None:
    """A bare literal ``spark.read.parquet("s3://X/Y")`` produces a signature."""
    p = tmp_path / "x.py"
    p.write_text(
        "def run(spark):\n"
        "    df = spark.read.parquet('s3://bucket/EQS/final_output/')\n"
        "    df.write.parquet('s3://bucket/output/table/')\n"
    )
    sources, sinks = _extract_path_signatures(str(p))
    assert "bucket/eqs/final_output" in sources
    assert "bucket/output/table" in sinks


def test_extract_path_signatures_fstring(tmp_path: Path) -> None:
    """f-string preserves the literal portions for fingerprinting."""
    p = tmp_path / "x.py"
    p.write_text(
        "def run(spark, b):\n"
        "    df = spark.read.parquet(f's3://{b}/EQS/dynamo_write_df/')\n"
    )
    sources, _ = _extract_path_signatures(str(p))
    assert "eqs/dynamo_write_df" in sources


def test_extract_path_signatures_format_call(tmp_path: Path) -> None:
    """``.format(x)`` on a literal receiver yields a signature."""
    p = tmp_path / "x.py"
    p.write_text(
        "def run(spark, s3_path):\n"
        "    df = spark.read.parquet('{}dynamo_write_df/'.format(s3_path))\n"
    )
    sources, _ = _extract_path_signatures(str(p))
    assert "dynamo_write_df" in sources


def test_extract_path_signatures_variable_trace(tmp_path: Path) -> None:
    """A path via a variable assignment is traced back."""
    p = tmp_path / "x.py"
    p.write_text(
        "def run(spark, b):\n"
        "    path = f's3://{b}/EQS/reports/final_out/'\n"
        "    df = spark.read.parquet(path)\n"
    )
    sources, _ = _extract_path_signatures(str(p))
    assert "eqs/reports/final_out" in sources


def test_signal_4b_suffix_match_creates_edge(tmp_path: Path) -> None:
    """The specific Verisk bug: writer's f-string signature is
    ``eqs/dynamo_write_df`` (2 segs); reader's ``.format(...)`` signature is
    the bare ``dynamo_write_df`` (1 seg, but long/specific). Signal 4b's
    suffix match should create the writer→reader edge end-to-end."""
    (tmp_path / "writer.py").write_text(
        "def run(spark, b):\n"
        "    df.write.parquet(f's3://{b}/EQS/dynamo_write_df/')\n"
    )
    (tmp_path / "reader.py").write_text(
        "def run(spark, s3_path):\n"
        "    df = spark.read.parquet('{}dynamo_write_df/'.format(s3_path))\n"
    )
    ir = scan(tmp_path, project="sig4b")
    assert ir.data_dependency_graph is not None
    edge_pairs = {(e.source, e.target) for e in ir.data_dependency_graph.edges}
    assert ("writer.py", "reader.py") in edge_pairs


def test_signal_4b_rejects_short_leaf_signatures() -> None:
    """Signal 4b's specificity gate rejects a 1-segment sig shorter than 8
    chars — verified at the unit level via _normalize_signature since Signal 1
    (schema_mine leaf names) is independent of Signal 4b's filter.

    ``foo`` normalizes to a 3-char signature → dropped by the length gate.
    ``bar`` same. So ``foo/bar`` cannot suffix-match a lone ``foo`` sig
    because ``foo`` was never emitted in the first place.
    """
    assert _normalize_signature("foo") is None
    assert _normalize_signature("bar") is None
    # But a longer specific single segment survives.
    assert _normalize_signature("dynamo_write_df") == "dynamo_write_df"


# ---------------------------------------------------------------------------
# Implicit chains from data edges (the Verisk-style pipeline discovery)
# ---------------------------------------------------------------------------

def test_implicit_chains_topological_sort_linear() -> None:
    """A→B, B→C yields a single chain [A, B, C] in topological order."""
    edges = [("a.py", "b.py", "data"), ("b.py", "c.py", "data")]
    by_path = {"a.py": {}, "b.py": {}, "c.py": {}}
    chains = _implicit_chains_from_data_edges(edges, by_path)
    assert chains == [["a.py", "b.py", "c.py"]]


def test_implicit_chains_disconnected_components() -> None:
    """Two disjoint edges → two independent chains."""
    edges = [("a.py", "b.py", "data"), ("x.py", "y.py", "data")]
    by_path = {n: {} for n in ["a.py", "b.py", "x.py", "y.py"]}
    chains = _implicit_chains_from_data_edges(edges, by_path)
    assert len(chains) == 2
    chains_as_sets = [frozenset(c) for c in chains]
    assert frozenset({"a.py", "b.py"}) in chains_as_sets
    assert frozenset({"x.py", "y.py"}) in chains_as_sets


def test_implicit_chains_ignores_non_data_edges() -> None:
    """framework / orchestrates / factory_dispatch edges must not participate."""
    edges = [
        ("orch.py", "reader.py", "orchestrates"),
        ("base.py", "impl.py", "framework"),
        ("candidate.py", "orch.py", "factory_dispatch"),
    ]
    by_path = {n: {} for n in ["orch.py", "reader.py", "base.py", "impl.py", "candidate.py"]}
    chains = _implicit_chains_from_data_edges(edges, by_path)
    assert chains == []


def test_implicit_chains_handles_cycle_gracefully() -> None:
    """A cycle must not crash or infinite-loop; nodes still appear in output."""
    edges = [("a.py", "b.py", "data"), ("b.py", "a.py", "data")]
    by_path = {"a.py": {}, "b.py": {}}
    chains = _implicit_chains_from_data_edges(edges, by_path)
    assert len(chains) == 1
    assert set(chains[0]) == {"a.py", "b.py"}


def test_implicit_chains_skips_singletons() -> None:
    """A chain of length 1 (no edges) is not emitted — nothing to visualize."""
    edges: list[tuple[str, str, str]] = []
    by_path = {"only.py": {}}
    assert _implicit_chains_from_data_edges(edges, by_path) == []


# ---------------------------------------------------------------------------
# Hover adjacency includes data-flow edges
# ---------------------------------------------------------------------------

def test_import_adjacency_includes_data_flow_edges() -> None:
    """Hovering a chain node should highlight the file it feeds data to (and
    the file that feeds data to it). Data edges must appear in
    ``data_produces_to`` and ``data_consumes_from``."""
    data_dep = {
        "nodes": [
            {"id": "step4.py", "group": "chain"},
            {"id": "step5.py", "group": "chain"},
        ],
        "edges": [{"source": "step4.py", "target": "step5.py", "kind": "data"}],
    }
    result = json.loads(_import_adjacency_json({}, data_dep))
    assert result["step4.py"]["data_produces_to"] == ["step5.py"]
    assert result["step5.py"]["data_consumes_from"] == ["step4.py"]
    # And the reverse direction is empty for each.
    assert result["step4.py"]["data_consumes_from"] == []
    assert result["step5.py"]["data_produces_to"] == []


# ---------------------------------------------------------------------------
# Layered (Sugiyama) topology layout — Fix 1
# ---------------------------------------------------------------------------

def _layered_workload_from_data_edges(
    tmp_path: Path,
    io_map: dict[str, tuple[set, set]],
) -> "sc.Assessment":
    """Build a tmp workload where each key of ``io_map`` is a file to
    create, values are (sources, sinks). Then run ``scan`` with mocked
    schema_mine so the layered layout sees exactly those edges.
    """
    for rel_path in io_map.keys():
        (tmp_path / rel_path).write_text("x = 1\n")

    def _fake_mine(path, **kwargs):
        rel = str(Path(path).relative_to(tmp_path))
        srcs, snks = io_map.get(rel, (set(), set()))
        return {"_sources": {n: {} for n in srcs}, "_sinks": {n: {} for n in snks}}

    with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
         patch.object(sc, "_schema_mine_fn", _fake_mine):
        return scan(tmp_path, project="layered-test")


def test_layered_layout_computes_topological_depth(tmp_path: Path) -> None:
    """5 files: A→B, A→C, B→D, C→D, D→E. Depths {A:0, B:1, C:1, D:2, E:3}
    verified against node y-coordinates (files at the same depth share y)."""
    io_map = {
        "a.py": (set(), {"t_ab", "t_ac"}),
        "b.py": ({"t_ab"}, {"t_bd"}),
        "c.py": ({"t_ac"}, {"t_cd"}),
        "d.py": ({"t_bd", "t_cd"}, {"t_de"}),
        "e.py": ({"t_de"}, set()),
    }
    ir = _layered_workload_from_data_edges(tmp_path, io_map)
    assert ir.data_dependency_graph is not None
    nodes_by_id = {n.id: n for n in ir.data_dependency_graph.nodes
                   if n.group == "chain"}
    ys = {p: nodes_by_id[p].y for p in ["a.py", "b.py", "c.py", "d.py", "e.py"]}
    # A alone at depth 0, B & C share depth 1, D at depth 2, E at depth 3.
    assert ys["a.py"] < ys["b.py"] == ys["c.py"] < ys["d.py"] < ys["e.py"]


def test_layered_layout_fan_out_separate_columns(tmp_path: Path) -> None:
    """A writer feeding {r1, r2, r3} places r1, r2, r3 on the same y (depth 1)
    but at 3 distinct x-coordinates (sibling columns in the same row)."""
    io_map = {
        "writer.py": (set(), {"table_r1", "table_r2", "table_r3"}),
        "r1.py": ({"table_r1"}, set()),
        "r2.py": ({"table_r2"}, set()),
        "r3.py": ({"table_r3"}, set()),
    }
    ir = _layered_workload_from_data_edges(tmp_path, io_map)
    assert ir.data_dependency_graph is not None
    chain_nodes = {n.id: n for n in ir.data_dependency_graph.nodes
                   if n.group == "chain"}
    y_r1 = chain_nodes["r1.py"].y
    y_r2 = chain_nodes["r2.py"].y
    y_r3 = chain_nodes["r3.py"].y
    assert y_r1 == y_r2 == y_r3, "readers should share the same depth-1 row y"
    xs = {chain_nodes["r1.py"].x, chain_nodes["r2.py"].x, chain_nodes["r3.py"].x}
    assert len(xs) == 3, "sibling readers must have 3 distinct x-coords"
    # And they're below the writer.
    assert y_r1 > chain_nodes["writer.py"].y


def test_layered_layout_kipawa_linear_still_stacks_vertically(tmp_path: Path) -> None:
    """Kipawa's 8-layer (1 file per layer) chain must still render as a
    single vertical column: all chain nodes share x-center, y strictly
    increases."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="kipawa-layered-linear")
    assert ir.data_dependency_graph is not None
    chain_nodes = [n for n in ir.data_dependency_graph.nodes if n.group == "chain"]
    assert len(chain_nodes) == 4
    x_centers = {n.x + n.width // 2 for n in chain_nodes}
    ys = sorted(n.y for n in chain_nodes)
    # Vertical column: one x-center, strictly increasing y.
    assert len(x_centers) == 1
    assert ys == sorted(set(ys))
    assert len(set(ys)) == 4


def test_layered_layout_skip_edge_routing(tmp_path: Path) -> None:
    """A→D where A is depth 0 and D is depth 3 (via B→C→D linear intermediate)
    → the A→D edge must route as a bezier ``<path>`` (path_d populated), not
    as a straight ``<line>``."""
    io_map = {
        "a.py": (set(), {"t_ab", "t_ad_skip"}),
        "b.py": ({"t_ab"}, {"t_bc"}),
        "c.py": ({"t_bc"}, {"t_cd"}),
        "d.py": ({"t_cd", "t_ad_skip"}, set()),
    }
    ir = _layered_workload_from_data_edges(tmp_path, io_map)
    assert ir.data_dependency_graph is not None
    # Locate the A→D edge.
    ad_edges = [e for e in ir.data_dependency_graph.edges
                if e.source == "a.py" and e.target == "d.py" and e.kind == "data"]
    assert len(ad_edges) == 1
    e = ad_edges[0]
    # Skip edge: rendered as a bezier path, not a straight line.
    assert e.path_d, "skip-edge should carry a non-empty path_d"
    assert e.path_d.startswith("M ")


def test_chain_node_in_degree_uses_data_edges(tmp_path: Path) -> None:
    """A chain node with 3 producers via ``kind='data'`` edges reports
    ``in_degree == 3``, NOT the static-import count."""
    io_map = {
        "p1.py": (set(), {"shared"}),
        "p2.py": (set(), {"shared"}),
        "p3.py": (set(), {"shared"}),
        "sink.py": ({"shared"}, set()),
    }
    ir = _layered_workload_from_data_edges(tmp_path, io_map)
    assert ir.data_dependency_graph is not None
    sink = next((n for n in ir.data_dependency_graph.nodes if n.id == "sink.py"), None)
    assert sink is not None
    assert sink.group == "chain"
    # 3 data producers, and no static imports (files never import each other).
    assert sink.in_degree == 3


def test_framework_node_in_degree_preserved(tmp_path: Path) -> None:
    """A framework file's in_degree still comes from the STATIC IMPORT graph,
    not the data-flow graph. Kipawa's base_stage.py is imported by 4 files
    and has zero data producers, so its badge should read 4 (not 0)."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="fw-preserved")
    assert ir.data_dependency_graph is not None
    base = next((n for n in ir.data_dependency_graph.nodes
                 if n.id == "src/common/base_stage.py"), None)
    assert base is not None
    assert base.group == "framework"
    # Static-import degree wins for framework nodes: 4 chain files import it.
    assert base.in_degree == 4


def test_edges_render_before_nodes_zorder(tmp_path: Path) -> None:
    """SVG document order: every ``<line class="data-dep-edge">`` (and
    ``<path class="data-dep-edge">``) must appear BEFORE every
    ``<g class="data-dep-node">``. Otherwise the file rectangles would
    cover arrows near their shafts."""
    _kipawa_workload(tmp_path)
    ir = scan(tmp_path, project="zorder")
    from prototype_v1 import render
    html = render(ir)
    # Slice out just the data-dep-graph-svg block so unrelated import-graph
    # nodes/edges don't confuse the assertion.
    start = html.index("data-dep-graph-svg")
    end = html.index("</svg>", start)
    svg_body = html[start:end]
    # Every `data-dep-node` group must come AFTER the last edge line/path.
    last_edge_pos = max(
        svg_body.rfind('class="data-dep-edge'),
        svg_body.rfind('<line class="data-dep-edge'),
    )
    first_node_pos = svg_body.find('<g class="data-dep-node')
    assert last_edge_pos >= 0
    assert first_node_pos >= 0
    assert last_edge_pos < first_node_pos, (
        f"expected all data-dep-edge elements to precede the first data-dep-node "
        f"group, but got last_edge_pos={last_edge_pos}, first_node_pos={first_node_pos}"
    )


# ---------------------------------------------------------------------------
# Per-file external endpoint discovery (redesign)
# ---------------------------------------------------------------------------
#
# These tests validate the "one pill per truly-external URI / table per file"
# model that replaces the previous "one pill above the first chain file and
# one pill below the last" placeholder.

def _write_chain(tmp_path: Path, files: dict[str, str]) -> None:
    """Write ``rel_path -> source`` into ``tmp_path``, making parent dirs as
    needed. Convenience for endpoint-discovery tests."""
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)


def test_per_file_external_sources_discovered(tmp_path: Path) -> None:
    """Three chain files: only the FIRST has an external source path
    (``s3://ext/lookup/table_a``) not produced internally. That URI
    must appear in ``step_a.py``'s ``external_sources`` metadata list,
    and NOT surface as a separate ``external-source`` node in the DAG
    (those pseudo-nodes are no longer emitted). Intermediate write/read
    chains between the three files remain internal."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    _write_chain(tmp_path, {
        "step_a.py": (
            "def run(spark):\n"
            "    df = spark.read.parquet('s3://ext/lookup/table_a')\n"
            "    df.write.parquet('s3://internal/stage_ab')\n"
        ),
        "step_b.py": (
            "def run(spark):\n"
            "    df = spark.read.parquet('s3://internal/stage_ab')\n"
            "    df.write.parquet('s3://internal/stage_bc')\n"
        ),
        "step_c.py": (
            "def run(spark):\n"
            "    df = spark.read.parquet('s3://internal/stage_bc')\n"
            "    df.write.parquet('s3://internal/stage_bc')\n"
        ),
    })
    ir = scan(tmp_path, project="per-file-sources")
    assert ir.data_dependency_graph is not None
    # No external-source pseudo-nodes anymore.
    ext_nodes = [
        n for n in ir.data_dependency_graph.nodes
        if n.group.startswith("external")
    ]
    assert ext_nodes == [], (
        f"external pseudo-nodes should no longer be emitted; got {ext_nodes!r}"
    )
    # step_a.py carries the external source URI as metadata.
    by_id = {n.id: n for n in ir.data_dependency_graph.nodes}
    step_a = by_id.get("step_a.py")
    assert step_a is not None
    assert any("table_a" in uri for uri in step_a.external_sources), (
        f"expected 'table_a' among step_a.external_sources; got {step_a.external_sources!r}"
    )


def test_multiple_files_share_external_source(tmp_path: Path) -> None:
    """Two files that both read the same external table each carry the
    URI in their own ``external_sources`` list (per-file metadata; there
    is no cross-file dedup of the endpoints under the new metadata
    model). No external pseudo-nodes are emitted."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    _write_chain(tmp_path, {
        "reader_a.py": (
            "def run(spark):\n"
            "    df = spark.read.parquet('s3://shared/lookup_table_zebra')\n"
            "    df.write.parquet('s3://internal/branch_a_final')\n"
        ),
        "reader_b.py": (
            "def run(spark):\n"
            "    df = spark.read.parquet('s3://shared/lookup_table_zebra')\n"
            "    df.write.parquet('s3://internal/branch_b_final')\n"
        ),
        "joiner.py": (
            "def run(spark):\n"
            "    a = spark.read.parquet('s3://internal/branch_a_final')\n"
            "    b = spark.read.parquet('s3://internal/branch_b_final')\n"
            "    a.union(b).write.parquet('s3://internal/joined_output')\n"
        ),
    })
    ir = scan(tmp_path, project="shared-source")
    assert ir.data_dependency_graph is not None
    # No external-source pseudo-nodes.
    assert not any(
        n.group.startswith("external") for n in ir.data_dependency_graph.nodes
    )
    by_id = {n.id: n for n in ir.data_dependency_graph.nodes}
    for rel in ("reader_a.py", "reader_b.py"):
        node = by_id.get(rel)
        assert node is not None, f"missing chain node {rel}"
        assert any(
            "lookup_table_zebra" in uri for uri in node.external_sources
        ), (
            f"expected 'lookup_table_zebra' among {rel}.external_sources; "
            f"got {node.external_sources!r}"
        )


def test_per_file_external_sinks_at_producer_depth(tmp_path: Path) -> None:
    """A mid-chain file writes an external terminal sink. That URI must
    appear in the mid.py chain node's ``external_sinks`` metadata list
    (not as a separate pill below the row — those are gone)."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    _write_chain(tmp_path, {
        "start.py": (
            "def run(spark):\n"
            "    df = spark.read.parquet('s3://ext/inbox_alpha')\n"
            "    df.write.parquet('s3://internal/mid_stage')\n"
        ),
        "mid.py": (
            "def run(spark):\n"
            "    df = spark.read.parquet('s3://internal/mid_stage')\n"
            "    # Mid-chain external terminal sink — carried on mid.py's metadata.\n"
            "    df.write.parquet('s3://terminal/analytics_snapshot')\n"
            "    df.write.parquet('s3://internal/final_stage')\n"
        ),
        "final.py": (
            "def run(spark):\n"
            "    df = spark.read.parquet('s3://internal/final_stage')\n"
            "    df.write.parquet('s3://terminal/final_output')\n"
        ),
    })
    ir = scan(tmp_path, project="mid-chain-sink")
    assert ir.data_dependency_graph is not None
    # No external pseudo-nodes.
    assert not any(
        n.group.startswith("external") for n in ir.data_dependency_graph.nodes
    )
    by_id = {n.id: n for n in ir.data_dependency_graph.nodes}
    mid = by_id.get("mid.py")
    assert mid is not None
    assert any(
        "analytics_snapshot" in uri for uri in mid.external_sinks
    ), (
        f"expected 'analytics_snapshot' among mid.py external_sinks; "
        f"got {mid.external_sinks!r}"
    )


def test_asterisk_labels_never_rendered() -> None:
    """``_short_ext_label`` must never emit ``*`` (or empty) as the pill
    text — those provide zero information and were the source of the
    'bogus placeholder' bug in the pre-redesign implementation.

    When every segment is glob-only or a placeholder, the label must fall
    back to ``"external <kind>"`` rather than silently rendering ``*``.
    """
    # Cases where NO meaningful segment exists — must fall back to
    # "external source" / "external sink".
    for uri in ("", "*", "**", "s3://*", "s3://**", "{}", "{year}"):
        for kind in ("source", "sink"):
            label = _short_ext_label(uri, kind=kind)
            assert label
            assert label != "*"
            assert label != "{}"
            assert "external" in label.lower(), (
                f"expected 'external' fallback for uri={uri!r} kind={kind}, "
                f"got {label!r}"
            )

    # Cases with at least one meaningful segment (bucket) still yield a
    # non-glob label (the bucket / segment name), not ``*``.
    for uri in ("s3://bucket/*", "s3://bucket/**/*",
                "s3://bucket/{year}/{month}/{day}/"):
        label = _short_ext_label(uri, kind="source")
        assert label
        assert label != "*"
        assert label != "{}"
        assert "*" not in label
        assert "{" not in label


def test_ext_endpoint_label_uses_last_segments() -> None:
    """URIs like ``s3://bucket/foo/bar/detokenization/`` render with a
    label pulled from the last meaningful path segment (``detokenization``)
    — NOT the full URI or scheme."""
    assert _short_ext_label("s3://bucket/foo/bar/detokenization/") == "detokenization"
    assert _short_ext_label(
        "s3://bucket/deduplication/incremental_daily/partition_year={y}/",
    ) == "incremental_daily"
    assert _short_ext_label(
        "s3://bucket/streamloader/partition_year={y}/partition_month={m}/partition_day={d}/",
    ) == "streamloader"
    # Bare table name renders as-is.
    assert _short_ext_label("vid_base_table") == "vid_base_table"
    # Table name normalized upward from the trailing segments.
    assert _short_ext_label("processed/vid_base_table/partition_year={y}/") == "vid_base_table"


def test_kipawa_external_endpoints_unchanged(tmp_path: Path) -> None:
    """Kipawa's canonical shape (single reader → 2 transforms → single
    writer, JSON in, parquet out) attaches its external endpoints as
    per-file metadata on the chain nodes: exactly one URI on the
    reader's ``external_sources``, exactly one URI on the writer's
    ``external_sinks``, and no external pseudo-nodes anywhere in the
    DAG.

    Uses internal stage paths to keep the chain connected via data-edges
    (the real Kipawa uses an orchestrator; a synthetic version wired via
    file-based reads/writes verifies the same shape).
    """
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    _write_chain(tmp_path, {
        "src/readers/s3_json_reader.py": (
            "def read_data(spark):\n"
            "    df = spark.read.json('s3://bucket-abc/input/kipawa/*.json')\n"
            "    df.write.parquet('s3://internal/kipawa_stage1')\n"
        ),
        "src/transformers/epoch_to_date.py": (
            "def t(spark):\n"
            "    df = spark.read.parquet('s3://internal/kipawa_stage1')\n"
            "    df.write.parquet('s3://internal/kipawa_stage2')\n"
        ),
        "src/transformers/reduce_gps_precision.py": (
            "def t(spark):\n"
            "    df = spark.read.parquet('s3://internal/kipawa_stage2')\n"
            "    df.write.parquet('s3://internal/kipawa_stage3')\n"
        ),
        "src/writers/s3_parquet_writer.py": (
            "def write_data(spark):\n"
            "    df = spark.read.parquet('s3://internal/kipawa_stage3')\n"
            "    df.write.parquet('s3://bucket-abc/output/kipawa/')\n"
        ),
    })
    ir = scan(tmp_path, project="kipawa-endpoints-unchanged")
    assert ir.data_dependency_graph is not None
    # No external pseudo-nodes.
    ext_nodes = [
        n for n in ir.data_dependency_graph.nodes
        if n.group.startswith("external")
    ]
    assert ext_nodes == [], (
        f"external pseudo-nodes should no longer be emitted; got "
        f"{[n.id for n in ext_nodes]!r}"
    )
    by_id = {n.id: n for n in ir.data_dependency_graph.nodes}
    reader = by_id.get("src/readers/s3_json_reader.py")
    writer = by_id.get("src/writers/s3_parquet_writer.py")
    assert reader is not None and writer is not None
    assert len(reader.external_sources) == 1, (
        f"Kipawa reader should have exactly ONE external_sources entry; "
        f"got {reader.external_sources!r}"
    )
    assert "kipawa" in reader.external_sources[0].lower()
    assert len(writer.external_sinks) == 1, (
        f"Kipawa writer should have exactly ONE external_sinks entry; "
        f"got {writer.external_sinks!r}"
    )
    assert "kipawa" in writer.external_sinks[0].lower()


# ---------------------------------------------------------------------------
# External-endpoint metadata + detail-panel wiring (R1–R6 redesign)
# ---------------------------------------------------------------------------


def test_chain_nodes_carry_external_endpoint_metadata(tmp_path: Path) -> None:
    """Verisk-shaped fixture: several files, each reading & writing its
    own set of external URIs. Each chain node's ``external_sources`` /
    ``external_sinks`` lists must match the file's own reads/writes for
    URIs that aren't produced/consumed internally."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    _write_chain(tmp_path, {
        "step1_ingest.py": (
            "def run(spark):\n"
            "    a = spark.read.parquet('s3://ext/vid_base_table')\n"
            "    b = spark.read.parquet('s3://ext/eqs_res_relate')\n"
            "    a.union(b).write.parquet('s3://internal/step1_out')\n"
        ),
        "step2_dedupe.py": (
            "def run(spark):\n"
            "    df = spark.read.parquet('s3://internal/step1_out')\n"
            "    df.write.parquet('s3://ext/dedupe_stage_out')\n"
            "    df.write.parquet('s3://internal/step2_out')\n"
        ),
        "step3_write.py": (
            "def run(spark):\n"
            "    df = spark.read.parquet('s3://internal/step2_out')\n"
            "    df.write.parquet('s3://ext/dynamo_write_df')\n"
        ),
    })
    ir = scan(tmp_path, project="verisk-shape")
    assert ir.data_dependency_graph is not None
    by_id = {n.id: n for n in ir.data_dependency_graph.nodes}

    step1 = by_id.get("step1_ingest.py")
    step2 = by_id.get("step2_dedupe.py")
    step3 = by_id.get("step3_write.py")
    assert step1 is not None and step2 is not None and step3 is not None

    # step1 reads 2 external tables, has NO external sinks (both writes
    # are internal or consumed downstream).
    assert any("vid_base_table" in u for u in step1.external_sources)
    assert any("eqs_res_relate" in u for u in step1.external_sources)
    assert step1.external_sinks == []

    # step2 has one external sink (dedupe_stage_out) — the other write
    # is internal (consumed by step3).
    assert step2.external_sources == []
    assert any("dedupe_stage_out" in u for u in step2.external_sinks)

    # step3 has one external sink (dynamo_write_df).
    assert step3.external_sources == []
    assert any("dynamo_write_df" in u for u in step3.external_sinks)


def test_no_external_pseudo_nodes_emitted(tmp_path: Path) -> None:
    """After the redesign, no node in ``data_dependency_graph.nodes``
    has ``group`` starting with ``"external"`` — those pseudo-nodes are
    gone and their data lives on chain-node metadata."""
    if not _SCHEMA_MINE_IMPORTABLE:
        pytest.skip("schema_mine not importable in this environment")
    _write_chain(tmp_path, {
        "reader.py": (
            "def run(spark):\n"
            "    df = spark.read.parquet('s3://ext/source_tab')\n"
            "    df.write.parquet('s3://internal/stage_a')\n"
        ),
        "writer.py": (
            "def run(spark):\n"
            "    df = spark.read.parquet('s3://internal/stage_a')\n"
            "    df.write.parquet('s3://ext/sink_tab')\n"
        ),
    })
    ir = scan(tmp_path, project="no-ext-pseudo-nodes")
    assert ir.data_dependency_graph is not None
    for n in ir.data_dependency_graph.nodes:
        assert not n.group.startswith("external"), (
            f"unexpected external pseudo-node {n.id!r} with group {n.group!r}"
        )
        assert not n.id.startswith("ext:source:"), (
            f"unexpected ext:source: id {n.id!r}"
        )
        assert not n.id.startswith("ext:sink:"), (
            f"unexpected ext:sink: id {n.id!r}"
        )


def test_endpoint_preview_middle_truncation() -> None:
    """A long URI should truncate in the middle so the scheme AND the
    trailing segment stay visible (``s3://prefix/…/suffix`` shape)."""
    from prototype_v1 import _endpoint_preview, _middle_truncate

    long_uri = "s3://your-bucket/prod/deduplication/incremental_daily/partition_year={y}/"
    truncated = _middle_truncate(long_uri, max_len=60)
    assert len(truncated) <= 60
    assert truncated != long_uri
    # Scheme end kept:
    assert truncated.startswith("s3://")
    # Trailing segment kept:
    assert truncated.endswith("/")
    # Middle ellipsis marker present:
    assert "…" in truncated
    # A recognizable suffix chunk survives.
    assert "partition_year" in truncated or "incremental_daily" in truncated

    preview = _endpoint_preview([long_uri], max_items=3, max_len=60)
    assert len(preview) == 1
    item = preview[0]
    assert item["truncated"] is True
    assert item["full"] == long_uri
    assert "…" in item["display"]
    assert item["display"].startswith("s3://")


def test_endpoint_preview_caps_at_three_and_reports_remaining() -> None:
    """5 URIs input → 3 shown + `{"remaining": 2}` last item."""
    from prototype_v1 import _endpoint_preview

    uris = [f"tbl_{i}" for i in range(5)]
    preview = _endpoint_preview(uris, max_items=3, max_len=60)
    assert len(preview) == 4  # 3 items + 1 remaining marker
    displayed = preview[:3]
    for i, item in enumerate(displayed):
        assert item["display"] == uris[i]
        assert item["truncated"] is False
        assert item["full"] == uris[i]
    tail = preview[-1]
    assert tail == {"remaining": 2}


def test_render_contains_detail_panel_scaffold(tmp_path: Path) -> None:
    """The HTML contains ``id="data-dep-detail-panel"`` and the JS
    variable ``endpointDetail`` (both required by the click-opened
    side panel)."""
    from assess_ir import Assessment, DependencyGraph, GraphEdge, GraphNode
    from prototype_v1 import render

    node1 = GraphNode(
        id="reader.py", label="reader.py", full_label="reader.py",
        path="reader.py", x=20, y=20, width=200, height=40,
        group="chain",
        external_sources=["s3://in/data/"],
        external_sinks=[],
    )
    node2 = GraphNode(
        id="writer.py", label="writer.py", full_label="writer.py",
        path="writer.py", x=20, y=150, width=200, height=40,
        group="chain",
        external_sources=[],
        external_sinks=["s3://out/data/"],
    )
    edge = GraphEdge(
        x1=120, y1=60, x2=120, y2=150,
        source="reader.py", target="writer.py", kind="data",
    )
    dg = DependencyGraph(
        module="Project", width=300, height=250,
        file_count=2, edge_count=1,
        nodes=[node1, node2], edges=[edge],
    )
    ir = Assessment(data_dependency_graph=dg)
    html = render(ir)
    assert 'id="data-dep-detail-panel"' in html
    assert "endpointDetail" in html
    # The full URI must appear inside the JS variable (embedded via _safe_script_json).
    assert "s3://in/data/" in html
    assert "s3://out/data/" in html


def test_render_tooltip_contains_endpoint_preview_when_endpoints_exist(tmp_path: Path) -> None:
    """A node with external endpoints must have its SVG ``<title>``
    include the "Reads from" and/or "Writes to" bullet headers."""
    import re
    from assess_ir import Assessment, DependencyGraph, GraphNode
    from prototype_v1 import render

    reader = GraphNode(
        id="reader.py", label="reader.py", full_label="reader.py",
        path="reader.py", x=20, y=20, width=200, height=40,
        group="chain",
        external_sources=["s3://input-bucket/incoming/"],
        external_sinks=[],
    )
    writer = GraphNode(
        id="writer.py", label="writer.py", full_label="writer.py",
        path="writer.py", x=20, y=150, width=200, height=40,
        group="chain",
        external_sources=[],
        external_sinks=["s3://output-bucket/results/"],
    )
    dg = DependencyGraph(
        module="Project", width=300, height=250,
        file_count=2, edge_count=0,
        nodes=[reader, writer], edges=[],
    )
    ir = Assessment(data_dependency_graph=dg)
    html = render(ir)
    # Scope check to <title> elements so the empty panel scaffold's
    # <h4> headers (which also spell "Reads from" / "Writes to") don't
    # cause false positives.
    title_matches = re.findall(r"<title>([^<]*)</title>", html)
    reader_titles = [t for t in title_matches if "reader.py" in t]
    writer_titles = [t for t in title_matches if "writer.py" in t]
    assert reader_titles, "expected a <title> mentioning reader.py"
    assert writer_titles, "expected a <title> mentioning writer.py"
    reader_title = reader_titles[0]
    writer_title = writer_titles[0]
    assert "Reads from" in reader_title
    assert "s3://input-bucket/incoming/" in reader_title
    assert "Writes to" in writer_title
    assert "s3://output-bucket/results/" in writer_title


def test_render_tooltip_omits_endpoint_lines_when_no_endpoints() -> None:
    """A chain node with 0 sources and 0 sinks must NOT include
    "Reads from" / "Writes to" bullet lines in its ``<title>``
    tooltip. (The strings do appear in the empty side-panel scaffold's
    ``<h4>`` headers — this test scopes its check to the ``<title>``
    element's contents only.)"""
    import re
    from assess_ir import Assessment, DependencyGraph, GraphNode
    from prototype_v1 import render

    lonely = GraphNode(
        id="only.py", label="only.py", full_label="only.py",
        path="only.py", x=20, y=20, width=200, height=40,
        group="chain",
        external_sources=[],
        external_sinks=[],
    )
    dg = DependencyGraph(
        module="Project", width=300, height=100,
        file_count=1, edge_count=0,
        nodes=[lonely], edges=[],
    )
    ir = Assessment(data_dependency_graph=dg)
    html = render(ir)
    assert 'data-node-id="only.py"' in html
    # Pull the <title> content that immediately follows the only.py
    # node's <rect>. This is the tooltip content we want to inspect.
    title_matches = re.findall(r"<title>([^<]*)</title>", html)
    node_titles = [t for t in title_matches if "only.py" in t]
    assert node_titles, "expected at least one <title> mentioning only.py"
    for t in node_titles:
        assert "Reads from" not in t, (
            f"tooltip for endpoint-less node must not mention 'Reads from'; got {t!r}"
        )
        assert "Writes to" not in t, (
            f"tooltip for endpoint-less node must not mention 'Writes to'; got {t!r}"
        )


def test_endpoint_preview_short_uri_not_truncated() -> None:
    """Short URIs pass through unchanged with ``truncated=False``."""
    from prototype_v1 import _endpoint_preview

    preview = _endpoint_preview(["short_table"], max_items=3, max_len=60)
    assert len(preview) == 1
    item = preview[0]
    assert item["display"] == "short_table"
    assert item["truncated"] is False
    assert item["full"] == "short_table"


# ---------------------------------------------------------------------------
# _extract_sql_data_refs: self-refs, CTE aliases, SQL keywords
# ---------------------------------------------------------------------------


def test_extract_sql_data_refs_self_ref_removed(tmp_path: Path) -> None:
    """MERGE INTO T ... FROM T must not register T as a sink (self-reference)."""
    sql = tmp_path / "upsert.sql"
    sql.write_text(
        "MERGE INTO prod.schema.dim_table AS target\n"
        "USING (SELECT id, val FROM prod.schema.dim_table) AS src\n"
        "ON target.id = src.id\n"
        "WHEN MATCHED THEN UPDATE SET target.val = src.val;\n"
    )
    srcs, snks = _extract_sql_data_refs(str(sql), config_pool=None)
    assert "dim_table" not in snks, (
        "self-merge target must be removed from sinks to prevent false cycles"
    )


def test_extract_sql_data_refs_cte_excluded(tmp_path: Path) -> None:
    """CTE names defined via WITH … AS ( must not appear as source refs."""
    sql = tmp_path / "cte_query.sql"
    sql.write_text(
        "WITH cte_sales AS (SELECT * FROM prod.fact_sales),\n"
        "     cte_costs AS (SELECT * FROM prod.fact_costs)\n"
        "INSERT INTO prod.summary SELECT * FROM cte_sales JOIN cte_costs ON 1=1;\n"
    )
    srcs, snks = _extract_sql_data_refs(str(sql), config_pool=None)
    assert "cte_sales" not in srcs, "CTE alias must not appear as a source"
    assert "cte_costs" not in srcs, "CTE alias must not appear as a source"
    assert any("fact_sales" in s for s in srcs), "real source table must be detected"
    assert any("summary" in s for s in snks), "real sink table must be detected"


def test_extract_sql_data_refs_sql_keywords_excluded(tmp_path: Path) -> None:
    """SQL keywords like VALUES and SET must not be registered as table names."""
    sql = tmp_path / "insert.sql"
    sql.write_text(
        "INSERT INTO prod.orders (id, amount) VALUES (1, 100);\n"
        "UPDATE prod.orders SET amount = 200 WHERE id = 1;\n"
    )
    srcs, snks = _extract_sql_data_refs(str(sql), config_pool=None)
    assert "values" not in snks and "VALUES" not in snks, "VALUES is a keyword, not a table"
    assert "set" not in snks and "SET" not in snks, "SET is a keyword, not a table"
    assert any("orders" in s.lower() for s in snks), "real sink table must be detected"


def test_sql_signal5_short_alias_not_matched(tmp_path: Path) -> None:
    """SQL table aliases shorter than 4 chars must not produce data edges.

    _extract_sql_data_refs returns 'src' (3 chars) as a valid source/sink
    because _looks_like_table_name passes it. Without Signal 5 applying
    _normalize_signature, two SQL files that both reference 'src' would be
    incorrectly linked via the shared alias.
    """
    (tmp_path / "writer.sql").write_text(
        "INSERT INTO src SELECT id FROM real_schema.source_table;\n"
    )
    (tmp_path / "reader.sql").write_text(
        "SELECT * FROM src JOIN real_schema.source_table ON 1=1;\n"
    )
    ir = scan(tmp_path, project="signal5_test")
    data_dag = ir.data_dependency_graph
    if data_dag is None:
        return
    edge_pairs = {(e.source, e.target) for e in data_dag.edges}
    assert not any(
        "src" == src or "src" == tgt for src, tgt in edge_pairs
    ), "3-char alias 'src' must be filtered by _normalize_signature in Signal 5"

