"""Unit tests for ``preprocess_recipes._resolve_output_lines`` and the
``analyze_pyspark._recipe_edits_for_block`` consumer.

The two functions form a contract:
  * Phase 0.5 calls ``_resolve_output_lines`` after all recipes finish so
    that each edit dict gains an ``output_line`` key reflecting the line in
    the *final* output file.
  * Phase 1 calls ``_recipe_edits_for_block`` which prefers ``output_line``
    over the legacy ``src_line``.

Before ``output_line`` was introduced, ``src_line`` drifted ahead of the
final output line whenever recipes prepended SCOS comment lines.  The drift
caused ``_recipe_edits_for_block`` to return empty results (no recipe context
injected into the LLM prompt), so every issue received ``kind="llm_only"``
even for lines that Phase 0.5 had explicitly annotated.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from preprocess_recipes import _resolve_output_lines  # noqa: E402
from analyze_pyspark import _recipe_edits_for_block  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _edit(recipe_id: str, src_line: int) -> dict:
    return {"recipe_id": recipe_id, "src_line": src_line, "output_line_anchor": f"{recipe_id}:{src_line}:deadbeef"}


# ---------------------------------------------------------------------------
# _resolve_output_lines — prepended standalone comment
# ---------------------------------------------------------------------------

def test_prepended_comment_resolves_to_next_line() -> None:
    """The most common case: recipe inserts a ``# SCOS-TODO:`` line before code."""
    content = textwrap.dedent("""\
        x = 1
        # SCOS-TODO: [CODE] my_recipe: some message
        y = 2
        z = 3
    """)
    edits = [_edit("my_recipe", src_line=2)]
    _resolve_output_lines(content, edits)
    assert edits[0]["output_line"] == 3


def test_inline_comment_resolves_to_same_line() -> None:
    """Recipes that annotate inline (``pass  # SCOS-WARN: recipe:``) point to
    the code line itself, not the next line."""
    content = textwrap.dedent("""\
        x = 1
        pass  # SCOS-WARN: inline_recipe: dropped chain
        z = 3
    """)
    edits = [_edit("inline_recipe", src_line=1)]
    _resolve_output_lines(content, edits)
    assert edits[0]["output_line"] == 2


def test_stacked_scos_comments_same_recipe_deduplicated() -> None:
    """When a recipe prepends two SCOS comments above the same statement
    (e.g. SCOS: + SCOS-WARN:), both resolve to the same code line.  Only
    one edit entry exists so the dedup must not drop it."""
    content = textwrap.dedent("""\
        # SCOS: [CODE] my_recipe: first comment
        # SCOS-WARN: my_recipe: second comment
        result = spark.sql("SELECT 1")
    """)
    edits = [_edit("my_recipe", src_line=1)]
    _resolve_output_lines(content, edits)
    assert edits[0]["output_line"] == 3


def test_multiple_edits_same_recipe_positional_match() -> None:
    """Nth marker occurrence maps to the Nth edit (sorted by src_line)."""
    content = textwrap.dedent("""\
        # SCOS-TODO: [CODE] prop_recipe: bind at line 5
        self.sc = spark
        x = 1
        # SCOS-TODO: [CODE] prop_recipe: bind at line 9
        self.sc2 = spark
    """)
    edits = [
        _edit("prop_recipe", src_line=5),
        _edit("prop_recipe", src_line=9),
    ]
    _resolve_output_lines(content, edits)
    assert edits[0]["output_line"] == 2
    assert edits[1]["output_line"] == 5


def test_multiple_recipes_resolve_independently() -> None:
    """Two different recipes each have their own comment markers; they should
    resolve to the correct lines without interfering."""
    content = textwrap.dedent("""\
        # SCOS: [A] recipe_alpha: message
        x = alpha_code()
        # SCOS-TODO: [B] recipe_beta: message
        y = beta_code()
    """)
    edits = [
        _edit("recipe_alpha", src_line=1),
        _edit("recipe_beta", src_line=3),
    ]
    _resolve_output_lines(content, edits)
    assert edits[0]["output_line"] == 2  # recipe_alpha → line 2
    assert edits[1]["output_line"] == 4  # recipe_beta  → line 4


def test_stacked_comments_from_two_recipes_above_same_code() -> None:
    """Two different recipes both annotate the same code line.  Each recipe's
    group has one edit; both should resolve to the shared code line."""
    content = textwrap.dedent("""\
        # SCOS: [A] alpha_recipe: comment
        # SCOS-TODO: [B] beta_recipe: comment
        the_code = do_something()
    """)
    alpha = _edit("alpha_recipe", src_line=1)
    beta = _edit("beta_recipe", src_line=1)
    _resolve_output_lines(content, [alpha, beta])
    assert alpha["output_line"] == 3
    assert beta["output_line"] == 3


def test_marker_not_found_leaves_no_output_line() -> None:
    """If a recipe left no SCOS comment (e.g. a silent rewrite), the edit
    dict must not gain an ``output_line`` key."""
    content = "x = 1\ny = 2\n"
    edits = [_edit("silent_recipe", src_line=2)]
    _resolve_output_lines(content, edits)
    assert "output_line" not in edits[0]


def test_empty_edits_is_noop() -> None:
    _resolve_output_lines("x = 1\n", [])  # must not raise


def test_src_line_order_determines_positional_match() -> None:
    """Edits passed in reverse src_line order must still be matched correctly
    because the function sorts by src_line before matching."""
    content = textwrap.dedent("""\
        # SCOS: [A] r: first
        a = 1
        # SCOS: [A] r: second
        b = 2
    """)
    # Intentionally pass in reverse order.
    edits = [
        _edit("r", src_line=3),  # second in file
        _edit("r", src_line=1),  # first in file
    ]
    _resolve_output_lines(content, edits)
    # After sorting by src_line: [src_line=1 → line 2, src_line=3 → line 4]
    by_src = {e["src_line"]: e["output_line"] for e in edits}
    assert by_src[1] == 2
    assert by_src[3] == 4


# ---------------------------------------------------------------------------
# _recipe_edits_for_block — output_line preferred over src_line
# ---------------------------------------------------------------------------

def test_block_match_uses_output_line() -> None:
    """With ``output_line`` present the match uses it, ignoring ``src_line``."""
    edit = {**_edit("my_recipe", src_line=79), "output_line": 81}
    # Block is [81, 81]: output_line matches, src_line (79) would not.
    assert _recipe_edits_for_block([edit], 81, 81) == [edit]


def test_block_no_match_when_output_line_outside_range() -> None:
    """Even if src_line falls in the range, output_line outside means no match.
    This is the fix for the original bug: src_line=79 drifted; output was 81."""
    edit = {**_edit("my_recipe", src_line=79), "output_line": 81}
    # Range [70, 79] contains src_line=79 but NOT output_line=81.
    assert _recipe_edits_for_block([edit], 70, 79) == []


def test_block_legacy_fallback_to_src_line() -> None:
    """Entries without ``output_line`` (written before the fix) fall back to
    ``src_line`` so that old migration_state.json files still get partial
    recipe context."""
    edit = _edit("legacy_recipe", src_line=42)
    assert "output_line" not in edit
    assert _recipe_edits_for_block([edit], 40, 45) == [edit]
    assert _recipe_edits_for_block([edit], 50, 60) == []


def test_block_empty_edits_returns_empty() -> None:
    assert _recipe_edits_for_block([], 1, 10) == []


def test_block_multiple_edits_only_matching_returned() -> None:
    e1 = {**_edit("r", src_line=10), "output_line": 20}
    e2 = {**_edit("r", src_line=30), "output_line": 40}
    e3 = {**_edit("r", src_line=50), "output_line": 60}
    result = _recipe_edits_for_block([e1, e2, e3], 38, 42)
    assert result == [e2]


def test_block_reproduces_kipawa_main_py_scenario() -> None:
    """Regression: the exact values from the Kipawa SCOS conversion.

    sparkcontext_property_fallback_rewrite fired at src_line=79 and 91, but
    Phase 0.5 prepended comments that pushed the code to output lines 81 and
    94.  Before the fix, both blocks got kind=llm_only because the old code
    checked ``src_line in [81,81]`` (False) and ``src_line in [94,94]``
    (False).  With output_line the checks become ``81 in [81,81]`` (True) and
    ``94 in [94,94]`` (True).
    """
    edits = [
        {**_edit("sparkcontext_property_fallback_rewrite", 79), "output_line": 81},
        {**_edit("sparkcontext_property_fallback_rewrite", 91), "output_line": 94},
    ]
    assert _recipe_edits_for_block(edits, 81, 81) == [edits[0]]
    assert _recipe_edits_for_block(edits, 94, 94) == [edits[1]]
    # The old behaviour: src_line-only lookup would have found nothing.
    old_edits = [
        _edit("sparkcontext_property_fallback_rewrite", 79),
        _edit("sparkcontext_property_fallback_rewrite", 91),
    ]
    assert _recipe_edits_for_block(old_edits, 81, 81) == []
    assert _recipe_edits_for_block(old_edits, 94, 94) == []
