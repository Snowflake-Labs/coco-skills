"""Annotate RDD-instance methods that have NO SCOS equivalent with a TODO.

What it does
------------

Some RDD operations cannot be rewritten to DataFrame/Snowpark Connect form at
all. This recipe is annotate-only: it prepends a uniform ``# SCOS-TODO`` marker
so the marker is consistent across files/runs instead of left to LLM discretion.
It never changes code.

Targeted methods (all RDD-exclusive names -- no DataFrame / SparkSession
homonym, so any call is unambiguously RDD):

    saveAsSequenceFile                    -- Java-serialized / Hadoop sink
    glom                                  -- exposes partition layout
    isCheckpointed, getCheckpointFile     -- RDD checkpoint introspection
    getNumPartitions                      -- no meaningful value under Connect

Note: ``saveAsObjectFile`` is deliberately NOT here. Like ``saveAsPickleFile``
it has a [Partial] DataFrame parquet/table round-trip (rdd-conversion.md §16.11),
so it is annotated by ``rdd_exclusive_method_todo_annotate`` instead. Only the
reader entry point ``sc.objectFile`` has no equivalent.

Deliberately excluded:
  * ``pipe`` -- collides with ``pandas.DataFrame.pipe``; left to the LLM fixer to
    avoid a false positive on pandas code.
  * SparkContext entry points (``sc.sequenceFile``, ``sc.hadoopRDD``, ...) --
    already annotated by ``sparkcontext_property_fallback_rewrite`` (its
    method-call path). Annotating them here would double-comment.

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

RECIPE_ID = "rdd_no_equivalent_todo_annotate"
MIN_SCOS_VERSION = "0.4.0"

_TARGET_METHODS = frozenset(
    {
        "saveAsSequenceFile",
        "glom",
        "isCheckpointed",
        "getCheckpointFile",
        "getNumPartitions",
    }
)


def _comment_for(method: str) -> str:
    return (
        f"# SCOS-TODO: [SPRKCNTPY1500-Error] {RECIPE_ID}: RDD.{method}() has no SCOS "
        f"equivalent; manual migration required"
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
        self._record(start, f"annotated unsupported RDD method {det.method!r}")
        return _annotate.prepend_comment(updated_node, _comment_for(det.method))


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
