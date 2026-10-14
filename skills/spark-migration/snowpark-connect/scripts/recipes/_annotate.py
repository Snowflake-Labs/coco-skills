"""Shared helpers for ``*_annotate`` recipes under ``scripts/recipes``.

The half-dozen annotate-only recipes (wildcard read, self-join, hotpath
materialization, cloud-stage perf, unionByName allowMissingColumns,
passthrough SQL) all follow the same three-step pattern:

  1. Detect a target call/pattern inside a SimpleStatementLine subtree.
  2. Confirm the recipe hasn't already annotated this line (idempotency).
  3. Prepend a single ``# SCOS-TODO`` or ``# SCOS-WARN`` comment line.

The recipes provide their own detector logic (a small ``CSTVisitor``)
and their own comment text; everything else lives here so the recipes
stay short and consistent.
"""
from __future__ import annotations

from typing import Iterable

import libcst as cst


def comment_above_contains(
    source_lines: list[str], stmt_start_line: int, marker: str
) -> bool:
    """True iff at least one of the contiguous comment lines *immediately*
    preceding ``stmt_start_line`` contains ``marker`` as a substring.

    Walks upward from ``stmt_start_line - 1`` while lines are blank or
    start with ``#``. Stops at the first non-comment, non-blank line.

    ``stmt_start_line`` is the 1-based source line of the statement
    (as returned by LibCST ``PositionProvider``). ``source_lines`` is
    the file split by newlines without trailing newlines (as
    ``BaseRecipe._lines`` already holds).

    This catches both the normal case (LibCST stores the comment in
    ``SimpleStatementLine.leading_lines``) and the first-statement case
    (LibCST stores it in ``Module.header`` instead).
    """
    if stmt_start_line < 2:
        return False
    i = stmt_start_line - 2  # 0-based index of line immediately above
    while i >= 0:
        stripped = source_lines[i].lstrip()
        if not stripped:
            i -= 1
            continue
        if stripped.startswith("#"):
            if marker in stripped:
                return True
            i -= 1
            continue
        return False
    return False


def prepend_comment(
    stmt: cst.SimpleStatementLine, comment_text: str
) -> cst.SimpleStatementLine:
    """Return ``stmt`` with a single ``# ...`` EmptyLine prepended to
    ``leading_lines`` (preserving any existing leading whitespace/blanks).

    ``comment_text`` must start with ``#`` and not contain a newline.
    """
    if not comment_text.startswith("#") or "\n" in comment_text:
        raise ValueError(
            f"comment_text must start with '#' and be single-line; got {comment_text!r}"
        )
    new_leading = list(stmt.leading_lines) + [
        cst.EmptyLine(comment=cst.Comment(comment_text))
    ]
    return stmt.with_changes(leading_lines=tuple(new_leading))


def any_string_arg_matches(
    call: cst.Call,
    predicate,
    *,
    include_list_tuple: bool = True,
) -> bool:
    """Helper for detectors that look at positional string-literal args.

    ``predicate`` is called with the evaluated string value; returns True
    on the first hit. ``include_list_tuple=True`` also descends into list
    and tuple literals one level deep (the common "list of paths" shape).
    """
    for arg in call.args:
        if arg.keyword is not None:
            continue
        val = arg.value
        s = _string_value(val)
        if s is not None and predicate(s):
            return True
        if include_list_tuple and isinstance(val, (cst.List, cst.Tuple)):
            for el in val.elements:
                if isinstance(el, cst.Element):
                    inner = _string_value(el.value)
                    if inner is not None and predicate(inner):
                        return True
    return False


def _string_value(node: cst.CSTNode):
    """Return the Python string value of a SimpleString or
    ConcatenatedString, else None for anything we can't statically eval."""
    if isinstance(node, cst.SimpleString):
        try:
            return node.evaluated_value
        except Exception:  # noqa: BLE001
            return None
    if isinstance(node, cst.ConcatenatedString):
        try:
            return node.evaluated_value
        except Exception:  # noqa: BLE001
            return None
    return None


__all__ = [
    "comment_above_contains",
    "prepend_comment",
    "any_string_arg_matches",
]
