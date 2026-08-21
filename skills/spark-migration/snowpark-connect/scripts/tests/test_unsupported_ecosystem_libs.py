"""Unit tests for the unsupported-Spark-ecosystem-library detector and the
"HARD RISK FLOOR" prompt rule in ``analyze_pyspark``.

Regression these guard against: a block using an unsupported ecosystem library
(GraphFrames, Spark NLP, Mosaic, spark-xgboost, Koalas, SynapseML, Petastorm)
carries no curated ``kb_rules`` trigger and is not caught by the pyspark.ml /
JVM detectors. Before this detector, such a block produced no rule-based issue
and — absent a fuzzy RAG match — was dropped before the LLM pass, so the
unsupported code shipped un-migrated (observed live: a whole GraphFrames cell
received zero findings). The detector forces the block into the analysis pass;
the prompt rule then assigns it high risk.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from analyze_pyspark import (  # noqa: E402
    check_unknown_third_party_imports,
    check_unsupported_ecosystem_libs,
    extract_code_blocks_from_source,
    _is_reviewable_import,
)


def _apis(code: str) -> set[str]:
    return {i["api"] for i in check_unsupported_ecosystem_libs(code)}


def _max_risk(code: str) -> float:
    issues = check_unsupported_ecosystem_libs(code)
    return max((i["risk"] for i in issues), default=0.0)


# ---------------------------------------------------------------------------
# GraphFrames — the observed miss
# ---------------------------------------------------------------------------

def test_graphframes_import_is_flagged_high() -> None:
    code = "from graphframes import GraphFrame\ng = GraphFrame(v, e)"
    assert "graphframes" in _apis(code)
    assert _max_risk(code) >= 0.9


def test_graphframes_usage_without_local_import_is_flagged() -> None:
    # Import lives in another cell; usage tokens must still trigger.
    code = "g = GraphFrame(vertices_df, edges_df)\npr = g.pageRank(maxIter=10)"
    apis = _apis(code)
    assert any("graphframes" in a for a in apis)
    assert _max_risk(code) >= 0.9


def test_graphframes_methods_trigger() -> None:
    for snippet in [
        "res = g.connectedComponents()",
        "sp = g.shortestPaths(landmarks=['a'])",
        "lp = g.labelPropagation(maxIter=5)",
    ]:
        assert _max_risk(snippet) >= 0.9, snippet


# ---------------------------------------------------------------------------
# Other unsupported ecosystem libraries
# ---------------------------------------------------------------------------

def test_various_ecosystem_imports_are_flagged() -> None:
    cases = {
        "import sparknlp": "sparknlp",
        "import mosaic": "mosaic",
        "from sparkxgb import XGBoostClassifier": "sparkxgb",
        "import databricks.koalas as ks": "databricks.koalas",
        "import pyspark.pandas as ps": "pyspark.pandas",
        "import synapse.ml": "synapse.ml",
        "import petastorm": "petastorm",
    }
    for code, expected in cases.items():
        assert expected in _apis(code), code
        assert _max_risk(code) >= 0.9


# ---------------------------------------------------------------------------
# Precision — plain PySpark must NOT be flagged
# ---------------------------------------------------------------------------

def test_plain_pyspark_dataframe_not_flagged() -> None:
    code = (
        "from pyspark.sql import functions as F\n"
        "df = spark.table('sales.orders')\n"
        "agg = df.filter(F.col('amount') > 0).groupBy('region')"
        ".agg(F.sum('amount').alias('total')).orderBy(F.desc('total'))\n"
        "agg.show()"
    )
    assert check_unsupported_ecosystem_libs(code) == []


def test_plain_spark_sql_not_flagged() -> None:
    code = "spark.sql('SELECT region, COUNT(*) AS n FROM sales.orders GROUP BY region').show()"
    assert check_unsupported_ecosystem_libs(code) == []


# ---------------------------------------------------------------------------
# Unknown-import fail-safe (the long tail)
# ---------------------------------------------------------------------------

def test_is_reviewable_import() -> None:
    # stdlib + curated-safe third party + pyspark/snowflake => not reviewable
    for safe in ["os", "sys", "json", "numpy", "pandas", "sklearn",
                 "pyspark.sql", "snowflake.snowpark", "requests"]:
        assert _is_reviewable_import(safe) is False, safe
    # unknown / proprietary => reviewable
    for review in ["acme_spark_ext", "databricks.sdk", "some_internal_lib"]:
        assert _is_reviewable_import(review) is True, review


def test_unknown_import_flagged_as_review() -> None:
    issues = check_unknown_third_party_imports("from acme_spark_ext import TurboJoiner")
    assert len(issues) == 1
    assert issues[0]["risk"] == 0.4
    assert issues[0]["category"] == "Unknown Dependency (review)"


def test_unknown_import_ignores_safe_and_stdlib() -> None:
    for code in ["import numpy as np", "from pandas import DataFrame",
                 "import os, json", "from pyspark.sql import functions"]:
        assert check_unknown_third_party_imports(code) == [], code


def test_unknown_import_skips_known_unsupported_to_avoid_double_flag() -> None:
    # graphframes is owned by the ecosystem detector, not the unknown fail-safe.
    assert check_unknown_third_party_imports("from graphframes import GraphFrame") == []


# ---------------------------------------------------------------------------
# Import-marker block extraction — reviewable imports must become blocks
# ---------------------------------------------------------------------------

def test_reviewable_import_becomes_a_block() -> None:
    blocks = extract_code_blocks_from_source(
        "from acme_spark_ext import TurboJoiner\nout = TurboJoiner(spark).run(df)", None
    )
    codes = [b.code for b in blocks]
    assert any("from acme_spark_ext import TurboJoiner" in c for c in codes)


def test_safe_and_stdlib_imports_do_not_become_blocks() -> None:
    for src in ["import numpy as np\nx = np.array([1])", "import os\np = os.getcwd()"]:
        blocks = extract_code_blocks_from_source(src, None)
        assert all("import" not in b.block_type for b in blocks), src

