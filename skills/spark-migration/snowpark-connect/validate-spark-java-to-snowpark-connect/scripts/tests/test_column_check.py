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
    java_file = source_root / "Jobs.java"
    java_file.write_text("public class Jobs { public static void main(String[] args) {} }", encoding="utf-8")
    ast_facts = {
        "files": [{
            "path": str(java_file),
            "parse_ok": True,
            "column_refs": ["order_id", "amount"],
            "write_helpers": ["writeOut"],
        }],
    }
    analysis = {
        "entrypoints": [{
            "id": "jobs",
            "path": "Jobs.java",
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
    java_file = source_root / "Jobs.java"
    java_file.write_text("public class Jobs { public static void main(String[] args) {} }", encoding="utf-8")
    ast_facts = {
        "files": [{
            "path": str(java_file),
            "parse_ok": True,
            "column_refs": ["order_id"],
            "write_helpers": [],
        }],
    }
    analysis = {
        "entrypoints": [{
            "id": "jobs",
            "path": "Jobs.java",
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


# ── Java-specific column extraction patterns ────────────────────────────────

def test_java_col_patterns_df_col(tmp_path):
    """df.col("x") — Java-idiomatic column reference in schema."""
    source_root = tmp_path / "Validation" / "source"
    source_root.mkdir(parents=True)
    java_file = source_root / "Agg.java"
    java_file.write_text(
        'public class Agg { public static void main(String[] a) { df.col("customer_id"); } }',
        encoding="utf-8",
    )
    ast_facts = {
        "files": [{
            "path": str(java_file),
            "parse_ok": True,
            "column_refs": ["customer_id", "total"],
            "write_helpers": [],
        }],
    }
    analysis = {
        "entrypoints": [{"id": "agg", "path": "Agg.java",
                         "external_sources": [{"id": "s1", "category": "table",
                                               "schema": [{"name": "customer_id", "type": "string"},
                                                          {"name": "total", "type": "decimal(10,2)"}]}],
                         "sinks": []}],
        "external_sources": [{"id": "s1", "category": "table",
                               "schema": [{"name": "customer_id", "type": "string"},
                                          {"name": "total", "type": "decimal(10,2)"}]}],
    }
    probs = cc.check_columns(ast_facts, analysis, source_root=source_root)
    assert probs == [], f"unexpected column problems: {probs}"


def test_java_col_pattern_missing_column_flagged(tmp_path):
    """Column referenced in Java source but absent from schema is flagged."""
    source_root = tmp_path / "Validation" / "source"
    source_root.mkdir(parents=True)
    java_file = source_root / "Filter.java"
    java_file.write_text(
        'public class Filter { public static void main(String[] a) {} }',
        encoding="utf-8",
    )
    ast_facts = {
        "files": [{
            "path": str(java_file),
            "parse_ok": True,
            "column_refs": ["known_col", "mystery_col"],
            "write_helpers": [],
        }],
    }
    analysis = {
        "entrypoints": [{"id": "filter", "path": "Filter.java",
                         "external_sources": [{"id": "s1", "category": "table",
                                               "schema": [{"name": "known_col", "type": "string"}]}],
                         "sinks": []}],
        "external_sources": [{"id": "s1", "category": "table",
                               "schema": [{"name": "known_col", "type": "string"}]}],
    }
    probs = cc.check_columns(ast_facts, analysis, source_root=source_root)
    assert any("mystery_col" in p for p in probs), f"missing column not flagged: {probs}"
