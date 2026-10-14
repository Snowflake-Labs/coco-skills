"""Unit tests for the RDD-handling LibCST recipes under ``scripts/recipes``.

Each recipe is exercised for: a positive trigger (rewrites/annotates and records
an edit), a negative case (leaves non-matching code untouched), and idempotency
(applying twice yields identical source). No sqlite is needed -- ``record_edit``
is a no-op without ``$SCOS_FACTS_DB`` and ``RecipeResult.edits`` is populated in
memory regardless.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_rdd_recipes.py
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from recipes import _common  # provided on sys.path via pyproject pythonpath

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"


def _load(name: str):
    return _common.load_recipe_module(str(_RECIPES_DIR / name))


def _apply(name: str, source: str):
    return _load(name).apply(source, file="t.py")


def _code_only(source: str) -> str:
    """Drop comment-only lines so assertions about removed tokens aren't
    tripped by the explanatory ``# SCOS:`` comment (which names the old API)."""
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )


def _assert_idempotent(name: str, source: str) -> str:
    """Apply twice; assert the second pass is a no-op. Return rewritten source."""
    first = _apply(name, source).source
    second = _apply(name, first).source
    assert second == first, f"{name} not idempotent:\n{first!r}\n!=\n{second!r}"
    return first


# --------------------------------------------------------------------------- #
# df_rdd_passthrough_rewrite
# --------------------------------------------------------------------------- #
NAME_PASSTHROUGH = "df_rdd_passthrough_rewrite"


@pytest.mark.parametrize(
    "source, expected_fragment",
    [
        ("empty = df.rdd.isEmpty()\n", "df.isEmpty()"),
        ("if df.rdd.isEmpty():\n    pass\n", "if df.isEmpty():"),
        ("it = df.rdd.toLocalIterator()\n", "df.toLocalIterator()"),
    ],
)
def test_passthrough_rewrites(source, expected_fragment):
    res = _apply(NAME_PASSTHROUGH, source)
    assert expected_fragment in res.source
    assert ".rdd." not in _code_only(res.source)
    assert len(res.edits) == 1
    _assert_idempotent(NAME_PASSTHROUGH, source)


@pytest.mark.parametrize(
    "source",
    [
        "x = df.rdd.map(lambda r: r)\n",          # map: no DataFrame counterpart
        "n = df.rdd.getNumPartitions()\n",        # handled by a different recipe
        "e = df.isEmpty()\n",                     # already a DataFrame call
        "e = other.isEmpty()\n",                  # not via .rdd
    ],
)
def test_passthrough_leaves_other_forms(source):
    res = _apply(NAME_PASSTHROUGH, source)
    assert res.source == source
    assert res.edits == []


# --------------------------------------------------------------------------- #
# rdd_persist_to_cache_rewrite
# --------------------------------------------------------------------------- #
NAME_PERSIST = "rdd_persist_to_cache_rewrite"


@pytest.mark.parametrize(
    "source",
    [
        "out = df.rdd.persist(StorageLevel.MEMORY_AND_DISK)\n",
        "out = df.rdd.persist()\n",
    ],
)
def test_persist_rewrites_rdd_receiver(source):
    res = _apply(NAME_PERSIST, source)
    assert "df.cache()" in res.source
    assert "persist" not in _code_only(res.source)
    assert ".rdd" not in _code_only(res.source)
    assert len(res.edits) == 1
    _assert_idempotent(NAME_PERSIST, source)


@pytest.mark.parametrize(
    "source",
    [
        "out = df.persist(StorageLevel.MEMORY_AND_DISK)\n",  # bare DataFrame persist: accepted
        "out = df.cache()\n",                                # already cache
        "out = sc.parallelize([1]).persist()\n",            # receiver is a Call, not .rdd
    ],
)
def test_persist_leaves_non_rdd(source):
    res = _apply(NAME_PERSIST, source)
    assert res.source == source
    assert res.edits == []


# --------------------------------------------------------------------------- #
# sc_textfile_to_read_text_rewrite
# --------------------------------------------------------------------------- #
NAME_TEXTFILE = "sc_textfile_to_read_text_rewrite"


@pytest.mark.parametrize(
    "source, expected",
    [
        ('lines = sc.textFile("d.txt")\n', "spark.read.text("),
        ('lines = spark.sparkContext.textFile(path)\n', "spark.read.text("),
        ('lines = self.spark.sparkContext.textFile(path)\n', "self.spark.read.text("),
    ],
)
def test_textfile_rewrites(source, expected):
    res = _apply(NAME_TEXTFILE, source)
    assert expected in res.source
    assert "textFile" not in _code_only(res.source)
    assert len(res.edits) == 1
    _assert_idempotent(NAME_TEXTFILE, source)


@pytest.mark.parametrize(
    "source",
    [
        'lines = sc.textFile("d.txt", 4)\n',     # minPartitions arg -> skip
        'lines = reader.textFile("d.txt")\n',    # receiver not sc/.sparkContext
    ],
)
def test_textfile_leaves_other_forms(source):
    res = _apply(NAME_TEXTFILE, source)
    assert res.source == source
    assert res.edits == []


# --------------------------------------------------------------------------- #
# sc_range_to_spark_range_rewrite
# --------------------------------------------------------------------------- #
NAME_RANGE = "sc_range_to_spark_range_rewrite"


@pytest.mark.parametrize(
    "source, expected",
    [
        ("r = sc.range(0, 10)\n", "spark.range(0, 10)"),
        ("r = spark.sparkContext.range(5)\n", "spark.range(5)"),
    ],
)
def test_range_rewrites(source, expected):
    res = _apply(NAME_RANGE, source)
    assert expected in res.source
    assert len(res.edits) == 1
    _assert_idempotent(NAME_RANGE, source)


@pytest.mark.parametrize(
    "source",
    [
        "r = range(0, 10)\n",          # builtin range
        "r = obj.range(0, 10)\n",      # unrelated receiver
    ],
)
def test_range_leaves_other_forms(source):
    res = _apply(NAME_RANGE, source)
    assert res.source == source
    assert res.edits == []


# --------------------------------------------------------------------------- #
# rdd_no_equivalent_todo_annotate
# --------------------------------------------------------------------------- #
NAME_TODO = "rdd_no_equivalent_todo_annotate"


@pytest.mark.parametrize(
    "source",
    [
        "rdd.saveAsSequenceFile('p')\n",
        "parts = df.rdd.getNumPartitions()\n",
        "g = rdd.glom()\n",
        "c = rdd.isCheckpointed()\n",
        "f = rdd.getCheckpointFile()\n",
    ],
)
def test_todo_annotates_unsupported(source):
    res = _apply(NAME_TODO, source)
    assert "# SCOS-TODO: [SPRKCNTPY1500-Error]" in res.source
    assert source.strip() in res.source  # code itself unchanged
    assert len(res.edits) == 1
    _assert_idempotent(NAME_TODO, source)


@pytest.mark.parametrize(
    "source",
    [
        "df.select('a')\n",
        "rdd.map(lambda x: x)\n",     # not in the unsupported set
        "out = obj.pipe(fn)\n",       # pipe deliberately excluded (pandas clash)
        # saveAsObjectFile moved OUT of the no-equivalent recipe (it is a
        # [Partial] convertible round-trip, §16.11) -> this recipe must skip it.
        "rdd.saveAsObjectFile('p')\n",
    ],
)
def test_todo_leaves_other_calls(source):
    res = _apply(NAME_TODO, source)
    assert res.source == source
    assert res.edits == []


# --------------------------------------------------------------------------- #
# Composition: the sc_* rewrites must sort BEFORE the SparkContext fallback,
# i.e. they run first and remove the token the fallback would otherwise flag.
# --------------------------------------------------------------------------- #
def test_sc_recipes_sort_before_sparkcontext_fallback():
    names = sorted(
        p.name for p in _RECIPES_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")
    )
    i_fallback = names.index("sparkcontext_property_fallback_rewrite")
    for earlier in (
        "sc_textfile_to_read_text_rewrite",
        "sc_range_to_spark_range_rewrite",
        "sc_parallelize_to_createdataframe_rewrite",
        "sc_wholetextfiles_to_read_text_rewrite",
        "hadoop_conf_credential_todo_annotate",
    ):
        assert names.index(earlier) < i_fallback, f"{earlier} must sort before fallback"


# --------------------------------------------------------------------------- #
# df_rdd_passthrough_rewrite -- extended allow-list
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "source, expected_fragment",
    [
        ("rows = df.rdd.collect()\n", "df.collect()"),
        ("n = df.rdd.count()\n", "df.count()"),
        ("r = df.rdd.first()\n", "df.first()"),
        ("r = df.rdd.take(5)\n", "df.take(5)"),
        ("d = df.rdd.distinct()\n", "df.distinct()"),
        ("c = df.rdd.cache()\n", "df.cache()"),
        ("u = df.rdd.unpersist()\n", "df.unpersist()"),
        ("p = df.rdd.repartition(8)\n", "df.repartition(8)"),
        ("p = df.rdd.coalesce(1)\n", "df.coalesce(1)"),
    ],
)
def test_passthrough_extended_methods_rewrite(source, expected_fragment):
    res = _apply(NAME_PASSTHROUGH, source)
    assert expected_fragment in res.source
    assert ".rdd." not in _code_only(res.source)
    assert len(res.edits) == 1
    _assert_idempotent(NAME_PASSTHROUGH, source)


@pytest.mark.parametrize(
    "source",
    [
        "x = df.rdd.map(lambda r: r)\n",          # no DataFrame counterpart
        "x = df.rdd.flatMap(lambda r: r)\n",      # no DataFrame counterpart
        "x = df.rdd.keyBy(lambda r: r[0])\n",     # no DataFrame counterpart
        "n = df.rdd.getNumPartitions()\n",        # handled by todo-annotate recipe
        "o = df.rdd.persist()\n",                 # handled by persist recipe
        "r = other.collect()\n",                  # not via .rdd
    ],
)
def test_passthrough_still_leaves_non_allowlisted(source):
    res = _apply(NAME_PASSTHROUGH, source)
    assert res.source == source
    assert res.edits == []


# --------------------------------------------------------------------------- #
# sc_parallelize_to_createdataframe_rewrite
# --------------------------------------------------------------------------- #
NAME_PARALLELIZE = "sc_parallelize_to_createdataframe_rewrite"


@pytest.mark.parametrize(
    "source, expected",
    [
        ("rdd = sc.parallelize([1, 2, 3])\n", "spark.createDataFrame([1, 2, 3])"),
        ("rdd = spark.sparkContext.parallelize(data)\n", "spark.createDataFrame(data)"),
        ("rdd = self.spark.sparkContext.parallelize(data)\n", "self.spark.createDataFrame(data)"),
    ],
)
def test_parallelize_rewrites(source, expected):
    res = _apply(NAME_PARALLELIZE, source)
    assert expected in res.source
    assert "parallelize" not in _code_only(res.source)
    assert len(res.edits) == 1
    _assert_idempotent(NAME_PARALLELIZE, source)


@pytest.mark.parametrize(
    "source",
    [
        "rdd = sc.parallelize(data, 4)\n",       # numSlices arg -> skip
        "rdd = obj.parallelize(data)\n",         # receiver not sc/.sparkContext
    ],
)
def test_parallelize_leaves_other_forms(source):
    res = _apply(NAME_PARALLELIZE, source)
    assert res.source == source
    assert res.edits == []


# --------------------------------------------------------------------------- #
# sc_wholetextfiles_to_read_text_rewrite
# --------------------------------------------------------------------------- #
NAME_WHOLETEXT = "sc_wholetextfiles_to_read_text_rewrite"


@pytest.mark.parametrize(
    "source, expected",
    [
        ('d = sc.wholeTextFiles("p")\n', 'spark.read.text("p", wholetext=True)'),
        ('d = spark.sparkContext.wholeTextFiles(p)\n', "wholetext=True"),
        ('d = self.spark.sparkContext.wholeTextFiles(p)\n', "self.spark.read.text("),
    ],
)
def test_wholetextfiles_rewrites(source, expected):
    res = _apply(NAME_WHOLETEXT, source)
    assert expected in res.source
    assert "wholeTextFiles" not in _code_only(res.source)
    assert len(res.edits) == 1
    _assert_idempotent(NAME_WHOLETEXT, source)


@pytest.mark.parametrize(
    "source",
    [
        'd = sc.wholeTextFiles("p", 4)\n',       # minPartitions arg -> skip
        'd = reader.wholeTextFiles("p")\n',      # receiver not sc/.sparkContext
        'd = sc.binaryFiles("p")\n',             # binaryFile unsupported in SCOS -> not rewritten here
    ],
)
def test_wholetextfiles_leaves_other_forms(source):
    res = _apply(NAME_WHOLETEXT, source)
    assert res.source == source
    assert res.edits == []


def test_binaryfiles_is_annotated_by_fallback_not_rewritten():
    """SCOS has no ``binaryFile`` reader, so ``sc.binaryFiles`` must NOT be
    rewritten to ``spark.read.format("binaryFile")`` (that raises at runtime).
    It falls through to the SparkContext fallback's method-call annotation."""
    src = 'data = sc.binaryFiles("s3://b/p")\n'
    # The wholetext recipe leaves it untouched.
    assert _apply(NAME_WHOLETEXT, src).source == src
    # The fallback annotates it as an unsupported SparkContext method call.
    res = _apply("sparkcontext_property_fallback_rewrite", src)
    assert "SPRKCNTPY4002" in res.source
    assert 'binaryFile"' not in res.source  # no read.format("binaryFile") was synthesised


# --------------------------------------------------------------------------- #
# hadoop_conf_credential_todo_annotate
# --------------------------------------------------------------------------- #
NAME_HADOOP = "hadoop_conf_credential_todo_annotate"


@pytest.mark.parametrize(
    "source",
    [
        'sc.hadoopConfiguration.set("fs.s3a.access.key", KEY)\n',
        'spark.sparkContext._jsc.hadoopConfiguration().set("fs.s3a.secret.key", S)\n',
    ],
)
def test_hadoop_conf_annotates(source):
    res = _apply(NAME_HADOOP, source)
    assert "# SCOS-TODO: [SPRKCNTPY3202]" in res.source
    assert source.strip() in res.source  # code itself unchanged
    assert len(res.edits) == 1
    _assert_idempotent(NAME_HADOOP, source)


@pytest.mark.parametrize(
    "source",
    [
        'spark.conf.set("spark.sql.session.timeZone", "UTC")\n',  # not hadoopConfiguration
        "cfg = sc.hadoopConfiguration\n",                          # read, no .set call
        'other.set("k", "v")\n',                                  # set, but no hadoopConfiguration
    ],
)
def test_hadoop_conf_leaves_other_forms(source):
    res = _apply(NAME_HADOOP, source)
    assert res.source == source
    assert res.edits == []


def test_property_fallback_does_not_mangle_hadoop_conf():
    """The fallback recipe must leave ``sc.hadoopConfiguration.set(...)`` intact
    (no getattr() wrap) so the hadoop recipe's TODO stands and the line doesn't
    become ``getattr(sc, "hadoopConfiguration", ...).set(...)``."""
    src = 'sc.hadoopConfiguration.set("fs.s3a.access.key", KEY)\n'
    res = _apply("sparkcontext_property_fallback_rewrite", src)
    assert "getattr(" not in res.source
    assert "sc.hadoopConfiguration.set(" in res.source


# --------------------------------------------------------------------------- #
# pyspark_rdd_import_todo_annotate
# --------------------------------------------------------------------------- #
NAME_RDD_IMPORT = "pyspark_rdd_import_todo_annotate"


@pytest.mark.parametrize(
    "source",
    [
        "from pyspark import RDD\n",
        "from pyspark.rdd import RDD, PipelinedRDD\n",
        "import pyspark.rdd\n",
        "import pyspark.rdd as r\n",
    ],
)
def test_rdd_import_annotates(source):
    res = _apply(NAME_RDD_IMPORT, source)
    assert "# SCOS-TODO: [SPRKCNTPY1500]" in res.source
    assert source.strip() in res.source  # import itself unchanged
    assert len(res.edits) == 1
    _assert_idempotent(NAME_RDD_IMPORT, source)


@pytest.mark.parametrize(
    "source",
    [
        "from pyspark.sql import functions as F\n",  # not the RDD surface
        "from pyspark import SparkConf\n",           # not RDD
        "from pyspark import RDDInfo\n",             # name is not exactly RDD
        "import pyspark.sql\n",                      # not pyspark.rdd
    ],
)
def test_rdd_import_leaves_other_forms(source):
    res = _apply(NAME_RDD_IMPORT, source)
    assert res.source == source
    assert res.edits == []


# --------------------------------------------------------------------------- #
# rdd_exclusive_method_todo_annotate
# --------------------------------------------------------------------------- #
NAME_EXCLUSIVE = "rdd_exclusive_method_todo_annotate"


@pytest.mark.parametrize(
    "source",
    [
        # The dataflow gap: an RDD bound to a variable, operated on later with
        # NO .rdd/sc. token on the line. Must still be flagged.
        "out = rdd.reduceByKey(add)\n",
        "out = data.map(f).groupByKey()\n",
        "kv = rdd.mapValues(lambda v: v * 2)\n",
        "idx = rdd.zipWithIndex()\n",
        "u = rdd.zipWithUniqueId()\n",
        "k = rdd.keyBy(lambda r: r[0])\n",
        "s = rdd.sortByKey()\n",
        "p = rdd.mapPartitions(fn)\n",
        "t = rdd.takeOrdered(5)\n",
        "rdd.saveAsTextFile('out')\n",
        "agg = rdd.aggregateByKey(0)(seq, comb)\n",
        # Also fires through the .rdd hop where no passthrough rewrite applies.
        "z = df.rdd.zipWithIndex()\n",
        # Aggregate recipes + §10 additional verified ops (newly targeted).
        "n = data.treeAggregate(z, seq, comb)\n",
        "v = data.treeReduce(add)\n",
        "m = pairs.collectAsMap()\n",
        "c = data.countApprox(1000, 0.95)\n",
        "d = data.countApproxDistinct(0.05)\n",
        "mn = amounts.meanApprox(1000)\n",
        "tot = data.sumApprox(1000)\n",
        "r = data.collectWithJobGroup('etl', 'load')\n",
        "p = data.mapPartitionsWithSplit(fn)\n",
        "q = kv.repartitionAndSortWithinPartitions(8)\n",
        "data.saveAsPickleFile('/p')\n",
        "data.saveAsObjectFile('/p')\n",
        "lvl = data.getStorageLevel()\n",
        "dbg = data.toDebugString()\n",
    ],
)
def test_exclusive_annotates_without_gate(source):
    res = _apply(NAME_EXCLUSIVE, source)
    assert "# SCOS-TODO: [SPRKCNTPY1500]" in res.source
    assert source.strip() in res.source  # code itself unchanged
    assert len(res.edits) == 1
    _assert_idempotent(NAME_EXCLUSIVE, source)


@pytest.mark.parametrize(
    "source",
    [
        "df.select('a').filter(c)\n",          # ambiguous DataFrame methods
        "n = df.count()\n",                    # ambiguous
        "rows = df.collect()\n",               # ambiguous
        "d = df.distinct()\n",                 # ambiguous
        "m = cfg.values()\n",                  # dict.values(), not RDD
        "g = rdd.glom()\n",                    # handled by no_equivalent recipe
        "p = df.rdd.getNumPartitions()\n",     # handled by no_equivalent recipe
        "x = obj.cogroup(other)\n",            # excluded (pandas cogrouped-ops clash)
    ],
)
def test_exclusive_leaves_ambiguous_and_other(source):
    res = _apply(NAME_EXCLUSIVE, source)
    assert res.source == source
    assert res.edits == []


def test_exclusive_does_not_duplicate_no_equivalent():
    """The exclusive set and the no-equivalent set must not overlap, so a single
    method is never annotated by both recipes."""
    excl = _load(NAME_EXCLUSIVE)._TARGET_METHODS
    noeq = _load(NAME_TODO)._TARGET_METHODS
    assert excl.isdisjoint(noeq), f"overlap: {excl & noeq}"
