"""Deterministic structural rewrite for the legacy
``SparkContext.getOrCreate()`` / ``SparkSession(sc)`` session-bootstrap idiom.

What it does
------------

Spark Connect (and therefore SCOS) has no ``SparkContext`` and forbids
constructing a ``SparkSession`` directly — the session must come from
``snowpark_connect.init_spark_session()``. This recipe deterministically
rewrites the two statement shapes the
``spark_builder_drop_master_init_session_rewrite`` recipe intentionally does
NOT cover (it only matches ``SparkSession.builder...master()/config()...
getOrCreate()`` chains):

* **SparkContext acquisition** — the classmethod *or* the constructor::

      sc = SparkContext.getOrCreate()
      sc = SparkContext.getOrCreate(conf)
      sc = SparkContext(conf=conf)
      sc = pyspark.SparkContext.getOrCreate()

  becomes::

      from snowflake import snowpark_connect
      # SCOS: [SPRKCNTPY1001-Fixed] ... 
      sc = snowpark_connect.init_spark_session()

* **Direct SparkSession construction** wrapping a context::

      spark = SparkSession(sc)
      spark = SparkSession(sparkContext=sc)
      spark = SparkSession(SparkContext.getOrCreate())

  becomes::

      from snowflake import snowpark_connect
      # SCOS: [SPRKCNTPY1001-Fixed] ...
      spark = snowpark_connect.init_spark_session()

Why both statements become ``init_spark_session()``
---------------------------------------------------

The dominant legacy idiom is the two-line pair::

    sc = SparkContext.getOrCreate()
    spark = SparkSession(sc)

Rewriting *each* line independently to ``init_spark_session()`` keeps **both**
names bound (``init_spark_session()`` is idempotent and returns the cached
session), so:

* ``spark`` is a real Snowpark Connect session, and
* ``sc`` stays defined, so any downstream ``sc.<...>`` use still resolves to a
  name — those references are then handled / annotated by the companion
  ``sparkcontext_property_fallback_rewrite`` recipe (which matches the bare
  ``sc`` receiver). Dropping the ``sc`` binding here would turn every
  downstream ``sc.foo`` into a ``NameError`` that no other recipe can repair.

Empirical motivation
--------------------

Diagnosed from the ``rbi`` customer bundle (run 2026-06-16): every helper
module under ``rinsights/utils/`` opened with ``sc = SparkContext.getOrCreate()``
followed by ``spark = SparkSession(sc)``. The builder recipe could not fire
(no ``.master()`` / ``.config()`` in the chain, and ``SparkSession(sc)`` is a
constructor, not a ``.getOrCreate()`` chain), so the pattern fell through to
the LLM, which fixed it inconsistently — rewriting it in some modules and
leaving a bare ``# TODO`` in others. The validation phase then had to patch
the misses by hand across several commits. This recipe closes that gap
deterministically before the LLM ever runs.

Trigger
-------

The value expression of an ``Assign`` / ``Return`` / bare ``Expr`` statement is
a ``Call`` whose:

  * ``func`` is ``Attribute(attr=Name("getOrCreate"))`` whose receiver name
    ends in ``SparkContext`` (the ``SparkContext.getOrCreate(...)`` classmethod),
    OR
  * ``func`` name ends in ``SparkContext`` (the ``SparkContext(...)``
    constructor), OR
  * ``func`` name ends in ``SparkSession`` (the direct ``SparkSession(...)``
    constructor).

Negative cases (must NOT trigger)
---------------------------------

  * ``SparkSession.builder...getOrCreate()`` — owned by
    ``spark_builder_drop_master_init_session_rewrite``; ``func.attr`` is
    ``getOrCreate`` and its receiver does not end in ``SparkContext``, so it is
    skipped here.
  * ``SparkSession.getActiveSession()`` — ``func.attr`` is
    ``getActiveSession``, not a constructor; skipped.
  * ``snowpark_connect.init_spark_session()`` already present — idempotent
    no-op (does not match any trigger).
  * ``sc.parallelize(...)`` / ``spark.sparkContext.applicationId`` — property /
    method access on an established binding, owned by
    ``sparkcontext_property_fallback_rewrite``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "sparkcontext_getorcreate_init_session_rewrite"
MIN_SCOS_VERSION = "0.4.0"

# The replacement expression: snowpark_connect.init_spark_session()
_REPLACEMENT_EXPR = cst.Call(
    func=cst.Attribute(
        value=cst.Name("snowpark_connect"),
        attr=cst.Name("init_spark_session"),
    ),
    args=[],
)
# The import we insert at top-of-file when any rewrite happens.
# Renders as ``from snowflake import snowpark_connect``.
_IMPORT_NODE = cst.SimpleStatementLine(
    body=[
        cst.ImportFrom(
            module=cst.Name("snowflake"),
            names=[cst.ImportAlias(name=cst.Name("snowpark_connect"))],
            relative=[],
        )
    ]
)

_COMMENT_ACQUIRE = (
    f"# SCOS: [SPRKCNTPY1001-Fixed] {RECIPE_ID}: SparkContext is not available in "
    f"Spark Connect; replaced the SparkContext acquisition with "
    f"snowpark_connect.init_spark_session()."
)
_COMMENT_CTOR = (
    f"# SCOS: [SPRKCNTPY1001-Fixed] {RECIPE_ID}: direct SparkSession(...) construction "
    f"is not supported in Spark Connect; replaced with "
    f"snowpark_connect.init_spark_session()."
)
_WARN_DROPPED_ARGS = (
    f"# SCOS-WARN: [SPRKCNTPY4000-Warning] {RECIPE_ID}: dropped SparkContext/SparkConf constructor "
    f"argument(s); re-apply any required settings via spark.conf.set(...)."
)


# ---------------------------------------------------------------------------
# Chain inspection
# ---------------------------------------------------------------------------


def _name_ends_with(expr: cst.BaseExpression, target: str) -> bool:
    """True iff ``expr`` is ``Name(target)`` or an ``Attribute`` whose
    terminal attr is ``target`` (e.g. ``pyspark.SparkContext``)."""
    if isinstance(expr, cst.Name):
        return expr.value == target
    if isinstance(expr, cst.Attribute) and isinstance(expr.attr, cst.Name):
        return expr.attr.value == target
    return False


def _classify(val: cst.BaseExpression) -> tuple[Optional[str], bool]:
    """Return ``(kind, had_args)`` where ``kind`` is one of:

      * ``"acquire"`` — a SparkContext acquisition (classmethod or ctor),
      * ``"ctor"``    — a direct ``SparkSession(...)`` construction,
      * ``None``      — not a triggering expression.

    ``had_args`` is True iff the matched call carried any argument (used to
    surface a visible SCOS-WARN when a ``SparkConf`` may have been dropped).
    """
    if not isinstance(val, cst.Call):
        return None, False
    func = val.func
    # SparkContext.getOrCreate(...) -- classmethod on the SparkContext name.
    if (
        isinstance(func, cst.Attribute)
        and isinstance(func.attr, cst.Name)
        and func.attr.value == "getOrCreate"
        and _name_ends_with(func.value, "SparkContext")
    ):
        return "acquire", bool(val.args)
    # SparkContext(...) -- direct constructor (incl. pyspark.SparkContext(...)).
    if _name_ends_with(func, "SparkContext"):
        return "acquire", bool(val.args)
    # SparkSession(...) -- direct constructor (NOT .builder / .getActiveSession,
    # whose terminal attr differs from "SparkSession").
    if _name_ends_with(func, "SparkSession"):
        return "ctor", bool(val.args)
    return None, False


def _statement_value(small: cst.BaseSmallStatement) -> Optional[cst.BaseExpression]:
    """Return the ``value`` expression of an Assign / Return / Expr, else None."""
    if isinstance(small, (cst.Assign, cst.AnnAssign, cst.Return, cst.Expr)):
        return small.value
    return None


# ---------------------------------------------------------------------------
# Recipe
# ---------------------------------------------------------------------------


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, **kw):
        super().__init__(**kw)
        self.rewrites_made = 0

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        if len(updated_node.body) != 1:
            return updated_node
        small = updated_node.body[0]
        val = _statement_value(small)
        if val is None:
            return updated_node
        kind, had_args = _classify(val)
        if kind is None:
            return updated_node

        line = self._line_of(original_node)
        # Idempotency: if a previous run already annotated this line, leave it.
        if _annotate.comment_above_contains(self._lines, line, RECIPE_ID):
            return updated_node

        new_small = small.with_changes(value=_REPLACEMENT_EXPR)
        node = updated_node.with_changes(body=[new_small])

        self._record(line, f"{kind} -> snowpark_connect.init_spark_session()")
        self.rewrites_made += 1

        marker = _COMMENT_ACQUIRE if kind == "acquire" else _COMMENT_CTOR
        node = _annotate.prepend_comment(node, marker)
        # A dropped SparkConf is a silent behaviour change for the acquisition
        # form -- surface it. The ``ctor`` arg is just the context being
        # wrapped (intentionally dropped), so no warning there.
        if kind == "acquire" and had_args:
            node = _annotate.prepend_comment(node, _WARN_DROPPED_ARGS)
        return node


# ---------------------------------------------------------------------------
# Import injection
# ---------------------------------------------------------------------------


def _has_snowpark_connect_import(module: cst.Module) -> bool:
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for s in stmt.body:
            if isinstance(s, cst.ImportFrom):
                if (
                    isinstance(s.module, cst.Name) and s.module.value == "snowflake"
                ) or (
                    isinstance(s.module, cst.Attribute)
                    and isinstance(s.module.value, cst.Name)
                    and s.module.value.value == "snowflake"
                ):
                    for n in s.names:
                        if isinstance(n, cst.ImportAlias):
                            asname = n.asname.name.value if n.asname else None
                            name = (
                                n.name.value
                                if isinstance(n.name, cst.Name)
                                else None
                            )
                            if name == "snowpark_connect" and asname is None:
                                return True
                            if asname == "snowpark_connect":
                                return True
            if isinstance(s, cst.Import):
                for n in s.names:
                    asname = n.asname.name.value if n.asname else None
                    if asname == "snowpark_connect":
                        return True
    return False


def _ensure_import(module: cst.Module) -> cst.Module:
    if _has_snowpark_connect_import(module):
        return module
    body = list(module.body)
    insert_at = 0
    for i, stmt in enumerate(body):
        if isinstance(stmt, cst.SimpleStatementLine) and any(
            isinstance(s, (cst.Import, cst.ImportFrom)) for s in stmt.body
        ):
            insert_at = i + 1
        elif insert_at > 0:
            break
    new_body = body[:insert_at] + [_IMPORT_NODE] + body[insert_at:]
    return module.with_changes(body=tuple(new_body))


def apply(
    source: str, *, file: str = "<input.py>", facts_db: str | None = None
) -> _common.RecipeResult:
    module = cst.parse_module(source)
    wrapper = cst.MetadataWrapper(module, unsafe_skip_copy=True)
    recipe = _Recipe(source=source, file=file, facts_db=facts_db)
    new_module = wrapper.visit(recipe)
    if recipe.rewrites_made > 0:
        new_module = _ensure_import(new_module)
    return _common.RecipeResult(source=new_module.code, edits=list(recipe.edits))
