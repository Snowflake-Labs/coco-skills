"""
WARP Intel Models — semantic telemetry for engineering feedback.

Captures every moment the WARP engine had to "surrender" or use a
heuristic fallback, with enough ASG context to reproduce the scenario
without the original source code.

Think of this as the flight black box: if the parser had to make a
manual maneuver (regex fallback, naming heuristic, unhandled pattern),
it is recorded here so a developer can write a new rule or test case.

Audience: WARP developers, not workload clients.
Output artifact: XX_WARP_INTEL.json
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TriggerType(str, Enum):
    """Classification of the fallback event."""

    REGEX_FALLBACK = "regex_fallback"
    INFERENCE_DEGRADATION = "inference_degradation"
    UNHANDLED_PATTERN = "unhandled_pattern"
    UNRESOLVED_REFERENCE = "unresolved_reference"
    AMBIGUOUS_ATTRIBUTION = "ambiguous_attribution"


class TriggerSeverity(str, Enum):
    """Impact of the fallback on analysis quality."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class WarpIntelTrigger(BaseModel):
    """A single fallback event captured by the engine."""

    type: TriggerType
    severity: TriggerSeverity

    component: str = Field(
        ...,
        description="WARP component that triggered the fallback (e.g. AnomalyDetector, ParameterResolver)",
    )
    context_asg_node: str | None = Field(
        default=None,
        description="ASG node ID involved (e.g. tx_12_join, in_001)",
    )
    target_variable: str | None = Field(
        default=None,
        description="Variable or column name being resolved (for inference_degradation)",
    )

    reason: str = Field(
        ...,
        description="Human-readable explanation of why the fallback occurred",
    )
    agnostic_snippet: str | None = Field(
        default=None,
        description="Agnostic pseudocode reconstruction of the logic that could not be fully parsed",
    )
    ai_hints: dict[str, Any] = Field(
        default_factory=dict,
        description="Machine-readable context for AI-assisted parser improvement",
    )
    suggested_fix: str = Field(
        default="",
        description="Actionable suggestion for improving the parser or resolver",
    )


class WarpIntelSummary(BaseModel):
    """Aggregated counts for quick triage."""

    total: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_component: dict[str, int] = Field(default_factory=dict)


class WarpIntelReport(BaseModel):
    """Complete WARP Intel report — the XX_WARP_INTEL.json artifact."""

    version: str = Field(default="1.0")
    workload_id: str = Field(default="unknown")
    engine_version: str = Field(default="unknown")
    generated_at: str | None = None

    summary: WarpIntelSummary = Field(default_factory=WarpIntelSummary)
    triggers: list[WarpIntelTrigger] = Field(default_factory=list)

    def add(self, trigger: WarpIntelTrigger) -> None:
        self.triggers.append(trigger)

    def finalize(self) -> None:
        """Recompute summary from current trigger list."""
        self.generated_at = datetime.utcnow().isoformat()

        type_counts = Counter(t.type.value for t in self.triggers)
        sev_counts = Counter(t.severity.value for t in self.triggers)
        comp_counts = Counter(t.component for t in self.triggers)

        self.summary = WarpIntelSummary(
            total=len(self.triggers),
            by_type=dict(type_counts),
            by_severity=dict(sev_counts),
            by_component=dict(comp_counts),
        )
