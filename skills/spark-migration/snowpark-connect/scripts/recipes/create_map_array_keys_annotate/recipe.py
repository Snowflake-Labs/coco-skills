"""Annotate ``create_map(collect_list(...), collect_list(...))`` (MAP keyed by ARRAY).

Snowflake rejects ``MAP`` with ``ARRAY`` keys. Prefer ordered ``collect_list``
pairs stringified via ``array_join``, a join, or stringify keys — never emit
ARRAY-keyed maps (including ``create_map(...).cast("string")``).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "create_map_array_keys_annotate"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS-TODO: [SPRKCNTPY1000-Error] {RECIPE_ID}: create_map with "
    f"collect_list/array keys — Snowflake rejects MAP keyed by ARRAY; "
    f"prefer map_from_arrays, stringify keys (array_join / cast), or "
    f"restructure to a join; do not emit create_map(collect_list, collect_list)"
)


def _is_collect_listish(expr: cst.BaseExpression) -> bool:
    if not isinstance(expr, cst.Call):
        return False
    name = None
    if isinstance(expr.func, cst.Name):
        name = expr.func.value
    elif isinstance(expr.func, cst.Attribute) and isinstance(expr.func.attr, cst.Name):
        name = expr.func.attr.value
    return name in ("collect_list", "collect_set", "array")


def _is_create_map(call: cst.Call) -> bool:
    if isinstance(call.func, cst.Name):
        return call.func.value == "create_map"
    if isinstance(call.func, cst.Attribute) and isinstance(call.func.attr, cst.Name):
        return call.func.attr.value == "create_map"
    return False


class _Detector(cst.CSTVisitor):
    def __init__(self) -> None:
        super().__init__()
        self.matched = False

    def visit_Call(self, node: cst.Call) -> bool:
        if self.matched:
            return False
        if not _is_create_map(node) or len(node.args) < 2:
            return True
        # any pair where key-side looks like collect_list/array
        for i in range(0, len(node.args) - 1, 2):
            if _is_collect_listish(node.args[i].value):
                self.matched = True
                return False
        return True


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
        det = _Detector()
        updated_node.visit(det)
        if not det.matched:
            return updated_node
        self._record(start, "annotated create_map array keys")
        return _annotate.prepend_comment(updated_node, _COMMENT_TEXT)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
