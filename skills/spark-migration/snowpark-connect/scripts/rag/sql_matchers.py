# flake8: noqa
"""Named matchers and transforms referenced by the SQL rule catalog
(``data/sql_rules.json``) and run by :mod:`rag.sql_engine`.

This is the **escape-hatch** half of the hybrid catalog design: the genuinely
shape-dependent rules (NOT IN arity, LCA scope, multi-generator, EXPLAIN
classification, window-frame families) keep bespoke Python logic here, while
their metadata (id, severity, note, fixer_action, transform name) lives as data
in the catalog. Simple rules are expressed declaratively in the catalog's
``when`` predicates and need no entry here.

Two registries:

* ``MATCHERS[name](node, base_line) -> list[dict]`` — a *matcher* inspects one
  candidate node (the catalog ``find`` type) and returns zero or more override
  dicts. Each returned dict becomes one finding, merged over the catalog entry's
  defaults; recognised keys: ``line``, ``snippet``, ``severity``, ``note``,
  ``jira``. An empty list means "does not fire".
* ``TRANSFORMS[name](stmt, base_line) -> (stmt_or_None, list[SqlEdit])`` — a
  *transform* rewrites a whole statement in place (or returns a replacement
  node, or ``None`` to delete the statement) and reports the edits it made.
  These are statement-level (they ``find_all`` internally), matching the
  rewrite engine's per-statement application model.
"""
from __future__ import annotations

from rag.sql_ast import (
    _EXPLAIN_DDL_LEADERS,
    _EXPLAIN_MODE_LEADERS,
    _GENERATOR_NODE_NAMES,
    _ORDER_SENSITIVE_WINDOW,
    _SUPPORTED_LATERAL_GENERATORS,
    _node_line,
)
from rag.sql_rewrite_transforms import (  # transform bodies (kept separate to avoid cycles)
    SqlEdit,
    rw_cache,
    rw_explain,
    rw_grouping_sets,
    rw_listagg_within_group,
    rw_multicolumn_not_in,
    rw_qualify,
    rw_update_from,
    rw_window_order_by,
)


def _fn_name(node) -> str:
    """Canonical upper-case function name. sqlglot represents known functions as
    typed nodes (use ``sql_name()``) and unknown ones as ``exp.Anonymous`` whose
    name lives in ``.this`` — handle both so ``func_name_in`` is reliable."""
    if node is None:
        return ""
    from sqlglot import exp
    if isinstance(node, exp.Anonymous):
        return (str(node.this) or "").upper()
    if hasattr(node, "sql_name"):
        try:
            return (node.sql_name() or "").upper()
        except Exception:
            return ""
    return ""


# --------------------------------------------------------------------------- #
# Matchers (detection). Each returns a list of per-finding override dicts.
# --------------------------------------------------------------------------- #

def m_window_without_order_by(node, base_line):
    """exp.Window with an order-sensitive function and no ORDER BY."""
    name = _fn_name(node.this)
    if name not in _ORDER_SENSITIVE_WINDOW:
        return []
    if node.args.get("order"):
        return []
    return [{
        "line": _node_line(node, base_line),
        "snippet": node.sql(dialect="spark")[:200],
        "note": (
            f"`{name}` is used in an OVER(...) window with no ORDER BY. "
            "Spark raises AnalysisException ('Window function requires "
            "window to be ordered'); SCOS/Snowflake permits it and "
            "returns a nondeterministic result instead of erroring. Add "
            "an explicit ORDER BY to the window to get deterministic, "
            "portable behavior."
        ),
    }]


def m_in_subquery_in_on_clause(node, base_line):
    """exp.Join (LEFT) whose ON clause holds an IN (SELECT ...)."""
    from sqlglot import exp
    if (node.side or "").upper() != "LEFT":
        return []
    on = node.args.get("on")
    if on is None:
        return []
    out = []
    for in_expr in on.find_all(exp.In):
        if in_expr.args.get("query") is None:
            continue
        out.append({
            "line": _node_line(in_expr, base_line),
            "snippet": in_expr.sql(dialect="spark")[:200],
        })
    return out


def m_lca_alias_collision(node, base_line):
    """exp.Select whose output alias shadows a top-level GROUP BY column."""
    from sqlglot import exp
    group = node.args.get("group")
    if group is None:
        return []
    aliases = {a.alias.lower() for a in node.expressions
               if isinstance(a, exp.Alias) and a.alias}
    if not aliases:
        return []
    projections = list(node.expressions)
    gb_cols: set[str] = set()
    for e in (group.args.get("expressions") or []):
        # Plain / qualified / quoted column — exp.Column.name already strips the
        # table qualifier and quoting (``t.a`` and ```a``` both yield "a").
        if isinstance(e, exp.Column) and e.name:
            gb_cols.add(e.name.lower())
        # Positional ordinal (``GROUP BY 1``) — resolve to the underlying column
        # name of that projection (pre-alias), which is what the grain groups on.
        elif isinstance(e, exp.Literal) and e.is_int:
            idx = int(e.name) - 1
            if 0 <= idx < len(projections):
                base = projections[idx]
                base = base.this if isinstance(base, exp.Alias) else base
                if isinstance(base, exp.Column) and base.name:
                    gb_cols.add(base.name.lower())
    collide = sorted(aliases & gb_cols)
    if not collide:
        return []
    return [{
        "line": _node_line(group, base_line),
        "snippet": f"GROUP BY key collides with SELECT alias: {collide}",
    }]


def m_multicolumn_not_in(node, base_line):
    """exp.In that is a multi-column (tuple LHS) NOT IN."""
    from sqlglot import exp
    if not isinstance(node.parent, exp.Not):
        return []
    if not isinstance(node.this, exp.Tuple):
        return []
    is_subquery = node.args.get("query") is not None
    if is_subquery:
        severity = "medium"
        note = (
            "Multi-column `NOT IN (SELECT ...)` (a tuple LHS, e.g. "
            "`(a, b) NOT IN (SELECT a, b FROM t)`) evaluates NULL-aware "
            "anti-join semantics differently in SCOS than Spark: when "
            "NULLs are present in the probe columns or the subquery, "
            "SCOS can return a different row set than Spark's "
            "three-valued tuple NOT IN. Rewrite as a correlated NOT "
            "EXISTS or an anti-join to make the NULL handling explicit."
        )
    else:
        severity = "high"
        note = (
            "Multi-column `NOT IN` against a literal tuple list (e.g. "
            "`(a, b) NOT IN ((2, 3.0))`) is handled differently in SCOS "
            "than Spark: it can raise a parameter/type mismatch or "
            "return a different row set, whereas Spark applies "
            "three-valued NOT IN logic across the column tuple "
            "(including NULL handling). Rewrite as explicit OR'd "
            "predicates or a NOT EXISTS anti-join."
        )
    return [{
        "line": _node_line(node, base_line),
        "snippet": node.parent.sql(dialect="spark")[:200],
        "severity": severity,
        "note": note,
    }]


def m_insert_overwrite_partition(node, base_line):
    from sqlglot import exp
    if node.args.get("overwrite") is not True:
        return []
    if node.find(exp.Partition) is None:
        return []
    return [{"line": _node_line(node, base_line), "snippet": node.sql(dialect="spark")[:200]}]


def m_grouping_sets_with_groupby(node, base_line):
    """exp.Group: GROUPING SETS with non-empty GROUP BY, or multi-expr ROLLUP/CUBE."""
    gsets = node.args.get("grouping_sets") or []
    plain = node.args.get("expressions") or []
    rollup = node.args.get("rollup") or []
    cube = node.args.get("cube") or []
    multi_expr = sum(len(x.expressions) if hasattr(x, "expressions") else 1
                     for x in (*rollup, *cube))
    gs_with_gb = bool(gsets) and len(plain) > 0
    if not gs_with_gb and multi_expr <= 1:
        return []
    return [{"line": _node_line(node, base_line), "snippet": node.sql(dialect="spark")[:200]}]


def m_lateral_view_unsupported_generator(node, base_line):
    if node.args.get("view") is not True:
        return []
    name = _fn_name(node.this)
    if name in _SUPPORTED_LATERAL_GENERATORS:
        return []
    return [{
        "line": _node_line(node, base_line),
        "snippet": node.sql(dialect="spark")[:200],
        "note": (
            "SCOS supports only FLATTEN and SPLIT_TO_TABLE as LATERAL "
            f"VIEW generators; `{name or 'this generator'}` is not "
            "supported (and generator output columns must be qualified). "
            "Rewrite using DataFrame .explode()/.posexplode() for "
            "arbitrary array shapes, or a supported table function."
        ),
    }]


def m_multi_generator_select(node, base_line):
    from sqlglot import exp
    gen_exprs = [
        proj for proj in node.expressions
        if any(_fn_name(g) in _GENERATOR_NODE_NAMES
               for g in proj.find_all(exp.Func))
    ]
    if len(gen_exprs) <= 1:
        return []
    return [{"line": _node_line(node, base_line), "snippet": node.sql(dialect="spark")[:200]}]


def _explain_leader(node):
    if (str(node.this) or "").upper() != "EXPLAIN":
        return None
    payload = node.expression.this if node.expression else ""
    tokens = str(payload).strip().split()
    return tokens[0].upper() if tokens else ""


def m_explain_ddl(node, base_line):
    if _explain_leader(node) in _EXPLAIN_DDL_LEADERS:
        return [{"line": _node_line(node, base_line), "snippet": node.sql(dialect="spark")[:200]}]
    return []


def m_explain_mode(node, base_line):
    leader = _explain_leader(node)
    if leader in _EXPLAIN_MODE_LEADERS:
        return [{
            "line": _node_line(node, base_line),
            "snippet": node.sql(dialect="spark")[:200],
            "note": (
                f"SCOS emits a plain Snowflake EXPLAIN for every mode, so "
                f"`EXPLAIN {leader}` silently ignores the {leader} mode — "
                "the output is Snowflake's text plan, not Spark's "
                f"{leader.lower()} plan. Don't rely on mode-specific output."
            ),
        }]
    return []


# Window functions SCOS does not support with an explicit ROWS/RANGE frame
# (the window-frame family — gaps 5.7–5.13, 5.18, 4.11 in the gaps report).
_FRAME_SENSITIVE_WINDOW = frozenset({
    "NTH_VALUE", "LAST_VALUE", "FIRST_VALUE", "ANY_VALUE", "APPROX_COUNT_DISTINCT",
    "PERCENTILE_DISC", "PERCENTILE_CONT", "EVERY", "SOME", "BOOL_AND", "BOOL_OR",
    "BIT_XOR", "LEAD", "LAG",
})


def m_unsupported_window_frame(node, base_line):
    """exp.Window: a frame-sensitive function used with an explicit ROWS/RANGE
    frame (which SCOS does not support for these functions)."""
    name = _fn_name(node.this)
    if name not in _FRAME_SENSITIVE_WINDOW:
        return []
    spec = node.args.get("spec")
    if spec is None:
        return []  # no explicit frame → not this gap
    return [{
        "line": _node_line(node, base_line),
        "snippet": node.sql(dialect="spark")[:200],
        "note": (
            f"`{name}` is used with an explicit ROWS/RANGE window frame. SCOS "
            "does not support an explicit cumulative/sliding frame for this "
            "function (and INTERVAL/RANGE frame bounds): it raises or silently "
            "ignores the frame. Remove the frame (use a plain OVER(PARTITION BY "
            "... ORDER BY ...)) or compute the value with a supported function."
        ),
    }]


def m_qualify(node, base_line):
    """exp.Qualify — a QUALIFY clause, rejected by the Spark SQL parser."""
    return [{
        "line": _node_line(node, base_line),
        "snippet": node.sql(dialect="spark")[:200],
        "note": ("QUALIFY is not in the Spark SQL grammar used by spark.sql(); "
                 "rewrite it as a ROW_NUMBER()-style subquery with an outer WHERE."),
    }]


def m_listagg_within_group(node, base_line):
    """exp.WithinGroup wrapping a GroupConcat — ``LISTAGG ... WITHIN GROUP``."""
    from sqlglot import exp
    if not isinstance(node.this, exp.GroupConcat):
        return []
    return [{
        "line": _node_line(node, base_line),
        "snippet": node.sql(dialect="spark")[:200],
        "note": ("LISTAGG ... WITHIN GROUP is not supported by the Spark SQL "
                 "parser. When it orders (ascending) by the LISTAGG expression "
                 "itself it is rewritten to array_join(array_sort([array_distinct] "
                 "collect_list(CAST(x AS STRING))), sep); a different/DESC/multi-key "
                 "ORDER BY must reproduce that order explicitly before concatenating."),
    }]


def m_update_from(node, base_line):
    """exp.Update carrying a FROM clause — Snowflake ``UPDATE ... FROM`` join-update."""
    if node.args.get("from_") is None:
        return []
    return [{
        "line": _node_line(node, base_line),
        "snippet": node.sql(dialect="spark")[:200],
        "note": ("UPDATE ... SET ... FROM <source> is not in the Spark SQL grammar; "
                 "rewrite as MERGE INTO <target> USING <source> ON <WHERE> WHEN "
                 "MATCHED THEN UPDATE SET ...."),
    }]


def m_correlated_subquery_unsupported(node, base_line):
    """exp.Subquery used as a *scalar value* (not a FROM/JOIN derived table) that
    is correlated AND contains a set operation or GROUP BY.

    Snowflake rejects a correlated scalar subquery whose body has UNION/INTERSECT/
    EXCEPT (gaps report §5.2) or a GROUP BY (§5.4) as "unsupported subquery type"
    (error 002031). Candidate detector — the correlation check is a heuristic
    (a column qualified by a table/alias not defined inside the subquery), so the
    LLM fixer confirms before decorrelating."""
    from sqlglot import exp
    # Scalar position only — skip derived tables (FROM/JOIN/LATERAL) and the
    # outer wrapper of another subquery, which are not the failing shape.
    if isinstance(node.parent, (exp.From, exp.Join, exp.Lateral, exp.Subquery)):
        return []
    has_setop = next(node.find_all(exp.Union, exp.Intersect, exp.Except), None) is not None
    has_group = next(iter(node.find_all(exp.Group)), None) is not None
    if not (has_setop or has_group):
        return []
    # Correlation heuristic: a column qualified by a table/alias that is NOT
    # defined anywhere inside the subquery references the outer query.
    inner: set[str] = set()
    for t in node.find_all(exp.Table):
        if t.alias:
            inner.add(t.alias.lower())
        if t.name:
            inner.add(t.name.lower())
    for s in node.find_all(exp.Subquery):
        if s.alias:
            inner.add(s.alias.lower())
    correlated = any(
        c.table and c.table.lower() not in inner for c in node.find_all(exp.Column)
    )
    if not correlated:
        return []
    kind = "a set operation (UNION/INTERSECT/EXCEPT)" if has_setop else "a GROUP BY"
    return [{
        "line": _node_line(node, base_line),
        "snippet": node.sql(dialect="spark")[:200],
        "note": (f"Correlated scalar subquery whose body contains {kind}. Snowflake "
                 "rejects this as an unsupported subquery type (error 002031); Spark "
                 "evaluates it via its plan optimizer. Decorrelate it — rewrite as a "
                 "LEFT JOIN to a pre-aggregated / pre-unioned derived table keyed on "
                 "the correlation column."),
    }]


def m_identifier_dynamic(node, base_line):
    """exp.Anonymous ``IDENTIFIER(...)`` with a dynamic (non-literal) argument."""
    from sqlglot import exp
    if _fn_name(node) != "IDENTIFIER":
        return []
    args = node.expressions or []
    if not args:
        return []
    # The gap is the DYNAMIC form (a concatenation / expression) used to call a
    # table function or build an object name; a plain string literal may be fine.
    if isinstance(args[0], exp.Literal):
        return []
    return [{
        "line": _node_line(node, base_line),
        "snippet": node.sql(dialect="spark")[:200],
        "note": ("IDENTIFIER(...) with a dynamic (non-literal) argument. Snowflake's "
                 "IDENTIFIER() pseudo-function does not support dynamically calling a "
                 "table function, nor constructing object names in CREATE/DROP/ALTER "
                 "(gaps report §4.33–4.34). Resolve the name in Python and emit static "
                 "SQL, or use a stored procedure / EXECUTE IMMEDIATE."),
    }]


def m_map_unsupported_key(node, base_line):
    """exp.VarMap (``map(k1, v1, ...)``) with a key that is not VARCHAR/integer."""
    from sqlglot import exp
    keys = node.args.get("keys")
    key_exprs = keys.expressions if isinstance(keys, exp.Array) else []
    bad: list[str] = []
    for k in key_exprs:
        if isinstance(k, exp.Boolean):
            bad.append("boolean")
        elif isinstance(k, exp.Literal) and not k.is_string and not k.is_int:
            bad.append("float/decimal")
        elif isinstance(k, exp.Cast):
            tt = (k.to.sql(dialect="spark") if k.to else "").upper()
            if any(x in tt for x in ("TIMESTAMP", "DATE", "FLOAT", "DOUBLE", "DECIMAL", "BOOLEAN")):
                bad.append(tt.lower())
    if not bad:
        return []
    return [{
        "line": _node_line(node, base_line),
        "snippet": node.sql(dialect="spark")[:200],
        "note": (f"map() with an unsupported key type ({', '.join(sorted(set(bad)))}). "
                 "Snowflake MAP keys must be VARCHAR or NUMBER(p,0) (integer); Spark "
                 "allows any hashable key (gaps report §5.31). Cast the key to STRING "
                 "(or an integer code) — note this changes the key type, so verify "
                 "downstream map lookups."),
    }]


MATCHERS = {
    "window_without_order_by": m_window_without_order_by,
    "in_subquery_in_on_clause": m_in_subquery_in_on_clause,
    "lca_alias_collision": m_lca_alias_collision,
    "multicolumn_not_in": m_multicolumn_not_in,
    "insert_overwrite_partition": m_insert_overwrite_partition,
    "grouping_sets_with_groupby": m_grouping_sets_with_groupby,
    "lateral_view_unsupported_generator": m_lateral_view_unsupported_generator,
    "multi_generator_select": m_multi_generator_select,
    "explain_ddl": m_explain_ddl,
    "explain_mode": m_explain_mode,
    "unsupported_window_frame": m_unsupported_window_frame,
    "qualify": m_qualify,
    "listagg_within_group": m_listagg_within_group,
    "update_from": m_update_from,
    "correlated_subquery_unsupported": m_correlated_subquery_unsupported,
    "identifier_dynamic": m_identifier_dynamic,
    "map_unsupported_key": m_map_unsupported_key,
}

TRANSFORMS = {
    # Order matters: deleting/replacing transforms run before in-place mutators
    # (mirrors the original sql_rewrite._TRANSFORMS application order).
    "cache_delete": rw_cache,
    "explain_drop": rw_explain,
    "add_order_by_from_partition": rw_window_order_by,
    "multicolumn_not_in": rw_multicolumn_not_in,
    "grouping_sets_fold": rw_grouping_sets,
    "rw_qualify": rw_qualify,
    "rw_listagg_within_group": rw_listagg_within_group,
    # Whole-statement replacement (Update -> Merge): run last so nested fixes
    # (LISTAGG/QUALIFY/etc. in SET/WHERE) are applied before the node is copied.
    "rw_update_from": rw_update_from,
}
