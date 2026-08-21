"""Drop the unsupported ``prefetchPartitions=`` arg from ``df.toLocalIterator(...)``.

What it does
------------

PySpark's ``DataFrame.toLocalIterator(prefetchPartitions=False)`` accepts a
``prefetchPartitions`` boolean that controls whether to pre-fetch the next
partition while the current one is being consumed. SCOS accepts
``toLocalIterator()`` but silently ignores the ``prefetchPartitions`` flag
(behavioral-diffs §6.12). Leaving the flag in is harmless at runtime, but
it is dead code that confuses readers and triggers analyzer findings.

This recipe rewrites every::

    df.toLocalIterator(prefetchPartitions=True)   ->   df.toLocalIterator()
    df.toLocalIterator(True)                      ->   df.toLocalIterator()
    df.toLocalIterator(False)                     ->   df.toLocalIterator()

Trigger
-------

A ``Call`` whose ``func`` is ``Attribute(attr=Name("toLocalIterator"))`` AND
that has at least one argument. Zero-arg ``toLocalIterator()`` is already
correct.

Negative cases (must NOT trigger)
---------------------------------

* ``df.toLocalIterator()`` -- no args, already correct.
* ``rdd.toLocalIterator()`` -- RDD API; same method name but zero-arg form.
* ``some_list.toLocalIterator()`` -- hypothetical; no args, no match.

Idempotency
-----------

After rewrite the call has zero args, so it does not re-match. The leading
comment is fingerprinted by ``RECIPE_ID`` and not duplicated.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "toLocalIterator_drop_prefetch_arg_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY6100-Fixed] {RECIPE_ID}: dropped prefetchPartitions= arg "
    f"from toLocalIterator() (SCOS ignores the prefetch hint)"
)


def _is_target_call(node: cst.CSTNode) -> bool:
    """True iff ``node`` is ``<receiver>.toLocalIterator(...)`` with >= 1 arg."""
    if not (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value == "toLocalIterator"
    ):
        return False
    return len(node.args) > 0


def _strip_args(call: cst.Call) -> cst.Call:
    """Return ``call`` with all arguments removed."""
    return call.with_changes(args=[])


class _CallRewriter(cst.CSTTransformer):
    def __init__(self) -> None:
        super().__init__()
        self.rewrites = 0

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if _is_target_call(updated_node):
            self.rewrites += 1
            return _strip_args(updated_node)
        return updated_node


def _already_annotated(stmt: cst.SimpleStatementLine) -> bool:
    for line in stmt.leading_lines:
        if line.comment is not None and RECIPE_ID in line.comment.value:
            return True
    return False


def _with_leading_comment(
    stmt: cst.SimpleStatementLine,
) -> cst.SimpleStatementLine:
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
            f"toLocalIterator: dropped prefetchPartitions arg ({sub.rewrites} call(s))",
        )
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
