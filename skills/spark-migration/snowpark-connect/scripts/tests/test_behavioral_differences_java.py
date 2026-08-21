"""Tests for Java behavioral-difference (BD) detection in ``analyze_java``.

``check_behavioral_differences_java`` runs each regex in
``BEHAVIORAL_DIFFERENCE_PATTERNS`` against a code string and emits one issue per
matching pattern. These tests verify true-positive detection, true-negative
silence, and EWI-code accuracy for the patterns that were updated for Java
(e.g. BD-16 uses .equalTo()/.notEqual() instead of Scala's === / =!=).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

# Guard: analyze_java imports snowflake; mock it if unavailable.
try:
    import snowflake.snowpark  # noqa: F401
    _SNOWPARK_AVAILABLE = True
except ImportError:
    _SNOWPARK_AVAILABLE = False

if not _SNOWPARK_AVAILABLE:
    import types
    _sf = types.ModuleType("snowflake")
    _sf.snowpark = types.ModuleType("snowflake.snowpark")  # type: ignore[attr-defined]
    _sf.snowpark.Session = object  # type: ignore[attr-defined]
    sys.modules.setdefault("snowflake", _sf)
    sys.modules.setdefault("snowflake.snowpark", _sf.snowpark)

import analyze_java as A  # noqa: E402


def _codes(code: str) -> set[str]:
    """Return the set of EWI codes emitted for a code snippet."""
    return {issue["ewi_code"] for issue in A.check_behavioral_differences_java(code)}


# ---------------------------------------------------------------------------
# BD-2: cast failure semantics
# ---------------------------------------------------------------------------


def test_bd2_cast_method_detected():
    assert "SPRKCNTSCL5001" in _codes('df.select(col("x").cast(DataTypes.IntegerType()))')


def test_bd2_cast_sql_detected():
    assert "SPRKCNTSCL5001" in _codes('df.selectExpr("CAST(x AS INT) AS x")')


# ---------------------------------------------------------------------------
# BD-5: element_at indexing (1-based in Spark, may differ in Connect)
# ---------------------------------------------------------------------------


def test_bd5_element_at_detected():
    assert "SPRKCNTSCL5004" in _codes('df.select(functions.element_at(col("arr"), 1))')


# ---------------------------------------------------------------------------
# BD-16: string equality case-sensitivity.
# Java DOES NOT have Scala's === / =!= operators; it uses .equalTo()/.notEqual().
# The Java BEHAVIORAL_DIFFERENCE_PATTERNS must include those Java forms.
# ---------------------------------------------------------------------------


def test_bd16_equal_to_java_detected():
    """Java .equalTo("val") must trigger BD-16 (SPRKCNTSCL5015).

    Note: the pattern matches `.equalTo("literal")` directly (a bare string arg),
    NOT `.equalTo(lit("literal"))`. The `lit()` wrapper form is common in Scala
    but Java often passes bare strings.
    """
    assert "SPRKCNTSCL5015" in _codes('df.filter(col("s").equalTo("Hello"));')


def test_bd16_not_equal_java_detected():
    assert "SPRKCNTSCL5015" in _codes('df.filter(col("s").notEqual("world"));')


def test_bd16_not_fired_for_numeric_comparison():
    """Numeric .equalTo(lit(42)) should not fire BD-16 (not a string literal)."""
    codes = _codes('df.filter(col("n").equalTo(lit(42)));')
    assert "SPRKCNTSCL5015" not in codes


# ---------------------------------------------------------------------------
# BD-1: division by zero semantics
# ---------------------------------------------------------------------------


def test_bd1_division_detected():
    assert "SPRKCNTSCL5000" in _codes('df.select(col("a").divide(col("b")))')


def test_bd1_not_fired_for_unrelated_slash():
    """A plain string literal with a slash must not trigger BD-1."""
    assert "SPRKCNTSCL5000" not in _codes('"s3://bucket/path"')


# ---------------------------------------------------------------------------
# Whole-pattern table coverage assertions
# ---------------------------------------------------------------------------


def test_all_patterns_have_required_fields():
    """BD patterns in BEHAVIORAL_DIFFERENCE_PATTERNS must have valid fields.

    Most emit 5xxx codes (true behavioral differences). Two BD-IO patterns
    (BD-IO-1: 'ignore' write mode, BD-IO-2: JDBC) emit SPRKCNTSCL1000 because
    they are detected through the BD-pattern loop but categorised as structural
    incompatibilities rather than behavioral differences — this is intentional.
    """
    for pattern, ewi_code, risk, reason, how_to_fix in A.BEHAVIORAL_DIFFERENCE_PATTERNS:
        assert ewi_code.startswith("SPRKCNTSCL"), (
            f"BD pattern {reason!r} has unexpected EWI code {ewi_code!r}"
        )
        assert 0.0 < risk <= 1.0, f"BD pattern risk out of range: {risk}"
        assert reason, "BD pattern reason must not be empty"
        assert how_to_fix, "BD pattern how_to_fix must not be empty"


def test_behavioral_patterns_cover_expected_ewi_codes():
    """The key BD codes from behavioral-differences.md must be present."""
    expected = {
        "SPRKCNTSCL5000",  # BD-1  division by zero
        "SPRKCNTSCL5001",  # BD-2  cast failure
        "SPRKCNTSCL5004",  # BD-5  element_at indexing
        "SPRKCNTSCL5015",  # BD-16 string comparison case-sensitivity
    }
    actual = {ewi for _, ewi, *_ in A.BEHAVIORAL_DIFFERENCE_PATTERNS}
    missing = expected - actual
    assert not missing, f"BEHAVIORAL_DIFFERENCE_PATTERNS is missing BD codes: {missing}"


def test_behavioral_patterns_are_all_in_java_ewi_csv():
    """Every EWI code emitted by behavioral patterns must be in data/java/ewi_code_mapping.csv."""
    import csv
    csv_codes = {
        r["ewi_code"]
        for r in csv.DictReader(open(_SCRIPTS / "data" / "java" / "ewi_code_mapping.csv"))
    }
    pattern_codes = {ewi for _, ewi, *_ in A.BEHAVIORAL_DIFFERENCE_PATTERNS}
    missing = pattern_codes - csv_codes
    assert not missing, (
        f"EWI codes emitted by BEHAVIORAL_DIFFERENCE_PATTERNS but absent from "
        f"data/java/ewi_code_mapping.csv: {missing}"
    )
