"""Rewrite the Databricks ``display(<df>)`` helper to ``<df>.show()``.

What it does
------------

``display`` is a Databricks-notebook-only global used to render a DataFrame in
the cell output. It does not exist in SCOS / Snowpark Connect (or OSS Spark), so
a bare ``display(df)`` call fails with ``NameError`` at runtime. The standard
migration is the DataFrame-native renderer::

    display(df)                    ->   df.show()
    display(df.filter(cond))       ->   df.filter(cond).show()
    display(spark.table("t"))      ->   spark.table("t").show()

The recipe only rewrites the **bare** ``display(<single-positional>)`` form where
the argument is a trailer-able expression (Name / Attribute / Call / Subscript),
so attaching ``.show()`` is always syntactically valid without added parens.

Shape/behaviour caveat
----------------------

Databricks ``display`` renders up to ~1000 rows in a rich UI table; ``.show()``
prints 20 rows (truncated) to stdout. The leading ``# SCOS:`` comment flags this
so a reviewer can pass an explicit ``.show(n, truncate=False)`` if the row count
or formatting matters.

Negative cases (must NOT trigger)
---------------------------------

* ``obj.display(...)`` -- a *method* call, not the Databricks global.
* ``display()`` -- no argument.
* ``display(a, b)`` -- more than one positional arg (e.g. multiple frames).
* ``display(df, streamName=...)`` -- any keyword arg (streaming/options form);
  left for the LLM fixer.
* ``display(<literal/operator-expr>)`` -- arg is not a trailer-able atom (would
  need parens); left for the LLM fixer.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "display_to_show_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY3100-Fixed] {RECIPE_ID}: Databricks display(df) -> df.show() "
    f"(display rendered up to ~1000 rows; .show() prints 20 — pass .show(n, "
    f"truncate=False) if needed)"
)

# Expression node types that can safely take a trailing ``.show()`` without
# requiring parentheses around the receiver.
_TRAILERABLE = (cst.Name, cst.Attribute, cst.Call, cst.Subscript)


def _is_target_call(node: cst.CSTNode) -> bool:
    """True iff ``node`` is a bare ``display(<single trailer-able positional>)``."""
    if not (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Name)
        and node.func.value == "display"
    ):
        return False
    if len(node.args) != 1:
        return False
    arg = node.args[0]
    if arg.keyword is not None or getattr(arg, "star", "") == "*":
        return False
    return isinstance(arg.value, _TRAILERABLE)


def _rewrite(call: cst.Call) -> cst.Call:
    """``display(<expr>)`` -> ``<expr>.show()``."""
    receiver = call.args[0].value
    show = cst.Attribute(value=receiver, attr=cst.Name("show"))
    return cst.Call(func=show, args=[])


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
            f"display(df) -> df.show() ({sub.rewrites} call(s))",
        )
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
