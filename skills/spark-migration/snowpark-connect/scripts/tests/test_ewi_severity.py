"""Structural EWI severity: the classifier decision table + kb_rules invariants.

Replaces prose-pattern guessing with `support × category × disposition`.
Goal: an `Error` is ONLY a genuine code-logic failure left in the emitted code.
"""
import importlib.util
import json
from pathlib import Path

from ewi_severity import classify_status, infer_category

_SCRIPTS = Path(__file__).resolve().parents[1]
_KB = _SCRIPTS / "data" / "kb_rules.json"


def test_decision_table():
    # Fixed: a real rewrite/removal.
    assert classify_status("unsupported", "code_logic", "rewritten") == "Fixed"
    assert classify_status("unsupported", "code_logic", "dropped") == "Fixed"
    # Stubbed runs -> Warning, even for an unsupported API.
    assert classify_status("unsupported", "config", "stubbed") == "Warning"
    # Data I/O is a repoint -> IO, even when unsupported.
    assert classify_status("unsupported", "data_io", "left_in_place") == "IO"
    assert classify_status("behavioral", "data_io", "left_in_place") == "IO"
    # Genuine failure left in code -> Error (code_logic AND config AND perf-API
    # that actually raises).
    assert classify_status("unsupported", "code_logic", "left_in_place") == "Error"
    assert classify_status("unsupported", "config", "left_in_place") == "Error"
    assert classify_status("unsupported", "performance", "left_in_place") == "Error"
    # Perf/behavioral ADVISORY (not unsupported) -> Warning (code runs).
    assert classify_status("behavioral", "performance", "left_in_place") == "Warning"
    assert classify_status(None, "performance", "left_in_place") == "Warning"
    assert classify_status("behavioral", "code_logic", "left_in_place") == "Warning"
    assert classify_status("partial", "code_logic", "left_in_place") == "Warning"
    # Unknown (LLM-only, no structured facts) -> Warning, never a blind Error.
    assert classify_status(None, None, "left_in_place") == "Warning"


def test_infer_category_ignores_provenance_prefix():
    # rule_id provenance (csv:/parity:/apicat:) must NOT drive category.
    assert infer_category(["unpivot"], "csv:sql_test:unpivot.sql") == "code_logic"
    assert infer_category(["regexp_instr"], "csv:sql_test:regexp-functions.sql") == "code_logic"
    # genuine data-I/O anchors.
    assert infer_category(["orc"], "apicat:...DataFrameReader.orc#unsupported") == "data_io"
    assert infer_category(["read.format", "write.format"], "parity:jdbc#redshift") == "data_io"
    assert infer_category(["dbutils.fs.ls"], "csv:dbx:dbx_fs_ls") == "data_io"
    # config / performance anchors.
    assert infer_category(["spark.conf.set"], "scos:spark.conf.set#raise") == "config"
    assert infer_category(["cache"], "x:df.cache") == "performance"


def test_kb_error_rules_are_never_data_io():
    # Strong invariant: a data-I/O construct is a repoint (IO), never a
    # code-conversion Error. This is the primary false-positive-Error class.
    rules = json.loads(_KB.read_text())
    offenders = [
        r.get("rule_id") for r in rules
        if r.get("status_class") == "Error" and r.get("category") == "data_io"
    ]
    assert not offenders, f"data_io rules must be IO, not Error: {offenders}"


def test_kb_performance_errors_are_genuine_failures():
    # A performance-category rule may be Error ONLY when it is an api-catalog
    # `Unsupported` call that actually raises (e.g. DataFrame.checkpoint). A perf
    # ADVISORY (code runs, just slower) must never be Error.
    rules = json.loads(_KB.read_text())
    bad = [
        r.get("rule_id") for r in rules
        if r.get("status_class") == "Error"
        and r.get("category") == "performance"
        and (r.get("status") or "").lower() != "unsupported"
    ]
    assert not bad, f"performance Error rules must be api-catalog Unsupported: {bad}"


def test_kb_every_rule_has_category():
    rules = json.loads(_KB.read_text())
    missing = [r.get("rule_id") for r in rules if not r.get("category")]
    assert not missing, f"rules missing category: {missing[:10]}"
