"""Annotate Snowflake Spark connector writebacks that rely on ``preactions`` /
``postactions`` with the staged-table + ``MERGE`` rewrite they need — those
connector-only hooks have no execution path inside SCOS, so a write that
carries them silently loses its CREATE/MERGE/DROP side effects.

Implements recipe **G11** from ``references/python/glue-recipes.md``. This
recipe is **annotate-only**: it never edits the statement, because recovering
the target table, the primary key, and the upsert/delete split from an opaque
SQL string embedded in a connector option is not mechanically decidable. The
LLM fixer authors the rewrite from the emitted TODO.

What it does
------------

Prepends a multi-line ``# SCOS: TODO - [SPRKCNTPY3608-IO]`` block above the
statement, spelling out the replacement shape::

    # BEFORE
    df.write.format("net.snowflake.spark.snowflake").options(**sfOpts) \\
      .option("dbtable", f'{schema}."{T}_TEMP"') \\
      .option("preactions",  "CREATE TABLE IF NOT EXISTS ...; ...") \\
      .option("postactions", "MERGE INTO ... USING ..._TEMP ...; DROP TABLE ...") \\
      .mode("append").save()

    # AFTER
    staged.write.mode("overwrite").saveAsTable(f"{schema}.{T}_TEMP")
    spark.sql(f"CREATE TABLE IF NOT EXISTS {schema}.{T} AS "
              f"SELECT * FROM {schema}.{T}_TEMP WHERE 1=0")
    spark.sql(f"MERGE INTO {schema}.{T} AS t USING {schema}.{T}_TEMP AS s "
              f"ON t.{pk} = s.{pk} "
              f"WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")
    spark.sql(f"DROP TABLE IF EXISTS {schema}.{T}_TEMP")

and carrying the two hard-won ordering/guard rules plus the SQL-dialect trap:

* **Process upserts before deletes.** After dedup there is exactly one row per
  PK, so a PK only ever appears in one branch; upserts-first keeps the two
  MERGEs independent.
* **Guard the delete MERGE with ``spark.catalog.tableExists(tgt)``** — on a
  delete-only first run the target table does not exist yet.
* ``spark.sql`` on SCOS accepts **Spark SQL only**: unqualified/unquoted
  identifiers, backticks (not double quotes) for quoting, and no
  ``DELETE ... USING`` (Snowflake/Postgres-only).

Trigger
-------

A ``SimpleStatementLine`` containing ``.option("preactions", ...)`` or
``.option("postactions", ...)`` (key matched case-insensitively on a string
literal). Deliberately **not** gated on a statically visible
``.format("snowflake")`` / ``.format("net.snowflake.spark.snowflake")``: in real
Glue jobs the format and credentials arrive together via ``.options(**sfOpts)``,
so requiring a visible format string would miss the majority of real call
sites. ``preactions``/``postactions`` are connector-exclusive option names, so
widening on them alone does not over-fire.

Negative cases (must NOT trigger)
---------------------------------

* Plain PySpark or connector I/O with no ``preactions``/``postactions`` option —
  ``.format("snowflake").option("dbtable", T).save()`` is owned by
  ``snowflake_connector_io_to_snowflake_session_rewrite``.
* ``.option("mergeSchema", ...)`` / any other option key.
* A ``preactions`` / ``postactions`` **string** that is not an option key (e.g.
  a dict literal or a comment mentioning the word).

Non-overlap with ``snowflake_connector_io_to_snowflake_session_rewrite``
-----------------------------------------------------------------------

That recipe converts ``.format("snowflake")`` read/write chains that expose a
literal ``dbtable``/``query``, and emits its own TODO for chains it cannot
convert. Alphabetical recipe ordering runs
``glue_connector_writeback_todo_annotate`` **before**
``snowflake_connector_io_...``, so this recipe skips any statement already
carrying the other recipe's marker (the reverse direction, in case ordering ever
changes) as well as its own (standard idempotency). It never edits code, so it
can never conflict with that recipe's rewrite.

Idempotency
-----------

Re-running on annotated source is a byte-for-byte no-op — the leading-comment
check via ``_annotate.comment_above_contains`` sees this recipe's marker.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "glue_connector_writeback_todo_annotate"
MIN_SCOS_VERSION = "0.4.0"
NOTEBOOK_SCOPE = "cell"

# The recipe this one must never double-annotate (see module docstring).
_SIBLING_RECIPE_ID = "snowflake_connector_io_to_snowflake_session_rewrite"

# Connector-exclusive option keys that carry the SQL side effects.
_HOOK_OPTIONS = frozenset({"preactions", "postactions"})

_TODO_LINES = [
    (
        f"# SCOS: TODO - [SPRKCNTPY3608-IO] {RECIPE_ID}: the Snowflake Spark "
        f"connector is NOT usable inside SCOS (it would round-trip "
        f"Snowflake->Snowflake) and its preactions/postactions hooks have no "
        f"execution path, so this write silently loses its CREATE/MERGE/DROP "
        f"side effects. Rewrite as a staged table + spark.sql MERGE:"
    ),
    '#   1. staged.write.mode("overwrite").saveAsTable(f"{schema}.{T}_TEMP")',
    (
        '#   2. spark.sql(f"CREATE TABLE IF NOT EXISTS {schema}.{T} AS '
        'SELECT * FROM {schema}.{T}_TEMP WHERE 1=0")'
    ),
    (
        '#   3. spark.sql(f"MERGE INTO {schema}.{T} AS t USING {schema}.{T}_TEMP '
        'AS s ON t.{pk} = s.{pk} WHEN MATCHED THEN UPDATE SET * '
        'WHEN NOT MATCHED THEN INSERT *")   # delete path: WHEN MATCHED THEN DELETE'
    ),
    '#   4. spark.sql(f"DROP TABLE IF EXISTS {schema}.{T}_TEMP")',
    (
        "#   Ordering: process UPSERTS BEFORE DELETES - after dedup there is one "
        "row per PK, so a PK only appears in one branch and the two MERGEs stay "
        "independent."
    ),
    (
        "#   Guard the delete MERGE with spark.catalog.tableExists(tgt): on a "
        "delete-only first run the target table does not exist yet."
    ),
    (
        "#   Dialect: spark.sql on SCOS accepts SPARK SQL ONLY - unquoted "
        "identifiers, backticks (not double quotes) for quoting, and no "
        "`DELETE ... USING` (that is Snowflake/Postgres-only)."
    ),
]


def _literal_str(node: cst.BaseExpression) -> Optional[str]:
    if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
        try:
            val = node.evaluated_value
        except Exception:  # noqa: BLE001
            return None
        return val if isinstance(val, str) else None
    return None


class _Detector(cst.CSTVisitor):
    """Record the first ``.option("preactions"|"postactions", ...)`` key found."""

    def __init__(self) -> None:
        super().__init__()
        self.key: Optional[str] = None

    def visit_Call(self, node: cst.Call) -> None:
        if self.key is not None:
            return
        func = node.func
        if not (
            isinstance(func, cst.Attribute)
            and isinstance(func.attr, cst.Name)
            and func.attr.value == "option"
        ):
            return
        positional = [a for a in node.args if a.keyword is None and not a.star]
        if not positional:
            return
        key = _literal_str(positional[0].value)
        if key is not None and key.lower() in _HOOK_OPTIONS:
            self.key = key.lower()


def _already_annotated(lines: list[str], start: int) -> bool:
    return _annotate.comment_above_contains(
        lines, start, RECIPE_ID
    ) or _annotate.comment_above_contains(lines, start, _SIBLING_RECIPE_ID)


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        start = self._line_of(original_node)
        if _already_annotated(self._lines, start):
            return updated_node
        det = _Detector()
        updated_node.visit(det)
        if det.key is None:
            return updated_node
        self._record(start, f"annotated connector writeback hook {det.key!r}")
        # Annotate only: the statement body is returned completely unchanged.
        return updated_node.with_changes(
            leading_lines=tuple(
                list(updated_node.leading_lines)
                + [cst.EmptyLine(comment=cst.Comment(c)) for c in _TODO_LINES]
            )
        )


def apply(
    source: str, *, file: str = "<input.py>", facts_db: Optional[str] = None
) -> _common.RecipeResult:
    # Case-insensitive gate: the option key match below is case-insensitive too
    # (Glue jobs in the wild write "preActions"/"PreActions").
    lowered = source.lower()
    if "preactions" not in lowered and "postactions" not in lowered:
        return _common.RecipeResult(source=source, edits=[])
    return _common.run_recipe(_Recipe, source, file=file, facts_db=facts_db)
