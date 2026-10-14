"""Deterministic structural rewrite for the
``SparkSession.builder...master(...)...config(...)...getOrCreate()`` pattern.

What it does
------------

Any chain ending in ``.getOrCreate()`` whose chain *also* contains a
call to ``.master(...)`` OR ``.config(...)`` is replaced with
``snowpark_connect.init_spark_session()``, and a top-of-file
``import snowflake.snowpark_connect as snowpark_connect`` is inserted.

**Configs are NOT silently dropped** — every extractable
``.config(key, value)`` call is preserved as a follow-up
``<spark>.conf.set(key, value)`` statement.  Non-extractable forms
(``.config(map={...})``, ``.config(conf=SparkConf())``) get a
``# SCOS-WARN: dropped non-extractable .config(...)`` comment so the
silent-drop is visible to the reviewer.

Three statement shapes are supported:

* **Assignment**::

      spark = SparkSession.builder.appName("x").master("local[*]").config("a","b").getOrCreate()

  becomes::

      spark = snowpark_connect.init_spark_session()
      spark.conf.set("a", "b")

* **Return inside a function** (refactored to assign+conf.set+return)::

      def build_spark():
          return (
              SparkSession.builder.master("local[*]")
              .config("spark.sql.session.timeZone", "UTC")
              .getOrCreate()
          )

  becomes::

      def build_spark():
          spark = snowpark_connect.init_spark_session()
          spark.conf.set("spark.sql.session.timeZone", "UTC")
          return spark

* **Bare expression** (rare; can't preserve configs, emits a warning)::

      SparkSession.builder.master("local[*]").config("a","b").getOrCreate()

  becomes::

      snowpark_connect.init_spark_session()  # SCOS-WARN: dropped chained .config(...)

Empirical motivation
--------------------

Diagnosed from
``stackoverflow__02_rdd_map_to_dataframe`` (run 20260427T203430Z): the
agent slapped ``# SCOS-DIFF: D2: ...Builder.getOrCreate`` on the chain
instead of rewriting it.  The earlier version of this recipe rewrote
the call but **silently dropped** every ``.config(...)`` argument — the
exact bug ``stackoverflow__06_window_rangebetween`` exhibited at
runtime (``spark.sql.session.timeZone=UTC`` was lost, producing an
8-hour shift in the gold trace).  This version preserves the configs.

Trigger
-------

A ``Call`` whose ``func`` is ``Attribute(attr=Name("getOrCreate"))``
AND whose call chain (walking ``.func.value`` recursively through
``Call``/``Attribute`` nodes) either

  * contains a ``.master(...)`` call or a ``.config(...)`` call (any
    chain root), OR
  * is a *bare* chain rooted at the ``SparkSession`` name with no
    ``.master(...)``/``.config(...)`` — e.g.
    ``SparkSession.builder.appName("x").getOrCreate()``. This degenerate
    case has no config to preserve, so it is replaced with a plain
    ``snowpark_connect.init_spark_session()``. Doing it here (Phase 0.5)
    rather than leaving it to Phase 3 means the analyzer never sees a
    live ``SparkSession.builder`` to flag.

Negative cases (must NOT trigger)
---------------------------------

  * ``snowpark_connect.init_spark_session()`` already present —
    idempotent no-op.
  * Any ``.getOrCreate()`` call that is not rooted at ``SparkSession``
    (e.g. a custom builder ``b.getOrCreate()`` stored in a variable) and
    that has no ``.master(...)``/``.config(...)`` — left alone.
  * ``hadoopConfiguration().setMaster(...)`` — different API.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "spark_builder_drop_master_init_session_rewrite"
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
_DEFAULT_VAR_NAME = "spark"


# ---------------------------------------------------------------------------
# Chain inspection
# ---------------------------------------------------------------------------


def _is_get_or_create(call: cst.Call) -> bool:
    return (
        isinstance(call.func, cst.Attribute)
        and isinstance(call.func.attr, cst.Name)
        and call.func.attr.value == "getOrCreate"
    )


def _walk_chain(call: cst.Call):
    """Yield every Call in the chain rooted at ``call`` (outermost first)."""
    node: cst.CSTNode | None = call
    seen = 0
    while node is not None and seen < 100:
        seen += 1
        if isinstance(node, cst.Call):
            yield node
            if isinstance(node.func, cst.Attribute):
                node = node.func.value
                continue
            return
        if isinstance(node, cst.Attribute):
            node = node.value
            continue
        return


def _chain_attr_calls(call: cst.Call, attr_name: str) -> list[cst.Call]:
    """Return every ``.<attr_name>(...)`` Call in the chain (outermost first)."""
    out = []
    for c in _walk_chain(call):
        if (
            isinstance(c.func, cst.Attribute)
            and isinstance(c.func.attr, cst.Name)
            and c.func.attr.value == attr_name
        ):
            out.append(c)
    return out


def _chain_calls_master(call: cst.Call) -> bool:
    return bool(_chain_attr_calls(call, "master"))


def _chain_calls_config(call: cst.Call) -> bool:
    return bool(_chain_attr_calls(call, "config"))


# ---------------------------------------------------------------------------
# Config extraction
# ---------------------------------------------------------------------------


# Sentinel: ".config(map=...)" / ".config(conf=...)" — can't extract.
_NON_EXTRACTABLE = object()


def _extract_one_config(
    call: cst.Call,
) -> Optional[tuple[cst.BaseExpression, cst.BaseExpression]] | object:
    """Pull (key_expr, value_expr) from a single ``.config(...)`` call.

    Returns:
      * (key, value) tuple when extractable;
      * ``_NON_EXTRACTABLE`` when the form is e.g. ``.config(map=...)``
        or ``.config(conf=SparkConf())`` -- the caller should emit a
        SCOS-WARN comment so the silent-drop is visible.
      * ``None`` when the .config(...) has no args at all (PySpark
        accepts this and treats it as a no-op; we just drop it).
    """
    args = list(call.args)
    pos = [a for a in args if a.keyword is None]
    kw = {a.keyword.value: a.value for a in args if a.keyword is not None}

    # Non-extractable forms: caller passed a dict or a SparkConf.
    if "map" in kw or "conf" in kw:
        return _NON_EXTRACTABLE
    # Single positional dict: .config({"k": "v", ...})
    if len(pos) == 1 and isinstance(pos[0].value, cst.Dict):
        return _NON_EXTRACTABLE

    # Two positional: .config("k", "v")
    if len(pos) == 2:
        return (pos[0].value, pos[1].value)
    # Mixed: .config("k", value="v")
    if len(pos) == 1 and "value" in kw:
        return (pos[0].value, kw["value"])
    # Pure kwargs: .config(key="k", value="v")
    if "key" in kw and "value" in kw:
        return (kw["key"], kw["value"])
    # No args at all -> noop, drop silently.
    if not args:
        return None
    return _NON_EXTRACTABLE


def _extract_chain_configs(call: cst.Call):
    """Return list of ``(key_expr, value_expr) | _NON_EXTRACTABLE`` in
    source-order (i.e. outermost ``.config(...)`` last). The chain walk
    sees outermost first; we reverse to restore source order.
    """
    raw = []
    for c in _chain_attr_calls(call, "config"):
        extracted = _extract_one_config(c)
        if extracted is None:
            continue
        raw.append(extracted)
    raw.reverse()
    return raw


# ---------------------------------------------------------------------------
# Statement builders
# ---------------------------------------------------------------------------


def _conf_set_stmt(
    var_name: str, key: cst.BaseExpression, value: cst.BaseExpression
) -> cst.SimpleStatementLine:
    """Build ``<var>.conf.set(key, value)`` as a statement line."""
    call = cst.Call(
        func=cst.Attribute(
            value=cst.Attribute(
                value=cst.Name(var_name),
                attr=cst.Name("conf"),
            ),
            attr=cst.Name("set"),
        ),
        args=[cst.Arg(value=key), cst.Arg(value=value)],
    )
    return cst.SimpleStatementLine(body=[cst.Expr(value=call)])


def _conf_set_stmts_for_chain(
    var_name: str, call: cst.Call
) -> tuple[list[cst.SimpleStatementLine], int]:
    """Return ([statements], n_dropped_warnings).

    For each extractable config -> 1 ``var.conf.set(k, v)`` line.
    For each NON_EXTRACTABLE config -> 1 ``# SCOS-WARN`` comment line.
    """
    out: list[cst.SimpleStatementLine] = []
    dropped = 0
    for entry in _extract_chain_configs(call):
        if entry is _NON_EXTRACTABLE:
            dropped += 1
            warn = cst.SimpleStatementLine(
                body=[cst.Pass()],
                trailing_whitespace=cst.TrailingWhitespace(
                    whitespace=cst.SimpleWhitespace("  "),
                    comment=cst.Comment(
                        f"# SCOS-WARN: [SPRKCNTPY3500-Warning] {RECIPE_ID}: dropped non-extractable .config(...) call"
                    ),
                    newline=cst.Newline(),
                ),
            )
            out.append(warn)
            continue
        key_expr, value_expr = entry  # type: ignore[misc]
        out.append(_conf_set_stmt(var_name, key_expr, value_expr))
    return out, dropped


# ---------------------------------------------------------------------------
# Top-level recipe
# ---------------------------------------------------------------------------


def _is_triggering_chain(val: cst.BaseExpression) -> bool:
    if not isinstance(val, cst.Call):
        return False
    if not _is_get_or_create(val):
        return False
    # Existing behaviour: any builder chain that drops a ``.master(...)`` or
    # preserves a ``.config(...)`` — fires regardless of the chain root.
    if _chain_calls_master(val) or _chain_calls_config(val):
        return True
    # Extension: a *bare* ``SparkSession.builder...getOrCreate()`` with no
    # ``.master(...)`` and no ``.config(...)`` (e.g.
    # ``SparkSession.builder.appName("x").getOrCreate()``). Gate strictly on a
    # ``SparkSession`` chain root so an unrelated custom builder's
    # ``.getOrCreate()`` is never rewritten.
    return _chain_roots_at_sparksession(val)


def _chain_roots_at_sparksession(call: cst.Call) -> bool:
    """True if the chain's base name is ``SparkSession`` — i.e. a
    ``SparkSession.builder...getOrCreate()`` chain, as opposed to a custom
    builder stored in a variable (``b.getOrCreate()``)."""
    node: cst.CSTNode | None = call
    seen = 0
    while node is not None and seen < 200:
        seen += 1
        if isinstance(node, cst.Call):
            node = node.func
        elif isinstance(node, cst.Attribute):
            node = node.value
        elif isinstance(node, cst.Name):
            return node.value == "SparkSession"
        else:
            return False
    return False


def _is_already_init(val: cst.BaseExpression) -> bool:
    """``snowpark_connect.init_spark_session()`` -> idempotent no-op."""
    return (
        isinstance(val, cst.Call)
        and isinstance(val.func, cst.Attribute)
        and isinstance(val.func.value, cst.Name)
        and val.func.value.value == "snowpark_connect"
        and isinstance(val.func.attr, cst.Name)
        and val.func.attr.value == "init_spark_session"
    )


def _record_marker(rec: "_Recipe", original: cst.CSTNode, kind: str) -> None:
    line = rec._line_of(original)
    rec._record(
        line,
        f"SparkSession.builder...getOrCreate() rewrite [{kind}]",
    )
    rec.rewrites_made += 1


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, **kw):
        super().__init__(**kw)
        self.rewrites_made = 0

    # ---- Statement-level dispatch ----------------------------------------

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        if len(updated_node.body) != 1:
            return updated_node
        small = updated_node.body[0]
        # Pair the SmallStatement up with its original counterpart so
        # ``self._line_of`` works (PositionProvider is keyed on original
        # nodes).
        original_small = original_node.body[0]
        if isinstance(small, cst.Assign) and isinstance(
            original_small, cst.Assign
        ):
            return self._handle_assign(
                original_node, updated_node, original_small, small
            )
        if isinstance(small, cst.Return) and isinstance(
            original_small, cst.Return
        ):
            return self._handle_return(
                original_node, updated_node, original_small, small
            )
        if isinstance(small, cst.Expr) and isinstance(original_small, cst.Expr):
            return self._handle_bare_expr(
                original_node, updated_node, original_small, small
            )
        return updated_node

    # ---- Assign: ``<x> = <chain>`` ---------------------------------------

    def _handle_assign(
        self,
        original_line: cst.SimpleStatementLine,
        updated_line: cst.SimpleStatementLine,
        original_assign: cst.Assign,
        updated_assign: cst.Assign,
    ):
        val = updated_assign.value
        if _is_already_init(val):
            return updated_line
        if not _is_triggering_chain(val):
            return updated_line
        original_val = original_assign.value
        assert isinstance(original_val, cst.Call)
        # Pick the LHS variable name -- only handle simple ``Name`` targets.
        # Tuple/star/attr targets fall through to scalar replacement.
        var_name: Optional[str] = None
        if (
            len(updated_assign.targets) == 1
            and isinstance(updated_assign.targets[0].target, cst.Name)
        ):
            var_name = updated_assign.targets[0].target.value
        _record_marker(self, original_line, "assign")
        new_assign = updated_assign.with_changes(value=_REPLACEMENT_EXPR)
        new_line = updated_line.with_changes(body=[new_assign])
        if var_name is None:
            # Can't preserve configs without a name; emit a single warning
            # statement after if there were any configs.
            if _chain_calls_config(original_val):
                warn = cst.SimpleStatementLine(
                    body=[cst.Pass()],
                    trailing_whitespace=cst.TrailingWhitespace(
                        whitespace=cst.SimpleWhitespace("  "),
                        comment=cst.Comment(
                            f"# SCOS-WARN: [SPRKCNTPY3500-Warning] {RECIPE_ID}: dropped chained .config(...) "
                            f"because LHS is a non-Name target"
                        ),
                        newline=cst.Newline(),
                    ),
                )
                return cst.FlattenSentinel([new_line, warn])
            return new_line
        conf_stmts, _ = _conf_set_stmts_for_chain(var_name, original_val)
        if not conf_stmts:
            return new_line
        return cst.FlattenSentinel([new_line, *conf_stmts])

    # ---- Return inside a function ---------------------------------------

    def _handle_return(
        self,
        original_line: cst.SimpleStatementLine,
        updated_line: cst.SimpleStatementLine,
        original_return: cst.Return,
        updated_return: cst.Return,
    ):
        val = updated_return.value
        if val is None:
            return updated_line
        if _is_already_init(val):
            return updated_line
        if not _is_triggering_chain(val):
            return updated_line
        original_val = original_return.value
        assert isinstance(original_val, cst.Call)
        _record_marker(self, original_line, "return")
        configs = _extract_chain_configs(original_val)
        if not configs:
            # No configs -> just swap value, keep the return shape.
            new_ret = updated_return.with_changes(value=_REPLACEMENT_EXPR)
            return updated_line.with_changes(body=[new_ret])
        # Refactor: assign-conf.set-return so we have a name to bind
        # ``conf.set`` to.  Variable name is the conventional ``spark``
        # (function scope makes collisions extremely rare in practice; if
        # it happens, the resulting code is still syntactically valid --
        # the prior binding is just shadowed).
        var_name = _DEFAULT_VAR_NAME
        init_assign = cst.SimpleStatementLine(
            body=[
                cst.Assign(
                    targets=[cst.AssignTarget(target=cst.Name(var_name))],
                    value=_REPLACEMENT_EXPR,
                )
            ]
        )
        conf_stmts, _ = _conf_set_stmts_for_chain(var_name, original_val)
        new_ret_stmt = updated_line.with_changes(
            body=[cst.Return(value=cst.Name(var_name))]
        )
        return cst.FlattenSentinel([init_assign, *conf_stmts, new_ret_stmt])

    # ---- Bare expression statement --------------------------------------

    def _handle_bare_expr(
        self,
        original_line: cst.SimpleStatementLine,
        updated_line: cst.SimpleStatementLine,
        original_expr: cst.Expr,
        updated_expr: cst.Expr,
    ):
        val = updated_expr.value
        if _is_already_init(val):
            return updated_line
        if not _is_triggering_chain(val):
            return updated_line
        original_val = original_expr.value
        assert isinstance(original_val, cst.Call)
        _record_marker(self, original_line, "bare_expr")
        new_expr = updated_expr.with_changes(value=_REPLACEMENT_EXPR)
        if _chain_calls_config(original_val):
            new_line = updated_line.with_changes(
                body=[new_expr],
                trailing_whitespace=cst.TrailingWhitespace(
                    whitespace=cst.SimpleWhitespace("  "),
                    comment=cst.Comment(
                        f"# SCOS-WARN: [SPRKCNTPY3500-Warning] {RECIPE_ID}: dropped chained .config(...) "
                        f"calls (bare expression has no LHS to bind to)"
                    ),
                    newline=updated_line.trailing_whitespace.newline,
                ),
            )
            return new_line
        return updated_line.with_changes(body=[new_expr])


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
    return _common.RecipeResult(
        source=new_module.code, edits=list(recipe.edits)
    )
