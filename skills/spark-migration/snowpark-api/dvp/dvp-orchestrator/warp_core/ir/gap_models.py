"""
Gap Analysis Models for unified defect tracking.

Consolidates three formerly independent defect sources into a single
taxonomy and data structure:

1. Parsing errors   (syntax, understanding, inference warnings)
2. Structural gaps  (anomalies from AnomalyDetector)
3. Scoring gaps     (unnamed I/O, missing columns, UNKNOWN types, low confidence)

The resulting GapReport is serialized as ``gaps.json`` and consumed by
AI agents for automated remediation.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class GapCategory(str, Enum):
    """Top-level gap category."""

    PARSING = "parsing"
    STRUCTURAL = "structural"
    NAMING = "naming"
    INFERENCE = "inference"
    CONFIDENCE = "confidence"


class GapSubType(str, Enum):
    """Sub-type within each category."""

    # parsing (client-visible: file could not be parsed or understood)
    SYNTAX_ERROR = "syntax_error"
    UNDERSTANDING_ERROR = "understanding_error"
    # NOTE: INFERENCE_WARNING removed — routed to WARP_INTEL (tool limitation, not client issue)

    # structural (mapped from anomaly codes)
    CONSTRAINT_CONTRADICTION = "constraint_contradiction"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    Z3_UNSAT = "z3_unsat"
    LINEAGE_BROKEN = "lineage_broken"
    ORPHAN_RESOURCE = "orphan_resource"
    DANGLING_NODE = "dangling_node"
    MISSING_SINK = "missing_sink"
    SCHEMA_UNKNOWN = "schema_unknown"
    PHANTOM_COLUMN = "phantom_column"
    TYPE_ENTROPY = "type_entropy"
    MISSING_CAST = "missing_cast"
    BLACKBOX_UDF = "blackbox_udf"
    UNRESOLVED_PARAM = "unresolved_param"
    UNCLEAR_JOIN = "unclear_join"
    # NOTE: REGEX_FALLBACK removed — routed to WARP_INTEL (PAR_001 is a tool limitation)
    ANOMALY_OTHER = "anomaly_other"

    # naming
    UNNAMED_IO = "unnamed_io"

    # inference
    NO_COLUMNS = "no_columns"
    UNKNOWN_TYPE = "unknown_type"

    # confidence
    LOW_CONFIDENCE = "low_confidence"
    AMBIGUOUS_ORIGIN = "ambiguous_origin"


class GapSeverity(str, Enum):
    """Severity ranking (descending impact)."""

    BLOCKER = "blocker"
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_ORDER = {
    GapSeverity.BLOCKER: 0,
    GapSeverity.CRITICAL: 1,
    GapSeverity.HIGH: 2,
    GapSeverity.MEDIUM: 3,
    GapSeverity.LOW: 4,
}


class GapLocation(BaseModel):
    """Where in the codebase the gap originates."""

    file: str | None = Field(default=None, description="Source file path")
    line: int | None = Field(default=None, description="Line number hint")
    scope: str | None = Field(default=None, description="Function / class scope")
    asg_node_id: str | None = Field(default=None, description="ASG node ID (in_*, tx_*, out_*)")


class GapItem(BaseModel):
    """A single gap -- one specific reason the score is not 100 %."""

    gap_id: str = Field(..., description="Unique identifier, e.g. GAP_INF_001_TABLE_col")
    category: GapCategory
    sub_type: GapSubType
    severity: GapSeverity

    subject: str = Field(..., description="Human-readable subject (table name, column, file)")
    score_impact: float = Field(
        default=0.0,
        description="Estimated score-points lost (negative or zero)",
    )

    location: GapLocation = Field(default_factory=GapLocation)
    detail: str = Field(default="", description="Explanation of the gap")
    suggestion: str = Field(default="", description="Actionable suggestion")

    ai_hints: dict[str, Any] = Field(
        default_factory=dict,
        description="Machine-readable hints for AI agents",
    )
    blocks: list[str] = Field(
        default_factory=list,
        description="gap_ids suppressed by this gap (cascade)",
    )


class GapSummary(BaseModel):
    """Aggregated counts for quick triage."""

    total: int = 0
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    total_score_impact: float = 0.0


class GapReport(BaseModel):
    """Complete gap report -- the ``gaps.json`` artifact."""

    version: str = Field(default="1.0")
    project_name: str = Field(default="Workload")
    generated_at: str | None = None
    score: float | None = None

    summary: GapSummary = Field(default_factory=GapSummary)
    gaps: list[GapItem] = Field(default_factory=list)

    def add(self, item: GapItem) -> None:
        self.gaps.append(item)

    def finalize(self, score: float | None = None) -> None:
        """Recompute summary from the current gap list."""
        self.score = score
        self.generated_at = datetime.utcnow().isoformat()

        from collections import Counter

        sev_counts = Counter(g.severity.value for g in self.gaps)
        cat_counts = Counter(g.category.value for g in self.gaps)
        total_impact = sum(g.score_impact for g in self.gaps)

        self.summary = GapSummary(
            total=len(self.gaps),
            by_severity={s.value: sev_counts.get(s.value, 0) for s in GapSeverity},
            by_category={c.value: cat_counts.get(c.value, 0) for c in GapCategory},
            total_score_impact=round(total_impact, 2),
        )

    def sorted_gaps(self) -> list[GapItem]:
        """Return gaps sorted by severity (blocker first), then score impact."""
        return sorted(
            self.gaps,
            key=lambda g: (SEVERITY_ORDER.get(g.severity, 99), g.score_impact),
        )
