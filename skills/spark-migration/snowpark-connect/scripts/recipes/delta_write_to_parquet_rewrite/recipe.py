"""Rewrite a plain Delta **write** to Parquet: ``.write.format("delta")`` -> ``.write.format("parquet")``.

What it does
------------

Delta is not a supported write format in SCOS / Snowpark Connect, but Parquet
writes are (SCOS routes file writes through a stage). For a *plain* DataFrame
write the only incompatibility is the format string, so swapping it makes the
write run::

    df.write.format("delta").mode("overwrite").save(path)
    ->
    df.write.format("parquet").mode("overwrite").save(path)

Gating (important)
------------------

* Fires ONLY on a writer chain — the ``.format(...)`` receiver must contain a
  ``.write`` / ``.writeStream`` accessor. ``spark.read.format("delta")`` is left
  untouched (reading a Delta directory as Parquet would pick up stale snapshot
  files — not safe).
* If the file uses the **DeltaTable transactional API** (``DeltaTable`` /
  ``delta.tables`` — i.e. ``.merge``/``.update``/``.forPath`` upserts), the
  whole file is skipped: Parquet has no ACID/merge/time-travel, so those stay
  for the decidable rule + manual handling.

Caveats (flagged inline)
------------------------

Parquet loses Delta's ACID/merge/time-travel/schema-evolution, and the data is
written as files to a stage rather than a managed Snowflake table (a
``.saveAsTable(...)`` target is preferable when one is known). The path itself
(e.g. an ``s3://`` URL) is a *separate* finding handled elsewhere — this recipe
only fixes the format.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "delta_write_to_parquet_rewrite"
MIN_SCOS_VERSION = "0.4.0"

_COMMENT_TEXT = (
    f"# SCOS: [SPRKCNTPY3400-Fixed] {RECIPE_ID}: Delta write format is not supported in "
    f"SCOS - converted to Parquet (loses Delta ACID/merge/time-travel; written as "
    f"files to a stage - prefer .saveAsTable() for a managed table; verify the "
    f"output path resolves to a stage)"
)

# A file using the DeltaTable transactional API can't be safely down-converted.
_DELTATABLE_API = re.compile(r"\bDeltaTable\b|\bdelta\.tables\b")


def _str_value(node: cst.BaseExpression) -> Optional[str]:
    if isinstance(node, cst.SimpleString):
        try:
            return node.evaluated_value
        except Exception:
            return node.value.strip("'\"")
    return None


def _chain_has_writer(expr: cst.BaseExpression) -> bool:
    """Walk the receiver chain; True if it contains a ``.write``/``.writeStream``."""
    node: cst.BaseExpression | None = expr
    while node is not None:
        if isinstance(node, cst.Attribute):
            if isinstance(node.attr, cst.Name) and node.attr.value in ("write", "writeStream"):
                return True
            node = node.value
        elif isinstance(node, cst.Call):
            node = node.func
        elif isinstance(node, cst.Subscript):
            node = node.value
        else:
            break
    return False


def _is_write_delta_format(node: cst.CSTNode) -> bool:
    if not (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Attribute)
        and isinstance(node.func.attr, cst.Name)
        and node.func.attr.value == "format"
    ):
        return False
    pos = [a for a in node.args if a.keyword is None and not a.star]
    if len(pos) != 1:
        return False
    val = _str_value(pos[0].value)
    if val is None or val.lower() != "delta":
        return False
    return _chain_has_writer(node.func.value)


def _rewrite(call: cst.Call) -> cst.Call:
    new_args = [call.args[0].with_changes(value=cst.SimpleString('"parquet"'))] + list(call.args[1:])
    return call.with_changes(args=new_args)


class _CallRewriter(cst.CSTTransformer):
    def __init__(self) -> None:
        super().__init__()
        self.rewrites = 0

    def leave_Call(  # type: ignore[override]
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if _is_write_delta_format(updated_node):
            self.rewrites += 1
            return _rewrite(updated_node)
        return updated_node


def _already_annotated(stmt: cst.SimpleStatementLine) -> bool:
    for line in stmt.leading_lines:
        if line.comment is not None and RECIPE_ID in line.comment.value:
            return True
    return False


def _with_leading_comment(stmt: cst.SimpleStatementLine) -> cst.SimpleStatementLine:
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
            f'write.format("delta") -> "parquet" ({sub.rewrites} call(s))',
        )
        return new_stmt


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    # File-level gate: never down-convert a file that uses the DeltaTable
    # transactional API (merge/update/forPath) — Parquet can't replace it.
    if _DELTATABLE_API.search(source):
        return _common.RecipeResult(source=source, edits=[])
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
