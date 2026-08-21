"""Warn on unaliased self-joins: ``df.join(df, ...)`` without ``.alias()``.

What it does
------------

When Spark / SCOS sees the same DataFrame on both sides of a join
without an explicit ``.alias()`` on one side, column references on the
result become ambiguous and the post-join lookup can either fail
(``AmbiguousReference``) or silently pick the wrong side. This is a
high-yield class of D2 incompatibilities in real workloads.

This recipe annotates the call with a leading ``# SCOS-WARN`` comment
asking the reviewer to alias one side. It does **not** rewrite the
join because the right alias name is workload-specific (``df.alias("l")``
vs ``df.alias("r")`` etc.) and we don't want to fight downstream column
references.

Trigger
-------

A ``Call`` whose ``func`` is ``Attribute(attr=Name("join"))`` AND:
  * ``func.value`` is a ``Name`` (call it ``L``),
  * at least one positional arg is a ``Name`` equal to ``L``,
  * neither side has an ``.alias(...)`` call in the chain visible to us.

We don't trigger when either side is wrapped in ``.alias("...")`` --
the user has already done the right thing.

Negative cases (must NOT trigger)
---------------------------------

* ``a.join(b, ...)`` -- different names.
* ``a.alias("l").join(a.alias("r"), ...)`` -- aliased.
* ``a.join(a.alias("r"), ...)`` -- one side aliased; still ambiguous in
  principle if both reference the same lineage, but PySpark resolves
  the aliased side cleanly. Skip to avoid noisy warnings.
* ``a.join([a], ...)`` -- list of joins is non-standard; skip.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "self_join_unaliased_warn_annotate"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS-WARN: [SPRKCNTPY5400-Warning] {RECIPE_ID}: self-join with the same DataFrame name on "
    f"both sides; column references after the join may be ambiguous in "
    f"SCOS -- consider aliasing one side, e.g. df.alias('l').join("
    f"df.alias('r'), ...)."
)


def _chain_calls_alias(expr: cst.BaseExpression) -> bool:
    """True iff ``expr`` is a chain whose terminal call is
    ``.alias("...")`` (or contains ``.alias`` anywhere up the chain)."""
    node: cst.CSTNode | None = expr
    seen = 0
    while node is not None and seen < 100:
        seen += 1
        if isinstance(node, cst.Call):
            if (
                isinstance(node.func, cst.Attribute)
                and isinstance(node.func.attr, cst.Name)
                and node.func.attr.value == "alias"
            ):
                return True
            node = node.func
            continue
        if isinstance(node, cst.Attribute):
            node = node.value
            continue
        return False
    return False


def _root_name(expr: cst.BaseExpression) -> Optional[str]:
    """Return the root ``Name`` of an attribute/call chain, else None.

    Example: for ``df`` -> ``"df"``; for ``df.filter(...).select(...)`` ->
    ``"df"``; for ``F.col("x")`` -> ``"F"``; for ``self.df`` -> ``None``
    (we deliberately don't unwrap attribute access -- ``self.df`` is a
    different identity binding than ``df`` and the user might already
    have re-aliased).
    """
    node: cst.CSTNode | None = expr
    seen = 0
    while node is not None and seen < 100:
        seen += 1
        if isinstance(node, cst.Name):
            return node.value
        if isinstance(node, cst.Call):
            node = node.func
            continue
        if isinstance(node, cst.Attribute):
            return None
        return None
    return None


class _Detector(cst.CSTVisitor):
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
        if node.func.attr.value != "join":
            return
        if not node.args:
            return
        left_name = _root_name(node.func.value)
        if left_name is None:
            return
        if _chain_calls_alias(node.func.value):
            return
        # The first positional arg is the right-hand DataFrame in PySpark.
        first_pos = next(
            (a for a in node.args if a.keyword is None), None
        )
        if first_pos is None:
            return
        right_name = _root_name(first_pos.value)
        if right_name is None:
            return
        if _chain_calls_alias(first_pos.value):
            return
        if left_name == right_name:
            self.matched = True


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
        self._record(start, "annotated unaliased self-join")
        return _annotate.prepend_comment(updated_node, _COMMENT_TEXT)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
