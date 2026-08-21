"""Repoint AWS Glue Data Catalog I/O at native Snowflake tables: rewrite
``create_dynamic_frame.from_catalog(...)`` to ``<session>.read.table("db.tbl")``
followed by the **mandatory lowercase column normalization**, and
``write_dynamic_frame.from_catalog(...)`` to ``<frame>.write.mode("append").saveAsTable("db.tbl")``.

Implements recipes **G2** (catalog read) and the catalog-sink half of **G11**
(catalog write) from ``references/python/glue-recipes.md``.

What it does
------------

**Read path.** ``<recv>.create_dynamic_frame.from_catalog(database=D,
table_name=T, ...)`` assigned to a simple name becomes two statements::

    DyF = glueContext.create_dynamic_frame.from_catalog(
        database=db, table_name=tbl, transformation_ctx="ctx")
    ->
    DyF = glueContext.read.table(f"{db}.{tbl}")
    DyF = DyF.toDF(*[c.lower() for c in DyF.columns])

``<base>`` is whatever receiver sat to the left of ``.create_dynamic_frame``
(normally ``glueContext``); ``glue_session_bootstrap_rewrite`` turns that name
into the SCOS session, so this recipe never hardcodes ``spark``.

⚠️ **The lowercase normalization line is the whole point of this recipe and is
never omitted on a successful rewrite.** The Glue Data Catalog exposes
LOWERCASE column names; a native Snowflake read returns UPPERCASE. Any
case-sensitive downstream logic (``field.name in target_columns`` membership
tests, hand-built mapping dicts) then matches nothing and **silently drops the
affected columns** — in the validated workload this lost the primary key
(``document_id`` vs ``DOCUMENT_ID``) with no error raised. Because the
normalization statement can only be emitted when the assignment target is a
simple ``Name``, any other target shape (tuple, attribute, subscript) is
deliberately left as a TODO rather than rewritten without it.

When ``database=``/``table_name=`` are string literals a plain ``"db.tbl"``
literal is emitted; when they are expressions (``Name``, ``Attribute``,
``args["INPUT_DATABASE"]``, ...) an f-string ``f"{db}.{tbl}"`` is emitted, with
a ``(<db>) + "." + (<tbl>)`` concatenation fallback for expressions that cannot
be embedded in an f-string literal.

**Write path.** ``<recv>.write_dynamic_frame.from_catalog(frame=F, database=D,
table_name=T, ...)`` becomes ``F.write.mode("append").saveAsTable("D.T")``.
``append`` is Glue's catalog-sink default; the emitted comment says so
explicitly so a reviewer can confirm the mode is what the job intended.

**Dropped kwargs.** ``transformation_ctx=`` is the Glue job-bookmark handle and
has no SCOS equivalent: it is dropped and a ``SPRKCNTPY3606`` TODO is emitted
noting that incrementality is lost (see G8). The wording differs by path: on a
**read** the dropped handle was the bookmark cursor, so the read becomes a full
reprocess on every run; on a **write** it was the bookmark *commit* handle, so
there is no per-run write checkpoint and a re-run may re-emit rows already
written — the target's idempotency then rests entirely on the MERGE/dedup key.
``additional_options=`` /
``format_options=`` / ``connection_options=`` are Glue-reader/writer specific:
they are dropped with a ``SPRKCNTPY3602-Warning`` naming the kwarg so a human
can check whether it was doing real work.

Trigger
-------

A ``SimpleStatementLine`` containing a call to one of

* ``<recv>.create_dynamic_frame.from_catalog|from_options(...)``
* ``<recv>.create_dynamic_frame_from_catalog|_from_options(...)``
* ``<recv>.write_dynamic_frame.from_catalog|from_options|from_jdbc_conf(...)``
* ``<recv>.write_dynamic_frame_from_catalog|_from_options|_from_jdbc_conf(...)``

Negative cases (must NOT trigger)
---------------------------------

* Plain PySpark I/O — ``spark.read.table(...)``, ``df.write.saveAsTable(...)``.
* Any statement with no ``create_dynamic_frame`` / ``write_dynamic_frame``
  token (cheap substring gate).
* ``DynamicFrame.fromDF(...)`` / ``.toDF()`` lifecycle wrappers — owned by
  ``glue_transforms_to_dataframe_rewrite`` (G6).

Left as TODO (annotated, code unchanged) rather than guessed
------------------------------------------------------------

* Read whose assignment target is not a simple ``Name`` (tuple / attribute /
  subscript) — the normalization statement would be unrepresentable.
* Read that is not a plain ``<name> = <call>`` assignment (bare expression
  statement, ``.toDF()`` chained onto the catalog call, augmented assignment).
* Read/write missing ``database=``/``table_name=`` — e.g. a ``from_options``
  call whose table identity lives inside ``connection_options=``.
* Write whose ``frame=`` is absent, or that is not a bare expression statement
  (Glue returns the frame; ``x = ...saveAsTable(...)`` would bind ``None``).
* Table identity given positionally instead of by keyword.

Idempotency
-----------

Re-running on this recipe's own output is a byte-for-byte no-op: successful
rewrites erase the ``*_dynamic_frame`` token so the substring gate short-circuits,
and TODO-annotated statements are guarded by
``_annotate.comment_above_contains(..., RECIPE_ID)``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "glue_catalog_io_to_table_rewrite"
MIN_SCOS_VERSION = "0.4.0"
NOTEBOOK_SCOPE = "cell"

# ---------------------------------------------------------------------------
# Glue catalog I/O surface
# ---------------------------------------------------------------------------
_READ_CHAIN_ATTR = "create_dynamic_frame"
_READ_TERMINALS = frozenset({"from_catalog", "from_options"})
_READ_FLAT = frozenset(
    {"create_dynamic_frame_from_catalog", "create_dynamic_frame_from_options"}
)

_WRITE_CHAIN_ATTR = "write_dynamic_frame"
_WRITE_TERMINALS = frozenset({"from_catalog", "from_options", "from_jdbc_conf"})
_WRITE_FLAT = frozenset(
    {
        "write_dynamic_frame_from_catalog",
        "write_dynamic_frame_from_options",
        "write_dynamic_frame_from_jdbc_conf",
    }
)

# Glue-reader/writer-specific kwargs that have no SCOS equivalent. Dropped, but
# each one gets a warning naming it so a human can confirm it was inert.
_DROP_WITH_WARNING = ("additional_options", "format_options", "connection_options")
_BOOKMARK_KWARG = "transformation_ctx"

# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------
_READ_COMMENT = (
    f"# SCOS: [SPRKCNTPY3602-IO] {RECIPE_ID}: "
    f"create_dynamic_frame.from_catalog(database=, table_name=) -> "
    f"<session>.read.table(<db>.<table>), plus the MANDATORY lowercase column "
    f"normalization (Glue catalog exposes lowercase names, Snowflake returns "
    f"uppercase; without it case-sensitive column matching silently drops columns)"
)
_NORMALIZE_COMMENT = (
    "# CRITICAL: the Glue Data Catalog exposes LOWERCASE column names; a native "
    "Snowflake read returns UPPERCASE. Normalize so downstream case-sensitive "
    "logic behaves identically."
)
_WRITE_COMMENT = (
    f"# SCOS: [SPRKCNTPY3609-IO] {RECIPE_ID}: "
    f"write_dynamic_frame.from_catalog(frame=, database=, table_name=) -> "
    f'<frame>.write.mode("append").saveAsTable(<db>.<table>). '
    f"Glue's catalog sink appends by default - confirm append is the mode this "
    f"job intended (overwrite/error otherwise)"
)
_BOOKMARK_TODO_READ = (
    f"# SCOS: TODO - [SPRKCNTPY3606-Error] {RECIPE_ID}: dropped "
    f"transformation_ctx= (the Glue job-bookmark handle). It has no SCOS "
    f"equivalent, so this read is now a FULL reprocess on every run"
)
_BOOKMARK_TODO_WRITE = (
    f"# SCOS: TODO - [SPRKCNTPY3606-Error] {RECIPE_ID}: dropped "
    f"transformation_ctx= (the Glue job-bookmark COMMIT handle for this sink). "
    f"It has no SCOS equivalent, so there is no per-run write checkpoint: "
    f"re-running the job may RE-EMIT rows it already wrote, and the target's "
    f"idempotency now depends entirely on the MERGE/dedup key rather than on "
    f"Glue's bookmark"
)
_BOOKMARK_TODO_CONT = (
    "#   Re-establish incrementality per recipe G8 in "
    "references/python/glue-recipes.md (external stage + directory table + "
    "Stream consumed in a DML transaction), or explicitly accept and document "
    "the switch to full reprocessing."
)
_TODO_LEAD = f"# SCOS: TODO - [SPRKCNTPY3602-IO] {RECIPE_ID}: "
_WRITE_TODO_LEAD = f"# SCOS: TODO - [SPRKCNTPY3609-IO] {RECIPE_ID}: "
_READ_TODO_CONT = (
    '#   Target shape: <df> = <session>.read.table("<db>.<table>") followed by '
    "<df> = <df>.toDF(*[c.lower() for c in <df>.columns]) - the lowercase "
    "normalization is REQUIRED (Glue catalog columns are lowercase, Snowflake "
    "returns uppercase; omitting it silently drops case-sensitive matches)."
)
_WRITE_TODO_CONT = (
    '#   Target shape: <frame>.write.mode("append").saveAsTable("<db>.<table>").'
)


def _drop_warning(kwarg: str) -> str:
    return (
        f"# SCOS-WARN: [SPRKCNTPY3602-Warning] {RECIPE_ID}: dropped Glue-specific "
        f"kwarg '{kwarg}=' - it has no SCOS reader/writer equivalent. If it was "
        f"doing real work (e.g. mergeSchema, a pushdown predicate, a partition "
        f"spec), reproduce it explicitly."
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _str_value(node: cst.BaseExpression) -> Optional[str]:
    """Python value of a string literal, else None."""
    if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
        try:
            val = node.evaluated_value
        except Exception:  # noqa: BLE001
            return None
        return val if isinstance(val, str) else None
    return None


def _render(node: cst.CSTNode) -> Optional[str]:
    """Render ``node`` back to a single line of source, else None."""
    try:
        code = cst.Module([]).code_for_node(node)
    except Exception:  # noqa: BLE001
        return None
    if "\n" in code:
        return None
    return code.strip()


def _classify(call: cst.Call):
    """``(kind, base)`` for a Glue catalog I/O call, else ``(None, None)``.

    ``kind`` is ``"read"`` / ``"write"``; ``base`` is the receiver that sat to
    the left of ``.create_dynamic_frame`` / ``.write_dynamic_frame``.
    """
    func = call.func
    if not isinstance(func, cst.Attribute) or not isinstance(func.attr, cst.Name):
        return None, None
    name = func.attr.value
    recv = func.value
    if name in _READ_FLAT:
        return "read", recv
    if name in _WRITE_FLAT:
        return "write", recv
    if isinstance(recv, cst.Attribute) and isinstance(recv.attr, cst.Name):
        if recv.attr.value == _READ_CHAIN_ATTR and name in _READ_TERMINALS:
            return "read", recv.value
        if recv.attr.value == _WRITE_CHAIN_ATTR and name in _WRITE_TERMINALS:
            return "write", recv.value
    return None, None


class _Finder(cst.CSTVisitor):
    """Locate the first Glue catalog I/O call in a statement subtree."""

    def __init__(self) -> None:
        super().__init__()
        self.call: Optional[cst.Call] = None
        self.kind: Optional[str] = None
        self.base: Optional[cst.BaseExpression] = None

    def visit_Call(self, node: cst.Call) -> None:
        if self.call is not None:
            return
        kind, base = _classify(node)
        if kind is not None:
            self.call, self.kind, self.base = node, kind, base


def _kwargs(call: cst.Call) -> dict:
    out: dict = {}
    for arg in call.args:
        if arg.keyword is not None and isinstance(arg.keyword, cst.Name):
            out[arg.keyword.value] = arg.value
    return out


def _table_expr(
    db: cst.BaseExpression, tbl: cst.BaseExpression
) -> Optional[cst.BaseExpression]:
    """Build the ``"<db>.<table>"`` identifier expression.

    Literal ``database``/``table_name`` -> a plain string literal. Expressions ->
    an f-string, falling back to string concatenation when the rendered
    sub-expressions cannot legally be embedded in an f-string literal.
    """
    d_lit, t_lit = _str_value(db), _str_value(tbl)
    if d_lit is not None and t_lit is not None:
        joined = f"{d_lit}.{t_lit}"
        if '"' not in joined and "\\" not in joined:
            return cst.SimpleString(f'"{joined}"')
        return cst.parse_expression(repr(joined))

    d_code, t_code = _render(db), _render(tbl)
    if d_code is None or t_code is None:
        return None
    both = d_code + t_code
    # Braces/backslashes cannot appear inside an f-string replacement field.
    if not any(ch in both for ch in ("{", "}", "\\")):
        for quote in ('"', "'"):
            if quote not in both:
                return cst.parse_expression(
                    f"f{quote}{{{d_code}}}.{{{t_code}}}{quote}"
                )
    return cst.parse_expression(f'({d_code}) + "." + ({t_code})')


def _read_call(base: cst.BaseExpression, table: cst.BaseExpression) -> cst.Call:
    """``<base>.read.table(<table>)``."""
    return cst.Call(
        func=cst.Attribute(
            value=cst.Attribute(value=base, attr=cst.Name("read")),
            attr=cst.Name("table"),
        ),
        args=[cst.Arg(table)],
    )


def _write_call(frame: cst.BaseExpression, table: cst.BaseExpression) -> cst.Call:
    """``<frame>.write.mode("append").saveAsTable(<table>)``."""
    writer = cst.Call(
        func=cst.Attribute(
            value=cst.Attribute(value=frame, attr=cst.Name("write")),
            attr=cst.Name("mode"),
        ),
        args=[cst.Arg(cst.SimpleString('"append"'))],
    )
    return cst.Call(
        func=cst.Attribute(value=writer, attr=cst.Name("saveAsTable")),
        args=[cst.Arg(table)],
    )


def _normalize_stmt(name: str) -> cst.SimpleStatementLine:
    """``<name> = <name>.toDF(*[c.lower() for c in <name>.columns])`` with its
    explanatory comment. This is the silent-data-loss guard from G2."""
    stmt = cst.parse_statement(
        f"{name} = {name}.toDF(*[c.lower() for c in {name}.columns])"
    )
    assert isinstance(stmt, cst.SimpleStatementLine)
    return stmt.with_changes(
        leading_lines=(cst.EmptyLine(comment=cst.Comment(_NORMALIZE_COMMENT)),)
    )


def _with_comments(
    stmt: cst.SimpleStatementLine, comments: list[str]
) -> cst.SimpleStatementLine:
    return stmt.with_changes(
        leading_lines=tuple(
            list(stmt.leading_lines)
            + [cst.EmptyLine(comment=cst.Comment(c)) for c in comments]
        )
    )


def _dropped_kwarg_comments(kw: dict, kind: str) -> list[str]:
    """Bookmark TODO + one warning per dropped Glue-specific kwarg.

    ``kind`` is ``"read"`` / ``"write"``: a source's ``transformation_ctx`` is a
    read cursor (dropping it means full reprocessing), a sink's is the
    bookmark-commit handle (dropping it means no write checkpoint), so the two
    paths get different wording.
    """
    comments: list[str] = []
    if _BOOKMARK_KWARG in kw:
        lead = _BOOKMARK_TODO_READ if kind == "read" else _BOOKMARK_TODO_WRITE
        comments.extend([lead, _BOOKMARK_TODO_CONT])
    for name in _DROP_WITH_WARNING:
        if name in kw:
            comments.append(_drop_warning(name))
    return comments


def _single_small(stmt: cst.SimpleStatementLine):
    return stmt.body[0] if len(stmt.body) == 1 else None


# ---------------------------------------------------------------------------
# Recipe
# ---------------------------------------------------------------------------
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
        finder = _Finder()
        updated_node.visit(finder)
        if finder.call is None or finder.base is None:
            return updated_node
        if finder.kind == "read":
            return self._do_read(start, updated_node, finder)
        return self._do_write(start, updated_node, finder)

    # -- read ---------------------------------------------------------------
    def _do_read(self, start: int, stmt: cst.SimpleStatementLine, finder: _Finder):
        small = _single_small(stmt)
        target_name: Optional[str] = None
        if isinstance(small, cst.Assign) and len(small.targets) == 1:
            tgt = small.targets[0].target
            if isinstance(tgt, cst.Name) and small.value is finder.call:
                target_name = tgt.value

        kw = _kwargs(finder.call)
        table = None
        if "database" in kw and "table_name" in kw:
            table = _table_expr(kw["database"], kw["table_name"])

        if target_name is None:
            return self._todo(
                start,
                stmt,
                _TODO_LEAD
                + "Glue catalog read is not a plain `<name> = <call>` assignment "
                "(tuple/attribute/subscript target, chained call, or bare "
                "expression), so the required lowercase-normalization statement "
                "cannot be emitted safely. Convert by hand.",
                _READ_TODO_CONT,
                "read shape not rewritable (non-Name assignment target)",
            )
        if table is None:
            return self._todo(
                start,
                stmt,
                _TODO_LEAD
                + "Glue catalog read has no statically resolvable "
                "database=/table_name= pair (table identity likely inside "
                "connection_options=, or supplied positionally). Convert by hand.",
                _READ_TODO_CONT,
                "read table identity not resolvable",
            )

        assert isinstance(small, cst.Assign)
        new_stmt = stmt.with_changes(
            body=[small.with_changes(value=_read_call(finder.base, table))]
        )
        comments = [_READ_COMMENT] + _dropped_kwarg_comments(kw, "read")
        new_stmt = _with_comments(new_stmt, comments)
        self._record(start, "glue catalog read -> read.table + lowercase normalize")
        return cst.FlattenSentinel([new_stmt, _normalize_stmt(target_name)])

    # -- write --------------------------------------------------------------
    def _do_write(self, start: int, stmt: cst.SimpleStatementLine, finder: _Finder):
        small = _single_small(stmt)
        is_bare = isinstance(small, cst.Expr) and small.value is finder.call

        kw = _kwargs(finder.call)
        frame = kw.get("frame")
        table = None
        if "database" in kw and "table_name" in kw:
            table = _table_expr(kw["database"], kw["table_name"])

        if not is_bare:
            return self._todo(
                start,
                stmt,
                _WRITE_TODO_LEAD
                + "Glue catalog write is not a bare expression statement; "
                "saveAsTable() returns None, so binding its result would change "
                "behaviour. Convert by hand.",
                _WRITE_TODO_CONT,
                "write shape not rewritable (result is bound)",
            )
        if frame is None or table is None:
            missing = "frame=" if frame is None else "database=/table_name="
            return self._todo(
                start,
                stmt,
                _WRITE_TODO_LEAD
                + f"Glue catalog write has no statically resolvable {missing} "
                "(table identity likely inside connection_options=, or supplied "
                "positionally). Convert by hand.",
                _WRITE_TODO_CONT,
                "write frame/table identity not resolvable",
            )

        assert isinstance(small, cst.Expr)
        new_stmt = stmt.with_changes(
            body=[small.with_changes(value=_write_call(frame, table))]
        )
        comments = [_WRITE_COMMENT] + _dropped_kwarg_comments(kw, "write")
        new_stmt = _with_comments(new_stmt, comments)
        self._record(start, "glue catalog write -> write.mode(append).saveAsTable")
        return new_stmt

    # -- shared -------------------------------------------------------------
    def _todo(
        self,
        start: int,
        stmt: cst.SimpleStatementLine,
        lead: str,
        cont: str,
        snippet: str,
    ) -> cst.SimpleStatementLine:
        self._record(start, snippet)
        return _with_comments(stmt, [lead, cont])


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    if "dynamic_frame" not in source:
        return _common.RecipeResult(source=source, edits=[])
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
