"""Drop the unsupported ``rsd`` argument from ``approx_count_distinct`` / ``approxCountDistinct``.

What it does
------------

PySpark's ``approx_count_distinct(col, rsd=0.05)`` and legacy
``approxCountDistinct(col, rsd)`` accept an optional second argument
``rsd`` (relative standard deviation) that tunes HyperLogLog accuracy.
SCOS rejects the ``rsd`` parameter — passing it raises an error at runtime
(behavioral-diffs §2.2). Without ``rsd``, the function works correctly
using Snowflake's default HLL accuracy.

This recipe rewrites every::

    F.approx_count_distinct(col, 0.05)          ->   F.approx_count_distinct(col)
    F.approx_count_distinct(col, rsd=0.01)      ->   F.approx_count_distinct(col)
    F.approxCountDistinct(col, 0.05)            ->   F.approxCountDistinct(col)

Only the ``rsd`` argument is dropped; the first argument (column) is always
preserved.

Trigger
-------

A ``Call`` whose terminal function name is ``approx_count_distinct`` or
``approxCountDistinct`` AND that has more than one argument (positional or
keyword ``rsd``). Single-arg calls are already correct.

Negative cases (must NOT trigger)
---------------------------------

* ``F.approx_count_distinct(col)`` -- single arg, already correct.
* ``approx_count_distinct(col)`` -- bare import form, single arg.
* ``df.approx_count_distinct(col, rsd)`` -- hypothetical; matched anyway
  since name + arg count is sufficient.
* ``F.count_distinct(col1, col2)`` -- different function name.

Idempotency
-----------

After rewrite the call has exactly one arg, so it does not re-match. The
leading comment is fingerprinted by ``RECIPE_ID`` and not duplicated.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "approx_count_distinct_drop_rsd_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_TARGET_NAMES = frozenset({"approx_count_distinct", "approxCountDistinct"})

_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY5400-Fixed] {RECIPE_ID}: dropped rsd arg from "
    f"approx_count_distinct() (SCOS rejects the rsd accuracy parameter; "
    f"Snowflake uses its own HLL accuracy)"
)


def _get_func_name(func: cst.BaseExpression) -> Optional[str]:
    """Return the terminal name of the call target, or None."""
    if isinstance(func, cst.Name):
        return func.value
    if isinstance(func, cst.Attribute) and isinstance(func.attr, cst.Name):
        return func.attr.value
    return None


def _is_target_call(node: cst.CSTNode) -> bool:
    """True iff ``node`` is ``approx_count_distinct(col, rsd)`` with > 1 arg."""
    if not isinstance(node, cst.Call):
        return False
    name = _get_func_name(node.func)
    if name not in _TARGET_NAMES:
        return False
    # Must have more than 1 argument (the rsd to drop)
    if len(node.args) <= 1:
        return False
    return True


def _drop_rsd(call: cst.Call) -> cst.Call:
    """Keep only the first positional argument (the column expression)."""
    first_arg = call.args[0].with_changes(comma=cst.MaybeSentinel.DEFAULT)
    return call.with_changes(args=[first_arg])


class _CallRewriter(cst.CSTTransformer):
    def __init__(self) -> None:
        super().__init__()
        self.rewrites = 0

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if _is_target_call(updated_node):
            self.rewrites += 1
            return _drop_rsd(updated_node)
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
            f"approx_count_distinct: dropped rsd arg ({sub.rewrites} call(s))",
        )
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
