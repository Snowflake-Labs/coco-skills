"""Insert a ``df.cache()`` line before each ``df.createOrReplaceTempView(...)``
(and the global / non-replacing variants).

What it does
------------

Temporary views in PySpark don't materialize on registration -- they
just save a logical plan that's re-evaluated on each ``spark.sql(...)``
read. In SCOS, temp views are equally lazy. When the same view is read
multiple times downstream, the source DataFrame is recomputed each
time, which can dominate runtime for non-trivial pipelines.

The empirically-confirmed mitigation is to ``cache()`` the source
DataFrame before registering the view -- the next reads then hit the
cached materialization. This recipe inserts that single ``df.cache()``
line above::

    df.createOrReplaceTempView("v")
    df.createTempView("v")
    df.createGlobalTempView("v")
    df.createOrReplaceGlobalTempView("v")

The receiver must be a bare ``Name`` (so we have a stable variable to
``.cache()`` on); chained-call receivers (``spark.read.json(...)
.createOrReplaceTempView(...)``) are not rewritten because there is
no stable name to bind ``.cache()`` to without introducing a new
variable (left to the LLM fixer in the next phase).

Trigger
-------

A ``SimpleStatementLine`` whose single body element is an ``Expr``
wrapping a ``Call`` whose:
  * ``func.attr.value`` is one of the temp-view registration methods,
  * ``func.value`` is a bare ``Name``.

Negative cases (must NOT trigger)
---------------------------------

* Receiver is a chain (``df.filter(...).createOrReplaceTempView(...)``).
* The previous sibling statement is already ``<same-name>.cache()``
  (idempotent).
* The previous sibling statement is ``<same-name>.persist(...)`` -- we
  also treat persist as caching for this purpose.

Idempotency
-----------

After rewrite, the line above the create-view call is ``df.cache()``,
which the recipe recognizes as "already cached" -- re-running is a
no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "tempview_multiuse_cache_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_TARGET_METHODS = frozenset(
    {
        "createOrReplaceTempView",
        "createTempView",
        "createOrReplaceGlobalTempView",
        "createGlobalTempView",
    }
)
_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY6500-Fixed] {RECIPE_ID}: temp views are lazy in SCOS; cache() the "
    f"source DataFrame before registration so multi-use queries don't "
    f"recompute the underlying plan."
)


def _extract_create_view_call(stmt: cst.SimpleStatementLine):
    """Return (receiver_name, method_name) iff ``stmt`` is a bare-Expr
    call to a temp-view registration method on a Name receiver.
    Otherwise return None.
    """
    if len(stmt.body) != 1:
        return None
    small = stmt.body[0]
    if not isinstance(small, cst.Expr):
        return None
    call = small.value
    if not isinstance(call, cst.Call):
        return None
    if not isinstance(call.func, cst.Attribute):
        return None
    if not isinstance(call.func.attr, cst.Name):
        return None
    if call.func.attr.value not in _TARGET_METHODS:
        return None
    if not isinstance(call.func.value, cst.Name):
        return None
    return (call.func.value.value, call.func.attr.value)


def _is_cache_call_on(stmt: cst.SimpleStatementLine, name: str) -> bool:
    """True iff ``stmt`` is ``<name>.cache()`` or ``<name>.persist(...)``."""
    if len(stmt.body) != 1:
        return False
    small = stmt.body[0]
    if not isinstance(small, cst.Expr):
        return False
    call = small.value
    if not isinstance(call, cst.Call):
        return False
    if not isinstance(call.func, cst.Attribute):
        return False
    if not isinstance(call.func.attr, cst.Name):
        return False
    if call.func.attr.value not in ("cache", "persist"):
        return False
    if not isinstance(call.func.value, cst.Name):
        return False
    return call.func.value.value == name


def _build_cache_stmt(name: str) -> cst.SimpleStatementLine:
    call = cst.Call(
        func=cst.Attribute(
            value=cst.Name(name),
            attr=cst.Name("cache"),
        ),
        args=[],
    )
    return cst.SimpleStatementLine(
        body=[cst.Expr(value=call)],
        leading_lines=[cst.EmptyLine(comment=cst.Comment(_COMMENT_TEXT))],
    )


def _transform_body(
    body_items, recipe: "_Recipe", original_body_items
):
    """Walk a body's statements and insert ``<name>.cache()`` before any
    temp-view registration that doesn't already have one above it.

    Operates on ``updated_node`` body items but consults
    ``original_body_items`` for PositionProvider lookups via
    ``recipe._line_of``.
    """
    new_items: list = []
    prev_orig: Optional[cst.CSTNode] = None
    for i, item in enumerate(body_items):
        orig = original_body_items[i] if i < len(original_body_items) else None
        if isinstance(item, cst.SimpleStatementLine):
            view = _extract_create_view_call(item)
            if view is not None:
                name, _method = view
                already_cached = False
                # Look at the previous statement (skip blank lines).
                if new_items:
                    prev_new = new_items[-1]
                    if isinstance(prev_new, cst.SimpleStatementLine) and \
                            _is_cache_call_on(prev_new, name):
                        already_cached = True
                if not already_cached:
                    # Original line of the create-view statement.
                    if orig is not None:
                        recipe._record(
                            recipe._line_of(orig),
                            "inserted cache() before temp-view",
                        )
                    new_items.append(_build_cache_stmt(name))
        new_items.append(item)
        prev_orig = orig
    return tuple(new_items)


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def leave_Module(  # type: ignore[override]
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        new_body = _transform_body(
            list(updated_node.body), self, list(original_node.body)
        )
        return updated_node.with_changes(body=new_body)

    def leave_IndentedBlock(  # type: ignore[override]
        self,
        original_node: cst.IndentedBlock,
        updated_node: cst.IndentedBlock,
    ) -> cst.IndentedBlock:
        new_body = _transform_body(
            list(updated_node.body), self, list(original_node.body)
        )
        return updated_node.with_changes(body=new_body)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
