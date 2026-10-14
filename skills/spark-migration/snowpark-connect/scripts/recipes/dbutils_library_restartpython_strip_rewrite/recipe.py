"""Remove ``dbutils.library.restartPython()`` — a no-op on SCOS.

What it does
------------

Databricks' ``dbutils.library.restartPython()`` restarts the notebook's Python
interpreter after a library install. Snowflake Workspace notebooks / SCOS have
no such concept, and the call raises ``NameError`` (no ``dbutils`` runtime).

This recipe deletes any standalone expression statement that is exactly a
``dbutils.library.restartPython()`` call. Per transformation-rules.md rule 36
the action is simply "remove the call". The deletion is recorded in
``recipe_edits`` for traceability.

Targeted shape
--------------

A ``SimpleStatementLine`` whose only body element is an ``Expr`` wrapping the
call ``dbutils.library.restartPython()`` (no args). Anything else — the call
embedded in a larger statement, a non-empty arg list, or a different
``dbutils.library.*`` method (e.g. ``installPyPI`` — see
``dbutils_library_installpypi_stub_rewrite``) — is left untouched.

Idempotency
-----------

The statement is gone after the first pass; re-running is a no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "dbutils_library_restartpython_strip_rewrite"
MIN_SCOS_VERSION = "0.4.0"


def _is_restart_python(call: cst.Call) -> bool:
    """``dbutils.library.restartPython()`` with no positional/keyword args."""
    if call.args:
        return False
    func = call.func  # Attribute(value=Attribute(value=Name("dbutils"), attr="library"), attr="restartPython")
    return (
        isinstance(func, cst.Attribute)
        and isinstance(func.attr, cst.Name)
        and func.attr.value == "restartPython"
        and isinstance(func.value, cst.Attribute)
        and isinstance(func.value.attr, cst.Name)
        and func.value.attr.value == "library"
        and isinstance(func.value.value, cst.Name)
        and func.value.value.value == "dbutils"
    )


def _stmt_is_only_restart(stmt: cst.SimpleStatementLine) -> bool:
    """True iff ``stmt`` is exactly ``dbutils.library.restartPython()``."""
    if len(stmt.body) != 1:
        return False
    inner = stmt.body[0]
    return (
        isinstance(inner, cst.Expr)
        and isinstance(inner.value, cst.Call)
        and _is_restart_python(inner.value)
    )


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> Union[cst.SimpleStatementLine, cst.RemovalSentinel]:
        if not _stmt_is_only_restart(updated_node):
            return updated_node
        self._record(self._line_of(original_node), "removed dbutils.library.restartPython()")
        return cst.RemoveFromParent()


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
