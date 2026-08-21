"""
Audit Reporter - Generates migration audit reports.

This module analyzes the ASG after slicing to identify:
- Completely unused functions (orphans)
- Partially used functions (dead branches)
- Active helper functions (utilities called from active code)
- Global scope vs modular code distribution

The report helps architects decide what to migrate and what to skip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from warp_core.ir.pyspark_models import ASG
    from warp_core.slicer.slicer import SliceResult


@dataclass
class FunctionStatus:
    """Status of a single function in the pipeline."""

    name: str
    line_start: int
    line_end: int | None
    total_nodes: int
    active_nodes: int
    inactive_node_ids: list[str] = field(default_factory=list)
    category: str = "unknown"  # fully_active, partially_used, completely_unused, active_helper, orchestrator, no_transformations
    auxiliary_type: str | None = None  # For active_helper: "udf", "loader", "writer", None
    called_functions: list[str] = field(
        default_factory=list
    )  # Functions called by this one (for orchestrators)

    @property
    def usage_percent(self) -> float:
        """Percentage of nodes that are active."""
        if self.total_nodes == 0:
            return 0.0
        return (self.active_nodes / self.total_nodes) * 100

    @property
    def line_range_display(self) -> str:
        """Format line range for Markdown display."""
        return f"`L{self.line_start}-L{self.line_end}`"

    @property
    def loc(self) -> int:
        """Lines of code in this function."""
        if self.line_end is None:
            return 1
        return self.line_end - self.line_start + 1


@dataclass
class AuditReport:
    """Complete audit report for a pipeline."""

    # Metadata
    pipeline_name: str = ""
    source_file: str = ""
    generated_at: str = ""

    # Function categories
    completely_unused: list[FunctionStatus] = field(default_factory=list)
    partially_used: list[FunctionStatus] = field(default_factory=list)
    fully_active: list[FunctionStatus] = field(default_factory=list)
    active_helpers: list[FunctionStatus] = field(default_factory=list)
    orchestrators: list[FunctionStatus] = field(default_factory=list)
    no_transformations: list[FunctionStatus] = field(default_factory=list)

    # Helper subcategories (derived from active_helpers by auxiliary_type)
    @property
    def udfs(self) -> list[FunctionStatus]:
        """Helper functions used as UDFs."""
        return [h for h in self.active_helpers if h.auxiliary_type == "udf"]

    @property
    def loaders(self) -> list[FunctionStatus]:
        """Helper functions that load data (contain sources)."""
        return [h for h in self.active_helpers if h.auxiliary_type == "loader"]

    @property
    def writers(self) -> list[FunctionStatus]:
        """Helper functions that write data (contain sinks)."""
        return [h for h in self.active_helpers if h.auxiliary_type == "writer"]

    @property
    def other_helpers(self) -> list[FunctionStatus]:
        """Helper functions without specific auxiliary type."""
        return [h for h in self.active_helpers if h.auxiliary_type is None]

    # Global scope stats
    global_scope_nodes: int = 0
    global_scope_active: int = 0

    # Summary stats
    total_nodes: int = 0
    active_nodes: int = 0
    total_functions: int = 0
    active_functions: int = 0

    @property
    def efficiency_percent(self) -> float:
        """Percentage of code that contributes to outputs."""
        if self.total_nodes == 0:
            return 0.0
        return (self.active_nodes / self.total_nodes) * 100

    @property
    def dead_code_percent(self) -> float:
        """Percentage of code that is dead/unused."""
        return 100.0 - self.efficiency_percent

    @property
    def is_modular(self) -> bool:
        """Whether the code is organized in functions vs procedural."""
        if self.total_nodes == 0:
            return False
        # If more than 70% of nodes are inside functions, it's modular
        nodes_in_functions = self.total_nodes - self.global_scope_nodes
        return (nodes_in_functions / self.total_nodes) > 0.7


class AuditReporter:
    """
    Generates migration audit reports from ASG analysis.

    The reporter identifies code that doesn't contribute to outputs
    and categorizes functions by their usage patterns.
    """

    def __init__(self) -> None:
        self._function_ranges: list[tuple[str, int, int | None]] = []

    def generate(self, asg: ASG, slice_result: SliceResult) -> AuditReport:
        """
        Generate a complete audit report.

        Args:
            asg: The Abstract Semantic Graph
            slice_result: Result from the GraphSlicer

        Returns:
            AuditReport with categorized function status
        """
        report = AuditReport(
            pipeline_name=asg.extraction_metadata.app_name or "Unknown Pipeline",
            source_file=asg.extraction_metadata.source_file or "unknown.py",
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        # Build function ranges for lookup (sorted by start line, innermost first)
        # Use location.start_line and location.end_line properties
        self._function_ranges = sorted(
            [
                (
                    f.name,
                    f.location.start_line if f.location else 1,
                    f.location.end_line if f.location else 1,
                )
                for f in asg.functions
            ],
            key=lambda x: (x[1], -((x[2] or x[1]) - x[1])),  # Sort by start, then by smallest range
        )

        # Get active transformation IDs
        active_ids = slice_result.active_transformations

        # Extend active_ids with Window specs that are referenced in active nodes
        # These are orderBy nodes with "Window." in logic that are used via .over()
        active_ids = self._include_referenced_window_specs(asg, active_ids)

        # Build lookup of which function each transformation belongs to
        tx_to_func: dict[str, str | None] = {}
        for tx in asg.transformations:
            tx_line = tx.location.start_line if tx.location else None
            tx_to_func[tx.id] = self._find_parent_function(tx_line)

        # Categorize each function
        function_statuses: dict[str, FunctionStatus] = {}

        for func in asg.functions:
            # Find all transformations in this function (not in nested functions)
            func_nodes = [tx for tx in asg.transformations if tx_to_func.get(tx.id) == func.name]

            active_in_func = [n for n in func_nodes if n.id in active_ids]
            inactive_in_func = [n for n in func_nodes if n.id not in active_ids]

            status = FunctionStatus(
                name=func.name,
                line_start=func.location.start_line if func.location else 1,
                line_end=func.location.end_line if func.location else 1,
                total_nodes=len(func_nodes),
                active_nodes=len(active_in_func),
                inactive_node_ids=[n.id for n in inactive_in_func],
            )

            # Categorize
            if len(func_nodes) == 0:
                status.category = "no_transformations"
            elif len(active_in_func) == 0:
                status.category = "completely_unused"
            elif len(active_in_func) == len(func_nodes):
                status.category = "fully_active"
            else:
                status.category = "partially_used"

            function_statuses[func.name] = status

        # Check for active helpers (functions without transformations but called from active code)
        helper_info = self._find_active_helpers(asg, active_ids, function_statuses)
        for name, aux_type in helper_info.items():
            if name in function_statuses:
                function_statuses[name].category = "active_helper"
                function_statuses[name].auxiliary_type = aux_type

        # Check for orchestrators (functions that call active functions but have no active nodes themselves)
        orchestrator_info = self._find_orchestrators(asg, function_statuses)
        for name, called_funcs in orchestrator_info.items():
            if name in function_statuses:
                function_statuses[name].category = "orchestrator"
                function_statuses[name].called_functions = called_funcs

        # Distribute to report categories
        for status in function_statuses.values():
            match status.category:
                case "completely_unused":
                    report.completely_unused.append(status)
                case "partially_used":
                    report.partially_used.append(status)
                case "fully_active":
                    report.fully_active.append(status)
                case "active_helper":
                    report.active_helpers.append(status)
                case "orchestrator":
                    report.orchestrators.append(status)
                case "no_transformations":
                    report.no_transformations.append(status)

        # Sort by line number
        report.completely_unused.sort(key=lambda x: x.line_start)
        report.partially_used.sort(key=lambda x: x.line_start)
        report.fully_active.sort(key=lambda x: x.line_start)
        report.active_helpers.sort(key=lambda x: x.line_start)
        report.orchestrators.sort(key=lambda x: x.line_start)

        # Calculate global scope stats
        global_nodes = [tx for tx in asg.transformations if tx_to_func.get(tx.id) is None]
        report.global_scope_nodes = len(global_nodes)
        report.global_scope_active = len([n for n in global_nodes if n.id in active_ids])

        # Summary stats
        report.total_nodes = len(asg.data_in) + len(asg.transformations) + len(asg.data_out)
        report.active_nodes = slice_result.total_active
        report.total_functions = len(asg.functions)
        report.active_functions = (
            len(report.fully_active)
            + len(report.partially_used)
            + len(report.active_helpers)
            + len(report.orchestrators)
        )

        return report

    def _find_parent_function(self, line: int | None) -> str | None:
        """
        Find the innermost function containing a given line.

        Handles nested functions by returning the most specific (innermost) match.
        """
        if line is None:
            return None

        best_match: tuple[str, int, int | None] | None = None

        for name, start, end in self._function_ranges:
            # Skip if end is None (can't determine range)
            if end is None:
                continue
            if start <= line <= end:
                # Check if this is a more specific (nested) match
                if best_match is None:
                    best_match = (name, start, end)
                else:
                    # Prefer the function with the smallest range (innermost)
                    _, best_start, best_end = best_match
                    if best_end is not None and (end - start) < (best_end - best_start):
                        best_match = (name, start, end)

        return best_match[0] if best_match else None

    def _include_referenced_window_specs(self, asg: ASG, active_ids: set[str]) -> set[str]:
        """
        Extend active_ids to include Window spec nodes that are referenced by active nodes.

        Window specs are standalone orderBy nodes (e.g., Window.orderBy(...)) that get
        assigned to variables and later used via .over(window_var). The linker doesn't
        track this variable reference, so we detect it heuristically.
        """
        # Collect all logic from active transformations
        active_logic_combined = ""
        for tx in asg.transformations:
            if tx.id in active_ids:
                active_logic_combined += (tx.logic or "") + "\n"

        # If no active nodes use .over(), nothing to do
        if ".over(" not in active_logic_combined:
            return active_ids

        # Find Window spec nodes that might be referenced
        window_spec_ids: set[str] = set()
        for tx in asg.transformations:
            if tx.id in active_ids:
                continue  # Already active
            # Window specs are orderBy operations with "Window." in logic OR
            # orderBy operations with "param_Window" in inputs
            is_window_spec = tx.operation == "orderBy" and (
                (tx.logic and "Window." in tx.logic) or any("Window" in inp for inp in tx.inputs)
            )
            if is_window_spec:
                # Check if this window spec's logic pattern appears in active nodes
                # The variable name used is typically window_X, so we look for patterns
                # like ".over(window_" in active logic
                window_spec_ids.add(tx.id)

        if not window_spec_ids:
            return active_ids

        # For each window spec, check if an active node in the same function uses .over()
        # This is a heuristic - we assume window specs defined near active .over() calls are used
        extended_active = set(active_ids)

        for tx in asg.transformations:
            if tx.id not in window_spec_ids:
                continue

            # Find which function this window spec belongs to
            tx_line = tx.location.start_line if tx.location else None
            window_func = self._find_parent_function(tx_line)
            if not window_func:
                continue

            # Check if any active node in the same function uses .over()
            for active_tx in asg.transformations:
                if active_tx.id not in active_ids:
                    continue
                if not active_tx.logic or ".over(" not in active_tx.logic:
                    continue

                active_line = active_tx.location.start_line if active_tx.location else None
                active_func = self._find_parent_function(active_line)
                if active_func == window_func:
                    # Window spec in same function as an active .over() call
                    extended_active.add(tx.id)
                    break

        # Also check for intermediate DataFrames used in joins/unions
        # Pattern: tx_N creates a DataFrame assigned to a variable, and an active join
        # in the same function references that variable name
        for tx in asg.transformations:
            if tx.id in extended_active:
                continue
            # Look for aggregations or other DataFrame-producing operations
            if tx.operation not in ("groupBy_agg", "agg", "select", "filter"):
                continue

            # Find which function this node belongs to
            tx_line = tx.location.start_line if tx.location else None
            tx_func = self._find_parent_function(tx_line)
            if not tx_func:
                continue

            # Check if any active join/union in the same function might reference this
            for active_tx in asg.transformations:
                if active_tx.id not in extended_active:
                    continue
                if active_tx.operation not in ("join", "union", "unionAll", "unionByName"):
                    continue

                active_line = active_tx.location.start_line if active_tx.location else None
                active_func = self._find_parent_function(active_line)
                if active_func != tx_func:
                    continue

                # Heuristic: if join is on same or later line and references a variable,
                # and there's an aggregation earlier in the function, consider it active
                if active_line and tx_line:
                    if active_line >= tx_line:
                        extended_active.add(tx.id)
                        break

        return extended_active

    def _find_active_helpers(
        self, asg: ASG, active_ids: set[str], function_statuses: dict[str, FunctionStatus]
    ) -> dict[str, str | None]:
        """
        Find helper functions that are called from active transformations.

        These are functions without their own Spark transformations but that
        are invoked inside withColumn(), filter(), etc. of active nodes.

        Returns:
            Dict mapping function name to auxiliary_type:
            - "udf": Function used via UDF wrapper
            - "loader": Function contains data sources
            - "writer": Function contains data sinks
            - None: Other helper (direct call)
        """
        # Track helpers with their types (later types override earlier ones)
        helper_types: dict[str, str | None] = {}

        # Build sets for sink/source detection (use location.start.line)
        sink_lines = {sink.location.start_line for sink in asg.data_out if sink.location}
        source_lines = {src.location.start_line for src in asg.data_in if src.location}

        # Collect all logic from active transformations
        active_logic_combined = ""
        for tx in asg.transformations:
            if tx.id in active_ids:
                active_logic_combined += (tx.logic or "") + "\n"

        # Helper to check if function contains sinks
        def _contains_sink(status: FunctionStatus) -> bool:
            if status.line_end is None:
                return False
            for line in sink_lines:
                if status.line_start <= line <= status.line_end:
                    return True
            return False

        # Helper to check if function contains sources
        def _contains_source(status: FunctionStatus) -> bool:
            if status.line_end is None:
                return False
            for line in source_lines:
                if status.line_start <= line <= status.line_end:
                    return True
            return False

        # Helper to check if function is used as UDF
        def _is_udf(func_name: str) -> bool:
            udf_pattern = rf"\b{re.escape(func_name)}_udf\s*\("
            return bool(re.search(udf_pattern, active_logic_combined))

        # Process all functions
        for func_name, status in function_statuses.items():
            # Skip if not a potential helper
            if status.category not in ("no_transformations", "completely_unused"):
                continue

            # Determine auxiliary type (priority: writer > loader > udf > direct)
            aux_type: str | None = None
            is_helper = False

            # Check for writer (highest priority - writes output)
            if _contains_sink(status):
                aux_type = "writer"
                is_helper = True
            # Check for loader
            elif _contains_source(status):
                aux_type = "loader"
                is_helper = True
            # Check for UDF
            elif _is_udf(func_name):
                aux_type = "udf"
                is_helper = True
            # Check for direct function call (for no_transformations only)
            elif status.category == "no_transformations":
                if re.search(rf"\b{re.escape(func_name)}\s*\(", active_logic_combined):
                    aux_type = None  # Direct helper, no special type
                    is_helper = True

            if is_helper:
                helper_types[func_name] = aux_type

        return helper_types

    def _find_orchestrators(
        self, asg: ASG, function_statuses: dict[str, FunctionStatus]
    ) -> dict[str, list[str]]:
        """
        Find orchestrator functions that coordinate active functions.

        An orchestrator is a function that:
        - Has no active transformations itself (completely_unused)
        - But calls other functions that ARE active

        Examples: run_pipeline, main, execute_workflow

        Returns:
            Dict mapping orchestrator name to list of active functions it calls
        """
        orchestrators: dict[str, list[str]] = {}

        # Get names of active functions
        active_func_names = {
            name
            for name, status in function_statuses.items()
            if status.category in ("fully_active", "partially_used", "active_helper")
        }

        if not active_func_names:
            return orchestrators

        # Check functions that could be orchestrators
        # Include both completely_unused (has tx but none active) and no_transformations (no tx at all)
        for func in asg.functions:
            status = function_statuses.get(func.name)
            if not status:
                continue
            # Skip if already marked as active helper
            if status.category == "active_helper":
                continue
            # Only consider functions without active transformations
            if status.category not in ("completely_unused", "no_transformations"):
                continue

            # Find function calls within this function's body
            called_active_funcs: list[str] = []
            for active_name in active_func_names:
                # Check if this function calls the active function
                # We look for the pattern: active_name( in the function's line range
                for other_func in asg.functions:
                    if other_func.name == active_name:
                        # Check if there's a call site within the orchestrator's lines
                        # Using a simple heuristic: if the orchestrator's lines contain
                        # a reference to the active function name followed by (
                        # This is approximated by checking transformations and sources
                        pass

                # Simpler approach: look for the function name in call sites
                # Check the source code pattern by looking at the function's logic
                # Since we don't have direct access to source here, use the function
                # definitions to infer: if func A is in same file as func B and
                # func A (orchestrator) spans lines that could call func B (active)
                # and A comes after B in definition order, A likely calls B

                # For now, use a naming heuristic for common orchestrator patterns
                if func.name in ("run_pipeline", "main", "run", "execute", "process"):
                    called_active_funcs.append(active_name)

            # Better approach: check if the function's body contains calls to active functions
            # We can approximate this by checking if any active function's line range
            # is BEFORE this orchestrator (meaning it could be called)
            called_funcs_found = []
            for active_name in active_func_names:
                active_status = function_statuses.get(active_name)
                if active_status and active_status.line_start < status.line_start:
                    # The active function is defined before the orchestrator
                    # High chance the orchestrator calls it
                    called_funcs_found.append(active_name)

            # Only mark as orchestrator if:
            # 1. It would call at least 5 active functions (strong signal)
            # 2. OR it's named like a typical entry point AND calls at least 2 functions
            is_entry_point_name = func.name.lower() in (
                "run_pipeline",
                "main",
                "run",
                "execute",
                "process",
                "run_etl",
                "run_job",
                "main_pipeline",
                "orchestrate",
            )
            min_calls = 2 if is_entry_point_name else 5

            if len(called_funcs_found) >= min_calls:
                orchestrators[func.name] = called_funcs_found

        return orchestrators

    def export_markdown(self, report: AuditReport, output_path: str | Path) -> None:
        """
        Export the audit report as a Markdown file.

        Args:
            report: The generated AuditReport
            output_path: Path to write the .md file
        """
        lines: list[str] = []

        # Header
        lines.append("# 📋 Auditoría de Migración: Análisis de Código Muerto")
        lines.append("")
        lines.append(f"**Pipeline**: {report.pipeline_name}  ")
        lines.append(f"**Archivo Fuente**: `{report.source_file}`  ")
        lines.append(f"**Fecha**: {report.generated_at}  ")
        lines.append("")

        # Summary
        lines.append("## 📊 Resumen de Eficiencia")
        lines.append("")
        lines.append("| Métrica | Valor |")
        lines.append("|---------|-------|")
        lines.append(f"| Nodos Totales | {report.total_nodes} |")
        lines.append(
            f"| Nodos Activos | {report.active_nodes} ({report.efficiency_percent:.1f}%) |"
        )
        lines.append(f"| Funciones Totales | {report.total_functions} |")
        lines.append(f"| Funciones Activas | {report.active_functions} |")
        lines.append(f"| Código Muerto | {report.dead_code_percent:.1f}% |")
        lines.append("")

        # Code organization
        lines.append("### 🏗️ Organización del Código")
        lines.append("")
        code_style = "**Modular**" if report.is_modular else "**Procedural**"
        lines.append(f"- Estilo de código: {code_style}")
        lines.append(
            f"- Nodos en Global Scope: {report.global_scope_nodes} ({report.global_scope_active} activos)"
        )
        nodes_in_funcs = (
            report.total_nodes - report.global_scope_nodes - len(report.completely_unused)
        )
        lines.append(f"- Nodos en Funciones: {nodes_in_funcs}")
        lines.append("")

        # Completely unused functions (RED)
        if report.completely_unused:
            lines.append("## 🔴 Funciones Huérfanas (Candidatas a Eliminación)")
            lines.append("")
            lines.append("Estas funciones están definidas pero **no contribuyen a ningún Sink**.")
            lines.append("")
            lines.append("| Función | Líneas | Nodos | Recomendación |")
            lines.append("|---------|--------|-------|---------------|")
            for func in report.completely_unused:
                lines.append(
                    f"| `{func.name}` | {func.line_range_display} | "
                    f"{func.total_nodes} | No migrar |"
                )
            lines.append("")

        # Partially used functions (YELLOW)
        if report.partially_used:
            lines.append("## 🟡 Funciones Parcialmente Activas")
            lines.append("")
            lines.append("Estas funciones tienen lógica que **no se ejecuta** en el flujo actual.")
            lines.append("")
            lines.append("| Función | Activos | Total | % Uso | Nodos Inactivos |")
            lines.append("|---------|---------|-------|-------|-----------------|")
            for func in report.partially_used:
                inactive_preview = ", ".join(func.inactive_node_ids[:3])
                if len(func.inactive_node_ids) > 3:
                    inactive_preview += f" +{len(func.inactive_node_ids) - 3}"
                lines.append(
                    f"| `{func.name}` | {func.active_nodes} | {func.total_nodes} | "
                    f"{func.usage_percent:.0f}% | {inactive_preview} |"
                )
            lines.append("")

        # Fully active functions (GREEN)
        if report.fully_active:
            lines.append("## 🟢 Funciones Completamente Activas")
            lines.append("")
            lines.append("Estas funciones están 100% utilizadas en el pipeline.")
            lines.append("")
            for func in report.fully_active:
                lines.append(
                    f"- `{func.name}` ({func.line_range_display}) - {func.total_nodes} nodos"
                )
            lines.append("")

        # Active helpers (BLUE)
        if report.active_helpers:
            lines.append("## 🔵 Funciones Auxiliares Activas")
            lines.append("")
            lines.append(
                "Funciones sin transformaciones Spark propias, pero **llamadas desde código activo**."
            )
            lines.append("")
            for func in report.active_helpers:
                lines.append(f"- `{func.name}` ({func.line_range_display})")
            lines.append("")

        # Orchestrators (PURPLE)
        if report.orchestrators:
            lines.append("## 🟣 Orquestadores")
            lines.append("")
            lines.append("Funciones que **coordinan el pipeline** invocando funciones activas.")
            lines.append("")
            for func in report.orchestrators:
                called = ", ".join(func.called_functions[:5])
                if len(func.called_functions) > 5:
                    called += f" +{len(func.called_functions) - 5} más"
                lines.append(f"- `{func.name}` ({func.line_range_display}) → llama a: {called}")
            lines.append("")

        # Pure utility functions
        non_helper_utils = [
            f
            for f in report.no_transformations
            if f.name not in {h.name for h in report.active_helpers}
        ]
        if non_helper_utils:
            lines.append("## ⚪ Funciones de Utilidad (Sin Spark)")
            lines.append("")
            lines.append("Funciones Python puras no utilizadas en el flujo de datos.")
            lines.append("")
            for func in non_helper_utils:
                lines.append(f"- `{func.name}` ({func.line_range_display})")
            lines.append("")

        # Recommendation
        lines.append("---")
        lines.append("")
        lines.append("## 💡 Recomendación del Arquitecto")
        lines.append("")

        if report.dead_code_percent > 30:
            lines.append(
                f"El **{report.dead_code_percent:.0f}%** del código analizado no tiene impacto en los resultados finales. "
                f"Se recomienda **no migrar** las funciones huérfanas para reducir la complejidad "
                f"de las Dynamic Tables resultantes."
            )
        elif report.dead_code_percent > 10:
            lines.append(
                f"Se detectó un **{report.dead_code_percent:.0f}%** de código sin uso. "
                f"Considere revisar las funciones parcialmente activas para optimizar el pipeline."
            )
        else:
            lines.append(
                f"El pipeline tiene una excelente eficiencia (**{report.efficiency_percent:.0f}%** activo). "
                f"El código está bien organizado para la migración."
            )

        lines.append("")

        # Footer
        lines.append("---")
        lines.append("*Generado por SparkToDynamicTable Analyzer*")

        # Write file
        output_path = Path(output_path)
        output_path.write_text("\n".join(lines), encoding="utf-8")

    def export_json(self, report: AuditReport, output_path: str | Path) -> None:
        """
        Export the audit report as JSON for programmatic consumption.

        Args:
            report: The generated AuditReport
            output_path: Path to write the .json file
        """
        import json

        def status_to_dict(status: FunctionStatus) -> dict[str, Any]:
            return {
                "name": status.name,
                "line_start": status.line_start,
                "line_end": status.line_end,
                "total_nodes": status.total_nodes,
                "active_nodes": status.active_nodes,
                "usage_percent": round(status.usage_percent, 1),
                "category": status.category,
                "inactive_node_ids": status.inactive_node_ids,
            }

        data = {
            "metadata": {
                "pipeline_name": report.pipeline_name,
                "source_file": report.source_file,
                "generated_at": report.generated_at,
            },
            "summary": {
                "total_nodes": report.total_nodes,
                "active_nodes": report.active_nodes,
                "efficiency_percent": round(report.efficiency_percent, 1),
                "dead_code_percent": round(report.dead_code_percent, 1),
                "total_functions": report.total_functions,
                "active_functions": report.active_functions,
                "is_modular": report.is_modular,
                "global_scope_nodes": report.global_scope_nodes,
                "global_scope_active": report.global_scope_active,
            },
            "functions": {
                "completely_unused": [status_to_dict(f) for f in report.completely_unused],
                "partially_used": [status_to_dict(f) for f in report.partially_used],
                "fully_active": [status_to_dict(f) for f in report.fully_active],
                "active_helpers": [status_to_dict(f) for f in report.active_helpers],
                "orchestrators": [status_to_dict(f) for f in report.orchestrators],
            },
        }

        output_path = Path(output_path)
        output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def export_readiness_markdown(self, report: AuditReport, output_path: str | Path) -> None:
        """
        Export the modernization readiness report as Markdown.

        This report focuses on migration readiness with:
        - Readiness Matrix: All functions with LOC, nodes, status, usage
        - Migration Summary: Functions to migrate, optimization stats
        - Migration Impact: Effort breakdown (automatic, configuration, manual)

        Args:
            report: The generated AuditReport
            output_path: Path to write the .md file
        """
        lines: list[str] = []

        # Header
        lines.append("# 🚀 Auditoría de Modernización: Análisis de Readiness")
        lines.append("")
        lines.append(f"**Pipeline**: {report.pipeline_name}  ")
        lines.append(f"**Archivo Fuente**: `{report.source_file}`  ")
        lines.append(f"**Fecha**: {report.generated_at}  ")
        lines.append("")

        # === SECTION 1: Resumen de Migración Reestructurado ===
        lines.append("## 📊 Resumen de Migración")
        lines.append("")

        # Calculate metrics
        total_funcs = report.total_functions
        active_funcs = report.active_functions
        orphan_count = len(report.completely_unused) + len(
            [
                f
                for f in report.no_transformations
                if f.name not in {h.name for h in report.active_helpers}
                and f.name not in {o.name for o in report.orchestrators}
            ]
        )

        # Nodes breakdown
        total_nodes = report.total_nodes
        active_nodes = report.active_nodes
        optimized_nodes = total_nodes - active_nodes

        # Core functions (fully_active + partially_used with transformations)
        core_funcs = len(report.fully_active) + len(report.partially_used)
        len(report.active_helpers)
        orchestrator_funcs = len(report.orchestrators)

        lines.append("| Métrica | Valor | Detalle |")
        lines.append("|---------|-------|---------|")
        lines.append(
            f"| **Funciones para Migrar** | {active_funcs} de {total_funcs} | {(active_funcs/total_funcs*100):.1f}% del diseño funcional |"
        )
        lines.append(
            f"| **Nodos de Transformación** | {active_nodes} activos | El núcleo del SQL generado |"
        )
        lines.append(
            f"| **Optimización Automática** | {optimized_nodes} nodos | {(optimized_nodes/total_nodes*100):.1f}% excluidos (código muerto + expresiones) |"
        )
        lines.append(
            f"| **Código Huérfano Real** | {orphan_count} funciones | {(orphan_count/total_funcs*100):.1f}% del archivo |"
        )
        lines.append("")

        # === SECTION 2: Visualización de Impacto de Migración ===
        lines.append("## 🎯 Impacto de Migración")
        lines.append("")
        lines.append("Desglose del esfuerzo de transpilación por categoría:")
        lines.append("")

        # Calculate percentages with helper subcategories
        # Core transformations: Green + Yellow (direct SQL conversion)
        automatic_funcs = core_funcs

        # Helpers broken down by type
        loader_funcs = len(report.loaders)
        writer_funcs = len(report.writers)
        udf_funcs = len(report.udfs)
        other_helper_funcs = len(report.other_helpers)

        config_heavy_funcs = orchestrator_funcs  # Purple - Tasks/orchestration
        manual_funcs = orphan_count  # White + Red

        automatic_pct = (automatic_funcs / total_funcs * 100) if total_funcs > 0 else 0
        loader_pct = (loader_funcs / total_funcs * 100) if total_funcs > 0 else 0
        writer_pct = (writer_funcs / total_funcs * 100) if total_funcs > 0 else 0
        udf_pct = (udf_funcs / total_funcs * 100) if total_funcs > 0 else 0
        other_helper_pct = (other_helper_funcs / total_funcs * 100) if total_funcs > 0 else 0
        config_heavy_pct = (config_heavy_funcs / total_funcs * 100) if total_funcs > 0 else 0
        manual_pct = (manual_funcs / total_funcs * 100) if total_funcs > 0 else 0

        # Total migratable = all except manual
        config_total_pct = loader_pct + writer_pct + udf_pct + other_helper_pct + config_heavy_pct
        migratable_pct = automatic_pct + config_total_pct

        lines.append("| Nivel | Funciones | % | Destino Snowflake |")
        lines.append("|-------|-----------|---|-------------------|")
        lines.append(
            f"| 🟢 **Automático** | {automatic_funcs} | {automatic_pct:.0f}% | `transformations.sql` → CTEs/Dynamic Tables |"
        )
        if loader_funcs > 0:
            lines.append(
                f"| 🔵 **Loaders** | {loader_funcs} | {loader_pct:.0f}% | `sources.sql` → Stages/External Tables |"
            )
        if writer_funcs > 0:
            lines.append(
                f"| 🟠 **Writers** | {writer_funcs} | {writer_pct:.0f}% | `sinks.sql` → Dynamic Tables con TARGET |"
            )
        if udf_funcs > 0:
            lines.append(
                f"| 🟣 **UDFs** | {udf_funcs} | {udf_pct:.0f}% | `functions.sql` → Python UDFs |"
            )
        if other_helper_funcs > 0:
            lines.append(
                f"| 🔷 **Otros Helpers** | {other_helper_funcs} | {other_helper_pct:.0f}% | Helpers directos |"
            )
        if config_heavy_funcs > 0:
            lines.append(
                f"| 🔮 **Orquestador** | {config_heavy_funcs} | {config_heavy_pct:.0f}% | `orchestration.sql` → Stored Procedure |"
            )
        lines.append(f"| ⏸️ **Omitir** | {manual_funcs} | {manual_pct:.0f}% | No se migran |")
        lines.append("")

        # === SECTION 3: Matriz de Readiness ===
        lines.append("## 📋 Matriz de Readiness")
        lines.append("")
        lines.append("Estado de preparación por función:")
        lines.append("")
        lines.append("| Función | Categoría | LOC | Nodos Spark | Estado | % Uso |")
        lines.append("|---------|-----------|-----|-------------|--------|-------|")

        # Collect all functions with their status
        all_functions: list[tuple[FunctionStatus, str, str]] = []

        # Orchestrators first (they coordinate everything)
        for func in report.orchestrators:
            all_functions.append((func, "🔮 Orquestador", "ACTIVA"))

        # Fully active (core transformations)
        for func in report.fully_active:
            all_functions.append((func, "🟢 Core", "ACTIVA"))

        # Partially used (optimized)
        for func in report.partially_used:
            all_functions.append((func, "🟡 Parcial", "OPTIMIZADA"))

        # Active helpers - broken down by type
        for func in report.loaders:
            all_functions.append((func, "🔵 Loader", "ACTIVA"))
        for func in report.writers:
            all_functions.append((func, "🟠 Writer", "ACTIVA"))
        for func in report.udfs:
            all_functions.append((func, "🟣 UDF", "ACTIVA"))
        for func in report.other_helpers:
            all_functions.append((func, "🔷 Helper", "ACTIVA"))

        # Orphan functions (completely_unused that are NOT orchestrators)
        for func in report.completely_unused:
            if func.name not in {o.name for o in report.orchestrators}:
                all_functions.append((func, "🔴 Huérfana", "INACTIVA"))

        # Pure utility (no_transformations not in active_helpers)
        active_helper_names = {h.name for h in report.active_helpers}
        orchestrator_names = {o.name for o in report.orchestrators}
        for func in report.no_transformations:
            if func.name not in active_helper_names and func.name not in orchestrator_names:
                all_functions.append((func, "⚪ Utilidad", "HUÉRFANA"))

        # Sort by line number for natural reading order
        all_functions.sort(key=lambda x: x[0].line_start)

        # Generate table rows
        for func, category, status in all_functions:
            # Calculate usage percent with special cases
            if category == "🔮 Orquestador":
                usage = "100%¹"
            elif category in ("🔵 Loader", "🟠 Writer"):
                usage = "100%²"
            elif category == "🟣 UDF":
                usage = "100%³"
            elif category == "🔷 Helper":
                usage = "100%"
            elif category in ("🔴 Huérfana", "⚪ Utilidad"):
                usage = "0%"
            elif func.total_nodes == 0:
                usage = "—"
            else:
                usage = f"{func.usage_percent:.0f}%"

            lines.append(
                f"| `{func.name}` | {category} | {func.loc} | {func.total_nodes} | {status} | {usage} |"
            )

        lines.append("")

        # Footnotes
        lines.append("**Notas:**")
        lines.append("- ¹ Orquestadores: 100% activos si invocan funciones activas")
        lines.append("- ² Loaders/Writers: Activos vía data sources/sinks detectados")
        lines.append("- ³ UDFs: Activas vía referencia `func_name_udf()`")

        # === SECTION 4: Detalle de Optimizaciones ===
        if report.partially_used:
            lines.append("")
            lines.append("## 🔧 Detalle de Optimizaciones")
            lines.append("")
            lines.append("Código muerto detectado dentro de funciones activas:")
            lines.append("")

            for func in report.partially_used:
                dead_nodes = func.total_nodes - func.active_nodes
                inactive_list = ", ".join(func.inactive_node_ids[:5])
                if len(func.inactive_node_ids) > 5:
                    inactive_list += f" +{len(func.inactive_node_ids) - 5} más"
                lines.append(
                    f"- **`{func.name}`**: {dead_nodes} nodos eliminados ({inactive_list})"
                )

            lines.append("")

        # === SECTION 5: Recomendaciones ===
        lines.append("---")
        lines.append("")
        lines.append("## 💡 Recomendación de Migración")
        lines.append("")

        if migratable_pct >= 90:
            lines.append(
                f"✅ **Excelente viabilidad de migración.** "
                f"El {migratable_pct:.0f}% del código es migrable ({automatic_pct:.0f}% automático + "
                f"{config_total_pct:.0f}% con configuración). "
                f"El pipeline está listo para la modernización."
            )
        elif migratable_pct >= 70:
            lines.append(
                f"🟡 **Buena viabilidad de migración.** "
                f"El {migratable_pct:.0f}% del código es migrable. "
                f"Las funciones auxiliares requieren definir UDFs/Stages en Snowflake."
            )
        else:
            lines.append(
                f"⚠️ **Revisar arquitectura antes de migrar.** "
                f"Solo el {migratable_pct:.0f}% es migrable. "
                f"Considere refactorizar el código Spark antes de la transpilación."
            )

        lines.append("")
        lines.append("---")
        lines.append("*Generado por SparkToDynamicTable Analyzer*")

        # Write file
        output_path = Path(output_path)
        output_path.write_text("\n".join(lines), encoding="utf-8")
