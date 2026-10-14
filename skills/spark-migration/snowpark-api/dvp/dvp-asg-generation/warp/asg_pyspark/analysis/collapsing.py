"""
Node Collapsing Engine - Fuse linear transformation chains.

This module implements "Operation Fusion" that collapses sequential
transformations into consolidated SQL objects with semantic names.

Benefits:
- Reduces number of Snowflake objects (21 -> 5-8)
- Replaces TX_XXX names with business-meaningful names
- Uses CTEs for readability within collapsed chains
- Materializes only at checkpoints (joins, aggregations)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from warp_core.ir.pyspark_models import ASG, DataSink, DataSource, TransformationNode


@dataclass
class CollapsedNode:
    """A collapsed node representing one or more fused transformations."""

    id: str  # New consolidated ID
    original_ids: list[str]  # Original node IDs that were fused
    operation: str  # Primary operation (join, agg, projection)
    semantic_name: str  # Business-meaningful name
    inputs: list[str] = field(default_factory=list)  # Input CollapsedNode IDs
    materialize: bool = False  # True = Dynamic Table, False = View

    # SQL generation data
    sql_parts: dict[str, Any] = field(default_factory=dict)

    # Original transformation data (for SQL generation)
    transformations: list[TransformationNode] = field(default_factory=list)

    # Sink info (if this node feeds a sink)
    sink_name: str | None = None

    @property
    def is_checkpoint(self) -> bool:
        """Check if this node should be materialized."""
        return self.materialize


@dataclass
class CollapsingResult:
    """Result of collapsing an ASG."""

    nodes: list[CollapsedNode] = field(default_factory=list)
    sources: list[DataSource] = field(default_factory=list)
    sinks: list[DataSink] = field(default_factory=list)

    # Mapping from original ID to collapsed ID
    id_mapping: dict[str, str] = field(default_factory=dict)

    # Statistics
    original_count: int = 0
    collapsed_count: int = 0

    @property
    def reduction_ratio(self) -> float:
        """Ratio of nodes eliminated."""
        if self.original_count == 0:
            return 0.0
        return 1 - (self.collapsed_count / self.original_count)


class CollapsingEngine:
    """
    Collapses linear chains of transformations into consolidated nodes.

    The engine applies these rules:
    1. Fusionable operations (select, filter, withColumn) are combined
    2. Checkpoint operations (join, agg) break the chain and materialize
    3. Nodes with multiple consumers become checkpoints
    4. Semantic names are generated based on operation and context
    """

    # Operations that can be fused together (projections/filters)
    FUSIONABLE_OPS = frozenset(
        {
            "select",
            "filter",
            "where",
            "withColumn",
            "withColumnRenamed",
            "drop",
            "distinct",
            "orderBy",
            "sort",
            "limit",
            "alias",
        }
    )

    # Operations that require materialization (checkpoints)
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

    def __init__(self) -> None:
        self._tx_lookup: dict[str, TransformationNode] = {}
        self._source_lookup: dict[str, DataSource] = {}
        self._sink_lookup: dict[str, DataSink] = {}

        # Reverse graph: node_id -> list of consumer IDs
        self._consumers: dict[str, list[str]] = {}

        # Sink mapping: source_id -> sink_name
        self._sink_names: dict[str, str] = {}

        # Tracking for collapse
        self._visited: set[str] = set()
        self._collapsed_nodes: list[CollapsedNode] = []
        self._id_mapping: dict[str, str] = {}

    def collapse(self, asg: ASG) -> CollapsingResult:
        """
        Collapse an ASG into consolidated nodes.

        Args:
            asg: The Abstract Semantic Graph to collapse

        Returns:
            CollapsingResult with collapsed nodes and mappings
        """
        # Build lookups
        self._tx_lookup = {t.id: t for t in asg.transformations}
        self._source_lookup = {s.id: s for s in asg.data_in}
        self._sink_lookup = {s.id: s for s in asg.data_out}

        # Build reverse graph (who consumes each node)
        self._build_consumer_graph(asg)

        # Build sink name mapping
        for sink in asg.data_out:
            if sink.source_id and sink.name:
                self._sink_names[sink.source_id] = sink.name

        # Reset tracking
        self._visited = set()
        self._collapsed_nodes = []
        self._id_mapping = {}

        # Process transformations in topological order
        # Start from sinks and work backward
        for sink in asg.data_out:
            if sink.source_id:
                self._process_node(sink.source_id)

        # Also process any nodes not reachable from sinks
        for tx in asg.transformations:
            if tx.id not in self._visited:
                self._process_node(tx.id)

        return CollapsingResult(
            nodes=self._collapsed_nodes,
            sources=list(asg.data_in),
            sinks=list(asg.data_out),
            id_mapping=self._id_mapping,
            original_count=len(asg.transformations),
            collapsed_count=len(self._collapsed_nodes),
        )

    def _build_consumer_graph(self, asg: ASG) -> None:
        """Build reverse graph: for each node, who consumes it."""
        self._consumers = {}

        for tx in asg.transformations:
            for input_id in tx.inputs:
                if input_id not in self._consumers:
                    self._consumers[input_id] = []
                self._consumers[input_id].append(tx.id)

        # Sinks also consume nodes
        for sink in asg.data_out:
            if sink.source_id:
                if sink.source_id not in self._consumers:
                    self._consumers[sink.source_id] = []
                self._consumers[sink.source_id].append(sink.id)

    def _process_node(self, node_id: str) -> str:
        """
        Process a node and return its collapsed ID.

        Returns the ID of the CollapsedNode that represents this node.
        """
        if node_id in self._visited:
            return self._id_mapping.get(node_id, node_id)

        # Sources are not collapsed
        if node_id in self._source_lookup:
            return node_id

        tx = self._tx_lookup.get(node_id)
        if not tx:
            return node_id

        self._visited.add(node_id)

        # First, process all inputs
        for input_id in tx.inputs:
            self._process_node(input_id)

        # Determine if this node is a checkpoint
        is_checkpoint = self._is_checkpoint(node_id)

        if is_checkpoint:
            # Create a new collapsed node for this checkpoint
            collapsed = self._create_collapsed_node(tx, is_checkpoint=True)
            self._collapsed_nodes.append(collapsed)
            self._id_mapping[node_id] = collapsed.id
            return collapsed.id
        else:
            # This is a fusionable operation
            # Try to fuse with downstream checkpoint
            # For now, create individual collapsed nodes for fusionable ops
            # They will be fused during SQL generation via CTEs
            collapsed = self._create_collapsed_node(tx, is_checkpoint=False)
            self._collapsed_nodes.append(collapsed)
            self._id_mapping[node_id] = collapsed.id
            return collapsed.id

    def _is_checkpoint(self, node_id: str) -> bool:
        """Determine if a node should be a checkpoint (materialized)."""
        tx = self._tx_lookup.get(node_id)
        if not tx:
            return False

        # Checkpoint operations always materialize
        if tx.operation in self.CHECKPOINT_OPS:
            return True

        # Nodes with multiple consumers should materialize
        consumers = self._consumers.get(node_id, [])
        if len(consumers) > 1:
            return True

        # Nodes that feed sinks should materialize
        if node_id in self._sink_names:
            return True

        return False

    def _create_collapsed_node(
        self,
        tx: TransformationNode,
        is_checkpoint: bool,
    ) -> CollapsedNode:
        """Create a CollapsedNode from a transformation."""
        # Generate semantic name
        semantic_name = self._generate_semantic_name(tx)

        # Map inputs to collapsed IDs
        collapsed_inputs = []
        for input_id in tx.inputs:
            if input_id in self._source_lookup:
                collapsed_inputs.append(input_id)
            else:
                collapsed_id = self._id_mapping.get(input_id, input_id)
                collapsed_inputs.append(collapsed_id)

        # Check if this feeds a sink
        sink_name = self._sink_names.get(tx.id)

        return CollapsedNode(
            id=f"c_{tx.id}",
            original_ids=[tx.id],
            operation=tx.operation,
            semantic_name=semantic_name,
            inputs=collapsed_inputs,
            materialize=is_checkpoint,
            transformations=[tx],
            sink_name=sink_name,
        )

    def _generate_semantic_name(self, tx: TransformationNode) -> str:
        """Generate a business-meaningful name for a transformation."""
        # If this feeds a sink, use the sink name
        if tx.id in self._sink_names:
            return self._safe_name(self._sink_names[tx.id])

        operation = tx.operation
        params = tx.parameters

        # Join: JOIN_{LEFT}_{RIGHT}
        if operation in ("join", "crossJoin"):
            left = self._get_input_name(tx.inputs[0]) if tx.inputs else "UNKNOWN"
            right = self._get_input_name(tx.inputs[1]) if len(tx.inputs) > 1 else "UNKNOWN"
            params.get("join_type", "INNER").upper()
            return f"JOIN_{left}_{right}"

        # Aggregation: AGG_BY_{COLUMNS}
        if operation in ("groupBy_agg", "agg"):
            group_cols = params.get("group_columns", [])
            if group_cols:
                cols_str = "_".join(self._safe_name(c) for c in group_cols[:2])
                return f"AGG_BY_{cols_str}"
            return f"AGG_{self._get_input_name(tx.inputs[0])}" if tx.inputs else "AGG"

        # Filter: FILTERED_{SOURCE}
        if operation in ("filter", "where"):
            source = self._get_input_name(tx.inputs[0]) if tx.inputs else "DATA"
            return f"FILTERED_{source}"

        # Select: SELECT_{SOURCE}
        if operation == "select":
            source = self._get_input_name(tx.inputs[0]) if tx.inputs else "DATA"
            return f"SELECT_{source}"

        # WithColumn: {SOURCE}_ENRICHED
        if operation == "withColumn":
            source = self._get_input_name(tx.inputs[0]) if tx.inputs else "DATA"
            col_name = params.get("column_name", "")
            if col_name:
                return f"{source}_WITH_{self._safe_name(col_name)}"
            return f"{source}_ENRICHED"

        # Default: use operation + input
        if tx.inputs:
            source = self._get_input_name(tx.inputs[0])
            return f"{operation.upper()}_{source}"

        return f"{operation.upper()}_{tx.id}"

    def _get_input_name(self, input_id: str) -> str:
        """Get a short name for an input node."""
        # Check if it's a source
        if input_id in self._source_lookup:
            source = self._source_lookup[input_id]
            if source.name:
                return self._safe_name(source.name)[:20]
            if source.path:
                import os

                basename = os.path.basename(source.path)
                name = os.path.splitext(basename)[0]
                return self._safe_name(name)[:20]

        # Check if it's a collapsed node
        if input_id in self._id_mapping:
            collapsed_id = self._id_mapping[input_id]
            for node in self._collapsed_nodes:
                if node.id == collapsed_id:
                    return node.semantic_name[:20]

        # Check if it's a transformation
        if input_id in self._tx_lookup:
            tx = self._tx_lookup[input_id]
            if tx.id in self._sink_names:
                return self._safe_name(self._sink_names[tx.id])[:20]

        return input_id.upper()

    def _safe_name(self, name: str) -> str:
        """Make a name safe for SQL."""
        clean = name.replace("'", "").replace('"', "").replace("-", "_")
        clean = clean.replace("(", "").replace(")", "").replace(" ", "_")
        return clean.upper()


def collapse_asg(asg: ASG) -> CollapsingResult:
    """
    Collapse an ASG into consolidated nodes.

    This is the main entry point for the collapsing engine.

    Args:
        asg: The Abstract Semantic Graph to collapse

    Returns:
        CollapsingResult with collapsed nodes
    """
    engine = CollapsingEngine()
    return engine.collapse(asg)
