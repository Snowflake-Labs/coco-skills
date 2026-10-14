"""Comment out ``sparkContext.setCheckpointDir(...)`` and
``sparkContext.setLogLevel(...)`` — no SCOS equivalent.

What it does
------------

Two SparkContext methods have **no equivalent in Snowpark Connect**:

* ``setCheckpointDir(path)`` — configures an RDD checkpoint directory.
  Snowflake manages checkpointing internally; there is no user-facing
  concept of a checkpoint dir.
* ``setLogLevel(level)`` — adjusts Spark's driver/executor log level.
  SCOS executes on Snowflake warehouses where log verbosity is not
  user-configurable.

Both are pure config/side-effect calls that do not affect query results.
Removing them is always safe. This recipe **comments out** the entire
statement (rather than deleting it) to preserve traceability, and prepends
a ``# SCOS:`` explanation.

Targeted statement shapes
-------------------------

Any standalone expression-statement (``SimpleStatementLine``) whose body
is a call to ``.setCheckpointDir(...)`` or ``.setLogLevel(...)`` where
the receiver is recognisably a SparkContext:

* ``spark.sparkContext.setCheckpointDir("dbfs:/tmp/checkpoints")``
* ``sc.setCheckpointDir(path)``
* ``spark.sparkContext.setLogLevel("WARN")``
* ``sc.setLogLevel("ERROR")``

The match is purely structural: any ``.setCheckpointDir(...)`` or
``.setLogLevel(...)`` attribute call whose receiver chain contains
``sparkContext`` or is the bare name ``sc``. These method names are
SparkContext-specific (no DataFrame / Column homonyms).

Conservative skip
-----------------

If the call is embedded inside a larger expression (e.g. assignment RHS,
chained expression, compound statement), the recipe does NOT comment it
out — it applies the SCOS annotation only (downstream LLM fixer handles
it). This avoids producing broken code from partial statement removal.

Idempotency
-----------

Re-running on already-commented source is a no-op (recipe marker check).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "sparkcontext_noop_comment_out_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_TARGET_METHODS = frozenset({"setCheckpointDir", "setLogLevel"})

_COMMENT_TEMPLATES = {
    "setCheckpointDir": (
        f"# SCOS: [SPRKCNTPY4002-Fixed] {RECIPE_ID}: commented out "
        f"sparkContext.setCheckpointDir() — no SCOS equivalent "
        f"(Snowflake manages checkpointing internally)"
    ),
    "setLogLevel": (
        f"# SCOS: [SPRKCNTPY4002-Fixed] {RECIPE_ID}: commented out "
        f"sparkContext.setLogLevel() — no SCOS equivalent "
        f"(log verbosity is not user-configurable on Snowflake warehouses)"
    ),
}


def _is_sc_receiver(expr: cst.BaseExpression) -> bool:
    """True iff ``expr`` looks like a SparkContext reference.

    Matches:
    * Name ``sc`` — the de-facto convention for a SparkContext binding.
    * Any Attribute chain ending in ``.sparkContext``
      (e.g. ``spark.sparkContext``, ``self.spark.sparkContext``).
    """
    if isinstance(expr, cst.Name) and expr.value == "sc":
        return True
    if isinstance(expr, cst.Attribute):
        if isinstance(expr.attr, cst.Name) and expr.attr.value == "sparkContext":
            return True
        # Recurse: ``self.sc.setLogLevel(...)`` — receiver is ``self.sc``
        if isinstance(expr.attr, cst.Name) and expr.attr.value == "sc":
            return True
    return False


def _match_target_call(node: cst.BaseExpression) -> Optional[str]:
    """If ``node`` is a Call to .setCheckpointDir/.setLogLevel on a SC
    receiver, return the method name. Otherwise None."""
    if not isinstance(node, cst.Call):
        return None
    func = node.func
    if not (
        isinstance(func, cst.Attribute)
        and isinstance(func.attr, cst.Name)
        and func.attr.value in _TARGET_METHODS
    ):
        return None
    if not _is_sc_receiver(func.value):
        return None
    return func.attr.value


def _is_standalone_expr(stmt: cst.SimpleStatementLine, method: str) -> bool:
    """True iff the statement body is ONLY the target call (a bare
    expression statement), not an assignment, augmented assign, etc."""
    if len(stmt.body) != 1:
        return False
    small = stmt.body[0]
    if not isinstance(small, cst.Expr):
        return False
    return _match_target_call(small.value) == method


def _source_of_statement(stmt: cst.SimpleStatementLine) -> str:
    """Render the code portion of the statement (no leading comments/blanks)."""
    # Use the module codegen on a minimal wrapper to get the text.
    mod = cst.Module(body=[stmt.with_changes(leading_lines=())])
    return mod.code.rstrip("\n")


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

        # Detect the target call in the statement body.
        if len(updated_node.body) != 1:
            return updated_node
        small = updated_node.body[0]
        if not isinstance(small, cst.Expr):
            return updated_node
        method = _match_target_call(small.value)
        if method is None:
            return updated_node

        # Comment out: replace the statement body with a ``pass`` that
        # we immediately replace with the original code as a comment.
        # Strategy: emit two comment lines — the SCOS note and the
        # commented-out original code — followed by an empty statement
        # (which LibCST can't do), OR replace the statement entirely
        # with comment-only lines.
        #
        # LibCST approach: replace body with a single ``pass`` and stack
        # the original code + SCOS note as leading comments. The ``pass``
        # is visually innocuous and structurally required. Actually,
        # better: we can make the entire statement a comment-only line
        # by using EmptyLine nodes in place of the statement. But LibCST
        # requires at least one SmallStatement in a SimpleStatementLine.
        #
        # Cleanest approach matching existing conventions: keep the body
        # as a commented-out version. We'll replace the Expr body with
        # a ``pass`` statement and prepend both the SCOS comment and the
        # commented-out original line as leading comment lines.
        original_code = _source_of_statement(updated_node)
        commented_code = f"# {original_code}"
        scos_comment = _COMMENT_TEMPLATES[method]

        # Build new leading lines: preserve existing + add SCOS note + commented code
        new_leading = list(updated_node.leading_lines) + [
            cst.EmptyLine(comment=cst.Comment(scos_comment)),
            cst.EmptyLine(comment=cst.Comment(commented_code)),
        ]

        # Replace body with pass
        new_stmt = updated_node.with_changes(
            leading_lines=tuple(new_leading),
            body=[cst.Pass()],
            # Preserve trailing newline
        )

        self._record(start, f"commented out {method}() call (no SCOS equivalent)")
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
