"""Strip no-op ``.coalesce(n)`` / ``.repartition(...)`` from DataFrame chains.

What it does
------------

On SCOS, Snowflake manages partitioning, so ``DataFrame.coalesce`` /
``repartition`` / ``repartitionByRange`` are no-ops. Removing them keeps the
chain clean and removes the (non-decidable) finding from the analyzer entirely::

    shared_df.coalesce(1).write.parquet(p)   ->   shared_df.write.parquet(p)
    df.repartition(10, "k").write...          ->   df.write...
    out = df.repartition(c)                    ->   out = df

Critical false-positive guard
-----------------------------

``F.coalesce(col1, col2, ...)`` is the **Column** null-coalescing function — a
real, supported expression that must NOT be stripped. We only strip the
**DataFrame method** form: a ``.coalesce``/``.repartition``/``.repartitionByRange``
call whose receiver is NOT the functions module (``F`` / ``f`` / ``functions``).
The bare ``coalesce(...)`` import form (no receiver) is never matched either.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "dataframe_partition_noop_strip_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_PARTITION_METHODS = {"coalesce", "repartition", "repartitionByRange"}

_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY6100-Fixed] {RECIPE_ID}: removed no-op .coalesce()/.repartition() "
    f"- Snowflake manages partitioning (DataFrame repartitioning has no effect in SCOS)"
)


def _is_functions_module(expr: cst.BaseExpression) -> bool:
    """``F`` / ``f`` / ``functions`` / ``<x>.functions`` — the Column-function namespace."""
    if isinstance(expr, cst.Name) and expr.value in ("F", "f", "functions"):
        return True
    if isinstance(expr, cst.Attribute) and isinstance(expr.attr, cst.Name) and expr.attr.value == "functions":
        return True
    return False


def _is_partition_noop(node: cst.CSTNode) -> bool:
    if not (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
    ):
        return False
    if node.func.attr.value not in _PARTITION_METHODS:
        return False
    # F.coalesce(...) is the Column function — keep it.
    if node.func.attr.value == "coalesce" and _is_functions_module(node.func.value):
        return False
    return True


class _CallRewriter(cst.CSTTransformer):
    def __init__(self) -> None:
        super().__init__()
        self.rewrites = 0

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if _is_partition_noop(updated_node):
            self.rewrites += 1
            # Replace ``<recv>.coalesce(n)`` with just ``<recv>`` (drop the no-op).
            assert isinstance(updated_node.func, cst.Attribute)
            return updated_node.func.value
        return updated_node


def _already_annotated(stmt: cst.SimpleStatementLine) -> bool:
    for line in stmt.leading_lines:
        if line.comment is not None and RECIPE_ID in line.comment.value:
            return True
    return False


def _with_leading_comment(stmt: cst.SimpleStatementLine) -> cst.SimpleStatementLine:
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
            f"stripped no-op partition call(s) ({sub.rewrites})",
        )
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
