"""Warn on ``df.unionByName(other, allowMissingColumns=True)``.

What it does
------------

PySpark's ``df.unionByName(other, allowMissingColumns=True)`` fills
missing columns with ``NULL`` on the side that doesn't have them and
returns a unioned DataFrame with the column-set union. SCOS's
implementation has historically diverged here:

  * Some SCOS releases ignore ``allowMissingColumns`` and raise on
    schema mismatch.
  * Others fill missing columns but with engine-specific defaults
    (``NULL`` typed as VARIANT, not the target type), causing
    downstream type errors.
  * The fix-rules document (Rule 6, telemetry-confirmed) advises
    pre-aligning the schemas manually before the union.

This recipe annotates every call with ``allowMissingColumns=True``
with a leading ``# SCOS-WARN`` comment. It does not rewrite the union
because the right pre-alignment depends on the workload's intent
(``df.withColumn(missing, lit(None).cast(target_type))`` is one
option; using a Snowflake ``UNION ALL`` with explicit casting is
another).

Trigger
-------

A ``Call`` whose ``func`` is ``Attribute(attr=Name("unionByName"))``
AND whose args include a keyword ``allowMissingColumns`` with value
``True`` (or any non-False literal).

Negative cases (must NOT trigger)
---------------------------------

* ``df.unionByName(other)`` -- no ``allowMissingColumns``; the
  default is False and the union will already validate schemas.
* ``df.unionByName(other, allowMissingColumns=False)`` -- explicit
  False is equivalent to the default; no warning needed.
* ``df.union(other)`` -- the position-based ``union`` is a different
  surface (no column matching).
* Already-annotated lines.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "unionbyname_allowmissing_schema_align_warn_annotate"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS-WARN: [SPRKCNTPY5400-Warning] {RECIPE_ID}: unionByName(..., allowMissingColumns=True) "
    f"semantics differ between SCOS releases (null-fill type, raise vs "
    f"silently align) -- pre-align schemas with explicit lit(None).cast"
    f"(<type>) columns before the union or use snowflake.sql.UNION ALL "
    f"with matching columns."
)


def _has_allowmissing_true(call: cst.Call) -> bool:
    for arg in call.args:
        if arg.keyword is None:
            continue
        if not isinstance(arg.keyword, cst.Name):
            continue
        if arg.keyword.value != "allowMissingColumns":
            continue
        # We consider anything that is not literal False / 0 / None as
        # "may be True". The dominant case in real workloads is
        # ``allowMissingColumns=True``; the conservative side is to
        # warn even for ambiguous expressions, since the cost of a
        # missed warning is silent data drift.
        val = arg.value
        if isinstance(val, cst.Name):
            if val.value in ("False", "None"):
                return False
            return True
        if isinstance(val, cst.Integer) and val.value == "0":
            return False
        return True
    return False


class _Detector(cst.CSTVisitor):
    def __init__(self) -> None:
        super().__init__()
        self.matched = False

    def visit_Call(self, node: cst.Call) -> None:
        if self.matched:
            return
        if not isinstance(node.func, cst.Attribute):
            return
        if not isinstance(node.func.attr, cst.Name):
            return
        if node.func.attr.value != "unionByName":
            return
        if _has_allowmissing_true(node):
            self.matched = True


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
        if not det.matched:
            return updated_node
        self._record(start, "annotated unionByName allowMissingColumns=True")
        return _annotate.prepend_comment(updated_node, _COMMENT_TEXT)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
