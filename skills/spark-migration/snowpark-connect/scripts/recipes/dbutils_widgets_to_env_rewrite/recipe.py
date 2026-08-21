"""Rewrite Databricks ``dbutils.widgets.*`` parameter calls to ``os.environ``.

What it does
------------

``dbutils.widgets`` is a Databricks-notebook-only API for job parameters; it
does not exist in SCOS / Snowpark Connect. The standard, runnable replacement is
environment-variable parameterisation::

    dbutils.widgets.text("yr", "2024")        ->   os.environ.setdefault("yr", "2024")
    dbutils.widgets.get("yr")                 ->   os.environ["yr"]
    int(dbutils.widgets.get("yr"))            ->   int(os.environ["yr"])
    dbutils.widgets.getArgument("yr", "0")    ->   os.environ.get("yr", "0")
    dbutils.widgets.remove("yr")              ->   os.environ.pop("yr", None)

Declaration calls (``text`` / ``dropdown`` / ``combobox`` / ``multiselect``) carry
the default as the 2nd positional arg, so they become ``setdefault(name, default)``
— preserving the default while letting an externally-set env var override it.
This yields code that actually runs, instead of leaving an unsupported
``dbutils`` reference behind for a human to fix.

A single ``import os`` is injected at the top of the module if a rewrite fired
and ``os`` is not already imported.

Negative cases (must NOT trigger)
---------------------------------

* ``dbutils.widgets.removeAll()`` -- no clean per-key env equivalent; left for
  annotation/LLM.
* ``dbutils.fs.* / dbutils.secrets.* / dbutils.notebook.*`` -- not widgets;
  handled by their own rules (contextual: stage paths, secrets, control flow).
* ``x.widgets.get(...)`` where the receiver is not the ``dbutils`` global.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "dbutils_widgets_to_env_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY3200-Fixed] {RECIPE_ID}: dbutils.widgets.* -> os.environ "
    f"(env-var parameterisation; set the env var to override the default)"
)

# widget methods that DECLARE a parameter with a default (2nd positional arg).
_DECLARE = {"text", "dropdown", "combobox", "multiselect"}

_OS_ENVIRON = cst.Attribute(value=cst.Name("os"), attr=cst.Name("environ"))


def _is_dbutils_widgets(value: cst.BaseExpression) -> bool:
    """True iff ``value`` is the attribute chain ``dbutils.widgets``."""
    return (
        isinstance(value, cst.Attribute)
        and isinstance(value.attr, cst.Name)
        and value.attr.value == "widgets"
        and isinstance(value.value, cst.Name)
        and value.value.value == "dbutils"
    )


def _method_of(call: cst.Call) -> Optional[str]:
    """Return the widget method name iff ``call`` is ``dbutils.widgets.<m>(...)``."""
    func = call.func
    if (
        isinstance(func, cst.Attribute)
        and isinstance(func.attr, cst.Name)
        and _is_dbutils_widgets(func.value)
    ):
        return func.attr.value
    return None


def _pos_args(call: cst.Call) -> list[cst.Arg]:
    return [a for a in call.args if a.keyword is None and not a.star]


def _rewrite(call: cst.Call, method: str) -> Optional[cst.BaseExpression]:
    pos = _pos_args(call)
    if method == "get" and len(pos) >= 1:
        # os.environ[name]
        return cst.Subscript(
            value=_OS_ENVIRON,
            slice=[cst.SubscriptElement(slice=cst.Index(value=pos[0].value))],
        )
    if method == "getArgument" and len(pos) >= 1:
        args = [cst.Arg(pos[0].value)]
        if len(pos) >= 2:
            args.append(cst.Arg(pos[1].value))
        return cst.Call(func=cst.Attribute(_OS_ENVIRON, cst.Name("get")), args=args)
    if method in _DECLARE and len(pos) >= 2:
        # os.environ.setdefault(name, default)
        return cst.Call(
            func=cst.Attribute(_OS_ENVIRON, cst.Name("setdefault")),
            args=[cst.Arg(pos[0].value), cst.Arg(pos[1].value)],
        )
    if method == "remove" and len(pos) >= 1:
        # os.environ.pop(name, None)
        return cst.Call(
            func=cst.Attribute(_OS_ENVIRON, cst.Name("pop")),
            args=[cst.Arg(pos[0].value), cst.Arg(cst.Name("None"))],
        )
    return None  # removeAll(), or unexpected arity -> leave untouched


class _CallRewriter(cst.CSTTransformer):
    def __init__(self) -> None:
        super().__init__()
        self.rewrites = 0

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        method = _method_of(updated_node)
        if method is None:
            return updated_node
        new = _rewrite(updated_node, method)
        if new is None:
            return updated_node
        self.rewrites += 1
        return new


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


def _imports_os(module: cst.Module) -> bool:
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for s in stmt.body:
            if isinstance(s, cst.Import):
                for alias in s.names:
                    if isinstance(alias.name, cst.Name) and alias.name.value == "os":
                        return True
    return False


_IMPORT_OS = cst.SimpleStatementLine(body=[cst.Import(names=[cst.ImportAlias(name=cst.Name("os"))])])


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.total = 0

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
        self.total += sub.rewrites
        self._record(
            self._line_of(original_node),
            f"dbutils.widgets.* -> os.environ ({sub.rewrites} call(s))",
        )
        return new_stmt

    def leave_Module(  # type: ignore[override]
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        if self.total == 0 or _imports_os(updated_node):
            return updated_node
        return updated_node.with_changes(body=(_IMPORT_OS,) + tuple(updated_node.body))


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
