"""Rewrite ``df.checkpoint(...)`` / ``df.localCheckpoint(...)`` to ``df.cache()``.

What it does
------------

PySpark exposes three closely-related materialization APIs:

* ``df.cache()``                     -- LRU memory cache
* ``df.checkpoint(eager=True)``      -- writes RDD lineage to durable storage
* ``df.localCheckpoint(eager=True)`` -- in-executor durable cut

Only ``cache()`` has a meaningful equivalent in Snowpark Connect (SCOS)
because the engine is Snowflake — there is no Spark RDD lineage to break
and no executor-local checkpoint dir. ``checkpoint(...)`` / ``localCheckpoint(...)``
calls usually appear in workloads that hit deep DAG re-computations on
Spark; in SCOS they're cheap-no-ops at best and confusing at worst
because users assume durability semantics that the engine doesn't
provide.

This recipe rewrites every::

    df.checkpoint(...)           ->   df.cache()
    df.localCheckpoint(...)      ->   df.cache()

regardless of arguments (``eager=True/False`` is silently dropped),
preserving the receiver and emitting a one-line SCOS comment above the
statement so the analyzer/fixer can audit the change.

Trigger
-------

A ``Call`` whose ``func`` is ``Attribute(attr=Name("checkpoint"))`` or
``Attribute(attr=Name("localCheckpoint"))``. Receiver can be anything
(simple ``Name``, chained method call, etc.) — the recipe rewrites only
the terminal call and leaves the rest of the chain alone.

Negative cases (must NOT trigger)
---------------------------------

* ``df.cache()`` / ``df.persist(...)`` -- already the right call.
* ``sc.setCheckpointDir(...)`` -- SparkContext API, different surface.
* ``StreamingContext.checkpoint(...)`` -- Streaming API; SCOS does not
  support streaming and a separate rule catches that.

Idempotency
-----------

After rewrite the call shape is ``.cache(...)``, which does not match
the trigger -- so re-running is a no-op. The leading comment is also
detected and not duplicated.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "dataframe_checkpoint_to_cache_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_TARGET_METHODS = frozenset({"checkpoint", "localCheckpoint"})
_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY6100-Fixed] {RECIPE_ID}: checkpoint()/localCheckpoint() not supported in "
    f"SCOS; replaced with cache()"
)


def _is_target_call(node: cst.CSTNode) -> bool:
    """True iff ``node`` is ``<receiver>.checkpoint(...)`` or
    ``<receiver>.localCheckpoint(...)``."""
    return (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value in _TARGET_METHODS
    )


def _rewrite_to_cache(call: cst.Call) -> cst.Call:
    """Return ``<same-receiver>.cache()`` with no args."""
    assert isinstance(call.func, cst.Attribute)
    return cst.Call(
        func=cst.Attribute(
            value=call.func.value,
            attr=cst.Name("cache"),
        ),
        args=[],
    )


class _CallRewriter(cst.CSTTransformer):
    """Inline sub-transformer that swaps every target call in a subtree.

    Used inside ``leave_SimpleStatementLine`` -- the outer recipe counts
    rewrites at the statement level so it only emits one leading
    comment per statement.
    """

    def __init__(self) -> None:
        super().__init__()
        self.rewrites = 0

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if _is_target_call(updated_node):
            self.rewrites += 1
            return _rewrite_to_cache(updated_node)
        return updated_node


def _already_annotated(stmt: cst.SimpleStatementLine) -> bool:
    for line in stmt.leading_lines:
        if line.comment is not None and RECIPE_ID in line.comment.value:
            return True
    return False


def _with_leading_comment(
    stmt: cst.SimpleStatementLine,
) -> cst.SimpleStatementLine:
    """Prepend a single ``# SCOS: ...`` comment line preserving the
    statement's existing leading blanks."""
    new_leading = list(stmt.leading_lines) + [
        cst.EmptyLine(comment=cst.Comment(_COMMENT_TEXT))
    ]
    return stmt.with_changes(leading_lines=tuple(new_leading))


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        if _already_annotated(updated_node):
            return updated_node

        sub = _CallRewriter()
        new_stmt = updated_node.visit(sub)
        if sub.rewrites == 0:
            return updated_node

        assert isinstance(new_stmt, cst.SimpleStatementLine)
        new_stmt = _with_leading_comment(new_stmt)
        self._record(
            self._line_of(original_node),
            f"checkpoint/localCheckpoint -> cache ({sub.rewrites} call(s))",
        )
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
