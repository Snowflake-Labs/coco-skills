"""Comment out catalog switches — ``spark.sql("USE CATALOG ...")`` /
``SET CATALOG`` and the DataFrame/Catalog-API form
``spark.catalog.setCurrentCatalog(...)`` — Snowflake only has the built-in
``snowflake`` catalog.

What it does
------------

Databricks Unity Catalog exposes a three-level namespace
(``catalog.schema.table``) and lets a notebook switch the *current catalog*
with ``USE CATALOG <name>`` (or the OSS-Spark spelling ``SET CATALOG <name>``),
or programmatically via the Catalog API ``spark.catalog.setCurrentCatalog(name)``.

Snowpark Connect runs on Snowflake, whose object hierarchy is
``database.schema.table`` and which exposes a **single, built-in catalog**
(``snowflake`` / the Spark alias ``spark_catalog``). There is **no**
``USE CATALOG`` statement in Snowflake SQL, and SCOS does not translate it:
the string is passed through and Snowflake rejects it with a syntax error
(``USE`` only accepts ``DATABASE`` / ``SCHEMA`` / ``ROLE`` / ``WAREHOUSE``).
The Catalog-API call ``setCurrentCatalog`` is the programmatic equivalent and
is equally unsupported — there is only one catalog to switch to.

Because "switch catalog" has no faithful Snowflake equivalent, this recipe
does not silently rewrite it to ``USE DATABASE`` (which would change
semantics and can be wrong when the Unity catalog name does not match a
Snowflake database). Instead it **comments the statement out** — preserving
the original as a comment for traceability — and prepends a ``# SCOS:`` note
telling developers what to do instead:

* qualify tables with **fully-qualified names** (``database.schema.table``),
  which map naturally from Unity's ``catalog.schema.table``; or
* change the session's default database/schema explicitly via
  **``SnowflakeSession``** (``SnowflakeSession(spark).use_database(...)`` /
  ``.use_schema(...)``) or ``spark.sql("USE DATABASE ...")``.

Targeted statement shapes
-------------------------

Either of the following as a standalone expression-statement:

1. A ``*.sql("<sql>")`` call (optionally chained with ``.collect()`` /
   ``.show()`` etc.) where ``<sql>`` is a *static* string literal whose first
   keywords are ``USE CATALOG`` or ``SET CATALOG`` (case-insensitive):

   * ``spark.sql("USE CATALOG na_global_risk_systems_explore")``
   * ``spark.sql("USE CATALOG my_catalog").collect()``
   * ``spark.sql("set catalog my_catalog")``

2. A ``*.setCurrentCatalog(...)`` call — the PySpark Catalog-API equivalent
   of ``USE CATALOG`` (matched on the method name, so any receiver spelling
   works):

   * ``spark.catalog.setCurrentCatalog("my_catalog")``
   * ``session.catalog.setCurrentCatalog(name)``

Standalone matches are commented out (body replaced with ``pass``).

Conservative skip / annotate-only
---------------------------------

If the catalog switch is embedded in a larger expression (assignment
RHS, argument, chained into other logic) the recipe does **not** delete it —
commenting it out could produce broken code. It instead leaves a
``# SCOS-TODO:`` annotation for the LLM fixer / human to resolve.

Negative cases (must NOT trigger)
---------------------------------

* ``spark.sql("USE DATABASE x")`` / ``spark.sql("USE SCHEMA x")`` /
  ``spark.sql("USE x")`` — supported catalog-namespace switches → skip.
* ``spark.sql(f"USE CATALOG {name}")`` / ``spark.sql(q)`` — dynamic SQL we
  cannot statically read → skip (``_string_value`` returns ``None``).
* ``spark.catalog.setCurrentDatabase("db")`` / ``.currentCatalog()`` — the
  database switch is supported (maps to ``USE SCHEMA``) and the getter is not
  a switch → skip.

Idempotency
-----------

Re-running on already-commented source is a no-op (recipe marker check).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "spark_sql_use_catalog_comment_out_rewrite"
MIN_SCOS_VERSION = "0.4.0"

# ``USE CATALOG <name>`` / ``SET CATALOG <name>`` (case-insensitive), tolerating
# leading whitespace/comments. The trailing ``\b`` ensures we do not match a
# database literally named ``catalog`` in ``USE catalogdb``.
_USE_CATALOG_RE = re.compile(r"^\s*(?:USE|SET)\s+CATALOG\b", re.IGNORECASE)

_SCOS_COMMENT = (
    f"# SCOS: [SPRKCNTPY3300-Fixed] {RECIPE_ID}: commented out catalog switch "
    f"(USE/SET CATALOG or catalog.setCurrentCatalog) — Snowflake only supports "
    f"the built-in `snowflake` catalog. Use fully-qualified names "
    f"(database.schema.table) or SnowflakeSession (use_database/use_schema) — "
    f"e.g. session.sql(\"USE DATABASE ...\") / session.sql(\"USE SCHEMA ...\") — "
    f"to change the default database/schema."
)
_SCOS_TODO = (
    f"# SCOS-TODO: [SPRKCNTPY3300-Error] {RECIPE_ID}: catalog switch (USE/SET "
    f"CATALOG or catalog.setCurrentCatalog) is not supported (Snowflake has one "
    f"built-in `snowflake` catalog). This call is embedded in a larger "
    f"expression — remove it and switch context via fully-qualified names or "
    f"SnowflakeSession.use_database/use_schema."
)


def _is_use_catalog_sql(sql: str) -> bool:
    return bool(_USE_CATALOG_RE.match(sql))


def _sql_call_has_use_catalog(node: cst.CSTNode) -> bool:
    """True iff ``node`` is a ``*.sql("USE/SET CATALOG ...")`` call with a
    static string first positional argument."""
    if not (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value == "sql"
    ):
        return False
    for arg in node.args:
        if arg.keyword is not None:
            continue
        s = _annotate._string_value(arg.value)
        if s is None:
            return False  # first positional is dynamic → cannot classify
        return _is_use_catalog_sql(s)
    return False


def _is_set_current_catalog(node: cst.CSTNode) -> bool:
    """True iff ``node`` is a ``*.setCurrentCatalog(...)`` call — the PySpark
    Catalog-API equivalent of ``USE CATALOG``. Matched on the method name so
    it fires regardless of the receiver spelling (``spark.catalog``,
    ``session.catalog``, an aliased catalog handle, …). ``setCurrentDatabase``
    is intentionally *not* matched: the database switch is supported."""
    return (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value == "setCurrentCatalog"
    )


def _is_catalog_switch(node: cst.CSTNode) -> bool:
    """True for either catalog-switch shape: ``*.sql("USE/SET CATALOG ...")``
    or ``*.setCurrentCatalog(...)``."""
    return _sql_call_has_use_catalog(node) or _is_set_current_catalog(node)


class _Finder(cst.CSTVisitor):
    """Detect any catalog switch (``*.sql("USE/SET CATALOG ...")`` or
    ``*.setCurrentCatalog(...)``) in a subtree."""

    def __init__(self) -> None:
        super().__init__()
        self.found = False

    def visit_Call(self, node: cst.Call) -> None:
        if _is_catalog_switch(node):
            self.found = True


def _statement_contains_catalog_switch(stmt: cst.SimpleStatementLine) -> bool:
    finder = _Finder()
    stmt.visit(finder)
    return finder.found


def _is_standalone_catalog_switch(stmt: cst.SimpleStatementLine) -> bool:
    """True iff the statement body is a single bare expression whose outermost
    call chain resolves to a catalog switch — i.e. the whole statement's only
    job is the catalog switch (optionally chained with ``.collect()`` /
    ``.show()`` etc.)."""
    if len(stmt.body) != 1:
        return False
    small = stmt.body[0]
    if not isinstance(small, cst.Expr):
        return False
    expr = small.value
    # Unwrap trailing method chains (``.collect()``, ``.show()`` …) to find the
    # underlying catalog-switch call.
    while isinstance(expr, cst.Call):
        if _is_catalog_switch(expr):
            return True
        func = expr.func
        if isinstance(func, cst.Attribute):
            expr = func.value
            continue
        break
    return False


def _source_of_statement(stmt: cst.SimpleStatementLine) -> str:
    """Render the code portion of the statement (no leading comments/blanks)."""
    mod = cst.Module(body=[stmt.with_changes(leading_lines=())])
    return mod.code.rstrip("\n")


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

        if not _statement_contains_catalog_switch(updated_node):
            return updated_node

        if _is_standalone_catalog_switch(updated_node):
            # Comment out the whole statement, preserving the original as a
            # comment, and replace the body with ``pass``.
            original_code = _source_of_statement(updated_node)
            new_leading = list(updated_node.leading_lines) + [
                cst.EmptyLine(comment=cst.Comment(_SCOS_COMMENT)),
                cst.EmptyLine(comment=cst.Comment(f"# {original_code}")),
            ]
            new_stmt = updated_node.with_changes(
                leading_lines=tuple(new_leading),
                body=[cst.Pass()],
            )
            self._record(start, "commented out catalog switch (no Snowflake equivalent)")
            return new_stmt

        # Embedded in a larger expression — annotate only, do not remove.
        new_stmt = _annotate.prepend_comment(updated_node, _SCOS_TODO)
        self._record(start, "annotated embedded catalog switch (needs manual fix)")
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
