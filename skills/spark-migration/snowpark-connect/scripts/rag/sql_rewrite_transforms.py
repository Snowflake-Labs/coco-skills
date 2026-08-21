# flake8: noqa
"""Statement-level SQL rewrite transforms, lifted verbatim from the original
``sql_rewrite.py`` so the catalog engine can reference them by name.

Each ``rw_*`` takes a parsed sqlglot statement and the statement's source line
and returns ``(stmt_or_replacement_or_None, list[SqlEdit])``. The returned
statement may be the same object (mutated in place), a new node, or ``None`` to
delete the statement. An empty edit list means "no change".
"""
from __future__ import annotations

from dataclasses import dataclass

from rag.sql_ast import (
    _EXPLAIN_DDL_LEADERS,
    _EXPLAIN_MODE_LEADERS,
    _ORDER_SENSITIVE_WINDOW,
    _node_line,
)


@dataclass
class SqlEdit:
    """One mechanical rewrite that was applied."""
    rule_id: str
    line: int
    before: str
    after: str
    note: str


def _snip(s: str, n: int = 200) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def rw_window_order_by(stmt, base_line):
    from sqlglot import exp
    edits = []
    for win in stmt.find_all(exp.Window):
        fn = win.this
        name = ""
        if fn is not None and hasattr(fn, "sql_name"):
            try:
                name = (fn.sql_name() or "").upper()
            except Exception:
                name = ""
        if name not in _ORDER_SENSITIVE_WINDOW:
            continue
        if win.args.get("order"):
            continue
        parts = win.args.get("partition_by") or []
        if not parts:
            continue  # no safe ordering key — leave for residual/LLM
        before = _snip(win.sql(dialect="spark"))
        order = exp.Order(expressions=[exp.Ordered(this=p.copy()) for p in parts])
        win.set("order", order)
        edits.append(SqlEdit(
            rule_id="detector:window_without_order_by",
            line=_node_line(win, base_line),
            before=before,
            after=_snip(win.sql(dialect="spark")),
            note=("added ORDER BY (from PARTITION BY keys) so the window is "
                  "deterministic and SCOS-portable"),
        ))
    return stmt, edits


def rw_explain(stmt, base_line):
    import sqlglot
    from sqlglot import exp
    if not isinstance(stmt, exp.Command):
        return stmt, []
    if (str(stmt.this) or "").upper() != "EXPLAIN":
        return stmt, []
    payload = ""
    if stmt.expression is not None:
        payload = str(stmt.expression.this or "")
    tokens = payload.strip().split()
    if not tokens:
        return stmt, []
    leader = tokens[0].upper()
    before = _snip(stmt.sql(dialect="spark"))
    if leader in _EXPLAIN_DDL_LEADERS:
        try:
            new_stmt = sqlglot.parse_one(payload, dialect="spark")
        except Exception:
            return stmt, []
        return new_stmt, [SqlEdit(
            rule_id="detector:explain_ddl_rejected",
            line=_node_line(stmt, base_line), before=before,
            after=_snip(new_stmt.sql(dialect="spark")),
            note=("dropped EXPLAIN over DDL — Snowflake EXPLAIN is DML-only; the "
                  "DDL now runs directly"),
        )]
    if leader in _EXPLAIN_MODE_LEADERS:
        rest = " ".join(tokens[1:])
        try:
            new_stmt = sqlglot.parse_one("EXPLAIN " + rest, dialect="spark")
        except Exception:
            return stmt, []
        return new_stmt, [SqlEdit(
            rule_id="detector:explain_mode_ignored",
            line=_node_line(stmt, base_line), before=before,
            after=_snip(new_stmt.sql(dialect="spark")),
            note=(f"dropped EXPLAIN mode '{leader}' — SCOS ignores it and emits a "
                  "plain EXPLAIN regardless"),
        )]
    return stmt, []


def rw_multicolumn_not_in(stmt, base_line):
    from sqlglot import exp
    edits = []
    for in_expr in list(stmt.find_all(exp.In)):
        if not isinstance(in_expr.parent, exp.Not):
            continue
        if not isinstance(in_expr.this, exp.Tuple):
            continue
        not_node = in_expr.parent
        lhs = list(in_expr.this.expressions)
        if not lhs:
            continue
        before = _snip(not_node.sql(dialect="spark"))
        query = in_expr.args.get("query")
        if query is None:
            values = in_expr.args.get("expressions") or []
            if not values or not all(isinstance(v, exp.Tuple) for v in values):
                continue
            if any(len(v.expressions) != len(lhs) for v in values):
                continue
            disj = None
            for v in values:
                grp = None
                for i in range(len(lhs)):
                    eq = exp.EQ(this=lhs[i].copy(), expression=v.expressions[i].copy())
                    grp = eq if grp is None else exp.And(this=grp, expression=eq)
                term = exp.Paren(this=grp)
                disj = term if disj is None else exp.Or(this=disj, expression=term)
            replacement = exp.Not(this=exp.Paren(this=disj))
            note = ("rewrote multi-column NOT IN (literal) as negated OR'd "
                    "equality groups — SCOS-portable and NULL-explicit")
        else:
            sel = query.this if isinstance(query, exp.Subquery) else query
            if not isinstance(sel, exp.Select):
                continue
            projs = sel.expressions
            if len(projs) != len(lhs) or not all(isinstance(p, exp.Column) for p in projs):
                continue
            alias = "__ne"
            cond = None
            for i, p in enumerate(projs):
                c = exp.EQ(this=exp.column(p.name, table=alias), expression=lhs[i].copy())
                cond = c if cond is None else exp.And(this=cond, expression=c)
            inner = (
                exp.select(exp.Literal.number(1))
                .from_(exp.Subquery(this=sel.copy(),
                                    alias=exp.TableAlias(this=exp.to_identifier(alias))))
                .where(cond)
            )
            replacement = exp.Not(this=exp.Exists(this=inner))
            note = ("rewrote multi-column NOT IN (subquery) as a NOT EXISTS "
                    "anti-join with explicit NULL-aware correlation")
        not_node.replace(replacement)
        edits.append(SqlEdit(
            rule_id="detector:multicolumn_not_in",
            line=_node_line(not_node, base_line), before=before,
            after=_snip(replacement.sql(dialect="spark")), note=note,
        ))
    return stmt, edits


def _grouping_set_columns(node):
    from sqlglot import exp
    if isinstance(node, exp.Paren):
        return [node.this]
    if isinstance(node, exp.Tuple):
        return list(node.expressions)
    return [node]


def rw_grouping_sets(stmt, base_line):
    from sqlglot import exp
    edits = []
    for grp in stmt.find_all(exp.Group):
        plain = grp.args.get("expressions") or []
        gsets = grp.args.get("grouping_sets") or []
        if not plain or not gsets:
            continue
        before = _snip(grp.sql(dialect="spark"))
        for gs in gsets:
            new_sets = [
                exp.Tuple(expressions=[p.copy() for p in plain]
                          + [c.copy() for c in _grouping_set_columns(s)])
                for s in gs.expressions
            ]
            gs.set("expressions", new_sets)
        grp.set("expressions", [])
        edits.append(SqlEdit(
            rule_id="detector:grouping_sets_with_groupby",
            line=_node_line(grp, base_line), before=before,
            after=_snip(grp.sql(dialect="spark")),
            note=("folded the plain GROUP BY columns into the GROUPING SETS "
                  "tuples — SCOS supports only the empty-GROUP-BY form"),
        ))
    return stmt, edits


def rw_cache(stmt, base_line):
    from sqlglot import exp
    if not isinstance(stmt, (exp.Cache, exp.Uncache)):
        return stmt, []
    edit = SqlEdit(
        rule_id="behavioral:sql.cache-table-unsupported",
        line=_node_line(stmt, base_line),
        before=_snip(stmt.sql(dialect="spark")), after="",
        note=("removed CACHE/UNCACHE TABLE — caching is a no-op on Snowflake/SCOS "
              "and the statement may raise"),
    )
    return None, [edit]


def rw_qualify(stmt, base_line):
    """``QUALIFY`` is not in the Spark SQL grammar used by ``spark.sql()`` — the
    SCOS parser (Spark 3.5.3 ``SparkSqlParser``) raises ``PARSE_SYNTAX_ERROR``.

    No manual restructuring is needed: sqlglot's spark generator rewrites
    ``QUALIFY <pred>`` into a ``ROW_NUMBER()``-style subquery with an outer
    ``WHERE`` on regeneration. Detecting the ``exp.Qualify`` node and returning a
    non-empty edit list is enough to make the engine regenerate the statement.
    """
    from sqlglot import exp
    import re as _re
    qualifies = list(stmt.find_all(exp.Qualify))
    if not qualifies:
        return stmt, []
    # The fold is a generation-time behavior of sqlglot's spark generator (it
    # rewrites QUALIFY into a subquery + outer WHERE when emitting). Verify it
    # actually happened: if the regenerated spark SQL still contains a QUALIFY
    # keyword (outside string literals), the fold failed for this shape — emit no
    # edit and leave it as a residual gap rather than shipping invalid SQL while
    # claiming a fix.
    regen = stmt.sql(dialect="spark")
    masked = _re.sub(r"'(?:''|[^'])*'", " ", regen)
    if _re.search(r"\bQUALIFY\b", masked, _re.IGNORECASE):
        return stmt, []
    edits = []
    for q in qualifies:
        edits.append(SqlEdit(
            rule_id="detector:qualify_unsupported",
            line=_node_line(q, base_line),
            before=_snip(q.sql(dialect="spark")),
            after="(folded into a subquery + outer WHERE on the window value)",
            note=("QUALIFY is not in the Spark SQL grammar used by spark.sql(); "
                  "rewrote it as a ROW_NUMBER()-style subquery with an outer "
                  "WHERE filter (SCOS-portable, semantics-preserving)"),
        ))
    return stmt, edits


def _listagg_order_matches_value(wg, value_expr, exp) -> bool:
    """True only when ``WITHIN GROUP (ORDER BY ...)`` is reproducible by
    ``array_sort`` over the collected values: a single ASC key equal to the
    aggregated expression. ``array_sort`` sorts the collected values ascending,
    so a different key, a DESC key, or multiple keys are NOT reproducible and
    must be left as a residual (LLM) gap rather than silently mis-ordered."""
    order = wg.args.get("expression")
    if not isinstance(order, exp.Order):
        return False
    oexprs = order.expressions or []
    if len(oexprs) != 1:
        return False
    o = oexprs[0]
    if o.args.get("desc"):  # descending — array_sort only ascends
        return False
    return o.this.sql(dialect="spark") == value_expr.sql(dialect="spark")


def rw_listagg_within_group(stmt, base_line):
    """``LISTAGG(x, sep) WITHIN GROUP (ORDER BY y)`` is rejected by the Spark SQL
    parser (``WITHIN GROUP`` is not in the grammar) and ``LISTAGG`` is not a SCOS
    function. Rewrite it as
    ``array_join(array_sort([array_distinct] collect_list(CAST(x AS STRING))), sep)``
    — all four functions parse and are supported by SCOS.

    Ordering is preserved by ``array_sort`` only when ``WITHIN GROUP`` orders by
    the LISTAGG expression itself, ascending. Any other ORDER BY key (or a DESC /
    multi-key order) is NOT reproducible by ``array_sort``, so this transform
    DECLINES those cases (emits no edit) and leaves the construct as a residual
    gap for the LLM fixer — the matcher still detects it. This keeps the
    mechanical rewrite genuinely semantics-preserving (it is gate-enforced).
    """
    from sqlglot import exp
    edits = []
    for wg in list(stmt.find_all(exp.WithinGroup)):
        gc = wg.this
        if not isinstance(gc, exp.GroupConcat):
            continue
        inner = gc.this
        distinct = isinstance(inner, exp.Distinct)
        value_expr = inner.expressions[0] if distinct else inner
        if not _listagg_order_matches_value(wg, value_expr, exp):
            continue  # order key not reproducible by array_sort — leave residual
        before = _snip(wg.sql(dialect="spark"))
        sep = gc.args.get("separator")
        sep_expr = sep.copy() if sep is not None else exp.Literal.string(",")
        collected = exp.func("collect_list", exp.cast(value_expr.copy(), "string"))
        arr = exp.func("array_distinct", collected) if distinct else collected
        arr = exp.func("array_sort", arr)
        repl = exp.func("array_join", arr, sep_expr)
        wg.replace(repl)
        edits.append(SqlEdit(
            rule_id="detector:listagg_within_group",
            line=_node_line(wg, base_line),
            before=before,
            after=_snip(repl.sql(dialect="spark")),
            note=("LISTAGG ... WITHIN GROUP is not supported by the Spark SQL "
                  "parser; rewrote as array_join(array_sort([array_distinct] "
                  "collect_list(CAST(x AS STRING))), sep). Applied only because "
                  "WITHIN GROUP orders (ascending) by the LISTAGG expression "
                  "itself, so array_sort reproduces the order exactly"),
        ))
    return stmt, edits


def rw_update_from(stmt, base_line):
    """Snowflake ``UPDATE t SET col = s.col, ... FROM s WHERE <conds>`` (join-update)
    is rejected by the Spark SQL parser — ``UPDATE`` has no ``FROM`` clause in the
    grammar (SCOS error 5001). Rewrite as
    ``MERGE INTO t USING s ON <conds> WHEN MATCHED THEN UPDATE SET col = s.col, ...``,
    folding the entire WHERE predicate into the MERGE ``ON``. SCOS supports MERGE
    (``MergeIntoTable``) and the generated MERGE parses on the Spark 3.5.3 parser.

    Only the single-source-table form is rewritten; an ``UPDATE ... FROM a JOIN b``
    (or comma-joined sources) is left untouched (reported as a residual gap).

    Caveat: when a target row matches multiple source rows, ``UPDATE ... FROM`` picks
    an arbitrary source row whereas Snowflake MERGE raises a nondeterministic-merge
    error — i.e. the ambiguity is surfaced rather than silently resolved. Equivalent
    for 1:1 / filtered joins (every observed case).
    """
    from sqlglot import exp
    if not isinstance(stmt, exp.Update):
        return stmt, []
    src = stmt.args.get("from_")
    if src is None:
        return stmt, []
    # Single simple source table only; bail on joins / multiple sources.
    if not isinstance(src.this, exp.Table) or next(iter(src.find_all(exp.Join)), None):
        return stmt, []
    set_items = [
        exp.EQ(this=e.this.copy(), expression=e.expression.copy())
        for e in stmt.expressions
        if isinstance(e, exp.EQ)
    ]
    if not set_items or len(set_items) != len(stmt.expressions):
        return stmt, []
    where = stmt.args.get("where")
    cond = where.this.copy() if where is not None else exp.condition("TRUE")
    before = _snip(stmt.sql(dialect="spark"))
    when = exp.When(matched=True, then=exp.Update(expressions=set_items))
    merge = exp.Merge(
        this=stmt.this.copy(),
        using=src.this.copy(),
        on=cond,
        whens=exp.Whens(expressions=[when]),
    )
    edit = SqlEdit(
        rule_id="detector:update_from_unsupported",
        line=_node_line(stmt, base_line),
        before=before,
        after=_snip(merge.sql(dialect="spark")),
        note=("UPDATE ... SET ... FROM <source> is not in the Spark SQL grammar; "
              "rewrote as MERGE INTO ... USING <source> ON <WHERE> WHEN MATCHED "
              "THEN UPDATE SET .... Multi-match raises a nondeterministic-merge "
              "error in Snowflake (vs an arbitrary pick); equivalent for "
              "1:1/filtered joins"),
    )
    return merge, [edit]
