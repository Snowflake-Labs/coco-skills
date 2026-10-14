"""Unit tests for ``glue_filter_apply_null_semantics_annotate`` (Glue recipe G5).

The defining property under test: this recipe NEVER rewrites code. Every
positive case asserts the code portion is byte-identical to the input.

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_glue_filter_apply_null_semantics_annotate.py
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "glue_filter_apply_null_semantics_annotate"


def _apply(source: str):
    source = textwrap.dedent(source).lstrip("\n")
    return _common.load_recipe_module(str(_RECIPES_DIR / _NAME)).apply(source, file="t.py")


def _code(source: str) -> str:
    """Strip comment lines so assertions target code, not SCOS markers."""
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


_NEG = 'Upsert = Filter.apply(frame=DyF, f=lambda row: row["op"] != "d")\n'
_POS = 'Delete = Filter.apply(frame=DyF, f=lambda row: row["op"] == "d")\n'


# ---------------------------------------------------------------------------
# never rewrites -- code byte-identical
# ---------------------------------------------------------------------------
def _assert_annotated_not_rewritten(src: str):
    src = textwrap.dedent(src).lstrip("\n")
    res = _apply(src)
    assert _code(res.source) == src.rstrip("\n"), res.source
    assert "SCOS: TODO" in res.source
    assert f"[SPRKCNTPY3604-Error] {_NAME}:" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")
    return res


def test_negated_predicate_code_is_byte_identical():
    _assert_annotated_not_rewritten(_NEG)


def test_positive_predicate_code_is_byte_identical():
    _assert_annotated_not_rewritten(_POS)


def test_no_rewrite_artifacts_ever_emitted():
    for src in (_NEG, _POS):
        out = _apply(src).source
        code = _code(out)
        assert "df.filter(" not in code
        assert "isNull()" not in code
        assert "Filter.apply(" in code  # original call intact


def test_positional_form_annotated_not_rewritten():
    _assert_annotated_not_rewritten(
        'Out = Filter.apply(DyF, lambda row: row["op"] != "d")\n'
    )


def test_dotted_receiver_annotated_not_rewritten():
    _assert_annotated_not_rewritten(
        'Out = awsglue.transforms.Filter.apply(frame=DyF, f=lambda row: row["op"] != "d")\n'
    )


# ---------------------------------------------------------------------------
# the trap is explained precisely
# ---------------------------------------------------------------------------
def test_comment_explains_the_actual_trap():
    out = _apply(_NEG).source
    assert "PYTHON" in out and "three-valued logic" in out
    assert "SILENTLY DROPS ROWS" in out
    # canonical CDC example
    assert 'row["op"] != "d"' in out
    assert "KEEPS null-op rows" in out
    assert "DROPPED" in out


def test_comment_states_the_asymmetry_rule():
    out = _apply(_NEG).source
    assert "isNull() guard" in out
    assert "positive predicates" in out


# ---------------------------------------------------------------------------
# negated vs positive produce DIFFERENT comment text
# ---------------------------------------------------------------------------
def test_negated_and_positive_comments_differ():
    neg = _apply(_NEG).source
    pos = _apply(_POS).source
    assert neg != pos
    assert "NEGATED" in neg and "NEGATED" not in pos
    assert "ALREADY CORRECT" in pos and "ALREADY CORRECT" not in neg


def test_negated_emits_concrete_suggested_rewrite():
    out = _apply(_NEG).source
    assert 'Suggested rewrite: df.filter(F.col("op").isNull() | (F.col("op") != "d"))' in out
    # the suggestion lives in a comment, not in the code
    assert "isNull()" not in _code(out)


def test_positive_says_direct_port_is_correct_and_needs_no_guard():
    out = _apply(_POS).source
    assert "ALREADY CORRECT" in out
    assert "No isNull() guard is needed" in out
    assert "Suggested rewrite" not in out


def test_not_negation_is_classified_negated():
    out = _apply('Out = Filter.apply(frame=DyF, f=lambda row: not row["flag"])\n').source
    assert "NEGATED" in out


def test_tilde_negation_is_classified_negated():
    out = _apply('Out = Filter.apply(frame=DyF, f=lambda row: ~row["flag"])\n').source
    assert "NEGATED" in out


def test_greater_than_is_classified_positive():
    out = _apply('Out = Filter.apply(frame=DyF, f=lambda row: row["n"] > 5)\n').source
    assert "ALREADY CORRECT" in out


def test_in_comparison_is_classified_positive():
    out = _apply(
        'Out = Filter.apply(frame=DyF, f=lambda row: row["op"] in ("i", "u"))\n'
    ).source
    assert "ALREADY CORRECT" in out


def test_named_function_predicate_is_unclassified():
    out = _apply("Out = Filter.apply(frame=DyF, f=my_predicate)\n").source
    assert "compound or" in out
    assert "NEGATED" not in out and "ALREADY CORRECT" not in out
    # generic guidance still carries the asymmetry rule
    assert "isNull() guard" in out


def test_call_body_predicate_is_unclassified():
    out = _apply(
        'Out = Filter.apply(frame=DyF, f=lambda row: is_live(row["op"]))\n'
    ).source
    assert "compound or" in out


def test_negated_without_recoverable_column_omits_suggestion():
    out = _apply("Out = Filter.apply(frame=DyF, f=lambda row: row.op != other)\n").source
    assert "NEGATED" in out
    assert "Suggested rewrite" not in out


# ---------------------------------------------------------------------------
# must not fire
# ---------------------------------------------------------------------------
def test_benign_pyspark_untouched():
    src = 'df = spark.read.table("t")\ndf.select("a").show()\n'
    res = _apply(src)
    assert res.source == src and res.edits == []


def test_plain_pyspark_filter_untouched():
    src = 'out = df.filter(F.col("op") != "d")\n'
    res = _apply(src)
    assert res.source == src and res.edits == []


def test_applymapping_apply_untouched():
    src = 'Out = ApplyMapping.apply(frame=DyF, mappings=[("a", "string", "b", "string")])\n'
    res = _apply(src)
    assert res.source == src and res.edits == []


def test_unrelated_apply_untouched():
    src = "out = pdf.apply(fn, axis=1)\n"
    res = _apply(src)
    assert res.source == src and res.edits == []


# ---------------------------------------------------------------------------
# multiple sites / idempotency
# ---------------------------------------------------------------------------
def test_both_cdc_branches_annotated():
    src = _NEG + _POS
    res = _apply(src)
    assert _code(res.source) == src.rstrip("\n")
    assert len(res.edits) == 2
    assert "NEGATED" in res.source and "ALREADY CORRECT" in res.source


def test_idempotent():
    for src in (_NEG, _POS, _NEG + _POS):
        once = _apply(src).source
        twice = _apply(once).source
        assert once == twice
        assert _apply(once).edits == []
