"""
Symbol Table for tracking DataFrame variable assignments and lineage.

This module tracks the mapping between variable names and ASG node IDs,
enabling lineage resolution (filling the `inputs` field in transformations).

Key features:
- Shadowing support: Handles reassignment (df = df.filter(...))
- Multi-input resolution: Tracks both sides of joins
- Scope handling: Manages function-level vs global scope
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, ClassVar

from warp_core.ir.pyspark_models import GLOBAL_SCOPE


@dataclass
class ScopeInfo:
    """Information about a single scope (function or global)."""

    name: str
    variables: dict[str, str] = field(default_factory=dict)  # var_name -> node_id
    parent: ScopeInfo | None = None

    def set(self, name: str, node_id: str) -> None:
        """Set a variable in this scope."""
        self.variables[name] = node_id

    def get(self, name: str) -> str | None:
        """Get a variable, checking parent scopes if not found locally."""
        if name in self.variables:
            return self.variables[name]
        if self.parent:
            return self.parent.get(name)
        return None


@dataclass
class SourceBinding:
    """Binding between a variable name and a DataSource."""
    variable_name: str
    source_id: str
    source_name: str
    file: str


@dataclass
class FunctionSignature:
    """Function metadata for cross-file resolution."""
    
    name: str
    file: str
    returns_type: str              # "transformation" | "variable" | "expression"
    returns_id: str | None         # "tx_026" or "final_df"
    containing_class: str | None = None  # "UtilsS3", "DataProcessor", etc.
    line_start: int = 0
    line_end: int = 0


class SymbolTable:
    """
    Track DataFrame variable assignments and resolve lineage.

    Usage:
        table = SymbolTable()

        # When parsing: df = spark.read.table("raw")
        table.set("df", "src_001")

        # When parsing: df = df.filter(...)
        inputs = table.resolve_inputs(node)  # Returns ["src_001"]
        table.set("df", "tx_001")  # Overwrites with new node

        # When parsing: result = df.join(other, ...)
        inputs = table.resolve_inputs(node)  # Returns ["tx_001", "src_002"]
    """

    # =========================================================================
    # Global Registry (class-level, shared across all instances)
    # =========================================================================
    
    _global_functions: ClassVar[dict[str, FunctionSignature]] = {}
    _global_call_sites: ClassVar[list[dict[str, Any]]] = []
    _global_sources: ClassVar[dict[str, SourceBinding]] = {}
    _global_var_assignments: ClassVar[dict[str, str]] = {}  # "scope::var" -> "tx_XXX"
    _global_window_specs: ClassVar[dict[str, str]] = {}  # "scope::var" -> "ORDER BY col DESC"
    _global_control_usages: ClassVar[set[str]] = set()  # source_ids used in control flow (.rdd, .count, etc.)
    _global_string_literals: ClassVar[dict[str, str]] = {}  # "scope::var" -> "literal_value"
    
    @classmethod
    def reset_global(cls) -> None:
        """Reset global registry. Call between workload parsings."""
        cls._global_functions.clear()
        cls._global_call_sites.clear()
        cls._global_sources.clear()
        cls._global_var_assignments.clear()
        cls._global_window_specs.clear()
        cls._global_control_usages.clear()
        cls._global_string_literals.clear()
    
    @classmethod
    def register_function(cls, func: FunctionSignature) -> None:
        """Register a function for cross-file resolution."""
        cls._global_functions[func.name] = func
    
    @classmethod
    def register_call_site(cls, call_site: dict[str, Any]) -> None:
        """Register a call site for cross-file resolution."""
        cls._global_call_sites.append(call_site)
    
    @classmethod
    def resolve_function_return(cls, func_name: str, visited: set[str] | None = None) -> str | None:
        """
        Resolve what a function returns (with recursive resolution).
        
        Handles chains like:
            run_pipeline() -> returns variable "final_df"
            -> final_df = obfuscate_and_score() 
            -> obfuscate_and_score() returns tx_026
            -> returns "tx_026"
        """
        # Prevent infinite recursion
        if visited is None:
            visited = set()
        if func_name in visited:
            return None
        visited.add(func_name)
        
        sig = cls._global_functions.get(func_name)
        if not sig:
            return None
        
        # Direct transformation return
        if sig.returns_type == "transformation" and sig.returns_id:
            if sig.returns_id.startswith("tx_"):
                return sig.returns_id
        
        # Variable return - trace through call sites or direct assignments
        if sig.returns_type == "variable" and sig.returns_id:
            var_name = sig.returns_id
            
            # First, check if variable was assigned directly to a transformation
            direct_node = cls.resolve_var_to_node(func_name, var_name)
            if direct_node and direct_node.startswith("tx_"):
                return direct_node
            
            # Otherwise, find call site inside this function that assigns to var_name
            for call_site in cls._global_call_sites:
                output_var = call_site.get("output_variable")
                call_line = call_site.get("line_number", 0)
                called_func = call_site.get("function_name")
                
                # Check if this call is inside our function and assigns to var_name
                if (sig.line_start <= call_line <= sig.line_end and 
                    output_var == var_name and called_func):
                    # Recursively resolve the called function
                    nested_return = cls.resolve_function_return(called_func, visited)
                    if nested_return:
                        return nested_return
        
        return None
    
    @classmethod
    def register_var_assignment(cls, scope: str, var_name: str, node_id: str) -> None:
        """
        Register a variable assignment to a transformation.
        
        This enables tracing variables like final_enriched_df back to their
        producing transformation tx_XXX when resolving function returns.
        
        Args:
            scope: Function name where assignment occurs (e.g., "complex_retail_pipeline")
            var_name: Variable name (e.g., "final_enriched_df")
            node_id: Transformation ID (e.g., "tx_015")
        """
        key = f"{scope}::{var_name}"
        cls._global_var_assignments[key] = node_id
    
    @classmethod
    def resolve_var_to_node(cls, scope: str, var_name: str) -> str | None:
        """Resolve a variable in a scope to its node ID."""
        key = f"{scope}::{var_name}"
        return cls._global_var_assignments.get(key)

    @classmethod
    def register_window_spec(cls, scope: str, var_name: str, sql_definition: str) -> None:
        """
        Register a Window specification variable with its SQL equivalent.
        
        Example: window_spec = Window.orderBy(col('x').desc())
        -> register_window_spec("my_func", "window_spec", "ORDER BY x DESC")
        """
        key = f"{scope}::{var_name}"
        cls._global_window_specs[key] = sql_definition

    @classmethod
    def resolve_window_spec(cls, scope: str, var_name: str) -> str | None:
        """Resolve a window spec variable to its SQL definition."""
        key = f"{scope}::{var_name}"
        result = cls._global_window_specs.get(key)
        if not result:
            # Try without scope (global window specs)
            result = cls._global_window_specs.get(var_name)
        
        if result:
            # Convert PySpark expression to SQL if not already converted
            if result.startswith("Window.") or "Window." in result:
                # Lazy import to avoid circular dependencies
                from asg_pyspark.analysis.spark_to_sql import spark_expr_to_sql
                sql_result = spark_expr_to_sql(result)
                if sql_result:
                    # Cache the converted result
                    cls._global_window_specs[key] = sql_result
                    return sql_result
            return result
        return None

    @classmethod
    def register_string_literal(cls, scope: str, var_name: str, value: str) -> None:
        """Register a string literal assignment for later resolution.
        
        Args:
            scope: Function name or GLOBAL_SCOPE
            var_name: Variable name
            value: The literal string value
        """
        key = f"{scope}::{var_name}"
        cls._global_string_literals[key] = value
    
    @classmethod
    def resolve_string_literal(cls, scope: str, var_name: str) -> str | None:
        """Resolve a variable to its literal string value if known.
        
        Args:
            scope: Function name or GLOBAL_SCOPE
            var_name: Variable name
            
        Returns:
            The literal value if found, None otherwise
        """
        # Try scope-qualified first
        key = f"{scope}::{var_name}"
        result = cls._global_string_literals.get(key)
        if result:
            return result
        
        # Try global scope
        global_key = f"{GLOBAL_SCOPE}::{var_name}"
        return cls._global_string_literals.get(global_key)

    @classmethod
    def register_source(cls, var_name: str, source_id: str, source_name: str, file: str = "") -> None:
        """Register a DataSource binding globally."""
        binding = SourceBinding(
            variable_name=var_name,
            source_id=source_id,
            source_name=source_name,
            file=file,
        )
        # Register by var_name (may overwrite from other files)
        cls._global_sources[var_name] = binding
        # Also register by source_id for direct lookups
        cls._global_sources[source_id] = binding
        # Register with file-scoped key for precise lookups
        if file:
            file_key = f"{file}:{var_name}"
            cls._global_sources[file_key] = binding
    
    @classmethod
    def register_control_usage(cls, source_id: str) -> None:
        """
        Register that a source is used in control flow (.rdd, .count, .isEmpty, etc.).
        
        This prevents the source from being marked as orphan (LIN_002).
        """
        cls._global_control_usages.add(source_id)
    
    @classmethod
    def resolve_source(cls, var_name_or_id: str, file_context: str = "") -> SourceBinding | None:
        """
        Resolve a variable name or ID to its SourceBinding.
        
        Supports:
        - Direct lookup: "df_sales" -> SourceBinding
        - ID lookup: "in_001" -> SourceBinding
        - File-scoped lookup: prioritizes bindings from same file
        - Partial match: "sales" matches "df_sales" or "sales_data"
        
        Args:
            var_name_or_id: Variable name or source ID to resolve
            file_context: Optional file path to prioritize bindings from
        """
        # Try file-scoped lookup first (most precise)
        if file_context:
            file_key = f"{file_context}:{var_name_or_id}"
            if file_key in cls._global_sources:
                return cls._global_sources[file_key]
        
        # Direct lookup
        if var_name_or_id in cls._global_sources:
            return cls._global_sources[var_name_or_id]
        
        # Normalized partial match
        normalized = var_name_or_id.lower().replace("df_", "").replace("_df", "").replace("_data", "").replace("data_", "")
        
        for key, binding in cls._global_sources.items():
            if not isinstance(binding, SourceBinding):
                continue
            # Check source_name partial match (defensive against None)
            if binding.source_name:
                source_normalized = binding.source_name.lower().replace("_data", "").replace("data_", "")
                if normalized == source_normalized:
                    return binding
            # Check variable_name partial match (defensive against None)
            if binding.variable_name:
                var_normalized = binding.variable_name.lower().replace("df_", "").replace("_df", "")
                if normalized == var_normalized:
                    return binding
        
        return None
    
    @classmethod
    def get_registered_sources(cls) -> dict[str, SourceBinding]:
        """Get all registered sources (for debugging)."""
        return {k: v for k, v in cls._global_sources.items() if isinstance(v, SourceBinding)}

    @classmethod
    def get_registered_functions(cls) -> dict[str, FunctionSignature]:
        """Get all registered functions (for debugging)."""
        return cls._global_functions.copy()
    
    @classmethod
    def update_from_asg(cls, asg: Any) -> None:
        """
        Update global registry with final ASG information.
        
        Called after parsing is complete to get accurate return types
        that are only known after visiting the full function body.
        """
        for func in asg.functions:
            name = func.name
            if name in cls._global_functions:
                sig = cls._global_functions[name]
                # Update with correct return info from ASG
                returns = func.returns
                if returns:
                    # Handle both dict and object returns
                    if isinstance(returns, dict):
                        ref_type = returns.get('ref_type')
                        ref_id = returns.get('ref_id')
                    else:
                        ref_type = getattr(returns, 'ref_type', None)
                        ref_id = getattr(returns, 'ref_id', None)
                    
                    # Handle enum types
                    if hasattr(ref_type, 'value'):
                        ref_type = ref_type.value
                    
                    cls._global_functions[name] = FunctionSignature(
                        name=sig.name,
                        file=sig.file,
                        returns_type=str(ref_type) if ref_type else sig.returns_type,
                        returns_id=ref_id if ref_id else sig.returns_id,
                        containing_class=sig.containing_class,
                        line_start=sig.line_start,
                        line_end=sig.line_end,
                    )
        
        # Update sources with final (rebased) IDs
        cls._update_sources_from_asg(asg)
    
    @classmethod
    def _update_sources_from_asg(cls, asg: Any) -> None:
        """Update source registry with rebased IDs from final ASG."""
        # Build lookup from source name to final ID
        source_by_name: dict[str, tuple[str, str]] = {}
        for src in asg.data_in:
            name = src.name or getattr(src, 'path', '') or ''
            if name:
                source_by_name[name] = (src.id, name)
        
        # Update existing bindings with rebased IDs
        updated_sources: dict[str, SourceBinding] = {}
        seen_vars: set[str] = set()
        
        for key, binding in list(cls._global_sources.items()):
            if not isinstance(binding, SourceBinding):
                continue
            if binding.variable_name in seen_vars:
                continue
            
            # Find matching source by name
            if binding.source_name in source_by_name:
                new_id, _ = source_by_name[binding.source_name]
                new_binding = SourceBinding(
                    variable_name=binding.variable_name,
                    source_id=new_id,
                    source_name=binding.source_name,
                    file=binding.file,
                )
                updated_sources[binding.variable_name] = new_binding
                updated_sources[new_id] = new_binding
                seen_vars.add(binding.variable_name)
        
        # Also add any sources from ASG not yet registered
        for src in asg.data_in:
            name = src.name or getattr(src, 'path', '') or ''
            if src.id not in updated_sources:
                binding = SourceBinding(
                    variable_name=name,  # Use name as var if no var known
                    source_id=src.id,
                    source_name=name,
                    file="",
                )
                updated_sources[src.id] = binding
                if name:
                    updated_sources[name] = binding
        
        cls._global_sources.update(updated_sources)

    def __init__(self) -> None:
        self._global_scope = ScopeInfo(name="<global>")
        self._current_scope = self._global_scope
        self._all_scopes: list[ScopeInfo] = [self._global_scope]

    # =========================================================================
    # Scope Management
    # =========================================================================

    def enter_scope(self, name: str) -> None:
        """Enter a new scope (e.g., function definition)."""
        new_scope = ScopeInfo(name=name, parent=self._current_scope)
        self._all_scopes.append(new_scope)
        self._current_scope = new_scope

    def exit_scope(self) -> None:
        """Exit the current scope and return to parent."""
        if self._current_scope.parent:
            self._current_scope = self._current_scope.parent

    @property
    def current_scope_name(self) -> str:
        """Get the name of the current scope."""
        return self._current_scope.name

    # =========================================================================
    # Variable Tracking
    # =========================================================================

    def set(self, name: str, node_id: str) -> None:
        """
        Set a variable to point to a node ID.

        This handles shadowing: if 'df' already exists, it will be
        overwritten with the new node_id.
        """
        self._current_scope.set(name, node_id)

    def get(self, name: str) -> str | None:
        """
        Get the node ID for a variable name.

        Searches current scope first, then parent scopes.
        """
        return self._current_scope.get(name)

    def has(self, name: str) -> bool:
        """Check if a variable is defined in any accessible scope."""
        return self.get(name) is not None

    # =========================================================================
    # Input Resolution
    # =========================================================================

    def resolve_inputs(self, node: ast.Call) -> list[str]:
        """
        Resolve the input node IDs for a method call.

        Given a call like `df.filter(...)` or `df.join(other, ...)`,
        returns the list of node IDs that are inputs to this operation.

        Examples:
            df.filter(...)          -> [<id of df>]
            df.join(other, ...)     -> [<id of df>, <id of other>]
            df.union(other)         -> [<id of df>, <id of other>]
        """
        inputs: list[str] = []

        # 1. Resolve the object the method is called on (df in df.filter())
        caller_id = self._resolve_caller(node)
        if caller_id:
            inputs.append(caller_id)

        # 2. For binary operations (join, union, etc.), resolve the other DataFrame
        method_name = self._get_method_name(node)
        if method_name in self._BINARY_OPERATIONS:
            other_id = self._resolve_first_arg(node)
            if other_id:
                inputs.append(other_id)

        return inputs

    # Binary operations that take another DataFrame as first argument
    _BINARY_OPERATIONS = {
        "join",
        "crossJoin",
        "union",
        "unionAll",
        "unionByName",
        "intersect",
        "intersectAll",
        "except",
        "exceptAll",
        "subtract",
    }

    def _resolve_caller(self, node: ast.Call) -> str | None:
        """
        Resolve the caller object in a method call chain.

        For `df.filter(...).select(...)`, traverses back to find `df`.
        """
        current = node.func

        while True:
            match current:
                case ast.Attribute(value=ast.Name(id=name)):
                    # Found the root variable (e.g., df in df.filter())
                    return self.get(name)

                case ast.Attribute(value=ast.Call() as inner_call):
                    # Method chain (e.g., df.filter().select())
                    # Recurse into the inner call
                    current = inner_call.func

                case ast.Attribute(value=ast.Attribute() as inner_attr):
                    # Attribute chain (e.g., spark.read.table())
                    current = inner_attr

                case ast.Name(id=name):
                    # Direct variable reference
                    return self.get(name)

                case _:
                    return None

    def _resolve_first_arg(self, node: ast.Call) -> str | None:
        """
        Resolve the first positional argument if it's a DataFrame.

        For `df.join(other, ...)`, returns the node ID of `other`.
        """
        if not node.args:
            return None

        first_arg = node.args[0]

        match first_arg:
            case ast.Name(id=name):
                return self.get(name)
            case ast.Call():
                # The argument is a method call chain, resolve it
                return self._resolve_caller(first_arg)
            case _:
                return None

    def _get_method_name(self, node: ast.Call) -> str | None:
        """Extract the method name from a call node."""
        match node.func:
            case ast.Attribute(attr=name):
                return name
            case _:
                return None

    # =========================================================================
    # Debugging
    # =========================================================================

    def dump(self) -> dict[str, Any]:
        """Dump the symbol table for debugging."""
        return {
            "current_scope": self._current_scope.name,
            "scopes": [{"name": s.name, "variables": dict(s.variables)} for s in self._all_scopes],
        }

    def __repr__(self) -> str:
        return f"SymbolTable(scope={self._current_scope.name}, vars={len(self._current_scope.variables)})"


# =============================================================================
# Type Tracking Extension
# =============================================================================

# Known PySpark module mappings for import validation
_PYSPARK_MODULES = {
    "pyspark.sql.functions": "Column",  # F.col(), F.lit() -> Column
    "pyspark.sql": "pyspark.sql",
    "pyspark.sql.types": "pyspark.sql.types",
    "pyspark.sql.window": "Window",
}

# Standard aliases by convention
_STANDARD_ALIASES = {
    "F": "pyspark.sql.functions",
    "T": "pyspark.sql.types",
    "Window": "pyspark.sql.window",
}


class TypeTracker:
    """
    Track inferred types for variables and validate import aliases.
    
    Extends SymbolTable functionality with:
    - Import alias tracking (F -> pyspark.sql.functions)
    - Variable type inference (df -> DataFrame)
    - Shadowing detection
    """
    
    # Import aliases: "F" -> "pyspark.sql.functions"
    _import_aliases: ClassVar[dict[str, str]] = {}
    
    # Variable types: "scope::var" -> "DataFrame" | "str" | "Column" | ...
    _var_types: ClassVar[dict[str, str]] = {}
    
    # Shadowed variables: "scope::var" -> True (local assignment overrides)
    _shadowed: ClassVar[dict[str, bool]] = {}
    
    @classmethod
    def reset(cls) -> None:
        """Reset all type tracking. Call between workload parsings."""
        cls._import_aliases.clear()
        cls._var_types.clear()
        cls._shadowed.clear()
    
    # =========================================================================
    # Import Tracking
    # =========================================================================
    
    @classmethod
    def register_import(cls, alias: str, module: str) -> None:
        """
        Register an import alias.
        
        Examples:
            from pyspark.sql import functions as F
            -> register_import("F", "pyspark.sql.functions")
            
            import pyspark.sql.functions as F  
            -> register_import("F", "pyspark.sql.functions")
        """
        cls._import_aliases[alias] = module
    
    @classmethod
    def resolve_import(cls, alias: str) -> str | None:
        """
        Resolve an import alias to its module.
        
        Returns the module path, or None if not a known import.
        Falls back to standard conventions if not explicitly imported.
        """
        # Explicit import takes precedence
        if alias in cls._import_aliases:
            return cls._import_aliases[alias]
        
        # Fall back to standard conventions
        if alias in _STANDARD_ALIASES:
            return _STANDARD_ALIASES[alias]
        
        return None
    
    @classmethod
    def is_pyspark_functions(cls, alias: str) -> bool:
        """Check if alias refers to pyspark.sql.functions (e.g., F.col())."""
        module = cls.resolve_import(alias)
        return module == "pyspark.sql.functions"
    
    @classmethod
    def get_import_aliases(cls) -> dict[str, str]:
        """Get all registered import aliases (for debugging)."""
        return cls._import_aliases.copy()
    
    # =========================================================================
    # Type Tracking
    # =========================================================================
    
    @classmethod
    def register_type(cls, scope: str, var_name: str, inferred_type: str) -> None:
        """
        Register the inferred type for a variable.
        
        Args:
            scope: Function name or "<global>" for module level
            var_name: Variable name
            inferred_type: "DataFrame", "str", "int", "Column", "list", etc.
        """
        key = f"{scope}::{var_name}"
        cls._var_types[key] = inferred_type
    
    @classmethod
    def resolve_type(cls, scope: str, var_name: str) -> str | None:
        """
        Resolve the type of a variable.
        
        Checks current scope first, then global scope.
        
        Special cases:
        - "spark" -> "SparkSession" (by convention, unless shadowed)
        """
        # Check for shadowing first
        key = f"{scope}::{var_name}"
        if cls._shadowed.get(key):
            # Variable was reassigned locally, type unknown
            return cls._var_types.get(key)
        
        # Check current scope
        if key in cls._var_types:
            return cls._var_types[key]
        
        # Check global scope
        global_key = f"<global>::{var_name}"
        if global_key in cls._var_types:
            return cls._var_types[global_key]
        
        # Convention: "spark" is SparkSession (Databricks implicit)
        # Note: This is a limitation - we assume spark is not shadowed
        if var_name == "spark":
            return "SparkSession"
        
        return None
    
    @classmethod
    def mark_shadowed(cls, scope: str, var_name: str) -> None:
        """
        Mark a variable as shadowed (local assignment overrides).
        
        Example: spark = "something_else" in a function
        """
        key = f"{scope}::{var_name}"
        cls._shadowed[key] = True
    
    @classmethod
    def is_shadowed(cls, scope: str, var_name: str) -> bool:
        """Check if a variable is shadowed in this scope."""
        key = f"{scope}::{var_name}"
        return cls._shadowed.get(key, False)
    
    @classmethod
    def get_var_types(cls) -> dict[str, str]:
        """Get all registered variable types (for debugging)."""
        return cls._var_types.copy()


# Add TypeTracker to module exports
__all__ = ["SymbolTable", "ScopeInfo", "SourceBinding", "FunctionSignature", "TypeTracker"]
