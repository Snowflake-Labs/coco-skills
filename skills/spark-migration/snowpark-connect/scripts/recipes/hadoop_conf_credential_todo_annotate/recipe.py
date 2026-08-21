"""Annotate ``sc.hadoopConfiguration.set(...)`` credential config with a
``SPRKCNTPY3202`` TODO.

What it does
------------

Setting cloud-storage credentials through the Hadoop configuration::

    sc.hadoopConfiguration.set("fs.s3a.access.key", KEY)
    spark.sparkContext._jsc.hadoopConfiguration().set("fs.s3a.secret.key", SECRET)

has **no** Spark Connect / SCOS equivalent -- ``sparkContext.hadoopConfiguration``
(and the ``_jsc`` Java handle) are unavailable. The credential must instead be
provisioned as a Snowflake **storage integration** / external stage, which is a
human decision (integration name, allowed locations, IAM role) the recipe cannot
synthesise. So this recipe is **annotate-only**: it prepends a uniform
``# SCOS-TODO: [SPRKCNTPY3202]`` marker (the EWI code the config reference and
``ewi-codes.md`` assign to "Hadoop credential configuration replaced with
Snowflake storage integration") and never changes code.

This is deliberately distinct from ``spark_config_noop_annotate`` (which flags
cluster/runtime ``spark.*`` configs as silent no-ops with ``SPRKCNTPY1000``): a
Hadoop credential set is a *blocking* migration, not a no-op.

Trigger
-------

A ``SimpleStatementLine`` whose subtree contains BOTH (a) an attribute/method
named ``hadoopConfiguration`` and (b) a ``.set(...)`` call -- i.e. the
credential-setting shape. Requiring the ``.set`` call avoids annotating a bare
read of ``hadoopConfiguration``.

Composition note
----------------

Sorts BEFORE ``sparkcontext_property_fallback_rewrite`` (``"h" < "s"``). That
recipe is additionally guarded to skip the ``hadoopConfiguration`` attribute, so
the credential line keeps its original shape (a getattr() fallback on a
``.set(...)`` receiver would raise ``AttributeError``) and is left for the human
migrating it to a storage integration.

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

RECIPE_ID = "hadoop_conf_credential_todo_annotate"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS-TODO: [SPRKCNTPY3202] {RECIPE_ID}: sparkContext.hadoopConfiguration "
    f"is unavailable in Spark Connect; provision these cloud credentials as a "
    f"Snowflake storage integration / external stage (CREATE STORAGE INTEGRATION)"
)


class _Detector(cst.CSTVisitor):
    """Flag a statement that both references ``hadoopConfiguration`` and makes a
    ``.set(...)`` call (the credential-setting shape)."""

    def __init__(self) -> None:
        super().__init__()
        self._has_hadoop_conf = False
        self._has_set_call = False

    @property
    def matched(self) -> bool:
        return self._has_hadoop_conf and self._has_set_call

    def visit_Attribute(self, node: cst.Attribute) -> None:
        if isinstance(node.attr, cst.Name) and node.attr.value == "hadoopConfiguration":
            self._has_hadoop_conf = True

    def visit_Call(self, node: cst.Call) -> None:
        if (
            isinstance(node.func, cst.Attribute)
            and isinstance(node.func.attr, cst.Name)
            and node.func.attr.value == "set"
        ):
            self._has_set_call = True


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
        self._record(start, "annotated hadoopConfiguration credential set")
        return _annotate.prepend_comment(updated_node, _COMMENT_TEXT)


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
