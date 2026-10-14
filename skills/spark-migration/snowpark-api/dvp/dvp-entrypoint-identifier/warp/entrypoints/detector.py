"""
Entrypoint Detector - Identify execution entry points from ASG.

This module analyzes ASG JSON files to identify:
- Databricks notebooks
- Python scripts with main guard
- Files that create SparkSession
- Input/output summary per entry point

All detection is based exclusively on ASG data — no source code parsing,
no hardcoded function names, no project-specific assumptions.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from warp_core.diagnostics import (
    DiagnosticIssue,
    DiagnosticReport,
    Severity,
    IssueCategory,
    EntrypointIssueCode,
)


@dataclass
class IOSummary:
    """Summary of inputs or outputs for an entry point."""

    total: int = 0
    by_type: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "by_type": dict(sorted(self.by_type.items())),
        }


@dataclass
class Entrypoint:
    """An identified entry point in the workload.

    The ``source`` field encodes a composite identifier following the WARP
    hybrid standard (see docs/32_ENTRYPOINTS_FORMAT.md):

        <relative_path>:<lineno>[::Scope::method]

    - Python __main__ scripts: ``pipeline.py:40``  (no scope)
    - Scala objects:           ``App.scala:5::MyApp::main``
    - Notebooks:               ``notebook.py:1``   (line 1 by convention)
    """

    name: str
    source: str
    type: str  # databricks_notebook, script, module
    origin: str = "ASG"
    status: str = "detected"
    reason: str | None = None  # notebook | main_guard | spark_session_creation | main_method
    inputs: IOSummary = field(default_factory=IOSummary)
    outputs: IOSummary = field(default_factory=IOSummary)

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.name,
            "origin": self.origin,
            "status": self.status,
            "source": self.source,
            "type": self.type,
        }
        if self.reason is not None:
            d["reason"] = self.reason
        d["inputs"] = self.inputs.to_dict()
        d["outputs"] = self.outputs.to_dict()
        return d


class EntrypointDetector:
    """
    Detects entry points from ASG JSON data.

    Uses only ASG-provided information (data_in, data_out, execution_calls,
    notebook_dependencies) — no hardcoded function names or project-specific
    assumptions.  Indirect I/O is discovered by tracing the call graph from
    entry points to their transitive dependencies.
    """

    JDBC_PATTERNS = {
        "sqlserver": "SQLSERVER",
        "mysql": "MySQL",
        "postgresql": "PostgreSQL",
        "oracle": "Oracle",
    }

    def __init__(self) -> None:
        self.entrypoints: list[Entrypoint] = []
        self._issues: DiagnosticReport = DiagnosticReport(tool_name="entrypoints")

    @property
    def issues(self) -> DiagnosticReport:
        """Get diagnostic issues from last detection run."""
        return self._issues

    def _add_issue(
        self,
        code: str,
        severity: Severity,
        category: IssueCategory,
        message: str,
        context: dict,
        suggestion: str = "",
    ) -> None:
        self._issues.add(
            DiagnosticIssue(
                code=code,
                severity=severity,
                category=category,
                message=message,
                context=context,
                suggestion=suggestion,
            )
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, asg: dict) -> list[Entrypoint]:
        """
        Detect entry points from an ASG dictionary.

        Strategy:
        1. Group data_in / data_out by file (direct I/O).
        2. Build a call graph from notebook_dependencies + cross-file
           execution_calls.
        3. For each entry point, find all transitively reachable files.
        4. Roll up I/O from those dependency files to the entry point.
        """
        self.entrypoints = []

        source_files = asg.get("source_files", [])
        data_in = asg.get("data_in", [])
        data_out = asg.get("data_out", [])
        execution_calls = asg.get("execution_calls", [])

        inputs_by_file = self._group_io_by_file(data_in, skip_test_files=True)
        outputs_by_file = self._group_io_by_file(data_out, skip_test_files=False)

        call_graph = self._build_call_graph(source_files, execution_calls)

        shared_notebooks = self._collect_shared_notebooks(source_files)

        for sf in source_files:
            if not sf.get("is_entry_point", False):
                continue

            name_stem = Path(sf.get("path", "")).stem
            if name_stem in shared_notebooks:
                continue

            path = sf.get("path", "")
            source_type = sf.get("source_type", "unknown")
            ep_reason_for_type: str | None = sf.get("entry_point_reason")

            if source_type == "notebook":
                ep_type = "databricks_notebook"
            elif ep_reason_for_type == "main_method":
                # Scala object with def main — semantic type is "module", not "script"
                ep_type = "module"
            elif source_type == "script":
                ep_type = "script"
            else:
                ep_type = "module"

            name = Path(path).stem

            direct_in = inputs_by_file.get(path, IOSummary())
            direct_out = outputs_by_file.get(path, IOSummary())

            inputs = IOSummary(total=direct_in.total, by_type=dict(direct_in.by_type))
            outputs = IOSummary(total=direct_out.total, by_type=dict(direct_out.by_type))

            dep_files = self._get_transitive_deps(path, call_graph)
            dep_files.discard(path)

            for dep_file in dep_files:
                dep_in = inputs_by_file.get(dep_file)
                if dep_in:
                    inputs.total += dep_in.total
                    for t, c in dep_in.by_type.items():
                        inputs.by_type[t] = inputs.by_type.get(t, 0) + c

                dep_out = outputs_by_file.get(dep_file)
                if dep_out:
                    outputs.total += dep_out.total
                    for t, c in dep_out.by_type.items():
                        outputs.by_type[t] = outputs.by_type.get(t, 0) + c

            ep_reason: str | None = sf.get("entry_point_reason")
            ep_lineno: int = sf.get("entry_point_lineno") or 1
            ep_scope: str | None = sf.get("entry_point_scope")

            # Build composite source identifier: path:lineno[::Scope::method]
            # Notebooks always use line 1 by convention (whole-file execution).
            ep_source = f"{path}:{ep_lineno}"
            if ep_scope:
                ep_source = f"{ep_source}::{ep_scope}"

            entrypoint = Entrypoint(
                name=name,
                source=ep_source,
                type=ep_type,
                origin="ASG",
                status="detected",
                reason=ep_reason,
                inputs=inputs,
                outputs=outputs,
            )

            self.entrypoints.append(entrypoint)

        self._track_entrypoint_issues(source_files, inputs_by_file, outputs_by_file)

        return self.entrypoints

    def detect_from_file(self, asg_path: str | Path) -> list[Entrypoint]:
        """Detect entry points from an ASG JSON file."""
        import json

        asg_path = Path(asg_path)

        if asg_path.is_dir():
            asg_path = asg_path / "asg_pyspark.json"

        if not asg_path.exists():
            raise FileNotFoundError(f"ASG file not found: {asg_path}")

        with open(asg_path) as f:
            asg = json.load(f)

        return self.detect(asg)

    def to_list(self) -> list[dict]:
        """Convert all detected entry points to a list of dicts."""
        return [ep.to_dict() for ep in self.entrypoints]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_shared_notebooks(source_files: list[dict]) -> set[str]:
        """Collect stems of notebooks referenced via %run — not independent entrypoints."""
        shared: set[str] = set()
        for sf in source_files:
            for dep in sf.get("notebook_dependencies", []):
                target = dep.get("target", "").strip("/").split("/")[-1]
                if target:
                    shared.add(target)
        return shared

    def _build_call_graph(
        self,
        source_files: list[dict],
        execution_calls: list[dict],
    ) -> dict[str, set[str]]:
        """Build caller_file -> {callee_files} from notebook_dependencies and execution_calls."""
        graph: dict[str, set[str]] = defaultdict(set)

        stems: dict[str, str] = {}
        for sf in source_files:
            path = sf.get("path", "")
            if path:
                stems[Path(path).stem] = path

        for sf in source_files:
            caller = sf.get("path", "")
            if not caller:
                continue
            for dep in sf.get("notebook_dependencies", []):
                target_stem = dep.get("target", "").strip("/").split("/")[-1]
                if target_stem and target_stem in stems:
                    graph[caller].add(stems[target_stem])

        for ec in execution_calls:
            caller_file = ec.get("caller", {}).get("file", "")
            callee_file = ec.get("callee", {}).get("file", "")
            if caller_file and callee_file and caller_file != callee_file:
                graph[caller_file].add(callee_file)

        return graph

    @staticmethod
    def _get_transitive_deps(
        file_path: str,
        call_graph: dict[str, set[str]],
    ) -> set[str]:
        """Get all files transitively reachable from file_path (inclusive)."""
        visited: set[str] = set()
        stack = [file_path]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            for dep in call_graph.get(current, ()):
                if dep not in visited:
                    stack.append(dep)
        return visited

    def _normalize_type(self, entry: dict) -> str:
        """Normalize ASG type, with optional JDBC subtype detection from path."""
        entry_type = entry.get("type", "unknown").upper()

        if entry_type == "JDBC":
            path = str(entry.get("path", "") or "")
            for pattern, db_type in self.JDBC_PATTERNS.items():
                if pattern in path.lower():
                    return db_type
            return "JDBC"

        return entry_type

    def _group_io_by_file(
        self,
        entries: list[dict],
        skip_test_files: bool = False,
    ) -> dict[str, IOSummary]:
        """Group data_in or data_out entries by their source file."""
        result: dict[str, IOSummary] = {}

        for entry in entries:
            if skip_test_files and entry.get("is_test_file", False):
                continue

            location = entry.get("location", {})
            file_path = location.get("pathfile", "") if location else ""
            if not file_path:
                continue

            entry_type = self._normalize_type(entry)

            if file_path not in result:
                result[file_path] = IOSummary()

            result[file_path].total += 1
            result[file_path].by_type[entry_type] = (
                result[file_path].by_type.get(entry_type, 0) + 1
            )

        return result

    def _track_entrypoint_issues(
        self,
        source_files: list[dict],
        inputs_by_file: dict[str, IOSummary],
        outputs_by_file: dict[str, IOSummary],
    ) -> None:
        self._issues = DiagnosticReport(tool_name="entrypoints")

        for ep in self.entrypoints:
            if ep.inputs.total == 0:
                self._add_issue(
                    code=EntrypointIssueCode.INPUT_UNTRACED,
                    severity=Severity.INFO,
                    category=IssueCategory.IO_TRACKING,
                    message="Entry point has no detected inputs",
                    context={
                        "entry_name": ep.name,
                        "entry_type": ep.type,
                        "source": ep.source,
                    },
                    suggestion="May use dynamic sources or inputs from other files",
                )

            if ep.outputs.total == 0:
                self._add_issue(
                    code=EntrypointIssueCode.OUTPUT_UNLINKED,
                    severity=Severity.WARNING,
                    category=IssueCategory.IO_TRACKING,
                    message="Entry point has no detected outputs",
                    context={
                        "entry_name": ep.name,
                        "entry_type": ep.type,
                        "source": ep.source,
                    },
                    suggestion="Check for utility function writes or indirect outputs",
                )

        entry_paths = {ep.source.split(":")[0] for ep in self.entrypoints}

        for path, inputs in inputs_by_file.items():
            if path not in entry_paths and inputs.total > 0:
                self._add_issue(
                    code=EntrypointIssueCode.INDIRECT_ENTRY,
                    severity=Severity.INFO,
                    category=IssueCategory.INDIRECT_CALL,
                    message="File has inputs but is not an entry point",
                    context={
                        "file": path,
                        "inputs": inputs.total,
                    },
                    suggestion="Likely a utility module called by entry points",
                )
