"""
Origin Resolver - Propagate data origins through execution_calls.

This module performs "late binding" to resolve inferred_schema_origin
for function arguments by tracing the call graph.

The problem:
    When parsing `run_pipeline(df_sales, df_products)`, we don't know
    where df_sales comes from at definition time. The origin is only
    known at the call site: `df_sales = _read_table(spark, "sales_data")`.

The solution:
    1. Find calls to data-reading functions (return DataFrames from sources)
    2. Extract the data name from literal arguments
    3. Set resolved_origin on OutputBinding
    4. Propagate origins through the call graph (call_output references)
    5. Update FunctionDefinition.arguments with resolved origins
    6. Build execution_instances to trace the complete flow
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from warp_core.symbol_table import SymbolTable

from warp_core.ir.pyspark_models import (
        ASG,
        DataSource,
        ExecutionCall,
        FunctionDefinition,
        TransformationNode,
    )

from warp_core.symbol_table import SymbolTable

from warp_core.ir.pyspark_models import (
    BindingAction,
    DataSource,
    ExecutionBinding,
    ExecutionInstance,
    InferredColumnRef,
    InputMapping,
    ResolveDetails,
)


class OriginResolver:
    """
    Resolves data origins by propagating through execution_calls.

    Performs late binding: after all execution_calls are captured,
    trace the call graph to determine where DataFrames originate.
    """

    def __init__(self, asg: "ASG") -> None:
        self._asg = asg

        # Build lookup tables
        self._calls_by_id: dict[str, "ExecutionCall"] = {
            call.call_id: call for call in asg.execution_calls
        }
        self._calls_by_output_var: dict[str, "ExecutionCall"] = {}
        for call in asg.execution_calls:
            if call.bindings.output:
                var_name = call.bindings.output.variable_name
                # Store by (caller_function, variable_name) to handle scoping
                key = f"{call.caller.function}:{var_name}"
                self._calls_by_output_var[key] = call

        self._functions_by_name: dict[str, "FunctionDefinition"] = {
            f.name: f for f in asg.functions
        }

        # Track data_in names for origin resolution
        self._data_in_names: dict[str, str] = {
            src.id: src.name or src.path or src.id
            for src in asg.data_in
        }

    def resolve(self) -> "ASG":
        """
        Resolve origins and update the ASG.

        Returns:
            Updated ASG with resolved origins
        """
        # Phase 1: Identify calls that produce data origins
        self._resolve_data_producing_calls()

        # Phase 2: Propagate origins through call_output references
        self._propagate_origins()

        # Phase 3: Update function argument origins
        self._update_function_arguments()

        # Phase 4: Create data_in entries from resolved origins
        # Must happen before execution_instances to have correct IDs
        self._create_data_in_from_origins()

        # Phase 5: Build variable-to-data_in mapping for column propagation
        self._build_variable_to_data_in_map()

        # Phase 6: Build execution_instances trace (after data_in creation)
        self._build_execution_instances()

        # Phase 7: Propagate required columns back to data_in
        self._propagate_columns_to_data_in()

        # Phase 8: Clean up placeholder data_in entries
        self._cleanup_placeholder_data_in()

        return self._asg

    def _resolve_data_producing_calls(self) -> None:
        """
        Find calls that produce DataFrames from data sources.

        These are calls to functions like:
        - _read_table(spark, "table_name")
        - Functions that wrap spark.read.table(), spark.read.csv(), etc.
        """
        for call in self._asg.execution_calls:
            func_name = call.callee.function

            # Check if this is a known data-reading function
            origin = self._detect_data_origin(call)
            if origin and call.bindings.output:
                call.bindings.output.resolved_origin = origin

    def _detect_data_origin(self, call: "ExecutionCall") -> str | None:
        """
        Detect if a call produces a data origin.

        Checks for patterns like:
        - _read_table(spark, "sales_data") -> "data_in.sales_data"
        - Functions that return data_in nodes

        Returns:
            Data origin string (e.g., "data_in.sales_data") or None
        """
        func_name = call.callee.function

        # Pattern 1: Function name suggests data reading
        DATA_READER_PATTERNS = {
            "_read_table", "read_table", "load_table",
            "_read_csv", "read_csv", "load_csv",
            "_read_parquet", "read_parquet", "load_parquet",
            "_read_json", "read_json", "load_json",
            "_load_data", "load_data", "get_data",
        }

        if func_name in DATA_READER_PATTERNS:
            # Look for a literal string argument that names the data
            # This could be table name, file path, etc.
            data_name = self._extract_data_name_from_call(call)
            if data_name:
                return f"data_in.{data_name}"

        # Pattern 2: Function returns a data_in node directly
        func_def = self._functions_by_name.get(func_name)
        if func_def and func_def.returns:
            ref_id = func_def.returns.ref_id
            if ref_id and ref_id.startswith("in_"):
                # Get the name of the data_in
                data_name = self._data_in_names.get(ref_id, ref_id)
                return f"data_in.{data_name}"

        # Pattern 3: Check if the function contains data_in nodes
        # (function body reads from a source)
        if func_def:
            origin = self._check_function_body_for_data_in(func_def)
            if origin:
                return origin

        return None

    def _extract_data_name_from_call(self, call: "ExecutionCall") -> str | None:
        """
        Extract the data name from a call's arguments.

        For `_read_table(spark, "sales_data")`, extracts "sales_data".
        Looks for literal string arguments that could be table/file names.
        """
        # Look for literal bindings that contain data names
        for binding in call.bindings.inputs:
            # Check if this is a literal binding
            if binding.source_type.value == "literal":
                source_id = binding.source_id
                # Return the literal value as the data name
                return source_id

        # Fallback: look for any non-standard source_id
        for binding in call.bindings.inputs:
            source_id = binding.source_id
            # If source_id looks like a table/file name (not in_XXX, tx_XXX, etc.)
            if not source_id.startswith(("in_", "tx_", "call_", "param_")):
                # Could be a literal value passed through
                return source_id

        return None

    def _check_function_body_for_data_in(
        self, func_def: "FunctionDefinition"
    ) -> str | None:
        """
        Check if a function body contains data_in references.

        If the function reads from a single data source, return that origin.
        """
        # Find transformations within this function's scope
        func_file = func_def.source_file
        func_name = func_def.name

        for tx in self._asg.transformations:
            if tx.location and tx.location.scope == func_name:
                # Check if this transformation's input is a data_in
                for input_id in tx.inputs:
                    if input_id.startswith("in_"):
                        data_name = self._data_in_names.get(input_id, input_id)
                        return f"data_in.{data_name}"

        return None

    def _propagate_origins(self) -> None:
        """
        Propagate origins through call_output references.

        When a call's input has source_type=call_output, inherit the
        origin from that call's output.
        """
        # Multiple passes to handle chained calls
        max_iterations = 10
        for _ in range(max_iterations):
            changes = 0

            for call in self._asg.execution_calls:
                for binding in call.bindings.inputs:
                    if binding.inferred_origin:
                        continue  # Already resolved

                    # Check source type
                    if binding.source_type.value == "call_output":
                        # source_id is like "call_001" or "call_001.output"
                        source_call_id = binding.source_id.split(".")[0]
                        source_call = self._calls_by_id.get(source_call_id)

                        if source_call and source_call.bindings.output:
                            origin = source_call.bindings.output.resolved_origin
                            if origin:
                                binding.inferred_origin = origin
                                changes += 1

                    elif binding.source_type.value == "data_in":
                        # Direct data_in reference
                        data_name = self._data_in_names.get(
                            binding.source_id, binding.source_id
                        )
                        binding.inferred_origin = f"data_in.{data_name}"
                        changes += 1

                    elif binding.source_type.value == "variable":
                        # Try to resolve variable to a call output
                        origin = self._resolve_variable_origin(
                            binding.source_id, call.caller.function
                        )
                        if origin:
                            binding.inferred_origin = origin
                            changes += 1

            if changes == 0:
                break

    def _resolve_variable_origin(
        self, var_name: str, caller_function: str
    ) -> str | None:
        """
        Resolve a variable to its data origin.

        Looks for a call that produced this variable in the same scope.
        """
        # Check for call that output to this variable in caller's scope
        key = f"{caller_function}:{var_name}"
        source_call = self._calls_by_output_var.get(key)

        if source_call and source_call.bindings.output:
            return source_call.bindings.output.resolved_origin

        # Check __main__ scope if not found
        if caller_function != "__main__":
            key = f"__main__:{var_name}"
            source_call = self._calls_by_output_var.get(key)
            if source_call and source_call.bindings.output:
                return source_call.bindings.output.resolved_origin

        return None

    def _update_function_arguments(self) -> None:
        """
        Update FunctionDefinition.arguments with resolved origins.

        For each function, find execution_calls that invoke it and
        propagate the inferred_origin to the argument definitions.
        """
        for call in self._asg.execution_calls:
            func_name = call.callee.function
            func_def = self._functions_by_name.get(func_name)

            if not func_def:
                continue

            for binding in call.bindings.inputs:
                if not binding.inferred_origin:
                    continue

                # Find the corresponding argument in the function definition
                for arg in func_def.arguments:
                    if arg.name == binding.arg_name:
                        # Update if not already set or if this is more specific
                        if not arg.inferred_schema_origin:
                            arg.inferred_schema_origin = binding.inferred_origin

    def _build_execution_instances(self) -> None:
        """
        Build execution_instances to trace value resolution and flow.

        Creates instances starting from entry points (__main__) and traces:
        - RESOLVE_INBOUND: when data is loaded from a source (literal names)
        - EXECUTE_FLOW: when functions are called with data parameters
        """
        # Find entry points: typically __main__ or functions with calls but no callers
        entry_points = self._find_entry_points()

        instance_counter = 1
        for entry_point in entry_points:
            bindings = self._trace_execution_flow(entry_point)

            if bindings:
                instance = ExecutionInstance(
                    instance_id=f"exec_{instance_counter:03d}",
                    entry_point=entry_point,
                    bindings=bindings,
                )
                self._asg.execution_instances.append(instance)
                instance_counter += 1

    def _find_entry_points(self) -> list[str]:
        """Find entry point functions (callers that are never called)."""
        callers = {call.caller.function for call in self._asg.execution_calls}
        callees = {call.callee.function for call in self._asg.execution_calls}

        # Entry points are callers that are not callees
        entry_points = callers - callees

        # Ensure __main__ is first if present
        result = []
        if "__main__" in entry_points:
            result.append("__main__")
            entry_points.discard("__main__")

        result.extend(sorted(entry_points))
        return result

    def _trace_execution_flow(
        self, entry_point: str, visited: set[str] | None = None
    ) -> list[ExecutionBinding]:
        """
        Trace execution flow from an entry point recursively.

        Captures RESOLVE_INBOUND for data-reading calls and
        EXECUTE_FLOW for function calls with data parameters.
        Recursively traces into called functions.
        """
        if visited is None:
            visited = set()

        # Prevent infinite recursion
        if entry_point in visited:
            return []
        visited.add(entry_point)

        bindings: list[ExecutionBinding] = []

        # Get calls from this entry point, ordered by line number
        entry_calls = [
            call for call in self._asg.execution_calls
            if call.caller.function == entry_point
        ]
        entry_calls.sort(key=lambda c: c.caller.line or 0)

        for call in entry_calls:
            output = call.bindings.output
            resolved = output.resolved_origin if output else None

            # Check if this is a RESOLVE_INBOUND (data loading)
            if resolved and resolved.startswith("data_in."):
                data_name = resolved.replace("data_in.", "")
                var_name = output.variable_name if output else "?"

                # Find the data_in ID from the name
                source_id = self._find_data_in_id(data_name)

                resolve_details = ResolveDetails(
                    source_id=source_id,
                    resolved_name=data_name,
                    output_variable=var_name,
                )
                bindings.append(ExecutionBinding(
                    call_id=call.call_id,
                    action=BindingAction.RESOLVE_INBOUND,
                    details=resolve_details,
                ))

            # Check if this is EXECUTE_FLOW (function with data parameters)
            data_inputs = [
                b for b in call.bindings.inputs
                if b.inferred_origin and b.inferred_origin.startswith("data_in.")
            ]

            if data_inputs:
                input_map = []
                for b in data_inputs:
                    origin = b.inferred_origin
                    if origin:
                        # Map from origin to find data_in ID
                        data_name = origin.replace("data_in.", "")
                        source_id = self._find_data_in_id(data_name)
                        input_map.append(InputMapping(
                            parameter=b.arg_name,
                            origin_node=source_id,
                        ))

                if input_map:
                    bindings.append(ExecutionBinding(
                        call_id=call.call_id,
                        action=BindingAction.EXECUTE_FLOW,
                        context=call.callee.function,
                        input_map=input_map,
                    ))

            # Recursively trace into the called function
            callee_bindings = self._trace_execution_flow(call.callee.function, visited)
            bindings.extend(callee_bindings)

        return bindings

    def _extract_columns_from_expression(self, expr: str) -> list[str]:
        """Extract column names from a Spark expression.

        Uses Python ast as primary parser; regex as fallback.

        Handles:
        - Simple column names: "store_id" -> ["store_id"]
        - col() expressions: "col('a') == col('b')" -> ["a", "b"]
        - F.col() expressions: "F.col('x')" -> ["x"]
        """
        stripped = expr.strip("'\"")
        if stripped.isidentifier():
            return [stripped]

        try:
            import ast
            tree = ast.parse(expr, mode="eval")
            columns: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fname = None
                    if isinstance(node.func, ast.Name):
                        fname = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        fname = node.func.attr
                    if fname == "col" and node.args:
                        if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                            columns.append(node.args[0].value)
                elif isinstance(node, ast.Subscript):
                    if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                        columns.append(node.slice.value)
            if columns:
                return columns
        except (SyntaxError, ValueError):
            pass

        import re
        col_pattern = r"(?:F\.)?col\(['\"\']([^'\"\']+')\"\']\)"
        matches = re.findall(col_pattern, expr)
        return matches if matches else []


    def _find_data_in_id(self, data_name: str) -> str:
        """Find the data_in ID for a given data name."""
        # Try global SymbolTable first (has cross-file visibility)
        source_binding = SymbolTable.resolve_source(data_name)
        if source_binding:
            return source_binding.source_id
        
        # Fallback to local lookup
        for data_id, name in self._data_in_names.items():
            if name == data_name:
                return data_id
        
        # Fallback: return as-is with in_ prefix if looks like an ID
        if data_name.startswith("in_"):
            return data_name
        return f"in_{data_name}"

    def _create_data_in_from_origins(self) -> None:
        """
        Create data_in entries from resolved origins.

        When execution_calls identify data sources like "sales_data" that
        don't have corresponding data_in entries, create them.
        """
        existing_names = set(self._data_in_names.values())
        next_id = len(self._asg.data_in) + 1

        # Find all RESOLVE_INBOUND bindings in execution_instances
        for instance in self._asg.execution_instances:
            for binding in instance.bindings:
                if binding.action == BindingAction.RESOLVE_INBOUND and binding.details:
                    data_name = binding.details.resolved_name

                    # Skip if already exists
                    if data_name in existing_names:
                        continue

                    # Create new data_in entry
                    new_id = f"in_{next_id:03d}"
                    next_id += 1

                    new_data_in = DataSource(
                        id=new_id,
                        type="table",  # Default; could be inferred from call
                        name=data_name,
                    )
                    self._asg.data_in.append(new_data_in)

                    # Update lookup
                    self._data_in_names[new_id] = data_name
                    existing_names.add(data_name)

                    # Update the binding to use the new ID
                    binding.details.source_id = new_id

        # Also check execution_calls output bindings for resolved origins
        for call in self._asg.execution_calls:
            if call.bindings.output and call.bindings.output.resolved_origin:
                origin = call.bindings.output.resolved_origin
                if origin.startswith("data_in."):
                    data_name = origin.replace("data_in.", "")

                    if data_name in existing_names:
                        continue

                    # Create new data_in entry
                    new_id = f"in_{next_id:03d}"
                    next_id += 1

                    new_data_in = DataSource(
                        id=new_id,
                        type="table",
                        name=data_name,
                    )
                    self._asg.data_in.append(new_data_in)
                    self._data_in_names[new_id] = data_name
                    existing_names.add(data_name)

    def _build_variable_to_data_in_map(self) -> None:
        """
        Build a mapping from variable names to data_in IDs.

        This enables the column propagation to trace from transformation
        inputs (which are variable names) to data_in nodes.

        Maps:
        - Variable names (df_sales) -> data_in ID
        - Parameter references (param_df_sales) -> data_in ID
        """
        self._var_to_data_in: dict[str, str] = {}

        # From execution_calls: variable_name -> data_in
        for call in self._asg.execution_calls:
            if call.bindings.output and call.bindings.output.resolved_origin:
                var_name = call.bindings.output.variable_name
                origin = call.bindings.output.resolved_origin

                if origin.startswith("data_in."):
                    data_name = origin.replace("data_in.", "")
                    # Find the data_in ID for this name
                    data_in_id = self._find_data_in_id(data_name)
                    self._var_to_data_in[var_name] = data_in_id

            # Track inferred origins in input bindings
            # This maps both source variables AND function parameters
            for binding in call.bindings.inputs:
                if binding.inferred_origin and binding.inferred_origin.startswith("data_in."):
                    data_name = binding.inferred_origin.replace("data_in.", "")
                    data_in_id = self._find_data_in_id(data_name)

                    # Map the source variable (e.g., df_sales)
                    self._var_to_data_in[binding.source_id] = data_in_id

                    # Also map the parameter reference format (e.g., param_df_sales)
                    # This is how parameters appear as transformation inputs
                    param_ref = f"param_{binding.arg_name}"
                    self._var_to_data_in[param_ref] = data_in_id

                    # Also map the raw argument name for functions that use it directly
                    self._var_to_data_in[binding.arg_name] = data_in_id

    def _propagate_columns_to_data_in(self) -> None:
        """
        Trace column requirements from transformations back to data_in nodes.

        For each transformation, identify columns it needs and trace backward
        to find which data_in provides them. Records:
        - name: column name
        - source: why needed (join_requirement, filter_condition, select, etc.)
        - origin_node: which transformation requires it
        """
        # Build lookup: transformation ID -> TransformationNode
        tx_by_id = {tx.id: tx for tx in self._asg.transformations}

        # Build lookup: node ID -> list of downstream transformation IDs
        downstream: dict[str, list[str]] = {}
        for tx in self._asg.transformations:
            for input_id in tx.inputs:
                if input_id not in downstream:
                    downstream[input_id] = []
                downstream[input_id].append(tx.id)

        # Build data_in lookup
        data_in_by_id = {d.id: d for d in self._asg.data_in}

        # Track columns per data_in: {data_in_id: [(name, source, origin_node)]}
        data_in_columns: dict[str, list[tuple[str, str, str]]] = {
            d.id: [] for d in self._asg.data_in
        }

        # Process each transformation to find column requirements
        for tx in self._asg.transformations:
            columns_needed = self._extract_column_requirements(tx)

            # Trace each column back to data_in sources
            for col_name, source_type in columns_needed:
                # Find which data_in this column comes from
                data_in_ids = self._trace_column_to_data_in(
                    col_name, tx.inputs, tx_by_id, data_in_by_id, source_type
                )

                for data_in_id in data_in_ids:
                    data_in_columns[data_in_id].append((col_name, source_type, tx.id))

        # Update data_in nodes with required_columns
        for data_in in self._asg.data_in:
            seen_columns: set[str] = set()
            for col_name, source_type, origin_node in data_in_columns.get(data_in.id, []):
                # Avoid duplicates (same column from same transformation)
                key = f"{col_name}:{origin_node}"
                if key in seen_columns:
                    continue
                seen_columns.add(key)

                data_in.required_columns.append(InferredColumnRef(
                    name=col_name,
                    source=source_type,
                    origin_node=origin_node,
                ))

    def _extract_column_requirements(
        self, tx: "TransformationNode"
    ) -> list[tuple[str, str]]:
        """
        Extract columns required by a transformation and their purpose.

        Returns list of (column_name, source_type) tuples.
        """
        from warp_core.ir.pyspark_models import TransformationNode  # noqa: F811

        columns: list[tuple[str, str]] = []

        # Determine source type based on transformation operation
        tx_type = tx.operation.lower() if tx.operation else ""

        if "join" in tx_type:
            # Join: columns in inferred_input that are shared keys
            # Look for columns that appear in multiple from_inputs
            col_counts: dict[str, int] = {}
            for col in tx.inferred_input:
                if col.name in col_counts:
                    col_counts[col.name] += 1
                else:
                    col_counts[col.name] = 1

            for col in tx.inferred_input:
                if col_counts.get(col.name, 0) > 1:
                    columns.append((col.name, "join_requirement"))
                else:
                    columns.append((col.name, "select"))

            # Also extract join key from parameters if not in inferred_input
            join_condition = tx.parameters.get("join_condition")
            if join_condition and isinstance(join_condition, str):
                # Extract column names from join condition
                join_keys = self._extract_columns_from_expression(join_condition)
                for join_key in join_keys:
                    if join_key and join_key not in col_counts:
                        columns.append((join_key, "join_requirement"))

        elif "filter" in tx_type or "where" in tx_type:
            # Filter: all columns used in condition
            for col in tx.inferred_input:
                columns.append((col.name, "filter_condition"))

        elif "agg" in tx_type or "group" in tx_type:
            # Aggregation: group-by keys and aggregated columns
            for col in tx.inferred_input:
                # Heuristic: if column appears in output, it's likely a group key
                output_names = {c.name for c in tx.inferred_output}
                if col.name in output_names:
                    columns.append((col.name, "group_key"))
                else:
                    columns.append((col.name, "aggregation"))

        elif "select" in tx_type or "withcolumn" in tx_type:
            # Select or column manipulation
            for col in tx.inferred_input:
                columns.append((col.name, "select"))

        else:
            # Default: all input columns as "select"
            for col in tx.inferred_input:
                columns.append((col.name, "select"))

        return columns

    def _trace_column_to_data_in(
        self,
        col_name: str,
        input_ids: list[str],
        tx_by_id: dict[str, "TransformationNode"],
        data_in_by_id: dict[str, "DataSource"],
        source_type: str = "select",
    ) -> list[str]:
        """
        Trace a column backward through transformations to find source data_in.

        Args:
            col_name: Column name to trace
            input_ids: Starting input node IDs
            tx_by_id: Transformation lookup
            data_in_by_id: Data source lookup
            source_type: Type of column requirement (for special handling of join keys)

        Returns list of data_in IDs that could provide this column.
        """
        data_in_sources: list[str] = []

        # BFS/DFS to trace backward
        to_visit = list(input_ids)
        visited: set[str] = set()

        while to_visit:
            node_id = to_visit.pop()
            if node_id in visited:
                continue
            visited.add(node_id)

            # Check if this is a data_in
            if node_id in data_in_by_id:
                data_in_sources.append(node_id)
                continue

            # Check if this is a transformation
            if node_id in tx_by_id:
                tx = tx_by_id[node_id]
                output_names = {c.name for c in tx.inferred_output}

                # Check if this transformation CREATES the column (vs just passing it through)
                # withColumn, withColumn_custom, and similar operations CREATE new columns
                op = tx.operation.lower() if tx.operation else ""
                is_column_creator = op in ("withcolumn", "withcolumn_custom", "lit", "alias")
                
                # If this transformation creates the column, STOP tracing
                # The column doesn't exist in the source data
                if is_column_creator:
                    # Check if col_name is the newly created column
                    params = tx.parameters if hasattr(tx, 'parameters') else {}
                    created_col = params.get("column_name") if isinstance(params, dict) else None
                    if created_col == col_name:
                        # Column was CREATED here, not from source - don't trace further
                        continue

                # For join keys, trace ALL inputs (key must exist on both sides)
                if source_type == "join_requirement":
                    # Always trace all inputs for join keys
                    to_visit.extend(tx.inputs)
                elif col_name in output_names:
                    # Column flows through this transformation - trace its inputs
                    to_visit.extend(tx.inputs)
                continue

            # Check if this is a variable name that maps to a data_in
            if hasattr(self, "_var_to_data_in") and node_id in self._var_to_data_in:
                data_in_id = self._var_to_data_in[node_id]
                if data_in_id not in visited:
                    data_in_sources.append(data_in_id)
                continue

            # Try to resolve as param_xxx to find the actual source
            if node_id.startswith("param_"):
                # This is a function parameter - check if we have origin info
                param_name = node_id.replace("param_", "")
                if hasattr(self, "_var_to_data_in") and param_name in self._var_to_data_in:
                    data_in_id = self._var_to_data_in[param_name]
                    if data_in_id not in visited:
                        data_in_sources.append(data_in_id)

        return data_in_sources

    def _cleanup_placeholder_data_in(self) -> None:
        """
        Remove only truly empty placeholder data_in entries.

        We keep most data sources even without static info because:
        1. Dynamic sources (e.g., createDataFrame(results)) are valid
        2. Downstream transformations will reveal column usage
        3. Location info is valuable for code understanding
        
        Only remove: literal empty DataFrames like createDataFrame([], ...)
        """
        # For now, keep all data sources - the downstream analysis
        # will discover columns through transformation usage.
        # 
        # Future: could filter literal empty DataFrames by detecting
        # createDataFrame([], ...) pattern during parsing.
        pass


def resolve_origins(asg: "ASG") -> "ASG":
    """
    Convenience function to resolve data origins in an ASG.

    Args:
        asg: The ASG to process

    Returns:
        Updated ASG with resolved origins
    """
    resolver = OriginResolver(asg)
    return resolver.resolve()
