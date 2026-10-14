"""Rewrite the Databricks ``<df>.display()`` *method* to ``<df>.show()``.

What it does
------------

Databricks Runtime 13+ exposes ``DataFrame.display()`` as a method (in addition
to the notebook-global ``display(df)`` helper). It renders the frame in the cell
UI and does not exist in SCOS / Snowpark Connect (or OSS Spark), so a
``df.display()`` call fails with ``AttributeError`` at runtime. The standard
migration is the DataFrame-native renderer::

    df.display()                        ->   df.show()
    spark.table("t").display()          ->   spark.table("t").show()
    df.filter(cond).display()           ->   df.filter(cond).show()

The bare **global** form ``display(df)`` is handled by the separate
``display_to_show_rewrite`` recipe; the matplotlib form ``display(plt.gcf())`` by
``display_matplotlib_to_show_rewrite``. This recipe owns only the zero-arg
*method* form, which those two intentionally skip.

Because the receiver already carries a ``.display`` attribute access, swapping
the attribute to ``.show`` is always syntactically valid without added parens,
for any receiver (Name / Attribute / Call / Subscript / parenthesised expr).

Shape/behaviour caveat
----------------------

Databricks ``display`` renders up to ~1000 rows in a rich UI table; ``.show()``
prints 20 rows (truncated) to stdout. The leading ``# SCOS:`` comment flags this
so a reviewer can pass an explicit ``.show(n, truncate=False)`` if the row count
or formatting matters.

Negative cases (must NOT trigger)
---------------------------------

* ``display(df)`` -- the notebook global (a ``Name`` call), not a method; owned
  by ``display_to_show_rewrite``.
* ``obj.display(x)`` -- a method call *with arguments*; the Databricks renderer
  takes none, so an argument means this is some other API. Left for the LLM
  fixer.
* ``obj.display(streamName=...)`` -- any keyword/star arg; left for the LLM fixer.
* ``obj.display`` -- attribute access without a call.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "dataframe_display_method_to_show_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY3100-Fixed] {RECIPE_ID}: Databricks df.display() -> df.show() "
    f"(display rendered up to ~1000 rows; .show() prints 20 — pass .show(n, "
    f"truncate=False) if needed)"
)


def _is_target_call(node: cst.CSTNode) -> bool:
    """True iff ``node`` is a zero-arg ``<receiver>.display()`` method call."""
    if not (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value == "display"
    ):
        return False
    return len(node.args) == 0


def _rewrite(call: cst.Call) -> cst.Call:
    """``<receiver>.display()`` -> ``<receiver>.show()``."""
    assert isinstance(call.func, cst.Attribute)
    new_func = call.func.with_changes(attr=cst.Name("show"))
    return call.with_changes(func=new_func)


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
            f"df.display() -> df.show() ({sub.rewrites} call(s))",
        )
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
