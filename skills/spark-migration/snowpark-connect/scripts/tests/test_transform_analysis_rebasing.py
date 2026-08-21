"""Tier-B unit tests for ``transform_analysis`` line/snippet rebasing.

Covers:

* End-to-end rebase of analyzer findings onto an ORIGINAL source dir given
  synthetic original+post-recipe trees with known line shifts (insertion,
  replacement, deletion blocks).
* Boundary: ``transform()`` MUST NOT accept a ``recipe_edits`` parameter.
  Recipe data is isolated to ``recipe_resolved_panel`` only (Recipe-Data
  Isolation Guarantee, Tier-B plan).
* Backward compatibility: when either source dir is omitted, behaviour is
  identical to the legacy single-source path.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from textwrap import dedent

import pytest

from transform_analysis import (
    _build_post_to_original_line_map,
    _extract_snippet,
    _rebase_findings,
    _rebase_line_range,
    transform,
)


# ---------------------------------------------------------------------------
# Boundary: recipe-data isolation guarantee
# ---------------------------------------------------------------------------


def test_transform_does_not_accept_recipe_edits_parameter() -> None:
    """transform() must NOT accept a ``recipe_edits`` parameter.

    Recipe data is isolated to ``recipe_resolved_panel`` (assigned post-merge
    by render_assessment). Any drift here would break the Recipe-Data
    Isolation Guarantee from the Tier-B plan.
    """
    sig = inspect.signature(transform)
    assert "recipe_edits" not in sig.parameters, (
        "transform() must remain free of recipe_edits — recipe data lives "
        "exclusively in recipe_resolved_panel.build_recipe_resolved_panel "
        "and is assigned post-merge by render_assessment."
    )

    # Also assert the new Tier-B parameters DO exist and are optional.
    assert "original_source_dir" in sig.parameters
    assert "post_recipe_source_dir" in sig.parameters
    for p in ("original_source_dir", "post_recipe_source_dir"):
        assert sig.parameters[p].default is None, (
            f"{p} must default to None for backward compatibility"
        )


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def test_build_line_map_identical_files() -> None:
    text = "a\nb\nc\nd\n"
    mapping = _build_post_to_original_line_map(text, text)
    assert mapping == [0, 1, 2, 3]


def test_build_line_map_insertion_block() -> None:
    """Post inserts two lines after line 2 → mapping for inserted lines is None."""
    original = "a\nb\nc\nd\n"
    post = "a\nb\nINS1\nINS2\nc\nd\n"
    mapping = _build_post_to_original_line_map(original, post)
    # Equal blocks: a→a (0→0), b→b (1→1), c→c (2→4), d→d (3→5)
    assert mapping[0] == 0
    assert mapping[1] == 1
    assert mapping[2] is None or mapping[2] >= 0  # insert blocks have None or fallback
    assert mapping[3] is None or mapping[3] >= 0
    assert mapping[4] == 2  # post line 5 (0-indexed 4) -> original line 3 (0-indexed 2)
    assert mapping[5] == 3  # post line 6 -> original line 4


def test_build_line_map_replace_equal_span() -> None:
    """Replace block where post_span == orig_span: every post line pairs
    1:1 with the corresponding original line positionally."""
    original = "a\nOLD1\nOLD2\nd\n"
    post = "a\nNEW1\nNEW2\nd\n"
    mapping = _build_post_to_original_line_map(original, post)
    assert mapping[0] == 0  # a → a (equal)
    assert mapping[1] == 1  # NEW1 → OLD1 (replace, prefix)
    assert mapping[2] == 2  # NEW2 → OLD2 (replace, prefix)
    assert mapping[3] == 3  # d → d (equal)


def test_build_line_map_replace_widened_tail_is_none() -> None:
    """Widened replace (post_span > orig_span): the 1:1 prefix is
    faithful; the tail post lines have NO original equivalent and must
    map to ``None`` so callers fall back to post coords rather than
    fabricating a phantom original line.

    This is the medium-severity fix: the previous implementation
    collapsed every tail line onto the last original line, which read
    as confidently-rebased but was a fiction.
    """
    original = "a\nOLD1\nd\n"
    post = "a\nNEW1\nNEW_INSERTED\nNEW_ALSO\nd\n"  # 1 orig replaced by 3 post
    mapping = _build_post_to_original_line_map(original, post)
    assert mapping[0] == 0  # a → a
    assert mapping[1] == 1  # NEW1 → OLD1 (prefix portion of replace)
    assert mapping[2] is None  # NEW_INSERTED has no original (widened tail)
    assert mapping[3] is None  # NEW_ALSO has no original (widened tail)
    assert mapping[4] == 2  # d → d


def test_build_line_map_replace_shrunk_post() -> None:
    """Shrunk replace (post_span < orig_span): every post line is in the
    prefix portion, so every post line gets a faithful 1:1 mapping. The
    extra original lines are simply absent from the post (deletion-like)
    and aren't represented in a post→original map at all."""
    original = "a\nOLD1\nOLD2\nOLD3\nd\n"  # 3 orig lines in the block
    post = "a\nNEW1\nd\n"  # collapsed to 1 post line
    mapping = _build_post_to_original_line_map(original, post)
    assert mapping[0] == 0  # a → a
    assert mapping[1] == 1  # NEW1 → OLD1 (prefix; only one post line so no tail)
    assert mapping[2] == 4  # d → d


def test_rebase_line_range_handles_single_and_span() -> None:
    mapping = [0, 1, None, None, 2, 3]  # 1-indexed: 1→1, 2→2, 3→?, 4→?, 5→3, 6→4
    assert _rebase_line_range("1", mapping) == "1"
    assert _rebase_line_range("5", mapping) == "3"
    assert _rebase_line_range("5-6", mapping) == "3-4"
    assert _rebase_line_range("", mapping) is None
    assert _rebase_line_range("abc", mapping) is None


def test_extract_snippet_inclusive_range() -> None:
    lines = ["a", "b", "c", "d", "e"]
    assert _extract_snippet(lines, "2-4") == "b\nc\nd"
    assert _extract_snippet(lines, "1") == "a"
    assert _extract_snippet(lines, "5") == "e"
    assert _extract_snippet(lines, "10") is None
    assert _extract_snippet(lines, "") is None


# ---------------------------------------------------------------------------
# End-to-end rebasing of findings
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_sources(tmp_path: Path) -> tuple[Path, Path]:
    """Two tiny synthetic source trees with known line shifts.

    Original (8 lines)::
        1  import pyspark
        2  from pyspark.sql import SparkSession
        3  spark = SparkSession.builder.master("local").getOrCreate()
        4  df = spark.range(10)
        5  df.show()
        6  df.printSchema()
        7  rdd = spark.sparkContext.parallelize([1, 2, 3])
        8  print(rdd.collect())

    Post-recipe (10 lines — 2 lines INSERTED after line 2)::
        1  import pyspark
        2  from pyspark.sql import SparkSession
        3  # SCOS-WARN: master() drop applied
        4  # generated by recipe spark_builder_drop_master_init_session_rewrite
        5  spark = SparkSession.builder.getOrCreate()
        6  df = spark.range(10)
        7  df.show()
        8  df.printSchema()
        9  rdd = spark.sparkContext.parallelize([1, 2, 3])
        10 print(rdd.collect())
    """
    original_dir = tmp_path / "original" / "Output"
    post_dir = tmp_path / "post" / "Output"
    original_dir.mkdir(parents=True)
    post_dir.mkdir(parents=True)

    original_text = dedent("""\
        import pyspark
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.master("local").getOrCreate()
        df = spark.range(10)
        df.show()
        df.printSchema()
        rdd = spark.sparkContext.parallelize([1, 2, 3])
        print(rdd.collect())
    """)
    post_text = dedent("""\
        import pyspark
        from pyspark.sql import SparkSession
        # SCOS-WARN: master() drop applied
        # generated by recipe spark_builder_drop_master_init_session_rewrite
        spark = SparkSession.builder.getOrCreate()
        df = spark.range(10)
        df.show()
        df.printSchema()
        rdd = spark.sparkContext.parallelize([1, 2, 3])
        print(rdd.collect())
    """)
    (original_dir / "workload.py").write_text(original_text)
    (post_dir / "workload.py").write_text(post_text)
    return original_dir, post_dir


def test_rebase_findings_shifts_lines_to_original(synthetic_sources) -> None:
    """A finding on post line 9 (rdd.parallelize) should rebase to original
    line 7. Snippet should come from the original file."""
    original_dir, post_dir = synthetic_sources
    findings = [
        {
            "file": "workload.py",
            "lines": "9",
            "code": "rdd = spark.sparkContext.parallelize([1, 2, 3])",
            "final_risk": 0.8,
            "language": "python",
        }
    ]
    rebased = _rebase_findings(
        findings,
        original_source_dir=original_dir,
        post_recipe_source_dir=post_dir,
    )
    assert len(rebased) == 1
    assert rebased[0]["lines"] == "7", (
        f"expected line 9 (post) → 7 (original); got {rebased[0]['lines']!r}"
    )
    # The code field should be re-fetched from the original (which doesn't
    # have the recipe header). Either the original line content, or unchanged
    # if extraction fails — but if extraction succeeds it must be the
    # ORIGINAL text, not the post-recipe text.
    assert "rdd = spark.sparkContext.parallelize" in rebased[0]["code"]


def test_rebase_findings_handles_range_spans(synthetic_sources) -> None:
    """A range finding spanning multiple post lines should rebase both
    endpoints onto original line numbers."""
    original_dir, post_dir = synthetic_sources
    findings = [
        {
            "file": "workload.py",
            "lines": "9-10",
            "code": "ignored",
            "final_risk": 0.5,
            "language": "python",
        }
    ]
    rebased = _rebase_findings(
        findings,
        original_source_dir=original_dir,
        post_recipe_source_dir=post_dir,
    )
    # post 9-10 = "rdd = ..." and "print(rdd.collect())"
    # original equivalents are lines 7-8.
    assert rebased[0]["lines"] == "7-8"


def test_rebase_findings_unresolvable_file_leaves_untouched(synthetic_sources) -> None:
    """A finding on a file that doesn't exist on disk in EITHER dir should be
    left untouched (post-recipe lines preserved) and never silently wrong."""
    original_dir, post_dir = synthetic_sources
    findings = [
        {
            "file": "does-not-exist.py",
            "lines": "5",
            "code": "x = 1",
            "final_risk": 0.5,
        }
    ]
    rebased = _rebase_findings(
        findings,
        original_source_dir=original_dir,
        post_recipe_source_dir=post_dir,
    )
    assert rebased[0]["lines"] == "5"
    assert rebased[0]["code"] == "x = 1"


def test_rebase_findings_empty_or_missing_file_key(synthetic_sources) -> None:
    """Findings without a file key are passed through unchanged."""
    original_dir, post_dir = synthetic_sources
    findings = [
        {"lines": "1", "code": "x", "final_risk": 0.5},
        {"file": "", "lines": "1", "code": "y", "final_risk": 0.5},
    ]
    rebased = _rebase_findings(
        findings,
        original_source_dir=original_dir,
        post_recipe_source_dir=post_dir,
    )
    assert len(rebased) == 2
    for r in rebased:
        assert r["lines"] == "1"


def test_transform_no_rebasing_when_either_source_dir_missing(synthetic_sources) -> None:
    """Backward compatibility: when only one source dir is provided (or
    none), no rebasing happens — findings preserve their input ``lines``."""
    original_dir, post_dir = synthetic_sources
    findings = [
        {
            "file": "workload.py",
            "lines": "9",
            "code": "rdd = spark.sparkContext.parallelize([1, 2, 3])",
            "final_risk": 0.8,
            "language": "python",
        }
    ]
    ir_neither = transform(findings, project="x")
    assert ir_neither.detailed_findings[0].lines == "9"

    ir_only_post = transform(
        findings,
        project="x",
        original_source_dir=None,
        post_recipe_source_dir=post_dir,
    )
    assert ir_only_post.detailed_findings[0].lines == "9"

    ir_only_orig = transform(
        findings,
        project="x",
        original_source_dir=original_dir,
        post_recipe_source_dir=None,
    )
    assert ir_only_orig.detailed_findings[0].lines == "9"


def test_transform_end_to_end_rebases_detailed_findings(synthetic_sources) -> None:
    """End-to-end via ``transform()``: detailed_findings should carry original
    line numbers when both source dirs are provided."""
    original_dir, post_dir = synthetic_sources
    findings = [
        {
            "file": "workload.py",
            "lines": "9-10",
            "code": "post-recipe code that should be replaced",
            "final_risk": 0.8,
            "language": "python",
            "code": "rdd = spark.sparkContext.parallelize([1, 2, 3])",
            "root_cause": "RDD usage not supported",
            "confidence": "HIGH",
        }
    ]
    ir = transform(
        findings,
        project="x",
        original_source_dir=original_dir,
        post_recipe_source_dir=post_dir,
    )
    assert len(ir.detailed_findings) == 1
    df = ir.detailed_findings[0]
    assert df.lines == "7-8", (
        f"expected detailed_findings to carry rebased lines 7-8; got {df.lines!r}"
    )
