"""Tests for analyze_scala._classify_rdd_usage (R1 bucket-aware RDD guidance).

Bucket A (unsupported, manual EWI): .rdd accessor, partition introspection,
mapPartitions/foreachPartition, SparkContext file APIs — no DataFrame equivalent.
Bucket B (convertible): sc.parallelize / sc.emptyRDD and key-based pair ops.
"""

from __future__ import annotations

import pytest

import analyze_scala as a


def test_rdd_accessor_is_unsupported():
    g = a._classify_rdd_usage("val n = df2.rdd.getNumPartitions")
    assert g["unsupported"] is True
    assert "SPRKCNTSCL1500" in g["fix"]
    assert "Do NOT fabricate" in g["fix"]


def test_partitions_and_mappartitions_unsupported():
    assert a._classify_rdd_usage("df.rdd.partitions.length")["unsupported"] is True
    assert a._classify_rdd_usage("df.rdd.mapPartitions(it => it)")["unsupported"] is True


def test_parallelize_is_convertible():
    g = a._classify_rdd_usage("val rdd = spark.sparkContext.parallelize(data)")
    assert g["unsupported"] is False
    assert "createDataFrame" in g["fix"]
    assert "Tuple1" in g["fix"]  # warns against the wrong form


def test_emptyrdd_is_convertible():
    g = a._classify_rdd_usage("spark.createDataFrame(spark.sparkContext.emptyRDD[Row], schema)")
    assert g["unsupported"] is False
    assert "asJava" in g["fix"]


def test_pairop_is_convertible_with_agg_guidance():
    g = a._classify_rdd_usage(
        "val rdd = spark.sparkContext.parallelize(data)\nval r2 = rdd.reduceByKey(_ + _)"
    )
    assert g["unsupported"] is False
    assert "groupBy" in g["fix"]


def test_unsupported_wins_over_convertible_when_both_present():
    # A block that both parallelizes AND touches .rdd must be treated as manual.
    g = a._classify_rdd_usage(
        "val rdd = spark.sparkContext.parallelize(data)\nprintln(rdd.rdd.getNumPartitions)"
    )
    assert g["unsupported"] is True


# --------------------------------------------------------------------------- #
# §10 "additional verified RDD operations" + aggregate recipes. These are the
# newly-added, genuinely RDD-exclusive names (no DataFrame homonym). They live
# in RDD_METHODS (the gated detection set) — mirror of the Python
# RDD_EXCLUSIVE_METHODS additions. Frozen L3 token set (drift guard).
# --------------------------------------------------------------------------- #
_NEW_EXCLUSIVE_TOKENS = [
    "treeAggregate", "treeReduce", "collectAsMap", "countApprox",
    "countApproxDistinct", "meanApprox", "sumApprox",
    "repartitionAndSortWithinPartitions", "toDebugString",
]


@pytest.mark.parametrize("token", _NEW_EXCLUSIVE_TOKENS)
def test_new_exclusive_tokens_are_detected_rdd_methods(token):
    """The 9 aggregate/§10 additions must be present in the analyzer's
    RDD_METHODS detection set so they surface RDD guidance when gated."""
    assert token in a.RDD_METHODS


# DataFrame homonyms — names that also exist on DataFrame/DataFrameWriter or are
# bare attributes. They must stay OUT of any exclusive / no-equivalent path: a
# plain DataFrame call carrying one of these names (with no .rdd / SparkContext
# token) must NOT be flagged as RDD usage. Mirror of the Python
# test_ambiguous_methods_not_flagged_without_token guard.
@pytest.mark.parametrize(
    "code",
    [
        'val g = df.groupBy("k").count()',            # DataFrame.groupBy
        'df.write.partitionBy("c").parquet("p")',     # DataFrameWriter.partitionBy
        'val d = data.toDF("a", "b")',                # toDF: routed via the .rdd hop, not a broad token
        "val ctx = obj.context",                       # bare attribute, not RDD-exclusive
        "val r = xs.reduce(_ + _)",                   # Scala collection reduce
        "val f = xs.fold(0)(_ + _)",                  # Scala collection fold
        "val ag = ds.aggregate(z)(sq, cb)",           # Dataset.aggregate homonym
        "val gk = ds.groupByKey(_.k)",                # Dataset.groupByKey homonym
    ],
)
def test_homonyms_not_flagged_without_rdd_token(code):
    flagged, _ = a.has_rdd_usage(code)
    assert flagged is False, f"homonym unexpectedly flagged as RDD usage: {code!r}"


def test_treeaggregate_is_convertible_workaround():
    g = a._classify_rdd_usage("val r = data.treeAggregate(0)(seqOp, combOp)")
    assert g["unsupported"] is False
    assert "df.agg" in g["fix"]
    assert "depth" in g["fix"]           # drop-the-depth-arg guidance
    assert "Do NOT fabricate" not in g["fix"]  # NOT the no-equivalent branch


def test_treereduce_is_convertible_workaround():
    g = a._classify_rdd_usage("val r = data.treeReduce(_ + _)")
    assert g["unsupported"] is False
    assert "df.agg" in g["fix"]
    assert "Do NOT fabricate" not in g["fix"]


# Accumulators are CONVERTIBLE ([Workaround]) — a driver-side
# count/sum/min/max/collect accumulator maps to a DataFrame agg (§6.10-6.14) —
# NOT a blanket no-equivalent TODO. There is no sc.accumulator / AccumulatorParam
# API in Scala, so detection keys off the real factory forms + class names only.
@pytest.mark.parametrize(
    "code",
    [
        'val acc = sc.longAccumulator("c")',
        'val acc = spark.sparkContext.doubleAccumulator("bytes")',
        "val acc = sc.collectionAccumulator()",
        "val acc = new LongAccumulator()",
        "val acc = new DoubleAccumulator()",
        "val acc = new CollectionAccumulator[String]()",
        "class Hist extends AccumulatorV2[Long, Long] {}",
    ],
)
def test_accumulator_forms_are_convertible_not_no_equivalent(code):
    g = a._classify_rdd_usage(code)
    assert g["unsupported"] is False, f"accumulator wrongly no-equivalent: {code!r}"
    assert "accumulator" in g["fix"].lower()
    assert "agg" in g["fix"]                    # routed to DataFrame aggregation
    assert "Do NOT fabricate" not in g["fix"]   # NOT the no-equivalent branch


def test_saveasobjectfile_writer_is_partial():
    """saveAsObjectFile(path) as a WRITER is a [Partial] parquet/table round-trip,
    not a no-equivalent TODO. (The reader-side sc.objectFile stays no-equivalent,
    and a .rdd-qualified use routes to the manual/unsupported branch.)"""
    g = a._classify_rdd_usage('data.saveAsObjectFile("/p")')
    assert g["unsupported"] is False
    assert "[Partial]" in g["fix"]
    assert "saveAsObjectFile" in g["fix"]
    assert "Do NOT fabricate" not in g["fix"]


def test_saveassequencefile_stays_no_equivalent():
    """Its sibling saveAsSequenceFile has no DataFrame equivalent and stays
    unsupported — guards against over-broadening the [Partial] verdict."""
    g = a._classify_rdd_usage('data.saveAsSequenceFile("/p")')
    assert g["unsupported"] is True
