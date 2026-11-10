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
