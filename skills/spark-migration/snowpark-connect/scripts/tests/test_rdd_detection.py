"""Unit tests for the RDD *detection* improvements in ``analyze_pyspark`` —
the ``RDD_EXCLUSIVE_METHODS`` ungating in ``has_rdd_usage`` and the file-scope
RDD import / type-annotation markers emitted by the block extractor.

These import ``analyze_pyspark`` directly, which pulls in the full SCOS
dependency stack (rag / snowflake). When that stack is absent (local dev
without the connectors installed) the whole module is skipped — CI runs it for
real, mirroring the existing ``test_decidability_gate`` / ``test_self_consistency``
suites.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_rdd_detection.py
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip(
    "snowflake.snowpark",
    reason="RDD detection tests need the full SCOS dependency stack (CI only)",
)

from analyze_pyspark import (  # noqa: E402
    RDD_EXCLUSIVE_METHODS,
    RDD_METHODS,
    RDD_NO_EQUIVALENT,
    RDD_PATTERNS,
    build_rdd_conversion_guidance,
    extract_code_blocks_from_source,
    has_rdd_usage,
    load_api_compatibility,
)

_DATA = Path(__file__).resolve().parents[1] / "data" / "api_compatibility.csv"


def _methods():
    _, methods = load_api_compatibility(_DATA)
    return methods


def _file_flagged(src: str) -> bool:
    """True if any extracted block of ``src`` is flagged as RDD usage."""
    return any(has_rdd_usage(b.code)[0] for b in extract_code_blocks_from_source(src, _methods()))


# --------------------------------------------------------------------------- #
# RDD_EXCLUSIVE_METHODS ungating: flagged WITHOUT a co-located .rdd/sc. token.
# This is the dataflow gap — an RDD bound to a variable / parameter and operated
# on in a separate statement.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "code",
    [
        "out = rdd.reduceByKey(add)",
        "x = data.map(f).groupByKey()",
        "kv = rdd.mapValues(lambda v: v + 1)",
        "idx = rdd.zipWithIndex()",
        "u = rdd.zipWithUniqueId()",
        "s = rdd.sortByKey()",
        "p = rdd.mapPartitions(fn)",
        "t = rdd.takeOrdered(5)",
        "rdd.saveAsTextFile('out')",
        "agg = rdd.aggregateByKey(0)(seq, comb)",
        # Aggregate recipes + §10 additional verified ops — all RDD-exclusive,
        # flagged WITHOUT a co-located .rdd/sc. token.
        "n, s, ss = data.treeAggregate(z, seq, comb)",
        "v = data.treeReduce(add)",
        "m = pairs.collectAsMap()",
        "c = data.countApprox(1000, 0.95)",
        "d = data.countApproxDistinct(0.05)",
        "mn = amounts.meanApprox(1000)",
        "tot = data.sumApprox(1000)",
        "r = data.collectWithJobGroup('etl', 'load')",
        "p2 = data.mapPartitionsWithSplit(fn)",
        "q = kv.repartitionAndSortWithinPartitions(8)",
        "data.saveAsPickleFile('/p')",
        "data.saveAsObjectFile('/p')",
        "lvl = data.getStorageLevel()",
        "dbg = data.toDebugString()",
    ],
)
def test_exclusive_methods_flagged_without_token(code):
    is_rdd, why = has_rdd_usage(code)
    assert is_rdd, f"expected RDD flag for {code!r}"
    assert "RDD-exclusive" in why


# --------------------------------------------------------------------------- #
# Ambiguous names (DataFrame homonyms) stay gated — no false positives.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "code",
    [
        'df.select("a").filter("a > 1")',
        "n = df.count()",
        "rows = df.collect()",
        "d = df.distinct()",
        "u = df1.union(df2)",
        "j = df1.join(df2, 'k')",
        "cfg = settings.values()",  # dict.values(), not RDD
        # §10 DataFrame homonyms stay GATED (kept out of the exclusive set) so
        # they don't false-fire on plain DataFrame code.
        "g = df.groupBy('k').count()",              # DataFrame.groupBy
        "out = df.write.partitionBy('c').parquet('p')",  # DataFrameWriter.partitionBy
        "d = data.toDF(['a', 'b'])",                # toDF: route via the .rdd hop, not a broad token
        "ctx = obj.context",                         # bare attribute, not RDD-exclusive
    ],
)
def test_ambiguous_methods_not_flagged_without_token(code):
    assert not has_rdd_usage(code)[0], f"false positive on {code!r}"


@pytest.mark.parametrize(
    "code",
    [
        "x = df.rdd.map(f)",
        "y = sc.parallelize(d).collect()",
    ],
)
def test_ambiguous_methods_flagged_with_rdd_token(code):
    assert has_rdd_usage(code)[0]


def test_exclusive_is_subset_of_rdd_methods():
    assert RDD_EXCLUSIVE_METHODS <= set(RDD_METHODS)


@pytest.mark.parametrize(
    "token",
    [
        "treeAggregate", "treeReduce", "collectAsMap", "countApprox",
        "countApproxDistinct", "meanApprox", "sumApprox", "collectWithJobGroup",
        "mapPartitionsWithSplit", "repartitionAndSortWithinPartitions",
        "saveAsPickleFile", "getStorageLevel", "toDebugString",
    ],
)
def test_new_aggregate_and_section10_tokens_are_exclusive(token):
    """The 13 unambiguous aggregate/§10 additions are in BOTH detection sets."""
    assert token in RDD_METHODS
    assert token in RDD_EXCLUSIVE_METHODS


@pytest.mark.parametrize(
    "code",
    [
        "acc = sc.collectionAccumulator()",
        "class MinParam(AccumulatorParam):\n    pass",
        "class Hist(AccumulatorV2):\n    pass",
        # Modern factory forms + PascalCase classes (§12.1/§12.2).
        "n = sc.longAccumulator()",
        "d = sc.doubleAccumulator('bytes')",
        "acc: LongAccumulator = make()",
        "acc: DoubleAccumulator = make()",
    ],
)
def test_accumulator_patterns_flagged(code):
    """Accumulator constructors/base classes/factory methods are flagged as RDD
    usage (routed to the §12 DataFrame-aggregation workaround, not a blanket
    TODO)."""
    assert has_rdd_usage(code)[0], f"expected RDD flag for {code!r}"


def test_section10_op_is_convertible_not_todo():
    """A §10 op with a DataFrame workaround must direct a rewrite, not a TODO."""
    g = build_rdd_conversion_guidance("m = df.rdd.collectAsMap()")
    assert g["rdd_class"] == "convertible"
    assert g["suggested_fixer_action"], "collectAsMap has a workaround -> rewrite directive"
    assert "collectAsMap" in g["fix"]
    assert "TODO" not in g["suggested_fixer_action"]


def test_saveasobjectfile_is_convertible_not_todo():
    """saveAsObjectFile is a [Partial] parquet/table round-trip (§16.11), the SAME
    verdict as its sibling saveAsPickleFile — it must direct a rewrite, NOT be
    left as a no-equivalent TODO (the pre-fix inconsistency this guards against)."""
    g = build_rdd_conversion_guidance('df.rdd.saveAsObjectFile("/p")')
    assert g["rdd_class"] == "convertible"
    assert g["suggested_fixer_action"], "saveAsObjectFile has a round-trip workaround -> rewrite directive"
    assert "saveAsObjectFile" in g["fix"]
    assert "TODO" not in g["suggested_fixer_action"]


def test_saveasobjectfile_and_picklefile_share_verdict():
    """The two sibling save ops the guide treats identically must classify
    identically in code (both convertible)."""
    obj = build_rdd_conversion_guidance('df.rdd.saveAsObjectFile("/p")')
    pkl = build_rdd_conversion_guidance('df.rdd.saveAsPickleFile("/p")')
    assert obj["rdd_class"] == pkl["rdd_class"] == "convertible"


# --------------------------------------------------------------------------- #
# File-scope markers: RDD imports + RDD type annotations become flagged blocks
# even though they form no assignment/expression block.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "src",
    [
        "from pyspark import RDD\n",
        "from pyspark.rdd import RDD, PipelinedRDD\n",
        "import pyspark.rdd\n",
        "my_rdd: RDD = build()\n",
        "def process(x: RDD) -> RDD:\n    return x\n",
        "def to_pairs(items) -> RDD:\n    return items\n",
    ],
)
def test_file_scope_imports_and_annotations_detected(src):
    assert _file_flagged(src), f"no RDD-flagged block for {src!r}"


# --------------------------------------------------------------------------- #
# Regressions: plain DataFrame code and a bare ``import pyspark`` must NOT be
# flagged as RDD (no spurious markers, gate intact).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "src",
    [
        'import pyspark\ndf = spark.range(10)\nout = df.select("id").filter("id > 1")\n',
        'res = df.groupBy("k").count()\n',
        'from pyspark.sql import functions as F\nx = df.withColumn("y", F.col("z"))\n',
    ],
)
def test_plain_dataframe_not_flagged(src):
    assert not _file_flagged(src), f"unexpected RDD flag for {src!r}"


# --------------------------------------------------------------------------- #
# Actionable conversion guidance: the RDD issue must NAME the detected op(s) and
# direct the fixer to rewrite convertible ops (never TODO), reserving TODOs for
# the genuinely no-equivalent set — never the old generic "Convert to DataFrame
# operations. RDD operations are not supported" string.
# --------------------------------------------------------------------------- #
def test_convertible_rdd_op_directs_rewrite_not_todo():
    g = build_rdd_conversion_guidance("out = df.rdd.reduceByKey(add)")
    assert g["rdd_class"] == "convertible"
    assert g["suggested_fixer_action"], "convertible op must supply a rewrite directive"
    # Names the op, points at the reference, and directs a rewrite (not a punt).
    assert "reduceByKey" in g["fix"], g["fix"]
    assert "rdd-conversion.md" in g["fix"], g["fix"]
    assert "APPLY the rewrite" in g["fix"], g["fix"]
    assert "TODO" not in g["suggested_fixer_action"], "the action must be a rewrite, not a TODO"
    assert "are not supported" not in g["fix"].lower()


def test_no_equivalent_rdd_op_gets_todo_and_no_action():
    g = build_rdd_conversion_guidance("x = rdd.glom()")
    assert g["rdd_class"] == "no_equivalent"
    assert g["suggested_fixer_action"] is None, "no-equivalent op must not supply a rewrite"
    assert "TODO" in g["fix"]
    assert "glom" in g["fix"]


def test_mixed_rdd_block_rewrites_convertible_and_todos_rest():
    g = build_rdd_conversion_guidance("a = rdd.reduceByKey(add)\nb = rdd.glom()")
    assert g["rdd_class"] == "mixed"
    assert g["suggested_fixer_action"], "mixed block must still direct the convertible rewrite"
    # Both ops named; convertible rewritten, no-equivalent TODO'd.
    assert "reduceByKey" in g["fix"] and "glom" in g["fix"], g["fix"]
    assert "TODO" in g["fix"]


def test_bare_rdd_attribute_gets_removable_hop_guidance():
    g = build_rdd_conversion_guidance("pairs = df.rdd\n")
    assert g["rdd_class"] == "convertible"
    assert ".rdd" in g["fix"]
    assert "TODO" not in g["suggested_fixer_action"], "a bare .rdd hop is removable, not a TODO"
    assert "are not supported" not in g["fix"].lower()


def test_no_equivalent_set_is_subset_of_detected_names():
    """Belt-and-suspenders to the ast-based sync test: exercised against the
    imported module objects. Every no-equivalent token must be a real RDD name."""
    known = {m.lower() for m in RDD_METHODS} | {
        p.strip().lstrip(".").rstrip("(").lower() for p in RDD_PATTERNS
    }
    unknown = sorted(t for t in RDD_NO_EQUIVALENT if t not in known)
    assert not unknown, f"RDD_NO_EQUIVALENT has unknown tokens: {unknown}"
