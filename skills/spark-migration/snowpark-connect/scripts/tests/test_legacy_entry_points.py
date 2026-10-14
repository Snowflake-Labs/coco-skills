"""Unit tests for legacy SQL/Hive entry-point detection in analyze_pyspark.py.

Covers `check_legacy_entry_points` (sqlContext / SQLContext / HiveContext) and
its EWI resolution to SPRKCNTPY3500 via the python mapping CSV.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_legacy_entry_points.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import analyze_pyspark as a  # noqa: E402
import generate_scos_reports as g  # noqa: E402


def _apis(code: str):
    return {i["api"] for i in a.check_legacy_entry_points(code)}


# --------------------------------------------------------------------------
# Detection — positive
# --------------------------------------------------------------------------


def test_detects_sqlcontext_sql():
    issues = a.check_legacy_entry_points('df = sqlContext.sql("SELECT 1")')
    assert len(issues) == 1
    assert issues[0]["api"] == "sqlContext"
    assert issues[0]["category"] == "Spark Session Element"
    assert issues[0]["risk"] >= 0.7


def test_detects_sqlcontext_read():
    assert _apis("rows = sqlContext.read.parquet('@stage/x')") == {"sqlContext"}


def test_detects_sqlcontext_constructor_and_import():
    assert "SQLContext" in _apis("from pyspark.sql import SQLContext")
    assert "SQLContext" in _apis("sqlContext = SQLContext(sc)")


def test_detects_hivecontext():
    assert "HiveContext" in _apis("from pyspark.sql import HiveContext")
    assert "HiveContext" in _apis("hc = HiveContext(sc)")


# --------------------------------------------------------------------------
# Detection — negative (no false positives)
# --------------------------------------------------------------------------


def test_plain_spark_sql_not_flagged():
    assert a.check_legacy_entry_points('spark.sql("SELECT 1")') == []


def test_substring_not_flagged():
    # `sqlContext` as a bare substring inside another identifier must not match;
    # detection requires attribute access (`sqlContext.`).
    assert a.check_legacy_entry_points("my_sqlContextHelper = 1") == []
    assert a.check_legacy_entry_points("sqlContext = build()  # bare assign") == []


def test_spark_conf_not_flagged():
    assert a.check_legacy_entry_points('spark.conf.set("spark.sql.ansi.enabled", "true")') == []


# --------------------------------------------------------------------------
# EWI resolution — sqlContext/HiveContext -> SPRKCNTPY3500, no regression on
# the existing SparkSession -> SPRKCNTPY1001 rule.
# --------------------------------------------------------------------------


def test_ewi_resolves_to_3500():
    m = g.load_ewi_mapping("python")
    for desc in (
        "sqlContext is not available; sqlContext.sql -> spark.sql",
        "HiveContext was deprecated in Spark 2.0",
        "SQLContext is unavailable in Spark Connect",
    ):
        assert g.resolve_ewi_code(m, "Spark Session Element", desc, "")["ewi_code"] == "SPRKCNTPY3500"


def test_sparksession_rule_not_regressed():
    m = g.load_ewi_mapping("python")
    r = g.resolve_ewi_code(m, "SparkSession", "SparkSession creation replaced", "")
    assert r["ewi_code"] == "SPRKCNTPY1001"
