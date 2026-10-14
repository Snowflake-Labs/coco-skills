"""Tests for the sqlglot-backed structural SQL detectors (rag/sql_ast.py) and
their integration with the trigger KB (window-order suppression, LCA / IN-in-ON
supersession)."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from rag.sql_ast import analyze_sql  # noqa: E402
from rag.trigger_kb import TriggerKB  # noqa: E402


def _rule_ids(findings) -> list[str]:
    return [f.rule_id for f in findings]


# --------------------------------------------------------------------------- #
# Window function ORDER BY
# --------------------------------------------------------------------------- #

def test_ordered_window_is_not_flagged() -> None:
    res = analyze_sql(
        "SELECT ROW_NUMBER() OVER (PARTITION BY x ORDER BY y) AS rn FROM t")
    assert res is not None
    assert "detector:window_without_order_by" not in _rule_ids(res.findings)


def test_unordered_window_is_flagged() -> None:
    res = analyze_sql("SELECT ROW_NUMBER() OVER (PARTITION BY x) AS rn FROM t")
    assert res is not None
    win = [f for f in res.findings if f.rule_id == "detector:window_without_order_by"]
    assert len(win) == 1
    assert win[0].severity == "high"
    assert win[0].line == 1


def test_unordered_lead_lag_flagged_but_aggregate_window_ignored() -> None:
    # LEAD without ORDER BY -> flagged; SUM windowed aggregate -> not an
    # order-sensitive function, so never flagged for missing ORDER BY.
    flagged = analyze_sql("SELECT LEAD(v) OVER (PARTITION BY k) FROM t")
    assert "detector:window_without_order_by" in _rule_ids(flagged.findings)
    agg = analyze_sql("SELECT SUM(v) OVER (PARTITION BY k) FROM t")
    assert "detector:window_without_order_by" not in _rule_ids(agg.findings)


# --------------------------------------------------------------------------- #
# IN (SELECT ...) in LEFT JOIN ON clause
# --------------------------------------------------------------------------- #

def test_in_subquery_in_left_join_on_is_flagged() -> None:
    res = analyze_sql(
        "SELECT * FROM a LEFT JOIN b ON a.id = b.id "
        "AND a.k IN (SELECT k FROM c)")
    assert "detector:in_subquery_in_on_clause" in _rule_ids(res.findings)


def test_in_subquery_in_where_is_not_flagged() -> None:
    # The same IN (SELECT) in a WHERE clause is legal — must not fire (this is
    # the regex detector's classic false positive).
    res = analyze_sql(
        "SELECT * FROM a LEFT JOIN b ON a.id = b.id "
        "WHERE a.k IN (SELECT k FROM c)")
    assert "detector:in_subquery_in_on_clause" not in _rule_ids(res.findings)


def test_value_list_in_on_clause_is_not_flagged() -> None:
    # IN (1, 2, 3) is a value list, not a subquery — not the gap.
    res = analyze_sql(
        "SELECT * FROM a LEFT JOIN b ON a.id = b.id AND a.k IN (1, 2, 3)")
    assert "detector:in_subquery_in_on_clause" not in _rule_ids(res.findings)


# --------------------------------------------------------------------------- #
# LCA alias / GROUP BY collision
# --------------------------------------------------------------------------- #

def test_lca_collision_is_flagged() -> None:
    res = analyze_sql(
        "SELECT k, SUM(v) AS k FROM t GROUP BY k")
    assert "detector:lca_alias_collision" in _rule_ids(res.findings)


def test_no_lca_collision_when_alias_distinct() -> None:
    res = analyze_sql(
        "SELECT k, SUM(v) AS total FROM t GROUP BY k")
    assert "detector:lca_alias_collision" not in _rule_ids(res.findings)


def test_lca_collision_via_group_by_ordinal() -> None:
    # GROUP BY 1 references projection #1 (column k); alias `k` on another
    # projection still shadows it. Ordinal grouping must be resolved.
    res = analyze_sql(
        "SELECT k, SUM(v) AS k FROM t GROUP BY 1")
    assert "detector:lca_alias_collision" in _rule_ids(res.findings)


# --------------------------------------------------------------------------- #
# Multi-column NOT IN (arity-aware, all occurrences)
# --------------------------------------------------------------------------- #

def test_multicolumn_not_in_subquery_is_flagged() -> None:
    res = analyze_sql(
        "SELECT * FROM r WHERE (item, loc) NOT IN (SELECT item, loc FROM t)")
    hits = [f for f in res.findings if f.rule_id == "detector:multicolumn_not_in"]
    assert len(hits) == 1
    assert hits[0].severity == "medium"


def test_multicolumn_not_in_literal_list_is_flagged_high() -> None:
    res = analyze_sql(
        "SELECT * FROM r WHERE (a, b) NOT IN ((1, 2), (3, 4))")
    hits = [f for f in res.findings if f.rule_id == "detector:multicolumn_not_in"]
    assert len(hits) == 1
    assert hits[0].severity == "high"


def test_single_column_not_in_is_not_flagged() -> None:
    # Scalar column and concatenated-key NOT IN share three-valued NULL
    # semantics across SCOS and Spark — not a divergence, must not fire.
    scalar = analyze_sql("SELECT * FROM r WHERE x NOT IN (10, 11)")
    assert "detector:multicolumn_not_in" not in _rule_ids(scalar.findings)
    concat = analyze_sql("SELECT * FROM r WHERE a || b NOT IN (SELECT k FROM t)")
    assert "detector:multicolumn_not_in" not in _rule_ids(concat.findings)


def test_every_multicolumn_not_in_occurrence_is_flagged() -> None:
    # find_all walks the whole tree — not just the first occurrence per file.
    sql = (
        "SELECT * FROM r WHERE (a, b) NOT IN (SELECT a, b FROM t1);\n"
        "SELECT * FROM s WHERE (c, d) NOT IN (SELECT c, d FROM t2);\n"
    )
    res = analyze_sql(sql)
    hits = [f for f in res.findings if f.rule_id == "detector:multicolumn_not_in"]
    assert len(hits) == 2
    assert {h.line for h in hits} == {1, 2}


def test_detect_suppresses_not_in_token_rule_for_single_column() -> None:
    """A single-column NOT IN must not raise the coarse multi-column token
    finding once the AST has adjudicated arity."""
    kb = TriggerKB.load()
    matches = kb.detect("SELECT * FROM r WHERE x NOT IN (10, 11);")
    rule_ids = {m.rule_id for m in matches}
    assert not any(rid.startswith("csv:sql_test:subquery/in-subquery/not-in")
                   for rid in rule_ids)
    assert "detector:multicolumn_not_in" not in rule_ids


# --------------------------------------------------------------------------- #
# SCOS §9 structural behavioral differences (Tier A)
# --------------------------------------------------------------------------- #

def test_insert_overwrite_with_partition_is_flagged() -> None:
    res = analyze_sql(
        "INSERT OVERWRITE TABLE s.daily PARTITION (region='NA') SELECT a FROM x")
    assert "detector:insert_overwrite_partition" in _rule_ids(res.findings)


def test_insert_overwrite_without_partition_is_not_flagged() -> None:
    res = analyze_sql("INSERT OVERWRITE TABLE s.daily SELECT a FROM x")
    assert "detector:insert_overwrite_partition" not in _rule_ids(res.findings)


def test_grouping_sets_with_nonempty_group_by_is_flagged() -> None:
    res = analyze_sql(
        "SELECT a, b FROM t GROUP BY a, b GROUPING SETS ((a), (b))")
    assert "detector:grouping_sets_with_groupby" in _rule_ids(res.findings)


def test_grouping_sets_with_empty_group_by_is_not_flagged() -> None:
    res = analyze_sql("SELECT a FROM t GROUP BY GROUPING SETS ((a), ())")
    assert "detector:grouping_sets_with_groupby" not in _rule_ids(res.findings)


def test_multi_expression_rollup_is_flagged() -> None:
    res = analyze_sql("SELECT a, b FROM t GROUP BY ROLLUP(a, b)")
    assert "detector:grouping_sets_with_groupby" in _rule_ids(res.findings)


def test_lateral_view_explode_is_flagged() -> None:
    res = analyze_sql("SELECT id FROM t LATERAL VIEW explode(x) v AS c")
    assert "detector:lateral_view_unsupported_generator" in _rule_ids(res.findings)


def test_lateral_view_flatten_is_not_flagged() -> None:
    # FLATTEN / SPLIT_TO_TABLE are supported generators.
    res = analyze_sql("SELECT id FROM t LATERAL VIEW flatten(x) v AS c")
    assert "detector:lateral_view_unsupported_generator" not in _rule_ids(res.findings)


def test_lateral_subquery_is_not_a_lateral_view() -> None:
    # Standard lateral join (view=False) must not trip the LATERAL VIEW detector.
    res = analyze_sql("SELECT * FROM t, LATERAL (SELECT 1) s")
    assert "detector:lateral_view_unsupported_generator" not in _rule_ids(res.findings)


def test_multiple_generators_in_one_select_is_flagged() -> None:
    res = analyze_sql("SELECT id, explode(a) AS t, explode(b) AS s FROM e")
    assert "detector:multi_generator_select" in _rule_ids(res.findings)


def test_single_generator_in_select_is_not_flagged() -> None:
    res = analyze_sql("SELECT id, explode(a) AS t FROM e")
    assert "detector:multi_generator_select" not in _rule_ids(res.findings)


def test_multi_generator_with_stack_is_flagged() -> None:
    # STACK parses as exp.Anonymous (sql_name()=='ANONYMOUS'); the matcher must
    # resolve it via _fn_name so STACK counts as a generator (catalog claims it).
    res = analyze_sql("SELECT stack(1, a) AS s, explode(b) AS e FROM t")
    assert "detector:multi_generator_select" in _rule_ids(res.findings)


def test_detect_suppresses_behavioral_token_rules_when_ast_adjudicates() -> None:
    """INSERT OVERWRITE without PARTITION and GROUPING SETS with an empty
    GROUP BY must produce no finding — the coarse behavioral:sql.* token rules
    are suppressed and the AST stays silent."""
    kb = TriggerKB.load()
    ow = {m.rule_id for m in kb.detect("INSERT OVERWRITE TABLE t SELECT a FROM s;")}
    assert "behavioral:sql.insert-overwrite-partition" not in ow
    assert "detector:insert_overwrite_partition" not in ow
    gs = {m.rule_id for m in kb.detect("SELECT a FROM t GROUP BY GROUPING SETS ((a), ());")}
    assert "behavioral:sql.grouping-sets" not in gs
    assert "detector:grouping_sets_with_groupby" not in gs


# --------------------------------------------------------------------------- #
# SCOS §9 new coverage (Tier B): TABLESAMPLE, TRANSFORM USING, EXPLAIN
# --------------------------------------------------------------------------- #

def test_tablesample_is_flagged() -> None:
    res = analyze_sql("SELECT * FROM t TABLESAMPLE (10 PERCENT)")
    assert "detector:tablesample_unsupported" in _rule_ids(res.findings)


def test_transform_using_is_flagged() -> None:
    res = analyze_sql("SELECT TRANSFORM(a, b) USING 'cmd' AS (x, y) FROM t")
    assert "detector:transform_using_unsupported" in _rule_ids(res.findings)


def test_explain_over_ddl_is_flagged() -> None:
    res = analyze_sql("EXPLAIN CREATE TABLE x AS SELECT 1")
    assert "detector:explain_ddl_rejected" in _rule_ids(res.findings)


def test_explain_over_dml_is_not_flagged() -> None:
    # EXPLAIN SELECT is supported (Snowflake EXPLAIN covers DML).
    res = analyze_sql("EXPLAIN SELECT * FROM t")
    assert res is not None
    ids = _rule_ids(res.findings)
    assert "detector:explain_ddl_rejected" not in ids
    assert "detector:explain_mode_ignored" not in ids


def test_explain_mode_is_flagged() -> None:
    for mode in ("FORMATTED", "EXTENDED", "CODEGEN", "COST"):
        res = analyze_sql(f"EXPLAIN {mode} SELECT 1")
        assert "detector:explain_mode_ignored" in _rule_ids(res.findings), mode


# --------------------------------------------------------------------------- #
# Graceful fallback contract
# --------------------------------------------------------------------------- #

def test_unparseable_sql_returns_none() -> None:
    assert analyze_sql("@@@ this is not ::: parseable !!!") is None


def test_templated_sql_parses_after_placeholder_normalization() -> None:
    res = analyze_sql(
        "SELECT ROW_NUMBER() OVER (PARTITION BY x) FROM ${DB}.${SCHEMA}.tbl")
    assert res is not None
    assert "detector:window_without_order_by" in _rule_ids(res.findings)


# --------------------------------------------------------------------------- #
# Integration with TriggerKB.detect
# --------------------------------------------------------------------------- #

def test_detect_suppresses_window_token_rule_when_ordered() -> None:
    """An ordered ROW_NUMBER must not produce the coarse 'window requires ORDER
    BY' token finding once the AST has adjudicated it."""
    kb = TriggerKB.load()
    ordered = kb.detect(
        "SELECT ROW_NUMBER() OVER (PARTITION BY x ORDER BY y) AS rn FROM t;")
    notes = " ".join(m.note for m in ordered)
    assert "window to be ordered" not in notes
    assert "detector:window_without_order_by" not in {m.rule_id for m in ordered}


def test_detect_flags_window_when_order_missing() -> None:
    kb = TriggerKB.load()
    unordered = kb.detect(
        "SELECT ROW_NUMBER() OVER (PARTITION BY x) AS rn FROM t;")
    assert "detector:window_without_order_by" in {m.rule_id for m in unordered}


def test_detect_runs_ast_on_embedded_spark_sql() -> None:
    """SQL inside a spark.sql("...") string in Python source must flow through
    the AST detectors, not just the coarse token rules — so shape-dependent gaps
    (here, a window missing ORDER BY) are caught in embedded SQL too."""
    kb = TriggerKB.load()
    src = 'df = spark.sql("SELECT ROW_NUMBER() OVER (PARTITION BY x) AS rn FROM t")\n'
    matches = kb.detect(src)
    assert "detector:window_without_order_by" in {m.rule_id for m in matches}


def test_detect_no_window_finding_for_ordered_embedded_spark_sql() -> None:
    kb = TriggerKB.load()
    src = 'df = spark.sql("SELECT ROW_NUMBER() OVER (PARTITION BY x ORDER BY y) AS rn FROM t")\n'
    matches = kb.detect(src)
    assert "detector:window_without_order_by" not in {m.rule_id for m in matches}
