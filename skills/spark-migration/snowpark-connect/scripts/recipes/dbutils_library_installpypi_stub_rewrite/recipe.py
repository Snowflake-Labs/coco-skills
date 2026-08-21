"""Stub ``dbutils.library.installPyPI(...)`` with ``None`` + a pip-install TODO.

What it does
------------

Databricks' ``dbutils.library.installPyPI("pkg")`` installs a PyPI package into
the notebook session. There is no ``dbutils`` runtime on SCOS, and Snowflake
Workspace notebooks install packages differently (``!pip install`` /
notebook package picker / Snowflake-managed packages). The call raises
``NameError`` if left in place.

This recipe rewrites the *call expression* to ``None`` and prepends a
``# SCOS-TODO`` that preserves the package name extracted from the call, so the
owner knows exactly what to re-install. This mirrors transformation-rules.md
rule 35 ("convert to ``!pip install <package>``"), adapted to plain ``.py``
SCOS output where a ``!`` line-magic is not valid Python::

    dbutils.library.installPyPI("numpy", version="1.26")
    ->
    # SCOS-TODO: [SPRKCNTPY1000] dbutils_library_installpypi_stub_rewrite:
    #   install 'numpy' via Snowflake notebook packages / `!pip install numpy`.
    None

Targeted shape
--------------

A call ``dbutils.library.installPyPI(...)`` (any args). The package name is the
first positional string literal or the ``package=``/``pypiPackage=`` kwarg when
it is a string literal; otherwise the comment omits the name. Other
``dbutils.library.*`` methods are left to their own recipes.

Idempotency
-----------

Re-running is a no-op (call gone + leading-comment guard).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "dbutils_library_installpypi_stub_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_PKG_KWARGS = frozenset({"package", "pypiPackage"})


def _is_install_pypi(call: cst.Call) -> bool:
    """``dbutils.library.installPyPI(...)``."""
    func = call.func
    return (
        isinstance(func, cst.Attribute)
        and isinstance(func.attr, cst.Name)
        and func.attr.value == "installPyPI"
        and isinstance(func.value, cst.Attribute)
        and isinstance(func.value.attr, cst.Name)
        and func.value.attr.value == "library"
        and isinstance(func.value.value, cst.Name)
        and func.value.value.value == "dbutils"
    )


def _string_value(node: cst.CSTNode) -> Optional[str]:
    if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
        try:
            return node.evaluated_value
        except Exception:  # noqa: BLE001
            return None
    return None


def _package_name(call: cst.Call) -> Optional[str]:
    """First positional string literal, or a ``package=``/``pypiPackage=`` string."""
    for arg in call.args:
        if arg.keyword is None:
            s = _string_value(arg.value)
            if s is not None:
                return s
            return None  # first positional is non-literal -> give up
    for arg in call.args:
        if arg.keyword is not None and arg.keyword.value in _PKG_KWARGS:
            return _string_value(arg.value)
    return None


def _comment_for(pkg: Optional[str]) -> str:
    if pkg:
        return (
            f"# SCOS-TODO: [SPRKCNTPY1000] {RECIPE_ID}: install {pkg!r} via "
            f"Snowflake notebook packages / `!pip install {pkg}`."
        )
    return (
        f"# SCOS-TODO: [SPRKCNTPY1000] {RECIPE_ID}: install the package via "
        f"Snowflake notebook packages / `!pip install`."
    )


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._comment_by_line: dict[int, str] = {}

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if not _is_install_pypi(updated_node):
            return updated_node
        line = self._line_of(original_node)
        self._comment_by_line[line] = _comment_for(_package_name(updated_node))
        self._record(line, "dbutils.library.installPyPI -> None")
        return cst.Name("None")

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        line = self._line_of(original_node)
        comment = self._comment_by_line.get(line)
        if comment is None:
            return updated_node
        if _annotate.comment_above_contains(self._lines, line, RECIPE_ID):
            return updated_node
        return _annotate.prepend_comment(updated_node, comment)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
