"""Rewrite map Column-key lookups to ``element_at(map, key)``.

Covers:
  * ``df["mapcol"][df["key"]]`` / ``col("m")[col("k")]``
  * ``create_map(...).getItem(col("k"))`` (also ``map_from_arrays`` /
    ``map_from_entries``)

``.getItem`` on a generic Column is **not** rewritten: that API is
shared with arrays, where ``getItem`` is 0-based and ``element_at`` is
1-based.

What it does
------------

PySpark accepts a Column as the index of a map-typed Column::

    df.select(df["m"][df["k"]])
    df.select(col("m")[col("k")])

In SCOS this consistently fails or returns NULL because the Snowflake
``GET`` SQL function used under the hood requires a literal or scalar
key. The supported pattern is the explicit ``element_at(map, key)``
function from ``pyspark.sql.functions``, which SCOS maps to
``OBJECT_GET`` with proper Column-typed key handling.

The recipe rewrites every such subscript to ``element_at(<outer>,
<key>)`` and ensures ``element_at`` is imported from
``pyspark.sql.functions`` exactly once.

Trigger
-------

A ``Subscript`` node where:
  * the value is a *Column-typed* expression -- approximated by
    "another Subscript" (``df["m"]``), a ``Call`` to ``col`` or
    ``F.col`` / ``functions.col``, or any Attribute access on a Name
    (e.g. ``df.m`` is *not* matched because we can't tell statically
    that it is a map, but ``df["m"]`` is matched), AND
  * the slice is itself a Column-typed expression: a ``Subscript`` or
    a ``Call`` (not a literal int, float, or string).

The literal-key case (``df["m"]["x"]``) is **not rewritten** because
PySpark / SCOS both handle it fine with the literal path.

Negative cases (must NOT trigger)
---------------------------------

* ``df["int_col"][0]`` -- integer index on an array; supported
  natively in SCOS.
* ``col("arr").getItem(col("i"))`` -- array getItem; 0-based vs
  ``element_at`` 1-based, so left alone.
* ``df["str_col"][1:5]`` -- slicing; different surface.
* ``df["m"]["k"]`` -- literal-key map subscript; supported.
* Already-rewritten ``element_at(...)``.

Idempotency
-----------

After rewrite the construct is a Call (to ``element_at``), no longer a
Subscript -- so re-running is a no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "map_column_subscript_colkey_to_element_at_rewrite"
MIN_SCOS_VERSION = "0.4.0"


def _is_literal_key(slice_expr: cst.BaseExpression) -> bool:
    """True iff the subscript index is a constant literal (string / int /
    float / bool / None)."""
    return isinstance(
        slice_expr,
        (
            cst.SimpleString,
            cst.ConcatenatedString,
            cst.Integer,
            cst.Float,
            cst.Imaginary,
            cst.FormattedString,
        ),
    ) or (
        isinstance(slice_expr, cst.Name)
        and slice_expr.value in ("True", "False", "None")
    )


def _is_col_like(expr: cst.BaseExpression) -> bool:
    """Heuristic for "looks like a Column expression"."""
    if isinstance(expr, cst.Subscript):
        return True
    if isinstance(expr, cst.Call):
        # ``col(...)``, ``F.col(...)``, ``functions.col(...)``, etc.
        fn = expr.func
        if isinstance(fn, cst.Name) and fn.value == "col":
            return True
        if (
            isinstance(fn, cst.Attribute)
            and isinstance(fn.attr, cst.Name)
            and fn.attr.value == "col"
        ):
            return True
        # Any other Call -- could be a Column-returning function (lit,
        # expr, etc.). We trust the user; this is intentionally broad.
        return True
    if isinstance(expr, cst.Attribute):
        # ``df.col_name`` -- treat as Column-like.
        return True
    return False


def _is_target_subscript(node: cst.Subscript) -> bool:
    """True iff ``node`` is ``<map_col_like>[<col_key>]`` with a
    Column-typed key (i.e. not a constant)."""
    # Outer must be a Column-like expression.
    if not _is_col_like(node.value):
        return False
    # Only one slice element -- multi-dim Subscript is not Spark.
    if len(node.slice) != 1:
        return False
    slc = node.slice[0]
    if not isinstance(slc, cst.SubscriptElement):
        return False
    if not isinstance(slc.slice, cst.Index):
        return False
    inner = slc.slice.value
    # Literal-key path is fine in SCOS -- skip.
    if _is_literal_key(inner):
        return False
    # Column-typed key.
    return _is_col_like(inner)


def _rewrite(node: cst.Subscript) -> cst.Call:
    """Return ``element_at(<value>, <key>)``."""
    slc = node.slice[0]
    assert isinstance(slc, cst.SubscriptElement)
    assert isinstance(slc.slice, cst.Index)
    inner = slc.slice.value
    return cst.Call(
        func=cst.Name("element_at"),
        args=[
            cst.Arg(value=node.value),
            cst.Arg(value=inner),
        ],
    )


# ---------------------------------------------------------------------------
# Import injection: ``from pyspark.sql.functions import element_at``
# ---------------------------------------------------------------------------


def _has_element_at_import(module: cst.Module) -> bool:
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for s in stmt.body:
            if isinstance(s, cst.ImportFrom):
                mod_name = ""
                if isinstance(s.module, cst.Name):
                    mod_name = s.module.value
                elif isinstance(s.module, cst.Attribute):
                    # Reconstruct dotted form (pyspark.sql.functions).
                    parts = []
                    node: cst.CSTNode | None = s.module
                    while isinstance(node, cst.Attribute):
                        if isinstance(node.attr, cst.Name):
                            parts.append(node.attr.value)
                        node = node.value
                    if isinstance(node, cst.Name):
                        parts.append(node.value)
                    mod_name = ".".join(reversed(parts))
                if mod_name not in ("pyspark.sql.functions",):
                    continue
                if isinstance(s.names, cst.ImportStar):
                    return True
                for n in s.names:
                    if (
                        isinstance(n, cst.ImportAlias)
                        and isinstance(n.name, cst.Name)
                        and n.name.value == "element_at"
                    ):
                        return True
    return False


_IMPORT_NODE = cst.SimpleStatementLine(
    body=[
        cst.ImportFrom(
            module=cst.Attribute(
                value=cst.Attribute(
                    value=cst.Name("pyspark"),
                    attr=cst.Name("sql"),
                ),
                attr=cst.Name("functions"),
            ),
            names=[cst.ImportAlias(name=cst.Name("element_at"))],
            relative=[],
        )
    ]
)


def _ensure_import(module: cst.Module) -> cst.Module:
    if _has_element_at_import(module):
        return module
    body = list(module.body)
    insert_at = 0
    for i, stmt in enumerate(body):
        if isinstance(stmt, cst.SimpleStatementLine) and any(
            isinstance(s, (cst.Import, cst.ImportFrom)) for s in stmt.body
        ):
            insert_at = i + 1
        elif insert_at > 0:
            break
    new_body = body[:insert_at] + [_IMPORT_NODE] + body[insert_at:]
    return module.with_changes(body=tuple(new_body))


_MAP_CTORS = frozenset({"create_map", "map_from_arrays", "map_from_entries"})


def _call_func_name(func: cst.BaseExpression) -> Optional[str]:
    if isinstance(func, cst.Name):
        return func.value
    if isinstance(func, cst.Attribute) and isinstance(func.attr, cst.Name):
        return func.attr.value
    return None


def _is_map_ctor(expr: cst.BaseExpression) -> bool:
    """True for ``create_map`` / ``map_from_arrays`` / ``map_from_entries``."""
    return isinstance(expr, cst.Call) and _call_func_name(expr.func) in _MAP_CTORS


def _is_getitem_colkey(call: cst.Call) -> bool:
    """Rewrite only map-ctor ``.getItem(Column)``, never array ``.getItem``."""
    if not (
        isinstance(call.func, cst.Attribute)
        and isinstance(call.func.attr, cst.Name)
        and call.func.attr.value == "getItem"
        and len(call.args) == 1
    ):
        return False
    if not _is_map_ctor(call.func.value):
        return False
    return _is_col_like(call.args[0].value) and not _is_literal_key(call.args[0].value)


def _rewrite_getitem(call: cst.Call) -> cst.Call:
    return cst.Call(
        func=cst.Name("element_at"),
        args=[
            cst.Arg(value=call.func.value),  # type: ignore[union-attr]
            cst.Arg(value=call.args[0].value),
        ],
    )


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.rewrites_made = 0

    def leave_Call(  # type: ignore[override]
        self,
        original_node: cst.Call,
        updated_node: cst.Call,
    ) -> cst.BaseExpression:
        if not _is_getitem_colkey(updated_node):
            return updated_node
        line = self._line_of(original_node)
        self._record(line, "map.getItem(ColKey) -> element_at")
        self.rewrites_made += 1
        return _rewrite_getitem(updated_node)

    def leave_Subscript(  # type: ignore[override]
        self,
        original_node: cst.Subscript,
        updated_node: cst.Subscript,
    ) -> cst.BaseExpression:
        if not _is_target_subscript(original_node):
            return updated_node
        # Use updated_node so any deeper rewrites (rare) are preserved.
        if not _is_target_subscript(updated_node):
            return updated_node
        line = self._line_of(original_node)
        self._record(line, "map[ColKey] -> element_at")
        self.rewrites_made += 1
        return _rewrite(updated_node)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    module = cst.parse_module(source)
    wrapper = cst.MetadataWrapper(module, unsafe_skip_copy=True)
    recipe = _Recipe(source=source, file=file, facts_db=facts_db)
    new_module = wrapper.visit(recipe)
    if recipe.rewrites_made > 0:
        new_module = _ensure_import(new_module)
    return _common.RecipeResult(
        source=new_module.code, edits=list(recipe.edits)
    )
