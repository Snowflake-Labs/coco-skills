"""Rewrite the Spark **Snowflake connector** read/write to the SCOS-native
``SnowflakeSession`` / ``saveAsTable`` form.

What it does
------------

Under Snowpark Connect (SCOS) the workload already runs *inside* Snowflake, so
the Spark Snowflake connector (``.format("snowflake")`` /
``.format("net.snowflake.spark.snowflake")``) is unnecessary. The correct
replacements are:

**Reads** — run the query as native Snowflake SQL through ``SnowflakeSession``,
whose ``.sql()`` wraps the statement with a ``PRIVATE-SNOWFLAKE-SQL``
pass-through marker (see ``snowflake.snowpark_connect.snowflake_session``). A
bare ``spark.sql(...)`` would be parsed as *Spark* SQL and break on
Snowflake-specific syntax, so it is NOT a valid replacement::

    spark.read.format("snowflake").option("query", Q).load()
    ->
    SnowflakeSession(spark).sql(Q)

    <sess>.read.format("snowflake").option("dbtable", "DB.SC.T").load()
    ->
    SnowflakeSession(<sess>).sql("SELECT * FROM DB.SC.T")

**Writes** — write straight to a managed Snowflake table::

    df.write.format("snowflake").option("dbtable", T).mode("overwrite").save()
    ->
    df.write.mode("overwrite").saveAsTable(T)

Session generality
------------------

The rewrite never hardcodes ``spark``. It reuses the **actual receiver** to the
left of ``.read`` / ``.write`` (``spark``, ``spark_session``, ``self.spark``,
…) so it is correct under multiple / renamed sessions. ``SnowflakeSession`` is
constructed inline per call site (no module-level session variable) so it is
safe across notebook cell boundaries.

Import injection
----------------

When any read is rewritten, ``from
snowflake.snowpark_connect.snowflake_session import SnowflakeSession`` is
prepended to the top of the processed unit (a notebook cell, or the whole
module for a plain ``.py``) unless it is already imported. This recipe is
``NOTEBOOK_SCOPE == "cell"`` (the default): the rewrites are local, so the
import lands in the same cell that uses it.

Ambiguity fallback (never break code)
-------------------------------------

If the connector options are not statically extractable (options supplied via
``.options(**cfg)`` / a dict, or ``.option`` with a non-literal key, or a read
with neither ``query`` nor ``dbtable``, or a write with no ``dbtable``), the
recipe does **not** rewrite — it leaves the code intact and annotates a
``# SCOS: TODO`` so the LLM fixer / a human can finish it.

Negative cases (must NOT trigger)
---------------------------------

* ``.format("parquet"|"delta"|"csv"|"json"|…)`` — any non-snowflake format.
* A ``.load()`` / ``.save()`` whose chain has no snowflake ``.format(...)``.
* ``df.write.saveAsTable(...)`` with no snowflake ``.format`` (owned by
  ``saveastable_drop_format_path_kwargs_rewrite``).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "snowflake_connector_io_to_snowflake_session_rewrite"
MIN_SCOS_VERSION = "0.4.0"
NOTEBOOK_SCOPE = "cell"

_SNOWFLAKE_FORMATS = {"snowflake", "net.snowflake.spark.snowflake"}
_SF_IMPORT = "from snowflake.snowpark_connect.snowflake_session import SnowflakeSession"

_READ_COMMENT = (
    f'# SCOS: [SPRKCNTPY5400-Fixed] {RECIPE_ID}: spark.read.format("snowflake").load() '
    f"-> SnowflakeSession(<session>).sql() (native Snowflake SQL pass-through)"
)
_WRITE_COMMENT = (
    f'# SCOS: [SPRKCNTPY5400-Fixed] {RECIPE_ID}: write.format("snowflake").save() '
    f"-> DataFrameWriter.saveAsTable() (native managed-table write)"
)
_TODO_COMMENT = (
    f"# SCOS: TODO - [SPRKCNTPY5400-IO] {RECIPE_ID}: Snowflake connector I/O with "
    f"non-literal/options-dict configuration; convert to SnowflakeSession(<session>).sql(...) "
    f"for reads or DataFrameWriter.saveAsTable(...) for writes manually"
)


def _str_value(node: cst.BaseExpression) -> Optional[str]:
    if isinstance(node, cst.SimpleString):
        try:
            return node.evaluated_value
        except Exception:
            return node.value.strip("'\"")
    return None


def _is_terminal(node: cst.CSTNode, name: str) -> bool:
    """True iff ``node`` is a ``<recv>.<name>()`` call (any args)."""
    return (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value == name
    )


class _ChainInfo:
    __slots__ = ("formats", "options", "ambiguous", "mode", "read_base", "write_base")

    def __init__(self) -> None:
        self.formats: list[str] = []
        self.options: dict[str, cst.BaseExpression] = {}
        self.ambiguous: bool = False
        self.mode: Optional[cst.BaseExpression] = None
        self.read_base: Optional[cst.BaseExpression] = None
        self.write_base: Optional[cst.BaseExpression] = None


def _analyze_chain(recv: cst.BaseExpression) -> _ChainInfo:
    """Walk a reader/writer receiver chain and collect format/options/mode plus
    the base session (before ``.read``) or DataFrame (before ``.write``)."""
    info = _ChainInfo()
    node: Optional[cst.BaseExpression] = recv
    while node is not None:
        if isinstance(node, cst.Call) and isinstance(node.func, cst.Attribute):
            meth = node.func.attr.value
            pos = [a for a in node.args if a.keyword is None and not a.star]
            has_kw_or_star = any(a.keyword is not None or a.star for a in node.args)
            if meth == "format":
                v = _str_value(pos[0].value) if pos else None
                if v is not None:
                    info.formats.append(v)
            elif meth == "option":
                if len(pos) == 2 and _str_value(pos[0].value) is not None:
                    info.options[_str_value(pos[0].value)] = pos[1].value
                else:
                    info.ambiguous = True
            elif meth == "options":
                # .options(**cfg) / .options(dict) -> not statically extractable
                info.ambiguous = True
            elif meth == "mode":
                if pos:
                    info.mode = pos[0].value
            # descend the chain
            node = node.func.value
        elif isinstance(node, cst.Attribute):
            if node.attr.value == "read":
                info.read_base = node.value
                break
            if node.attr.value in ("write", "writeStream"):
                info.write_base = node.value
                break
            node = node.value
        else:
            break
    return info


def _sf_session_sql(session: cst.BaseExpression, arg: cst.BaseExpression) -> cst.Call:
    """Build ``SnowflakeSession(<session>).sql(<arg>)``."""
    return cst.Call(
        func=cst.Attribute(
            value=cst.Call(func=cst.Name("SnowflakeSession"), args=[cst.Arg(session)]),
            attr=cst.Name("sql"),
        ),
        args=[cst.Arg(arg)],
    )


def _select_star_arg(dbtable: cst.BaseExpression) -> cst.BaseExpression:
    """Build the ``sql()`` argument for a ``dbtable`` read."""
    lit = _str_value(dbtable)
    if lit is not None:
        return cst.SimpleString(f'"SELECT * FROM {lit}"')
    # non-literal table expression -> "SELECT * FROM " + <expr>
    return cst.BinaryOperation(
        left=cst.SimpleString('"SELECT * FROM "'),
        operator=cst.Add(),
        right=dbtable,
    )


def _write_saveastable(
    df: cst.BaseExpression, dbtable: cst.BaseExpression, mode: Optional[cst.BaseExpression]
) -> cst.Call:
    """Build ``<df>.write[.mode(<mode>)].saveAsTable(<dbtable>)``."""
    writer: cst.BaseExpression = cst.Attribute(value=df, attr=cst.Name("write"))
    if mode is not None:
        writer = cst.Call(
            func=cst.Attribute(value=writer, attr=cst.Name("mode")), args=[cst.Arg(mode)]
        )
    return cst.Call(
        func=cst.Attribute(value=writer, attr=cst.Name("saveAsTable")),
        args=[cst.Arg(dbtable)],
    )


def _formats_are_snowflake(info: _ChainInfo) -> bool:
    return any((f or "").lower() in _SNOWFLAKE_FORMATS for f in info.formats)


# Snowflake connector session-context options -> SnowflakeSession helper method.
# Emitting these before the query preserves the DB/SCHEMA/WAREHOUSE/ROLE context
# the connector set via options, so an unqualified query still resolves. Only
# statically-literal ``.option("sfDatabase", "…")`` forms are handled; values in
# a ``.options(**cfg)`` splat are not visible to a per-statement recipe.
_CONTEXT_OPTION_METHODS = {
    "sfdatabase": "use_database",
    "sfschema": "use_schema",
    "sfwarehouse": "use_warehouse",
    "sfrole": "use_role",
}
# Deterministic emission order: role -> warehouse -> database -> schema
# (database before schema so the schema resolves within it).
_CONTEXT_METHOD_ORDER = ["use_role", "use_warehouse", "use_database", "use_schema"]


def _extract_context(options: dict) -> dict:
    """Return ``{use_method: value_node}`` for the literal session-context
    options present in ``options`` (case-insensitive key match)."""
    ctx: dict[str, cst.BaseExpression] = {}
    for key, val in options.items():
        method = _CONTEXT_OPTION_METHODS.get((key or "").lower())
        if method is not None:
            ctx[method] = val
    return ctx


def _use_stmt(
    session: cst.BaseExpression, method: str, val: cst.BaseExpression
) -> cst.SimpleStatementLine:
    """``SnowflakeSession(<session>).<method>(<val>)`` as a statement."""
    call = cst.Call(
        func=cst.Attribute(
            value=cst.Call(func=cst.Name("SnowflakeSession"), args=[cst.Arg(session)]),
            attr=cst.Name(method),
        ),
        args=[cst.Arg(val)],
    )
    return cst.SimpleStatementLine(body=[cst.Expr(call)])


def _context_stmts(
    session: cst.BaseExpression, context: dict
) -> list[cst.SimpleStatementLine]:
    """Ordered ``use_*`` statements for the extracted context options."""
    return [
        _use_stmt(session, method, context[method])
        for method in _CONTEXT_METHOD_ORDER
        if method in context
    ]


class _CallRewriter(cst.CSTTransformer):
    """Rewrites terminal ``.load()`` / ``.save()`` snowflake-connector calls."""

    def __init__(self) -> None:
        super().__init__()
        self.read_rewrites = 0
        self.write_rewrites = 0
        self.todo = False
        self.read_session: cst.BaseExpression | None = None
        self.read_context: dict = {}

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        # Snowflake connector READ: <sess>.read.format("snowflake")...load()
        if _is_terminal(updated_node, "load"):
            info = _analyze_chain(updated_node.func.value)  # type: ignore[union-attr]
            if _formats_are_snowflake(info) and info.read_base is not None:
                if "query" in info.options or "dbtable" in info.options:
                    self.read_rewrites += 1
                    self.read_session = info.read_base
                    self.read_context = _extract_context(info.options)
                    if "query" in info.options:
                        return _sf_session_sql(info.read_base, info.options["query"])
                    return _sf_session_sql(
                        info.read_base, _select_star_arg(info.options["dbtable"])
                    )
                # snowflake read we can't statically convert
                self.todo = True
                return updated_node
        # Snowflake connector WRITE: <df>.write.format("snowflake")...save()
        if _is_terminal(updated_node, "save"):
            info = _analyze_chain(updated_node.func.value)  # type: ignore[union-attr]
            if _formats_are_snowflake(info) and info.write_base is not None:
                if "dbtable" in info.options:
                    self.write_rewrites += 1
                    return _write_saveastable(
                        info.write_base, info.options["dbtable"], info.mode
                    )
                self.todo = True
                return updated_node
        return updated_node


def _already_annotated(stmt: cst.SimpleStatementLine) -> bool:
    for line in stmt.leading_lines:
        if line.comment is not None and RECIPE_ID in line.comment.value:
            return True
    return False


def _with_leading_comments(
    stmt: cst.SimpleStatementLine, comments: list[str]
) -> cst.SimpleStatementLine:
    new_leading = list(stmt.leading_lines) + [
        cst.EmptyLine(comment=cst.Comment(c)) for c in comments
    ]
    return stmt.with_changes(leading_lines=tuple(new_leading))


def _has_sf_import(module: cst.Module) -> bool:
    for stmt in module.body:
        body = getattr(stmt, "body", [])
        for small in body if isinstance(body, (list, tuple)) else []:
            if isinstance(small, cst.ImportFrom):
                mod = small.module
                dotted = _dotted(mod) if mod is not None else ""
                if dotted.endswith("snowflake_session") and not isinstance(
                    small.names, cst.ImportStar
                ):
                    for n in small.names:
                        if isinstance(n.name, cst.Name) and n.name.value == "SnowflakeSession":
                            return True
    return False


def _dotted(node: cst.BaseExpression) -> str:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted(node.value)}.{node.attr.value}"
    return ""


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kw)
        self._need_import = False

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        if _already_annotated(updated_node):
            return updated_node
        sub = _CallRewriter()
        new_stmt = updated_node.visit(sub)
        if sub.read_rewrites == 0 and sub.write_rewrites == 0 and not sub.todo:
            return updated_node
        assert isinstance(new_stmt, cst.SimpleStatementLine)
        comments: list[str] = []
        if sub.read_rewrites:
            comments.append(_READ_COMMENT)
            self._need_import = True
        if sub.write_rewrites:
            comments.append(_WRITE_COMMENT)
        if sub.todo and not (sub.read_rewrites or sub.write_rewrites):
            comments.append(_TODO_COMMENT)
        new_stmt = _with_leading_comments(new_stmt, comments)
        self._record(
            self._line_of(original_node),
            f"snowflake connector -> SnowflakeSession/saveAsTable "
            f"(reads={sub.read_rewrites}, writes={sub.write_rewrites}, todo={int(sub.todo)})",
        )
        # Preserve the connector's session context (sfDatabase/sfSchema/…):
        # emit SnowflakeSession(<session>).use_*() calls before the query so an
        # unqualified table still resolves.
        if sub.read_rewrites and sub.read_session is not None and sub.read_context:
            use_stmts = _context_stmts(sub.read_session, sub.read_context)
            if use_stmts:
                return cst.FlattenSentinel([*use_stmts, new_stmt])
        return new_stmt

    def leave_Module(  # type: ignore[override]
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        if not self._need_import or _has_sf_import(updated_node):
            return updated_node
        import_stmt = cst.SimpleStatementLine(
            body=[
                cst.ImportFrom(
                    module=cst.parse_expression(
                        "snowflake.snowpark_connect.snowflake_session"
                    ),
                    names=[cst.ImportAlias(name=cst.Name("SnowflakeSession"))],
                )
            ]
        )
        return updated_node.with_changes(body=(import_stmt, *updated_node.body))


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    # Fast no-op gate: nothing to do without a snowflake connector format string.
    if "snowflake" not in source:
        return _common.RecipeResult(source=source, edits=[])
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
