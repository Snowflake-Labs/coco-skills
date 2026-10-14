"""
Graph Slicer - Backward slicing from data sinks.

This module implements "The Pruning" algorithm that identifies only the
nodes in an ASG that contribute to the final data outputs. This is essential
for generating efficient Snowflake SQL without dead code.

Algorithm:
1. Start from all DataSinks (data_out)
2. Walk backward through source_id and inputs
3. Mark all visited nodes as "active"
4. Return only the active subgraph

Benefits:
- Eliminates dead variables and debugging code
- Reduces SQL complexity and Snowflake costs
- Produces clean, auditable lineage
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from warp_core.ir.pyspark_models import ASG, DataSource, TransformationNode


@dataclass
class SliceResult:
    """Result of slicing an ASG."""

    active_nodes: set[str] = field(default_factory=set)
    excluded_nodes: set[str] = field(default_factory=set)

    # Breakdown by type
    active_sources: set[str] = field(default_factory=set)
    active_transformations: set[str] = field(default_factory=set)
    active_sinks: set[str] = field(default_factory=set)

    @property
    def total_active(self) -> int:
        """Total number of active nodes."""
        return len(self.active_nodes)

    @property
    def total_excluded(self) -> int:
        """Total number of excluded nodes."""
        return len(self.excluded_nodes)

    @property
    def reduction_ratio(self) -> float:
        """Ratio of excluded to total nodes (0.0 to 1.0)."""
        total = self.total_active + self.total_excluded
        if total == 0:
            return 0.0
        return self.total_excluded / total


class GraphSlicer:
    """
    Performs backward slicing on an ASG from data sinks.

    The slicer answers: "Which nodes are ancestors of the final outputs?"
    Any node that doesn't contribute to a DataSink is considered dead code.
    """

    def __init__(self) -> None:
        self._node_lookup: dict[str, TransformationNode] = {}
        self._source_lookup: dict[str, DataSource] = {}

    def slice(self, asg: ASG) -> SliceResult:
        """
        Perform backward slicing from all data sinks.

        Args:
            asg: The Abstract Semantic Graph to slice

        Returns:
            SliceResult with active and excluded node sets
        """
        # Build lookups for fast access
        self._build_lookups(asg)

        # Initialize result
        result = SliceResult()

        # All sinks are active by definition
        for sink in asg.data_out:
            result.active_nodes.add(sink.id)
            result.active_sinks.add(sink.id)

        # BFS backward from sink sources
        queue: deque[str] = deque()
        for sink in asg.data_out:
            if sink.source_id:
                queue.append(sink.source_id)

        # Walk backward through the graph
        while queue:
            current_id = queue.popleft()

            # Skip if already processed
            if current_id in result.active_nodes:
                continue

            result.active_nodes.add(current_id)

            # Check if it's a transformation
            if current_id in self._node_lookup:
                tx = self._node_lookup[current_id]
                result.active_transformations.add(current_id)

                # Add all inputs to the queue
                for input_id in tx.inputs:
                    if input_id not in result.active_nodes:
                        queue.append(input_id)

            # Check if it's a data source
            elif current_id in self._source_lookup:
                result.active_sources.add(current_id)

        # Calculate excluded nodes
        all_nodes = self._get_all_node_ids(asg)
        result.excluded_nodes = all_nodes - result.active_nodes

        return result

    def get_pruned_asg(self, asg: ASG) -> ASG:
        """
        Return a new ASG containing only active nodes.

        Args:
            asg: The original ASG

        Returns:
            A new ASG with only the nodes that contribute to outputs
        """
        from warp_core.ir.pyspark_models import ASG

        slice_result = self.slice(asg)

        return ASG(
            source_file=asg.extraction_metadata.source_file,
            source_files=asg.source_files,  # Keep all source files with imports
            data_in=[s for s in asg.data_in if s.id in slice_result.active_sources],
            data_out=asg.data_out,  # Keep all outputs
            transformations=[
                t for t in asg.transformations if t.id in slice_result.active_transformations
            ],
            functions=asg.functions,  # Keep all functions
        )

    def get_execution_order(self, asg: ASG) -> list[str]:
        """
        Return nodes in topological order for execution.

        This is the order in which SQL statements should be generated:
        sources first, then transformations in dependency order, then sinks.

        Args:
            asg: The ASG (preferably already pruned)

        Returns:
            List of node IDs in execution order
        """
        slice_result = self.slice(asg)

        # Build dependency graph
        dependencies: dict[str, set[str]] = {}
        for tx in asg.transformations:
            if tx.id in slice_result.active_transformations:
                dependencies[tx.id] = set(tx.inputs)

        # Topological sort using Kahn's algorithm
        in_degree: dict[str, int] = {node: 0 for node in dependencies}
        for node, deps in dependencies.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[node] = in_degree.get(node, 0)

        # Calculate in-degrees
        for node, deps in dependencies.items():
            for dep in deps:
                if dep in dependencies:
                    in_degree[node] += 1

        # Start with sources (no dependencies)
        result: list[str] = []

        # Add sources first
        result.extend(sorted(slice_result.active_sources))

        # Process transformations in order
        queue = deque([n for n, d in in_degree.items() if d == 0])
        while queue:
            node = queue.popleft()
            result.append(node)

            for other, deps in dependencies.items():
                if node in deps:
                    in_degree[other] -= 1
                    if in_degree[other] == 0:
                        queue.append(other)

        # Add remaining transformations (handles cycles gracefully)
        for tx_id in slice_result.active_transformations:
            if tx_id not in result:
                result.append(tx_id)

        # Add sinks last
        result.extend(sorted(slice_result.active_sinks))

        return result

    def _build_lookups(self, asg: ASG) -> None:
        """Build fast lookup dictionaries."""
        self._node_lookup = {tx.id: tx for tx in asg.transformations}
        self._source_lookup = {s.id: s for s in asg.data_in}

    def _get_all_node_ids(self, asg: ASG) -> set[str]:
        """Get all node IDs in the ASG."""
        ids: set[str] = set()
        ids.update(s.id for s in asg.data_in)
        ids.update(t.id for t in asg.transformations)
        ids.update(s.id for s in asg.data_out)
        return ids


# =============================================================================
# Public API
# =============================================================================


def slice_asg(asg: ASG) -> SliceResult:
    """
    Perform backward slicing on an ASG.

    This is the main entry point for the slicer.

    Args:
        asg: The Abstract Semantic Graph to slice

    Returns:
        SliceResult with active and excluded node information
    """
    slicer = GraphSlicer()
    return slicer.slice(asg)


def prune_asg(asg: ASG) -> ASG:
    """
    Return a pruned ASG containing only active nodes.

    Args:
        asg: The original ASG

    Returns:
        A new ASG with dead code removed
    """
    slicer = GraphSlicer()
    return slicer.get_pruned_asg(asg)


def get_execution_order(asg: ASG) -> list[str]:
    """
    Get the execution order for an ASG.

    Args:
        asg: The ASG

    Returns:
        List of node IDs in the order they should be executed
    """
    slicer = GraphSlicer()
    return slicer.get_execution_order(asg)
