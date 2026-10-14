"""Drop unsupported ``format=`` and ``path=`` keyword arguments from
``DataFrameWriter.saveAsTable(...)`` calls.

What it does
------------

PySpark's ``DataFrameWriter.saveAsTable(name, format=None, mode=None,
partitionBy=None, **options)`` accepts ``format=`` (file format hint) and
the ``path=`` option (used to register an external Hive table at a
specific HDFS / cloud path).

Snowpark Connect (SCOS) does NOT accept either keyword argument:

* ``format=`` is silently rejected — Snowflake-managed tables don't take
  a writer format here; the engine handles storage internally.
* ``path=`` is rejected — Snowflake tables are not backed by an external
  HDFS / cloud path.

A common Hadoop / Hive-on-Spark idiom is::

    df.write.saveAsTable(
        'schema.table',
        format='parquet',
        mode='overwrite',
        path='hdfs://.../warehouse/schema.table',
    )

Calling that against SCOS raises a ``TypeError: saveAsTable() got an
unexpected keyword argument 'format'`` (or 'path') at runtime.

This recipe rewrites every::

    <receiver>.saveAsTable(name, format=<X>, path=<Y>, **other_kwargs)
                                  ^^^^^^^^^^^^^^^^^^^
                                  drop these two

to::

    <receiver>.saveAsTable(name, **other_kwargs)

preserving the receiver, the table-name argument, ``mode=`` and any
other kwargs (e.g. ``partitionBy``) verbatim.  It emits a single
``# SCOS: ...`` comment above the statement so the analyzer / fixer can
audit the change.

Trigger
-------

A ``Call`` whose ``func`` is ``Attribute(attr=Name("saveAsTable"))`` AND
whose keyword arguments include at least one of ``format`` or ``path``.
The receiver can be anything (``df.write``, ``df.write.partitionBy(...)``,
chained options, etc.) -- the recipe rewrites only the terminal call and
leaves the rest of the chain alone.

Negative cases (must NOT trigger)
---------------------------------

* ``df.write.saveAsTable('t')`` -- no format / path kwarg, already SCOS-safe.
* ``df.write.saveAsTable('t', mode='overwrite')`` -- no format / path kwarg.
* ``df.write.format('parquet').saveAsTable('t')`` -- ``format()`` is a
  chained method on the writer; that surface is handled by a different
  recipe and *not* by ``saveAsTable`` kwargs.
* ``df.saveAsTextFile(...)`` / ``rdd.saveAsTable(...)`` -- different APIs.

Idempotency
-----------

After rewrite the call no longer carries ``format=`` or ``path=``, so
re-running is a no-op.  The leading ``# SCOS:`` comment is also
fingerprinted by ``RECIPE_ID`` and not duplicated on a second pass.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "saveastable_drop_format_path_kwargs_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_TARGET_METHOD = "saveAsTable"
_DROP_KWARGS = frozenset({"format", "path"})
_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY1000-Fixed] {RECIPE_ID}: dropped unsupported format=/path= kwargs "
    f"(Snowpark Connect saveAsTable() does not accept them; Snowflake manages storage)"
)


def _is_target_call(node: cst.CSTNode) -> bool:
    """True iff ``node`` is ``<receiver>.saveAsTable(...)`` and at least
    one of ``format=`` or ``path=`` appears as a keyword argument."""
    if not (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value == _TARGET_METHOD
    ):
        return False
    for arg in node.args:
        if (
            arg.keyword is not None
            and isinstance(arg.keyword, cst.Name)
            and arg.keyword.value in _DROP_KWARGS
        ):
            return True
    return False


def _strip_dropped_kwargs(call: cst.Call) -> cst.Call:
    """Return a copy of ``call`` with every ``format=``/``path=`` kwarg removed.

    Other arguments (positional, ``mode=``, ``partitionBy=``, ``**opts`` style,
    etc.) are preserved verbatim.  When the dropped kwarg is the last argument
    we also strip the trailing comma on the new last surviving argument so the
    resulting source is canonical.
    """
    kept: list[cst.Arg] = []
    for arg in call.args:
        if (
            arg.keyword is not None
            and isinstance(arg.keyword, cst.Name)
            and arg.keyword.value in _DROP_KWARGS
        ):
            continue
        kept.append(arg)

    # Drop trailing comma on the new last surviving arg so the rewrite reads
    # ``saveAsTable('t', mode='overwrite')`` rather than
    # ``saveAsTable('t', mode='overwrite',)``.
    if kept:
        kept[-1] = kept[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
    return call.with_changes(args=kept)


class _CallRewriter(cst.CSTTransformer):
    """Inline sub-transformer that strips dropped kwargs from every target
    call in a subtree.  The outer recipe counts rewrites at the statement
    level so it only emits one leading comment per statement."""

    def __init__(self) -> None:
        super().__init__()
        self.rewrites = 0

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if _is_target_call(updated_node):
            self.rewrites += 1
            return _strip_dropped_kwargs(updated_node)
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
            f"saveAsTable: dropped format=/path= kwargs ({sub.rewrites} call(s))",
        )
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
