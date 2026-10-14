"""
Unified Gap Analyzer -- produces a single ``gaps.json`` artifact.

Collects gaps from three sources:
1. Parsing report   (syntax errors, understanding errors)
2. Anomaly report   (structural / lineage / schema anomaly codes)
3. Scoring context  (unnamed I/O, inputs without columns, UNKNOWN types,
                     low-confidence XREF columns)

After collection, cascade suppression removes downstream gaps whose root
cause is a parsing error in the same file.

NOTE: PAR_001 (regex fallback) and inference_warnings are NOT collected
here — they are routed to XX_WARP_INTEL.json via warp_intel.py because
they reflect tool limitations, not client code issues.
"""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER_RE = re.compile(r"^(PTH|SNK|SQL|NOD|VAR)_\d+$")


def _is_real_name(node: dict) -> bool:
    """Same Strict Identity rule as unified_report.compute_score."""
    name = (node.get("name") or "").strip()
    if not name:
        return False
    if _PLACEHOLDER_RE.match(name):
        return False
    if name.startswith("runtime:"):
        return False
    return True

from warp_core.ir.gap_models import (
    GapCategory,
    GapItem,
    GapLocation,
    GapReport,
    GapSeverity,
    GapSubType,
)
from warp_core.unified_report import CONFIDENCE_WEIGHTS, ReportContext


_TEST_PATH_MARKERS: tuple[str, ...] = (
    "/test/", "/tests/", "/spec/", "spec.scala", "spec.py",
)


def _is_test_path(path: str | None) -> bool:
    """Return True if *path* points to a test/spec file."""
    if not path:
        return False
    lower = path.lower().replace("\\", "/")
    return any(m in lower for m in _TEST_PATH_MARKERS)


# Anomaly codes that belong to WARP_INTEL, not gaps.json.
# These reflect tool limitations (not client code issues) and are skipped here.
# Keep in sync with warp_intel._INTERNAL_ANOMALY_CODES.
_INTERNAL_ANOMALY_CODES: frozenset[str] = frozenset({"PAR_001"})

# Maps anomaly codes to (sub_type, severity) pairs.
# PAR_001 is intentionally absent: it reflects a Warp tool limitation
# (regex used instead of AST) and is routed to WARP_INTEL, not gaps.json.
_ANOMALY_CODE_MAP: dict[str, tuple[GapSubType, GapSeverity]] = {
    "LIN_001": (GapSubType.LINEAGE_BROKEN, GapSeverity.HIGH),
    "LIN_002": (GapSubType.ORPHAN_RESOURCE, GapSeverity.MEDIUM),
    "LIN_003": (GapSubType.DANGLING_NODE, GapSeverity.LOW),
    "LIN_004": (GapSubType.MISSING_SINK, GapSeverity.MEDIUM),
    "SCH_001": (GapSubType.SCHEMA_UNKNOWN, GapSeverity.MEDIUM),
    "SCH_002": (GapSubType.PHANTOM_COLUMN, GapSeverity.HIGH),
    "SCH_003": (GapSubType.TYPE_ENTROPY, GapSeverity.MEDIUM),
    "SCH_004": (GapSubType.MISSING_CAST, GapSeverity.LOW),
    "LOG_001": (GapSubType.BLACKBOX_UDF, GapSeverity.HIGH),
    "REF_002": (GapSubType.UNRESOLVED_PARAM, GapSeverity.HIGH),
    "JOIN_001": (GapSubType.UNCLEAR_JOIN, GapSeverity.MEDIUM),
}


def analyze_gaps(
    asg: dict[str, Any],
    anomalies: dict[str, Any] | list | None,
    ctx: ReportContext,
) -> GapReport:
    """Build the unified gap report.

    Parameters
    ----------
    asg : dict
        The parsed ASG (contains ``parsing_report``, ``data_in``, ``data_out``).
    anomalies : dict | list | None
        The anomaly report (``anomalies.json`` content) or ``None``.
    ctx : ReportContext
        Pre-computed scoring context from ``_build_context()``.

    Returns
    -------
    GapReport
        Finalized gap report ready for serialization.
    """
    report = GapReport(project_name=ctx.project_name)

    parsing_gaps = _collect_parsing_gaps(asg)
    structural_gaps = _collect_structural_gaps(anomalies)
    scoring_gaps = _collect_scoring_gaps(ctx)

    for g in parsing_gaps:
        report.add(g)
    for g in structural_gaps:
        report.add(g)
    for g in scoring_gaps:
        report.add(g)

    _apply_cascade_suppression(report)

    return report


# ── Parsing gaps ──────────────────────────────────────────────────────────


def _collect_parsing_gaps(asg: dict[str, Any]) -> list[GapItem]:
    """Extract gaps from the embedded parsing report."""
    items: list[GapItem] = []
    pr = asg.get("parsing_report")
    if not pr:
        return items

    seq = 0

    for finfo in pr.get("files", []):
        path = finfo.get("path", "unknown")

        if finfo.get("syntax_status") == "error":
            seq += 1
            items.append(GapItem(
                gap_id=f"GAP_PAR_{seq:03d}_{_slug(path)}",
                category=GapCategory.PARSING,
                sub_type=GapSubType.SYNTAX_ERROR,
                severity=GapSeverity.BLOCKER,
                subject=path,
                location=GapLocation(file=path),
                detail=finfo.get("syntax_error", "Syntax error"),
                suggestion="Fix the syntax error so the parser can extract the ASG.",
                ai_hints={
                    "action": "fix_syntax",
                    "error_message": finfo.get("syntax_error", ""),
                },
            ))

        if finfo.get("understanding_status") == "error":
            seq += 1
            items.append(GapItem(
                gap_id=f"GAP_PAR_{seq:03d}_{_slug(path)}",
                category=GapCategory.PARSING,
                sub_type=GapSubType.UNDERSTANDING_ERROR,
                severity=GapSeverity.CRITICAL,
                subject=path,
                location=GapLocation(file=path),
                detail=finfo.get("understanding_error", "Understanding error"),
                suggestion="Investigate the construct the parser could not understand.",
                ai_hints={
                    "action": "improve_parser_or_simplify_code",
                    "error_message": finfo.get("understanding_error", ""),
                },
            ))

    # inference_warnings are intentionally NOT collected here.
    # They reflect Warp tool limitations (low-confidence inference fallbacks)
    # and are routed to XX_WARP_INTEL.json via warp_intel.build_warp_intel().

    return items


# ── Structural gaps (from anomaly report) ─────────────────────────────────


def _collect_structural_gaps(
    anomalies: dict[str, Any] | list | None,
) -> list[GapItem]:
    """Map anomaly groups/items into GapItems."""
    items: list[GapItem] = []
    if not anomalies:
        return items

    groups: list[dict] = []
    if isinstance(anomalies, dict):
        groups = anomalies.get("anomalies", [])
    elif isinstance(anomalies, list):
        groups = anomalies

    seq = 0
    for grp in groups:
        code = grp.get("code", "")
        if code in _INTERNAL_ANOMALY_CODES:
            continue
        sub_type, severity = _ANOMALY_CODE_MAP.get(
            code, (GapSubType.ANOMALY_OTHER, GapSeverity.MEDIUM)
        )

        subject = grp.get("subject", code)
        occurrences = grp.get("occurrences", [])

        for occ in occurrences:
            source_file = occ.get("source_file")
            if _is_test_path(source_file):
                continue
            seq += 1
            node_id = occ.get("node_id", "")
            items.append(GapItem(
                gap_id=f"GAP_STR_{seq:03d}_{_slug(subject)}_{_slug(node_id)}",
                category=GapCategory.STRUCTURAL,
                sub_type=sub_type,
                severity=severity,
                subject=subject,
                location=GapLocation(
                    file=source_file,
                    line=occ.get("source_line"),
                    asg_node_id=node_id,
                ),
                detail=grp.get("issue_description", ""),
                suggestion=grp.get("ai_instruction", ""),
                ai_hints={
                    "action": "resolve_anomaly",
                    "anomaly_code": code,
                    "spark_snippet": occ.get("spark_snippet", ""),
                },
            ))

    return items


# ── Scoring gaps ──────────────────────────────────────────────────────────


def _collect_scoring_gaps(ctx: ReportContext) -> list[GapItem]:
    """Detect gaps that directly cause score loss."""
    items: list[GapItem] = []
    prod_in = [d for d in ctx.data_in if not d.get("is_test_file")]
    prod_out = [d for d in ctx.data_out if not d.get("is_test_file")]

    io_total = len(prod_in) + len(prod_out)
    total_cols = ctx.real_total_cols or 1

    seq_nm = 0
    seq_inf = 0
    seq_conf = 0

    # ── naming: unnamed / placeholder-named I/O ──
    # Uses same Strict Identity rule: PTH_/SNK_/SQL_/NOD_/VAR_ and runtime: are not real names.
    for d in prod_in + prod_out:
        if not _is_real_name(d):
            seq_nm += 1
            role = "input" if d in prod_in else "output"
            impact = -(25.0 / io_total) if io_total else 0
            items.append(GapItem(
                gap_id=f"GAP_NAM_{seq_nm:03d}_{_slug(d.get('type', 'unknown'))}",
                category=GapCategory.NAMING,
                sub_type=GapSubType.UNNAMED_IO,
                severity=GapSeverity.MEDIUM,
                subject=f"Unnamed {role} ({d.get('name', '?')} / {d.get('type', '?')})",
                score_impact=round(impact, 3),
                location=GapLocation(
                    file=d.get("source_file"),
                    scope=d.get("scope"),
                    asg_node_id=d.get("id"),
                ),
                detail=f"Data {role} has no resolved name (dynamic or missing path).",
                suggestion=f"Resolve the dynamic path to a static name in the source code.",
                ai_hints={
                    "action": "resolve_dynamic_path",
                    "io_type": d.get("type", ""),
                    "role": role,
                },
            ))

    # ── inference: inputs without columns ──
    # Structure (25 pts, data_in × 1.0) — each input without schema loses
    # approximately 25 / total_inputs from the Structure dimension.
    synth_total = len(prod_in) if prod_in else 1
    for d in prod_in:
        if not d.get("inferred_columns"):
            seq_inf += 1
            name = d.get("name", d.get("id", "unknown"))
            impact_structure = -(25.0 / synth_total) if synth_total else 0
            items.append(GapItem(
                gap_id=f"GAP_INF_{seq_inf:03d}_{_slug(name)}",
                category=GapCategory.INFERENCE,
                sub_type=GapSubType.NO_COLUMNS,
                severity=GapSeverity.HIGH,
                subject=name,
                score_impact=round(impact_structure, 3),
                location=GapLocation(
                    file=d.get("source_file"),
                    scope=d.get("scope"),
                    asg_node_id=d.get("id"),
                ),
                detail=f"Input '{name}' has zero detected columns.",
                suggestion=(
                    "Add a .select() or explicit column reference in the source code, "
                    "or connect to a catalog to retrieve the schema."
                ),
                ai_hints={
                    "action": "discover_columns",
                    "source_type": d.get("type", ""),
                    "source_name": name,
                },
            ))

    # ── inference: UNKNOWN-type columns / confidence: low weight ──
    for d in prod_in:
        name = d.get("name", d.get("id", "unknown"))
        for col in d.get("inferred_columns", []):
            raw_src = col.get("source", "")
            src = raw_src.value if hasattr(raw_src, "value") else str(raw_src)
            if "." in src:
                src = src.rsplit(".", 1)[-1].lower()

            raw_type = col.get("inferred_type", "UNKNOWN")
            inf_type = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
            col_name = col.get("name", "?")

            if inf_type == "UNKNOWN":
                seq_inf += 1
                impact = -(15.0 / total_cols)   # Typing dimension = 15 pts
                items.append(GapItem(
                    gap_id=f"GAP_INF_{seq_inf:03d}_{_slug(name)}_{_slug(col_name)}",
                    category=GapCategory.INFERENCE,
                    sub_type=GapSubType.UNKNOWN_TYPE,
                    severity=GapSeverity.MEDIUM,
                    subject=f"{name}.{col_name}",
                    score_impact=round(impact, 4),
                    location=GapLocation(
                        file=d.get("source_file"),
                        scope=d.get("scope"),
                        asg_node_id=d.get("id"),
                    ),
                    detail=f"Column '{col_name}' has type UNKNOWN (source: {src}).",
                    suggestion="Check source SQL or catalog for type definition.",
                    ai_hints={
                        "action": "infer_column_type",
                        "column_name": col_name,
                        "source_table": name,
                        "inference_source": src,
                    },
                ))
            else:
                weight = CONFIDENCE_WEIGHTS.get(src, 0.5)
                if weight < 0.8:
                    seq_conf += 1
                    penalty = (1.0 - weight) * (15.0 / total_cols)  # Typing = 15 pts
                    items.append(GapItem(
                        gap_id=f"GAP_CNF_{seq_conf:03d}_{_slug(name)}_{_slug(col_name)}",
                        category=GapCategory.CONFIDENCE,
                        sub_type=GapSubType.LOW_CONFIDENCE,
                        severity=GapSeverity.LOW,
                        subject=f"{name}.{col_name}",
                        score_impact=round(-penalty, 4),
                        location=GapLocation(
                            file=d.get("source_file"),
                            scope=d.get("scope"),
                            asg_node_id=d.get("id"),
                        ),
                        detail=(
                            f"Column '{col_name}' typed as {inf_type} via {src} "
                            f"(weight {weight})."
                        ),
                        suggestion=(
                            "Add an explicit cast or code evidence to raise confidence to 1.0."
                        ),
                        ai_hints={
                            "action": "strengthen_evidence",
                            "column_name": col_name,
                            "current_source": src,
                            "current_weight": weight,
                            "inferred_type": inf_type,
                        },
                    ))

    # ── confidence: ambiguous origin (column may belong to wrong source) ──
    seq_amb = 0
    for d in ctx.data_io:
        if d.get("role") != "input":
            continue
        name = d.get("name", "unknown")
        for col in d.get("columns", []):
            if col.get("confidence") != "ambiguous":
                continue
            seq_amb += 1
            col_name = col.get("name", "?")
            potential = col.get("potential_sources", [])
            reason = col.get("reason", "multi_origin_join")
            impact = -(15.0 * 0.5 / total_cols)  # Typing = 15 pts, ambiguous = 0.5 weight
            items.append(GapItem(
                gap_id=f"GAP_AMB_{seq_amb:03d}_{_slug(name)}_{_slug(col_name)}",
                category=GapCategory.CONFIDENCE,
                sub_type=GapSubType.AMBIGUOUS_ORIGIN,
                severity=GapSeverity.MEDIUM,
                subject=f"{name}.{col_name}",
                score_impact=round(impact, 4),
                location=GapLocation(
                    asg_node_id=d.get("id"),
                ),
                detail=(
                    f"Column '{col_name}' in '{name}' has ambiguous lineage. "
                    f"Potential sources: {', '.join(potential) if potential else 'unknown'}. "
                    f"Reason: {reason}."
                ),
                suggestion=(
                    f"Ambiguous lineage detected. To reach 100% confidence, "
                    f"qualify this column in your code (e.g., df['{col_name}']) "
                    f"or add an explicit .select() to the branches of the join."
                ),
                ai_hints={
                    "action": "disambiguate_column_origin",
                    "column_name": col_name,
                    "source_name": name,
                    "potential_sources": potential,
                    "reason": reason,
                },
            ))

    return items


# ── Cascade suppression ──────────────────────────────────────────────────


def _apply_cascade_suppression(report: GapReport) -> None:
    """If a file has a parsing blocker/critical, remove downstream gaps for that file."""
    blocked_files: set[str] = set()
    blocker_ids: dict[str, str] = {}

    for g in report.gaps:
        if (
            g.category == GapCategory.PARSING
            and g.severity in (GapSeverity.BLOCKER, GapSeverity.CRITICAL)
            and g.location.file
        ):
            blocked_files.add(g.location.file)
            blocker_ids[g.location.file] = g.gap_id

    if not blocked_files:
        return

    kept: list[GapItem] = []
    for g in report.gaps:
        loc_file = g.location.file
        if (
            loc_file in blocked_files
            and g.category != GapCategory.PARSING
        ):
            blocker = blocker_ids.get(loc_file, "")
            for bg in report.gaps:
                if bg.gap_id == blocker:
                    if g.gap_id not in bg.blocks:
                        bg.blocks.append(g.gap_id)
                    break
        else:
            kept.append(g)
    report.gaps = kept


# ── Helpers ───────────────────────────────────────────────────────────────


def _slug(value: str | None, max_len: int = 25) -> str:
    """Create a short, safe identifier fragment from a string."""
    if not value:
        return "unknown"
    import re
    s = re.sub(r"[^a-zA-Z0-9_]", "_", value)
    return s[:max_len].rstrip("_") or "unknown"
