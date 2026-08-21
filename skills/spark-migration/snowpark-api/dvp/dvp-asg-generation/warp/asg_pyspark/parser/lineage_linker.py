"""
Lineage Linker: Cross-Function Lineage Resolution

This module resolves the disconnect between function-local transformations
(which use param_xxx inputs) and the actual data flow (which connects to
real tx_xxx or in_xxx nodes).

The Linker acts as a post-processing phase that "sews" together the isolated
function blocks into a continuous data flow graph.
"""

from __future__ import annotations

from typing import Any

from warp_core.symbol_table import SymbolTable

# Avoid circular imports - use string annotation
# The actual type will be resolved at runtime


class LineageLinker:
    """
    Resolves cross-function lineage by connecting param_xxx references
    to their actual source nodes (tx_xxx or in_xxx).

    The resolution happens in order of call appearance, allowing
    function returns to feed into subsequent function calls.
    """

    def __init__(self, call_sites: list[dict[str, Any]], functions: list[dict[str, Any]]) -> None:
        """
        Initialize the linker with captured call-sites and function definitions.

        Args:
            call_sites: List of function call records from the parser
            functions: List of function definitions from the ASG
        """
        self.call_sites = call_sites
        self.functions = functions

        # Map: function_name -> list of param names (ordered)
        self.func_params: dict[str, list[str]] = self._build_param_map()

        # Map: variable_name -> resolved node ID
        # Used to track function return values for chaining
        self.resolved_vars: dict[str, str] = {}

        # Statistics for reporting
        self.stats = {
            "links_resolved": 0,
            "params_linked": 0,
            "functions_processed": 0,
        }

    def _extract_call_site_fields(self, call_site: dict) -> tuple[str | None, str | None, int]:
        """
        Extract output_var, called_func, and call_line from a call_site.
        
        Handles both parser.call_sites structure and asg.execution_calls structure:
        - parser.call_sites: function_name, output_variable, line_number
        - execution_calls: callee.function, bindings.output.variable_name, caller.line
        """
        if isinstance(call_site, dict):
            # Try parser.call_sites structure first (more common in this context)
            output_var = call_site.get("output_variable")
            called_func = call_site.get("function_name")
            call_line = call_site.get("line_number", 0)
            
            # If not found, try execution_calls structure
            if output_var is None:
                bindings = call_site.get("bindings", {})
                output = bindings.get("output", {}) if bindings else {}
                output_var = output.get("variable_name") if output else None
            
            if called_func is None:
                callee = call_site.get("callee", {})
                called_func = callee.get("function") if callee else None
            
            if call_line == 0:
                caller = call_site.get("caller", {})
                call_line = caller.get("line", 0) if caller else 0
        else:
            # Pydantic model - try both access patterns
            output_var = getattr(call_site, 'output_variable', None)
            called_func = getattr(call_site, 'function_name', None)
            call_line = getattr(call_site, 'line_number', 0)
            
            if output_var is None and hasattr(call_site, 'bindings'):
                output = getattr(call_site.bindings, 'output', None)
                output_var = getattr(output, 'variable_name', None) if output else None
            if called_func is None and hasattr(call_site, 'callee'):
                called_func = getattr(call_site.callee, 'function', None)
            if call_line == 0 and hasattr(call_site, 'caller'):
                call_line = getattr(call_site.caller, 'line', 0)
        
        return output_var, called_func, call_line

    def _build_param_map(self) -> dict[str, list[str]]:
        """Build a map of function names to their parameter names."""
        param_map = {}
        for func in self.functions:
            func_name = func.get("name")
            if func_name:
                args = func.get("arguments", [])
                param_map[func_name] = [arg.get("name") for arg in args if arg.get("name")]
        return param_map

    def resolve(self, asg: Any) -> Any:
        """
        Main entry point: resolve all param_xxx references to real IDs.

        Args:
            asg: The Abstract Semantic Graph to resolve

        Returns:
            The ASG with resolved lineage
        """
        # Build a map of function parameters to their resolved values
        # by processing outer calls first
        self._build_parameter_resolution_map(asg)

        # Process call-sites in order of appearance (simulating execution)
        for call_site in self.call_sites:
            self._process_call_site(call_site, asg)

        # Second pass: resolve any remaining param_xxx references
        # using the accumulated parameter resolution map
        self._resolve_remaining_params(asg)

        # Third pass: resolve sink source_ids (data_out)
        self._resolve_sink_sources(asg)

        return asg

    def _build_parameter_resolution_map(self, asg: Any) -> None:
        """
        Build a map from function parameter IDs to their resolved values.

        Process the call chain to determine what each param_xxx ultimately
        resolves to (in_xxx or tx_xxx).
        """
        self._param_resolution: dict[str, str] = {}

        # Find the top-level call (caller_function is None)
        top_level_calls = [cs for cs in self.call_sites if cs.get("caller_function") is None]

        for top_call in top_level_calls:
            top_call.get("function_name")
            bindings = top_call.get("argument_bindings", {})

            # These bindings map param names to real IDs (in_xxx, tx_xxx)
            for param_name, real_id in bindings.items():
                param_id = f"param_{param_name}"
                self._param_resolution[param_id] = real_id

        # Now propagate through nested calls
        # Find calls where bindings point to params, and resolve them
        changed = True
        max_iterations = 10  # Prevent infinite loops
        iteration = 0

        while changed and iteration < max_iterations:
            changed = False
            iteration += 1

            for call_site in self.call_sites:
                call_site.get("function_name")
                bindings = call_site.get("argument_bindings", {})

                for param_name, source_id in bindings.items():
                    target_param = f"param_{param_name}"

                    # If source is a param, try to resolve it
                    if source_id.startswith("param_") and source_id in self._param_resolution:
                        resolved = self._param_resolution[source_id]
                        if target_param not in self._param_resolution:
                            self._param_resolution[target_param] = resolved
                            changed = True
                    elif not source_id.startswith("param_"):
                        # Direct resolution (in_xxx or tx_xxx)
                        if target_param not in self._param_resolution:
                            self._param_resolution[target_param] = source_id
                            changed = True

    def _resolve_remaining_params(self, asg: Any) -> None:
        """
        Second pass: resolve any remaining param_xxx references
        using the accumulated parameter resolution map.
        """
        for tx in asg.transformations:
            if not tx.inputs:
                continue

            new_inputs = []
            for input_id in tx.inputs:
                if input_id.startswith("param_") and input_id in self._param_resolution:
                    resolved = self._param_resolution[input_id]
                    new_inputs.append(resolved)

                    # Update metadata
                    if not hasattr(tx, "parameters") or tx.parameters is None:
                        tx.parameters = {}
                    if "_lineage_metadata" not in tx.parameters:
                        tx.parameters["_lineage_metadata"] = {}
                    tx.parameters["_lineage_metadata"][resolved] = {
                        "original_ref": input_id,
                        "resolved_at": "second_pass",
                    }

                    self.stats["params_linked"] += 1
                else:
                    new_inputs.append(input_id)

            if new_inputs != tx.inputs:
                tx.inputs = new_inputs
                self.stats["links_resolved"] += 1

    def _resolve_sink_sources(self, asg: Any) -> None:
        """
        Resolve sink source_id fields (data_out).

        Sinks inside functions have source_id like 'param_df' which needs
        to be resolved to the actual tx_xxx that feeds them.
        
        Also resolves sinks with source_id=None by matching sink name to
        function call output variables (cross-function resolution).

        Strategy:
        1. Find which function contains each sink (by line number)
        2. Find the call site for that function
        3. Use the bindings to resolve param_xxx to tx_xxx
        """
        # Build function line ranges using location
        func_ranges: dict[str, tuple[int, int]] = {}
        for func in asg.functions:
            if func.location:
                func_ranges[func.name] = (func.location.start_line, func.location.end_line)

        # First: resolve sinks with source_id=None (cross-function case)
        self._resolve_none_source_sinks(asg, func_ranges)

        for sink in asg.data_out:
            source_id = sink.source_id
            if not source_id or not source_id.startswith("param_"):
                continue

            sink_line = sink.location.start_line if sink.location else 0
            param_name = source_id[6:]  # Remove "param_" prefix

            # Find which function contains this sink
            containing_func = None
            for func_name, (start, end) in func_ranges.items():
                if start <= sink_line <= end:
                    containing_func = func_name
                    break

            if not containing_func:
                continue

            # Find the call site for this function
            for call_site in self.call_sites:
                if call_site.get("function_name") == containing_func:
                    bindings = call_site.get("argument_bindings", {})

                    # Look for the parameter in bindings
                    if param_name in bindings:
                        resolved = bindings[param_name]
                        if not resolved.startswith("param_"):
                            sink.source_id = resolved
                            self.stats["links_resolved"] += 1
                            break

                    # Also try 'df' as common name for DataFrame params
                    if param_name == "df" and "df" in bindings:
                        resolved = bindings["df"]
                        if not resolved.startswith("param_"):
                            sink.source_id = resolved
                            self.stats["links_resolved"] += 1
                            break

    def _resolve_none_source_sinks(self, asg: Any, func_ranges: dict[str, tuple[int, int]]) -> None:
        """
        Resolve sinks with source_id=None by tracing through function calls.
        
        When we have:
            final_df = run_pipeline(...)
            final_df.write.saveAsTable("final_df")
        
        The sink "final_df" has source_id=None because run_pipeline() is a function call.
        We need to trace through the function to find the actual transformation.
        
        Key: We must find the call site in the SAME SCOPE as the sink (closest preceding line).
        """
        
        for sink in asg.data_out:
            if sink.source_id is not None:
                continue  # Already resolved
            
            sink_name = sink.name
            if not sink_name:
                continue
            
            # Get sink line number
            sink_line = 0
            if hasattr(sink, 'location') and sink.location:
                span = getattr(sink.location, 'span', '') or ''
                sink_line = self._parse_line_from_span(span, "start")
            
            # Find ALL matching call sites (same output variable name)
            candidates = []
            for call_site in self.call_sites:
                output_var, called_func, call_line = self._extract_call_site_fields(call_site)
                if output_var and output_var == sink_name and called_func:
                    candidates.append((call_line, called_func))
            
            if not candidates:
                continue
            
            # Find the call closest to (and before) the sink line
            best_call = None
            best_distance = float('inf')
            for call_line, called_func in candidates:
                if call_line <= sink_line:
                    distance = sink_line - call_line
                    if distance < best_distance:
                        best_distance = distance
                        best_call = called_func
            
            if best_call:
                # Only resolve if the function is defined in THIS file.
                # Cross-file references will be resolved after rebasing by the
                # cross-file linker pass. This prevents incorrect rebasing of
                # source_ids that reference transformations from other files.
                func_is_local = any(
                    (f.get("name") if isinstance(f, dict) else getattr(f, "name", None)) == best_call
                    for f in self.functions
                )
                
                if func_is_local:
                    # Recursively resolve the function's return
                    resolved = self._get_function_return_id(best_call)
                    if resolved:
                        sink.source_id = resolved
                        self.stats["links_resolved"] += 1
                    elif sink_name in self.resolved_vars:
                        sink.source_id = self.resolved_vars[sink_name]
                        self.stats["links_resolved"] += 1
                # else: Leave source_id=None for cross-file linker to handle

    def _process_call_site(self, call_site: dict[str, Any], asg: Any) -> None:
        """
        Process a single function call, resolving its parameter bindings.

        Args:
            call_site: The call-site record
            asg: The ASG being processed
        """
        func_name = call_site.get("function_name")
        bindings = call_site.get("argument_bindings", {})
        arg_metadata = call_site.get("argument_metadata", {})
        output_var = call_site.get("output_variable")
        caller_func = call_site.get("caller_function")
        line_number = call_site.get("line_number")

        if not func_name or not bindings:
            return

        # Resolve any bindings that reference previously resolved variables
        resolved_bindings = self._enhance_bindings(bindings)

        # Find all transformations that belong to this function
        func_transformations = [
            tx for tx in asg.transformations if self._belongs_to_function(tx, func_name, asg)
        ]

        # Resolve param_xxx inputs in each transformation
        for tx in func_transformations:
            self._resolve_node_inputs(tx, resolved_bindings, arg_metadata, caller_func, line_number)

        # If the function returns something, track it for later calls
        if output_var:
            func_return = self._get_function_return_id(func_name)
            if func_return:
                self.resolved_vars[output_var] = func_return

        self.stats["functions_processed"] += 1

    def _enhance_bindings(self, bindings: dict[str, str]) -> dict[str, str]:
        """
        Enhance bindings by resolving references to previously computed results.

        If a binding points to a variable that was the result of a previous
        function call, resolve it to the actual node ID.
        """
        enhanced = {}
        for param_name, source_id in bindings.items():
            # Check if this is a variable reference we've already resolved
            if source_id in self.resolved_vars:
                enhanced[param_name] = self.resolved_vars[source_id]
            else:
                enhanced[param_name] = source_id
        return enhanced

    def _belongs_to_function(self, tx: Any, func_name: str, asg: Any) -> bool:
        """
        Determine if a transformation belongs to a specific function.

        Uses line numbers to determine if the transformation falls within
        the function's definition range.
        """
        # Get function line range using location
        for func in asg.functions:
            if func.name == func_name and func.location and tx.location:
                func_start = func.location.start_line
                func_end = func.location.end_line
                tx_line = tx.location.start_line
                return func_start <= tx_line <= func_end
        return False

    def _resolve_node_inputs(
        self,
        tx: Any,
        bindings: dict[str, str],
        arg_metadata: dict[str, dict],
        caller_func: str | None,
        call_line: int,
    ) -> None:
        """
        Resolve param_xxx inputs in a transformation node.

        Args:
            tx: The transformation node
            bindings: Map of param_name -> real_id
            arg_metadata: Metadata about how arguments were resolved (nested_transformation, variable)
            caller_func: Name of the calling function (for metadata)
            call_line: Line number of the call (for metadata)
        """
        if not tx.inputs:
            return

        new_inputs = []
        metadata = {}

        for input_id in tx.inputs:
            if input_id.startswith("param_"):
                # Extract parameter name: param_df_sales -> df_sales
                param_name = input_id[6:]  # Remove "param_" prefix

                if param_name in bindings:
                    real_id = bindings[param_name]
                    new_inputs.append(real_id)

                    # Build enhanced metadata including resolution info
                    meta_entry = {
                        "original_ref": input_id,
                        "resolved_at": f"{caller_func or 'global'}:L{call_line}",
                    }

                    # Add argument resolution metadata if available
                    if param_name in arg_metadata:
                        meta_entry.update(arg_metadata[param_name])

                    metadata[real_id] = meta_entry
                    self.stats["params_linked"] += 1
                else:
                    # Try matching without common prefixes
                    matched = False
                    for key in bindings:
                        if param_name.endswith(key) or key.endswith(param_name):
                            real_id = bindings[key]
                            new_inputs.append(real_id)

                            meta_entry = {
                                "original_ref": input_id,
                                "resolved_at": f"{caller_func or 'global'}:L{call_line}",
                            }
                            if key in arg_metadata:
                                meta_entry.update(arg_metadata[key])

                            metadata[real_id] = meta_entry
                            matched = True
                            self.stats["params_linked"] += 1
                            break

                    if not matched:
                        # Keep original if no binding found
                        new_inputs.append(input_id)
            else:
                # Not a param reference, keep as-is
                new_inputs.append(input_id)

        # Update the node
        if new_inputs != tx.inputs:
            tx.inputs = new_inputs
            self.stats["links_resolved"] += 1

            # Add metadata to parameters
            if metadata:
                if not hasattr(tx, "parameters") or tx.parameters is None:
                    tx.parameters = {}
                tx.parameters["_lineage_metadata"] = metadata

    def _get_function_return_id(self, func_name: str, visited: set[str] | None = None) -> str | None:
        """
        Get the return node ID for a function (with recursive resolution).
        
        Delegates to the global SymbolTable registry which has cross-file visibility
        and handles both direct transformation returns and variable assignments.
        """
        return SymbolTable.resolve_function_return(func_name, visited)
    
    def _parse_line_from_span(self, span: str, which: str) -> int:
        """Parse line number from span string like '116:1-127:1'."""
        if not span:
            return 0
        try:
            parts = span.split("-")
            if which == "start":
                return int(parts[0].split(":")[0])
            else:
                return int(parts[1].split(":")[0]) if len(parts) > 1 else int(parts[0].split(":")[0])
        except (ValueError, IndexError):
            return 0

    def get_stats(self) -> dict[str, int]:
        """Return resolution statistics."""
        return self.stats.copy()
