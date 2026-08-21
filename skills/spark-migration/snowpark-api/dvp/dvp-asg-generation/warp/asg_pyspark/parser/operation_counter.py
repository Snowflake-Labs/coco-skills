"""
Operation Counter - Count Spark DataFrame operations in Python code.

This module provides utilities for counting DataFrame operations without
building a full ASG. Useful for quick analysis and metrics.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OperationCount:
    """Count of detected DataFrame operations."""

    # Relational operations
    select: int = 0
    filter: int = 0
    where: int = 0
    join: int = 0
    groupBy: int = 0
    agg: int = 0
    orderBy: int = 0
    distinct: int = 0
    union: int = 0

    # Column operations
    withColumn: int = 0
    withColumnRenamed: int = 0
    drop: int = 0
    alias: int = 0

    # I/O operations
    read: int = 0
    write: int = 0
    saveAsTable: int = 0

    # Window operations
    over: int = 0

    # Other
    other: list[str] = field(default_factory=list)

    def total(self) -> int:
        """Total number of known operations."""
        return (
            self.select
            + self.filter
            + self.where
            + self.join
            + self.groupBy
            + self.agg
            + self.orderBy
            + self.distinct
            + self.union
            + self.withColumn
            + self.withColumnRenamed
            + self.drop
            + self.alias
            + self.read
            + self.write
            + self.saveAsTable
            + self.over
        )

    def summary(self) -> dict[str, int]:
        """Return non-zero counts as a dictionary."""
        result = {}
        for name in [
            "select",
            "filter",
            "where",
            "join",
            "groupBy",
            "agg",
            "orderBy",
            "distinct",
            "union",
            "withColumn",
            "withColumnRenamed",
            "drop",
            "alias",
            "read",
            "write",
            "saveAsTable",
            "over",
        ]:
            count = getattr(self, name)
            if count > 0:
                result[name] = count
        return result


class SparkOperationCounter(ast.NodeVisitor):
    """
    AST Visitor that counts Spark DataFrame operations.

    Uses Structural Pattern Matching for clean, readable detection.
    """

    # Known DataFrame method names
    KNOWN_OPERATIONS = {
        "select",
        "filter",
        "where",
        "join",
        "groupBy",
        "agg",
        "orderBy",
        "sort",
        "distinct",
        "dropDuplicates",
        "union",
        "unionAll",
        "unionByName",
        "intersect",
        "except",
        "subtract",
        "limit",
        "drop",
        "withColumn",
        "withColumnRenamed",
        "alias",
        "read",
        "write",
        "save",
        "saveAsTable",
        "over",
        "cache",
        "persist",
        "unpersist",
        "repartition",
        "coalesce",
        "crossJoin",
        "rollup",
        "cube",
        "pivot",
        "unpivot",
    }

    def __init__(self) -> None:
        self.counts = OperationCount()
        self.all_calls: list[tuple[int, str]] = []  # (line_number, method_name)

    def visit_Call(self, node: ast.Call) -> None:
        """Visit a function call and check if it's a DataFrame operation."""

        match node.func:
            # Pattern: df.method_name(...)
            case ast.Attribute(attr=method_name) if method_name in self.KNOWN_OPERATIONS:
                self._count_operation(method_name, node.lineno)

            # Pattern: spark.read.table(...) or spark.read.csv(...)
            case ast.Attribute(value=ast.Attribute(attr="read"), attr=read_method):
                self._count_operation("read", node.lineno)
                self.all_calls.append((node.lineno, f"read.{read_method}"))

            # Pattern: df.write.mode(...).parquet/csv/json(...)
            # Note: These aren't in KNOWN_OPERATIONS, so need explicit pattern
            case ast.Attribute(value=ast.Call(), attr="parquet" | "csv" | "json"):
                self._count_operation("write", node.lineno)

        # Continue visiting child nodes
        self.generic_visit(node)

    def _count_operation(self, method_name: str, line_number: int) -> None:
        """Increment the count for a detected operation."""

        # Map aliases to canonical names
        method_map = {
            "sort": "orderBy",
            "dropDuplicates": "distinct",
            "unionAll": "union",
            "unionByName": "union",
        }
        canonical = method_map.get(method_name, method_name)

        # Increment count if we have a field for it
        if hasattr(self.counts, canonical):
            current = getattr(self.counts, canonical)
            setattr(self.counts, canonical, current + 1)
        else:
            self.counts.other.append(method_name)

        self.all_calls.append((line_number, method_name))


def count_operations(source_code: str) -> OperationCount:
    """
    Parse Python source code and count Spark DataFrame operations.

    Args:
        source_code: Python source code as a string

    Returns:
        OperationCount with counts of each operation type
    """
    tree = ast.parse(source_code)
    counter = SparkOperationCounter()
    counter.visit(tree)
    return counter.counts


def count_operations_in_file(file_path: str | Path) -> OperationCount:
    """
    Parse a Python file and count Spark DataFrame operations.

    Args:
        file_path: Path to the Python file

    Returns:
        OperationCount with counts of each operation type
    """
    path = Path(file_path)
    source_code = path.read_text()
    return count_operations(source_code)


def analyze_file(file_path: str | Path) -> dict[str, Any]:
    """
    Analyze a Spark Python file and return detailed information.

    Args:
        file_path: Path to the Python file

    Returns:
        Dictionary with analysis results
    """
    path = Path(file_path)
    source_code = path.read_text()
    tree = ast.parse(source_code)

    counter = SparkOperationCounter()
    counter.visit(tree)

    return {
        "file": str(path),
        "total_operations": counter.counts.total(),
        "operations": counter.counts.summary(),
        "calls_by_line": counter.all_calls,
        "other_operations": counter.counts.other,
    }
