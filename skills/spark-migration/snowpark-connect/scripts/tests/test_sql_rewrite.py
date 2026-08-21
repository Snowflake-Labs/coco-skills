"""Tests for the deterministic SQL rewriter (rag/sql_rewrite.py).

Stage A covers the harness contract (parse-failure no-op, idempotency,
statement-level reconstruction, residual reporting) plus the first two mechanical
transforms: window-missing-ORDER-BY and EXPLAIN drop. Later stages add a cluster
per additional transform.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from rag.sql_rewrite import rewrite_sql  # noqa: E402


def _applied_ids(res):
    return [e.rule_id for e in res.applied]


def _residual_ids(res):
    return [f.rule_id for f in res.residual]


# --- harness contract --------------------------------------------------------

def test_parse_failure_is_verbatim_noop():
    junk = "this is not (sql at all <<<"
    res = rewrite_sql(junk)
    assert res.parsed is False
    assert res.changed is False
    assert res.new_text == junk
    assert res.applied == []


def test_clean_sql_is_unchanged():
    sql = "SELECT a, b FROM t WHERE a > 1\n"
    res = rewrite_sql(sql)
    assert res.parsed is True
    assert res.changed is False
    assert res.new_text == sql
    assert res.applied == []


# --- window without ORDER BY (detect-only; NOT auto-rewritten) ---------------

def test_window_with_partition_is_residual_not_rewritten():
    # No semantics-preserving fix exists: ordering by the PARTITION BY keys
    # leaves peer rows tied (arbitrary order), it only silences Spark's error.
    # So the window is detected and left as a residual judgment gap, never
    # auto-rewritten.
    sql = "SELECT ROW_NUMBER() OVER (PARTITION BY x) AS rn FROM t"
    res = rewrite_sql(sql)
    assert res.changed is False
    assert "detector:window_without_order_by" not in _applied_ids(res)
    assert "detector:window_without_order_by" in _residual_ids(res)


def test_window_with_existing_order_is_untouched():
    sql = "SELECT ROW_NUMBER() OVER (PARTITION BY x ORDER BY y) AS rn FROM t"
    res = rewrite_sql(sql)
    assert res.changed is False
    assert res.applied == []
    assert "detector:window_without_order_by" not in _residual_ids(res)


def test_window_without_partition_is_residual_not_rewritten():
    # No PARTITION BY → still no safe ordering key; remains a residual finding
    # for the LLM/annotation path.
    sql = "SELECT ROW_NUMBER() OVER () AS rn FROM t"
    res = rewrite_sql(sql)
    assert "detector:window_without_order_by" not in _applied_ids(res)
    assert "detector:window_without_order_by" in _residual_ids(res)


# --- EXPLAIN -----------------------------------------------------------------

def test_explain_over_ddl_is_dropped():
    sql = "EXPLAIN CREATE TABLE t AS SELECT 1"
    res = rewrite_sql(sql)
    assert res.changed is True
    assert "detector:explain_ddl_rejected" in _applied_ids(res)
    assert res.new_text.upper().lstrip().startswith("CREATE")
    assert rewrite_sql(res.new_text).changed is False


def test_explain_mode_is_dropped():
    sql = "EXPLAIN FORMATTED SELECT 1"
    res = rewrite_sql(sql)
    assert res.changed is True
    assert "detector:explain_mode_ignored" in _applied_ids(res)
    assert "FORMATTED" not in res.new_text.upper()
    assert rewrite_sql(res.new_text).changed is False


def test_plain_explain_select_is_untouched():
    sql = "EXPLAIN SELECT 1"
    res = rewrite_sql(sql)
    assert res.changed is False
    assert res.applied == []


# --- multi-statement: only the changed statement is regenerated --------------

def test_multistatement_preserves_untouched_statements_verbatim():
    sql = (
        "-- header comment\n"
        "SELECT a, b\nFROM my_table\nWHERE a = 1;\n"
        "EXPLAIN CREATE TABLE t AS SELECT 1;\n"
    )
    res = rewrite_sql(sql)
    assert res.changed is True
    # The untouched first statement (and its bespoke formatting/comment) survive.
    assert "-- header comment" in res.new_text
    assert "SELECT a, b\nFROM my_table\nWHERE a = 1;" in res.new_text
    # The second statement is rewritten: EXPLAIN over DDL is dropped.
    assert "detector:explain_ddl_rejected" in _applied_ids(res)
    assert "EXPLAIN" not in res.new_text.upper()


# --- judgment-heavy gaps surface as residual, not applied --------------------

def test_judgment_heavy_is_residual_not_applied():
    # TRANSFORM USING is judgment-heavy; it must land in residual and NOT be
    # silently dropped or wrongly "applied".
    sql = "SELECT TRANSFORM(a, b) USING 'cat' AS (x, y) FROM t"
    res = rewrite_sql(sql)
    assert res.applied == []
    assert "detector:transform_using_unsupported" in _residual_ids(res)


# --- multi-column NOT IN (detect-only; NOT auto-rewritten) -------------------

def test_multicolumn_not_in_literal_is_residual_not_rewritten():
    sql = "SELECT * FROM t WHERE (a, b) NOT IN ((1, 2), (3, 4))"
    res = rewrite_sql(sql)
    assert res.changed is False
    assert "detector:multicolumn_not_in" not in _applied_ids(res)
    assert "detector:multicolumn_not_in" in _residual_ids(res)


def test_multicolumn_not_in_subquery_is_residual_not_rewritten():
    # A plain-equality NOT EXISTS is NOT NULL-equivalent to multi-column NOT IN,
    # so this is detected and left for the LLM fixer rather than auto-rewritten.
    sql = "SELECT * FROM t WHERE (a, b) NOT IN (SELECT a, b FROM u)"
    res = rewrite_sql(sql)
    assert res.changed is False
    assert "detector:multicolumn_not_in" not in _applied_ids(res)
    assert "detector:multicolumn_not_in" in _residual_ids(res)


def test_single_column_not_in_is_untouched():
    sql = "SELECT * FROM t WHERE a NOT IN (1, 2, 3)"
    res = rewrite_sql(sql)
    assert res.changed is False
    assert res.applied == []


# --- GROUPING SETS fold (Stage D) --------------------------------------------

def test_grouping_sets_with_group_by_is_folded():
    sql = "SELECT a, b, SUM(x) FROM t GROUP BY a GROUPING SETS ((b), ())"
    res = rewrite_sql(sql)
    assert res.changed is True
    assert "detector:grouping_sets_with_groupby" in _applied_ids(res)
    up = res.new_text.upper()
    assert "(A, B)" in up  # plain GROUP BY col folded into the set
    assert rewrite_sql(res.new_text).changed is False


def test_grouping_sets_without_group_by_is_untouched():
    sql = "SELECT a, b FROM t GROUP BY GROUPING SETS ((a), (b))"
    res = rewrite_sql(sql)
    assert res.changed is False


# --- CACHE / UNCACHE deletion (Stage D) --------------------------------------

def test_cache_table_is_removed():
    sql = "CACHE TABLE t;\nSELECT 1 FROM t;\n"
    res = rewrite_sql(sql)
    assert res.changed is True
    assert "behavioral:sql.cache-table-unsupported" in _applied_ids(res)
    assert "CACHE TABLE" not in res.new_text.upper()
    assert "SELECT 1 FROM t" in res.new_text  # sibling statement preserved
    assert rewrite_sql(res.new_text).changed is False


def test_uncache_table_is_removed():
    sql = "UNCACHE TABLE t"
    res = rewrite_sql(sql)
    assert res.changed is True
    assert "behavioral:sql.cache-table-unsupported" in _applied_ids(res)
    assert res.new_text.strip() == ""


# --- QUALIFY / LISTAGG WITHIN GROUP / :: cast (Rouses ETL validation) --------
# These three are the only cheat-sheet rewrites that genuinely fail SCOS: each
# is a Spark 3.5.3 parser (SparkSqlParser) syntax error. IFNULL/NVL/DATEADD/
# GROUP BY ALL are supported natively and must NOT be rewritten (asserted below).

def test_qualify_is_folded_into_subquery():
    sql = "SELECT a FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1"
    res = rewrite_sql(sql)
    assert res.changed is True
    assert "detector:qualify_unsupported" in _applied_ids(res)
    assert "QUALIFY" not in res.new_text.upper()
    assert "ROW_NUMBER()" in res.new_text.upper()
    assert "WHERE" in res.new_text.upper()
    assert rewrite_sql(res.new_text).changed is False


def test_listagg_within_group_is_rewritten_to_array_join():
    # Safe case: WITHIN GROUP orders (ascending) by the LISTAGG expression
    # itself, so array_sort over the collected values reproduces the order.
    sql = "SELECT LISTAGG(name, ',') WITHIN GROUP (ORDER BY name) AS d FROM t GROUP BY id"
    res = rewrite_sql(sql)
    assert res.changed is True
    assert "detector:listagg_within_group" in _applied_ids(res)
    out = res.new_text.upper()
    assert "LISTAGG" not in out
    assert "WITHIN GROUP" not in out
    assert "ARRAY_JOIN" in out and "ARRAY_SORT" in out and "COLLECT_LIST" in out
    assert rewrite_sql(res.new_text).changed is False


def test_listagg_within_group_mismatched_order_key_is_not_rewritten():
    # Unsafe: ordering by a DIFFERENT column than the aggregated expression
    # cannot be reproduced by array_sort over the values. Must be left as a
    # residual gap (detected, NOT mechanically rewritten) so the pipeline never
    # ships a silently mis-ordered concatenation.
    sql = "SELECT LISTAGG(name, ',') WITHIN GROUP (ORDER BY age) AS d FROM t GROUP BY id"
    res = rewrite_sql(sql)
    assert "detector:listagg_within_group" not in _applied_ids(res)
    assert "LISTAGG" in res.new_text.upper()  # left intact
    # Still surfaced as a residual finding for the LLM fixer.
    assert "detector:listagg_within_group" in _residual_ids(res)


def test_listagg_within_group_desc_order_is_not_rewritten():
    # array_sort only ascends, so a DESC WITHIN GROUP is not reproducible.
    sql = "SELECT LISTAGG(name, ',') WITHIN GROUP (ORDER BY name DESC) AS d FROM t GROUP BY id"
    res = rewrite_sql(sql)
    assert "detector:listagg_within_group" not in _applied_ids(res)
    assert "detector:listagg_within_group" in _residual_ids(res)


def test_listagg_distinct_adds_array_distinct():
    sql = "SELECT LISTAGG(DISTINCT c, '|') WITHIN GROUP (ORDER BY c) AS x FROM t"
    res = rewrite_sql(sql)
    assert res.changed is True
    assert "ARRAY_DISTINCT" in res.new_text.upper()
    assert rewrite_sql(res.new_text).changed is False


def test_colon_cast_is_rewritten_to_cast():
    sql = "SELECT c::STRING AS x, n::INT AS y FROM t"
    res = rewrite_sql(sql)
    assert res.changed is True
    assert "dialect:colon_cast" in _applied_ids(res)
    assert "::" not in res.new_text
    assert res.new_text.upper().count("CAST(") == 2
    assert rewrite_sql(res.new_text).changed is False


def test_colon_in_string_literal_does_not_trigger():
    # `::` only inside a string literal (no real cast) must not rewrite.
    sql = "SELECT 'a::b' AS lit FROM t"
    res = rewrite_sql(sql)
    assert res.changed is False
    assert res.applied == []


def test_colon_in_string_literal_with_real_cast_does_not_emit_colon_edit():
    # A `::` inside a string literal must not emit a phantom colon_cast edit even
    # when a genuine CAST(...) node exists elsewhere in the statement.
    sql = "SELECT CAST(x AS INT) AS y, 'a::b' AS lit FROM t"
    res = rewrite_sql(sql)
    assert "dialect:colon_cast" not in _applied_ids(res)


def test_supported_functions_are_not_rewritten():
    # Validated against SCOS source: IFNULL/NVL (map_unresolved_function.py:7426),
    # DATEADD->TimestampAdd->timestamp_add (:9956), GROUP BY ALL (map_extension.py:446).
    for sql in (
        "SELECT IFNULL(a, b), NVL(c, d) FROM t",
        "SELECT DATEADD(DAY, -7, d) AS x FROM t",
        "SELECT a, SUM(b) FROM t GROUP BY ALL",
    ):
        res = rewrite_sql(sql)
        assert res.changed is False, f"unexpectedly rewrote: {sql}"
        assert res.applied == []


def test_template_vars_preserved_in_rewritten_statement():
    # Rewritten statements must keep ${DATABASE_NAME}.${SCHEMA}. qualifiers
    # (round-tripped as identifier tokens), not strip them to bare names.
    sql = ("SELECT c::STRING AS x FROM ${DATABASE_NAME}.${SCHEMA_STAGING}.MYTAB "
           "QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1")
    res = rewrite_sql(sql)
    assert res.changed is True
    assert "${DATABASE_NAME}.${SCHEMA_STAGING}.MYTAB" in res.new_text
    assert "_ph_" not in res.new_text and "SCOSPHK" not in res.new_text


# --- UPDATE ... FROM -> MERGE (Rouses dim_sql_ml.sql) ------------------------

def test_update_from_is_rewritten_to_merge():
    sql = ("UPDATE T.ML.CUSTOM c SET c.STATUS = w.STATUS, c.KVI = w.UDA2 "
           "FROM T.ML.DIM w WHERE w.loc = c.loc AND w.item = c.item "
           "AND (c.IGNORE = FALSE OR c.IGNORE IS NULL)")
    res = rewrite_sql(sql)
    assert res.changed is True
    assert "detector:update_from_unsupported" in _applied_ids(res)
    out = res.new_text.upper()
    assert "MERGE INTO" in out and "WHEN MATCHED THEN UPDATE SET" in out
    assert "USING T.ML.DIM" in out
    # the entire WHERE folds into ON, incl. the target-only filter
    assert "C.IGNORE = FALSE OR C.IGNORE IS NULL" in out
    assert rewrite_sql(res.new_text).changed is False


def test_plain_update_without_from_is_untouched():
    # UPDATE without FROM parses fine on Spark; must not be rewritten.
    sql = "UPDATE t SET status = 0 WHERE item NOT IN (SELECT item FROM dim)"
    res = rewrite_sql(sql)
    assert res.changed is False
    assert res.applied == []


def test_update_from_with_join_source_is_left_as_residual():
    # Multi-source / joined FROM is not safe to fold; leave it (no rewrite).
    sql = ("UPDATE t c SET c.x = a.x FROM a JOIN b ON a.k = b.k "
           "WHERE a.k = c.k")
    res = rewrite_sql(sql)
    assert "detector:update_from_unsupported" not in _applied_ids(res)
