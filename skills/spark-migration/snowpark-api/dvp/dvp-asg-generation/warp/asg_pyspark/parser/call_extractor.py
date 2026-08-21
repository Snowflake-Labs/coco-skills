"""
Call Extractor - Build ExecutionCall objects from captured call sites.

This module transforms the raw call_sites captured by SparkASTParser into
structured ExecutionCall objects that track the call graph between functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from warp_core.ir.pyspark_models import (
    BindingSourceType,
    CallBindings,
    CalleeRef,
    CallLocation,
    ExecutionCall,
    InputBinding,
    OutputBinding,
)

if TYPE_CHECKING:
    from warp_core.ir.pyspark_models import ASG, FunctionDefinition


class CallExtractor:
    """
    Extract ExecutionCall objects from captured call sites.

    Takes the raw call_sites from SparkASTParser and transforms them into
    structured ExecutionCall models with proper binding resolution.
    """

    def __init__(
        self,
        call_sites: list[dict[str, Any]],
        functions: list[dict[str, Any]],
        source_file: str,
        imports: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """
        Initialize the extractor.

        Args:
            call_sites: Raw call sites from SparkASTParser
            functions: List of function dicts from the parser
            source_file: Current source file path
            imports: Optional imports dict for cross-file call resolution
        """
        self._call_sites = call_sites
        self._functions = functions
        self._source_file = source_file
        self._imports = imports or {}
        self._call_counter = 0

        # Build function lookup for quick access (name -> function dict)
        self._func_lookup: dict[str, dict[str, Any]] = {
            f["name"]: f for f in functions if f.get("name")
        }

        # Build import lookup for cross-file calls (func_name -> module)
        self._import_lookup: dict[str, str] = {}
        for module, entry in self._imports.items():
            for name in entry.get("imported_names", []):
                # Handle "name:alias" format
                clean_name = name.split(":")[0] if ":" in name else name
                self._import_lookup[clean_name] = module

    def extract(self) -> list[ExecutionCall]:
        """
        Extract ExecutionCall objects from all captured call sites.

        Returns:
            List of ExecutionCall objects
        """
        execution_calls: list[ExecutionCall] = []

        for call_site in self._call_sites:
            exec_call = self._build_execution_call(call_site)
            if exec_call:
                execution_calls.append(exec_call)

        return execution_calls

    def _build_execution_call(self, call_site: dict[str, Any]) -> ExecutionCall | None:
        """
        Build an ExecutionCall from a raw call site.

        Args:
            call_site: Raw call site dict with:
                - function_name: Name of called function
                - argument_bindings: {param_name: source_id}
                - argument_metadata: {param_name: {resolved_from, ...}}
                - output_variable: Variable receiving the return
                - line_number: Line of the call
                - caller_function: Function containing the call (or None for module level)

        Returns:
            ExecutionCall object or None if invalid
        """
        func_name = call_site.get("function_name")
        if not func_name:
            return None

        # Generate call ID
        self._call_counter += 1
        call_id = f"call_{self._call_counter:03d}"

        # Build caller location
        caller_func = call_site.get("caller_function") or "__main__"
        caller = CallLocation(
            function=caller_func,
            line=call_site.get("line_number", 0),
            file=self._source_file,
        )

        # Build callee reference
        # Determine if the function is from another file
        callee_file: str | None = None
        if func_name in self._func_lookup:
            # Function is defined - check its source file
            func_source = self._func_lookup[func_name].get("source_file")
            if func_source and func_source != self._source_file:
                callee_file = func_source
        elif func_name in self._import_lookup:
            # Function is imported - use the module as a hint
            # Convert module path to file path (e.g., "obfuscate_data" -> "obfuscate_data.py")
            module = self._import_lookup[func_name]
            # Simple conversion: assume local module maps to .py file
            if not module.startswith("."):
                callee_file = f"{module.replace('.', '/')}.py"

        callee = CalleeRef(
            function=func_name,
            file=callee_file,
        )

        # Build input bindings
        input_bindings = self._build_input_bindings(
            call_site.get("argument_bindings", {}),
            call_site.get("argument_metadata", {}),
        )

        # Build output binding
        output_binding = self._build_output_binding(
            call_site.get("output_variable"),
            func_name,
        )

        bindings = CallBindings(
            inputs=input_bindings,
            output=output_binding,
        )

        # Extract literal arguments from call_site
        literal_args = call_site.get("literal_arguments", {})
        resolved_literals = call_site.get("resolved_literals", {})
        # Merge: resolved_literals takes priority (resolved variable values)
        all_literals = {**literal_args, **resolved_literals}
        
        return ExecutionCall(
            call_id=call_id,
            caller=caller,
            callee=callee,
            bindings=bindings,
            literal_arguments=all_literals,
        )

    def _build_input_bindings(
        self,
        argument_bindings: dict[str, str],
        argument_metadata: dict[str, dict[str, Any]],
    ) -> list[InputBinding]:
        """
        Build InputBinding objects from argument bindings.

        Determines the source_type based on the source_id format:
        - "in_XXX" -> data_in
        - "tx_XXX" -> transformation
        - "call_XXX" -> call_output
        - otherwise -> variable
        """
        bindings: list[InputBinding] = []

        for arg_name, source_id in argument_bindings.items():
            source_type = self._determine_source_type(source_id, argument_metadata.get(arg_name))

            bindings.append(
                InputBinding(
                    arg_name=arg_name,
                    source_type=source_type,
                    source_id=source_id,
                )
            )

        return bindings

    def _determine_source_type(
        self,
        source_id: str,
        metadata: dict[str, Any] | None,
    ) -> BindingSourceType:
        """
        Determine the binding source type from the source ID.

        Args:
            source_id: The resolved source ID (e.g., "in_008", "tx_004", "df_sales")
            metadata: Optional metadata about how the binding was resolved

        Returns:
            BindingSourceType enum value
        """
        if source_id.startswith("in_"):
            return BindingSourceType.DATA_IN
        elif source_id.startswith("tx_"):
            return BindingSourceType.TRANSFORMATION
        elif source_id.startswith("call_"):
            return BindingSourceType.CALL_OUTPUT
        elif source_id.startswith("param_"):
            return BindingSourceType.VARIABLE
        else:
            # Check if it looks like a variable name (has underscore prefix or common patterns)
            # or a literal value (table name, file path, etc.)
            # Variables typically don't have dots or slashes
            if "/" in source_id or "." in source_id or source_id.endswith("_data"):
                # Looks like a table name or file path -> literal
                return BindingSourceType.LITERAL
            # Check if source_id is a valid Python identifier (variable name)
            if source_id.isidentifier():
                return BindingSourceType.VARIABLE
            # Otherwise treat as literal
            return BindingSourceType.LITERAL

    def _build_output_binding(
        self,
        output_variable: str | None,
        func_name: str,
    ) -> OutputBinding | None:
        """
        Build an OutputBinding from the call site.

        Args:
            output_variable: The variable name receiving the return value
            func_name: The called function name (to look up return info)

        Returns:
            OutputBinding or None if no output variable
        """
        if not output_variable:
            return None

        # Look up the function's return to find target_node
        target_node: str | None = None
        if func_name in self._func_lookup:
            func_info = self._func_lookup[func_name]
            returns = func_info.get("returns", {})
            ref_type = returns.get("ref_type")
            ref_id = returns.get("ref_id")

            if ref_type == "transformation" and ref_id:
                target_node = ref_id

        return OutputBinding(
            variable_name=output_variable,
            target_node=target_node,
        )


def extract_execution_calls(
    call_sites: list[dict[str, Any]],
    functions: list[dict[str, Any]],
    source_file: str,
    imports: dict[str, dict[str, Any]] | None = None,
) -> list[ExecutionCall]:
    """
    Convenience function to extract ExecutionCall objects.

    Args:
        call_sites: Raw call sites from SparkASTParser
        functions: List of function dicts from the parser
        source_file: Current source file path
        imports: Optional imports dict for cross-file call resolution

    Returns:
        List of ExecutionCall objects
    """
    extractor = CallExtractor(call_sites, functions, source_file, imports)
    return extractor.extract()
