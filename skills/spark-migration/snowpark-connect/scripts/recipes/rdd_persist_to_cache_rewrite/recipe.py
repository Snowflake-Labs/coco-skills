"""Rewrite ``<df>.rdd.persist(...)`` to ``<df>.cache()``.

What it does
------------

``DataFrame.rdd`` is unavailable in Spark Connect / SCOS, and RDD
``persist(StorageLevel)`` has no analogue. When code persists via the RDD hop::

    df.rdd.persist(StorageLevel.MEMORY_AND_DISK)   ->   df.cache()

the only meaningful SCOS equivalent is ``cache()`` (the storage level is dropped
-- Snowflake manages caching). This recipe rewrites that exact shape.

Why the ``.rdd`` gate is mandatory
----------------------------------

``DataFrame.persist(level)`` is a **valid, accepted** API in SCOS. Blindly
rewriting any ``.persist(...)`` to ``cache()`` would silently drop the storage
level and rewrite already-correct code. So this recipe matches **only** when the
receiver is literally ``<x>.rdd`` -- a provably-RDD persist. A bare
``df.persist(...)`` on a DataFrame is left untouched (and a ``sc.parallelize(...)
.persist(...)`` chain is left for the LLM fixer, since the ``parallelize`` itself
still needs migrating).

Trigger
-------

A ``Call`` whose ``func`` is ``Attribute(attr=Name("persist"))`` and whose
``func.value`` is ``Attribute(attr=Name("rdd"))``. The rewrite is performed at
the ``Call`` level so it fires in every context; a ``# SCOS:`` comment is added
when the enclosing statement is a simple statement. Arguments (storage level)
are dropped.

Negative cases (must NOT trigger)
---------------------------------

* ``df.persist(...)`` -- DataFrame persist, accepted; receiver is not ``.rdd``.
* ``df.cache()`` -- already correct.
* ``sc.parallelize([...]).persist(...)`` -- receiver is a Call, not ``.rdd``.

Idempotency
-----------

Output ``<x>.cache()`` has ``func.attr == "cache"`` and no ``.rdd`` receiver, so
it does not re-match. The leading comment is de-duped.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "rdd_persist_to_cache_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY1500-Fixed] {RECIPE_ID}: df.rdd.persist(...) -> df.cache() "
    f"(RDD persist unavailable in Spark Connect; storage level dropped)"
)


def _is_rdd_attribute(node: cst.BaseExpression) -> bool:
    return (
        isinstance(node, cst.Attribute)
        and isinstance(node.attr, cst.Name)
        and node.attr.value == "rdd"
    )


def _is_target_call(node: cst.CSTNode) -> bool:
    """True iff ``node`` is ``<x>.rdd.persist(...)``."""
    return (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value == "persist"
        and _is_rdd_attribute(node.func.value)
    )


def _rewrite(call: cst.Call) -> cst.Call:
    """``<x>.rdd.persist(args)`` -> ``<x>.cache()`` (drop .rdd hop + args)."""
    assert isinstance(call.func, cst.Attribute)
    rdd_attr = call.func.value
    assert isinstance(rdd_attr, cst.Attribute)
    return cst.Call(
        func=cst.Attribute(value=rdd_attr.value, attr=cst.Name("cache")),
        args=[],
    )


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._rewritten_lines: set[int] = set()

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if not _is_target_call(updated_node):
            return updated_node
        line = self._line_of(original_node)
        self._rewritten_lines.add(line)
        self._record(line, "df.rdd.persist -> df.cache")
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
