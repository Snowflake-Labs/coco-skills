"""
Spark AST Parser - Extract DataFrame operations from Python code.

This module uses Python's built-in `ast` module with Structural Pattern Matching
(Python 3.10+) to identify Spark DataFrame operations and build an Abstract
Semantic Graph (ASG).

The module is organized into several components:
- operation_counter.py: Simple operation counting utilities
- import_handler.py: Import statement analysis (mixin)
- function_extractor.py: Function definition analysis (mixin)
- This file: Main SparkASTParser class and public API
"""

from __future__ import annotations

import ast
from pathlib import Path
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, ClassVar

from asg_pyspark.parser.astroid_inference import AstroidInferenceEngine
from asg_pyspark.parser.function_extractor import FunctionExtractorMixin
from asg_pyspark.parser.import_handler import ImportHandlerMixin
from asg_pyspark.parser.operation_counter import (
    OperationCount,
    SparkOperationCounter,
    analyze_file,
    count_operations,
    count_operations_in_file,
)
from warp_core.symbol_table import FunctionSignature, SymbolTable, TypeTracker
from asg_pyspark.parser.sql_schema_extractor import extract_sql_schema
from warp_core.ir.pyspark_models import SourceLocation, GLOBAL_SCOPE, AnalysisWarning, WarningSeverity
from warp_core.ir.pyspark_models import (
    ControlNode, ControlType, ExitStrategy, LoopType, ControlLogic, ControlBranch,
    ExtractionMetadata, WindowSpecDefinition, ParsingReport, ParsedFileInfo,
    SyntaxSummary, UnderstandingSummary, InferenceSummary, TypeInferenceWarning,
)

from warp_core.ir.pyspark_models import ASG
from warp_core.pandas_functions import (
    ALL_PANDAS_NAMES,
    DF_RETURNING as PD_DF_RETURNING,
)
from warp_core.spark_functions import (
    ALL_FUNCTION_NAMES,
    ALL_SPARK_NAMES,
    DF_RETURNING_METHODS,
)

# Re-export from operation_counter for backward compatibility
__all__ = [
    "OperationCount",
    "SparkOperationCounter",
    "count_operations",
    "count_operations_in_file",
    "analyze_file",
    "SparkASTParser",
    "parse_spark_file",
    "parse_spark_directory",
]


# =============================================================================
# Databricks Notebook Detection and Preprocessing — delegated to notebook_utils
# =============================================================================

from asg_pyspark.parser.notebook_utils import (
    DBX_NOTEBOOK_HEADER,
    is_databricks_notebook,
    has_spark_session_creation,
    has_main_guard,
    detect_source_type,
    is_entry_point,
    detect_entry_point_reason,
    find_main_guard_lineno,
    fix_indentation_errors,
    preprocess_source,
    extract_notebook_dependencies,
    extract_udf_definitions,
    count_display_outputs,
    extract_notebook_description,
    extract_widget_parameters,
    _resolve_notebook_path,
)


# =============================================================================
# ASG Parser - Full Implementation
# =============================================================================


class CallType(Enum):
    """Classification of method/function calls for unified processing."""
    
    # Spark DataFrame operations
    SPARK_READ = auto()       # spark.read.*, spark.table(), spark.sql()
    SPARK_WRITE = auto()      # df.write.*, df.save()
    SPARK_TRANSFORM = auto()  # df.filter(), df.select(), df.withColumn()
    
    # User-defined calls
    USER_FUNCTION = auto()    # my_func(df) - direct function call
    USER_METHOD = auto()      # obj.method(df) - method call on instance
    
    # Column expressions (not DataFrame operations)
    COLUMN_EXPR = auto()      # F.col(), F.lit(), col().alias()
    
    # Window specifications
    WINDOW_SPEC = auto()      # Window.partitionBy().orderBy()
    
    # Ignored calls
    BUILTIN = auto()          # print(), len(), range()
    UNKNOWN = auto()          # Cannot classify


class SparkASTParser(ImportHandlerMixin, FunctionExtractorMixin, ast.NodeVisitor):
    """
    Parse a Spark Python script and build an Abstract Semantic Graph (ASG).

    The ASG contains:
    - data_in: Data inputs (tables, files)
    - data_out: Data outputs (tables, files)
    - transformations: DataFrame operations with lineage
    """

    # =========================================================================
    # Lifecycle — init, parse, entry points
    # =========================================================================

    # Class-level counters for globally unique IDs across all parser instances
    _global_node_counter: ClassVar[int] = 0

    _global_control_counter: ClassVar[int] = 0

    @classmethod
    def reset_global_counters(cls) -> None:
        """Reset global ID counters (call before parsing a new project)."""
        cls._global_node_counter = 0
        cls._global_control_counter = 0

    def __init__(self, workload_root: Path | None = None) -> None:
        self.data_in: list[dict[str, Any]] = []
        self.data_out: list[dict[str, Any]] = []
        self.transformations: list[dict[str, Any]] = []

        # Structured imports: module -> {alias, imported_names, type, has_source}
        self.imports: dict[str, dict[str, Any]] = {}

        self.functions: list[dict[str, Any]] = []

        # UDF registry: function_name -> return_type (e.g., "calculate_score" -> "double")
        self._udf_registry: dict[str, str] = {}

        # Symbol table for lineage tracking
        self.symbol_table = SymbolTable()

        # Astroid inference engine for resolving variables
        self._inference_engine: AstroidInferenceEngine | None = None

        # Type inference tracking for transformation detection
        self._inference_stats = {"inferred": 0, "name_match": 0, "excluded": 0}
        self._inference_warnings: list[dict[str, Any]] = []

        # Tracking
        self._current_function: str | None = None
        self._current_class: str | None = None  # Track current class for scope
        self._node_counter = 0
        self._current_assignment_target: str | None = None  # LHS of current assignment
        self._last_node_id: str | None = None  # Last node created (for assignment tracking)
        self._partial_readers: dict[str, dict] = {}  # var_name -> partial reader info

        # Track nodes processed by chain unrolling to avoid duplicates
        self._processed_chain_nodes: set[int] = set()
        
        # Control flow nodes (if/match/for/while/try/with)
        self.control_nodes: list[dict[str, Any]] = []
        self._control_counter = 0

        # Call-sites for cross-function lineage resolution
        # Each entry: {function_name, argument_bindings, output_variable, line_number}
        self.call_sites: list[dict[str, Any]] = []

        # Current file being parsed (relative path from workload root)
        self._current_filepath: str = "<unknown>"

        # Workload root for source availability detection
        self._workload_root: Path | None = workload_root
        
        # Source code of current file (set during parse)
        self._source_code: str | None = None

    def _extract_and_register_imports(self, tree: ast.Module) -> None:
        """
        Extract import statements and register aliases in TypeTracker.
        
        Handles:
        - from pyspark.sql import functions as F
        - from pyspark.sql.functions import col, lit
        - import pyspark.sql.functions as F
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    name = alias.asname or alias.name
                    # from pyspark.sql import functions as F
                    if alias.name == "functions" and "pyspark" in module:
                        TypeTracker.register_import(name, "pyspark.sql.functions")
                    # from pyspark.sql.functions import col, lit
                    elif module == "pyspark.sql.functions":
                        TypeTracker.register_import(name, "pyspark.sql.functions")
                    # from pyspark.sql.types import ...
                    elif "pyspark.sql.types" in module:
                        TypeTracker.register_import(name, "pyspark.sql.types")
                    # from pyspark.sql.window import Window
                    elif "window" in module.lower():
                        TypeTracker.register_import(name, "pyspark.sql.window")
            
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[-1]
                    # import pyspark.sql.functions as F
                    if "pyspark.sql.functions" in alias.name:
                        TypeTracker.register_import(name, "pyspark.sql.functions")

    def parse(self, source_code: str, filename: str = "<unknown>") -> ASG:
        """
        Parse Python source code and return an ASG.

        Args:
            source_code: Python source code as string
            filename: Name of the source file (relative path from workload root)

        Returns:
            ASG model with data_in, data_out, and transformations
        """
        from warp_core.ir.pyspark_models import ASG, DataSink, DataSource, ImportEntry, TransformationNode

        # Set current filepath for location tracking
        self._current_filepath = filename

        # Initialize astroid inference engine for variable resolution
        self._inference_engine = AstroidInferenceEngine(source_code)
        self._source_code = source_code  # Save for expression resolution

        tree = ast.parse(source_code)
        self.tree = tree  # Save for later use in cross-function lineage resolution
        
        # Extract and register import aliases for type inference
        self._extract_and_register_imports(tree)
        
        self.visit(tree)

        # Detect UDF registrations: udf(function_name, ReturnType())
        self._detect_udf_registrations(tree)

        # Apply UDF type information to functions
        self._apply_udf_types()

        # Resolution phase: fill in inferred_schema_origin for function arguments
        # Must happen before converting to Pydantic models (uses raw dicts)
        self._resolve_function_argument_origins(tree)

        # Propagate columns from control conditions to data sources
        # Must happen before converting to Pydantic models
        self._propagate_control_columns_to_sources()
        
        # Resolve branch steps (link execution_calls/transformations to branches)
        self._resolve_branch_steps()

        # Remove control nodes where no branch has any steps
        self.control_nodes = [
            cn for cn in self.control_nodes
            if any(b.get("steps") for b in cn.get("branches", []))
        ]

        # Resolve convergence points (identify first node after control blocks)
        self._resolve_convergence_points()
        
        # Convert to Pydantic models
        data_in = [DataSource(**s) for s in self.data_in]
        data_out = [DataSink(**s) for s in self.data_out]
        transformations = [TransformationNode(**t) for t in self.transformations]

        # Convert imports to ImportEntry models
        imports_models: dict[str, ImportEntry] = {}
        for module, entry in self.imports.items():
            imports_models[module] = ImportEntry(
                alias=entry["alias"],
                imported_names=entry["imported_names"],
                type=entry["type"],
                has_source=entry.get("has_source", True),
            )

        # Extract app name if present
        app_name = self._extract_app_name(tree)

        # Convert function dicts to FunctionDefinition models
        from warp_core.ir.pyspark_models import FunctionArgument, FunctionDefinition, FunctionReturn

        function_models = []
        for func in self.functions:
            arguments = [
                FunctionArgument(
                    name=arg["name"],
                    inferred_type=arg["inferred_type"],
                    inferred_schema_origin=arg["inferred_schema_origin"],
                    is_optional=arg["is_optional"],
                )
                for arg in func.get("arguments", [])
            ]

            returns = None
            if func.get("returns"):
                from warp_core.ir.pyspark_models import ReturnRefType

                ref_type_str = func["returns"].get("ref_type", "void")
                returns = FunctionReturn(
                    ref_type=ReturnRefType(ref_type_str),
                    ref_id=func["returns"].get("ref_id"),
                    inferred_type=func["returns"]["inferred_type"],
                )

            # Create location for the function
            # For functions, scope is the containing class (if any)
            func_scope = func.get("containing_class")
            line_start = func["line_start"]
            line_end = func["line_end"] or line_start
            func_location = SourceLocation(
                pathfile=filename,
                scope=func_scope,
                span=f"{line_start}:1-{line_end}:1",
            )

            function_models.append(
                FunctionDefinition(
                    name=func["name"],
                    containing_class=func.get("containing_class"),
                    source_file=filename,
                    location=func_location,
                    arguments=arguments,
                    returns=returns,
                )
            )

        # Create SourceFile entry with imports and entrypoint detection
        from warp_core.ir.pyspark_models import SourceFile, SourceType
        
        # Detect source type and entrypoint status
        source_code = self._source_code or ""
        is_notebook = is_databricks_notebook(source_code)
        src_type = detect_source_type(source_code, is_notebook)
        ep_reason = detect_entry_point_reason(source_code, is_notebook)
        is_entry = ep_reason is not None
        has_spark = has_spark_session_creation(source_code)

        # Determine line number of the entry point construct.
        # Notebooks: always None (convention = line 1, no calculation needed).
        # Python main_guard: AST scan for the if __name__ == '__main__' line.
        # spark_session_creation: no specific line to anchor to, use None.
        ep_lineno: int | None = None
        if ep_reason == "main_guard":
            ep_lineno = find_main_guard_lineno(source_code)

        source_file_entry = SourceFile(
            path=filename,
            imports=imports_models,
            source_type=SourceType(src_type),
            is_entry_point=is_entry,
            entry_point_reason=ep_reason,
            entry_point_lineno=ep_lineno,
            has_spark_session=has_spark,
        )

        # Extract execution calls from captured call sites
        from asg_pyspark.parser.call_extractor import extract_execution_calls

        execution_calls = extract_execution_calls(
            call_sites=self.call_sites,
            functions=self.functions,
            source_file=filename,
            imports=self.imports,
        )

        execution_calls = self._expand_higher_order_calls(execution_calls)

        # Convert control nodes to Pydantic models
        control_node_models = [ControlNode(**cn) for cn in self.control_nodes]
        
        # Export window specs from SymbolTable for SQL generation
        window_spec_models = []
        for key, pyspark_expr in SymbolTable._global_window_specs.items():
            if "::" in key:
                scope, var_name = key.split("::", 1)
            else:
                scope, var_name = "", key
            window_spec_models.append(WindowSpecDefinition(
                scope=scope,
                variable_name=var_name,
                pyspark_expr=pyspark_expr,
            ))
        
        return ASG(
            extraction_metadata=ExtractionMetadata(
                source_file=filename,
                app_name=app_name,
            ),
            source_files=[source_file_entry],
            functions=function_models,
            execution_calls=execution_calls,
            data_in=data_in,
            data_out=data_out,
            transformations=transformations,
            control_nodes=control_node_models,
            window_specs=window_spec_models,
        )

    def parse_file(self, file_path: str | Path) -> ASG:
        """Parse a Python file and return an ASG."""
        path = Path(file_path)
        source_code = path.read_text()
        return self.parse(source_code, filename=str(path))

    def _extract_app_name(self, tree: ast.Module) -> str | None:
        """Extract the Spark app name from SparkSession.builder.appName(...)."""
        for node in ast.walk(tree):
            match node:
                case ast.Call(func=ast.Attribute(attr="appName"), args=[ast.Constant(value=name)]):
                    return str(name)
        return None


    # =========================================================================
    # Location & ID utilities
    # =========================================================================

    def _get_current_scope(self) -> str:
        """
        Get the current scope as 'ClassName.function_name' or just 'function_name'.
        Returns GLOBAL_SCOPE ("<global>") if at module level.
        """
        parts = []
        if self._current_class:
            parts.append(self._current_class)
        if self._current_function:
            parts.append(self._current_function)
        return ".".join(parts) if parts else GLOBAL_SCOPE

    def _make_span(
        self,
        line_start: int,
        col_start: int,
        line_end: int,
        col_end: int,
    ) -> str:
        """Create span string in format 'start_line:start_col-end_line:end_col'."""
        return f"{line_start}:{col_start}-{line_end}:{col_end}"

    def _make_location(self, node: ast.AST) -> SourceLocation:
        """
        Create a SourceLocation from an AST node.

        Uses the node's line and column information. Column offsets in Python AST
        are 0-indexed, so we add 1 to make them 1-indexed.
        """
        line_start = getattr(node, "lineno", 1)
        col_start = getattr(node, "col_offset", 0) + 1  # Convert to 1-indexed
        line_end = getattr(node, "end_lineno", line_start)
        col_end = getattr(node, "end_col_offset", col_start)
        if col_end == 0:
            col_end = col_start

        return SourceLocation(
            pathfile=self._current_filepath,
            scope=self._get_current_scope(),
            span=self._make_span(line_start, col_start, line_end, col_end),
        )

    def _is_test_file(self) -> bool:
        """Check if current file is a test file based on path."""
        if not self._current_filepath:
            return False
        path_lower = self._current_filepath.lower()
        return "/tests/" in path_lower or "/test/" in path_lower or path_lower.startswith("tests/") or path_lower.startswith("test/")

    def _make_location_dict(self, node: ast.AST) -> dict[str, Any]:
        """
        Create a location dict from an AST node (for use in raw dicts before Pydantic conversion).
        """
        return self._make_location(node).model_dump()

    def _make_location_from_range(
        self,
        line_start: int,
        line_end: int,
        col_start: int = 1,
        col_end: int | None = None,
    ) -> dict[str, Any]:
        """
        Create a location dict from explicit line/column ranges.
        Used for chain elements where line info is pre-extracted.
        """
        return {
            "pathfile": self._current_filepath,
            "scope": self._get_current_scope(),
            "span": self._make_span(line_start, col_start, line_end, col_end or col_start),
        }

    def _next_id(self, prefix: str = "node") -> str:
        """Generate a unique node ID (globally unique across all parsers)."""
        SparkASTParser._global_node_counter += 1
        self._node_counter = SparkASTParser._global_node_counter  # Keep in sync
        return f"{prefix}_{SparkASTParser._global_node_counter:03d}"

    def _next_control_id(self) -> str:
        """Generate a unique control node ID (globally unique across all parsers)."""
        SparkASTParser._global_control_counter += 1
        self._control_counter = SparkASTParser._global_control_counter  # Keep in sync
        return f"ctrl_{SparkASTParser._global_control_counter:03d}"


    # =========================================================================
    # Core AST visitors — Assign, Return, Call
    # =========================================================================

    # Import handling methods are inherited from ImportHandlerMixin
    # Function extraction methods are inherited from FunctionExtractorMixin

    def visit_Assign(self, node: ast.Assign) -> None:
        """
        Track variable assignments for lineage.

        When we see `df = spark.read.table(...)`, we need to:
        1. Process the RHS to create nodes
        2. Register the variable name in the symbol table

        For chained calls like `df.filter().withColumn()`, we unroll
        them into atomic nodes with proper lineage linking.
        """
        # Get the assignment target (LHS)
        # Handle both single (a = expr) and multiple (a = b = expr) assignments
        target_names: list[str] = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                target_names.append(target.id)
        
        # Use first target as primary
        target_name = target_names[0] if target_names else None

        # Store target for use in visit_Call
        self._current_assignment_target = target_name
        self._last_node_id = None

        # Check if this is a Spark method chain or operation that should be unrolled
        if isinstance(node.value, ast.Call) and self._is_spark_chain(node.value):
            self._process_spark_chain(node.value)
            # Save the node ID before any nested processing overwrites it
            assignment_node_id = self._last_node_id
        elif isinstance(node.value, ast.Call) and self._is_spark_operation(node.value):
            # Single Spark operation (e.g., df.join(...)) - also use _process_spark_chain
            # This avoids generic_visit which would overwrite _last_node_id
            self._process_spark_chain(node.value)
            assignment_node_id = self._last_node_id
        else:
            # Visit the RHS normally (this will create nodes and set _last_node_id)
            self.visit(node.value)
            assignment_node_id = self._last_node_id

        # Infer and register the type of the RHS expression
        if target_name:
            inferred_type = self._infer_expression_type(node.value)
            if inferred_type:
                scope = self._get_current_scope()
                TypeTracker.register_type(scope, target_name, inferred_type)
            # DB cursor detection: cursor = conn.cursor() / session.cursor()
            # Registers "DB_CURSOR" so the execute() handler can do deterministic
            # matching instead of relying purely on variable-name heuristics.
            elif (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "cursor"
            ):
                TypeTracker.register_type(
                    self._get_current_scope(), target_name, "DB_CURSOR"
                )
        
        # Register the assignment in symbol table
        if target_name and assignment_node_id:
            self.symbol_table.set(target_name, assignment_node_id)
            # Register sources globally for cross-file resolution
            if assignment_node_id.startswith("in_"):
                source_entry = next(
                    (s for s in self.data_in if s.get("id") == assignment_node_id), None
                )
                if source_entry:
                    # Use name, path, query, or variable name as source_name (fallback chain)
                    source_name = (
                        source_entry.get("name") 
                        or source_entry.get("path") 
                        or source_entry.get("query", "")[:50] if source_entry.get("query") else None
                        or f"var:{target_name}"
                    )
                    SymbolTable.register_source(
                        var_name=target_name,
                        source_id=assignment_node_id,
                        source_name=source_name or f"var:{target_name}",
                        file=self._current_filepath,
                    )
            # Register transformation assignments globally for cross-function lineage
            elif assignment_node_id.startswith("tx_") and self._current_function:
                SymbolTable.register_var_assignment(
                    scope=self._current_function,
                    var_name=target_name,
                    node_id=assignment_node_id,
                )
        elif target_name and isinstance(node.value, ast.Call):
            # Check if this is a call to a known function
            # If so, register the function's return as this variable's source
            func_return_id = self._get_function_call_return_id(node.value)
            if func_return_id:
                self.symbol_table.set(target_name, func_return_id)
                # Register transformation assignments globally for cross-function lineage
                if func_return_id.startswith("tx_") and self._current_function:
                    SymbolTable.register_var_assignment(
                        scope=self._current_function,
                        var_name=target_name,
                        node_id=func_return_id,
                    )
                # If the function returns a source, register it globally
                elif func_return_id.startswith("in_"):
                    # Try to find source details locally first
                    source_entry = next(
                        (s for s in self.data_in if s.get("id") == func_return_id), None
                    )
                    if source_entry:
                        source_name = (
                            source_entry.get("name") 
                            or source_entry.get("path") 
                            or source_entry.get("query", "")[:50] if source_entry.get("query") else None
                            or f"var:{target_name}"
                        )
                    else:
                        # Source is from another file (cross-file function call)
                        # Use variable name as source_name
                        source_name = f"var:{target_name}"
                    
                    SymbolTable.register_source(
                        var_name=target_name,
                        source_id=func_return_id,
                        source_name=source_name,
                        file=self._current_filepath,
                    )
            else:
                # Fallback: if the function reads a table and we have the table name as argument,
                # try to find the source by table name
                # This handles: df_products = _read_table("products_data")
                source_id = self._resolve_table_read_call(node.value, target_name)
                if source_id:
                    self.symbol_table.set(target_name, source_id)

        # Detect Window spec assignments: window_spec = Window.orderBy(...)
        # Store the raw expression - will be converted to SQL lazily when needed
        if target_name and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Attribute):
                value = node.value.func.value
                is_window = False
                # Check for Window.XXX(...)
                if isinstance(value, ast.Name) and value.id == "Window":
                    is_window = True
                elif isinstance(value, ast.Call):
                    # Could be chained: Window.partitionBy(...).orderBy(...)
                    inner = value
                    while isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                        inner = inner.func.value
                    if isinstance(inner, ast.Name) and inner.id == "Window":
                        is_window = True
                
                if is_window:
                    # Store raw expression string - converted to SQL on demand
                    # Support both function scope and global scope (notebooks)
                    scope = self._current_function or GLOBAL_SCOPE
                    expr_str = ast.unparse(node.value)
                    SymbolTable.register_window_spec(
                        scope=scope,
                        var_name=target_name,
                        sql_definition=expr_str,  # Will be converted later
                    )

        # Detect partial readers (spark.read.option()... without terminal)
        if target_name and isinstance(node.value, ast.Call) and self._is_partial_reader(node.value):
            # Store partial reader info for later completion
            reader_info = {
                "file": self._current_filepath,
                "location": self._make_location_dict(node),
            }
            self._partial_readers[target_name] = reader_info
            # Also register in file-scoped key
            file_key = f"{self._current_filepath}:{target_name}"
            self._partial_readers[file_key] = reader_info

        # Capture string literal assignments for variable resolution
        # e.g., table_name = 'my_table' or path = "s3://bucket/file"
        if target_name and isinstance(node.value, (ast.Constant, ast.JoinedStr)):
            scope = self._current_function or GLOBAL_SCOPE
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                # Simple string literal: name = 'value'
                SymbolTable.register_string_literal(scope, target_name, node.value.value)
            elif isinstance(node.value, ast.JoinedStr):
                # F-string: name = f'prefix/{var}'
                # Store the raw f-string expression for later analysis
                try:
                    fstring_repr = ast.unparse(node.value)
                    SymbolTable.register_string_literal(scope, target_name, f"runtime:{fstring_repr}")
                except Exception:
                    pass

        # Reset
        self._current_assignment_target = None

    def visit_Return(self, node: ast.Return) -> None:
        """
        Handle return statements with Spark DataFrame operations.

        For `return df.filter().withColumn()` (chains) AND
        `return df.withColumn(...)` (single ops), we process them
        as transformations, creating proper tx nodes.

        For `return spark.read.format().load()` (data sources),
        we capture the data source ID as the return value.

        For calls that don't generate tx nodes (non-Spark calls),
        we use ref_type="expression" to indicate it's not traceable.
        """
        if node.value is None:
            return

        # Check if the return value is a Spark operation (chain OR single op)
        if isinstance(node.value, ast.Call) and self._is_spark_operation(node.value):
            # Save previous ID to detect if new nodes were created
            prev_last_id = self._last_node_id

            # Process the Spark operation (works for single ops too)
            self._process_spark_chain(node.value)

            # Did we generate new transformation nodes?
            if self._last_node_id and self._last_node_id != prev_last_id:
                # YES → transformation with valid ref_id
                self._update_function_return(
                    ref_type="transformation",
                    ref_id=self._last_node_id,
                    inferred_type="pyspark.sql.DataFrame",
                )
            else:
                # NO → expression (should not happen with proper chain processing)
                self._update_function_return(
                    ref_type="expression", ref_id=None, inferred_type="pyspark.sql.DataFrame"
                )
        elif isinstance(node.value, ast.Call) and self._is_read_chain(node.value):
            # It's a data source read (spark.read.format().load())
            # Save previous ID to detect if new data source was created
            prev_last_id = self._last_node_id
            
            # Visit the call to process the data source
            self.visit(node.value)
            
            # Did we generate a new data source node?
            if self._last_node_id and self._last_node_id != prev_last_id:
                # YES → data_source with valid ref_id (in_XXX)
                self._update_function_return(
                    ref_type="data_source",
                    ref_id=self._last_node_id,
                    inferred_type="pyspark.sql.DataFrame",
                )
            else:
                # Fallback - keep existing data_source type
                self._update_function_return(
                    ref_type="data_source", ref_id=None, inferred_type="pyspark.sql.DataFrame"
                )
        elif isinstance(node.value, ast.Call):
            # It's a call but not a Spark operation (e.g., return some_func(df))
            self._update_function_return(
                ref_type="expression", ref_id=None, inferred_type="Unknown"
            )
            self.visit(node.value)
        elif isinstance(node.value, ast.Name):
            # Return of a variable (e.g., return df)
            # Check if the variable is bound to a data source or transformation
            var_name = node.value.id
            
            # Look up in SymbolTable with file-scoped key first, then global
            file_key = f"{self._current_filepath}:{var_name}"
            binding = SymbolTable._global_sources.get(file_key) or SymbolTable._global_sources.get(var_name)
            
            if binding and binding.source_id:
                if binding.source_id.startswith("in_"):
                    # Variable is bound to a data source
                    self._update_function_return(
                        ref_type="data_source",
                        ref_id=binding.source_id,
                        inferred_type="pyspark.sql.DataFrame",
                    )
                elif binding.source_id.startswith("tx_"):
                    # Variable is bound to a transformation
                    self._update_function_return(
                        ref_type="transformation",
                        ref_id=binding.source_id,
                        inferred_type="pyspark.sql.DataFrame",
                    )
                else:
                    # Visit normally
                    self.visit(node.value)
            else:
                # Visit normally
                self.visit(node.value)
        else:
            # Visit normally (literals, etc.)
            self.visit(node.value)

    def _update_function_return(
        self, ref_type: str, ref_id: str | None, inferred_type: str
    ) -> None:
        """
        Update the return metadata for the current function.

        Called by visit_Return when it processes a Spark chain to link
        the function's return contract to the last transformation node.
        """
        if not self._current_function:
            return

        # Find the current function in our list
        for func in self.functions:
            if func["name"] == self._current_function:
                if func.get("returns"):
                    current_ref_type = func["returns"].get("ref_type")
                    # Don't downgrade data_source to expression
                    # (spark.read chains produce data sources, not transformations)
                    if current_ref_type == "data_source" and ref_type == "expression":
                        # Keep data_source, just update inferred_type
                        if func["returns"]["inferred_type"] == "Unknown":
                            func["returns"]["inferred_type"] = "pyspark.sql.DataFrame"
                    else:
                        func["returns"]["ref_type"] = ref_type
                        func["returns"]["ref_id"] = ref_id
                        # Only update inferred_type if it was Unknown
                        if func["returns"]["inferred_type"] == "Unknown":
                            func["returns"]["inferred_type"] = inferred_type
                break

    def visit_Call(self, node: ast.Call) -> None:
        """Visit function calls and detect Spark operations."""

        # Skip nodes already processed by chain unrolling
        if id(node) in self._processed_chain_nodes:
            return

        # Detect control flow usage (.rdd, .count, .isEmpty, .first, .collect, .take)
        self._detect_control_flow_usage(node)

        match node.func:
            # =========================================================
            # SOURCE DETECTION
            # =========================================================

            # spark.read.table("table_name")
            case ast.Attribute(value=ast.Attribute(value=_, attr="read"), attr="table"):
                table_name = self._extract_string_arg(node.args, 0)
                self._add_data_in(node, "table", name=table_name)

            # spark.read.csv/parquet/json("path") or partial_reader.csv("path")
            case ast.Attribute(attr=fmt) if (
                fmt in ("csv", "parquet", "json")
                and (self._is_read_chain(node) or self._is_partial_reader_completion(node))
            ):
                path = self._extract_string_arg(node.args, 0, capture_runtime=True)
                self._add_data_in(node, fmt, path=path)

            # spark.read.jdbc(url, table, ...) - JDBC data source
            case ast.Attribute(attr="jdbc") if self._is_read_chain(node):
                url = self._extract_string_arg(node.args, 0, capture_runtime=True)
                table = self._extract_string_arg(node.args, 1, capture_runtime=True)
                for kw in node.keywords:
                    if kw.arg == "url" and not url:
                        url = self._extract_string_arg([kw.value], 0, capture_runtime=True)
                    elif kw.arg == "table" and not table:
                        table = self._extract_string_arg([kw.value], 0, capture_runtime=True)
                
                node_id = self._add_data_in(
                    node, "jdbc", name=table, path=url, query=table,
                )
                if self._current_assignment_target:
                    SymbolTable.register_source(
                        var_name=self._current_assignment_target,
                        source_id=node_id,
                        source_name=table or f"var:{self._current_assignment_target}",
                        file=getattr(self, '_current_filepath', ''),
                    )

            # spark.read.format("X").option(...).load("path")
            case ast.Attribute(attr="load") if self._is_read_chain(node):
                path = self._extract_string_arg(node.args, 0, capture_runtime=True)
                raw_fmt = self._extract_chain_format(node)
                normalized_type, original_format = self._normalize_format_to_type(raw_fmt)
                options = self._extract_chain_options(node)
                name = options.get("dbtable") or options.get("table") or options.get("collection")
                query = options.get("query")
                if not name and query:
                    name = self._extract_table_from_sql(query)
                if not path:
                    path = options.get("path")
                self._add_data_in(
                    node, normalized_type,
                    format=original_format, name=name, path=path, query=query,
                )

            # spark.sql("SELECT ... FROM table") or DML
            case ast.Attribute(attr="sql") if self._is_spark_session_call(node):
                sql_query = self._extract_string_arg(node.args, 0, capture_runtime=True)
                # Keep original table-name extraction path intact to avoid
                # accidentally changing which nodes are created / their names.
                table_name = self._extract_table_from_sql(sql_query) if sql_query else None
                # Additionally harvest SELECT-list columns as low-confidence schema.
                # This is purely additive: existing node creation logic is unchanged.
                sql_select_cols: list[dict] | None = None
                if sql_query:
                    sql_result = extract_sql_schema(sql_query)
                    # Skip SELECT * (no useful column info) and f-string placeholders.
                    if not sql_result.has_star and sql_result.output_columns:
                        candidates = [
                            c for c in sql_result.output_columns
                            if c and c != "*" and "{" not in c
                        ]
                        if candidates:
                            sql_select_cols = [
                                {
                                    "name": c,
                                    "inferred_type": "UNKNOWN",
                                    "source": "select",
                                    "confidence": "low",
                                }
                                for c in candidates
                            ]
                if sql_query and self._is_dml_query(sql_query):
                    self._add_data_out(node, "other", name=table_name, format="sql_dml", mode="overwrite")
                else:
                    self._add_data_in(
                        node, "sql", name=table_name, query=sql_query,
                        **({"inferred_columns": sql_select_cols} if sql_select_cols else {}),
                    )

            # cursor.execute("INSERT INTO ...") / cursor.executemany(...)
            # Handles DBAPI2 and Snowflake connector cursor writes.
            # Primary check: TypeTracker knows the caller is a DB_CURSOR (deterministic).
            # Fallback: conventional cursor variable names (covers cursor-as-parameter cases).
            case ast.Attribute(attr=_exec_method) if _exec_method in ("execute", "executemany"):
                _caller_name: str | None = None
                match node.func:
                    case ast.Attribute(value=ast.Name(id=_n)):
                        _caller_name = _n                        # cursor.execute(...)
                    case ast.Attribute(value=ast.Attribute(attr=_n)):
                        _caller_name = _n                        # self.cursor.execute(...)

                _is_cursor = False
                if _caller_name:
                    _inferred = TypeTracker.resolve_type(
                        self._get_current_scope(), _caller_name
                    )
                    _is_cursor = (_inferred == "DB_CURSOR")
                    if not _is_cursor:
                        _is_cursor = _caller_name in {
                            "cursor", "cur", "curs", "conn",
                            "db", "db_cursor", "snowflake_cursor",
                        }

                if _is_cursor:
                    _sql = self._extract_string_arg(node.args, 0, capture_runtime=True)
                    if _sql and self._is_dml_query(_sql):
                        _table = self._extract_table_from_sql(_sql)
                        self._add_data_out(
                            node, "other",
                            name=_table,
                            format="sql_dml",
                            mode="overwrite",
                        )

            # spark.table("table_name") - direct table read
            case ast.Attribute(attr="table") if self._is_spark_session_call(node):
                table_name = self._extract_string_arg(node.args, 0, capture_runtime=True)
                self._add_data_in(node, "table", name=table_name)

            # spark.createDataFrame(...) - in-memory data source
            case ast.Attribute(attr="createDataFrame"):
                is_empty = (
                    bool(node.args)
                    and isinstance(node.args[0], ast.List)
                    and len(node.args[0].elts) == 0
                )
                df_name = f"df:{self._current_assignment_target}" if self._current_assignment_target else None
                node_id = self._add_data_in(
                    node, "memory", name=df_name, is_empty_fallback=is_empty,
                )
                if self._current_assignment_target:
                    SymbolTable.register_source(
                        var_name=self._current_assignment_target,
                        source_id=node_id,
                        source_name=f"memory:{self._current_assignment_target}",
                        file=self._current_filepath,
                    )

            # =========================================================
            # DATA_OUT DETECTION
            # =========================================================

            # df.write.saveAsTable("table_name")
            case ast.Attribute(attr="saveAsTable"):
                self._add_data_out(
                    node, "table",
                    name=self._extract_string_arg(node.args, 0, capture_runtime=True),
                    mode=self._extract_write_mode(node),
                    source_id=self._resolve_write_source(node),
                )

            # df.write.parquet("path") / df.write.csv("path") — write terminators
            case ast.Attribute(attr=fmt) if (
                fmt in ("parquet", "csv")
                and not self._is_read_chain(node)
                and self._is_write_chain(node)
            ):
                self._add_data_out(
                    node, fmt,
                    path=self._extract_string_arg(node.args, 0, capture_runtime=True),
                    mode=self._extract_write_mode(node),
                    source_id=self._resolve_write_source(node),
                )

            # df.write.format("parquet").mode("overwrite").save("path")
            case ast.Attribute(attr="save") if self._is_write_chain(node):
                raw_fmt = self._extract_chain_format(node)
                normalized_type, original_format = self._normalize_format_to_type(raw_fmt)
                self._add_data_out(
                    node, normalized_type,
                    format=original_format,
                    path=self._extract_string_arg(node.args, 0, capture_runtime=True),
                    mode=self._extract_write_mode(node),
                    source_id=self._resolve_write_source(node),
                )

            # df.write.jdbc(url, table, properties=..., mode="append")
            case ast.Attribute(attr="jdbc") if self._is_write_chain(node):
                table_name = self._extract_string_arg(node.args, 1, capture_runtime=True)
                mode = "append"
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = str(kw.value.value)
                        break
                if not mode or mode == "append":
                    chain_mode = self._extract_write_mode(node)
                    if chain_mode != "overwrite":
                        mode = chain_mode
                self._add_data_out(
                    node, "jdbc",
                    format="jdbc", name=table_name, mode=mode,
                    source_id=self._resolve_write_source(node),
                )

            # =========================================================
            # TRANSFORMATION DETECTION
            # =========================================================

            case ast.Attribute(attr=method_name) if (
                method_name in self._TRANSFORM_METHODS 
                and not self._is_ignored_call(node)
                and self._is_spark_operation(node)
            ):
                self._add_transformation(node, method_name)

        # =========================================================
        # UNIFIED CALL PROCESSING (for cross-function lineage)
        # =========================================================
        # Process the call through the unified classification system
        # This handles user functions, methods, and other call types
        self._process_call(node)

        # Continue visiting child nodes
        self.generic_visit(node)


    # =========================================================================
    # Data I/O helpers — read/write extraction
    # =========================================================================

    @staticmethod
    def _derive_name_from_path(path: str | None) -> str | None:
        """Extract a human-readable name from a file/S3/HDFS path.

        Handles literal paths and runtime expressions:
          "s3://bucket/folder/my_table"                          -> "my_table"
          "runtime:f'{prefix}/rest_data_sales'"                  -> "rest_data_sales"
          "runtime:'{}'.format(base.replace('table_name','tbl'))" -> "tbl"
          "/mnt/data/output.parquet"                             -> "output"
          "Uninferable"                                          -> None
        """
        import re as _re

        if not path or path in ("Uninferable", "None", "unknown"):
            return None

        clean = path
        if clean.startswith("runtime:"):
            clean = clean[len("runtime:"):]

        # ── .replace('table_name', 'logical_name') extractor ────────────────
        # Detects patterns like:
        #   '{}'.format(s3_path.replace('table_name', 'vid_base_table'))
        #   processed_s3_path.replace('table_name', 'step-one')
        # The second argument of .replace() IS the logical table name.
        replace_match = _re.search(
            r"""\.replace\(\s*['"]table_name['"]\s*,\s*['"]([^'"]+)['"]\s*\)""",
            clean,
        )
        if replace_match:
            return replace_match.group(1) or None

        # Strip .format(...) suffix from old-style string formatting
        fmt_idx = clean.find(".format(")
        if fmt_idx != -1:
            clean = clean[:fmt_idx]

        # Strip f-string prefix only when it genuinely precedes a quote character.
        # Using str.strip("f'\"") would incorrectly eat the leading 'f' from
        # plain variable names like 'finalFilePath' → 'inalFilePath'.
        if clean.startswith(("f'", 'f"')):
            clean = clean[1:]
        # Strip surrounding quote characters
        clean = clean.strip("'\"")

        # Strip trailing path separators so "folder/name/" → last segment is "name"
        clean = clean.rstrip("/\\")

        # Take last segment after / or \
        segment = clean.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        # Strip leading {variable} format placeholders (e.g. "{s3_path}my_table" → "my_table",
        # "{}dynamo_write_df" → "dynamo_write_df").  Only strip a single placeholder to avoid
        # over-stripping patterns like "{db}_{table}" where both parts are variables.
        import re as _re_seg
        segment = _re_seg.sub(r"^\{[^}]*\}", "", segment)

        # Remove file extensions (.parquet, .csv, .json, etc.)
        for ext in (".parquet", ".csv", ".json", ".orc", ".avro", ".delta"):
            if segment.lower().endswith(ext):
                segment = segment[: -len(ext)]
                break

        # Strip trailing quotes that may remain
        segment = segment.strip("'\"")

        # Skip if the segment contains variable interpolation or is empty
        if not segment or "{" in segment or segment == "*":
            return None

        if segment.startswith("Uninferable"):
            return None

        return segment

    def _add_data_in(self, node: ast.Call, source_type: str, **kwargs: Any) -> str:
        """Create a data_in entry and return its ID."""
        node_id = self._next_id("in")
        entry: dict[str, Any] = {
            "id": node_id,
            "type": source_type,
            "location": self._make_location_dict(node),
            "is_test_file": self._is_test_file(),
        }
        entry.update(kwargs)
        if not entry.get("name") and entry.get("path"):
            derived = self._derive_name_from_path(entry["path"])
            if derived:
                entry["name"] = derived
        self.data_in.append(entry)
        self._last_node_id = node_id
        return node_id

    def _add_data_out(self, node: ast.Call, sink_type: str, **kwargs: Any) -> str:
        """Create a data_out entry and return its ID."""
        node_id = self._next_id("out")
        entry: dict[str, Any] = {
            "id": node_id,
            "type": sink_type,
            "location": self._make_location_dict(node),
            "is_test_file": self._is_test_file(),
        }
        entry.update(kwargs)
        if not entry.get("name") and entry.get("path"):
            derived = self._derive_name_from_path(entry["path"])
            if derived:
                entry["name"] = derived
        self.data_out.append(entry)
        return node_id

    @staticmethod
    def _normalize_sql_for_dml(sql_query: str) -> str:
        """Strip runtime: prefix, f-string quotes, and escaped newlines."""
        s = sql_query.strip()
        if s.startswith("runtime:"):
            s = s[len("runtime:"):]
        s = s.replace("\\n", " ").replace("\n", " ")
        s = s.strip("f\'\"").strip()
        return s.upper()

    @classmethod
    def _is_dml_query(cls, sql_query: str) -> bool:
        """Check if a SQL query is a DML statement (DELETE, INSERT, UPDATE, MERGE, TRUNCATE)."""
        normalized = cls._normalize_sql_for_dml(sql_query)
        return any(normalized.startswith(kw) for kw in ("DELETE", "INSERT", "UPDATE", "MERGE", "TRUNCATE"))

    @classmethod
    def _get_dml_operation(cls, sql_query: str) -> str:
        """Extract the DML operation type from a SQL query."""
        normalized = cls._normalize_sql_for_dml(sql_query)
        for kw in ("DELETE", "INSERT", "UPDATE", "MERGE", "TRUNCATE"):
            if normalized.startswith(kw):
                return kw.lower()
        return "unknown"

    def _extract_table_from_sql(self, sql_query: str) -> str | None:
        """Extract table name from SQL query using AST parser (sqlglot) with regex fallback."""
        import re as _re

        if not sql_query:
            return None

        # For DML statements (INSERT / UPDATE / DELETE / MERGE / TRUNCATE) the
        # target table is the _write_ destination, not a source_tables entry.
        # Extract it with a simple regex before calling the (SELECT-focused) extractor.
        _norm = sql_query.strip()
        if _norm.startswith("runtime:"):
            _norm = _norm[len("runtime:"):]
        _norm = _norm.strip("f'\"").strip()

        _dml_target_re = _re.compile(
            r"(?:INSERT\s+(?:OVERWRITE\s+)?INTO|UPDATE|DELETE\s+FROM|TRUNCATE\s+TABLE|MERGE\s+INTO)"
            r"\s+([\w.`\"]+)",
            _re.IGNORECASE,
        )
        _dml_m = _dml_target_re.search(_norm)
        if _dml_m:
            return _dml_m.group(1).strip("`\"")

        result = extract_sql_schema(sql_query)
        # Note: regex-fallback for SQL table extraction is not emitted here
        # because _inference_warnings only accepts TypeInferenceWarning dicts
        # (DataFrame resolution context). The fallback is implicitly surfaced
        # through the gap report when the parse result is assembled.
        if result.source_tables:
            return result.source_tables[0]
        return None

    def _extract_chain_format(self, node: ast.Call) -> str | None:
        """Extract .format("X") from a read or write chain by walking the AST."""
        current = node.func
        while isinstance(current, ast.Attribute):
            if isinstance(current.value, ast.Call):
                call = current.value
                if isinstance(call.func, ast.Attribute) and call.func.attr == "format":
                    fmt = self._extract_string_arg(call.args, 0)
                    if fmt:
                        return fmt
                current = call.func
            else:
                break
        return None

    def _normalize_format_to_type(self, fmt: str | None) -> tuple[str, str | None]:
        """
        Normalize a format string to a canonical type and preserve the original.
        
        Returns: (normalized_type, original_format)
        
        Cloud DW connectors get their own type for semantic analysis:
        - Type inference benefits from knowing the specific DB
        - Synthetic data generation needs DB-specific formats
        """
        if not fmt:
            return ("other", None)
        
        fmt_lower = fmt.lower()
        
        # Cloud Data Warehouses - explicit types for semantic analysis
        FORMAT_TO_TYPE = {
            # Snowflake
            "snowflake": "snowflake",
            "net.snowflake.spark.snowflake": "snowflake",
            # Redshift
            "redshift": "redshift",
            "com.databricks.spark.redshift": "redshift",
            # BigQuery
            "bigquery": "bigquery",
            "com.google.cloud.spark.bigquery": "bigquery",
            # Databricks Delta
            "delta": "delta",
            "databricks": "databricks",
            # Standard formats
            "csv": "csv",
            "parquet": "parquet",
            "json": "json",
            "orc": "parquet",  # Similar enough
            "avro": "parquet",  # Similar enough
            "iceberg": "iceberg",
            "jdbc": "jdbc",
        }
        
        # Check exact match first
        if fmt_lower in FORMAT_TO_TYPE:
            return (FORMAT_TO_TYPE[fmt_lower], fmt)
        
        # Check if it contains known patterns
        for pattern, normalized_type in FORMAT_TO_TYPE.items():
            if pattern in fmt_lower:
                return (normalized_type, fmt)
        
        # Unknown format - preserve as "other" with original
        return ("other", fmt)

    def _expand_higher_order_calls(
        self, execution_calls: list
    ) -> list:
        """Expand higher-order wrapper calls like safe_call(df, func, ...).
        
        When a call passes a known function as an argument, create a synthetic
        execution_call that represents the delegated inner call, linking the
        DataFrame argument directly to the target function's parameters.
        """
        from warp_core.ir.pyspark_models import (
            ExecutionCall, CallLocation, CalleeRef, CallBindings, InputBinding,
        )
        known_funcs = {f.get("name") for f in self.functions if f.get("name")}
        new_calls: list = []

        for ec in execution_calls:
            inputs = ec.bindings.inputs if ec.bindings else []
            if len(inputs) < 2:
                continue

            func_binding = None
            df_bindings = []
            extra_bindings = []

            for inp in inputs:
                src = inp.source_id or ""
                if src in known_funcs and not func_binding:
                    func_binding = src
                elif not func_binding:
                    df_bindings.append(inp)
                else:
                    extra_bindings.append(inp)

            if not func_binding:
                continue

            target_func = next(
                (f for f in self.functions if f.get("name") == func_binding), None
            )
            if not target_func:
                continue

            params = [a.get("name", f"param_{i}") for i, a in enumerate(target_func.get("arguments", []))]

            synth_inputs = []
            for i, binding in enumerate(df_bindings + extra_bindings):
                param_name = params[i] if i < len(params) else f"param_{i}"
                source_type = binding.source_type if hasattr(binding, 'source_type') else BindingSourceType.VARIABLE
                synth_inputs.append(InputBinding(
                    arg_name=param_name,
                    source_type=source_type,
                    source_id=binding.source_id,
                    inferred_origin=f"higher_order:{ec.callee.function}" if ec.callee else None,
                ))

            synth_call = ExecutionCall(
                call_id=f"{ec.call_id}_expanded",
                caller=ec.caller,
                callee=CalleeRef(function=func_binding, file=ec.callee.file if ec.callee else None),
                bindings=CallBindings(inputs=synth_inputs, output=ec.bindings.output if ec.bindings else None),
                literal_arguments=ec.literal_arguments,
            )
            new_calls.append(synth_call)

        execution_calls.extend(new_calls)
        return execution_calls

    def _try_resolve_fstring(self, node: ast.JoinedStr) -> str | None:
        """Try to resolve an f-string by substituting known variables from SymbolTable.
        
        Returns the fully resolved string if all variables are known, None otherwise.
        """
        scope = self._current_function or GLOBAL_SCOPE
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name):
                var_name = value.value.id
                lit = SymbolTable.resolve_string_literal(scope, var_name)
                if lit and not lit.startswith("runtime:"):
                    parts.append(lit)
                else:
                    return None
            else:
                return None
        return "".join(parts)

    def _try_resolve_runtime_literal(self, runtime_val: str) -> str | None:
        """Try to resolve a 'runtime:f\'{...}\'' value by parsing and substituting variables."""
        if not runtime_val.startswith("runtime:"):
            return None
        expr = runtime_val[len("runtime:"):]
        if not (expr.startswith("f'") or expr.startswith('f"')):
            scope = self._current_function or GLOBAL_SCOPE
            lit = SymbolTable.resolve_string_literal(scope, expr)
            if lit and not lit.startswith("runtime:"):
                return lit
            return None
        try:
            tree = ast.parse(expr, mode="eval")
            if isinstance(tree.body, ast.JoinedStr):
                return self._try_resolve_fstring(tree.body)
        except Exception:
            pass
        return None

    def _extract_string_arg(self, args: list[Any], index: int, capture_runtime: bool = False) -> str | None:
        """
        Extract a string argument from a function call.

        Supports:
        - Direct string literals: "table_name"
        - Variable references: source_table (resolved via astroid)
        - F-strings: F"{prefix}/file.csv" -> "runtime:F\"{prefix}/file.csv\""
        - Unresolved variables: my_var -> "runtime:my_var"
        
        Args:
            args: List of arguments from the function call
            index: Index of the argument to extract
            capture_runtime: If True, capture non-literal expressions as "runtime:..."
        """
        if len(args) > index:
            arg = args[index]
            # Direct string literal - always preferred
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
            
            # Variable reference - try to resolve first
            if isinstance(arg, ast.Name):
                if self._inference_engine:
                    resolved = self._inference_engine.resolve_table_name(arg.id)
                    if resolved:
                        return resolved
                # Try SymbolTable for string literals (including f-strings)
                scope = self._current_function or GLOBAL_SCOPE
                lit = SymbolTable.resolve_string_literal(scope, arg.id)
                if lit and not lit.startswith("runtime:"):
                    return lit
                if lit and lit.startswith("runtime:"):
                    resolved = self._try_resolve_runtime_literal(lit)
                    if resolved:
                        return resolved
                # Could not resolve - capture as runtime if requested
                if capture_runtime:
                    return f"runtime:{arg.id}"
            
            # F-string (JoinedStr) - try to resolve variables first
            if isinstance(arg, ast.JoinedStr):
                resolved = self._try_resolve_fstring(arg)
                if resolved:
                    return resolved
                if capture_runtime:
                    try:
                        expr = ast.unparse(arg)
                        return f"runtime:{expr}"
                    except Exception:
                        return "runtime:<f-string>"
            
            # BinOp (string concatenation like "prefix" + var)
            if isinstance(arg, ast.BinOp) and capture_runtime:
                try:
                    expr = ast.unparse(arg)
                    return f"runtime:{expr}"
                except Exception:
                    return "runtime:<concat>"
            
            # Any other expression - capture if requested
            if capture_runtime:
                try:
                    expr = ast.unparse(arg)
                    # Truncate very long expressions
                    if len(expr) > 100:
                        expr = expr[:97] + "..."
                    return f"runtime:{expr}"
                except Exception:
                    return "runtime:<expr>"
        
        return None

    def _extract_write_mode(self, node: ast.Call) -> str:
        """Extract write mode from a write chain."""
        # Look for .mode("overwrite") in the chain
        current = node.func
        while isinstance(current, ast.Attribute):
            if isinstance(current.value, ast.Call):
                call = current.value
                if isinstance(call.func, ast.Attribute) and call.func.attr == "mode":
                    mode = self._extract_string_arg(call.args, 0)
                    if mode:
                        return mode
                current = call.func
            else:
                break
        return "overwrite"  # Default

    def _extract_chain_options(self, node: ast.Call) -> dict[str, str | None]:
        """
        Extract key options from a read/write chain.
        
        Walks through the chain looking for .option("key", "value") calls
        and extracts important ones like: dbtable, query, path, url, etc.
        
        Returns dict with extracted options (values may be "runtime:..." for dynamic values)
        """
        options: dict[str, str | None] = {}
        
        # Keys we want to extract
        important_keys = {
            "dbtable", "query", "path", "url", 
            "table", "collection", "database", "schema",
            "sfDatabase", "sfSchema", "sfWarehouse"
        }
        
        # Walk the chain backwards looking for .option() calls
        current = node
        while True:
            if isinstance(current, ast.Call):
                func = current.func
                if isinstance(func, ast.Attribute):
                    # Check if this is an .option("key", "value") call
                    if func.attr == "option" and len(current.args) >= 2:
                        key_arg = current.args[0]
                        val_arg = current.args[1]
                        
                        # Extract key (must be literal string)
                        if isinstance(key_arg, ast.Constant) and isinstance(key_arg.value, str):
                            key = key_arg.value
                            if key.lower() in {k.lower() for k in important_keys} or key in important_keys:
                                # Extract value (with runtime capture)
                                value = self._extract_string_arg(current.args, 1, capture_runtime=True)
                                if value and key not in options:  # Don't overwrite
                                    options[key] = value
                    
                    # Move up the chain
                    current = func.value
                    continue
            
            # Can't continue
            break
        
        return options


    # =========================================================================
    # Spark chain processing
    # =========================================================================

    def _unroll_spark_chain(self, node: ast.Call) -> list[tuple[str, ast.Call, int, int]]:
        """
        Unroll a Spark method chain into individual operations.

        For df.filter().withColumn().fillna(), returns:
        [
            ("filter", filter_call, line_start, line_end),
            ("withColumn", withColumn_call, line_start, line_end),
            ("fillna", fillna_call, line_start, line_end),
        ]

        The chain is built from inside-out (chronological order).
        """
        chain = []
        current = node

        while isinstance(current, ast.Call):
            if isinstance(current.func, ast.Attribute):
                method_name = current.func.attr
                line_start = current.lineno
                line_end = getattr(current, "end_lineno", current.lineno)
                chain.append((method_name, current, line_start, line_end))
                current = current.func.value
            else:
                break

        # Reverse to get chronological order (filter first, then withColumn, etc.)
        return list(reversed(chain))

    def _resolve_df_argument(self, arg: ast.AST) -> str | None:
        """
        Resolve a DataFrame argument to its node ID.

        Handles:
        - Simple variable: customer_df -> lookup in symbol table
        - Spark chain: df.select(...).withColumn(...) -> process sub-chain
          transformations and return the last node ID

        This is used for join/union arguments to connect the second DataFrame.
        """
        if isinstance(arg, ast.Name):
            return self.symbol_table.get(arg.id)

        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute):
            if self._is_spark_chain(arg) or self._is_spark_operation(arg):
                prev_id = self._last_node_id
                self._process_spark_chain(arg)
                if self._last_node_id and self._last_node_id != prev_id:
                    return self._last_node_id
            base_var, base_id = self._get_chain_base(arg)
            return base_id

        return None

    def _get_chain_base(self, node: ast.Call) -> tuple[str | None, str | None]:
        """
        Find the base of a method chain (the DataFrame variable or function call).

        For df.filter().withColumn(), returns ("df", resolved_id).
        For read_df_from_rds(...).drop(), returns ("read_df_from_rds", returns_id).
        """
        current = node
        while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
            current = current.func.value

        # Case 1: Simple variable (df.filter())
        if isinstance(current, ast.Name):
            var_name = current.id
            # Look up in symbol table
            resolved_id = self.symbol_table.get(var_name)
            return (var_name, resolved_id)

        # Case 2: Function call as base (read_df_from_rds(...).drop())
        if isinstance(current, ast.Call) and isinstance(current.func, ast.Name):
            func_name = current.func.id
            # Look up function's returns_id in global registry
            sig = SymbolTable._global_functions.get(func_name)
            if sig and sig.returns_id:
                return (func_name, sig.returns_id)
            # Fallback: create a placeholder for this function call
            return (func_name, f"call_{func_name}")

        return (None, None)

    def _extract_atomic_logic(self, method_name: str, call_node: ast.Call) -> str:
        """
        Extract only the current method's logic, not the entire chain.

        For df.filter(cond).withColumn("col", expr):
        - filter call → "filter(cond)"
        - withColumn call → "withColumn('col', expr)"
        """
        # Extract just the method call with its arguments
        args_str = ", ".join(ast.unparse(arg) for arg in call_node.args)
        kwargs_str = ", ".join(f"{kw.arg}={ast.unparse(kw.value)}" for kw in call_node.keywords)

        all_args = ", ".join(filter(None, [args_str, kwargs_str]))
        return f"{method_name}({all_args})"

    def _process_spark_chain(self, node: ast.Call) -> None:
        """
        Process a Spark method chain by unrolling it into atomic nodes.

        Each operation becomes a separate transformation with:
        - Atomic logic (just the current method)
        - Proper inputs (reference to previous node in chain)
        - Correct lineage tracking

        Special handling:
        - groupBy+agg on same line → unified into groupBy_agg
        - alias after agg → absorbed into agg parameters
        - Skip column expressions (F.col().alias() etc.)
        """
        # Skip column expressions - they are NOT DataFrame transformations
        if self._is_column_expression(node):
            return

        # Get the base DataFrame variable
        base_var, base_id = self._get_chain_base(node)

        # Unroll the chain
        chain = self._unroll_spark_chain(node)

        # Mark only TRANSFORMATION nodes as processed to avoid duplicates
        # Data sources (csv, parquet, table, etc.) need to be processed by visit_Call
        for method_name, call_node, _, _ in chain:
            if method_name in self._TRANSFORM_METHODS:
                self._processed_chain_nodes.add(id(call_node))

        # Pre-scan: identify groupBy+agg pairs for unification
        groupby_agg_pairs = set()
        for i, (method_name, _call_node, _line_start, _line_end) in enumerate(chain):
            if method_name == "groupBy":
                # Look ahead for agg
                if i + 1 < len(chain) and chain[i + 1][0] == "agg":
                    groupby_agg_pairs.add(i)

        # Process each operation in chronological order
        prev_id = base_id or (f"param_{base_var}" if base_var else None)
        skip_next = False

        for i, (method_name, call_node, line_start, line_end) in enumerate(chain):
            # Skip if marked (e.g., agg that was merged into groupBy_agg)
            if skip_next:
                skip_next = False
                continue

            # Skip non-transformation methods (but still process data sources/sinks)
            if method_name not in self._TRANSFORM_METHODS:
                # Visit to process data sources (csv, parquet, table, etc.) or sinks
                self.visit(call_node)
                # Update prev_id if a data source was created (e.g., spark.read.csv().filter())
                if self._last_node_id:
                    prev_id = self._last_node_id
                continue

            # Skip ignored calls
            if self._is_ignored_call(call_node):
                continue

            # Map aliases
            canonical = {
                "where": "filter",
                "sort": "orderBy",
                "dropDuplicates": "distinct",
                "unionAll": "union",
            }.get(method_name, method_name)

            # Handle groupBy+agg unification (look ahead)
            if i in groupby_agg_pairs:
                # This groupBy will be merged with the next agg
                agg_method, agg_call, agg_line_start, agg_line_end = chain[i + 1]

                # Extract both logics
                groupby_logic = self._extract_atomic_logic(method_name, call_node)
                agg_logic = self._extract_atomic_logic(agg_method, agg_call)

                # Get column info from the call nodes
                col_start = getattr(call_node, "col_offset", 0) + 1
                col_end = getattr(agg_call, "end_col_offset", col_start)

                node_id = self._next_id("tx")
                combined_logic = f"{groupby_logic}.{agg_logic}"
                is_det, non_det_reason = self._detect_non_deterministic(combined_logic)
                
                tx_dict = {
                    "id": node_id,
                    "operation": "groupBy_agg",
                    "inputs": [prev_id] if prev_id else [],
                    "logic": combined_logic,
                    "location": self._make_location_from_range(
                        line_start, agg_line_end, col_start, col_end
                    ),
                    "line_start": line_start,
                    "line_end": agg_line_end,
                    "parameters": {
                        "group_columns": [ast.unparse(arg) for arg in call_node.args],
                        **self._extract_parameters(agg_call, "agg"),
                    },
                    "is_deterministic": is_det,
                }
                if non_det_reason:
                    tx_dict["non_deterministic_reason"] = non_det_reason
                
                self.transformations.append(tx_dict)

                prev_id = node_id
                self._last_node_id = node_id
                skip_next = True  # Skip the agg on next iteration
                continue

            # Skip standalone agg if it was part of a pair (shouldn't happen due to skip_next)
            if canonical == "agg" and i > 0 and (i - 1) in groupby_agg_pairs:
                continue

            # Handle alias absorption into agg/groupBy_agg
            if canonical == "alias" and self.transformations:
                absorbed = False
                for j in range(len(self.transformations) - 1, -1, -1):
                    parent_tx = self.transformations[j]
                    if parent_tx["operation"] in ("agg", "groupBy_agg"):
                        if "column_aliases" not in parent_tx["parameters"]:
                            parent_tx["parameters"]["column_aliases"] = []
                        alias_name = self._extract_string_arg(call_node.args, 0)
                        if alias_name:
                            parent_tx["parameters"]["column_aliases"].append(alias_name)
                        prev_id = parent_tx["id"]
                        absorbed = True
                        break
                    elif parent_tx["operation"] != "alias":
                        break
                if absorbed:
                    continue

            # Extract atomic logic
            atomic_logic = self._extract_atomic_logic(method_name, call_node)

            # Detect UDF calls in arguments
            has_udf = self._contains_udf_call(call_node)

            # Determine operation name (add _custom suffix for UDFs)
            operation_name = f"{canonical}_custom" if has_udf else canonical

            # Create atomic transformation node
            node_id = self._next_id("tx")
            params = self._extract_parameters(call_node, method_name)
            if has_udf:
                params["contains_udf"] = True
                params["feasibility"] = "low"

            # Build inputs list
            inputs = [prev_id] if prev_id else []

            # For joins/unions, resolve the first argument (second DataFrame)
            if (
                canonical
                in (
                    "join",
                    "crossJoin",
                    "union",
                    "unionAll",
                    "unionByName",
                    "intersect",
                    "subtract",
                    "except",
                )
                and call_node.args
            ):
                second_df_id = self._resolve_df_argument(call_node.args[0])
                if second_df_id and second_df_id not in inputs:
                    inputs.append(second_df_id)

            # Get column info from call_node
            col_start = getattr(call_node, "col_offset", 0) + 1
            col_end = getattr(call_node, "end_col_offset", col_start)

            # Infer output types for column-producing operations
            inferred_output = []
            if operation_name in ("withColumn", "withColumnRenamed", "select", "withColumn_custom"):
                col_name = params.get("column_name")
                expr = params.get("expression", atomic_logic)
                if col_name and expr:
                    type_info = self._infer_column_type_from_expression(
                        expr, col_name, is_udf=has_udf
                    )
                    if type_info:
                        inferred_output.append(type_info)
            
            # Check for non-deterministic functions
            is_det, non_det_reason = self._detect_non_deterministic(atomic_logic)
            
            tx_dict = {
                "id": node_id,
                "operation": operation_name,
                "inputs": inputs,
                "logic": atomic_logic,
                "location": self._make_location_from_range(
                    line_start, line_end, col_start, col_end
                ),
                "line_start": line_start,
                "line_end": line_end,
                "parameters": params,
                "inferred_output": inferred_output,
                "is_deterministic": is_det,
            }
            if non_det_reason:
                tx_dict["non_deterministic_reason"] = non_det_reason
            
            self.transformations.append(tx_dict)

            # Update for next iteration
            prev_id = node_id
            self._last_node_id = node_id


    # =========================================================================
    # Transformation detection & extraction
    # =========================================================================

    # Known transformation methods
    _TRANSFORM_METHODS = {
        "select",
        "filter",
        "where",
        "join",
        "crossJoin",
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
        "intersectAll",
        "except",
        "exceptAll",
        "subtract",
        "limit",
        "withColumn",
        "withColumnRenamed",
        "colRegex",
        "crosstab",
        "describe",
        "freqItems",
        "hint",
        "summary",
        "drop",
        "alias",
        "pivot",
        "unpivot",
        "rollup",
        "cube",
        "fillna",
        "na",
        "dropna",
        "replace",  # null handling
        "sample",
        "coalesce",
        "repartition",  # data manipulation
        "cache",
        "persist",
        "unpersist",  # caching
        "toDF",
        "transform",  # DataFrame conversion
        "mapInPandas",  # Pandas UDF
        "selectExpr",  # SQL expression select
        "createTempView",
        "createOrReplaceTempView",
        "createGlobalTempView",  # View creation
        # GroupedData aggregate methods (return DataFrame)
        "avg",
        "mean",
        "sum",
        "min",
        "max",
        "count",  # GroupedData.count() returns DataFrame
    }

    _IGNORED_MODULES = {
        "os",
        "sys",
        "json",
        "logging",
        "re",
        "math",
        "datetime",
        "time",
        "random",
        "collections",
        "itertools",
        "functools",
        "pathlib",
        "shutil",
        "subprocess",
        "tempfile",
        "glob",
        "pickle",
        "csv",
        "io",
        "string",
        "textwrap",
        "copy",
        "typing",
        "abc",
        "enum",
        "dataclasses",
        "contextlib",
        "warnings",
        "traceback",
        "inspect",
        "dis",
        "gc",
        "requests",
        "urllib",
        "http",
        "socket",
        "ssl",
        "hashlib",
        "hmac",
        "secrets",
        "base64",
        "uuid",
    }

    def _is_ignored_call(self, node: ast.Call) -> bool:
        """
        Check if this call is from an ignored module (not Spark).

        Examples that should be ignored:
        - os.path.join(...)
        - json.loads(...)
        - logging.info(...)
        """
        root = self._get_call_root(node)
        return root in self._IGNORED_MODULES

    def _get_call_root(self, node: ast.Call) -> str | None:
        """
        Get the root name of a call chain.

        Examples:
        - os.path.join(...) -> 'os'
        - df.filter(...) -> 'df'
        - spark.read.table(...) -> 'spark'
        """
        current = node.func
        while True:
            match current:
                case ast.Name(id=name):
                    return name
                case ast.Attribute(value=value):
                    current = value
                case _:
                    return None

    # Non-deterministic function patterns
    NON_DETERMINISTIC_FUNCTIONS: ClassVar[set[str]] = {
        "current_timestamp", "current_date", "now",
        "rand", "random", "randn",
        "uuid", "monotonically_increasing_id",
        "input_file_name", "spark_partition_id",
    }

    def _detect_non_deterministic(self, logic: str | None) -> tuple[bool, str | None]:
        """
        Check if a transformation logic contains non-deterministic functions.
        
        Returns (is_deterministic, reason).
        """
        if not logic:
            return True, None
        
        logic_lower = logic.lower()
        for func in self.NON_DETERMINISTIC_FUNCTIONS:
            if func in logic_lower:
                return False, f"uses {func}()"
        
        return True, None

    def _add_transformation(self, node: ast.Call, method_name: str) -> None:
        """Add a transformation node with lineage tracking."""
        # Skip column expressions - they are NOT DataFrame transformations
        if self._is_column_expression(node):
            return
        
        # Skip Python string operations (e.g., some_str.replace("a", "b"))
        if self._is_python_string_operation(node):
            return
        
        # Skip Window spec chains (e.g., Window.partitionBy().orderBy())
        if isinstance(node.func, ast.Attribute):
            base = node.func.value
            if isinstance(base, ast.Call) and self._is_window_spec_chain(base):
                return
            if isinstance(base, ast.Name) and base.id == "Window":
                return

        # Extract code snippet
        try:
            logic = ast.unparse(node)
        except Exception:
            logic = None

        # Map aliases
        canonical = {
            "where": "filter",
            "sort": "orderBy",
            "dropDuplicates": "distinct",
            "unionAll": "union",
        }.get(method_name, method_name)

        # Resolve inputs from symbol table
        inputs = self.symbol_table.resolve_inputs(node)

        # Unify chained operations: merge groupBy+agg on same line
        # Note: agg is visited BEFORE groupBy (outer call first), so we check
        # if we're adding groupBy and the last was agg on same line
        if canonical == "groupBy" and self.transformations:
            last_tx = self.transformations[-1]
            if last_tx["operation"] == "agg" and last_tx["line_start"] == node.lineno:
                # Merge groupBy into the agg node, rename to groupBy_agg
                last_tx["operation"] = "groupBy_agg"
                last_tx["parameters"]["group_columns"] = [ast.unparse(arg) for arg in node.args]
                # Merge inputs (groupBy may have additional inputs)
                existing_inputs = last_tx.get("inputs", [])
                for inp in inputs:
                    if inp not in existing_inputs:
                        existing_inputs.append(inp)
                last_tx["inputs"] = existing_inputs
                return  # Don't add separate groupBy node

        # Absorb alias nodes into parent agg/groupBy_agg
        # Pattern: .agg(...).alias("col1").alias("col2") should merge aliases
        if canonical == "alias" and self.transformations:
            # Find the most recent agg/groupBy_agg (may be a few nodes back due to other aliases)
            for i in range(len(self.transformations) - 1, -1, -1):
                parent_tx = self.transformations[i]
                if parent_tx["operation"] in ("agg", "groupBy_agg"):
                    # Absorb this alias into the parent
                    if "column_aliases" not in parent_tx["parameters"]:
                        parent_tx["parameters"]["column_aliases"] = []

                    # Extract alias name from the call
                    alias_name = self._extract_string_arg(node.args, 0)
                    if alias_name:
                        parent_tx["parameters"]["column_aliases"].append(alias_name)
                    return  # Don't add separate alias node
                elif parent_tx["operation"] != "alias":
                    # Stop searching if we hit a non-alias, non-agg node
                    break

        node_id = self._next_id("tx")
        
        # Check for non-deterministic functions
        is_deterministic, non_det_reason = self._detect_non_deterministic(logic)
        
        tx_dict = {
            "id": node_id,
            "operation": canonical,
            "inputs": inputs,
            "logic": logic,
            "location": self._make_location_dict(node),
            "line_start": getattr(node, "lineno", 0),
            "line_end": getattr(node, "end_lineno", getattr(node, "lineno", 0)),
            "parameters": self._extract_parameters(node, method_name),
            "is_deterministic": is_deterministic,
        }
        if non_det_reason:
            tx_dict["non_deterministic_reason"] = non_det_reason
        
        self.transformations.append(tx_dict)
        self._last_node_id = node_id

    def _extract_parameters(self, node: ast.Call, method_name: str) -> dict[str, Any]:
        """Extract parameters for specific operations."""
        params: dict[str, Any] = {}

        match method_name:
            case "join" | "crossJoin":
                # Join signature: join(other, on=None, how=None)
                # Positional: join(df, 'key', 'left') or join(df, ['a', 'b'], 'inner')
                # Keyword: join(df, on='key', how='left')

                # Positional argument 1: join condition (on)
                if len(node.args) >= 2:
                    on_arg = node.args[1]
                    if isinstance(on_arg, ast.Constant):
                        params["join_condition"] = on_arg.value
                    elif isinstance(on_arg, ast.List):
                        params["join_condition"] = [
                            el.value for el in on_arg.elts if isinstance(el, ast.Constant)
                        ]
                    else:
                        params["join_condition"] = ast.unparse(on_arg)

                # Positional argument 2: join type (how)
                if len(node.args) >= 3:
                    how_arg = node.args[2]
                    if isinstance(how_arg, ast.Constant):
                        params["join_type"] = how_arg.value

                # Keyword arguments (override positionals)
                for kw in node.keywords:
                    if kw.arg == "how" and isinstance(kw.value, ast.Constant):
                        params["join_type"] = kw.value.value
                    elif kw.arg == "on":
                        if isinstance(kw.value, ast.Constant):
                            params["join_condition"] = kw.value.value
                        elif isinstance(kw.value, ast.List):
                            params["join_condition"] = [
                                el.value for el in kw.value.elts if isinstance(el, ast.Constant)
                            ]
                        else:
                            params["join_condition"] = ast.unparse(kw.value)

            case "groupBy":
                # Extract grouping columns
                if node.args:
                    params["columns"] = [ast.unparse(arg) for arg in node.args]

            case "filter" | "where":
                # Extract filter condition
                if node.args:
                    params["condition"] = ast.unparse(node.args[0])

            case "select":
                # Extract selected columns
                if node.args:
                    params["columns"] = [ast.unparse(arg) for arg in node.args]

            case "withColumn":
                # Extract column name and expression
                if len(node.args) >= 2:
                    params["column_name"] = self._extract_string_arg(node.args, 0)
                    params["expression"] = ast.unparse(node.args[1])

            case "orderBy" | "sort":
                if node.args:
                    params["columns"] = []
                    for arg in node.args:
                        col_info = self._parse_order_column(arg)
                        params["columns"].append(col_info)

            case "agg":
                # Extract column aliases from agg expressions like:
                # agg(sum("amount").alias("total"), count("id").alias("num"))
                aliases = []
                for arg in node.args:
                    alias = self._extract_column_alias(arg)
                    if alias:
                        aliases.append(alias)
                if aliases:
                    params["column_aliases"] = aliases

        return params

    def _extract_column_alias(self, expr: ast.AST) -> str | None:
        """
        Extract alias name from a Column expression.

        For sum("amount").alias("total"), returns "total".
        """
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
            if expr.func.attr == "alias" and expr.args:
                alias_arg = expr.args[0]
                if isinstance(alias_arg, ast.Constant):
                    return str(alias_arg.value)
        return None

    def _parse_order_column(self, arg: ast.AST) -> dict[str, Any]:
        """
        Parse an orderBy column argument into structured form.

        Examples:
            col("x")           -> {"column": "x", "direction": "ASC"}
            col("x").desc()    -> {"column": "x", "direction": "DESC"}
            col("x").asc()     -> {"column": "x", "direction": "ASC"}
            "column_name"      -> {"column": "column_name", "direction": "ASC"}
        """
        direction = "ASC"
        column_expr = arg

        # Check for .desc() or .asc() call
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute):
            if arg.func.attr == "desc":
                direction = "DESC"
                column_expr = arg.func.value
            elif arg.func.attr == "asc":
                direction = "ASC"
                column_expr = arg.func.value

        # Extract column name
        column_name: str | None = None
        match column_expr:
            case ast.Call(func=ast.Name(id="col"), args=[ast.Constant(value=name)]):
                column_name = str(name)
            case ast.Constant(value=name):
                column_name = str(name)
            case _:
                # Fallback to string representation
                try:
                    column_name = ast.unparse(column_expr)
                except Exception:
                    column_name = "<unknown>"

        return {
            "column": column_name,
            "direction": direction,
            "raw": ast.unparse(arg) if hasattr(ast, "unparse") else str(arg),
        }


    # =========================================================================
    # Type inference
    # =========================================================================

    def _is_spark_operation(self, node: ast.Call) -> bool:
        """
        Check if this is a Spark DataFrame operation (chain OR single op).
        
        Uses type inference when possible, falls back to name matching otherwise.

        Returns True for:
        - Chains: df.filter().withColumn()
        - Single ops: df.withColumn(...)

        Returns False for:
        - Column expressions: F.col("x").alias("y")
        - Python string operations: some_str.replace("a", "b")
        - Window spec definitions: Window.partitionBy(...)
        """
        if not isinstance(node.func, ast.Attribute):
            return False

        method_name = node.func.attr
        if method_name not in self._TRANSFORM_METHODS:
            return False

        # Check base: must be Name (df) or another Call (chain)
        base = node.func.value
        
        # Step 1: Try type inference on the receiver
        receiver_type = self._infer_receiver_type(base)
        
        if receiver_type is not None:
            # We could infer the type - use it for decision
            if receiver_type in ("str", "int", "float", "bool", "list", "dict", "tuple", "set", "NoneType"):
                # Not a DataFrame - exclude this call
                self._inference_stats["excluded"] += 1
                return False
            elif receiver_type == "DataFrame":
                # Confirmed DataFrame - include
                self._inference_stats["inferred"] += 1
                return True
        
        # Step 2: Type inference failed - fall back to name matching
        # This is where we generate warnings for ambiguous cases
        
        if isinstance(base, ast.Name):
            # Exclude known column expression starters
            if base.id in ("col", "lit", "when", "coalesce", "expr"):
                return False
            # Exclude Window (it's a spec builder, not a DataFrame)
            if base.id == "Window":
                return False
            # Check if this looks like a Python string operation
            if self._is_python_string_operation(node):
                self._inference_stats["excluded"] += 1
                return False
            # Name matching fallback - record warning
            self._inference_stats["name_match"] += 1
            self._record_inference_warning(
                node=node,
                method=method_name,
                receiver=base.id,
                receiver_type=None,
                resolution="name_match",
                reason="Could not infer type of variable, assuming DataFrame based on method name"
            )
            return True  # Single op: df.withColumn(...)
            
        if isinstance(base, ast.Call):
            # Check if this is a column expression chain
            if self._is_column_expression(base):
                return False
            # Check if base is a Python string method call
            if self._is_python_string_chain(base):
                self._inference_stats["excluded"] += 1
                return False
            # Check if this is a Window spec chain (Window.partitionBy().orderBy())
            if self._is_window_spec_chain(base):
                return False
            # Chain fallback - record warning for call chains
            self._inference_stats["name_match"] += 1
            self._record_inference_warning(
                node=node,
                method=method_name,
                receiver=ast.unparse(base)[:50] + "..." if len(ast.unparse(base)) > 50 else ast.unparse(base),
                receiver_type=None,
                resolution="name_match",
                reason="Could not infer type of call chain, assuming DataFrame based on method name"
            )
            return True  # Chain: df.filter().withColumn(...)

        return False

    def _record_inference_warning(
        self,
        node: ast.Call,
        method: str,
        receiver: str,
        receiver_type: str | None,
        resolution: str,
        reason: str
    ) -> None:
        """Record a warning when type inference falls back to name matching."""
        try:
            code_snippet = ast.unparse(node)
        except Exception:
            code_snippet = f"{receiver}.{method}(...)"
        
        warning = {
            "path": self._current_filepath,
            "line": node.lineno,
            "column": getattr(node, "col_offset", None),
            "method": method,
            "receiver": receiver,
            "receiver_type": receiver_type,
            "resolution": resolution,
            "reason": reason,
            "code_snippet": code_snippet,
            "context_lines": [],  # Could be populated if needed
            "suggestion": f"Consider adding type annotation or registering '{receiver}' as DataFrame"
        }
        
        self._inference_warnings.append(warning)

    def _is_python_string_operation(self, node: ast.Call) -> bool:
        """
        Detect if this is a Python string/datetime operation, not a Spark operation.
        
        Heuristics:
        - .replace(str_literal, str_literal) with 2 string args -> Python str.replace()
        - .replace(day=..., month=...) with datetime kwargs -> datetime.replace()
        - Variable comes from .upper(), .lower(), .strip() -> Python string
        """
        if not isinstance(node.func, ast.Attribute):
            return False
        
        method = node.func.attr
        
        # Check for .replace() with string literals (Python str.replace pattern)
        if method == "replace" and len(node.args) >= 2:
            # str.replace(old, new) takes 2 string args
            # df.na.replace() or df.replace() usually takes dict or different patterns
            if all(isinstance(arg, ast.Constant) and isinstance(arg.value, str) 
                   for arg in node.args[:2]):
                return True
        
        # Check for datetime.replace() pattern (uses keyword args like day=, month=)
        if method == "replace" and node.keywords:
            datetime_kwargs = {"year", "month", "day", "hour", "minute", "second", "microsecond", "tzinfo", "fold"}
            for kw in node.keywords:
                if kw.arg in datetime_kwargs:
                    return True
        
        # Check for other datetime methods
        if method in self._PYTHON_DATETIME_METHODS and method != "replace":
            return True
        
        # Check if base variable comes from a string operation
        base = node.func.value
        if isinstance(base, ast.Name):
            # Check if this variable was assigned from a string method
            if hasattr(self, '_string_variables') and base.id in self._string_variables:
                return True
        
        return False

    def _infer_receiver_type(self, node: ast.expr) -> str | None:
        """
        Attempt to infer the type of an expression (receiver of a method call).
        
        Returns:
            - "str", "int", "float", "list", "dict", "tuple" for Python literals
            - "Column" for pyspark.sql.functions calls (F.col(), F.lit(), etc.)
            - "DataFrame" if we can determine it's a Spark DataFrame
            - None if we cannot determine the type
        
        This is the first step in type-aware transformation detection:
        1. Infer receiver type from literals first (highest confidence)
        2. Check TypeTracker for registered variable types
        3. Check symbol table for known DataFrame variables
        4. Return None if inference not possible (triggers fallback to name matching)
        """
        # Python literal types - direct inference with high confidence
        if isinstance(node, ast.Constant):
            val = node.value
            if isinstance(val, str):
                return "str"
            elif isinstance(val, bool):  # bool before int (bool is subclass of int)
                return "bool"
            elif isinstance(val, int):
                return "int"
            elif isinstance(val, float):
                return "float"
            elif val is None:
                return "NoneType"
            return type(val).__name__ if val is not None else None
        
        if isinstance(node, ast.List):
            return "list"
        
        if isinstance(node, ast.Dict):
            return "dict"
        
        if isinstance(node, ast.Tuple):
            return "tuple"
        
        if isinstance(node, ast.Set):
            return "set"
        
        # JoinedStr (f-strings) are strings
        if isinstance(node, ast.JoinedStr):
            return "str"
        
        # FormattedValue inside f-strings
        if isinstance(node, ast.FormattedValue):
            return "str"
        
        # For Name nodes, check type tracking first, then symbol table
        if isinstance(node, ast.Name):
            var_name = node.id
            scope = self._get_current_scope()
            
            # 1. Check TypeTracker for registered type
            registered_type = TypeTracker.resolve_type(scope, var_name)
            if registered_type:
                return registered_type
            
            # 2. Check symbol table for registered sources (known DataFrames)
            source = SymbolTable.resolve_source(var_name)
            if source is not None:
                return "DataFrame"
            
            # 3. Check if registered as input dataframe
            for data_in in self.data_in:
                if data_in.get("variable") == var_name:
                    return "DataFrame"
        
        # For Call nodes, check if the return type is known
        if isinstance(node, ast.Call):
            # Check for F.col(), F.lit(), etc. - pyspark.sql.functions calls
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    caller_name = node.func.value.id
                    # Validate that caller is pyspark.sql.functions (e.g., F)
                    if TypeTracker.is_pyspark_functions(caller_name):
                        return "Column"
                
                method = node.func.attr
                # Methods that return DataFrames
                if method.lower() in DF_RETURNING_METHODS or method in PD_DF_RETURNING:
                    return "DataFrame"
            
            # Check for re.sub(), re.match() etc. - return str or Match
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "re":
                    method = node.func.attr
                    if method in ("sub", "subn"):
                        return "str"
                    elif method in ("match", "search", "fullmatch"):
                        return "Match"
                    elif method in ("findall",):
                        return "list"
                    elif method in ("split",):
                        return "list"
        
        return None

    def _infer_expression_type(self, node: ast.expr) -> str | None:
        """
        Infer the type of an expression (RHS of assignment).
        
        This is used to register types in TypeTracker for later inference.
        
        Returns:
            - "str", "int", "float", "list", "dict", "tuple" for Python literals
            - "Column" for pyspark.sql.functions calls
            - "DataFrame" for Spark DataFrame operations
            - None if type cannot be determined
        """
        # Delegate to _infer_receiver_type for most cases
        receiver_type = self._infer_receiver_type(node)
        if receiver_type:
            return receiver_type
        
        # Additional inference for method chains
        # e.g., df.filter(...).select(...) -> DataFrame
        if isinstance(node, ast.Call):
            if self._is_spark_chain(node) or self._is_spark_operation(node):
                return "DataFrame"
            
            # re.sub(...).replace(...) -> str
            if self._is_python_string_chain(node):
                return "str"
        
        return None

    def _is_python_string_chain(self, node: ast.Call) -> bool:
        """Check if this call is part of a Python string/datetime method chain."""
        if not isinstance(node.func, ast.Attribute):
            return False
        
        method = node.func.attr
        
        # If the method is a known Python string method, this is a string chain
        if method in self._PYTHON_STRING_METHODS:
            return True
        
        # Check for datetime methods (e.g., raw_start_date.replace(day=1))
        if method in self._PYTHON_DATETIME_METHODS:
            # Additional check: datetime.replace() uses keyword args (day=, month=, etc.)
            # while df.replace() uses positional args
            if method == "replace":
                # If any keyword args are datetime-specific, it's a datetime call
                datetime_kwargs = {"year", "month", "day", "hour", "minute", "second", "microsecond", "tzinfo", "fold"}
                for kw in node.keywords:
                    if kw.arg in datetime_kwargs:
                        return True
            else:
                return True
        
        return False

    # Python string methods - these should NOT be treated as DataFrame ops
    _PYTHON_STRING_METHODS = {
        "upper", "lower", "strip", "lstrip", "rstrip", "replace",
        "split", "join", "startswith", "endswith", "find", "rfind",
        "index", "rindex", "count", "encode", "decode", "format",
        "capitalize", "title", "swapcase", "center", "ljust", "rjust",
        "zfill", "isalpha", "isdigit", "isalnum", "isspace", "isupper",
        "islower", "expandtabs", "partition", "rpartition",
    }

    # Python datetime methods - these should NOT be treated as DataFrame ops
    _PYTHON_DATETIME_METHODS = {
        "replace", "strftime", "strptime", "date", "time", "timestamp",
        "weekday", "isoweekday", "isocalendar", "isoformat", "ctime",
        "timetuple", "utctimetuple", "toordinal", "astimezone",
        "utcoffset", "tzname", "dst", "combine", "fromisoformat",
    }

    # Spark type to Logical type mapping
    _SPARK_TO_LOGICAL_TYPE: dict[str, str] = {
        # String types
        "string": "L_TEXT",
        "str": "L_TEXT",
        
        # Integer types
        "int": "L_INT",
        "integer": "L_INT",
        "long": "L_INT",
        "bigint": "L_INT",
        "short": "L_INT",
        "smallint": "L_INT",
        "tinyint": "L_INT",
        "byte": "L_INT",
        
        # Decimal/Float types
        "double": "L_DECIMAL",
        "float": "L_DECIMAL",
        "decimal": "L_DECIMAL",
        "numeric": "L_DECIMAL",
        
        # Boolean
        "boolean": "L_BOOLEAN",
        "bool": "L_BOOLEAN",
        
        # Date/Time types
        "date": "L_DATE",
        "timestamp": "L_DATETIME",
        "datetime": "L_DATETIME",
        
        # Binary
        "binary": "L_BINARY",
        
        # Complex types (opaque)
        "array": "L_ARRAY",
        "map": "L_MAP",
        "struct": "L_STRUCT",
    }

    def _infer_column_type_from_expression(
        self, expr: str, column_name: str, *, is_udf: bool = False
    ) -> dict | None:
        """
        Infer column type from a Spark expression using AST parsing.
        
        Detects:
        - .cast('type') expressions (via AST)
        - Arithmetic operations (via AST)
        - String/Date functions (via AST)
        - Boolean expressions (via AST)
        - UDFs with obfuscation/hashing patterns (name-based semantic inference)
        
        Returns InferredColumn dict or None if no type can be inferred.
        """
        from asg_pyspark.analysis.spark_to_sql import (
            extract_cast_type,
            detect_expression_type,
        )
        
        # Pattern 1: .cast('type') - use AST parsing
        cast_type = extract_cast_type(expr)
        if cast_type:
            spark_type = cast_type.lower()
            logical_type = self._SPARK_TO_LOGICAL_TYPE.get(spark_type, "UNKNOWN")
            
            return {
                "name": column_name,
                "inferred_type": logical_type,
                "exact_type": spark_type,
                "source": "explicit",
                "confidence": "high",
                "nullable": True,
            }
        
        # Pattern 2: UDF with obfuscation/hashing semantics -> L_TEXT
        # Name-based semantic inference (intentionally uses string matching on UDF names)
        if is_udf:
            udf_text_patterns = (
                "obfuscat", "hash", "encrypt", "encode", "mask",
                "anonymiz", "redact", "scrambl", "cipher", "digest",
                "pii", "sanitiz", "tokeniz",
            )
            expr_lower = expr.lower()
            if any(pattern in expr_lower for pattern in udf_text_patterns):
                return {
                    "name": column_name,
                    "inferred_type": "L_TEXT",
                    "source": "udf_semantic",
                    "confidence": "high",
                    "nullable": True,
                    "udf_pattern": next(p for p in udf_text_patterns if p in expr_lower),
                }
            
            # UDF with scoring/counting semantics -> L_DECIMAL
            udf_numeric_patterns = ("score", "count", "rank", "weight", "ratio", "percent")
            if any(pattern in expr_lower for pattern in udf_numeric_patterns):
                return {
                    "name": column_name,
                    "inferred_type": "L_DECIMAL",
                    "source": "udf_semantic",
                    "confidence": "medium",
                    "nullable": True,
                }
        
        # Pattern 3: Use AST-based expression type detection
        expr_type = detect_expression_type(expr)
        if expr_type:
            type_map = {
                "NUMERIC": "L_DECIMAL",
                "TEXT": "L_TEXT",
                "BOOLEAN": "L_BOOLEAN",
                "TIMESTAMP": "L_TIMESTAMP",
                "ARRAY": "L_ARRAY",
                "DATE": "L_DATE",
            }
            return {
                "name": column_name,
                "inferred_type": type_map.get(expr_type, "UNKNOWN"),
                "source": "explicit",
                "confidence": "medium",
                "nullable": expr_type != "BOOLEAN",
            }
        
        return None


    # =========================================================================
    # Chain detection predicates
    # =========================================================================

    def _is_window_spec_chain(self, node: ast.Call) -> bool:
        """
        Check if this call is part of a Window specification chain.
        
        Window specs like Window.partitionBy().orderBy().rowsBetween()
        should NOT be treated as DataFrame transformations.
        """
        # Window spec methods
        WINDOW_METHODS = {"partitionBy", "orderBy", "rowsBetween", "rangeBetween"}
        
        current = node
        while isinstance(current, ast.Call):
            if isinstance(current.func, ast.Attribute):
                method = current.func.attr
                base = current.func.value
                
                # Check if method is a Window method
                if method in WINDOW_METHODS:
                    return True
                
                # Check if base is Window
                if isinstance(base, ast.Name) and base.id == "Window":
                    return True
                
                # Continue up the chain
                if isinstance(base, ast.Call):
                    current = base
                    continue
            break
        
        return False

    def _is_column_expression(self, node: ast.Call) -> bool:
        """
        Check if this is a column expression (not a DataFrame operation).

        Column expressions are things like:
        - F.col("x"), F.lit(1), F.when(...), F.current_timestamp()
        - col("x"), lit(1), when(...)
        - F.col("x").alias("y"), F.col("x").cast("int")

        These should NOT be treated as DataFrame transformations.
        """
        # Set of functions that create column expressions
        COLUMN_FUNCTIONS = ALL_FUNCTION_NAMES

        # Column methods that indicate this is a column expression
        COLUMN_METHODS = {
            "alias",
            "name",
            "as",
            "cast",
            "astype",
            "isNull",
            "isNotNull",
            "isin",
            "between",
            "startswith",
            "endswith",
            "contains",
            "like",
            "rlike",
            "eqNullSafe",  # null-safe equality
            "bitwiseAND",
            "bitwiseOR",
            "bitwiseXOR",
            "over",  # window function
            # Ordering methods
            "asc",
            "desc",
            "asc_nulls_first",
            "asc_nulls_last",
            "desc_nulls_first",
            "desc_nulls_last",
            # Struct/Collection access
            "getField",
            "getItem",
            # String methods
            "substr",
        }

        # Case 0: Binary/Unary operators on column expressions
        # e.g., col("a") + col("b"), ~col("flag"), col("x") == col("y")
        if isinstance(node, ast.BinOp):
            # Check if either operand is a column expression
            return self._is_column_expression(node.left) or self._is_column_expression(
                node.right
            )
        if isinstance(node, ast.UnaryOp):
            # Check if operand is a column expression (e.g., ~col("flag"))
            return self._is_column_expression(node.operand)
        if isinstance(node, ast.Compare):
            # Check if any comparand is a column expression (e.g., col("x") == col("y"))
            if self._is_column_expression(node.left):
                return True
            return any(self._is_column_expression(c) for c in node.comparators)

        if not isinstance(node, ast.Call):
            return False

        # Case 1: F.col("x") or col("x") - direct function call
        if isinstance(node.func, ast.Name):
            return node.func.id in COLUMN_FUNCTIONS

        # Case 2: F.col("x") where F is pyspark.sql.functions
        if isinstance(node.func, ast.Attribute):
            func_attr = node.func.attr
            func_base = node.func.value

            # F.col("x"), F.lit(1), etc.
            if isinstance(func_base, ast.Name) and func_base.id == "F":
                return func_attr in COLUMN_FUNCTIONS

            # col("x").alias("y") - method on a column expression
            if func_attr in COLUMN_METHODS:
                if isinstance(func_base, ast.Call):
                    return self._is_column_expression(func_base)

            # Also check if it's a method on another column call
            if func_attr in COLUMN_FUNCTIONS:
                return True

        return False

    def _is_spark_chain(self, node: ast.Call) -> bool:
        """
        Check if this is a Spark method chain (e.g., df.filter().withColumn()).

        A chain exists when:
        1. The call is a method on an object (ast.Attribute)
        2. The object is itself a method call (chained)
        3. At least one method is a known Spark transformation

        Note: For single operations, use _is_spark_operation instead.
        """
        if not isinstance(node.func, ast.Attribute):
            return False

        method_name = node.func.attr

        # Check if this method is a transformation
        is_transform = method_name in self._TRANSFORM_METHODS

        # Check if the value is another call (chained)
        value = node.func.value
        is_chained = isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)

        # If this is a transformation and there's a chain, unroll it
        if is_transform and is_chained:
            # Check if any method in the chain is also a transformation
            current = value
            while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
                if current.func.attr in self._TRANSFORM_METHODS:
                    return True
                current = current.func.value

        return False

    def _contains_udf_call(self, node: ast.Call) -> bool:
        """
        Check if the arguments of a Spark call contain UDF calls.

        Detects:
        1. Registered UDFs: calls to functions in _udf_registry
        2. Non-standard functions: calls not in _STANDARD_SPARK_FUNCTIONS

        Example:
            df.withColumn("col", my_udf(F.col("x")))  # my_udf is not standard
        """
        for arg in node.args:
            if self._has_udf_in_expr(arg):
                return True
        for kw in node.keywords:
            if self._has_udf_in_expr(kw.value):
                return True
        return False

    def _has_udf_in_expr(self, expr: ast.AST) -> bool:
        """
        Recursively check if an expression contains a UDF call.
        """
        for child in ast.walk(expr):
            if isinstance(child, ast.Call):
                func_name = self._get_call_name(child)
                if func_name:
                    # Check 1: Registered UDF
                    if func_name in self._udf_registry:
                        return True
                    # Check 2: Non-standard Spark function
                    # Only check if it looks like a column expression call
                    if self._is_column_context_call(child):
                        if func_name not in self._STANDARD_SPARK_FUNCTIONS:
                            return True
        return False

    def _get_call_name(self, call: ast.Call) -> str | None:
        """Extract the function name from a call node."""
        if isinstance(call.func, ast.Name):
            return call.func.id
        elif isinstance(call.func, ast.Attribute):
            return call.func.attr
        return None

    def _is_column_context_call(self, call: ast.Call) -> bool:
        """
        Check if a call is in a column expression context.

        This helps distinguish between:
        - my_udf(F.col("x"))  → column context, likely a UDF
        - helper_func(df)     → not column context, just a helper
        """
        # If any argument is a Spark column function, we're in column context
        for arg in call.args:
            if isinstance(arg, ast.Call):
                name = self._get_call_name(arg)
                if name in {"col", "column", "lit", "when", "expr"}:
                    return True
                # F.col("x") pattern
                if isinstance(arg.func, ast.Attribute):
                    if arg.func.attr in self._STANDARD_SPARK_FUNCTIONS:
                        return True
        return False

    _STANDARD_SPARK_FUNCTIONS = ALL_SPARK_NAMES | ALL_PANDAS_NAMES

    def _is_read_chain(self, node: ast.Call) -> bool:
        """Check if this call is part of a spark.read chain."""
        current = node.func
        while isinstance(current, ast.Attribute):
            if current.attr == "read":
                return True
            if isinstance(current.value, ast.Call):
                current = current.value.func
            elif isinstance(current.value, ast.Attribute):
                current = current.value
            else:
                break
        return False

    def _is_partial_reader(self, node: ast.Call) -> bool:
        """
        Check if this call is a partial spark.read chain (no terminal method).
        
        A partial reader is spark.read.option().format() etc. without
        a terminal method like .csv(), .load(), .parquet(), etc.
        """
        # Must be a read chain
        if not self._is_read_chain(node):
            return False
        
        # Terminal methods that complete a read
        terminal_methods = {"csv", "parquet", "json", "orc", "text", "load", "table", "jdbc"}
        
        # Get the outermost method name
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            # If it's a terminal method, it's NOT partial
            return method not in terminal_methods
        
        return False

    def _is_partial_reader_completion(self, node: ast.Call) -> bool:
        """
        Check if this call completes a partial reader.
        
        Example: data_table_read.csv("path") where data_table_read was
        assigned as spark.read.option().option()
        """
        if not isinstance(node.func, ast.Attribute):
            return False
        
        # Get the base (what .csv() is called on)
        base = node.func.value
        
        # Must be a Name (variable reference)
        if not isinstance(base, ast.Name):
            return False
        
        var_name = base.id
        
        # Check if this variable is a registered partial reader
        if var_name in self._partial_readers:
            return True
        
        # Also check file-scoped key
        file_key = f"{self._current_filepath}:{var_name}"
        if file_key in self._partial_readers:
            return True
        
        return False

    def _is_write_chain(self, node: ast.Call) -> bool:
        """Check if this call is part of a df.write chain."""
        current = node.func
        while isinstance(current, ast.Attribute):
            if current.attr == "write":
                return True
            if isinstance(current.value, ast.Call):
                current = current.value.func
            elif isinstance(current.value, ast.Attribute):
                current = current.value
            else:
                break
        return False

    def _is_spark_session_call(self, node: ast.Call) -> bool:
        """Check if this call is on a SparkSession (e.g., spark.sql())."""
        if not isinstance(node.func, ast.Attribute):
            return False
        # Check if the base is a Name that looks like a SparkSession
        base = node.func.value
        if isinstance(base, ast.Name):
            # Common SparkSession variable names
            return base.id.lower() in ("spark", "session", "spark_session", "sparksession")
        return False


    # =========================================================================
    # Call classification & processing
    # =========================================================================

    # =========================================================================
    # Call Classification (Unified Entry Point)
    # =========================================================================

    def _classify_call(self, node: ast.Call) -> tuple[CallType, dict[str, Any]]:
        """
        Classify a function/method call for unified processing.
        
        This is the central decision point for all calls. Instead of scattered
        checks throughout the codebase, all classification logic is here.
        
        Args:
            node: The ast.Call node to classify
            
        Returns:
            Tuple of (CallType, metadata dict with relevant info)
        """
        metadata: dict[str, Any] = {
            "func_name": None,
            "method_name": None,
            "receiver": None,
            "receiver_type": None,
            "containing_class": None,
        }
        
        # Case 1: Direct function call - func(args)
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            metadata["func_name"] = func_name
            
            # Check builtins
            if func_name in self._BUILTINS_TO_SKIP:
                return CallType.BUILTIN, metadata
            
            # Check if it's a known user function
            known_func_names = {f.get("name") for f in self.functions}
            if func_name in known_func_names:
                return CallType.USER_FUNCTION, metadata
            
            # Check if imported
            is_imported = any(
                func_name in entry.get("imported_names", [])
                for entry in self.imports.values()
            )
            if is_imported:
                return CallType.USER_FUNCTION, metadata
            
            return CallType.UNKNOWN, metadata
        
        # Case 2: Method call - receiver.method(args)
        if isinstance(node.func, ast.Attribute):
            method_name = node.func.attr
            metadata["method_name"] = method_name
            receiver = node.func.value
            
            # Get receiver name if it's a simple Name
            if isinstance(receiver, ast.Name):
                metadata["receiver"] = receiver.id
                
                # Check for spark.read.*, spark.table(), spark.sql()
                if receiver.id == "spark":
                    if method_name in ("read", "table", "sql", "createDataFrame"):
                        return CallType.SPARK_READ, metadata
                
                # Check for F.col(), F.lit() - column expressions
                if TypeTracker.is_pyspark_functions(receiver.id):
                    return CallType.COLUMN_EXPR, metadata
                
                # Check for Window.partitionBy()
                if receiver.id == "Window":
                    return CallType.WINDOW_SPEC, metadata
            
            # Check for df.write.*
            if method_name == "write":
                return CallType.SPARK_WRITE, metadata
            if method_name in ("save", "saveAsTable", "insertInto", "jdbc", "parquet", "csv", "json"):
                # Check if this is part of a write chain
                if self._is_write_chain(node):
                    return CallType.SPARK_WRITE, metadata
            
            # Check for DataFrame transformations
            if method_name in self._TRANSFORM_METHODS:
                # Use type inference to confirm it's a DataFrame
                receiver_type = self._infer_receiver_type(receiver)
                metadata["receiver_type"] = receiver_type
                
                if receiver_type in ("str", "int", "float", "list", "dict", "tuple", "set"):
                    # Not a DataFrame operation
                    return CallType.UNKNOWN, metadata
                
                return CallType.SPARK_TRANSFORM, metadata
            
            # Check for column expression chains
            if self._is_column_expression(node):
                return CallType.COLUMN_EXPR, metadata
            
            # Check if this is a method of a known class (user method)
            containing_class = self._find_method_class(method_name)
            if containing_class:
                metadata["containing_class"] = containing_class
                return CallType.USER_METHOD, metadata
            
            # Capture method calls on instance variables even without known class
            # This catches utility class methods like my_utils.save_data()
            # Heuristic: receiver is set and method looks like a write/update op
            receiver = metadata.get("receiver")
            if receiver:
                LIKELY_USER_METHODS = [
                    'update', 'save', 'write', 'insert', 'delete', 'load', 'read',
                    'process', 'transform', 'convert', 'create', 'build', 'execute',
                    'run', 'call', 'apply', 'get', 'set', 'fetch', 'send', 'push',
                    'data_update', 'overwrite',
                ]
                method_lower = method_name.lower()
                if any(m in method_lower for m in LIKELY_USER_METHODS):
                    metadata["containing_class"] = "UnknownClass"
                    return CallType.USER_METHOD, metadata
            
            return CallType.UNKNOWN, metadata
        
        return CallType.UNKNOWN, metadata

    def _find_method_class(self, method_name: str) -> str | None:
        """
        Find which class contains a method with the given name (heuristic).
        
        First checks local functions (current file), then global SymbolTable
        for cross-file methods.
        
        Returns the class name if found, None otherwise.
        """
        # Check local functions first (current file)
        for func in self.functions:
            containing_class = func.get("containing_class")
            if containing_class and func.get("name") == method_name:
                return containing_class
        
        # Check global SymbolTable for cross-file methods
        func_sig = SymbolTable._global_functions.get(method_name)
        if func_sig and func_sig.containing_class:
            return func_sig.containing_class
        
        return None

    def _process_call(self, node: ast.Call) -> None:
        """
        Unified entry point for processing all function/method calls.
        
        This method classifies the call and delegates to the appropriate handler.
        Currently, it delegates to existing logic; future iterations will
        consolidate all call processing here.
        
        Args:
            node: The ast.Call node to process
        """
        call_type, metadata = self._classify_call(node)
        
        match call_type:
            case CallType.SPARK_READ:
                # Handled by _handle_data_sources in visit_Call
                pass
            
            case CallType.SPARK_WRITE:
                # Handled by _handle_data_sinks in visit_Call
                pass
            
            case CallType.SPARK_TRANSFORM:
                # Handled by _capture_transformation in visit_Call/visit_Assign
                pass
            
            case CallType.USER_FUNCTION:
                # Capture call site for cross-function lineage
                self._capture_call_site(node)
            
            case CallType.USER_METHOD:
                # Capture method calls for cross-function lineage
                method_name = metadata.get("method_name")
                containing_class = metadata.get("containing_class")
                if method_name:
                    self._capture_method_call_site(node, method_name, containing_class)
            
            case CallType.COLUMN_EXPR:
                # Column expressions are handled in transformation context
                pass
            
            case CallType.WINDOW_SPEC:
                # Window specs are captured when encountered
                pass
            
            case CallType.BUILTIN | CallType.UNKNOWN:
                # Nothing to do for builtins or unknown calls
                pass

    # Builtins to skip
    _BUILTINS_TO_SKIP: ClassVar[set[str]] = {
        "print", "len", "range", "str", "int", "float", "bool", "list",
        "dict", "set", "tuple", "open", "type", "isinstance", "hasattr",
        "getattr", "setattr", "enumerate", "zip", "map", "filter", "sorted",
        "min", "max", "sum", "any", "all", "abs", "round", "format",
    }


    # =========================================================================
    # Call site capture (cross-function lineage)
    # =========================================================================

    def _capture_call_site(self, node: ast.Call) -> None:
        """
        Capture calls to known functions for cross-function lineage resolution.

        When we see `result = my_function(df_a, df_b)`, we record:
        - Which function was called
        - What arguments were passed (mapped to their real IDs)
        - What variable receives the result
        - Metadata about nested transformations in arguments

        For nested chains like: result = process(df.filter(col("x") > 0))
        The chain is processed first, creating tx_XXX, then we record
        that the argument resolved to that transformation node.
        """
        # Capture calls to functions defined in this file OR imported functions
        if not isinstance(node.func, ast.Name):
            return

        func_name = node.func.id

        # Check if it's a known function (defined in this file)
        known_func_names = {f.get("name") for f in self.functions}
        is_known_function = func_name in known_func_names

        # Check if it's an imported function (from imports)
        is_imported_function = any(
            func_name in entry.get("imported_names", [])
            for entry in self.imports.values()
        )

        # If it's a known or imported function, capture it regardless of name conflicts
        if not is_known_function and not is_imported_function:
            # Skip Python builtins and common non-function calls
            BUILTINS_TO_SKIP = {
                "print", "len", "range", "str", "int", "float", "bool", "list",
                "dict", "set", "tuple", "open", "type", "isinstance", "hasattr",
                "getattr", "setattr", "enumerate", "zip", "map", "filter", "sorted",
                "min", "max", "sum", "any", "all", "abs", "round", "format",
            }
            if func_name in BUILTINS_TO_SKIP:
                return

            # Skip Spark column functions (F.col, lit, etc.) - they're not user functions
            if func_name in self._STANDARD_SPARK_FUNCTIONS:
                return

            # Unknown function - skip it
            return

        # Build argument bindings: param_name -> real_id
        argument_bindings: dict[str, str] = {}
        # Track metadata about how arguments were resolved
        argument_metadata: dict[str, dict] = {}
        param_names = self._get_function_param_names(func_name, len(node.args))

        # Process positional arguments
        for i, arg in enumerate(node.args):
            if i < len(param_names):
                param_name = param_names[i]

                # Save state to detect if new nodes are created
                prev_last_id = self._last_node_id

                real_id = self._resolve_argument_to_id(arg)
                if real_id:
                    argument_bindings[param_name] = real_id

                    # Track metadata about how this was resolved
                    if isinstance(arg, ast.Call) and self._last_node_id != prev_last_id:
                        # Argument was a nested transformation
                        argument_metadata[param_name] = {
                            "resolved_from": "nested_transformation",
                            "created_node": self._last_node_id,
                        }
                    elif isinstance(arg, ast.Name):
                        argument_metadata[param_name] = {
                            "resolved_from": "variable",
                            "original_name": arg.id,
                        }

        # Process keyword arguments (also use _resolve_argument_to_id)
        for kw in node.keywords:
            if kw.arg:
                prev_last_id = self._last_node_id
                real_id = self._resolve_argument_to_id(kw.value)
                if real_id:
                    argument_bindings[kw.arg] = real_id

                    # Track metadata
                    if isinstance(kw.value, ast.Call) and self._last_node_id != prev_last_id:
                        argument_metadata[kw.arg] = {
                            "resolved_from": "nested_transformation",
                            "created_node": self._last_node_id,
                        }
                    elif isinstance(kw.value, ast.Name):
                        argument_metadata[kw.arg] = {
                            "resolved_from": "variable",
                            "original_name": kw.value.id,
                        }

        # Resolve string literals for variable arguments
        resolved_literals: dict[str, str] = {}
        scope = self._current_function or GLOBAL_SCOPE
        for param_name, meta in argument_metadata.items():
            if meta.get("resolved_from") == "variable":
                original_name = meta.get("original_name")
                if original_name:
                    literal_value = SymbolTable.resolve_string_literal(scope, original_name)
                    if literal_value:
                        resolved_literals[param_name] = literal_value
        
        # Also try to resolve literals passed directly as arguments
        for i, arg in enumerate(node.args):
            if i < len(param_names):
                param_name = param_names[i]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    resolved_literals[param_name] = arg.value
        
        # Record the call-site with enhanced metadata
        call_site = {
            "function_name": func_name,
            "argument_bindings": argument_bindings,
            "argument_metadata": argument_metadata,
            "resolved_literals": resolved_literals,  # NEW: resolved string values
            "output_variable": self._current_assignment_target,
            "line_number": node.lineno,
            "caller_function": self._current_function,
        }
        self.call_sites.append(call_site)
        
        # Register in global symbol table for cross-file resolution
        SymbolTable.register_call_site(call_site)

    def _capture_method_call_site(
        self, node: ast.Call, method_name: str, containing_class: str | None
    ) -> None:
        """
        Capture calls to methods (obj.method()) for cross-function lineage.
        
        Similar to _capture_call_site but handles instance method calls.
        
        Args:
            node: The ast.Call node
            method_name: Name of the method being called
            containing_class: Class that contains the method (if known)
        """
        # Get instance variable name if available
        instance_var = None
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            instance_var = node.func.value.id
        
        # Build argument bindings
        argument_bindings: dict[str, str] = {}
        argument_metadata: dict[str, dict] = {}
        literal_arguments: dict[str, str] = {}  # For string/query/path arguments
        
        # Get parameter names (excluding 'self')
        param_names = self._get_method_param_names(method_name, containing_class, len(node.args))
        
        # Process positional arguments
        for i, arg in enumerate(node.args):
            if i < len(param_names):
                param_name = param_names[i]
                prev_last_id = self._last_node_id
                real_id = self._resolve_argument_to_id(arg)
                if real_id:
                    argument_bindings[param_name] = real_id
                    
                    if isinstance(arg, ast.Call) and self._last_node_id != prev_last_id:
                        argument_metadata[param_name] = {
                            "resolved_from": "nested_transformation",
                            "created_node": self._last_node_id,
                        }
                    elif isinstance(arg, ast.Name):
                        argument_metadata[param_name] = {
                            "resolved_from": "variable",
                            "original_name": arg.id,
                        }
                else:
                    # Capture literal values (strings, f-strings, queries, paths)
                    literal_val = self._extract_string_arg([arg], 0, capture_runtime=True)
                    if literal_val:
                        literal_arguments[param_name] = literal_val
        
        # Process keyword arguments
        for kw in node.keywords:
            if kw.arg:
                prev_last_id = self._last_node_id
                real_id = self._resolve_argument_to_id(kw.value)
                if real_id:
                    argument_bindings[kw.arg] = real_id
                    
                    if isinstance(kw.value, ast.Call) and self._last_node_id != prev_last_id:
                        argument_metadata[kw.arg] = {
                            "resolved_from": "nested_transformation",
                            "created_node": self._last_node_id,
                        }
                    elif isinstance(kw.value, ast.Name):
                        argument_metadata[kw.arg] = {
                            "resolved_from": "variable",
                            "original_name": kw.value.id,
                        }
                else:
                    # Capture literal values (strings, f-strings, queries, paths)
                    literal_val = self._extract_string_arg([kw.value], 0, capture_runtime=True)
                    if literal_val:
                        literal_arguments[kw.arg] = literal_val
        
        # Resolve string literals for variable arguments  
        resolved_literals: dict[str, str] = {}
        scope = self._current_function or GLOBAL_SCOPE
        for param_name, meta in argument_metadata.items():
            if meta.get("resolved_from") == "variable":
                original_name = meta.get("original_name")
                if original_name:
                    literal_value = SymbolTable.resolve_string_literal(scope, original_name)
                    if literal_value:
                        resolved_literals[param_name] = literal_value
        
        # Merge: resolved literals + direct literals
        all_literals = {**literal_arguments, **resolved_literals}
        
        # Record the call-site with method-specific metadata
        call_site = {
            "function_name": method_name,
            "containing_class": containing_class,
            "instance_variable": instance_var,
            "argument_bindings": argument_bindings,
            "argument_metadata": argument_metadata,
            "literal_arguments": all_literals,  # Merged: direct + resolved
            "resolved_literals": resolved_literals,  # Variable-resolved values
            "output_variable": self._current_assignment_target,
            "line_number": node.lineno,
            "caller_function": self._current_function,
            "source_file": self._current_filepath,
            "call_type": "method",
        }
        self.call_sites.append(call_site)
        
        # Register in global symbol table for cross-file resolution
        SymbolTable.register_call_site(call_site)

    def _get_method_param_names(
        self, method_name: str, containing_class: str | None, arg_count: int = 0
    ) -> list[str]:
        """
        Get ordered list of parameter names for a method (excluding 'self').
        
        Args:
            method_name: Name of the method
            containing_class: Class that contains the method
            arg_count: Number of positional arguments (used for fallback)
            
        Returns:
            List of parameter names (without 'self')
        """
        for func in self.functions:
            if func.get("name") == method_name:
                # If we know the class, match it
                if containing_class and func.get("containing_class") != containing_class:
                    continue
                args = func.get("arguments", [])
                # Filter out 'self'
                return [
                    arg.get("name") for arg in args
                    if arg.get("name") and arg.get("name") != "self"
                ]
        
        # Fallback: generate synthetic names
        return [f"arg_{i}" for i in range(arg_count)]

    def _get_function_param_names(self, func_name: str, arg_count: int = 0) -> list[str]:
        """
        Get ordered list of parameter names for a function.

        If the function is not found (e.g., imported from another file),
        generates synthetic parameter names based on arg_count.

        Args:
            func_name: Name of the function
            arg_count: Number of positional arguments (used for fallback)

        Returns:
            List of parameter names
        """
        for func in self.functions:
            if func.get("name") == func_name:
                args = func.get("arguments", [])
                return [arg.get("name") for arg in args if arg.get("name")]

        # Fallback for imported/unknown functions: generate synthetic names
        # These will be resolved later during multi-file merge
        if arg_count > 0:
            return [f"arg_{i}" for i in range(arg_count)]

        return []


    # =========================================================================
    # Source & variable resolution
    # =========================================================================

    def _resolve_write_source(self, node: ast.Call) -> str | None:
        """
        Resolve the source transformation for a write operation.

        For `df.write.mode("overwrite").saveAsTable(...)`, find the last 
        transformation that produced `df` and return its ID.

        Handles chains like: df.write.mode(...).saveAsTable(...)
        Also handles cross-function lineage.
        """
        # Navigate through the chain to find .write and then the DataFrame
        current = node
        
        # First, navigate through Call nodes to find the Attribute chain
        while isinstance(current, ast.Call):
            current = current.func
        
        # Now navigate through Attribute nodes to find .write
        while isinstance(current, ast.Attribute):
            if current.attr == "write":
                # Found .write, the value should be the DataFrame
                df_node = current.value
                if isinstance(df_node, ast.Name):
                    var_name = df_node.id
                    # First try direct symbol table lookup
                    source_id = self.symbol_table.get(var_name)
                    if source_id:
                        return source_id
                    # Try to resolve through function call
                    return self._resolve_through_function_call(var_name)
                elif isinstance(df_node, ast.Call):
                    # Chain like df.filter().write - resolve the chain
                    resolved = self.symbol_table.resolve_inputs(df_node)
                    return resolved[0] if resolved else None
                break
            
            # Navigate deeper: check if value is a Call (like .mode(...))
            if isinstance(current.value, ast.Call):
                # Get the func of the call to continue navigating
                current = current.value.func
            elif isinstance(current.value, ast.Attribute):
                current = current.value
            else:
                break
        
        return None

    def _resolve_through_function_call(self, var_name: str) -> str | None:
        """
        Resolve a variable that was assigned from a function call.

        Given `result = my_function(...)`, trace through my_function's
        return to find the actual transformation ID.
        """
        # Find the assignment in the AST where var_name was assigned
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == var_name:
                        if isinstance(node.value, ast.Call):
                            # It's a function call - find the function name
                            func_name = self._get_call_name(node.value)
                            if func_name:
                                return self._get_function_return_source(func_name)
        return None

    def _get_function_return_source(self, func_name: str) -> str | None:
        """
        Get the source_id from a function's return.

        Recursively traces through function returns until finding a tx_xxx ID.
        Falls back to finding the last transformation within the function scope.
        """
        # Find the function in our parsed functions
        for func in self.functions:
            if func.get("name") == func_name:
                returns = func.get("returns", {})
                ref_type = returns.get("ref_type")
                ref_id = returns.get("ref_id")

                # If it returns a transformation, we're done
                if ref_type == "transformation" and ref_id and ref_id.startswith("tx_"):
                    return ref_id

                # If it returns a variable, trace through that variable
                if ref_type == "variable" and ref_id:
                    # The variable might be from another function call
                    # Check if this variable was assigned inside the function
                    inner_source = self._trace_variable_in_function(func_name, ref_id)
                    if inner_source:
                        return inner_source
                    
                    # Fallback: find the last transformation within the function's line range
                    func_start = func.get("line_start")
                    func_end = func.get("line_end")
                    if func_start and func_end:
                        last_tx = self._find_last_tx_in_range(func_start, func_end)
                        if last_tx:
                            return last_tx

        return None

    def _find_last_tx_for_variable(self, var_name: str) -> str | None:
        """
        Find the last transformation that outputs to a variable.
        
        Searches through all parsed transformations to find which one
        has var_name in its outputs.
        """
        # Search in reverse order to find the LAST transformation
        for tx in reversed(self.transformations):
            outputs = tx.get('outputs', [])
            if var_name in outputs:
                return tx.get('id')
        return None

    def _find_last_tx_in_range(self, start_line: int, end_line: int) -> str | None:
        """
        Find the last transformation within a line range.
        
        Used as fallback when we can't trace a variable to its transformation.
        """
        last_tx_id = None
        last_line = 0
        
        for tx in self.transformations:
            loc = tx.get('location', {})
            # Parse line from span string "line:col-line:col"
            span = loc.get('span', '')
            if span:
                try:
                    line_start = int(span.split(':')[0])
                except (ValueError, IndexError):
                    line_start = 0
            else:
                line_start = 0
            
            if start_line <= line_start <= end_line:
                if line_start >= last_line:
                    last_line = line_start
                    last_tx_id = tx.get('id')
        
        return last_tx_id

    def _trace_variable_in_function(self, func_name: str, var_name: str) -> str | None:
        """
        Trace a variable inside a function to find its source.

        If `var_name` was assigned from another function call, recursively trace.
        Also checks the symbol table for direct transformation mappings.
        """
        # First, check symbol table for direct mapping
        source_id = self.symbol_table.get(var_name)
        if source_id and source_id.startswith("tx_"):
            return source_id
        
        # Check if any transformation outputs this variable
        tx_source = self._find_last_tx_for_variable(var_name)
        if tx_source:
            return tx_source
        
        # Find the function definition
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                # Find assignments to var_name within this function
                for stmt in ast.walk(node):
                    if isinstance(stmt, ast.Assign):
                        for target in stmt.targets:
                            if isinstance(target, ast.Name) and target.id == var_name:
                                if isinstance(stmt.value, ast.Call):
                                    inner_func = self._get_call_name(stmt.value)
                                    if inner_func:
                                        return self._get_function_return_source(inner_func)
                                    # Not a function call - try to resolve from symbol table
                                    resolved = self.symbol_table.resolve_inputs(stmt.value)
                                    if resolved:
                                        return resolved[0]
        return None

    def _resolve_argument_to_id(self, arg: ast.AST) -> str | None:
        """
        Resolve a function argument to its real node ID.

        Handles:
        - Case 1: Simple variable (df_sales) -> symbol table lookup
        - Case 2: Spark chain (df.filter(...).select(...)) -> process chain, return last node
        - Case 3: Attribute (df.column) -> lookup the base variable
        - Case 4: Fallback for unknown expressions

        This is vital for handling nested transformations in function arguments:
        enriched_df = obfuscate(df_sales.filter(col("amount") > 0))
        The filter chain must be processed first, creating tx_010,
        and then the call_site records {"df": "tx_010"}.
        """
        # Case 1: Simple variable (df_sales)
        if isinstance(arg, ast.Name):
            # Try file-scoped lookup first (most precise)
            file_context = getattr(self, '_current_filepath', '')
            binding = SymbolTable.resolve_source(arg.id, file_context=file_context)
            if binding:
                return binding.source_id
            
            # Fallback to local symbol table
            resolved = self.symbol_table.get(arg.id)
            return resolved if resolved else arg.id

        # Case 2: Spark chain (df.filter(...).select(...))
        if isinstance(arg, ast.Call) and self._is_spark_operation(arg):
            # Save the ID before processing to detect new nodes
            prev_id = self._last_node_id

            # Process the chain: this creates tx_010, tx_011, etc.
            self._process_spark_chain(arg)

            # If a new node was created, that's our argument ID
            if self._last_node_id and self._last_node_id != prev_id:
                return self._last_node_id

            # Fallback: try to resolve via symbol table
            inputs = self.symbol_table.resolve_inputs(arg)
            return inputs[0] if inputs else None

        # Case 3: Attribute access (df.column - less common as DF but possible)
        if isinstance(arg, ast.Attribute):
            if isinstance(arg.value, ast.Name):
                return self.symbol_table.get(arg.value.id)
            return None

        # Case 4: Generic Call (not a Spark operation, maybe a function call)
        if isinstance(arg, ast.Call):
            # Try to get the function return ID if it's a known function
            if isinstance(arg.func, ast.Name):
                return_id = self._get_function_call_return_id(arg)
                if return_id:
                    return return_id
            # Fallback to symbol table resolution
            inputs = self.symbol_table.resolve_inputs(arg)
            return inputs[0] if inputs else None

        # Case 5: Literal constant (string, int, etc.)
        # Used for table names like _read_table("sales_data")
        if isinstance(arg, ast.Constant):
            if isinstance(arg.value, str):
                return arg.value  # Return the literal string value
            return str(arg.value)  # Convert other types to string

        return None

    def _get_function_call_return_id(self, call: ast.Call) -> str | None:
        """
        Get the return ID of a function call if the function is known.

        When we see `result = my_function(...)` or `result = obj.method(...)`,
        look up the function/method's return ref_id to register in the symbol table.

        Handles:
        - transformation returns (ref_type == "transformation")
        - variable returns (ref_type == "variable") by tracing the variable
        - data_source returns (ref_type == "data_source") for spark.read wrappers
        """
        # Extract function/method name
        func_name = None
        if isinstance(call.func, ast.Name):
            func_name = call.func.id
        elif isinstance(call.func, ast.Attribute):
            # obj.method() - get method name
            func_name = call.func.attr

        if not func_name:
            return None

        # Look up in current file's functions first
        for func in self.functions:
            func_name_attr = func.get("name") if isinstance(func, dict) else getattr(func, "name", None)
            if func_name_attr == func_name:
                returns = func.get("returns") if isinstance(func, dict) else getattr(func, "returns", None)
                if not returns:
                    continue
                if isinstance(returns, dict):
                    ref_type_str = returns.get("ref_type")
                    ref_id = returns.get("ref_id")
                else:
                    ref_type = getattr(returns, "ref_type", None)
                    ref_id = getattr(returns, "ref_id", None)
                    ref_type_str = getattr(ref_type, "value", ref_type) if ref_type else None

                if ref_type_str == "transformation" and ref_id and ref_id.startswith("tx_"):
                    return ref_id
                if ref_type_str == "data_source" and ref_id and ref_id.startswith("in_"):
                    return ref_id
                if ref_type_str == "variable" and ref_id:
                    return self._trace_variable_to_transformation(func_name, ref_id)

        # Look up in global SymbolTable for cross-file resolution
        sig = SymbolTable._global_functions.get(func_name)
        if sig:
            ref_id = sig.returns_id
            ref_type = sig.returns_type
            if ref_type == "transformation" and ref_id and ref_id.startswith("tx_"):
                return ref_id
            if ref_type == "data_source" and ref_id and ref_id.startswith("in_"):
                return ref_id
            if ref_type == "variable" and ref_id:
                return self._trace_variable_to_transformation(func_name, ref_id)

        return None

    def _resolve_table_read_call(self, call: ast.Call, var_name: str) -> str | None:
        """
        Resolve a function call that reads a table to its source ID.
        
        Handles patterns like:
            df_products = _read_table("products_data")
            df = read_data("table_name")
        
        By finding the source with a matching name from the call arguments.
        """
        if not isinstance(call.func, ast.Name):
            return None
        
        func_name = call.func.id
        
        # Common patterns for table-reading functions
        table_read_funcs = {"_read_table", "read_table", "read_data", "load_table"}
        if func_name not in table_read_funcs:
            return None
        
        # Get the first string argument (table name)
        table_name = None
        if call.args:
            first_arg = call.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                table_name = first_arg.value
        
        if not table_name:
            return None
        
        # Find a source with this name
        for source in self.data_in:
            # Handle both dict and Pydantic model
            if isinstance(source, dict):
                source_name = source.get("name") or ""
                source_path = source.get("path") or ""
                source_id = source.get("id")
            else:
                source_name = getattr(source, "name", "") or ""
                source_path = getattr(source, "path", "") or ""
                source_id = getattr(source, "id", "")
            
            if table_name == source_name or table_name in source_path:
                # Register this mapping globally
                SymbolTable.register_source(
                    var_name=var_name,
                    source_id=source_id,
                    source_name=source_name,
                    file=self._current_filepath,
                )
                return source_id
        
        return None

    def _trace_variable_to_transformation(self, func_name: str, var_name: str) -> str | None:
        """
        Trace a variable inside a function to find the node (transformation or source) that created it.

        This handles cases like:
            def my_func(df):
                result = df.filter(...).withColumn(...)  # Creates tx_xxx
                return result  # Returns variable, not chain
            
            def _read_table(name):
                df = spark.read.table(name)  # Creates in_xxx
                return df  # Returns variable pointing to source
        """
        # First, try to resolve using the global SymbolTable (most reliable)
        resolved = SymbolTable.resolve_var_to_node(func_name, var_name)
        if resolved:
            return resolved

        # Fallback: Look for the function's line range and find nodes by line
        func_line_range = None
        for func in self.functions:
            if func.get("name") == func_name:
                func_line_range = (func.get("line_start"), func.get("line_end"))
                break

        if not func_line_range:
            return None

        # Find transformations within this function (only if line_start is set)
        func_txs = [
            tx
            for tx in self.transformations
            if tx.get("line_start") and func_line_range[0] <= tx.get("line_start", 0) <= func_line_range[1]
        ]

        # Return the last transformation in the function (usually the one that's returned)
        if func_txs:
            return func_txs[-1].get("id")

        # If no transformations, check for sources created in this function
        # This handles cases like: df = spark.read.table(...)
        func_sources = [
            src
            for src in self.data_in
            if src.get("line_start") and func_line_range[0] <= src.get("line_start", 0) <= func_line_range[1]
        ]
        
        if func_sources:
            return func_sources[-1].get("id")

        return None


    # =========================================================================
    # Control flow — if/for/while/try/with
    # =========================================================================

    # Control flow methods that indicate DataFrame usage (not transformations)
    _CONTROL_FLOW_METHODS = {
        "isEmpty", "count", "first", "collect", "take", "head",
        "foreach", "foreachPartition", "toLocalIterator",
        "show", "printSchema", "explain", "dtypes", "columns",
    }

    def _detect_control_flow_usage(self, node: ast.Call) -> None:
        """
        Detect when a DataFrame is used in control flow operations.
        
        Patterns:
        - df.rdd.isEmpty()
        - df.count()
        - df.first()
        - df.collect()
        
        When detected, register the source_id to prevent LIN_002.
        """
        if not isinstance(node.func, ast.Attribute):
            return
        
        method = node.func.attr
        base = node.func.value
        
        # Check for .rdd.method() pattern (e.g., df.rdd.isEmpty())
        if isinstance(base, ast.Attribute) and base.attr == "rdd":
            # base.value is the DataFrame variable
            df_var = base.value
            if isinstance(df_var, ast.Name):
                self._register_control_usage_for_var(df_var.id)
            return
        
        # Check for df.count(), df.first(), etc.
        if method in self._CONTROL_FLOW_METHODS:
            if isinstance(base, ast.Name):
                self._register_control_usage_for_var(base.id)
            return

    def _register_control_usage_for_var(self, var_name: str) -> None:
        """Register control flow usage for a variable by finding its source_id."""
        # Look up the variable in SymbolTable
        file_key = f"{self._current_filepath}:{var_name}"
        binding = SymbolTable._global_sources.get(file_key) or SymbolTable._global_sources.get(var_name)
        
        if binding and binding.source_id:
            SymbolTable.register_control_usage(binding.source_id)

    # Standard library modules to ignore (not Spark operations)

    # =========================================================================
    # Control Flow Visitors - Extract if/match/for/while/try/with structures
    # =========================================================================

    def _detect_loop_type(self, iter_node: ast.AST) -> str:
        """
        Detect loop type by analyzing the iterator expression using AST.
        Returns: CODE_GENERATION, TABLE_ITERATION, or DATA_ITERATION
        """
        for node in ast.walk(iter_node):
            if isinstance(node, ast.Call):
                func_name = None
                # Direct call: range(), glob()
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                # Attribute call: os.listdir(), pathlib.glob()
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                
                if func_name:
                    if func_name == "range":
                        return "CODE_GENERATION"
                    elif func_name in ("glob", "listdir", "rglob", "iterdir"):
                        return "TABLE_ITERATION"
        
        # Check for static list/tuple
        if isinstance(iter_node, (ast.List, ast.Tuple)):
            return "CODE_GENERATION"
        
        return "DATA_ITERATION"

    def _extract_static_iterable(self, iter_node: ast.AST) -> tuple[bool, list | None]:
        """
        Extract static iterable values from a loop iterator using AST.
        Returns: (is_unrollable, static_values)
        
        Examples:
            - ['a', 'b', 'c'] -> (True, ['a', 'b', 'c'])
            - range(3) -> (True, [0, 1, 2])
            - range(2, 5) -> (True, [2, 3, 4])
            - df.collect() -> (False, None)
        """
        # Static list: ['a', 'b', 'c']
        if isinstance(iter_node, ast.List):
            try:
                values = []
                for elt in iter_node.elts:
                    if isinstance(elt, ast.Constant):
                        values.append(elt.value)
                    else:
                        return False, None  # Non-constant element
                return True, values
            except Exception:
                return False, None
        
        # Static tuple: ('a', 'b', 'c')
        if isinstance(iter_node, ast.Tuple):
            try:
                values = []
                for elt in iter_node.elts:
                    if isinstance(elt, ast.Constant):
                        values.append(elt.value)
                    else:
                        return False, None
                return True, values
            except Exception:
                return False, None
        
        # range() call
        if isinstance(iter_node, ast.Call):
            if isinstance(iter_node.func, ast.Name) and iter_node.func.id == "range":
                args = iter_node.args
                try:
                    if len(args) == 1 and isinstance(args[0], ast.Constant):
                        # range(n)
                        return True, list(range(args[0].value))
                    elif len(args) >= 2:
                        if isinstance(args[0], ast.Constant) and isinstance(args[1], ast.Constant):
                            start, end = args[0].value, args[1].value
                            step = 1
                            if len(args) >= 3 and isinstance(args[2], ast.Constant):
                                step = args[2].value
                            # range(start, end, step)
                            return True, list(range(start, end, step))
                except Exception:
                    return False, None
        
        return False, None

    def _detect_opaque_code(self, body: list[ast.stmt]) -> tuple[str | None, str | None]:
        """
        Analyze a block of statements to determine opacity code.
        Returns: (opaque_code, opaque_reason)
        
        Codes:
        - UNSUPPORTED_LIB: Uses library not available in Snowflake
        - IO_SIDE_EFFECT: File I/O, network, email
        - COMPLEX_RECURSION: Recursive function calls
        - DYNAMIC_SCHEMA: Schema determined at runtime
        - EXTERNAL_API: HTTP/REST API calls
        - STATEFUL_ITERATION: Loop with accumulating state
        """
        # Libraries that indicate opacity
        unsupported_libs = {'pandas', 'numpy', 'scipy', 'sklearn', 'tensorflow', 'torch', 'keras'}
        io_patterns = {'open', 'write', 'read', 'send', 'smtp', 'requests', 'urllib', 'http'}
        api_patterns = {'requests', 'urllib', 'httpx', 'aiohttp', 'fetch'}
        
        for stmt in body:
            for node in ast.walk(stmt):
                # Check for function calls
                if isinstance(node, ast.Call):
                    func_name = None
                    module_name = None
                    
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr
                        if isinstance(node.func.value, ast.Name):
                            module_name = node.func.value.id
                    
                    # Check for I/O operations
                    if func_name and func_name.lower() in io_patterns:
                        return "IO_SIDE_EFFECT", f"I/O operation detected: {func_name}"
                    
                    # Check for API calls
                    if module_name and module_name.lower() in api_patterns:
                        return "EXTERNAL_API", f"External API call: {module_name}.{func_name}"
                    
                    # Check for recursive calls (self-reference in function)
                    # This is simplified - real detection would need function context
                
                # Check for imports of unsupported libraries
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split('.')[0] in unsupported_libs:
                            return "UNSUPPORTED_LIB", f"Unsupported library: {alias.name}"
                
                if isinstance(node, ast.ImportFrom):
                    if node.module and node.module.split('.')[0] in unsupported_libs:
                        return "UNSUPPORTED_LIB", f"Unsupported library: {node.module}"
                
                # Check for dynamic schema (eval, exec, getattr with variable)
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id in ('eval', 'exec'):
                        return "DYNAMIC_SCHEMA", "Dynamic code execution with eval/exec"
        
        return None, None

    def _affects_dataframe(self, body: list[ast.stmt]) -> bool:
        """Check if a body of statements affects DataFrame lineage or data I/O."""
        _IO_METHODS = {
            "data_update_into_s3", "write_table", "truncate_and_write_table",
            "execute_query", "snowflake_update", "write_frum_restlist",
            "write_dataframe_in_rds", "overwrite_dataframe_in_rds",
            "read_dataframe", "read_df_from_rds",
        }
        for stmt in body:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call):
                    if self._is_spark_chain(node) or self._is_spark_operation(node):
                        return True
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr in _IO_METHODS:
                        return True
                    if isinstance(func, ast.Name) and func.id in _IO_METHODS:
                        return True
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            if "df" in target.id.lower() or "frame" in target.id.lower():
                                return True
        return False

    def _extract_branch_steps(self, body: list[ast.stmt]) -> list[str]:
        """
        Extract step IDs will be resolved in post-processing.
        Returns empty list; actual resolution happens in _resolve_branch_steps().
        """
        return []

    def _get_branch_line_range(self, body: list[ast.stmt]) -> tuple[int, int] | None:
        """Get the line range (start, end) for a branch body."""
        if not body:
            return None
        start_line = body[0].lineno
        end_line = body[-1].end_lineno or body[-1].lineno
        return (start_line, end_line)

    def _resolve_control_expression(self, expr_node: ast.AST) -> str | None:
        """
        Resolve a control expression by finding its definition in the source.
        
        For simple variables like 'is_latam', find the assignment that defines it
        and return the right-hand side expression.
        
        Returns resolved expression string or None if not resolvable.
        """
        if not isinstance(expr_node, ast.Name):
            # Already a complex expression, return as-is
            return None
        
        var_name = expr_node.id
        if_line = expr_node.lineno
        
        # Need source code to resolve expressions
        if not self._source_code:
            return None
        
        # Parse the source to find the function containing this if
        try:
            tree = ast.parse(self._source_code)
        except SyntaxError:
            return None
        
        # Find assignments to var_name that appear BEFORE the if statement
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                # Must be before the if statement
                if node.lineno >= if_line:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == var_name:
                        return ast.unparse(node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.lineno >= if_line:
                    continue
                if node.target.id == var_name and node.value:
                    return ast.unparse(node.value)
        
        return None

    def _determine_exit_strategy(self, branches: list[dict]) -> str:
        """Determine how branches exit."""
        targets = set()
        for branch in branches:
            if branch.get("target_variable"):
                targets.add(branch["target_variable"])
        
        if len(targets) == 1:
            return "MERGE"
        elif len(targets) > 1:
            return "INDEPENDENT_SINK"
        return "MERGE"

    def _get_target_variable(self, body: list[ast.stmt]) -> str | None:
        """Get the variable assigned in a branch body."""
        for stmt in body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        return target.id
        return None

    def _extract_columns_from_condition(self, condition_node: ast.AST) -> list[tuple[str, str]]:
        """
        Extract column references from a control condition using AST.
        
        Patterns detected:
        - df.col['column_name'] -> (df, column_name)
        - df['column_name'] -> (df, column_name)
        - col('column_name') with context -> (None, column_name)
        
        Returns list of (variable_name, column_name) tuples.
        """
        columns: list[tuple[str, str]] = []
        
        class ColumnVisitor(ast.NodeVisitor):
            def visit_Subscript(inner_self, node: ast.Subscript) -> None:
                # Pattern: df.col['column_name'] or df['column_name']
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    col_name = node.slice.value
                    
                    # df.col['col'] pattern
                    if isinstance(node.value, ast.Attribute) and node.value.attr == 'col':
                        if isinstance(node.value.value, ast.Name):
                            var_name = node.value.value.id
                            columns.append((var_name, col_name))
                    # df['col'] pattern (direct subscript on DataFrame)
                    elif isinstance(node.value, ast.Name):
                        var_name = node.value.id
                        columns.append((var_name, col_name))
                
                inner_self.generic_visit(node)
            
            def visit_Call(inner_self, node: ast.Call) -> None:
                # Pattern: col('column_name') - standalone column reference
                if isinstance(node.func, ast.Name) and node.func.id == 'col':
                    if node.args and isinstance(node.args[0], ast.Constant):
                        col_name = node.args[0].value
                        # No specific DataFrame, mark as None
                        columns.append((None, col_name))
                
                inner_self.generic_visit(node)
        
        visitor = ColumnVisitor()
        visitor.visit(condition_node)
        return columns

    def _propagate_control_columns_to_sources(self) -> None:
        """
        Post-processing: propagate columns from control conditions to DataSource.required_columns.
        
        For each control node, extract referenced columns and add them to the
        corresponding DataSource's required_columns with origin tracking.
        
        Uses multiple strategies to resolve variable -> DataSource:
        1. Direct symbol table lookup (for simple assignments)
        2. Execution call tracing (for function return values)
        """
        for cn in self.control_nodes:
            node_id = cn.get("node_id", "")
            logic = cn.get("logic", {})
            expression = logic.get("expression", "")
            
            if not expression:
                continue
            
            try:
                condition_ast = ast.parse(expression, mode='eval').body
                columns = self._extract_columns_from_condition(condition_ast)
                
                for var_name, col_name in columns:
                    if var_name is None:
                        continue
                    
                    # Strategy 1: Direct symbol table lookup
                    source_id = self.symbol_table.get(var_name)
                    
                    # Strategy 2: Trace through execution calls
                    if not source_id or not source_id.startswith("in_"):
                        source_id = self._trace_variable_to_datasource(var_name)
                    
                    if source_id and source_id.startswith("in_"):
                        self._add_required_column(source_id, col_name, "control_condition", node_id)
            except SyntaxError:
                pass

    def _trace_variable_to_datasource(self, var_name: str) -> str | None:
        """
        Trace a variable back to its originating DataSource through call sites.
        
        When df_sales = _read_table("sales_data"), and _read_table() contains
        spark.read.table(table_name), we need to find the DataSource created inside.
        
        Strategy:
        1. Find call_site where output_variable == var_name
        2. Find data_in created within the function's scope
        3. Use argument bindings to enrich the data_in name if it was dynamic
        """
        for call_site in self.call_sites:
            output = call_site.get("output_variable")
            if output == var_name:
                func_name = call_site.get("function_name", "")
                bindings = call_site.get("argument_bindings", {})
                
                # Find data_in created within this function's scope
                for data_in in self.data_in:
                    location = data_in.get("location", {})
                    scope = location.get("scope") if isinstance(location, dict) else getattr(location, "scope", None)
                    
                    if scope == func_name:
                        data_in_id = data_in.get("id")
                        
                        # Enrich the data_in with the actual table name from bindings
                        # (if name was None due to dynamic parameter)
                        if data_in.get("name") is None:
                            # Look for a binding that looks like a table name
                            for param_name, value in bindings.items():
                                if "table" in param_name.lower() or "name" in param_name.lower():
                                    data_in["name"] = value
                                    break
                        
                        return data_in_id
        return None

    def _add_required_column(
        self, 
        source_id: str, 
        col_name: str, 
        source_reason: str, 
        origin_node: str
    ) -> None:
        """Add a column to a DataSource's required_columns if not already present."""
        for data_in_entry in self.data_in:
            if data_in_entry.get("id") == source_id:
                if "required_columns" not in data_in_entry:
                    data_in_entry["required_columns"] = []
                
                # Check if column already exists
                existing = [c for c in data_in_entry["required_columns"] 
                           if c.get("name") == col_name]
                if not existing:
                    data_in_entry["required_columns"].append({
                        "name": col_name,
                        "source": source_reason,
                        "origin_node": origin_node,
                    })
                break

    def _resolve_branch_steps(self) -> None:
        """
        Post-processing: resolve which execution_calls and transformations belong to each branch.
        
        Uses line_range stored in each branch to find steps that fall within that range.
        Updates the 'steps' array with matching call_xxx and tx_xxx IDs.
        """
        # Build a list of all steps with their line numbers
        step_lines: list[tuple[str, int]] = []
        
        # Add execution calls - use index to generate call_xxx ID
        # The execution_calls are built from call_sites in order
        for idx, cs in enumerate(self.call_sites):
            line_num = cs.get("line_number")
            if line_num:
                call_id = f"call_{idx + 1:03d}"  # call_001, call_002, etc.
                step_lines.append((call_id, line_num))
        
        # Add transformations
        for tx in self.transformations:
            loc = tx.get("location", {})
            span = loc.get("span", "")
            if span:
                # Extract start line from span like "96:5-97:10"
                try:
                    start_line = int(span.split(":")[0])
                    step_lines.append((tx["id"], start_line))
                except (ValueError, IndexError):
                    pass
        
        # Resolve steps for each control node's branches
        for cn in self.control_nodes:
            for branch in cn.get("branches", []):
                line_range = branch.get("line_range")
                if not line_range:
                    continue
                
                start_line, end_line = line_range
                matched_steps = []
                
                # Find steps that fall within this branch's line range
                for step_id, step_line in step_lines:
                    if start_line <= step_line <= end_line:
                        matched_steps.append((step_line, step_id))
                
                # Sort by line number and extract IDs
                matched_steps.sort(key=lambda x: x[0])
                branch["steps"] = [s[1] for s in matched_steps]
                
                # Remove line_range from final output (internal use only)
                branch.pop("line_range", None)

    def _resolve_convergence_points(self) -> None:
        """
        Post-processing: identify convergence points for MERGE control nodes.
        
        For each control node with exit_strategy=MERGE:
        1. Find the last transformation in each branch (branch_outputs)
        2. Find the first transformation AFTER the control block in the same scope
           that consumes the branch variable (convergence_point)
        """
        # Build lookup: tx_id -> transformation dict
        tx_by_id: dict[str, dict] = {tx["id"]: tx for tx in self.transformations}
        
        # Build lookup: tx_id -> (scope, start_line)
        tx_scope_line: dict[str, tuple[str, int]] = {}
        for tx in self.transformations:
            loc = tx.get("location", {})
            scope = loc.get("scope") or GLOBAL_SCOPE
            span = loc.get("span", "")
            if span:
                try:
                    start_line = int(span.split(":")[0])
                    tx_scope_line[tx["id"]] = (scope, start_line)
                except (ValueError, IndexError):
                    pass
        
        for cn in self.control_nodes:
            # Only process MERGE strategy
            if cn.get("exit_strategy") != "MERGE":
                continue
            
            # Get control node scope and end line
            cn_loc = cn.get("source_location", {})
            cn_scope = cn_loc.get("scope") or GLOBAL_SCOPE
            cn_span = cn_loc.get("span", "")
            if not cn_span:
                continue
            
            try:
                # Parse span like "75:5-90:9" to get end line
                span_parts = cn_span.split("-")
                cn_end_line = int(span_parts[1].split(":")[0]) if len(span_parts) > 1 else 0
            except (ValueError, IndexError):
                continue
            
            if cn_end_line == 0:
                continue
            
            # Collect branch outputs (last step in each branch that is a transformation)
            branch_outputs: list[str] = []
            for branch in cn.get("branches", []):
                steps = branch.get("steps", [])
                # Find last transformation step (tx_xxx, not call_xxx)
                for step_id in reversed(steps):
                    if step_id.startswith("tx_"):
                        branch_outputs.append(step_id)
                        break
            
            if not branch_outputs:
                continue
            
            cn["branch_outputs"] = branch_outputs
            
            # Generate SSA output IDs for branches that share the same target_variable
            # This enables unambiguous lineage tracking at convergence
            target_vars = [b.get("target_variable") for b in cn.get("branches", [])]
            shared_target = len(target_vars) > 1 and len(set(target_vars)) == 1 and target_vars[0]
            
            if shared_target:
                # All branches assign to same variable - create SSA names
                for i, branch in enumerate(cn.get("branches", [])):
                    steps = branch.get("steps", [])
                    if steps:
                        last_tx = None
                        for step_id in reversed(steps):
                            if step_id.startswith("tx_"):
                                last_tx = step_id
                                break
                        if last_tx:
                            # Generate SSA name based on branch label
                            # e.g., tx_015 in "true" branch -> tx_015_TRUE
                            label = branch.get("label", str(i)).upper()
                            branch["ssa_output_id"] = f"{last_tx}_{label}"
            
            # Find convergence point: first transformation AFTER control block in same scope
            # that references the target variable (or comes from branch outputs)
            target_var = None
            for branch in cn.get("branches", []):
                tv = branch.get("target_variable")
                if tv:
                    target_var = tv
                    break
            
            # Collect candidate convergence points
            candidates: list[tuple[int, str]] = []  # (line, tx_id)
            for tx_id, (tx_scope, tx_line) in tx_scope_line.items():
                # Must be in same scope
                # Skip if scopes are incompatible:
                # - Both must be global, OR
                # - tx_scope must contain cn_scope (same or parent scope)
                if cn_scope != GLOBAL_SCOPE and (tx_scope == GLOBAL_SCOPE or cn_scope not in tx_scope):
                    continue
                # Must be after control block ends
                if tx_line <= cn_end_line:
                    continue
                # Must not be inside a branch
                if tx_id in [s for b in cn.get("branches", []) for s in b.get("steps", [])]:
                    continue
                
                candidates.append((tx_line, tx_id))
            
            if candidates:
                # Sort by line and take first one
                candidates.sort(key=lambda x: x[0])
                cn["convergence_point"] = candidates[0][1]
            
            # Check for type reconciliation requirements
            # Review ALL transformations in each branch for potential type issues
            if shared_target:
                type_warnings = []
                
                # Collect ALL transformations in each branch
                all_branch_steps = []
                for branch in cn.get("branches", []):
                    steps = branch.get("steps", [])
                    branch_info = {"label": branch.get("label"), "has_cast": False, "has_arithmetic": False}
                    for step_id in steps:
                        tx = tx_by_id.get(step_id)
                        if tx:
                            logic = tx.get("logic", "") or ""
                            if any(op in logic for op in ["*", "/", "+", "-"]):
                                branch_info["has_arithmetic"] = True
                            if ".cast(" in logic.lower():
                                branch_info["has_cast"] = True
                    all_branch_steps.append(branch_info)
                
                # Compare branches for type inconsistencies
                if len(all_branch_steps) >= 2:
                    # Arithmetic without CAST is a risk
                    arith_branches = [b for b in all_branch_steps if b["has_arithmetic"] and not b["has_cast"]]
                    if arith_branches:
                        branch_labels = [b["label"] for b in arith_branches]
                        type_warnings.append(
                            f"Arithmetic operations without explicit CAST in branches: {branch_labels}. "
                            f"If source column is STRING, this will fail at runtime."
                        )
                    
                    # Mixed CAST usage is concerning
                    cast_status = [b["has_cast"] for b in all_branch_steps]
                    if any(cast_status) and not all(cast_status):
                        type_warnings.append(
                            f"Inconsistent CAST usage between branches. "
                            f"Some branches use CAST, others don't - types may not match at convergence."
                        )
                
                if type_warnings:
                    cn["type_reconciliation_required"] = True
                    cn["type_warnings"] = type_warnings
                    # Recommend injection strategy to maintain separation of concerns
                    # ReconciliationStep should be virtual node BEFORE convergence_point
                    cn["reconciliation_strategy"] = "INJECT_BEFORE_CONVERGENCE"

            # If we have SSA outputs, update convergence_point's inputs to include all branches
            convergence_pt = cn.get("convergence_point")
            if shared_target and convergence_pt:
                ssa_inputs = []
                for branch in cn.get("branches", []):
                    ssa_id = branch.get("ssa_output_id")
                    if ssa_id:
                        ssa_inputs.append(ssa_id)
                
                if ssa_inputs:
                    # Find the convergence_point transformation and update its inputs
                    conv_tx = tx_by_id.get(convergence_pt)
                    if conv_tx:
                        # Store original input for reference, add SSA inputs
                        conv_tx["convergence_inputs"] = ssa_inputs
                        # Also note this is a merge point
                        conv_tx["is_convergence_point"] = True

    def visit_If(self, node: ast.If) -> None:
        """Extract if/elif/else as BRANCH control node."""
        # Only process if it affects DataFrame lineage
        affects_df = (
            self._affects_dataframe(node.body) or 
            self._affects_dataframe(node.orelse)
        )
        
        if affects_df:
            branches = []
            
            # True branch
            true_range = self._get_branch_line_range(node.body)
            branches.append({
                "label": "true",
                "condition": ast.unparse(node.test),
                "steps": [],  # Will be resolved in post-processing
                "line_range": true_range,  # (start, end) for step resolution
                "sub_controls": [],
                "produces_dataframe": self._affects_dataframe(node.body),
                "target_variable": self._get_target_variable(node.body),
            })
            
            # False/else branch
            if node.orelse:
                # Check if it's elif (another If) or else (other statements)
                if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                    # elif - will be processed separately
                    elif_range = self._get_branch_line_range(node.orelse[0].body)
                    branches.append({
                        "label": "elif",
                        "condition": ast.unparse(node.orelse[0].test),
                        "steps": [],  # Will be resolved in post-processing
                        "line_range": elif_range,
                        "sub_controls": [],
                        "produces_dataframe": self._affects_dataframe(node.orelse[0].body),
                        "target_variable": self._get_target_variable(node.orelse[0].body),
                    })
                else:
                    false_range = self._get_branch_line_range(node.orelse)
                    branches.append({
                        "label": "false",
                        "steps": [],  # Will be resolved in post-processing
                        "line_range": false_range,
                        "sub_controls": [],
                        "produces_dataframe": self._affects_dataframe(node.orelse),
                        "target_variable": self._get_target_variable(node.orelse),
                    })
            
            # Try to resolve the control expression if it's a simple variable
            expression_str = ast.unparse(node.test)
            resolved_expr = self._resolve_control_expression(node.test)
            
            # Build logic with optional resolved expression
            logic_dict = {
                "expression": expression_str,
                "engine": "PYTHON_AST",
            }
            if resolved_expr:
                logic_dict["resolved_expression"] = resolved_expr
            
            # Determine merge semantic based on branch structure
            exit_strategy = self._determine_exit_strategy(branches)
            merge_semantic = None
            if exit_strategy == "MERGE":
                # All branches assign to same variable -> CONDITIONAL (runtime decides)
                merge_semantic = "CONDITIONAL"
            
            control_node = {
                "node_id": self._next_control_id(),
                "control_type": "BRANCH",
                "logic": logic_dict,
                "branches": branches,
                "exit_strategy": exit_strategy,
                "merge_semantic": merge_semantic,
                "affects_dataframe": True,
                "is_opaque": False,
                "source_location": self._make_location_dict(node),
            }
            self.control_nodes.append(control_node)
        
        # Continue visiting children
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        """Extract match/case as BRANCH control node (Python 3.10+)."""
        branches = []
        affects_df = False
        
        for case in node.cases:
            case_affects_df = self._affects_dataframe(case.body)
            affects_df = affects_df or case_affects_df
            
            pattern_str = ast.unparse(case.pattern) if hasattr(ast, 'unparse') else str(case.pattern)
            branches.append({
                "label": f"case_{pattern_str}",
                "condition": pattern_str,
                "steps": [],
                "line_range": self._get_branch_line_range(case.body),
                "sub_controls": [],
                "produces_dataframe": case_affects_df,
                "target_variable": self._get_target_variable(case.body),
            })
        
        if affects_df:
            control_node = {
                "node_id": self._next_control_id(),
                "control_type": "BRANCH",
                "logic": {
                    "expression": ast.unparse(node.subject),
                    "engine": "PYTHON_AST",
                },
                "branches": branches,
                "exit_strategy": self._determine_exit_strategy(branches),
                "affects_dataframe": True,
                "is_opaque": False,
                "source_location": self._make_location_dict(node),
            }
            self.control_nodes.append(control_node)
        
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        """Extract for loop as LOOP control node."""
        affects_df = self._affects_dataframe(node.body)
        
        if affects_df:
            # Determine loop type using AST analysis
            loop_type = self._detect_loop_type(node.iter)
            iter_str = ast.unparse(node.iter)
            
            # Check if the loop is unrollable (static iterable)
            is_unrollable, static_iterable = self._extract_static_iterable(node.iter)
            
            body_range = self._get_branch_line_range(node.body)
            control_node = {
                "node_id": self._next_control_id(),
                "control_type": "LOOP",
                "logic": {
                    "expression": iter_str,
                    "engine": "PYTHON_AST",
                },
                "branches": [{
                    "label": "body",
                    "steps": [],
                    "line_range": body_range,
                    "sub_controls": [],
                    "produces_dataframe": True,
                    "target_variable": self._get_target_variable(node.body),
                }],
                "exit_strategy": "MERGE",
                "loop_type": loop_type,
                "loop_variable": ast.unparse(node.target),
                "loop_iterable": iter_str,
                "affects_dataframe": True,
                "is_opaque": loop_type == "DATA_ITERATION" and not is_unrollable,
                "opaque_code": "STATEFUL_ITERATION" if (loop_type == "DATA_ITERATION" and not is_unrollable) else self._detect_opaque_code(node.body)[0],
                "opaque_reason": "Iterative data processing requires UDF" if (loop_type == "DATA_ITERATION" and not is_unrollable) else self._detect_opaque_code(node.body)[1],
                "is_unrollable": is_unrollable,
                "static_iterable": static_iterable,
                "source_location": self._make_location_dict(node),
            }
            self.control_nodes.append(control_node)
        
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        """Extract while loop as LOOP control node."""
        affects_df = self._affects_dataframe(node.body)
        
        if affects_df:
            body_range = self._get_branch_line_range(node.body)
            control_node = {
                "node_id": self._next_control_id(),
                "control_type": "LOOP",
                "logic": {
                    "expression": ast.unparse(node.test),
                    "engine": "PYTHON_AST",
                },
                "branches": [{
                    "label": "body",
                    "steps": [],
                    "line_range": body_range,
                    "sub_controls": [],
                    "produces_dataframe": True,
                    "target_variable": self._get_target_variable(node.body),
                }],
                "exit_strategy": "MERGE",
                "loop_type": "DATA_ITERATION",
                "affects_dataframe": True,
                "is_opaque": True,
                "opaque_code": self._detect_opaque_code(node.body)[0] or "STATEFUL_ITERATION",
                "opaque_reason": self._detect_opaque_code(node.body)[1] or "While loops require iterative processing",
                "source_location": self._make_location_dict(node),
            }
            self.control_nodes.append(control_node)
        
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        """Extract try/except/finally as PROTECTED control node."""
        affects_df = (
            self._affects_dataframe(node.body) or
            any(self._affects_dataframe(h.body) for h in node.handlers) or
            self._affects_dataframe(node.finalbody)
        )
        
        if affects_df:
            branches = [{
                "label": "try_block",
                "steps": [],
                "line_range": self._get_branch_line_range(node.body),
                "sub_controls": [],
                "produces_dataframe": self._affects_dataframe(node.body),
                "target_variable": self._get_target_variable(node.body),
            }]
            
            for i, handler in enumerate(node.handlers):
                exc_type = ast.unparse(handler.type) if handler.type else "Exception"
                branches.append({
                    "label": f"except_{exc_type}",
                    "condition": exc_type,
                    "steps": [],
                    "line_range": self._get_branch_line_range(handler.body),
                    "sub_controls": [],
                    "produces_dataframe": self._affects_dataframe(handler.body),
                    "target_variable": self._get_target_variable(handler.body),
                })
            
            if node.finalbody:
                branches.append({
                    "label": "finally_block",
                    "steps": [],
                    "line_range": self._get_branch_line_range(node.finalbody),
                    "sub_controls": [],
                    "produces_dataframe": self._affects_dataframe(node.finalbody),
                    "target_variable": self._get_target_variable(node.finalbody),
                })
            
            control_node = {
                "node_id": self._next_control_id(),
                "control_type": "PROTECTED",
                "branches": branches,
                "exit_strategy": "MERGE",
                "affects_dataframe": True,
                "is_opaque": False,
                "source_location": self._make_location_dict(node),
            }
            self.control_nodes.append(control_node)
        
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        """Extract with statement as SCOPED control node."""
        affects_df = self._affects_dataframe(node.body)
        
        if affects_df:
            # Extract context manager info
            context_items = []
            for item in node.items:
                ctx_expr = ast.unparse(item.context_expr)
                ctx_var = ast.unparse(item.optional_vars) if item.optional_vars else None
                context_items.append({"expr": ctx_expr, "var": ctx_var})
            
            control_node = {
                "node_id": self._next_control_id(),
                "control_type": "SCOPED",
                "logic": {
                    "expression": ", ".join(c["expr"] for c in context_items),
                    "engine": "PYTHON_AST",
                },
                "branches": [{
                    "label": "body",
                    "steps": [],
                    "line_range": self._get_branch_line_range(node.body),
                    "sub_controls": [],
                    "produces_dataframe": True,
                    "target_variable": self._get_target_variable(node.body),
                }],
                "exit_strategy": "MERGE",
                "context_manager": context_items[0]["expr"] if context_items else None,
                "context_variable": context_items[0]["var"] if context_items else None,
                "affects_dataframe": True,
                "is_opaque": False,
                "source_location": self._make_location_dict(node),
            }
            self.control_nodes.append(control_node)
        
        self.generic_visit(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        """Extract try/except* (ExceptionGroup) as PROTECTED control node (Python 3.11+)."""
        affects_df = (
            self._affects_dataframe(node.body) or
            any(self._affects_dataframe(h.body) for h in node.handlers) or
            self._affects_dataframe(node.finalbody)
        )

        if affects_df:
            branches = [{
                "label": "try_block",
                "steps": [],
                "line_range": self._get_branch_line_range(node.body),
                "sub_controls": [],
                "produces_dataframe": self._affects_dataframe(node.body),
                "target_variable": self._get_target_variable(node.body),
            }]

            for handler in node.handlers:
                exc_type = ast.unparse(handler.type) if handler.type else "ExceptionGroup"
                branches.append({
                    "label": f"except_star_{exc_type}",
                    "condition": exc_type,
                    "steps": [],
                    "line_range": self._get_branch_line_range(handler.body),
                    "sub_controls": [],
                    "produces_dataframe": self._affects_dataframe(handler.body),
                    "target_variable": self._get_target_variable(handler.body),
                })

            if node.finalbody:
                branches.append({
                    "label": "finally_block",
                    "steps": [],
                    "line_range": self._get_branch_line_range(node.finalbody),
                    "sub_controls": [],
                    "produces_dataframe": self._affects_dataframe(node.finalbody),
                    "target_variable": self._get_target_variable(node.finalbody),
                })

            control_node = {
                "node_id": self._next_control_id(),
                "control_type": "PROTECTED",
                "branches": branches,
                "exit_strategy": "MERGE",
                "affects_dataframe": True,
                "is_opaque": False,
                "source_location": self._make_location_dict(node),
            }
            self.control_nodes.append(control_node)

        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        """Extract async for as LOOP control node."""
        affects_df = self._affects_dataframe(node.body)

        if affects_df:
            body_range = self._get_branch_line_range(node.body)
            control_node = {
                "node_id": self._next_control_id(),
                "control_type": "LOOP",
                "logic": {
                    "expression": ast.unparse(node.iter),
                    "engine": "PYTHON_AST",
                },
                "branches": [{
                    "label": "body",
                    "steps": [],
                    "line_range": body_range,
                    "sub_controls": [],
                    "produces_dataframe": True,
                    "target_variable": self._get_target_variable(node.body),
                }],
                "exit_strategy": "MERGE",
                "loop_type": "ASYNC_ITERATION",
                "loop_variable": ast.unparse(node.target),
                "loop_iterable": ast.unparse(node.iter),
                "affects_dataframe": True,
                "is_opaque": True,
                "opaque_code": "ASYNC_ITERATION",
                "opaque_reason": "Async for loops require special handling",
                "source_location": self._make_location_dict(node),
            }
            self.control_nodes.append(control_node)

        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        """Extract async with as SCOPED control node."""
        affects_df = self._affects_dataframe(node.body)

        if affects_df:
            context_items = []
            for item in node.items:
                ctx_expr = ast.unparse(item.context_expr)
                ctx_var = ast.unparse(item.optional_vars) if item.optional_vars else None
                context_items.append({"expr": ctx_expr, "var": ctx_var})

            control_node = {
                "node_id": self._next_control_id(),
                "control_type": "SCOPED",
                "logic": {
                    "expression": ", ".join(c["expr"] for c in context_items),
                    "engine": "PYTHON_AST",
                },
                "branches": [{
                    "label": "body",
                    "steps": [],
                    "line_range": self._get_branch_line_range(node.body),
                    "sub_controls": [],
                    "produces_dataframe": True,
                    "target_variable": self._get_target_variable(node.body),
                }],
                "exit_strategy": "MERGE",
                "context_manager": context_items[0]["expr"] if context_items else None,
                "context_variable": context_items[0]["var"] if context_items else None,
                "affects_dataframe": True,
                "is_opaque": False,
                "source_location": self._make_location_dict(node),
            }
            self.control_nodes.append(control_node)

        self.generic_visit(node)


def parse_spark_file(
    file_path: str | Path,
    *,
    infer_schemas: bool = True,
    catalog: dict[str, Any] | None = None,
    base_dir: Path | None = None,
    include_timestamp: bool = True,
) -> ASG:
    """
    Parse a Spark file and return an enriched ASG.

    This function implements the "One-Pass Experience" pipeline:
    1. AST Traversal - Build structure and Symbol Table
    2. Linkage - Connect inputs/outputs of nodes
    3. Schema Enrichment - Infer column types (optional)

    Args:
        file_path: Path to the Python file
        infer_schemas: If True (default), run Schema Tracker to infer
            column types from code analysis. Set to False for faster
            parsing when schema info is not needed.
        catalog: Optional external catalog (dict) with table schemas.
            If provided, enriches inferred schemas with exact types.
        base_dir: If provided, compute relative paths from this directory
            for multi-file workloads.

    Returns:
        ASG model with data_in, data_out, transformations, and
        inferred_columns (if infer_schemas=True)
    """
    from warp_core.ir.pyspark_models import SourceFile

    # Phase 1 & 2: AST Traversal + Linkage
    parser = SparkASTParser()
    asg = parser.parse_file(file_path)

    # Phase 2.5: Cross-Function Lineage Resolution
    # Connect param_xxx references to their actual source nodes
    if parser.call_sites:
        from asg_pyspark.parser.lineage_linker import LineageLinker

        linker = LineageLinker(
            call_sites=parser.call_sites, functions=[f.model_dump() for f in asg.functions]
        )
        asg = linker.resolve(asg)

    # Phase 3: Schema Enrichment (optional but recommended)
    if infer_schemas:
        from warp_core.schema.schema_tracker import SchemaPropagator

        propagator = SchemaPropagator()
        asg = propagator.process(asg)

    source_code = Path(file_path).read_text(encoding="utf-8", errors="replace")
    _infer_column_types_from_usage(asg, [(str(file_path), source_code)])

    # Phase 4: Origin Resolution (late binding)
    # Propagate data origins through execution_calls to function arguments
    from asg_pyspark.parser.origin_resolver import resolve_origins

    asg = resolve_origins(asg)
    
    # Phase 5: Extract column constraints and relationships
    from asg_pyspark.parser.constraint_extractor import enrich_asg_with_constraints
    
    asg = enrich_asg_with_constraints(asg)

    # Update paths if base_dir is provided (for multi-file workloads)
    if base_dir:
        path = Path(file_path)
        relative_path = str(path.relative_to(base_dir))

        # Update source_file path
        asg.extraction_metadata.source_file = relative_path

        # Update source_files entry path
        if asg.source_files:
            asg.source_files[0].path = relative_path

        # Update source_file in each function
        for func in asg.functions:
            func.source_file = relative_path

    # Set timestamp if requested
    if include_timestamp:
        from datetime import datetime
        asg.extraction_metadata.generated_at = datetime.now()
    
    return asg


def _rebase_ids(
    asg: "ASG",
    offset: int,
) -> tuple["ASG", int]:
    """
    Rebase all node IDs in an ASG by adding an offset.

    Returns the modified ASG and the new offset (for the next file).
    """

    def _split_node_id(node_id: str) -> tuple[str, int] | None:
        """Split 'tx_001' into ('tx', 1). Returns None if not a valid node ID."""
        idx = node_id.rfind("_")
        if idx < 1:
            return None
        prefix, num_str = node_id[:idx], node_id[idx + 1:]
        if num_str.isdigit() and prefix.isalpha():
            return prefix, int(num_str)
        return None

    def _is_node_id(node_id: str) -> bool:
        return _split_node_id(node_id) is not None

    def rebase_id(node_id: str) -> str:
        """Add offset to a node ID like 'tx_001' -> 'tx_011' (if offset=10)."""
        parts = _split_node_id(node_id)
        if parts:
            prefix, num = parts
            return f"{prefix}_{num + offset:03d}"
        return node_id

    def rebase_id_list(ids: list[str]) -> list[str]:
        return [rebase_id(i) for i in ids]

    max_id = 0

    def extract_num(node_id: str) -> int:
        parts = _split_node_id(node_id)
        return parts[1] if parts else 0

    # Rebase data_in
    for node in asg.data_in:
        max_id = max(max_id, extract_num(node.id))
        node.id = rebase_id(node.id)

    # Rebase data_out
    for node in asg.data_out:
        max_id = max(max_id, extract_num(node.id))
        node.id = rebase_id(node.id)
        if node.source_id:
            node.source_id = rebase_id(node.source_id)

    # Rebase transformations
    for node in asg.transformations:
        max_id = max(max_id, extract_num(node.id))
        node.id = rebase_id(node.id)
        node.inputs = rebase_id_list(node.inputs)
        node.outputs = rebase_id_list(node.outputs)
        # Rebase convergence_inputs (SSA IDs in format tx_XXX_LABEL)
        if node.convergence_inputs:
            rebased_conv_inputs = []
            for conv_in in node.convergence_inputs:
                # Format: tx_XXX_LABEL -> tx_YYY_LABEL
                parts = conv_in.rsplit("_", 1)
                if len(parts) == 2 and parts[0].startswith("tx_"):
                    rebased_tx = rebase_id(parts[0])
                    rebased_conv_inputs.append(f"{rebased_tx}_{parts[1]}")
                else:
                    rebased_conv_inputs.append(conv_in)
            node.convergence_inputs = rebased_conv_inputs

    # Rebase function returns
    for func in asg.functions:
        if func.returns and func.returns.ref_id:
            if _is_node_id(func.returns.ref_id):
                func.returns.ref_id = rebase_id(func.returns.ref_id)

    # Rebase execution calls (source_id in bindings, target_node in output)
    for exec_call in asg.execution_calls:
        for binding in exec_call.bindings.inputs:
            if _is_node_id(binding.source_id):
                binding.source_id = rebase_id(binding.source_id)
        if exec_call.bindings.output and exec_call.bindings.output.target_node:
            if _is_node_id(exec_call.bindings.output.target_node):
                exec_call.bindings.output.target_node = rebase_id(
                    exec_call.bindings.output.target_node
                )

    # Rebase control nodes (branch steps, branch_outputs, convergence_point, ssa_output_id)
    for cn in asg.control_nodes:
        max_id = max(max_id, extract_num(cn.node_id))
        cn.node_id = rebase_id(cn.node_id)
        # Rebase steps in each branch
        for branch in cn.branches:
            branch.steps = rebase_id_list(branch.steps)
            # Rebase SSA output ID (format: tx_XXX_LABEL -> tx_YYY_LABEL)
            if branch.ssa_output_id:
                parts = branch.ssa_output_id.rsplit("_", 1)
                if len(parts) == 2 and parts[0].startswith("tx_"):
                    rebased_tx = rebase_id(parts[0])
                    branch.ssa_output_id = f"{rebased_tx}_{parts[1]}"
        # Rebase convergence metadata
        if cn.branch_outputs:
            cn.branch_outputs = rebase_id_list(cn.branch_outputs)
        if cn.convergence_point:
            cn.convergence_point = rebase_id(cn.convergence_point)

    # New offset is current offset + max ID found
    new_offset = offset + max_id

    return asg, new_offset


def _generate_missing_source_warnings(asg: "ASG") -> "ASG":
    """
    Generate warnings for transformations that depend on imports without source code.

    Scans all source files for custom_library imports with has_source=False,
    then checks if any UDF transformations in those files use those imports.

    Warning code: W001 - Missing Source Logic

    Detection strategy:
    1. Build a map of file -> missing source imports
    2. For each UDF-related transformation, check if its file has missing imports
    3. If so, generate a warning linking the UDF to the missing dependency
    """
    from warp_core.ir.pyspark_models import AnalysisWarning, ImportType, WarningSeverity

    # Build map: file_path -> list of (module, imported_names) with missing source
    files_with_missing_imports: dict[str, list[tuple[str, list[str]]]] = {}

    for source_file in asg.source_files:
        missing_imports = []
        for module, entry in source_file.imports.items():
            if entry.type == ImportType.CUSTOM_LIBRARY and not entry.has_source:
                # Get clean import names (strip aliases)
                clean_names = [
                    name.split(":")[0] if ":" in name else name
                    for name in entry.imported_names
                ]
                missing_imports.append((module, clean_names))

        if missing_imports:
            files_with_missing_imports[source_file.path] = missing_imports

    if not files_with_missing_imports:
        return asg  # No missing source imports

    # Find UDF transformations in files with missing imports
    warning_counter = 0
    warned_combinations: set[tuple[str, str]] = set()  # (node_id, module) to avoid duplicates

    for tx in asg.transformations:
        # Check if this is a UDF-related transformation
        is_udf_related = (
            "udf" in tx.operation.lower()
            or tx.operation == "withColumn_custom"
            or (tx.logic and "udf" in tx.logic.lower())
        )

        if not is_udf_related:
            continue

        # Get the file this transformation is in
        tx_file = tx.location.pathfile if tx.location else None
        if not tx_file:
            continue

        # Check if this file has missing source imports
        if tx_file not in files_with_missing_imports:
            continue

        # Generate warning for each missing import in this file
        for module, imported_names in files_with_missing_imports[tx_file]:
            # Avoid duplicate warnings
            warn_key = (tx.id, module)
            if warn_key in warned_combinations:
                continue
            warned_combinations.add(warn_key)

            warning_counter += 1
            names_str = ", ".join(imported_names) if imported_names else module

            warning = AnalysisWarning(
                code=f"W{warning_counter:03d}",
                severity=WarningSeverity.WARNING,
                node_id=tx.id,
                message=(
                    f"Missing source logic. Node {tx.id} uses a UDF that depends on "
                    f"{names_str} from {module}. Without the source code, the equivalence "
                    f"of the migrated logic cannot be automatically guaranteed. "
                    f"Manual intervention or an equivalent Snowflake UDF is required."
                ),
                source_module=module,
                suggested_action=(
                    f"Provide source code for {module} in the workload directory, "
                    f"or create an equivalent Snowflake Python UDF."
                ),
            )
            asg.warnings.append(warning)

    return asg


def _resolve_cross_file_function_sources(asg: "ASG") -> None:
    """
    Resolve sources from cross-file function calls (Phase 2.9).
    
    After all files are parsed, functions have their returns_id populated.
    This pass finds execution_calls that call functions returning data_sources
    and registers the output variables as sources.
    
    Example:
        # In utils_redshift.py:
        def read_dataframe(query):
            return spark.read.format("redshift").load()  # returns_id = in_1734
        
        # In load_data.py (parsed BEFORE utils_redshift.py):
        df_data = brand_redshift_utils.read_dataframe(query)
        # -> df_data should be registered as source pointing to in_1734
    """
    for call in asg.execution_calls:
        # Get function name from the call
        func_name = call.callee.function if call.callee else None
        if not func_name:
            continue
        
        # Skip if no output variable
        output_var = None
        if call.bindings and call.bindings.output:
            output_var = call.bindings.output.variable_name
        if not output_var:
            continue
        
        # Check if function returns a data source
        sig = SymbolTable._global_functions.get(func_name)
        if not sig:
            continue
        
        if sig.returns_type == "data_source" and sig.returns_id:
            # Register the output variable as a source
            # Only if not already registered
            file_context = call.caller.file if call.caller else ""
            key = f"{file_context}:{output_var}" if file_context else output_var
            
            if key not in SymbolTable._global_sources and output_var not in SymbolTable._global_sources:
                SymbolTable.register_source(
                    var_name=output_var,
                    source_id=sig.returns_id,
                    source_name=f"var:{output_var}",
                    file=file_context,
                )


def _relink_param_inputs(asg: "ASG") -> None:
    """
    Relink param_* inputs to resolved sources (Phase 2.10).
    
    When a transformation was created with input like param_df_data, it means
    df_data wasn't in SymbolTable at parse time. Now that Phase 2.9 has populated
    sources from cross-file function calls, we can resolve these.
    
    Also uses call site argument_bindings to resolve function parameters.
    
    Example:
        Before: tx_370.inputs = ["param_df_fct_restaurant_success_score"]
        After:  tx_370.inputs = ["in_1734"] (if df_fct_restaurant_success_score -> in_1734)
    """
    # Build lookup from variable name to source_id
    var_to_source: dict[str, str] = {}
    for key, binding in SymbolTable._global_sources.items():
        # Key might be "file:var" or just "var"
        var_name = binding.variable_name
        if var_name and binding.source_id:
            var_to_source[var_name] = binding.source_id
    
    # Also add file-scoped entries (extract var from "file:var" keys)
    for key, binding in SymbolTable._global_sources.items():
        if ":" in key:
            # Format: "file:var_name"
            var_name = key.split(":")[-1]
            if var_name and binding.source_id:
                var_to_source[var_name] = binding.source_id
    
    # Build function -> param -> source_id mapping from call sites
    # This helps resolve params that were bound via function calls
    func_param_to_source: dict[str, dict[str, str]] = {}
    for call_site in SymbolTable._global_call_sites:
        func_name = call_site.get('function_name', '')
        bindings = call_site.get('argument_bindings', {})
        if func_name and bindings:
            if func_name not in func_param_to_source:
                func_param_to_source[func_name] = {}
            for param, source in bindings.items():
                # Only use valid source_ids (in_*, tx_*)
                if isinstance(source, str) and (source.startswith('in_') or source.startswith('tx_')):
                    func_param_to_source[func_name][param] = source
    
    # Build transformation -> containing_function mapping
    tx_to_func: dict[str, str] = {}
    for tx in asg.transformations:
        if tx.location:
            loc_str = str(tx.location)
            # Extract function name from location like "file@ClassName.FunctionName[line]"
            if '@' in loc_str:
                scope_part = loc_str.split('@')[1]
                if '[' in scope_part:
                    full_name = scope_part.split('[')[0]
                    # Handle both "ClassName.func_name" and "func_name"
                    # Store the method name (without class prefix) for matching
                    func_name = full_name.split('.')[-1] if '.' in full_name else full_name
                    tx_to_func[tx.id] = func_name
    
    # Relink transformations
    relinked_count = 0
    for tx in asg.transformations:
        new_inputs = []
        changed = False
        containing_func = tx_to_func.get(tx.id, '')
        
        for inp in tx.inputs:
            if inp.startswith("param_"):
                # Extract variable name: param_df_data -> df_data
                var_name = inp[6:]  # Remove "param_" prefix
                resolved = False
                
                # First try global sources
                if var_name in var_to_source:
                    new_inputs.append(var_to_source[var_name])
                    changed = True
                    relinked_count += 1
                    resolved = True
                
                # Then try call site bindings for the containing function
                if not resolved and containing_func in func_param_to_source:
                    func_bindings = func_param_to_source[containing_func]
                    if var_name in func_bindings:
                        new_inputs.append(func_bindings[var_name])
                        changed = True
                        relinked_count += 1
                        resolved = True
                
                if not resolved:
                    new_inputs.append(inp)
            else:
                new_inputs.append(inp)
        
        if changed:
            tx.inputs = new_inputs


def _resolve_synthetic_arg_names(asg: "ASG") -> None:
    """
    Resolve synthetic argument names (arg_0, arg_1, ...) to actual parameter names.

    During single-file parsing, imported functions don't have their parameter names
    available. This function resolves those names after multi-file merge when all
    function definitions are available.

    Modifies the ASG in place.
    """
    # Build function lookup: name -> FunctionDefinition
    func_lookup = {f.name: f for f in asg.functions}

    for call in asg.execution_calls:
        func_name = call.callee.function
        func_def = func_lookup.get(func_name)

        if not func_def:
            continue

        # Get actual parameter names
        actual_params = [arg.name for arg in func_def.arguments]

        # Update synthetic names in input bindings
        for binding in call.bindings.inputs:
            # Check if this is a synthetic name (arg_0, arg_1, etc.)
            if binding.arg_name.startswith("arg_") and binding.arg_name[4:].isdigit():
                idx = int(binding.arg_name[4:])
                if idx < len(actual_params):
                    binding.arg_name = actual_params[idx]


def _extract_definitions(
    source_code: str,
    filename: str,
    workload_root: Path | None = None,
) -> None:
    """
    Extract function/class definitions from source code (Pass 1).
    
    This is a lightweight parsing pass that only extracts function and class
    definitions, registering them in SymbolTable. No transformations, calls,
    or data sources are processed.
    
    This enables Pass 2 to correctly classify method calls like obj.method()
    even when the method is defined in a file parsed later alphabetically.
    
    Args:
        source_code: Python source code to parse
        filename: Relative path of the file
        workload_root: Root directory of the workload
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return  # Skip files with syntax errors - Pass 2 will report them
    
    current_class: str | None = None
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # Track class context for methods
            current_class = node.name
            
        elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            # Determine containing class by checking parent nodes
            containing_class = None
            for parent in ast.walk(tree):
                if isinstance(parent, ast.ClassDef):
                    for child in ast.iter_child_nodes(parent):
                        if child is node:
                            containing_class = parent.name
                            break
            
            # Register function/method in SymbolTable
            SymbolTable.register_function(FunctionSignature(
                name=node.name,
                file=filename,
                returns_type="unknown",
                returns_id=None,
                containing_class=containing_class,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
            ))


def _link_widget_gets_to_transformations(asg: "ASG", file_asts: list[tuple[str, str]]) -> None:
    """
    Second traversal: link dbutils.widgets.get() variables to transformations.

    Widget config data_in entries are created during _promote_widgets_to_data_in,
    but the variables assigned from .get() calls are plain Python variables invisible
    to the SymbolTable. This function re-scans each file AST to:
    1. Find assignments like  x = dbutils.widgets.get("name")
    2. Map x -> config data_in ID that contains column "name"
    3. Inject the config ID into transformations whose logic references x
    """
    import ast as ast_mod
    import re as re_mod

    config_entries = [
        d for d in asg.data_in
        if getattr(d, "type", None) == "config"
        and getattr(d, "format", None) == "dbutils.widgets"
    ]
    if not config_entries:
        return

    widget_col_to_config_id: dict[str, str] = {}
    for cfg in config_entries:
        for col in cfg.inferred_columns or []:
            widget_col_to_config_id[col.name] = cfg.id

    var_to_config_id: dict[str, str] = {}
    used_widget_names: set[str] = set()

    for _file_path, source_code in file_asts:
        try:
            tree = ast_mod.parse(source_code)
        except SyntaxError:
            continue

        for node in ast_mod.walk(tree):
            if not isinstance(node, ast_mod.Assign):
                continue
            if not node.targets or not isinstance(node.targets[0], ast_mod.Name):
                continue

            var_name = node.targets[0].id
            widget_name, wrapper_type = _extract_widget_get_name(node.value)
            if not widget_name and isinstance(node.value, ast_mod.Compare):
                # Handle: x = dbutils.widgets.get("y") == 'True'
                widget_name, wrapper_type = _extract_widget_get_name(node.value.left)
                if widget_name:
                    wrapper_type = "BOOLEAN"
            if widget_name and widget_name in widget_col_to_config_id:
                var_to_config_id[var_name] = widget_col_to_config_id[widget_name]
                used_widget_names.add(widget_name)
                if wrapper_type:
                    _upgrade_widget_column_type(config_entries, widget_name, wrapper_type)

    # Mark declared-but-never-used widget columns so downstream tools
    # can exclude them from metrics while still reporting their existence.
    for cfg in config_entries:
        if cfg.inferred_columns:
            for col in cfg.inferred_columns:
                if col.source == "widget_default" and col.name not in used_widget_names:
                    col.source = "widget_unused"

    if not var_to_config_id:
        return

    var_patterns = {
        var: re_mod.compile(r"\b" + re_mod.escape(var) + r"\b")
        for var in var_to_config_id
    }

    linked = 0
    for tx in asg.transformations:
        logic = getattr(tx, "logic", None) or ""
        if not logic:
            continue
        for var, pattern in var_patterns.items():
            if pattern.search(logic):
                config_id = var_to_config_id[var]
                inputs = tx.inputs or []
                if config_id not in inputs:
                    inputs.append(config_id)
                    tx.inputs = inputs
                    linked += 1

    if linked:
        import logging
        logging.getLogger(__name__).info(
            "Linked %d transformation(s) to widget config data_in", linked
        )


def _upgrade_widget_column_type(
    config_entries: list, widget_name: str, confirmed_type: str
) -> None:
    """Upgrade a widget column's type and confidence using wrapper evidence."""
    from warp_core.ir.pyspark_models import InferenceConfidence

    for cfg in config_entries:
        for col in cfg.inferred_columns or []:
            if col.name == widget_name and col.source == "widget_default":
                if col.inferred_type == "UNKNOWN" or col.confidence != InferenceConfidence.HIGH:
                    col.inferred_type = confirmed_type
                    col.confidence = InferenceConfidence.HIGH


def _extract_widget_get_name(node) -> tuple[str | None, str | None]:
    """
    Extract widget name and wrapper type from dbutils.widgets.get("name").

    Returns (widget_name, inferred_type_from_wrapper).
    The wrapper type is derived from the surrounding call:
      int(dbutils.widgets.get("x"))        -> ("x", "INT")
      float(dbutils.widgets.get("x"))      -> ("x", "DECIMAL")
      bool(dbutils.widgets.get("x"))       -> ("x", "BOOLEAN")
      dbutils.widgets.get("x").lower()     -> ("x", "STRING")
      dbutils.widgets.get("x").strip()     -> ("x", "STRING")
      dbutils.widgets.get("x") == 'True'   -> ("x", "BOOLEAN")
      dbutils.widgets.get("x")             -> ("x", None)
    """
    import ast as ast_mod

    call = node
    if not isinstance(call, ast_mod.Call):
        return None, None

    wrapper_type: str | None = None
    CAST_WRAPPERS = {"int": "INT", "float": "DECIMAL", "str": "STRING", "bool": "BOOLEAN"}
    STRING_METHODS = {"lower", "upper", "strip", "lstrip", "rstrip", "title", "capitalize"}

    # Unwrap type-cast wrappers: int(...), float(...), str(...), bool(...)
    if isinstance(call.func, ast_mod.Name) and call.func.id in CAST_WRAPPERS:
        wrapper_type = CAST_WRAPPERS[call.func.id]
        if call.args and isinstance(call.args[0], ast_mod.Call):
            call = call.args[0]

    # Unwrap method chains: dbutils.widgets.get("x").lower()
    if isinstance(call.func, ast_mod.Attribute) and isinstance(call.func.value, ast_mod.Call):
        if call.func.attr in STRING_METHODS:
            wrapper_type = "STRING"
        call = call.func.value

    # Detect comparison patterns: dbutils.widgets.get("x") == 'True'
    # (handled at Assign level — the RHS is a Compare, not a Call)

    if not isinstance(call, ast_mod.Call) or not isinstance(call.func, ast_mod.Attribute):
        return None, None
    if call.func.attr not in ("get", "getArgument"):
        return None, None

    receiver = call.func.value
    if not (isinstance(receiver, ast_mod.Attribute) and receiver.attr == "widgets"):
        return None, None
    if not (isinstance(receiver.value, ast_mod.Name) and receiver.value.id == "dbutils"):
        return None, None

    if call.args and isinstance(call.args[0], ast_mod.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value, wrapper_type
    return None, None


def _discover_cross_file_calls(asg: "ASG", file_asts: list[tuple[str, str]]) -> None:
    """
    Discover function calls that were missed during single-file parsing.

    During parsing, calls to functions defined in other files (e.g., loaded
    via %run) are classified as UNKNOWN because the function isn't in the
    local scope. Now that we have the global function registry, re-scan
    source files to find these calls and create execution_call entries
    with resolved literal arguments.

    This enables _promote_indirect_outputs to create concrete outputs
    for each call site, no matter how deep the call chain.
    """
    import ast as ast_mod
    from warp_core.ir.pyspark_models import (
        ExecutionCall,
        CallLocation,
        CalleeRef,
        CallBindings,
        InputBinding,
    )

    global_funcs = {func.name: func for func in asg.functions}
    if not global_funcs:
        return

    existing_calls: set[tuple[str, str, int]] = set()
    for ec in asg.execution_calls:
        if ec.caller and ec.callee:
            existing_calls.add((
                ec.caller.file or "",
                ec.callee.function or "",
                ec.caller.line or 0,
            ))

    max_call_id = 0
    for ec in asg.execution_calls:
        if ec.call_id and ec.call_id.startswith("call_"):
            try:
                max_call_id = max(max_call_id, int(ec.call_id[5:]))
            except ValueError:
                pass

    new_calls: list[ExecutionCall] = []

    for file_path, source_code in file_asts:
        try:
            tree = ast_mod.parse(source_code)
        except SyntaxError:
            continue

        for node in ast_mod.walk(tree):
            if not isinstance(node, ast_mod.Call):
                continue
            if not isinstance(node.func, ast_mod.Name):
                continue

            func_name = node.func.id
            if func_name not in global_funcs:
                continue

            func_def = global_funcs[func_name]
            func_file = func_def.source_file or ""
            if func_file == file_path:
                continue

            line = getattr(node, "lineno", 0)
            key = (file_path, func_name, line)
            if key in existing_calls:
                continue
            existing_calls.add(key)

            # Resolve literal arguments by looking at recent variable
            # assignments before this call in the source code
            lit_args = _resolve_call_literals(node, tree, source_code, func_def)

            max_call_id += 1
            ec = ExecutionCall(
                call_id=f"call_{max_call_id:04d}",
                caller=CallLocation(
                    function="__main__",
                    line=line,
                    file=file_path,
                ),
                callee=CalleeRef(
                    function=func_name,
                    file=func_file,
                ),
                literal_arguments=lit_args or {},
            )
            new_calls.append(ec)

    if new_calls:
        asg.execution_calls.extend(new_calls)


def _resolve_call_literals(
    call_node,
    tree,
    source_code: str,
    func_def,
) -> dict[str, str]:
    """
    Resolve literal argument values for a function call.

    Handles:
    - Direct literals: func("value")
    - Variable with recent literal assignment: x = "value"; func(x)
    - Keyword arguments: func(table_name=table_name) where table_name was assigned
    - Positional arguments mapped via function signature (if available)
    """
    import ast as ast_mod

    result: dict[str, str] = {}
    call_line = getattr(call_node, "lineno", 0)

    # Get parameter names from function definition (may be empty for some functions)
    params = [p.name for p in (func_def.arguments or [])]

    # Map arguments to parameter names
    arg_values: dict[str, ast_mod.AST] = {}
    for i, arg in enumerate(call_node.args):
        if i < len(params):
            arg_values[params[i]] = arg
        else:
            arg_values[f"arg_{i}"] = arg
    for kw in call_node.keywords:
        if kw.arg:
            arg_values[kw.arg] = kw.value

    # Build a map of variable → last literal assignment visible before this call.
    # Track ALL assignments to handle overwrites (last one wins).
    var_literals: dict[str, str] = {}
    assignments: list[tuple[int, str, str]] = []
    for node in ast_mod.walk(tree):
        if isinstance(node, ast_mod.Assign):
            node_line = getattr(node, "lineno", 0)
            if node_line >= call_line:
                continue
            if len(node.targets) == 1 and isinstance(node.targets[0], ast_mod.Name):
                var_name = node.targets[0].id
                val = node.value
                if isinstance(val, ast_mod.Constant) and isinstance(val.value, (str, int, float, bool)):
                    assignments.append((node_line, var_name, str(val.value)))

    # Sort by line number so last assignment wins
    assignments.sort(key=lambda x: x[0])
    for _, var_name, val in assignments:
        var_literals[var_name] = val

    # Resolve each argument
    for param_name, arg_node in arg_values.items():
        if isinstance(arg_node, ast_mod.Constant) and isinstance(arg_node.value, (str, int, float, bool)):
            result[param_name] = str(arg_node.value)
        elif isinstance(arg_node, ast_mod.Name) and arg_node.id in var_literals:
            result[param_name] = var_literals[arg_node.id]

    return result


# ── Dynamic output name extraction ────────────────────────────────────────────

def _ast_expr_to_template(node: "ast.expr") -> str | None:  # type: ignore[name-defined]
    """Recursively convert an AST expression node to a readable name template.

    Handles the patterns most common in PySpark utility function calls:
    - String literals: ``'STATIC'`` → ``"STATIC"``
    - String concatenation: ``'PREFIX_' + var`` → ``"PREFIX_<var>"``
    - f-strings: ``f'PREFIX_{var}'`` → ``"PREFIX_<var>"``
    - Plain name references: ``var`` → ``"<var>"``
    """
    import ast as _ast

    if isinstance(node, _ast.Constant) and isinstance(node.value, str):
        return node.value

    if isinstance(node, _ast.Name):
        return f"<{node.id}>"

    if isinstance(node, _ast.BinOp) and isinstance(node.op, _ast.Add):
        left = _ast_expr_to_template(node.left)
        right = _ast_expr_to_template(node.right)
        if left is not None and right is not None:
            return left + right
        return left or right

    if isinstance(node, _ast.JoinedStr):  # f-string
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, _ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, _ast.FormattedValue):
                inner = _ast_expr_to_template(value.value)
                parts.append(inner if inner is not None else "<?>")
        return "".join(parts) if parts else None

    return None


def _clean_runtime_template(value: str | None) -> str | None:
    """Convert a ``runtime:...`` literal_argument value to a readable name template.

    ``_promote_indirect_outputs`` stores unresolved expressions as
    ``"runtime:<python_expr>"`` strings.  This function strips the prefix and
    simplifies the expression into a human-readable template so the resulting
    ``DataSink`` has a meaningful name instead of ``None``.

    Examples::

        "runtime:'PAR_CLE_SRC_MRB_' + app"  →  "PAR_CLE_SRC_MRB_<app>"
        "runtime:f'table_{schema}_{name}'"   →  "table_<schema>_<name>"
        "runtime:'STATIC_NAME'"              →  "STATIC_NAME"
        "PLAIN_NAME"                         →  "PLAIN_NAME"  (no runtime: prefix)
        None                                 →  None
    """
    import ast as _ast

    if not value:
        return None

    # Values without the "runtime:" prefix are already resolved plain names.
    if not value.startswith("runtime:"):
        return value.strip() or None

    expr = value[len("runtime:"):]

    try:
        tree = _ast.parse(expr.strip(), mode="eval")
        result = _ast_expr_to_template(tree.body)
        return result.strip() if result else None
    except SyntaxError:
        # Fallback: strip quotes from simple literals
        stripped = expr.strip().strip("'\"")
        return stripped if stripped else None


def _promote_indirect_outputs(asg: "ASG") -> None:
    """
    Promote indirect outputs from utility function calls to caller scope (Phase 2.11).

    When an execution_call invokes a write utility (e.g., data_update_into_s3),
    the actual df.write happens inside the utility. This phase creates concrete
    DataSink entries at each call site with the resolved destination name from
    literal_arguments, enabling downstream tools to see ALL outputs.

    Uses call-graph tracing: if a function has no direct data_out but calls
    another function that does (e.g., update_data_into_rds -> insert_into_rds),
    the callee's data_out is used as template. This follows the real code path
    instead of guessing by class membership.
    """
    from warp_core.ir.pyspark_models import DataSink, SourceLocation

    # Index: function name -> data_out entries from that function's scope
    func_data_out: dict[str, list] = {}
    for out in asg.data_out:
        if out.location and out.location.scope:
            scope_parts = out.location.scope.split(".")
            func_name = scope_parts[-1] if scope_parts else ""
            if func_name:
                func_data_out.setdefault(func_name, []).append(out)

    if not func_data_out:
        return

    # Build internal call graph: function -> set of callees
    call_graph: dict[str, set[str]] = {}
    for call in asg.execution_calls:
        caller_func = call.caller.function if call.caller else None
        callee_func = call.callee.function if call.callee else None
        if caller_func and callee_func:
            call_graph.setdefault(caller_func, set()).add(callee_func)

    def _find_transitive_data_out(
        func_name: str, visited: set[str] | None = None
    ) -> list | None:
        """Follow call graph to find data_out reachable from func_name."""
        if visited is None:
            visited = set()
        if func_name in visited:
            return None
        visited.add(func_name)

        if func_name in func_data_out:
            return func_data_out[func_name]

        for callee in call_graph.get(func_name, set()):
            result = _find_transitive_data_out(callee, visited)
            if result:
                return result
        return None

    max_id = 0
    for out in asg.data_out:
        if out.id.startswith("out_"):
            try:
                num = int(out.id[4:])
                max_id = max(max_id, num)
            except ValueError:
                pass

    promoted = []
    for call in asg.execution_calls:
        func_name = call.callee.function if call.callee else None
        if not func_name:
            continue

        callee_outputs = _find_transitive_data_out(func_name)
        if not callee_outputs:
            continue

        # Skip internal delegation: if the caller is in the same file as the
        # data_out definition, this is an internal call between utility methods
        # (e.g., update_data_into_rds -> insert_into_rds, both in utils_rds.py)
        template = callee_outputs[0]
        template_file = template.location.pathfile if template.location else None
        caller_file = call.caller.file if call.caller else None
        if template_file and caller_file and template_file == caller_file:
            continue

        lit_args = call.literal_arguments or {}
        # Named keys take priority; fall back to positional arg_1 / arg_2
        # (write_csv(df, name, ...) stores the name as arg_1).
        # Apply _clean_runtime_template so "runtime:'PREFIX_' + var" becomes
        # the readable template "PREFIX_<var>" instead of staying as None.
        raw_dest = (
            lit_args.get("table_name")
            or lit_args.get("name")
            or lit_args.get("path")
            or lit_args.get("dbtable")
            or lit_args.get("arg_1")
            or lit_args.get("arg_2")
        )
        dest_name = _clean_runtime_template(raw_dest)

        df_source_id = None
        if call.bindings and call.bindings.inputs:
            for binding in call.bindings.inputs:
                if binding.arg_name in (
                    "dataframe", "new_data_table", "df", "data",
                    "data_table", "table", "arg_0", "arg_1",
                ):
                    df_source_id = binding.source_id
                    break

        template = callee_outputs[0]

        max_id += 1
        caller_file = call.caller.file if call.caller else None
        caller_line = call.caller.line if call.caller else None

        promoted_out = DataSink(
            id=f"out_{max_id:04d}",
            type=template.type,
            format=template.format,
            name=dest_name,
            path=dest_name,
            mode=template.mode,
            source_id=df_source_id,
            location=SourceLocation(
                pathfile=caller_file or "",
                scope=f"indirect:{func_name}",
                span=f"{caller_line}:0-{caller_line}:0" if caller_line else None,
            ) if caller_file else None,
            is_indirect=True,
            via_function=func_name,
        )
        promoted.append(promoted_out)

    if promoted:
        asg.data_out.extend(promoted)


def _promote_indirect_inputs(asg: "ASG") -> None:
    """
    Promote indirect inputs from utility function calls to caller scope (Phase 2.12).

    Mirror of _promote_indirect_outputs: when a utility function (e.g., insert_into_rds)
    reads data internally (spark.read.csv(s3_full_path)), this phase creates concrete
    DataSource entries at each call site with the resolved source name from
    literal_arguments.
    """
    from warp_core.ir.pyspark_models import DataSource, SourceLocation

    func_data_in: dict[str, list] = {}
    for inp in asg.data_in:
        if inp.location and inp.location.scope:
            scope_parts = inp.location.scope.split(".")
            func_name = scope_parts[-1] if scope_parts else ""
            if func_name:
                func_data_in.setdefault(func_name, []).append(inp)

    if not func_data_in:
        return

    call_graph: dict[str, set[str]] = {}
    for call in asg.execution_calls:
        caller_func = call.caller.function if call.caller else None
        callee_func = call.callee.function if call.callee else None
        if caller_func and callee_func:
            call_graph.setdefault(caller_func, set()).add(callee_func)

    def _find_transitive_data_in(
        func_name: str, visited: set[str] | None = None
    ) -> list | None:
        if visited is None:
            visited = set()
        if func_name in visited:
            return None
        visited.add(func_name)

        if func_name in func_data_in:
            return func_data_in[func_name]

        for callee in call_graph.get(func_name, set()):
            result = _find_transitive_data_in(callee, visited)
            if result:
                return result
        return None

    max_id = 0
    for inp in asg.data_in:
        if inp.id.startswith("in_"):
            try:
                num = int(inp.id[3:])
                max_id = max(max_id, num)
            except ValueError:
                pass

    promoted = []
    for call in asg.execution_calls:
        func_name = call.callee.function if call.callee else None
        if not func_name:
            continue

        callee_inputs = _find_transitive_data_in(func_name)
        if not callee_inputs:
            continue

        template = callee_inputs[0]
        template_file = template.location.pathfile if template.location else None
        caller_file = call.caller.file if call.caller else None
        if template_file and caller_file and template_file == caller_file:
            continue

        lit_args = call.literal_arguments or {}
        source_name = (
            lit_args.get("table_name")
            or lit_args.get("name")
            or lit_args.get("path")
            or lit_args.get("dbtable")
        )
        if not source_name:
            continue

        max_id += 1
        caller_line = call.caller.line if call.caller else None

        promoted_in = DataSource(
            id=f"in_{max_id:04d}",
            type=template.type,
            format=template.format,
            name=source_name,
            path=source_name,
            location=SourceLocation(
                pathfile=caller_file or "",
                scope=f"indirect:{func_name}",
                span=f"{caller_line}:0-{caller_line}:0" if caller_line else None,
            ) if caller_file else None,
            is_indirect=True,
            via_function=func_name,
        )
        promoted.append(promoted_in)

    if promoted:
        asg.data_in.extend(promoted)


def _assign_fallback_names(asg: "ASG") -> None:
    """
    Assign sequential fallback names to unnamed non-test inputs (Phase 3.2).

    Inputs that still have no name after all derivation phases get a
    type-based sequential name so they appear in data_io and become testable.

    Prefixes by type:
      - redshift  -> RDSHFT_001, RDSHFT_002, ...
      - jdbc      -> JDBC_001, ...
      - other non-memory types with path -> PTH_001, ...
      - memory without name -> skipped (internal constructs)
    """
    import logging

    logger = logging.getLogger(__name__)

    PREFIX_MAP = {
        "redshift": "RDSHFT",
        "jdbc": "JDBC",
        "snowflake": "SNFLK",
    }

    counters: dict[str, int] = {}
    named_count = 0

    for inp in asg.data_in:
        if inp.name or getattr(inp, "is_test_file", False):
            continue

        src_type = inp.type or "unknown"

        if src_type == "memory":
            continue

        prefix = PREFIX_MAP.get(src_type, "PTH")
        counters[prefix] = counters.get(prefix, 0) + 1
        inp.name = f"{prefix}_{counters[prefix]:03d}"
        named_count += 1

    if named_count:
        logger.info("Fallback naming: %d inputs assigned sequential names", named_count)


def _propagate_columns_by_name(asg: "ASG") -> None:
    """
    Cross-reference column propagation (Phase 2.13).

    When a data_in has no inferred_columns but shares its name with a data_out
    or another data_in that does have columns, propagate those columns with
    LOW confidence and a distinct source (XREF_OUTPUT or XREF_INPUT) so
    downstream tools and users know the origin.
    """
    from warp_core.ir.pyspark_models import InferredColumn, InferenceSource, InferenceConfidence

    # Build name -> best columns map from data_out (priority) and data_in
    out_columns: dict[str, list] = {}
    for out in asg.data_out:
        name = out.name
        if name and out.inferred_columns and name not in out_columns:
            out_columns[name] = out.inferred_columns

    in_columns: dict[str, list] = {}
    for inp in asg.data_in:
        name = inp.name
        if name and inp.inferred_columns:
            if name not in in_columns or len(inp.inferred_columns) > len(in_columns[name]):
                in_columns[name] = inp.inferred_columns

    propagated_count = 0
    for inp in asg.data_in:
        if inp.inferred_columns or not inp.name:
            continue

        source_cols = None
        xref_source = None

        if inp.name in out_columns:
            source_cols = out_columns[inp.name]
            xref_source = InferenceSource.XREF_OUTPUT
        elif inp.name in in_columns:
            source_cols = in_columns[inp.name]
            xref_source = InferenceSource.XREF_INPUT

        if source_cols and xref_source:
            inp.inferred_columns = [
                InferredColumn(
                    name=col.name,
                    inferred_type=col.inferred_type,
                    source=xref_source,
                    confidence=InferenceConfidence.LOW,
                )
                for col in source_cols
            ]
            propagated_count += 1

    if propagated_count:
        import logging
        logging.getLogger(__name__).info(
            "Cross-reference propagation: %d inputs enriched with columns", propagated_count
        )


def _infer_widget_type(wp: dict) -> str:
    """Infer a column type from a widget's default_value and valid_values."""
    default = wp.get("default_value") or ""
    valids = wp.get("valid_values", [])
    if default.startswith("runtime:"):
        return "STRING"
    all_vals = ([default] if default else []) + valids
    clean = [v.replace("runtime:", "") for v in all_vals if v]
    if clean and all(v in ("True", "False") for v in clean):
        return "BOOLEAN"
    if default and len(default) == 10 and default[4:5] == "-" and default.replace("-", "").isdigit():
        return "DATE"
    if default and default.lstrip("-").isdigit():
        return "INT"
    if default and default.lstrip("-").replace(".", "", 1).isdigit():
        return "DECIMAL"
    return "STRING"


def _promote_widgets_to_data_in(
    widget_params: list[dict],
    relative_path: str,
    file_asg: "SparkASTParser",
    next_id_fn,
) -> list:
    """Consolidate widget parameters into a single data_in config entry.

    Returns a list of ColumnConstraint objects for widgets with valid_values.
    """
    from warp_core.ir.pyspark_models import (
        DataSource,
        SourceLocation,
        InferredColumn,
        InferenceSource,
        InferenceConfidence,
        ColumnConstraint,
        ConstraintType,
    )

    seen: set[str] = set()
    columns: list[InferredColumn] = []
    constraints: list[ColumnConstraint] = []
    first_line = None

    for wp in widget_params:
        name = wp.get("name", "")
        if not name or name.startswith("runtime:") or name in seen:
            continue
        seen.add(name)
        if first_line is None:
            first_line = wp.get("line", 1)
        default = wp.get("default_value")
        columns.append(InferredColumn(
            name=name,
            inferred_type=_infer_widget_type(wp),
            source=InferenceSource.WIDGET_DEFAULT,
            confidence=InferenceConfidence.HIGH if default else InferenceConfidence.MEDIUM,
            default_value=default,
        ))
        valid_values = wp.get("valid_values", [])
        if valid_values:
            constraints.append(ColumnConstraint(
                column_name=name,
                constraint_type=ConstraintType.ENUM,
                value=valid_values,
                value_type="STRING",
                source_transformation="widget_definition",
                location=SourceLocation(
                    pathfile=relative_path,
                    span=f"{wp.get('line', 1)}-{wp.get('line', 1)}",
                ),
            ))

    if not columns:
        return constraints

    node_id = next_id_fn("in")
    file_stem = relative_path.rsplit("/", 1)[-1].replace(".py", "")

    entry = DataSource(
        id=node_id,
        type="config",
        format="dbutils.widgets",
        name=f"widget_config_{file_stem}",
        path=None,
        location=SourceLocation(
            pathfile=relative_path,
            span=f"{first_line}-{first_line}",
        ),
        inferred_columns=columns,
    )
    file_asg.data_in.append(entry)
    return constraints




# ---------------------------------------------------------------------------
# Per-file parsing result container
# ---------------------------------------------------------------------------

from dataclasses import dataclass as _dc, field as _fld
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from warp_core.ir.pyspark_models import SourceFile as _SF


@_dc
class _FileResult:
    """Result of parsing a single Python file."""

    source_file: "_SF | None" = None
    data_in: list = _fld(default_factory=list)
    data_out: list = _fld(default_factory=list)
    transformations: list = _fld(default_factory=list)
    functions: list = _fld(default_factory=list)
    execution_calls: list = _fld(default_factory=list)
    control_nodes: list = _fld(default_factory=list)
    window_specs: list = _fld(default_factory=list)
    call_sites: list = _fld(default_factory=list)
    inference_warnings: list = _fld(default_factory=list)
    inference_stats: dict = _fld(default_factory=dict)
    widget_constraints: list = _fld(default_factory=list)
    app_name: str | None = None
    file_ast: tuple | None = None  # (relative_path, processed_code)


def _parse_single_file(
    py_file: Path,
    dir_path: Path,
    parsing_stats: dict,
    call_offset: int,
) -> tuple[_FileResult | None, int]:
    """Parse a single Python file, updating parsing_stats in-place.

    Returns (result, updated_call_offset).
    """
    from warp_core.ir.pyspark_models import ParsedFileInfo, SourceFile, NotebookDependency

    parsing_stats["total"] += 1
    relative_path = str(py_file.relative_to(dir_path))

    # Read source
    try:
        source_code = py_file.read_text(encoding="utf-8")
    except Exception as e:
        parsing_stats["syntax_errors"] += 1
        parsing_stats["file_details"].append(ParsedFileInfo(
            path=relative_path,
            file_type="python_script",
            syntax_status="error",
            syntax_error=f"Cannot read file: {e}",
            understanding_status="skipped",
        ))
        return None, call_offset

    processed_code, file_type, corrections = preprocess_source(source_code, relative_path)

    if file_type == "databricks_notebook":
        parsing_stats["databricks_notebooks"] += 1
    else:
        parsing_stats["python_scripts"] += 1

    # Phase 1: Syntax
    syntax_status = "ok"
    syntax_correction = None
    if corrections:
        syntax_status = "corrected"
        syntax_correction = "; ".join(corrections)
        parsing_stats["syntax_corrected"] += 1
    else:
        parsing_stats["syntax_ok"] += 1

    # Phase 2: Understanding
    try:
        parser = SparkASTParser(workload_root=dir_path)
        file_asg = parser.parse(processed_code, relative_path)
        parsing_stats["understanding_ok"] += 1
    except (SyntaxError, IndentationError) as e:
        parsing_stats["syntax_ok"] -= 1 if not corrections else 0
        parsing_stats["syntax_corrected"] -= 1 if corrections else 0
        parsing_stats["syntax_errors"] += 1
        parsing_stats["file_details"].append(ParsedFileInfo(
            path=relative_path,
            file_type=file_type,
            syntax_status="error",
            syntax_error=f"{type(e).__name__}: {e}",
            understanding_status="skipped",
        ))
        return None, call_offset
    except Exception as e:
        parsing_stats["understanding_errors"] += 1
        parsing_stats["file_details"].append(ParsedFileInfo(
            path=relative_path,
            file_type=file_type,
            syntax_status=syntax_status,
            syntax_correction=syntax_correction,
            understanding_status="error",
            understanding_error=f"{type(e).__name__}: {e}",
        ))
        return None, call_offset

    if syntax_status == "corrected":
        parsing_stats["file_details"].append(ParsedFileInfo(
            path=relative_path,
            file_type=file_type,
            syntax_status="corrected",
            syntax_correction=syntax_correction,
            understanding_status="ok",
        ))

    # Intra-file lineage
    result = _FileResult()
    result.file_ast = (relative_path, processed_code)
    result.inference_stats = dict(parser._inference_stats)
    result.inference_warnings = list(parser._inference_warnings)

    if parser.call_sites:
        from asg_pyspark.parser.lineage_linker import LineageLinker
        linker = LineageLinker(
            call_sites=parser.call_sites,
            functions=[f.model_dump() for f in file_asg.functions],
        )
        file_asg = linker.resolve(file_asg)
        result.call_sites = list(parser.call_sites)

    SymbolTable.update_from_asg(file_asg)

    # Build SourceFile entry
    if file_asg.source_files:
        orig = file_asg.source_files[0]
        sf_entry = SourceFile(
            path=relative_path,
            imports=orig.imports.copy(),
            source_type=orig.source_type,
            is_entry_point=orig.is_entry_point,
            entry_point_reason=orig.entry_point_reason,
            entry_point_lineno=orig.entry_point_lineno,
            entry_point_scope=orig.entry_point_scope,
            has_spark_session=orig.has_spark_session,
        )
    else:
        sf_entry = SourceFile(path=relative_path)

    display_count = count_display_outputs(processed_code)
    if display_count > 0:
        sf_entry.display_outputs = display_count

    if file_type == "databricks_notebook":
        desc = extract_notebook_description(source_code)
        if desc:
            sf_entry.description = desc
        pip_deps = []
        for line in source_code.splitlines():
            stripped = line.strip()
            if stripped.startswith("# MAGIC %pip install"):
                pkgs = stripped[len("# MAGIC %pip install"):].strip().split()
                pip_deps.extend(p for p in pkgs if p and not p.startswith("-"))
        if pip_deps:
            sf_entry.pip_dependencies = pip_deps
        nb_deps = extract_notebook_dependencies(source_code, relative_path)
        if nb_deps:
            sf_entry.notebook_dependencies = [NotebookDependency(**dep) for dep in nb_deps]

    widget_params = extract_widget_parameters(processed_code)
    if widget_params:
        wc = _promote_widgets_to_data_in(
            widget_params, relative_path, file_asg, parser._next_id,
        )
        result.widget_constraints = wc

    udf_defs = extract_udf_definitions(processed_code)
    if udf_defs:
        for udf_def in udf_defs:
            udf_fname = udf_def["function_name"]
            bare_name = udf_fname.split(".")[-1] if "." in udf_fname else udf_fname
            for func in file_asg.functions:
                if func.name == bare_name:
                    func.is_udf = True
                    func.udf_return_schema = udf_def.get("return_schema")

    result.source_file = sf_entry

    for func in file_asg.functions:
        func.source_file = relative_path
        if func.location:
            func.location.pathfile = relative_path
    for node in file_asg.data_in:
        if node.location:
            node.location.pathfile = relative_path
    for node in file_asg.data_out:
        if node.location:
            node.location.pathfile = relative_path
    for node in file_asg.transformations:
        if node.location:
            node.location.pathfile = relative_path

    result.data_in = list(file_asg.data_in)
    result.data_out = list(file_asg.data_out)
    result.transformations = list(file_asg.transformations)
    result.functions = list(file_asg.functions)

    for cn in file_asg.control_nodes:
        if cn.source_location:
            cn.source_location.pathfile = relative_path
    result.control_nodes = list(file_asg.control_nodes)
    result.window_specs = list(file_asg.window_specs)

    for exec_call in file_asg.execution_calls:
        call_offset += 1
        exec_call.call_id = f"call_{call_offset:03d}"
        exec_call.caller.file = relative_path
        if exec_call.callee.file is None:
            exec_call.callee.file = relative_path
    result.execution_calls = list(file_asg.execution_calls)

    if file_asg.extraction_metadata.app_name:
        result.app_name = file_asg.extraction_metadata.app_name

    return result, call_offset


import re as _re

# ---------------------------------------------------------------------------
# Node nature classification
# ---------------------------------------------------------------------------

# Matches SQL queries whose top-level projection is purely aggregation and
# therefore returns a scalar, not a business DataFrame.  SHOW and DESCRIBE
# are also orchestration/discovery — not data.
_PYSPARK_METADATA_QUERY_RE = _re.compile(
    r"^\s*(?:SELECT\s+(?:MAX|MIN|COUNT|AVG|SUM)\s*\(|SHOW\b|DESCRIBE\b|EXPLAIN\b)",
    _re.IGNORECASE,
)

# Python test-file naming patterns: test_*.py, *_test.py, *Test.py, *Spec.py,
# or files living under a test/ tests/ directory segment.
_PYSPARK_TEST_PATH_RE = _re.compile(
    r"(?:(?:^|[/\\])tests?[/\\])"           # …/test/  or  …/tests/
    r"|(?:(?:^|[/\\])test_[^/\\]+\.py$)"    # test_foo.py
    r"|(?:_test\.py$)"                       # foo_test.py
    r"|(?:(?:^|[/\\])[A-Z][^/\\]*Test\.py$)"  # FooTest.py
    r"|(?:(?:^|[/\\])[A-Z][^/\\]*Spec\.py$)", # FooSpec.py
    _re.IGNORECASE,
)


def _is_metadata_sql(query: str) -> bool:
    """Return True if *query* is an orchestration/discovery SQL expression.

    Uses sqlglot's SQL AST when available (more precise) and falls back to a
    regex for edge cases where sqlglot cannot parse the dialect.

    A query is classified as *metadata* when its top-level SELECT has:
    - at least one aggregate function (MAX, MIN, COUNT, AVG, SUM), AND
    - no GROUP BY clause (which would produce a multi-row DataFrame).

    SHOW / DESCRIBE / EXPLAIN are always metadata regardless of structure.
    """
    # Fast path: SHOW / DESCRIBE / EXPLAIN don't need sqlglot
    stripped = query.strip().upper()
    if stripped.startswith(("SHOW ", "DESCRIBE ", "EXPLAIN ")):
        return True

    try:
        import sqlglot
        import sqlglot.expressions as exp

        stmt = sqlglot.parse_one(query, error_level=sqlglot.ErrorLevel.IGNORE)
        if not isinstance(stmt, exp.Select):
            return False

        has_agg = any(
            isinstance(node, exp.AggFunc)
            for node in stmt.find_all(exp.AggFunc)
        )
        has_group_by = stmt.args.get("group") is not None
        return has_agg and not has_group_by

    except Exception:
        # Fallback: regex on first token sequence
        return bool(_PYSPARK_METADATA_QUERY_RE.match(query))


def _classify_node_nature(asg: "ASG") -> None:
    """Classify each data_in node's ``nature`` field.

    Operates on the fully merged ASG (after all resolve/propagate passes) so
    that ``location.pathfile`` and ``query`` are already populated.

    Rules (applied in priority order):

    - ``fixture``: node comes from a test file (``is_test_file=True``) or its
      source path matches ``_PYSPARK_TEST_PATH_RE``.
    - ``metadata``: node has a SQL ``query`` that is an orchestration/discovery
      expression (SELECT MAX without GROUP BY, SHOW, DESCRIBE, EXPLAIN) as
      determined by sqlglot AST analysis with regex fallback.
    - ``data`` (default): everything else — standard pipeline input.
    """
    for inp in asg.data_in:
        pathfile = ""
        if inp.location and inp.location.pathfile:
            pathfile = inp.location.pathfile

        if inp.is_test_file or _PYSPARK_TEST_PATH_RE.search(pathfile):
            inp.nature = "fixture"
        elif inp.query and _is_metadata_sql(inp.query):
            inp.nature = "metadata"


# ── Artifact-node classification ──────────────────────────────────────────────
# Regex: names that are only punctuation / symbols (no letters, digits, or _ . -)
_ARTIFACT_PUNCTUATION_RE = _re.compile(r"^[^a-zA-Z0-9_.\-]+$")

# Regex: Python-style config/path variable names that leak into table parsing
# (path_file_*, file_*, dir_*, directoryout, param_*)
_ARTIFACT_CONFVAR_RE = _re.compile(
    r"^(?:path|file|dir(?:ectory)?|out(?:put)?|in(?:put)?|param|conf(?:ig)?)"
    r"[_][a-zA-Z0-9_]+$",
    _re.IGNORECASE,
)


def _classify_artifact_nodes(asg: "ASG", file_asts: list[tuple[str, str]]) -> None:
    """Mark parser false-positives as ``nature='artifact'``.

    These are ``data_in`` nodes whose names are not real data-source identifiers
    but are instead parser artefacts: leaked variable names, relative paths,
    punctuation tokens, or Python module/import names accidentally resolved as
    table references.

    Rules (applied in priority order; first match wins):

    1. **Pure punctuation** — name contains only non-identifier characters
       (e.g. ``";"``).  These are tokenisation errors.
    2. **Relative/absolute path fragment** — name contains ``/`` or ``\\``.
       Real table names never contain path separators.
    3. **Config-variable pattern** — name matches
       ``path_*``, ``file_*``, ``dir_*``, ``output_*``, ``param_*``, etc.
       AND the node has no ``location.pathfile`` (i.e. it has no source-file
       context that would justify it being a real table read).
    4. **Imported module name** — name is a bare Python identifier that appears
       as an imported module in any of the parsed source files AND the node has
       no ``location.pathfile`` and no discovered columns.
    5. **DDL statement** — a ``sql``-type node whose ``query`` starts with a
       DDL keyword (``CREATE``, ``DROP``, ``ALTER``, ``USE``, ``SHOW``,
       ``DESCRIBE``).  ``spark.sql("CREATE TABLE ...")`` registers a table in
       the metastore; it does not read data.  These nodes are schema-management
       operations, not data sources.

    Only nodes whose ``nature`` is currently ``"data"`` are re-classified;
    nodes already marked ``metadata`` or ``fixture`` are not touched.
    """
    import ast as _ast

    # --- Build the set of imported module names across all parsed files ---
    imported_modules: set[str] = set()
    for _fpath, source in file_asts:
        try:
            tree = _ast.parse(source)
        except SyntaxError:
            continue
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    # top-level module name (e.g. "alimentation" from "jobs.alimentation")
                    imported_modules.add(alias.name.split(".")[-1])
                    if alias.asname:
                        imported_modules.add(alias.asname)
            elif isinstance(node, _ast.ImportFrom) and node.names:
                for alias in node.names:
                    imported_modules.add(alias.name)
                    if alias.asname:
                        imported_modules.add(alias.asname)

    for inp in asg.data_in:
        if inp.nature != "data":
            continue

        name: str = (inp.name or "").strip()
        if not name:
            continue

        pathfile: str = (inp.location.pathfile or "") if inp.location else ""
        has_location = bool(pathfile)
        col_count = len(inp.columns or []) + len(inp.inferred_columns or [])
        # For Rule 4, SQL SELECT-list columns (source="select") are weak evidence:
        # a Python module name may coincidentally appear as a SQL table reference,
        # but that alone doesn't prove the node is a real data source.  We only
        # count columns from schema_definition, catalog, usage, etc.
        _WEAK_SOURCES = {"select", "order_by", "filter_condition"}
        strong_col_count = len(inp.columns or []) + sum(
            1 for c in (inp.inferred_columns or [])
            if getattr(c, "source", None) not in _WEAK_SOURCES
        )

        reason: str | None = None

        # Rule 1: pure punctuation / symbol tokens
        if _ARTIFACT_PUNCTUATION_RE.match(name):
            reason = f"pure-punctuation token: {name!r}"

        # Rule 2: path-separator in name → filesystem path fragment
        elif "/" in name or "\\" in name:
            reason = f"filesystem path leaked as table name: {name!r}"

        # Rule 3: Python config-variable naming convention + no source file
        elif not has_location and _ARTIFACT_CONFVAR_RE.match(name):
            reason = f"Python config variable name with no source location: {name!r}"

        # Rule 4: imported module name with no source file and no substantive columns.
        # SQL SELECT columns (source="select") are excluded because a Python module
        # name can appear verbatim in a SQL query without being a real external table.
        elif not has_location and not strong_col_count and name in imported_modules:
            reason = f"Python module/import name with no source location or columns: {name!r}"

        # Rule 5: DDL statement registered as sql data_in.
        # spark.sql("CREATE TABLE ...") / "DROP TABLE" / "ALTER TABLE" etc. are
        # schema-management operations, not data reads.
        elif inp.type == "sql":
            raw_q = (getattr(inp, "query", None) or "").strip()
            if raw_q.startswith("runtime:"):
                raw_q = raw_q[len("runtime:"):]
            norm_q = raw_q.strip("f'\"").replace("\\n", " ").strip().upper()
            _DDL_PREFIXES = ("CREATE ", "DROP ", "ALTER ", "USE ", "SHOW ", "DESCRIBE ")
            if any(norm_q.startswith(kw) for kw in _DDL_PREFIXES):
                reason = f"DDL/metadata statement (not a data read): {norm_q[:60]!r}"

        if reason:
            inp.nature = "artifact"
            # Preserve reason in a machine-readable field for auditability
            if inp.location is None:
                from warp_core.ir.pyspark_models import SourceLocation
                inp.location = SourceLocation(pathfile="", span="0:0-0:0")
            # Store reason as a synthetic scope annotation so it survives serialisation
            if not inp.location.scope:
                inp.location.scope = f"[artifact] {reason}"

    # Rule 6: phantom indirect output with no name and no columns.
    # _promote_indirect_outputs() creates placeholder DataSink nodes (is_indirect=True)
    # for every function call site that *might* write data.  When the function does
    # not produce a real write side-effect (e.g. deid_scos.py which only reads),
    # those placeholders have no name, no format, and no columns — they are noise.
    # We mark them as artifacts so they are excluded from Identity scoring.
    for snk in asg.data_out:
        if snk.nature != "data":
            continue
        if not snk.is_indirect:
            continue
        snk_name = (snk.name or "").strip()
        snk_cols = len(getattr(snk, "columns", None) or [])
        if not snk_name and not snk_cols:
            snk.nature = "artifact"


def _stitch_sink_passthrough_arcs(asg: "ASG") -> None:
    """Create passthrough transformations for data_in nodes that feed a sink via a utility function.

    After ``_promote_indirect_outputs`` runs, ``DataSink`` nodes that were created
    from utility-function call sites have their ``source_id`` field set to the
    ``data_in`` node that was passed as the DataFrame argument
    (e.g. ``ut.write_csv(df_cle, …)`` → ``source_id = "in_089"``).

    However, the connectivity score only counts ``data_in`` IDs that appear in
    ``transformation.inputs`` — it does NOT inspect ``data_out.source_id``.
    This pass bridges that gap by creating a minimal ``passthrough`` transformation
    for each such pairing that is not already connected.

    A second sweep also covers ``execution_calls`` that bind a ``data_in`` as
    first positional / "df" argument to any callee — catching sink patterns whose
    utility function did not produce a promoted ``data_out`` (e.g. when the
    callee writes to an external system with no templatable data_out).
    """
    from warp_core.ir.pyspark_models import TransformationNode

    # Set of data_in IDs already wired into at least one transformation
    already_connected: set[str] = {
        inp
        for tx in asg.transformations
        for inp in (tx.inputs or [])
    }

    # Counter for synthetic transformation IDs
    max_tx = 0
    for tx in asg.transformations:
        if tx.id.startswith("tx_"):
            try:
                max_tx = max(max_tx, int(tx.id[3:]))
            except ValueError:
                pass

    new_txs: list[TransformationNode] = []

    def _maybe_add(source_id: str) -> None:
        nonlocal max_tx
        if not source_id or source_id in already_connected:
            return
        # Guard: source_id must belong to a known data_in
        if not any(d.id == source_id for d in asg.data_in):
            return
        max_tx += 1
        new_txs.append(TransformationNode(
            id=f"tx_{max_tx:04d}",
            operation="passthrough",
            inputs=[source_id],
            outputs=[],
            logic="implicit sink via utility function call",
        ))
        already_connected.add(source_id)

    # Pass 1: promoted data_out nodes that have source_id set
    for out in asg.data_out:
        if out.source_id:
            _maybe_add(out.source_id)

    # Pass 2: execution_calls that bind a data_in as the DataFrame argument
    _DF_ARG_NAMES = frozenset({
        "df", "dataframe", "data", "data_table", "new_data_table",
        "table", "arg_0", "arg_1",
    })
    for call in asg.execution_calls:
        if not (call.bindings and call.bindings.inputs):
            continue
        for binding in call.bindings.inputs:
            if (
                binding.source_type == "data_in"
                and binding.arg_name in _DF_ARG_NAMES
                and binding.source_id
            ):
                _maybe_add(binding.source_id)

    if new_txs:
        asg.transformations.extend(new_txs)


def _stitch_function_return_arcs(asg: "ASG", file_asts: list[tuple[str, str]]) -> None:
    """Connect orphan memory data_in nodes that are returned by cross-module functions.

    Pattern (common in MRB):
        # par_cle_src_mrb.py
        def generate(...):
            df_return = spark.createDataFrame(...)
            return df_return

        # param.py
        df_cle = par_cle_src_mrb.generate(...)
        ut.write_csv(df_cle, 'PAR_CLE_SRC_MRB_' + app, ...)

    The parser collapses all ``var = module.generate()`` calls to the same
    ``data_in`` node, leaving the other callee modules' ``df_return`` nodes
    permanently orphaned. This pass scans every caller file's AST, resolves
    ``var = alias.function(...)`` assignments back to the callee module's
    orphan data_in nodes (with path-prefix disambiguation for multi-variant
    workloads), and creates minimal ``passthrough`` transformations so those
    nodes are counted by the connectivity score.
    """
    import ast as _ast
    import os
    from collections import defaultdict
    from warp_core.ir.pyspark_models import TransformationNode

    # --- Step 1: Build registry of orphan data_in nodes keyed by function scope ---
    already_connected: set[str] = {
        inp
        for tx in asg.transformations
        for inp in (tx.inputs or [])
    }

    # (basename, scope_function) → [(data_in_id, pathfile)]
    return_registry: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for ds in asg.data_in:
        if ds.id in already_connected:
            continue
        scope = (ds.location.scope or "").strip() if ds.location else ""
        pathfile = (ds.location.pathfile or "") if ds.location else ""
        if not scope or not pathfile:
            continue
        base = os.path.splitext(os.path.basename(pathfile))[0]
        return_registry[(base, scope)].append((ds.id, pathfile))

    if not return_registry:
        return

    # --- Step 2: Scan caller files for `var = alias.function(...)` assignments ---
    max_tx = 0
    for tx in asg.transformations:
        if tx.id.startswith("tx_"):
            try:
                max_tx = max(max_tx, int(tx.id[3:]))
            except ValueError:
                pass

    new_txs: list[TransformationNode] = []
    newly_connected: set[str] = set()

    for caller_file, source_code in file_asts:
        try:
            tree = _ast.parse(source_code)
        except SyntaxError:
            continue

        # Build import alias map: local_name → module_basename
        import_aliases: dict[str, str] = {}
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    mod_base = alias.name.split(".")[-1]
                    local = alias.asname or mod_base
                    import_aliases[local] = mod_base
            elif isinstance(node, _ast.ImportFrom):
                mod = node.module or ""
                mod_base = mod.split(".")[-1]
                for alias in node.names:
                    local = alias.asname or alias.name
                    # Only add if the imported name itself is a module alias
                    # (e.g. `from jobs.param import par_cle_src_mrb`)
                    import_aliases[local] = alias.name

        # Scan function bodies and top-level for `var = alias.function(...)`
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Assign):
                continue
            if not isinstance(node.value, _ast.Call):
                continue
            call = node.value
            if not isinstance(call.func, _ast.Attribute):
                continue
            if not isinstance(call.func.value, _ast.Name):
                continue

            func_name = call.func.attr
            module_alias = call.func.value.id
            # Resolve alias → module basename
            module_base = import_aliases.get(module_alias, module_alias)

            registry_key = (module_base, func_name)
            if registry_key not in return_registry:
                continue

            candidates = return_registry[registry_key]

            # Disambiguate by longest common path prefix between caller and callee
            def _common_prefix_len(a: str, b: str) -> int:
                return len(os.path.commonprefix([a, b]))

            best_candidates = sorted(
                candidates,
                key=lambda c: _common_prefix_len(caller_file, c[1]),
                reverse=True,
            )

            for data_in_id, _callee_path in best_candidates:
                if data_in_id in already_connected or data_in_id in newly_connected:
                    continue
                max_tx += 1
                new_txs.append(TransformationNode(
                    id=f"tx_{max_tx:04d}",
                    operation="passthrough",
                    inputs=[data_in_id],
                    outputs=[],
                    logic=f"cross-module return: {module_alias}.{func_name}() → {_callee_path}",
                ))
                newly_connected.add(data_in_id)
                # Only connect the best-matched candidate per call site
                break

    if new_txs:
        asg.transformations.extend(new_txs)


def _run_post_merge_phases(
    asg: "ASG",
    all_call_sites: list,
    file_asts: list[tuple[str, str]],
    widget_constraints: list,
    infer_schemas: bool,
    include_timestamp: bool,
    column_types_path: "str | Path | None" = None,
) -> "ASG":
    """Run all post-merge phases (2.7 through 6) on the merged ASG."""

    _resolve_synthetic_arg_names(asg)

    # Phase 2.8: Cross-file lineage
    if all_call_sites and asg.functions:
        from asg_pyspark.parser.lineage_linker import LineageLinker

        all_func_names = {fn.name for fn in asg.functions}
        per_file_funcs: dict[str, set[str]] = {}
        for fn in asg.functions:
            if fn.location and fn.location.pathfile:
                per_file_funcs.setdefault(fn.location.pathfile, set()).add(fn.name)

        cross_file_sites = [
            cs for cs in all_call_sites
            if cs.get("function_name") in all_func_names
            and cs.get("function_name") not in per_file_funcs.get(cs.get("source_file", ""), set())
        ]
        if cross_file_sites:
            linker = LineageLinker(
                call_sites=cross_file_sites,
                functions=[f.model_dump() for f in asg.functions],
            )
            asg = linker.resolve(asg)

    _resolve_cross_file_function_sources(asg)
    _relink_param_inputs(asg)
    _discover_cross_file_calls(asg, file_asts)
    _link_widget_gets_to_transformations(asg, file_asts)
    _promote_indirect_outputs(asg)
    _promote_indirect_inputs(asg)
    _stitch_sink_passthrough_arcs(asg)
    _stitch_function_return_arcs(asg, file_asts)

    if infer_schemas:
        from warp_core.schema.schema_tracker import SchemaPropagator
        asg = SchemaPropagator().process(asg)

    _resolve_runtime_paths(asg)
    _infer_column_types_from_usage(asg, file_asts)

    from warp_core.schema.naming_conventions import apply_naming_conventions
    apply_naming_conventions(
        asg,
        config_path=column_types_path,
        workload_root=getattr(asg.extraction_metadata, "workload_root", None),
    )

    _propagate_columns_by_name(asg)
    _assign_fallback_names(asg)

    # StructType schema extraction: scan all files for StructType/StructField
    # definitions and link them to DataSource nodes, replacing garbage
    # join-condition columns with authoritative SCHEMA_DEFINITION columns.
    from asg_pyspark.parser.struct_schema_extractor import apply_struct_schemas
    apply_struct_schemas(asg, file_asts)

    # Classify node nature (metadata / fixture / data) after all names and
    # schemas are resolved so that pathfile and query are fully populated.
    _classify_node_nature(asg)

    # TODO (backward-type-prop): apply_backward_type_propagation(asg) once
    # param_resolution is wired into the ASG — see pending task.

    from asg_pyspark.parser.origin_resolver import resolve_origins
    asg = resolve_origins(asg)

    asg = _generate_missing_source_warnings(asg)

    from asg_pyspark.parser.constraint_extractor import enrich_asg_with_constraints
    asg = enrich_asg_with_constraints(asg)

    if widget_constraints:
        asg.column_constraints.extend(widget_constraints)

    # Demote parser false-positives LAST — after resolve_origins and
    # enrich_asg_with_constraints, which may create new data_in nodes
    # (e.g. unresolved table references from Python 2 code) that are
    # themselves garbage.  Running here ensures all nodes are visible.
    _classify_artifact_nodes(asg, file_asts)

    if include_timestamp:
        from datetime import datetime
        asg.extraction_metadata.generated_at = datetime.now()

    return asg


def parse_spark_directory(
    dir_path: str | Path,
    *,
    infer_schemas: bool = True,
    catalog: dict[str, Any] | None = None,
    include_timestamp: bool = True,
    column_types_path: str | Path | None = None,
) -> "ASG":
    """Parse all Python files in a directory and return a merged ASG.

    Supports multi-file workloads where different modules may have
    different imports and functions.
    """
    from datetime import datetime

    SymbolTable.reset_global()
    TypeTracker.reset()
    SparkASTParser.reset_global_counters()

    dir_path = Path(dir_path)
    if not dir_path.is_dir():
        raise ValueError(f"Not a directory: {dir_path}")

    py_files = sorted(dir_path.rglob("*.py"))
    if not py_files and not sorted(dir_path.rglob("*.ipynb")):
        raise ValueError(f"No Python files found in: {dir_path}")

    # Pass 1: populate SymbolTable with all definitions
    for py_file in py_files:
        try:
            code = py_file.read_text(encoding="utf-8")
            rel = str(py_file.relative_to(dir_path))
            processed, _, _ = preprocess_source(code, rel)
            _extract_definitions(processed, rel, dir_path)
        except Exception:
            continue

    # Pass 2: full parsing
    parsing_stats = {
        "total": 0,
        "databricks_notebooks": 0, "python_scripts": 0,
        "syntax_ok": 0, "syntax_corrected": 0, "syntax_errors": 0,
        "understanding_ok": 0, "understanding_errors": 0,
        "file_details": [],
    }

    source_files = []
    merged_data_in = []
    merged_data_out = []
    merged_transformations = []
    merged_functions = []
    merged_execution_calls = []
    merged_control_nodes = []
    merged_window_specs = []
    all_call_sites = []
    merged_inference_warnings = []
    merged_inference_stats = {"inferred": 0, "name_match": 0, "excluded": 0}
    widget_constraints = []
    file_asts = []
    app_name = None
    call_offset = 0

    for py_file in py_files:
        result, call_offset = _parse_single_file(py_file, dir_path, parsing_stats, call_offset)
        if result is None:
            continue

        source_files.append(result.source_file)
        merged_data_in.extend(result.data_in)
        merged_data_out.extend(result.data_out)
        merged_transformations.extend(result.transformations)
        merged_functions.extend(result.functions)
        merged_execution_calls.extend(result.execution_calls)
        merged_control_nodes.extend(result.control_nodes)
        merged_window_specs.extend(result.window_specs)
        all_call_sites.extend(result.call_sites)
        merged_inference_warnings.extend(result.inference_warnings)
        for key in merged_inference_stats:
            merged_inference_stats[key] += result.inference_stats.get(key, 0)
        widget_constraints.extend(result.widget_constraints)
        if result.file_ast:
            file_asts.append(result.file_ast)
        if result.app_name and not app_name:
            app_name = result.app_name

    # Build parsing report
    parsing_report = ParsingReport(
        total_files=parsing_stats["total"],
        databricks_notebooks=parsing_stats["databricks_notebooks"],
        python_scripts=parsing_stats["python_scripts"],
        syntax=SyntaxSummary(
            ok=parsing_stats["syntax_ok"],
            corrected=parsing_stats["syntax_corrected"],
            errors=parsing_stats["syntax_errors"],
        ),
        understanding=UnderstandingSummary(
            ok=parsing_stats["understanding_ok"],
            errors=parsing_stats["understanding_errors"],
        ),
        inference=InferenceSummary(
            inferred=merged_inference_stats["inferred"],
            name_match=merged_inference_stats["name_match"],
            excluded=merged_inference_stats["excluded"],
        ) if any(merged_inference_stats.values()) else None,
        files=parsing_stats["file_details"],
        inference_warnings=[TypeInferenceWarning(**w) for w in merged_inference_warnings],
        generated_at=datetime.now() if include_timestamp else None,
    )

    asg = ASG(
        extraction_metadata=ExtractionMetadata(
            workload_root=str(dir_path.resolve()),
            source_file=str(dir_path),
            app_name=app_name,
        ),
        source_files=source_files,
        functions=merged_functions,
        execution_calls=merged_execution_calls,
        data_in=merged_data_in,
        data_out=merged_data_out,
        transformations=merged_transformations,
        control_nodes=merged_control_nodes,
        window_specs=merged_window_specs,
        parsing_report=parsing_report,
    )

    return _run_post_merge_phases(
        asg, all_call_sites, file_asts, widget_constraints,
        infer_schemas, include_timestamp, column_types_path,
    )


# =============================================================================
# Quick Win: CLI for testing
# =============================================================================

def _resolve_runtime_paths(asg) -> None:
    """Resolve runtime:f'...' paths in data_in using the full SymbolTable.

    After all files are parsed, the SymbolTable contains variables from
    every file. This pass tries to resolve paths that were unresolvable
    during single-file parsing because the variables were defined in
    a different file.

    Also extracts logical names from .replace('table_name', 'X') patterns
    for nodes that still have a PTH_XXX placeholder name.
    """
    import ast as ast_mod
    import re as re_mod
    from warp_core.symbol_table import SymbolTable, GLOBAL_SCOPE

    _REPLACE_PAT = re_mod.compile(
        r"""\.replace\(\s*['"]table_name['"]\s*,\s*['"]([^'"]+)['"]\s*\)"""
    )

    for data_in in asg.data_in:
        path = data_in.path
        if not path or not isinstance(path, str) or not path.startswith("runtime:"):
            continue

        # ── .replace('table_name', 'X') extractor (retroactive for PTH_XXX) ─
        if not data_in.name or data_in.name.startswith("PTH_"):
            m = _REPLACE_PAT.search(path)
            if m:
                data_in.name = m.group(1)
                continue
        
        expr = path[len("runtime:"):]
        if not (expr.startswith("f'") or expr.startswith('f"')):
            resolved = SymbolTable.resolve_string_literal(GLOBAL_SCOPE, expr)
            if resolved and not resolved.startswith("runtime:"):
                data_in.path = resolved
                if not data_in.name or data_in.name.startswith("PTH_"):
                    data_in.name = resolved
            continue
        
        try:
            tree = ast_mod.parse(expr, mode="eval")
            if not isinstance(tree.body, ast_mod.JoinedStr):
                continue
            
            parts: list[str] = []
            all_resolved = True
            for value in tree.body.values:
                if isinstance(value, ast_mod.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                elif isinstance(value, ast_mod.FormattedValue) and isinstance(value.value, ast_mod.Name):
                    var_name = value.value.id
                    lit = SymbolTable.resolve_string_literal(GLOBAL_SCOPE, var_name)
                    if lit and not lit.startswith("runtime:"):
                        parts.append(lit)
                    else:
                        all_resolved = False
                        break
                else:
                    all_resolved = False
                    break
            
            if all_resolved and parts:
                resolved_path = "".join(parts)
                data_in.path = resolved_path
                if not data_in.name or data_in.name.startswith("PTH_"):
                    data_in.name = resolved_path
        except Exception:
            continue


def _infer_column_types_from_usage(asg, file_asts: list) -> None:
    """Infer column types for data_in columns by analyzing how they are used.
    
    Patterns detected (all via AST, no regex):
    - F.col('X').like/rlike/startswith/endswith/contains -> X is STRING
    - F.col('X') == F.lit('string') -> X is STRING
    - .withColumn('X', F.lit('string')) -> X is STRING
    - F.col('X') == F.lit(123) -> X is NUMERIC
    """
    import ast as ast_mod
    
    string_cols: set[str] = set()
    numeric_cols: set[str] = set()
    
    for _path, source_code in file_asts:
        try:
            tree = ast_mod.parse(source_code)
        except SyntaxError:
            continue
        
        for node in ast_mod.walk(tree):
            if isinstance(node, ast_mod.Call) and isinstance(node.func, ast_mod.Attribute):
                if node.func.attr in ('like', 'rlike', 'startswith', 'endswith', 'contains', 'substr', 'substring'):
                    receiver = node.func.value
                    if isinstance(receiver, ast_mod.Call) and isinstance(receiver.func, ast_mod.Attribute):
                        if receiver.func.attr == 'col' and receiver.args:
                            arg = receiver.args[0]
                            if isinstance(arg, ast_mod.Constant) and isinstance(arg.value, str):
                                string_cols.add(arg.value)
            
            if isinstance(node, ast_mod.Call) and isinstance(node.func, ast_mod.Attribute):
                if node.func.attr == 'withColumn' and len(node.args) >= 2:
                    col_arg = node.args[0]
                    val_arg = node.args[1]
                    if isinstance(col_arg, ast_mod.Constant) and isinstance(col_arg.value, str):
                        if isinstance(val_arg, ast_mod.Call) and isinstance(val_arg.func, ast_mod.Attribute):
                            if val_arg.func.attr == 'lit' and val_arg.args:
                                lit_val = val_arg.args[0]
                                if isinstance(lit_val, ast_mod.Constant):
                                    if isinstance(lit_val.value, str):
                                        string_cols.add(col_arg.value)
                                    elif isinstance(lit_val.value, (int, float)):
                                        numeric_cols.add(col_arg.value)
                                elif isinstance(lit_val, ast_mod.JoinedStr):
                                    string_cols.add(col_arg.value)
            
            # String functions applied to F.col('X') -> X is STRING
            if isinstance(node, ast_mod.Call) and isinstance(node.func, ast_mod.Attribute):
                str_funcs = ('trim', 'ltrim', 'rtrim', 'lower', 'upper', 'initcap',
                             'lpad', 'rpad', 'regexp_replace', 'regexp_extract',
                             'translate', 'reverse', 'split', 'substring', 'length',
                             'locate', 'instr')
                if node.func.attr in str_funcs and node.args:
                    for arg in node.args:
                        if isinstance(arg, ast_mod.Call) and isinstance(arg.func, ast_mod.Attribute):
                            if arg.func.attr == 'col' and arg.args:
                                col_arg = arg.args[0]
                                if isinstance(col_arg, ast_mod.Constant) and isinstance(col_arg.value, str):
                                    string_cols.add(col_arg.value.rsplit('.', 1)[-1])
            
            if isinstance(node, ast_mod.Compare):
                pairs = [(node.left, c) for c in node.comparators]
                for side_a, side_b in pairs + [(b, a) for a, b in pairs]:
                    if isinstance(side_a, ast_mod.Call) and isinstance(side_a.func, ast_mod.Attribute):
                        if side_a.func.attr == 'col' and side_a.args:
                            if isinstance(side_a.args[0], ast_mod.Constant) and isinstance(side_a.args[0].value, str):
                                col_name = side_a.args[0].value
                                if isinstance(side_b, ast_mod.Call) and isinstance(side_b.func, ast_mod.Attribute):
                                    if side_b.func.attr == 'lit' and side_b.args:
                                        lit_val = side_b.args[0]
                                        if isinstance(lit_val, ast_mod.Constant):
                                            if isinstance(lit_val.value, str):
                                                string_cols.add(col_name)
                                            elif isinstance(lit_val.value, (int, float)):
                                                numeric_cols.add(col_name)
                                        elif isinstance(lit_val, ast_mod.JoinedStr):
                                            string_cols.add(col_name)
    
    # Pattern 4: F.col('X').alias('Y') or F.lit(val).alias('Y')
    for _path, source_code in file_asts:
        try:
            tree = ast_mod.parse(source_code)
        except SyntaxError:
            continue
        for node in ast_mod.walk(tree):
            if isinstance(node, ast_mod.Call) and isinstance(node.func, ast_mod.Attribute):
                if node.func.attr == 'alias' and node.args:
                    alias_arg = node.args[0]
                    if not (isinstance(alias_arg, ast_mod.Constant) and isinstance(alias_arg.value, str)):
                        continue
                    alias_name = alias_arg.value
                    receiver = node.func.value
                    if isinstance(receiver, ast_mod.Call) and isinstance(receiver.func, ast_mod.Attribute):
                        if receiver.func.attr == 'lit' and receiver.args:
                            lit_val = receiver.args[0]
                            if isinstance(lit_val, ast_mod.Constant):
                                if isinstance(lit_val.value, str):
                                    string_cols.add(alias_name)
                                elif isinstance(lit_val.value, (int, float)):
                                    numeric_cols.add(alias_name)
                            elif isinstance(lit_val, ast_mod.JoinedStr):
                                string_cols.add(alias_name)
                        elif receiver.func.attr == 'col' and receiver.args:
                            col_arg = receiver.args[0]
                            if isinstance(col_arg, ast_mod.Constant) and isinstance(col_arg.value, str):
                                src_col = col_arg.value.rsplit('.', 1)[-1]
                                if src_col in string_cols:
                                    string_cols.add(alias_name)
                                elif src_col in numeric_cols:
                                    numeric_cols.add(alias_name)
    
    # Patterns 4b-5: Collect alias pairs, equality pairs, and rename mappings
    # then run a unified convergence loop
    alias_pairs: list[tuple[str, str]] = []
    col_equality_pairs: list[tuple[str, str]] = []
    rename_map: dict[str, str] = {}
    
    for _path, source_code in file_asts:
        try:
            tree = ast_mod.parse(source_code)
        except SyntaxError:
            continue
        for node in ast_mod.walk(tree):
            if isinstance(node, ast_mod.Call) and isinstance(node.func, ast_mod.Attribute):
                # alias() pairs
                if node.func.attr == 'alias' and node.args:
                    alias_arg = node.args[0]
                    if isinstance(alias_arg, ast_mod.Constant) and isinstance(alias_arg.value, str):
                        receiver = node.func.value
                        if isinstance(receiver, ast_mod.Call) and isinstance(receiver.func, ast_mod.Attribute):
                            if receiver.func.attr == 'col' and receiver.args:
                                col_arg = receiver.args[0]
                                if isinstance(col_arg, ast_mod.Constant) and isinstance(col_arg.value, str):
                                    alias_pairs.append((col_arg.value.rsplit('.', 1)[-1], alias_arg.value))
                
                # withColumnRenamed() mappings
                if node.func.attr == 'withColumnRenamed' and len(node.args) >= 2:
                    old_arg = node.args[0]
                    new_arg = node.args[1]
                    if (isinstance(old_arg, ast_mod.Constant) and isinstance(old_arg.value, str) and
                        isinstance(new_arg, ast_mod.Constant) and isinstance(new_arg.value, str)):
                        rename_map[new_arg.value] = old_arg.value
            
            # Equality comparisons: F.col('X') == F.col('Y')
            if isinstance(node, ast_mod.Compare):
                all_sides = [node.left] + list(node.comparators)
                col_names_in_compare = []
                for side in all_sides:
                    if isinstance(side, ast_mod.Call) and isinstance(side.func, ast_mod.Attribute):
                        if side.func.attr == 'col' and side.args:
                            arg = side.args[0]
                            if isinstance(arg, ast_mod.Constant) and isinstance(arg.value, str):
                                col_names_in_compare.append(arg.value.rsplit('.', 1)[-1])
                for i in range(len(col_names_in_compare)):
                    for j in range(i + 1, len(col_names_in_compare)):
                        col_equality_pairs.append((col_names_in_compare[i], col_names_in_compare[j]))
    
    # Unified convergence: propagate types through all relationships until stable
    changed = True
    while changed:
        changed = False
        # Alias pairs (bidirectional)
        for src_col, alias_name in alias_pairs:
            if alias_name in string_cols and src_col not in string_cols:
                string_cols.add(src_col); changed = True
            elif src_col in string_cols and alias_name not in string_cols:
                string_cols.add(alias_name); changed = True
            elif alias_name in numeric_cols and src_col not in numeric_cols:
                numeric_cols.add(src_col); changed = True
            elif src_col in numeric_cols and alias_name not in numeric_cols:
                numeric_cols.add(alias_name); changed = True
        # Rename mappings (bidirectional)
        for new_name, old_name in rename_map.items():
            if old_name in string_cols and new_name not in string_cols:
                string_cols.add(new_name); changed = True
            elif new_name in string_cols and old_name not in string_cols:
                string_cols.add(old_name); changed = True
            elif old_name in numeric_cols and new_name not in numeric_cols:
                numeric_cols.add(new_name); changed = True
            elif new_name in numeric_cols and old_name not in numeric_cols:
                numeric_cols.add(old_name); changed = True
        # Equality pairs (bidirectional)
        for col_a, col_b in col_equality_pairs:
            if col_a in string_cols and col_b not in string_cols:
                string_cols.add(col_b); changed = True
            elif col_b in string_cols and col_a not in string_cols:
                string_cols.add(col_a); changed = True
            elif col_a in numeric_cols and col_b not in numeric_cols:
                numeric_cols.add(col_b); changed = True
            elif col_b in numeric_cols and col_a not in numeric_cols:
                numeric_cols.add(col_a); changed = True
    
    if not string_cols and not numeric_cols:
        return
    
    type_map: dict[str, tuple[str, str]] = {}
    for c in string_cols:
        type_map[c] = ("STRING", "L_TEXT")
    for c in numeric_cols:
        if c not in type_map:
            type_map[c] = ("NUMERIC", "L_DECIMAL")
    
    from warp_core.ir.pyspark_models import InferenceSource, InferenceConfidence
    
    for data_in in asg.data_in:
        for col in (data_in.inferred_columns or []):
            if (col.inferred_type or "UNKNOWN") == "UNKNOWN" and col.name in type_map:
                exact, logical = type_map[col.name]
                col.inferred_type = logical
                col.source = InferenceSource.FILTER_CONDITION
                col.confidence = InferenceConfidence.MEDIUM
    
    # Also collect types already known in transformations (e.g. from SchemaPropagator)
    for tx in asg.transformations:
        for col in (tx.inferred_output or []):
            t = col.inferred_type or "UNKNOWN"
            if t != "UNKNOWN" and col.name not in type_map:
                if t.startswith("L_"):
                    logical = t
                elif t == "NUMERIC":
                    logical = "L_DECIMAL"
                elif t in ("STRING", "TEXT"):
                    logical = "L_TEXT"
                else:
                    logical = t
                type_map[col.name] = (t, logical)
    
    for tx in asg.transformations:
        for col in (tx.inferred_output or []):
            if (col.inferred_type or "UNKNOWN") == "UNKNOWN" and col.name in type_map:
                col.inferred_type = type_map[col.name][1]
                col.source = InferenceSource.FILTER_CONDITION
                col.confidence = InferenceConfidence.MEDIUM
        for col in (tx.inferred_input or []):
            if (col.inferred_type or "UNKNOWN") == "UNKNOWN" and col.name in type_map:
                col.inferred_type = type_map[col.name][1]



if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m src.parser.spark_ast <file.py> [--asg]")
        print("\nExamples:")
        print(
            "  python -m src.parser.spark_ast path/to/workload.py        # Count operations"
        )
        print("  python -m src.parser.spark_ast path/to/workload.py --asg  # Full ASG")
        sys.exit(1)

    file_path = sys.argv[1]
    use_asg = "--asg" in sys.argv

    print(f"\n{'='*60}")
    print(f"Analyzing: {file_path}")
    print(f"{'='*60}\n")

    try:
        if use_asg:
            # Full ASG parsing
            asg = parse_spark_file(file_path)

            print(f"App Name: {asg.extraction_metadata.app_name or 'N/A'}")
            print(f"Data In: {len(asg.data_in)}")
            print(f"Data Out: {len(asg.data_out)}")
            print(f"Transformations: {len(asg.transformations)}")
            print(f"Source Files: {len(asg.source_files)}")
            print(f"Imports: {len(asg.get_all_imports())}")

            print(f"\n{'='*60}")
            print("DATA_IN:")
            print("-" * 30)
            for src in asg.data_in:
                loc = src.location.start_line if src.location else "?"
                print(f"  [{src.id}] {src.type}: {src.name or src.path} (line {loc})")

            print(f"\n{'='*60}")
            print("DATA_OUT:")
            print("-" * 30)
            for out in asg.data_out:
                loc = out.location.start_line if out.location else "?"
                print(f"  [{out.id}] {out.type}: {out.name or out.path} (line {loc})")

            print(f"\n{'='*60}")
            print("TRANSFORMATIONS:")
            print("-" * 30)
            for tx in asg.transformations:
                params_str = (
                    ", ".join(f"{k}={v}" for k, v in tx.parameters.items()) if tx.parameters else ""
                )
                loc = tx.location.start_line if tx.location else "?"
                print(f"  [{tx.id}] {tx.operation} (line {loc}) {params_str[:50]}")

            print("\n✅ ASG parsed successfully!")

            # Optionally save to file
            if "--json" in sys.argv:
                output_path = file_path.replace(".py", "_asg_pyspark.json")
                Path(output_path).write_text(asg.model_dump_json(indent=2, exclude_none=True))
                print(f"\n📄 Saved to: {output_path}")

        else:
            # Quick Win: Count operations
            result = analyze_file(file_path)

            print(f"Total DataFrame operations: {result['total_operations']}\n")

            print("Operations by type:")
            print("-" * 30)
            for op, count in sorted(result["operations"].items(), key=lambda x: -x[1]):
                print(f"  {op:20} : {count}")

            if result["other_operations"]:
                print(f"\nOther operations: {result['other_operations']}")

            print(f"\n{'='*60}")
            print("Calls by line number:")
            print("-" * 30)
            for line_no, method in result["calls_by_line"]:
                print(f"  Line {line_no:4}: {method}")

            print("\n✅ Quick Win validated: ast module successfully identified all operations!")

    except FileNotFoundError:
        print(f"❌ Error: File not found: {file_path}")
        sys.exit(1)
    except SyntaxError as e:
        print(f"❌ Error: Invalid Python syntax: {e}")
        sys.exit(1)
