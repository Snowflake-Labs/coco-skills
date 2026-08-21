"""
Diagnostic Reporter - Generate comprehensive analysis reports from WARP outputs.

Analyzes all available WARP outputs to produce a unified diagnostic report
including scores, weaknesses, and improvement opportunities.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Score:
    """A diagnostic score with value and details."""
    name: str
    value: int  # 0-100
    details: str = ""


@dataclass
class DiagnosticReport:
    """Complete diagnostic report for a project."""
    project_name: str
    generated_at: str
    
    # Overview metrics
    source_files: int = 0
    data_inputs: int = 0
    data_outputs: int = 0
    transformations: int = 0
    entry_points: int = 0
    
    # Quality metrics
    parse_success_rate: int = 0
    schema_coverage: int = 0
    lineage_completeness: int = 0
    static_coverage: int = 0
    
    # Detailed counts
    total_columns: int = 0
    known_type_columns: int = 0
    constraints: int = 0
    relationships: int = 0
    
    # Anomalies
    anomaly_counts: dict = field(default_factory=dict)
    anomaly_by_severity: dict = field(default_factory=dict)
    
    # I/O breakdown
    input_by_type: dict = field(default_factory=dict)
    input_by_format: dict = field(default_factory=dict)
    output_by_type: dict = field(default_factory=dict)
    
    # Entry point breakdown
    entrypoints_by_type: dict = field(default_factory=dict)
    
    # Synthetic data
    synthetic_files: int = 0
    
    # Issues
    weaknesses: list = field(default_factory=list)
    opportunities: list = field(default_factory=list)
    
    @property
    def overall_score(self) -> int:
        """Calculate overall health score."""
        scores = [
            self.parse_success_rate,
            self.schema_coverage,
            self.lineage_completeness,
            self.static_coverage,
        ]
        return sum(scores) // len(scores) if scores else 0
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "project_name": self.project_name,
            "generated_at": self.generated_at,
            "overview": {
                "source_files": self.source_files,
                "data_inputs": self.data_inputs,
                "data_outputs": self.data_outputs,
                "transformations": self.transformations,
                "entry_points": self.entry_points,
            },
            "scores": {
                "parse_success_rate": self.parse_success_rate,
                "schema_coverage": self.schema_coverage,
                "lineage_completeness": self.lineage_completeness,
                "static_coverage": self.static_coverage,
                "overall": self.overall_score,
            },
            "details": {
                "total_columns": self.total_columns,
                "known_type_columns": self.known_type_columns,
                "constraints": self.constraints,
                "relationships": self.relationships,
            },
            "anomalies": {
                "by_code": self.anomaly_counts,
                "by_severity": self.anomaly_by_severity,
            },
            "data_io": {
                "input_by_type": self.input_by_type,
                "input_by_format": self.input_by_format,
                "output_by_type": self.output_by_type,
            },
            "entry_points": {
                "by_type": self.entrypoints_by_type,
            },
            "synthetic_data": {
                "files_generated": self.synthetic_files,
            },
            "assessment": {
                "weaknesses": self.weaknesses,
                "opportunities": self.opportunities,
            },
        }


class DiagnosticReporter:
    """
    Generates diagnostic reports by analyzing WARP outputs.
    
    Expects a directory containing:
    - *asg_pyspark.json or asg_pyspark.json
    - *data_io.json or data_io.json
    - *entrypoints.json or entrypoints.json
    - *anomaly*.json
    - parsing_report.json (optional)
    - synthetic_data/*.csv (optional - synthetic data)
    """
    
    def __init__(self, output_dir: Path | str):
        self.output_dir = Path(output_dir)
        self.asg: dict | None = None
        self.data_io: list | None = None
        self.entrypoints: list | None = None
        self.anomalies: list | None = None
        self.parsing: dict | None = None
        
        self._load_files()
    
    def _load_json(self, *patterns: str) -> Any:
        """Try to load JSON from multiple file patterns."""
        for pattern in patterns:
            files = list(self.output_dir.glob(pattern))
            if files:
                try:
                    with open(files[0]) as f:
                        return json.load(f)
                except:
                    pass
        return None
    
    def _load_files(self) -> None:
        """Load all available WARP output files."""
        self.asg = self._load_json("*asg_pyspark.json", "asg_pyspark.json")
        self.data_io = self._load_json("*data_io.json", "data_io.json")
        self.entrypoints = self._load_json("*entrypoints*.json", "entrypoints.json")
        # Prefer rbi_* prefixed files
        self.anomalies = self._load_json("rbi_anomaly*.json", "*anomaly*.json", "anomaly_report.json")
        self.parsing = self._load_json("parsing_report.json")
    
    def generate(self, project_name: str = "Project") -> DiagnosticReport:
        """Generate comprehensive diagnostic report."""
        report = DiagnosticReport(
            project_name=project_name,
            generated_at=datetime.now().isoformat(),
        )
        
        self._analyze_asg(report)
        self._analyze_data_io(report)
        self._analyze_entrypoints(report)
        self._analyze_anomalies(report)
        self._analyze_parsing(report)
        self._analyze_synthetic(report)
        self._identify_issues(report)
        
        return report
    
    def _analyze_asg(self, report: DiagnosticReport) -> None:
        """Analyze ASG data."""
        if not self.asg:
            return
        
        report.source_files = len(self.asg.get("source_files", []))
        report.data_inputs = len(self.asg.get("data_in", []))
        report.data_outputs = len(self.asg.get("data_out", []))
        report.transformations = len(self.asg.get("transformations", []))
        report.constraints = len(self.asg.get("column_constraints", []))
        report.relationships = len(self.asg.get("column_relationships", []))
    
    def _analyze_data_io(self, report: DiagnosticReport) -> None:
        """Analyze data I/O information."""
        if not self.data_io:
            return
        
        data = self.data_io if isinstance(self.data_io, list) else []
        
        inputs = [d for d in data if d.get("role") == "input"]
        outputs = [d for d in data if d.get("role") == "output"]
        
        # Counts
        report.input_by_type = dict(Counter(d.get("type", "unknown") for d in inputs))
        report.input_by_format = dict(Counter(d.get("format", "unknown") for d in inputs))
        report.output_by_type = dict(Counter(d.get("type", "unknown") for d in outputs))
        
        # Schema coverage
        report.total_columns = sum(len(d.get("columns", [])) for d in data)
        report.known_type_columns = sum(
            1 for d in data for c in d.get("columns", [])
            if c.get("type") and c.get("type") != "UNKNOWN"
        )
        
        if report.total_columns > 0:
            report.schema_coverage = report.known_type_columns * 100 // report.total_columns
        
        # Static vs dynamic coverage
        dynamic_count = sum(1 for d in inputs if d.get("detection") == "dynamic")
        static_count = len(inputs) - dynamic_count
        if inputs:
            report.static_coverage = static_count * 100 // len(inputs)
    
    def _analyze_entrypoints(self, report: DiagnosticReport) -> None:
        """Analyze entry points."""
        if not self.entrypoints:
            return
        
        eps = (
            self.entrypoints 
            if isinstance(self.entrypoints, list) 
            else self.entrypoints.get("entrypoints", [])
        )
        
        report.entry_points = len(eps)
        report.entrypoints_by_type = dict(Counter(ep.get("type", "unknown") for ep in eps))
    
    def _analyze_anomalies(self, report: DiagnosticReport) -> None:
        """Analyze anomalies."""
        if not self.anomalies:
            report.lineage_completeness = 100
            return
        
        anom = (
            self.anomalies 
            if isinstance(self.anomalies, list) 
            else self.anomalies.get("anomalies", [])
        )
        
        report.anomaly_counts = dict(Counter(a.get("code", "UNKNOWN") for a in anom))
        report.anomaly_by_severity = dict(Counter(a.get("severity", "UNKNOWN") for a in anom))
        
        # Calculate lineage completeness
        critical = report.anomaly_by_severity.get("CRITICAL", 0)
        medium = report.anomaly_by_severity.get("MEDIUM", 0)
        
        # Deduct points for anomalies
        score = 100 - (critical * 1) - (medium * 0.05)
        report.lineage_completeness = max(0, min(100, int(score)))
    
    def _analyze_parsing(self, report: DiagnosticReport) -> None:
        """Analyze parsing quality."""
        if not self.parsing:
            # If no parsing report, estimate from ASG
            if self.asg and self.asg.get("source_files"):
                report.parse_success_rate = 100
            return
        
        # Support both old and new field names
        syntax = self.parsing.get("syntax_summary", self.parsing.get("syntax", {}))
        total = self.parsing.get("total_files", syntax.get("total", 0))
        ok = syntax.get("ok", 0)
        corrected = syntax.get("corrected", 0)
        
        if total > 0:
            report.parse_success_rate = (ok + corrected) * 100 // total
    
    def _analyze_synthetic(self, report: DiagnosticReport) -> None:
        """Analyze synthetic data generation."""
        synth_dir = self.output_dir / "synthetic_data"
        if synth_dir.exists():
            report.synthetic_files = len(list(synth_dir.glob("*.csv")))
    
    def _identify_issues(self, report: DiagnosticReport) -> None:
        """Identify weaknesses and improvement opportunities."""
        # Weaknesses
        if report.total_columns - report.known_type_columns > 100:
            unknown = report.total_columns - report.known_type_columns
            report.weaknesses.append(
                f"{unknown} columns with UNKNOWN type - schema inference incomplete"
            )
        
        if report.static_coverage < 50:
            dynamic = 100 - report.static_coverage
            report.weaknesses.append(
                f"{dynamic}% of sources are dynamic (runtime-dependent paths/queries)"
            )
        
        critical = report.anomaly_by_severity.get("CRITICAL", 0)
        if critical > 0:
            report.weaknesses.append(
                f"{critical} CRITICAL anomalies requiring attention"
            )
        
        lin_001 = report.anomaly_counts.get("LIN_001", 0)
        lin_002 = report.anomaly_counts.get("LIN_002", 0)
        if lin_001 + lin_002 > 20:
            report.weaknesses.append(
                f"{lin_001 + lin_002} lineage issues (disconnected data flow)"
            )
        
        ref_002 = report.anomaly_counts.get("REF_002", 0)
        if ref_002 > 10:
            report.weaknesses.append(
                f"{ref_002} unresolved references (cross-function lineage gaps)"
            )
        
        # Opportunities
        if report.constraints < 50 and report.data_inputs > 10:
            report.opportunities.append(
                "Add more filter conditions to improve synthetic data quality"
            )
        
        if report.relationships < 20 and report.data_inputs > 5:
            report.opportunities.append(
                "Capture more join relationships for better data consistency"
            )
        
        if report.schema_coverage < 80:
            report.opportunities.append(
                "Add explicit schema definitions or type hints to improve inference"
            )
        
        if report.static_coverage < 70:
            report.opportunities.append(
                "Refactor helper functions to use literal paths/table names"
            )
        
        if report.entry_points > 20:
            report.opportunities.append(
                "Consider consolidating entry points for easier maintenance"
            )
    
    def print_report(self, report: DiagnosticReport) -> None:
        """Print formatted report to console."""
        print("=" * 70)
        print(f"WARP DIAGNOSTIC REPORT - {report.project_name}")
        print(f"Generated: {report.generated_at}")
        print("=" * 70)
        
        print("\n" + "─" * 70)
        print("1. PROJECT OVERVIEW")
        print("─" * 70)
        print(f"  Source Files:     {report.source_files}")
        print(f"  Data Inputs:      {report.data_inputs}")
        print(f"  Data Outputs:     {report.data_outputs}")
        print(f"  Transformations:  {report.transformations}")
        print(f"  Entry Points:     {report.entry_points}")
        print(f"  Constraints:      {report.constraints}")
        print(f"  Relationships:    {report.relationships}")
        
        print("\n" + "─" * 70)
        print("2. DATA I/O BREAKDOWN")
        print("─" * 70)
        print(f"\n  Input Types:  {report.input_by_type}")
        print(f"  Input Formats: {report.input_by_format}")
        print(f"  Output Types: {report.output_by_type}")
        
        print("\n" + "─" * 70)
        print("3. ANOMALY SUMMARY")
        print("─" * 70)
        print(f"\n  By Severity: {report.anomaly_by_severity}")
        print(f"  Top Issues:  {dict(Counter(report.anomaly_counts).most_common(5))}")
        
        print("\n" + "─" * 70)
        print("4. ASSESSMENT")
        print("─" * 70)
        
        if report.weaknesses:
            print("\n  WEAKNESSES:")
            for w in report.weaknesses:
                print(f"    ⚠ {w}")
        
        if report.opportunities:
            print("\n  OPPORTUNITIES:")
            for o in report.opportunities:
                print(f"    → {o}")
        
        print("\n" + "─" * 70)
        print("5. HEALTH SCORES")
        print("─" * 70)
        print(f"""
  ┌─────────────────────────────────────┐
  │  Parse Quality:        {report.parse_success_rate:>3}%        │
  │  Schema Inference:     {report.schema_coverage:>3}%        │
  │  Lineage Completeness: {report.lineage_completeness:>3}%        │
  │  Static Coverage:      {report.static_coverage:>3}%        │
  ├─────────────────────────────────────┤
  │  OVERALL SCORE:        {report.overall_score:>3}%        │
  └─────────────────────────────────────┘
""")
        
        print("=" * 70)
        print("END OF REPORT")
        print("=" * 70)
