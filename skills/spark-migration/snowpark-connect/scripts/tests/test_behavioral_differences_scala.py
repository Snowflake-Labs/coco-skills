"""Tests for Scala behavioral-difference (BD) detection in ``analyze_scala``.

``check_behavioral_differences_scala`` runs each regex in
``BEHAVIORAL_DIFFERENCE_PATTERNS`` against a code string and emits one issue per
matching pattern. These tests assert true-positive detection, true-negative
silence, and the guard cases where a regex must NOT fire (e.g. BD-11 ``concat``
must not collide with ``concat_ws``; BD-29's ``.coalesce`` method must not
collide with the SQL ``coalesce(...)`` function used in other BD fixes).

This file focuses on the Wave-1 (BD-2,5,6,10,11,14,15,17,18,26) and Wave-2
(BD-29) additions, plus coverage assertions over the whole pattern table.
"""

from __future__ import annotations

from analyze_scala import (
    BEHAVIORAL_DIFFERENCE_PATTERNS,
    check_behavioral_differences_scala,
    check_unsupported_imports_scala,
)


def _codes(code: str) -> set[str]:
    """Return the set of EWI codes emitted for a code snippet."""
    return {issue["ewi_code"] for issue in check_behavioral_differences_scala(code)}


# --- Wave 1: true-positive detection ---------------------------------------


def test_bd2_cast_method_detected():
    assert "SPRKCNTSCL5001" in _codes('df.select(col("x").cast(IntegerType))')


def test_bd2_cast_sql_uppercase_detected():
    assert "SPRKCNTSCL5001" in _codes('df.selectExpr("CAST(x AS INT) as x")')


def test_bd5_element_at_detected():
    assert "SPRKCNTSCL5004" in _codes('df.select(element_at(col("arr"), 1))')


def test_bd6_concat_ws_detected():
    assert "SPRKCNTSCL5005" in _codes('df.select(concat_ws(",", col("a"), col("b")))')


def test_bd10_greatest_detected():
    assert "SPRKCNTSCL5009" in _codes('df.select(greatest(col("a"), col("b")))')


def test_bd10_least_detected():
    assert "SPRKCNTSCL5009" in _codes('df.select(least(col("a"), col("b")))')


def test_bd11_concat_detected():
    assert "SPRKCNTSCL5010" in _codes('df.select(concat(col("a"), col("b")))')


def test_bd14_round_detected():
    assert "SPRKCNTSCL5013" in _codes('df.select(round(col("x"), 2))')


def test_bd14_bround_detected():
    assert "SPRKCNTSCL5013" in _codes('df.select(bround(col("x"), 2))')


def test_bd15_explode_detected():
    assert "SPRKCNTSCL5014" in _codes('df.select(explode(col("arr")))')


def test_bd15_explode_outer_detected():
    assert "SPRKCNTSCL5014" in _codes('df.select(explode_outer(col("arr")))')


def test_bd17_months_between_detected():
    assert "SPRKCNTSCL5016" in _codes('df.select(months_between(col("a"), col("b")))')


def test_bd18_eqnullsafe_method_detected():
    assert "SPRKCNTSCL5017" in _codes('df.filter(col("a").eqNullSafe(col("b")))')


def test_bd18_nullsafe_operator_detected():
    assert "SPRKCNTSCL5017" in _codes('df.filter(col("a") <=> col("b"))')


def test_bd26_approx_count_distinct_detected():
    assert "SPRKCNTSCL5025" in _codes('df.agg(approx_count_distinct(col("id"), 0.01))')


# --- Wave 2: BD-29 distribution hints --------------------------------------


def test_bd29_broadcast_detected():
    assert "SPRKCNTSCL5028" in _codes("df.join(broadcast(small), Seq(\"k\"))")


def test_bd29_repartition_detected():
    assert "SPRKCNTSCL5028" in _codes("df.repartition(8)")


def test_bd29_coalesce_method_detected():
    assert "SPRKCNTSCL5028" in _codes("df.coalesce(1).write.parquet(path)")


# --- Guard cases: regexes that must NOT fire -------------------------------


def test_bd11_concat_does_not_fire_on_concat_ws():
    """concat_ws should trigger BD-6 only, never BD-11 (concat)."""
    codes = _codes('df.select(concat_ws(",", col("a"), col("b")))')
    assert "SPRKCNTSCL5005" in codes  # BD-6
    assert "SPRKCNTSCL5010" not in codes  # BD-11 must not fire


def test_bd29_sql_coalesce_function_does_not_fire():
    """The SQL coalesce(...) function (used by BD-6/10/11 fixes) must not be
    misread as the DataFrame .coalesce(n) partition hint (BD-29)."""
    codes = _codes('df.select(coalesce(col("a"), lit("")))')
    assert "SPRKCNTSCL5028" not in codes


def test_clean_code_emits_nothing():
    code = 'val out = df.select(col("a"), col("b")).filter(col("a") > 0)'
    assert _codes(code) == set()


def test_multiple_bds_in_one_snippet():
    code = (
        'df.select(\n'
        '  concat_ws(",", col("a"), col("b")),\n'
        '  round(col("x"), 2),\n'
        '  element_at(col("arr"), 1)\n'
        ').repartition(4)'
    )
    codes = _codes(code)
    assert {"SPRKCNTSCL5005", "SPRKCNTSCL5013", "SPRKCNTSCL5004", "SPRKCNTSCL5028"} <= codes


# --- Pattern-table integrity ------------------------------------------------


def test_all_ewi_codes_unique():
    ewi_codes = [t[1] for t in BEHAVIORAL_DIFFERENCE_PATTERNS]
    assert len(ewi_codes) == len(set(ewi_codes)), "duplicate EWI code in BD table"


def test_wave1_and_wave2_codes_present_in_table():
    table_codes = {t[1] for t in BEHAVIORAL_DIFFERENCE_PATTERNS}
    expected = {
        "SPRKCNTSCL5001",  # BD-2
        "SPRKCNTSCL5004",  # BD-5
        "SPRKCNTSCL5005",  # BD-6
        "SPRKCNTSCL5009",  # BD-10
        "SPRKCNTSCL5010",  # BD-11
        "SPRKCNTSCL5013",  # BD-14
        "SPRKCNTSCL5014",  # BD-15
        "SPRKCNTSCL5016",  # BD-17
        "SPRKCNTSCL5017",  # BD-18
        "SPRKCNTSCL5025",  # BD-26
        "SPRKCNTSCL5028",  # BD-29
    }
    assert expected <= table_codes


def test_risk_scores_in_valid_range():
    for pattern, _ewi, risk, _reason, _fix in BEHAVIORAL_DIFFERENCE_PATTERNS:
        assert 0.0 < risk <= 1.0, f"risk out of range for {pattern!r}"


# --- Unsupported-import detection (former analyzer blind spots) -------------


def test_spline_import_detected():
    """Spline was the one analyzer blind spot not natively detected; adding it
    to UNSUPPORTED_IMPORTS lets the deterministic analyzer flag it directly."""
    issues = check_unsupported_imports_scala("import za.co.absa.spline.harvester.SparkLineageInitializer")
    apis = {i["api"] for i in issues}
    assert "za.co.absa.spline" in apis


def test_native_blind_spots_detected():
    """The other former-supplementation patterns are natively detected, so the
    LLM Step-2 supplementation is redundant."""
    apis = {i["api"] for i in check_unsupported_imports_scala(
        "import org.apache.spark.sql.catalyst.expressions._\n"
        "import org.apache.hadoop.fs.FileSystem\n"
        "import com.hortonworks.spark.sql.hive.llap.HiveWarehouseSession\n"
    )}
    assert {"org.apache.spark.sql.catalyst", "org.apache.hadoop",
            "com.hortonworks.spark.sql.hive"} <= apis


def test_clean_imports_not_flagged():
    issues = check_unsupported_imports_scala("import org.apache.spark.sql.functions._")
    assert issues == []
