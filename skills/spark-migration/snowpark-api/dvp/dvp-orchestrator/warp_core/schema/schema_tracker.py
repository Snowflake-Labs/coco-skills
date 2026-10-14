"""
Schema Tracker - Infer and propagate column schemas through ASG.

This module provides proactive schema inference from code analysis,
without requiring an external catalog. It works in three phases:

1. Discovery Phase (ColumnCollector): Collect all column mentions
2. Type Inference (TypeInferencer): Infer types from usage patterns
3. Propagation (SchemaPropagator): Build virtual catalog and propagate
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from warp_core.ir.pyspark_models import (
    InferenceConfidence,
    InferenceSource,
    InferredColumn,
    InputColumn,
)
from warp_core.pandas_functions import (
    DATE_FUNCS as PD_DATE_FUNCS,
    MATH_FUNCS as PD_MATH_FUNCS,
    STRING_FUNCS as PD_STRING_FUNCS,
)
from warp_core.spark_functions import (
    DATE_INPUT_FUNCS,
    NUMERIC_INPUT_FUNCS,
    STRING_INPUT_FUNCS,
    TIMESTAMP_INPUT_FUNCS,
)

if TYPE_CHECKING:
    from warp_core.ir.pyspark_models import ASG, DataSource, TransformationNode


# =============================================================================
# Column Reference
# =============================================================================


@dataclass
class ColumnReference:
    """A reference to a column found in the code."""

    name: str
    node_id: str  # Which node references this column
    source: InferenceSource
    context: str | None = None  # The code context where found
    line: int | None = None


# =============================================================================
# Column Collector (Discovery Phase)
# =============================================================================


class ColumnCollector:
    """
    Collects all column references from ASG transformations.

    This is the "Discovery Phase" that builds a Virtual Catalog
    by observing how columns are used in the code.
    """

    # Patterns to extract column names from code
    COL_PATTERN = re.compile(r"col\(['\"]([^'\"]+)['\"]\)")
    F_COL_PATTERN = re.compile(r"F\.col\(['\"]([^'\"]+)['\"]\)")
    STRING_ARG_PATTERN = re.compile(r"['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]")

    def __init__(self) -> None:
        self.references: list[ColumnReference] = []
        self._seen: set[tuple[str, str]] = set()  # (column_name, node_id)

    def collect_from_asg(self, asg: ASG) -> dict[str, list[ColumnReference]]:
        """
        Collect all column references from an ASG.

        Returns: Mapping of node_id -> list of column references
        """
        self.references = []
        self._seen = set()

        # Collect from data_in nodes (table/file names give hints)
        for data_in in asg.data_in:
            self._collect_from_data_in(data_in)

        # Collect from transformations
        for tx in asg.transformations:
            self._collect_from_transformation(tx)

        # Group by node_id
        result: dict[str, list[ColumnReference]] = {}
        for ref in self.references:
            if ref.node_id not in result:
                result[ref.node_id] = []
            result[ref.node_id].append(ref)

        return result

    def _collect_from_data_in(self, data_in: DataSource) -> None:
        """Collect column hints from data source."""
        # Currently we can't infer columns from just the source name
        # This will be enhanced with catalog integration
        pass

    def _collect_from_transformation(self, tx: TransformationNode) -> None:
        """Collect column references from a transformation."""
        # Extract from parameters
        self._collect_from_parameters(tx)

        # Extract from logic string
        if tx.logic:
            line = tx.location.start_line if tx.location else None
            self._collect_from_logic(tx.id, tx.logic, line)

    def _collect_from_parameters(self, tx: TransformationNode) -> None:
        """Extract columns from transformation parameters."""
        params = tx.parameters
        operation = tx.operation

        # groupBy columns
        if "group_columns" in params:
            for col in params["group_columns"]:
                col_name = self._extract_column_name(col)
                if col_name:
                    self._add_reference(col_name, tx.id, InferenceSource.GROUP_BY)

        # orderBy columns
        if "columns" in params and operation in ("orderBy", "sort"):
            for col_info in params["columns"]:
                if isinstance(col_info, dict) and "column" in col_info:
                    self._add_reference(col_info["column"], tx.id, InferenceSource.ORDER_BY)
                elif isinstance(col_info, str):
                    col_name = self._extract_column_name(col_info)
                    if col_name:
                        self._add_reference(col_name, tx.id, InferenceSource.ORDER_BY)

        # join condition
        if "join_condition" in params:
            cond = params["join_condition"]
            if isinstance(cond, str):
                self._add_reference(cond, tx.id, InferenceSource.JOIN_KEY)

        # withColumn - new column
        if "column_name" in params:
            self._add_reference(
                params["column_name"], tx.id, InferenceSource.EXPLICIT, is_created=True
            )

        # column_aliases from groupBy_agg
        if "column_aliases" in params:
            for alias in params["column_aliases"]:
                self._add_reference(alias, tx.id, InferenceSource.EXPLICIT)

    # Pattern for select('col1', 'col2') - string args in select
    SELECT_ARGS_PATTERN = re.compile(r"\.select\(([^)]+)\)")

    def _collect_from_logic(self, node_id: str, logic: str, line: int | None) -> None:
        """Extract column names from code logic string."""
        # col("column_name") pattern
        for match in self.COL_PATTERN.finditer(logic):
            self._add_reference(
                match.group(1), node_id, InferenceSource.FUNCTION_ARG, context=logic, line=line
            )

        # F.col("column_name") pattern
        for match in self.F_COL_PATTERN.finditer(logic):
            self._add_reference(
                match.group(1), node_id, InferenceSource.FUNCTION_ARG, context=logic, line=line
            )

        # .select('col1', 'col2') pattern - extract string arguments
        for match in self.SELECT_ARGS_PATTERN.finditer(logic):
            args_str = match.group(1)
            # Extract all quoted strings from the args
            for col_match in self.STRING_ARG_PATTERN.finditer(args_str):
                col_name = col_match.group(1)
                if col_name and re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", col_name):
                    self._add_reference(
                        col_name, node_id, InferenceSource.SELECT, context=logic, line=line
                    )

    def _add_reference(
        self,
        name: str,
        node_id: str,
        source: InferenceSource,
        context: str | None = None,
        line: int | None = None,
        is_created: bool = False,
    ) -> None:
        """Add a column reference, avoiding duplicates per node."""
        # Clean up column name (remove quotes, col() wrapper, etc.)
        clean_name = self._clean_column_name(name)
        if not clean_name:
            return

        key = (clean_name, node_id)
        if key in self._seen:
            return

        self._seen.add(key)
        self.references.append(
            ColumnReference(
                name=clean_name,
                node_id=node_id,
                source=source,
                context=context,
                line=line,
            )
        )

    def _extract_column_name(self, expr: str) -> str | None:
        """Extract column name from an expression like 'category' or col('x')."""
        expr = expr.strip()

        # Already a simple string (quoted or unquoted)
        if expr.startswith("'") and expr.endswith("'"):
            return expr[1:-1]
        if expr.startswith('"') and expr.endswith('"'):
            return expr[1:-1]

        # col("name") pattern
        match = self.COL_PATTERN.search(expr)
        if match:
            return match.group(1)

        # F.col("name") pattern
        match = self.F_COL_PATTERN.search(expr)
        if match:
            return match.group(1)

        # Simple identifier
        if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", expr):
            return expr

        return None

    def _clean_column_name(self, name: str) -> str | None:
        """Clean and validate a column name."""
        if not name:
            return None

        name = name.strip().strip("'\"")

        # Skip if it looks like a full expression, not a column name
        if "(" in name or ")" in name:
            return None
        if " " in name and "==" not in name:
            return None

        # Extract from comparison if present
        if "==" in name:
            parts = name.split("==")
            name = parts[0].strip().strip("'\"")

        # Validate it's a valid identifier
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
            return None

        return name
    @staticmethod
    def _extract_aliases_from_expression(expr: str) -> list[str]:
        """Extract .alias("name") references from an expression string."""
        return re.findall(r'\.alias\([\x27"]([^\x27"]+)[\x27"]\)', expr)


# =============================================================================
# Type Inferencer
# =============================================================================


class TypeInferencer:
    """
    Infers column types from usage patterns.

    Analyzes how columns are used in transformations to deduce
    their likely data types.
    """

    NUMERIC_FUNCTIONS = NUMERIC_INPUT_FUNCS | PD_MATH_FUNCS
    STRING_FUNCTIONS = STRING_INPUT_FUNCS | PD_STRING_FUNCS
    DATE_FUNCTIONS = DATE_INPUT_FUNCS | PD_DATE_FUNCS
    TIMESTAMP_FUNCTIONS = TIMESTAMP_INPUT_FUNCS

    # Column name patterns that suggest DATE type
    DATE_NAME_PATTERNS = frozenset(
        {
            "_date",
            "date_",
            "_dt",
            "dt_",
        }
    )
    DATE_NAME_EXACT = frozenset(
        {
            "date",
            "dt",
            "created_at",
            "updated_at",
            "deleted_at",
        }
    )

    # Column name patterns that suggest TIMESTAMP type
    TIMESTAMP_NAME_PATTERNS = frozenset(
        {
            "_timestamp",
            "timestamp_",
            "_ts",
            "ts_",
            "_time",
            "time_",
        }
    )
    TIMESTAMP_NAME_EXACT = frozenset(
        {
            "timestamp",
            "ts",
            "datetime",
            "created_time",
            "updated_time",
        }
    )

    # Patterns for type inference from comparisons
    NUMERIC_COMPARISON = re.compile(r"col\(['\"][^'\"]+['\"]\)\s*[<>=!]+\s*\d+")
    STRING_COMPARISON = re.compile(r"col\(['\"][^'\"]+['\"]\)\s*==\s*['\"]")

    # Pattern for cast expressions
    CAST_PATTERN = re.compile(r"\.cast\(['\"]([^'\"]+)['\"]\)")

    # Spark type to logical type mapping
    SPARK_TYPE_MAP: dict[str, str] = {
        "string": "L_TEXT", "str": "L_TEXT",
        "int": "L_INT", "integer": "L_INT", "long": "L_INT", "bigint": "L_INT",
        "short": "L_INT", "smallint": "L_INT", "tinyint": "L_INT", "byte": "L_INT",
        "double": "L_DECIMAL", "float": "L_DECIMAL", "decimal": "L_DECIMAL", "numeric": "L_DECIMAL",
        "boolean": "L_BOOLEAN", "bool": "L_BOOLEAN",
        "date": "L_DATE", "timestamp": "L_DATETIME", "datetime": "L_DATETIME",
        "binary": "L_BINARY",
    }
    
    # Pattern for cast expressions: .cast('type') or .cast("type")
    CAST_PATTERN = re.compile(r"\.cast\(['\"]([^'\"]+)['\"]\)")
    
    # Spark type to logical type mapping
    SPARK_TYPE_MAP: dict[str, str] = {
        # String types
        "string": "L_TEXT", "str": "L_TEXT",
        # Integer types
        "int": "L_INT", "integer": "L_INT", "long": "L_INT", "bigint": "L_INT",
        "short": "L_INT", "smallint": "L_INT", "tinyint": "L_INT", "byte": "L_INT",
        # Decimal/Float types
        "double": "L_DECIMAL", "float": "L_DECIMAL", "decimal": "L_DECIMAL", "numeric": "L_DECIMAL",
        # Boolean
        "boolean": "L_BOOLEAN", "bool": "L_BOOLEAN",
        # Date/Time types
        "date": "L_DATE", "timestamp": "L_DATETIME", "datetime": "L_DATETIME",
        # Binary
        "binary": "L_BINARY",
    }

    def infer_type(
        self, column_name: str, context: str | None, source: InferenceSource
    ) -> tuple[str, InferenceConfidence]:
        """
        Infer type and confidence for a column based on context.

        Returns: (inferred_type_string, confidence)
        """
        # Explicit creation - check for cast expressions and expression types
        if source == InferenceSource.EXPLICIT:
            if context:
                # Priority 1: Check for explicit .cast('type')
                cast_type = self._infer_from_cast(context)
                if cast_type != "UNKNOWN":
                    return cast_type, InferenceConfidence.HIGH
                
                # Priority 2: Check expression type via AST analysis
                expr_type = self._infer_from_expression(context)
                if expr_type != "UNKNOWN":
                    return expr_type, InferenceConfidence.HIGH
            return "UNKNOWN", InferenceConfidence.HIGH

        # Catalog source is always high confidence
        if source == InferenceSource.CATALOG:
            return "UNKNOWN", InferenceConfidence.HIGH

        # Check for function usage first (highest signal)
        if context:
            inferred = self._infer_from_functions(column_name, context)
            if inferred != "UNKNOWN":
                return inferred, InferenceConfidence.MEDIUM

            # Check for comparison patterns
            inferred = self._infer_from_comparisons(column_name, context)
            if inferred != "UNKNOWN":
                return inferred, InferenceConfidence.MEDIUM

        # Check column name patterns (lower confidence than function usage)
        inferred = self._infer_from_column_name(column_name)
        if inferred != "UNKNOWN":
            return inferred, InferenceConfidence.LOW

        return "UNKNOWN", InferenceConfidence.LOW

    def _infer_from_column_name(self, column_name: str) -> str:
        """Infer type from column naming conventions."""
        name_lower = column_name.lower()

        # Check exact matches for timestamp (more specific)
        if name_lower in self.TIMESTAMP_NAME_EXACT:
            return "TIMESTAMP"

        # Check patterns for timestamp
        for pattern in self.TIMESTAMP_NAME_PATTERNS:
            if pattern in name_lower:
                return "TIMESTAMP"

        # Check exact matches for date
        if name_lower in self.DATE_NAME_EXACT:
            return "DATE"

        # Check patterns for date (e.g., "transaction_date", "rate_date")
        for pattern in self.DATE_NAME_PATTERNS:
            if pattern in name_lower:
                return "DATE"

        # Special case: column name ends with "date" (e.g., "transaction_date")
        if name_lower.endswith("date"):
            return "DATE"

        return "UNKNOWN"

    def _infer_from_cast(self, context: str) -> str:
        """Infer type from cast expressions like .cast('double')."""
        match = self.CAST_PATTERN.search(context)
        if match:
            spark_type = match.group(1).lower()
            return self.SPARK_TYPE_MAP.get(spark_type, "UNKNOWN")
        return "UNKNOWN"
    
    def _infer_from_expression(self, context: str) -> str:
        """
        Infer type from Spark expression using AST analysis.
        
        Detects:
        - Arithmetic operations (*, /, +, -) → L_DECIMAL
        - Date functions (current_date, to_date) → L_DATE/L_DATETIME
        - String functions (concat, lower) → L_TEXT
        - Boolean comparisons (>, <, ==) → L_BOOLEAN
        """
        try:
            from asg_pyspark.analysis.spark_to_sql import detect_expression_type
            
            expr_type = detect_expression_type(context)
            if expr_type:
                type_map = {
                    "NUMERIC": "L_DECIMAL",
                    "TEXT": "L_TEXT",
                    "BOOLEAN": "L_BOOLEAN",
                    "TIMESTAMP": "L_DATETIME",
                    "DATE": "L_DATE",
                }
                return type_map.get(expr_type, "UNKNOWN")
        except ImportError:
            # Fallback if asg_pyspark not available
            pass
        
        return "UNKNOWN"
    
    def _infer_from_functions(self, column_name: str, context: str) -> str:
        """Infer type from function usage."""
        context_lower = context.lower()

        # Check each function category
        for func in self.NUMERIC_FUNCTIONS:
            # Pattern: func(col("name")) or func("name")
            if re.search(rf"{func}\s*\([^)]*[\'\"]{column_name}[\'\"]", context_lower):
                return "NUMERIC"

        for func in self.STRING_FUNCTIONS:
            if re.search(rf"{func}\s*\([^)]*[\'\"]{column_name}[\'\"]", context_lower):
                return "STRING"

        for func in self.DATE_FUNCTIONS:
            if re.search(rf"{func}\s*\([^)]*[\'\"]{column_name}[\'\"]", context_lower):
                return "DATE"

        for func in self.TIMESTAMP_FUNCTIONS:
            if re.search(rf"{func}\s*\([^)]*[\'\"]{column_name}[\'\"]", context_lower):
                return "TIMESTAMP"

        return "UNKNOWN"

    def _infer_from_comparisons(self, column_name: str, context: str) -> str:
        """Infer type from comparison patterns."""
        # col("name") > 100 -> NUMERIC
        # Pattern: col('column_name') followed by comparison operator and number
        pattern = r"col\(['\"]" + re.escape(column_name) + r"['\"]\)\s*[<>=!]+\s*\d+"
        if re.search(pattern, context):
            return "NUMERIC"

        # col("name") == "string" -> STRING
        pattern = r"col\(['\"]" + re.escape(column_name) + r"['\"]\)\s*==\s*['\"]"
        if re.search(pattern, context):
            return "STRING"


        # col("name").isin(['a', 'b', 'c']) -> STRING (if values are strings)
        # Pattern: col('column_name').isin([...])
        isin_pattern = r"col\(['\"']" + re.escape(column_name) + r"['\"']\)\.isin\(\[([^\]]+)\]\)"
        match = re.search(isin_pattern, context)
        if match:
            values = match.group(1)
            # Check if values are strings (quoted) or numbers
            if "'" in values or '"' in values:
                return "STRING"
            elif values.replace(",", "").replace(" ", "").isdigit():
                return "NUMERIC"

        return "UNKNOWN"


# =============================================================================
# Schema Propagator
# =============================================================================


class SchemaPropagator:
    """
    Propagates schemas through the ASG lineage graph.

    Uses collected column references and inferred types to build
    a Virtual Catalog that assigns schemas to each node.

    The schema_store is a Global Schema Registry that maps any node ID
    (including param_XXX for function parameters) to its column schema.
    This enables proper Join schema merging and column-level lineage.
    """

    def __init__(self) -> None:
        self.collector = ColumnCollector()
        self.inferencer = TypeInferencer()
        self.virtual_catalog: dict[str, list[InferredColumn]] = {}
        # Global Schema Store: node_id -> list of columns
        # Includes: in_XXX, tx_XXX, param_XXX
        self.schema_store: dict[str, list[InferredColumn]] = {}
        # Maps param_funcName_argName -> data_in ID (from execution_calls)
        self._param_to_source: dict[str, str] = {}

    def process(self, asg: ASG) -> ASG:
        """
        Process ASG and fill inferred schema fields.

        Returns: ASG with populated inferred_columns fields
        """
        # Phase 0: Build param -> data_in mapping from execution_calls
        for ec in asg.execution_calls:
            if not (ec.bindings and ec.bindings.inputs):
                continue
            callee_fn = ec.callee.function if ec.callee else None
            if not callee_fn:
                continue
            for binding in ec.bindings.inputs:
                if binding.source_id and binding.arg_name:
                    param_id = f"param_{callee_fn}_{binding.arg_name}"
                    self._param_to_source[param_id] = binding.source_id

        # Phase 1: Discovery - collect all column references
        refs_by_node = self.collector.collect_from_asg(asg)

        # Phase 2: Build virtual catalog for data_in nodes
        self._build_source_schemas(asg, refs_by_node)

        # Phase 2.5: Initialize schema_store with data_in schemas
        self._initialize_schema_store(asg)

        # Phase 2.6: Inject function parameter schemas
        self._inject_function_param_schemas(asg)

        # Phase 2.7: Ensure join columns exist in upstream sources
        # This traces backward from JOINs to add missing join keys
        self._ensure_join_columns(asg)

        # Phase 3: Propagate through transformations
        self._propagate_schemas(asg, refs_by_node)

        # Phase 4: Assign to sinks
        self._assign_sink_schemas(asg)

        return asg

    def _initialize_schema_store(self, asg: ASG) -> None:
        """
        Initialize schema_store with data_in node schemas.

        This provides the "seed" schemas that will propagate through
        the transformation chain.
        """
        for data_in in asg.data_in:
            # Deep copy to prevent retroactive modifications
            self.schema_store[data_in.id] = [deepcopy(col) for col in data_in.inferred_columns]

    def _inject_function_param_schemas(self, asg: ASG) -> None:
        """
        Pre-populate schema_store with function parameter schemas.

        This enables Join operations to access right-side schemas via
        param_XXX identifiers. Uses two strategies:

        1. inferred_schema_origin: If the argument has a known origin
           (e.g., "data_in.global_transactions"), use that schema.

        2. Name matching: Fall back to matching param name with data_in
           names for cases where origin resolution failed.
        """
        # Build lookup of data_in by name and id
        data_in_by_name: dict[str, list[InferredColumn]] = {}
        data_in_by_id: dict[str, list[InferredColumn]] = {}

        for data_in in asg.data_in:
            if data_in.name:
                data_in_by_name[data_in.name] = [deepcopy(col) for col in data_in.inferred_columns]
            data_in_by_id[data_in.id] = [deepcopy(col) for col in data_in.inferred_columns]

        for func in asg.functions:
            for arg in func.arguments:
                param_id = f"param_{arg.name}"

                # Skip if already populated
                if param_id in self.schema_store:
                    continue

                schema: list[InferredColumn] = []

                # Strategy 1: Use inferred_schema_origin
                if arg.inferred_schema_origin:
                    # Parse "data_in.table_name" format
                    origin = arg.inferred_schema_origin
                    if origin.startswith("data_in."):
                        source_name = origin[8:]  # Remove "data_in." prefix
                        if source_name in data_in_by_name:
                            schema = deepcopy(data_in_by_name[source_name])
                        elif source_name in data_in_by_id:
                            schema = deepcopy(data_in_by_id[source_name])

                # Strategy 2: Name matching fallback
                if not schema:
                    # Try to match param name with data_in names
                    for data_in in asg.data_in:
                        # Check if param name is similar to data_in name
                        if data_in.name:
                            # df_sales -> sales, df_products -> products
                            param_clean = arg.name.replace("df_", "").replace("_df", "")
                            source_clean = data_in.name.replace("df_", "").replace("_df", "")

                            if param_clean in source_clean or source_clean in param_clean:
                                schema = deepcopy(data_in_by_id.get(data_in.id, []))
                                break

                # Strategy 3: Execution call bindings (Scala convention)
                # Match param_funcName_argName to data_in via execution_calls
                if not schema:
                    for ec in asg.execution_calls:
                        if not (ec.bindings and ec.bindings.inputs):
                            continue
                        callee_fn = ec.callee.function if ec.callee else None
                        if not callee_fn:
                            continue
                        for binding in ec.bindings.inputs:
                            bound_param_id = f"param_{callee_fn}_{binding.arg_name}"
                            if bound_param_id != param_id:
                                continue
                            src_id = binding.source_id
                            if src_id and src_id in data_in_by_id:
                                schema = deepcopy(data_in_by_id[src_id])
                                break
                        if schema:
                            break

                # Register in schema_store
                if schema:
                    self.schema_store[param_id] = schema

    def _ensure_join_columns(self, asg: ASG) -> None:
        """
        Ensure join columns exist in upstream data sources.

        For each JOIN operation, this method:
        1. Identifies the join key column
        2. Traces backward through the lineage to find the data source
        3. Adds the column to the data source if missing
        4. Updates the schema_store so propagation will carry it forward

        This ensures that when a JOIN requires a column (e.g., product_id),
        that column is present in the entire lineage chain from data source
        through all intermediate transformations.
        """
        # Build lookup: node_id -> transformation for backward navigation
        tx_by_id: dict[str, TransformationNode] = {tx.id: tx for tx in asg.transformations}

        # Build lookup: node_id -> data_in for identifying sources
        data_in_by_id: dict[str, DataSource] = {d.id: d for d in asg.data_in}

        # Find all JOINs and process their join keys
        for tx in asg.transformations:
            if tx.operation not in ("join", "crossJoin"):
                continue

            join_key = tx.parameters.get("join_condition")
            if not join_key:
                continue

            # Normalize join_key to list (handles both single column and multi-column joins)
            join_columns = join_key if isinstance(join_key, list) else [join_key]

            # For each input of the join, ensure each join key column exists
            for input_id in tx.inputs:
                for col in join_columns:
                    self._trace_and_add_join_column(
                        input_id, col, tx_by_id, data_in_by_id, asg
                    )

    def _trace_and_add_join_column(
        self,
        node_id: str,
        column_name: str,
        tx_by_id: dict[str, "TransformationNode"],
        data_in_by_id: dict[str, "DataSource"],
        asg: ASG,
    ) -> None:
        """
        Trace backward from a node to find/add a column in the data source.

        Navigates the lineage chain until it reaches a data_in node,
        then adds the column if missing.
        """
        visited: set[str] = set()
        sources_to_update: list[str] = []

        # Trace backward to find data sources
        self._find_data_sources(node_id, tx_by_id, data_in_by_id, visited, sources_to_update)

        # Add column to each data source if missing
        for source_id in sources_to_update:
            if source_id in data_in_by_id:
                data_in = data_in_by_id[source_id]

                # Check if column already exists
                existing_names = {col.name for col in data_in.inferred_columns}
                if column_name not in existing_names:
                    # Add the join key column
                    new_col = InferredColumn(
                        name=column_name,
                        inferred_type="UNKNOWN",
                        source=InferenceSource.JOIN_KEY,
                        confidence=InferenceConfidence.MEDIUM,
                        first_seen_nodes=[source_id],
                    )
                    data_in.inferred_columns.append(new_col)

                    # Also update schema_store
                    if source_id in self.schema_store:
                        store_names = {col.name for col in self.schema_store[source_id]}
                        if column_name not in store_names:
                            self.schema_store[source_id].append(deepcopy(new_col))

    def _find_data_sources(
        self,
        node_id: str,
        tx_by_id: dict[str, "TransformationNode"],
        data_in_by_id: dict[str, "DataSource"],
        visited: set[str],
        sources: list[str],
    ) -> None:
        """
        Recursively find all data sources reachable from a node.

        Traverses the transformation graph backward to find all
        data_in nodes that feed into the given node.
        """
        if node_id in visited:
            return
        visited.add(node_id)

        # If this is a data source, add it
        if node_id in data_in_by_id:
            if node_id not in sources:
                sources.append(node_id)
            return

        # If this is a transformation, recurse to its inputs
        if node_id in tx_by_id:
            tx = tx_by_id[node_id]
            for input_id in tx.inputs:
                self._find_data_sources(input_id, tx_by_id, data_in_by_id, visited, sources)

        # Handle param_ references (function parameters)
        if node_id.startswith("param_"):
            resolved = self._param_to_source.get(node_id)
            if resolved:
                self._find_data_sources(
                    resolved, tx_by_id, data_in_by_id, visited, sources
                )

    @staticmethod
    def _collect_created_columns(asg: "ASG") -> set[str]:
        """Identify columns CREATED by transformations (not read from sources).

        Supports both PySpark and Scala parameter conventions:
        - PySpark agg: column_aliases list
        - Scala agg: expressions string with .alias("name") calls
        - withColumnRenamed: old_name/new_name or existing/new
        """
        created: set[str] = set()
        for tx in asg.transformations:
            op = tx.operation
            params = tx.parameters

            if op == "withColumn":
                col_name = params.get("column_name")
                if col_name:
                    created.add(col_name)

            elif op == "withColumnRenamed":
                new_name = params.get("new_name") or params.get("new")
                if new_name:
                    created.add(new_name)

            elif op == "alias":
                if tx.logic:
                    for alias in ColumnCollector._extract_aliases_from_expression(tx.logic):
                        created.add(alias)

            elif op in ("groupBy_agg", "agg"):
                for alias in params.get("column_aliases", []):
                    created.add(alias)
                expr = params.get("expressions", "")
                if expr:
                    for alias in ColumnCollector._extract_aliases_from_expression(expr):
                        created.add(alias)

        return created

    def _build_source_schemas(
        self, asg: ASG, refs_by_node: dict[str, list[ColumnReference]]
    ) -> None:
        """
        Build inferred schemas for data_in nodes.

        IMPORTANT: Only include columns that are READ from sources,
        not columns that are CREATED by transformations (withColumn, alias).
        """
        created_columns = self._collect_created_columns(asg)

        # Second pass: collect columns that trace back to data sources
        # Initialize with existing inferred_columns (from catalog or test data)
        source_columns: dict[str, dict[str, InferredColumn]] = {}

        for data_in in asg.data_in:
            # Preserve existing columns (e.g., from catalog integration)
            source_columns[data_in.id] = {col.name: col for col in data_in.inferred_columns}

        # Only consider transformations that directly read from data sources
        for tx in asg.transformations:
            if not tx.inputs:
                continue

            # Get columns referenced in this transformation
            refs = refs_by_node.get(tx.id, [])

            for ref in refs:
                # SKIP columns that were created by transformations
                if ref.name in created_columns:
                    continue

                # SKIP if this is an EXPLICIT creation (not a read)
                if ref.source == InferenceSource.EXPLICIT:
                    continue

                # Infer type from context
                inferred_type, confidence = self.inferencer.infer_type(
                    ref.name, ref.context, ref.source
                )

                col = InferredColumn(
                    name=ref.name,
                    inferred_type=inferred_type,
                    source=ref.source,
                    confidence=confidence,
                    first_seen_nodes=[ref.node_id],
                )

                # Trace back to original data sources
                self._trace_to_sources(tx, ref.name, col, source_columns, asg, created_columns)

        # Apply to data_in nodes
        for data_in in asg.data_in:
            if data_in.id in source_columns:
                data_in.inferred_columns = list(source_columns[data_in.id].values())

        # Store in virtual catalog
        self.virtual_catalog = {
            node_id: list(cols.values()) for node_id, cols in source_columns.items()
        }

    def _trace_to_sources(
        self,
        tx: TransformationNode,
        col_name: str,
        col: InferredColumn,
        source_columns: dict[str, dict[str, InferredColumn]],
        asg: ASG,
        created_columns: set[str],
    ) -> None:
        """
        Trace a column reference back to its original data source(s).

        Only assigns the column to a source if it wasn't created by
        an intermediate transformation.

        IMPORTANT: For source columns, first_seen_nodes is set to the
        SOURCE ID (in_XXX), not the transformation that references it.
        
        Uses recursive tracing through transformation chains to reach
        all original data sources, not just direct inputs.
        """
        # Build lookup tables for recursive tracing
        tx_by_id = {t.id: t for t in asg.transformations}
        data_in_by_id = {d.id: d for d in asg.data_in}
        
        # Find ALL data sources reachable from this transformation
        reachable_sources: list[str] = []
        visited: set[str] = set()
        self._find_data_sources(tx.id, tx_by_id, data_in_by_id, visited, reachable_sources)
        
        # Add the column to each reachable data source
        for source_id in reachable_sources:
            if source_id in source_columns:
                # Create a copy with first_seen_nodes = [source ID]
                source_col = InferredColumn(
                    name=col.name,
                    inferred_type=col.inferred_type,
                    source=col.source,
                    confidence=col.confidence,
                    exact_type=col.exact_type,
                    nullable=col.nullable,
                    first_seen_nodes=[source_id],  # Source ID, not tx ID
                    usage_count=col.usage_count,
                )

                if col_name in source_columns[source_id]:
                    # Merge with existing (will combine first_seen_nodes)
                    existing = source_columns[source_id][col_name]
                    source_columns[source_id][col_name] = existing.merge_with(source_col)
                else:
                    source_columns[source_id][col_name] = source_col

    def _propagate_schemas(self, asg: ASG, refs_by_node: dict[str, list[ColumnReference]]) -> None:
        """
        Propagate schemas through transformation chain.

        Uses schema_store as the global registry, enabling:
        - Join schema merging (left + right sides)
        - Column-level lineage tracking
        - Proper nullability propagation

        IMPORTANT: inferred_input includes from_input to track which input
        each column originates from. For joins, the join key column appears
        once for each input that provides it (e.g., product_id from tx_003
        AND product_id from in_009).
        """
        for tx in asg.transformations:
            # Build inferred_input with from_inputs tracking
            # Each column tracks which input it comes from
            # Uses InputColumn which requires from_inputs
            inferred_input_list: list[InputColumn] = []

            # Also maintain merged dict for transformation logic
            input_cols: dict[str, InferredColumn] = {}

            for input_id in tx.inputs:
                for col in self.schema_store.get(input_id, []):
                    # Create InputColumn with required from_inputs
                    input_col = InputColumn(
                        name=col.name,
                        inferred_type=col.inferred_type,
                        source=col.source,
                        confidence=col.confidence,
                        exact_type=col.exact_type,
                        nullable=col.nullable,
                        first_seen_nodes=col.first_seen_nodes.copy() if col.first_seen_nodes else [],
                        usage_count=col.usage_count,
                        from_inputs=[input_id],  # Required: track the source input
                    )
                    inferred_input_list.append(input_col)

                    # For merged dict (used by transformation handlers)
                    if col.name not in input_cols:
                        input_cols[col.name] = deepcopy(col)
                    else:
                        # Collision handling: merge with existing
                        input_cols[col.name] = input_cols[col.name].merge_with(deepcopy(col))

            # Apply transformation effect (handles join merge, select, etc.)
            output_cols = self._apply_transformation(tx, input_cols, refs_by_node)

            # Store in ASG node
            tx.inferred_input = inferred_input_list
            tx.inferred_output = [deepcopy(col) for col in output_cols.values()]

            # Register in schema_store for downstream nodes
            # IMPORTANT: Deep copy to create immutable snapshot
            self.schema_store[tx.id] = [deepcopy(col) for col in output_cols.values()]

    def _apply_transformation(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        refs_by_node: dict[str, list[ColumnReference]],
    ) -> dict[str, InferredColumn]:
        """
        Apply transformation effect on schema using operation-specific rules.

        This is the "Dispatcher" that decides which inference rule to apply
        based on the operation type.
        """
        operation = tx.operation
        params = tx.parameters
        refs = refs_by_node.get(tx.id, [])

        # Dispatch to operation-specific handler
        handler = self._get_operation_handler(operation)
        return handler(tx, input_cols, params, refs)

    def _get_operation_handler(self, operation: str) -> Any:
        """Get the handler function for a specific operation."""
        handlers = {
            # Pass-through operations (output = input)
            "filter": self._handle_passthrough,
            "where": self._handle_passthrough,
            "distinct": self._handle_passthrough,
            "dropDuplicates": self._handle_passthrough,
            "orderBy": self._handle_passthrough,
            "sort": self._handle_passthrough,
            "limit": self._handle_passthrough,
            "cache": self._handle_passthrough,
            "persist": self._handle_passthrough,
            # Column modification operations
            "select": self._handle_select,
            "withColumn": self._handle_withColumn,
            "withColumn_custom": self._handle_withColumn_custom,
            "withColumnRenamed": self._handle_withColumnRenamed,
            "drop": self._handle_drop,
            "alias": self._handle_alias,
            # Join operations (merge schemas)
            "join": self._handle_join,
            "crossJoin": self._handle_join,
            "union": self._handle_union,
            "unionAll": self._handle_union,
            "unionByName": self._handle_union,
            # Aggregation operations
            "groupBy_agg": self._handle_groupBy_agg,
            "agg": self._handle_agg,
            "groupBy": self._handle_passthrough,  # groupBy alone passes through
            # Window operations
            "over": self._handle_passthrough,
        }
        return handlers.get(operation, self._handle_default)

    def _handle_passthrough(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        params: dict[str, Any],
        refs: list[ColumnReference],
    ) -> dict[str, InferredColumn]:
        """
        Pass-through: output schema = input schema.

        Used for filter, orderBy, distinct, etc.
        Also validates that referenced columns exist.
        """
        output_cols = dict(input_cols)

        # Add any new columns discovered in references
        for ref in refs:
            if ref.name not in output_cols:
                inferred_type, confidence = self.inferencer.infer_type(
                    ref.name, ref.context, ref.source
                )
                output_cols[ref.name] = InferredColumn(
                    name=ref.name,
                    inferred_type=inferred_type,
                    source=ref.source,
                    confidence=confidence,
                    first_seen_nodes=[tx.id],
                )

        return output_cols

    def _handle_select(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        params: dict[str, Any],
        refs: list[ColumnReference],
    ) -> dict[str, InferredColumn]:
        """
        Select: output contains only selected columns.

        If we can parse the select columns, filter to those.
        Otherwise, keep columns found in references.
        """
        selected_cols: dict[str, InferredColumn] = {}

        # Strategy 1: use explicit column names from parameters
        param_cols = params.get("columns", [])
        if param_cols and isinstance(param_cols, list):
            for col_expr in param_cols:
                clean = col_expr.strip().strip("'\"")
                # Simple column name
                if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", clean):
                    col_name = clean
                else:
                    # Complex expression — try extracting alias
                    aliases = ColumnCollector._extract_aliases_from_expression(col_expr)
                    col_name = aliases[0] if aliases else None
                if not col_name:
                    continue
                if col_name in input_cols:
                    selected_cols[col_name] = input_cols[col_name]
                else:
                    inferred_type, confidence = self.inferencer.infer_type(
                        col_name, col_expr, InferenceSource.SELECT
                    )
                    selected_cols[col_name] = InferredColumn(
                        name=col_name,
                        inferred_type=inferred_type,
                        source=InferenceSource.SELECT,
                        confidence=confidence,
                        first_seen_nodes=[tx.id],
                    )

        # Strategy 2: use column references from ColumnCollector (PySpark convention)
        if not selected_cols:
            for ref in refs:
                if ref.name in input_cols:
                    selected_cols[ref.name] = input_cols[ref.name]
                else:
                    inferred_type, confidence = self.inferencer.infer_type(
                        ref.name, ref.context, ref.source
                    )
                    selected_cols[ref.name] = InferredColumn(
                        name=ref.name,
                        inferred_type=inferred_type,
                        source=ref.source,
                        confidence=confidence,
                        first_seen_nodes=[tx.id],
                    )

        return selected_cols if selected_cols else dict(input_cols)

    def _handle_withColumn(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        params: dict[str, Any],
        refs: list[ColumnReference],
    ) -> dict[str, InferredColumn]:
        """
        withColumn: adds or replaces a column.

        Output = input + new column with HIGH confidence (explicit).
        """
        output_cols = dict(input_cols)

        col_name = params.get("column_name")
        if col_name:
            # Try to infer type from expression if available
            expr = params.get("expression", "")
            inferred_type, _ = self.inferencer.infer_type(col_name, expr, InferenceSource.EXPLICIT)

            output_cols[col_name] = InferredColumn(
                name=col_name,
                inferred_type=inferred_type,
                source=InferenceSource.EXPLICIT,
                confidence=InferenceConfidence.HIGH,
                first_seen_nodes=[tx.id],
            )

        return output_cols

    def _handle_withColumn_custom(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        params: dict[str, Any],
        refs: list[ColumnReference],
    ) -> dict[str, InferredColumn]:
        """
        withColumn_custom: UDF-based column creation.

        Preserves UDF-inferred types from parser (e.g., obfuscate -> L_TEXT).
        Also adds referenced columns (UDF arguments) to track column dependencies.
        Falls back to UNKNOWN for unrecognized UDFs.
        """
        output_cols = dict(input_cols)

        # Add any columns referenced in the UDF call (e.g., customer_email in obfuscated_udf(col('customer_email')))
        # These track which columns are consumed by the UDF
        for ref in refs:
            if ref.name not in output_cols:
                inferred_type, confidence = self.inferencer.infer_type(
                    ref.name, ref.context, ref.source
                )
                output_cols[ref.name] = InferredColumn(
                    name=ref.name,
                    inferred_type=inferred_type,
                    source=ref.source,
                    confidence=confidence,
                    first_seen_nodes=[tx.id],
                )

        col_name = params.get("column_name")
        if col_name:
            # Check if parser already inferred a type for this column
            # Parser's inferred_output may have L_TEXT from UDF semantic patterns
            parser_inferred = None
            for col in tx.inferred_output:
                if col.name == col_name and col.source == InferenceSource.UDF_SEMANTIC:
                    parser_inferred = col
                    break

            if parser_inferred:
                # Preserve the parser's UDF-semantic inference
                output_cols[col_name] = InferredColumn(
                    name=col_name,
                    inferred_type=parser_inferred.inferred_type,
                    source=InferenceSource.UDF_SEMANTIC,
                    confidence=InferenceConfidence.HIGH,
                    first_seen_nodes=[tx.id],
                )
            else:
                # UDF with unknown semantics - mark as UNKNOWN
                output_cols[col_name] = InferredColumn(
                    name=col_name,
                    inferred_type="UNKNOWN",
                    source=InferenceSource.EXPLICIT,
                    confidence=InferenceConfidence.LOW,
                    first_seen_nodes=[tx.id],
                )

        return output_cols

    def _handle_withColumnRenamed(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        params: dict[str, Any],
        refs: list[ColumnReference],
    ) -> dict[str, InferredColumn]:
        """
        withColumnRenamed: renames a column.

        Output = input - old_name + new_name (preserving type info).
        """
        output_cols = dict(input_cols)

        old_name = params.get("old_name") or params.get("existing")
        new_name = params.get("new_name") or params.get("new")

        if old_name and new_name and old_name in output_cols:
            old_col = output_cols.pop(old_name)
            output_cols[new_name] = InferredColumn(
                name=new_name,
                inferred_type=old_col.inferred_type,
                source=InferenceSource.EXPLICIT,
                confidence=old_col.confidence,
                exact_type=old_col.exact_type,
                nullable=old_col.nullable,
                first_seen_nodes=[tx.id],
                usage_count=old_col.usage_count,
            )

        return output_cols

    def _handle_drop(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        params: dict[str, Any],
        refs: list[ColumnReference],
    ) -> dict[str, InferredColumn]:
        """
        drop: removes columns from schema.

        Output = input - dropped columns.
        """
        output_cols = dict(input_cols)

        dropped = params.get("columns", [])
        for col_name in dropped:
            clean_name = self._extract_col_name(col_name)
            if clean_name and clean_name in output_cols:
                del output_cols[clean_name]

        return output_cols

    def _handle_alias(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        params: dict[str, Any],
        refs: list[ColumnReference],
    ) -> dict[str, InferredColumn]:
        """
        alias: creates a named column (usually in aggregations).

        Extract alias name from logic: .alias('name')
        """
        output_cols = dict(input_cols)

        # Try to extract alias name from logic
        if tx.logic:
            alias_match = re.search(r"\.alias\(['\"]([^'\"]+)['\"]\)", tx.logic)
            if alias_match:
                alias_name = alias_match.group(1)
                # Try to infer type from the expression
                inferred_type = "UNKNOWN"
                if "coalesce" in tx.logic or "sum" in tx.logic or "count" in tx.logic:
                    inferred_type = "NUMERIC"

                output_cols[alias_name] = InferredColumn(
                    name=alias_name,
                    inferred_type=inferred_type,
                    source=InferenceSource.EXPLICIT,
                    confidence=InferenceConfidence.HIGH,
                    first_seen_nodes=[tx.id],
                )

        # Also add from refs
        for ref in refs:
            if ref.name not in output_cols:
                output_cols[ref.name] = InferredColumn(
                    name=ref.name,
                    inferred_type="UNKNOWN",
                    source=InferenceSource.EXPLICIT,
                    confidence=InferenceConfidence.HIGH,
                    first_seen_nodes=[tx.id],
                )

        return output_cols

    def _handle_join(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        params: dict[str, Any],
        refs: list[ColumnReference],
    ) -> dict[str, InferredColumn]:
        """
        Join: merges schemas from BOTH sides.

        Output = left_columns + right_columns

        Key behaviors:
        1. Join key columns appear once (from left side)
        2. Right-side columns get nullable=True for LEFT joins
        3. Each column tracks its origins (first_seen_nodes)

        This is the core of Column-Level Lineage for joins.
        """
        # Get join metadata
        join_type = params.get("join_type", "inner").lower()
        join_key_raw = params.get("join_condition")
        
        # Normalize join_key to list (handles both single column and multi-column joins)
        join_keys: list[str] = []
        if join_key_raw:
            join_keys = join_key_raw if isinstance(join_key_raw, list) else [join_key_raw]

        # Start with input_cols (already has columns from both sides via _propagate_schemas)
        # But we need to properly handle the merge with nullability
        output_cols: dict[str, InferredColumn] = {}

        # Identify which input is left vs right
        left_input_id = tx.inputs[0] if tx.inputs else None
        right_input_id = tx.inputs[1] if len(tx.inputs) >= 2 else None

        # Get schemas from schema_store
        left_schema = self.schema_store.get(left_input_id, []) if left_input_id else []
        right_schema = self.schema_store.get(right_input_id, []) if right_input_id else []

        # If schema_store is empty, fall back to input_cols (which has merged columns)
        if not left_schema and not right_schema:
            return dict(input_cols)

        # Track column names from left side
        left_col_names: set[str] = set()

        # Add left side columns (use deepcopy for immutability)
        for col in left_schema:
            col_copy = deepcopy(col)
            output_cols[col.name] = col_copy
            left_col_names.add(col.name)

        # Find right-side join key columns for merging (keyed by column name)
        right_join_cols: dict[str, InferredColumn] = {}
        for col in right_schema:
            if col.name in join_keys:
                right_join_cols[col.name] = col

        # Mark each join key with merged info from both sides
        for join_key in join_keys:
            right_join_col = right_join_cols.get(join_key)
            if join_key not in output_cols:
                continue
            existing = output_cols[join_key]
            # Merge first_seen_nodes from both left and right
            merged_nodes = existing.first_seen_nodes.copy()
            if right_join_col:
                merged_nodes = InferredColumn._merge_node_lists(
                    merged_nodes, right_join_col.first_seen_nodes
                )
            # Merge inferred_type
            merged_type = existing.inferred_type
            if right_join_col:
                merged_type = InferredColumn._merge_types(
                    existing.inferred_type, right_join_col.inferred_type
                )
            # Use lower confidence
            merged_confidence = existing.confidence
            if right_join_col and right_join_col.confidence != existing.confidence:
                priority = {InferenceConfidence.HIGH: 0, InferenceConfidence.MEDIUM: 1, InferenceConfidence.LOW: 2}
                merged_confidence = (
                    existing.confidence
                    if priority[existing.confidence] > priority[right_join_col.confidence]
                    else right_join_col.confidence
                )
            output_cols[join_key] = InferredColumn(
                name=join_key,
                inferred_type=merged_type,
                source=InferenceSource.JOIN_KEY,
                confidence=merged_confidence,
                first_seen_nodes=merged_nodes,
                usage_count=existing.usage_count + (right_join_col.usage_count if right_join_col else 1),
            )

        # Add right side columns (excluding duplicates)
        for col in right_schema:
            # Skip join keys (already merged above)
            if col.name in join_keys:
                continue

            # Skip if column already exists (collision with left)
            if col.name in left_col_names:
                continue

            # Create column with proper nullability and origin
            new_col = InferredColumn(
                name=col.name,
                inferred_type=col.inferred_type,
                source=col.source,
                confidence=col.confidence,
                exact_type=col.exact_type,
                # LEFT/RIGHT OUTER joins make right-side columns nullable
                nullable=True if "left" in join_type or "outer" in join_type else col.nullable,
                first_seen_nodes=col.first_seen_nodes.copy() if col.first_seen_nodes else [right_input_id or tx.id],
                usage_count=col.usage_count,
            )
            output_cols[col.name] = new_col

        # Also add columns from references (may include derived columns)
        for ref in refs:
            if ref.name not in output_cols:
                inferred_type, confidence = self.inferencer.infer_type(
                    ref.name, ref.context, ref.source
                )
                output_cols[ref.name] = InferredColumn(
                    name=ref.name,
                    inferred_type=inferred_type,
                    source=ref.source,
                    confidence=confidence,
                    first_seen_nodes=[tx.id],
                )

        # Ensure all join keys are present (may come from right side)
        for jk in join_keys:
            if jk not in output_cols:
                output_cols[jk] = InferredColumn(
                    name=jk,
                    inferred_type="UNKNOWN",
                    source=InferenceSource.JOIN_KEY,
                    confidence=InferenceConfidence.MEDIUM,
                    first_seen_nodes=[tx.id],
                )

        return output_cols

    def _handle_union(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        params: dict[str, Any],
        refs: list[ColumnReference],
    ) -> dict[str, InferredColumn]:
        """
        union: combines rows, schema should match.

        Output = input schema (assuming both sides match).
        """
        return dict(input_cols)

    def _handle_groupBy_agg(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        params: dict[str, Any],
        refs: list[ColumnReference],
    ) -> dict[str, InferredColumn]:
        """
        groupBy_agg: groups and aggregates.

        Output = group columns + aggregation result columns.
        Aggregation columns are typed as NUMERIC by default.
        """
        new_cols: dict[str, InferredColumn] = {}

        # Group columns (preserved from input)
        for gc in params.get("group_columns", []):
            name = self._extract_col_name(gc)
            if name and name in input_cols:
                new_cols[name] = input_cols[name]
            elif name:
                # Group column not in input - infer it
                new_cols[name] = InferredColumn(
                    name=name,
                    inferred_type="UNKNOWN",
                    source=InferenceSource.GROUP_BY,
                    confidence=InferenceConfidence.MEDIUM,
                    first_seen_nodes=[tx.id],
                )

        # Aggregation aliases - infer type from the aggregation function
        aliases = list(params.get("column_aliases", []))
        expr = params.get("expressions", "")
        logic = tx.logic or ""
        if expr and not aliases:
            aliases = ColumnCollector._extract_aliases_from_expression(expr)
        for alias in aliases:
            agg_type = self._infer_agg_type(alias, logic)
            new_cols[alias] = InferredColumn(
                name=alias,
                inferred_type=agg_type,
                source=InferenceSource.AGGREGATION,
                confidence=InferenceConfidence.HIGH,
                first_seen_nodes=[tx.id],
            )

        return new_cols if new_cols else dict(input_cols)

    def _infer_agg_type(self, alias: str, logic: str) -> str:
        """Infer the return type of an aggregation based on the function used."""
        logic_lower = logic.lower()
        
        array_funcs = ('collect_set', 'collect_list', 'array_agg')
        for func in array_funcs:
            if f"{func}(" in logic_lower and f".alias('{alias}')" in logic:
                return "L_ARRAY"
        
        string_funcs = ('concat_ws(', 'first(', 'last(', 'min(', 'max(')
        for func in string_funcs:
            if func in logic_lower and f".alias('{alias}')" in logic:
                pass
        
        return "NUMERIC"

    def _handle_agg(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        params: dict[str, Any],
        refs: list[ColumnReference],
    ) -> dict[str, InferredColumn]:
        """
        agg (standalone): aggregation without groupBy.

        Similar to groupBy_agg but no group columns.
        """
        new_cols: dict[str, InferredColumn] = {}

        aliases = list(params.get("column_aliases", []))
        expr = params.get("expressions", "")
        logic = tx.logic or ""
        if expr and not aliases:
            aliases = ColumnCollector._extract_aliases_from_expression(expr)
        for alias in aliases:
            agg_type = self._infer_agg_type(alias, logic)
            new_cols[alias] = InferredColumn(
                name=alias,
                inferred_type=agg_type,
                source=InferenceSource.AGGREGATION,
                confidence=InferenceConfidence.HIGH,
                first_seen_nodes=[tx.id],
            )

        # Add columns from references
        for ref in refs:
            if ref.name not in new_cols:
                inferred_type, confidence = self.inferencer.infer_type(
                    ref.name, ref.context, ref.source
                )
                new_cols[ref.name] = InferredColumn(
                    name=ref.name,
                    inferred_type=inferred_type,
                    source=ref.source,
                    confidence=confidence,
                    first_seen_nodes=[tx.id],
                )

        return new_cols if new_cols else dict(input_cols)

    def _handle_default(
        self,
        tx: TransformationNode,
        input_cols: dict[str, InferredColumn],
        params: dict[str, Any],
        refs: list[ColumnReference],
    ) -> dict[str, InferredColumn]:
        """
        Default handler: pass-through with column discovery.

        Used for unknown operations - preserves input and adds
        any new columns discovered in references.
        """
        output_cols = dict(input_cols)

        for ref in refs:
            if ref.name not in output_cols:
                inferred_type, confidence = self.inferencer.infer_type(
                    ref.name, ref.context, ref.source
                )
                output_cols[ref.name] = InferredColumn(
                    name=ref.name,
                    inferred_type=inferred_type,
                    source=ref.source,
                    confidence=confidence,
                    first_seen_nodes=[tx.id],
                )

        return output_cols

    def _assign_sink_schemas(self, asg: ASG) -> None:
        """Assign final schemas to data_out nodes."""
        # Build lookup of transformation outputs
        tx_outputs: dict[str, list[InferredColumn]] = {}
        for tx in asg.transformations:
            tx_outputs[tx.id] = tx.inferred_output

        for sink in asg.data_out:
            if sink.source_id and sink.source_id in tx_outputs:
                sink.inferred_columns = tx_outputs[sink.source_id]

    def _extract_col_name(self, expr: str) -> str | None:
        """Extract column name from expression."""
        expr = expr.strip().strip("'\"")
        if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", expr):
            return expr
        return None


# =============================================================================
# Public API
# =============================================================================


def infer_schemas(asg: ASG) -> ASG:
    """
    Infer and propagate schemas through an ASG.

    This is the main entry point for schema inference.
    Modifies the ASG in place and returns it.
    """
    propagator = SchemaPropagator()
    return propagator.process(asg)
