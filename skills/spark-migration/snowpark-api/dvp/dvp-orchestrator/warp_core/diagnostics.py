"""
Diagnostic Issue Models for WARP Tools.

Provides a common structure for reporting issues/gaps across all tools
in the pipeline, enabling systematic improvement prioritization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(Enum):
    """Issue severity levels."""
    ERROR = "error"      # Blocks processing or produces wrong results
    WARNING = "warning"  # Degraded results but processing continues
    INFO = "info"        # Informational, might be improved


class IssueCategory(Enum):
    """Common issue categories across tools."""
    # Parser
    SYNTAX = "syntax"
    UNDERSTANDING = "understanding"
    
    # Entrypoint
    DETECTION = "detection"
    IO_TRACKING = "io_tracking"
    INDIRECT_CALL = "indirect_call"
    
    # Schema
    TYPE_INFERENCE = "type_inference"
    COLUMN_TRACKING = "column_tracking"
    PROPAGATION = "propagation"
    CONFLICT = "conflict"
    MISSING_SCHEMA = "missing_schema"
    
    # Synthetic
    CONSTRAINT = "constraint"
    RELATIONSHIP = "relationship"
    UNKNOWN_TYPE = "unknown_type"
    GENERATION = "generation"


@dataclass
class DiagnosticIssue:
    """
    Base class for all diagnostic issues.
    
    Captures enough context to reproduce and fix issues
    without needing access to the original source code.
    """
    code: str  # e.g., "EP_001", "SCH_002"
    severity: Severity
    category: IssueCategory
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    suggestion: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "category": self.category.value,
            "message": self.message,
            "context": self.context,
            "suggestion": self.suggestion,
        }


@dataclass
class DiagnosticReport:
    """
    Collection of issues from a tool with summary statistics.
    """
    tool_name: str
    issues: list[DiagnosticIssue] = field(default_factory=list)
    
    @property
    def total(self) -> int:
        return len(self.issues)
    
    @property
    def by_severity(self) -> dict[str, int]:
        counts = {"error": 0, "warning": 0, "info": 0}
        for issue in self.issues:
            counts[issue.severity.value] += 1
        return counts
    
    @property
    def by_category(self) -> dict[str, int]:
        from collections import Counter
        return dict(Counter(i.category.value for i in self.issues))
    
    @property
    def by_code(self) -> dict[str, int]:
        from collections import Counter
        return dict(Counter(i.code for i in self.issues))
    
    def add(self, issue: DiagnosticIssue) -> None:
        self.issues.append(issue)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool_name,
            "summary": {
                "total": self.total,
                "by_severity": self.by_severity,
                "by_category": self.by_category,
                "by_code": self.by_code,
            },
            "issues": [i.to_dict() for i in self.issues],
        }


# Issue code enums for each tool
class EntrypointIssueCode:
    UNKNOWN_TYPE = "EP_001"      # File type couldn't be determined
    AMBIGUOUS_ENTRY = "EP_002"   # Multiple main guards
    INPUT_UNTRACED = "EP_003"    # Input not traced to entry
    OUTPUT_UNLINKED = "EP_004"   # Output from utility unlinked
    INDIRECT_ENTRY = "EP_005"    # Entry delegates to another


class SchemaIssueCode:
    TYPE_UNKNOWN = "SCH_001"     # Column type not inferred
    ORIGIN_UNKNOWN = "SCH_002"   # Column origin not found
    NO_PROPAGATION = "SCH_003"   # Type didn't propagate
    TYPE_CONFLICT = "SCH_004"    # Conflicting inferences
    NO_COLUMNS = "SCH_005"       # Source has no columns
    AMBIGUOUS_ORIGIN = "SCH_006" # Column origin ambiguous (multi-source)


class SyntheticIssueCode:
    CONSTRAINT_FAIL = "SYN_001"  # Couldn't satisfy constraint
    JOIN_FAIL = "SYN_002"        # Join relationship not honored
    TYPE_UNKNOWN = "SYN_003"     # No generator for type
    NO_SCHEMA = "SYN_004"        # Source has no schema
    CONFLICT = "SYN_005"         # Conflicting constraints


def generate_markdown_report(reports: list[DiagnosticReport], project_name: str = "project") -> str:
    """
    Generate a human-readable Markdown report from multiple diagnostic reports.
    
    Args:
        reports: List of DiagnosticReport from each tool
        project_name: Name of the project
        
    Returns:
        Markdown formatted string
    """
    from datetime import datetime
    
    lines = [
        f"# Diagnostic Report: {project_name}",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        "| Tool | Total | Errors | Warnings | Info |",
        "|------|-------|--------|----------|------|",
    ]
    
    total_all = 0
    total_errors = 0
    total_warnings = 0
    total_info = 0
    
    for report in reports:
        sev = report.by_severity
        total_all += report.total
        total_errors += sev.get("error", 0)
        total_warnings += sev.get("warning", 0)
        total_info += sev.get("info", 0)
        lines.append(
            f"| {report.tool_name} | {report.total} | {sev.get('error', 0)} | {sev.get('warning', 0)} | {sev.get('info', 0)} |"
        )
    
    lines.append(f"| **TOTAL** | **{total_all}** | **{total_errors}** | **{total_warnings}** | **{total_info}** |")
    lines.append("")
    
    # Issues by code (grouped)
    lines.append("## Issues by Code")
    lines.append("")
    
    for report in reports:
        if report.total == 0:
            continue
        lines.append(f"### {report.tool_name}")
        lines.append("")
        lines.append("| Code | Count | Severity | Description |")
        lines.append("|------|-------|----------|-------------|")
        
        # Group issues by code
        by_code: dict[str, list[DiagnosticIssue]] = {}
        for issue in report.issues:
            if issue.code not in by_code:
                by_code[issue.code] = []
            by_code[issue.code].append(issue)
        
        for code, issues in sorted(by_code.items()):
            sample = issues[0]
            lines.append(f"| {code} | {len(issues)} | {sample.severity.value.upper()} | {sample.message} |")
        
        lines.append("")
    
    # Top issues (sample with context)
    lines.append("## Sample Issues (for debugging)")
    lines.append("")
    
    for report in reports:
        # Show up to 3 WARNING issues per tool
        warnings = [i for i in report.issues if i.severity == Severity.WARNING][:3]
        if warnings:
            lines.append(f"### {report.tool_name} - Warnings")
            lines.append("")
            for issue in warnings:
                lines.append(f"**{issue.code}**: {issue.message}")
                lines.append("")
                lines.append("```json")
                import json
                lines.append(json.dumps(issue.context, indent=2))
                lines.append("```")
                lines.append("")
                if issue.suggestion:
                    lines.append(f"> 💡 {issue.suggestion}")
                    lines.append("")
    
    # Recommendations
    lines.append("## Recommendations")
    lines.append("")
    
    priorities = []
    for report in reports:
        sev = report.by_severity
        if sev.get("error", 0) > 0:
            priorities.append((1, report.tool_name, f"Fix {sev['error']} errors"))
        if sev.get("warning", 0) > 10:
            priorities.append((2, report.tool_name, f"Address {sev['warning']} warnings"))
    
    if priorities:
        lines.append("| Priority | Tool | Action |")
        lines.append("|----------|------|--------|")
        for prio, tool, action in sorted(priorities):
            lines.append(f"| {prio} | {tool} | {action} |")
    else:
        lines.append("No critical issues found.")
    
    lines.append("")
    lines.append("---")
    lines.append("*Report generated by WARP Diagnostic System*")
    
    return "\n".join(lines)
