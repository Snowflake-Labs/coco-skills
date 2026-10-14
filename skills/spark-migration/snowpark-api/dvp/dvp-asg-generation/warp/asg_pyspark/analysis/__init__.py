"""
Analysis module for ASG processing.

Contains:
- audit_reporter: Dead code analysis and migration audit reports
- sink_inliner: Collapse transformations into CTEs per Sink
- collapsing: Node fusion for linear chains
- anomaly_detector: Quality analysis and issue detection
"""

from asg_pyspark.analysis.audit_reporter import (
    AuditReport,
    AuditReporter,
    FunctionStatus,
)
from asg_pyspark.analysis.sink_inliner import (
    SinkInliner,
    SinkPipeline,
)

# Alias for compatibility
SinkFirstInliner = SinkInliner
from asg_pyspark.analysis.collapsing import (
    CollapsedNode,
    CollapsingEngine,
    CollapsingResult,
)
from asg_pyspark.analysis.anomaly_detector import (
    AnomalyDetector,
    analyze_asg,
    analyze_asg_file,
)

__all__ = [
    # Audit Reporter
    "AuditReporter",
    "AuditReport",
    "FunctionStatus",
    # Sink Inliner
    "SinkFirstInliner",
    "SinkPipeline",
    # Collapsing
    "CollapsingEngine",
    "CollapsedNode",
    "CollapsingResult",
    # Anomaly Detection
    "AnomalyDetector",
    "analyze_asg",
    "analyze_asg_file",
]
