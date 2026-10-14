"""
Anomaly Reporting Models for ASG Migration Quality Assurance.

This module defines the taxonomy and data structures for detecting,
reporting, and resolving anomalies during PySpark-to-Snowflake migration.

The Anomaly Report acts as a "satellite" file to the ASG/ASG-A, providing:
1. Diagnostic hints for AI-assisted resolution
2. Progress tracking for migration teams
3. Quality metrics and KPIs

Architecture:
    ASG-A (Blueprint) + Anomaly Report (Diagnostics) = Complete Migration Picture
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# =============================================================================
# Anomaly Taxonomy (Standard Codes)
# =============================================================================


class AnomalyCategory(str, Enum):
    """High-level categorization of anomaly types."""
    
    SCHEMA = "SCH"      # Schema and type-related issues
    LINEAGE = "LIN"     # Data flow and lineage issues
    LOGIC = "LOG"       # Business logic and UDF issues
    REFERENCE = "REF"   # Broken references and missing resources
    JOIN = "JOIN"       # Join-related issues
    CONTROL = "CTRL"    # Control flow and branching issues
    PARSE = "PAR"       # Parsing quality issues (regex fallback usage)


class AnomalyCode(str, Enum):
    """
    Standard anomaly codes following the pattern: CATEGORY_NNN
    
    Each code maps to a specific detection rule and resolution strategy.
    """
    
    # Schema Anomalies (SCH_*)
    SCH_001 = "SCH_001"  # Type UNKNOWN - Column has L_UNKNOWN type
    SCH_002 = "SCH_002"  # Phantom Column - Referenced but not declared in input
    SCH_003 = "SCH_003"  # Type Entropy - Type changes unexpectedly through flow
    SCH_004 = "SCH_004"  # Missing Cast - Numeric operation without defensive cast
    
    # Lineage Anomalies (LIN_*)
    LIN_001 = "LIN_001"  # Truncated Flow - Transformation has no source_id
    LIN_002 = "LIN_002"  # Orphan Resource - Input/Output not connected to flow
    LIN_003 = "LIN_003"  # Dangling Node - Transformation produces unused output
    LIN_004 = "LIN_004"  # Missing Sink - Flow ends without writing to output
    LIN_005 = "LIN_005"  # Empty Fallback - DataFrame created empty as fallback pattern
    
    # Logic Anomalies (LOG_*)
    LOG_001 = "LOG_001"  # Black-box UDF - External library function
    LOG_002 = "LOG_002"  # Python Magic - Dynamic attributes (getattr/setattr)
    LOG_003 = "LOG_003"  # Untranslatable - Spark operation without SQL equivalent
    LOG_004 = "LOG_004"  # Side Effect - Operation with external dependencies
    
    # Reference Anomalies (REF_*)
    REF_001 = "REF_001"  # Broken Reference - Table/column doesn't exist
    REF_002 = "REF_002"  # Unresolved Param - param_* without binding
    REF_003 = "REF_003"  # Missing Import - Referenced module not found
    REF_004 = "REF_004"  # Circular Dependency - Nodes reference each other
    
    # Join Anomalies (JOIN_*)
    JOIN_001 = "JOIN_001"  # Unclear Join Key - No explicit join condition
    JOIN_002 = "JOIN_002"  # Cross Join Warning - Potential cartesian product
    JOIN_003 = "JOIN_003"  # Key Type Mismatch - Join keys have different types
    
    # Control Flow Anomalies (CTRL_*)
    CTRL_001 = "CTRL_001"  # Ambiguous Branch - Multiple paths produce same output
    CTRL_002 = "CTRL_002"  # Dead Code - Branch that's never executed
    CTRL_003 = "CTRL_003"  # Missing Convergence - Branches don't merge properly

    # Parsing Quality Anomalies (PAR_*)
    PAR_001 = "PAR_001"    # Regex Fallback - AST/sqlglot failed, regex used instead


class Severity(str, Enum):
    """
    Anomaly severity levels affecting migration workflow.
    
    CRITICAL: Blocks SQL generation - must be resolved
    MEDIUM: Generates with warnings - should be reviewed
    LOW: Informational - can be ignored or deferred
    """
    
    CRITICAL = "CRITICAL"  # Blocking: Cannot generate valid SQL
    MEDIUM = "MEDIUM"      # Warning: Generates but may have issues
    LOW = "LOW"            # Quality improvement opportunity
    INFO = "INFO"          # Informational: Known pattern, no action needed


def get_category(code: AnomalyCode) -> AnomalyCategory:
    """Extract category from anomaly code."""
    prefix = code.value.split("_")[0]
    return AnomalyCategory(prefix)


# =============================================================================
# Diagnostic Hint Models
# =============================================================================


class SourceLocation(BaseModel):
    """Location in source code for diagnostic purposes."""
    
    file: str = Field(description="Source file path (relative to workload root)")
    line: int = Field(description="Line number in source file")
    column: int | None = Field(default=None, description="Column number if available")
    function: str | None = Field(default=None, description="Enclosing function name")
    
    def __str__(self) -> str:
        return f"{self.file}:{self.line}"


class DiagnosticHint(BaseModel):
    """
    AI-friendly diagnostic information for anomaly resolution.
    
    Contains all context needed for an AI to understand and fix the issue
    without accessing the original source code.
    """
    
    source_location: SourceLocation = Field(
        description="Where the anomaly occurs in source code"
    )
    spark_snippet: str = Field(
        description="Relevant Spark/Python code snippet"
    )
    issue_description: str = Field(
        description="Human-readable explanation of what's wrong"
    )
    ai_instruction: str = Field(
        description="Specific instruction for AI resolution"
    )
    suggested_fix: str | None = Field(
        default=None,
        description="Suggested code or configuration fix"
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context (column names, types, etc.)"
    )


# =============================================================================
# Anomaly and Report Models
# =============================================================================


class Anomaly(BaseModel):
    """
    A single detected anomaly in the ASG.
    
    Contains all information needed to understand, track, and resolve the issue.
    """
    
    anomaly_id: str = Field(
        description="Unique identifier (e.g., ANOM_01_001)"
    )
    code: AnomalyCode = Field(
        description="Standard anomaly code from taxonomy"
    )
    severity: Severity = Field(
        description="Impact level of this anomaly"
    )
    target_node: str = Field(
        description="ID of the affected ASG node (tx_*, in_*, out_*, LU_*)"
    )
    target_type: str = Field(
        description="Type of affected node (transformation, source, sink, logical_unit)"
    )
    diagnostic_hints: DiagnosticHint = Field(
        description="AI-friendly diagnostic information"
    )
    resolved: bool = Field(
        default=False,
        description="Whether this anomaly has been addressed"
    )
    resolution: str | None = Field(
        default=None,
        description="Description of how the anomaly was resolved"
    )
    resolved_at: datetime | None = Field(
        default=None,
        description="When the anomaly was resolved"
    )
    resolved_by: str | None = Field(
        default=None,
        description="Who/what resolved the anomaly (human, AI, auto)"
    )
    
    @property
    def category(self) -> AnomalyCategory:
        """Get the category of this anomaly."""
        return get_category(self.code)
    
    @property
    def is_blocking(self) -> bool:
        """Whether this anomaly blocks SQL generation."""
        return self.severity == Severity.CRITICAL and not self.resolved


class AnomalySummary(BaseModel):
    """Summary statistics for an anomaly report."""
    
    total: int = Field(description="Total number of unique anomalies")
    groups: int = Field(default=0, description="Same as total (kept for backward compatibility)")
    resolved: int = Field(default=0, description="Anomalies already addressed")
    unresolved: int = Field(default=0, description="Anomalies still pending")
    by_severity: dict[str, int] = Field(
        default_factory=dict,
        description="Unresolved anomalies by severity level"
    )
    by_category: dict[str, int] = Field(
        default_factory=dict,
        description="Anomalies by category (SCH, LIN, REF, etc.)"
    )
    by_group: dict[str, int] = Field(
        default_factory=dict,
        description="Anomalies by code (SCH_001, REF_002, etc.)"
    )
    blocking_count: int = Field(
        default=0,
        description="CRITICAL unresolved anomalies that block generation"
    )
    
    @property
    def can_generate(self) -> bool:
        """Whether SQL generation can proceed (no blocking anomalies)."""
        return self.blocking_count == 0




class AnomalyOccurrence(BaseModel):
    """Single occurrence of an anomaly (location detail)."""
    
    node_id: str = Field(description="Affected node ID (tx_*, in_*, out_*)")
    node_type: str = Field(description="Type of node")
    spark_snippet: str = Field(description="Relevant code snippet")
    source_file: str | None = Field(default=None, description="Source file if available")
    source_line: int | None = Field(default=None, description="Line number if available")


class ColumnOrigin(BaseModel):
    """Origin information for a column - where it was first defined."""
    
    source_dataframe: str | None = Field(
        default=None,
        description="Name of the DataFrame/table where column originates"
    )
    first_seen_node: str | None = Field(
        default=None,
        description="First node ID where column appears (in_*, tx_*)"
    )
    created_by_operation: str | None = Field(
        default=None,
        description="Operation that created this column (withColumn, alias, join)"
    )


class TransformationStep(BaseModel):
    """A single transformation in the lineage chain."""
    
    node_id: str = Field(description="Transformation node ID")
    operation: str = Field(description="Operation type (join, filter, withColumn)")
    logic: str = Field(description="Spark expression/code")
    affects_column: bool = Field(
        default=True,
        description="Whether this transformation directly affects the subject column"
    )


class AnomalyContext(BaseModel):
    """
    Rich context for anomaly resolution without source code access.
    
    Contains all information needed to understand and fix the anomaly
    when the original source code is not available.
    """
    
    # Where the column/subject comes from
    origin: ColumnOrigin | None = Field(
        default=None,
        description="Origin of the affected column/subject"
    )
    
    # Path through the pipeline
    lineage_summary: str | None = Field(
        default=None,
        description="Human-readable lineage path (e.g., 'in_001 → tx_003 → tx_007')"
    )
    
    # Detailed transformation chain
    transformation_chain: list[TransformationStep] = Field(
        default_factory=list,
        description="Ordered list of transformations affecting this subject"
    )
    
    # Schema at the point of anomaly
    inferred_schema: dict[str, str] = Field(
        default_factory=dict,
        description="Column names to inferred types at anomaly location"
    )
    
    # Related columns (for joins, expressions)
    related_columns: list[str] = Field(
        default_factory=list,
        description="Other columns involved in the same expression/operation"
    )
    
    # Input DataFrames involved
    input_dataframes: list[str] = Field(
        default_factory=list,
        description="Names of input DataFrames/tables involved"
    )


class AnomalyGroup(BaseModel):
    """
    Grouped anomalies sharing the same code and root cause.
    
    Instead of N separate anomalies for 'column X has UNKNOWN type',
    we group them: one entry with N occurrences listing where it happens.
    """
    
    group_id: str = Field(description="Unique group identifier")
    code: AnomalyCode = Field(description="Anomaly code shared by all in group")
    severity: Severity = Field(description="Severity level")
    
    # What the group is about
    subject: str = Field(
        description="The common subject (e.g., column name, UDF name, param name)"
    )
    subject_type: str = Field(
        description="Type of subject: 'column', 'udf', 'parameter', 'resource'"
    )
    
    # AI hint for the entire group
    issue_description: str = Field(description="What's wrong")
    ai_instruction: str = Field(description="How to fix it once for all occurrences")
    suggested_fix: str | None = Field(default=None, description="Suggested resolution")
    
    # Where it occurs
    occurrences: list[AnomalyOccurrence] = Field(
        description="List of all places where this anomaly manifests"
    )
    
    # Rich context for debugging without source code
    context: AnomalyContext | None = Field(
        default=None,
        description="Rich context including lineage, schema, and transformation chain"
    )
    
    # Resolution tracking (per group, not per occurrence)
    resolved: bool = Field(
        default=False,
        description="Whether this anomaly group has been addressed"
    )
    resolution: str | None = Field(
        default=None,
        description="Description of how the anomaly was resolved"
    )
    resolved_at: datetime | None = Field(
        default=None,
        description="When the anomaly was resolved"
    )
    resolved_by: str | None = Field(
        default=None,
        description="Who/what resolved the anomaly (human, AI, auto)"
    )
    
    @property
    def count(self) -> int:
        """Number of occurrences in this group."""
        return len(self.occurrences)
    
    @property
    def category(self) -> AnomalyCategory:
        """Get the category of this anomaly group."""
        return get_category(self.code)
    
    @property
    def is_blocking(self) -> bool:
        """Whether this anomaly group blocks SQL generation."""
        return self.severity == Severity.CRITICAL and not self.resolved



class AnomalyReport(BaseModel):
    """
    Complete anomaly report for a migration workload.
    
    This is the "satellite" file that accompanies the ASG/ASG-A,
    providing diagnostic information without polluting the blueprint.
    """
    
    # Identification
    migration_id: str = Field(
        description="Unique identifier for this migration (e.g., example_01)"
    )
    generated_at: datetime | None = Field(
        default=None,
        description="When this report was generated (ISO format)"
    )
    
    # Links to related files
    source_asg: str = Field(
        description="Path to the source ASG JSON file"
    )
    source_asg_agnostic: str | None = Field(
        default=None,
        description="Path to the ASG-Agnostic JSON file (if generated)"
    )
    
    # Content - anomalies are always grouped for efficiency
    anomalies: list[AnomalyGroup] = Field(
        default_factory=list,
        description="Anomalies grouped by code+subject (column, UDF, param, etc.)"
    )
    summary: AnomalySummary = Field(
        default_factory=lambda: AnomalySummary(total=0),
        description="Summary statistics"
    )
    
    # Metadata
    detector_version: str = Field(
        default="1.0.0",
        description="Version of the anomaly detector"
    )
    detection_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Configuration used for detection"
    )
    
    def add_group(self, group: AnomalyGroup) -> None:
        """Add an anomaly group and update summary."""
        self.anomalies.append(group)
        self._update_summary()
    
    def resolve_group(
        self,
        group_id: str,
        resolution: str,
        resolved_by: str = "manual"
    ) -> bool:
        """Mark an anomaly group as resolved."""
        for group in self.anomalies:
            if group.group_id == group_id:
                group.resolved = True
                group.resolution = resolution
                group.resolved_at = datetime.utcnow()
                group.resolved_by = resolved_by
                self._update_summary()
                return True
        return False
    
    def get_blocking(self) -> list[AnomalyGroup]:
        """Get all anomaly groups that block SQL generation."""
        return [g for g in self.anomalies if g.is_blocking]
    
    def get_by_code(self, code: AnomalyCode) -> list[AnomalyGroup]:
        """Get all anomaly groups with a specific code."""
        return [g for g in self.anomalies if g.code == code]
    
    def get_by_category(self, category: AnomalyCategory) -> list[AnomalyGroup]:
        """Get all anomaly groups in a category."""
        return [g for g in self.anomalies if g.category == category]
    
    @property
    def total_occurrences(self) -> int:
        """Total number of individual occurrences across all groups."""
        return sum(g.count for g in self.anomalies)
    
    def _update_summary(self) -> None:
        """Recalculate summary statistics counting unique groups (not occurrences)."""
        from collections import Counter
        
        num_groups = len(self.anomalies)
        
        code_counts = Counter(g.code.value for g in self.anomalies)
        
        self.summary = AnomalySummary(
            total=num_groups,
            groups=num_groups,
            resolved=sum(1 for g in self.anomalies if g.resolved),
            unresolved=sum(1 for g in self.anomalies if not g.resolved),
            by_severity={
                sev.value: sum(1 for g in self.anomalies if g.severity == sev and not g.resolved)
                for sev in Severity
            },
            by_category={
                cat.value: sum(1 for g in self.anomalies if g.category == cat)
                for cat in AnomalyCategory
            },
            by_group={
                code: count for code, count in code_counts.items()
            },
            blocking_count=sum(1 for g in self.anomalies if g.is_blocking),
        )


# =============================================================================
# Anomaly Code Metadata (AI Hint Templates)
# =============================================================================


ANOMALY_METADATA: dict[AnomalyCode, dict[str, Any]] = {
    # Schema
    AnomalyCode.SCH_001: {
        "category": AnomalyCategory.SCHEMA,
        "default_severity": Severity.MEDIUM,
        "title": "Unknown Column Type",
        "description": "Column type could not be inferred from the source code.",
        "ai_hint_template": (
            "Column '{column}' has type L_UNKNOWN. "
            "Infer type based on operators: * -> DECIMAL, + with strings -> TEXT."
        ),
    },
    AnomalyCode.SCH_002: {
        "category": AnomalyCategory.SCHEMA,
        "default_severity": Severity.CRITICAL,
        "title": "Phantom Column",
        "description": "Column is referenced but not declared in any input schema.",
        "ai_hint_template": (
            "Column '{column}' is used but not found in input. "
            "Check if it's an alias or comes from a missing join."
        ),
    },
    AnomalyCode.LIN_001: {
        "category": AnomalyCategory.LINEAGE,
        "default_severity": Severity.CRITICAL,
        "title": "Truncated Flow",
        "description": "Transformation has no source_id - lineage is broken.",
        "ai_hint_template": (
            "Node '{node_id}' has no source. "
            "Check if variable was renamed or assigned from function call."
        ),
    },
    AnomalyCode.LOG_001: {
        "category": AnomalyCategory.LOGIC,
        "default_severity": Severity.CRITICAL,
        "title": "Black-box UDF",
        "description": "UDF references external library without visible source code.",
        "ai_hint_template": (
            "UDF '{udf_name}' calls external function '{external_function}'. "
            "Options: Map to Snowflake equivalent, create Snowpark UDF, or use MASKING POLICY."
        ),
    },
    AnomalyCode.REF_002: {
        "category": AnomalyCategory.REFERENCE,
        "default_severity": Severity.CRITICAL,
        "title": "Unresolved Parameter",
        "description": "Function parameter (param_*) has no binding to actual source.",
        "ai_hint_template": (
            "Parameter '{param_name}' has no binding. "
            "Check execution_instances for missing input_map entry."
        ),
    },
    AnomalyCode.JOIN_001: {
        "category": AnomalyCategory.JOIN,
        "default_severity": Severity.MEDIUM,
        "title": "Unclear Join Key",
        "description": "Join operation without explicit join condition detected.",
        "ai_hint_template": (
            "Join has no explicit key. Inferred key: '{inferred_key}'. "
            "Confirm or specify correct column."
        ),
    },
    AnomalyCode.PAR_001: {
        "category": AnomalyCategory.PARSE,
        "default_severity": Severity.MEDIUM,
        "title": "Regex Fallback Parsing",
        "description": (
            "Primary parser (AST/sqlglot) failed; extraction was completed "
            "using regex fallback. Results are best-effort and may be incomplete."
        ),
        "ai_hint_template": (
            "Parser failed at '{source_file}:{source_line}' due to: {failure_reason}. "
            "Regex recovered: {recovered_elements}. "
            "Verify the extracted structure matches the original code intent."
        ),
    },
}


def get_anomaly_metadata(code: AnomalyCode) -> dict[str, Any]:
    """Get metadata for an anomaly code."""
    return ANOMALY_METADATA.get(code, {
        "default_severity": Severity.MEDIUM,
        "title": code.value,
        "description": "Unknown anomaly type",
        "ai_hint_template": "Review and fix manually.",
    })


# =============================================================================
# Factory Functions
# =============================================================================


def create_anomaly(
    code: AnomalyCode,
    target_node: str,
    target_type: str,
    source_file: str,
    source_line: int,
    spark_snippet: str,
    migration_id: str = "unknown",
    sequence: int = 1,
    severity: Severity | None = None,
    **context: Any,
) -> Anomaly:
    """
    Factory function to create an Anomaly with proper defaults.
    
    Uses the anomaly code's metadata to generate appropriate hints.
    """
    metadata = get_anomaly_metadata(code)
    
    # Use default severity from metadata if not specified
    if severity is None:
        severity = metadata.get("default_severity", Severity.MEDIUM)
    
    # Generate AI instruction from template
    template = metadata.get("ai_hint_template", "Review and fix manually.")
    try:
        # Build format kwargs, avoiding duplicates
        format_kwargs = {
            "column": context.get("column", "unknown"),
            "node_id": target_node,
            "spark_snippet": spark_snippet,
            "source_file": source_file,
            "source_line": source_line,
            "operation": context.get("operation", "unknown"),
        }
        # Add remaining context items (excluding already defined keys)
        for k, v in context.items():
            if k not in format_kwargs:
                format_kwargs[k] = v
        ai_instruction = template.format(**format_kwargs)
    except (KeyError, IndexError):
        ai_instruction = template
    
    # Generate anomaly ID
    anomaly_id = f"ANOM_{migration_id}_{sequence:03d}"
    
    return Anomaly(
        anomaly_id=anomaly_id,
        code=code,
        severity=severity,
        target_node=target_node,
        target_type=target_type,
        diagnostic_hints=DiagnosticHint(
            source_location=SourceLocation(
                file=source_file,
                line=source_line,
                function=context.get("function"),
            ),
            spark_snippet=spark_snippet,
            issue_description=metadata.get("description", "Unknown issue"),
            ai_instruction=ai_instruction,
            suggested_fix=context.get("suggested_fix"),
            context=context,
        ),
    )




def group_anomalies(anomalies: list[Anomaly]) -> list[AnomalyGroup]:
    """
    Group similar anomalies to reduce redundancy while preserving detail.
    
    Groups by: code + subject (column name, UDF name, param name, etc.)
    Each group lists all occurrences (nodes where it happens).
    """
    from collections import defaultdict
    
    # Build groups: key = (code, subject)
    groups: dict[tuple[str, str], list[Anomaly]] = defaultdict(list)
    
    for anomaly in anomalies:
        # Extract subject based on anomaly code
        subject = _extract_subject(anomaly)
        key = (anomaly.code.value, subject)
        groups[key].append(anomaly)
    
    # Convert to AnomalyGroup objects
    result = []
    for (code_str, subject), members in groups.items():
        if not members:
            continue
        
        # Use first member as template
        template = members[0]
        
        # Build occurrences
        occurrences = [
            AnomalyOccurrence(
                node_id=m.target_node,
                node_type=m.target_type,
                spark_snippet=m.diagnostic_hints.spark_snippet,
                source_file=m.diagnostic_hints.source_location.file,
                source_line=m.diagnostic_hints.source_location.line,
            )
            for m in members
        ]
        
        # Determine subject type
        subject_type = _determine_subject_type(template.code)
        
        # A group is resolved if ALL its members are resolved
        all_resolved = all(m.resolved for m in members)

        group = AnomalyGroup(
            group_id=f"GRP_{code_str}_{subject.replace(' ', '_')[:20]}",
            code=template.code,
            severity=template.severity,
            subject=subject,
            subject_type=subject_type,
            issue_description=template.diagnostic_hints.issue_description,
            ai_instruction=template.diagnostic_hints.ai_instruction,
            suggested_fix=template.diagnostic_hints.suggested_fix,
            occurrences=occurrences,
            resolved=all_resolved,
            resolution=template.resolution if all_resolved else None,
            resolved_by=template.resolved_by if all_resolved else None,
        )
        result.append(group)
    
    # Sort by severity (CRITICAL first), then by count (most occurrences first)
    severity_order = {"CRITICAL": 0, "MEDIUM": 1, "LOW": 2}
    result.sort(key=lambda g: (severity_order.get(g.severity.value, 99), -g.count))
    
    return result


def _extract_subject(anomaly: Anomaly) -> str:
    """Extract the grouping subject from an anomaly."""
    hints = anomaly.diagnostic_hints
    context = hints.context
    
    # Try common subject fields
    if "column" in context and context["column"] != "unknown":
        return context["column"]
    if "udf_name" in context:
        return context["udf_name"]
    if "param_name" in context:
        return context["param_name"]
    if "resource_id" in context:
        return context["resource_id"]
    if "join_key" in context:
        return context["join_key"]
    if "parse_context" in context:
        return context["parse_context"]
    
    # Fallback: extract from spark_snippet if possible
    snippet = hints.spark_snippet
    if ".isin" in snippet or "col(" in snippet:
        # Try to extract column name from F.col('name')
        import re
        match = re.search(r"col\(['\"]([^'\"]+)['\"]\)", snippet)
        if match:
            return match.group(1)
    
    # Last resort: use node ID
    return anomaly.target_node


def _determine_subject_type(code: AnomalyCode) -> str:
    """Determine subject type based on anomaly code."""
    code_to_type = {
        AnomalyCode.SCH_001: "column",
        AnomalyCode.SCH_002: "column",
        AnomalyCode.SCH_003: "column",
        AnomalyCode.LOG_001: "udf",
        AnomalyCode.LOG_002: "attribute",
        AnomalyCode.REF_002: "parameter",
        AnomalyCode.LIN_002: "resource",
        AnomalyCode.JOIN_001: "join_key",
        AnomalyCode.PAR_001: "parse_context",
    }
    return code_to_type.get(code, "node")


def create_report(
    migration_id: str,
    source_asg: str,
    source_asg_agnostic: str | None = None,
    include_timestamp: bool = True,
) -> AnomalyReport:
    """Create an empty anomaly report."""
    from datetime import datetime
    return AnomalyReport(
        migration_id=migration_id,
        generated_at=datetime.utcnow() if include_timestamp else None,
        source_asg=source_asg,
        source_asg_agnostic=source_asg_agnostic,
        anomalies=[],
        summary=AnomalySummary(total=0),
    )
