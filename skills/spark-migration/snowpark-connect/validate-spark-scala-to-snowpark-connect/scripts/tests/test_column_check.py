"""Tests for column_check.py."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import column_check as cc  # noqa: E402


def test_flags_missing_columns_and_write_helpers(tmp_path):
    source_root = tmp_path / "Validation" / "source"
    source_root.mkdir(parents=True)
    scala = source_root / "Jobs.scala"
    scala.write_text("object Main {}", encoding="utf-8")
    ast_facts = {
        "files": [{
            "path": str(scala),
            "parse_ok": True,
            "column_refs": ["order_id", "amount"],
            "write_helpers": ["writeOut"],
        }],
    }
    analysis = {
        "entrypoints": [{
            "id": "jobs",
            "path": "Jobs.scala",
            "external_sources": [{
                "id": "src_orders",
                "category": "table",
                "schema": [{"name": "order_id", "type": "string"}],
            }],
            "sinks": [],
        }],
        "external_sources": [{
            "id": "src_orders",
            "category": "table",
            "schema": [{"name": "order_id", "type": "string"}],
        }],
    }
    probs = cc.check_columns(ast_facts, analysis, source_root=source_root)
    assert any("amount" in p for p in probs)
    assert any("write_helper" in p for p in probs)


def test_passes_when_columns_declared(tmp_path):
    source_root = tmp_path / "Validation" / "source"
    source_root.mkdir(parents=True)
    scala = source_root / "Jobs.scala"
    scala.write_text("object Main {}", encoding="utf-8")
    ast_facts = {
        "files": [{
            "path": str(scala),
            "parse_ok": True,
            "column_refs": ["order_id"],
            "write_helpers": [],
        }],
    }
    analysis = {
        "entrypoints": [{
            "id": "jobs",
            "path": "Jobs.scala",
            "external_sources": ["src_orders"],
            "sinks": ["sink_out"],
        }],
        "external_sources": [{
            "id": "src_orders",
            "category": "table",
            "schema": [{"name": "order_id", "type": "string"}],
        }],
        "sinks": [{"id": "sink_out", "kind": "table"}],
    }
    probs = cc.check_columns(ast_facts, analysis, source_root=source_root)
    assert probs == []


def test_external_sinks_satisfy_write_helper(tmp_path):
    """Synthesizer-canonical external_sinks must satisfy write_helper coverage."""
    source_root = tmp_path / "Validation" / "source"
    source_root.mkdir(parents=True)
    scala = source_root / "Jobs.scala"
    scala.write_text("object Main {}", encoding="utf-8")
    ast_facts = {
        "files": [{
            "path": str(scala),
            "parse_ok": True,
            "column_refs": ["order_id"],
            "write_helpers": ["writeOut"],
        }],
    }
    analysis = {
        "entrypoints": [{
            "id": "jobs",
            "path": "Jobs.scala",
            "external_sources": [{
                "id": "src_orders",
                "category": "table",
                "schema": [{"name": "order_id", "type": "string"}],
            }],
            "sinks": [],
            "external_sinks": [{"id": "writeOut", "name": "writeOut", "kind": "table"}],
        }],
        "external_sources": [],
        "external_sinks": [{"id": "writeOut", "name": "writeOut", "kind": "table"}],
    }
    probs = cc.check_columns(ast_facts, analysis, source_root=source_root)
    assert probs == []
