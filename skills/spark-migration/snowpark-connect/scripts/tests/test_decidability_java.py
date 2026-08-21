"""Tests for the KB-decidability bypass in ``analyze_java`` (Java parity).

Fully-decidable blocks — those whose only signals are exact unsupported
triggers (unsupported import/format/module or unsupported Dataset API) with no
fuzzy RAG evidence — are emitted deterministically and must NOT be sent to the
batch LLM. Context-dependent signals (behavioral differences, UDFs, no-ops) and
any fuzzy ``matching_patterns`` keep the block on the LLM path.

Also tests:
  * facts-path vs regex-fallback parity (SCOS_NO_AST_FACTS=1 behavior)
  * Java RDD gateway detection (JavaRDD / JavaSparkContext)
  * Safe-API drop
  * Unsupported import detection (Java import syntax with semicolons)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure scripts/ is on sys.path.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Guard: analyze_java imports snowflake.snowpark; mock it if unavailable.
try:
    import snowflake.snowpark  # noqa: F401
    _SNOWPARK_AVAILABLE = True
except ImportError:
    _SNOWPARK_AVAILABLE = False

if not _SNOWPARK_AVAILABLE:
    # Patch snowflake.snowpark before importing analyze_java
    import types
    _sf = types.ModuleType("snowflake")
    _sf.snowpark = types.ModuleType("snowflake.snowpark")  # type: ignore[attr-defined]
    _sf.snowpark.Session = object  # type: ignore[attr-defined]
    sys.modules.setdefault("snowflake", _sf)
    sys.modules.setdefault("snowflake.snowpark", _sf.snowpark)

import analyze_java as A


class _StubRAG:
    """Offline RAG stub: returns no fuzzy matches."""

    def predict_failure(self, code: str) -> dict:
        return {"similar_patterns": []}


def _block(code: str = "x", ls: int = 1, le: int = 1) -> A.JavaCodeBlock:
    return A.JavaCodeBlock(code=code, line_start=ls, line_end=le, block_type="expr")


# ---------------------------------------------------------------------------
# check_* emitters tag decidability correctly
# ---------------------------------------------------------------------------

def test_exact_unsupported_emitters_tag_decidable_true():
    imp = A.check_unsupported_imports_java(
        "import org.apache.spark.ml.Pipeline;"
    )
    fmt = A.check_unsupported_formats_java('spark.read().format("avro").load("/p");')
    dfapi = A.check_unsupported_df_apis_java("df.checkpoint();")
    assert imp and all(i.get("decidable") for i in imp)
    assert fmt and all(i.get("decidable") for i in fmt)
    assert dfapi and all(i.get("decidable") for i in dfapi)


def test_context_dependent_emitters_not_decidable():
    bd = A.check_behavioral_differences_java('df.select(col("x").cast("int"));')
    udf = A.check_udf_patterns_java('spark.udf().register("f", myFn);')
    noop = A.check_noop_apis_java('df.hint("broadcast");')
    # behavioral/udf/noop must never be tagged decidable.
    assert bd and not any(i.get("decidable") for i in bd)
    assert udf and not any(i.get("decidable") for i in udf)
    assert noop and not any(i.get("decidable") for i in noop)


# ---------------------------------------------------------------------------
# Java RDD gateway detection
# ---------------------------------------------------------------------------

def test_java_rdd_pattern_detected():
    code = "import org.apache.spark.api.java.JavaRDD;\nJavaRDD<String> rdd = jsc.textFile(path);"
    detected, reason = A.has_rdd_usage(code)
    assert detected, f"Expected RDD detected, got reason={reason}"


def test_java_spark_context_pattern_detected():
    code = "JavaSparkContext jsc = new JavaSparkContext(conf);"
    detected, reason = A.has_rdd_usage(code)
    assert detected


def test_java_rdd_from_facts():
    facts = {
        "imports": [{"ref": "org.apache.spark.api.java.JavaRDD", "line": 1}],
        "calls": [], "selects": [], "new_types": [], "spark_sql": [],
    }
    detected, reason = A.has_rdd_usage_from_facts(facts)
    assert detected, "Expected RDD detected from import fact"


def test_java_spark_context_from_new_fact():
    facts = {
        "imports": [], "calls": [], "selects": [], "spark_sql": [],
        "new_types": [{"type": "JavaSparkContext", "line": 1}],
    }
    detected, reason = A.has_rdd_usage_from_facts(facts)
    assert detected, "Expected JavaSparkContext ctor detected"


def test_java_rdd_select_fact():
    facts = {
        "imports": [], "calls": [], "spark_sql": [],
        "selects": [{"member": "JavaRDD", "recv_leaf": "df", "line": 5}],
        "new_types": [],
    }
    detected, _ = A.has_rdd_usage_from_facts(facts)
    assert detected


# ---------------------------------------------------------------------------
# Unsupported import detection — Java semicolon syntax
# ---------------------------------------------------------------------------

def test_java_import_with_semicolon():
    code = "import org.apache.spark.ml.Pipeline;\nimport org.apache.spark.sql.Dataset;"
    issues = A.check_unsupported_imports_java(code)
    modules = [i["api"] for i in issues]
    assert "org.apache.spark.ml" in modules


def test_java_wildcard_import():
    code = "import org.apache.spark.mllib.*;"
    issues = A.check_unsupported_imports_java(code)
    modules = [i["api"] for i in issues]
    assert "org.apache.spark.mllib" in modules


def test_java_rdd_import_decidable():
    code = "import org.apache.spark.api.java.JavaRDD;"
    issues = A.check_unsupported_imports_java(code)
    assert issues
    assert all(i.get("decidable") for i in issues)


# ---------------------------------------------------------------------------
# _block_is_fully_decidable_java
# ---------------------------------------------------------------------------

def test_decidable_true_when_all_exact_and_no_fuzzy():
    item = {
        "block": _block(),
        "matching_patterns": [],
        "scos_issues": [
            {"risk": 1.0, "reason": "avro unsupported", "category": "x", "decidable": True}
        ],
    }
    assert A._block_is_fully_decidable_java(item) is True


def test_decidable_false_when_fuzzy_match_present():
    item = {
        "block": _block(),
        "matching_patterns": [{"score": 0.9, "root_cause": "rc"}],
        "scos_issues": [{"risk": 1.0, "reason": "r", "decidable": True}],
    }
    assert A._block_is_fully_decidable_java(item) is False


def test_decidable_false_when_any_issue_not_decidable():
    item = {
        "block": _block(),
        "matching_patterns": [],
        "scos_issues": [
            {"risk": 1.0, "reason": "exact", "decidable": True},
            {"risk": 0.6, "reason": "behavioral"},  # no decidable flag
        ],
    }
    assert A._block_is_fully_decidable_java(item) is False


def test_decidable_false_when_no_issues():
    item = {"block": _block(), "matching_patterns": [], "scos_issues": []}
    assert A._block_is_fully_decidable_java(item) is False


# ---------------------------------------------------------------------------
# _build_decidable_result_java
# ---------------------------------------------------------------------------

def test_build_decidable_result_shape():
    item = {
        "block": _block(code='spark.read().format("avro")', ls=3, le=3),
        "matching_patterns": [],
        "scos_issues": [
            {"risk": 0.5, "reason": "low", "category": "A"},
            {"risk": 1.0, "reason": "avro unsupported", "category": "Unsupported Format",
             "how_to_fix": "use parquet", "ewi_code": "SPRKCNTSCL1000", "decidable": True},
        ],
    }
    row = A._build_decidable_result_java(Path("/x/Job.java"), item, risk_threshold=0.1)
    assert row is not None
    assert row["final_risk"] == 1.0
    assert row["root_cause"] == "avro unsupported"
    assert row["fix"] == "use parquet"
    assert row["category"] == "Unsupported Format"
    assert row["confidence"] == "HIGH"
    assert row["source"] == "trigger_decidable"
    assert row["ewi_code"] == "SPRKCNTSCL1000"
    assert row["language"] == "java"
    assert row["lines"] == "3-3"


def test_build_decidable_result_below_threshold_returns_none():
    item = {
        "block": _block(),
        "matching_patterns": [],
        "scos_issues": [{"risk": 0.2, "reason": "low", "decidable": True}],
    }
    assert A._build_decidable_result_java(Path("/x"), item, risk_threshold=0.5) is None


# ---------------------------------------------------------------------------
# _partition_decidable_blocks_java
# ---------------------------------------------------------------------------

def test_partition_splits_decidable_and_deferred():
    """Decidable blocks are decided; non-decidable ones are DEFERRED, not queued.

    The analyzer no longer has an LLM batch path, so ``remaining`` is always
    empty: every non-decidable block becomes a ``needs_adjudication`` row for the
    Phase 1.1 adjudicator (matching analyze_pyspark / analyze_scala).
    """
    decidable_item = {
        "block": _block(ls=1, le=1),
        "matching_patterns": [],
        "scos_issues": [{"risk": 1.0, "reason": "exact", "decidable": True}],
    }
    deferred_item = {
        "block": _block(ls=2, le=2),
        "matching_patterns": [],
        "scos_issues": [{"risk": 0.6, "reason": "behavioral"}],
    }
    decided, remaining = A._partition_decidable_blocks_java(
        [decidable_item, deferred_item], Path("/x"), risk_threshold=0.1
    )
    assert remaining == [], "there is no LLM batch path — nothing may be left over"
    by_kind = {r["kind"]: r for r in decided}
    assert set(by_kind) == {"standard", "needs_adjudication"}
    assert by_kind["standard"]["source"] == "trigger_decidable"
    deferred = by_kind["needs_adjudication"]
    assert deferred["source"] == "deferred_adjudication"
    assert deferred["adjudicated"] is False
    assert deferred["confidence"] == "UNADJUDICATED"
    # The adjudicator needs the raw evidence that used to go into the LLM prompt.
    assert [c["kind"] for c in deferred["deferred_candidates"]] == ["scos_issue"]


# ---------------------------------------------------------------------------
# analyze_file integration (no session => LLM never invoked)
# ---------------------------------------------------------------------------

def test_analyze_file_emits_decidable_without_llm(tmp_path):
    f = tmp_path / "Job.java"
    f.write_text(
        'Dataset<Row> df = spark.read().format("avro").load("/data");\n',
        encoding="utf-8",
    )
    rows = A.analyze_file(_StubRAG(), f, risk_threshold=0.1, session=None)
    avro = [r for r in rows if r.get("source") == "trigger_decidable"]
    assert avro, f"expected a decidable avro finding, got {rows}"
    assert avro[0]["confidence"] == "HIGH"
    assert avro[0]["final_risk"] >= 0.7


def test_analyze_file_behavioral_stays_off_decidable_path(tmp_path):
    f = tmp_path / "Beh.java"
    f.write_text(
        'Dataset<Row> y = df.select(col("x").cast("int"));\n',
        encoding="utf-8",
    )
    rows = A.analyze_file(_StubRAG(), f, risk_threshold=0.1, session=None)
    assert not any(r.get("source") == "trigger_decidable" for r in rows)


def test_analyze_file_unsupported_import_decidable(tmp_path):
    f = tmp_path / "Main.java"
    f.write_text(
        "import org.apache.spark.ml.Pipeline;\npublic class Main {}\n",
        encoding="utf-8",
    )
    rows = A.analyze_file(_StubRAG(), f, risk_threshold=0.1, session=None)
    decidable = [r for r in rows if r.get("source") == "trigger_decidable"]
    assert decidable, f"expected decidable ML import issue, got {rows}"


# ---------------------------------------------------------------------------
# Facts-path vs regex-fallback parity (SCOS_NO_AST_FACTS=1)
# ---------------------------------------------------------------------------

def test_facts_parity_unsupported_import(monkeypatch):
    """Facts-backed import detection must match regex on same code."""
    code = "import org.apache.spark.ml.Pipeline;\n"
    facts = {
        "imports": [{"ref": "org.apache.spark.ml.Pipeline", "line": 1}],
        "calls": [], "selects": [], "new_types": [], "spark_sql": [],
    }
    regex_issues = A.check_unsupported_imports_java(code)
    facts_issues = A.check_scos_issues_from_facts(facts)

    def _norm(i):
        return (i.get("category"), i.get("api") or i.get("format"), bool(i.get("decidable")))

    assert {_norm(i) for i in facts_issues} == {_norm(i) for i in regex_issues}


def test_facts_parity_unsupported_format():
    code = 'spark.read().format("avro").load("/path");'
    facts = {
        "imports": [],
        "calls": [
            {"method": "format", "recv_leaf": "read", "args": ["avro"], "line": 1},
            {"method": "load", "recv_leaf": "format", "args": ["/path"], "line": 1},
        ],
        "selects": [], "new_types": [], "spark_sql": [],
    }
    regex_issues = A.check_unsupported_formats_java(code)
    facts_issues = A.check_scos_issues_from_facts(facts)

    fmt_from_regex = {i.get("format") for i in regex_issues if i.get("decidable")}
    fmt_from_facts = {i.get("format") for i in facts_issues if i.get("decidable")}
    assert fmt_from_facts == fmt_from_regex


def test_safe_api_drop(tmp_path):
    """A block consisting entirely of safe relational APIs must be skipped."""
    f = tmp_path / "Safe.java"
    f.write_text(
        'Dataset<Row> r = df.select("a").filter(col("a").gt(1));\n',
        encoding="utf-8",
    )
    safe_apis = A.load_safe_apis()
    blk = A.JavaCodeBlock(
        code='df.select("a").filter(col("a").gt(1))',
        line_start=1, line_end=1, block_type="statement",
        functions=["select", "filter"],
    )

    class _ExplodingRAG:
        def predict_failure(self, code):
            raise AssertionError("RAG called for a safe block")

    result, block_data = A._process_single_block(
        blk, _ExplodingRAG(), f, 0.55, safe_apis, None
    )
    assert result is None and block_data is None, (
        "safe-only block should be dropped without querying RAG"
    )


def test_no_ast_facts_env_forces_regex(monkeypatch, tmp_path):
    """SCOS_NO_AST_FACTS=1 forces regex path — facts module not imported."""
    monkeypatch.setenv("SCOS_NO_AST_FACTS", "1")
    f = tmp_path / "Main.java"
    f.write_text(
        'Dataset<Row> df = spark.read().format("avro").load("/d");\n',
        encoding="utf-8",
    )
    # With no session no LLM is called; decidable avro should still be found
    # via the regex fallback because SCOS_NO_AST_FACTS=1 disables facts.
    rows = A.analyze_file(_StubRAG(), f, risk_threshold=0.1, session=None)
    avro = [r for r in rows if "avro" in str(r.get("root_cause", "")).lower()
            or r.get("source") == "trigger_decidable"]
    assert avro, f"expected avro finding via regex fallback, got {rows}"
