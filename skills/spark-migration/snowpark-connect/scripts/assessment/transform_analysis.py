#!/usr/bin/env python3
"""Transform ``analysis.json`` (Phase 1 output) into a migration-readiness IR.

Called by Phase 1a of ``migrate-pyspark-to-snowpark-connect`` (the reporter
agent, Section A) alongside ``scan_codebase.py``. Together they populate the
:class:`Assessment` IR that the HTML adapter renders.

The Analyzer agent (``agents/analyzer.md``) emits a JSON array of risk-scored
findings::

    [
      {
        "file": "...",
        "lines": "17-27",
        "language": "python",
        "code": "...",
        "final_risk": 0.85,
        "root_cause": "...",
        "explanation": "...",
        "fix": "...",
        "confidence": "HIGH"
      },
      ...
    ]

Mapping into the IR (analyzer-derived sections only — codebase metrics like
file counts and LOC come from :mod:`scan_codebase`):

* ``workload.changes_needed / primary_language / executive_summary``
* ``compatibility``  (supported vs not-supported usages)
* ``issues``         (EWI rollup; one row per ``(severity, root_cause)``)
* ``files``          (Per-File Compatibility — analyzer view only; merged with
                      the codebase scan downstream)
* ``recommendations``
* ``code_churn``     (Ready / Light Refactor / Active Refactor category + per-bucket
                      file counts, from the per-file readiness distribution)
* ``migration_categories`` (grouped by root_cause → effort bucket)

Usage::

    python transform_analysis.py \
        --analysis-json analysis.json \
        --project my-workload \
        --output AssessmentIR.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from assess_ir import (
    Assessment,
    AssessmentMetadata,
    CompatibilitySummary,
    DetailedFinding,
    FileCompatibilityRow,
    IssueRow,
    MigrationCategoryRow,
    WorkloadSummary,
    code_churn_from_files,
    readiness_from_issues,
    render_executive_summary,
    severity_from_risk,
)


_LANG_TO_TECH = {
    "python": "Python",
    "scala": "Scala",
    "sql": "SQL",
    "java": "Java",
    "r": "R",
}


def transform(
    findings: list[dict],
    project: str = "unknown-project",
    workload_root: str | None = None,
    analysis_json_path: str = "",
    original_source_dir: Path | None = None,
    post_recipe_source_dir: Path | None = None,
    language: str = "python",
) -> Assessment:
    """Turn a parsed ``analysis.json`` array into an Assessment IR.

    Only the analyzer-derived sections of the IR are populated; the codebase
    scanner fills file counts, file types, library imports, etc. The two
    partial IRs are merged in :mod:`render_assessment`.

    When BOTH ``original_source_dir`` and ``post_recipe_source_dir`` are
    provided, each kept finding's ``lines`` and ``code`` are rebased from
    the analyzer's post-Phase-0.5 view onto the original source via a
    per-file ``difflib`` line map. This makes the Per-File / Issue-Summary
    / Recommendations sections quote original-source line numbers and
    original-source snippets — closing the Tier-B line-drift and snippet-
    context pollution vectors with no extra LLM cost.

    ``recipe_edits`` is deliberately NOT a parameter — recipe data is
    isolated to :mod:`recipe_resolved_panel` (Recipe-Data Isolation
    Guarantee, Tier-B plan).
    """
    metadata = AssessmentMetadata(
        project=project,
        analysis_json_path=analysis_json_path,
        mode="ANALYSIS_JSON",
    )
    if not findings:
        return Assessment(metadata=metadata)

    # Filter out analyzer noise BEFORE any downstream aggregation. Every
    # number derived from ``findings`` (per-file ``issues``, code-churn
    # category, ``changes_needed``, severity counts, executive summary) sees the
    # filtered list. Three classes get dropped:
    #
    # 1. **Analyzer self-vetoes** — ``final_risk == 0.0`` AND
    #    ``confidence == "HIGH"``. The analyzer matched a pattern
    #    syntactically but its own ``explanation`` reads "this is a custom
    #    method call, the similar test cases are unrelated". On Kipawa
    #    these were the noise hits on the literal string ``"sparkApp"``
    #    inside dict assignments in ``pipeline_impl.py``.
    #
    # 2. **Meta-warnings** — ``code == "SPRKCNTPY0099"`` or
    #    ``category == "Partial Migration"``. These mean "the LLM fixer
    #    agent didn't process this file; a deterministic fallback was
    #    applied; review manually." That's a workflow advisory, not a
    #    Spark API compatibility finding, and counting it inflates the
    #    per-file ``issues`` count and ``changes_needed`` with
    #    findings that have nothing to do with Spark compatibility.
    #
    # 3. **Adjudicator-dismissed** — ``resolution == "safe"``. Phase 1.1
    #    already decided this ``needs_adjudication`` row is a false positive
    #    (e.g. ``numpy.array()`` colliding with a ``pyspark`` ``array()`` KB
    #    anchor). Dismiss doesn't change ``kind`` — only confirm does — so
    #    this has to be checked on its own, or it renders identically to a
    #    confirmed issue.
    def _keep(f: dict) -> bool:
        risk = float(f.get("final_risk", 0.0))
        _raw_conf = f.get("confidence") or ""
        confidence = (str(_raw_conf) if not isinstance(_raw_conf, str) else _raw_conf).upper()
        if risk == 0.0 and confidence == "HIGH":
            return False
        code = (f.get("code") or "").upper()
        category = (f.get("category") or f.get("snowpark_connect_category") or "")
        if code in ("SPRKCNTPY0099", "SPRKCNTSCL0099") or category == "Partial Migration":
            return False
        _raw_res = f.get("resolution") or ""
        resolution = (str(_raw_res) if not isinstance(_raw_res, str) else _raw_res).upper()
        if resolution == "SAFE":
            return False
        return True

    findings = [f for f in findings if _keep(f)]
    if not findings:
        return Assessment(metadata=metadata)

    # Tier-B rebasing pass. When both source dirs are wired through, every
    # subsequent step (per-file counts, IssueRow rollup, MigrationCategoryRow,
    # code_churn, executive summary) sees rebased findings.
    # No-op when either dir is missing — preserves the analyzer's
    # post-Phase-0.5 numbers as today.
    if original_source_dir is not None and post_recipe_source_dir is not None:
        findings = _rebase_findings(
            findings,
            original_source_dir=original_source_dir,
            post_recipe_source_dir=post_recipe_source_dir,
        )

    root = _resolve_workload_root(findings, workload_root)

    files_agg: dict[str, dict] = defaultdict(
        lambda: {"issues": 0, "max_risk": 0.0, "language": language}
    )
    issues_agg: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"count": 0, "max_risk": 0.0, "category": "", "files": set()}
    )
    category_agg: dict[str, dict] = defaultdict(
        lambda: {"files": set(), "occurrences": 0, "max_risk": 0.0, "root_causes": []}
    )

    severity_counts = {"High": 0, "Medium": 0, "Low": 0}
    primary_language = language
    detailed_findings: list[DetailedFinding] = []

    for raw in findings:
        raw_file_path = raw.get("file", "<unknown>")
        # Normalize the bucket key so producers that disagree on path shape
        # (Phase 1 emits absolute paths; Phase 2a fallback emits basenames)
        # don't double-count the same physical file. Relativize against
        # ``root`` when possible; otherwise fall back to basename. This
        # collapses the abs-path-vs-basename split for the same file
        # without losing directory disambiguation when a workload contains
        # two files with the same name in different subdirectories.
        file_path = _normalize_finding_path(raw_file_path, root)
        language = (raw.get("language") or "python").lower()
        primary_language = language
        risk = float(raw.get("final_risk", 0.0))
        severity = severity_from_risk(risk)

        severity_counts[severity] += 1
        raw_lines_str = str(raw.get("lines", ""))

        agg = files_agg[file_path]
        agg["issues"] += 1
        agg["max_risk"] = max(agg["max_risk"], risk)
        agg["language"] = language

        root_cause = (raw.get("root_cause") or raw.get("explanation") or "Unknown")[:200]
        category = _category_for(root_cause)
        # Use the deterministic EWI code as the bucket key when available;
        # fall back to a severity-tagged label for LLM-only findings that
        # have no rule-catalog entry. This means rule-based issues group by
        # their actual EWI code (e.g. "SPRKCNTPY3100") and show the real
        # code in the Issue Summary table, while LLM-only findings still
        # get a clear-but-distinct label ("LLM-H", "LLM-M", "LLM-L").
        raw_ewi = (raw.get("ewi_code") or "").strip()
        code = raw_ewi if raw_ewi else f"LLM-{severity[0].upper()}"
        raw_status = (raw.get("status_class") or "").strip()
        bucket = issues_agg[(code, root_cause)]
        bucket["count"] += 1
        bucket["max_risk"] = max(bucket["max_risk"], risk)
        bucket["category"] = category
        bucket["files"].add(_relativize(file_path, root))
        # Carry the deterministic EWI code + status from the rule catalog (first
        # non-empty wins for the bucket). Empty for rule-less/LLM-only findings.
        if not bucket.get("ewi_code"):
            bucket["ewi_code"] = raw_ewi
            bucket["status_class"] = raw_status
            bucket["rule_id"] = raw.get("test_name") or ""
        # Derive the issue_type once for the bucket (first-write semantics).
        # Pass severity and kind so: (a) LLM-only buckets get a meaningful
        # type from risk level, and (b) recipe_validated findings are always
        # Fixed regardless of whether a KB EWI code fired.
        raw_kind = (raw.get("kind") or "").strip()
        if not bucket.get("issue_type"):
            bucket["issue_type"] = _derive_issue_type(
                raw_ewi, raw_status, root_cause, severity, raw_kind
            )

        cat_bucket = category_agg[category]
        cat_bucket["files"].add(file_path)  # uses normalized basename, same coalescing applies
        cat_bucket["occurrences"] += 1
        cat_bucket["max_risk"] = max(cat_bucket["max_risk"], risk)
        cat_bucket["root_causes"].append(root_cause)

        # Build the display lines field for HTML rendering.
        # Exported .py notebooks: _rebase_findings sets _notebook_lines_rebased=True
        #   after converting cell-relative → file-absolute → original-source coords.
        #   Display as plain file-absolute number (e.g. "219-225").
        # .ipynb / other notebooks without rebasing: display as "cell N: L" so the
        #   reader knows the coordinate is within-cell, not file-absolute.
        # Plain .py files: no cell_id, display raw lines as-is.
        cell_id_val = raw.get("cell_id")
        if cell_id_val is not None and not raw.get("_notebook_lines_rebased"):
            display_lines = (
                f"cell {cell_id_val}: {raw_lines_str}" if raw_lines_str
                else f"cell {cell_id_val}"
            )
        else:
            display_lines = raw_lines_str

        # Keep the raw finding for the per-file expandable drill-down.
        # ``file`` mirrors FileCompatibilityRow.path so the two surfaces group
        # by the same relativized key.
        detailed_findings.append(
            DetailedFinding(
                file=_relativize(file_path, root),
                name=Path(file_path).name,
                lines=display_lines,
                language=language,
                severity=severity,
                category=category,
                final_risk=risk,
                confidence=str(raw.get("confidence") or ""),
                code=(raw.get("code") or ""),
                root_cause=(raw.get("root_cause") or ""),
                explanation=(raw.get("explanation") or ""),
                fix=raw.get("fix"),
                kind=(raw.get("kind") or ""),
            )
        )

    # Sort by file, then by descending risk within each file. Jinja's groupby
    # sorts by the grouping key (file) with a stable sort, so the in-file
    # severity ordering set here is preserved in the rendered accordions.
    detailed_findings.sort(key=lambda d: (d.file, -d.final_risk))

    files = [
        FileCompatibilityRow(
            path=_relativize(path_str, root),
            name=Path(path_str).name,
            technology=_LANG_TO_TECH.get(agg["language"], "Python"),
            issues=agg["issues"],
            status=readiness_from_issues(agg["issues"]),
        )
        for path_str, agg in sorted(files_agg.items())
    ]

    issues = [
        IssueRow(
            code=code,
            description=desc,
            count=bucket["count"],
            category=bucket["category"],
            files=sorted(bucket["files"]),
            rule_id=bucket.get("rule_id", ""),
            ewi_code=bucket.get("ewi_code", ""),
            status_class=bucket.get("status_class", ""),
            issue_type=bucket.get("issue_type", "Other"),
        )
        for (code, desc), bucket in sorted(
            issues_agg.items(), key=lambda kv: -kv[1]["max_risk"]
        )
    ]

    migration_categories = [
        MigrationCategoryRow(
            name=cat,
            # Description is the most-common root_cause text from this bucket —
            # never canned. Falls back to a generic phrase only when the bucket
            # has zero root_cause strings (which would indicate a malformed input).
            description=_most_common_root_cause(bucket["root_causes"]),
            effort=severity_from_risk(bucket["max_risk"]),
            files_affected=len(bucket["files"]),
            occurrences=bucket["occurrences"],
            sample_root_causes=_dedup_take(bucket["root_causes"], 3),
        )
        for cat, bucket in sorted(
            category_agg.items(), key=lambda kv: -kv[1]["occurrences"]
        )
    ]

    workload = WorkloadSummary(
        changes_needed=len(findings),
        primary_language=_LANG_TO_TECH.get(primary_language, "Python"),
        executive_summary=render_executive_summary(
            total_findings=len(findings),
            files_count=len(files_agg),
            primary_language=_LANG_TO_TECH.get(primary_language, "Python"),
            severity_counts=severity_counts,
        ),
    )

    # Compatibility summary: 1 finding == 1 "not_supported" usage. The scanner
    # contributes the total Spark API usage count which lets us derive supported.
    compatibility = CompatibilitySummary(
        not_supported_usages=len(findings),
        highly_compatible_files=sum(1 for f in files if f.status == "High"),
        total_code_files=len(files),
    )

    # Code churn — the deterministic per-file readiness distribution, no score.
    code_churn = code_churn_from_files(files)

    return Assessment(
        metadata=metadata,
        workload=workload,
        compatibility=compatibility,
        files=files,
        detailed_findings=detailed_findings,
        issues=issues,
        migration_categories=migration_categories,
        recommendations=_generate_recommendations(findings),
        code_churn=code_churn,
    )


# ---------------------------------------------------------------------------
# Heuristics — kept dependency-free so analysis-only runs work in CI
# ---------------------------------------------------------------------------


_CATEGORY_KEYWORDS = {
    "RDD / SparkContext": ("rdd", "sparkcontext", "parallelize"),
    "Streaming": ("streaming", "kafka", "readstream", "writestream"),
    "ML / MLlib": ("mllib", "ml.feature", "pyspark.ml"),
    "Hive / Catalog": ("hive", "metastore", "catalog"),
    "Delta Lake": ("delta", "deltatable"),
    "Custom UDF / UDAF": ("udf", "udaf"),
    "Data Source / Sink": ("read.format", "write.format", "save"),
    "Configuration / Session": ("sparksession", "sparkconf", "spark.conf"),
}

# EWI code families where the finding describes a parser-level issue (SQL or
# Python parse failure) rather than a Spark API compatibility issue.
_PARSING_EWI_CODES: frozenset[str] = frozenset({
    "SPRKCNTPY1000", "SPRKCNTPY1001", "SPRKCNTPY1500",
    "SPRKCNTSCL1000", "SPRKCNTSCL1001", "SPRKCNTSCL1500",
})

_PARSING_REASON_KEYWORDS = (
    "parseerror", "sqlglot parse error", "sql parse failure",
    "parse failure", "parse error", "could not parse", "cannot parse",
)


def _derive_issue_type(
    ewi_code: str,
    status_class: str,
    description: str,
    severity: str = "",
    kind: str = "",
) -> str:
    """Derive the display category for an IssueRow from its metadata.

    Rules (checked in order):
      * kind "recipe_validated" → "Fixed" regardless of status_class.
        ``kind='recipe_validated'`` means the LLM confirmed a LibCST recipe
        was applied to this code site.  No KB EWI code is required — the
        recipe application itself is the signal that the tool handled it.
      * status_class "Fixed"   → "Fixed" (KB rule confirms tool handled it)
      * status_class "Error"   → "Conversion" (active work required)
      * status_class "Warning" → "Warning" (advisory)
      * Parsing EWI code range → "Parsing"
      * Reason text contains parse-error markers → "Parsing"
      * Severity "High"  (KB-less / LLM-only, final_risk >= 0.7) → "Conversion"
      * Severity "Medium"                                         → "Warning"
      * Otherwise → "Other"

    The severity fallback ensures LLM-only findings (no KB rule, no status_class)
    still land in a meaningful bucket so the issue summary toggle hides only
    genuinely advisory / low-confidence items rather than every LLM finding.
    """
    # recipe_validated: tool already applied a recipe — always Fixed.
    kind_lower = (kind or "").strip().lower()
    if kind_lower == "recipe_validated":
        return "Fixed"
    sc = (status_class or "").strip()
    if sc == "Fixed":
        return "Fixed"
    if sc == "Error":
        return "Conversion"
    if sc == "Warning":
        return "Warning"
    code_upper = (ewi_code or "").upper()
    if code_upper in _PARSING_EWI_CODES:
        return "Parsing"
    desc_lower = (description or "").lower()
    for kw in _PARSING_REASON_KEYWORDS:
        if kw in desc_lower:
            return "Parsing"
    # Fallback for LLM-only findings that carry no KB rule metadata.
    sev = (severity or "").strip()
    if sev == "High":
        return "Conversion"
    if sev == "Medium":
        return "Warning"
    return "Other"


def _category_for(root_cause: str) -> str:
    rc = root_cause.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in rc for kw in keywords):
            return category
    return "Other"


def _most_common_root_cause(strs: list[str]) -> str:
    if not strs:
        return "Patterns flagged by the analyzer."
    counts: dict[str, int] = defaultdict(int)
    for s in strs:
        counts[s] += 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _dedup_take(strs: list[str], n: int) -> list[str]:
    seen: list[str] = []
    for s in strs:
        if s not in seen:
            seen.append(s)
        if len(seen) >= n:
            break
    return seen


def _generate_recommendations(findings: list[dict]) -> list[str]:
    high_risk = sorted(
        (f for f in findings if float(f.get("final_risk", 0)) >= 0.7),
        key=lambda f: -float(f.get("final_risk", 0)),
    )
    recs: list[str] = []
    seen: set[str] = set()
    for f in high_risk:
        path = Path(f.get("file", "")).name
        fix = f.get("fix")
        root_cause = f.get("root_cause") or "Review and refactor."
        if fix:
            rec = f"{path}: {fix.split('.')[0]}."
        else:
            rec = f"{path}: review — {root_cause}"
        if rec not in seen:
            seen.add(rec)
            recs.append(rec)
        if len(recs) >= 5:
            break
    if not recs:
        recs.append(
            "No critical-risk findings detected. Run the full migration "
            "skill to convert the workload and validate end-to-end."
        )
    return recs


# ---------------------------------------------------------------------------
# Tier-B: rebase analyzer findings onto original (pre-Phase-0.5) source.
# Per-file diff via ``difflib`` is cached for the duration of one transform()
# call so a file with N findings only diffs once.
# ---------------------------------------------------------------------------


def _notebook_cell_code_start_line(post_text: str, cell_id: int) -> int | None:
    """Return the 1-based file-absolute start line for a notebook cell's code.

    Handles Databricks exported ``.py`` / ``.scala`` notebooks (those whose
    first line is ``# Databricks notebook source`` or
    ``// Databricks notebook source``).  Returns ``None`` for non-notebook
    files or when ``cell_id`` is out of range.

    Algorithm:

    * Cell 0 starts after the single header line.
    * Cell n (n ≥ 1) starts after the (n-1)-th ``# COMMAND ----------`` line.
    * Blank lines immediately after the boundary are skipped.
    * A leading ``# DBTITLE`` line (stripped from ``cell.source`` by
      ``notebook_io``) is also skipped.
    """
    lines = post_text.splitlines()
    if not lines:
        return None

    if lines[0].startswith("# Databricks notebook source"):
        separator = "# COMMAND ----------"
        dbtitle_prefix = "# DBTITLE "
    elif lines[0].startswith("// Databricks notebook source"):
        separator = "// COMMAND ----------"
        dbtitle_prefix = "// DBTITLE "
    else:
        return None

    sep_indices = [i for i, ln in enumerate(lines) if ln.strip() == separator]

    if cell_id == 0:
        i = 1
    elif cell_id <= len(sep_indices):
        i = sep_indices[cell_id - 1] + 1
    else:
        return None

    while i < len(lines) and not lines[i].strip():
        i += 1

    if i < len(lines) and lines[i].strip().startswith(dbtitle_prefix):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1

    return (i + 1) if i < len(lines) else None


def _cell_relative_to_absolute(lines_str: str, cell_start_line: int) -> str:
    """Convert a cell-relative ``lines`` string to file-absolute.

    ``lines_str`` is the 1-based cell-relative range (e.g. ``"2-8"`` or
    ``"1"``).  ``cell_start_line`` is the 1-based file-absolute line where
    cell-relative line 1 lives.  Returns the rebased string unchanged when it
    cannot be parsed.
    """
    spec = (lines_str or "").strip()
    if not spec:
        return lines_str
    offset = cell_start_line - 1  # additive offset: cell_rel + offset = file_abs
    if "-" in spec:
        try:
            l1_str, l2_str = spec.split("-", 1)
            return f"{int(l1_str) + offset}-{int(l2_str) + offset}"
        except ValueError:
            return lines_str
    try:
        return str(int(spec) + offset)
    except ValueError:
        return lines_str


def _build_post_to_original_line_map(
    original_text: str, post_text: str
) -> list[int | None]:
    """Return a 0-indexed post→original line map.

    ``mapping[j]`` is the 0-indexed original line that corresponds to the
    0-indexed post line ``j``, or ``None`` when no faithful correspondence
    exists (purely-inserted lines that didn't appear in the original, or
    tail lines of a *widened* replace block that have no plausible 1:1
    pre-image).

    Per-opcode behaviour (``difflib.SequenceMatcher``):

    * ``equal`` — map post lines one-to-one with original lines. The
      cheapest and most common case.
    * ``replace`` — pair the 1:1 *prefix* (up to ``min(orig_span,
      post_span)``) faithfully. Any *tail* post lines beyond that prefix
      (i.e. ``j - j1 >= orig_span``) are recipe-introduced code with no
      original equivalent; they are mapped to ``None`` so callers fall
      back to post-recipe coordinates rather than fabricating a phantom
      original line. This keeps the report's "lines reference the
      original source" contract honest for widened rewrites — the
      previous behaviour collapsed every tail line onto the last
      original line, which read as confidently-rebased but was a fiction.
    * ``insert`` — wholly-new post lines; mapped to ``None``.
    * ``delete`` — original lines absent from post; nothing to map.

    Callers (``_rebase_line_range`` / ``_rebase_findings``) treat
    ``None`` as "could not rebase, keep post-recipe coords" and emit a
    WARN log so the fallback is auditable.
    """
    original_lines = original_text.splitlines()
    post_lines = post_text.splitlines()
    mapping: list[int | None] = [None] * len(post_lines)

    matcher = difflib.SequenceMatcher(a=original_lines, b=post_lines, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for offset in range(j2 - j1):
                mapping[j1 + offset] = i1 + offset
        elif op == "replace":
            orig_span = i2 - i1
            post_span = j2 - j1
            prefix = min(orig_span, post_span)
            # 1:1 prefix: each of the first ``prefix`` post lines pairs
            # with the corresponding original line positionally.
            for offset in range(prefix):
                mapping[j1 + offset] = i1 + offset
            # Tail (only present when post_span > orig_span, i.e. a
            # widened rewrite): no faithful original line exists; leave
            # as ``None`` so the caller falls back to post coords.
        # ``insert`` / ``delete`` already covered by the all-None default.
    return mapping


def _rebase_one_indexed_line(
    one_indexed_line: int, mapping: list[int | None]
) -> int | None:
    idx = one_indexed_line - 1
    if idx < 0 or idx >= len(mapping):
        return None
    orig = mapping[idx]
    return orig + 1 if orig is not None else None


def _rebase_line_range(lines_str: str, mapping: list[int | None]) -> str | None:
    """Rebase ``"L"`` or ``"L1-L2"`` to original-source line numbers.

    Returns the rebased string, or ``None`` when the input cannot be
    rebased (unparseable, out of range, or both endpoints map to inserted
    lines). Callers fall back to the original ``lines`` string in the
    None case so the finding's reported range is never silently wrong —
    it's either rebased or untouched plus a log warning.
    """
    spec = (lines_str or "").strip()
    if not spec:
        return None
    if "-" in spec:
        try:
            l1_str, l2_str = spec.split("-", 1)
            l1, l2 = int(l1_str), int(l2_str)
        except ValueError:
            return None
        new_l1 = _rebase_one_indexed_line(l1, mapping)
        new_l2 = _rebase_one_indexed_line(l2, mapping)
        if new_l1 is None and new_l2 is None:
            return None
        # If one endpoint is unmappable, fall back to the other so the
        # range collapses to a single line rather than disappearing.
        if new_l1 is None:
            new_l1 = new_l2
        if new_l2 is None:
            new_l2 = new_l1
        if new_l1 > new_l2:
            new_l1, new_l2 = new_l2, new_l1
        if new_l1 == new_l2:
            return str(new_l1)
        return f"{new_l1}-{new_l2}"
    try:
        l = int(spec)
    except ValueError:
        return None
    new_l = _rebase_one_indexed_line(l, mapping)
    return str(new_l) if new_l is not None else None


def _extract_snippet(text_lines: list[str], lines_str: str) -> str | None:
    """Pull lines ``L`` or ``L1..L2`` (1-indexed, inclusive) out of ``text_lines``."""
    spec = (lines_str or "").strip()
    if not spec:
        return None
    if "-" in spec:
        try:
            l1_str, l2_str = spec.split("-", 1)
            l1, l2 = int(l1_str), int(l2_str)
        except ValueError:
            return None
    else:
        try:
            l1 = l2 = int(spec)
        except ValueError:
            return None
    if l1 < 1 or l2 < l1 or l2 > len(text_lines):
        return None
    return "\n".join(text_lines[l1 - 1 : l2])


def _relative_to_anchor(raw_file_path: str, anchor_dir: Path) -> str:
    """Return ``raw_file_path`` as a path relative to ``anchor_dir``.

    The analyzer's ``file`` field is rooted in ``post_recipe_source_dir``
    (the analyzer's ``--path`` arg). Relativizing against THAT dir before
    looking up in BOTH the post and the original tree avoids the trap
    where an absolute path happens to exist in the live conversion repo
    (post-recipe content) but not in the materialized original tree.

    Returns a relative path string; falls back to basename when
    relativization fails (file not under ``anchor_dir`` at all).
    """
    p = Path(raw_file_path)
    if not p.is_absolute():
        return raw_file_path
    try:
        return str(p.resolve().relative_to(anchor_dir.resolve()))
    except ValueError:
        return p.name


def _resolve_under(source_dir: Path, rel_path: str) -> Path | None:
    """Return an existing file under ``source_dir`` matching ``rel_path``.

    Tries ``source_dir/rel_path`` first; falls back to
    ``source_dir/basename(rel_path)`` for shallow workloads where the
    analyzer emitted just the basename. Returns ``None`` if neither
    exists.
    """
    direct = source_dir / rel_path
    if direct.is_file():
        return direct
    basename = source_dir / Path(rel_path).name
    if basename.is_file():
        return basename
    return None


def _rebase_findings(
    findings: list[dict],
    *,
    original_source_dir: Path,
    post_recipe_source_dir: Path,
) -> list[dict]:
    """Mutate-and-return findings with ``lines`` / ``code`` rebased to original.

    Returns NEW dicts (shallow copies) so the caller can still hand the raw
    input to other consumers without surprise mutation. The per-file diff is
    cached for the duration of the call.

    Findings whose file cannot be resolved on disk, or whose ``lines`` field
    cannot be rebased, are returned with the post-recipe values intact and
    a ``WARN`` log line — never silently wrong.
    """
    line_map_cache: dict[Path, list[int | None]] = {}
    original_text_cache: dict[Path, list[str]] = {}
    post_text_cache: dict[Path, str] = {}
    rebased: list[dict] = []

    for raw in findings:
        out = dict(raw)
        raw_file_path = out.get("file", "")
        if not isinstance(raw_file_path, str) or not raw_file_path:
            rebased.append(out)
            continue

        # Relativize against the post-recipe dir FIRST (the analyzer's
        # --path root) so an absolute path doesn't accidentally resolve
        # to the live conversion repo when looking up the original tree.
        rel = _relative_to_anchor(raw_file_path, post_recipe_source_dir)
        post_path = _resolve_under(post_recipe_source_dir, rel)
        orig_path = _resolve_under(original_source_dir, rel)
        if post_path is None or orig_path is None:
            logger.warning(
                "rebase: cannot resolve %r in both original and post-recipe "
                "source dirs (post=%s, original=%s); leaving finding untouched.",
                raw_file_path,
                post_path,
                orig_path,
            )
            rebased.append(out)
            continue

        cache_key = post_path
        mapping = line_map_cache.get(cache_key)
        if mapping is None:
            try:
                post_text = post_path.read_text(encoding="utf-8", errors="replace")
                orig_text = orig_path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                logger.warning(
                    "rebase: failed to read source for %s (%s); leaving "
                    "finding untouched.",
                    raw_file_path,
                    e,
                )
                rebased.append(out)
                continue
            mapping = _build_post_to_original_line_map(orig_text, post_text)
            line_map_cache[cache_key] = mapping
            post_text_cache[post_path] = post_text
            original_text_cache[orig_path] = orig_text.splitlines()

        # Notebook coordinate resolution: analysis.json always stores
        # cell-relative line numbers (line-within-cell) when cell_id is
        # present.  For Databricks exported .py notebooks (detected by
        # _notebook_cell_code_start_line returning non-None) we convert to
        # file-absolute here so the difflib pass maps against the right lines.
        # For .ipynb and other formats (returns None) we skip difflib entirely
        # — cell-relative numbers are kept as-is and will be displayed as
        # "cell N: L" in the report.
        stated_lines = out.get("lines", "")
        if out.get("cell_id") is not None and stated_lines:
            post_text_str = post_text_cache.get(post_path)
            if post_text_str is None:
                try:
                    post_text_str = post_path.read_text(encoding="utf-8", errors="replace")
                    post_text_cache[post_path] = post_text_str
                except OSError:
                    post_text_str = None

            if post_text_str is not None:
                cell_start = _notebook_cell_code_start_line(
                    post_text_str, int(out["cell_id"])
                )
                if cell_start is not None:
                    # Databricks exported .py: convert cell-relative → file-absolute,
                    # then fall through to the difflib pass below.
                    abs_lines = _cell_relative_to_absolute(stated_lines, cell_start)
                    logger.debug(
                        "rebase: cell-relative lines %r → file-absolute %r "
                        "for cell_id=%s in %s",
                        stated_lines, abs_lines, out["cell_id"], raw_file_path,
                    )
                    out["lines"] = abs_lines
                    post_lines_list = post_text_str.splitlines()
                    abs_snippet = _extract_snippet(post_lines_list, abs_lines)
                    if abs_snippet is not None:
                        out["code"] = abs_snippet
                    out["_notebook_lines_rebased"] = True
                else:
                    # .ipynb or unrecognised format: keep cell-relative, skip difflib.
                    rebased.append(out)
                    continue

        rebased_lines = _rebase_line_range(out.get("lines", ""), mapping)
        if rebased_lines is None:
            logger.warning(
                "rebase: could not rebase lines=%r for %s; keeping post-"
                "recipe line numbers.",
                out.get("lines"),
                raw_file_path,
            )
            rebased.append(out)
            continue

        out["lines"] = rebased_lines

        orig_text_lines = original_text_cache.get(orig_path)
        if orig_text_lines is None:
            try:
                orig_text_lines = orig_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                original_text_cache[orig_path] = orig_text_lines
            except OSError:
                orig_text_lines = None

        if orig_text_lines is not None:
            snippet = _extract_snippet(orig_text_lines, rebased_lines)
            if snippet is not None:
                out["code"] = snippet
            # If snippet extraction returns None we leave the post-recipe
            # ``code`` field as-is; the lines field is still rebased.

        rebased.append(out)

    return rebased


def _resolve_workload_root(findings: list[dict], explicit: str | None) -> Path | None:
    if explicit is not None:
        return Path(explicit) if explicit else None
    try:
        return _longest_common_parent([Path(f["file"]) for f in findings])
    except (KeyError, ValueError):
        return None


def _longest_common_parent(paths: list[Path]) -> Path | None:
    """Longest common *directory* ancestor of ``paths``.

    The result is always a directory, never a file. When every finding lives in
    the same file (or the common prefix otherwise reaches a full file path), the
    naive longest-common-prefix returns the file itself; relativizing a file
    against itself yields ``"."`` and ``Path(".").name == ""``, which surfaces as
    a blank, line-less row in the Per-File table. Backing off to the parent makes
    relativizing produce the basename instead.
    """
    if not paths:
        return None
    parts = [list(p.parts) for p in paths]
    common: list[str] = []
    for chunk in zip(*parts):
        if len(set(chunk)) == 1:
            common.append(chunk[0])
        else:
            break
    if not common:
        return None
    candidate = Path(*common)
    # If the common ancestor IS one of the inputs, it's a file path (e.g. the
    # single-file case where all parts match) — use its parent directory.
    if any(candidate == p for p in paths):
        candidate = candidate.parent
    return candidate if str(candidate) not in ("", ".") else None


def _relativize(path_str: str, root: Path | None) -> str:
    if not root or str(root) in (".", ""):
        return path_str
    try:
        return str(Path(path_str).relative_to(root))
    except ValueError:
        return path_str


def _normalize_finding_path(path_str: str, root: Path | None) -> str:
    """Canonicalize a finding's ``file`` field for bucket-key use.

    Producers disagree on path shape (Phase 1 emits absolute paths rooted
    in ``Output/``; Phase 2a's fallback transform emits relative paths or
    bare basenames). To coalesce findings about the same physical file:

    1. If ``root`` is set and ``path_str`` is absolute under it,
       relativize. ``"/Users/.../Output/sub/foo.py"`` with root
       ``"/Users/.../Output"`` becomes ``"sub/foo.py"``.
    2. If ``path_str`` is already relative, leave it alone — it's
       already in the canonical short form (whether it's a bare name
       like ``"foo.py"`` or a multi-segment ``"sub/foo.py"``). Stripping
       to basename here would silently collapse multiple ``__init__.py``
       files into one bucket downstream.
    3. Otherwise (absolute path that doesn't sit under root) fall back
       to basename — the best we can do without more context. Truly
       ambiguous same-name-in-different-dirs cases will still collide,
       but that case is rare and already ambiguous without directory
       info.
    """
    if not path_str or path_str == "<unknown>":
        return path_str or "<unknown>"
    p = Path(path_str)
    if not p.is_absolute():
        return path_str
    rel = _relativize(path_str, root)
    if rel != path_str:
        return rel
    return p.name or path_str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--analysis-json", required=True, type=Path)
    parser.add_argument("--project", default="unknown-project")
    parser.add_argument(
        "--workload-root",
        default=None,
        help="File paths in the IR are made relative to this. "
        "Auto-detected as longest-common-parent if omitted.",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        findings = json.loads(args.analysis_json.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise SystemExit(
            f"analysis.json at {args.analysis_json} is not valid JSON: {e}. "
            "If Phase 1 was interrupted, re-run the analyzer to regenerate it."
        ) from e
    if not isinstance(findings, list):
        raise SystemExit(
            f"Expected analysis.json to be a JSON array; got {type(findings).__name__}"
        )

    assessment = transform(
        findings,
        project=args.project,
        workload_root=args.workload_root,
        analysis_json_path=str(args.analysis_json.resolve()),
    )
    payload = assessment.model_dump(mode="json")
    out = json.dumps(payload, indent=2, default=str)

    if args.output:
        args.output.write_text(out)
        print(f"Wrote assessment IR to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
