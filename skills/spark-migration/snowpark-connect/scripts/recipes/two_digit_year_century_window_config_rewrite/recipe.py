"""Restore Spark's two-digit-year century window when a datetime format
string parses a standalone ``yy``.

What it does
------------

SCOS maps the Spark/Java pattern token ``yy`` to Snowflake's ``YY``
(``date_time_format_mapping.py``), and Snowflake resolves ``YY`` against the
``TWO_DIGIT_CENTURY_START`` session parameter, which defaults to **1970**:
``00-69 -> 2000-2069`` but ``70-99 -> 1970-1999``. Spark resolves ``yy``
against a fixed base year of 2000: ``00-99 -> 2000-2099``. So every
two-digit year in ``70..99`` parses one century early on SCOS, with no
error and no EWI at runtime -- a silent value divergence.

This recipe injects the session config that closes the gap::

    <session>.conf.set("snowpark.connect.use2000AsTwoDigitCenturyStart", "true")

SCOS translates that key to ``ALTER SESSION SET TWO_DIGIT_CENTURY_START =
2000`` (``config.py:set_snowflake_parameters``), which restores Spark's
window for **every** two-digit-year parse in the session -- the whole
``to_timestamp`` / ``to_timestamp_ltz`` / ``to_timestamp_ntz`` / ``to_date``
/ ``unix_timestamp`` family and the same functions reached through
``spark.sql(...)``, because they all share the one format mapping.

Trigger
-------

A call to one of ``_PARSE_FUNCS`` (any receiver: ``F.to_timestamp``,
``functions.to_timestamp``, bare ``to_timestamp`` from ``import *``) whose
**format** argument -- positional index >= 1, or the ``format`` / ``fmt``
keyword, optionally wrapped in ``lit(...)`` -- is a string literal
containing a standalone ``yy``; or a ``.sql("...")`` call whose text names
one of ``_SQL_PARSE_FUNCS`` and contains a quoted format literal with a
standalone ``yy``.

The injection is a single module-level line; it is **not** repeated per
call site (the session parameter is session-scoped, so once is enough).

Negative cases (must NOT trigger)
---------------------------------

* ``yyyy`` / ``yyy`` / ``y`` -- four-, three-, and variable-width years all
  map to Snowflake ``YYYY`` and never consult ``TWO_DIGIT_CENTURY_START``.
* ``yy`` inside a Java-quoted literal segment (``"'yy'-MM-dd"`` means the
  literal text ``yy``), which is stripped before the scan.
* Format-only APIs (``date_format``, ``from_unixtime``): rendering a
  two-digit year does not consult the century start.
* A ``yy`` in the *value* argument rather than the format argument
  (``to_date(lit("yy"), "yyyy-MM-dd")`` — argument 0 is data, not a pattern).
* A bare ``yy`` in SQL text that is not inside a quoted literal (a column
  or table named ``yy``), or quoted ``yy`` with no parse function in the
  statement.
* The config is already set anywhere in the module (idempotency).

Deliberate limits (left to the analyzer / fixer, see fix-rules Rule 31)
----------------------------------------------------------------------

The format must be a **literal** at the call site. A format held in a
variable, built at runtime, or read from config is invisible here, as are
reader options (``.option("timestampFormat", ...)``). Those sites keep the
KB parity note and are the LLM fixer's job.

Scope note
----------

The key is Spark-compatibility-scoped: it only moves the century window for
two-digit-year parsing, and only for this session. It is still a behavior
change for a workload that *wants* Snowflake's window -- the injected
comment says so, and removing the line restores the SCOS default.
"""
from __future__ import annotations

import ast as _ast
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "two_digit_year_century_window_config_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_CONF_KEY = "snowpark.connect.use2000AsTwoDigitCenturyStart"

# Datetime *parsing* APIs that take a Spark/Java format pattern. Formatting
# APIs (date_format, from_unixtime) are excluded: rendering a two-digit year
# never consults the century start.
_PARSE_FUNCS = frozenset({
    "to_timestamp",
    "to_timestamp_ltz",
    "to_timestamp_ntz",
    "try_to_timestamp",
    "to_date",
    "unix_timestamp",
    "to_unix_timestamp",
})

# Same family on the SQL surface (spark.sql("...")).
_SQL_PARSE_FUNCS = (
    "to_timestamp",
    "to_timestamp_ltz",
    "to_timestamp_ntz",
    "try_to_timestamp",
    "to_date",
    "unix_timestamp",
    "to_unix_timestamp",
)

_FORMAT_KEYWORDS = frozenset({"format", "fmt"})

# ``yy`` not preceded or followed by another ``y``.
_STANDALONE_YY = re.compile(r"(?<!y)yy(?!y)")
# Java pattern quoted-literal segment: text inside single quotes is literal.
_JAVA_QUOTED = re.compile(r"'[^']*'")
# A quoted literal inside a SQL string.
_SQL_QUOTED = re.compile(r"'([^']*)'")

_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY5400-Fixed] {RECIPE_ID}: a datetime format parses a "
    f"two-digit year ('yy'). Snowflake resolves 'YY' against "
    f"TWO_DIGIT_CENTURY_START (default 1970, so 70-99 -> 1970-1999) while "
    f"Spark uses 2000-2099; this session config restores Spark's window for "
    f"every two-digit-year parse in the session. Remove it only if the "
    f"workload wants Snowflake's window."
)
_TODO_TEXT = (
    f"# SCOS-TODO: [SPRKCNTPY5400-Warning] {RECIPE_ID}: a datetime format "
    f"parses a two-digit year ('yy'), which resolves to 1970-1999 for 70-99 "
    f"on SCOS but 2000-2099 on Spark. No session anchor was found in this "
    f'module -- add <session>.conf.set("{_CONF_KEY}", "true") after the '
    f"session is created."
)


def _terminal_name(expr: cst.BaseExpression) -> Optional[str]:
    """Innermost identifier of a callee: ``to_date`` for ``to_date``,
    ``F.to_date``, ``pyspark.sql.functions.to_date``."""
    if isinstance(expr, cst.Name):
        return expr.value
    if isinstance(expr, cst.Attribute) and isinstance(expr.attr, cst.Name):
        return expr.attr.value
    return None


def _string_value(expr: cst.BaseExpression) -> Optional[str]:
    """The Python value of a plain string literal, unwrapping ``lit(...)``.

    Returns None for f-strings, concatenations, names, or anything else that
    is not a single decidable literal.
    """
    if isinstance(expr, cst.Call):
        if _terminal_name(expr.func) != "lit":
            return None
        for arg in expr.args:
            if arg.keyword is None:
                return _string_value(arg.value)
        return None
    if isinstance(expr, cst.SimpleString):
        try:
            value = _ast.literal_eval(expr.value)
        except Exception:  # noqa: BLE001 - malformed/exotic literal
            return None
        return value if isinstance(value, str) else None
    return None


def has_standalone_yy(fmt: str) -> bool:
    """True iff ``fmt`` parses a two-digit year outside a quoted literal."""
    return bool(_STANDALONE_YY.search(_JAVA_QUOTED.sub("", fmt)))


def _sql_text_parses_two_digit_year(text: str) -> bool:
    """True iff SQL ``text`` calls a parse function AND some quoted literal in
    it is a format with a standalone ``yy``."""
    low = text.lower()
    if not any(f"{fn}(" in low.replace(" ", "") for fn in _SQL_PARSE_FUNCS):
        return False
    return any(has_standalone_yy(seg) for seg in _SQL_QUOTED.findall(text))


class _Detector(cst.CSTVisitor):
    """Set ``self.hit`` when a two-digit-year parse is decidable statically."""

    def __init__(self) -> None:
        super().__init__()
        self.hit = False

    def visit_Call(self, node: cst.Call) -> None:
        if self.hit:
            return
        name = _terminal_name(node.func)
        if name is None:
            return
        if name in _PARSE_FUNCS:
            for i, arg in enumerate(node.args):
                is_format_position = (
                    arg.keyword is None and i >= 1
                ) or (
                    arg.keyword is not None
                    and arg.keyword.value in _FORMAT_KEYWORDS
                )
                if not is_format_position:
                    continue
                fmt = _string_value(arg.value)
                if fmt is not None and has_standalone_yy(fmt):
                    self.hit = True
                    return
            return
        if name == "sql":
            for arg in node.args:
                # Triple-quoted SQL is a SimpleString too, so literal_eval
                # handles it. An f-string or a variable is opaque -> skip.
                text = _string_value(arg.value)
                if text is None:
                    continue
                if _sql_text_parses_two_digit_year(text):
                    self.hit = True
                    return


def _module_already_sets_conf(source: str) -> bool:
    return _CONF_KEY in source


def _build_conf_stmt(receiver: str) -> cst.SimpleStatementLine:
    call = cst.Call(
        func=cst.Attribute(
            value=cst.Attribute(
                value=cst.Name(receiver), attr=cst.Name("conf")
            ),
            attr=cst.Name("set"),
        ),
        args=[
            cst.Arg(value=cst.SimpleString(f'"{_CONF_KEY}"')),
            cst.Arg(value=cst.SimpleString('"true"')),
        ],
    )
    return cst.SimpleStatementLine(
        body=[cst.Expr(value=call)],
        leading_lines=[cst.EmptyLine(comment=cst.Comment(_COMMENT_TEXT))],
    )


def _build_todo_stmt() -> cst.SimpleStatementLine:
    return cst.SimpleStatementLine(
        body=[cst.Pass()],
        leading_lines=[cst.EmptyLine(comment=cst.Comment(_TODO_TEXT))],
    )


def _assignment_target_name(stmt: cst.SimpleStatementLine) -> Optional[str]:
    if len(stmt.body) != 1:
        return None
    small = stmt.body[0]
    if not isinstance(small, cst.Assign) or len(small.targets) != 1:
        return None
    target = small.targets[0].target
    return target.value if isinstance(target, cst.Name) else None


def _is_init_spark_session_assignment(
    stmt: cst.SimpleStatementLine,
) -> Optional[str]:
    """``<name> = snowpark_connect.init_spark_session()`` -> ``<name>``."""
    name = _assignment_target_name(stmt)
    if name is None:
        return None
    small = stmt.body[0]
    assert isinstance(small, cst.Assign)
    value = small.value
    if not isinstance(value, cst.Call):
        return None
    if _terminal_name(value.func) != "init_spark_session":
        return None
    return name


def _is_import(stmt: cst.SimpleStatementLine) -> bool:
    return any(isinstance(s, (cst.Import, cst.ImportFrom)) for s in stmt.body)


def _inject_after_init_in_body(body: list) -> Optional[list]:
    for i, stmt in enumerate(body):
        if isinstance(stmt, cst.SimpleStatementLine):
            name = _is_init_spark_session_assignment(stmt)
            if name is not None:
                return (
                    list(body[: i + 1])
                    + [_build_conf_stmt(name)]
                    + list(body[i + 1:])
                )
    return None


def _inject_inside_first_builder_function(
    module: cst.Module,
) -> Optional[cst.Module]:
    new_top: list = []
    injected = False
    for top in module.body:
        if (
            not injected
            and isinstance(top, cst.FunctionDef)
            and isinstance(top.body, cst.IndentedBlock)
        ):
            new_inner = _inject_after_init_in_body(list(top.body.body))
            if new_inner is not None:
                top = top.with_changes(
                    body=top.body.with_changes(body=tuple(new_inner))
                )
                injected = True
        new_top.append(top)
    return module.with_changes(body=tuple(new_top)) if injected else None


def _inject_at_module_session_assignment(
    module: cst.Module,
) -> Optional[cst.Module]:
    """Insert after the last module-level ``<name> = ...`` that binds a
    session: an ``init_spark_session()`` call, else a plain ``spark = ...``."""
    body = list(module.body)
    anchor = -1
    receiver = "spark"
    for i, stmt in enumerate(body):
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        name = _is_init_spark_session_assignment(stmt)
        if name is not None:
            anchor, receiver = i, name
            continue
        if anchor == -1 and _assignment_target_name(stmt) == "spark":
            anchor, receiver = i, "spark"
    if anchor == -1:
        return None
    return module.with_changes(
        body=tuple(
            body[: anchor + 1]
            + [_build_conf_stmt(receiver)]
            + body[anchor + 1:]
        )
    )


def _inject_top_level_todo(module: cst.Module) -> cst.Module:
    body = list(module.body)
    at = 0
    if (
        body
        and isinstance(body[0], cst.SimpleStatementLine)
        and len(body[0].body) == 1
        and isinstance(body[0].body[0], cst.Expr)
        and isinstance(body[0].body[0].value, cst.SimpleString)
    ):
        at = 1
    while (
        at < len(body)
        and isinstance(body[at], cst.SimpleStatementLine)
        and _is_import(body[at])
    ):
        at += 1
    return module.with_changes(
        body=tuple(body[:at] + [_build_todo_stmt()] + body[at:])
    )


def _first_hit_line(module: cst.Module) -> int:
    """Line of the first triggering call, for the recipe_edits anchor."""
    wrapper = cst.MetadataWrapper(module, unsafe_skip_copy=True)
    holder = {"line": None}

    class _Finder(cst.CSTVisitor):
        METADATA_DEPENDENCIES = (cst.metadata.PositionProvider,)

        def visit_Call(self, node: cst.Call) -> None:
            if holder["line"] is not None:
                return
            probe = _Detector()
            probe.visit_Call(node)
            if probe.hit:
                holder["line"] = self.get_metadata(
                    cst.metadata.PositionProvider, node
                ).start.line

    wrapper.visit(_Finder())
    return holder["line"] or 1


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    module = cst.parse_module(source)
    detector = _Detector()
    module.visit(detector)
    if not detector.hit:
        return _common.RecipeResult(source=source, edits=[])
    if _module_already_sets_conf(source):
        return _common.RecipeResult(source=source, edits=[])
    if RECIPE_ID in source:  # previous TODO-fallback run
        return _common.RecipeResult(source=source, edits=[])

    new_module = _inject_inside_first_builder_function(module)
    if new_module is None:
        new_module = _inject_at_module_session_assignment(module)
    if new_module is None:
        new_module = _inject_top_level_todo(module)

    src_line = _first_hit_line(module)
    edit = _record_edit_passthrough(
        file=file,
        src_line=src_line,
        recipe_id=RECIPE_ID,
        output_line_anchor=_common.output_anchor(
            RECIPE_ID, src_line, "two-digit-year century window config"
        ),
        facts_db=facts_db,
    )
    return _common.RecipeResult(source=new_module.code, edits=[edit])


def _record_edit_passthrough(
    *, file: str, src_line: int, recipe_id: str,
    output_line_anchor: str, facts_db: Optional[str],
):
    """Module-level injection has no BaseRecipe instance to carry the edit, so
    call the shared recorder directly (same shape as the udtf recipe)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import _recipe_base  # noqa: E402

    return _recipe_base.record_edit(
        file=file,
        src_line=src_line,
        recipe_id=recipe_id,
        output_line_anchor=output_line_anchor,
        facts_db=facts_db,
    )
