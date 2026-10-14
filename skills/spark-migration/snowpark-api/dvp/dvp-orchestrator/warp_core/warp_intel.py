"""
WARP Intel Builder — collects semantic telemetry from existing pipeline signals.

Phase 1 sources (no new parser instrumentation required):
1. PAR_001 anomalies  → type: regex_fallback
2. inference_warnings from asg.parsing_report → type: inference_degradation

Phase 2 (future): hooks inside ExpressionEvaluator and ParameterResolver
will emit triggers directly via is_opaque flags and resolver level events.
"""

from __future__ import annotations

from typing import Any

from warp_core.ir.warp_intel_models import (
    TriggerSeverity,
    TriggerType,
    WarpIntelReport,
    WarpIntelTrigger,
)

# Anomaly codes that indicate Warp tool limitations (not client code issues).
# These are routed to WARP_INTEL instead of gaps.json.
_INTERNAL_ANOMALY_CODES: set[str] = {"PAR_001"}


def build_warp_intel(
    asg: dict[str, Any],
    anomalies: dict[str, Any] | list | None,
    workload_id: str = "unknown",
    engine_version: str = "unknown",
    data_io: list[dict[str, Any]] | None = None,
) -> WarpIntelReport:
    """Build the WARP Intel report from existing pipeline signals.

    Parameters
    ----------
    asg:
        Parsed ASG dict (contains ``parsing_report`` with inference_warnings).
    anomalies:
        Content of anomalies.json (dict or list).
    workload_id:
        Project/workload name for the report header.
    engine_version:
        WARP engine version string.

    Returns
    -------
    WarpIntelReport
        Finalized report ready for serialization.
    """
    report = WarpIntelReport(workload_id=workload_id, engine_version=engine_version)

    _collect_inference_degradations(asg, report)
    _collect_regex_fallbacks(anomalies, report)
    _collect_ambiguous_attributions(data_io or [], report)

    report.finalize()
    return report


# ── Inference degradations (from parsing_report.inference_warnings) ────────


def _collect_inference_degradations(
    asg: dict[str, Any], report: WarpIntelReport
) -> None:
    """Convert inference_warnings into inference_degradation triggers."""
    pr = asg.get("parsing_report") or {}
    warnings = pr.get("inference_warnings", [])

    for warn in warnings:
        method = warn.get("method", "unknown")
        resolution = warn.get("resolution", "unknown")
        reason_parts = [f"Resolved via {resolution}."]
        if warn.get("reason"):
            reason_parts.append(warn["reason"])

        report.add(WarpIntelTrigger(
            type=TriggerType.INFERENCE_DEGRADATION,
            severity=TriggerSeverity.MEDIUM,
            component="ParameterResolver",
            context_asg_node=None,
            target_variable=f"{warn.get('path', '?')}:{method}",
            reason=" ".join(reason_parts),
            agnostic_snippet=warn.get("code_snippet"),
            ai_hints={
                "action": "refine_type_inference",
                "method": method,
                "receiver": warn.get("receiver", ""),
                "inference_source": resolution,
                "file": warn.get("path"),
                "line": warn.get("line"),
            },
            suggested_fix=warn.get("suggestion", ""),
        ))


# ── Regex fallbacks (from PAR_001 anomalies) ───────────────────────────────


def _collect_regex_fallbacks(
    anomalies: dict[str, Any] | list | None,
    report: WarpIntelReport,
) -> None:
    """Convert PAR_001 anomaly occurrences into regex_fallback triggers."""
    if not anomalies:
        return

    groups: list[dict] = (
        anomalies.get("anomalies", [])
        if isinstance(anomalies, dict)
        else anomalies
    )

    for grp in groups:
        code = grp.get("code", "")
        if code not in _INTERNAL_ANOMALY_CODES:
            continue

        for occ in grp.get("occurrences", []):
            node_id = occ.get("node_id", "")
            snippet = occ.get("spark_snippet", "")

            report.add(WarpIntelTrigger(
                type=TriggerType.REGEX_FALLBACK,
                severity=TriggerSeverity.MEDIUM,
                component="AnomalyDetector",
                context_asg_node=node_id or None,
                reason=grp.get("issue_description", f"{code}: regex fallback used instead of AST match."),
                agnostic_snippet=snippet or None,
                ai_hints={
                    "action": "improve_parser_grammar",
                    "anomaly_code": code,
                    "spark_snippet": snippet,
                    "source_file": occ.get("source_file"),
                    "source_line": occ.get("source_line"),
                },
                suggested_fix=grp.get("ai_instruction", "Improve grammar rule to handle this pattern via AST instead of regex."),
            ))


# ── Ambiguous column attributions (from DataIODetector) ─────────────────


def _collect_ambiguous_attributions(
    data_io: list[dict[str, Any]], report: WarpIntelReport
) -> None:
    """Convert ambiguous column attributions into warp intel triggers."""
    for item in data_io:
        if item.get("role") != "input":
            continue
        source_name = item.get("name", "unknown")
        for col in item.get("columns", []):
            if col.get("confidence") != "ambiguous":
                continue
            col_name = col.get("name", "?")
            potential = col.get("potential_sources", [])
            reason = col.get("reason", "multi_origin_join")
            report.add(WarpIntelTrigger(
                type=TriggerType.AMBIGUOUS_ATTRIBUTION,
                severity=TriggerSeverity.MEDIUM,
                component="DataIODetector",
                context_asg_node=None,
                target_variable=f"{source_name}.{col_name}",
                reason=(
                    f"Column '{col_name}' is ambiguous in '{source_name}' "
                    f"because neither branch of the upstream join has a ceiling "
                    f"(select/groupBy). Potential sources: {', '.join(potential)}. "
                    f"Semantic Scorer (Phase 3) would resolve this by comparing "
                    f"column-name affinity against table names."
                ),
                ai_hints={
                    "action": "implement_semantic_scorer",
                    "column_name": col_name,
                    "source_name": source_name,
                    "potential_sources": potential,
                    "reason": reason,
                    "phase": "3.1_semantic_disambiguation",
                },
                suggested_fix=(
                    f"Add a .select() before the join to make column "
                    f"'{col_name}' ownership explicit."
                ),
            ))
