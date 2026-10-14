"""
Sink-First Inliner - Collapse all transformations into CTEs per Sink.

This module implements the "Sink-First Inlining" strategy:
- Instead of generating one SQL file per transformation (18+ files)
- Generate ONE SQL file per Sink with all intermediate steps as CTEs (5 files)

Benefits:
- No orphan tables (TX_017, TX_024 don't exist in Snowflake)
- Credit savings: Snowflake manages 5 dependency graphs, not 18
- Maintainability: One file per business output

Algorithm:
1. Identify all Sinks (write operations)
2. For each Sink, trace backward to find all contributing nodes
3. Generate a single SQL with CTEs for each intermediate step
4. The final SELECT feeds the Dynamic Table / View
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from asg_pyspark.analysis.spark_to_sql import (
    extract_filter_condition,
    extract_withcolumn_expression,
    load_window_specs_from_asg,
    set_expression_scope,
    spark_expr_to_sql,
)

from warp_core.symbol_table import SymbolTable
from warp_core.ir.pyspark_models import ASG, DataSink, DataSource, TransformationNode


@dataclass
class SinkPipeline:
    """A complete pipeline from sources to a single sink."""

    sink: DataSink
    sink_name: str

    # Nodes in topological order (sources first, sink last)
    sources: list[DataSource] = field(default_factory=list)
    transformations: list[TransformationNode] = field(default_factory=list)

    # The generated SQL with CTEs
    sql: str = ""

    # Metadata
    node_count: int = 0
    cte_count: int = 0


@dataclass
class InliningResult:
    """Result of sink-first inlining."""

    pipelines: list[SinkPipeline] = field(default_factory=list)

    # Statistics
    total_sinks: int = 0
    total_nodes_inlined: int = 0
    original_tx_count: int = 0

    @property
    def reduction_ratio(self) -> float:
        """How many fewer SQL objects we generate."""
        if self.original_tx_count == 0:
            return 0.0
        # Original: sources + transformations + sinks
        # New: just sinks (sources become CTEs)
        return 1 - (self.total_sinks / (self.original_tx_count + self.total_sinks))


class SinkInliner:
    """
    Collapses all upstream transformations into CTEs for each sink.

    This produces ONE SQL file per Sink, with all intermediate steps
    wrapped as CTEs inside a single Dynamic Table definition.

    Handles orphan nodes (nodes with empty inputs) by finding the
    nearest valid parent transformation on the same line.
    """

    # Checkpoint operations that might benefit from materialization hints
    CHECKPOINT_OPS = frozenset(
        {
            "join",
            "crossJoin",
            "groupBy_agg",
            "agg",
            "union",
            "unionAll",
        }
    )

    # Operations that are typically orphaned (column-level, not DataFrame-level)
    ORPHAN_OPS = frozenset(
        {
            "alias",
            "cast",
            "otherwise",
        }
    )

    # Columns that should receive defensive casting (Bronze Layer pattern)
    # These are common numeric columns that often have data quality issues
    DEFENSIVE_COLUMNS = frozenset({"amount", "price", "quantity", "discount", "revenue", "cost"})

    def __init__(
        self,
        *,
        target_lag: str = "1 day",
        warehouse: str = "COMPUTE_WH",
        use_dynamic_tables: bool = True,
        defensive_casting: bool = True,  # Enable defensive casting by default
    ) -> None:
        self.target_lag = target_lag
        self.warehouse = warehouse
        self.use_dynamic_tables = use_dynamic_tables
        self.defensive_casting = defensive_casting

        # Lookups built during inlining
        self._tx_lookup: dict[str, TransformationNode] = {}
        self._source_lookup: dict[str, DataSource] = {}
        self._sink_lookup: dict[str, DataSink] = {}

        # CTE naming: node_id -> cte_name
        self._cte_names: dict[str, str] = {}

        # Line number lookup for orphan resolution
        self._line_to_txs: dict[int, list[str]] = {}
        
        # Control flow lookup for branch inlining (Fix 3)
        self._control_nodes: list[dict] = []
        self._tx_to_branch: dict[str, dict] = {}  # tx_id -> {ctrl_id, branch_label, condition}
        
        # Column renaming tracking for pre-projection (Golden SQL)
        self._column_renames: dict[str, str] = {}  # original -> typed (e.g., amount -> amount_typed)
        
        # Schema tracking for explicit column selection (Golden SQL - no SELECT *)
        self._cte_schemas: dict[str, list[str]] = {}  # cte_name -> [col1, col2, ...]

    def _get_tx_columns(self, tx: TransformationNode) -> list[str]:
        """Get output columns for a transformation from inferred_output.
        
        For UDF operations that use SELECT *, we inherit ALL columns from
        the input CTE (preserving PII columns like customer_email for masking policies).
        The UDF-generated column itself is excluded since it requires Snowflake UDF.
        """
        # For UDF operations, use SELECT * semantics - inherit all input columns
        if tx.parameters.get("contains_udf", False):
            udf_col = tx.parameters.get("column_name", "")
            input_columns = self._get_input_columns(tx)
            # Include all input columns (for masking policies), exclude UDF-generated column
            return [c for c in input_columns if c != udf_col]
        
        if tx.inferred_output:
            return [col.name for col in tx.inferred_output]
        
        return []

    def _get_input_columns(self, tx: TransformationNode) -> list[str]:
        """Get input columns for a transformation (from its input's schema)."""
        if not tx.inputs:
            return []
        
        # Try to get schema from the input CTE
        input_id = tx.inputs[0]
        cte_name = self._cte_names.get(input_id)
        if cte_name and cte_name in self._cte_schemas:
            return self._cte_schemas[cte_name]
        
        # Fallback to inferred_input
        if tx.inferred_input:
            return [col.name for col in tx.inferred_input]
        
        return []

    def _register_cte_schema(self, cte_name: str, columns: list[str]) -> None:
        """Register the schema (column list) for a CTE."""
        if columns:
            self._cte_schemas[cte_name] = columns

    def _format_column_list(self, columns: list[str], indent: str = "        ") -> str:
        """Format a list of columns for SQL SELECT."""
        if not columns:
            return "*"
        if len(columns) <= 3:
            return ", ".join(columns)
        # Multi-line for readability
        return "\n" + indent + f",\n{indent}".join(columns)

    def inline(self, asg: ASG) -> InliningResult:
        """
        Generate one SQL per Sink with all upstream as CTEs.

        Args:
            asg: The Abstract Semantic Graph

        Returns:
            InliningResult with one SinkPipeline per sink
        """
        # Load window specs from ASG for variable resolution
        load_window_specs_from_asg(asg)
        
        # Build lookups
        self._tx_lookup = {t.id: t for t in asg.transformations}
        self._source_lookup = {s.id: s for s in asg.data_in}
        self._sink_lookup = {s.id: s for s in asg.data_out}

        # Build line number lookup for orphan resolution (using location.start.line)
        self._line_to_txs = {}
        for tx in asg.transformations:
            tx_line = tx.location.start_line if tx.location else None
            if tx_line:
                if tx_line not in self._line_to_txs:
                    self._line_to_txs[tx_line] = []
                self._line_to_txs[tx_line].append(tx.id)
        
        # Build control flow lookup for branch inlining (Fix 3)
        self._control_nodes = getattr(asg, 'control_nodes', []) or []
        self._tx_to_branch = {}
        
        def safe_get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)
        
        for ctrl in self._control_nodes:
            control_type = safe_get(ctrl, 'control_type')
            merge_semantic = safe_get(ctrl, 'merge_semantic')
            if control_type == 'BRANCH' and merge_semantic == 'CONDITIONAL':
                branches = safe_get(ctrl, 'branches', [])
                for branch in branches:
                    branch_condition = safe_get(branch, 'condition', '')
                    branch_label = safe_get(branch, 'label', '')
                    steps = safe_get(branch, 'steps', [])
                    for step_id in steps:
                        self._tx_to_branch[step_id] = {
                            'ctrl_id': safe_get(ctrl, 'node_id'),
                            'branch_label': branch_label,
                            'condition': branch_condition,
                            'ctrl': ctrl,
                            'branch': branch,
                        }

        # Build param -> source lookup from execution_instances
        # This maps param_df_stores -> in_003 (stores_data) for cross-function joins
        self._param_to_source: dict[str, str] = {}
        exec_instances = getattr(asg, 'execution_instances', []) or []
        for inst in exec_instances:
            bindings = safe_get(inst, 'bindings', []) or []
            for binding in bindings:
                input_map = safe_get(binding, 'input_map', []) or []
                for mapping in input_map:
                    param_name = safe_get(mapping, 'parameter')
                    origin_node = safe_get(mapping, 'origin_node')
                    if param_name and origin_node:
                        # Store with param_ prefix (as used in transformation inputs)
                        self._param_to_source[f"param_{param_name}"] = origin_node

        result = InliningResult(
            total_sinks=len(asg.data_out),
            original_tx_count=len(asg.transformations),
        )

        # Process each sink
        for sink in asg.data_out:
            pipeline = self._build_pipeline(sink)
            if pipeline:
                result.pipelines.append(pipeline)
                result.total_nodes_inlined += pipeline.node_count

        return result

    def _resolve_orphan_node(self, node_id: str) -> str:
        """
        Resolve an orphan node (no inputs) to a valid parent.

        Orphan nodes occur when the parser creates nodes for column-level
        operations like .alias() that don't have DataFrame inputs.

        Strategy:
        1. Check if node has inputs - if yes, return as-is
        2. Search backwards in line numbers (up to 20 lines) for a valid parent
        3. Return the best candidate (prefer joins, then aggregations, then others)
        """
        tx = self._tx_lookup.get(node_id)
        if not tx:
            return node_id

        # If node has inputs, it's not orphan
        if tx.inputs:
            return node_id

        # If node is a source, it's not orphan
        if node_id in self._source_lookup:
            return node_id

        tx_line = tx.location.start_line if tx.location else None
        if not tx_line:
            return node_id

        # Priority order: join > groupBy_agg > select > filter > others
        priority_ops = ["join", "crossJoin", "groupBy_agg", "agg", "select", "filter", "withColumn"]

        best_candidate = None
        best_priority = 999
        best_line_distance = 999

        # Search in a range of lines (current line and up to 20 lines before)
        for line_offset in range(0, 21):
            search_line = tx_line - line_offset
            if search_line <= 0:
                break

            candidates = self._line_to_txs.get(search_line, [])

            for cand_id in candidates:
                if cand_id == node_id:
                    continue
                cand_tx = self._tx_lookup.get(cand_id)
                if not cand_tx:
                    continue

                # Must have inputs to be valid
                if not cand_tx.inputs:
                    continue

                # Skip other orphan-like operations
                if cand_tx.operation in self.ORPHAN_OPS:
                    continue

                # Check priority
                try:
                    priority = priority_ops.index(cand_tx.operation)
                except ValueError:
                    priority = 100  # Low priority for unknown ops

                # Prefer closer lines when priority is equal
                if priority < best_priority or (
                    priority == best_priority and line_offset < best_line_distance
                ):
                    best_priority = priority
                    best_candidate = cand_id
                    best_line_distance = line_offset

            # If we found a good candidate (join or agg), stop searching
            if best_candidate and best_priority <= 1:
                break

        return best_candidate if best_candidate else node_id

    def _build_pipeline(self, sink: DataSink) -> SinkPipeline | None:
        """Build a complete pipeline for a single sink."""
        if not sink.source_id:
            return None

        # Resolve orphan nodes (e.g., alias nodes with no inputs)
        resolved_source = self._resolve_orphan_node(sink.source_id)

        # Trace backward from sink to find all contributing nodes
        contributing_ids = self._trace_backward(resolved_source)

        # Separate sources and transformations
        sources = [
            self._source_lookup[nid] for nid in contributing_ids if nid in self._source_lookup
        ]
        transformations = [
            self._tx_lookup[nid] for nid in contributing_ids if nid in self._tx_lookup
        ]

        # Sort sources by ID for deterministic output
        sources = sorted(sources, key=lambda s: s.id)

        # Sort transformations in topological order
        transformations = self._topological_sort(transformations)

        # Reset per-pipeline state to avoid cross-contamination
        self._cte_names = {}
        self._cte_schemas = {}
        self._column_renames = {}
        
        # Generate CTE names
        for source in sources:
            self._cte_names[source.id] = self._get_source_cte_name(source)
        for i, tx in enumerate(transformations):
            self._cte_names[tx.id] = self._get_tx_cte_name(tx, i)

        # Generate the SQL
        sink_name = sink.name or f"SINK_{sink.id.upper()}"
        sql = self._generate_sql(sink, sources, transformations)

        return SinkPipeline(
            sink=sink,
            sink_name=sink_name,
            sources=sources,
            transformations=transformations,
            sql=sql,
            node_count=len(sources) + len(transformations),
            cte_count=len(sources) + len(transformations),
        )

    def _trace_backward(self, start_id: str) -> list[str]:
        """
        Trace backward from a node to find all contributing nodes.

        Returns an ordered list (not a set) for deterministic iteration.
        The order is breadth-first from sink to sources.
        """
        seen: set[str] = set()
        result: list[str] = []
        queue = [start_id]

        while queue:
            node_id = queue.pop(0)
            if node_id in seen:
                continue

            seen.add(node_id)
            result.append(node_id)

            # If it's a transformation, add its inputs to the queue
            if node_id in self._tx_lookup:
                tx = self._tx_lookup[node_id]
                for input_id in tx.inputs:
                    if input_id not in seen:
                        queue.append(input_id)
            # Sources are leaf nodes, nothing to trace further

        return result

    def _topological_sort(
        self, transformations: list[TransformationNode]
    ) -> list[TransformationNode]:
        """Sort transformations so dependencies come first."""
        tx_ids = {tx.id for tx in transformations}
        tx_by_id = {tx.id: tx for tx in transformations}

        # Build in-degree map
        in_degree: dict[str, int] = {tx.id: 0 for tx in transformations}
        for tx in transformations:
            for input_id in tx.inputs:
                if input_id in tx_ids:
                    in_degree[tx.id] += 1

        # Kahn's algorithm
        queue = [tx_id for tx_id, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            current_id = queue.pop(0)
            result.append(tx_by_id[current_id])

            # Reduce in-degree for dependents
            for tx in transformations:
                if current_id in tx.inputs and tx.id in in_degree:
                    in_degree[tx.id] -= 1
                    if in_degree[tx.id] == 0:
                        queue.append(tx.id)

        return result

    def _get_source_cte_name(self, source: DataSource) -> str:
        """Generate a CTE name for a source."""
        if source.name:
            return self._safe_name(source.name)
        if source.path:
            import os

            basename = os.path.basename(source.path)
            name = os.path.splitext(basename)[0]
            return self._safe_name(name)
        return f"SOURCE_{source.id.upper()}"

    def _get_tx_cte_name(self, tx: TransformationNode, index: int) -> str:
        """Generate a CTE name for a transformation."""
        operation = tx.operation

        # Use semantic naming based on operation
        if operation in ("join", "crossJoin"):
            left = self._get_input_cte_name(tx.inputs[0]) if tx.inputs else "DATA"
            right = self._get_input_cte_name(tx.inputs[1]) if len(tx.inputs) > 1 else "DATA"
            return f"joined_{left}_{right}".lower()

        if operation in ("groupBy_agg", "agg"):
            group_cols = tx.parameters.get("group_columns", [])
            if group_cols:
                cols = "_".join(self._safe_name(c) for c in group_cols[:2])
                return f"agg_by_{cols}".lower()
            return f"aggregated_step_{index}"

        if operation in ("filter", "where"):
            source = self._get_input_cte_name(tx.inputs[0]) if tx.inputs else "data"
            return f"filtered_{source}".lower()

        if operation == "select":
            source = self._get_input_cte_name(tx.inputs[0]) if tx.inputs else "data"
            return f"selected_{source}".lower()

        if operation == "withColumn":
            col_name = tx.parameters.get("column_name", "")
            if col_name:
                return f"with_{self._safe_name(col_name)}".lower()
            return f"enriched_step_{index}"

        if operation == "distinct":
            return f"distinct_step_{index}"

        if operation in ("orderBy", "sort"):
            return f"sorted_step_{index}"

        # Default
        return f"{operation}_step_{index}"

    def _get_input_cte_name(self, input_id: str) -> str:
        """Get the CTE name for an input reference.
        
        When defensive_casting is enabled and the input resolves to a raw source table,
        this method will ensure a defensive CTE exists for that source and return
        its name instead of the raw table name.
        """
        if input_id in self._cte_names:
            return self._cte_names[input_id]
        
        # Resolve param_* references to their bound source tables
        # e.g., param_df_stores -> in_003 -> stores_data
        if input_id.startswith("param_") and input_id in self._param_to_source:
            resolved_source_id = self._param_to_source[input_id]
            if resolved_source_id in self._source_lookup:
                source = self._source_lookup[resolved_source_id]
                return self._ensure_source_cte(source)
        
        if input_id in self._source_lookup:
            source = self._source_lookup[input_id]
            return self._ensure_source_cte(source)
        
        # Use global symbol table for cross-file source resolution
        source_binding = SymbolTable.resolve_source(input_id)
        if source_binding:
            # Find the actual DataSource by ID
            if source_binding.source_id in self._source_lookup:
                source = self._source_lookup[source_binding.source_id]
                return self._ensure_source_cte(source)
            # Fallback to source name (still apply defensive pattern if enabled)
            return self._safe_name(source_binding.source_name)
        
        # Fallback: if input_id looks like a variable name (not tx_/in_ prefix),
        # search for a source by name that might match
        if not input_id.startswith("tx_") and not input_id.startswith("in_"):
            # Try to find a source whose name contains the input_id
            for source in self._source_lookup.values():
                source_name = source.name or ""
                # Handle patterns like df_products -> products_data
                # by extracting the core name (removing df_ prefix, _data suffix)
                normalized_input = input_id.replace("df_", "").replace("_df", "")
                normalized_source = source_name.replace("_data", "").replace("data_", "")
                if normalized_input == normalized_source:
                    return self._ensure_source_cte(source)
        
        return input_id.replace("tx_", "").replace("in_", "")
    
    def _ensure_source_cte(self, source: DataSource) -> str:
        """Ensure a source has a CTE and return its name.
        
        When defensive_casting is enabled, this dynamically registers a defensive
        CTE for the source if it hasn't been registered yet.
        """
        # Check if we already have a CTE for this source
        if source.id in self._cte_names:
            return self._cte_names[source.id]
        
        # Generate the CTE name
        cte_name = self._get_source_cte_name(source)
        
        # For defensive casting, we need to ensure the CTE is registered
        # This will be used when generating the CTE SQL
        self._cte_names[source.id] = cte_name
        
        # Also add the source to the lookup for defensive column detection
        self._source_lookup[source.id] = source
        
        return cte_name

    def _generate_sql(
        self,
        sink: DataSink,
        sources: list[DataSource],
        transformations: list[TransformationNode],
    ) -> str:
        """Generate the complete SQL with CTEs."""
        lines = []
        sink_name = self._safe_name(sink.name or f"SINK_{sink.id}")

        # Header comment
        lines.append("-- ================================================")
        lines.append(f"-- {sink_name}")
        lines.append("-- Generated by Spark-DT-Analyzer (Sink-First Inlining)")
        lines.append(
            f"-- Nodes absorbed: {len(sources)} sources + {len(transformations)} transformations"
        )
        lines.append("-- ================================================")
        lines.append("")

        # Dynamic Table definition
        if self.use_dynamic_tables:
            lines.append(f"CREATE OR REPLACE DYNAMIC TABLE {sink_name}")
            lines.append(f"  TARGET_LAG = '{self.target_lag}'")
            lines.append(f"  WAREHOUSE = {self.warehouse}")
            lines.append("AS")
        else:
            lines.append(f"CREATE OR REPLACE VIEW {sink_name} AS")

        # CTEs
        cte_parts = []

        # Source CTEs (if they need transformation like CSV type casting)
        for source in sources:
            cte_name = self._cte_names[source.id]
            cte_sql = self._generate_source_cte(source)
            cte_parts.append(f"{cte_name} AS (\n{cte_sql}\n)")

        # Transformation CTEs with Branch Inlining (Fix 3)
        absorbed_tx_ids: set[str] = set()
        branch_cte_added = False
        
        # Get IDs of transformations that are ancestors of this sink
        tx_ids_in_pipeline = {tx.id for tx in transformations}
        
        # First, check if we have any CONDITIONAL branches to inline
        # ONLY process control nodes whose branches are ancestors of this sink
        for ctrl in self._control_nodes:
            # Handle both dict and Pydantic model
            merge_semantic = ctrl.get('merge_semantic') if isinstance(ctrl, dict) else getattr(ctrl, 'merge_semantic', None)
            if merge_semantic != 'CONDITIONAL':
                continue
                
            # Check if any branch tx is in our pipeline ancestors
            branches = ctrl.get('branches') if isinstance(ctrl, dict) else getattr(ctrl, 'branches', [])
            branch_tx_in_pipeline = False
            for branch in (branches or []):
                # ASG uses 'steps' not 'tx_ids'
                branch_steps = branch.get('steps') if isinstance(branch, dict) else getattr(branch, 'steps', [])
                if any(tx_id in tx_ids_in_pipeline for tx_id in (branch_steps or [])):
                    branch_tx_in_pipeline = True
                    break
            
            if not branch_tx_in_pipeline:
                # This control node's branches are NOT ancestors of the current sink - skip
                continue
            
            ctes_list, absorbed = self._generate_branch_unified_cte(ctrl, transformations)
            if ctes_list:
                for cte_name, cte_sql in ctes_list:
                    cte_parts.append(f"{cte_name} AS (\n{cte_sql}\n)")
                absorbed_tx_ids |= absorbed
                # Update cte_names so convergence point knows where to find us
                convergence_pt = ctrl.get('convergence_point') if isinstance(ctrl, dict) else getattr(ctrl, 'convergence_point', None)
                if ctes_list:
                    # The convergence point should reference the last unified CTE
                    last_cte_name = ctes_list[-1][0]
                    node_id = ctrl.get('node_id') if isinstance(ctrl, dict) else getattr(ctrl, 'node_id', '')
                    self._cte_names[f"branch_unified_{node_id}"] = last_cte_name
                    # Also register all absorbed tx_ids to point to the unified CTE
                    # so that convergence point transformations can find their input
                    for absorbed_id in absorbed:
                        self._cte_names[absorbed_id] = last_cte_name
                branch_cte_added = True
        
        for tx in transformations:
            # Skip absorbed transformations (already inlined in branch CTE)
            if tx.id in absorbed_tx_ids:
                continue
            
            cte_name = self._cte_names[tx.id]
            
            # Set expression scope for variable resolution (e.g., window_spec)
            tx_scope = tx.location.scope if tx.location else None
            set_expression_scope(tx_scope)
            
            cte_sql = self._generate_tx_cte(tx)
            cte_parts.append(f"{cte_name} AS (\n{cte_sql}\n)")
            
            # Register schema for this CTE (Golden SQL - explicit columns)
            tx_columns = self._get_tx_columns(tx)
            self._register_cte_schema(cte_name, tx_columns)

        if cte_parts:
            lines.append("WITH")
            lines.append(",\n".join(cte_parts))

        # Final SELECT from the last transformation (Golden SQL - explicit columns)
        if transformations:
            final_cte = self._cte_names[transformations[-1].id]
            last_tx = transformations[-1]
            
            # For UDF pipelines: Use SELECT * to preserve all columns including PII
            # (like customer_email) for Snowflake masking policies. The UDF CTE
            # uses SELECT *, so the final SELECT should too.
            if last_tx.parameters.get("contains_udf", False):
                # Use SELECT * EXCLUDE for UDF column (which wasn't generated)
                udf_col = last_tx.parameters.get("column_name", "")
                lines.append(f"SELECT * EXCLUDE ({udf_col}) FROM {final_cte};")
            else:
                # Non-UDF: use explicit columns when available
                final_columns = self._cte_schemas.get(final_cte, [])
                if final_columns:
                    col_list = self._format_column_list(final_columns)
                    lines.append(f"SELECT {col_list} FROM {final_cte};")
                else:
                    lines.append(f"SELECT * FROM {final_cte};")
        elif sources:
            # No transformations, select directly from source
            final_cte = self._cte_names[sources[0].id]
            final_columns = self._cte_schemas.get(final_cte, [])
            if final_columns:
                col_list = self._format_column_list(final_columns)
                lines.append(f"SELECT {col_list} FROM {final_cte};")
            else:
                lines.append(f"SELECT * FROM {final_cte};")
        else:
            lines.append("SELECT 1;  -- Empty pipeline")

        return "\n".join(lines)

    def _generate_source_cte(self, source: DataSource) -> str:
        """Generate CTE SQL for a source.
        
        When defensive_casting is enabled (Bronze Layer pattern), this method
        will inject COALESCE(TRY_CAST(...)) for known numeric columns to ensure
        consistent handling of data quality issues across all pipelines.
        """
        # Determine the actual source reference in Snowflake
        if source.type == "table" and source.name:
            # Table source: use table name directly
            source_ref = self._safe_name(source.name)
        elif source.path:
            # File source: use derived table name from path
            import os

            basename = os.path.basename(source.path)
            name = os.path.splitext(basename)[0]
            source_ref = self._safe_name(name)
        else:
            source_ref = self._safe_name(source.id)

        # For CSV sources, apply type casting
        if source.type == "csv" and source.inferred_columns:
            columns = []
            for col in source.inferred_columns:
                col_expr = self._generate_column_cast(col)
                columns.append(col_expr)
            return "    SELECT\n        " + ",\n        ".join(columns) + f"\n    FROM {source_ref}"

        # Defensive Casting (Bronze Layer pattern)
        # Inject COALESCE(TRY_CAST(...)) for known numeric columns
        if self.defensive_casting:
            defensive_columns = self._get_defensive_columns(source)
            if defensive_columns:
                cast_exprs = []
                for col_name in defensive_columns:
                    typed_name = f"{col_name}_typed"
                    cast_exprs.append(
                        f"COALESCE(TRY_CAST({col_name} AS NUMBER(18,2)), 0) AS {typed_name}"
                    )
                    # Track the rename for downstream reference
                    self._column_renames[col_name] = typed_name
                
                # SELECT *, defensive_casts FROM source
                cast_str = ",\n        ".join(cast_exprs)
                return f"    SELECT\n        *,\n        {cast_str}\n    FROM {source_ref}"

        # Default: simple SELECT *
        return f"    SELECT * FROM {source_ref}"
    
    def _get_defensive_columns(self, source: DataSource) -> list[str]:
        """Get columns from this source that need defensive casting.
        
        Returns columns that are:
        1. In DEFENSIVE_COLUMNS (known numeric columns like 'amount', 'price')
        2. Actually used by downstream transformations (from required_columns)
        """
        if not source.required_columns:
            return []
        
        # Get unique column names that are in DEFENSIVE_COLUMNS
        required_names = {col.name if hasattr(col, 'name') else col.get('name', '') 
                         for col in source.required_columns}
        
        return sorted(required_names & self.DEFENSIVE_COLUMNS)

    def _generate_column_cast(self, col: Any) -> str:
        """Generate column expression with type casting."""
        from warp_core.ir.pyspark_models import InferredType

        name = col.name
        inferred_type = col.inferred_type

        if inferred_type == InferredType.DATE:
            return f"TRY_TO_DATE({name}) AS {name}"
        elif inferred_type == InferredType.TIMESTAMP:
            return f"TRY_TO_TIMESTAMP({name}) AS {name}"
        elif inferred_type == InferredType.NUMERIC:
            return f"TRY_TO_NUMBER({name}) AS {name}"
        else:
            return name

    def _safe_get(self, obj: Any, key: str, default: Any = None) -> Any:
        """Safely get attribute from dict or Pydantic model."""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _should_inline_as_branch(self, tx: TransformationNode) -> tuple[bool, dict | None]:
        """Check if this tx is part of a CONDITIONAL branch that should be inlined as CASE WHEN."""
        if tx.id not in self._tx_to_branch:
            return False, None
        
        branch_info = self._tx_to_branch[tx.id]
        ctrl = branch_info.get('ctrl', {})
        
        # Only inline if merge_semantic is CONDITIONAL
        if self._safe_get(ctrl, 'merge_semantic') != 'CONDITIONAL':
            return False, None
            
        return True, branch_info

    def _generate_branch_unified_cte(
        self,
        ctrl: dict,
        transformations: list[TransformationNode],
    ) -> tuple[list[tuple[str, str]], set[str]]:
        """
        Generate unified CTEs with Pre-Projection for a CONDITIONAL branch.
        
        Golden SQL Pattern:
        1. input_normalized: TRY_CAST centralizado + filtro consolidado
        2. with_regional_enrichment: CASE WHEN usando valor ya tipado
        
        Returns:
            (list of (cte_name, cte_sql), absorbed_tx_ids)
        """
        branches = self._safe_get(ctrl, 'branches', [])
        if len(branches) != 2:
            return [], set()
        
        true_branch = next((b for b in branches if self._safe_get(b, 'label') == 'true'), None)
        false_branch = next((b for b in branches if self._safe_get(b, 'label') == 'false'), None)
        
        if not true_branch or not false_branch:
            return [], set()
        
        # Collect all tx IDs that will be absorbed
        absorbed = set(self._safe_get(true_branch, 'steps', [])) | set(self._safe_get(false_branch, 'steps', []))
        
        # Find the source
        true_steps = [self._tx_lookup.get(s) for s in self._safe_get(true_branch, 'steps', []) if s in self._tx_lookup]
        false_steps = [self._tx_lookup.get(s) for s in self._safe_get(false_branch, 'steps', []) if s in self._tx_lookup]
        
        source_ref = "UNKNOWN"
        if true_steps and true_steps[0] and true_steps[0].inputs:
            source_ref = self._get_input_cte_name(true_steps[0].inputs[0])
        
        # Extract expressions and filters from each branch
        true_columns = {}  # col_name -> expression
        false_columns = {}
        true_filter = None
        false_filter = None
        cast_columns = set()  # Columns that need TRY_CAST
        
        for tx in true_steps:
            if tx and tx.operation == 'withColumn':
                col_name = tx.parameters.get('column_name', 'col')
                if tx.logic:
                    _, expr = extract_withcolumn_expression(tx.logic)
                    if expr:
                        true_columns[col_name] = expr
                        cast_columns |= self._detect_arithmetic_columns(expr)
            elif tx and tx.operation in ('filter', 'where'):
                if tx.logic:
                    true_filter = extract_filter_condition(tx.logic)
        
        for tx in false_steps:
            if tx and tx.operation == 'withColumn':
                col_name = tx.parameters.get('column_name', 'col')
                if tx.logic:
                    _, expr = extract_withcolumn_expression(tx.logic)
                    if expr:
                        false_columns[col_name] = expr
                        cast_columns |= self._detect_arithmetic_columns(expr)
            elif tx and tx.operation in ('filter', 'where'):
                if tx.logic:
                    false_filter = extract_filter_condition(tx.logic)
        
        # Build consolidated filter
        true_condition = true_filter if true_filter else "TRUE"
        false_condition = false_filter if false_filter else "TRUE"
        consolidated_filter = f"({true_condition}) OR ({false_condition})" if true_filter and false_filter else ""
        
        ctes = []
        
        # [Step 1] Pre-Projection: Defensive Normalization (Certified)
        if cast_columns:
            cast_parts = []
            for col in sorted(cast_columns):
                # COALESCE ensures arithmetic calculations don't fail/nullify rows
                cast_parts.append(f"COALESCE(TRY_CAST({col} AS NUMBER(18,2)), 0) AS {col}_typed")
            
            # Payload Reduction: Only columns needed for final aggregation
            # country is needed for GROUP BY and CASE WHEN routing
            base_cols = ["country"]
            select_cols = base_cols + cast_parts
            
            where_clause = f"\n    WHERE {consolidated_filter}" if consolidated_filter else ""
            
            col_separator = ",\n        "
            normalize_sql = f"    SELECT\n        {col_separator.join(select_cols)}\n    FROM {source_ref}{where_clause}"
            ctes.append(("input_normalized", normalize_sql))
            
            # Track column renames for downstream CTEs (Golden SQL)
            for col in sorted(cast_columns):
                self._column_renames[col] = f"{col}_typed"
            
            # Update source for next CTE
            source_ref = "input_normalized"
        
        # [Step 2] Regional Enrichment with CASE WHEN (Certified)
        all_columns = set(true_columns.keys()) | set(false_columns.keys())
        
        # Payload Reduction: Only columns needed for aggregation
        select_parts = ["country"]
        if cast_columns:
            for col in sorted(cast_columns):
                select_parts.append(f"{col}_typed")
        
        for col_name in sorted(all_columns):
            true_expr = true_columns.get(col_name, 'NULL')
            false_expr = false_columns.get(col_name, 'NULL')
            
            # Replace raw column with typed version
            if cast_columns:
                for cc in cast_columns:
                    true_expr = true_expr.replace(cc, f"{cc}_typed")
                    false_expr = false_expr.replace(cc, f"{cc}_typed")
            
            case_expr = f"""CASE 
            WHEN {true_condition} THEN {true_expr}
            ELSE {false_expr}
        END AS {col_name}"""
            select_parts.append(case_expr)
        
        col_sep = ",\n        "
        enrichment_sql = f"    SELECT\n        {col_sep.join(select_parts)}\n    FROM {source_ref}"
        ctes.append(("with_regional_enrichment", enrichment_sql))
        
        # Register schema for the unified CTE so downstream CTEs know what columns are available
        # This is critical for proper schema propagation after branch unification
        unified_columns = ["country"]
        if cast_columns:
            for col in sorted(cast_columns):
                unified_columns.append(f"{col}_typed")
        unified_columns.extend(sorted(all_columns))
        self._register_cte_schema("with_regional_enrichment", unified_columns)
        
        return ctes, absorbed

    @staticmethod
    def _detect_arithmetic_columns(expr: str) -> set[str]:
        """Detect column names involved in multiplication.

        Uses sqlglot AST as primary; regex as fallback for
        PySpark-specific expressions sqlglot cannot parse.
        """
        columns: set[str] = set()
        try:
            import sqlglot
            parsed = sqlglot.parse_one(expr, error_level=sqlglot.ErrorLevel.IGNORE)
            for mul in parsed.find_all(sqlglot.exp.Mul):
                for side in (mul.left, mul.right):
                    if isinstance(side, sqlglot.exp.Column):
                        columns.add(side.name)
            if columns:
                return columns
        except Exception:
            pass
        import re
        for m in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*\*', expr):
            columns.add(m.group(1))
        return columns

    def _apply_defensive_cast(self, expr: str) -> str:
        """Apply TRY_CAST to arithmetic expressions.

        Uses sqlglot AST to find column*number patterns;
        regex as fallback for non-standard expressions.
        """
        if not expr:
            return expr

        try:
            import sqlglot
            from sqlglot import exp as sexp
            parsed = sqlglot.parse_one(expr, error_level=sqlglot.ErrorLevel.IGNORE)
            modified = False
            for mul in parsed.find_all(sexp.Mul):
                col_node = lit_node = None
                if isinstance(mul.left, sexp.Column) and isinstance(mul.right, sexp.Literal):
                    col_node, lit_node = mul.left, mul.right
                elif isinstance(mul.right, sexp.Column) and isinstance(mul.left, sexp.Literal):
                    col_node, lit_node = mul.right, mul.left
                if col_node and lit_node:
                    cast_node = sexp.TryCast(
                        this=col_node.copy(),
                        to=sexp.DataType(this=sexp.DataType.Type.DECIMAL, expressions=[
                            sexp.DataTypeParam(this=sexp.Literal.number(18)),
                            sexp.DataTypeParam(this=sexp.Literal.number(2)),
                        ]),
                    )
                    col_node.replace(cast_node)
                    modified = True
            if modified:
                return parsed.sql()
        except Exception:
            pass

        import re
        pattern = r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\*\s*(\d+\.?\d*)"
        def replace_with_cast(m):
            col = m.group(1)
            num = m.group(2)
            return f"TRY_CAST({col} AS NUMBER(18,2)) * {num}"
        return re.sub(pattern, replace_with_cast, expr)
    def _generate_tx_cte(self, tx: TransformationNode) -> str:
        """Generate CTE SQL for a transformation."""
        operation = tx.operation

        # Get input reference
        input_ref = self._get_input_cte_name(tx.inputs[0]) if tx.inputs else "UNKNOWN"

        # Dispatch to operation-specific handlers
        if operation == "filter" or operation == "where":
            return self._gen_filter_cte(tx, input_ref)
        elif operation == "select":
            return self._gen_select_cte(tx, input_ref)
        elif operation == "withColumn":
            return self._gen_with_column_cte(tx, input_ref)
        elif operation in ("join", "crossJoin"):
            return self._gen_join_cte(tx)
        elif operation in ("groupBy_agg", "agg"):
            return self._gen_agg_cte(tx, input_ref)
        elif operation == "distinct":
            input_columns = self._get_input_columns(tx)
            if input_columns:
                col_list = ", ".join(input_columns)
                return f"    SELECT DISTINCT {col_list} FROM {input_ref}"
            return f"    SELECT DISTINCT * FROM {input_ref}"
        elif operation in ("orderBy", "sort"):
            return self._gen_orderby_cte(tx, input_ref)
        elif operation == "limit":
            limit = tx.parameters.get("limit", 100)
            input_columns = self._get_input_columns(tx)
            if input_columns:
                col_list = ", ".join(input_columns)
                return f"    SELECT {col_list} FROM {input_ref} LIMIT {limit}"
            return f"    SELECT * FROM {input_ref} LIMIT {limit}"
        elif operation == "drop":
            return self._gen_drop_cte(tx, input_ref)
        elif operation == "withColumnRenamed":
            return self._gen_rename_cte(tx, input_ref)
        elif operation == "union" or operation == "unionAll":
            return self._gen_union_cte(tx)
        elif operation == "withColumn_custom":
            return self._gen_with_column_custom_cte(tx, input_ref)
        else:
            # Default: pass-through with SELECT * to preserve all columns
            return f"    -- [{operation}]\n    SELECT * FROM {input_ref}"

    def _gen_filter_cte(self, tx: TransformationNode, input_ref: str) -> str:
        """Generate filter CTE using AST-based expression parsing.
        
        When defensive_casting is enabled and this filter reads directly from
        a raw source table (not a CTE), defensive casting is injected here.
        """
        condition = None

        # Always try AST parser first for proper PySpark -> SQL conversion
        if tx.logic:
            condition = extract_filter_condition(tx.logic)

        # Fall back to raw parameter if AST fails
        if not condition:
            condition = tx.parameters.get("condition", "TRUE")

        # Check if we're reading from a raw source table (not a generated CTE)
        # A raw source is one where the input_ref directly matches a physical table name
        # (not an intermediate CTE name like 'with_amount_net')
        is_raw_source = (
            input_ref.upper() == input_ref or  # Physical tables are usually UPPERCASE
            input_ref.endswith("_DATA") or     # Common pattern: SALES_DATA, PRODUCTS_DATA
            "_" not in input_ref.lower()       # Simple table names without underscores
        )
        
        if self.defensive_casting and is_raw_source:
            # Find the source to get defensive columns
            defensive_cols = self._find_defensive_columns_for_source(input_ref)
            if defensive_cols:
                cast_exprs = []
                for col_name in defensive_cols:
                    typed_name = f"{col_name}_typed"
                    cast_exprs.append(
                        f"COALESCE(TRY_CAST({col_name} AS NUMBER(18,2)), 0) AS {typed_name}"
                    )
                    self._column_renames[col_name] = typed_name
                
                cast_str = ",\n        ".join(cast_exprs)
                return f"    SELECT\n        *,\n        {cast_str}\n    FROM {input_ref}\n    WHERE {condition}"

        # Default: SELECT * with filter
        return f"    SELECT * FROM {input_ref}\n    WHERE {condition}"
    
    def _find_defensive_columns_for_source(self, source_ref: str) -> list[str]:
        """Find defensive columns for a source table by name."""
        # Look for source by name (case-insensitive comparison)
        source_ref_lower = source_ref.lower()
        for source in self._source_lookup.values():
            source_name = (source.name or "").lower()
            if source_name == source_ref_lower or source_ref_lower in source_name:
                return self._get_defensive_columns(source)
        
        # No source found - return common defensive columns if they might apply
        # Based on known patterns (e.g., SALES_DATA likely has 'amount')
        if "sales" in source_ref_lower:
            return ["amount"]
        return []

    def _gen_select_cte(self, tx: TransformationNode, input_ref: str) -> str:
        """Generate select CTE."""
        columns = tx.parameters.get("columns", ["*"])
        if not columns:
            columns = ["*"]
        # Clean column names of quotes
        columns = [c.strip("'\"") for c in columns]
        col_str = ", ".join(columns)
        return f"    SELECT {col_str} FROM {input_ref}"

    def _gen_with_column_cte(self, tx: TransformationNode, input_ref: str) -> str:
        """Generate withColumn CTE using AST-based expression parsing."""
        col_name = tx.parameters.get("column_name", "new_col")
        expression = None

        # Always try AST parser first for proper PySpark -> SQL conversion
        if tx.logic:
            parsed_name, parsed_expr = extract_withcolumn_expression(tx.logic)
            if parsed_name:
                col_name = parsed_name
            if parsed_expr:
                expression = parsed_expr

        # Fall back to raw parameter if AST fails
        if not expression:
            expression = tx.parameters.get("expression", "NULL")

        # Apply column renames from pre-projection (Golden SQL)
        if self._column_renames:
            expression = self._rename_columns_in_expr(expression, self._column_renames)

        # Use SELECT * to preserve all input columns, then add the new column
        # This ensures join keys and other columns aren't lost in the pipeline
        return f"    SELECT *, {expression} AS {col_name} FROM {input_ref}"

    @staticmethod
    def _rename_columns_in_expr(expression: str, renames: dict[str, str]) -> str:
        """Rename columns in a SQL expression.

        Uses sqlglot AST to precisely rename Column nodes;
        regex with word-boundary matching as fallback.
        """
        try:
            import sqlglot
            from sqlglot import exp as sexp
            parsed = sqlglot.parse_one(expression, error_level=sqlglot.ErrorLevel.IGNORE)
            modified = False
            for col in parsed.find_all(sexp.Column):
                if col.name in renames:
                    col.set("this", sexp.to_identifier(renames[col.name]))
                    modified = True
            if modified:
                return parsed.sql()
        except Exception:
            pass
        import re
        for orig, renamed in renames.items():
            pattern = rf"(?<![a-zA-Z0-9_]){re.escape(orig)}(?![a-zA-Z0-9_])"
            expression = re.sub(pattern, renamed, expression)
        return expression

    def _gen_join_cte(self, tx: TransformationNode) -> str:
        """Generate join CTE."""
        # Use _get_input_cte_name for proper source resolution
        left_ref = self._get_input_cte_name(tx.inputs[0]) if tx.inputs else "UNKNOWN"
        right_ref = (
            self._get_input_cte_name(tx.inputs[1]) if len(tx.inputs) > 1 else "UNKNOWN"
        )

        join_type = tx.parameters.get("join_type", "inner").upper()
        # Try multiple parameter names for join key (different ASG versions)
        join_key = (
            tx.parameters.get("join_key")
            or tx.parameters.get("join_condition")
            or tx.parameters.get("on")
            or "id"  # Last resort fallback
        )

        # Clean join key of quotes
        if isinstance(join_key, str):
            join_key = join_key.strip("'\"")
        elif isinstance(join_key, list):
            join_key = [k.strip("'\"") for k in join_key]

        # Handle array of keys
        if isinstance(join_key, list):
            on_clause = " AND ".join(f"a.{k} = b.{k}" for k in join_key)
            exclude_str = ", ".join(join_key)
        else:
            on_clause = f"a.{join_key} = b.{join_key}"
            exclude_str = join_key

        return f"""    SELECT a.*, b.* EXCLUDE ({exclude_str})
    FROM {left_ref} a
    {join_type} JOIN {right_ref} b ON {on_clause}"""

    def _gen_agg_cte(self, tx: TransformationNode, input_ref: str) -> str:
        """Generate aggregation CTE with proper parsing of logic."""
        group_cols = tx.parameters.get("group_columns", [])
        column_aliases = tx.parameters.get("column_aliases", [])
        agg_exprs = tx.parameters.get("agg_expressions", [])

        # Try to parse logic if agg_expressions is empty
        if not agg_exprs and tx.logic:
            agg_exprs = self._parse_agg_expressions(tx.logic, column_aliases)

        if not agg_exprs:
            agg_exprs = ["COUNT(*) AS count"]

        # Clean column names of quotes
        group_cols = [c.strip("'\"") for c in group_cols]

        # Add audit column if processed_at exists in pipeline
        audit_col = ""
        if any("processed_at" in str(tx.logic or "") for tx in self._tx_lookup.values()):
            audit_col = ", MAX(processed_at) AS last_updated_at"
        
        if group_cols:
            group_str = ", ".join(group_cols)
            agg_str = ", ".join(agg_exprs)
            return (
                f"    SELECT {group_str}, {agg_str}{audit_col}\n    FROM {input_ref}\n    GROUP BY {group_str}"
            )
        else:
            agg_str = ", ".join(agg_exprs)
            return f"    SELECT {agg_str}{audit_col} FROM {input_ref}"

    def _parse_agg_expressions(self, logic: str, aliases: list[str]) -> list[str]:
        """Parse aggregation expressions from logic string.

        Uses Python ast as primary parser for PySpark expressions
        like F.sum('col').alias('name'); regex as fallback.
        """
        results = self._parse_agg_with_ast(logic)
        if results:
            return results
        return self._parse_agg_with_regex(logic)

    @staticmethod
    def _parse_agg_with_ast(logic: str) -> list[str]:
        """Extract aggregation expressions using Python ast."""
        import ast
        results: list[str] = []
        AGG_FUNCS = {"sum", "count", "avg", "min", "max", "mean", "first", "last"}
        try:
            tree = ast.parse(logic, mode="eval")
        except SyntaxError:
            try:
                tree = ast.parse(logic, mode="exec")
            except SyntaxError:
                return []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = None
            if isinstance(node.func, ast.Attribute) and node.func.attr == "alias":
                inner = node.func.value
                if isinstance(inner, ast.Call):
                    if isinstance(inner.func, ast.Attribute):
                        func_name = inner.func.attr
                    elif isinstance(inner.func, ast.Name):
                        func_name = inner.func.id
                    if func_name and func_name.lower() in AGG_FUNCS:
                        col_arg = "*"
                        if inner.args and isinstance(inner.args[0], ast.Constant):
                            col_arg = str(inner.args[0].value)
                        alias_arg = None
                        if node.args and isinstance(node.args[0], ast.Constant):
                            alias_arg = str(node.args[0].value)
                        if alias_arg:
                            fn = func_name.upper()
                            if fn == "MEAN":
                                fn = "AVG"
                            col_str = "*" if col_arg == "*" else col_arg
                            results.append(f"{fn}({col_str}) AS {alias_arg}")
        return results

    @staticmethod
    def _parse_agg_with_regex(logic: str) -> list[str]:
        """Regex fallback for aggregation expressions."""
        import re
        results = []
        pattern = r"F?\.?(sum|count|avg|min|max)\(['\"]?([^'\"\)\]]+)['\"]?\)\.alias\(['\"]([^'\"]+)['\"]\)"
        for match in re.finditer(pattern, logic, re.IGNORECASE):
            func = match.group(1).upper()
            col = match.group(2).strip("'\"")
            alias = match.group(3)
            if col == '*':
                results.append(f"{func}(*) AS {alias}")
            else:
                results.append(f"{func}({col}) AS {alias}")
        return results


    def _gen_orderby_cte(self, tx: TransformationNode, input_ref: str) -> str:
        """Generate orderBy CTE."""
        columns = tx.parameters.get("columns", [])
        if not columns:
            input_columns = self._get_input_columns(tx)
            if input_columns:
                col_list = ", ".join(input_columns)
                return f"    SELECT {col_list} FROM {input_ref}"
            return f"    SELECT * FROM {input_ref}"

        order_parts = []
        for col in columns:
            if isinstance(col, dict):
                name = col.get("column", col.get("name", "id"))
                direction = col.get("direction", "ASC")
                order_parts.append(f"{name} {direction}")
            else:
                order_parts.append(str(col))

        input_columns = self._get_input_columns(tx)
        if input_columns:
            col_list = ", ".join(input_columns)
            return f"    SELECT {col_list} FROM {input_ref}\n    ORDER BY {', '.join(order_parts)}"
        return f"    SELECT * FROM {input_ref}\n    ORDER BY {', '.join(order_parts)}"

    def _gen_drop_cte(self, tx: TransformationNode, input_ref: str) -> str:
        """Generate drop columns CTE."""
        columns = tx.parameters.get("columns", [])
        if not columns:
            return f"    SELECT * FROM {input_ref}"

        drop_str = ", ".join(columns)
        return f"    SELECT * EXCLUDE ({drop_str}) FROM {input_ref}"

    def _gen_rename_cte(self, tx: TransformationNode, input_ref: str) -> str:
        """Generate rename column CTE."""
        old_name = tx.parameters.get("old_name", "")
        new_name = tx.parameters.get("new_name", "")
        if old_name and new_name:
            return f"    SELECT * RENAME ({old_name} AS {new_name}) FROM {input_ref}"
        return f"    SELECT * FROM {input_ref}"

    def _gen_union_cte(self, tx: TransformationNode) -> str:
        """Generate union CTE."""
        refs = [self._cte_names.get(inp, inp) for inp in tx.inputs]
        if len(refs) < 2:
            return f"    SELECT * FROM {refs[0] if refs else 'UNKNOWN'}"

        union_type = "UNION ALL" if tx.operation == "unionAll" else "UNION"
        parts = [f"    SELECT * FROM {ref}" for ref in refs]
        return f"\n    {union_type}\n".join(parts)

    def _gen_with_column_custom_cte(self, tx: TransformationNode, input_ref: str) -> str:
        """Generate withColumn_custom CTE for UDF calls.
        
        For UDFs, we generate a placeholder that can be replaced with actual
        Snowflake UDF calls. The column name comes from parameters.
        """
        col_name = tx.parameters.get("column_name", "udf_result")
        expr = tx.parameters.get("expression", "")
        contains_udf = tx.parameters.get("contains_udf", False)
        
        # Get input columns
        input_columns = self._get_input_columns(tx)
        
        if contains_udf:
            # Generate UDF placeholder - the UDF name can be extracted from expression
            # e.g., "obfuscated_udf(SprkF.col('customer_email'))" -> call to UDF
            # Use SELECT * to preserve ALL columns (including PII like customer_email)
            # so Snowflake masking policies can be applied at the final table
            return f"""    -- [withColumn_custom: {col_name}]
    -- TODO: Replace with Snowflake UDF call
    -- Original PySpark: {expr}
    SELECT * FROM {input_ref}"""
        else:
            # Non-UDF custom logic - try to convert expression
            sql_expr = spark_expr_to_sql(expr)
            if input_columns:
                col_list = ", ".join(input_columns)
                return f"    SELECT {col_list}, {sql_expr} AS {col_name} FROM {input_ref}"
            return f"    SELECT *, {sql_expr} AS {col_name} FROM {input_ref}"

    def _safe_name(self, name: str) -> str:
        """Make a name safe for SQL identifiers."""
        clean = name.replace("'", "").replace('"', "").replace("-", "_")
        clean = clean.replace("(", "").replace(")", "").replace(" ", "_")
        clean = clean.replace("/", "_").replace(".", "_")
        return clean.upper()

    def write_files(self, result: InliningResult, output_dir: str) -> list[str]:
        """
        Write SQL files - one per sink.

        Structure:
            output_dir/
            ├── 01_DAILY_SALES_SUMMARY.sql
            ├── 02_CUSTOMER_CLV.sql
            ├── 03_TOP_CATEGORIES.sql
            ├── 04_FOREIGN_TRANSACTIONS.sql
            ├── 05_UNMATCHED_TRANSACTIONS.sql
            └── deploy_all.sql
        """
        import os

        os.makedirs(output_dir, exist_ok=True)
        written_files = []
        deploy_order = []

        for i, pipeline in enumerate(result.pipelines, 1):
            filename = f"{i:02d}_{self._safe_name(pipeline.sink_name)}.sql"
            filepath = os.path.join(output_dir, filename)

            with open(filepath, "w") as f:
                f.write(pipeline.sql)
                f.write("\n")

            written_files.append(filepath)
            deploy_order.append(filename)

        # Generate deploy_all.sql
        deploy_path = os.path.join(output_dir, "deploy_all.sql")
        with open(deploy_path, "w") as f:
            f.write("-- ============================================\n")
            f.write("-- Deploy All Dynamic Tables (Sink-First)\n")
            f.write("-- Generated by Spark-DT-Analyzer\n")
            f.write(
                f"-- {len(result.pipelines)} pipelines from {result.original_tx_count} transformations\n"
            )
            f.write("-- ============================================\n\n")

            for i, filename in enumerate(deploy_order, 1):
                f.write(f"-- Step {i}\n")
                f.write(f"-- !source '{filename}';\n\n")

        written_files.append(deploy_path)
        return written_files


def inline_to_sinks(
    asg: ASG,
    *,
    target_lag: str = "1 day",
    warehouse: str = "COMPUTE_WH",
    use_dynamic_tables: bool = True,
) -> InliningResult:
    """
    Inline all transformations into CTEs per Sink.

    This is the main entry point for sink-first inlining.

    Args:
        asg: The Abstract Semantic Graph
        target_lag: TARGET_LAG for Dynamic Tables
        warehouse: Warehouse for Dynamic Table refresh
        use_dynamic_tables: Use Dynamic Tables (True) or Views (False)

    Returns:
        InliningResult with one SinkPipeline per sink
    """
    inliner = SinkInliner(
        target_lag=target_lag,
        warehouse=warehouse,
        use_dynamic_tables=use_dynamic_tables,
    )
    return inliner.inline(asg)


# Legacy alias for backward compatibility
SinkFirstInliner = SinkInliner
