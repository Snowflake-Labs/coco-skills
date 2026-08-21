"""
Astroid-based inference engine for resolving variable values and definitions.

This module uses astroid's inference capabilities to:
1. Resolve constant values (source_table -> "raw_transactions")
2. Expand variable definitions (window_spec -> Window.partitionBy(...))
3. Track DataFrame lineage through function calls

Key differences from stdlib ast:
- astroid builds an inference graph that allows "time travel" to see variable values
- Can resolve variables defined many lines earlier
- Handles cross-scope references (function returns, class methods)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import astroid
from astroid import nodes


@dataclass
class InferredValue:
    """Result of inferring a variable's value."""

    name: str
    value: str | None  # The actual value (for constants like strings)
    definition: str | None  # The full definition as code (for complex expressions)
    line_number: int | None
    confidence: str  # "high", "medium", "low"

    @property
    def is_constant(self) -> bool:
        """Check if this is a simple constant value."""
        return self.value is not None

    @property
    def is_window_spec(self) -> bool:
        """Check if this looks like a Window specification."""
        return self.definition is not None and "Window." in self.definition


class AstroidInferenceEngine:
    """
    Engine for resolving variable values using astroid.

    Usage:
        engine = AstroidInferenceEngine(source_code)

        # Resolve a string constant
        result = engine.infer_value("source_table")
        # -> InferredValue(name="source_table", value="raw_transactions", ...)

        # Resolve a Window definition
        result = engine.infer_value("window_spec")
        # -> InferredValue(name="window_spec", definition="Window.partitionBy(...)", ...)
    """

    def __init__(self, source_code: str) -> None:
        """Initialize with Python source code."""
        self._source = source_code
        self._root: nodes.Module | None = None
        self._variable_definitions: dict[str, nodes.AssignName] = {}
        self._parse()

    def _parse(self) -> None:
        """Parse the source code and build variable index."""
        try:
            self._root = astroid.parse(self._source)
            self._build_variable_index()
        except astroid.exceptions.AstroidSyntaxError:
            # Fall back to empty module if parsing fails
            self._root = None

    def _build_variable_index(self) -> None:
        """Build an index of all variable assignments."""
        if not self._root:
            return

        for node in self._root.nodes_of_class(nodes.AssignName):
            # Store the first definition of each variable
            if node.name not in self._variable_definitions:
                self._variable_definitions[node.name] = node

    def infer_value(self, variable_name: str) -> InferredValue | None:
        """
        Infer the value of a variable.

        Returns:
            InferredValue with the resolved value/definition, or None if not found.
        """
        if not self._root:
            return None

        # Find usages of this variable and try to infer
        for node in self._root.nodes_of_class(nodes.Name):
            if node.name == variable_name:
                return self._infer_from_name(node)

        # If no usage found, try to get from definition directly
        return self._get_definition(variable_name)

    def _infer_from_name(self, name_node: nodes.Name) -> InferredValue | None:
        """Infer value from a Name node using astroid's inference."""
        try:
            inferred = list(name_node.inferred())

            for val in inferred:
                # Handle constant strings
                if isinstance(val, nodes.Const) and isinstance(val.value, str):
                    return InferredValue(
                        name=name_node.name,
                        value=val.value,
                        definition=None,
                        line_number=val.lineno,
                        confidence="high",
                    )

                # Handle other constants
                if isinstance(val, nodes.Const):
                    return InferredValue(
                        name=name_node.name,
                        value=str(val.value),
                        definition=None,
                        line_number=val.lineno,
                        confidence="high",
                    )
        except (astroid.exceptions.InferenceError, StopIteration):
            pass

        # Fall back to looking up the definition
        return self._get_definition(name_node.name)

    def _get_definition(self, variable_name: str) -> InferredValue | None:
        """Get the definition of a variable by looking it up."""
        if variable_name not in self._variable_definitions:
            return None

        assign_name = self._variable_definitions[variable_name]
        assign = assign_name.parent

        if isinstance(assign, nodes.Assign):
            definition_str = assign.value.as_string()
            return InferredValue(
                name=variable_name,
                value=None,
                definition=definition_str,
                line_number=assign.lineno,
                confidence="medium",
            )

        return None

    def get_all_variable_names(self) -> list[str]:
        """Get all variable names defined in the code."""
        return list(self._variable_definitions.keys())

    def resolve_window_spec(self, variable_name: str) -> dict[str, Any] | None:
        """
        Resolve a Window specification to structured form.

        Returns:
            {
                "partition_by": ["column1", "column2"],
                "order_by": [{"column": "col", "direction": "DESC"}]
            }
        """
        inferred = self.infer_value(variable_name)

        if not inferred or not inferred.definition:
            return None

        # Parse the definition to extract Window components
        return self._parse_window_definition(inferred.definition)

    def _parse_window_definition(self, definition: str) -> dict[str, Any] | None:
        """Parse a Window definition string into structured form."""
        if "Window." not in definition:
            return None

        result: dict[str, Any] = {"partition_by": [], "order_by": []}

        try:
            # Parse the definition as an expression
            expr_module = astroid.parse(definition)
            expr = expr_module.body[0].value if expr_module.body else None

            if not expr:
                return None

            # Walk the method chain to extract partitionBy and orderBy
            self._extract_window_components(expr, result)

            return result if result["partition_by"] or result["order_by"] else None

        except Exception:
            return None

    def _extract_window_components(self, node: nodes.NodeNG, result: dict[str, Any]) -> None:
        """Recursively extract Window components from a method chain."""
        if isinstance(node, nodes.Call):
            func = node.func

            if isinstance(func, nodes.Attribute):
                method_name = func.attrname

                if method_name == "partitionBy":
                    result["partition_by"] = self._extract_column_args(node)
                elif method_name == "orderBy":
                    result["order_by"] = self._extract_order_args(node)

                # Continue traversing the chain
                self._extract_window_components(func.expr, result)

    def _extract_column_args(self, call_node: nodes.Call) -> list[str]:
        """Extract column names from a function call's arguments."""
        columns = []

        for arg in call_node.args:
            if isinstance(arg, nodes.Const) and isinstance(arg.value, str):
                columns.append(arg.value)
            elif isinstance(arg, nodes.Call):
                # Handle col("column_name")
                col_name = self._extract_col_name(arg)
                if col_name:
                    columns.append(col_name)

        return columns

    def _extract_order_args(self, call_node: nodes.Call) -> list[dict[str, str]]:
        """Extract order by specifications."""
        order_specs = []

        for arg in call_node.args:
            if isinstance(arg, nodes.Const) and isinstance(arg.value, str):
                order_specs.append({"column": arg.value, "direction": "ASC"})
            elif isinstance(arg, nodes.Call):
                # Handle col("column").desc() or col("column").asc()
                order_spec = self._extract_order_spec(arg)
                if order_spec:
                    order_specs.append(order_spec)

        return order_specs

    def _extract_order_spec(self, call_node: nodes.Call) -> dict[str, str] | None:
        """Extract a single order specification like col("x").desc()."""
        if isinstance(call_node.func, nodes.Attribute):
            direction = call_node.func.attrname.upper()
            if direction not in ("ASC", "DESC"):
                direction = "ASC"

            # Get the column from the inner call
            inner = call_node.func.expr
            if isinstance(inner, nodes.Call):
                col_name = self._extract_col_name(inner)
                if col_name:
                    return {"column": col_name, "direction": direction}

        return None

    def _extract_col_name(self, call_node: nodes.Call) -> str | None:
        """Extract column name from col("column_name")."""
        if isinstance(call_node.func, nodes.Name) and call_node.func.name == "col":
            if call_node.args and isinstance(call_node.args[0], nodes.Const):
                return call_node.args[0].value
        return None

    def resolve_table_name(self, variable_name: str) -> str | None:
        """
        Resolve a table name variable to its string value.

        For: source_table = "raw_transactions"
        Returns: "raw_transactions"
        """
        inferred = self.infer_value(variable_name)

        if inferred and inferred.is_constant:
            return inferred.value

        return None


def create_inference_engine(source_code: str) -> AstroidInferenceEngine:
    """Factory function to create an inference engine."""
    return AstroidInferenceEngine(source_code)
