"""Mechanically rewrite SCOS-incompatible SQL inside ``spark.sql("...")`` strings.

What it does
------------

The deterministic SQL rewriter (``rag/sql_rewrite.py``) fixes a set of known,
schema-independent SQL incompatibilities that have a safe, semantics-preserving
syntactic fix (EXPLAIN drops, GROUPING SETS folding, CACHE/UNCACHE removal, …).
This recipe finds every
``<session>.sql("<static SQL>")`` call whose string argument is a *static*
literal, runs the rewriter over it, and — when the rewriter actually changed
something — swaps in the rewritten SQL and leaves a ``# SCOS:`` audit comment.
Any residual gaps the rewriter could not safely fix mechanically get an
additional ``# SCOS-TODO:`` so the LLM fixer attempts them.

Because the recipe id ends in ``_rewrite``, the analyzer tiers these as
``recipe_validated`` (already fixed) and the LLM fixer won't redo the work.

Trigger
-------

A ``Call`` whose ``func`` is ``Attribute(attr=Name("sql"))`` (i.e. ``x.sql(...)``)
with a first positional argument that is a *static* ``SimpleString`` /
``ConcatenatedString``. The rewriter is itself the strongest guard: it only
reports ``changed`` when a real SCOS gap is present, so a ``.sql(...)`` on an
unrelated object, or a string that isn't SCOS-incompatible SQL, is a no-op.

Negative cases (must NOT trigger / must no-op)
----------------------------------------------

* ``spark.sql(f"... {var} ...")`` / ``spark.sql("a" + b)`` / ``spark.sql(q)`` —
  dynamic SQL we cannot statically read; ``_string_value`` returns ``None`` → skip.
* ``spark.sql("SELECT a FROM t")`` — already SCOS-compatible → rewriter no-op.

Idempotency
-----------

After rewrite the SQL no longer triggers the rewriter (``changed=False``), and
the leading ``# SCOS:`` comment is detected so it is never duplicated.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # scripts/ for `rag`
import _common  # noqa: E402
from _annotate import _string_value  # noqa: E402
import libcst as cst  # noqa: E402

# NOTE: ``rag.sql_rewrite`` is imported lazily inside the rewriter (it pulls the
# rag package __init__, which has heavier deps). Keeping it out of module import
# means recipe *discovery* never depends on those deps — only execution does.

RECIPE_ID = "spark_sql_mechanical_rewrite"
MIN_SCOS_VERSION = "0.4.0"


def _is_sql_call(node: cst.CSTNode) -> bool:
    return (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value == "sql"
    )


def _first_static_sql_arg(call: cst.Call):
    """Return ``(index, value_str)`` for the first positional static-string arg,
    or ``(None, None)`` if the SQL is dynamic / absent."""
    for i, arg in enumerate(call.args):
        if arg.keyword is not None:
            continue
        s = _string_value(arg.value)
        if s is not None:
            return i, s
        # First positional arg is dynamic (f-string, concat, name) → don't rewrite.
        return None, None
    return None, None


def _string_literal(sql: str) -> cst.SimpleString:
    """Build a valid Python string literal node holding ``sql``, preferring a
    triple-quoted form for multi-line SQL (the rewriter pretty-prints)."""
    if "\n" not in sql:
        return cst.SimpleString(repr(sql))
    if '"""' not in sql and not sql.endswith('"'):
        return cst.SimpleString('"""' + sql + '"""')
    if "'''" not in sql and not sql.endswith("'"):
        return cst.SimpleString("'''" + sql + "'''")
    return cst.SimpleString(repr(sql))  # last resort: escaped single-line


class _SqlCallRewriter(cst.CSTTransformer):
    """Swap the SQL string of every ``*.sql("...")`` call in a subtree."""

    def __init__(self) -> None:
        super().__init__()
        self.rewrites = 0
        self.rule_ids: list[str] = []
        self.residual_rule_ids: list[str] = []

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if not _is_sql_call(updated_node):
            return updated_node
        idx, sql = _first_static_sql_arg(updated_node)
        if idx is None:
            return updated_node
        from rag.sql_rewrite import rewrite_sql  # lazy: keep discovery light
        result = rewrite_sql(sql, dialect="spark")
        if not result.changed:
            return updated_node
        self.rewrites += 1
        self.rule_ids.extend(e.rule_id for e in result.applied)
        self.residual_rule_ids.extend(f.rule_id for f in result.residual)
        new_args = list(updated_node.args)
        new_args[idx] = new_args[idx].with_changes(value=_string_literal(result.new_text))
        return updated_node.with_changes(args=new_args)


def _already_annotated(stmt: cst.SimpleStatementLine) -> bool:
    for line in stmt.leading_lines:
        if line.comment is not None and RECIPE_ID in line.comment.value:
            return True
    return False


def _with_comments(stmt: cst.SimpleStatementLine, comments: list[str]) -> cst.SimpleStatementLine:
    new_leading = list(stmt.leading_lines) + [
        cst.EmptyLine(comment=cst.Comment(c)) for c in comments
    ]
    return stmt.with_changes(leading_lines=tuple(new_leading))


def _dedupe(seq: list[str]) -> list[str]:
    out: list[str] = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        if _already_annotated(updated_node):
            return updated_node

        sub = _SqlCallRewriter()
        new_stmt = updated_node.visit(sub)
        if sub.rewrites == 0:
            return updated_node

        assert isinstance(new_stmt, cst.SimpleStatementLine)
        applied = _dedupe(sub.rule_ids)
        comments = [
            f"# SCOS: [SPRKCNTPY5400-Fixed] {RECIPE_ID}: rewrote embedded SQL ({', '.join(applied)})"
        ]
        residual = _dedupe(sub.residual_rule_ids)
        if residual:
            comments.append(
                f"# SCOS-TODO: [SPRKCNTPY5400-Error] {RECIPE_ID}: embedded SQL still has unhandled "
                f"gaps ({', '.join(residual)}); review/rewrite manually"
            )
        new_stmt = _with_comments(new_stmt, comments)
        self._record(
            self._line_of(original_node),
            f"embedded SQL rewritten ({sub.rewrites} call(s)): {', '.join(applied)}",
        )
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
