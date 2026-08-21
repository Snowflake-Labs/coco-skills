"""
Intermediate Representation (IR) module.

Contains Pydantic models for the analysis pipeline and
the rules engine for Spark-to-Snowflake mappings.
"""

from warp_core.ir.pyspark_models import (
    AnalysisMetadata,
    CompatibilitySummary,
    ComplexityRisk,
    ExecutionStep,
    FeasibilityIR,
    TargetType,
)
from warp_core.ir.rules_engine import FunctionMapping, RulesEngine
from warp_core.ir.anomaly_models import (
    Anomaly,
    AnomalyCategory,
    AnomalyCode,
    AnomalyGroup,
    AnomalyOccurrence,
    AnomalyReport,
    AnomalySummary,
    DiagnosticHint,
    Severity,
    SourceLocation,
    create_anomaly,
    create_report,
    get_anomaly_metadata,
    group_anomalies,
)
from warp_core.ir.gap_models import (
    GapCategory,
    GapItem,
    GapLocation,
    GapReport,
    GapSeverity,
    GapSubType,
    GapSummary,
)

__all__ = [
    # Core IR models
    "AnalysisMetadata",
    "CompatibilitySummary",
    "ExecutionStep",
    "FeasibilityIR",
    "TargetType",
    "ComplexityRisk",
    "RulesEngine",
    "FunctionMapping",
    # Anomaly models
    "Anomaly",
    "AnomalyCategory",
    "AnomalyCode",
    "AnomalyGroup",
    "AnomalyOccurrence",
    "AnomalyReport",
    "AnomalySummary",
    "DiagnosticHint",
    "Severity",
    "SourceLocation",
    "create_anomaly",
    "create_report",
    "get_anomaly_metadata",
    "group_anomalies",
    # Gap models
    "GapCategory",
    "GapItem",
    "GapLocation",
    "GapReport",
    "GapSeverity",
    "GapSubType",
    "GapSummary",
]
