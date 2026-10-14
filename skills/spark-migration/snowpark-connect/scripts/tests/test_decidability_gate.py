"""Tests for the analyzer's decidability gate (analyze_pyspark.py).

The gate emits structurally-certain trigger findings without an LLM
round-trip, while keeping behavioral / fuzzy / recipe-touched blocks on the
LLM path. These tests exercise the partition logic directly with synthetic
block items (no Snowflake session, no real Cortex call).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from analyze_pyspark import (  # noqa: E402
    _block_is_fully_decidable,
    _build_decidable_result,
    _build_deferred_result,
    _curated_fix,
    _partition_decidable_blocks,
)


@dataclass
class _Block:
    line_start: int = 10
    line_end: int = 12
    code: str = "out = df.rdd.map(lambda r: r)"
    cell_id: int | None = None
    language: str = "python"


def _candidate(*, decidable: bool, score: float = 0.95, fuzzy: bool = False) -> dict:
    return {
        "match_kind": "rag_similar" if fuzzy else "trigger_exact",
        "score": score,
        "root_cause": "RDD API is unsupported in SCOS.",
        "decidable": decidable,
    }


def _item(*, candidates: list[dict], scos_issues: list | None = None) -> dict:
    return {
        "block": _Block(),
        "matching_patterns": candidates,
        "scos_issues": scos_issues or [],
    }


# --- _block_is_fully_decidable ---------------------------------------------


def test_all_decidable_exact_triggers_is_decidable() -> None:
    item = _item(candidates=[_candidate(decidable=True), _candidate(decidable=True)])
    assert _block_is_fully_decidable(item) is True


def test_low_severity_decidable_still_decidable() -> None:
    # Severity (score) is irrelevant to decidability — a low-severity
    # unsupported API is still a guaranteed true positive.
    item = _item(candidates=[_candidate(decidable=True, score=0.45)])
    assert _block_is_fully_decidable(item) is True


def test_any_nondecidable_candidate_blocks_bypass() -> None:
    item = _item(candidates=[_candidate(decidable=True), _candidate(decidable=False)])
    assert _block_is_fully_decidable(item) is False


def test_fuzzy_candidate_never_decidable() -> None:
    item = _item(candidates=[_candidate(decidable=True, fuzzy=True)])
    assert _block_is_fully_decidable(item) is False


def test_soft_rule_issue_blocks_bypass() -> None:
    item = _item(candidates=[_candidate(decidable=True)], scos_issues=[{"risk": 0.8}])
    assert _block_is_fully_decidable(item) is False


def test_no_candidates_not_decidable() -> None:
    assert _block_is_fully_decidable(_item(candidates=[])) is False


# --- _build_decidable_result -----------------------------------------------


def test_build_decidable_result_shape() -> None:
    res = _build_decidable_result(Path("/tmp/f.py"), _item(candidates=[_candidate(decidable=True)]), 0.3)
    assert res is not None
    assert res["final_risk"] == 0.95
    assert res["confidence"] == "HIGH"
    assert res["kind"] == "standard"
    assert res["detected_by"] == "decidable_trigger"
    assert res["fix"] is None
    assert res["lines"] == "10-12"
    assert res["language"] == "python"


def test_build_decidable_result_below_threshold_dropped() -> None:
    res = _build_decidable_result(
        Path("/tmp/f.py"), _item(candidates=[_candidate(decidable=True, score=0.2)]), 0.3
    )
    assert res is None


# --- _partition_decidable_blocks -------------------------------------------


def test_partition_emits_decidable_and_defers_rest() -> None:
    # Defer-adjudication is the ONLY mode: a decidable block is emitted as a
    # decidable_trigger; an uncertain block is emitted as needs_adjudication.
    # Nothing is ever left "remaining" (there is no LLM batch path).
    decidable = _item(candidates=[_candidate(decidable=True)])
    behavioral = _item(candidates=[_candidate(decidable=False)])
    emitted, remaining = _partition_decidable_blocks(
        [decidable, behavioral], file_recipe_edits=[], file_path=Path("/tmp/f.py"), risk_threshold=0.3
    )
    assert remaining == []
    by = {e["detected_by"]: e for e in emitted}
    assert by["decidable_trigger"]["detected_by"] == "decidable_trigger"
    assert by["deferred_to_fixer"]["kind"] == "needs_adjudication"


def test_partition_recipe_touched_block_is_deferred() -> None:
    # A decidable block that a Phase 0.5 recipe touched is kept UNCERTAIN so the
    # adjudicator/fixer can still propose a suggested_fixer_action — it is now
    # emitted as needs_adjudication (deferred), never left remaining.
    decidable = _item(candidates=[_candidate(decidable=True)])  # block at lines 10-12
    recipe_edits = [{"recipe_id": "some_annotate", "src_line": 11}]
    emitted, remaining = _partition_decidable_blocks(
        [decidable], file_recipe_edits=recipe_edits, file_path=Path("/tmp/f.py"), risk_threshold=0.3
    )
    assert remaining == []
    assert len(emitted) == 1 and emitted[0]["kind"] == "needs_adjudication"


def test_partition_defer_mode_emits_needs_adjudication() -> None:
    # Uncertain blocks are emitted as needs_adjudication deterministically (no
    # LLM), so `remaining` is empty.
    behavioral = _item(candidates=[_candidate(decidable=False)])
    emitted, remaining = _partition_decidable_blocks(
        [behavioral], file_recipe_edits=[], file_path=Path("/tmp/f.py"), risk_threshold=0.3
    )
    assert remaining == []
    assert len(emitted) == 1 and emitted[0]["kind"] == "needs_adjudication"


# --- the curated remedy must survive the DEFERRED path ----------------------
# A rule's `fix` is the only place a concrete rewrite lives. When it is dropped,
# the fixer sees a diagnosis with no instruction — which reads as "the catalog
# has no remedy" and produces a TODO where a rewrite was available.


def _candidate_with_fix(**kw) -> dict:
    c = _candidate(**kw)
    c["test_name"] = "parity:some-rule#some-case"
    c["additional_notes"] = "Fix: rewrite X as Y | disposition: fix; severity: high"
    return c


def test_curated_fix_extracted_from_additional_notes() -> None:
    assert _curated_fix(_candidate_with_fix(decidable=True)) == "rewrite X as Y"
    assert _curated_fix({"additional_notes": "disposition: annotate"}) is None
    assert _curated_fix({}) is None


def test_deferred_result_keeps_the_curated_fix_and_rule_id() -> None:
    item = _item(candidates=[_candidate_with_fix(decidable=True)])
    result = _build_deferred_result(Path("/tmp/f.py"), item)
    assert result["fix"] == "rewrite X as Y"
    cand = result["deferred_candidates"][0]
    assert cand["rule_id"] == "parity:some-rule#some-case", (
        "trigger candidates carry the rule id as `test_name`; a null rule_id "
        "makes the finding untraceable"
    )
    assert cand["fix"] == "rewrite X as Y"


def test_deferred_result_fix_is_none_when_the_rule_has_no_remedy() -> None:
    item = _item(candidates=[_candidate(decidable=False)])
    assert _build_deferred_result(Path("/tmp/f.py"), item)["fix"] is None
