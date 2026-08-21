"""Annotate imports of the RDD surface with a ``SPRKCNTPY1500`` TODO.

What it does
------------

The RDD API is unavailable in Spark Connect / SCOS. Code that imports it::

    from pyspark import RDD
    from pyspark.rdd import RDD, PipelinedRDD
    import pyspark.rdd

is relying on a surface that does not exist at runtime. The import itself is
harmless to leave in place (it is the *usage* that fails), and removing it could
strip a name that other lines still reference, so this recipe is
**annotate-only**: it prepends a uniform ``# SCOS-TODO: [SPRKCNTPY1500]`` marker
flagging that the import -- and every RDD usage it enables -- needs manual
migration to the DataFrame / Snowpark Connect surface (see
``references/python/rdd-conversion.md``). It never changes code.

Trigger
-------

A ``SimpleStatementLine`` containing:

  * ``from pyspark import RDD``           -- ``ImportFrom`` from ``pyspark`` whose
                                             imported names include ``RDD``; or
  * ``from pyspark.rdd import ...``       -- ``ImportFrom`` from the ``pyspark.rdd``
                                             module (any names); or
  * ``import pyspark.rdd`` (``as ...``)   -- ``Import`` of the ``pyspark.rdd``
                                             module.

Negative cases (must NOT trigger)
---------------------------------

* ``from pyspark.sql import functions`` / ``from pyspark import SparkConf`` --
  not the RDD surface.
* ``from pyspark import RDDInfo`` -- name is not exactly ``RDD``.

Idempotency
-----------

Re-running on annotated source is a no-op (leading-comment check via
``_annotate.comment_above_contains``).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "pyspark_rdd_import_todo_annotate"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS-TODO: [SPRKCNTPY1500] {RECIPE_ID}: the RDD API is unavailable in "
    f"Spark Connect; migrate this import and every RDD usage it enables to the "
    f"DataFrame / Snowpark Connect surface (see references/python/rdd-conversion.md)"
)


def _dotted_name(node: cst.BaseExpression) -> Optional[str]:
    """Return the dotted module name for a ``Name``/``Attribute`` chain
    (``pyspark.rdd`` -> ``"pyspark.rdd"``), else None."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute) and isinstance(node.attr, cst.Name):
        head = _dotted_name(node.value)
        if head is not None:
            return f"{head}.{node.attr.value}"
    return None


def _import_from_matches(node: cst.ImportFrom) -> bool:
    """``from pyspark.rdd import ...`` (any names) or
    ``from pyspark import RDD``."""
    if node.module is None:  # ``from . import x`` -- relative, not pyspark
        return False
    module = _dotted_name(node.module)
    if module == "pyspark.rdd":
        return True
    if module == "pyspark" and not isinstance(node.names, cst.ImportStar):
        for alias in node.names:
            if isinstance(alias.name, cst.Name) and alias.name.value == "RDD":
                return True
    return False


def _import_matches(node: cst.Import) -> bool:
    """``import pyspark.rdd`` / ``import pyspark.rdd as r`` (also matches a
    deeper ``pyspark.rdd.<x>`` submodule import)."""
    for alias in node.names:
        dotted = _dotted_name(alias.name)
        if dotted is not None and (dotted == "pyspark.rdd" or dotted.startswith("pyspark.rdd.")):
            return True
    return False


class _Detector(cst.CSTVisitor):
    def __init__(self) -> None:
        super().__init__()
        self.matched = False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        if _import_from_matches(node):
            self.matched = True

    def visit_Import(self, node: cst.Import) -> None:
        if _import_matches(node):
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
        self._record(start, "annotated RDD-surface import")
        return _annotate.prepend_comment(updated_node, _COMMENT_TEXT)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
