"""Cast / annotate ``DataFrame.count()`` results that flow into other calls.

What it does
------------

Under Snowpark Connect (the Spark **Connect** client, not classic PySpark),
``DataFrame.count()`` returns a ``numpy.int64`` rather than a native Python
``int``. Several Spark Connect DataFrame APIs do a *strict* type check on their
partition/row-count argument and reject anything that isn't exactly an ``int``::

    pyspark.errors.exceptions.base.PySparkTypeError:
        [NOT_COLUMN_OR_STR] Argument `numPartitions` should be a Column or str,
        got int64.

(Verified live: ``type(df.count()) is numpy.int64`` and
``df.repartition(df.count())`` raises the above; ``df.repartition(int(df.count()))``
works.) The classic ``pyspark.sql.DataFrame.count`` docs say it returns ``int`` --
that contract holds for classic PySpark; the Connect path deviates.

This recipe therefore:

  * **Rewrites** (wraps in ``int(...)``) when a ``count()`` result is fed to a
    known strict-int-argument DataFrame method -- either inline
    (``df.repartition(other.count())``) or via a variable
    (``n = df.count(); ... df.repartition(n)``, cast applied at the
    assignment). Value-preserving, deterministic, fixes the failure.
  * **Annotates** (``# SCOS:`` comment, no rewrite) when a ``count()`` result is
    passed to some *other* function we can't prove needs an ``int`` -- a
    heads-up that the value is ``numpy.int64`` and may need an ``int()`` cast if
    that callee does a strict ``isinstance(int)`` check.

Strict-int-argument methods
---------------------------

``repartition``, ``coalesce``, ``repartitionByRange``, ``limit``, ``take``,
``head``, ``tail``, ``offset`` -- all take an int count as their (first)
positional argument.

Trigger
-------

A ``count()`` call is ``<expr>.count()`` with **no** arguments (the DataFrame
form; ``str.count(x)`` / ``list.count(x)`` require an argument and are skipped).

Negative cases (must NOT trigger)
---------------------------------

* ``df.count()`` used in arithmetic / comparison / logging only -- ``numpy.int64``
  is harmless there.
* ``print(df.count())`` / ``str(...)`` / ``len(...)`` etc. -- safe sinks.
* A ``count()`` already wrapped in ``int(...)`` (idempotency, plus the
  ``# SCOS:`` marker guard).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "count_numpy_int_cast_rewrite"
MIN_SCOS_VERSION = "0.4.0"

# DataFrame methods whose (first) positional arg is a strict int partition/row
# count in the Spark Connect client.
_INT_ARG_METHODS = frozenset(
    {
        "repartition",
        "coalesce",
        "repartitionByRange",
        "limit",
        "take",
        "head",
        "tail",
        "offset",
    }
)

# Callees where a numpy.int64 is harmless -- don't annotate these.
_SAFE_SINKS = frozenset(
    {
        "print", "str", "repr", "len", "format", "int", "float", "range",
        "sorted", "list", "tuple", "set", "abs", "min", "max", "sum",
        "info", "debug", "warning", "warn", "error", "critical",
        "exception", "log",
    }
)

_MARKER = "count-returns-numpy-int64"

_CAST_COMMENT = (
    "# SCOS: [SPRKCNTPY5000-Warning] count-returns-numpy-int64: DataFrame.count() returns numpy.int64 "
    "under Snowpark Connect, which strict int-argument APIs "
    "(repartition/coalesce/limit/take/head/tail) reject as NOT_COLUMN_OR_STR; "
    "wrapped the count in int() to normalize (value unchanged)."
)
_ANNOTATE_COMMENT = (
    "# SCOS: [SPRKCNTPY5000-Warning] count-returns-numpy-int64: DataFrame.count() returns numpy.int64 "
    "under Snowpark Connect (not a Python int). If this value is passed to an "
    "API that does a strict isinstance(int) check, wrap it with int(<expr>)."
)


def _callee_method(call: cst.Call) -> Optional[str]:
    func = call.func
    if isinstance(func, cst.Name):
        return func.value
    if isinstance(func, cst.Attribute) and isinstance(func.attr, cst.Name):
        return func.attr.value
    return None


def _is_count_call(expr: cst.BaseExpression) -> bool:
    """True iff ``expr`` is ``<something>.count()`` with no arguments."""
    return (
        isinstance(expr, cst.Call)
        and isinstance(expr.func, cst.Attribute)
        and isinstance(expr.func.attr, cst.Name)
        and expr.func.attr.value == "count"
        and len(expr.args) == 0
    )


def _wrap_int(expr: cst.BaseExpression) -> cst.Call:
    return cst.Call(func=cst.Name("int"), args=[cst.Arg(value=expr)])


def _single_name_target(node: cst.CSTNode) -> Optional[str]:
    if isinstance(node, cst.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0].target, cst.Name):
            return node.targets[0].target.value
    if isinstance(node, cst.AnnAssign) and isinstance(node.target, cst.Name):
        return node.target.value
    return None


class _Collector(cst.CSTVisitor):
    """Module-wide pass: which names are bound to a ``count()`` and where each
    name is consumed."""

    def __init__(self) -> None:
        super().__init__()
        self.count_vars: set[str] = set()
        self.int_consumed: set[str] = set()
        self.other_consumed: set[str] = set()

    def _record_assign(self, node: cst.CSTNode, value: Optional[cst.BaseExpression]):
        name = _single_name_target(node)
        if name is not None and value is not None and _is_count_call(value):
            self.count_vars.add(name)

    def visit_Assign(self, node: cst.Assign) -> None:
        self._record_assign(node, node.value)

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        self._record_assign(node, node.value)

    def visit_Call(self, node: cst.Call) -> None:
        method = _callee_method(node)
        is_int = method in _INT_ARG_METHODS
        safe = method in _SAFE_SINKS
        for arg in node.args:
            if arg.keyword is not None:
                continue
            if isinstance(arg.value, cst.Name):
                if is_int:
                    self.int_consumed.add(arg.value.value)
                elif not safe:
                    self.other_consumed.add(arg.value.value)


class _Classifier(cst.CSTVisitor):
    """Per-statement: does it need a cast, an annotation, or nothing?"""

    def __init__(
        self, count_vars: set[str], needs_cast: set[str], note_only: set[str]
    ) -> None:
        super().__init__()
        self._needs_cast = needs_cast
        self._note_only = note_only
        self.cast = False
        self.annotate = False

    def visit_Call(self, node: cst.Call) -> None:
        method = _callee_method(node)
        has_inline_count = any(
            a.keyword is None and _is_count_call(a.value) for a in node.args
        )
        if not has_inline_count:
            return
        if method in _INT_ARG_METHODS:
            self.cast = True
        elif method not in _SAFE_SINKS:
            self.annotate = True

    def _check_assign(self, node: cst.CSTNode, value: Optional[cst.BaseExpression]):
        if value is None or not _is_count_call(value):
            return
        name = _single_name_target(node)
        if name is None:
            return
        if name in self._needs_cast:
            self.cast = True
        elif name in self._note_only:
            self.annotate = True

    def visit_Assign(self, node: cst.Assign) -> None:
        self._check_assign(node, node.value)

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        self._check_assign(node, node.value)


class _Rewriter(cst.CSTTransformer):
    """Wraps count() args in int() for strict-int methods and for
    needs_cast variable assignments."""

    def __init__(self, needs_cast: set[str]) -> None:
        super().__init__()
        self._needs_cast = needs_cast

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.BaseExpression:
        if _callee_method(updated_node) not in _INT_ARG_METHODS:
            return updated_node
        new_args = []
        changed = False
        for arg in updated_node.args:
            if arg.keyword is None and _is_count_call(arg.value):
                new_args.append(arg.with_changes(value=_wrap_int(arg.value)))
                changed = True
            else:
                new_args.append(arg)
        return updated_node.with_changes(args=new_args) if changed else updated_node

    def _rewrite_assign_value(self, name: Optional[str], value: cst.BaseExpression):
        if name in self._needs_cast and _is_count_call(value):
            return _wrap_int(value)
        return value

    def leave_Assign(self, original_node: cst.Assign, updated_node: cst.Assign) -> cst.Assign:
        name = _single_name_target(updated_node)
        return updated_node.with_changes(
            value=self._rewrite_assign_value(name, updated_node.value)
        )

    def leave_AnnAssign(self, original_node: cst.AnnAssign, updated_node: cst.AnnAssign) -> cst.AnnAssign:
        if updated_node.value is None:
            return updated_node
        name = _single_name_target(updated_node)
        return updated_node.with_changes(
            value=self._rewrite_assign_value(name, updated_node.value)
        )


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(
        self,
        *,
        source: str,
        file: str,
        facts_db: Optional[str] = None,
        count_vars: Optional[set[str]] = None,
        needs_cast: Optional[set[str]] = None,
        note_only: Optional[set[str]] = None,
    ) -> None:
        super().__init__(source=source, file=file, facts_db=facts_db)
        self._count_vars = count_vars or set()
        self._needs_cast = needs_cast or set()
        self._note_only = note_only or set()

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        start = self._line_of(original_node)
        if _annotate.comment_above_contains(self._lines, start, _MARKER):
            return updated_node
        cls = _Classifier(self._count_vars, self._needs_cast, self._note_only)
        original_node.visit(cls)
        if cls.cast:
            new_node = updated_node.visit(_Rewriter(self._needs_cast))
            self._record(start, "int() cast for count() numpy.int64")
            return _annotate.prepend_comment(new_node, _CAST_COMMENT)
        if cls.annotate:
            self._record(start, "annotate count() numpy.int64")
            return _annotate.prepend_comment(updated_node, _ANNOTATE_COMMENT)
        return updated_node


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    module = cst.parse_module(source)
    collector = _Collector()
    module.visit(collector)
    needs_cast = collector.count_vars & collector.int_consumed
    note_only = (collector.count_vars & collector.other_consumed) - needs_cast
    wrapper = cst.MetadataWrapper(module, unsafe_skip_copy=True)
    recipe = _Recipe(
        source=source, file=file, facts_db=facts_db,
        count_vars=collector.count_vars, needs_cast=needs_cast,
        note_only=note_only,
    )
    new_module = wrapper.visit(recipe)
    return _common.RecipeResult(source=new_module.code, edits=list(recipe.edits))
