"""
SQL Schema Extractor - Extract output schema from SQL queries.

Uses sqlglot for precise AST parsing, with regex fallback for cases
where SQL contains Python f-string variables or other unparseable content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import sqlglot
from sqlglot import exp


@dataclass
class SQLSchemaResult:
    """Result of SQL schema extraction."""
    
    output_columns: list[str] = field(default_factory=list)
    source_tables: list[str] = field(default_factory=list)
    method: Literal["sqlglot", "regex", "failed"] = "failed"
    error: str | None = None
    has_star: bool = False  # True if SELECT * was used


def extract_sql_schema(sql: str) -> SQLSchemaResult:
    """
    Extract output schema from SQL query.
    
    Tries sqlglot first for precise parsing, falls back to regex
    if SQL contains unparseable content (f-strings, invalid syntax).
    
    Args:
        sql: SQL query string (may contain f-string placeholders like {var})
        
    Returns:
        SQLSchemaResult with extracted columns and tables
    """
    # Clean the query (remove runtime: prefix if present)
    clean_sql = sql
    if clean_sql.startswith("runtime:"):
        clean_sql = clean_sql[8:]
    
    # Remove f-string prefix if present
    if clean_sql.startswith('f"') or clean_sql.startswith("f'"):
        clean_sql = clean_sql[2:-1]  # Remove f" and trailing "
    elif clean_sql.startswith('"') or clean_sql.startswith("'"):
        clean_sql = clean_sql[1:-1]
    
    # Convert escaped newlines/tabs to real ones
    clean_sql = clean_sql.replace('\\n', '\n').replace('\\t', '\t')
    
    # Strip leading/trailing whitespace
    clean_sql = clean_sql.strip()
    
    # Try sqlglot first
    result = _extract_with_sqlglot(clean_sql)
    if result.method == "sqlglot":
        return result
    
    # Fallback to regex
    return _extract_with_regex(clean_sql)


def _extract_with_sqlglot(sql: str) -> SQLSchemaResult:
    """Extract schema using sqlglot AST parser."""
    result = SQLSchemaResult()
    
    # Normalize Python f-string variables to valid SQL identifiers
    # {var_name} -> __FVAR_var_name__
    normalized_sql = re.sub(r'\{(\w+)\}', r'__FVAR_\1__', sql)
    
    try:
        # Parse with auto-detect dialect
        parsed = sqlglot.parse_one(normalized_sql)
        
        # For CTEs, find the outermost/final SELECT (not inside WITH)
        # The main statement is the parsed expression itself
        main_select = None
        if isinstance(parsed, exp.Select):
            main_select = parsed
        
        # Extract columns from main SELECT
        if main_select:
            for expr in main_select.expressions:
                col_name = _get_column_output_name(expr)
                if col_name:
                    # Restore f-string variables in column names
                    col_name = re.sub(r'__FVAR_(\w+)__', r'{\1}', col_name)
                    # Clean whitespace
                    col_name = col_name.strip()
                    if col_name == "*":
                        result.has_star = True
                    result.output_columns.append(col_name)
        
        # If main SELECT is *, try to get columns from CTE definition
        if result.has_star and result.output_columns == ["*"]:
            # Look for CTE definitions
            for cte in parsed.find_all(exp.CTE):
                cte_select = cte.find(exp.Select)
                if cte_select:
                    result.output_columns = []
                    result.has_star = False
                    for expr in cte_select.expressions:
                        col_name = _get_column_output_name(expr)
                        if col_name:
                            col_name = re.sub(r'__FVAR_(\w+)__', r'{\1}', col_name)
                            col_name = col_name.strip()
                            if col_name == "*":
                                result.has_star = True
                            result.output_columns.append(col_name)
                    break  # Use first CTE
        
        # Extract FROM tables (excluding CTE names)
        cte_names = {cte.alias for cte in parsed.find_all(exp.CTE)}
        for table in parsed.find_all(exp.Table):
            table_name = table.name
            # Restore f-string variables in table names
            table_name = re.sub(r'__FVAR_(\w+)__', r'{\1}', table_name)
            # Skip CTE references, only keep real tables
            if table_name and table_name not in result.source_tables and table_name not in cte_names:
                result.source_tables.append(table_name)
        
        result.method = "sqlglot"
        return result
        
    except Exception as e:
        result.error = str(e)
        result.method = "failed"
        return result


def _get_column_output_name(expr: exp.Expression) -> str | None:
    """Get the output name of a SELECT column expression."""
    # If aliased, use the alias
    if isinstance(expr, exp.Alias):
        return expr.alias
    
    # If it's a column, use the column name
    if isinstance(expr, exp.Column):
        return expr.name
    
    # If it's a star, return *
    if isinstance(expr, exp.Star):
        return "*"
    
    # For other expressions (functions, etc.), try to get alias or name
    if hasattr(expr, 'alias') and expr.alias:
        return expr.alias
    if hasattr(expr, 'name') and expr.name:
        return expr.name
    
    return None


def _extract_with_regex(sql: str) -> SQLSchemaResult:
    """Extract schema using regex (fallback method)."""
    result = SQLSchemaResult()
    result.method = "regex"
    
    # For CTEs (WITH ... AS), find the final SELECT after the CTE block
    # or the SELECT inside the main CTE if final is SELECT *
    sql_to_parse = sql
    
    # Check for CTE pattern
    cte_match = re.search(r'WITH\s+\w+\s+AS\s*\(', sql, re.IGNORECASE)
    if cte_match:
        # Find the final SELECT (after the CTE definitions)
        # Look for SELECT that's not inside parentheses (simplified)
        final_select = re.search(
            r'\)\s*SELECT\s+(DISTINCT\s+)?(.*?)\s+FROM',
            sql,
            re.IGNORECASE | re.DOTALL
        )
        if final_select:
            select_clause = final_select.group(2).strip()
            # If it's SELECT *, get columns from first CTE SELECT
            if select_clause == '*':
                inner_select = re.search(
                    r'AS\s*\(\s*SELECT\s+(DISTINCT\s+)?(.*?)\s+FROM',
                    sql,
                    re.IGNORECASE | re.DOTALL
                )
                if inner_select:
                    sql_to_parse = f"SELECT {inner_select.group(2)} FROM dummy"
            else:
                sql_to_parse = f"SELECT {select_clause} FROM dummy"
    
    # Find SELECT ... FROM
    select_match = re.search(
        r'SELECT\s+(DISTINCT\s+)?(.*?)\s+FROM',
        sql_to_parse,
        re.IGNORECASE | re.DOTALL
    )
    
    if not select_match:
        result.error = "Could not find SELECT ... FROM pattern"
        result.method = "failed"
        return result
    
    select_clause = select_match.group(2).strip()
    
    # Handle SELECT *
    if select_clause == '*':
        result.has_star = True
        result.output_columns = ['*']
    else:
        # Split by comma (careful with nested parens)
        columns = _split_select_columns(select_clause)
        
        # Extract alias or column name from each
        for col in columns:
            col_name = _extract_column_name(col)
            if col_name:
                if col_name == '*':
                    result.has_star = True
                result.output_columns.append(col_name)
    
    # Extract FROM tables (main table and JOINs)
    # Main table
    from_match = re.search(r'FROM\s+([^\s,\(\)]+)', sql, re.IGNORECASE)
    if from_match:
        result.source_tables.append(from_match.group(1))
    
    # JOINed tables
    join_matches = re.findall(r'JOIN\s+([^\s\(\)]+)', sql, re.IGNORECASE)
    for table in join_matches:
        if table not in result.source_tables:
            result.source_tables.append(table)
    
    return result


def _split_select_columns(select_clause: str) -> list[str]:
    """Split SELECT clause by commas, respecting nested parentheses."""
    columns = []
    depth = 0
    current = ''
    
    for char in select_clause + ',':
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif char == ',' and depth == 0:
            if current.strip():
                columns.append(current.strip())
            current = ''
            continue
        current += char
    
    return columns


def _extract_column_name(col_expr: str) -> str | None:
    """Extract the output name from a column expression."""
    col_expr = col_expr.strip()
    
    if not col_expr:
        return None
    
    # Check for AS alias (case insensitive)
    as_match = re.search(r'\s+AS\s+([^\s,\)]+)', col_expr, re.IGNORECASE)
    if as_match:
        return as_match.group(1)
    
    # No alias - get the last token (column name)
    # Handle table.column notation
    tokens = col_expr.split()
    if tokens:
        last_token = tokens[-1]
        # Get column name after dot if present
        return last_token.split('.')[-1]
    
    return None
