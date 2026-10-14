"""Annotate nested window functions like ``lag(last_value(...).over(...)).over(...)``.

Snowflake rejects nested analytic SQL (``002062``). Conversion must
materialize the inner window into a column before applying the outer
``lag`` / ``lead`` / ``last`` / ``last_value`` / ``first`` /
``first_value``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "nested_window_functions_annotate"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS-TODO: [SPRKCNTPY5300-Error] {RECIPE_ID}: nested window functions "
    f"(e.g. lag(last_value(...).over(...)).over(...)) — materialize the inner "
    f"window as a column first; do not nest analytics in one expression"
)

# Outer function names that match the nested-analytics pattern the fixer
# is told to split. Aggregates like sum/collect_list are omitted so a
# window whose args merely mention another .over is not over-flagged.
_WINDOW_FUNCS = frozenset(
    {
        "lag",
        "lead",
        "last",
        "last_value",
        "first",
        "first_value",
    }
)


def _func_name(func: cst.BaseExpression) -> Optional[str]:
    if isinstance(func, cst.Name):
        return func.value
    if isinstance(func, cst.Attribute) and isinstance(func.attr, cst.Name):
        return func.attr.value
    return None


class _NestedWindowDetector(cst.CSTVisitor):
    def __init__(self) -> None:
        super().__init__()
        self.matched = False

    def visit_Call(self, node: cst.Call) -> bool:
        if self.matched:
            return False
        # pattern: something.over(...) where something is Call to window fn
        # whose args contain another *.over(...)
        if not (
            isinstance(node.func, cst.Attribute)
            and isinstance(node.func.attr, cst.Name)
            and node.func.attr.value == "over"
            and isinstance(node.func.value, cst.Call)
        ):
            return True
        outer_fn = node.func.value
        outer_name = _func_name(outer_fn.func)
        if outer_name not in _WINDOW_FUNCS:
            return True
        for arg in outer_fn.args:
            if self._contains_over(arg.value):
                self.matched = True
                return False
        return True

    def _contains_over(self, node: cst.CSTNode) -> bool:
        class _Find(cst.CSTVisitor):
            def __init__(self) -> None:
                self.found = False

            def visit_Attribute(self, n: cst.Attribute) -> bool:
                if isinstance(n.attr, cst.Name) and n.attr.value == "over":
                    self.found = True
                    return False
                return True

        f = _Find()
        node.visit(f)
        return f.found


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
        det = _NestedWindowDetector()
        updated_node.visit(det)
        if not det.matched:
            return updated_node
        self._record(start, "annotated nested window")
        return _annotate.prepend_comment(updated_node, _COMMENT_TEXT)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
