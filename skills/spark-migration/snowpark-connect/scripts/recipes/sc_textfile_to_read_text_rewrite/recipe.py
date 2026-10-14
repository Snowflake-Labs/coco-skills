"""Rewrite ``sc.textFile(path)`` / ``<x>.sparkContext.textFile(path)`` to
``spark.read.text(path)``.

What it does
------------

``SparkContext.textFile`` is unavailable in Spark Connect / SCOS. The closest
DataFrame equivalent is ``spark.read.text(path)``::

    lines = sc.textFile("data.txt")   ->   lines = spark.read.text("data.txt")

The recipe only rewrites the single-positional-argument form (the path). When
the receiver is ``<x>.sparkContext`` the session ``<x>`` is reused; when it is a
bare ``sc`` (the de-facto convention) the session name ``spark`` is used.

Composition note
----------------

This recipe sorts BEFORE ``sparkcontext_property_fallback_rewrite``
(``"sc_" < "sp"``), so it rewrites ``sc.textFile`` first and leaves nothing for
that recipe's method-call annotation path to flag. It is also idempotent, so a
re-run after the fallback is a no-op.

Type/shape caveat
-----------------

``sc.textFile`` yields ``RDD[str]`` (one element per line); ``spark.read.text``
yields a 1-column ``DataFrame[value: string]`` (one row per line). Downstream
RDD-style ``.map``/indexing on the result still needs the LLM fixer -- the
leading comment flags this.

Negative cases (must NOT trigger)
---------------------------------

* ``something.textFile(...)`` where the receiver is not ``sc`` / ``.sparkContext``.
* ``sc.textFile(path, minPartitions)`` -- more than one positional arg; skipped
  (``read.text`` has no ``minPartitions``), left for the LLM fixer.
* **RDD-chain guard:** ``sc.textFile(path)`` whose result flows into an RDD-only
  op (``a = sc.textFile(p)`` ... ``a.cartesian(a)``); the whole block is left
  intact for the LLM fixer to convert holistically. See
  ``_common.is_rdd_chain_statement`` / ``collect_rdd_chained_names``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "sc_textfile_to_read_text_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY4000-Fixed] {RECIPE_ID}: sc.textFile -> spark.read.text "
    f"(1-col DataFrame 'value'; verify downstream line-based logic)"
)


def _is_sc_receiver(expr: cst.BaseExpression) -> bool:
    """True iff ``expr`` is bare ``sc`` or terminates in ``.sparkContext``."""
    if isinstance(expr, cst.Name) and expr.value == "sc":
        return True
    return (
        isinstance(expr, cst.Attribute)
        and isinstance(expr.attr, cst.Name)
        and expr.attr.value == "sparkContext"
    )


def _session_of(receiver: cst.BaseExpression) -> cst.BaseExpression:
    """``<x>.sparkContext`` -> ``<x>``; bare ``sc`` -> ``spark`` (convention)."""
    if isinstance(receiver, cst.Attribute) and isinstance(receiver.attr, cst.Name) \
            and receiver.attr.value == "sparkContext":
        return receiver.value
    return cst.Name("spark")


def _is_target_call(node: cst.CSTNode) -> bool:
    """True iff ``node`` is ``<sc>.textFile(<single_arg>)``."""
    if not (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value == "textFile"
        and _is_sc_receiver(node.func.value)
    ):
        return False
    # Single positional path arg only.
    return len(node.args) == 1 and node.args[0].keyword is None


def _rewrite(call: cst.Call) -> cst.Call:
    """``<sc>.textFile(path)`` -> ``<session>.read.text(path)``."""
    assert isinstance(call.func, cst.Attribute)
    session = _session_of(call.func.value)
    read_text = cst.Attribute(
        value=cst.Attribute(value=session, attr=cst.Name("read")),
        attr=cst.Name("text"),
    )
    return cst.Call(func=read_text, args=[call.args[0].with_changes(comma=cst.MaybeSentinel.DEFAULT)])


class _CallRewriter(cst.CSTTransformer):
    def __init__(self) -> None:
        super().__init__()
        self.rewrites = 0

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if _is_target_call(updated_node):
            self.rewrites += 1
            return _rewrite(updated_node)
        return updated_node


def _already_annotated(stmt: cst.SimpleStatementLine) -> bool:
    for line in stmt.leading_lines:
        if line.comment is not None and RECIPE_ID in line.comment.value:
            return True
    return False


def _with_leading_comment(
    stmt: cst.SimpleStatementLine,
) -> cst.SimpleStatementLine:
    new_leading = list(stmt.leading_lines) + [
        cst.EmptyLine(comment=cst.Comment(_COMMENT_TEXT))
    ]
    return stmt.with_changes(leading_lines=tuple(new_leading))


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, *, source, file="<input.py>", facts_db=None) -> None:
        super().__init__(source=source, file=file, facts_db=facts_db)
        self._rdd_names = _common.collect_rdd_chained_names(source)

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        if _already_annotated(updated_node):
            return updated_node
        # Chain guard: leave sc.textFile(...) intact when the result flows into
        # an RDD-only op (e.g. `sc.textFile(p).cartesian(a)`) so the fixer
        # converts the whole block holistically (see _common gating rationale).
        if _common.is_rdd_chain_statement(updated_node) or (
            _common.assignment_target_names(updated_node) & self._rdd_names
        ):
            return updated_node
        sub = _CallRewriter()
        new_stmt = updated_node.visit(sub)
        if sub.rewrites == 0:
            return updated_node
        assert isinstance(new_stmt, cst.SimpleStatementLine)
        new_stmt = _with_leading_comment(new_stmt)
        self._record(
            self._line_of(original_node),
            f"sc.textFile -> spark.read.text ({sub.rewrites} call(s))",
        )
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
