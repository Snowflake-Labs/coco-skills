"""Unit tests for the ``udf_backed_builtin_perf_annotate`` LibCST recipe.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_udf_backed_builtin_perf_recipe.py
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

# Make ``scripts/recipes`` importable so we can load the recipe module
# directly (same pattern preprocess_recipes.py uses).
_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
sys.path.insert(0, str(_RECIPES_DIR))

from _common import load_recipe_module  # noqa: E402

_RECIPE_DIR = _RECIPES_DIR / "udf_backed_builtin_perf_annotate"
_recipe = load_recipe_module(_RECIPE_DIR)

_MARKER = "SCOS: udf_backed_builtin_perf_annotate"


def _apply(src: str):
    src = textwrap.dedent(src).lstrip("\n")
    res = _recipe.apply(src, file="t.py")
    return res.source, res.edits


# --------------------------------------------------------------------------
# Positive cases — recipe must annotate (but never alter the code line)
# --------------------------------------------------------------------------


def test_annotates_attribute_crc32() -> None:
    new, edits = _apply(
        """
        out = df.withColumn("h", F.crc32(col("x")))
        """
    )
    assert _MARKER in new
    assert "crc32()" in new
    # Code line preserved verbatim (annotate-only).
    assert 'out = df.withColumn("h", F.crc32(col("x")))' in new
    assert len(edits) == 1


def test_annotates_bare_name_format_number() -> None:
    new, edits = _apply(
        """
        out = df.select(format_number("amt", 2))
        """
    )
    assert _MARKER in new
    assert "format_number()" in new
    assert len(edits) == 1


def test_annotates_each_targeted_function() -> None:
    for fn, call in [
        ("format_string", 'format_string("%s", col("a"))'),
        ("printf", 'printf("%d", col("n"))'),
        ("from_csv", 'from_csv(col("c"), "a INT")'),
        ("map_concat", "map_concat(m1, m2)"),
        ("map_from_arrays", "map_from_arrays(keys, vals)"),
    ]:
        new, edits = _apply(f"x = {call}\n")
        assert _MARKER in new, f"{fn} not annotated"
        assert f"{fn}()" in new
        assert len(edits) == 1


def test_multiple_targets_on_one_line_single_comment() -> None:
    new, edits = _apply(
        """
        x = F.crc32(map_concat(a, b))
        """
    )
    # One comment for the statement, listing both functions.
    assert new.count(_MARKER) == 1
    assert "crc32()" in new and "map_concat()" in new
    assert len(edits) == 1


# --------------------------------------------------------------------------
# Negative cases — must NOT annotate
# --------------------------------------------------------------------------


def test_stdlib_crc32_not_flagged() -> None:
    new, edits = _apply(
        """
        import zlib
        checksum = zlib.crc32(payload)
        """
    )
    assert _MARKER not in new
    assert edits == []


def test_native_functions_not_flagged() -> None:
    # These are native in SCOS (commonly assumed slow but aren't).
    for call in [
        "xxhash64(col('x'))",
        "map_values(m)",
        "map_filter(m, f)",
        "transform_keys(m, f)",
        "percentile_approx(col('x'), 0.5)",
        "array_repeat(col('x'), 3)",
    ]:
        new, edits = _apply(f"y = {call}\n")
        assert _MARKER not in new, f"{call} should not be flagged"
        assert edits == []


def test_conditional_functions_not_flagged() -> None:
    # UDF-backed only for specific arg shapes -> left to the LLM analyzer.
    for call in [
        "bit_count(col('x'))",
        "encode(col('x'), 'utf-8')",
        "transform(col('arr'), lambda x: x + 1)",
    ]:
        new, edits = _apply(f"z = {call}\n")
        assert _MARKER not in new, f"{call} should not be flagged"
        assert edits == []


# --------------------------------------------------------------------------
# Idempotency — re-running must not double-annotate
# --------------------------------------------------------------------------


def test_idempotent() -> None:
    src = 'out = F.crc32(col("x"))\n'
    once, e1 = _apply(src)
    twice, e2 = _recipe.apply(once, file="t.py").source, None
    assert once.count(_MARKER) == 1
    assert twice.count(_MARKER) == 1
    assert len(e1) == 1
    # Second pass produces no new edits.
    assert _recipe.apply(once, file="t.py").edits == []
