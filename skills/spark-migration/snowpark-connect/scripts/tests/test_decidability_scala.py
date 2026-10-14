"""Tests for the KB-decidability bypass in ``analyze_scala`` (Row A parity).

Fully-decidable blocks — those whose only signals are exact unsupported
triggers (unsupported import/format/module or unsupported Dataset API) with no
fuzzy RAG evidence — are emitted deterministically and must NOT be sent to the
batch LLM. Context-dependent signals (behavioral differences, UDFs, no-ops) and
any fuzzy ``matching_patterns`` keep the block on the LLM path.
"""
from __future__ import annotations

from pathlib import Path

from analyze_scala import (
    ScalaCodeBlock,
    _block_is_fully_decidable_scala,
    _build_decidable_result_scala,
    _partition_decidable_blocks_scala,
    analyze_file,
    check_behavioral_differences_scala,
    check_hive_ddl_patterns_scala,
    check_noop_apis_scala,
    check_udf_patterns_scala,
    check_unsupported_df_apis_scala,
    check_unsupported_formats_scala,
    check_unsupported_imports_scala,
)


class _StubRAG:
    """Offline RAG stub: returns no fuzzy matches so blocks rely solely on the
    deterministic ``scos_issues`` produced by the ``check_*`` functions."""

    def predict_failure(self, code: str) -> dict:
        return {"similar_patterns": []}


def _block(code: str = "x", ls: int = 1, le: int = 1) -> ScalaCodeBlock:
    return ScalaCodeBlock(code=code, line_start=ls, line_end=le, block_type="expr")


# --- check_* emitters tag decidability correctly ---------------------------


def test_exact_unsupported_emitters_tag_decidable_true():
    imp = check_unsupported_imports_scala(
        "import com.hortonworks.spark.sql.hive.llap.HiveWarehouseSession"
    )
    fmt = check_unsupported_formats_scala('spark.read.format("avro").load("/p")')
    hive = check_hive_ddl_patterns_scala(
        "val s = SparkSession.builder().enableHiveSupport().getOrCreate()"
    )
    dfapi = check_unsupported_df_apis_scala("df.checkpoint()")
    assert imp and all(i.get("decidable") for i in imp)
    assert fmt and all(i.get("decidable") for i in fmt)
    assert hive and all(i.get("decidable") for i in hive)
    assert dfapi and all(i.get("decidable") for i in dfapi)


def test_context_dependent_emitters_not_decidable():
    bd = check_behavioral_differences_scala('df.select(col("x").cast(IntegerType))')
    udf = check_udf_patterns_scala("spark.udf.register(\"f\", myFn _)")
    noop = check_noop_apis_scala("df.hint(\"broadcast\")")
    # behavioral/udf/noop are context-dependent: must never be tagged decidable.
    assert bd and not any(i.get("decidable") for i in bd)
    assert udf and not any(i.get("decidable") for i in udf)
    assert noop and not any(i.get("decidable") for i in noop)


# --- _block_is_fully_decidable_scala ---------------------------------------


def test_decidable_true_when_all_exact_and_no_fuzzy():
    item = {
        "block": _block(),
        "matching_patterns": [],
        "scos_issues": [
            {"risk": 1.0, "reason": "avro unsupported", "category": "x", "decidable": True}
        ],
    }
    assert _block_is_fully_decidable_scala(item) is True


def test_decidable_false_when_fuzzy_match_present():
    item = {
        "block": _block(),
        "matching_patterns": [{"score": 0.9, "root_cause": "rc"}],
        "scos_issues": [{"risk": 1.0, "reason": "r", "decidable": True}],
    }
    assert _block_is_fully_decidable_scala(item) is False


def test_decidable_false_when_any_issue_not_decidable():
    item = {
        "block": _block(),
        "matching_patterns": [],
        "scos_issues": [
            {"risk": 1.0, "reason": "exact", "decidable": True},
            {"risk": 0.6, "reason": "behavioral"},  # no decidable flag
        ],
    }
    assert _block_is_fully_decidable_scala(item) is False


def test_decidable_false_when_no_issues():
    item = {"block": _block(), "matching_patterns": [], "scos_issues": []}
    assert _block_is_fully_decidable_scala(item) is False


# --- _build_decidable_result_scala -----------------------------------------


def test_build_decidable_result_shape():
    item = {
        "block": _block(code="spark.read.format(\"avro\")", ls=3, le=3),
        "matching_patterns": [],
        "scos_issues": [
            {"risk": 0.5, "reason": "low", "category": "A"},
            {"risk": 1.0, "reason": "avro unsupported", "category": "Unsupported Format",
             "how_to_fix": "use parquet", "ewi_code": "SPRKX", "decidable": True},
        ],
    }
    row = _build_decidable_result_scala(Path("/x/Job.scala"), item, risk_threshold=0.1)
    assert row is not None
    assert row["final_risk"] == 1.0  # picks the highest-risk issue
    assert row["root_cause"] == "avro unsupported"
    assert row["fix"] == "use parquet"
    assert row["suggested_fix"] == "use parquet"
    assert row["category"] == "Unsupported Format"
    assert row["confidence"] == "HIGH"
    assert row["source"] == "trigger_decidable"
    assert row["ewi_code"] == "SPRKX"
    assert row["language"] == "scala"
    assert row["lines"] == "3-3"


def test_build_decidable_result_below_threshold_returns_none():
    item = {
        "block": _block(),
        "matching_patterns": [],
        "scos_issues": [{"risk": 0.2, "reason": "low", "decidable": True}],
    }
    assert _build_decidable_result_scala(Path("/x"), item, risk_threshold=0.5) is None


# --- _partition_decidable_blocks_scala -------------------------------------


def test_partition_splits_decidable_and_deferred():
    decidable_item = {
        "block": _block(ls=1, le=1),
        "matching_patterns": [],
        "scos_issues": [{"risk": 1.0, "reason": "exact", "decidable": True}],
    }
    non_decidable_item = {
        "block": _block(ls=2, le=2),
        "matching_patterns": [],
        "scos_issues": [{"risk": 0.6, "reason": "behavioral"}],
    }
    decided, remaining = _partition_decidable_blocks_scala(
        [decidable_item, non_decidable_item], Path("/x"), risk_threshold=0.1
    )
    assert len(decided) == 2
    assert remaining == []
    decidable_row = next(r for r in decided if r.get("source") == "trigger_decidable")
    deferred_row = next(r for r in decided if r.get("kind") == "needs_adjudication")
    assert decidable_row["lines"] == "1-1"
    assert deferred_row["lines"] == "2-2"
    assert deferred_row["source"] == "deferred_adjudication"
    assert deferred_row["adjudicated"] is False


# --- analyze_file integration (fully deterministic, no LLM ever) -----------


def test_analyze_file_emits_decidable_without_llm(tmp_path):
    f = tmp_path / "Job.scala"
    f.write_text('val df = spark.read.format("avro").load("/data")\n', encoding="utf-8")
    rows = analyze_file(_StubRAG(), f, risk_threshold=0.1)
    avro = [r for r in rows if r.get("source") == "trigger_decidable"]
    assert avro, f"expected a decidable avro finding, got {rows}"
    assert avro[0]["confidence"] == "HIGH"
    assert avro[0]["final_risk"] >= 0.7


def test_analyze_file_behavioral_deferred_not_decidable(tmp_path):
    # A behavioral-difference pattern is context-dependent: it must NOT be
    # emitted via the decidable bypass (no trigger_decidable source) — it is
    # deferred as needs_adjudication for the Phase 1.1 adjudicator instead.
    f = tmp_path / "Beh.scala"
    f.write_text('val y = df.select(col("x").cast(IntegerType))\n', encoding="utf-8")
    rows = analyze_file(_StubRAG(), f, risk_threshold=0.1)
    assert not any(r.get("source") == "trigger_decidable" for r in rows)
