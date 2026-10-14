"""Parity guard for the EWI taxonomy after removing ewi_refine_rules.json /
ewi_status_rules.json.

The two prose-regex fallback rule-sets were deleted; their code-assignment role
is now carried by kb_rules.json (deterministic detection). This test locks in
that the taxonomy codes those fallbacks used to emit are each producible by at
least one catalog rule, so a regression that drops coverage is caught.

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_ewi_parity.py
"""
from __future__ import annotations

import json
from pathlib import Path

_KB = Path(__file__).resolve().parents[1] / "data" / "kb_rules.json"
_DATA = Path(__file__).resolve().parents[1] / "data" / "python"


def _codes():
    return {r.get("ewi_code") for r in json.loads(_KB.read_text()) if r.get("ewi_code")}


def test_fallback_rule_files_are_removed():
    assert not (_DATA / "ewi_refine_rules.json").exists()
    assert not (_DATA / "ewi_status_rules.json").exists()


def test_taxonomy_codes_are_covered_by_kb_rules():
    # Codes that previously depended on the prose refine fallback must now be
    # emitted by >= 1 kb_rule (detection-time), incl. the 8 formerly-uncovered.
    required = {
        "SPRKCNTPY5100",  # datetime / timezone
        "SPRKCNTPY5300",  # aggregation / window
        "SPRKCNTPY5450",  # subquery / lateral
        "SPRKCNTPY5500",  # regex / string
        "SPRKCNTPY5900",  # hive partitioning
        "SPRKCNTPY6000",  # jdbc source / sink
        "SPRKCNTPY6300",  # observability / debugging
    }
    have = _codes()
    missing = sorted(required - have)
    assert not missing, f"taxonomy codes with no kb_rule coverage: {missing}"


def test_no_prose_fallback_symbols_in_reporter():
    src = (Path(__file__).resolve().parents[1] / "generate_scos_reports.py").read_text()
    for sym in ("load_refine_rules", "load_status_rules", "def refine_code", "def derive_status"):
        assert sym not in src, f"reporter still references removed symbol: {sym}"
