"""Rewrite ``sc.range(...)`` / ``<x>.sparkContext.range(...)`` to
``spark.range(...)``.

What it does
------------

``SparkContext.range`` is unavailable in Spark Connect / SCOS, but the
SparkSession exposes an identically-shaped ``spark.range(...)`` that returns a
``DataFrame[id: bigint]``::

    r = sc.range(0, 100)   ->   r = spark.range(0, 100)

When the receiver is ``<x>.sparkContext`` the session ``<x>`` is reused; a bare
``sc`` becomes ``spark`` (convention). All arguments are preserved.

Composition note
----------------

Sorts BEFORE ``sparkcontext_property_fallback_rewrite`` (``"sc_" < "sp"``), so it
rewrites first and leaves nothing for that recipe to flag. Idempotent.

Negative cases (must NOT trigger)
---------------------------------

* builtin ``range(...)`` -- ``func`` is a ``Name``, not an ``Attribute``.
* ``obj.range(...)`` where ``obj`` is not ``sc`` / ``.sparkContext``.
* **RDD-chain guard:** ``sc.range(...)`` whose result flows into an RDD-only op
  (``sc.range(10).map(fail)``); the whole block is left intact for the LLM fixer
  to convert holistically. See ``_common.is_rdd_chain_statement``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "sc_range_to_spark_range_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY4000-Fixed] {RECIPE_ID}: sc.range(...) -> spark.range(...)"
)


def _is_sc_receiver(expr: cst.BaseExpression) -> bool:
    if isinstance(expr, cst.Name) and expr.value == "sc":
        return True
    return (
        isinstance(expr, cst.Attribute)
        and isinstance(expr.attr, cst.Name)
        and expr.attr.value == "sparkContext"
    )


def _session_of(receiver: cst.BaseExpression) -> cst.BaseExpression:
    if isinstance(receiver, cst.Attribute) and isinstance(receiver.attr, cst.Name) \
            and receiver.attr.value == "sparkContext":
        return receiver.value
    return cst.Name("spark")


def _is_target_call(node: cst.CSTNode) -> bool:
    """True iff ``node`` is ``<sc>.range(...)``."""
    return (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value == "range"
        and _is_sc_receiver(node.func.value)
    )


def _rewrite(call: cst.Call) -> cst.Call:
    """``<sc>.range(args)`` -> ``<session>.range(args)`` (keep all args)."""
    assert isinstance(call.func, cst.Attribute)
    session = _session_of(call.func.value)
    return call.with_changes(
        func=cst.Attribute(value=session, attr=cst.Name("range"))
    )


class _CallRewriter(cst.CSTTransformer):
    def __init__(self) -> None:
        super().__init__()
        self.rewrites = 0

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if _is_target_call(updated_node):
            self.rewrites += 1
            return _rewrite(updated_node)
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

    def __init__(self, *, source, file="<input.py>", facts_db=None) -> None:
        super().__init__(source=source, file=file, facts_db=facts_db)
        self._rdd_names = _common.collect_rdd_chained_names(source)

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        if _already_annotated(updated_node):
            return updated_node
        # Chain guard: leave sc.range(...) intact when the result flows into an
        # RDD-only op (e.g. `sc.range(10).map(fail)`) so the fixer converts the
        # whole block holistically (see _common gating rationale).
        if _common.is_rdd_chain_statement(updated_node) or (
            _common.assignment_target_names(updated_node) & self._rdd_names
        ):
            return updated_node
        sub = _CallRewriter()
        new_stmt = updated_node.visit(sub)
        if sub.rewrites == 0:
            return updated_node
        assert isinstance(new_stmt, cst.SimpleStatementLine)
        new_stmt = _with_leading_comment(new_stmt)
        self._record(
            self._line_of(original_node),
            f"sc.range -> spark.range ({sub.rewrites} call(s))",
        )
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
