"""Stub out ``dbutils.secrets.get(...)`` with ``None`` + a migration TODO.

What it does
------------

Databricks ``dbutils.secrets.get(scope=..., key=...)`` (and ``getArgument``)
has no Snowpark Connect / Snowflake Workspace equivalent — there is no
``dbutils`` runtime. Leaving the call in place raises ``NameError`` at import
time, which masks every downstream issue in the file.

This recipe rewrites only the *call expression*::

    token = dbutils.secrets.get(scope="kv", key="db-pw")
    ->
    # SCOS-TODO: [SPRKCNTPY1000] dbutils_secrets_get_stub_rewrite: dbutils.secrets
    #   has no SCOS equivalent; stubbed to None. Migrate to Snowflake Secrets.
    token = None

The assignment target (``token``) is preserved so downstream cells that read
the variable still resolve. This mirrors transformation-rules.md rule 13
("comment out the call, assign a placeholder so downstream cells resolve").

Targeted shapes
---------------

* ``dbutils.secrets.get(...)``
* ``dbutils.secrets.getArgument(...)``

Both as an assigned RHS and as a bare expression statement. Non-``dbutils``
receivers and other ``dbutils.secrets.*`` methods (``list``/``listScopes``/
``getBytes`` — see rule 38) are left untouched.

Idempotency
-----------

Re-running on stubbed source is a no-op: the call is already gone, and the
leading-comment guard prevents a second TODO.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "dbutils_secrets_get_stub_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_TARGET_METHODS = frozenset({"get", "getArgument"})

_COMMENT_TEXT = (
    f"# SCOS-TODO: [SPRKCNTPY1000] {RECIPE_ID}: dbutils.secrets has no SCOS "
    f"equivalent; stubbed to None. Migrate to Snowflake Secrets."
)


def _is_secrets_get(call: cst.Call) -> bool:
    """``dbutils.secrets.get(...)`` / ``dbutils.secrets.getArgument(...)``."""
    func = call.func
    if not (isinstance(func, cst.Attribute) and isinstance(func.attr, cst.Name)):
        return False
    if func.attr.value not in _TARGET_METHODS:
        return False
    secrets_attr = func.value  # expect Attribute(value=Name("dbutils"), attr="secrets")
    return (
        isinstance(secrets_attr, cst.Attribute)
        and isinstance(secrets_attr.attr, cst.Name)
        and secrets_attr.attr.value == "secrets"
        and isinstance(secrets_attr.value, cst.Name)
        and secrets_attr.value.value == "dbutils"
    )


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._rewritten_lines: set[int] = set()

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if not _is_secrets_get(updated_node):
            return updated_node
        line = self._line_of(original_node)
        self._rewritten_lines.add(line)
        self._record(line, "dbutils.secrets.get -> None")
        return cst.Name("None")

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        line = self._line_of(original_node)
        if line not in self._rewritten_lines:
            return updated_node
        if _annotate.comment_above_contains(self._lines, line, RECIPE_ID):
            return updated_node
        return _annotate.prepend_comment(updated_node, _COMMENT_TEXT)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
