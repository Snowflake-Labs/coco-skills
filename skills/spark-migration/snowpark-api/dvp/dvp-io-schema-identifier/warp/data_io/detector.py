"""
Data I/O Detector - Extract data inputs/outputs with schema inference from ASG JSON.

Uses a 6-phase type inference pipeline (evidence before patterns):
0. ASG seed (high-confidence inferred_columns)
1. Code analysis (from inferred_output fields)
1b. Agnostic ASG types (from asg_agnostic.json)
1c. StructField explicit schemas
3. Filter condition analysis — evidence (col == 'value' -> STRING)
5. Function usage inference — evidence (F.sum(col) -> DECIMAL)
4. First join propagation (spreads evidence types)
2. Pattern-based inference — fallback (_id -> INT, _dt -> DATE, etc.)
6. Second join propagation (spreads pattern types)
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from warp_core.diagnostics import (
    DiagnosticIssue,
    DiagnosticReport,
    Severity,
    IssueCategory,
    SchemaIssueCode,
)
from warp_core.pandas_functions import (
    AGGREGATION_FUNCS as PD_AGG_FUNCS,
    DATE_FUNCS as PD_DATE_FUNCS,
    MATH_FUNCS as PD_MATH_FUNCS,
    STRING_FUNCS as PD_STRING_FUNCS,
)
from warp_core.spark_functions import (
    STRING_OP_FUNCS,
    DATE_TIME_FUNCS,
    NUMERIC_OP_FUNCS,
    AGGREGATION_PATTERN,
)


# Pattern-based type inference rules (by column name).
# Only generic patterns that apply to any PySpark/data workload.
TYPE_PATTERNS: list[tuple[str, str]] = [
    # IDs and keys
    (r'(?:^|_)id$', 'INT'), (r'_id$', 'INT'), (r'^id$', 'INT'),
    (r'_key$', 'INT'), (r'_no$', 'INT'), (r'_nbr$', 'INT'),
    (r'_num$', 'INT'), (r'_count$', 'INT'), (r'^count$', 'INT'),
    (r'_qty$', 'INT'), (r'_rank$', 'INT'), (r'_idx$', 'INT'),
    (r'_number$', 'INT'),
    # Dates and timestamps
    (r'_dt$', 'DATE'), (r'_date$', 'DATE'), (r'^date$', 'DATE'),
    (r'^date_of_', 'DATE'),
    (r'_timestamp$', 'TIMESTAMP'), (r'_ts$', 'TIMESTAMP'), (r'_time$', 'TIMESTAMP'),
    (r'created_at$', 'TIMESTAMP'), (r'updated_at$', 'TIMESTAMP'),
    (r'_month$', 'INT'), (r'^month$', 'INT'), (r'_year$', 'INT'), (r'^year$', 'INT'),
    (r'_day$', 'INT'), (r'^day$', 'INT'), (r'_week$', 'INT'),
    # Booleans
    (r'^is_', 'BOOLEAN'), (r'^has_', 'BOOLEAN'), (r'^can_', 'BOOLEAN'),
    (r'_flag$', 'BOOLEAN'), (r'_ind$', 'BOOLEAN'),
    # Decimals and numerics
    (r'_amt$', 'DECIMAL'), (r'_amount$', 'DECIMAL'), (r'_price$', 'DECIMAL'),
    (r'_cost$', 'DECIMAL'), (r'_rate$', 'DECIMAL'), (r'_pct$', 'DECIMAL'),
    (r'_percent$', 'DECIMAL'), (r'_ratio$', 'DECIMAL'), (r'_score$', 'DECIMAL'),
    (r'_weight$', 'DECIMAL'), (r'_total$', 'DECIMAL'), (r'_sum$', 'DECIMAL'),
    (r'_average$', 'DECIMAL'), (r'_avg$', 'DECIMAL'), (r'_mean$', 'DECIMAL'),
    (r'avg$', 'DECIMAL'),
    (r'_min$', 'DECIMAL'), (r'_max$', 'DECIMAL'), (r'_median$', 'DECIMAL'),
    (r'_variance$', 'DECIMAL'), (r'_stddev$', 'DECIMAL'),
    (r'_per$', 'DECIMAL'), (r'_value$', 'DECIMAL'),
    (r'_rating$', 'DECIMAL'), (r'_threshold$', 'DECIMAL'),
    (r'_target$', 'DECIMAL'), (r'_goal$', 'DECIMAL'), (r'_benchmark$', 'DECIMAL'),
    (r'^score$', 'DECIMAL'), (r'score$', 'DECIMAL'),
    (r'rate$', 'DECIMAL'), (r'rating$', 'DECIMAL'), (r'weight$', 'DECIMAL'),
    # Strings
    (r'_nm$', 'STRING'), (r'_name$', 'STRING'), (r'^name$', 'STRING'),
    (r'_desc$', 'STRING'), (r'_cd$', 'STRING'), (r'_code$', 'STRING'),
    (r'_type$', 'STRING'), (r'_status$', 'STRING'), (r'_category$', 'STRING'),
    (r'_text$', 'STRING'), (r'_address$', 'STRING'), (r'_email$', 'STRING'),
    (r'_phone$', 'STRING'), (r'_url$', 'STRING'), (r'_path$', 'STRING'),
    (r'_label$', 'STRING'), (r'_title$', 'STRING'), (r'_message$', 'STRING'),
    (r'_area$', 'STRING'), (r'_region$', 'STRING'), (r'_zone$', 'STRING'),
    (r'_fld$', 'STRING'), (r'_field$', 'STRING'),
    (r'_user$', 'STRING'), (r'_users$', 'STRING'),
    (r'_group\d*$', 'STRING'), (r'_system$', 'STRING'),
    (r'_grade$', 'STRING'), (r'_tier$', 'STRING'),
    (r'_level$', 'STRING'), (r'_bucket$', 'STRING'),
    (r'_option$', 'STRING'),
    (r'status$', 'STRING'), (r'code$', 'STRING'),
    (r'^value$', 'STRING'), (r'^content$', 'STRING'),
    (r'^problem$', 'STRING'), (r'^issue$', 'STRING'),
    (r'^firstname$', 'STRING'), (r'^lastname$', 'STRING'),
    (r'^countrycode$', 'STRING'),
    (r'number\d*$', 'INT'),
]

STRUCTFIELD_TYPE_MAP: dict[str, str] = {
    "IntegerType": "INT", "StringType": "STRING", "FloatType": "DECIMAL",
    "DecimalType": "DECIMAL", "DoubleType": "DECIMAL", "LongType": "INT",
    "DateType": "DATE", "TimestampType": "TIMESTAMP", "BooleanType": "BOOLEAN",
}

CAST_TYPE_MAP: dict[str, str] = {
    'int': 'INT', 'integer': 'INT', 'integertype': 'INT', 'longtype': 'INT',
    'long': 'INT', 'double': 'DECIMAL', 'float': 'DECIMAL', 'decimal': 'DECIMAL',
    'doubletype': 'DECIMAL', 'floattype': 'DECIMAL', 'decimaltype': 'DECIMAL',
    'string': 'STRING', 'stringtype': 'STRING',
    'boolean': 'BOOLEAN', 'booleantype': 'BOOLEAN',
    'date': 'DATE', 'datetype': 'DATE',
    'timestamp': 'TIMESTAMP', 'timestamptype': 'TIMESTAMP',
}

# =============================================================================
# AST-based expression parsing (replaces regex for PySpark expression strings)
# =============================================================================

def _safe_parse_expr(expr: str) -> ast.AST | None:
    """Parse a PySpark expression string into a walkable AST node.

    Tries eval mode (single expression) first, then falls back to exec mode
    for multiline logic strings (e.g. concatenated transformation logic).
    """
    if not isinstance(expr, str):
        return None
    text = expr.strip()
    if not text:
        return None
    try:
        return ast.parse(text, mode="eval").body
    except (SyntaxError, ValueError):
        pass
    try:
        tree = ast.parse(text, mode="exec")
        if tree.body:
            return tree
    except (SyntaxError, ValueError):
        pass
    return None


def _extract_col_names_from_ast(node: ast.AST) -> list[str]:
    """
    Walk an AST and extract all column names from F.col('x') / col('x') patterns.

    Handles:
        F.col('name'), col('name'), F.col("name")
    """
    names: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        is_col_call = (
            (isinstance(func, ast.Attribute) and func.attr == "col")
            or (isinstance(func, ast.Name) and func.id == "col")
        )
        if is_col_call and child.args:
            first_arg = child.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                names.append(first_arg.value)
    return names


def _extract_alias_from_ast(node: ast.AST) -> str | None:
    """Extract the alias name from .alias('x') in an AST."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr == "alias" and child.args:
                arg = child.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    return arg.value
    return None


def _find_comparisons(node: ast.AST) -> list[tuple[str, ast.expr]]:
    """
    Find comparisons like F.col('x') == literal or F.col('x') > literal.

    Returns list of (column_name, comparator_node).
    """
    results: list[tuple[str, ast.expr]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Compare):
            continue
        col_names = _extract_col_names_from_ast(child.left)
        if col_names and child.comparators:
            for cname in col_names:
                results.append((cname, child.comparators[0]))
    return results


def _find_method_calls_on_col(node: ast.AST, method_names: set[str]) -> list[tuple[str, str]]:
    """
    Find F.col('x').method(...) patterns and return (column_name, method_name).
    """
    results: list[tuple[str, str]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Attribute) and child.func.attr in method_names:
            col_names = _extract_col_names_from_ast(child.func.value)
            if col_names:
                for cname in col_names:
                    results.append((cname, child.func.attr))
    return results


def _find_pyspark_func_wrapping_col(
    node: ast.AST, func_names: set[str]
) -> list[tuple[str, str]]:
    """
    Find F.func(F.col('x')) patterns and return (column_name, function_name).

    Covers: F.upper(F.col('x')), F.sum(F.col('x')), etc.
    """
    results: list[tuple[str, str]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and func.attr in func_names:
            for arg in child.args:
                for cname in _extract_col_names_from_ast(arg):
                    results.append((cname, func.attr))
    return results


def _find_cast_calls(node: ast.AST) -> list[tuple[str, str]]:
    """
    Find F.col('x').cast('type') patterns and return (column_name, cast_type).
    """
    results: list[tuple[str, str]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Attribute) and child.func.attr == "cast":
            col_names = _extract_col_names_from_ast(child.func.value)
            if col_names and child.args:
                arg = child.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    for cname in col_names:
                        results.append((cname, arg.value))
    return results


def _find_arithmetic_on_col(node: ast.AST) -> list[str]:
    """Find F.col('x') used in arithmetic (+, -, *, /)."""
    results: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.BinOp) and isinstance(
            child.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            for cname in _extract_col_names_from_ast(child):
                if cname not in results:
                    results.append(cname)
    return results


def _is_valid_column_name(name: str) -> bool:
    """Filter out join conditions and expressions mistakenly captured as columns."""
    if not name:
        return False
    # Filter join conditions (contain ==, !=, etc.)
    if "==" in name or "!=" in name or " == " in name:
        return False
    # Filter array subscript comparisons
    if "['" in name and "']" in name and "==" in name:
        return False
    # Filter general comparison expressions
    if any(op in name for op in [' > ', ' < ', ' >= ', ' <= ']):
        return False
    return True


INTERNAL_TYPE_MAP: dict[str, str] = {
    'L_TEXT': 'STRING', 'L_INT': 'INT', 'L_NUMBER': 'INT',
    'L_DECIMAL': 'DECIMAL', 'NUMERIC': 'DECIMAL',
    'L_BOOLEAN': 'BOOLEAN', 'L_DATE': 'DATE',
    'L_TIMESTAMP': 'TIMESTAMP', 'L_DATETIME': 'TIMESTAMP', 'TIMESTAMP': 'TIMESTAMP',
}


class InferenceConfidence:
    """Confidence levels for type inference."""
    EVIDENCE = "evidence"  # Inferred from code analysis (casts, filters, functions)
    PATTERN = "pattern"    # Inferred from column name patterns
    UNKNOWN = "unknown"    # Could not infer type


@dataclass
class Column:
    """A column with name and inferred type."""
    name: str
    type: str = "UNKNOWN"
    confidence: str = "unknown"  # evidence, pattern, unknown, ambiguous
    default_value: str | None = None
    reason: str | None = None
    potential_sources: list[str] | None = None
    
    def to_dict(self) -> dict[str, Any]:
        d = {"name": self.name, "type": self.type, "confidence": self.confidence}
        if self.default_value is not None:
            d["default_value"] = self.default_value
        if self.confidence == "ambiguous":
            if self.reason:
                d["reason"] = self.reason
            if self.potential_sources:
                d["potential_sources"] = self.potential_sources
        return d
    
    def __repr__(self) -> str:
        return f"Column(name=\'{self.name}\', type=\'{self.type}\', confidence=\'{self.confidence}\')"


@dataclass
class DataIO:
    """A data input or output with schema."""
    name: str
    full_name: str
    source: str
    type: str  # file, table, database
    format: str  # csv, parquet, jdbc, etc.
    detection: str  # static, dynamic
    role: str  # input, output
    path: str
    columns: list[Column] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "full_name": self.full_name,
            "source": self.source,
            "type": self.type,
            "format": self.format,
            "detection": self.detection,
            "role": self.role,
            "path": self.path,
            "columns": [c.to_dict() for c in self.columns],
        }


class DataIODetector:
    """Detect data I/O from ASG JSON files with schema inference."""
    
    def __init__(self) -> None:
        self._data_io: list[DataIO] = []
        self._stats: dict[str, Any] = {}
        self._issues: DiagnosticReport = DiagnosticReport(tool_name="data_io")
    
    @property
    def data_io(self) -> list[DataIO]:
        return self._data_io
    
    @property
    def stats(self) -> dict[str, Any]:
        return self._stats
    
    @property
    def issues(self) -> DiagnosticReport:
        """Get diagnostic issues from last detection run."""
        return self._issues
    
    def _add_issue(
        self,
        code: str,
        severity: Severity,
        category: IssueCategory,
        message: str,
        context: dict,
        suggestion: str = "",
    ) -> None:
        """Add a diagnostic issue."""
        self._issues.add(DiagnosticIssue(
            code=code,
            severity=severity,
            category=category,
            message=message,
            context=context,
            suggestion=suggestion,
        ))
    
    def detect_from_file(
        self,
        asg_path: str | Path,
        agnostic_path: str | Path | None = None,
    ) -> list[DataIO]:
        """Detect data I/O from ASG JSON file."""
        asg_path = Path(asg_path)
        
        with open(asg_path) as f:
            asg = json.load(f)
        
        agnostic = None
        if agnostic_path:
            with open(agnostic_path) as f:
                agnostic = json.load(f)
        
        return self.detect(asg, agnostic)
    
    def detect(
        self,
        asg: dict[str, Any],
        agnostic: dict[str, Any] | None = None,
    ) -> list[DataIO]:
        """Detect data I/O from ASG dictionary."""
        # Reset issues for this run
        self._issues = DiagnosticReport(tool_name="data_io")
        
        self._data_io, self._stats = self._extract_data_io(asg, agnostic)
        
        # Track schema issues
        self._track_schema_issues()
        
        return self._data_io
    
    def _track_schema_issues(self) -> None:
        """Track diagnostic issues for schema inference."""
        for item in self._data_io:
            # Track sources with no columns
            if not item.columns:
                self._add_issue(
                    code=SchemaIssueCode.NO_COLUMNS,
                    severity=Severity.WARNING,
                    category=IssueCategory.MISSING_SCHEMA,
                    message=f"No columns detected for {item.role}: {item.name}",
                    context={
                        "name": item.name,
                        "role": item.role,
                        "type": item.type,
                        "format": item.format,
                        "source_location": item.source,
                    },
                    suggestion="Check if source has schema or add columns in transformations",
                )
            else:
                for col in item.columns:
                    if col.type == "UNKNOWN":
                        self._add_issue(
                            code=SchemaIssueCode.TYPE_UNKNOWN,
                            severity=Severity.INFO,
                            category=IssueCategory.TYPE_INFERENCE,
                            message=f"Type not inferred for column: {col.name}",
                            context={
                                "column_name": col.name,
                                "source_name": item.name,
                                "source_type": item.type,
                                "role": item.role,
                            },
                            suggestion="Add pattern for column suffix or provide schema hint",
                        )
                    if col.confidence == "ambiguous":
                        self._add_issue(
                            code=SchemaIssueCode.AMBIGUOUS_ORIGIN,
                            severity=Severity.WARNING,
                            category=IssueCategory.MISSING_SCHEMA,
                            message=(
                                f"Column '{col.name}' in {item.role} '{item.name}' "
                                f"has ambiguous origin — it may belong to a different source"
                            ),
                            context={
                                "column_name": col.name,
                                "source_name": item.name,
                                "role": item.role,
                                "confidence": "ambiguous",
                            },
                            suggestion=(
                                f"Add a .select() before the join to make column "
                                f"ownership explicit, or verify '{col.name}' belongs "
                                f"to '{item.name}'"
                            ),
                        )
    
    def to_list(self) -> list[dict[str, Any]]:
        """Convert results to list of dicts for JSON serialization."""
        return [d.to_dict() for d in self._data_io]
    
    @staticmethod
    def _find_scoped_key(
        col_name: str,
        columns: dict[str, tuple[str, str]],
    ) -> str | None:
        """Find the first scoped key matching col_name suffix."""
        if col_name in columns:
            return col_name
        suffix = f".{col_name}"
        for key in columns:
            if key.endswith(suffix):
                return key
        return None

    @staticmethod
    def _find_all_scoped_keys(
        col_name: str,
        columns: dict[str, tuple[str, str]],
    ) -> list[str]:
        """Find ALL scoped keys matching col_name suffix."""
        if col_name in columns:
            return [col_name]
        suffix = f".{col_name}"
        return [k for k in columns if k.endswith(suffix)]

    def _normalize_type(self, t: str) -> str:
        """Normalize type to standard form."""
        if not t or t == 'UNKNOWN' or t == 'L_UNKNOWN':
            return 'UNKNOWN'
        return INTERNAL_TYPE_MAP.get(t, t.upper())
    
    def _infer_type_from_name(self, column_name: str) -> str | None:
        """Phase 2: Pattern-based type inference."""
        name_lower = column_name.lower()
        for pattern, col_type in TYPE_PATTERNS:
            if re.search(pattern, name_lower):
                return col_type
        return None
    
    @staticmethod
    def _looks_like_date(s: str) -> bool:
        return len(s) == 10 and s[4] == "-" and s[7] == "-" and s[:4].isdigit() and s[5:7].isdigit() and s[8:].isdigit()

    @staticmethod
    def _looks_like_timestamp(s: str) -> bool:
        return len(s) >= 19 and s[4] == "-" and s[7] == "-" and s[10] == " " and s[13] == ":" and s[16] == ":"

    def _infer_type_from_literal(self, literal: str) -> str | None:
        """Infer type from literal value using structural checks."""
        literal = literal.strip()
        if (literal.startswith("'") and literal.endswith("'")) or \
           (literal.startswith('"') and literal.endswith('"')):
            inner = literal[1:-1]
            if self._looks_like_date(inner):
                return "DATE"
            if self._looks_like_timestamp(inner):
                return "TIMESTAMP"
            return "STRING"
        bare = literal.lstrip("-")
        if bare and bare.isdigit():
            return "INT"
        if "." in bare:
            parts = bare.split(".", 1)
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                return "DECIMAL"
        if literal.lower() in ("true", "false"):
            return "BOOLEAN"
        return None
    
    def _extract_column_name(self, col_expr: Any) -> str | None:
        """Extract column name from expression using AST parsing."""
        if not isinstance(col_expr, str):
            return None
        col_expr = col_expr.strip()
        if col_expr.startswith('*') or col_expr.startswith('['):
            return None
        # Simple quoted string: 'name' or "name"
        if len(col_expr) >= 3 and col_expr[0] in ("'", '"') and col_expr[-1] == col_expr[0]:
            inner = col_expr[1:-1]
            if inner.isidentifier():
                return inner
        node = _safe_parse_expr(col_expr)
        if node is None:
            return None
        alias = _extract_alias_from_ast(node)
        if alias:
            return alias
        col_names = _extract_col_names_from_ast(node)
        return col_names[0] if col_names else None
    
    _STRING_METHODS = {"like", "rlike", "contains", "startswith", "endswith"}
    _STRING_FUNCS = STRING_OP_FUNCS | PD_STRING_FUNCS
    _DATE_FUNCS = DATE_TIME_FUNCS | PD_DATE_FUNCS

    def _infer_from_filter_conditions(
        self, condition: str, columns: dict[str, tuple[str, str]]
    ) -> int:
        """Phase 3: Infer types from filter conditions via AST (EVIDENCE-based)."""
        node = _safe_parse_expr(condition)
        if node is None:
            return 0

        inferred = 0

        # F.col('x') == literal  →  infer type from literal
        for col_name, comparator in _find_comparisons(node):
            for key in self._find_all_scoped_keys(col_name, columns):
                if columns[key][0] == "UNKNOWN":
                    literal_str = ast.unparse(comparator)
                    inferred_type = self._infer_type_from_literal(literal_str)
                    if inferred_type:
                        columns[key] = (inferred_type, "evidence")
                        inferred += 1

        # F.col('x').between(...)  →  DECIMAL
        for col_name, method in _find_method_calls_on_col(node, {"between"}):
            for key in self._find_all_scoped_keys(col_name, columns):
                if columns[key][0] == "UNKNOWN":
                    columns[key] = ("DECIMAL", "evidence")
                    inferred += 1

        # F.col('x').like/contains/startswith/endswith(...)  →  STRING
        for col_name, _ in _find_method_calls_on_col(node, self._STRING_METHODS):
            for key in self._find_all_scoped_keys(col_name, columns):
                if columns[key][0] == "UNKNOWN":
                    columns[key] = ("STRING", "evidence")
                    inferred += 1

        # F.upper(F.col('x')), F.lower(...)  →  STRING
        for col_name, _ in _find_pyspark_func_wrapping_col(node, self._STRING_FUNCS):
            for key in self._find_all_scoped_keys(col_name, columns):
                if columns[key][0] == "UNKNOWN":
                    columns[key] = ("STRING", "evidence")
                    inferred += 1

        # F.year(F.col('x')), F.month(...)  →  DATE
        for col_name, _ in _find_pyspark_func_wrapping_col(node, self._DATE_FUNCS):
            for key in self._find_all_scoped_keys(col_name, columns):
                if columns[key][0] == "UNKNOWN":
                    columns[key] = ("DATE", "evidence")
                    inferred += 1

        return inferred
    
    _STR_TRANSFORM_FUNCS = STRING_OP_FUNCS | PD_STRING_FUNCS
    _DATE_EXTRACT_FUNCS = DATE_TIME_FUNCS | PD_DATE_FUNCS
    _NUMERIC_PRECISION_FUNCS = NUMERIC_OP_FUNCS | PD_MATH_FUNCS
    _AGGREGATE_FUNCS = AGGREGATION_PATTERN | PD_AGG_FUNCS

    def _infer_from_functions(
        self, all_logic: str, columns: dict[str, tuple[str, str]]
    ) -> dict[str, int]:
        """Phase 5: Infer types from function usage via AST."""
        stats: dict[str, int] = {
            "string_funcs": 0, "date_funcs": 0, "numeric_funcs": 0,
            "arithmetic": 0, "aggregates": 0, "casts": 0,
        }
        node = _safe_parse_expr(all_logic)
        if node is None:
            return stats

        for col_name, _ in _find_pyspark_func_wrapping_col(node, self._STR_TRANSFORM_FUNCS):
            for key in self._find_all_scoped_keys(col_name, columns):
                if columns[key][0] == "UNKNOWN":
                    columns[key] = ("STRING", "evidence")
                    stats["string_funcs"] += 1

        for col_name, _ in _find_pyspark_func_wrapping_col(node, self._DATE_EXTRACT_FUNCS):
            for key in self._find_all_scoped_keys(col_name, columns):
                if columns[key][0] == "UNKNOWN":
                    columns[key] = ("DATE", "evidence")
                    stats["date_funcs"] += 1

        for col_name, _ in _find_pyspark_func_wrapping_col(node, self._NUMERIC_PRECISION_FUNCS):
            for key in self._find_all_scoped_keys(col_name, columns):
                if columns[key][0] == "UNKNOWN":
                    columns[key] = ("DECIMAL", "evidence")
                    stats["numeric_funcs"] += 1

        for col_name in _find_arithmetic_on_col(node):
            for key in self._find_all_scoped_keys(col_name, columns):
                if columns[key][0] == "UNKNOWN":
                    columns[key] = ("DECIMAL", "evidence")
                    stats["arithmetic"] += 1

        for col_name, _ in _find_pyspark_func_wrapping_col(node, self._AGGREGATE_FUNCS):
            for key in self._find_all_scoped_keys(col_name, columns):
                if columns[key][0] == "UNKNOWN":
                    columns[key] = ("DECIMAL", "evidence")
                    stats["aggregates"] += 1

        for col_name, cast_type in _find_cast_calls(node):
            for key in self._find_all_scoped_keys(col_name, columns):
                if columns[key][0] == "UNKNOWN":
                    mapped = CAST_TYPE_MAP.get(cast_type.lower())
                    if mapped:
                        columns[key] = (mapped, "evidence")
                        stats["casts"] += 1

        return stats
    
    def _extract_structfield_types(self, execution_calls: list[dict]) -> dict[str, str]:
        """Extract column->type mappings from StructField(name, TypeX()) calls."""
        from collections import defaultdict
        
        schema_map: dict[str, str] = {}
        
        by_scope: dict[str, list[dict]] = defaultdict(list)
        for c in execution_calls:
            if not isinstance(c, dict):
                continue
            caller = c.get("caller", {})
            if not isinstance(caller, dict):
                continue
            key = f"{caller.get('file', '')}:{caller.get('function', '')}"
            by_scope[key].append(c)
        
        for scope, calls in by_scope.items():
            sf_calls = sorted(
                [c for c in calls if c.get("callee", {}).get("function") == "StructField"],
                key=lambda c: c.get("caller", {}).get("line", 0)
            )
            type_calls = sorted(
                [c for c in calls if c.get("callee", {}).get("function") in STRUCTFIELD_TYPE_MAP],
                key=lambda c: c.get("caller", {}).get("line", 0)
            )
            
            if not sf_calls or not type_calls:
                continue
            
            # Pair by position when counts match
            if len(sf_calls) == len(type_calls):
                for sf, tc in zip(sf_calls, type_calls):
                    lits = sf.get("literal_arguments", {})
                    if isinstance(lits, dict):
                        col_name = lits.get("arg_0")
                        type_func = tc.get("callee", {}).get("function")
                        if col_name and type_func in STRUCTFIELD_TYPE_MAP:
                            schema_map[col_name] = STRUCTFIELD_TYPE_MAP[type_func]
        
        return schema_map
    
    def _extract_join_columns(self, condition: Any) -> list[tuple[str, str]]:
        """Extract column pairs from join condition using AST when possible."""
        pairs: list[tuple[str, str]] = []
        if isinstance(condition, list):
            for c in condition:
                if isinstance(c, str):
                    pairs.append((c, c))
        elif isinstance(condition, str):
            if condition.isidentifier():
                pairs.append((condition, condition))
            else:
                node = _safe_parse_expr(condition)
                if node is not None:
                    cols = _extract_col_names_from_ast(node)
                    if len(cols) == 2:
                        pairs.append((cols[0], cols[1]))
                    elif len(cols) == 1:
                        pairs.append((cols[0], cols[0]))
                    elif not cols:
                        # df['col_a'] == df['col_b'] → extract from Subscript nodes
                        subs = [
                            n.slice for n in ast.walk(node)
                            if isinstance(n, ast.Subscript)
                            and isinstance(n.slice, ast.Constant)
                            and isinstance(n.slice.value, str)
                        ]
                        if len(subs) == 2:
                            pairs.append((subs[0].value, subs[1].value))
        return pairs
    
    def _propagate_via_joins(
        self, columns: dict[str, tuple[str, str]], equivalences: dict[str, set[str]]
    ) -> int:
        """Phase 4/6: Propagate types through join equivalences (EVIDENCE-based)."""
        propagated = 0
        changed = True
        while changed:
            changed = False
            for col, (col_type, confidence) in list(columns.items()):
                if col_type != "UNKNOWN":
                    for equiv_col in equivalences.get(col, set()):
                        if equiv_col in columns and columns[equiv_col][0] == "UNKNOWN":
                            # Propagated types inherit confidence from source
                            columns[equiv_col] = (col_type, confidence)
                            propagated += 1
                            changed = True
        return propagated
    

    def _resolve_output_name_from_calls(self, sink: dict, asg: dict) -> str | None:
        """Resolve output name from execution_calls that consume this sink's source.

        Matches by binding (source_id) rather than function name heuristics.
        When a call's input references the sink's transformation, its
        literal_arguments are checked for table/name parameters.
        """
        source_id = sink.get('source_id')
        if not source_id:
            return None

        for call in asg.get('execution_calls', []):
            for inp in call.get('bindings', {}).get('inputs', []):
                if inp.get('source_id') == source_id or (
                    inp.get('source_type') == 'transformation' and source_id.startswith('tx_')
                ):
                    for key, value in call.get('literal_arguments', {}).items():
                        if 'table' in key.lower() or 'name' in key.lower():
                            if isinstance(value, str):
                                clean = value.replace('runtime:', '').strip("'\"")
                                if clean and len(clean) < 100:
                                    return clean
        return None

    # =========================================================================
    # _extract_data_io: decomposed into phases
    # =========================================================================

    @staticmethod
    def _build_param_map(execution_calls: list[dict]) -> dict[str, set[str]]:
        """Build param_funcName_paramName -> {source_id} from execution_calls."""
        param_map: dict[str, set[str]] = {}
        for ec in execution_calls:
            func_name = (ec.get("callee") or {}).get("function", "")
            bindings = ec.get("bindings") or {}
            for inp in bindings.get("inputs", []):
                arg_name = inp.get("arg_name", "")
                source_id = inp.get("source_id", "")
                if func_name and arg_name and source_id:
                    key = f"param_{func_name}_{arg_name}"
                    param_map.setdefault(key, set()).add(source_id)
        return param_map

    @staticmethod
    def _build_origin_map(
        transformations: list[dict],
        param_map: dict[str, set[str]] | None = None,
    ) -> dict[str, set[str]]:
        """Trace each transformation back to its origin data_in source(s)."""
        node_inputs: dict[str, list[str]] = {}
        for tx in transformations:
            node_inputs[tx["id"]] = tx.get("inputs", [])

        cache: dict[str, set[str]] = {}

        def _trace(node_id: str) -> set[str]:
            if node_id in cache:
                return cache[node_id]
            if node_id.startswith("in_"):
                cache[node_id] = {node_id}
                return cache[node_id]
            if param_map and node_id.startswith("param_"):
                resolved: set[str] = set()
                for sid in param_map.get(node_id, set()):
                    resolved |= _trace(sid)
                if resolved:
                    cache[node_id] = resolved
                    return resolved
            origins: set[str] = set()
            for inp in node_inputs.get(node_id, []):
                origins |= _trace(inp)
            if not origins:
                origins = {node_id}
            cache[node_id] = origins
            return origins

        for tx in transformations:
            _trace(tx["id"])
        return cache

    @staticmethod
    def _find_output_ceiling(
        node_id: str,
        tx_map: dict[str, dict],
    ) -> set[str] | None:
        """Trace backward from *node_id* to the nearest select / groupBy_agg
        and return the set of column names it produces (the "ceiling").

        Returns ``None`` when no ceiling can be determined (the input is a
        raw data_in or the chain has no narrowing operation).
        """
        visited: set[str] = set()
        current = node_id
        while current in tx_map and current not in visited:
            visited.add(current)
            tx = tx_map[current]
            op = tx.get("operation", "")
            params = tx.get("parameters", {})

            if op == "select":
                cols: set[str] = set()
                for col_expr in params.get("columns", []):
                    if not isinstance(col_expr, str):
                        continue
                    node = _safe_parse_expr(col_expr)
                    if node:
                        alias = _extract_alias_from_ast(node)
                        if alias:
                            cols.add(alias)
                            continue
                        col_refs = _extract_col_names_from_ast(node)
                        if col_refs:
                            for cn in col_refs:
                                cols.add(cn)
                            continue
                    # Bare quoted string: "'product_id'" -> product_id
                    bare = col_expr.strip().strip("'\"")
                    if bare and bare.isidentifier():
                        cols.add(bare)
                return cols or None


            if op == "groupBy_agg":
                cols = set()
                for gc in params.get("group_columns", []):
                    if isinstance(gc, str):
                        bare = gc.strip().strip("'\"\"")
                        if bare and bare.isidentifier():
                            cols.add(bare)
                for alias in params.get("column_aliases", []):
                    if isinstance(alias, str):
                        cols.add(alias)
                return cols or None

            inputs = tx.get("inputs", [])
            if len(inputs) == 1:
                current = inputs[0]
            else:
                break
        return None

    def _collect_columns_and_logic(
        self,
        transformations: list[dict],
        data_in: list[dict],
        origin_map: dict[str, set[str]],
    ) -> tuple[dict[str, set[str]], str, dict[str, list[str]]]:
        """Gather scoped column names (source_id -> {col_names}), logic, and
        ambiguous columns with their potential source names.

        Uses a two-pass approach to reduce cross-contamination from joins:
        Pass 1 processes single-origin transformations to build a baseline,
        Pass 2 processes multi-origin transformations using disambiguation.

        Column attribution relies exclusively on transformation *parameters*
        (columns, condition, expression, group_columns, column_aliases,
        join_condition) — NOT on ``inferred_input`` / ``inferred_output``
        which may carry contamination from the schema tracker.

        Returns (scoped_columns, all_logic, ambiguous_columns).
        """
        scoped_columns: dict[str, set[str]] = {}
        all_logic_parts: list[str] = []
        created_columns: set[str] = set()
        ambiguous_columns: dict[str, list[str]] = {}

        _id_to_name: dict[str, str] = {
            src.get("id", ""): src.get("name", src.get("id", ""))
            for src in data_in
        }

        def _add_col(
            col_name: str,
            source_ids: set[str],
            *,
            force: bool = False,
        ) -> None:
            if not col_name or not _is_valid_column_name(col_name):
                return
            if not force and len(source_ids) > 1:
                already_in = {
                    sid for sid in source_ids
                    if col_name in scoped_columns.get(sid, set())
                }
                if already_in:
                    source_ids = already_in
                elif col_name not in single_origin_ownership:
                    ambiguous_columns[col_name] = sorted({
                        _id_to_name.get(sid, sid) for sid in source_ids
                    })
            for sid in source_ids:
                scoped_columns.setdefault(sid, set()).add(col_name)

        def _extract_all_col_refs(expr_str: str, targets: set[str]) -> None:
            """Parse *expr_str* and add every referenced column to *targets*."""
            node = _safe_parse_expr(expr_str)
            if node:
                alias = _extract_alias_from_ast(node)
                if alias:
                    targets.add(alias)
                for cn in _extract_col_names_from_ast(node):
                    targets.add(cn)

        def _process_tx(tx: dict, origins: set[str]) -> None:
            params = tx.get("parameters", {})

            if "columns" in params:
                for col_expr in params["columns"]:
                    refs: set[str] = set()
                    _extract_all_col_refs(col_expr, refs)
                    if isinstance(col_expr, str) and ".alias(" in col_expr:
                        _alias_m = re.search(
                            r"\.alias\(['\"](\w+)['\"]+\)", col_expr,
                        )
                        if _alias_m:
                            _alias_name = _alias_m.group(1)
                            created_columns.add(_alias_name)
                            refs.discard(_alias_name)
                    for r in refs:
                        _add_col(r, origins)
                    if not refs:
                        name = self._extract_column_name(col_expr)
                        _add_col(name, origins)

            if "condition" in params:
                cond_node = _safe_parse_expr(params["condition"])
                if cond_node is not None:
                    for cname in _extract_col_names_from_ast(cond_node):
                        _add_col(cname, origins)

            if "group_by" in params:
                for col in params["group_by"]:
                    name = self._extract_column_name(col)
                    _add_col(name, origins)

            for gc in params.get("group_columns", []):
                if isinstance(gc, str):
                    name = self._extract_column_name(gc)
                    _add_col(name, origins)

            for alias in params.get("column_aliases", []):
                if isinstance(alias, str):
                    created_columns.add(alias)
                    _add_col(alias, origins)

            col_name_param = params.get("column_name")
            if col_name_param and isinstance(col_name_param, str):
                created_columns.add(col_name_param)
                _add_col(col_name_param, origins)

            expression = params.get("expression")
            if expression and isinstance(expression, str):
                expr_node = _safe_parse_expr(expression)
                if expr_node:
                    for cname in _extract_col_names_from_ast(expr_node):
                        _add_col(cname, origins)

            join_cond = params.get("join_condition")
            if join_cond:
                conds = join_cond if isinstance(join_cond, list) else [join_cond]
                inputs = tx.get("inputs", [])
                if tx.get("operation") in ("join", "join_custom") and len(inputs) >= 2:
                    left_origins = origin_map.get(inputs[0], set())
                    right_origins = origin_map.get(inputs[1], set())
                    for cond in conds:
                        if isinstance(cond, str) and _is_valid_column_name(cond):
                            _add_col(cond, left_origins, force=True)
                            _add_col(cond, right_origins, force=True)
                        elif isinstance(cond, str):
                            cond_node = _safe_parse_expr(cond)
                            if cond_node:
                                col_refs = _extract_col_names_from_ast(cond_node)
                                if len(col_refs) == 2:
                                    _add_col(col_refs[0], left_origins, force=True)
                                    _add_col(col_refs[1], right_origins, force=True)
                                else:
                                    for cn in col_refs:
                                        _add_col(cn, left_origins, force=True)
                                        _add_col(cn, right_origins, force=True)
                else:
                    for cond in conds:
                        name = self._extract_column_name(cond) or (
                            cond
                            if isinstance(cond, str) and _is_valid_column_name(cond)
                            else None
                        )
                        _add_col(name, origins)

            # Fallback: use inferred_input/inferred_output ONLY when params
            # provided no column evidence AND this is a single-origin tx.
            # Multi-origin inferred data carries schema-tracker contamination.
            params_contributed = any(
                k in params
                for k in ("columns", "condition", "group_by", "group_columns",
                           "column_aliases", "column_name", "expression",
                           "join_condition")
            )
            if len(origins) <= 1 and not params_contributed:
                for col in tx.get("inferred_output", []):
                    if isinstance(col, dict) and "name" in col:
                        _add_col(col["name"], origins)
                for col in tx.get("inferred_input", []):
                    if isinstance(col, dict) and "name" in col:
                        _add_col(col["name"], origins)

        # ------------------------------------------------------------------
        # Pass 1: single-origin transformations → clean ownership baseline
        # ------------------------------------------------------------------
        single_origin_ownership: dict[str, set[str]] = {}

        multi_origin_txs: list[tuple[dict, set[str]]] = []
        for tx in transformations:
            origins = origin_map.get(tx["id"], set())
            logic = tx.get("logic", "")
            if logic:
                all_logic_parts.append(logic)
            if len(origins) <= 1:
                _process_tx(tx, origins)
                for oid in origins:
                    for col_name in scoped_columns.get(oid, set()):
                        single_origin_ownership.setdefault(col_name, set()).add(oid)
            else:
                multi_origin_txs.append((tx, origins))

        # ------------------------------------------------------------------
        # Pass 2: multi-origin transformations
        # ------------------------------------------------------------------
        for tx, origins in multi_origin_txs:
            _process_tx(tx, origins)

        # ------------------------------------------------------------------
        # Created Barrier: columns produced by withColumn / alias / agg
        # are derived — they never belong to a data_in source.
        # ------------------------------------------------------------------
        if created_columns:
            for sid, cols in scoped_columns.items():
                if sid.startswith("in_"):
                    cols -= created_columns

        # ------------------------------------------------------------------
        # Build join metadata
        # ------------------------------------------------------------------
        join_keys: set[str] = set()
        tx_map = {tx["id"]: tx for tx in transformations}

        for tx in transformations:
            if tx.get("operation") not in ("join", "join_custom"):
                continue
            jc = tx.get("parameters", {}).get("join_condition")
            if not jc:
                continue
            conds = jc if isinstance(jc, list) else [jc]
            for c in conds:
                if isinstance(c, str) and _is_valid_column_name(c):
                    join_keys.add(c)
                elif isinstance(c, str):
                    cond_node = _safe_parse_expr(c)
                    if cond_node:
                        for cn in _extract_col_names_from_ast(cond_node):
                            join_keys.add(cn)

        # ------------------------------------------------------------------
        # Cleanup 1: single-origin ownership
        # ------------------------------------------------------------------
        def _apply_ownership_cleanup() -> None:
            for col_name, owners in single_origin_ownership.items():
                if col_name in join_keys or col_name in created_columns:
                    continue
                for sid, cols in scoped_columns.items():
                    if col_name in cols and sid not in owners:
                        cols.discard(col_name)

        _apply_ownership_cleanup()

        # ------------------------------------------------------------------
        # Cleanup 2: join column ceiling — restrict right-side data_in nodes
        # to the columns actually exposed by pre-join select / groupBy_agg.
        # Uses per-join right keys (not global join_keys) to avoid leaking
        # left-side join keys into the right-side allowed set.
        # ------------------------------------------------------------------
        # data_in ids used as left-side input in any join — these serve
        # multiple purposes and must not be constrained by a single join's
        # right-side ceiling.
        left_side_inputs: set[str] = set()
        for tx in transformations:
            if tx.get("operation") not in ("join", "join_custom"):
                continue
            inputs = tx.get("inputs", [])
            if inputs:
                left_side_inputs |= {
                    o for o in origin_map.get(inputs[0], set())
                    if o.startswith("in_")
                }

        def _apply_ceiling_cleanup() -> None:
            for tx in transformations:
                if tx.get("operation") not in ("join", "join_custom"):
                    continue
                inputs = tx.get("inputs", [])
                if len(inputs) < 2:
                    continue
                right_ceiling = self._find_output_ceiling(inputs[1], tx_map)
                if right_ceiling is None:
                    continue
                right_jk: set[str] = set()
                jc = tx.get("parameters", {}).get("join_condition")
                if jc:
                    conds = jc if isinstance(jc, list) else [jc]
                    for c in conds:
                        if isinstance(c, str) and _is_valid_column_name(c):
                            right_jk.add(c)
                        elif isinstance(c, str):
                            refs = _extract_col_names_from_ast(
                                _safe_parse_expr(c) or ast.parse("0", mode="eval").body
                            )
                            if len(refs) >= 2:
                                right_jk.add(refs[1])
                            elif refs:
                                right_jk.add(refs[0])
                allowed = right_ceiling | right_jk
                right_origins = origin_map.get(inputs[1], set())
                right_data_in = {
                    o for o in right_origins if o.startswith("in_")
                }
                if len(right_data_in) > 1:
                    continue
                for sid in right_data_in:
                    if sid in left_side_inputs:
                        continue
                    cols = scoped_columns.get(sid)
                    if cols is None:
                        continue
                    legitimate = {
                        cn for cn, owners in single_origin_ownership.items()
                        if sid in owners
                    }
                    cols -= {
                        c for c in cols
                        if c not in allowed and c not in legitimate
                    }

        _apply_ceiling_cleanup()

        # ------------------------------------------------------------------
        # Cleanup 3: Branch Scoping — negative attribution via topology.
        # At each join(L, R), if a branch has a ceiling (select/groupBy_agg
        # upstream), columns NOT in that ceiling cannot physically originate
        # from that branch's data_in nodes. Only cross-branch columns
        # (present in sources on BOTH sides) are checked; single-branch
        # columns are untouched. Join keys are immune.
        # ------------------------------------------------------------------
        def _apply_branch_scoping() -> None:
            for tx in transformations:
                if tx.get("operation") not in ("join", "join_custom"):
                    continue
                inputs = tx.get("inputs", [])
                if len(inputs) < 2:
                    continue

                left_input, right_input = inputs[0], inputs[1]
                left_src = {
                    o for o in origin_map.get(left_input, set())
                    if o.startswith("in_")
                }
                right_src = {
                    o for o in origin_map.get(right_input, set())
                    if o.startswith("in_")
                }
                if not left_src or not right_src:
                    continue

                l_ceil = self._find_output_ceiling(left_input, tx_map)
                r_ceil = self._find_output_ceiling(right_input, tx_map)
                if l_ceil is None and r_ceil is None:
                    continue

                left_col_union: set[str] = set()
                for sid in left_src:
                    left_col_union |= scoped_columns.get(sid, set())
                right_col_union: set[str] = set()
                for sid in right_src:
                    right_col_union |= scoped_columns.get(sid, set())
                cross = left_col_union & right_col_union
                if not cross:
                    continue

                per_join_keys: set[str] = set()
                jc = tx.get("parameters", {}).get("join_condition")
                if jc:
                    conds = jc if isinstance(jc, list) else [jc]
                    for c in conds:
                        if isinstance(c, str) and _is_valid_column_name(c):
                            per_join_keys.add(c)
                        elif isinstance(c, str):
                            cond_node = _safe_parse_expr(c)
                            if cond_node:
                                for cn in _extract_col_names_from_ast(cond_node):
                                    per_join_keys.add(cn)

                for col_name in cross:
                    if col_name in per_join_keys:
                        continue
                    can_left = l_ceil is None or col_name in l_ceil
                    can_right = r_ceil is None or col_name in r_ceil
                    if can_left and not can_right:
                        for sid in right_src:
                            scoped_columns.get(sid, set()).discard(col_name)
                    elif can_right and not can_left:
                        for sid in left_src:
                            scoped_columns.get(sid, set()).discard(col_name)

        _apply_branch_scoping()

        # ------------------------------------------------------------------
        # Seed from data_in.inferred_columns (may re-introduce bleeding)
        # ------------------------------------------------------------------
        for src in data_in:
            if src.get("type") == "config":
                continue
            sid = src.get("id", "")
            for col in src.get("inferred_columns", []):
                if isinstance(col, dict) and "name" in col:
                    _add_col(col["name"], {sid})

        # ------------------------------------------------------------------
        # Cleanup 4: re-apply all barriers after inferred_columns seeding
        # ------------------------------------------------------------------
        _apply_ownership_cleanup()

        _apply_ceiling_cleanup()

        _apply_branch_scoping()

        if created_columns:
            for sid, cols in scoped_columns.items():
                if sid.startswith("in_"):
                    cols -= created_columns

        return scoped_columns, "\n".join(all_logic_parts), ambiguous_columns

    def _run_type_inference(
        self,
        asg: dict[str, Any],
        agnostic: dict[str, Any] | None,
        global_columns: dict[str, tuple[str, str]],
        transformations: list[dict],
        all_logic: str,
        origin_map: dict[str, set[str]],
    ) -> dict[str, Any]:
        """Run the 6-phase type inference pipeline with scoped column keys.

        Keys in global_columns are 'source_id.col_name'.
        """
        stats: dict[str, Any] = {
            "phase1_code_analysis": 0,
            "phase1b_agnostic_asg": 0,
            "phase1c_structfield": 0,
            "phase2_pattern_based": 0,
            "phase3_filter_inference": 0,
            "phase4_join_propagation": 0,
            "phase5_function_usage": 0,
            "phase5_details": {},
            "phase6_join_propagation_2": 0,
            "join_equivalences": 0,
            "total_columns": 0,
            "total_known_columns": 0,
        }

        # Phase 1: Code analysis — scoped to transformation origins
        for tx in transformations:
            origins = origin_map.get(tx["id"], set())
            for col_list_key in ("inferred_output", "inferred_input"):
                for col in tx.get(col_list_key, []):
                    if isinstance(col, dict):
                        name = col.get("name")
                        col_type = col.get("inferred_type") or col.get("type")
                        if name and col_type:
                            normalized = self._normalize_type(col_type)
                            if normalized != "UNKNOWN":
                                for sid in origins:
                                    key = f"{sid}.{name}"
                                    if key in global_columns and global_columns[key][0] == "UNKNOWN":
                                        global_columns[key] = (normalized, "evidence")
                                        stats["phase1_code_analysis"] += 1

        # Phase 1b: Agnostic ASG types (match by column name suffix)
        if agnostic:
            for resource in agnostic.get("resources", []):
                for attr in resource.get("attributes", []):
                    name = attr.get("name")
                    logical_type = attr.get("logical_type")
                    if name and logical_type:
                        normalized = self._normalize_type(logical_type)
                        if normalized != "UNKNOWN":
                            for key in self._find_all_scoped_keys(name, global_columns):
                                if global_columns[key][0] == "UNKNOWN":
                                    global_columns[key] = (normalized, "evidence")
                                    stats["phase1b_agnostic_asg"] += 1

        # Phase 1c: StructField explicit schemas (match by suffix)
        structfield_types = self._extract_structfield_types(asg.get("execution_calls", []))
        for col_name, col_type in structfield_types.items():
            for key in self._find_all_scoped_keys(col_name, global_columns):
                if global_columns[key][0] == "UNKNOWN":
                    global_columns[key] = (col_type, "evidence")
                    stats["phase1c_structfield"] += 1

        # Phase 3: Filter condition inference — scoped to filter origins
        # (runs before patterns so evidence takes priority)
        for tx in transformations:
            if tx.get("operation") in ("filter", "where"):
                condition = tx.get("parameters", {}).get("condition", "")
                if condition:
                    origins = origin_map.get(tx["id"], set())
                    scoped_view = {
                        k: v for k, v in global_columns.items()
                        if any(k.startswith(f"{sid}.") for sid in origins)
                    }
                    count = self._infer_from_filter_conditions(condition, scoped_view)
                    global_columns.update(scoped_view)
                    stats["phase3_filter_inference"] += count

        # Phase 5: Function usage inference
        # (runs before patterns so evidence takes priority)
        phase5_details = self._infer_from_functions(all_logic, global_columns)
        stats["phase5_details"] = phase5_details
        stats["phase5_function_usage"] = sum(phase5_details.values())

        # Build scoped join equivalences
        equivalences = self._build_scoped_join_equivalences(transformations, origin_map)
        stats["join_equivalences"] = len(equivalences)

        # Phase 4: First join propagation (spreads evidence types)
        stats["phase4_join_propagation"] = self._propagate_via_joins(
            global_columns, equivalences
        )

        # Phase 2: Pattern-based inference (fallback for columns still UNKNOWN)
        for key in list(global_columns.keys()):
            if global_columns[key][0] == "UNKNOWN":
                col_name = key.split(".", 1)[1] if "." in key else key
                inferred = self._infer_type_from_name(col_name)
                if inferred:
                    global_columns[key] = (inferred, "pattern")
                    stats["phase2_pattern_based"] += 1

        # Phase 6: Second join propagation (spreads pattern types)
        stats["phase6_join_propagation_2"] = self._propagate_via_joins(
            global_columns, equivalences
        )

        # Totals
        stats["total_columns"] = len(global_columns)
        stats["total_known_columns"] = sum(
            1 for (t, _) in global_columns.values() if t != "UNKNOWN"
        )
        stats["by_evidence"] = sum(
            1 for (t, c) in global_columns.values() if t != "UNKNOWN" and c == "evidence"
        )
        stats["by_pattern"] = sum(
            1 for (t, c) in global_columns.values() if t != "UNKNOWN" and c == "pattern"
        )
        return stats

    def _build_join_equivalences(
        self, transformations: list[dict]
    ) -> dict[str, set[str]]:
        """Build column equivalence sets from join conditions (unscoped, legacy)."""
        equivalences: dict[str, set[str]] = {}
        for tx in transformations:
            if tx.get("operation") == "join":
                join_cond = tx.get("parameters", {}).get("join_condition")
                if join_cond:
                    for left, right in self._extract_join_columns(join_cond):
                        equivalences.setdefault(left, set()).add(right)
                        equivalences.setdefault(right, set()).add(left)
        return equivalences

    def _build_scoped_join_equivalences(
        self,
        transformations: list[dict],
        origin_map: dict[str, set[str]],
    ) -> dict[str, set[str]]:
        """Build scoped equivalences: joins create edges between source_id.col pairs.

        For a join on column 'rest_no' between sources {in_001} and {in_002},
        this produces: in_001.rest_no <-> in_002.rest_no
        """
        equivalences: dict[str, set[str]] = {}
        for tx in transformations:
            if tx.get("operation") != "join":
                continue
            join_cond = tx.get("parameters", {}).get("join_condition")
            if not join_cond:
                continue
            inputs = tx.get("inputs", [])
            if len(inputs) < 2:
                continue
            left_origins = origin_map.get(inputs[0], set())
            right_origins = origin_map.get(inputs[1], set())
            for left_col, right_col in self._extract_join_columns(join_cond):
                left_keys = [f"{sid}.{left_col}" for sid in left_origins]
                right_keys = [f"{sid}.{right_col}" for sid in right_origins]
                for lk in left_keys:
                    for rk in right_keys:
                        equivalences.setdefault(lk, set()).add(rk)
                        equivalences.setdefault(rk, set()).add(lk)
        return equivalences

    @staticmethod
    def _classify_source_type(raw_type: str) -> str:
        raw = raw_type.lower()
        if raw == "config":
            return "config"
        if raw in ("csv", "parquet", "json"):
            return "file"
        if raw in ("jdbc", "sql", "redshift"):
            return "database"
        if raw == "table":
            return "table"
        return "file"

    @staticmethod
    def _classify_sink_type(raw_type: str) -> str:
        raw = raw_type.lower()
        if raw in ("table", "saveastable"):
            return "table"
        if raw in ("jdbc", "redshift"):
            return "database"
        return "file"

    def _build_columns(
        self,
        inferred_columns: list[dict],
        global_columns: dict[str, tuple[str, str]],
        *,
        source_id: str = "",
        validate_name: bool = True,
        use_asg_types_only: bool = False,
        ambiguous_columns: dict[str, list[str]] | None = None,
    ) -> list[Column]:
        """Materialize Column objects from raw ASG inferred_columns.

        When use_asg_types_only=True (config/widget sources), types come exclusively
        from the ASG's inferred_columns — global_columns is not consulted, since
        widget parameters are semantically different from DataFrame columns.

        When use_asg_types_only=False (tables/files), ASG high-confidence types
        take priority, with scoped global_columns as fallback for gap-filling.
        source_id is used for direct scoped lookup (e.g. 'in_001.col_name').
        """
        cols: list[Column] = []
        for col in inferred_columns:
            col_name = col.get("name")
            if not col_name:
                continue
            if validate_name and not _is_valid_column_name(col_name):
                continue

            asg_type = col.get("inferred_type") or col.get("type")
            asg_conf = col.get("confidence")

            if use_asg_types_only:
                if asg_type and asg_type != "UNKNOWN":
                    col_type = self._normalize_type(asg_type)
                    confidence = "evidence" if asg_conf in ("high", "HIGH") else "pattern"
                else:
                    col_type = "UNKNOWN"
                    confidence = "unknown"
            elif asg_type and asg_type != "UNKNOWN" and asg_conf in ("high", "HIGH"):
                col_type = self._normalize_type(asg_type)
                confidence = "evidence"
            elif asg_type and asg_type != "UNKNOWN" and asg_conf in ("low", "LOW"):
                col_type = self._normalize_type(asg_type)
                src = col.get("source", "")
                confidence = "xref_output" if src == "xref_output" else "xref_input" if src == "xref_input" else "pattern"
            else:
                type_info = ("UNKNOWN", "unknown")
                # Direct scoped lookup first
                if source_id:
                    scoped_key = f"{source_id}.{col_name}"
                    if scoped_key in global_columns:
                        type_info = global_columns[scoped_key]
                # Fallback: find best typed match across all scopes
                if type_info[0] == "UNKNOWN":
                    for key, val in global_columns.items():
                        if key.endswith(f".{col_name}") and val[0] != "UNKNOWN":
                            type_info = val
                            break
                col_type, confidence = type_info

            amb_reason = None
            amb_sources = None
            if ambiguous_columns and col_name in ambiguous_columns:
                confidence = "ambiguous"
                amb_reason = "multi_origin_join_no_ceiling"
                amb_sources = ambiguous_columns[col_name]
            cols.append(Column(
                name=col_name, type=col_type, confidence=confidence,
                default_value=col.get("default_value"),
                reason=amb_reason,
                potential_sources=amb_sources,
            ))
        return cols

    @staticmethod
    def _compute_core_inputs(
        data_in: list[dict],
        data_out: list[dict],
        transformations: list[dict],
        origin_map: dict[str, set[str]],
    ) -> set[str]:
        """Compute the set of data_in IDs reachable from any data_out.

        A data_in is 'Core' only if it participates in the main data flow
        that produces an output (sink).  Inputs that are only read inside
        utility / verification functions (e.g. verify_outputs) and never
        feed a sink are 'Utility' and should be excluded from the schema.
        """
        all_input_ids = {src.get("id", "") for src in data_in} - {""}
        if not data_out:
            return all_input_ids

        core: set[str] = set()
        for sink in data_out:
            source_id = sink.get("source_id", "")
            if source_id:
                origins = origin_map.get(source_id, set())
                core |= {o for o in origins if o.startswith("in_")}
        # Graceful degradation: if the graph has no explicit connectivity
        # (e.g. transformations lack 'inputs' fields), treat all as core
        # rather than silently dropping everything.
        if not core:
            return all_input_ids
        return core

    def _build_input_data_io(
        self,
        data_in: list[dict],
        global_columns: dict[str, tuple[str, str]],
        *,
        core_input_ids: set[str] | None = None,
        ambiguous_columns: dict[str, list[str]] | None = None,
    ) -> list[DataIO]:
        """Build DataIO entries for inputs."""
        # Build DataIO for named sources
        result: list[DataIO] = []
        for src in data_in:
            if src.get("is_test_file"):
                continue
            # Main-Flow Connectivity: skip utility inputs not reachable from any sink
            if core_input_ids is not None:
                sid = src.get("id", "")
                if sid and sid not in core_input_ids:
                    continue
            name = src.get("name") or src.get("path") or "unknown"
            if name == "None" or (src.get("name") is None and not src.get("path")):
                continue
            if isinstance(name, str) and "/" in name:
                name = name.split("/")[-1]

            src_type = src.get("type", "unknown").lower()
            is_config = src_type == "config"
            raw_cols = src.get("inferred_columns", [])
            if is_config:
                raw_cols = [c for c in raw_cols if c.get("source") == "widget_default"]

            sid = src.get("id", "")
            if not is_config and sid:
                prefix = f"{sid}."
                allowed_names = {
                    key[len(prefix):]
                    for key in global_columns
                    if key.startswith(prefix)
                }
                if allowed_names:
                    if raw_cols:
                        raw_cols = [
                            c for c in raw_cols
                            if isinstance(c, dict) and c.get("name") in allowed_names
                        ]
                    existing = {c.get("name") for c in raw_cols if isinstance(c, dict)}
                    for extra_name in sorted(allowed_names - existing):
                        raw_cols.append({"name": extra_name})
                elif not raw_cols:
                    raw_cols = [
                        {"name": key[len(prefix):]}
                        for key in sorted(global_columns)
                        if key.startswith(prefix)
                    ]

            columns = self._build_columns(
                raw_cols, global_columns,
                source_id=sid,
                use_asg_types_only=is_config,
                ambiguous_columns=ambiguous_columns,
            )

            loc = src.get("location", {})
            source_loc = f"{loc.get('pathfile', 'unknown')}:{loc.get('line', 0)}" if loc else "unknown"
            full_name_val = src.get("name") or src.get("path") or "unknown"
            path_val = src.get("path") or ""

            result.append(DataIO(
                name=str(name),
                full_name=str(full_name_val),
                source=source_loc,
                type=self._classify_source_type(src_type),
                format=src_type,
                detection="dynamic" if "runtime:" in str(path_val) else "static",
                role="input",
                path=str(path_val),
                columns=columns,
            ))
        return result

    @staticmethod
    def _collect_output_chain_columns(
        source_id: str,
        transformations: list[dict],
    ) -> set[str]:
        """BFS backward from source_id to collect column names from the chain."""
        tx_map = {tx["id"]: tx for tx in transformations}
        visited: set[str] = set()
        queue = [source_id]
        columns: set[str] = set()

        while queue:
            current = queue.pop(0)
            if current in visited or current not in tx_map:
                continue
            visited.add(current)
            tx = tx_map[current]

            for col in tx.get("inferred_output", []):
                if isinstance(col, dict) and col.get("name"):
                    columns.add(col["name"])
            for col in tx.get("inferred_input", []):
                if isinstance(col, dict) and col.get("name"):
                    columns.add(col["name"])

            params = tx.get("parameters", {})
            for col_expr in params.get("columns", []):
                if isinstance(col_expr, str) and _is_valid_column_name(col_expr):
                    columns.add(col_expr)
            for col_expr in params.get("group_columns", []):
                if isinstance(col_expr, str) and _is_valid_column_name(col_expr):
                    columns.add(col_expr)
            col_name = params.get("column_name")
            if isinstance(col_name, str) and _is_valid_column_name(col_name):
                columns.add(col_name)
            join_cond = params.get("join_condition")
            if join_cond:
                conds = join_cond if isinstance(join_cond, list) else [join_cond]
                for cond in conds:
                    if isinstance(cond, str) and _is_valid_column_name(cond):
                        columns.add(cond)

            for inp_id in tx.get("inputs", []):
                queue.append(inp_id)

        return columns

    def _build_output_data_io(
        self,
        data_out: list[dict],
        global_columns: dict[str, tuple[str, str]],
        asg: dict[str, Any],
    ) -> list[DataIO]:
        """Build DataIO entries for outputs."""
        result: list[DataIO] = []
        for sink in data_out:
            if sink.get("is_test_file"):
                continue
            name = sink.get("name") or sink.get("path")
            if not name or name in ("unknown", "Uninferable", None):
                resolved = self._resolve_output_name_from_calls(sink, asg)
                if resolved:
                    name = resolved
            name = name or "unknown"
            if isinstance(name, str) and "/" in name:
                name = name.split("/")[-1]

            sink_type = sink.get("type", "file").lower()
            raw_out_cols = sink.get("inferred_columns", [])
            sid = sink.get("id", "")
            if not raw_out_cols:
                prefix = f"{sid}."
                raw_out_cols = [
                    {"name": key[len(prefix):]}
                    for key in sorted(global_columns)
                    if key.startswith(prefix)
                ]
            columns = self._build_columns(
                raw_out_cols, global_columns,
                source_id=sid,
            )

            loc = sink.get("location", {})
            source_loc = f"{loc.get('pathfile', 'unknown')}:{loc.get('line', 0)}" if loc else "unknown"
            full_name_val = sink.get("name") or sink.get("path") or "unknown"
            path_val = sink.get("path") or ""

            result.append(DataIO(
                name=str(name),
                full_name=str(full_name_val),
                source=source_loc,
                type=self._classify_sink_type(sink_type),
                format=sink_type,
                detection="inferred",
                role="output",
                path=str(path_val),
                columns=columns,
            ))
        return result

    def _extract_data_io(
        self,
        asg: dict[str, Any],
        agnostic: dict[str, Any] | None = None,
    ) -> tuple[list[DataIO], dict[str, Any]]:
        """Main extraction with scoped column keys (source_id.col_name).

        Prevents cross-source type contamination — joins explicitly create
        cross-source type edges via scoped equivalences.
        """
        transformations = asg.get("transformations", [])
        data_in = asg.get("data_in", [])
        data_out = asg.get("data_out", [])

        param_map = self._build_param_map(asg.get("execution_calls", []))
        origin_map = self._build_origin_map(transformations, param_map)
        scoped_columns, all_logic, ambiguous_columns = self._collect_columns_and_logic(
            transformations, data_in, origin_map,
        )

        # Propagate scoped columns to indirect inputs from their origin source
        via_origin: dict[str, str] = {}
        for ec in asg.get("execution_calls", []):
            callee_fn = (ec.get("callee") or {}).get("function", "")
            target = ((ec.get("bindings") or {}).get("output") or {}).get("target_node", "")
            if callee_fn and target and target.startswith("in_"):
                via_origin[callee_fn] = target
        for src in data_in:
            sid = src.get("id", "")
            via = src.get("via_function", "")
            if src.get("is_indirect") and via and sid not in scoped_columns:
                origin_sid = via_origin.get(via)
                if origin_sid and origin_sid in scoped_columns:
                    scoped_columns[sid] = set(scoped_columns[origin_sid])

        # Collect output columns by tracing transformation chains from data_out
        for sink in data_out:
            sid = sink.get("id", "")
            source_id = sink.get("source_id", "")
            if source_id and not sink.get("inferred_columns"):
                resolved_id = source_id
                if resolved_id.startswith("param_"):
                    mapped = param_map.get(resolved_id)
                    if mapped:
                        resolved_id = next(iter(mapped))
                chain_cols = self._collect_output_chain_columns(
                    resolved_id, transformations,
                )
                if chain_cols:
                    scoped_columns[sid] = chain_cols

        # Build global_columns with scoped keys: "in_XXX.col_name" / "out_XXX.col_name"
        global_columns: dict[str, tuple[str, str]] = {}
        for source_id, col_names in scoped_columns.items():
            for col_name in col_names:
                global_columns[f"{source_id}.{col_name}"] = ("UNKNOWN", "unknown")

        # Phase 0: Seed from ASG inferred_columns — scoped by source id
        phase0_count = 0
        for src in data_in + data_out:
            if src.get("type") == "config":
                continue
            sid = src.get("id", "")
            for col in src.get("inferred_columns", []):
                col_name = col.get("name")
                col_type = col.get("inferred_type") or col.get("type")
                confidence = col.get("confidence")
                if not col_name or not col_type:
                    continue
                key = f"{sid}.{col_name}"
                if key not in global_columns:
                    continue
                normalized = self._normalize_type(col_type)
                if normalized == "UNKNOWN":
                    continue
                if global_columns[key][0] != "UNKNOWN":
                    continue
                if confidence in ("high", "HIGH"):
                    global_columns[key] = (normalized, "evidence")
                    phase0_count += 1

        stats = self._run_type_inference(
            asg, agnostic, global_columns, transformations, all_logic, origin_map,
        )
        stats["phase0_asg_seed"] = phase0_count

        core_input_ids = self._compute_core_inputs(
            data_in, data_out, transformations, origin_map,
        )
        data_io_list = self._build_input_data_io(
            data_in, global_columns,
            core_input_ids=core_input_ids,
            ambiguous_columns=ambiguous_columns,
        )
        data_io_list += self._build_output_data_io(data_out, global_columns, asg)

        # Inject placeholder columns for inputs with no schema
        placeholder_count = 0
        for dio in data_io_list:
            if dio.role == "input" and not dio.columns:
                dio.columns = [
                    Column(name="id", type="INT", confidence="placeholder"),
                    Column(name="value", type="STRING", confidence="placeholder"),
                    Column(name="created_at", type="TIMESTAMP", confidence="placeholder"),
                ]
                placeholder_count += 1
        if placeholder_count:
            stats["placeholder_inputs"] = placeholder_count

        return data_io_list, stats
