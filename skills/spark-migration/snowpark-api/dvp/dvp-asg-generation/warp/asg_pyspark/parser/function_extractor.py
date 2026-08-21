"""
Function Extractor Mixin - Extract and analyze function definitions.

This mixin provides methods for extracting function arguments, return types,
UDF detection, and type inference from Python AST.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from warp_core.pandas_functions import DATAFRAME_METHODS as PANDAS_DF_METHODS
from warp_core.spark_functions import ALL_DATAFRAME_METHODS, SPARK_SESSION_METHODS
from warp_core.symbol_table import FunctionSignature, SymbolTable

if TYPE_CHECKING:
    from typing import Any

# Combined PySpark + pandas DataFrame methods for duck-typing.
# If a variable calls any of these, it's a DataFrame (PySpark or pandas).
_DF_DUCK_TYPING_METHODS: frozenset[str] = ALL_DATAFRAME_METHODS | PANDAS_DF_METHODS


class FunctionExtractorMixin:
    """
    Mixin for extracting function definitions and their metadata.

    Provides:
    - visit_FunctionDef: Extract function arguments and returns
    - _extract_function_arguments: Parse function parameters
    - _extract_function_return: Analyze return statements
    - _detect_udf_registrations: Find UDF registrations
    - Type inference methods
    """

    # Attributes expected from the main parser class
    functions: list[dict[str, Any]]
    symbol_table: Any
    _current_function: str | None
    _current_class: str | None
    _udf_registry: dict[str, str]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """
        Track class definitions for scope context.
        """
        # Enter class scope
        old_class = self._current_class
        if self._current_class:
            # Nested class
            self._current_class = f"{self._current_class}.{node.name}"
        else:
            self._current_class = node.name

        self.generic_visit(node)

        # Exit class scope
        self._current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """
        Track function definitions with arguments and return info.

        Extracts:
        - Argument names, type hints, and default values
        - Return statements and their variable names
        """
        # Extract arguments
        arguments = self._extract_function_arguments(node)

        # Find return statement (look for the last return in the function)
        returns = self._extract_function_return(node)

        self.functions.append(
            {
                "name": node.name,
                "line_start": node.lineno,
                "line_end": node.end_lineno,
                "containing_class": self._current_class,
                "arguments": arguments,
                "returns": returns,
            }
        )
        
        # Register in global symbol table for cross-file resolution
        SymbolTable.register_function(FunctionSignature(
            name=node.name,
            file=getattr(self, '_current_filepath', '<unknown>'),
            containing_class=self._current_class,
            returns_type=returns.get("ref_type", "unknown") if returns else "unknown",
            returns_id=returns.get("ref_id") if returns else None,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
        ))

        # Enter new scope
        old_function = self._current_function
        self._current_function = node.name
        self.symbol_table.enter_scope(node.name)

        # Register function parameters in symbol table
        for arg in arguments:
            # Mark parameters as potential DataFrames for lineage
            self.symbol_table.set(arg["name"], f"param_{arg['name']}")

        self.generic_visit(node)

        # Exit scope
        self.symbol_table.exit_scope()
        self._current_function = old_function

    def _extract_function_arguments(self, node: ast.FunctionDef) -> list[dict]:
        """Extract function arguments with type hints and defaults."""
        arguments = []

        # Get default values (they align from the right)
        defaults = node.args.defaults
        num_defaults = len(defaults)
        num_args = len(node.args.args)

        # Build set of argument names for usage analysis
        arg_names = {arg.arg for arg in node.args.args if arg.arg != "self"}

        # Analyze how arguments are used in the function body
        arg_usage_types = self._infer_types_by_usage(node, arg_names)

        for i, arg in enumerate(node.args.args):
            # Skip 'self' for methods
            if arg.arg == "self":
                continue

            # Extract type hint if available
            inferred_type = "Unknown"
            if arg.annotation:
                inferred_type = self._annotation_to_string(arg.annotation)

            # If no type hint, try inference by usage
            if inferred_type == "Unknown":
                inferred_type = arg_usage_types.get(arg.arg, "Unknown")

            # If still unknown, try inference by name convention
            if inferred_type == "Unknown":
                inferred_type = self._infer_type_by_name(arg.arg)

            # Check if this argument has a default value
            default_index = i - (num_args - num_defaults)
            is_optional = default_index >= 0

            arguments.append(
                {
                    "name": arg.arg,
                    "inferred_type": inferred_type,
                    "inferred_schema_origin": None,  # Filled in resolution phase
                    "is_optional": is_optional,
                }
            )

        return arguments

    def _infer_types_by_usage(
        self, func_node: ast.FunctionDef, arg_names: set[str]
    ) -> dict[str, str]:
        """
        Infer argument types by analyzing how they're used in the function body.

        "If it walks like a DataFrame and quacks like a DataFrame, it's a DataFrame."
        """
        usage_types: dict[str, str] = {}

        for node in ast.walk(func_node):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                method_name = node.func.attr
                obj = node.func.value
                if isinstance(obj, ast.Name) and obj.id in arg_names:
                    var_name = obj.id
                    if method_name in _DF_DUCK_TYPING_METHODS:
                        usage_types[var_name] = "pyspark.sql.DataFrame"
                    elif method_name in SPARK_SESSION_METHODS:
                        usage_types[var_name] = "pyspark.sql.SparkSession"

        return usage_types

    def _infer_type_by_name(self, name: str) -> str:
        """
        Infer type based on common naming conventions.

        Examples:
        - spark, session → SparkSession
        - df, df_*, *_df → DataFrame
        """
        name_lower = name.lower()

        # SparkSession patterns
        if name_lower in ("spark", "session", "spark_session"):
            return "pyspark.sql.SparkSession"

        # DataFrame patterns
        if name_lower == "df" or name_lower.startswith("df_") or name_lower.endswith("_df"):
            return "pyspark.sql.DataFrame"

        return "Unknown"

    def _extract_function_return(self, node: ast.FunctionDef) -> dict | None:
        """
        Extract return statement info from a function.

        Uses ref_type/ref_id structure:
        - transformation: Points to last tx_xxx node from Spark chain
        - variable: Points to a named variable
        - multiple: Function has multiple return paths
        - literal: Returns a constant value
        - void: No return statement

        Note: ref_type="transformation" is set later by visit_Return when
        it processes a Spark chain. This method handles the initial analysis.
        """
        return_type = "Unknown"
        return_exprs: list[ast.expr] = []  # Collect all return expressions
        has_return = False

        # Check return type annotation first (highest priority)
        if node.returns:
            return_type = self._annotation_to_string(node.returns)

        # Collect ALL return statements (recursive through try/except, if/else, etc.)
        for child in ast.walk(node):
            # Skip nested function definitions
            if isinstance(child, ast.FunctionDef) and child is not node:
                continue

            if isinstance(child, ast.Return):
                has_return = True
                if child.value:
                    return_exprs.append(child.value)

        # No return statement found - function implicitly returns None
        if not has_return:
            return {
                "ref_type": "void",
                "ref_id": None,
                "inferred_type": "None",
            }

        # Analyze collected return expressions
        if return_exprs:
            # Check if all returns are the same type
            inferred_types = [self._infer_type_from_return_expr(expr) for expr in return_exprs]
            unique_types = set(t for t in inferred_types if t != "Unknown")

            # Determine ref_type and ref_id
            if len(return_exprs) == 1:
                ref_type, ref_id = self._get_return_ref(return_exprs[0])
            else:
                ref_type = "multiple"
                ref_id = None

            # Determine unified type
            if return_type == "Unknown":
                if len(unique_types) == 1:
                    return_type = unique_types.pop()
                elif len(unique_types) > 1:
                    # Multiple types - prefer non-None types
                    non_none = [t for t in unique_types if t != "None"]
                    if non_none:
                        return_type = non_none[0]
                    else:
                        return_type = "None"
                elif inferred_types:
                    # Try to infer from expressions if types are still Unknown
                    for expr in return_exprs:
                        inferred = self._infer_type_from_expression(expr)
                        if inferred != "Unknown":
                            return_type = inferred
                            break
        else:
            # Empty returns only (return without value)
            ref_type = "void"
            ref_id = None
            if return_type == "Unknown":
                return_type = "None"

        # Final fallback: try variable-based inference
        if return_type == "Unknown" and ref_type == "variable" and ref_id:
            return_type = self._infer_return_type(node, ref_id)

        return {
            "ref_type": ref_type,
            "ref_id": ref_id,
            "inferred_type": return_type,
        }


    def _check_is_read_chain(self, node: ast.Call) -> bool:
        """Check if this call is part of a spark.read chain (inline version)."""
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

    def _get_return_ref(self, expr: ast.expr) -> tuple[str, str | None]:
        """
        Determine ref_type and ref_id from a return expression.

        Returns (ref_type, ref_id) tuple.
        """
        if isinstance(expr, ast.Name):
            # return df -> variable reference
            return ("variable", expr.id)
        elif isinstance(expr, ast.Constant):
            # return True, return 42 -> literal
            value = expr.value
            if isinstance(value, bool):
                return ("literal", "boolean")
            elif isinstance(value, int):
                return ("literal", "int")
            elif isinstance(value, float):
                return ("literal", "float")
            elif isinstance(value, str):
                return ("literal", "string")
            elif value is None:
                return ("void", None)
            return ("literal", type(value).__name__)
        elif isinstance(expr, ast.Call):
            # return df.filter().withColumn() or return spark.read.load()
            # Check if this is a spark.read chain - if so, mark as data source
            if self._check_is_read_chain(expr):
                # The actual ID will be assigned when visit_Call processes this
                # For now, mark as "data_source" so we know it returns a DataFrame
                return ("data_source", None)
            # Otherwise mark as expression; visit_Return will upgrade if chain unrolls
            return ("expression", None)
        elif isinstance(expr, ast.Tuple):
            # return (df, something) -> expression
            return ("variable", "<tuple>")
        else:
            return ("variable", "<expression>")

    def _infer_type_from_return_expr(self, expr: ast.expr) -> str:
        """Infer type from a return expression (constants, names, calls)."""
        # Handle constants: return True, return False, return 42, return "string"
        if isinstance(expr, ast.Constant):
            value = expr.value
            if isinstance(value, bool):
                return "boolean"
            elif isinstance(value, int):
                return "int"
            elif isinstance(value, float):
                return "float"
            elif isinstance(value, str):
                return "string"
            elif value is None:
                return "None"
            return "Unknown"

        # For other expressions, delegate to existing inference
        return self._infer_type_from_expression(expr)

    def _infer_type_from_expression(self, expr: ast.expr) -> str:
        """Infer type from an inline expression like: return df.withColumn(...)"""
        current = expr
        while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
            method_name = current.func.attr
            if method_name in _DF_DUCK_TYPING_METHODS:
                return "pyspark.sql.DataFrame"
            current = current.func.value

        return "Unknown"

    def _infer_return_type(self, func_node: ast.FunctionDef, return_var: str) -> str:
        """
        Infer the return type by tracing the return variable back to its origin.

        Strategy:
        1. Find the last assignment to return_var
        2. Check if it's the result of a DataFrame operation
        3. Check if it's a call to a known function that returns DataFrame
        4. If so, infer DataFrame type
        """
        known_functions = {f["name"] for f in self.functions}

        last_assignment = None
        for node in ast.walk(func_node):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == return_var:
                        last_assignment = node.value

        if last_assignment is None:
            return "Unknown"

        if isinstance(last_assignment, ast.Call):
            if isinstance(last_assignment.func, ast.Attribute):
                if last_assignment.func.attr in _DF_DUCK_TYPING_METHODS:
                    return "pyspark.sql.DataFrame"
            elif isinstance(last_assignment.func, ast.Name):
                if last_assignment.func.id in known_functions:
                    return "pyspark.sql.DataFrame"

        current = last_assignment
        while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
            if current.func.attr in _DF_DUCK_TYPING_METHODS:
                return "pyspark.sql.DataFrame"
            current = current.func.value

        return "Unknown"

    def _annotation_to_string(self, annotation: ast.expr) -> str:
        """Convert a type annotation AST node to a string."""
        if isinstance(annotation, ast.Name):
            return annotation.id
        elif isinstance(annotation, ast.Constant):
            return str(annotation.value)
        elif isinstance(annotation, ast.Attribute):
            # Handle things like pyspark.sql.DataFrame
            parts = []
            node = annotation
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
            return ".".join(reversed(parts))
        elif isinstance(annotation, ast.Subscript):
            # Handle things like Optional[DataFrame]
            if isinstance(annotation.value, ast.Name):
                return annotation.value.id
        return "Unknown"

    def _detect_udf_registrations(self, tree: ast.Module) -> None:
        """
        Detect UDF registrations: var = udf(function_name, ReturnType())

        Populates self._udf_registry with function_name -> return_type mappings.
        """
        # Mapping from Spark type classes to simple type names
        spark_type_map = {
            "StringType": "string",
            "IntegerType": "int",
            "LongType": "long",
            "DoubleType": "double",
            "FloatType": "float",
            "BooleanType": "boolean",
            "DateType": "date",
            "TimestampType": "timestamp",
            "BinaryType": "binary",
            "ArrayType": "array",
            "MapType": "map",
            "StructType": "struct",
            "DecimalType": "decimal",
            "StructField": "field",
            "ShortType": "short",
            "ByteType": "byte",
        }

        for node in ast.walk(tree):
            # Look for: var = udf(func_name, ReturnType())
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call = node.value
                func_name = None

                # Check if it's a call to 'udf'
                if isinstance(call.func, ast.Name) and call.func.id == "udf":
                    pass  # Direct udf() call
                elif isinstance(call.func, ast.Attribute) and call.func.attr == "udf":
                    pass  # F.udf() call
                else:
                    continue

                # Extract function name (first argument)
                if call.args and isinstance(call.args[0], ast.Name):
                    func_name = call.args[0].id

                # Extract return type (second argument)
                return_type = "Unknown"
                if len(call.args) >= 2:
                    type_arg = call.args[1]
                    # Handle: DoubleType()
                    if isinstance(type_arg, ast.Call):
                        if isinstance(type_arg.func, ast.Name):
                            type_class = type_arg.func.id
                            return_type = spark_type_map.get(type_class, type_class)
                        elif isinstance(type_arg.func, ast.Attribute):
                            type_class = type_arg.func.attr
                            return_type = spark_type_map.get(type_class, type_class)
                    # Handle: DoubleType (without parentheses, though rare)
                    elif isinstance(type_arg, ast.Name):
                        return_type = spark_type_map.get(type_arg.id, type_arg.id)

                if func_name:
                    self._udf_registry[func_name] = return_type

    def _apply_udf_types(self) -> None:
        """
        Apply UDF type information to function definitions.

        For functions registered as UDFs:
        - Mark arguments as scalar types (not DataFrames)
        - Set return type from the UDF registration
        """
        for func in self.functions:
            func_name = func["name"]
            if func_name in self._udf_registry:
                udf_return_type = self._udf_registry[func_name]

                # Update return type
                if func.get("returns") and func["returns"]["inferred_type"] == "Unknown":
                    func["returns"]["inferred_type"] = udf_return_type

                # Mark arguments as scalars (UDFs receive column values, not DataFrames)
                for arg in func.get("arguments", []):
                    if arg["inferred_type"] == "Unknown":
                        # UDF arguments are scalar values, mark as "scalar"
                        # We could be more specific by analyzing the withColumn call
                        arg["inferred_type"] = "scalar"

    def _resolve_function_argument_origins(self, tree: ast.Module) -> None:
        """
        Resolution phase: Link function arguments to their data sources.

        This method analyzes function calls in the main scope to determine
        what variables are passed as arguments, then maps them to data_in
        entries to fill inferred_schema_origin.

        Algorithm:
        1. Build a map: variable_name -> data_in source (from data sources)
        2. Find all function calls in the main scope
        3. Match passed variables to function arguments
        4. Update inferred_schema_origin in the function definitions
        """
        # Step 1: Build variable -> data source mapping
        var_to_source: dict[str, str] = {}

        # Get variables from the global scope
        st_dump = self.symbol_table.dump()
        global_vars = {}
        for scope in st_dump.get("scopes", []):
            if scope.get("name") == "<global>":
                global_vars = scope.get("variables", {})
                break

        for source in self.data_in:
            source_id = source.get("id")
            source_name = source.get("name") or source.get("path", "")
            # Find what variable holds this source
            for var_name, node_id in global_vars.items():
                if node_id == source_id:
                    # Format: "data_in.{source_name}"
                    var_to_source[var_name] = f"data_in.{source_name}"

        # Step 2: Find function calls in the GLOBAL scope only
        # We only analyze top-level statements (not inside function definitions)
        # Build a map: function_name -> {arg_position: variable_passed}
        func_calls: dict[str, dict[int, str]] = {}
        func_names = [f["name"] for f in self.functions]

        def analyze_calls_in_scope(nodes: list[ast.stmt]) -> None:
            """Recursively analyze calls in top-level statements (not inside function defs)."""
            for node in nodes:
                # Skip function definitions - we only want global scope calls
                if isinstance(node, ast.FunctionDef):
                    continue

                # For if/for/while/with blocks, recurse into their body
                if isinstance(node, (ast.If, ast.For, ast.While, ast.With)):
                    if hasattr(node, "body"):
                        analyze_calls_in_scope(node.body)
                    if hasattr(node, "orelse"):
                        analyze_calls_in_scope(node.orelse)
                    continue

                # Look for calls in this statement
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        func_name = None
                        if isinstance(child.func, ast.Name):
                            func_name = child.func.id
                        elif isinstance(child.func, ast.Attribute):
                            func_name = child.func.attr

                        if func_name and func_name in func_names:
                            arg_map = {}
                            for i, arg in enumerate(child.args):
                                if isinstance(arg, ast.Name):
                                    arg_map[i] = arg.id

                            if func_name not in func_calls:
                                func_calls[func_name] = arg_map
                            else:
                                func_calls[func_name].update(arg_map)

        # Analyze only top-level statements
        analyze_calls_in_scope(tree.body)

        # Step 3: Update function arguments with inferred_schema_origin and type
        for func in self.functions:
            func_name = func["name"]
            if func_name in func_calls:
                call_args = func_calls[func_name]
                for i, arg in enumerate(func.get("arguments", [])):
                    if i in call_args:
                        passed_var = call_args[i]
                        if passed_var in var_to_source:
                            arg["inferred_schema_origin"] = var_to_source[passed_var]
                            # If it comes from data_in, it's a DataFrame
                            if arg["inferred_type"] == "Unknown":
                                arg["inferred_type"] = "pyspark.sql.DataFrame"
