"""Tests for edge_reconcile — the shared gate/render reconciliation helper."""
from __future__ import annotations

import sys
from pathlib import Path

_ASSESS = Path(__file__).resolve().parent.parent
if str(_ASSESS) not in sys.path:
    sys.path.insert(0, str(_ASSESS))

import edge_reconcile
from assess_ir import (
    LLMResolvedDataEdges, LLMResolvedEdge, LLMResolvedImport,
    LLMUnresolvableEdge, UnresolvedDataEdge, UnresolvedDynamicImport,
)


def _edge(file, line, kind, sig, src, rtype="traced"):
    return LLMResolvedEdge(file=file, line=line, kind=kind, resolved_signature=sig,
                           resolution_type=rtype, source=src)


class TestDataAccounting:
    def test_resolved_and_unresolvable_are_accounted(self):
        llm = LLMResolvedDataEdges(
            model="t",
            edges=[_edge("a.py", 1, "read", "t", "resolved_unresolved")],
            unresolvable_edges=[LLMUnresolvableEdge(file="b.py", line=2, kind="write",
                                                    why_unresolvable="runtime",
                                                    severity="benign")],
        )
        keys = edge_reconcile.accounted_data_keys(llm)
        assert ("a.py", 1, "read") in keys
        assert ("b.py", 2, "write") in keys

    def test_newly_discovered_not_accounted_against_baseline(self):
        # newly_discovered edges resolve nothing in the unresolved baseline.
        llm = LLMResolvedDataEdges(
            model="t", edges=[_edge("a.py", 1, "read", "t", "newly_discovered")],
        )
        assert edge_reconcile.accounted_data_keys(llm) == set()

    def test_inferred_counts_as_accounted(self):
        """A resolved inferred edge must reconcile (it's drawn in the DAG)."""
        llm = LLMResolvedDataEdges(
            model="t",
            edges=[_edge("a.py", 1, "read", "t", "resolved_unresolved", rtype="inferred")],
        )
        assert ("a.py", 1, "read") in edge_reconcile.accounted_data_keys(llm)

    def test_remaining_is_baseline_minus_accounted(self):
        baseline = [
            UnresolvedDataEdge(file="a.py", line=1, kind="read",
                               call_expr="spark.sql", arg_expr="q", reason="x"),
            UnresolvedDataEdge(file="c.py", line=9, kind="read",
                               call_expr="spark.sql", arg_expr="q", reason="x"),
        ]
        llm = LLMResolvedDataEdges(
            model="t", edges=[_edge("a.py", 1, "read", "t", "resolved_unresolved")],
        )
        remaining = edge_reconcile.remaining_data_edges(baseline, llm)
        assert [(e.file, e.line) for e in remaining] == [("c.py", 9)]

    def test_remaining_does_not_mutate_baseline(self):
        baseline = [UnresolvedDataEdge(file="a.py", line=1, kind="read",
                                       call_expr="x", arg_expr="y", reason="z")]
        edge_reconcile.remaining_data_edges(baseline, LLMResolvedDataEdges(model="t"))
        assert len(baseline) == 1  # untouched

    def test_works_on_dicts_too(self):
        baseline = [{"file": "a.py", "line": 1, "kind": "read"}]
        llm = {"edges": [{"file": "a.py", "line": 1, "kind": "read",
                          "source": "resolved_unresolved"}],
               "unresolvable_edges": [], "resolved_imports": []}
        assert edge_reconcile.remaining_data_edges(baseline, llm) == []


class TestImportAccounting:
    def _imp(self, file, line, targets, rtype, why=""):
        return LLMResolvedImport(file=file, line=line, kind="spec_from_file",
                                 resolved_targets=targets, resolution_type=rtype,
                                 why_unresolvable=why)

    def test_resolved_and_unresolvable_accounted_for_gate(self):
        llm = LLMResolvedDataEdges(model="t", resolved_imports=[
            self._imp("o.py", 1, ["t.py"], "traced"),
            self._imp("o.py", 2, [], "unresolvable", "runtime path"),
        ])
        acc = edge_reconcile.accounted_import_keys(llm)
        assert ("o.py", 1, "spec_from_file") in acc
        assert ("o.py", 2, "spec_from_file") in acc

    def test_display_drops_resolved_keeps_unresolvable_with_reason(self):
        baseline = [
            UnresolvedDynamicImport(file="o.py", line=1, kind="spec_from_file",
                                    reason="path not resolvable", raw_expr="spec(p)"),
            UnresolvedDynamicImport(file="o.py", line=2, kind="spec_from_file",
                                    reason="path not resolvable", raw_expr="spec(q)"),
        ]
        llm = LLMResolvedDataEdges(model="t", resolved_imports=[
            self._imp("o.py", 1, ["t.py"], "traced"),                 # resolved → drop
            self._imp("o.py", 2, [], "unresolvable", "runtime argv"),  # keep w/ reason
        ])
        disp = edge_reconcile.remaining_dynamic_imports(baseline, llm)
        assert [(i.file, i.line) for i in disp] == [("o.py", 2)]
        assert disp[0].reason == "LLM: runtime argv"

    def test_gate_import_leak_when_unaccounted(self):
        baseline = [UnresolvedDynamicImport(file="o.py", line=5, kind="spec_from_file",
                                            reason="?", raw_expr="spec(z)")]
        leaks = edge_reconcile.import_leaks(baseline, LLMResolvedDataEdges(model="t"))
        assert len(leaks) == 1


class TestGateRenderParity:
    """The single most important invariant: for the SAME IR, the gate's data
    leak set equals the render's still-unresolved display set — no drift."""

    def test_data_leaks_equal_render_remaining(self):
        baseline = [
            UnresolvedDataEdge(file="a.py", line=1, kind="read",
                               call_expr="spark.sql", arg_expr="q", reason="x"),
            UnresolvedDataEdge(file="b.py", line=2, kind="read",
                               call_expr="spark.sql", arg_expr="q", reason="x"),
            UnresolvedDataEdge(file="c.py", line=3, kind="write",
                               call_expr="df.write", arg_expr="p", reason="x"),
        ]
        llm = LLMResolvedDataEdges(
            model="t",
            edges=[_edge("a.py", 1, "read", "t", "resolved_unresolved")],
            unresolvable_edges=[LLMUnresolvableEdge(file="b.py", line=2, kind="read",
                                                    why_unresolvable="runtime",
                                                    severity="benign")],
        )
        gate_leaks = {(e.file, e.line, e.kind) for e in edge_reconcile.data_leaks(baseline, llm)}
        render_remaining = {(e.file, e.line, e.kind)
                            for e in edge_reconcile.remaining_data_edges(baseline, llm)}
        assert gate_leaks == render_remaining == {("c.py", 3, "write")}
