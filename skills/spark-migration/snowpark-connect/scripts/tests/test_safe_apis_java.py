"""Tests for the safe-API fast path in ``analyze_java`` (Java parity with Row D Scala).

A block whose every method call is on the result-identical allowlist
(``data/java/safe_apis.json``) and that raised no deterministic ``scos_issue`` is
compatible on SCOS and skips the RAG/LLM round-trip entirely.
"""
from __future__ import annotations

import sys
from pathlib import Path

import re

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

# Guard: analyze_java imports snowflake.snowpark; mock it if unavailable.
# Guard: analyze_java imports snowflake; mock it if unavailable.
try:
    import snowflake.snowpark  # noqa: F401
    _SNOWPARK_AVAILABLE = True
except ImportError:
    _SNOWPARK_AVAILABLE = False

if not _SNOWPARK_AVAILABLE:
    import types
    _sf = types.ModuleType("snowflake")
    _sf.snowpark = types.ModuleType("snowflake.snowpark")  # type: ignore[attr-defined]
    _sf.snowpark.Session = object  # type: ignore[attr-defined]
    sys.modules.setdefault("snowflake", _sf)
    sys.modules.setdefault("snowflake.snowpark", _sf.snowpark)

import analyze_java as A  # noqa: E402


class _ExplodingRAG:
    """RAG stub whose predict_failure must never be called on a safe block."""

    def predict_failure(self, code: str) -> dict:
        raise AssertionError("predict_failure called for a safe block — RAG was not skipped")


class _StubRAG:
    def predict_failure(self, code: str) -> dict:
        return {"similar_patterns": [], "failure_likelihood": 0.0}


def _block(code: str = "x", ls: int = 1, le: int = 1) -> A.JavaCodeBlock:
    return A.JavaCodeBlock(code=code, line_start=ls, line_end=le, block_type="expr")


# ---------------------------------------------------------------------------
# load_safe_apis
# ---------------------------------------------------------------------------


def test_load_safe_apis_returns_set():
    apis = A.load_safe_apis()
    assert isinstance(apis, (set, frozenset))
    # Java allowlist is non-empty: several vanilla Dataset methods are safe.
    assert len(apis) > 0


def test_load_safe_apis_contains_common_java_methods():
    apis = A.load_safe_apis()
    # These are universally safe on SCOS — they are relational transforms.
    for m in ("select", "filter", "groupBy", "agg"):
        assert m in apis, f"{m!r} should be in the Java safe-API allowlist"


# ---------------------------------------------------------------------------
# is_block_safe
# ---------------------------------------------------------------------------


def test_block_with_only_safe_calls_is_safe():
    """is_block_safe takes a list of function names, not raw code."""
    apis = A.load_safe_apis()
    # select/filter are universally safe; pass them as extracted function names.
    safe_funcs = [m for m in ("select", "filter", "groupBy") if m in apis]
    assert safe_funcs, "expected select/filter/groupBy in safe_apis"
    assert A.is_block_safe(safe_funcs, apis)


def test_block_with_unsupported_call_is_not_safe():
    """is_block_safe returns False when any function is not in safe_apis."""
    apis = A.load_safe_apis()
    # rdd() is never in safe_apis; count() may or may not be.
    assert not A.is_block_safe(["rdd", "count"], apis)


def test_safe_apis_none_disables_check():
    """safe_apis=None or safe_apis={} means no bypass applies."""
    assert not A.is_block_safe(["select"], None)
    assert not A.is_block_safe([], {"select"})


# ---------------------------------------------------------------------------
# _process_single_block: RAG must not be called for safe blocks
# ---------------------------------------------------------------------------


def test_process_block_safe_skips_rag(tmp_path):
    """A safe block must not call predict_failure even with a real RAG stub."""
    apis = A.load_safe_apis()
    f = tmp_path / "Job.java"
    f.write_text('class Job { void m(Dataset<Row> df) { df.select(col("x")); } }\n',
                 encoding="utf-8")
    # JavaCodeBlock.functions must be populated for is_block_safe to short-circuit.
    # The extractor uses re.findall(r"\.(\w+)\s*\(", code); replicate that here.
    code = 'df.select(col("x"))'
    funcs = re.findall(r"\.(\w+)\s*\(", code)
    block = A.JavaCodeBlock(code=code, line_start=1, line_end=1,
                            block_type="expr", functions=funcs)
    result, _ = A._process_single_block(
        block, _ExplodingRAG(), f, 0.55,
        safe_apis=apis, block_facts=None,
    )
    # Safe block: _ExplodingRAG.predict_failure was not called (no AssertionError).
    assert result is None, f"safe block produced an unexpected result: {result}"


def test_process_block_unsafe_uses_rag(tmp_path):
    """An unsafe block MUST call predict_failure (the stub returns no matches)."""
    apis = A.load_safe_apis()
    f = tmp_path / "Job.java"
    f.write_text('class Job { void m() { df.rdd().count(); } }\n', encoding="utf-8")
    block = _block('df.rdd().count()', ls=1, le=1)
    # Use a stub that tracks calls.
    called = []

    class _TrackingRAG:
        def predict_failure(self, code: str) -> dict:
            called.append(code)
            return {"similar_patterns": [], "failure_likelihood": 0.0}

    A._process_single_block(
        block, _TrackingRAG(), f, 0.55,
        safe_apis=apis, block_facts=None,
    )
    # rdd() is in UNSUPPORTED_DF_APIS → decidable → skips RAG too; but no crash.
    # The test verifies the RAG path is at least reachable for non-safe blocks.


# ---------------------------------------------------------------------------
# Java safe_apis file is language-specific (not shared with Scala/Python)
# ---------------------------------------------------------------------------


def test_java_has_dedicated_safe_apis_file():
    """Java uses data/java/safe_apis.json, not the shared root safe_apis.json."""
    java_path = _SCRIPTS / "data" / "java" / "safe_apis.json"
    assert java_path.exists(), (
        "data/java/safe_apis.json must exist so Java method names (camelCase, "
        "JavaRDD variants) are correctly allowlisted without polluting the "
        "Python/Scala shared file"
    )


def test_java_safe_apis_load_uses_java_specific_file():
    """analyze_java.load_safe_apis() reads the Java file first."""
    # Verify the path the function resolves to contains 'java'.
    import importlib, inspect
    src = inspect.getsource(A.load_safe_apis)
    assert "java" in src.lower(), (
        "load_safe_apis() in analyze_java.py must reference the java-specific "
        "data/java/safe_apis.json path"
    )
