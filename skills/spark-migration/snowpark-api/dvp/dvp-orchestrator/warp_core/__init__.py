"""WARP Core - IR models, slicer, and schema utilities."""

from warp_core.ir.pyspark_models import (
    ASG,
    DataSink,
    DataSource,
    FunctionDefinition,
    TransformationNode,
)
from warp_core.slicer.slicer import GraphSlicer

# Aliases
FunctionInfo = FunctionDefinition
Transformation = TransformationNode

__all__ = [
    "ASG",
    "DataSink",
    "DataSource",
    "FunctionDefinition",
    "FunctionInfo",
    "TransformationNode",
    "Transformation",
    "GraphSlicer",
]

# Diagnostic models
from warp_core.diagnostics import (
    Severity,
    IssueCategory,
    DiagnosticIssue,
    DiagnosticReport,
    EntrypointIssueCode,
    SchemaIssueCode,
    SyntheticIssueCode,
)

from warp_core.unified_report import generate_unified_report

