"""Rewrite ``<df>.rdd.<m>(...)`` to the direct DataFrame method, dropping the
unsupported ``.rdd`` hop, for a curated allow-list of identical-semantics
methods.

What it does
------------

``DataFrame.rdd`` is unavailable in Spark Connect / SCOS (accessing it raises
at runtime). A curated set of methods called on ``df.rdd`` exist *identically*
on the DataFrame itself -- same name, same signature, same result semantics --
so the ``.rdd`` hop can be dropped byte-for-byte (arguments are preserved)::

    df.rdd.isEmpty()           ->  df.isEmpty()
    df.rdd.toLocalIterator()   ->  df.toLocalIterator()
    df.rdd.collect()           ->  df.collect()
    df.rdd.count()             ->  df.count()
    df.rdd.first()             ->  df.first()
    df.rdd.take(5)             ->  df.take(5)
    df.rdd.distinct()          ->  df.distinct()
    df.rdd.cache()             ->  df.cache()
    df.rdd.unpersist()         ->  df.unpersist()
    df.rdd.repartition(8)      ->  df.repartition(8)
    df.rdd.coalesce(1)         ->  df.coalesce(1)

This recipe is intentionally a **hard allow-list**. A general "strip ``.rdd``
when the next op is DataFrame-valid" rule would require the recipe to know the
entire DataFrame API surface (there is no type inference in the recipe
framework), so an open-ended rule would silently misrewrite. Every name on the
list is one whose ``df.rdd.<m>(...)`` form is behaviourally identical to
``df.<m>(...)``; ``repartition``/``coalesce`` keep the surviving DataFrame call
(it is **not** a no-op -- it controls write file count, per the RDD reference).
Other ``df.rdd.<x>`` forms are deliberately left alone:

  * ``df.rdd.map(...)`` / ``flatMap`` / ``keyBy`` / ``zipWithIndex`` -- no
    DataFrame counterpart; stripping ``.rdd`` would produce invalid or
    semantically-wrong code. Left for the LLM fixer.
  * ``df.rdd.getNumPartitions()`` -- handled (annotate-only) by
    ``rdd_no_equivalent_todo_annotate``.
  * ``df.rdd.persist(level)`` -- handled by ``rdd_persist_to_cache_rewrite``
    (it must additionally drop the storage-level argument).

Composition note
----------------

``dataframe_checkpoint_to_cache_rewrite`` sorts before this recipe and rewrites
``df.rdd.checkpoint()`` -> ``df.rdd.cache()``; this recipe then strips the
remaining ``.rdd`` hop to land on ``df.cache()``.

Trigger
-------

A ``Call`` whose ``func`` is ``Attribute(attr=Name(m))`` for ``m`` in the
allow-list and whose ``func.value`` is ``Attribute(attr=Name("rdd"))``. The
rewrite is performed at the ``Call`` level so it fires in every context
(assignments, ``if``/``while`` conditions, comprehensions, ...). A ``# SCOS:``
comment is added when the enclosing statement is a simple statement.

Idempotency
-----------

After the rewrite the chain is ``<recv>.<m>()`` -- ``func.value`` is no longer a
``.rdd`` attribute, so re-running is a no-op. The leading comment is de-duped.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "df_rdd_passthrough_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_PASSTHROUGH_METHODS = frozenset(
    {
        "isEmpty",
        "toLocalIterator",
        "collect",
        "count",
        "first",
        "take",
        "distinct",
        "cache",
        "unpersist",
        "repartition",
        "coalesce",
    }
)
_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY1500-Fixed] {RECIPE_ID}: df.rdd is unavailable in Spark "
    f"Connect; dropped the .rdd hop (method exists directly on DataFrame)"
)


def _is_rdd_attribute(node: cst.BaseExpression) -> bool:
    """True iff ``node`` is ``<x>.rdd``."""
    return (
        isinstance(node, cst.Attribute)
        and isinstance(node.attr, cst.Name)
        and node.attr.value == "rdd"
    )


def _is_target_call(node: cst.CSTNode) -> bool:
    """True iff ``node`` is ``<x>.rdd.<m>(...)`` for an allow-listed ``m``."""
    return (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value in _PASSTHROUGH_METHODS
        and _is_rdd_attribute(node.func.value)
    )


def _rewrite(call: cst.Call) -> cst.Call:
    """``<x>.rdd.<m>(args)`` -> ``<x>.<m>(args)`` (drop the ``.rdd`` hop)."""
    assert isinstance(call.func, cst.Attribute)
    rdd_attr = call.func.value
    assert isinstance(rdd_attr, cst.Attribute)
    return call.with_changes(func=call.func.with_changes(value=rdd_attr.value))


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        # Source lines (1-based) where a rewrite fired, so the matching
        # SimpleStatementLine can attach exactly one comment.
        self._rewritten_lines: set[int] = set()

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if not _is_target_call(updated_node):
            return updated_node
        line = self._line_of(original_node)
        self._rewritten_lines.add(line)
        self._record(line, "df.rdd passthrough -> DataFrame method")
        return _rewrite(updated_node)

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        line = self._line_of(original_node)
        if line not in self._rewritten_lines:
            return updated_node
        if _annotate.comment_above_contains(self._lines, line, RECIPE_ID):
            return updated_node
        return _annotate.prepend_comment(updated_node, _COMMENT_TEXT)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
