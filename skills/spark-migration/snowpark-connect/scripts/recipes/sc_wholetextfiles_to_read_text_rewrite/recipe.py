"""Rewrite ``sc.wholeTextFiles(path)`` / ``<x>.sparkContext.wholeTextFiles(path)``
to ``spark.read.text(path, wholetext=True)``.

What it does
------------

``SparkContext.wholeTextFiles`` is unavailable in Spark Connect / SCOS. The
closest supported DataFrame reader is ``spark.read.text(path, wholetext=True)``,
which the SCOS engine honors (``map_read_text`` reads ``wholetext`` to drop the
line separator so each file becomes one row)::

    pairs = sc.wholeTextFiles("s3://b/p")
    ->
    pairs = spark.read.text("s3://b/p", wholetext=True)

When the receiver is ``<x>.sparkContext`` the session ``<x>`` is reused; a bare
``sc`` (the de-facto convention) becomes ``spark``. The single positional path
argument is preserved.

Shape / parity caveat (flagged in the leading comment)
------------------------------------------------------

``wholeTextFiles`` yields ``RDD[(path, content)]`` (one element per **file**);
``read.text(..., wholetext=True)`` yields a 1-column ``DataFrame[value]`` with
one row per file but **no path column** -- add ``F.input_file_name()`` if the
path is needed (verify ``input_file_name`` availability in SCOS).

Why not ``binaryFiles`` too
---------------------------

``sc.binaryFiles`` maps to ``spark.read.format("binaryFile")``, but SCOS does
**not** register a ``binaryFile`` reader -- ``map_read.py`` recognizes only
``csv``/``json``/``parquet``/``text``/``xml`` and raises
``SnowparkConnectNotImplementedError`` for anything else (and the engine's file
I/O is UTF-8 only, so raw-byte content has no analogue -- see the SCOS gaps
report, "Binary file format read"). So ``binaryFiles`` is deliberately left
unrewritten; ``sparkcontext_property_fallback_rewrite`` annotates it as an
unsupported SparkContext method call for a human to migrate.

Composition note
----------------

Sorts BEFORE ``sparkcontext_property_fallback_rewrite``
(``"sc_w" < "sp"``), so it rewrites first and leaves nothing for that recipe's
method-call annotation path to flag. Idempotent (output has no ``sc`` /
``.sparkContext`` receiver).

Negative cases (must NOT trigger)
---------------------------------

* ``obj.wholeTextFiles(...)`` where the receiver is not ``sc`` / ``.sparkContext``.
* ``sc.wholeTextFiles(path, minPartitions)`` -- more than one positional arg;
  skipped (no ``minPartitions`` analogue), left for the LLM fixer.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "sc_wholetextfiles_to_read_text_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY1500] {RECIPE_ID}: sc.wholeTextFiles -> "
    f"spark.read.text(..., wholetext=True) (1-col DataFrame 'value', one row "
    f"per file; add F.input_file_name() for the path)"
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
    """True iff ``node`` is ``<sc>.wholeTextFiles(<single_positional_arg>)``."""
    if not (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value == "wholeTextFiles"
        and _is_sc_receiver(node.func.value)
    ):
        return False
    return len(node.args) == 1 and node.args[0].keyword is None


def _rewrite(call: cst.Call) -> cst.Call:
    """``<sc>.wholeTextFiles(path)`` -> ``<session>.read.text(path, wholetext=True)``."""
    assert isinstance(call.func, cst.Attribute)
    session = _session_of(call.func.value)
    read_text = cst.Attribute(
        value=cst.Attribute(value=session, attr=cst.Name("read")),
        attr=cst.Name("text"),
    )
    path_arg = call.args[0].with_changes(
        comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
    )
    wholetext_arg = cst.Arg(
        keyword=cst.Name("wholetext"),
        value=cst.Name("True"),
        equal=cst.AssignEqual(
            whitespace_before=cst.SimpleWhitespace(""),
            whitespace_after=cst.SimpleWhitespace(""),
        ),
    )
    return cst.Call(func=read_text, args=[path_arg, wholetext_arg])


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
        self._record(
            self._line_of(original_node),
            f"sc.wholeTextFiles -> spark.read.text ({sub.rewrites} call(s))",
        )
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
