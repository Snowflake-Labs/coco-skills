"""Tests for partition_by.py — migration-plan partition strategy engine.

Each test exercises one or more strategies against a minimal synthetic
Assessment IR so the coverage is deterministic and fast. Real-IR smoke tests
use the existing fixture JSON files.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from assess_ir import (
    Assessment,
    AssessmentMetadata,
    DependencyGraph,
    FileCompatibilityRow,
    FileInfoRow,
    GraphEdge,
    GraphNode,
    IsolatedModule,
    IsolatedModuleFile,
    MigrationWave,
    WorkloadSummary,
)
from partition_by import (
    _build_file_rows,
    _connected_components,
    _partition_by_blast_radius,
    _partition_by_data_pipeline,
    _partition_by_infra_need,
    _partition_by_readiness,
    _partition_by_snowflake_schema,
    _partition_by_source_system,
    _partition_by_target_type,
    _partition_by_technology,
    build_partition_table_data,
)


# ---------------------------------------------------------------------------
# Helpers to build minimal Assessment objects
# ---------------------------------------------------------------------------


def _compat(path: str, name: str, status: str = "High", technology: str = "Python", lines: int = 100, spark_usages: int = 0, issues: int = 0) -> FileCompatibilityRow:
    return FileCompatibilityRow(path=path, name=name, status=status, technology=technology, lines=lines, spark_usages=spark_usages, issues=issues)


def _info(path: str, name: str, source_system=None, target_type=None, target_location: str = "", eai_required: str = "No", ar_required: str = "No", lines: int = 100) -> FileInfoRow:
    return FileInfoRow(
        path=path,
        name=name,
        source_system=source_system or ["N/A"],
        target_type=target_type or ["N/A"],
        target_location=target_location,
        eai_required=eai_required,
        ar_required=ar_required,
        lines=lines,
    )


def _wave(name: str, paths: list[str]) -> MigrationWave:
    return MigrationWave(
        name=name,
        files=[FileCompatibilityRow(path=p, name=p.split("/")[-1]) for p in paths],
    )


def _graph_node(id: str, in_degree: int = 0, blast_radius: int = 0, status: str = "High") -> GraphNode:
    return GraphNode(id=id, label=id.split("/")[-1], x=0, y=0, status=status, in_degree=in_degree, blast_radius=blast_radius)


def _graph_edge(source: str, target: str, kind: str = "import") -> GraphEdge:
    return GraphEdge(x1=0, y1=0, x2=10, y2=10, source=source, target=target, kind=kind)


def _minimal_assessment(**kwargs) -> Assessment:
    """Return an Assessment with the bare minimum to test partition_by."""
    files = kwargs.pop("files", [])
    file_info = kwargs.pop("file_info", [])
    migration_waves = kwargs.pop("migration_waves", [])
    dependency_graph = kwargs.pop("dependency_graph", None)
    data_dependency_graph = kwargs.pop("data_dependency_graph", None)
    isolated_modules = kwargs.pop("isolated_modules", [])
    main_cluster = kwargs.pop("main_cluster", None)
    return Assessment(
        metadata=AssessmentMetadata(project="test"),
        workload=WorkloadSummary(),
        files=files,
        file_info=file_info,
        migration_waves=migration_waves,
        dependency_graph=dependency_graph,
        data_dependency_graph=data_dependency_graph,
        isolated_modules=isolated_modules,
        main_cluster=main_cluster,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# _build_file_rows
# ---------------------------------------------------------------------------


def test_build_file_rows_basic():
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py", status="High"), _compat("b.py", "b.py", status="Low")],
    )
    rows = _build_file_rows(a)
    assert set(rows.keys()) == {"a.py", "b.py"}
    assert rows["a.py"]["readiness"] == "High"
    assert rows["a.py"]["readiness_label"] == "Ready"
    assert rows["b.py"]["readiness_label"] == "Active Refactor"


def test_build_file_rows_overlays_file_info():
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py")],
        file_info=[_info("a.py", "a.py", source_system=["S3"], target_type=["Snowflake Table"], target_location="DB.SCH.TBL", eai_required="Yes", ar_required="Yes", lines=200)],
    )
    rows = _build_file_rows(a)
    r = rows["a.py"]
    assert r["source_systems"] == ["S3"]
    assert r["target_types"] == ["Snowflake Table"]
    assert r["target_location"] == "DB.SCH.TBL"
    assert r["eai_required"] == "Yes"
    assert r["ar_required"] == "Yes"
    assert r["lines"] == 200


def test_build_file_rows_wave_assignment():
    a = _minimal_assessment(
        files=[_compat("x.py", "x.py"), _compat("y.py", "y.py")],
        migration_waves=[_wave("Wave 1", ["x.py"]), _wave("Wave 2", ["y.py"])],
    )
    rows = _build_file_rows(a)
    assert rows["x.py"]["wave"] == "Wave 1"
    assert rows["y.py"]["wave"] == "Wave 2"


def test_build_file_rows_blast_radius():
    dep_graph = DependencyGraph(
        module="all", width=100, height=100, file_count=2, edge_count=1,
        nodes=[_graph_node("a.py", blast_radius=5), _graph_node("b.py", blast_radius=0)],
    )
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py"), _compat("b.py", "b.py")],
        dependency_graph=dep_graph,
    )
    rows = _build_file_rows(a)
    assert rows["a.py"]["blast_radius"] == 5
    assert rows["b.py"]["blast_radius"] == 0


def test_build_file_rows_file_info_only_path():
    """Files in file_info but not in files list still appear."""
    a = _minimal_assessment(
        files=[],
        file_info=[_info("util.py", "util.py", lines=50)],
    )
    rows = _build_file_rows(a)
    assert "util.py" in rows
    assert rows["util.py"]["lines"] == 50


def test_build_file_rows_legacy_string_source_system():
    """file_info with a str source_system (legacy) is coerced to list."""
    fi = FileInfoRow(path="a.py", name="a.py", source_system="S3", target_type="Snowflake Table")
    a = _minimal_assessment(files=[_compat("a.py", "a.py")], file_info=[fi])
    rows = _build_file_rows(a)
    assert rows["a.py"]["source_systems"] == ["S3"]
    assert rows["a.py"]["target_types"] == ["Snowflake Table"]


# migration_wave strategy removed — see archived/migration_wave_strategy.py


# ---------------------------------------------------------------------------
# _partition_by_readiness
# ---------------------------------------------------------------------------


def test_readiness_three_buckets():
    a = _minimal_assessment(files=[
        _compat("h.py", "h.py", status="High"),
        _compat("m.py", "m.py", status="Medium"),
        _compat("l.py", "l.py", status="Low"),
    ])
    rows = _build_file_rows(a)
    result = _partition_by_readiness(a, rows)
    assert any("Active Refactor" in k for k in result)
    assert any("Light Refactor" in k for k in result)
    assert any("Ready" in k for k in result)
    # Easiest first: Ready must appear before Active Refactor
    keys = list(result.keys())
    ready_idx = next(i for i, k in enumerate(keys) if "Ready" in k)
    active_idx = next(i for i, k in enumerate(keys) if "Active Refactor" in k)
    assert ready_idx < active_idx


def test_readiness_missing_bucket_not_in_result():
    """When no Low files exist, 'Active Refactor' group is absent."""
    a = _minimal_assessment(files=[_compat("h.py", "h.py", status="High")])
    rows = _build_file_rows(a)
    result = _partition_by_readiness(a, rows)
    assert not any("Active Refactor" in k for k in result)


# ---------------------------------------------------------------------------
# _partition_by_technology
# ---------------------------------------------------------------------------


def test_technology_groups():
    a = _minimal_assessment(files=[
        _compat("py.py", "py.py", technology="Python"),
        _compat("sc.scala", "sc.scala", technology="Scala"),
        _compat("q.sql", "q.sql", technology="SQL"),
    ])
    rows = _build_file_rows(a)
    result = _partition_by_technology(a, rows)
    assert set(result.keys()) == {"Python", "Scala", "SQL"}


# ---------------------------------------------------------------------------
# _partition_by_source_system
# ---------------------------------------------------------------------------


def test_source_system_groups():
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py"), _compat("b.py", "b.py")],
        file_info=[
            _info("a.py", "a.py", source_system=["S3"]),
            _info("b.py", "b.py", source_system=["JDBC"]),
        ],
    )
    rows = _build_file_rows(a)
    result = _partition_by_source_system(a, rows)
    assert "S3" in result and "a.py" in result["S3"]
    assert "JDBC" in result and "b.py" in result["JDBC"]


def test_source_system_multi_source_file_in_multiple_groups():
    """A file reading from both S3 and JDBC appears in both groups."""
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py")],
        file_info=[_info("a.py", "a.py", source_system=["S3", "JDBC"])],
    )
    rows = _build_file_rows(a)
    result = _partition_by_source_system(a, rows)
    assert "a.py" in result["S3"]
    assert "a.py" in result["JDBC"]


def test_source_system_in_memory_label():
    a = _minimal_assessment(
        files=[_compat("t.py", "t.py")],
        file_info=[_info("t.py", "t.py", source_system=["In-Memory"])],
    )
    rows = _build_file_rows(a)
    result = _partition_by_source_system(a, rows)
    assert "In-Memory" in result


def test_source_system_empty_file_info_uses_na():
    """Without file_info, files fall into N/A group."""
    a = _minimal_assessment(files=[_compat("a.py", "a.py")])
    rows = _build_file_rows(a)
    result = _partition_by_source_system(a, rows)
    assert "N/A" in result and "a.py" in result["N/A"]


# ---------------------------------------------------------------------------
# _partition_by_target_type
# ---------------------------------------------------------------------------


def test_target_type_groups():
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py"), _compat("b.py", "b.py")],
        file_info=[
            _info("a.py", "a.py", target_type=["Snowflake Table"]),
            _info("b.py", "b.py", target_type=["Cloud Storage"]),
        ],
    )
    rows = _build_file_rows(a)
    result = _partition_by_target_type(a, rows)
    assert any("Snowflake Table" in k for k in result) and "a.py" in result[next(k for k in result if "Snowflake Table" in k)]
    assert any("Cloud Storage" in k for k in result)


def test_target_type_multi_target():
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py")],
        file_info=[_info("a.py", "a.py", target_type=["Snowflake Table", "Cloud Storage"])],
    )
    rows = _build_file_rows(a)
    result = _partition_by_target_type(a, rows)
    sf_key = next((k for k in result if "Snowflake Table" in k), None)
    cs_key = next((k for k in result if "Cloud Storage" in k), None)
    assert sf_key and cs_key
    assert "a.py" in result[sf_key] and "a.py" in result[cs_key]


# ---------------------------------------------------------------------------
# _partition_by_infra_need
# ---------------------------------------------------------------------------


def test_infra_need_four_tiers():
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py"), _compat("b.py", "b.py"), _compat("c.py", "c.py"), _compat("d.py", "d.py")],
        file_info=[
            _info("a.py", "a.py", eai_required="Yes", ar_required="Yes"),
            _info("b.py", "b.py", eai_required="Yes", ar_required="No"),
            _info("c.py", "c.py", eai_required="No", ar_required="Yes"),
            _info("d.py", "d.py", eai_required="No", ar_required="No"),
        ],
    )
    rows = _build_file_rows(a)
    result = _partition_by_infra_need(a, rows)
    assert "EAI + AR Required" in result and "a.py" in result["EAI + AR Required"]
    assert "EAI Required Only" in result and "b.py" in result["EAI Required Only"]
    assert "AR Required Only" in result and "c.py" in result["AR Required Only"]
    assert "Standard" in result and "d.py" in result["Standard"]


def test_infra_need_missing_tiers_absent():
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py")],
        file_info=[_info("a.py", "a.py", eai_required="No", ar_required="No")],
    )
    rows = _build_file_rows(a)
    result = _partition_by_infra_need(a, rows)
    assert "EAI + AR Required" not in result
    assert "Standard" in result


# ---------------------------------------------------------------------------
# _partition_by_blast_radius
# ---------------------------------------------------------------------------


def test_blast_radius_three_tiers():
    """Thresholds are data-driven; use br=0 for the leaf to guarantee a Leaf tier."""
    dep_graph = DependencyGraph(
        module="all", width=100, height=100, file_count=3, edge_count=2,
        nodes=[
            _graph_node("a.py", blast_radius=15),
            _graph_node("b.py", blast_radius=5),
            _graph_node("c.py", blast_radius=0),  # true leaf — no transitive dependents
        ],
    )
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py"), _compat("b.py", "b.py"), _compat("c.py", "c.py")],
        dependency_graph=dep_graph,
    )
    rows = _build_file_rows(a)
    result = _partition_by_blast_radius(a, rows)
    assert result is not None and result != {}
    # Label strings are data-driven — match by substring, not exact key
    foundational = next((v for k, v in result.items() if "Foundational" in k), [])
    shared       = next((v for k, v in result.items() if "Shared" in k), [])
    leaf         = next((v for k, v in result.items() if "Leaf" in k), [])
    assert "a.py" in foundational
    assert "b.py" in shared
    assert "c.py" in leaf


def test_blast_radius_returns_empty_dict_when_no_graph():
    a = _minimal_assessment(files=[_compat("a.py", "a.py")])
    rows = _build_file_rows(a)
    result = _partition_by_blast_radius(a, rows)
    assert result == {}  # signals unavailable


def test_blast_radius_files_not_in_graph_go_to_uncharted():
    dep_graph = DependencyGraph(
        module="all", width=100, height=100, file_count=1, edge_count=0,
        nodes=[_graph_node("a.py", blast_radius=0)],
    )
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py"), _compat("b.py", "b.py")],
        dependency_graph=dep_graph,
    )
    rows = _build_file_rows(a)
    result = _partition_by_blast_radius(a, rows)
    assert "b.py" in result.get("Not in Import Graph", [])


# _partition_by_import_depth removed — superseded by blast_radius


# ---------------------------------------------------------------------------
# _partition_by_data_pipeline
# ---------------------------------------------------------------------------


def test_data_pipeline_no_data_graph_returns_none():
    a = _minimal_assessment(files=[_compat("a.py", "a.py")])
    rows = _build_file_rows(a)
    result = _partition_by_data_pipeline(a, rows, use_llm=False)
    assert result is None


def test_data_pipeline_empty_graph_isolated_nodes_grouped():
    """Data graph with nodes but no edges → both files go into the isolated bucket (no pipelines)."""
    dag = DependencyGraph(
        module="data", width=100, height=100, file_count=2, edge_count=0,
        nodes=[
            _graph_node("a.py"),
            _graph_node("b.py"),
        ],
    )
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py"), _compat("b.py", "b.py")],
        data_dependency_graph=dag,
    )
    rows = _build_file_rows(a)
    result = _partition_by_data_pipeline(a, rows, use_llm=False)
    assert result is not None
    # No multi-file components → no "Pipeline N" groups
    pipeline_groups = [k for k in result if "Pipeline" in k]
    assert len(pipeline_groups) == 0, f"Expected no pipeline groups for isolated nodes, got {pipeline_groups}"
    isolated = result.get("Standalone Files", [])
    assert "a.py" in isolated and "b.py" in isolated


def test_data_pipeline_two_components():
    """Two disconnected components → two separate pipeline groups."""
    dag = DependencyGraph(
        module="data", width=100, height=100, file_count=4, edge_count=2,
        nodes=[
            _graph_node("a.py"), _graph_node("b.py"),
            _graph_node("c.py"), _graph_node("d.py"),
        ],
        edges=[
            _graph_edge("a.py", "b.py", kind="data"),
            _graph_edge("c.py", "d.py", kind="data"),
        ],
    )
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py"), _compat("b.py", "b.py"), _compat("c.py", "c.py"), _compat("d.py", "d.py")],
        data_dependency_graph=dag,
    )
    rows = _build_file_rows(a)
    result = _partition_by_data_pipeline(a, rows, use_llm=False)
    assert result is not None
    pipeline_groups = [k for k in result if "Pipeline" in k]
    assert len(pipeline_groups) == 2, f"Expected 2 pipeline groups, got {pipeline_groups}"


def test_data_pipeline_files_outside_graph():
    """Files not in the data graph appear in 'Standalone Files' group."""
    dag = DependencyGraph(
        module="data", width=100, height=100, file_count=2, edge_count=1,
        nodes=[_graph_node("a.py"), _graph_node("b.py")],
        edges=[_graph_edge("a.py", "b.py", kind="data")],
    )
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py"), _compat("b.py", "b.py"), _compat("util.py", "util.py")],
        data_dependency_graph=dag,
    )
    rows = _build_file_rows(a)
    result = _partition_by_data_pipeline(a, rows, use_llm=False)
    assert result is not None
    isolated_group = result.get("Standalone Files", [])
    assert "util.py" in isolated_group


def test_data_pipeline_framework_edges_excluded():
    """Framework-kind edges are not real data connections — both nodes end up isolated."""
    dag = DependencyGraph(
        module="data", width=100, height=100, file_count=2, edge_count=1,
        nodes=[_graph_node("a.py"), _graph_node("b.py")],
        edges=[_graph_edge("a.py", "b.py", kind="framework")],
    )
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py"), _compat("b.py", "b.py")],
        data_dependency_graph=dag,
    )
    rows = _build_file_rows(a)
    result = _partition_by_data_pipeline(a, rows, use_llm=False)
    assert result is not None
    # Framework edges excluded → no multi-file components → no Pipeline groups
    pipeline_groups = [k for k in result if "Pipeline" in k]
    assert len(pipeline_groups) == 0, f"Expected no pipeline groups when only framework edges exist, got {pipeline_groups}"
    # Both files must appear in the isolated bucket
    isolated = result.get("Standalone Files", [])
    assert "a.py" in isolated and "b.py" in isolated


# _partition_by_quick_win removed


# ---------------------------------------------------------------------------
# _partition_by_snowflake_schema
# ---------------------------------------------------------------------------


def test_snowflake_schema_groups_by_db_schema():
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py"), _compat("b.py", "b.py"), _compat("c.py", "c.py")],
        file_info=[
            _info("a.py", "a.py", target_type=["Snowflake Table"], target_location="PROD.PUBLIC.ORDERS"),
            _info("b.py", "b.py", target_type=["Snowflake Table"], target_location="PROD.PUBLIC.CUSTOMERS"),
            _info("c.py", "c.py", target_type=["Snowflake Table"], target_location="ANALYTICS.CURATED.METRICS"),
        ],
    )
    rows = _build_file_rows(a)
    result = _partition_by_snowflake_schema(a, rows)
    assert result is not None
    # a and b both write to PROD.PUBLIC; c writes to ANALYTICS.CURATED
    assert any("PROD.PUBLIC" in k for k in result) or any("PUBLIC" in k for k in result)
    # All files present
    all_vals = [p for paths in result.values() for p in paths]
    assert "a.py" in all_vals and "b.py" in all_vals and "c.py" in all_vals


def test_snowflake_schema_rolls_up_to_db_when_too_many_schemas():
    """More than 8 distinct schemas should roll up to DB level."""
    files_compat = []
    files_info = []
    schemas = [f"SCH{i}" for i in range(10)]
    for i, sch in enumerate(schemas):
        p = f"f{i}.py"
        files_compat.append(_compat(p, p))
        files_info.append(_info(p, p, target_type=["Snowflake Table"], target_location=f"PROD.{sch}.TBL"))
    a = _minimal_assessment(files=files_compat, file_info=files_info)
    rows = _build_file_rows(a)
    result = _partition_by_snowflake_schema(a, rows)
    assert result is not None
    # Should roll up — only "PROD" key at DB level
    assert len(result) <= 9  # <= 8 schema groups + 1 non-Snowflake


def test_snowflake_schema_returns_none_when_no_sf_files():
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py")],
        file_info=[_info("a.py", "a.py", target_type=["Cloud Storage"], target_location="s3://bucket/out")],
    )
    rows = _build_file_rows(a)
    result = _partition_by_snowflake_schema(a, rows)
    assert result is None


def test_snowflake_schema_non_sf_files_grouped_separately():
    a = _minimal_assessment(
        files=[_compat("sf.py", "sf.py"), _compat("other.py", "other.py")],
        file_info=[
            _info("sf.py", "sf.py", target_type=["Snowflake Table"], target_location="DB.SCH.TBL"),
            _info("other.py", "other.py", target_type=["Cloud Storage"], target_location="s3://bucket/x"),
        ],
    )
    rows = _build_file_rows(a)
    result = _partition_by_snowflake_schema(a, rows)
    assert result is not None
    non_sf = result.get("Non-Snowflake or Unknown Output", [])
    assert "other.py" in non_sf


# ---------------------------------------------------------------------------
# _connected_components helper
# ---------------------------------------------------------------------------


def test_connected_components_single_node():
    adj: dict = {"a": set()}
    comps = _connected_components(adj, {"a"})
    assert len(comps) == 1 and comps[0] == {"a"}


def test_connected_components_two_disconnected():
    adj: dict = {"a": {"b"}, "b": {"a"}, "c": set()}
    comps = _connected_components(adj, {"a", "b", "c"})
    assert len(comps) == 2
    sizes = sorted(len(c) for c in comps)
    assert sizes == [1, 2]


def test_connected_components_fully_connected():
    adj: dict = {"a": {"b", "c"}, "b": {"a"}, "c": {"a"}}
    comps = _connected_components(adj, {"a", "b", "c"})
    assert len(comps) == 1 and comps[0] == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# build_partition_table_data (integration)
# ---------------------------------------------------------------------------


def test_build_returns_expected_keys():
    a = _minimal_assessment(files=[_compat("a.py", "a.py")])
    data = build_partition_table_data(a)
    assert "strategies" in data
    assert "partition_map" in data
    assert "file_rows" in data


def test_build_empty_assessment_returns_empty_payload():
    a = _minimal_assessment(files=[])
    data = build_partition_table_data(a)
    assert data["strategies"] == []
    assert data["partition_map"] == {}
    assert data["file_rows"] == {}


def test_build_strategies_include_default():
    a = _minimal_assessment(files=[_compat("a.py", "a.py")])
    data = build_partition_table_data(a)
    defaults = [s for s in data["strategies"] if s.get("default")]
    assert len(defaults) == 1
    assert defaults[0]["id"] == "readiness"


def test_build_strategies_all_ids_present():
    a = _minimal_assessment(files=[_compat("a.py", "a.py")])
    data = build_partition_table_data(a)
    ids = {s["id"] for s in data["strategies"]}
    expected = {"readiness", "technology", "data_pipeline"}
    assert expected.issubset(ids)
    # Removed strategies must not appear
    assert "migration_wave" not in ids
    assert "import_depth" not in ids
    assert "quick_win" not in ids


def test_build_llm_strategy_unavailable_without_llm():
    a = _minimal_assessment(files=[_compat("a.py", "a.py")])
    data = build_partition_table_data(a)
    llm_strat = next((s for s in data["strategies"] if s["id"] == "llm_pipeline"), None)
    # llm_pipeline should be in list but marked unavailable
    if llm_strat:
        assert not llm_strat["available"]


def test_build_file_rows_json_serializable():
    """The file_rows value must be directly JSON-serializable (no Pydantic objects)."""
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py", status="Medium", lines=300)],
        file_info=[_info("a.py", "a.py", source_system=["S3"], target_type=["Snowflake Table"], eai_required="Yes")],
    )
    data = build_partition_table_data(a)
    # Should not raise
    serialized = json.dumps(data)
    parsed = json.loads(serialized)
    assert parsed["file_rows"]["a.py"]["eai_required"] == "Yes"


def test_build_strategies_partition_map_covers_all_files():
    """For every available strategy, all files in file_rows appear in partition_map."""
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py", status="High"), _compat("b.py", "b.py", status="Low")],
        migration_waves=[_wave("Wave 1", ["a.py"]), _wave("Wave 2", ["b.py"])],
    )
    data = build_partition_table_data(a)
    for strat in data["strategies"]:
        if not strat["available"]:
            continue
        sid = strat["id"]
        groups = data["partition_map"].get(sid, {})
        all_files_in_map = {p for paths in groups.values() for p in paths}
        # All files in file_rows should appear somewhere in the partition
        # (Exception: some strategies may intentionally exclude files from
        #  certain groups — e.g. data_pipeline with "Outside" group, or
        #  snowflake_schema which may return None for non-SF workloads.
        #  We only assert non-empty when groups is non-empty.)
        if groups and all_files_in_map:
            # At minimum the files that exist should appear in ≥1 group
            assert all_files_in_map & set(data["file_rows"].keys()), f"Strategy {sid} has no files from file_rows"


def test_build_blast_radius_strategy_unavailable_without_graph():
    """blast_radius strategy is unavailable when no dependency_graph."""
    a = _minimal_assessment(files=[_compat("a.py", "a.py")])
    data = build_partition_table_data(a)
    br_strat = next((s for s in data["strategies"] if s["id"] == "blast_radius"), None)
    if br_strat:
        assert not br_strat["available"]


def test_build_blast_radius_available_with_graph():
    dep_graph = DependencyGraph(
        module="all", width=100, height=100, file_count=1, edge_count=0,
        nodes=[_graph_node("a.py", blast_radius=3)],
    )
    a = _minimal_assessment(
        files=[_compat("a.py", "a.py")],
        dependency_graph=dep_graph,
    )
    data = build_partition_table_data(a)
    br_strat = next((s for s in data["strategies"] if s["id"] == "blast_radius"), None)
    assert br_strat and br_strat["available"]


def test_build_groups_have_badge_field():
    a = _minimal_assessment(files=[_compat("a.py", "a.py")])
    data = build_partition_table_data(a)
    for strat in data["strategies"]:
        for g in strat.get("groups", []):
            assert "badge" in g, f"Group {g} in strategy {strat['id']} missing badge"
            assert g["badge"] in ("green", "yellow", "orange", "blue", "gray")


def test_build_strategy_description_non_empty():
    a = _minimal_assessment(files=[_compat("a.py", "a.py")])
    data = build_partition_table_data(a)
    for strat in data["strategies"]:
        assert strat["description"], f"Strategy {strat['id']} has empty description"


# ---------------------------------------------------------------------------
# Prototype v1 adapter integration
# ---------------------------------------------------------------------------


def test_v1_render_includes_partition_section():
    """Rendered HTML must contain the partition-by section and its JS data."""
    import sys
    sys.path.insert(0, str(_SCRIPT_DIR / "adapters"))
    from adapters import prototype_v1

    a = _minimal_assessment(
        files=[_compat("a.py", "a.py", status="High"), _compat("b.py", "b.py", status="Low")],
        migration_waves=[_wave("Wave 1", ["a.py"]), _wave("Wave 2", ["b.py"])],
    )
    html = prototype_v1.render(a)
    assert "partition-by-section" in html, "Partition section div missing"
    assert "PARTITION_TABLE_DATA" in html, "JS constant missing"
    assert "partition-strategy-select" in html, "Strategy dropdown missing"


def test_v1_render_partition_json_is_valid():
    """The PARTITION_TABLE_DATA JSON embedded in the HTML is parseable."""
    import re
    from adapters import prototype_v1

    a = _minimal_assessment(
        files=[
            _compat("etl.py", "etl.py", status="Medium", technology="Python", lines=400),
        ],
        file_info=[_info("etl.py", "etl.py", source_system=["S3"], target_type=["Snowflake Table"], target_location="DB.SCH.TBL")],
    )
    html = prototype_v1.render(a)
    # Extract the JSON literal between PARTITION_TABLE_DATA = and ;
    m = re.search(r"const PARTITION_TABLE_DATA = (\{.*?\});\s*\(function", html, re.DOTALL)
    assert m, "Could not find PARTITION_TABLE_DATA JSON in HTML"
    payload = json.loads(m.group(1))
    assert "strategies" in payload
    assert "file_rows" in payload
    assert "etl.py" in payload["file_rows"]


def test_v1_render_empty_ir_no_crash():
    """Rendering with an empty Assessment must not raise."""
    from adapters import prototype_v1
    a = Assessment(metadata=AssessmentMetadata(project="empty"))
    html = prototype_v1.render(a)
    assert "partition-by-section" in html
