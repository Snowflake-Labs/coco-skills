"""Annotate RDD-exclusive transformation/action methods with a migration TODO.

What it does
------------

The analyzer's RDD detection (``has_rdd_usage``) only flags an RDD *method*
(``.reduceByKey(``, ``.groupByKey(``, ...) when the same statement also contains
a ``.rdd`` / ``sc.`` / ``sparkContext`` token -- a context gate that prevents
false positives on DataFrame homonyms (``.filter``/``.count``/``.map`` exist on
both). The cost of that gate: when an RDD flows through a *variable* and is
operated on in a separate statement (or a function parameter), the downstream
call is not flagged::

    rdd = sc.parallelize(data)        # flagged (sc.parallelize)
    out = rdd.reduceByKey(add)        # NOT flagged -- no .rdd/sc. on this line

This recipe closes that gap deterministically at the rewrite layer: recipes run
on every file unconditionally with **no** block extraction and **no** gate, so a
call to any RDD-*exclusive* method name -- one with no DataFrame / dict / builtin
homonym -- is unambiguously RDD wherever it appears and is annotated with a
``# SCOS-TODO: [SPRKCNTPY1500]`` marker pointing at the DataFrame/Snowpark
equivalent. It is annotate-only: the rewrite itself needs lambda→column
translation the recipe can't do, so a human/LLM performs it.

Targeted methods (RDD-exclusive -- no DataFrame homonym)
--------------------------------------------------------

Pair/key-value + partition + RDD-only ordering/sampling/save ops, plus the
aggregate recipes and the §10 "additional verified RDD operations"::

    reduceByKey, reduceByKeyLocally, groupByKey, aggregateByKey, foldByKey,
    combineByKey, sampleByKey, countByKey, countByValue, mapValues,
    flatMapValues, keyBy, zipWithIndex, zipWithUniqueId, sortByKey,
    mapPartitions, mapPartitionsWithIndex, takeOrdered, takeSample,
    saveAsTextFile, saveAsObjectFile, treeAggregate, treeReduce, collectAsMap,
    countApprox, countApproxDistinct, meanApprox, sumApprox, collectWithJobGroup,
    mapPartitionsWithSplit, repartitionAndSortWithinPartitions,
    saveAsPickleFile, getStorageLevel, toDebugString

Deliberately excluded:
  * **Ambiguous names** that also exist on DataFrame / builtins / dict and so
    need the analyzer's context gate, not an unconditional annotation:
    ``map``, ``filter``, ``collect``, ``count``, ``first``, ``take``,
    ``distinct``, ``union``, ``join``, ``intersection``, ``subtract``,
    ``cache``, ``persist``, ``unpersist``, ``repartition``, ``coalesce``,
    ``sort``, ``reduce``, ``fold``, ``aggregate``, ``sum``/``max``/``min``/
    ``mean``, ``keys``/``values`` (dict), ``sample``, ``pipe``, ``foreach``,
    ``lookup``, ``top``, ``histogram``, ``cogroup``/``groupWith`` (the pandas
    cogrouped-ops API uses ``.cogroup`` on a grouped DataFrame).
  * **No-equivalent RDD ops** (``glom``, ``getNumPartitions``,
    ``isCheckpointed``, ``getCheckpointFile``, ``saveAsSequenceFile``) --
    handled by ``rdd_no_equivalent_todo_annotate`` with a distinct "no SCOS
    equivalent" message; not duplicated here. (``saveAsObjectFile`` is NOT in
    that bucket: like ``saveAsPickleFile`` it has a [Partial] parquet/table
    round-trip, §16.11, so it is targeted here; only the reader
    ``sc.objectFile`` has no equivalent.)

Trigger
-------

A ``Call`` whose ``func`` is ``Attribute(attr=Name(m))`` for ``m`` in the target
set, anywhere inside a ``SimpleStatementLine``.

Idempotency
-----------

Re-running on annotated source is a no-op (leading-comment check via
``_annotate.comment_above_contains``).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "rdd_exclusive_method_todo_annotate"
MIN_SCOS_VERSION = "0.4.0"

_TARGET_METHODS = frozenset(
    {
        "reduceByKey",
        "reduceByKeyLocally",
        "groupByKey",
        "aggregateByKey",
        "foldByKey",
        "combineByKey",
        "sampleByKey",
        "countByKey",
        "countByValue",
        "mapValues",
        "flatMapValues",
        "keyBy",
        "zipWithIndex",
        "zipWithUniqueId",
        "sortByKey",
        "mapPartitions",
        "mapPartitionsWithIndex",
        "takeOrdered",
        "takeSample",
        "saveAsTextFile",
        # [Partial] parquet/table round-trip (§16.11) — same verdict as its
        # sibling saveAsPickleFile, so convertible, not no-equivalent.
        "saveAsObjectFile",
        # Aggregate recipes + §10 "additional verified RDD operations": all
        # RDD-exclusive names (no DataFrame homonym), mirroring
        # RDD_EXCLUSIVE_METHODS in analyze_pyspark.py. Each has a DataFrame
        # workaround in references/python/rdd-conversion.md (§14 / §16), so this
        # annotate-only marker points a human/LLM at the rewrite. Kept disjoint
        # from rdd_no_equivalent_todo_annotate (which now keeps only
        # saveAsSequenceFile among the save ops).
        "treeAggregate",
        "treeReduce",
        "collectAsMap",
        "countApprox",
        "countApproxDistinct",
        "meanApprox",
        "sumApprox",
        "collectWithJobGroup",
        "mapPartitionsWithSplit",
        "repartitionAndSortWithinPartitions",
        "saveAsPickleFile",
        "getStorageLevel",
        "toDebugString",
    }
)


def _comment_for(method: str) -> str:
    return (
        f"# SCOS-TODO: [SPRKCNTPY1500] {RECIPE_ID}: RDD.{method}() is unavailable "
        f"in Spark Connect; migrate to the DataFrame / Snowpark Connect "
        f"equivalent (see references/python/rdd-conversion.md)"
    )


class _Detector(cst.CSTVisitor):
    """Flag the first target method name seen in the statement subtree."""

    def __init__(self) -> None:
        super().__init__()
        self.method: Optional[str] = None

    def visit_Call(self, node: cst.Call) -> None:
        if self.method is not None:
            return
        if (
            isinstance(node.func, cst.Attribute)
            and isinstance(node.func.attr, cst.Name)
            and node.func.attr.value in _TARGET_METHODS
        ):
            self.method = node.func.attr.value


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        start = self._line_of(original_node)
        if _annotate.comment_above_contains(self._lines, start, RECIPE_ID):
            return updated_node
        det = _Detector()
        updated_node.visit(det)
        if det.method is None:
            return updated_node
        self._record(start, f"annotated RDD-exclusive method {det.method!r}")
        return _annotate.prepend_comment(updated_node, _comment_for(det.method))


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
