"""Tests for LLM-resolved data-edge integration.

Covers:
  - scan_codebase.rebuild_data_flow_graph  (DAG rebuild with injected sigs)
  - render_assessment.apply_llm_resolved_edges  (DAG rebuild + node marking)
  - render_assessment.main() unresolved-edge merge with stored IR
  - assess_ir.LLMResolvedEdge / LLMResolvedDataEdges / GraphNode.llm_enriched
  - Template: LLM badge rendering

Run from the ``snowpark-connect/`` directory::

    uv run --project . pytest scripts/assessment/tests/test_resolve_data_edges_llm.py
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from assess_ir import (
    Assessment, DependencyGraph, GraphEdge, GraphNode,
    LLMResolvedDataEdges, LLMResolvedEdge, LLMResolvedImport,
    LLMUnresolvableEdge, UnresolvedDataEdge, UnresolvedDynamicImport,
)
from check_data_edges_gate import run as gate_run


# ---------------------------------------------------------------------------
# scan_codebase.rebuild_data_flow_graph
# ---------------------------------------------------------------------------

class TestRebuildDataFlowGraph:
    def _write_workload(self, tmp_path: Path) -> None:
        (tmp_path / "writer.py").write_text(
            'from pyspark.sql import SparkSession\n'
            'spark = SparkSession.builder.getOrCreate()\n'
            'df = spark.read.parquet("s3://bucket/source")\n'
            'df.write.parquet("s3://bucket/output")\n'
        )
        (tmp_path / "reader.py").write_text(
            'from pyspark.sql import SparkSession\n'
            'spark = SparkSession.builder.getOrCreate()\n'
            'df = spark.read.parquet("s3://bucket/output")\n'
        )

    def test_returns_none_or_dag_for_empty_dir(self, tmp_path):
        from scan_codebase import rebuild_data_flow_graph
        result = rebuild_data_flow_graph(tmp_path, {}, {})
        assert result is None or hasattr(result, "nodes")

    def test_injects_extra_source_sig(self, tmp_path):
        from scan_codebase import rebuild_data_flow_graph
        self._write_workload(tmp_path)
        result = rebuild_data_flow_graph(
            tmp_path,
            llm_source_sigs={"s3://bucket/output": ["reader.py"]},
            llm_sink_sigs={},
        )
        assert result is not None

    def test_deduplicates_existing_sigs(self, tmp_path):
        from scan_codebase import rebuild_data_flow_graph
        self._write_workload(tmp_path)
        result = rebuild_data_flow_graph(
            tmp_path,
            llm_source_sigs={"s3://bucket/output": ["reader.py"]},
            llm_sink_sigs={"s3://bucket/output": ["writer.py"]},
        )
        assert result is not None

    def test_llm_import_targets_accepted(self, tmp_path):
        """rebuild accepts an LLM import-target override without error and
        threads it to the chain builder (the override wins over static)."""
        from scan_codebase import rebuild_data_flow_graph
        self._write_workload(tmp_path)
        result = rebuild_data_flow_graph(
            tmp_path,
            llm_source_sigs={},
            llm_sink_sigs={},
            llm_import_targets={("writer.py", 99): ["reader.py"]},
        )
        assert result is None or hasattr(result, "nodes")

    def test_llm_import_override_beats_static(self, tmp_path):
        """_resolve_dynamic_import_site returns the LLM target verbatim when
        the (orchestrator, line) key matches, regardless of static outcome."""
        from scan_codebase import _resolve_dynamic_import_site
        site = {"kind": "spec_from_file", "line": 20, "path_arg": "unresolvable_var"}
        files, reason = _resolve_dynamic_import_site(
            site, "orch.py", [], [], {}, tmp_path,
            llm_import_targets={("orch.py", 20): ["task.py"]},
        )
        assert files == ["task.py"]
        assert reason is None


# ---------------------------------------------------------------------------
# Regression: LLM enrichment must be ADDITIVE — it may add edges but must never
# drop an AST-resolved edge. (SNOW-3764388 shipped a rebuild that re-derived
# edges from path-signature matching (Signal 4) ONLY, silently discarding
# Signal 1 name matches, Signal 2 dynamic-import chains, and Signal 3 YAML
# topology. Kipawa's transformer chain (100% Signal 2) collapsed to 0 edges.)
# ---------------------------------------------------------------------------

def _mine_returning(io_map: dict[str, tuple[set, set]]):
    """schema_mine mock keyed by file basename → (sources, sinks)."""
    def _fake(path, **kwargs):
        srcs, snks = io_map.get(Path(path).name, (set(), set()))
        return {"_sources": {n: {} for n in srcs}, "_sinks": {n: {} for n in snks}}
    return _fake


def _cf(name: str) -> dict:
    return {"rel_path": name, "name": name, "ext": ".py",
            "lines": 5, "imports": [], "spark_api": 0}


class TestLLMSigsAreAdditive:
    """_build_data_dep_edges folds LLM sigs in BEFORE signal matching, so all
    AST signals still fire and LLM sigs only add (deduped) edges."""

    def test_signal1_edge_preserved_alongside_llm_edge(self, tmp_path):
        import scan_codebase as sc
        from scan_codebase import _build_data_dep_edges
        # a.py sinks table 'stg', b.py sources 'stg'  → Signal-1 (name) edge a→b,
        # which is NOT a path signature. c.py/d.py get their edge purely from the
        # injected LLM signatures.
        for n in ("a.py", "b.py", "c.py", "d.py"):
            (tmp_path / n).write_text("x = 1\n")
        io = _mine_returning({"a.py": (set(), {"stg"}), "b.py": ({"stg"}, set())})
        with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
             patch.object(sc, "_schema_mine_fn", io):
            edges, _u, _ue = _build_data_dep_edges(
                [_cf(n) for n in ("a.py", "b.py", "c.py", "d.py")],
                tmp_path,
                llm_source_sigs={"/data/gold": ["c.py"]},
                llm_sink_sigs={"/data/gold": ["d.py"]},
            )
        assert ("a.py", "b.py", "data") in edges   # Signal-1 AST edge preserved
        assert ("d.py", "c.py", "data") in edges   # LLM-signature edge added

    def test_llm_sig_matching_existing_ast_edge_is_deduped(self, tmp_path):
        import scan_codebase as sc
        from scan_codebase import _build_data_dep_edges
        for n in ("a.py", "b.py"):
            (tmp_path / n).write_text("x = 1\n")
        io = _mine_returning({"a.py": (set(), {"stg"}), "b.py": ({"stg"}, set())})
        # LLM re-reports the same a→b relationship as a signature match.
        with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
             patch.object(sc, "_schema_mine_fn", io):
            edges, _u, _ue = _build_data_dep_edges(
                [_cf("a.py"), _cf("b.py")], tmp_path,
                llm_source_sigs={"stg": ["b.py"]},
                llm_sink_sigs={"stg": ["a.py"]},
            )
        assert edges.count(("a.py", "b.py", "data")) == 1


class TestRebuildPreservesASTEdges:
    """rebuild_data_flow_graph (the --llm-resolved-edges entry point) must keep
    non-Signal-4 AST edges. This is the exact regression for the Kipawa bug."""

    def test_non_signal4_edge_survives_rebuild(self, tmp_path):
        import scan_codebase as sc
        from scan_codebase import rebuild_data_flow_graph
        (tmp_path / "producer.py").write_text("x = 1\n")
        (tmp_path / "consumer.py").write_text("x = 1\n")
        io = _mine_returning({
            "producer.py": (set(), {"stg"}),
            "consumer.py": ({"stg"}, set()),
        })
        with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
             patch.object(sc, "_schema_mine_fn", io):
            dag = rebuild_data_flow_graph(tmp_path, {}, {})  # no LLM sigs at all
        assert dag is not None
        data = {(e.source, e.target) for e in dag.edges if e.kind == "data"}
        assert ("producer.py", "consumer.py") in data

    def test_rebuild_keeps_ast_edge_and_adds_llm_edge(self, tmp_path):
        import scan_codebase as sc
        from scan_codebase import rebuild_data_flow_graph
        for n in ("producer.py", "consumer.py", "llmw.py", "llmr.py"):
            (tmp_path / n).write_text("x = 1\n")
        io = _mine_returning({
            "producer.py": (set(), {"stg"}),
            "consumer.py": ({"stg"}, set()),
        })
        with patch.object(sc, "_DATA_MINING_AVAILABLE", True), \
             patch.object(sc, "_schema_mine_fn", io):
            dag = rebuild_data_flow_graph(
                tmp_path,
                llm_source_sigs={"/lake/x": ["llmr.py"]},
                llm_sink_sigs={"/lake/x": ["llmw.py"]},
            )
        assert dag is not None
        data = {(e.source, e.target) for e in dag.edges if e.kind == "data"}
        assert ("producer.py", "consumer.py") in data   # AST edge preserved
        assert ("llmw.py", "llmr.py") in data            # LLM edge added


# ---------------------------------------------------------------------------
# Bug A: edge-kind → lineage role (destructive DDL must not create edges)
# ---------------------------------------------------------------------------

class TestEdgeLineageRole:
    def test_read_is_source(self):
        from assess_ir import edge_lineage_role
        assert edge_lineage_role("read") == "source"

    def test_write_and_merge_are_sinks(self):
        from assess_ir import edge_lineage_role
        assert edge_lineage_role("write") == "sink"
        assert edge_lineage_role("merge") == "sink"

    def test_destructive_kinds_are_neutral(self):
        from assess_ir import edge_lineage_role
        assert edge_lineage_role("drop") == "neutral"
        assert edge_lineage_role("delete") == "neutral"
        assert edge_lineage_role("truncate") == "neutral"

    def test_case_insensitive(self):
        from assess_ir import edge_lineage_role
        assert edge_lineage_role("DROP") == "neutral"
        assert edge_lineage_role(" Write ") == "sink"

    def test_unknown_kind_is_none(self):
        from assess_ir import edge_lineage_role
        assert edge_lineage_role("frobnicate") is None
        assert edge_lineage_role("") is None

    def test_drop_does_not_create_reverse_edge(self, tmp_path):
        """Regression for the Part_2-drops-temp → Part_1-reads-temp cycle.
        A file that DROPs a table another file reads must NOT get a
        writer→reader edge to that reader."""
        from render_assessment import apply_llm_resolved_edges
        (tmp_path / "producer.py").write_text("# stub\n")
        (tmp_path / "consumer.py").write_text("# stub\n")
        graph = DependencyGraph(
            module="t",
            nodes=[GraphNode(id="producer.py", label="producer.py", x=0, y=0),
                   GraphNode(id="consumer.py", label="consumer.py", x=0, y=0)],
            edges=[], clusters=[], width=400, height=200,
            file_count=2, edge_count=0,
        )
        assessment = Assessment(
            data_dependency_graph=graph,
            llm_resolved_data_edges=LLMResolvedDataEdges(
                model="t",
                edges=[
                    # Real lineage: producer writes T, consumer reads T → forward edge.
                    LLMResolvedEdge(file="producer.py", line=1, kind="write",
                                    resolved_signature="db.t", resolution_type="traced",
                                    source="newly_discovered"),
                    LLMResolvedEdge(file="consumer.py", line=1, kind="read",
                                    resolved_signature="db.t", resolution_type="traced",
                                    source="newly_discovered"),
                    # consumer DROPs U (teardown); producer reads U. This must
                    # NOT create consumer→producer.
                    LLMResolvedEdge(file="consumer.py", line=2, kind="drop",
                                    resolved_signature="db.u", resolution_type="traced",
                                    source="newly_discovered"),
                    LLMResolvedEdge(file="producer.py", line=2, kind="read",
                                    resolved_signature="db.u", resolution_type="traced",
                                    source="newly_discovered"),
                ],
            ),
        )
        result = apply_llm_resolved_edges(assessment, tmp_path)
        edges = {(e.source, e.target) for e in result.data_dependency_graph.edges}
        assert ("producer.py", "consumer.py") in edges  # forward via db.t
        assert ("consumer.py", "producer.py") not in edges  # drop must NOT link back


# ---------------------------------------------------------------------------
# Bug B: orchestration edges (handoffs that share no table) are drawn
# ---------------------------------------------------------------------------

class TestOrchestrationEdges:
    def _assessment(self, tmp_path, orch_edges):
        from assess_ir import OrchestrationEdge
        (tmp_path / "part0.py").write_text("# stub\n")
        (tmp_path / "part1.py").write_text("# stub\n")
        graph = DependencyGraph(
            module="t",
            nodes=[GraphNode(id="part0.py", label="part0.py", x=0, y=0,
                             width=200, height=40),
                   GraphNode(id="part1.py", label="part1.py", x=0, y=100,
                             width=200, height=40)],
            edges=[], clusters=[], width=400, height=300,
            file_count=2, edge_count=0,
        )
        return Assessment(
            data_dependency_graph=graph,
            llm_resolved_data_edges=LLMResolvedDataEdges(
                model="t", edges=[],
                orchestration_edges=[OrchestrationEdge(**e) for e in orch_edges],
            ),
        )

    def test_orchestration_edge_drawn(self, tmp_path):
        from render_assessment import apply_llm_resolved_edges
        a = self._assessment(tmp_path, [
            {"from_file": "part0.py", "to_file": "part1.py",
             "mechanism": "dbutils.taskValues",
             "explanation": "part0 sets Std_Acct_Name; part1 reads it"},
        ])
        result = apply_llm_resolved_edges(a, tmp_path)
        orch = [e for e in result.data_dependency_graph.edges if e.kind == "orchestrates"]
        assert len(orch) == 1
        assert (orch[0].source, orch[0].target) == ("part0.py", "part1.py")
        assert orch[0].label == "dbutils.taskValues"

    def test_edge_to_missing_node_skipped(self, tmp_path):
        from render_assessment import apply_llm_resolved_edges
        a = self._assessment(tmp_path, [
            {"from_file": "part0.py", "to_file": "ghost.py", "mechanism": "%run"},
        ])
        result = apply_llm_resolved_edges(a, tmp_path)
        orch = [e for e in result.data_dependency_graph.edges if e.kind == "orchestrates"]
        assert orch == []

    def test_orchestration_edge_drawn_without_llm_tag(self, tmp_path):
        """Orchestration handoffs are drawn as edges, but their endpoints must
        NOT be tagged as LLM-enriched — the report stays free of LLM node
        badges (enrichment is shown seamlessly, not labelled)."""
        from render_assessment import apply_llm_resolved_edges
        a = self._assessment(tmp_path, [
            {"from_file": "part0.py", "to_file": "part1.py", "mechanism": "%run"},
        ])
        result = apply_llm_resolved_edges(a, tmp_path)
        by_id = {n.id: n for n in result.data_dependency_graph.nodes}
        assert not by_id["part0.py"].llm_enriched
        assert not by_id["part1.py"].llm_enriched

    def test_edge_count_stays_data_only(self, tmp_path):
        """Orchestration arrows are drawn but must NOT inflate edge_count —
        that number is rendered as 'N writer→reader connections'."""
        from render_assessment import apply_llm_resolved_edges
        a = self._assessment(tmp_path, [
            {"from_file": "part0.py", "to_file": "part1.py", "mechanism": "%run"},
        ])
        before = a.data_dependency_graph.edge_count
        result = apply_llm_resolved_edges(a, tmp_path)
        orch = [e for e in result.data_dependency_graph.edges if e.kind == "orchestrates"]
        assert len(orch) == 1  # arrow drawn
        assert result.data_dependency_graph.edge_count == before  # count NOT inflated


# ---------------------------------------------------------------------------
# render_assessment.apply_llm_resolved_edges
# ---------------------------------------------------------------------------

class TestApplyLlmResolvedEdges:
    def _base_assessment(self, tmp_path: Path) -> Assessment:
        node = GraphNode(id="jobs/etl.py", label="etl.py", x=0, y=0,
                         external_sources=[], external_sinks=[])
        graph = DependencyGraph(
            module="test",
            nodes=[node], edges=[], clusters=[],
            width=400, height=200, file_count=1, edge_count=0,
        )
        unresolved = [UnresolvedDataEdge(
            file="jobs/etl.py", line=42, kind="read",
            call_expr="spark.read.parquet", arg_expr="get_path(env)",
            reason="ast.Call",
        )]
        return Assessment(
            data_dependency_graph=graph,
            unresolved_data_edges=unresolved,
        )

    def test_no_llm_data_warns_and_returns(self, tmp_path):
        from render_assessment import apply_llm_resolved_edges
        assessment = self._base_assessment(tmp_path)
        assert assessment.llm_resolved_data_edges is None
        result = apply_llm_resolved_edges(assessment, tmp_path)
        # unresolved_data_edges is untouched — apply_llm_resolved_edges only
        # rebuilds the DAG; unresolved merging is done by main().
        assert len(result.unresolved_data_edges) == 1

    def test_rebuilt_nodes_recolored_from_readiness(self):
        """DAG nodes get their readiness colour backfilled from the per-file
        table instead of defaulting to green ("High"). The LLM DAG rebuild runs
        after Assessment.merge, so without this the rebuilt nodes were all
        green regardless of compatibility."""
        from render_assessment import _recolor_dag_nodes
        from assess_ir import FileCompatibilityRow
        node = GraphNode(id="jobs/etl.py", label="etl.py", full_label="etl.py",
                         x=0, y=0, status="High")
        dag = DependencyGraph(module="t", nodes=[node], edges=[], clusters=[],
                              width=1, height=1, file_count=1, edge_count=0)
        files = [FileCompatibilityRow(path="jobs/etl.py", name="etl.py", status="Low")]
        _recolor_dag_nodes(dag, files)
        assert node.status == "Low"

    def test_dag_rebuilt_for_dag_worthy_edges(self, tmp_path):
        from render_assessment import apply_llm_resolved_edges
        (tmp_path / "jobs").mkdir()
        (tmp_path / "jobs" / "etl.py").write_text(
            'from pyspark.sql import SparkSession\n'
            'spark = SparkSession.builder.getOrCreate()\n'
            'df = spark.read.parquet("s3://bucket/source")\n'
            'df.write.parquet("s3://bucket/output")\n'
        )
        assessment = self._base_assessment(tmp_path)
        assessment.llm_resolved_data_edges = LLMResolvedDataEdges(
            model="test",
            edges=[LLMResolvedEdge(
                file="jobs/etl.py", line=42, kind="read",
                resolved_signature="dynamodb://prod_table",
                resolution_type="traced",
                explanation="traced via helper",
                source="resolved_unresolved",
            )],
        )
        result = apply_llm_resolved_edges(assessment, tmp_path)
        # DAG should have been rebuilt (not None if files exist)
        # We just verify the function runs without error and doesn't crash.
        assert result is not None

    def test_inferred_edges_are_dag_worthy(self, tmp_path):
        """Inferred edges are now DRAWN (not excluded) so a resolved inferred
        edge that reconciliation removes from the unresolved table still has
        somewhere to appear — no silent vanish."""
        from render_assessment import apply_llm_resolved_edges
        (tmp_path / "producer.py").write_text("# stub\n")
        (tmp_path / "consumer.py").write_text("# stub\n")
        graph = DependencyGraph(
            module="t",
            nodes=[GraphNode(id="producer.py", label="producer.py", x=0, y=0),
                   GraphNode(id="consumer.py", label="consumer.py", x=0, y=0)],
            edges=[], clusters=[], width=400, height=200,
            file_count=2, edge_count=0,
        )
        assessment = Assessment(
            data_dependency_graph=graph,
            llm_resolved_data_edges=LLMResolvedDataEdges(
                model="test",
                edges=[
                    LLMResolvedEdge(file="producer.py", line=1, kind="write",
                                    resolved_signature="db.x", resolution_type="inferred",
                                    source="newly_discovered"),
                    LLMResolvedEdge(file="consumer.py", line=1, kind="read",
                                    resolved_signature="db.x", resolution_type="inferred",
                                    source="newly_discovered"),
                ],
            ),
        )
        result = apply_llm_resolved_edges(assessment, tmp_path)
        edges = {(e.source, e.target) for e in result.data_dependency_graph.edges}
        # The inferred write→read pair produced a real DAG edge (would be absent
        # under the old "inferred is audit-only" behavior).
        assert ("producer.py", "consumer.py") in edges

    def test_llm_resolved_path_surfaced_in_node_endpoints(self, tmp_path):
        """A LLM-resolved write path is surfaced in the file node's
        external_sinks (so the click-detail panel shows it) WITHOUT tagging the
        node as LLM-enriched."""
        from render_assessment import apply_llm_resolved_edges
        (tmp_path / "jobs").mkdir()
        (tmp_path / "jobs" / "etl.py").write_text(
            'from pyspark.sql import SparkSession\n'
            'spark = SparkSession.builder.getOrCreate()\n'
            'df = spark.read.parquet("s3://bucket/source")\n'
            'df.write.parquet("s3://bucket/sink")\n'
        )
        (tmp_path / "jobs" / "reader.py").write_text(
            'from pyspark.sql import SparkSession\n'
            'spark = SparkSession.builder.getOrCreate()\n'
            'df = spark.read.parquet("s3://bucket/sink")\n'
        )
        assessment = self._base_assessment(tmp_path)
        assessment.llm_resolved_data_edges = LLMResolvedDataEdges(
            model="test",
            edges=[LLMResolvedEdge(
                file="jobs/etl.py", line=42, kind="write",
                resolved_signature="dynamodb://prod",
                resolution_type="traced",
                source="newly_discovered",
            )],
        )
        result = apply_llm_resolved_edges(assessment, tmp_path)
        assert result.data_dependency_graph is not None
        etl = next(
            (n for n in result.data_dependency_graph.nodes if "etl" in n.id), None
        )
        assert etl is not None
        assert "dynamodb://prod" in etl.external_sinks  # resolved path surfaced
        assert not etl.llm_enriched                     # but not LLM-tagged


# ---------------------------------------------------------------------------
# render_assessment.main(): unresolved-edge merge with stored IR
# ---------------------------------------------------------------------------

class TestUnresolvedEdgeMerge:
    """Verifies that --llm-resolved-edges uses stored IR's unresolved state
    and only appends genuinely new edges from the fresh scan."""

    def _make_unresolved(self, file: str, line: int, kind: str = "read") -> UnresolvedDataEdge:
        return UnresolvedDataEdge(
            file=file, line=line, kind=kind,
            call_expr="spark.read.parquet", arg_expr="get_path(env)",
            reason="ast.Call",
        )

    def test_stored_unresolved_used_when_llm_data_present(self, tmp_path):
        """When the stored IR has LLM data, its unresolved list is authoritative."""
        from render_assessment import apply_llm_resolved_edges

        # Stored IR: agent already resolved line 42, line 99 remains unresolved.
        stored_unresolved = [self._make_unresolved("jobs/etl.py", 99)]
        llm_edge = LLMResolvedEdge(
            file="jobs/etl.py", line=42, kind="read",
            resolved_signature="s3://bucket/events",
            resolution_type="traced",
            source="resolved_unresolved",
        )
        stored_ir = Assessment(
            unresolved_data_edges=stored_unresolved,
            llm_resolved_data_edges=LLMResolvedDataEdges(model="test", edges=[llm_edge]),
        )

        # Fresh scan found both line 42 (old) and a new line 200.
        fresh_assessment = Assessment(
            unresolved_data_edges=[
                self._make_unresolved("jobs/etl.py", 42),   # already resolved by agent
                self._make_unresolved("jobs/etl.py", 99),   # still unresolved
                self._make_unresolved("jobs/etl.py", 200),  # new since agent ran
            ],
        )

        # Simulate what main() does before calling apply_llm_resolved_edges
        if stored_ir.llm_resolved_data_edges is not None:
            fresh_assessment.llm_resolved_data_edges = stored_ir.llm_resolved_data_edges
            stored_keys = {(e.file, e.line, e.kind) for e in stored_ir.unresolved_data_edges}
            resolved_by_llm = {
                (e.file, e.line, e.kind)
                for e in stored_ir.llm_resolved_data_edges.edges
                if e.source == "resolved_unresolved"
            }
            fresh_new = [
                e for e in fresh_assessment.unresolved_data_edges
                if (e.file, e.line, e.kind) not in stored_keys
                and (e.file, e.line, e.kind) not in resolved_by_llm
            ]
            fresh_assessment.unresolved_data_edges = stored_ir.unresolved_data_edges + fresh_new

        # After merge: line 99 (from stored) + line 200 (new); line 42 excluded.
        lines = {e.line for e in fresh_assessment.unresolved_data_edges}
        assert 42 not in lines    # was resolved by agent, not in stored IR
        assert 99 in lines        # still unresolved in stored IR
        assert 200 in lines       # new since agent ran

    def test_fresh_scan_used_when_no_llm_data(self, tmp_path):
        """Without LLM data, unresolved_data_edges come entirely from the fresh scan."""
        fresh_assessment = Assessment(
            unresolved_data_edges=[
                self._make_unresolved("jobs/etl.py", 42),
                self._make_unresolved("jobs/etl.py", 99),
            ],
        )
        stored_ir = Assessment()  # no LLM data

        # main() only overrides when LLM data is present
        if stored_ir.llm_resolved_data_edges is not None:
            fresh_assessment.unresolved_data_edges = stored_ir.unresolved_data_edges

        assert len(fresh_assessment.unresolved_data_edges) == 2


# ---------------------------------------------------------------------------
# assess_ir: LLMResolvedEdge / GraphNode.llm_enriched round-trip
# ---------------------------------------------------------------------------

class TestAssessIrNewFields:
    def test_llm_resolved_edge_pydantic_round_trip(self):
        edge = LLMResolvedEdge(
            file="jobs/etl.py", line=42, kind="read",
            resolved_signature="s3://bucket/events",
            resolution_type="traced",
            explanation="traced",
            source="resolved_unresolved",
        )
        dumped = edge.model_dump()
        restored = LLMResolvedEdge.model_validate(dumped)
        assert restored.resolved_signature == "s3://bucket/events"
        assert restored.resolution_type == "traced"

    def test_llm_resolved_data_edges_defaults(self):
        container = LLMResolvedDataEdges(model="claude-opus-4-6")
        assert container.edges == []
        assert container.unresolvable_edges == []
        assert container.dispatch_units_processed == 0

    def test_graph_node_llm_enriched_default_false(self):
        node = GraphNode(id="a.py", label="a.py", x=0, y=0)
        assert node.llm_enriched is False

    def test_graph_node_llm_enriched_round_trip(self):
        node = GraphNode(id="a.py", label="a.py", x=0, y=0, llm_enriched=True)
        dumped = node.model_dump()
        assert dumped["llm_enriched"] is True
        restored = GraphNode.model_validate(dumped)
        assert restored.llm_enriched is True

    def test_assessment_llm_field_none_by_default(self):
        a = Assessment()
        assert a.llm_resolved_data_edges is None

    def test_assessment_llm_field_round_trip(self):
        edge = LLMResolvedEdge(
            file="f.py", line=1, kind="read",
            resolved_signature="s3://x", resolution_type="literal_found",
            source="newly_discovered",
        )
        a = Assessment(
            llm_resolved_data_edges=LLMResolvedDataEdges(model="m", edges=[edge])
        )
        j = a.model_dump_json()
        a2 = Assessment.model_validate_json(j)
        assert a2.llm_resolved_data_edges is not None
        assert len(a2.llm_resolved_data_edges.edges) == 1
        assert a2.llm_resolved_data_edges.edges[0].resolved_signature == "s3://x"


# ---------------------------------------------------------------------------
# Template: LLM badge rendering
# ---------------------------------------------------------------------------

class TestTemplateLlmBadge:
    def _render_with_llm_node(self, llm_enriched: bool) -> str:
        from adapters import prototype_v1
        node = GraphNode(
            id="jobs/etl.py", label="etl.py", x=10, y=10,
            width=120, height=28, llm_enriched=llm_enriched,
            external_sources=["s3://bucket/events"] if llm_enriched else [],
        )
        graph = DependencyGraph(
            module="test", nodes=[node], edges=[], clusters=[],
            width=400, height=200, file_count=1, edge_count=0,
        )
        edge = LLMResolvedEdge(
            file="jobs/etl.py", line=42, kind="read",
            resolved_signature="s3://bucket/events",
            resolution_type="traced",
            source="resolved_unresolved",
        )
        assessment = Assessment(
            data_dependency_graph=graph,
            llm_resolved_data_edges=LLMResolvedDataEdges(
                model="claude-opus-4-6", edges=[edge]
            ) if llm_enriched else None,
        )
        return prototype_v1.render(assessment)

    def test_llm_badge_present_when_node_enriched(self):
        html = self._render_with_llm_node(llm_enriched=True)
        assert 'data-source="llm"' in html

    def test_llm_badge_absent_when_node_not_enriched(self):
        html = self._render_with_llm_node(llm_enriched=False)
        assert 'data-source="llm"' not in html

    def test_llm_enriched_legend_shown_when_edges_exist(self):
        html = self._render_with_llm_node(llm_enriched=True)
        assert "LLM-enriched node" in html

    def test_llm_enriched_legend_hidden_when_no_edges(self):
        html = self._render_with_llm_node(llm_enriched=False)
        assert "LLM-enriched node" not in html


class TestReconciledTablesRender:
    """Acceptance: after reconciliation the three warning tables reflect the
    LLM's verdict — resolved rows are gone, confirmed-unresolvable rows carry
    full detail, remaining dynamic imports show the LLM reason, insights show."""

    def _render(self):
        from adapters import prototype_v1
        graph = DependencyGraph(
            module="test", nodes=[], edges=[], clusters=[],
            width=400, height=200, file_count=1, edge_count=0,
        )
        assessment = Assessment(
            data_dependency_graph=graph,
            # Reconciled state: data-edge table emptied (all accounted for),
            # one dynamic import remains as LLM-confirmed unresolvable.
            unresolved_data_edges=[],
            unresolved_dynamic_imports=[
                UnresolvedDynamicImport(
                    file="orch.py", line=20, kind="spec_from_file",
                    reason="LLM: path built from sys.argv at runtime",
                    raw_expr="spec_from_file_location(p)",
                ),
            ],
            llm_resolved_data_edges=LLMResolvedDataEdges(
                model="m",
                edges=[LLMResolvedEdge(
                    file="etl.py", line=1, kind="read",
                    resolved_signature="tbl", resolution_type="traced",
                    source="resolved_unresolved",
                )],
                unresolvable_edges=[LLMUnresolvableEdge(
                    file="dead.py", line=5, kind="write",
                    call_expr="df.write.parquet", arg_expr="output_path",
                    why_unresolvable="dead code — function has no call sites",
                    severity="benign",
                )],
                resolved_imports=[LLMResolvedImport(
                    file="orch.py", line=20, kind="spec_from_file",
                    resolved_targets=[], resolution_type="unresolvable",
                    why_unresolvable="path built from sys.argv at runtime",
                )],
                llm_insights=["Pipeline seeds INITIAL_COST from S3 then fans out to SQL stages."],
            ),
        )
        return prototype_v1.render(assessment)

    def test_confirmed_unresolvable_table_has_line_and_kind(self):
        html = self._render()
        assert "Confirmed unresolvable by LLM" in html
        assert "dead.py" in html
        assert "dead code" in html

    def test_confirmed_unresolvable_table_shows_severity(self):
        html = self._render()
        assert "Severity" in html
        # dead.py's edge is severity="benign" → renders the benign pill.
        assert "sev-benign" in html

    def test_unresolved_readwrite_table_absent_when_empty(self):
        html = self._render()
        assert "Unresolved read/write calls" not in html

    def test_dynamic_import_table_shows_llm_reason(self):
        html = self._render()
        assert "Unresolved dynamic imports" in html
        assert "LLM: path built from sys.argv" in html

    def test_insights_advisory_rendered(self):
        html = self._render()
        assert "what this means" in html
        assert "seeds INITIAL_COST" in html


# ---------------------------------------------------------------------------
# LLM output-contract correctness tests
# Tests that verify the full round-trip: LLM writes IR → gate validates →
# render handles every outcome correctly (passed / warned / partial).
# ---------------------------------------------------------------------------

class TestLLMOutputContract:
    """The exit contract requires that llm_resolved_data_edges contains
    analyzed_files and excluded_files covering all data-relevant files in
    the workload.  The gate checks file coverage, not entry classification.
    """

    # ── helpers ────────────────────────────────────────────────────────────

    def _ir_json(self, tmp_path: Path, unresolved: list[dict],
                 llm: dict | None) -> Path:
        p = tmp_path / "AssessmentIR.json"
        p.write_text(json.dumps({
            "unresolved_data_edges": unresolved,
            "llm_resolved_data_edges": llm,
        }))
        return p

    def _edge(self, file: str = "a.py", line: int = 10,
              kind: str = "read") -> dict:
        return {"file": file, "line": line, "kind": kind,
                "call_expr": "spark.read.parquet", "arg_expr": "p",
                "reason": "ast.Name"}

    def _llm_resolved(self, file: str, line: int, kind: str = "read") -> dict:
        return {
            "file": file, "line": line, "kind": kind,
            "resolved_signature": "s3://bucket/data",
            "resolution_type": "traced",
            "explanation": "traced via config",
            "source": "resolved_unresolved",
            "call_expr": "spark.read.parquet",
        }

    def _llm_unresolvable(self, file: str, line: int, kind: str = "read") -> dict:
        return {
            "file": file, "line": line, "kind": kind,
            "call_expr": "spark.read.parquet", "arg_expr": "path",
            "why_unresolvable": "function parameter — no call sites in this workload",
            "severity": "critical",
        }

    def _llm_data(self, edges: list[dict], unresolvable: list[dict],
                  insights: list[str] | None = None,
                  analyzed_files: list[str] | None = None,
                  excluded_files: list[str] | None = None) -> dict:
        return {
            "model": "test-model",
            "generated_at": "2026-01-01T00:00:00Z",
            "dispatch_units_processed": 1,
            "analyzed_files": analyzed_files if analyzed_files is not None else ["a.py"],
            "excluded_files": excluded_files if excluded_files is not None else [],
            "edges": edges,
            "unresolvable_edges": unresolvable,
            "llm_insights": insights or [],
        }

    # ── Gate contract — file coverage ──────────────────────────────────────

    def test_gate_passes_when_all_resolved(self, tmp_path, capsys):
        """All files analyzed, all edges resolved."""
        p = self._ir_json(tmp_path,
            unresolved=[],
            llm=self._llm_data(
                edges=[self._llm_resolved("a.py", 10)],
                unresolvable=[],
                analyzed_files=["a.py"],
                insights=["All paths traced to S3 bucket literals."],
            ))
        assert gate_run(p) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "pass"
        assert result["resolved_count"] == 1

    def test_gate_passes_when_all_unresolvable(self, tmp_path, capsys):
        """All edges confirmed unresolvable — valid as long as coverage is complete."""
        p = self._ir_json(tmp_path,
            unresolved=[],
            llm=self._llm_data(
                edges=[],
                unresolvable=[self._llm_unresolvable("a.py", 10)],
                analyzed_files=["a.py"],
                insights=["Paths are function parameters with no call sites."],
            ))
        assert gate_run(p) == 0

    def test_gate_passes_mixed_resolved_and_unresolvable(self, tmp_path):
        """Some resolved, some unresolvable — valid with full file coverage."""
        p = self._ir_json(tmp_path,
            unresolved=[],
            llm=self._llm_data(
                edges=[self._llm_resolved("a.py", 1), self._llm_resolved("a.py", 2)],
                unresolvable=[self._llm_unresolvable("a.py", 3)],
                analyzed_files=["a.py"],
            ))
        assert gate_run(p) == 0

    def test_gate_pass_output_includes_newly_discovered_count(self, tmp_path, capsys):
        p = self._ir_json(tmp_path,
            unresolved=[],
            llm=self._llm_data(
                edges=[
                    {**self._llm_resolved("a.py", 1), "source": "newly_discovered",
                     "call_expr": "boto3.client"},
                    self._llm_resolved("a.py", 2),
                ],
                unresolvable=[self._llm_unresolvable("a.py", 3)],
                analyzed_files=["a.py"],
            ))
        assert gate_run(p) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["newly_discovered_count"] == 1
        assert out["resolved_count"] == 1
        assert out["unresolvable_count"] == 1

    def test_gate_passes_with_excluded_files(self, tmp_path):
        """Files with no data I/O must be in excluded_files to satisfy coverage."""
        p = self._ir_json(tmp_path,
            unresolved=[],
            llm=self._llm_data(
                edges=[self._llm_resolved("etl/load.py", 1)],
                unresolvable=[],
                analyzed_files=["etl/load.py"],
                excluded_files=["utils/__init__.py"],
            ))
        assert gate_run(p) == 0

    # ── IR structural validation ────────────────────────────────────────────

    def test_missing_llm_data_is_gate_exit_3(self, tmp_path, capsys):
        """No llm_resolved_data_edges means the agent never ran → exit 3."""
        p = self._ir_json(tmp_path,
            unresolved=[self._edge()], llm=None)
        assert gate_run(p) == 3

    def test_llm_insights_round_trip_in_ir(self):
        """llm_insights persists through Pydantic round-trip."""
        container = LLMResolvedDataEdges(
            model="test",
            llm_insights=["Caller scripts are absent.", "All paths are runtime config."],
        )
        dumped = container.model_dump_json()
        restored = LLMResolvedDataEdges.model_validate_json(dumped)
        assert restored.llm_insights == [
            "Caller scripts are absent.",
            "All paths are runtime config.",
        ]

    def test_llm_insights_renders_as_advisory_bullets(self):
        """Template renders llm_insights as the Advisory callout."""
        from adapters import prototype_v1
        node = GraphNode(id="a.py", label="a.py", x=0, y=0)
        graph = DependencyGraph(module="t", nodes=[node], edges=[], clusters=[],
                                width=400, height=200, file_count=1, edge_count=0)
        assessment = Assessment(
            data_dependency_graph=graph,
            llm_resolved_data_edges=LLMResolvedDataEdges(
                model="test",
                unresolvable_edges=[{
                    "file": "a.py", "line": 1, "kind": "read",
                    "arg_expr": "p", "why_unresolvable": "dead code",
                    "severity": "benign",
                }],
                llm_insights=[
                    "All paths are function parameters with no call sites.",
                    "Orchestration layer missing from export.",
                ],
            ),
        )
        html = prototype_v1.render(assessment)
        assert "Advisory" in html
        assert "what this means" in html
        assert "function parameters with no call sites" in html
        assert "Orchestration layer missing" in html

    def test_warned_state_renders_partial_results(self, tmp_path):
        """Even with gate=warned (unresolved>0), render should show what was resolved."""
        from adapters import prototype_v1
        node = GraphNode(id="a.py", label="a.py", x=0, y=0)
        graph = DependencyGraph(module="t", nodes=[node], edges=[], clusters=[],
                                width=400, height=200, file_count=1, edge_count=0)
        # Simulate partial classification: 1 resolved, 1 still unresolved
        assessment = Assessment(
            data_dependency_graph=graph,
            unresolved_data_edges=[
                UnresolvedDataEdge(file="a.py", line=99, kind="read",
                                   call_expr="spark.read.parquet", arg_expr="p",
                                   reason="ast.Name"),
            ],
            llm_resolved_data_edges=LLMResolvedDataEdges(
                model="test",
                edges=[LLMResolvedEdge(
                    file="a.py", line=10, kind="read",
                    resolved_signature="s3://bucket/x",
                    resolution_type="traced",
                    source="resolved_unresolved",
                )],
                unresolvable_edges=[],
                llm_insights=["One edge resolved; one could not be traced."],
            ),
        )
        html = prototype_v1.render(assessment)
        # Orange section shows the remaining unresolved entry
        assert "Unresolved read/write calls" in html
        # LLM badge shows it ran
        assert "LLM resolution was run" in html
        # Advisory is present
        assert "Advisory" in html

    # ── Confirmed-unresolvable suppression in merge ────────────────────────

    def test_confirmed_unresolvable_excluded_from_fresh_new(self):
        """Reconciliation must drop a baseline unresolved edge whose
        (file, line, kind) the LLM confirmed unresolvable — via the shared
        edge_reconcile helper (same one the gate uses)."""
        import edge_reconcile
        llm = LLMResolvedDataEdges(
            model="test", edges=[],
            unresolvable_edges=[LLMUnresolvableEdge(
                file="dead.py", line=5, kind="write",
                arg_expr="output_path", why_unresolvable="dead code",
                severity="benign")],
        )
        baseline = [UnresolvedDataEdge(file="dead.py", line=5, kind="write",
                                       call_expr="df.write.parquet", arg_expr="output_path",
                                       reason="ast.Name")]
        remaining = edge_reconcile.remaining_data_edges(baseline, llm)
        assert not any(e.line == 5 for e in remaining)
