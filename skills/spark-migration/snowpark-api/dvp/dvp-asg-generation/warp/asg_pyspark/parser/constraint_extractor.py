"""
Constraint Extractor - Extract column constraints and relationships from ASG.

This module extracts:
1. Column constraints from filter/where conditions
2. Column relationships from join conditions

These are useful for synthetic data generation to ensure test data
satisfies filter conditions and maintains referential integrity.
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING, Any

from warp_core.ir.pyspark_models import (
    ColumnConstraint,
    ColumnRelationship,
    ConstraintType,
    RelationshipType,
    SourceLocation,
)

if TYPE_CHECKING:
    from warp_core.ir.pyspark_models import ASG, TransformationNode


def extract_constraints(asg: "ASG") -> list[ColumnConstraint]:
    """
    Extract column constraints from filter/where transformations.
    
    Looks for patterns like:
    - F.col('x') == 'value'
    - F.col('x') > 5
    - F.col('x').isNotNull()
    - F.col('x').isin([...])
    - F.col('x').between(a, b)
    """
    constraints: list[ColumnConstraint] = []
    
    for tx in asg.transformations:
        if tx.operation not in ("filter", "where"):
            continue
        
        condition = tx.parameters.get("condition", "")
        if not condition:
            continue
        
        location = tx.location
        
        # Extract constraints from the condition string
        constraints.extend(_parse_condition(condition, tx.id, location))
    
    return constraints


def _parse_condition(condition: str, tx_id: str, location: SourceLocation | None) -> list[ColumnConstraint]:
    """Parse a filter condition string using Python AST for precise operator detection."""
    try:
        tree = ast.parse(condition, mode="eval")
    except SyntaxError:
        return []

    constraints: list[ColumnConstraint] = []
    _walk_ast_for_constraints(tree.body, constraints, tx_id, location)
    return constraints


_CMP_OP_MAP: dict[type, ConstraintType] = {
    ast.Eq: ConstraintType.EQUALS,
    ast.NotEq: ConstraintType.NOT_EQUALS,
    ast.Gt: ConstraintType.GREATER_THAN,
    ast.GtE: ConstraintType.GREATER_EQ,
    ast.Lt: ConstraintType.LESS_THAN,
    ast.LtE: ConstraintType.LESS_EQ,
}


def _node_to_str(node: ast.AST) -> str:
    """Convert an AST node back to a readable source string."""
    try:
        return ast.unparse(node)
    except Exception:
        return "?"


def _extract_col_name(node: ast.AST) -> str | None:
    """Extract column name from F.col('name') or col('name') patterns."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    args = node.args
    # F.col('x') -> Attribute(value=Name('F'), attr='col')
    if isinstance(func, ast.Attribute) and func.attr == "col" and args:
        if isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
            return args[0].value
    # col('x') -> Name('col')
    if isinstance(func, ast.Name) and func.id == "col" and args:
        if isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
            return args[0].value
    return None


def _extract_col_from_chain(node: ast.AST) -> str | None:
    """Extract column name from a method chain like F.col('x').isNotNull()."""
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            inner = node.func.value
            col = _extract_col_name(inner)
            if col:
                return col
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                return _extract_col_name(inner.func.value) or _extract_col_name(inner)
        return _extract_col_name(node)
    return None


def _walk_ast_for_constraints(
    node: ast.AST,
    out: list[ColumnConstraint],
    tx_id: str,
    location: SourceLocation | None,
) -> None:
    """Recursively walk the AST and extract constraints."""

    # --- Comparison: F.col('x') == value, F.col('x') >= value, etc. ---
    if isinstance(node, ast.Compare):
        left = node.left
        for op, comparator in zip(node.ops, node.comparators):
            ct = _CMP_OP_MAP.get(type(op))
            if ct is None:
                continue
            col = _extract_col_name(left) or _extract_col_from_chain(left)
            if col:
                val = _node_to_str(comparator)
                out.append(ColumnConstraint(
                    column_name=col,
                    constraint_type=ct,
                    value=val,
                    value_type=_infer_value_type(val),
                    source_transformation=tx_id,
                    location=location,
                ))
            col_r = _extract_col_name(comparator) or _extract_col_from_chain(comparator)
            if col_r and not col:
                val = _node_to_str(left)
                flipped = {
                    ConstraintType.GREATER_THAN: ConstraintType.LESS_THAN,
                    ConstraintType.GREATER_EQ: ConstraintType.LESS_EQ,
                    ConstraintType.LESS_THAN: ConstraintType.GREATER_THAN,
                    ConstraintType.LESS_EQ: ConstraintType.GREATER_EQ,
                }.get(ct, ct)
                out.append(ColumnConstraint(
                    column_name=col_r,
                    constraint_type=flipped,
                    value=val,
                    value_type=_infer_value_type(val),
                    source_transformation=tx_id,
                    location=location,
                ))
        return

    # --- Method calls: .isNotNull(), .isNull(), .isin(), .like(), .between() ---
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        attr = node.func.attr
        inner = node.func.value

        if attr == "isNotNull":
            col = _extract_col_name(inner)
            if col:
                out.append(ColumnConstraint(
                    column_name=col, constraint_type=ConstraintType.NOT_NULL,
                    value=None, value_type="boolean",
                    source_transformation=tx_id, location=location,
                ))
                return

        if attr == "isNull":
            col = _extract_col_name(inner)
            if col:
                out.append(ColumnConstraint(
                    column_name=col, constraint_type=ConstraintType.IS_NULL,
                    value=None, value_type="boolean",
                    source_transformation=tx_id, location=location,
                ))
                return

        if attr == "isin":
            col = _extract_col_name(inner)
            if col and node.args:
                values = [_node_to_str(a) for a in node.args]
                if len(values) == 1 and isinstance(node.args[0], ast.List):
                    values = [_node_to_str(e) for e in node.args[0].elts]
                out.append(ColumnConstraint(
                    column_name=col, constraint_type=ConstraintType.IN_LIST,
                    value=values, value_type="list",
                    source_transformation=tx_id, location=location,
                ))
                return

        if attr == "between":
            col = _extract_col_name(inner)
            if col and len(node.args) >= 2:
                low = _node_to_str(node.args[0])
                high = _node_to_str(node.args[1])
                out.append(ColumnConstraint(
                    column_name=col, constraint_type=ConstraintType.BETWEEN,
                    value=[low, high], value_type="range",
                    source_transformation=tx_id, location=location,
                ))
                return

        if attr == "like":
            col = _extract_col_name(inner)
            if col and node.args:
                pattern = _node_to_str(node.args[0]).strip("\'\"")
                out.append(ColumnConstraint(
                    column_name=col, constraint_type=ConstraintType.LIKE,
                    value=pattern, value_type="pattern",
                    source_transformation=tx_id, location=location,
                ))
                return

        if attr == "rlike":
            col = _extract_col_name(inner)
            if col and node.args:
                pattern = _node_to_str(node.args[0]).strip("\'\"")
                out.append(ColumnConstraint(
                    column_name=col, constraint_type=ConstraintType.RLIKE,
                    value=pattern, value_type="regex",
                    source_transformation=tx_id, location=location,
                ))
                return

    # --- Boolean operators: & | (BinOp) and `and` `or` (BoolOp) ---
    if isinstance(node, ast.BinOp):
        _walk_ast_for_constraints(node.left, out, tx_id, location)
        _walk_ast_for_constraints(node.right, out, tx_id, location)
        return

    if isinstance(node, ast.BoolOp):
        for val in node.values:
            _walk_ast_for_constraints(val, out, tx_id, location)
        return

    # --- Unary: ~ (negation) ---
    if isinstance(node, ast.UnaryOp):
        _walk_ast_for_constraints(node.operand, out, tx_id, location)
        return



def _infer_value_type(value: str) -> str:
    """Infer the type of a value from its string representation."""
    value = value.strip()
    if value.lower() in ("true", "false"):
        return "boolean"
    if value.lower() in ("none", "null"):
        return "null"
    try:
        int(value)
        return "integer"
    except ValueError:
        pass
    try:
        float(value)
        return "float"
    except ValueError:
        pass
    return "string"


def extract_relationships(asg: "ASG") -> list[ColumnRelationship]:
    """
    Extract column relationships from join transformations.
    
    Looks at join conditions to identify which columns are related
    across different data sources.
    """
    relationships: list[ColumnRelationship] = []
    _regex_fallback_events: list[dict] = []
    
    # Build a map of transformation inputs to trace back to sources
    tx_by_id = {tx.id: tx for tx in asg.transformations}
    source_ids = {src.id for src in asg.data_in}
    
    for tx in asg.transformations:
        if tx.operation != "join":
            continue
        
        join_condition = tx.parameters.get("join_condition", [])
        join_type = tx.parameters.get("join_type", "inner")
        
        # Get the two inputs to the join
        if len(tx.inputs) < 2:
            continue
        
        left_input = tx.inputs[0]
        right_input = tx.inputs[1]
        
        # Trace back to find original sources
        left_source = _trace_to_source(left_input, tx_by_id, source_ids)
        right_source = _trace_to_source(right_input, tx_by_id, source_ids)
        
        # Parse join condition to get column names
        if isinstance(join_condition, list):
            # Simple case: list of column names
            for col_name in join_condition:
                if isinstance(col_name, str):
                    relationships.append(ColumnRelationship(
                        left_column=col_name,
                        left_source=left_source or left_input,
                        right_column=col_name,
                        right_source=right_source or right_input,
                        relationship_type=RelationshipType.JOIN_KEY,
                        join_type=join_type,
                        source_transformation=tx.id,
                    ))
        elif isinstance(join_condition, str):
            join_cols, _join_used_fb = _extract_join_columns(join_condition)
            if _join_used_fb and join_cols:
                _regex_fallback_events.append({
                    "match_type": "JOIN_CONDITION",
                    "raw_snippet": join_condition[:200],
                    "identified_elements": {"pairs": [(l, r) for l, r in join_cols]},
                    "failure_reason": "Python ast parse failed",
                    "primary_parser": "ast",
                })
            for left_col, right_col in join_cols:
                relationships.append(ColumnRelationship(
                    left_column=left_col,
                    left_source=left_source or left_input,
                    right_column=right_col,
                    right_source=right_source or right_input,
                    relationship_type=RelationshipType.JOIN_KEY,
                    join_type=join_type,
                    source_transformation=tx.id,
                ))
    
    return relationships, _regex_fallback_events


def _extract_join_columns(condition: str) -> tuple[list[tuple[str, str]], bool]:
    """Extract (left_col, right_col) pairs from a join condition string.

    Uses Python ast as primary parser for expressions like
    df['col'] == other['col']; regex as fallback.

    Returns (pairs, used_regex_fallback).
    """
    stripped = condition.strip()
    if stripped.isidentifier():
        return [(stripped, stripped)], False

    try:
        import ast
        tree = ast.parse(stripped, mode="eval")
        pairs: list[tuple[str, str]] = []

        def _extract_compare(node: ast.AST) -> None:
            if isinstance(node, ast.BoolOp):
                for val in node.values:
                    _extract_compare(val)
                return
            if isinstance(node, ast.Compare) and len(node.comparators) == 1:
                left_col = _subscript_key(node.left)
                right_col = _subscript_key(node.comparators[0])
                if left_col and right_col:
                    pairs.append((left_col, right_col))

        def _subscript_key(node: ast.AST) -> str | None:
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                return str(node.slice.value)
            if isinstance(node, ast.Call):
                if hasattr(node.func, 'attr') and node.func.attr == 'col':
                    if node.args and isinstance(node.args[0], ast.Constant):
                        return str(node.args[0].value)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            return None

        _extract_compare(tree.body)
        if pairs:
            return pairs, False
    except (SyntaxError, ValueError):
        pass

    import re
    if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', stripped):
        return [(stripped, stripped)], True
    pairs_fb = []
    for m in re.finditer(
        r"\['([a-zA-Z_][a-zA-Z0-9_]*)'\]\s*==\s*[a-zA-Z_][a-zA-Z0-9_]*\['([a-zA-Z_][a-zA-Z0-9_]*)'\]",
        condition
    ):
        pairs_fb.append((m.group(1), m.group(2)))
    return pairs_fb, True


def _trace_to_source(node_id: str, tx_by_id: dict[str, "TransformationNode"], source_ids: set[str]) -> str | None:
    """Trace back from a node to find the original data source."""
    visited = set()
    
    def _trace(nid: str) -> str | None:
        if nid in visited:
            return None
        visited.add(nid)
        
        if nid in source_ids:
            return nid
        
        if nid in tx_by_id:
            tx = tx_by_id[nid]
            for inp in tx.inputs:
                result = _trace(inp)
                if result:
                    return result
        
        return None
    
    return _trace(node_id)


def enrich_asg_with_constraints(asg: "ASG") -> "ASG":
    """
    Enrich an ASG with extracted column constraints and relationships.
    
    This should be called after parsing and schema propagation.
    """
    asg.column_constraints = extract_constraints(asg)
    relationships, _rel_fb_events = extract_relationships(asg)
    asg.column_relationships = relationships
    for evt in _rel_fb_events:
        from warp_core.ir.pyspark_models import AnalysisWarning, WarningSeverity
        asg.warnings.append(AnalysisWarning(
            code="W_PAR_001",
            severity=WarningSeverity.WARNING,
            message="Regex fallback for join condition parsing",
            regex_evidence=evt,
        ))
    return asg
