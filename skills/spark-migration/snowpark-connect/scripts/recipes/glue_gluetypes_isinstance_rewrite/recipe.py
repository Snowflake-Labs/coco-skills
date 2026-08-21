"""Rewrite Glue ``gluetypes`` value-membership tests
(``field.dataType in [BooleanType(), IntegerType()]``) to the Spark-correct
``isinstance(field.dataType, (T.BooleanType, T.IntegerType))``.

Implements Glue recipe **G9** from ``references/python/glue-recipes.md``.

What it does
------------

Glue's ``gluetypes`` classes are singletons compared **by value**, so Glue code
legitimately does a membership test against a list of *instances*. The
``pyspark.sql.types`` equivalents must be compared **by type**::

    # BEFORE
    if field.dataType in [BooleanType(), IntegerType(), LongType(), NullType()]:

    # AFTER
    import pyspark.sql.types as T

    if isinstance(field.dataType, (T.BooleanType, T.IntegerType, T.LongType, T.NullType)):

Two things change together: the instantiation drops (``BooleanType()`` — an
instance in a list — becomes ``T.BooleanType``, the bare class) and the
container becomes an ``isinstance`` tuple.

``not in`` is handled too, as a negated ``isinstance``::

    x not in [IntegerType()]   ->   not isinstance(x, (T.IntegerType,))

A single-element tuple renders with the required trailing comma
(``(T.IntegerType,)``). Any parentheses on the original comparison are carried
over to the replacement node, so the result compiles and keeps its original
precedence.

Import strategy (one strategy, always)
--------------------------------------

The recipe **always** emits ``T.``-prefixed class names and injects
``import pyspark.sql.types as T`` exactly once at the top of the processed unit,
unless that exact import is already present. It deliberately does *not* try to
reuse individual ``from pyspark.sql.types import BooleanType`` bindings that may
already exist: a single strategy keeps the output uniform, and the injected
alias import coexists harmlessly with any pre-existing named imports.

If the name ``T`` is already bound to **anything else** in the module (another
``import ... as T``, a ``from x import T``, an assignment, a ``def``/``class``
``T``, or a parameter named ``T``), the recipe rewrites **nothing** and returns
the source untouched rather than shadowing the existing binding.

Trigger
-------

A membership ``Comparison`` with exactly one comparison target, whose operator
is ``in`` or ``not in`` and whose right-hand side is a literal ``list`` /
``tuple`` / ``set`` in which **every** element is a **zero-argument** call to a
``Name`` (or dotted ``Attribute``) whose final identifier ends in ``Type``.

Handled statement positions: ``SimpleStatementLine`` (assignment, ``return``,
``assert``, bare expression, ...), ``if`` / ``elif`` tests and ``while`` tests.
A comparison in any other statement position is left alone.

Negative cases (must NOT trigger)
---------------------------------

* ``x in [1, 2, 3]`` — elements are not calls.
* ``x in [SomeClass()]`` — the name does not end in ``Type``.
* ``x in [IntegerType(), 5]`` — mixed; **any** non-conforming element disables
  the whole rewrite, with no annotation (it is probably an ordinary membership
  test).
* ``x in [IntegerType(1)]`` / ``[DecimalType(10, 2)]`` — the call has arguments,
  so it is not a plain singleton comparison.
* ``x in types`` — right side is not a literal container.
* Chained comparisons (``a in [T()] == b``) — more than one comparison target.
* Any module where ``T`` is already bound to something else.

Idempotency
-----------

Re-running on this recipe's own output is a byte-for-byte no-op with zero
edits: the output contains no ``in [<Type>()]`` membership test, the substring
gate (``"Type("``) no longer matches, the injected import is guarded by an
existing-import check, and each rewritten statement is additionally guarded by
``_annotate.comment_above_contains``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "glue_gluetypes_isinstance_rewrite"
MIN_SCOS_VERSION = "0.4.0"
NOTEBOOK_SCOPE = "cell"

_CODE = "SPRKCNTPY3607"
_ALIAS = "T"
_IMPORT_TEXT = "import pyspark.sql.types as T"
_TYPES_MODULE = "pyspark.sql.types"


def _comment(names: list, negated: bool) -> str:
    before = ", ".join(f"{n}()" for n in names)
    after = ", ".join(f"{_ALIAS}.{n}" for n in names)
    call = f"isinstance(<expr>, ({after},))" if len(names) == 1 else (
        f"isinstance(<expr>, ({after}))"
    )
    op = "not in" if negated else "in"
    prefix = "not " if negated else ""
    return (
        f"# SCOS: [{_CODE}-Fixed] {RECIPE_ID}: <expr> {op} [{before}] -> "
        f"{prefix}{call} (gluetypes singletons compare by value; "
        f"pyspark.sql.types must compare by type)"
    )


def _final_name(node: cst.CSTNode) -> Optional[str]:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute) and isinstance(node.attr, cst.Name):
        return node.attr.value
    return None


def _dotted(node: cst.CSTNode) -> str:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted(node.value)}.{node.attr.value}"
    return ""


def _type_class_name(node: cst.CSTNode) -> Optional[str]:
    """Final identifier of a **zero-argument** call to a ``*Type`` name."""
    if not isinstance(node, cst.Call) or node.args:
        return None
    name = _final_name(node.func)
    if name is not None and name.endswith("Type"):
        return name
    return None


def _membership_type_names(node: cst.CSTNode):
    """``(type_names, negated)`` for a qualifying membership comparison.

    Returns ``None`` unless ``node`` is ``<expr> in|not in <literal container>``
    where every element is a zero-arg ``*Type()`` call.
    """
    if not isinstance(node, cst.Comparison) or len(node.comparisons) != 1:
        return None
    target = node.comparisons[0]
    if isinstance(target.operator, cst.In):
        negated = False
    elif isinstance(target.operator, cst.NotIn):
        negated = True
    else:
        return None
    container = target.comparator
    if not isinstance(container, (cst.List, cst.Tuple, cst.Set)):
        return None
    if not container.elements:
        return None
    names: list[str] = []
    for el in container.elements:
        if not isinstance(el, cst.Element):
            return None  # starred element
        name = _type_class_name(el.value)
        if name is None:
            return None  # any non-conforming element disables the rewrite
        names.append(name)
    return names, negated


def _isinstance_expr(
    left: cst.BaseExpression, names: list, negated: bool, original: cst.Comparison
) -> cst.BaseExpression:
    """``isinstance(left, (T.A, T.B))`` / ``not isinstance(...)``.

    LibCST renders a one-element ``Tuple`` with the required trailing comma.
    Parentheses from ``original`` are carried over so precedence is preserved.
    """
    classes = cst.Tuple(
        elements=[
            cst.Element(
                value=cst.Attribute(value=cst.Name(_ALIAS), attr=cst.Name(n))
            )
            for n in names
        ]
    )
    call = cst.Call(
        func=cst.Name("isinstance"),
        args=[cst.Arg(left), cst.Arg(classes)],
    )
    if not negated:
        return call.with_changes(lpar=original.lpar, rpar=original.rpar)
    return cst.UnaryOperation(
        operator=cst.Not(),
        expression=call,
        lpar=original.lpar,
        rpar=original.rpar,
    )


class _ComparisonRewriter(cst.CSTTransformer):
    """Rewrite qualifying membership comparisons in one expression subtree."""

    def __init__(self) -> None:
        super().__init__()
        self.comments: list[str] = []

    def leave_Comparison(  # type: ignore[override]
        self, original_node: cst.Comparison, updated_node: cst.Comparison
    ) -> cst.BaseExpression:
        found = _membership_type_names(updated_node)
        if found is None:
            return updated_node
        names, negated = found
        comment = _comment(names, negated)
        if comment not in self.comments:
            self.comments.append(comment)
        return _isinstance_expr(updated_node.left, names, negated, updated_node)


def _already_annotated(stmt: cst.CSTNode) -> bool:
    for line in getattr(stmt, "leading_lines", ()) or ():
        if line.comment is not None and RECIPE_ID in line.comment.value:
            return True
    return False


def _with_leading_comments(stmt, comments: list):
    new_leading = list(stmt.leading_lines) + [
        cst.EmptyLine(comment=cst.Comment(c)) for c in comments
    ]
    return stmt.with_changes(leading_lines=tuple(new_leading))


class _TBindingScan(cst.CSTVisitor):
    """Detect an existing ``import pyspark.sql.types as T`` vs a conflicting
    binding of the name ``T``."""

    def __init__(self) -> None:
        super().__init__()
        self.has_alias = False
        self.conflict = False

    def visit_Import(self, node: cst.Import) -> None:
        for alias in node.names:
            asname = alias.asname
            if asname is None or not isinstance(asname.name, cst.Name):
                continue
            if asname.name.value != _ALIAS:
                continue
            if _dotted(alias.name) == _TYPES_MODULE:
                self.has_alias = True
            else:
                self.conflict = True

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        if isinstance(node.names, cst.ImportStar):
            return
        for alias in node.names:
            bound = (
                alias.asname.name.value
                if alias.asname is not None and isinstance(alias.asname.name, cst.Name)
                else _final_name(alias.name)
            )
            if bound == _ALIAS:
                self.conflict = True

    def visit_Assign(self, node: cst.Assign) -> None:
        for tgt in node.targets:
            if isinstance(tgt.target, cst.Name) and tgt.target.value == _ALIAS:
                self.conflict = True

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        if isinstance(node.target, cst.Name) and node.target.value == _ALIAS:
            self.conflict = True

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        if node.name.value == _ALIAS:
            self.conflict = True

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        if node.name.value == _ALIAS:
            self.conflict = True

    def visit_Param(self, node: cst.Param) -> None:
        if node.name.value == _ALIAS:
            self.conflict = True


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kw)
        self._need_import = False

    # -- shared statement handling -----------------------------------------
    def _annotate_stmt(self, original_node, updated_node, new_stmt, comments: list):
        self._need_import = True
        self._record(
            self._line_of(original_node),
            f"gluetypes membership -> isinstance ({len(comments)} comparison(s))",
        )
        return _with_leading_comments(new_stmt, comments)

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        start = self._line_of(original_node)
        if _already_annotated(updated_node) or _annotate.comment_above_contains(
            self._lines, start, RECIPE_ID
        ):
            return updated_node
        sub = _ComparisonRewriter()
        new_stmt = updated_node.visit(sub)
        if not sub.comments:
            return updated_node
        return self._annotate_stmt(original_node, updated_node, new_stmt, sub.comments)

    def _leave_test_stmt(self, original_node, updated_node):
        """``if`` / ``elif`` / ``while``: rewrite the *test* expression only.

        Nested statements in the body have already been handled bottom-up by
        ``leave_SimpleStatementLine``, so restricting the sub-transformer to
        ``test`` avoids double-handling and keeps each comment on the line that
        actually changed.
        """
        start = self._line_of(original_node)
        if _already_annotated(updated_node) or _annotate.comment_above_contains(
            self._lines, start, RECIPE_ID
        ):
            return updated_node
        sub = _ComparisonRewriter()
        new_test = updated_node.test.visit(sub)
        if not sub.comments:
            return updated_node
        new_stmt = updated_node.with_changes(test=new_test)
        return self._annotate_stmt(original_node, updated_node, new_stmt, sub.comments)

    def leave_If(self, original_node: cst.If, updated_node: cst.If):  # type: ignore[override]
        return self._leave_test_stmt(original_node, updated_node)

    def leave_While(  # type: ignore[override]
        self, original_node: cst.While, updated_node: cst.While
    ):
        return self._leave_test_stmt(original_node, updated_node)

    def leave_Module(  # type: ignore[override]
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        if not self._need_import:
            return updated_node
        scan = _TBindingScan()
        updated_node.visit(scan)
        if scan.has_alias:
            return updated_node
        import_stmt = cst.parse_statement(_IMPORT_TEXT)
        return updated_node.with_changes(body=(import_stmt, *updated_node.body))


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    # Cheap gate: the trigger requires a zero-arg call to a *Type name.
    if "Type(" not in source:
        return _common.RecipeResult(source=source, edits=[])
    # Never shadow an existing binding of the alias ``T``.
    try:
        module = cst.parse_module(source)
    except Exception:  # noqa: BLE001
        return _common.RecipeResult(source=source, edits=[])
    scan = _TBindingScan()
    module.visit(scan)
    if scan.conflict:
        return _common.RecipeResult(source=source, edits=[])
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
