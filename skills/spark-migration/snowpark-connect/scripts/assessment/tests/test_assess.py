"""Unit tests for the migration-readiness HTML report generator.

Covers the Phase-4 reporter pipeline (``analysis.json`` + workload dir → IR → HTML):
  * ``assess_ir`` helpers (severity / readiness bucketing, merge semantics).
  * ``transform_analysis`` (analysis.json → IR).
  * ``scan_codebase`` (workload dir → IR).
  * ``render_assessment.build_assessment`` (merge + audit trail).
  * ``adapters.prototype_v1`` (HTML structure checks).

These tests assert PROTOTYPE STRUCTURE — every tab pane, every section
heading, every JS hook from the reference prototype must survive into the
generated HTML even when the IR is sparse.

Run from the ``snowpark-connect/`` directory::

    uv run --project . pytest scripts/assessment/tests/test_assess.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from adapters import prototype_v1
from assess_ir import (
    Assessment,
    AssessmentMetadata,
    CodeChurnEstimate,
    CompatibilitySummary,
    ComplexPatternRow,
    DependencyGraph,
    DetailedFinding,
    FileCompatibilityRow,
    FileTypeRow,
    GraphNode,
    IssueRow,
    MigrationWave,
    ProjectType,
    SectionNarratives,
    UnresolvedDataEdge,
    UnresolvedDynamicImport,
    WorkloadClassification,
    WorkloadSummary,
    readiness_from_issues,
    severity_from_risk,
)
from render_assessment import build_assessment
from scan_codebase import scan as scan_codebase
from transform_analysis import _longest_common_parent
from transform_analysis import transform as transform_analysis

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "migrate-single-file"
_ANALYSIS_JSON = _FIXTURE_DIR / "analysis.json"


# ---------------------------------------------------------------------------
# assess_ir helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "risk,expected",
    [
        (0.95, "High"),
        (0.70, "High"),
        (0.69, "Medium"),
        (0.30, "Medium"),
        (0.29, "Low"),
        (0.0, "Low"),
    ],
)
def test_severity_from_risk_buckets(risk: float, expected: str) -> None:
    assert severity_from_risk(risk) == expected


@pytest.mark.parametrize(
    "issues,expected",
    [(0, "High"), (1, "Medium"), (2, "Medium"), (3, "Low"), (10, "Low")],
)
def test_readiness_from_issues_buckets(issues: int, expected: str) -> None:
    assert readiness_from_issues(issues) == expected


def test_assessment_merge_combines_partial_irs() -> None:
    """Codebase IR + analyzer IR merge into a single rich IR."""
    codebase = Assessment(
        metadata=AssessmentMetadata(project="t", mode="CODEBASE"),
        workload=WorkloadSummary(files_scanned=10, lines_of_code=500, library_imports=20),
        file_types=[FileTypeRow(extension="Python", count=10, lines=500)],
        files=[
            FileCompatibilityRow(path="a.py", name="a.py", lines=100, issues=0, status="High"),
        ],
    )
    analyzer = Assessment(
        metadata=AssessmentMetadata(project="t", mode="ANALYSIS_JSON"),
        workload=WorkloadSummary(changes_needed=3),
        files=[
            FileCompatibilityRow(path="a.py", name="a.py", issues=2, status="Medium"),
        ],
        issues=[IssueRow(code="X", description="d", count=2)],
        detailed_findings=[
            DetailedFinding(file="a.py", name="a.py", lines="1-1", code="x", severity="Medium"),
        ],
    )
    merged = codebase.merge(analyzer)

    assert merged.metadata.mode == "HYBRID"
    assert merged.workload.files_scanned == 10
    assert merged.workload.changes_needed == 3
    # Code churn is recomputed from the merged per-file readiness distribution:
    # the single file is Medium (Light Refactor).
    assert merged.code_churn.category == "Medium"
    assert merged.code_churn.files_light_refactor == 1

    # Detailed findings are analyzer-only and must survive the merge.
    assert len(merged.detailed_findings) == 1
    assert merged.detailed_findings[0].file == "a.py"

    # File got merged + status re-derived from issues, not optimistically left at High.
    by_name = {f.name: f for f in merged.files}
    assert by_name["a.py"].issues == 2
    assert by_name["a.py"].lines == 100
    assert by_name["a.py"].status == "Medium"

    # Issues + file_types both made it through.
    assert len(merged.issues) == 1
    assert merged.issues[0].count == 2
    assert len(merged.file_types) == 1


# ---------------------------------------------------------------------------
# transform_analysis (analysis.json → IR)
# ---------------------------------------------------------------------------


def test_transform_analysis_real_fixture() -> None:
    findings = json.loads(_ANALYSIS_JSON.read_text())
    ir = transform_analysis(findings, project="tiny-workload")

    assert ir.metadata.project == "tiny-workload"
    assert ir.metadata.mode == "ANALYSIS_JSON"
    assert ir.workload.changes_needed == len(findings) == 5
    # Code churn is a category (no numeric score) with per-bucket file counts.
    assert ir.code_churn.category in ("High", "Medium", "Low")
    assert (ir.code_churn.files_ready + ir.code_churn.files_light_refactor
            + ir.code_churn.files_active_refactor) == len(ir.files)

    # Issues rolled up (deduped); files surfaced.
    assert ir.issues, "expected at least one rolled-up issue row"
    assert ir.files, "expected per-file rows"
    assert sum(i.count for i in ir.issues) == len(findings)

    # Detailed drill-down keeps one entry per (filtered) finding with the raw
    # fields preserved.
    assert len(ir.detailed_findings) == len(findings)
    d = ir.detailed_findings[0]
    assert d.code, "detailed finding should carry the code snippet"
    assert d.root_cause, "detailed finding should carry the root cause"
    assert d.severity in ("High", "Medium", "Low")
    # Sorted by file then descending risk within each file.
    by_file: dict[str, list[float]] = {}
    for f in ir.detailed_findings:
        by_file.setdefault(f.file, []).append(f.final_risk)
    for risks in by_file.values():
        assert risks == sorted(risks, reverse=True)

    # CONTENT correctness — the fixture is a single file; the row must carry
    # the real basename, not a blank / "." placeholder.
    assert len(ir.files) == 1
    only = ir.files[0]
    assert only.name == "tiny_workload.py"
    assert only.path == "tiny_workload.py"
    assert only.issues == len(findings)

    # The template joins detailed_findings to file rows by ``path``. Assert that
    # join is real (not silently relying on the basename fallback): every
    # finding's ``file`` key must equal a file row's ``path``.
    file_paths = {f.path for f in ir.files}
    assert file_paths == {"tiny_workload.py"}
    assert all(d.file in file_paths for d in ir.detailed_findings), (
        f"every finding must join to a file row by path; "
        f"finding files={sorted({d.file for d in ir.detailed_findings})}, "
        f"file paths={sorted(file_paths)}"
    )

    # Each issue row records the relative file path(s) it was found in, and
    # those paths must correspond to real per-file rows.
    for issue in ir.issues:
        assert issue.files, f"issue {issue.description!r} should list its files"
        for fp in issue.files:
            assert fp in file_paths, f"issue file {fp!r} not in per-file rows {file_paths}"


def test_transform_analysis_file_rows_never_blank_single_file() -> None:
    """Regression: a single-file analysis.json must NOT collapse the workload
    root onto the file itself, which produced a blank-name, ``"."``-path row."""
    findings = [
        {
            "file": "/Users/me/proj/src/job.py", "lines": "10-12",
            "final_risk": 0.8, "root_cause": "rc", "explanation": "e",
            "fix": None, "confidence": "HIGH", "language": "python",
        },
        {
            "file": "/Users/me/proj/src/job.py", "lines": "20-20",
            "final_risk": 0.4, "root_cause": "rc2", "explanation": "e2",
            "fix": None, "confidence": "MEDIUM", "language": "python",
        },
    ]
    ir = transform_analysis(findings, project="t")  # no explicit workload_root
    assert len(ir.files) == 1
    assert ir.files[0].name == "job.py"
    assert ir.files[0].path == "job.py"
    assert ir.files[0].path not in (".", "")
    assert all(d.file == "job.py" for d in ir.detailed_findings)


@pytest.mark.parametrize(
    "paths,expected",
    [
        # Single file → parent directory, never the file itself.
        (["/a/b/c.py"], "/a/b"),
        # Multiple files in the same dir → that dir.
        (["/a/b/c.py", "/a/b/d.py"], "/a/b"),
        # Nested → common ancestor dir.
        (["/a/b/c.py", "/a/b/sub/e.py"], "/a/b"),
        # No common prefix → None.
        (["/a/x.py", "/z/y.py"], "/"),
    ],
)
def test_longest_common_parent_returns_directory(paths, expected) -> None:
    result = _longest_common_parent([Path(p) for p in paths])
    assert str(result) == expected
    # The result is never one of the input file paths.
    assert str(result) not in paths


def test_transform_analysis_detailed_findings_exclude_filtered() -> None:
    """Self-veto and Partial-Migration meta-warnings must be dropped from the
    detailed drill-down too, not just the aggregated counts."""
    findings = [
        {
            "file": "/r/a.py", "lines": "1-1", "final_risk": 0.5,
            "root_cause": "real", "explanation": "e", "fix": None,
            "confidence": "MEDIUM", "language": "python",
        },
        # Self-veto — dropped.
        {
            "file": "/r/a.py", "lines": "2-2", "final_risk": 0.0,
            "root_cause": "noise", "explanation": "doesn't apply", "fix": None,
            "confidence": "HIGH", "language": "python",
        },
        # Meta-warning — dropped.
        {
            "file": "/r/b.py", "code": "SPRKCNTPY0099", "lines": "1",
            "final_risk": 0.9, "root_cause": "fallback",
            "category": "Partial Migration", "fix": "manual review",
        },
    ]
    ir = transform_analysis(findings, project="t", workload_root="/r")
    assert len(ir.detailed_findings) == 1
    assert ir.detailed_findings[0].file == "a.py"
    assert ir.detailed_findings[0].root_cause == "real"


def test_transform_analysis_issue_files_are_relative_and_grouped() -> None:
    """An issue seen in two files lists both relative paths; distinct issues
    keep their own file sets."""
    findings = [
        {"file": "/r/src/a.py", "lines": "1-1", "final_risk": 0.8,
         "root_cause": "shared cause", "explanation": "e", "fix": None,
         "confidence": "HIGH", "language": "python"},
        {"file": "/r/src/b.py", "lines": "2-2", "final_risk": 0.8,
         "root_cause": "shared cause", "explanation": "e", "fix": None,
         "confidence": "HIGH", "language": "python"},
        {"file": "/r/src/a.py", "lines": "3-3", "final_risk": 0.4,
         "root_cause": "other cause", "explanation": "e", "fix": None,
         "confidence": "MEDIUM", "language": "python"},
    ]
    ir = transform_analysis(findings, project="t", workload_root="/r")
    by_desc = {i.description: i for i in ir.issues}
    assert by_desc["shared cause"].files == ["src/a.py", "src/b.py"]
    assert by_desc["other cause"].files == ["src/a.py"]


def test_transform_analysis_empty() -> None:
    ir = transform_analysis([], project="empty")
    assert ir.workload.changes_needed == 0
    assert ir.issues == []
    assert ir.workload.executive_summary == ""
    # No files → default (empty) code-churn estimate, no numbers.
    assert ir.code_churn.category == "High"
    assert ir.code_churn.files_ready == 0
    assert ir.code_churn.description == ""


def test_transform_analysis_relativizes_paths() -> None:
    findings = [
        {
            "file": "/abs/root/sub/foo.py",
            "lines": "1-1",
            "code": "x",
            "final_risk": 0.5,
            "root_cause": "rc",
            "explanation": "e",
            "fix": None,
            "confidence": "MEDIUM",
            "language": "python",
        }
    ]
    ir = transform_analysis(findings, project="t", workload_root="/abs/root")
    assert ir.files[0].path == "sub/foo.py"


def test_transform_analysis_coalesces_mixed_path_shapes_for_same_file() -> None:
    """Regression: Phase 1 (absolute path) and Phase 2a fallback (basename)
    findings about the same physical file must collapse into one row.

    Before the fix, ``files_agg`` bucketed by raw ``file`` string, so:
        "/abs/root/Output/tiny.py"  (Phase 1, 6 findings)
        "tiny.py"                   (Phase 2a fallback, 1 finding)
    produced ``len(files_agg) == 2``, which propagated to:
      * executive summary "across 2 files"
      * readiness description "across 2 file(s)"
      * compatibility summary "1 of 2 code files"
    while the deterministic codebase scan correctly reported 1.
    """
    findings = [
        {
            "file": "/abs/root/Output/tiny.py",
            "lines": "1-1", "final_risk": 0.5,
            "root_cause": "rc", "explanation": "e", "fix": None,
            "confidence": "HIGH", "language": "python",
        },
        {
            "file": "/abs/root/Output/tiny.py",
            "lines": "2-2", "final_risk": 0.7,
            "root_cause": "rc2", "explanation": "e2", "fix": None,
            "confidence": "HIGH", "language": "python",
        },
        # Real Phase-2a finding with the basename-only path shape.
        {
            "file": "tiny.py",
            "lines": "3-3", "final_risk": 0.4,
            "root_cause": "rc3", "explanation": "e3", "fix": None,
            "confidence": "MEDIUM", "language": "python",
        },
    ]
    ir = transform_analysis(findings, project="t", workload_root="/abs/root/Output")
    assert len(ir.files) == 1, (
        f"expected mixed-path findings to coalesce into 1 file, got "
        f"{[f.path for f in ir.files]}"
    )
    assert ir.files[0].issues == 3, "all 3 findings should attribute to the one file"
    assert "<strong>1</strong> file" in ir.workload.executive_summary, (
        f"executive summary should say '1 file' (singular), got: "
        f"{ir.workload.executive_summary}"
    )


def test_transform_analysis_filters_analyzer_self_vetoes() -> None:
    """Findings with ``final_risk == 0.0`` and ``confidence == "HIGH"`` are
    the analyzer's self-veto: "I matched a pattern but I'm sure it doesn't
    apply." Counting them inflates per-file Issues on Spark-free files
    where the analyzer accidentally matched a string literal like
    ``"sparkApp"`` in a dict assignment.
    """
    findings = [
        # Real finding — should be counted.
        {
            "file": "/r/a.py", "lines": "1-1", "final_risk": 0.5,
            "root_cause": "real", "explanation": "e", "fix": None,
            "confidence": "MEDIUM", "language": "python",
        },
        # Self-veto — should be dropped.
        {
            "file": "/r/a.py", "lines": "2-2", "final_risk": 0.0,
            "root_cause": "noise", "explanation": "doesn't apply", "fix": None,
            "confidence": "HIGH", "language": "python",
        },
    ]
    ir = transform_analysis(findings, project="t", workload_root="/r")
    assert ir.files[0].issues == 1, (
        f"self-veto finding should be filtered, expected 1 issue, got "
        f"{ir.files[0].issues}"
    )


def test_transform_analysis_filters_partial_migration_meta_warnings() -> None:
    """``SPRKCNTPY0099`` / ``Partial Migration`` findings are workflow
    advisories ("LLM fixer skipped this file; deterministic fallback
    applied"), not Spark API compatibility findings. Counting them
    pollutes downstream numbers — ``changes_needed`` and
    per-file ``issues`` on Spark-free files like ``__init__.py``.
    """
    findings = [
        # Real Spark finding — should be counted.
        {
            "file": "/r/a.py", "lines": "10-10", "final_risk": 0.6,
            "root_cause": "real", "explanation": "e", "fix": None,
            "confidence": "MEDIUM", "language": "python",
        },
        # Meta-warning by code — should be dropped.
        {
            "file": "/r/a.py", "code": "SPRKCNTPY0099", "lines": "1",
            "final_risk": 0.9, "root_cause": "fallback",
            "category": "Partial Migration", "fix": "manual review",
        },
        # Meta-warning by category alone — should be dropped.
        {
            "file": "/r/b.py", "lines": "1",
            "final_risk": 0.9, "root_cause": "fallback2",
            "category": "Partial Migration",
            "fix": "review b",
        },
    ]
    ir = transform_analysis(findings, project="t", workload_root="/r")
    a_row = next(f for f in ir.files if f.name == "a.py")
    assert a_row.issues == 1, "meta-warning on a.py must NOT count toward issues"
    assert all(f.name != "b.py" for f in ir.files), (
        "b.py had only a meta-warning — it should not appear as a finding file"
    )
    assert ir.workload.changes_needed == 1, (
        f"changes_needed must exclude meta-warnings, got {ir.workload.changes_needed}"
    )


def test_transform_analysis_filters_adjudicator_dismissed_findings() -> None:
    """``resolution == "safe"`` means Phase 1.1 already confirmed this is a
    false positive (e.g. ``numpy.array()`` colliding with a KB anchor for
    ``pyspark.sql.functions.array``). Dismiss doesn't change ``kind``, so
    before this filter it rendered identically to a confirmed issue.
    """
    findings = [
        # Real, confirmed finding — should be counted.
        {
            "file": "/r/a.py", "lines": "1-1", "final_risk": 0.5,
            "root_cause": "real", "explanation": "e", "fix": None,
            "confidence": "MEDIUM", "language": "python",
        },
        # Adjudicator-dismissed false positive — should be dropped even
        # though it has a real final_risk and HIGH confidence.
        {
            "file": "/r/a.py", "lines": "2-2", "final_risk": 0.4,
            "root_cause": "numpy.array collides with F.array KB anchor",
            "explanation": "e", "fix": None, "confidence": "HIGH",
            "language": "python", "kind": "needs_adjudication",
            "resolution": "safe",
            "resolution_reason": "file imports numpy as np; unrelated to pyspark.sql.functions.array",
        },
        # Dismissed on a file with no other findings — must not appear at all.
        {
            "file": "/r/b.py", "lines": "1-1", "final_risk": 0.9,
            "root_cause": "math.ceil collides with F.ceil KB anchor",
            "explanation": "e", "fix": None, "confidence": "HIGH",
            "language": "python", "kind": "needs_adjudication",
            "resolution": "safe",
            "resolution_reason": "single-arg math.ceil, not the two-arg F.ceil overload",
        },
    ]
    ir = transform_analysis(findings, project="t", workload_root="/r")
    a_row = next(f for f in ir.files if f.name == "a.py")
    assert a_row.issues == 1, (
        f"dismissed finding on a.py must NOT count toward issues, got {a_row.issues}"
    )
    assert all(f.name != "b.py" for f in ir.files), (
        "b.py had only a dismissed finding — it should not appear as a finding file"
    )
    assert ir.workload.changes_needed == 1, (
        f"changes_needed must exclude adjudicator-dismissed findings, got {ir.workload.changes_needed}"
    )
    assert ir.compatibility.not_supported_usages == 1, (
        f"not_supported_usages must exclude adjudicator-dismissed findings, "
        f"got {ir.compatibility.not_supported_usages}"
    )


def test_transform_analysis_keeps_confirmed_adjudicated_findings() -> None:
    """Only ``resolution == "safe"`` is excluded — a confirmed finding
    (``kind`` flipped to ``standard``, no ``resolution`` set) still counts."""
    findings = [
        {
            "file": "/r/a.py", "lines": "1-1", "final_risk": 0.7,
            "root_cause": "confirmed real divergence", "explanation": "e",
            "fix": "rewrite x", "confidence": "HIGH", "language": "python",
            "kind": "standard", "adjudicated": True,
        },
    ]
    ir = transform_analysis(findings, project="t", workload_root="/r")
    assert ir.files[0].issues == 1, (
        f"confirmed adjudicated finding must still be counted, got {ir.files[0].issues}"
    )


def test_transform_analysis_resolution_check_is_case_insensitive() -> None:
    """resolution="SAFE"/"Safe" must be excluded the same as "safe"."""
    findings = [
        {
            "file": "/r/a.py", "lines": "1-1", "final_risk": 0.5,
            "root_cause": "r", "explanation": "e", "fix": None,
            "confidence": "HIGH", "language": "python",
            "kind": "needs_adjudication", "resolution": "SAFE",
        },
    ]
    ir = transform_analysis(findings, project="t", workload_root="/r")
    assert ir.files == [], "resolution='SAFE' (any casing) must be excluded"




def test_transform_analysis_keeps_distinct_subdir_files() -> None:
    """Defensive: same basename under different subdirs must NOT coalesce
    when a workload_root keeps them distinguishable."""
    findings = [
        {
            "file": "/abs/root/a/foo.py", "lines": "1-1", "final_risk": 0.3,
            "root_cause": "rc", "explanation": "e", "fix": None,
            "confidence": "MEDIUM", "language": "python",
        },
        {
            "file": "/abs/root/b/foo.py", "lines": "1-1", "final_risk": 0.3,
            "root_cause": "rc", "explanation": "e", "fix": None,
            "confidence": "MEDIUM", "language": "python",
        },
    ]
    ir = transform_analysis(findings, project="t", workload_root="/abs/root")
    paths = sorted(f.path for f in ir.files)
    assert paths == ["a/foo.py", "b/foo.py"], (
        f"distinct subdirs should remain distinct; got {paths}"
    )


def test_transform_analysis_categorizes_findings() -> None:
    """RDD-flavored findings land in the SparkContext/RDD migration category."""
    findings = [
        {
            "file": "a.py", "lines": "1-1", "code": "rdd.collect()",
            "final_risk": 0.8, "root_cause": "RDD usage", "explanation": "e",
            "fix": None, "confidence": "HIGH", "language": "python",
        },
    ]
    ir = transform_analysis(findings, project="t")
    categories = {c.name for c in ir.migration_categories}
    assert "RDD / SparkContext" in categories


# ---------------------------------------------------------------------------
# scan_codebase (workload dir → IR)
# ---------------------------------------------------------------------------


def test_scan_codebase_populates_structural_fields(tmp_path: Path) -> None:
    """Even a tiny synthetic project should fill the prototype's structural sections."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "import pandas\n"
        "from pyspark.sql import SparkSession\n"
        "df = spark.read.parquet('s3://bucket/data')\n"
        "rdd = sc.parallelize([1, 2, 3])\n"
        "df.write.parquet('s3://bucket/out')\n"
    )
    (tmp_path / "src" / "lib.py").write_text(
        "import numpy\n"
        "def helper(): return 1\n"
    )
    (tmp_path / "README.md").write_text("hello\n")

    ir = scan_codebase(tmp_path, project="tiny")

    assert ir.metadata.mode == "CODEBASE"
    assert ir.workload.files_scanned >= 2
    assert ir.workload.lines_of_code > 0
    assert ir.workload.library_imports >= 2  # pandas + numpy + pyspark

    # File-type tile populated.
    assert ir.file_types, "expected file types"
    types = {row.extension for row in ir.file_types}
    assert "Python" in types or "Markdown" in types

    # Patterns detected.
    pattern_names = {p.pattern for p in ir.complex_patterns}
    assert "RDD Operations" in pattern_names

    # Data sources detected from URL scheme + reader.
    src_formats = {s.format for s in ir.data_sources}
    assert any(s in src_formats for s in ("S3", "Parquet"))

    # Migration waves built — at least one wave with at least one file.
    assert ir.migration_waves, "expected at least one wave"
    assert any(w.files for w in ir.migration_waves)


# ---------------------------------------------------------------------------
# render_assessment (CLI)
# ---------------------------------------------------------------------------


def test_build_assessment_fails_fast_when_analysis_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError, match="Phase 1 of the migrate skill"):
        build_assessment(project="t", analysis_json=missing)


def test_build_assessment_rejects_non_array(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "an array"}')
    with pytest.raises(ValueError, match="JSON array"):
        build_assessment(project="t", analysis_json=bad)


def test_build_assessment_records_audit_trail() -> None:
    ir = build_assessment(project="t", analysis_json=_ANALYSIS_JSON)
    assert ir.metadata.analysis_json_path == str(_ANALYSIS_JSON.resolve())


def test_build_assessment_merges_when_workload_dir_provided(tmp_path: Path) -> None:
    """When both sources are present, the merged IR carries codebase + analyzer fields."""
    # Build a workload dir that mirrors the analysis.json's file path
    src = tmp_path / "tiny_workload.py"
    src.write_text("import pandas\nx = 1\n")
    ir = build_assessment(
        project="tiny-workload",
        analysis_json=_ANALYSIS_JSON,
        workload_dir=tmp_path,
    )
    assert ir.metadata.mode == "HYBRID"
    assert ir.workload.files_scanned >= 1
    assert ir.workload.changes_needed >= 5
    assert ir.file_types  # from codebase
    assert ir.issues      # from analyzer


# ---------------------------------------------------------------------------
# adapter: prototype_v1 (labels + polarity)
# ---------------------------------------------------------------------------


def test_prototype_disambiguates_readiness_vs_risk_labels() -> None:
    """The same word 'High' must not read as self-conflicting in one row.

    Per-file readiness 'High' is good (green ``Ready`` badge); a per-finding
    severity 'High' indicates work to do but no longer reads as a verdict —
    the badge now says 'Resolution Planned' (yellow) and the cell is paired
    with a separate 'Analyzer confidence' tag so users don't confuse the
    analyzer's certainty with a risk score. Asserted against the v1 adapter
    (the v0 prototype is deprecated and still emits the old "Risk: High"
    wording).
    """
    ir = Assessment(
        files=[
            FileCompatibilityRow(path="src/main.py", name="main.py",
                                 technology="Python", lines=10, issues=1, status="High"),
        ],
        detailed_findings=[
            DetailedFinding(file="src/main.py", name="main.py", lines="1-1",
                            code="x", severity="High", final_risk=0.9,
                            confidence="HIGH", root_cause="rc", explanation="e"),
        ],
    )
    html = prototype_v1.render(ir)
    # The scary "Risk: High" framing has been replaced by a calmer plan label.
    assert "Risk: High" not in html
    assert "Resolution Planned" in html
    # Confidence is framed as analyzer certainty, separate from risk.
    assert "Analyzer confidence: HIGH" in html


# ---------------------------------------------------------------------------
# Tone of voice: no red, no "Risk: High", no scary "Low" readiness
# ---------------------------------------------------------------------------


def _no_red_badge(html: str) -> bool:
    """A rendered report must never emit ``class="badge ... badge-red"``.

    We retired red from the readiness palette to avoid the "scary verdict"
    tone. Any new code path that builds a badge has to route through
    ``severity_badge`` / ``readiness_badge`` / ``color_badge`` (none of
    which can return ``badge-red`` any more).
    """
    return 'class="badge badge-red"' not in html


def test_severity_badge_never_returns_red() -> None:
    """The v1 severity → badge mapping has retired red and mirrors readiness.

    The severity badge (used by the expanded finding cards in Detailed
    Compatibility) must agree with the parent file row's readiness badge:

      * ``High``   → orange  (mirrors readiness ``Low``  / Active Refactor)
      * ``Medium`` → yellow  (mirrors readiness ``Medium`` / Light Refactor)
      * ``Low``    → green   (mirrors readiness ``High`` / Ready)

    Red is only kept as a defensive identity (``Red`` → orange) so legacy
    IRs don't crash the renderer. This palette lives in the v1 adapter;
    v0 is deprecated and keeps its original red-for-High palette.
    """
    from adapters.prototype_v1 import _severity_badge

    for level in ("High", "Medium", "Low", "Green", "Yellow", "Red", "Unknown"):
        assert _severity_badge(level) != "badge-red", level
    assert _severity_badge("High") == "badge-orange"
    assert _severity_badge("Medium") == "badge-yellow"
    assert _severity_badge("Low") == "badge-green"


def test_readiness_badge_never_returns_red() -> None:
    """The v1 readiness → badge mapping has retired red.

    ``High`` is green, ``Medium`` is yellow, and ``Low`` has its own
    orange track so the file table, dependency graph, and prerequisites
    chart all show the same color for the "Active Refactor" bucket.
    """
    from adapters.prototype_v1 import _readiness_badge

    for level in ("High", "Medium", "Low", "Unknown"):
        assert _readiness_badge(level) != "badge-red", level
    assert _readiness_badge("High") == "badge-green"
    assert _readiness_badge("Medium") == "badge-yellow"
    assert _readiness_badge("Low") == "badge-orange"


def test_risk_label_swaps_scary_high_for_plan_language() -> None:
    """A ``High`` severity finding renders as ``"Resolution Planned"``."""
    from adapters.prototype_v1 import _risk_label

    assert _risk_label("High") == "Resolution Planned"
    assert _risk_label("Medium") == "Adjustments Planned"
    assert _risk_label("Low") == "Minor"
    # Anything we don't recognize is passed through untouched.
    assert _risk_label("WeirdValue") == "WeirdValue"


def test_readiness_label_swaps_scary_low_for_plan_language() -> None:
    """``Low`` readiness renders as ``"Active Refactor"`` rather than a verdict.

    The three labels must read as clearly different scales of work — earlier
    drafts (``"Some Updates"`` vs ``"Updates Planned"``) collapsed visually
    and reviewers couldn't tell Medium from Low at a glance.
    """
    from adapters.prototype_v1 import _readiness_label

    assert _readiness_label("High") == "Ready"
    assert _readiness_label("Medium") == "Light Refactor"
    assert _readiness_label("Low") == "Active Refactor"
    # Medium and Low must remain visibly distinct phrases.
    assert _readiness_label("Medium") != _readiness_label("Low")


def test_prototype_v1_renders_resolution_planned_for_high_risk_findings() -> None:
    """The per-file drill-down badge must say ``Resolution Planned``, not
    ``Risk: High``, for a ``severity="High"`` finding."""
    ir = Assessment(
        files=[
            FileCompatibilityRow(path="src/main.py", name="main.py",
                                 technology="Python", lines=10, issues=1, status="Low"),
        ],
        detailed_findings=[
            DetailedFinding(file="src/main.py", name="main.py", lines="1-1",
                            code="x", severity="High", final_risk=0.9,
                            confidence="HIGH", root_cause="rc", explanation="e"),
        ],
    )
    html = prototype_v1.render(ir)
    assert "Risk: High" not in html
    assert "Resolution Planned" in html


def test_prototype_v1_renders_scoped_refactor_label_for_low_readiness_files() -> None:
    """The per-file readiness column must say ``Active Refactor`` for a
    ``status="Low"`` file, not the scary ``Low`` label.

    Medium files render as ``Light Refactor`` so the two yellow rows still
    look meaningfully different to a reviewer.
    """
    ir = Assessment(
        files=[
            FileCompatibilityRow(path="src/x.py", name="x.py", technology="Python",
                                 lines=20, issues=5, status="Low"),
            FileCompatibilityRow(path="src/y.py", name="y.py", technology="Python",
                                 lines=20, issues=2, status="Medium"),
            FileCompatibilityRow(path="src/z.py", name="z.py", technology="Python",
                                 lines=20, issues=0, status="High"),
        ],
    )
    html = prototype_v1.render(ir)
    assert ">Active Refactor</span>" in html
    assert ">Light Refactor</span>" in html
    assert ">Ready</span>" in html
    # Filter dropdown matches the visible labels.
    assert "<option value=\"low\">Active Refactor</option>" in html
    assert "<option value=\"medium\">Light Refactor</option>" in html
    assert "<option value=\"high\">Ready</option>" in html
    # The Active Refactor badge picks up its own orange track (distinct from
    # Light Refactor's yellow) so reviewers can scan Medium vs Low at a glance.
    assert 'class="badge badge-orange">Active Refactor' in html
    assert 'class="badge badge-yellow">Light Refactor' in html


def test_prototype_v1_finding_and_readiness_badges_are_not_red() -> None:
    """The two badges we explicitly de-scared must not render as red.

    Scope is narrow on purpose: the report intentionally keeps red on the
    overview's Migration Approach Stage 3 banner, the File Compatibility
    Breakdown card/bar/legend for unsupported APIs, and the third-party
    library "No" badge. Those are factual signals reviewers expect. The two
    badges that previously sounded like verdicts — the per-finding
    "Risk: High" pill and the per-file "Low" readiness pill — must instead
    share the orange "Active Refactor" track so the expanded view agrees
    with its parent row.
    """
    ir = Assessment(
        files=[
            FileCompatibilityRow(path="src/main.py", name="main.py",
                                 technology="Python", lines=10, issues=5, status="Low"),
        ],
        detailed_findings=[
            DetailedFinding(file="src/main.py", name="main.py", lines="1-1",
                            code="x", severity="High", final_risk=0.9,
                            confidence="HIGH", root_cause="rc", explanation="e"),
        ],
    )
    html = prototype_v1.render(ir)
    # The per-finding "Resolution Planned" badge is orange, mirroring the
    # parent row's "Active Refactor" badge (severity High ↔ readiness Low).
    assert 'class="badge badge-orange">Resolution Planned' in html
    # The per-file "Active Refactor" badge is orange, not red — and matching
    # color across the file table, dep-graph nodes, and prerequisites chart.
    assert 'class="badge badge-orange">Active Refactor' in html


def test_migration_stage_three_remains_red_on_overview() -> None:
    """Stage 3 on the Migration Approach overview is intentionally red.

    The overview's stage banners are cosmetic stage markers, not severity
    verdicts on individual findings. Keeping Stage 3 red preserves the
    "this stage is the heavy lift" signal without it reading as a hazard
    on a specific file or pattern.
    """
    from assess_ir import render_migration_stages

    stages = render_migration_stages(high=3, medium=2, low=1)
    third = stages[2]
    assert third.color == "red"
    assert third.name == "Stage 3: Complex Refactoring"

    ir = Assessment(migration_stages=stages)
    html = prototype_v1.render(ir)
    assert 'migration-stage-banner stage-red' in html


def _overview_section(html: str) -> str:
    """Extract the overview tab content from the rendered HTML."""
    start = html.find('id="overview"')
    end = html.find('<!-- PAGE: ADDITIONAL DISCOVERY -->', start)
    return html[start:end] if start != -1 and end != -1 else html[start:]


def test_prototype_v1_overview_has_migration_process_at_top() -> None:
    """The overview tab must open with a 'Migration Process' panel containing
    the phase-journey stepper, placed before the source/target charts.

    The phase-journey was previously only on the Migration Plan tab. After the
    redesign it also appears at the top of the Overview tab so the reader
    immediately understands where this report fits in the end-to-end flow.
    """
    html = prototype_v1.render(Assessment())
    overview = _overview_section(html)

    assert "Migration Process" in overview
    assert "phase-journey" in overview
    assert "phase-step current" in overview
    assert "You are here" in overview

    # The stepper must appear before the tiles and data-distribution charts.
    assert overview.index("Migration Process") < overview.index("Files scanned")
    assert overview.index("Migration Process") < overview.index("data-distribution")


def test_prototype_v1_overview_approach_section_renamed() -> None:
    """The bottom section on the overview tab is labelled 'Approach', not
    'Migration approach'.  The phase-journey has been pulled out to its own
    'Migration Process' panel, leaving the stage banners under 'Approach'.
    """
    html = prototype_v1.render(Assessment())
    overview = _overview_section(html)

    assert "<h2>Approach</h2>" in overview
    assert "<h2>Migration approach</h2>" not in overview


def test_prototype_v1_overview_charts_wrapped_in_overview_row() -> None:
    """The source/target and code-deps panels share an overview-row flex
    container so they sit side-by-side on wider viewports."""
    html = prototype_v1.render(Assessment())
    overview = _overview_section(html)
    assert 'class="overview-row"' in overview


def test_prototype_v1_file_compat_breakdown_keeps_red_for_unsupported() -> None:
    """The File Compatibility Breakdown card/bar/legend for unsupported APIs
    is allowed to render red — these are factual API counts, not verdicts."""
    ir = Assessment(
        files=[
            FileCompatibilityRow(path="a.py", name="a.py", lines=10, issues=0, status="High"),
            FileCompatibilityRow(path="b.py", name="b.py", lines=10, issues=5, status="Low"),
        ],
    )
    html = prototype_v1.render(ir)
    # The legend keeps its red swatch / bar segment / icon.
    assert 'compat-legend-swatch red' in html
    assert 'compat-bar-segment red' in html
    assert 'compat-card-icon--red' in html


def test_prototype_v1_per_file_expanded_findings_match_parent_row_color() -> None:
    """In Detailed Compatibility, the expanded finding badges must agree
    with the parent file row's readiness badge.

    Concretely:

      * A file with readiness ``Low`` ("Active Refactor", orange) expanded
        to a ``High``-severity finding ("Resolution Planned") — both must
        render orange.
      * A file with readiness ``Medium`` ("Light Refactor", yellow)
        expanded to a ``Medium``-severity finding ("Adjustments Planned")
        — both must render yellow.
      * A file with readiness ``High`` ("Ready", green) expanded to a
        ``Low``-severity finding ("Minor") — both must render green.

    Previously, the per-finding pills always landed on yellow regardless
    of the parent row's color, so an orange "Active Refactor" row would
    open up to yellow "Resolution Planned" / "Adjustments Planned" pills
    and the eye couldn't tie the rollup to the detail.
    """
    ir = Assessment(
        files=[
            FileCompatibilityRow(path="src/low.py", name="low.py",
                                 technology="Python", lines=10, issues=5, status="Low"),
            FileCompatibilityRow(path="src/med.py", name="med.py",
                                 technology="Python", lines=10, issues=2, status="Medium"),
            FileCompatibilityRow(path="src/high.py", name="high.py",
                                 technology="Python", lines=10, issues=0, status="High"),
        ],
        detailed_findings=[
            DetailedFinding(file="src/low.py", name="low.py", lines="1-1",
                            code="x", severity="High", final_risk=0.9,
                            confidence="HIGH", root_cause="rc", explanation="e"),
            DetailedFinding(file="src/med.py", name="med.py", lines="2-2",
                            code="y", severity="Medium", final_risk=0.5,
                            confidence="MED", root_cause="rc", explanation="e"),
            DetailedFinding(file="src/high.py", name="high.py", lines="3-3",
                            code="z", severity="Low", final_risk=0.1,
                            confidence="LOW", root_cause="rc", explanation="e"),
        ],
    )
    html = prototype_v1.render(ir)

    # Parent row ↔ child finding pairs share a color.
    assert 'class="badge badge-orange">Active Refactor' in html
    assert 'class="badge badge-orange">Resolution Planned' in html

    assert 'class="badge badge-yellow">Light Refactor' in html
    assert 'class="badge badge-yellow">Adjustments Planned' in html

    assert 'class="badge badge-green">Ready' in html
    assert 'class="badge badge-green">Minor' in html

    # And specifically: "Resolution Planned" never lands on yellow again.
    assert 'class="badge badge-yellow">Resolution Planned' not in html


def test_prototype_v1_low_readiness_is_orange_everywhere() -> None:
    """Active Refactor (``status="Low"``) must be orange across every surface.

    The same readiness bucket shows up in three different views:

      1. The per-file Readiness column in Detailed Compatibility.
      2. The dependency-graph nodes + cluster file-list badges in
         Migration Plan.
      3. The Wave-0 prerequisites bar chart in Migration Plan
         (``_STATUS_COLORS["Low"]`` → ``prerequisites_chart_json``).

    Previously these disagreed (yellow in the file table, red in the
    Migration Plan diagrams). They must all signal orange now so a
    reviewer can match a node to a row at a glance.
    """
    from adapters.prototype_v1 import _STATUS_COLORS

    # 1. The chart color used for the prerequisites bar is the orange tone.
    assert _STATUS_COLORS["Low"] == "#FF7C1D"

    ir = Assessment(
        files=[
            FileCompatibilityRow(path="src/a.py", name="a.py", technology="Python",
                                 lines=10, issues=5, status="Low"),
        ],
        most_depended_files=[{"path": "src/a.py", "name": "a.py", "metric": 1}],
        # Dependency graph required so _prerequisite_rows includes the file
        # (rows without an import-graph entry are skipped by design).
        dependency_graph=DependencyGraph(
            module="src", width=200, height=50, file_count=1, edge_count=0,
            nodes=[GraphNode(id="src/a.py", label="a.py", x=0, y=0,
                             status="Low", in_degree=1, blast_radius=1)],
            edges=[],
        ),
    )
    html = prototype_v1.render(ir)

    # 2. The per-file readiness badge uses the orange palette.
    assert 'class="badge badge-orange">Active Refactor' in html

    # 3. The dependency-graph node-stroke color for Low is the orange tone.
    #    (Template inlines the value as ``stroke="#FF7C1D"``.)
    assert '#FF7C1D' in html

    # 4. The orange tone appears in the prerequisites chart JSON payload
    #    fed to Chart.js (this is what colors the bar in Migration Plan).
    assert '"color": "#FF7C1D"' in html


def test_prototype_v1_migration_categories_effort_is_plain_text() -> None:
    """Effort column in Migration Categories must not wear a badge.

    Every effort bucket maps to the same yellow severity badge after the
    red retirement on findings, which means a uniformly-yellow badge added
    visual noise without information. Render the label as plain text so the
    column still scans cleanly.
    """
    from assess_ir import MigrationCategoryRow

    ir = Assessment(
        migration_categories=[
            MigrationCategoryRow(name="RDD / SparkContext", description="d",
                                 effort="High", files_affected=2, occurrences=3),
        ],
    )
    html = prototype_v1.render(ir)
    # Locate the Migration Categories table body and inspect only that slice.
    marker = "<h2>Migration categories</h2>"
    assert marker in html
    table_slice = html.split(marker, 1)[1].split("</table>", 1)[0]
    # No badge span (severity or otherwise) on the effort cell.
    assert 'class="badge' not in table_slice
    # The effort label still appears as text.
    assert "Major" in table_slice  # effort_label("High") == "Major"


def test_prototype_v1_issue_summary_tiles_are_uncolored() -> None:
    """The Warnings / Conversion Issues tile values must render without a
    color class. Tiles now count UNIQUE ISSUE ROWS (not summed occurrences),
    so three IssueRows produce tile counts of 1 each."""
    ir = Assessment(
        issues=[
            IssueRow(code="X-H", description="d1", count=4),  # → conversion bucket (1 row)
            IssueRow(code="X-H", description="d2", count=3),  # → conversion bucket (1 more row)
            IssueRow(code="X-M", description="d", count=7),   # → warnings bucket (1 row)
            # X-L is no longer mapped to parsing; it goes to "other"
        ],
    )
    html = prototype_v1.render(ir)
    marker = "<h2>Issue summary</h2>"
    assert marker in html
    after_marker = html.split(marker, 1)[1]
    tiles_slice = after_marker.split('<div class="issue-summary-table-wrap">', 1)[0]
    # No `.value.yellow` or `.value.red` classes inside the tile group.
    assert 'class="value yellow"' not in tiles_slice
    assert 'class="value red"' not in tiles_slice
    # Tiles count rows: 2 conversion rows, 1 warning row.
    assert ">2<" in tiles_slice   # 2 conversion rows
    assert ">1<" in tiles_slice   # 1 warning row


def test_prototype_v1_status_filter_uses_data_status_attribute() -> None:
    """The Readiness dropdown filter still works after the badge text changed.

    The visible label is now ``Ready`` / ``Some Updates`` / ``Updates Planned``,
    but the dropdown options still carry ``value="high|medium|low"`` so the JS
    must read the underlying status token from ``data-status`` on each row
    rather than parsing the badge text.
    """
    ir = Assessment(
        files=[
            FileCompatibilityRow(path="src/a.py", name="a.py", technology="Python",
                                 lines=5, issues=0, status="High"),
            FileCompatibilityRow(path="src/b.py", name="b.py", technology="Python",
                                 lines=5, issues=5, status="Low"),
        ],
    )
    html = prototype_v1.render(ir)
    assert 'data-status="high"' in html
    assert 'data-status="low"' in html
    # And the filter JS sources from data-status (not just the cell text).
    assert "row.dataset.status" in html


def test_code_churn_is_category_from_per_file_readiness() -> None:
    """Code churn is deterministic categories + per-bucket file counts derived
    from the per-file readiness table — never a nondeterministic 0-100 score."""
    findings = json.loads(_ANALYSIS_JSON.read_text())
    ir = transform_analysis(findings, project="t")

    cc = ir.code_churn
    assert cc.category in ("High", "Medium", "Low")
    # Counts partition the file rows exactly.
    assert (cc.files_ready + cc.files_light_refactor
            + cc.files_active_refactor) == len(ir.files)
    assert cc.files_ready == sum(1 for f in ir.files if f.status == "High")
    assert cc.files_active_refactor == sum(1 for f in ir.files if f.status == "Low")
    # No numeric score leaks into the model or its description.
    assert not hasattr(cc, "percent")
    assert "%" not in cc.description
    assert "readiness_score" not in cc.description


def test_code_churn_overall_category_multi_signal() -> None:
    """Multi-signal composite: file fraction, issue concentration, code surface.

    A small minority of hard files only triggers Active Refactor when they
    collectively carry significant weight across all three signals; a single
    hard file in a large workload gives at most Light Refactor.
    """
    from assess_ir import code_churn_from_files

    def _f(status, issues=0, lines=100):
        return FileCompatibilityRow(path="x", name="x",
                                    issues=issues, lines=lines, status=status)

    # Kipawa-like distribution: 3 active files with heavy issue load among 23 files.
    # active_score ≈ 0.50*(3/23) + 0.30*1.0*(12/20) + 0.20*(663/2300) ≈ 0.30 → Active.
    kipawa = (
        [_f("High", issues=0, lines=64)] * 14 +
        [_f("Medium", issues=1, lines=172)] * 6 +
        [_f("Low", issues=4, lines=221)] * 3
    )
    cc = code_churn_from_files(kipawa)
    assert cc.category == "Low", f"Kipawa-like: expected Active Refactor, got {cc.category}"
    assert (cc.files_ready, cc.files_light_refactor, cc.files_active_refactor) == (14, 6, 3)

    # 1 hard file in 100 easy ones with low total issues → Light Refactor, NOT Active.
    # active_score ≈ 0.50*0.01 + 0.30*0.3*1.0 + small ≈ 0.10 — well below 0.20.
    one_in_hundred = [_f("High", issues=0, lines=50)] * 99 + [_f("Low", issues=3, lines=50)]
    assert code_churn_from_files(one_in_hundred).category == "Medium"

    # All light, no active → Light Refactor.
    all_light = [_f("High", issues=0)] * 10 + [_f("Medium", issues=2)] * 10
    assert code_churn_from_files(all_light).category == "Medium"

    # All clean → Ready.
    assert code_churn_from_files([_f("High")] * 20).category == "High"

    # No files → empty estimate defaults to Ready.
    assert code_churn_from_files([]).category == "High"


def test_migration_category_description_uses_analyzer_root_cause() -> None:
    """Regression: category description must come from analyzer ``root_cause`` text,
    not a hand-written ``_DESCRIPTION_BY_CATEGORY`` lookup."""
    findings = [
        {"file": "a.py", "lines": "1-1", "code": "x", "final_risk": 0.8,
         "root_cause": "RDD usage triggers deprecation warning",
         "explanation": "e", "fix": None, "confidence": "HIGH", "language": "python"},
        {"file": "b.py", "lines": "2-2", "code": "y", "final_risk": 0.7,
         "root_cause": "RDD usage triggers deprecation warning",
         "explanation": "e", "fix": None, "confidence": "HIGH", "language": "python"},
    ]
    ir = transform_analysis(findings, project="t")
    rdd_cat = next(c for c in ir.migration_categories if c.name == "RDD / SparkContext")
    # Description matches the analyzer's own root_cause text
    assert rdd_cat.description == "RDD usage triggers deprecation warning"
    assert "RDD usage triggers deprecation warning" in rdd_cat.sample_root_causes


def test_scan_codebase_does_not_set_hard_coded_code_churn(tmp_path: Path) -> None:
    """Codebase scanner must NOT fabricate a code-churn estimate; churn is
    computed from the per-file readiness table at transform/merge time."""
    (tmp_path / "a.py").write_text("import pandas\nx = 1\n")
    ir = scan_codebase(tmp_path, project="t")
    assert ir.code_churn.category == "High"
    assert ir.code_churn.files_ready == 0
    assert ir.code_churn.files_light_refactor == 0
    assert ir.code_churn.files_active_refactor == 0
    assert ir.code_churn.description == ""


def test_prototype_renders_self_contained(tmp_path: Path) -> None:
    out = tmp_path / "report.html"
    prototype_v1.render_to_file(
        Assessment(metadata=AssessmentMetadata(project="t")), out
    )
    contents = out.read_text()
    assert "<!DOCTYPE html>" in contents
    # v1 pulls Inter (Google Fonts) + Chart.js from a CDN by design; the report
    # is a single HTML file with its own markup/CSS/JS inlined otherwise.
    assert "{{ " not in contents and "{%" not in contents


# ---------------------------------------------------------------------------
# R8 (light): per-issue category
# ---------------------------------------------------------------------------


def test_transform_analysis_detailed_finding_carries_category() -> None:
    """Each per-file drill-down finding is tagged with the same migration
    category bucket the Issue Summary uses, derived from ``root_cause``."""
    findings = [
        {"file": "/r/a.py", "lines": "1-1", "final_risk": 0.8,
         "root_cause": "RDD parallelize is not supported", "explanation": "e",
         "fix": None, "confidence": "HIGH", "language": "python"},
        {"file": "/r/a.py", "lines": "2-2", "final_risk": 0.5,
         "root_cause": "readStream kafka source needs redesign", "explanation": "e",
         "fix": None, "confidence": "MEDIUM", "language": "python"},
    ]
    ir = transform_analysis(findings, project="t", workload_root="/r")
    by_lines = {d.lines: d.category for d in ir.detailed_findings}
    assert by_lines["1-1"] == "RDD / SparkContext"
    assert by_lines["2-2"] == "Streaming"

    # Category surfaces in the rendered drill-down card + the CSV export header.
    html = prototype_v1.render(ir)
    assert 'data-category="RDD / SparkContext"' in html
    assert "'Category'" in html  # CSV export column header


# ---------------------------------------------------------------------------
# R1: workload classification — 4-archetype 2×2 matrix
# ---------------------------------------------------------------------------

_VALID_ARCHETYPES = {
    "End-to-End ETL Pipeline",
    "Data Router / Ingestion Hub",
    "In-Warehouse Compute",
    "Lightweight Orchestration",
}


def test_scan_codebase_workload_classification_archetype(tmp_path: Path) -> None:
    """scan_codebase produces one of the four valid archetypes and populates
    the new file-based metrics (no legacy binary labels)."""
    (tmp_path / "io.py").write_text(
        "df = spark.read.parquet('s3://b/in')\n"
        "df.write.parquet('s3://b/out')\n"
        "df2 = spark.read.json('s3://b/j')\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    wc = ir.workload_classification
    assert wc.classification in _VALID_ARCHETYPES
    assert wc.ingestion_level in ("High", "Low")
    assert wc.compute_level in ("High", "Low")
    assert 0.0 <= wc.ingestion_file_pct <= 1.0
    assert wc.spark_per_10_loc >= 0.0
    assert wc.total_files >= 1
    assert "external" in wc.description.lower() or "file" in wc.description.lower()
    assert "loc" in wc.description.lower() or "spark" in wc.description.lower()


@pytest.mark.parametrize("ingestion_level,compute_level,expected_name", [
    ("High", "High", "End-to-End ETL Pipeline"),
    ("High", "Low",  "Data Router / Ingestion Hub"),
    ("Low",  "High", "In-Warehouse Compute"),
    ("Low",  "Low",  "Lightweight Orchestration"),
])
def test_workload_classification_all_four_archetypes(
    ingestion_level: str, compute_level: str, expected_name: str
) -> None:
    """WorkloadClassification with every ingestion/compute combination renders
    the correct archetype name and migration profile in the HTML report."""
    wc = WorkloadClassification(
        classification=expected_name,
        ingestion_level=ingestion_level,
        compute_level=compute_level,
        migration_profile="Test profile.",
        ingestion_file_count=5,
        ingestion_file_pct=0.5 if ingestion_level == "High" else 0.1,
        total_files=10,
        spark_per_10_loc=1.5 if compute_level == "High" else 0.3,
    )
    ir = Assessment(workload_classification=wc)
    html = prototype_v1.render(ir)
    assert expected_name in html
    assert ingestion_level in html
    assert compute_level in html


def test_workload_classification_metrics_not_raw_counts(tmp_path: Path) -> None:
    """A single file with many Spark ops must NOT override file-level ingestion
    signal — validates that the 2×2 matrix uses per-file and LOC-normalised
    metrics, not raw operation totals."""
    # One file with heavy compute, no I/O → Low Ingestion regardless of op count.
    (tmp_path / "heavy.py").write_text(
        "# lots of transforms, zero external I/O\n" * 5
        + "df = df.groupBy('x').agg({'y': 'sum'})\n" * 20
    )
    ir = scan_codebase(tmp_path, project="t")
    wc = ir.workload_classification
    assert wc.ingestion_file_count == 0
    assert wc.ingestion_file_pct == 0.0
    assert wc.ingestion_level == "Low"


def test_sql_file_ingestion_detected_from_dml(tmp_path: Path) -> None:
    """A .sql file with DML statements must count as an ingestion file.

    Previously SQL files scored zero against _DATA_FORMAT_PATTERNS (Python
    patterns), silently diluting ingestion_file_pct for SQL-heavy workloads.
    """
    (tmp_path / "etl.sql").write_text(
        "CREATE OR REPLACE TABLE sales AS\n"
        "SELECT a.id, b.value\n"
        "FROM raw_a a JOIN raw_b b ON a.id = b.id\n"
        "GROUP BY a.id, b.value;\n"
        "\n"
        "INSERT INTO summary SELECT region, SUM(amount) FROM sales GROUP BY region;\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    wc = ir.workload_classification
    assert wc.ingestion_file_count >= 1, "DML-bearing .sql file must count as ingestion"
    assert wc.ingestion_file_pct > 0.0
    assert wc.ingestion_level == "High"  # 100% of files have ingestion


def test_sql_file_compute_counted_via_sql_ops(tmp_path: Path) -> None:
    """A .sql file with complex SQL operations must contribute to compute
    intensity (spark_per_10_loc).  SQL JOINs / GROUP BYs / window functions
    are semantically equivalent to the Spark API calls counted by _SPARK_API_RE
    in Python files."""
    # Write a SQL-only project with dense compute logic.
    sql = "\n".join([
        "WITH base AS (SELECT id, val FROM raw_tbl),",
        "agg AS (SELECT id, SUM(val) AS total FROM base GROUP BY id),",
        "ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY total DESC) AS rn FROM agg)",
        "SELECT r.id, r.total FROM ranked r",
        "JOIN lookup l ON r.id = l.id",
        "WHERE r.rn = 1 AND r.total > 100",
        "HAVING COUNT(*) > 0",
        "UNION ALL",
        "SELECT id, 0 FROM empty_tbl",
        "ORDER BY total DESC;",
    ])
    (tmp_path / "complex.sql").write_text(sql)
    ir = scan_codebase(tmp_path, project="t")
    wc = ir.workload_classification
    assert wc.spark_per_10_loc > 0.0, "SQL compute ops must feed spark_per_10_loc"


def test_sql_select_only_not_counted_as_ingestion(tmp_path: Path) -> None:
    """A .sql file with only SELECT (no DML writes) must NOT count as an
    ingestion file — it is a pure transform with no persistent I/O."""
    (tmp_path / "view.sql").write_text(
        "SELECT a.id, b.name\n"
        "FROM table_a a\n"
        "JOIN table_b b ON a.id = b.id\n"
        "GROUP BY a.id, b.name;\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    wc = ir.workload_classification
    assert wc.ingestion_file_count == 0, "SELECT-only SQL must not count as ingestion"


def test_mixed_py_sql_workload_classification(tmp_path: Path) -> None:
    """A workload with both Python and SQL files classifies correctly using
    signals from both file types."""
    (tmp_path / "loader.py").write_text(
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "df = spark.read.parquet('s3://bucket/data')\n"
        "df.createOrReplaceTempView('raw')\n"
        "result = spark.sql(open('transform.sql').read())\n"
        "result.write.parquet('s3://bucket/out')\n"
    )
    (tmp_path / "transform.sql").write_text(
        "CREATE OR REPLACE TABLE output AS\n"
        "SELECT a.id, SUM(b.val) AS total\n"
        "FROM raw a JOIN lookup b ON a.id = b.id\n"
        "GROUP BY a.id\n"
        "HAVING SUM(b.val) > 0;\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    wc = ir.workload_classification
    # Both files have ingestion signal.
    assert wc.ingestion_file_count == 2
    assert wc.ingestion_file_pct == 1.0
    assert wc.ingestion_level == "High"
    assert wc.spark_per_10_loc > 0.0


def test_workload_classification_zero_files() -> None:
    """An empty Assessment (no scan) renders without errors and shows the
    fallback message instead of a classification card."""
    html = prototype_v1.render(Assessment())
    assert "Classification will populate" in html


def test_workload_classification_badges_render_from_ir(tmp_path: Path) -> None:
    """Risk-modifier badges are driven entirely by existing IR signals —
    EAI from file_info, UDFs from complex_patterns, custom deps from lib_deps.
    No signal → no badge."""
    # File with no EAI, no UDFs, no custom libs.
    (tmp_path / "simple.py").write_text(
        "from pyspark.sql import SparkSession\n"
        "df = spark.read.parquet('s3://b/in')\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    html = prototype_v1.render(ir)
    # EAI badge must NOT appear (no HTTP/network imports).
    assert "External Access Required" not in html


def test_workload_classification_udf_badge_from_complex_patterns() -> None:
    """UDF badge appears when complex_patterns reports Custom UDFs; count is
    derived from the pattern row, not hardcoded."""
    ir = Assessment(
        workload_classification=WorkloadClassification(
            classification="In-Warehouse Compute",
            ingestion_level="Low",
            compute_level="High",
            migration_profile="p",
            total_files=5,
        ),
        complex_patterns=[
            ComplexPatternRow(pattern="Custom UDFs", occurrences="7",
                              impact="Medium", files_affected=3),
        ],
    )
    html = prototype_v1.render(ir)
    assert "UDF Refactoring" in html
    assert "7" in html


def test_workload_classification_custom_dep_badge() -> None:
    """Custom-dep badge appears when unsupported third-party libraries exist;
    the count comes from lib_deps classification, not a hardcoded threshold."""
    from assess_ir import ThirdPartyLibRow
    ir = Assessment(
        workload_classification=WorkloadClassification(
            classification="Lightweight Orchestration",
            ingestion_level="Low",
            compute_level="Low",
            migration_profile="p",
            total_files=2,
        ),
        third_party_libs=[
            ThirdPartyLibRow(name="some_custom_lib", import_count=3,
                             snowpark_supported=False, classification="unsupported"),
        ],
    )
    html = prototype_v1.render(ir)
    assert "Custom Dependencies" in html


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# R11: advisory narrative layer (LLM override + deterministic fallback)
# ---------------------------------------------------------------------------


def test_narratives_fallback_renders_when_absent() -> None:
    """With no narratives supplied, each explained section still shows a
    deterministic advisory explanation grounded in the IR."""
    ir = Assessment(
        complex_patterns=[
            ComplexPatternRow(pattern="RDD Operations", occurrences="3", impact="High", files_affected=2),
        ],
        workload_classification=WorkloadClassification(
            classification="In-Warehouse Compute",
            ingestion_level="Low",
            compute_level="High",
            migration_profile="Heavy transforms, minimal external I/O.",
            ingestion_file_pct=0.1,
            spark_per_10_loc=1.5,
            total_files=5,
        ),
        project_type=ProjectType(label="AI-Assisted Refactoring", color="blue",
                                 resolution_engine="Deterministic Transpiler + Cortex LLM Engine"),
        code_churn=CodeChurnEstimate(
            category="Medium", files_ready=6, files_light_refactor=3,
            files_active_refactor=1,
        ),
    )
    html = prototype_v1.render(ir)
    # Advisory headers no longer use &mdash; — they're plain "Advisory" or "Code Conversion Forecast".
    assert html.count(">Advisory<") >= 3
    assert "Code Conversion Forecast" in html
    assert "high-impact" in html                    # complex-patterns fallback
    assert "In-Warehouse Compute" in html           # classification label echoed
    assert "Light Refactor" in html                 # code-churn fallback names the category


# ---------------------------------------------------------------------------
# Project type — 3-tier code-complexity classification
# ---------------------------------------------------------------------------

_VALID_PROJECT_TYPES = {
    "Standard Conversion",
    "AI-Assisted Refactoring",
    "Specialized Architecture",
}

_RESOLUTION_ENGINES = {
    "Standard Conversion":     "Deterministic Transpiler",
    "AI-Assisted Refactoring": "Deterministic Transpiler + Cortex LLM Engine",
    "Specialized Architecture": "Cortex LLM Engine + Platform Mapping",
}


@pytest.mark.parametrize("pattern,expected_label", [
    # Specialized Architecture — capabilities config marks these as "specialized"
    ("Streaming",        "Specialized Architecture"),
    ("Spark ML / MLlib", "Specialized Architecture"),
    # AI-Assisted Refactoring — capabilities config marks these as "ai_assisted"
    ("RDD Operations",         "AI-Assisted Refactoring"),
    ("Custom UDFs",            "AI-Assisted Refactoring"),
    ("Delta Lake",             "AI-Assisted Refactoring"),
    ("Hive / Catalog",         "AI-Assisted Refactoring"),
    ("Broadcast / Accumulator","AI-Assisted Refactoring"),
    ("MapType / Complex JSON",  "AI-Assisted Refactoring"),
])
def test_project_type_tier_from_complex_pattern(
    tmp_path: Path, pattern: str, expected_label: str
) -> None:
    """Each complex-pattern routes to the tier defined in llm_capabilities.json."""
    from scan_codebase import _classify_project_type, _LLM_CAPABILITIES
    from collections import Counter
    assert _LLM_CAPABILITIES, "llm_capabilities.json must be loaded for this test"
    pt = _classify_project_type(
        pattern_hits=Counter({pattern: 1}),
        third_party_libs=[],
        code_files=[{"ext": ".py", "rel_path": "f.py"}],
        all_imports=Counter(),
        has_xml=False,
        has_custom_validation=False,
    )
    assert pt.label == expected_label
    assert pt.color in ("green", "blue", "orange")


def test_project_type_unsupported_libs_does_not_change_tier(tmp_path: Path) -> None:
    """Unsupported third-party packages are infrastructure concerns and must NOT
    affect the code-complexity tier — they appear as Deployment Prerequisites."""
    from scan_codebase import _classify_project_type
    from collections import Counter
    from assess_ir import ThirdPartyLibRow

    pt = _classify_project_type(
        pattern_hits=Counter(),
        third_party_libs=[
            ThirdPartyLibRow(name="some_custom_pkg", import_count=2,
                             snowpark_supported=False, role="runtime-third-party"),
        ],
        code_files=[{"ext": ".py", "rel_path": "f.py"}],
        all_imports=Counter(),
        has_xml=False,
        has_custom_validation=False,
    )
    # Unsupported packages alone must NOT produce a tier change — Standard is correct.
    assert pt.label == "Standard Conversion"


def test_project_type_specialized_beats_ai_assisted(tmp_path: Path) -> None:
    """When both Streaming and RDD are present, Specialized Architecture wins."""
    from scan_codebase import _classify_project_type
    from collections import Counter
    pt = _classify_project_type(
        pattern_hits=Counter({"Streaming": 3, "RDD Operations": 5}),
        third_party_libs=[],
        code_files=[{"ext": ".py", "rel_path": "f.py"}],
        all_imports=Counter(),
        has_xml=False,
        has_custom_validation=False,
    )
    assert pt.label == "Specialized Architecture"


def test_project_type_tier1_clean_workload(tmp_path: Path) -> None:
    """A pure DataFrame workload with no complex patterns is Standard Conversion."""
    from scan_codebase import _classify_project_type
    from collections import Counter
    pt = _classify_project_type(
        pattern_hits=Counter(),
        third_party_libs=[],
        code_files=[{"ext": ".py", "rel_path": "main.py"}],
        all_imports=Counter(),
        has_xml=False,
        has_custom_validation=False,
    )
    assert pt.label == "Standard Conversion"
    assert pt.color == "green"
    assert pt.resolution_engine == "Deterministic Transpiler"


def test_project_type_xml_is_ai_assisted(tmp_path: Path) -> None:
    """XML parsing (no Streaming) routes to AI-Assisted Refactoring."""
    from scan_codebase import _classify_project_type
    from collections import Counter
    pt = _classify_project_type(
        pattern_hits=Counter({"XML Parsing": 2}),
        third_party_libs=[],
        code_files=[{"ext": ".py", "rel_path": "f.py"}],
        all_imports=Counter(),
        has_xml=True,
        has_custom_validation=False,
    )
    assert pt.label == "AI-Assisted Refactoring"


def test_project_type_tier_driven_by_capabilities_config() -> None:
    """Tier assignment is driven by llm_capabilities.json, not hardcoded Python.
    Streaming → 'specialized' and RDD → 'ai_assisted' as declared in the config."""
    from scan_codebase import _tier_from_ewi_prefix, _LLM_CAPABILITIES
    assert _LLM_CAPABILITIES, "llm_capabilities.json must be loaded"
    assert _tier_from_ewi_prefix("SPRKCNTPY2000") == "specialized", \
        "Streaming must be 'specialized' per llm_capabilities.json"
    assert _tier_from_ewi_prefix("SPRKCNTPY2500") == "specialized", \
        "ML/MLlib must be 'specialized' per llm_capabilities.json"
    assert _tier_from_ewi_prefix("SPRKCNTPY1500") == "ai_assisted", \
        "RDD must be 'ai_assisted' per llm_capabilities.json"
    # Unknown prefix defaults conservatively to ai_assisted.
    assert _tier_from_ewi_prefix("SPRKCNTPY9999") == "ai_assisted"


def test_project_type_advisory_uses_issue_type_counts() -> None:
    """Advisory uses issues.issue_type counts (rolled-up) not detailed_findings.
    Auto-resolved and LLM conversion are shown as single buckets — no phase
    numbers and no risk stratification."""
    from assess_ir import IssueRow
    ir = Assessment(
        project_type=ProjectType(
            label="AI-Assisted Refactoring",
            color="blue",
            resolution_engine="Deterministic Transpiler + Cortex LLM Engine",
        ),
        issues=[
            IssueRow(code="R1", description="recipe fixed", count=5,
                     issue_type="Fixed"),
            IssueRow(code="C1", description="needs conversion", count=3,
                     issue_type="Conversion"),
        ],
    )
    html = prototype_v1.render(ir)
    # Auto-resolved count from issues.issue_type='Fixed'.
    assert "5</strong> findings auto-resolved" in html
    # LLM conversion count from issues.issue_type='Conversion'.
    assert "3</strong> findings queued for" in html
    # No phase numbers in user-facing text.
    assert "Phase 0.5" not in html
    assert "Phase 2" not in html
    # SCOS-TODO mention still present for honesty.
    assert "SCOS-TODO" in html
    # No "minimal manual intervention" promises.
    assert "minimal manual intervention" not in html.lower()


def test_eai_does_not_change_project_type_tier() -> None:
    """EAI is an infrastructure concern and must NOT change the code-complexity tier.
    It appears as a workload classification badge, not in the project type label."""
    from assess_ir import FileInfoRow
    ir = Assessment(
        project_type=ProjectType(
            label="AI-Assisted Refactoring",
            color="blue",
            resolution_engine="Deterministic Transpiler + Cortex LLM Engine",
        ),
        file_info=[
            FileInfoRow(path="a.py", name="a.py",
                        eai_required="Yes", eai_packages=["requests"]),
            FileInfoRow(path="b.py", name="b.py", eai_required="No"),
        ],
    )
    html = prototype_v1.render(ir)
    # The project type label must remain AI-Assisted.
    assert "AI-Assisted Refactoring" in html
    assert "Human-in-the-Loop" not in html
    # Resolution engine must not have changed to User Review.
    assert "Transpiler + User Review" not in html


def test_project_type_scan_only_advisory_no_overconfident_claims() -> None:
    """Scan-only advisory must not promise automatic LLM resolution.
    scan_advisory is now carried in the IR (populated by scan_codebase.py from
    llm_capabilities.json _tiers); the test uses the real config value."""
    from scan_codebase import _LLM_TIER_CONFIG
    tier_cfg = _LLM_TIER_CONFIG.get("ai_assisted", {})
    ir = Assessment(
        project_type=ProjectType(
            label=tier_cfg.get("label", "AI-Assisted Refactoring"),
            color="blue",
            resolution_engine=tier_cfg.get("resolution_engine", "Deterministic Transpiler + Cortex LLM Engine"),
            scan_advisory=tier_cfg.get("scan_advisory", ""),
        ),
    )
    html = prototype_v1.render(ir)
    assert "minimal manual intervention" not in html.lower()
    assert "SCOS-TODO" in html


def test_project_type_renders_resolution_engine_in_html() -> None:
    """The resolution engine text and advisory phase breakdown appear in the
    rendered HTML for every project type tier."""
    for label, engine in _RESOLUTION_ENGINES.items():
        ir = Assessment(project_type=ProjectType(
            label=label, color="green", resolution_engine=engine,
        ))
        html = prototype_v1.render(ir)
        assert engine in html, f"engine missing for {label}"
        assert label in html


def test_project_type_scan_produces_valid_tier(tmp_path: Path) -> None:
    """End-to-end: scan_codebase on a file with RDD patterns produces a
    valid project type with all required fields populated."""
    (tmp_path / "rdd_work.py").write_text(
        "from pyspark import SparkContext\n"
        "sc = SparkContext()\n"
        "rdd = sc.parallelize([1, 2, 3])\n"
        "result = rdd.reduceByKey(lambda a, b: a + b)\n"
    )
    ir = scan_codebase(tmp_path, project="t")
    pt = ir.project_type
    assert pt.label in _VALID_PROJECT_TYPES
    assert pt.resolution_engine in _RESOLUTION_ENGINES.values()
    assert pt.color in ("green", "blue", "yellow", "orange")
    assert pt.indicators


def test_narratives_override_wins_over_fallback() -> None:
    """A supplied narrative replaces the deterministic fallback for its section."""
    ir = Assessment(
        complex_patterns=[
            ComplexPatternRow(pattern="RDD Operations", occurrences="3", impact="High"),
        ],
        narratives=SectionNarratives(complex_patterns="CUSTOM LLM EXPLANATION HERE."),
    )
    html = prototype_v1.render(ir)
    assert "CUSTOM LLM EXPLANATION HERE." in html
    # The deterministic complex-patterns fallback must not also render.
    assert "high-impact" not in html


def test_build_assessment_loads_inline_narratives_payload() -> None:
    """Inline narratives JSON can be supplied without writing a file."""
    ir = build_assessment(
        project="t",
        analysis_json=_ANALYSIS_JSON,
        narratives_inline_json=json.dumps(
            {
                "complex_patterns": "CP inline explanation.",
                "unknown_key": "ignored",
            }
        ),
    )
    assert ir.narratives.complex_patterns == "CP inline explanation."
    assert not hasattr(ir.narratives, "unknown_key")
    assert ir.narratives.workload_classification == ""


def test_build_assessment_rejects_non_object_inline_narratives_payload() -> None:
    """Inline narratives payload must be a JSON object."""
    with pytest.raises(ValueError, match="to be an object"):
        build_assessment(
            project="t",
            analysis_json=_ANALYSIS_JSON,
            narratives_inline_json='["not", "an", "object"]',
        )


def test_build_assessment_normalizes_placeholder_inline_narratives_payload() -> None:
    """Whitespace/placeholder narrative values normalize to empty strings so
    deterministic section fallbacks remain active."""
    ir = build_assessment(
        project="t",
        analysis_json=_ANALYSIS_JSON,
        narratives_inline_json=json.dumps(
            {
                "complex_patterns": "   ",
                "workload_classification": "N/A",
                "project_type": "unknown",
                "code_churn": None,
            }
        ),
    )
    assert ir.narratives.complex_patterns == ""
    assert ir.narratives.workload_classification == ""
    assert ir.narratives.project_type == ""
    assert ir.narratives.code_churn == ""


def test_template_uses_fallback_when_narrative_is_whitespace() -> None:
    """Whitespace-only narrative text must not override deterministic fallback."""
    ir = Assessment(
        complex_patterns=[
            ComplexPatternRow(pattern="RDD Operations", occurrences="3", impact="High", files_affected=2),
        ],
        narratives=SectionNarratives(complex_patterns="   "),
    )
    html = prototype_v1.render(ir)
    assert "high-impact" in html  # fallback text


# ---------------------------------------------------------------------------
# Unified cross-folder dependency graph + Wave 0 prerequisites (PR 1)
# ---------------------------------------------------------------------------


def _write_crossfolder_workload(root: Path) -> None:
    """A tiny project whose imports cross folder boundaries.

    ``readers/base.py`` is shared infrastructure imported by a writer in a
    different folder and by the root driver — so the dependency graph MUST
    carry cross-folder edges, which the legacy folder-bucketed diagram threw
    away.
    """
    (root / "readers").mkdir()
    (root / "writers").mkdir()
    (root / "readers" / "base.py").write_text("class Base:\n    pass\n")
    (root / "writers" / "writer.py").write_text(
        "from readers.base import Base\n"
        "class Writer(Base):\n    pass\n"
    )
    (root / "main.py").write_text(
        "from readers.base import Base\n"
        "from writers.writer import Writer\n"
        "def run():\n    return Writer()\n"
    )


def test_scan_builds_unified_cross_folder_dependency_graph(tmp_path: Path) -> None:
    """The scanner emits one unified graph with cross-folder edges, in-degree,
    and blast-radius — not the legacy per-module subgraphs."""
    _write_crossfolder_workload(tmp_path)
    ir = scan_codebase(tmp_path, project="xf")

    # Legacy per-module list is no longer populated; the unified graph is.
    assert ir.dependency_graphs == []
    g = ir.dependency_graph
    assert g is not None and g.nodes and g.edges

    # Edges carry node ids so the SVG can be made interactive.
    assert all(e.source and e.target for e in g.edges)

    # At least one edge crosses a folder boundary (the whole point).
    import os as _os
    cross = [
        e for e in g.edges
        if _os.path.dirname(e.source) != _os.path.dirname(e.target)
    ]
    assert cross, "expected at least one cross-folder dependency edge"

    # base.py is shared infra: imported by writer + main → in-degree 2,
    # blast radius >= 2.
    base = next(n for n in g.nodes if n.id.endswith("readers/base.py"))
    assert base.in_degree == 2
    assert base.blast_radius >= 2


def test_prototype_v1_renders_unified_graph_and_wave0(tmp_path: Path) -> None:
    """The v1 report renders the interactive unified diagram and the Wave 0
    prerequisites bar chart + table from a codebase scan."""
    _write_crossfolder_workload(tmp_path)
    ir = scan_codebase(tmp_path, project="xf")
    html = prototype_v1.render(ir)

    # No unrendered Jinja.
    assert "{{ " not in html and "{%" not in html

    # Unified diagram + blast-radius interactivity hooks.
    assert 'id="dep-graph-svg"' in html
    assert "data-node-id=" in html
    assert "initDependencyGraphInteractivity" in html
    assert "dependentsAdjacency" in html
    assert 'id="dep-graph-status"' in html

    # Prerequisites chart under the File dependencies group (the redundant
    # table was removed; LOC, readiness, and blast radius now live in the bar
    # tooltip).
    assert "Dependency analysis" in html
    assert "File dependencies" in html
    assert "Data dependencies" in html
    assert "Conversion prerequisites" in html
    assert 'id="chart-prerequisites"' in html
    # The shared base.py shows up as a prerequisite in the chart data.
    assert "base.py" in html


def test_merge_backfills_unified_graph_node_status(tmp_path: Path) -> None:
    """Per-file readiness from analyzer findings recolors the unified graph
    nodes (they start optimistic at 'High' in the scan)."""
    _write_crossfolder_workload(tmp_path)
    codebase_ir = scan_codebase(tmp_path, project="xf")
    # An analyzer side that flags main.py as low readiness.
    analyzer_ir = Assessment(
        files=[FileCompatibilityRow(path="main.py", name="main.py", issues=5, status="Low")],
    )
    merged = codebase_ir.merge(analyzer_ir)
    main_node = next(n for n in merged.dependency_graph.nodes if n.id.endswith("main.py"))
    assert main_node.status == "Low"


# ---------------------------------------------------------------------------
# Quick-Win isolated modules (PR 2)
# ---------------------------------------------------------------------------


def test_scan_finds_isolated_modules(tmp_path: Path) -> None:
    """A standalone script that shares no imports with the main cluster is
    surfaced as an isolated Quick-Win module."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text("def a():\n    return 1\n")
    (tmp_path / "core" / "b.py").write_text(
        "from core.a import a\n"
        "def b():\n    return a() + 1\n"
    )
    (tmp_path / "standalone.py").write_text(
        "import math\n"
        "def solo():\n    return math.pi\n"
    )

    ir = scan_codebase(tmp_path, project="iso")

    # core/a + core/b are the main mass (2 connected files).
    assert ir.largest_component_size == 2
    # standalone.py is the lone isolated module.
    assert len(ir.isolated_modules) == 1
    island = ir.isolated_modules[0]
    assert island.file_count == 1
    assert island.files[0].name == "standalone.py"
    assert island.total_lines > 0


def test_scan_no_isolated_modules_when_fully_connected(tmp_path: Path) -> None:
    """A fully-connected project yields no Quick-Win islands."""
    _write_crossfolder_workload(tmp_path)
    ir = scan_codebase(tmp_path, project="xf")
    assert ir.isolated_modules == []
    assert ir.largest_component_size == 3  # base + writer + main


def test_prototype_v1_renders_isolated_module_cards(tmp_path: Path) -> None:
    """The File-dependency migration units section (Quick-win candidates +
    Main cluster) is intentionally hidden in v1; even when isolated modules
    exist the section must not appear in the rendered HTML."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text("def a():\n    return 1\n")
    (tmp_path / "core" / "b.py").write_text("from core.a import a\n")
    (tmp_path / "standalone.py").write_text("import math\nx = math.pi\n")

    ir = scan_codebase(tmp_path, project="iso")
    html = prototype_v1.render(ir)

    assert "{{ " not in html and "{%" not in html
    # Section is commented out — must not appear.
    assert "Quick-win candidates (isolated modules)" not in html
    assert "Safe for immediate cutover" not in html
    # The IR data (standalone.py) still exists but is not surfaced in this section.
    assert ir.isolated_modules, "scanner should still detect the isolated module"


def test_prototype_v1_renders_isolated_modules_empty_state(tmp_path: Path) -> None:
    """The File-dependency migration units section is hidden even when the
    project is fully connected (no isolated modules)."""
    _write_crossfolder_workload(tmp_path)
    ir = scan_codebase(tmp_path, project="xf")
    html = prototype_v1.render(ir)
    assert "Quick-win candidates (isolated modules)" not in html
    assert "No isolated modules" not in html


def test_merge_backfills_isolated_module_status(tmp_path: Path) -> None:
    """Analyzer readiness recolors isolated-module file badges."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text("def a():\n    return 1\n")
    (tmp_path / "core" / "b.py").write_text("from core.a import a\n")
    (tmp_path / "standalone.py").write_text("import math\nx = math.pi\n")

    codebase_ir = scan_codebase(tmp_path, project="iso")
    analyzer_ir = Assessment(
        files=[FileCompatibilityRow(path="standalone.py", name="standalone.py", issues=4, status="Low")],
    )
    merged = codebase_ir.merge(analyzer_ir)
    solo = merged.isolated_modules[0].files[0]
    assert solo.name == "standalone.py"
    assert solo.status == "Low"


# ---------------------------------------------------------------------------
# Report changes: hidden sections, enriched tooltips, in-degree layout
# ---------------------------------------------------------------------------


def test_file_dep_migration_units_hidden_in_v1(tmp_path: Path) -> None:
    """The 'File-dependency migration units' section (Quick-win candidates +
    Main cluster graphs) must not appear in the rendered v1 report."""
    _write_crossfolder_workload(tmp_path)
    ir = scan_codebase(tmp_path, project="xf")
    html = prototype_v1.render(ir)

    assert "{{ " not in html and "{%" not in html
    # The heading and its two sub-sections must not be rendered.
    assert "File-dependency migration units" not in html
    assert "Quick-win candidates (isolated modules)" not in html
    assert "Main cluster" not in html


def test_prerequisite_rows_includes_importers(tmp_path: Path) -> None:
    """_prerequisite_rows builds an 'importers' list of basenames that directly
    import each prerequisite file, and an 'importers_overflow' count."""
    from adapters.prototype_v1 import _prerequisite_rows

    dep_graph = {
        "nodes": [
            {"id": "main.py", "blast_radius": 0, "in_degree": 0},
            {"id": "base.py", "blast_radius": 2, "in_degree": 2},
        ],
        "edges": [
            {"source": "main.py", "target": "base.py"},
            {"source": "util.py", "target": "base.py"},
        ],
    }
    most_depended = [{"path": "base.py", "name": "base.py", "metric": 2}]
    files = [{"path": "base.py", "name": "base.py", "lines": 50, "status": "Medium"}]

    rows = _prerequisite_rows(most_depended, files, dep_graph)

    assert len(rows) == 1
    row = rows[0]
    assert row["in_degree"] == 2
    assert row["graph_in_degree"] == 2
    assert set(row["importers"]) == {"main.py", "util.py"}
    assert row["importers_overflow"] == 0


def test_prerequisite_rows_bar_uses_import_only_in_degree() -> None:
    """Bar length is the import-only in_degree from the graph node, not the
    metric field (which can include data-flow edges and would overcount)."""
    from adapters.prototype_v1 import _prerequisite_rows

    # writer.py: 1 import edge (from job.py) + 1 data-flow edge counted in metric.
    # Graph node reflects import-only: in_degree=1, blast_radius=1.
    dep_graph = {
        "nodes": [{"id": "writer.py", "blast_radius": 1, "in_degree": 1}],
        "edges": [{"source": "job.py", "target": "writer.py"}],
    }
    most_depended = [{"path": "writer.py", "name": "writer.py", "metric": 2}]
    files = [{"path": "writer.py", "name": "writer.py", "lines": 80, "status": "High"}]

    rows = _prerequisite_rows(most_depended, files, dep_graph)
    row = rows[0]

    # Bar length is import-only in_degree (1), not metric (2).
    assert row["in_degree"] == 1
    assert row["graph_in_degree"] == 1
    assert row["has_import_graph"] is True
    # Import in-degree never exceeds blast_radius.
    assert row["graph_in_degree"] <= row["blast_radius"]


def test_prerequisite_rows_no_import_graph_returns_empty() -> None:
    """Files with only data-flow connections (no import graph) are not shown in
    the prerequisites chart — it lives under File Dependencies, so only files
    that actually appear in the import graph are included.

    When dependency_graph is None the returned list is empty."""
    from adapters.prototype_v1 import _prerequisite_rows

    # Simulates Verisk: no dependency_graph, metric comes from data-flow edges only.
    most_depended = [{"path": "step4.py", "name": "step4.py", "metric": 3}]
    files = [{"path": "step4.py", "name": "step4.py", "lines": 100, "status": "High"}]

    rows = _prerequisite_rows(most_depended, files, dependency_graph=None)

    assert rows == []


def test_prerequisite_rows_includes_file_info(tmp_path: Path) -> None:
    """_prerequisite_rows enriches each row with source_system and target_type
    from the file_info table when available."""
    from adapters.prototype_v1 import _prerequisite_rows

    dep_graph = {"nodes": [{"id": "etl.py", "blast_radius": 3}], "edges": []}
    most_depended = [{"path": "etl.py", "name": "etl.py", "metric": 1}]
    files = [{"path": "etl.py", "name": "etl.py", "lines": 100, "status": "Low"}]
    file_info = [{"path": "etl.py", "source_system": "S3", "target_type": "Snowflake Table"}]

    rows = _prerequisite_rows(most_depended, files, dep_graph, file_info)

    assert rows[0]["source_system"] == "S3"
    assert rows[0]["target_type"] == "Snowflake Table"


def test_prerequisite_rows_file_info_defaults_when_absent() -> None:
    """When no file_info is supplied the row still renders with N/A defaults."""
    from adapters.prototype_v1 import _prerequisite_rows

    dep_graph = {
        "nodes": [{"id": "a.py", "blast_radius": 1, "in_degree": 1}],
        "edges": [{"source": "b.py", "target": "a.py"}],
    }
    rows = _prerequisite_rows(
        [{"path": "a.py", "name": "a.py", "metric": 1}],
        [{"path": "a.py", "name": "a.py", "lines": 10, "status": "High"}],
        dep_graph,
    )
    assert rows[0]["source_system"] == "N/A"
    assert rows[0]["target_type"] == "N/A"
    assert rows[0]["importers"] == ["b.py"]
    assert rows[0]["importers_overflow"] == 0


def test_prerequisites_chart_tooltip_has_blast_radius_definition(tmp_path: Path) -> None:
    """The rendered v1 HTML must include the inline blast-radius definition in
    the tooltip callback so readers know what 'blast radius' means."""
    _write_crossfolder_workload(tmp_path)
    ir = scan_codebase(tmp_path, project="xf")
    html = prototype_v1.render(ir)

    assert "blast radius" in html
    assert "transitively import" in html


def test_prerequisites_chart_tooltip_shows_importers_and_rw(tmp_path: Path) -> None:
    """The rendered v1 HTML must include the 'Imported by' and reads/writes
    tooltip lines in the Chart.js callback."""
    _write_crossfolder_workload(tmp_path)
    ir = scan_codebase(tmp_path, project="xf")
    html = prototype_v1.render(ir)

    assert "Imported by" in html
    assert "Reads from" in html or "Writes to" in html or "r.source_system" in html


def test_import_graph_uses_indegree_based_layout(tmp_path: Path) -> None:
    """Nodes are placed into rows by in-degree: in_degree=0 at the top (smallest y),
    higher in-degree values at larger y values.

    Crossfolder workload:
      main.py       → in_degree=0  (imports both; nothing imports it)
      writers/writer.py → in_degree=1 (main imports it)
      readers/base.py   → in_degree=2 (main + writer import it)

    Expected row order top-to-bottom: main < writer < base.
    """
    _write_crossfolder_workload(tmp_path)
    ir = scan_codebase(tmp_path, project="xf")

    g = ir.dependency_graph
    assert g is not None

    by_id = {n.id: n for n in g.nodes}
    main = next(n for n in g.nodes if n.id.endswith("main.py"))
    writer = next(n for n in g.nodes if n.id.endswith("writer.py"))
    base = next(n for n in g.nodes if n.id.endswith("base.py"))

    # in_degree values must match expectations.
    assert main.in_degree == 0
    assert writer.in_degree == 1
    assert base.in_degree == 2

    # Rows by in-degree: main is above writer, writer is above base.
    assert main.y < writer.y, (
        f"main.py (in_degree=0) should be in a higher row than writer.py "
        f"(in_degree=1), but y={main.y} >= {writer.y}"
    )
    assert writer.y < base.y, (
        f"writers/writer.py (in_degree=1) should be in a higher row than "
        f"readers/base.py (in_degree=2), but y={writer.y} >= {base.y}"
    )


# ---------------------------------------------------------------------------
# PR2: config-resolution pipeline fixes (Bugs 1-7)
# ---------------------------------------------------------------------------


def test_walk_config_values_collects_table_name_shaped_strings(tmp_path: Path) -> None:
    """Bug 1: _walk_config_values must collect table-name-shaped values.

    Previously only S3 URIs and file-extension paths were admitted; table
    names like 'ECOM_STAGING' (all-caps identifier) were discarded.
    """
    from scan_codebase import _walk_config_values

    pool: dict[str, set[str]] = {}
    _walk_config_values({"SCHEMA": "ECOM_STAGING", "PATH": "s3://b/k"}, pool)
    assert "ECOM_STAGING" in pool.get("SCHEMA", set()), (
        "table-name-shaped value 'ECOM_STAGING' must be admitted to config pool"
    )
    assert "s3://b/k" in pool.get("PATH", set())


def test_load_config_pool_finds_configs_outside_output(tmp_path: Path) -> None:
    """Bug 2: config pool must include YAML/JSON files in parent dirs.

    SCOS copies Python files into Output/ but does NOT copy companion config
    YAMLs.  _load_config_pool must scan up to 2 ancestor directories.
    """
    from scan_codebase import _load_config_pool

    # Mimic: workload_dir = parent/Output; config lives in parent/configs/
    output_dir = tmp_path / "Output"
    output_dir.mkdir()
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "app.yaml").write_text("DATABASE: dev_rouses_ecom\nSCHEMA: ECOM_STAGING\n")

    pool = _load_config_pool(output_dir)
    # At least the DATABASE key must appear when we search parent dirs
    assert pool.get("DATABASE") or pool.get("SCHEMA"), (
        "config pool must pick up YAML from sibling of Output/"
    )


def test_static_string_chained_get_fallback(tmp_path: Path) -> None:
    """Bug 3: _static_string must fall back to flat pool lookup for chained .get().

    CONFIG.get('config').get('databricks')[0].get('DATABASE') is a chained
    call whose receiver cannot be statically resolved; the key 'DATABASE'
    should still be used to look up the config pool directly.
    """
    import ast as _ast
    from scan_codebase import _static_string

    pool = {"DATABASE": {"dev_rouses_ecom"}}
    # Simulate: CONFIG.get("config").get("databricks")[0].get("DATABASE")
    tree = _ast.parse("CONFIG.get('config').get('databricks')[0].get('DATABASE')", mode="eval")
    call_node = tree.body
    result = _static_string(call_node, {}, pool)
    assert result == "dev_rouses_ecom", (
        f"chained .get('DATABASE') should resolve via pool fallback, got {result!r}"
    )


def test_extract_path_signatures_resolves_config_pool_fstring(tmp_path: Path) -> None:
    """Bug 5: config_pool must be threaded into _extract_path_signatures.

    f'{DATABASE_NAME}.{SCHEMA_STAGING}.FUT_COST' should resolve once the
    pool supplies concrete values for the variable names.
    """
    from data_edge_ast import _extract_path_signatures

    src = tmp_path / "futcost.py"
    src.write_text(
        "csv_file_read_path = 's3://cust-rouses/dailyincremental/FUT_COST.csv'\n"
        "table_write_name = f'{DATABASE_NAME}.{SCHEMA_STAGING}.FUT_COST'\n"
        "df = spark.read.csv(csv_file_read_path)\n"
        "df.write.saveAsTable(table_write_name)\n"
    )
    pool = {"DATABASE_NAME": {"dev_rouses_ecom"}, "SCHEMA_STAGING": {"ECOM_STAGING"}}
    sources, sinks, u_reads, u_writes = _extract_path_signatures(str(src), config_pool=pool)
    assert any("fut_cost" in s for s in sinks), (
        f"f-string table write must resolve via config_pool; sinks={sinks}"
    )
    assert any("fut_cost" in s for s in sources), (
        f"S3 path read must appear in sources; sources={sources}"
    )


def test_extract_path_signatures_call_site_inlining(tmp_path: Path) -> None:
    """Bug 6: parameter names inside helper functions must resolve via call-site args.

    read_csv_data_for_table(path, table) is called with literal args; the
    function body uses csv_file_read_path / table_write_name as the actual
    read/write targets.
    """
    from data_edge_ast import _extract_path_signatures

    src = tmp_path / "futcost.py"
    src.write_text(
        "def read_csv_data_for_table(csv_file_read_path, table_write_name):\n"
        "    df = spark.read.csv(csv_file_read_path)\n"
        "    df.write.saveAsTable(table_write_name)\n"
        "\n"
        "read_csv_data_for_table(\n"
        "    's3://cust-rouses/dailyincremental/FUT_COST.csv',\n"
        "    'dev_rouses_ecom.ECOM_STAGING.FUT_COST',\n"
        ")\n"
    )
    sources, sinks, u_reads, u_writes = _extract_path_signatures(str(src))
    assert any("fut_cost" in s for s in sources), (
        f"call-site path literal must appear in sources via inlining; sources={sources}"
    )
    assert any("fut_cost" in s for s in sinks), (
        f"call-site table literal must appear in sinks via inlining; sinks={sinks}"
    )


def test_parse_yaml_topology_extracts_dag_edges(tmp_path: Path) -> None:
    """Bug 7: _parse_yaml_topology must extract execution-order edges from Asset Bundle YAMLs."""
    from scan_codebase import _parse_yaml_topology

    # Build minimal workload with two Python files and a DAG YAML
    (tmp_path / "deployables").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "extract.py").write_text("# extract\n")
    (tmp_path / "tasks" / "load.py").write_text("# load\n")
    dag_yaml = (
        "resources:\n"
        "  jobs:\n"
        "    my_job:\n"
        "      tasks:\n"
        "        - task_key: extract_task\n"
        "          spark_python_task:\n"
        "            python_file: ${workspace.root_path}/files/tasks/extract.py\n"
        "        - task_key: load_task\n"
        "          depends_on:\n"
        "            - task_key: extract_task\n"
        "          spark_python_task:\n"
        "            python_file: ${workspace.root_path}/files/tasks/load.py\n"
    )
    (tmp_path / "deployables" / "my_dag.yml").write_text(dag_yaml)

    code_files = [
        {"rel_path": "tasks/extract.py"},
        {"rel_path": "tasks/load.py"},
    ]
    edges = _parse_yaml_topology(tmp_path, code_files)
    assert len(edges) == 1, f"expected 1 edge, got {edges}"
    src, tgt, kind = edges[0]
    assert src == "tasks/extract.py"
    assert tgt == "tasks/load.py"
    assert kind == "yaml_dag"


def test_parse_yaml_topology_empty_when_no_yml(tmp_path: Path) -> None:
    """_parse_yaml_topology must return empty list when no YAML files exist."""
    from scan_codebase import _parse_yaml_topology

    code_files = [{"rel_path": "a.py"}]
    assert _parse_yaml_topology(tmp_path, code_files) == []


def test_scan_data_dag_nodes_from_yaml_topology(tmp_path: Path) -> None:
    """End-to-end: scan() must produce data_dag nodes/edges when a DAG YAML is present."""
    dag_yaml = (
        "resources:\n"
        "  jobs:\n"
        "    my_job:\n"
        "      tasks:\n"
        "        - task_key: extract_task\n"
        "          spark_python_task:\n"
        "            python_file: ${workspace.root_path}/files/tasks/extract.py\n"
        "        - task_key: load_task\n"
        "          depends_on:\n"
        "            - task_key: extract_task\n"
        "          spark_python_task:\n"
        "            python_file: ${workspace.root_path}/files/tasks/load.py\n"
    )
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "extract.py").write_text("df = spark.read.csv('s3://b/input.csv')\n")
    (tasks_dir / "load.py").write_text("df.write.saveAsTable('db.schema.table')\n")
    deployables_dir = tmp_path / "deployables"
    deployables_dir.mkdir()
    (deployables_dir / "my_dag.yml").write_text(dag_yaml)

    ir = scan_codebase(tmp_path, project="dag_test")

    data_dag = ir.data_dependency_graph
    assert data_dag is not None, "data_dependency_graph must be populated when YAML topology is present"
    node_ids = {n.id for n in data_dag.nodes}
    assert any("extract" in nid for nid in node_ids), (
        f"extract.py must appear as a data_dependency_graph node; nodes={node_ids}"
    )
    assert len(data_dag.edges) > 0, "at least one yaml_dag edge must appear in data_dependency_graph"


# ---------------------------------------------------------------------------
# Generalizability fixes
# ---------------------------------------------------------------------------


def test_parse_yaml_topology_generic_argo_format(tmp_path: Path) -> None:
    """Generic YAML parser must handle non-Databricks orchestration formats.

    The parser must NOT hardcode Databricks Asset Bundle schema keys.  Any
    YAML with task-identity fields (name/task_id/…) and dependency fields
    (dependencies/after/needs/…) must produce edges, regardless of the
    surrounding structure.
    """
    from scan_codebase import _parse_yaml_topology

    argo_yaml = (
        "dag:\n"
        "  tasks:\n"
        "    - name: step_a\n"
        "      path: tasks/step_a.py\n"
        "    - name: step_b\n"
        "      dependencies: [step_a]\n"
        "      path: tasks/step_b.py\n"
    )
    (tmp_path / "pipeline.yml").write_text(argo_yaml)
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "step_a.py").write_text("")
    (tmp_path / "tasks" / "step_b.py").write_text("")

    code_files = [{"rel_path": "tasks/step_a.py"}, {"rel_path": "tasks/step_b.py"}]
    edges = _parse_yaml_topology(tmp_path, code_files)
    assert len(edges) == 1, f"expected 1 edge, got {edges}"
    src, tgt, kind = edges[0]
    assert src == "tasks/step_a.py"
    assert tgt == "tasks/step_b.py"
    assert kind == "yaml_dag"


def test_parse_yaml_topology_var_default_substitution(tmp_path: Path) -> None:
    """YAML variable defaults (${var.NAME}) must be substituted before path resolution."""
    from scan_codebase import _parse_yaml_topology

    dab_yaml = (
        "variables:\n"
        "  env:\n"
        "    default: dev-df\n"
        "resources:\n"
        "  jobs:\n"
        "    job:\n"
        "      tasks:\n"
        "        - task_key: a\n"
        "          spark_python_task:\n"
        "            python_file: ${workspace.root_path}/files/a.py\n"
        "        - task_key: b\n"
        "          depends_on:\n"
        "            - task_key: a\n"
        "          spark_python_task:\n"
        "            python_file: ${workspace.root_path}/files/b.py\n"
    )
    (tmp_path / "dag.yml").write_text(dab_yaml)
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")

    code_files = [{"rel_path": "a.py"}, {"rel_path": "b.py"}]
    edges = _parse_yaml_topology(tmp_path, code_files)
    assert any(e[0] == "a.py" and e[1] == "b.py" for e in edges), (
        f"a.py → b.py edge expected; got {edges}"
    )


def test_parse_yaml_topology_jinja_stripping(tmp_path: Path) -> None:
    """{{ var }} Jinja-style placeholders must be stripped from file paths."""
    from scan_codebase import _parse_yaml_topology

    airflow_yaml = (
        "tasks:\n"
        "  - task_id: extract\n"
        "    python_file: scripts/{{env}}/extract.py\n"
        "  - task_id: load\n"
        "    upstream_task_ids: [extract]\n"
        "    python_file: scripts/{{env}}/load.py\n"
    )
    (tmp_path / "dag.yml").write_text(airflow_yaml)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "extract.py").write_text("")
    (scripts / "load.py").write_text("")

    code_files = [{"rel_path": "scripts/extract.py"}, {"rel_path": "scripts/load.py"}]
    edges = _parse_yaml_topology(tmp_path, code_files)
    assert any(e[0].endswith("extract.py") and e[1].endswith("load.py") for e in edges), (
        f"extract → load edge expected after Jinja stripping; got {edges}"
    )


def test_extract_sql_data_refs_basic(tmp_path: Path) -> None:
    """SQL table references must be extracted from .sql files."""
    from scan_codebase import _extract_sql_data_refs

    sql = (
        "-- comment\n"
        "SELECT * FROM staging.ITEM_MASTER\n"
        "JOIN staging.TRANSACTION ON id = id;\n"
        "INSERT INTO prod.FUT_COST SELECT 1;\n"
        "CREATE TABLE IF NOT EXISTS prod.PRICECOST AS SELECT 1;\n"
    )
    p = tmp_path / "query.sql"
    p.write_text(sql)
    pool: dict = {}
    srcs, snks = _extract_sql_data_refs(str(p), pool)
    assert "staging.ITEM_MASTER" in srcs
    assert "staging.TRANSACTION" in srcs
    assert "prod.FUT_COST" in snks
    assert "prod.PRICECOST" in snks


def test_extract_sql_data_refs_config_pool_substitution(tmp_path: Path) -> None:
    """${KEY} placeholders in SQL must be resolved via the config pool."""
    from scan_codebase import _extract_sql_data_refs

    sql = "SELECT * FROM ${DATABASE}.${SCHEMA}.MY_TABLE;\n"
    p = tmp_path / "q.sql"
    p.write_text(sql)
    pool = {"DATABASE": {"dev_db"}, "SCHEMA": {"staging"}}
    srcs, snks = _extract_sql_data_refs(str(p), pool)
    assert any("MY_TABLE" in s for s in srcs), f"Expected table in sources, got {srcs}"


def test_extract_sql_data_refs_strips_comments(tmp_path: Path) -> None:
    """SQL comments (-- and /* */) must be ignored."""
    from scan_codebase import _extract_sql_data_refs

    sql = (
        "/* FROM noise_table */\n"
        "-- FROM another_noise\n"
        "SELECT * FROM real_table;\n"
    )
    p = tmp_path / "q.sql"
    p.write_text(sql)
    srcs, _ = _extract_sql_data_refs(str(p), {})
    assert "real_table" in srcs
    assert "noise_table" not in srcs
    assert "another_noise" not in srcs


def test_resolve_via_config_admits_table_names(tmp_path: Path) -> None:
    """_resolve_via_config must emit table-name shaped values, not only data paths."""
    from scan_codebase import _resolve_via_config

    src = (
        "def run(spark):\n"
        "    spark.read.table(TABLE_NAME)\n"
        "    spark.write.saveAsTable(SINK_TABLE)\n"
    )
    p = tmp_path / "f.py"
    p.write_text(src)
    pool = {"TABLE_NAME": {"staging_schema.MY_TABLE"}, "SINK_TABLE": {"prod_schema.RESULT"}}
    sources, sinks = _resolve_via_config(str(p), pool)
    assert any("MY_TABLE" in s or "staging_schema" in s for s in sources), (
        f"table-name shaped source expected; got {sources}"
    )
    assert any("RESULT" in s or "prod_schema" in s for s in sinks), (
        f"table-name shaped sink expected; got {sinks}"
    )


def test_two_hop_call_site_inlining(tmp_path: Path) -> None:
    """2-hop call-site inlining: param resolved via caller variable, not just literal."""
    from data_edge_ast import _extract_path_signatures

    src = (
        "TABLE = 'db.schema.actual_table'\n"
        "\n"
        "def load(table_name):\n"
        "    spark.read.table(table_name)\n"
        "\n"
        "load(TABLE)\n"
    )
    p = tmp_path / "f.py"
    p.write_text(src)
    sources, sinks, _, _ = _extract_path_signatures(str(p))
    assert any("actual_table" in s for s in sources), (
        f"2-hop inlining must resolve TABLE → 'db.schema.actual_table'; sources={sources}"
    )
# Regression: merge bug — unresolved edges from both sides must be combined
# ---------------------------------------------------------------------------


def test_merge_concatenates_unresolved_data_edges() -> None:
    """Both sides having unresolved_data_edges must produce a union, not just
    whichever side happens to be non-empty first (the old first-wins bug)."""
    edge_a = UnresolvedDataEdge(
        file="a.py", line=10, kind="read",
        call_expr="spark.read.parquet", arg_expr="x", reason="dynamic arg",
    )
    edge_b = UnresolvedDataEdge(
        file="b.py", line=20, kind="write",
        call_expr="df.write.csv", arg_expr="y", reason="dynamic arg",
    )
    side_a = Assessment(unresolved_data_edges=[edge_a])
    side_b = Assessment(unresolved_data_edges=[edge_b])

    merged = side_a.merge(side_b)
    files = {e.file for e in merged.unresolved_data_edges}
    assert "a.py" in files, "edge from self was lost"
    assert "b.py" in files, "edge from other was lost (regression: first-wins bug)"


def test_merge_deduplicates_unresolved_data_edges() -> None:
    """Identical (file, line, kind) tuples from both sides appear only once."""
    edge = UnresolvedDataEdge(
        file="dup.py", line=5, kind="read",
        call_expr="spark.read.parquet", arg_expr="x", reason="dynamic arg",
    )
    side_a = Assessment(unresolved_data_edges=[edge])
    side_b = Assessment(unresolved_data_edges=[edge])

    merged = side_a.merge(side_b)
    same = [e for e in merged.unresolved_data_edges if e.file == "dup.py"]
    assert len(same) == 1, f"Expected 1 deduplicated edge, got {len(same)}"


def test_merge_concatenates_unresolved_dynamic_imports() -> None:
    """Same first-wins regression applies to unresolved_dynamic_imports."""
    imp_a = UnresolvedDynamicImport(
        file="a.py", line=1, kind="import_module",
        reason="not found", raw_expr="importlib.import_module(x)",
    )
    imp_b = UnresolvedDynamicImport(
        file="b.py", line=2, kind="import_module",
        reason="not found", raw_expr="importlib.import_module(y)",
    )
    side_a = Assessment(unresolved_dynamic_imports=[imp_a])
    side_b = Assessment(unresolved_dynamic_imports=[imp_b])

    merged = side_a.merge(side_b)
    files = {e.file for e in merged.unresolved_dynamic_imports}
    assert "a.py" in files
    assert "b.py" in files, "dynamic import from other lost (first-wins bug)"


# ---------------------------------------------------------------------------
# Chart JSON helpers — _file_summary_chart_json / _spark_api_chart_json
# ---------------------------------------------------------------------------

from adapters.prototype_v1 import (  # noqa: E402
    _FILE_TYPE_COLORS,
    _file_summary_chart_json,
    _spark_api_chart_json,
)
from assess_ir import FileSummaryByType, SparkApiByCategory  # noqa: E402


def _fst(type_: str, files: int, lines: int = 0, percent: float = 0.0) -> dict:
    return FileSummaryByType(type=type_, files=files, lines=lines, percent=percent).model_dump()


def _sac(category: str, supported: int, unsupported: int) -> dict:
    return SparkApiByCategory(category=category, supported=supported, unsupported=unsupported).model_dump()


class TestFileSummaryChartJson:
    def test_empty_returns_empty_array(self) -> None:
        result = json.loads(_file_summary_chart_json([]))
        assert result == []

    def test_single_type_fields(self) -> None:
        result = json.loads(_file_summary_chart_json([_fst("Python", 10, 500, 100.0)]))
        assert len(result) == 1
        item = result[0]
        assert item["label"] == "Python"
        assert item["count"] == 10
        assert item["lines"] == 500
        assert item["percent"] == 100.0
        assert item["color"] == _FILE_TYPE_COLORS[0]

    def test_colors_cycle_through_palette(self) -> None:
        many = [_fst(f"Type{i}", i + 1) for i in range(len(_FILE_TYPE_COLORS) + 2)]
        result = json.loads(_file_summary_chart_json(many))
        assert result[0]["color"] == _FILE_TYPE_COLORS[0]
        assert result[len(_FILE_TYPE_COLORS)]["color"] == _FILE_TYPE_COLORS[0], "palette should cycle"
        assert result[len(_FILE_TYPE_COLORS) + 1]["color"] == _FILE_TYPE_COLORS[1]

    def test_each_type_gets_distinct_color_within_palette_length(self) -> None:
        rows = [_fst(f"T{i}", i + 1) for i in range(len(_FILE_TYPE_COLORS))]
        result = json.loads(_file_summary_chart_json(rows))
        colors = [item["color"] for item in result]
        assert colors == _FILE_TYPE_COLORS

    def test_multiple_types_preserve_order(self) -> None:
        rows = [_fst("Python", 8), _fst("SQL", 3), _fst("Notebook", 1)]
        result = json.loads(_file_summary_chart_json(rows))
        assert [r["label"] for r in result] == ["Python", "SQL", "Notebook"]


class TestSparkApiChartJson:
    def test_empty_input_returns_zero_totals(self) -> None:
        result = json.loads(_spark_api_chart_json([]))
        assert result["categories"] == []
        assert result["total_supported"] == 0
        assert result["total_unsupported"] == 0
        assert result["total"] == 0
        assert result["supported_pct"] == 0.0
        assert result["unsupported_pct"] == 0.0

    def test_totals_sum_correctly(self) -> None:
        rows = [
            _sac("DataFrame", 100, 5),
            _sac("RDD", 0, 20),
            _sac("SQL", 30, 0),
        ]
        result = json.loads(_spark_api_chart_json(rows))
        assert result["total_supported"] == 130
        assert result["total_unsupported"] == 25
        assert result["total"] == 155

    def test_percentages_sum_to_100_when_no_remainder(self) -> None:
        rows = [_sac("DataFrame", 75, 25)]
        result = json.loads(_spark_api_chart_json(rows))
        assert result["supported_pct"] == 75.0
        assert result["unsupported_pct"] == 25.0
        assert result["supported_pct"] + result["unsupported_pct"] == 100.0

    def test_all_supported_gives_100_pct(self) -> None:
        rows = [_sac("DataFrame", 50, 0), _sac("SQL", 30, 0)]
        result = json.loads(_spark_api_chart_json(rows))
        assert result["supported_pct"] == 100.0
        assert result["unsupported_pct"] == 0.0

    def test_all_unsupported_gives_0_pct(self) -> None:
        rows = [_sac("RDD", 0, 10), _sac("Streaming", 0, 5)]
        result = json.loads(_spark_api_chart_json(rows))
        assert result["supported_pct"] == 0.0
        assert result["unsupported_pct"] == 100.0

    def test_category_fields_preserved(self) -> None:
        rows = [_sac("DataFrame", 42, 3)]
        result = json.loads(_spark_api_chart_json(rows))
        cat = result["categories"][0]
        assert cat["category"] == "DataFrame"
        assert cat["supported"] == 42
        assert cat["unsupported"] == 3

    def test_order_preserved(self) -> None:
        rows = [_sac("RDD", 0, 10), _sac("DataFrame", 100, 0), _sac("SQL", 20, 2)]
        result = json.loads(_spark_api_chart_json(rows))
        assert [c["category"] for c in result["categories"]] == ["RDD", "DataFrame", "SQL"]


class TestDetailedChartsHtmlStructure:
    """Verify that the rendered HTML contains the new chart canvas elements
    and that the old tables are gone from the detailed compatibility tab."""

    def _detailed_section(self, html: str) -> str:
        start = html.find('id="detailed"')
        end = html.find('id="migration-plan"', start)
        return html[start:end] if start != -1 and end != -1 else html[start:]

    def test_file_summary_canvas_present(self) -> None:
        html = prototype_v1.render(Assessment())
        section = self._detailed_section(html)
        assert 'id="chart-file-summary"' in section

    def test_spark_api_canvas_present(self) -> None:
        html = prototype_v1.render(Assessment())
        section = self._detailed_section(html)
        assert 'id="chart-spark-api"' in section

    def test_detail_charts_row_present(self) -> None:
        html = prototype_v1.render(Assessment())
        section = self._detailed_section(html)
        assert 'class="detail-charts-row"' in section

    def test_old_by_technology_table_gone(self) -> None:
        html = prototype_v1.render(Assessment())
        section = self._detailed_section(html)
        assert "By technology" not in section

    def test_old_by_conversion_status_table_gone(self) -> None:
        html = prototype_v1.render(Assessment())
        section = self._detailed_section(html)
        assert "By conversion status" not in section

    def test_file_summary_js_data_injected(self) -> None:
        from assess_ir import FileSummaryByType
        ir = Assessment(
            file_summary_by_type=[
                FileSummaryByType(type="Python", files=5, lines=300, percent=83.3),
                FileSummaryByType(type="SQL", files=1, lines=50, percent=16.7),
            ]
        )
        html = prototype_v1.render(ir)
        assert "fileSummaryData" in html
        assert '"Python"' in html
        assert '"SQL"' in html

    def test_spark_api_js_data_injected(self) -> None:
        from assess_ir import SparkApiByCategory
        ir = Assessment(
            spark_api_by_category=[
                SparkApiByCategory(category="DataFrame", supported=40, unsupported=2),
                SparkApiByCategory(category="RDD", supported=0, unsupported=8),
            ]
        )
        html = prototype_v1.render(ir)
        assert "sparkApiData" in html
        assert '"DataFrame"' in html
        assert '"RDD"' in html

    def test_spark_stat_pills_container_present(self) -> None:
        html = prototype_v1.render(Assessment())
        section = self._detailed_section(html)
        assert 'id="spark-api-stat-pills"' in section

    def test_file_summary_legend_container_present(self) -> None:
        html = prototype_v1.render(Assessment())
        section = self._detailed_section(html)
        assert 'id="file-summary-legend"' in section
