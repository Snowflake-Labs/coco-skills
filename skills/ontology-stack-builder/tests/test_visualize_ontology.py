"""Ontology text reaches the visualizer's raw-HTML rendering only as escaped text.

Run:  uv run --extra dev pytest tests/
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import visualize_ontology as vo  # noqa: E402

PAYLOAD = '<img src=x onerror="alert(1)">'


@pytest.fixture
def html_calls(monkeypatch):
    """Every string rendered with unsafe_allow_html=True."""
    calls: list[str] = []

    def markdown(body, unsafe_allow_html=False, **_):
        if unsafe_allow_html:
            calls.append(body)

    monkeypatch.setattr(vo.st, "markdown", markdown)
    return calls


def _assert_escaped(calls: list[str]) -> None:
    assert calls, "nothing was rendered as HTML"
    joined = "\n".join(calls)
    assert "<img" not in joined
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in joined


@pytest.mark.parametrize("is_abstract", [False, True])
@pytest.mark.parametrize("with_child", [False, True])
def test_class_tree_escapes_label_and_description(html_calls, is_abstract, with_child):
    name_to_cls = {
        "A": {"name": "A", "label": PAYLOAD, "description": PAYLOAD, "is_abstract": is_abstract},
        "B": {"name": "B", "label": "Child"},
    }
    children = {"A": ["B"]} if with_child else {}
    # A leaf shows its description only when it matches the search.
    vo.render_interactive_tree("A", children, name_to_cls, search="img")
    _assert_escaped(html_calls)


@pytest.mark.parametrize("is_abstract", [False, True])
@pytest.mark.parametrize("with_child", [False, True])
def test_relation_tree_escapes_every_field(html_calls, is_abstract, with_child):
    rel_by_name = {
        "R": {"name": "R", "label": PAYLOAD, "description": PAYLOAD, "is_abstract": is_abstract,
              "domain_class": PAYLOAD, "range_class": PAYLOAD, "cardinality": PAYLOAD},
        "S": {"name": "S", "label": "Child"},
    }
    children = {"R": ["S"]} if with_child else {}
    vo.render_interactive_rel_tree("R", children, rel_by_name)
    _assert_escaped(html_calls)
    if with_child:
        assert any("Cardinality: &lt;img" in c for c in html_calls)


def test_escape_helper():
    assert vo._esc(PAYLOAD) == "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"
    assert vo._esc(None) == "None"
    assert vo._esc("A & B") == "A &amp; B"


def test_flat_relation_list_escapes_every_field(html_calls):
    rel_by_name = {"R": {"name": "R", "label": PAYLOAD, "description": PAYLOAD,
                         "domain_class": PAYLOAD, "range_class": PAYLOAD}}
    vo.render_flat_relations(["R"], rel_by_name)
    _assert_escaped(html_calls)


@pytest.mark.parametrize("status,cov", [
    ("mapped", {"status": "mapped", "view_name": PAYLOAD}),
    ("covered", {"status": "covered", "covering_ancestor": PAYLOAD}),
    ("unmapped", {"status": "unmapped"}),
])
def test_graph_tooltips_escape_ontology_text(status, cov):
    classes = [{"name": "A", "label": PAYLOAD, "description": PAYLOAD, "parent_name": "P"},
               {"name": "P", "label": PAYLOAD, "is_abstract": True}]
    # Show only A so P comes back as a ghost parent node.
    nodes, _ = vo.build_agraph_nodes_edges(classes, [], {"A": cov}, max_nodes=1)
    titles = [n.title for n in nodes]
    assert len(titles) == 2
    for title in titles:
        assert "<img" not in title
        assert "&lt;img" in title


def test_node_detail_header_escapes_label(monkeypatch):
    from unittest.mock import MagicMock

    calls: list[str] = []
    fake = MagicMock()
    fake.markdown.side_effect = lambda body, unsafe_allow_html=False, **_: (
        calls.append(body) if unsafe_allow_html else None)
    fake.columns.side_effect = lambda spec, **_: [MagicMock() for _ in range(
        spec if isinstance(spec, int) else len(spec))]
    monkeypatch.setattr(vo, "st", fake)
    classes = [{"name": "A", "label": PAYLOAD, "description": PAYLOAD}]
    vo.render_node_detail("A", classes, [], {"A": {"status": "unmapped"}}, None)
    _assert_escaped(calls)
