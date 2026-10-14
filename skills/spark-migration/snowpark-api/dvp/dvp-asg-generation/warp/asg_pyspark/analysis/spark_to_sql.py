"""
Spark Expression to SQL Converter.

This module provides AST-based conversion of PySpark expressions to SQL.
Used by both SinkInliner (CTE generation) and SnowflakeLifter (DT generation).

Features:
- Parses PySpark expressions using Python's AST module
- Converts F.col(), col() references to column names
- Translates operators (==, &, |) to SQL equivalents
- Handles null checks (.isNull(), .isNotNull())
- Supports aggregation, string, and date functions
"""

from __future__ import annotations

import ast
from typing import Any

# Module-level context for variable resolution
_current_scope: str | None = None


def set_expression_scope(scope: str | None) -> None:
    """Set the current scope for variable resolution in expressions."""
    global _current_scope
    _current_scope = scope


def load_window_specs_from_asg(asg: Any) -> None:
    """
    Load window spec definitions from ASG into SymbolTable.
    
    This must be called before SQL generation to ensure window specs
    (defined as variables in PySpark) can be resolved during expression conversion.
    
    Args:
        asg: The ASG object containing window_specs list
    """
    from warp_core.symbol_table import SymbolTable
    
    window_specs = getattr(asg, 'window_specs', None) or []
    for ws in window_specs:
        # Handle both Pydantic model and dict
        if hasattr(ws, 'scope'):
            scope = ws.scope
            var_name = ws.variable_name
            pyspark_expr = ws.pyspark_expr
        else:
            scope = ws.get('scope', '')
            var_name = ws.get('variable_name', '')
            pyspark_expr = ws.get('pyspark_expr', '')
        
        if var_name and pyspark_expr:
            SymbolTable.register_window_spec(scope, var_name, pyspark_expr)


def spark_expr_to_sql(expr: str) -> str | None:
    """
    Convert a PySpark expression string to SQL using AST parsing.

    Args:
        expr: A PySpark expression like "F.col('status') == 'completed'"

    Returns:
        SQL equivalent like "status = 'completed'", or None if parsing fails

    Examples:
        >>> spark_expr_to_sql("F.col('status') == 'completed'")
        "status = 'completed'"
        >>> spark_expr_to_sql("col('amount') > 1000")
        "amount > 1000"
        >>> spark_expr_to_sql("F.col('region').isNotNull()")
        "region IS NOT NULL"
    """
    if not expr:
        return None

    try:
        tree = ast.parse(expr, mode="eval")
        return _ast_to_sql(tree.body)
    except SyntaxError:
        return None


def extract_filter_condition(logic: str) -> str | None:
    """
    Extract and convert filter condition from PySpark logic string.

    Handles both method call and function call patterns:
    - "df.filter(F.col('x') > 5)" -> "x > 5"
    - "filter(F.col('x') > 5)" -> "x > 5"

    Args:
        logic: The full PySpark logic string

    Returns:
        SQL condition string, or None if extraction fails
    """
    if not logic:
        return None

    try:
        tree = ast.parse(logic, mode="eval")
        condition_node = _find_filter_arg(tree.body)
        if condition_node:
            return _ast_to_sql(condition_node)
    except SyntaxError:
        pass

    return None


def extract_withcolumn_expression(logic: str) -> tuple[str | None, str | None]:
    """
    Extract column name and expression from withColumn logic.

    Args:
        logic: PySpark logic like "withColumn('margin', F.col('price') - F.col('cost'))"

    Returns:
        Tuple of (column_name, sql_expression) or (None, None) if extraction fails
    """
    if not logic:
        return None, None

    try:
        tree = ast.parse(logic, mode="eval")
        return _find_withcolumn_args(tree.body)
    except SyntaxError:
        return None, None


# =============================================================================
# Internal AST Parsing Functions
# =============================================================================


def _find_filter_arg(node: Any) -> Any:
    """Find the argument to .filter() or .where() call."""
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in ("filter", "where") and node.args:
                return node.args[0]
            # Recurse into chained calls
            return _find_filter_arg(node.func.value)
        elif isinstance(node.func, ast.Name):
            # Handle function call pattern: filter(condition)
            if node.func.id in ("filter", "where") and node.args:
                return node.args[0]
    return None


def _find_withcolumn_args(node: Any) -> tuple[str | None, str | None]:
    """Find column name and expression from withColumn call."""
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "withColumn" and len(node.args) >= 2:
                # First arg is column name (string)
                col_name = None
                if isinstance(node.args[0], ast.Constant):
                    col_name = str(node.args[0].value)
                # Second arg is expression
                expr_sql = _ast_to_sql(node.args[1])
                return col_name, expr_sql
            # Recurse into chained calls
            return _find_withcolumn_args(node.func.value)
        elif isinstance(node.func, ast.Name):
            if node.func.id == "withColumn" and len(node.args) >= 2:
                col_name = None
                if isinstance(node.args[0], ast.Constant):
                    col_name = str(node.args[0].value)
                expr_sql = _ast_to_sql(node.args[1])
                return col_name, expr_sql
    return None, None


def _convert_window_arg(arg_node: Any) -> str:
    """
    Convert a window function argument (partitionBy/orderBy) to SQL using AST.
    
    In PySpark Window specs, string literals represent column names:
    - Window.partitionBy('store_id') -> PARTITION BY store_id
    - Window.orderBy(F.col('x').desc()) -> ORDER BY x DESC
    
    This function handles the AST node directly to properly distinguish
    between string literals (column names) and other expressions.
    
    Args:
        arg_node: AST node representing the argument
        
    Returns:
        SQL string for the column reference
    """
    # String literal -> column name (no quotes in SQL)
    if isinstance(arg_node, ast.Constant) and isinstance(arg_node.value, str):
        return arg_node.value
    
    # Any other expression (F.col('x').desc(), col('y'), etc.)
    return _ast_to_sql(arg_node)


def _normalize_func_name(name: str) -> str:
    """
    Normalize PySpark function names to SQL equivalents.
    
    Handles Python import aliases like:
        from pyspark.sql.functions import sum as _sum
        from pyspark.sql.functions import max as _max
    
    These are commonly used to avoid shadowing Python builtins.
    """
    # Map of aliased names to SQL function names
    ALIASES = {
        "_SUM": "SUM",
        "_MAX": "MAX", 
        "_MIN": "MIN",
        "_COUNT": "COUNT",
        "_AVG": "AVG",
    }
    upper = name.upper()
    return ALIASES.get(upper, upper)


def _ast_to_sql(node: Any) -> str:
    """Convert a PySpark AST expression to SQL."""
    if isinstance(node, ast.Call):
        func_name = _get_func_name(node)
        args_sql = [_ast_to_sql(arg) for arg in node.args]

        # Normalize function name (handle _sum -> SUM, etc.)
        func_upper = _normalize_func_name(func_name)

        # Column reference: F.col('x') or col('x') -> x (strip quotes)
        if func_upper == "COL":
            if args_sql:
                # Strip surrounding quotes from column name
                col_name = args_sql[0]
                if col_name.startswith("'") and col_name.endswith("'"):
                    return col_name[1:-1]
                return col_name
            return ""

        # Literal: lit(5) -> 5
        if func_upper == "LIT":
            return args_sql[0] if args_sql else "0"

        # Aggregation functions - arguments are column references, strip quotes
        if func_upper in ("SUM", "COUNT", "AVG", "MIN", "MAX", "FIRST", "LAST"):
            # Strip quotes from column references
            clean_args = [a[1:-1] if a.startswith("'") and a.endswith("'") else a for a in args_sql]
            return f"{func_upper}({', '.join(clean_args)})"

        # Utility functions
        if func_upper == "COALESCE":
            return f"COALESCE({', '.join(args_sql)})"

        # String functions
        if func_upper in ("LOWER", "UPPER", "TRIM", "LTRIM", "RTRIM"):
            return f"{func_upper}({', '.join(args_sql)})"
        if func_upper == "SUBSTRING":
            return f"SUBSTR({', '.join(args_sql)})"
        if func_upper == "CONCAT":
            return f"CONCAT({', '.join(args_sql)})"

        # Date functions
        if func_upper in ("YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND"):
            return f"{func_upper}({', '.join(args_sql)})"
        if func_upper == "TO_DATE":
            return f"TO_DATE({', '.join(args_sql)})"
        if func_upper == "DATEDIFF":
            return f"DATEDIFF({', '.join(args_sql)})"

        # Null handling - col('x').isNull() pattern
        if func_upper == "ISNULL":
            if isinstance(node.func, ast.Attribute):
                subject = _ast_to_sql(node.func.value)
                return f"{subject} IS NULL"
            return f"{args_sql[0]} IS NULL" if args_sql else "NULL"

        if func_upper == "ISNOTNULL":
            if isinstance(node.func, ast.Attribute):
                subject = _ast_to_sql(node.func.value)
                return f"{subject} IS NOT NULL"
            return f"{args_sql[0]} IS NOT NULL" if args_sql else "NOT NULL"

        # CASE WHEN
        if func_upper == "WHEN":
            if len(args_sql) >= 2:
                return f"CASE WHEN {args_sql[0]} THEN {args_sql[1]}"
            return f"WHEN({', '.join(args_sql)})"

        # .otherwise() - completes CASE WHEN...ELSE...END
        # Pattern: F.when(cond, val).otherwise(default) -> CASE WHEN cond THEN val ELSE default END
        if func_upper == "OTHERWISE":
            if isinstance(node.func, ast.Attribute):
                # Get the WHEN part (which generates "CASE WHEN ... THEN ...")
                when_sql = _ast_to_sql(node.func.value)
                # Get the ELSE value
                else_val = args_sql[0] if args_sql else "NULL"
                # Complete the CASE expression
                return f"{when_sql} ELSE {else_val} END"
            return f"OTHERWISE({', '.join(args_sql)})"

        # isin() method - col('country').isin(['MX', 'CR'])
        if func_upper == "ISIN":
            if isinstance(node.func, ast.Attribute):
                col_sql = _ast_to_sql(node.func.value)
                # args_sql[0] should be the list converted to SQL
                values_sql = args_sql[0] if args_sql else "()"
                return f"{col_sql} IN {values_sql}"
            return f"ISIN({', '.join(args_sql)})"

        # cast() method - col('amount').cast('double') -> CAST(amount AS DOUBLE)
        if func_upper == "CAST":
            if isinstance(node.func, ast.Attribute):
                # Get the column being cast (e.g., col('amount'))
                col_sql = _ast_to_sql(node.func.value)
                # Get the target type from args (e.g., 'double')
                if args_sql:
                    cast_type = args_sql[0].strip("'\"").upper()
                    # Map PySpark types to SQL types
                    type_map = {
                        "DOUBLE": "DOUBLE",
                        "FLOAT": "FLOAT",
                        "INT": "INTEGER",
                        "INTEGER": "INTEGER",
                        "LONG": "BIGINT",
                        "STRING": "VARCHAR",
                        "BOOLEAN": "BOOLEAN",
                        "DATE": "DATE",
                        "TIMESTAMP": "TIMESTAMP",
                        "DECIMAL": "NUMBER(18,2)",
                    }
                    sql_type = type_map.get(cast_type, cast_type)
                    return f"CAST({col_sql} AS {sql_type})"
            # Fallback for standalone cast()
            if args_sql:
                return f"CAST({args_sql[0]})"
            return "CAST(NULL)"

        # over() method - rank().over(window_spec) -> RANK() OVER (...)
        # Window functions: rank(), row_number(), dense_rank(), ntile(), lag(), lead()
        if func_upper == "OVER":
            if isinstance(node.func, ast.Attribute):
                # Get the window function (e.g., rank())
                window_func_sql = _ast_to_sql(node.func.value)
                # Get the window specification
                window_spec_sql = args_sql[0] if args_sql else ""
                return f"{window_func_sql} OVER ({window_spec_sql})"
            return f"OVER({', '.join(args_sql)})"

        # Window functions without arguments: rank(), row_number(), etc.
        if func_upper in ("RANK", "ROW_NUMBER", "DENSE_RANK", "NTILE", "CUME_DIST", "PERCENT_RANK"):
            return f"{func_upper}()"

        # Window functions with arguments: lag(col, offset), lead(col, offset)
        if func_upper in ("LAG", "LEAD", "FIRST_VALUE", "LAST_VALUE", "NTH_VALUE"):
            return f"{func_upper}({', '.join(args_sql)})"

        # Window.orderBy() / Window.partitionBy() - generate PARTITION BY / ORDER BY clauses
        if func_upper == "ORDERBY":
            # Convert arguments using AST - string literals become column names
            cols = [_convert_window_arg(arg) for arg in node.args]
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "Window":
                    # This is Window.orderBy(...) - generate ORDER BY clause
                    return f"ORDER BY {', '.join(cols)}"
            # Could also be chained: Window.partitionBy(...).orderBy(...)
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
                partition_sql = _ast_to_sql(node.func.value)
                return f"{partition_sql} ORDER BY {', '.join(cols)}"
            return f"ORDER BY {', '.join(cols)}"

        if func_upper == "PARTITIONBY":
            # Convert arguments using AST - string literals become column names
            cols = [_convert_window_arg(arg) for arg in node.args]
            # Handle empty partitionBy() - means partition over entire table
            if not cols:
                return ""  # Will result in just OVER () without PARTITION BY
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "Window":
                    # This is Window.partitionBy(...) - generate PARTITION BY clause
                    return f"PARTITION BY {', '.join(cols)}"
            return f"PARTITION BY {', '.join(cols)}"

        # .desc() / .asc() for column ordering - col('x').desc() -> x DESC
        if func_upper == "DESC":
            if isinstance(node.func, ast.Attribute):
                col_sql = _ast_to_sql(node.func.value)
                return f"{col_sql} DESC"
            return "DESC"

        if func_upper == "ASC":
            if isinstance(node.func, ast.Attribute):
                col_sql = _ast_to_sql(node.func.value)
                return f"{col_sql} ASC"
            return "ASC"

        # Unknown function - pass through uppercase
        return f"{func_upper}({', '.join(args_sql)})"

    elif isinstance(node, ast.Compare):
        # Handle comparisons: col('x') > 5
        left = _ast_to_sql(node.left)
        parts = [left]

        for op, comparator in zip(node.ops, node.comparators, strict=False):
            op_str = _compare_op_to_sql(op)
            right = _ast_to_sql(comparator)
            parts.append(f"{op_str} {right}")

        return " ".join(parts)

    elif isinstance(node, ast.BoolOp):
        # Handle AND/OR
        op_str = " AND " if isinstance(node.op, ast.And) else " OR "
        parts = [_ast_to_sql(v) for v in node.values]
        return f"({op_str.join(parts)})"

    elif isinstance(node, ast.UnaryOp):
        # Handle NOT, negation
        operand = _ast_to_sql(node.operand)
        if isinstance(node.op, ast.Not):
            return f"NOT ({operand})"
        elif isinstance(node.op, ast.USub):
            return f"-{operand}"
        return operand

    elif isinstance(node, ast.BinOp):
        # Handle arithmetic and logical operators
        left = _ast_to_sql(node.left)
        right = _ast_to_sql(node.right)

        # BitOr (|) -> OR
        if isinstance(node.op, ast.BitOr):
            if isinstance(node.right, ast.Constant) and node.right.value is None:
                left_col = _extract_column_from_comparison(node.left)
                if left_col:
                    return f"({left} OR {left_col} IS NULL)"
                return f"({left} OR NULL)"
            return f"({left} OR {right})"

        # BitAnd (&) -> AND
        if isinstance(node.op, ast.BitAnd):
            return f"({left} AND {right})"

        op_str = _binop_to_sql(node.op)
        return f"({left} {op_str} {right})"

    elif isinstance(node, ast.Attribute):
        # Handle method calls like col.isNull()
        if node.attr == "isNull":
            return f"{_ast_to_sql(node.value)} IS NULL"
        elif node.attr == "isNotNull":
            return f"{_ast_to_sql(node.value)} IS NOT NULL"
        elif node.attr == "desc":
            return f"{_ast_to_sql(node.value)} DESC"
        elif node.attr == "asc":
            return f"{_ast_to_sql(node.value)} ASC"
        return _ast_to_sql(node.value)

    elif isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            # Return string with quotes for SQL
            return f"'{node.value}'"
        elif node.value is None:
            return "NULL"
        return str(node.value)

    elif isinstance(node, ast.Name):
        # Try to resolve window spec variables
        if _current_scope:
            from warp_core.symbol_table import SymbolTable
            resolved = SymbolTable.resolve_window_spec(_current_scope, node.id)
            if resolved:
                return resolved
        return node.id

    elif isinstance(node, ast.List):
        # Fix 1: Literal Resolver - convert ast.List to SQL IN values
        values = []
        for item in node.elts:
            if isinstance(item, ast.Constant):
                if isinstance(item.value, str):
                    values.append(f"'{item.value}'")
                else:
                    values.append(str(item.value))
            else:
                values.append(_ast_to_sql(item))
        return f"({', '.join(values)})"

    return str(node)


def _get_func_name(call_node: Any) -> str:
    """Get function name from a Call AST node."""
    if isinstance(call_node.func, ast.Name):
        return call_node.func.id
    elif isinstance(call_node.func, ast.Attribute):
        return call_node.func.attr
    return ""


def _compare_op_to_sql(op: Any) -> str:
    """Convert Python comparison operator to SQL."""
    ops = {
        ast.Eq: "=",
        ast.NotEq: "!=",
        ast.Lt: "<",
        ast.LtE: "<=",
        ast.Gt: ">",
        ast.GtE: ">=",
        ast.Is: "IS",
        ast.IsNot: "IS NOT",
        ast.In: "IN",
        ast.NotIn: "NOT IN",
    }
    return ops.get(type(op), "=")


def _binop_to_sql(op: Any) -> str:
    """Convert Python binary operator to SQL."""
    ops = {
        ast.Add: "+",
        ast.Sub: "-",
        ast.Mult: "*",
        ast.Div: "/",
        ast.Mod: "%",
    }
    return ops.get(type(op), "+")


def _extract_column_from_comparison(node: Any) -> str | None:
    """Extract the column name from a comparison node."""
    if isinstance(node, ast.Compare):
        return _extract_column_name(node.left)
    elif isinstance(node, ast.Call):
        return _extract_column_name(node)
    return None


def _extract_column_name(node: Any) -> str | None:
    """Extract column name from a col() call."""
    if isinstance(node, ast.Call):
        func_name = _get_func_name(node)
        if func_name.lower() == "col" and node.args:
            if isinstance(node.args[0], ast.Constant):
                return str(node.args[0].value)
    return None


def extract_column_refs(expr: str) -> list[str]:
    """
    Extract all column references from a PySpark expression using AST.
    
    Handles:
    - col('name'), F.col('name'), SprkF.col('name')
    - Direct column references in aggregations like sum('col')
    
    Args:
        expr: PySpark expression string
        
    Returns:
        List of column names referenced
        
    Examples:
        >>> extract_column_refs("F.col('amount') > F.col('threshold')")
        ['amount', 'threshold']
        >>> extract_column_refs("sum('sales')")
        ['sales']
    """
    if not expr:
        return []
    
    try:
        tree = ast.parse(expr, mode="eval")
        refs: list[str] = []
        _collect_column_refs(tree.body, refs)
        return refs
    except SyntaxError:
        return []


def _collect_column_refs(node: Any, refs: list[str]) -> None:
    """Recursively collect column references from AST."""
    if isinstance(node, ast.Call):
        func_name = _get_func_name(node).lower()
        
        # col('name'), F.col('name')
        if func_name == "col" and node.args:
            if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                refs.append(node.args[0].value)
        
        # Aggregation functions with column name as string: sum('col'), avg('col')
        if func_name in ("sum", "count", "avg", "min", "max", "first", "last"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    refs.append(arg.value)
        
        # Recurse into arguments
        for arg in node.args:
            _collect_column_refs(arg, refs)
        
        # Recurse into the function value (for chained calls)
        if isinstance(node.func, ast.Attribute):
            _collect_column_refs(node.func.value, refs)
    
    elif isinstance(node, ast.BinOp):
        _collect_column_refs(node.left, refs)
        _collect_column_refs(node.right, refs)
    
    elif isinstance(node, ast.Compare):
        _collect_column_refs(node.left, refs)
        for comp in node.comparators:
            _collect_column_refs(comp, refs)
    
    elif isinstance(node, ast.BoolOp):
        for val in node.values:
            _collect_column_refs(val, refs)
    
    elif isinstance(node, ast.UnaryOp):
        _collect_column_refs(node.operand, refs)


def extract_cast_type(expr: str) -> str | None:
    """
    Extract the type from a .cast() expression using AST.
    
    Args:
        expr: PySpark expression like "col('x').cast('double')"
        
    Returns:
        The cast type (e.g., 'double') or None if not a cast expression
    """
    if not expr:
        return None
    
    try:
        tree = ast.parse(expr, mode="eval")
        return _find_cast_type(tree.body)
    except SyntaxError:
        return None


def _find_cast_type(node: Any) -> str | None:
    """Find .cast() in AST and extract the type."""
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "cast":
            if node.args and isinstance(node.args[0], ast.Constant):
                return str(node.args[0].value)
        # Recurse into chained calls
        if isinstance(node.func, ast.Attribute):
            return _find_cast_type(node.func.value)
    return None


def detect_expression_type(expr: str) -> str | None:
    """
    Detect the inferred type of a PySpark expression using AST analysis.
    
    Returns:
        'NUMERIC' for arithmetic operations
        'TEXT' for string functions
        'BOOLEAN' for boolean comparisons
        'TIMESTAMP' for date/time functions
        None if type cannot be determined
    """
    if not expr:
        return None
    
    try:
        tree = ast.parse(expr, mode="eval")
        return _infer_expression_type(tree.body)
    except SyntaxError:
        return None


def _infer_expression_type(node: Any) -> str | None:
    """Infer expression type from AST node."""
    if isinstance(node, ast.BinOp):
        # Arithmetic operations -> NUMERIC
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)):
            return "NUMERIC"
    
    elif isinstance(node, ast.Compare):
        # Comparisons -> BOOLEAN
        return "BOOLEAN"
    
    elif isinstance(node, ast.BoolOp):
        # Boolean operations -> BOOLEAN
        return "BOOLEAN"
    
    elif isinstance(node, ast.Constant):
        # Literal values
        if isinstance(node.value, str):
            return "TEXT"
        elif isinstance(node.value, bool):
            return "BOOLEAN"
        elif isinstance(node.value, (int, float)):
            return "NUMERIC"
    
    elif isinstance(node, ast.Call):
        func_name = _get_func_name(node).lower()
        
        # Use central function registry for type inference
        from warp_core.spark_functions import (
            returns_numeric, returns_integer, returns_string,
            returns_date, returns_timestamp, returns_boolean,
            get_return_type, ReturnType
        )
        
        fn_lower = func_name.lower()
        
        # Check return type from registry
        ret_type = get_return_type(fn_lower)
        if ret_type == ReturnType.NUMERIC or ret_type == ReturnType.INTEGER:
            return "NUMERIC"
        elif ret_type == ReturnType.STRING:
            return "TEXT"
        elif ret_type == ReturnType.DATE:
            return "DATE"
        elif ret_type == ReturnType.TIMESTAMP:
            return "TIMESTAMP"
        elif ret_type == ReturnType.ARRAY:
            return "ARRAY"
        elif ret_type == ReturnType.BOOLEAN:
            return "BOOLEAN"
        
        # Literal functions
        if func_name == "lit":
            # lit('hello') -> TEXT, lit(123) -> NUMERIC
            if node.args:
                return _infer_expression_type(node.args[0])
        
        # Recurse for chained calls
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr.lower()
            
            # Boolean methods
            if attr in ("isnull", "isnotnull", "isnan", "between", "like", "rlike",
                        "startswith", "endswith", "contains"):
                return "BOOLEAN"
            
            # when/otherwise - infer from result values
            if attr == "otherwise":
                # .otherwise('value') - check the argument type
                if node.args:
                    arg_type = _infer_expression_type(node.args[0])
                    if arg_type:
                        return arg_type
                # Also check the when() chain
                return _infer_expression_type(node.func.value)
            
            if attr == "when":
                # .when(condition, value) - check the value (second arg)
                if len(node.args) >= 2:
                    return _infer_expression_type(node.args[1])
            
            # F.when(condition, value) - standalone when
            if func_name == "when":
                if len(node.args) >= 2:
                    return _infer_expression_type(node.args[1])
            
            # Cast returns the target type (handled by extract_cast_type)
            if attr == "cast":
                return None  # Let extract_cast_type handle this
            
            # Recurse for other chained calls
            return _infer_expression_type(node.func.value)
    
    return None
