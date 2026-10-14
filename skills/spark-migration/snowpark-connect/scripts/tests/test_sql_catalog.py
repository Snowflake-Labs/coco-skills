"""Tests for the catalog-driven SQL engine (rag/sql_engine.py + sql_rules.json).

Covers (1) catalog schema validity — every rule references a real sqlglot node
type, a registered matcher/transform, and known predicate keys; (2) the
MECHANICAL/JUDGMENT partition is sourced correctly from the catalog; and (3) the
seeded new gaps fire on positive fixtures and not on negatives.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from rag.sql_ast import analyze_sql, MECHANICAL_RULE_IDS, JUDGMENT_RULE_IDS  # noqa: E402
from rag.sql_catalog import RULES  # noqa: E402
from rag.sql_matchers import MATCHERS, TRANSFORMS  # noqa: E402

_PREDICATE_KEYS = {
    "func_name_in", "arg_present", "arg_absent", "this_type",
    "parent_type", "has_descendant", "sql_matches",
}


def _ids(sql):
    res = analyze_sql(sql)
    return sorted({f.rule_id for f in res.findings}) if res is not None else None


# --- catalog schema ----------------------------------------------------------

def test_catalog_is_non_empty():
    assert len(RULES) >= 12


def test_validate_rejects_malformed_rules():
    import pytest
    from rag.sql_catalog import _validate
    # Missing required 'find'.
    with pytest.raises(ValueError):
        _validate([{"id": "x", "anchor": "a", "severity": "high"}])
    # Detecting rule with neither matcher nor when.
    with pytest.raises(ValueError):
        _validate([{"id": "x", "anchor": "a", "severity": "high", "find": "Select"}])
    # Wrong type for 'when'.
    with pytest.raises(ValueError):
        _validate([{"id": "x", "anchor": "a", "severity": "high", "find": "Select",
                    "when": "notalist"}])
    # detect:false rule with neither matcher nor when is allowed (lexical/transform-only).
    _validate([{"id": "x", "anchor": "a", "severity": "high", "find": "Cast",
                "detect": False}])
    # The shipped catalog must validate.
    _validate(RULES)


def test_every_rule_is_well_formed():
    import sqlglot.expressions as exp
    ids = [r["id"] for r in RULES]
    assert len(ids) == len(set(ids)), "duplicate rule ids"
    for r in RULES:
        assert r.get("id") and r.get("anchor") and r.get("severity")
        find = r["find"]
        assert find == "*" or getattr(exp, find, None) is not None, f"bad find: {find}"
        if r.get("matcher"):
            assert r["matcher"] in MATCHERS, f"unknown matcher {r['matcher']}"
        else:
            for pred in (r.get("when") or []):
                assert set(pred).issubset(_PREDICATE_KEYS), f"bad predicate {pred}"
        if r.get("transform"):
            assert r["transform"] in TRANSFORMS, f"unknown transform {r['transform']}"
        # A rule must be matchable: a matcher, or a `when` (possibly empty list).
        assert r.get("matcher") is not None or "when" in r or r.get("detect") is False


def test_mechanical_rules_have_a_transform():
    by_id = {r["id"]: r for r in RULES}
    # `dialect:colon_cast` is mechanical but handled by the engine's lexical
    # text-trigger (the `::` operator leaves no AST trace), not by a named
    # transform in the TRANSFORMS registry.
    ENGINE_HANDLED = {"dialect:colon_cast"}
    for rid in MECHANICAL_RULE_IDS:
        if rid in ENGINE_HANDLED:
            continue
        assert by_id[rid].get("transform"), f"{rid} is mechanical but has no transform"


def test_mechanical_judgment_partition_is_preserved():
    # The narrowed mechanical set (window/NOT-IN are JUDGMENT — their rewrites
    # are not semantics-preserving). The Rouses-ETL trio (QUALIFY, LISTAGG WITHIN
    # GROUP, :: cast) are all semantics-preserving Spark-parser fixes.
    assert MECHANICAL_RULE_IDS == frozenset({
        "detector:grouping_sets_with_groupby",
        "detector:explain_ddl_rejected",
        "detector:explain_mode_ignored",
        "detector:qualify_unsupported",
        "detector:listagg_within_group",
        "dialect:colon_cast",
        "detector:update_from_unsupported",
    })
    assert "detector:window_without_order_by" in JUDGMENT_RULE_IDS
    assert "detector:multicolumn_not_in" in JUDGMENT_RULE_IDS


# --- seeded new gaps: window-frame family (matcher path) ---------------------

def test_window_frame_fires_for_frame_sensitive_function():
    sql = ("SELECT NTH_VALUE(x, 2) OVER (PARTITION BY a ORDER BY b "
           "ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS v FROM t")
    assert "detector:unsupported_window_frame" in _ids(sql)


def test_window_frame_does_not_fire_without_explicit_frame():
    sql = "SELECT LAST_VALUE(x) OVER (PARTITION BY a ORDER BY b) AS v FROM t"
    assert "detector:unsupported_window_frame" not in (_ids(sql) or [])


def test_window_frame_does_not_fire_for_ordinary_function():
    sql = "SELECT SUM(x) OVER (PARTITION BY a ORDER BY b ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM t"
    assert "detector:unsupported_window_frame" not in (_ids(sql) or [])


# --- seeded new gaps: to_json/from_json options map -------------------------
# NOTE: function-name gaps (to_json/from_json options, etc.) are NOT in the AST
# catalog — they live in kb_rules.json as dual-surface (python_or_sql) rules so
# they cover BOTH the DataFrame API and SQL. The AST catalog is reserved for
# shape-dependent gaps a function name cannot decide.

def test_ast_catalog_holds_no_pure_function_name_rules():
    # Guard the division of labour: every AST rule either uses a shape matcher or
    # declarative predicates beyond a bare func_name_in (which belongs in
    # kb_rules.json). json_options was intentionally moved out.
    assert not any(r["id"] == "detector:json_options_map_ignored" for r in RULES)


# --- engine contract ---------------------------------------------------------

def test_parse_failure_returns_none():
    assert analyze_sql("this is not <<< sql") is None


# --- detect-only gaps seeded from the gaps report (2026-05-30) ---------------

def test_window_case_aggregate_fires_and_negative():
    pos = "SELECT CASE WHEN TRUE THEN MIN(x) ELSE MAX(x) END OVER (ORDER BY o) FROM t"
    assert "detector:window_case_aggregate" in _ids(pos)
    neg = "SELECT MIN(x) OVER (ORDER BY o) FROM t"
    assert "detector:window_case_aggregate" not in (_ids(neg) or [])


def test_cast_to_interval_fires_and_negative():
    assert "detector:cast_to_interval" in _ids("SELECT CAST(x AS INTERVAL YEAR TO MONTH) FROM t")
    assert "detector:cast_to_interval" not in (_ids("SELECT CAST(x AS INT) FROM t") or [])


def test_json_path_wildcard_fires_and_negative():
    pos = "SELECT get_json_object(val, '$.store.book[*].category') FROM t"
    assert "detector:json_path_wildcard" in _ids(pos)
    neg = "SELECT get_json_object(val, '$.store.book[0].category') FROM t"
    assert "detector:json_path_wildcard" not in (_ids(neg) or [])


def test_correlated_subquery_fires_for_setop_and_groupby():
    union = ("SELECT a, (SELECT sum(c) FROM (SELECT t1.c c FROM t1 WHERE t1.a=t0.a "
             "UNION ALL SELECT t2.c FROM t2)) FROM t0")
    assert "detector:correlated_subquery_unsupported" in _ids(union)
    group = ("SELECT a FROM t1 WHERE a < "
             "(SELECT max(t2.b) FROM t2 WHERE t2.c=t1.c GROUP BY t2.c)")
    assert "detector:correlated_subquery_unsupported" in _ids(group)


def test_correlated_subquery_negatives():
    # Non-correlated scalar subquery with GROUP BY — Snowflake accepts it.
    noncorr = "SELECT a, (SELECT max(b) FROM t2 GROUP BY t2.c) FROM t0"
    assert "detector:correlated_subquery_unsupported" not in (_ids(noncorr) or [])
    # Derived table in FROM (not a scalar value) — not the failing shape.
    derived = "SELECT * FROM (SELECT c FROM t1 UNION ALL SELECT c FROM t2) x"
    assert "detector:correlated_subquery_unsupported" not in (_ids(derived) or [])


def test_identifier_dynamic_fires_and_negative():
    assert "detector:identifier_dynamic" in _ids("SELECT * FROM IDENTIFIER('ra' || 'nge')(0, 1)")
    # A plain string-literal IDENTIFIER is not the dynamic gap.
    assert "detector:identifier_dynamic" not in (_ids("SELECT * FROM IDENTIFIER('range')") or [])


def test_map_unsupported_key_fires_and_negative():
    assert "detector:map_unsupported_key" in _ids("SELECT map(1.23, 'a') FROM t")
    assert "detector:map_unsupported_key" in _ids("SELECT map(true, 'b') FROM t")
    assert "detector:map_unsupported_key" not in (_ids("SELECT map('k', 'v') FROM t") or [])
    assert "detector:map_unsupported_key" not in (_ids("SELECT map(1, 'v') FROM t") or [])


def test_corr_distinct_is_lexical_not_ast():
    # sqlglot cannot parse CORR(DISTINCT ...) — the AST pass must bail (None) so
    # the regex detector in detectors.py fires via the fallback path.
    assert analyze_sql("SELECT corr(DISTINCT x, y) FROM t") is None
    from rag.detectors import run_detectors
    hits = {det.rule_id for det, _pos, _snip in run_detectors("SELECT corr(DISTINCT x, y) FROM t")}
    assert "detector:corr_distinct" in hits
    # Plain CORR (no DISTINCT) must not trip the lexical detector.
    hits2 = {det.rule_id for det, _pos, _snip in run_detectors("SELECT corr(x, y) FROM t")}
    assert "detector:corr_distinct" not in hits2
