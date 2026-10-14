"""Annotate AWS Glue ``Filter.apply(frame=…, f=…)`` calls with the null-predicate
semantics trap, and — where the lambda body is readable — the concrete null-safe
Spark rewrite. This recipe **never** modifies code; the output is byte-identical
to the input.

Why annotate-only (a deliberate design decision)
------------------------------------------------

This is recipe G5 in ``references/python/glue-recipes.md`` — the single
highest-value Glue recipe, because a naive port **silently loses rows** rather
than raising.

Glue runs the predicate as a **Python** callable, row-wise, under Python
truthiness. Spark evaluates a **Column** expression under SQL three-valued
logic. On a nullable column the two disagree::

    # Glue: None != "d" is True in Python -> null-op rows are KEPT
    Upsert = Filter.apply(frame=DyF, f=lambda row: row["op"] != "d")

    # Naive Spark port: F.col("op") != "d" is NULL for a null op, and NULL is
    # falsy in a filter -> the row is DROPPED. Row counts silently diverge.
    upsert_df = df.filter(F.col("op") != "d")

    # Correct port restores Glue semantics with an explicit isNull() guard:
    upsert_df = df.filter(F.col("op").isNull() | (F.col("op") != "d"))

The asymmetry is the whole point: a negated or ``!=`` predicate on a nullable
column needs an ``isNull()`` guard, a positive predicate (``==``, ``>``, ``<``,
``>=``, ``<=``, ``in``) does not — ``== "d"`` is NULL for a null op, which is
falsy, which excludes the row, exactly as Glue does.

Recovering the *correct* guard requires two facts this recipe cannot know:
whether the column is actually nullable, and what the predicate was *meant* to
express. An automatic rewrite would therefore risk changing row semantics in
exactly the way the recipe exists to prevent. So the transform is intentionally
annotate-only and permanently so — this is a design decision, not a limitation
to be fixed in a later phase. The recipe flags the site with
``SPRKCNTPY3604-Error``; a human (or the LLM fixer, with nullability in hand)
authors the rewrite and validates each branch's row counts against the source.

What it does
------------

Prepends a multi-line ``# SCOS: TODO - [SPRKCNTPY3604-Error]`` block above the
statement and returns the statement unchanged. The block always explains the
trap and the asymmetry rule; additionally, the ``f=`` predicate is inspected and
the guidance tailored:

* **negated** — the lambda body contains ``!=``, ``not``, or ``~``: the naive
  port drops null rows and an ``isNull()`` guard is required. When the compared
  column can be recovered from a ``row["col"]`` subscript, the concrete rewrite
  is emitted, e.g. ``df.filter(F.col("op").isNull() | (F.col("op") != "d"))``.
* **positive** — the body is a purely positive comparison (``==``, ``>``, ``<``,
  ``>=``, ``<=``, ``in``): the direct port is expected to be **already correct**,
  because the positive predicate excludes NULL in both engines. The TODO is
  still emitted so a human confirms nullability, but it says so.
* **unclassified** — the body is compound, a named function reference, or a
  call: generic guidance plus the asymmetry rule.

Trigger
-------

``Filter.apply(...)`` — keyword form (``frame=<F>, f=<PREDICATE>``), positional
form (``apply(<F>, <PRED>)``), mixed form, and a dotted receiver
(``awsglue.transforms.Filter.apply(...)``).

Negative cases (must NOT trigger)
---------------------------------

* No ``Filter.apply`` token in the source (cheap substring gate on ``Filter``).
* A plain ``df.filter(...)`` / ``F.col(...)`` PySpark filter — not the Glue
  transform.
* Some other ``.apply(...)`` (``ApplyMapping.apply``, ``ResolveChoice.apply``,
  a pandas ``.apply``) — the receiver must resolve to ``Filter``.

Idempotency
-----------

Re-running on annotated source is a byte-for-byte no-op with zero edits: the
leading-comment ``RECIPE_ID`` check (``_annotate.comment_above_contains``) skips
any statement this recipe already annotated. Since the recipe never rewrites,
the code portion is unconditionally identical across runs.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "glue_filter_apply_null_semantics_annotate"
MIN_SCOS_VERSION = "0.4.0"
NOTEBOOK_SCOPE = "cell"

_EWI = "SPRKCNTPY3604"

# Predicate classifications.
_NEGATED = "negated"
_POSITIVE = "positive"
_UNKNOWN = "unclassified"

_POSITIVE_OPS = (
    cst.Equal,
    cst.GreaterThan,
    cst.GreaterThanEqual,
    cst.LessThan,
    cst.LessThanEqual,
    cst.In,
)

_HEAD = (
    f"# SCOS: TODO - [{_EWI}-Error] {RECIPE_ID}: Glue Filter.apply runs a PYTHON "
    f"predicate row-wise (Python truthiness); Spark evaluates a COLUMN expression "
    f"under SQL three-valued logic. On a nullable column they diverge and a naive "
    f"port SILENTLY DROPS ROWS (no error is raised)."
)
_EXAMPLE = (
    f"# SCOS: TODO - [{_EWI}-Error] {RECIPE_ID}: Canonical CDC example: Glue's "
    f"lambda row: row[\"op\"] != \"d\" KEEPS null-op rows because None != \"d\" is True "
    f"in Python, but F.col(\"op\") != \"d\" is NULL for a null op, which is falsy in a "
    f"filter, so the row is DROPPED."
)
_RULE = (
    f"# SCOS: TODO - [{_EWI}-Error] {RECIPE_ID}: Rule: any negated or != predicate on "
    f"a nullable column needs an isNull() guard - "
    f"df.filter(F.col(\"c\").isNull() | (F.col(\"c\") != X)); positive predicates "
    f"(==, >, <, >=, <=, in) do NOT, because they already exclude NULL in both engines."
)
_VALIDATE = (
    f"# SCOS: TODO - [{_EWI}-Error] {RECIPE_ID}: Confirm the column's nullability and "
    f"validate each branch's row count against the Glue source before accepting the port."
)


def _dotted(node: cst.BaseExpression) -> str:
    """``a.b.c`` -> ``"a.b.c"``; anything else -> ``""``."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr.value}" if base else ""
    return ""


def _is_filter_apply(call: cst.Call) -> bool:
    """``Filter.apply(...)`` including a dotted receiver."""
    func = call.func
    if not (isinstance(func, cst.Attribute) and func.attr.value == "apply"):
        return False
    receiver = _dotted(func.value)
    return receiver == "Filter" or receiver.endswith(".Filter")


def _predicate_arg(call: cst.Call) -> Optional[cst.BaseExpression]:
    """The ``f=`` predicate, or the second positional arg."""
    positional: list[cst.BaseExpression] = []
    for arg in call.args:
        if arg.star:
            continue
        if arg.keyword is None:
            positional.append(arg.value)
        elif arg.keyword.value == "f":
            return arg.value
    # positional form: apply(<frame>, <predicate>)
    if len(positional) >= 2:
        return positional[1]
    return None


class _NegationScan(cst.CSTVisitor):
    """Look for negation markers and positive comparisons in a lambda body."""

    def __init__(self) -> None:
        self.negated = False
        self.positive = False
        self.has_call = False

    def visit_Comparison(self, node: cst.Comparison) -> None:
        for target in node.comparisons:
            op = target.operator
            if isinstance(op, (cst.NotEqual, cst.NotIn, cst.IsNot)):
                self.negated = True
            elif isinstance(op, _POSITIVE_OPS):
                self.positive = True

    def visit_UnaryOperation(self, node: cst.UnaryOperation) -> None:
        if isinstance(node.operator, (cst.Not, cst.BitInvert)):
            self.negated = True

    def visit_Call(self, node: cst.Call) -> None:
        self.has_call = True


def _subscript_key(node: cst.BaseExpression) -> Optional[str]:
    """Recover ``"op"`` from ``row["op"]``."""
    if not isinstance(node, cst.Subscript):
        return None
    for element in node.slice:
        index = element.slice
        if isinstance(index, cst.Index) and isinstance(
            index.value, cst.SimpleString
        ):
            try:
                return index.value.evaluated_value
            except Exception:  # noqa: BLE001
                return None
    return None


def _literal_text(node: cst.BaseExpression) -> Optional[str]:
    """Source text of a simple literal operand, for the suggested rewrite."""
    if isinstance(node, cst.SimpleString):
        return node.value
    if isinstance(node, (cst.Integer, cst.Float)):
        return node.value
    if isinstance(node, cst.Name) and node.value in ("None", "True", "False"):
        return node.value
    return None


def _suggested_rewrite(body: cst.BaseExpression) -> Optional[str]:
    """Concrete null-safe rewrite for a single ``row["c"] != <lit>`` comparison."""
    if not isinstance(body, cst.Comparison) or len(body.comparisons) != 1:
        return None
    target = body.comparisons[0]
    if not isinstance(target.operator, cst.NotEqual):
        return None
    column = _subscript_key(body.left)
    literal = _literal_text(target.comparator)
    if column is None or literal is None:
        return None
    return (
        f'df.filter(F.col("{column}").isNull() | (F.col("{column}") != {literal}))'
    )


def _classify(predicate: Optional[cst.BaseExpression]) -> tuple[str, Optional[str]]:
    """Classify the predicate and, when possible, build a suggested rewrite."""
    if not isinstance(predicate, cst.Lambda):
        return _UNKNOWN, None
    body = predicate.body
    scan = _NegationScan()
    body.visit(scan)
    if scan.negated:
        return _NEGATED, _suggested_rewrite(body)
    if scan.positive and not scan.has_call:
        return _POSITIVE, None
    return _UNKNOWN, None


def _comments_for(kind: str, suggestion: Optional[str]) -> list[str]:
    lines = [_HEAD, _EXAMPLE]
    if kind == _NEGATED:
        lines.append(
            f"# SCOS: TODO - [{_EWI}-Error] {RECIPE_ID}: This predicate is NEGATED "
            f"(contains != / not / ~), so the direct port WILL drop null rows - it "
            f"needs an explicit isNull() guard."
        )
        if suggestion is not None:
            lines.append(
                f"# SCOS: TODO - [{_EWI}-Error] {RECIPE_ID}: Suggested rewrite: "
                f"{suggestion}"
            )
    elif kind == _POSITIVE:
        lines.append(
            f"# SCOS: TODO - [{_EWI}-Error] {RECIPE_ID}: This predicate is POSITIVE "
            f"(==, >, <, >=, <=, in), so the direct port to df.filter(F.col(...)) is "
            f"expected to be ALREADY CORRECT: the positive predicate excludes NULL in "
            f"both engines, matching Glue. No isNull() guard is needed."
        )
    else:
        lines.append(
            f"# SCOS: TODO - [{_EWI}-Error] {RECIPE_ID}: The predicate is compound or "
            f"not statically readable (named function, multi-clause boolean, or a "
            f"call), so it must be ported clause by clause."
        )
    lines.append(_RULE)
    lines.append(_VALIDATE)
    return lines


class _Detector(cst.CSTVisitor):
    """Record the first ``Filter.apply`` in a statement and its classification."""

    def __init__(self) -> None:
        super().__init__()
        self.found = False
        self.kind = _UNKNOWN
        self.suggestion: Optional[str] = None

    def visit_Call(self, node: cst.Call) -> None:
        if self.found or not _is_filter_apply(node):
            return
        self.found = True
        self.kind, self.suggestion = _classify(_predicate_arg(node))


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
        det = _Detector()
        updated_node.visit(det)
        if not det.found:
            return updated_node
        self._record(start, f"annotated Filter.apply null semantics ({det.kind})")
        # Annotate ONLY: leading_lines change, statement body untouched.
        new_leading = list(updated_node.leading_lines) + [
            cst.EmptyLine(comment=cst.Comment(c))
            for c in _comments_for(det.kind, det.suggestion)
        ]
        return updated_node.with_changes(leading_lines=tuple(new_leading))


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    if "Filter" not in source:
        return _common.RecipeResult(source=source, edits=[])
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
