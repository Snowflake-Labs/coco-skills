"""Rewrite ``display(<matplotlib figure>)`` to ``<root>.show()``.

What it does
------------

On Databricks, ``display(plt.gcf())`` (or any pyplot figure handed to
``display``) renders the current matplotlib figure. Snowflake Workspace
notebooks render matplotlib via the standard ``plt.show()``. Per
transformation-rules.md rule 17::

    display(plt.gcf())          ->   plt.show()
    display(pyplot.figure())    ->   pyplot.show()
    display(matplotlib.pyplot.gcf()) -> matplotlib.pyplot.show()

This is a *rewrite-to-supported*: the ``display(...)`` call is gone, so the
analyzer no longer flags it and no LLM pass is needed.

Determinism / scope
-------------------

To stay deterministic without type inference, the recipe fires **only** when
``display`` has a single positional argument whose expression is rooted at a
recognised pyplot handle:

* root ``Name`` is ``plt`` or ``pyplot``; or
* root attribute chain is ``matplotlib.pyplot``.

``display(fig)`` where ``fig`` is a bare variable is **not** matched (the type
is unknown). ``display(df)`` of a DataFrame is handled by the separate
``display_to_show_rewrite`` recipe and is never matched here (root is not a
pyplot handle). Multi-arg / kwarg ``display`` calls and ``obj.display(...)``
method calls are skipped.

Idempotency
-----------

After rewrite the ``display(...)`` is gone; re-running is a no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "display_matplotlib_to_show_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_PYPLOT_NAMES = frozenset({"plt", "pyplot"})


def _is_bare_display(call: cst.Call) -> bool:
    """``display(<one positional arg>)`` — not ``obj.display(...)``, no kwargs."""
    if not (isinstance(call.func, cst.Name) and call.func.value == "display"):
        return False
    if len(call.args) != 1:
        return False
    return call.args[0].keyword is None and call.args[0].star == ""


def _pyplot_root(expr: cst.BaseExpression) -> Optional[str]:
    """Walk an attribute/call chain to its root and return the pyplot handle
    string (``"plt"`` / ``"pyplot"`` / ``"matplotlib.pyplot"``) iff the chain is
    rooted at pyplot; otherwise None."""
    node = expr
    while True:
        if isinstance(node, cst.Call):
            node = node.func
        elif isinstance(node, cst.Attribute):
            # matplotlib.pyplot.<...> : detect the two-level module root
            if (
                isinstance(node.value, cst.Name)
                and node.value.value == "matplotlib"
                and node.attr.value == "pyplot"
            ):
                return "matplotlib.pyplot"
            node = node.value
        elif isinstance(node, cst.Name):
            return node.value if node.value in _PYPLOT_NAMES else None
        else:
            return None


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if not _is_bare_display(updated_node):
            return updated_node
        root = _pyplot_root(updated_node.args[0].value)
        if root is None:
            return updated_node
        self._record(self._line_of(original_node), f"display(...) -> {root}.show()")
        return cst.parse_expression(f"{root}.show()")


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
