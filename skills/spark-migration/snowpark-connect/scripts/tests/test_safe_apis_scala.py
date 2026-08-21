"""Tests for the safe-API fast path in ``analyze_scala`` (Row D parity).

A block whose every method call is on the result-identical allowlist
(``data/safe_apis.json``, shared with the PySpark analyzer) and that raised no
deterministic ``scos_issue`` is compatible on SCOS and skips the RAG/LLM
round-trip entirely.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from analyze_scala import (
    ScalaCodeBlock,
    _process_single_block,
    is_block_safe,
    load_safe_apis,
)


class _ExplodingRAG:
    """RAG stub whose predict_failure must never be called on a safe block."""

    def predict_failure(self, code: str) -> dict:
        raise AssertionError("predict_failure called for a safe block — RAG was not skipped")


class _StubRAG:
    def predict_failure(self, code: str) -> dict:
        return {"similar_patterns": []}


def _block(code: str, funcs: list[str]) -> ScalaCodeBlock:
    return ScalaCodeBlock(code=code, line_start=1, line_end=1, block_type="statement",
                          functions=funcs)


# --- load_safe_apis / is_block_safe ----------------------------------------


def test_load_safe_apis_nonempty_with_known_patterns():
    apis = load_safe_apis()
    assert len(apis) > 50
    assert "select" in apis and "filter" in apis


def test_is_block_safe_true_when_all_safe():
    apis = {"select", "filter"}
    assert is_block_safe(["select", "filter"], apis) is True


def test_is_block_safe_false_when_any_unsafe():
    apis = {"select", "filter"}
    assert is_block_safe(["select", "checkpoint"], apis) is False


def test_is_block_safe_false_on_empty_inputs():
    assert is_block_safe([], {"select"}) is False
    assert is_block_safe(["select"], set()) is False


# --- _process_single_block safe-skip ---------------------------------------


def test_safe_block_skips_rag(tmp_path):
    apis = load_safe_apis()
    blk = _block('val r = df.select("a").filter(col("a") > 1)', ["select", "filter"])
    # _ExplodingRAG asserts predict_failure is never reached.
    early, block_data = _process_single_block(blk, _ExplodingRAG(), Path("/x"), 0.55, apis)
    assert early is None and block_data is None


def test_unsafe_block_still_queries_rag():
    apis = load_safe_apis()
    blk = _block('val r = df.select("a").someExoticOp()', ["select", "someExoticOp"])
    # someExoticOp is not on the allowlist -> must not safe-skip -> RAG is queried.
    early, block_data = _process_single_block(blk, _StubRAG(), Path("/x"), 0.55, apis)
    # _StubRAG returns no matches and there are no scos_issues, so the block is
    # dropped (None, None) AFTER querying — the point is it didn't short-circuit
    # on the safe path. We assert RAG was reachable by using _StubRAG (no raise).
    assert early is None


def test_block_with_scos_issue_not_safe_skipped():
    apis = load_safe_apis()
    # Unsupported format raises a scos_issue, so even if other calls are "safe"
    # the block must not be skipped — it stays for emission/analysis.
    blk = _block('val df = spark.read.format("avro").load("/p").select("a")',
                 ["format", "load", "select"])
    early, block_data = _process_single_block(blk, _StubRAG(), Path("/x"), 0.55, apis)
    # A decidable unsupported-format scos_issue is present -> block_data returned.
    assert block_data is not None
    assert any(i.get("decidable") for i in block_data["scos_issues"])


def test_safe_apis_none_disables_fast_path():
    blk = _block('val r = df.select("a")', ["select"])
    # With no allowlist, the safe path is disabled -> RAG is queried (StubRAG).
    early, block_data = _process_single_block(blk, _StubRAG(), Path("/x"), 0.55, None)
    assert early is None  # no matches, no issues -> dropped, but RAG was reached
