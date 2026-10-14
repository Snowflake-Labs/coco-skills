"""Unit tests for the deterministic EWI code + status_class carry (detection side).

Verifies that ewi_code/status_class are seeded on the catalog and carried from
the rule -> Match -> SCOSSearchResult, so the analyzer can emit them onto every
analysis.json finding (instead of reconstructing from LLM free-text).

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_ewi_code_carry.py
"""
from __future__ import annotations

import json
from pathlib import Path

from rag.trigger_kb import TriggerKB

_KB = Path(__file__).resolve().parents[1] / "data" / "kb_rules.json"


def _rules():
    return json.loads(_KB.read_text())


def test_all_rules_carry_ewi_code_and_status_class():
    rules = _rules()
    missing = [r.get("rule_id") for r in rules
               if not (r.get("ewi_code") and r.get("status_class"))]
    assert not missing, f"{len(missing)} rule(s) missing ewi_code/status_class: {missing[:5]}"


def test_status_class_values_are_valid():
    valid = {"Fixed", "Error", "Warning", "IO"}
    bad = {r.get("status_class") for r in _rules()} - valid
    assert not bad, f"unexpected status_class values: {bad}"


def test_ewi_codes_follow_sprkcntpy_shape():
    import re
    pat = re.compile(r"^SPRKCNT[A-Z]*\d+$")
    bad = [r.get("ewi_code") for r in _rules() if not pat.match(r.get("ewi_code") or "")]
    assert not bad, f"malformed ewi_code(s): {bad[:5]}"


def test_match_carries_ewi_code_from_rule():
    # A synthetic rule with a literal python anchor -> detect() must carry the
    # rule's ewi_code/status_class onto the Match.
    kb = TriggerKB(rules=[{
        "rule_id": "test:ewi_carry",
        "api": ["collect"],
        "kind": "python_method",
        "note": "n", "severity": "high",
        "surface": "function", "condition": "always",
        "ewi_code": "SPRKCNTPY9999", "status_class": "Error",
    }])
    kb._index()
    matches = kb.detect("df.collect()\n")
    hit = [m for m in matches if m.rule_id == "test:ewi_carry"]
    assert hit, "rule did not fire"
    assert hit[0].ewi_code == "SPRKCNTPY9999"
    assert hit[0].status_class == "Error"


def test_window_rules_tagged_error_or_f():
    # The window_no_order_by rules must surface with a real code + status when met.
    # row_number without ORDER BY genuinely raises in Spark -> Error; lead is
    # auto-rewritten -> Fixed; first_value(ignoreNulls) still executes on SCOS
    # (only the window-frame semantics differ) -> Warning, not a runtime failure.
    want = {"row_number": ("SPRKCNTPY5300", "Error"),
            "lead": ("SPRKCNTPY5300", "Fixed"),
            "first_value": ("SPRKCNTPY5300", "Warning")}
    got = {}
    for r in _rules():
        leaves = {(a or "").lower().rsplit(".", 1)[-1] for a in (r.get("api") or [])}
        for fn in want:
            if fn in leaves:
                got[fn] = (r.get("ewi_code"), r.get("status_class"))
    for fn, exp in want.items():
        assert got.get(fn) == exp, f"{fn}: got {got.get(fn)} want {exp}"


def test_cloud_path_literal_tagged_sprkcntpy5400_io():
    # External cloud-storage path literals are a synthetic (rule-less) detection
    # in detect(). They are an I/O repoint (read/write must go through a Snowflake
    # stage/table), NOT a code-conversion error, so the Match must carry
    # SPRKCNTPY5400 / IO — even with an empty rule catalog.
    kb = TriggerKB(rules=[])
    kb._index()
    for literal in (
        's3_path = "s3://bucket/prefix/data/"\n',
        'p = "dbfs:/mnt/raw/table/"\n',
        'g = "abfss://container@acct.dfs.core.windows.net/x"\n',
    ):
        matches = kb.detect(literal)
        hit = [m for m in matches if m.rule_id == "scos:external-cloud-path"]
        assert hit, f"cloud-path not detected for: {literal!r}"
        assert hit[0].ewi_code == "SPRKCNTPY5400", hit[0].ewi_code
        assert hit[0].status_class == "IO", hit[0].status_class

