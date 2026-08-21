"""
Parser module for extracting ASG from PySpark code.

Contains:
- spark_ast: Main AST visitor for Spark operations
- symbol_table: Variable tracking
- lineage_linker: Column lineage propagation
- astroid_inference: Type inference using astroid
"""

from asg_pyspark.parser.spark_ast import (
    OperationCount,
    SparkASTParser,
)

# Alias for convenience
SparkASTVisitor = SparkASTParser
from asg_pyspark.parser.astroid_inference import AstroidInferenceEngine
from asg_pyspark.parser.lineage_linker import LineageLinker
from warp_core.symbol_table import SymbolTable

__all__ = [
    "SparkASTVisitor",
    "OperationCount",
    "SymbolTable",
    "LineageLinker",
    "AstroidInferenceEngine",
]
