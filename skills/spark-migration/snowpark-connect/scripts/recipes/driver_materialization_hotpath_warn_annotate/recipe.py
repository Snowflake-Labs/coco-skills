"""Warn on driver-side materialization (``.collect()``, ``.toPandas()``,
``.first()``, ``.take()``, ``.head()``) inside ``for`` / ``while`` loops.

What it does
------------

Per-row driver materialization inside a loop is the single most common
perf footgun we see in SCOS workloads: every call round-trips data from
Snowflake to the client, gets converted to Python objects (or a Pandas
frame), and the iterator does it N times. Native Snowpark / SCOS users
get a 100x+ speedup by lifting the materialization out of the loop and
working on the DataFrame in aggregate.

This recipe is annotate-only -- the correct rewrite depends on what
the loop is doing with the result. We tag every offending call with a
``# SCOS-WARN`` comment so the LLM fixer can rewrite the loop in the
next phase with full context.

Trigger
-------

A ``Call`` whose ``func`` is ``Attribute(attr=Name(name))`` for ``name``
in ``{"collect", "toPandas", "first", "take", "head", "toLocalIterator"}``,
appearing **lexically inside** a ``cst.For`` or ``cst.While`` block
body (not inside a function definition that just happens to live
inside a loop).

Negative cases (must NOT trigger)
---------------------------------

* Same call at module top-level: ``df.collect()`` once is fine.
* ``df.head(5)`` inside a top-level expression: not in a loop.
* Generator/comprehension: ``[r.collect() for r in dfs]`` -- a
  comprehension is its own scope; we still trigger because the perf
  pattern is the same. (We use a simple "any For/While ancestor"
  rule.)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "driver_materialization_hotpath_warn_annotate"
MIN_SCOS_VERSION = "0.4.0"

_HOTPATH_METHODS = frozenset(
    {"collect", "toPandas", "first", "take", "head", "toLocalIterator"}
)
_COMMENT_TEXT = (
    f"# SCOS-WARN: [SPRKCNTPY6100-Warning] {RECIPE_ID}: driver-side materialization inside a "
    f"loop is a known SCOS perf hotpath -- consider lifting the call "
    f"out of the loop and operating on the DataFrame in bulk (e.g. "
    f"join / window) instead of calling .collect()/.toPandas()/.first()"
    f"/.take()/.head() per iteration."
)


class _Detector(cst.CSTVisitor):
    """Visit a statement subtree and flag a hotpath call."""

    def __init__(self) -> None:
        super().__init__()
        self.matched = False

    def visit_Call(self, node: cst.Call) -> None:
        if self.matched:
            return
        if not isinstance(node.func, cst.Attribute):
            return
        if not isinstance(node.func.attr, cst.Name):
            return
        if node.func.attr.value in _HOTPATH_METHODS:
            self.matched = True


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        # Walk-stack tracking: how many enclosing For/While bodies we're
        # currently inside. We only annotate statements when this is > 0.
        self._loop_depth = 0
        # We also track FunctionDef depth and reset/restore loop_depth at
        # function boundaries -- a function defined inside a for-loop
        # is not itself "in" the loop in a perf-relevant sense, because
        # the function body executes only when called.
        self._fn_loop_stack: list[int] = []

    # ----- For / While: increment loop_depth around body -----------------

    def visit_For(self, node: cst.For) -> None:
        self._loop_depth += 1

    def leave_For(  # type: ignore[override]
        self, original_node: cst.For, updated_node: cst.For
    ) -> cst.For:
        self._loop_depth -= 1
        return updated_node

    def visit_While(self, node: cst.While) -> None:
        self._loop_depth += 1

    def leave_While(  # type: ignore[override]
        self, original_node: cst.While, updated_node: cst.While
    ) -> cst.While:
        self._loop_depth -= 1
        return updated_node

    # ----- FunctionDef: save/restore depth so nested fn defs reset -------

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._fn_loop_stack.append(self._loop_depth)
        self._loop_depth = 0

    def leave_FunctionDef(  # type: ignore[override]
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
    ) -> cst.FunctionDef:
        self._loop_depth = self._fn_loop_stack.pop()
        return updated_node

    # ----- Statement-level annotation -------------------------------------

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        if self._loop_depth == 0:
            return updated_node
        start = self._line_of(original_node)
        if _annotate.comment_above_contains(self._lines, start, RECIPE_ID):
            return updated_node
        det = _Detector()
        updated_node.visit(det)
        if not det.matched:
            return updated_node
        self._record(start, "annotated hotpath materialization")
        return _annotate.prepend_comment(updated_node, _COMMENT_TEXT)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
