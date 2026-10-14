"""
=============================================================================
PySpark Function Registry - Central Source of Truth
=============================================================================

AUTO-GENERATED FILE - DO NOT EDIT MANUALLY

Generated from: docs/data/pyspark_api_inventory.csv
Generated at: 2026-02-26 14:31
Generator: scripts/generate_spark_functions.py

To regenerate:
    python scripts/generate_spark_functions.py

=============================================================================
"""

from __future__ import annotations
from enum import Enum
from typing import NamedTuple


class ReturnType(str, Enum):
    """What data type does the function produce?"""
    NUMERIC = "NUMERIC"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    STRING = "STRING"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    ARRAY = "ARRAY"
    MAP = "MAP"
    STRUCT = "STRUCT"
    COLUMN = "COLUMN"
    DATAFRAME = "DATAFRAME"
    VOID = "VOID"
    SAME_AS_INPUT = "SAME"
    UNKNOWN = "UNKNOWN"


class BehaviorPattern(str, Enum):
    """What behavioral pattern does this function match?"""
    AGGREGATION = "AGGREGATION"
    WINDOW = "WINDOW"
    COLUMN_EXPR = "COLUMN_EXPR"
    STRING_OP = "STRING_OP"
    NUMERIC_OP = "NUMERIC_OP"
    DATE_TIME_OP = "DATE_TIME_OP"
    COLLECTION_OP = "COLLECTION_OP"
    JSON_OP = "JSON_OP"
    CONDITIONAL = "CONDITIONAL"
    TYPE_CONVERSION = "TYPE_CONVERSION"
    NULL_HANDLING = "NULL_HANDLING"
    COLUMN_REF = "COLUMN_REF"
    UNKNOWN = "UNKNOWN"


class Category(str, Enum):
    """Semantic category for documentation."""
    AGGREGATION = "AGGREGATION"
    WINDOW = "WINDOW"
    STRING = "STRING"
    NUMERIC = "NUMERIC"
    DATE_TIME = "DATE_TIME"
    CONDITIONAL = "CONDITIONAL"
    NULL_HANDLING = "NULL_HANDLING"
    TYPE_CONVERSION = "TYPE_CONVERSION"
    COLUMN_REFERENCE = "COLUMN_REFERENCE"
    ARRAY = "ARRAY"
    MAP = "MAP"
    JSON = "JSON"
    COMPARISON = "COMPARISON"
    HASH = "HASH"
    MISC = "MISC"


class MappingType(str, Enum):
    """How does this function map to Snowflake?"""
    DIRECT = "DIRECT"
    TRANSFORM = "TRANSFORM"
    UDF_REQUIRED = "UDF_REQUIRED"
    BLOCKER = "BLOCKER"
    NO_OP = "NO_OP"
    UNKNOWN = "UNKNOWN"


class InputType(str, Enum):
    """What type does this function imply about its input column?"""
    NUMERIC = "NUMERIC"
    STRING = "STRING"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    ARRAY = "ARRAY"
    MAP = "MAP"
    JSON = "JSON"
    ANY = "ANY"


class FunctionMeta(NamedTuple):
    """Complete metadata for a function."""
    name: str
    return_type: ReturnType
    pattern: BehaviorPattern
    category: Category
    snowflake_name: str | None = None
    mapping_type: MappingType = MappingType.UNKNOWN
    expression_op: str = "FUNCTION_CALL"
    input_type: InputType = InputType.ANY
    notes: str | None = None


# =============================================================================
# FUNCTION REGISTRY - Generated from CSV
# =============================================================================

ALL_FUNCTIONS: dict[str, FunctionMeta] = {
    "!=": FunctionMeta("!=", ReturnType.BOOLEAN, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, "<>", MappingType.DIRECT, "NOT_EQUALS", InputType.ANY),
    "%": FunctionMeta("%", ReturnType.NUMERIC, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, "MOD", MappingType.DIRECT, "MODULO", InputType.ANY),
    "&": FunctionMeta("&", ReturnType.BOOLEAN, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, "AND", MappingType.DIRECT, "AND", InputType.ANY),
    "*": FunctionMeta("*", ReturnType.NUMERIC, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, "*", MappingType.DIRECT, "MULTIPLY", InputType.ANY),
    "+": FunctionMeta("+", ReturnType.NUMERIC, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, "+", MappingType.DIRECT, "ADD", InputType.ANY),
    "-": FunctionMeta("-", ReturnType.NUMERIC, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, "-", MappingType.DIRECT, "SUBTRACT", InputType.ANY),
    "/": FunctionMeta("/", ReturnType.NUMERIC, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, "/", MappingType.DIRECT, "DIVIDE", InputType.ANY),
    "<": FunctionMeta("<", ReturnType.BOOLEAN, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, "<", MappingType.DIRECT, "LESS_THAN", InputType.ANY),
    "<=": FunctionMeta("<=", ReturnType.BOOLEAN, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, "<=", MappingType.DIRECT, "LESS_EQUAL", InputType.ANY),
    "==": FunctionMeta("==", ReturnType.BOOLEAN, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, "=", MappingType.DIRECT, "EQUALS", InputType.ANY),
    ">": FunctionMeta(">", ReturnType.BOOLEAN, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, ">", MappingType.DIRECT, "GREATER_THAN", InputType.ANY),
    ">=": FunctionMeta(">=", ReturnType.BOOLEAN, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, ">=", MappingType.DIRECT, "GREATER_EQUAL", InputType.ANY),
    "abs": FunctionMeta("abs", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "ABS", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "acos": FunctionMeta("acos", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "ACOS", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "acosh": FunctionMeta("acosh", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "ACOSH", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "add_months": FunctionMeta("add_months", ReturnType.DATE, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DATEADD(month)", MappingType.TRANSFORM, "DATE_ADD", InputType.DATE),
    "approx_count_distinct": FunctionMeta("approx_count_distinct", ReturnType.INTEGER, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "APPROX_COUNT_DISTINCT", MappingType.DIRECT, "COUNT_DISTINCT", InputType.NUMERIC),
    "array": FunctionMeta("array", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_CONSTRUCT", MappingType.DIRECT, "FUNCTION_CALL", InputType.ARRAY),
    "array_contains": FunctionMeta("array_contains", ReturnType.BOOLEAN, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_CONTAINS", MappingType.DIRECT, "CONTAINS", InputType.ARRAY),
    "array_distinct": FunctionMeta("array_distinct", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_DISTINCT", MappingType.DIRECT, "FUNCTION_CALL", InputType.ARRAY),
    "array_except": FunctionMeta("array_except", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_EXCEPT", MappingType.DIRECT, "FUNCTION_CALL", InputType.ARRAY),
    "array_intersect": FunctionMeta("array_intersect", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_INTERSECTION", MappingType.DIRECT, "FUNCTION_CALL", InputType.ARRAY),
    "array_join": FunctionMeta("array_join", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_TO_STRING", MappingType.DIRECT, "CONCAT", InputType.ARRAY),
    "array_max": FunctionMeta("array_max", ReturnType.SAME_AS_INPUT, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_MAX", MappingType.DIRECT, "MAX", InputType.ARRAY),
    "array_min": FunctionMeta("array_min", ReturnType.SAME_AS_INPUT, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_MIN", MappingType.DIRECT, "MIN", InputType.ARRAY),
    "array_position": FunctionMeta("array_position", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_POSITION", MappingType.DIRECT, "FUNCTION_CALL", InputType.ARRAY),
    "array_remove": FunctionMeta("array_remove", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_REMOVE", MappingType.DIRECT, "FUNCTION_CALL", InputType.ARRAY),
    "array_repeat": FunctionMeta("array_repeat", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.ARRAY),
    "array_size": FunctionMeta("array_size", ReturnType.INTEGER, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_SIZE", MappingType.DIRECT, "LENGTH", InputType.ARRAY),
    "array_sort": FunctionMeta("array_sort", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_SORT", MappingType.DIRECT, "FUNCTION_CALL", InputType.ARRAY),
    "array_union": FunctionMeta("array_union", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_CAT + ARRAY_DISTINCT", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.ARRAY),
    "arrays_overlap": FunctionMeta("arrays_overlap", ReturnType.BOOLEAN, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAYS_OVERLAP", MappingType.DIRECT, "FUNCTION_CALL", InputType.ARRAY),
    "arrays_zip": FunctionMeta("arrays_zip", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.ARRAY),
    "ascii": FunctionMeta("ascii", ReturnType.INTEGER, BehaviorPattern.STRING_OP, Category.STRING, "ASCII", MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "asin": FunctionMeta("asin", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "ASIN", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "asinh": FunctionMeta("asinh", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "ASINH", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "assert_true": FunctionMeta("assert_true", ReturnType.BOOLEAN, BehaviorPattern.UNKNOWN, Category.MISC, "CASE WHEN + error", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.ANY),
    "astype": FunctionMeta("astype", ReturnType.SAME_AS_INPUT, BehaviorPattern.TYPE_CONVERSION, Category.TYPE_CONVERSION, "CAST(col AS type)", MappingType.DIRECT, "CAST", InputType.ANY),
    "atan": FunctionMeta("atan", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "ATAN", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "atan2": FunctionMeta("atan2", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "ATAN2", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "atanh": FunctionMeta("atanh", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "ATANH", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "avg": FunctionMeta("avg", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "AVG", MappingType.DIRECT, "AVG", InputType.NUMERIC),
    "base64": FunctionMeta("base64", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "BASE64_ENCODE", MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "between": FunctionMeta("between", ReturnType.BOOLEAN, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "BETWEEN", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "bin": FunctionMeta("bin", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.STRING),
    "bit_length": FunctionMeta("bit_length", ReturnType.INTEGER, BehaviorPattern.STRING_OP, Category.STRING, "BIT_LENGTH", MappingType.DIRECT, "LENGTH", InputType.STRING),
    "bitwiseand": FunctionMeta("bitwiseAND", ReturnType.INTEGER, BehaviorPattern.UNKNOWN, Category.MISC, "BITAND", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "bitwisenot": FunctionMeta("bitwiseNOT", ReturnType.INTEGER, BehaviorPattern.UNKNOWN, Category.MISC, "BITNOT", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "bitwiseor": FunctionMeta("bitwiseOR", ReturnType.INTEGER, BehaviorPattern.UNKNOWN, Category.MISC, "BITOR", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "bitwisexor": FunctionMeta("bitwiseXOR", ReturnType.INTEGER, BehaviorPattern.UNKNOWN, Category.MISC, "BITXOR", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "bround": FunctionMeta("bround", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "ROUND", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.NUMERIC),
    "cast": FunctionMeta("cast", ReturnType.SAME_AS_INPUT, BehaviorPattern.TYPE_CONVERSION, Category.TYPE_CONVERSION, "CAST(col AS type)", MappingType.DIRECT, "CAST", InputType.ANY),
    "cbrt": FunctionMeta("cbrt", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "CBRT", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "ceil": FunctionMeta("ceil", ReturnType.INTEGER, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "CEIL", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "ceiling": FunctionMeta("ceiling", ReturnType.INTEGER, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "CEILING", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "coalesce": FunctionMeta("coalesce", ReturnType.SAME_AS_INPUT, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "COALESCE", MappingType.DIRECT, "COALESCE", InputType.ANY),
    "col": FunctionMeta("col", ReturnType.SAME_AS_INPUT, BehaviorPattern.COLUMN_REF, Category.COLUMN_REFERENCE, "Column reference", MappingType.DIRECT, "COLUMN_REF", InputType.ANY),
    "collect_list": FunctionMeta("collect_list", ReturnType.ARRAY, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "ARRAY_AGG", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "collect_set": FunctionMeta("collect_set", ReturnType.ARRAY, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "ARRAY_AGG DISTINCT", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.NUMERIC),
    "column": FunctionMeta("column", ReturnType.SAME_AS_INPUT, BehaviorPattern.COLUMN_REF, Category.COLUMN_REFERENCE, "Column reference", MappingType.DIRECT, "COLUMN_REF", InputType.ANY),
    "concat": FunctionMeta("concat", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "CONCAT", MappingType.DIRECT, "CONCAT", InputType.STRING),
    "concat_ws": FunctionMeta("concat_ws", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "CONCAT_WS", MappingType.DIRECT, "CONCAT", InputType.STRING),
    "contains": FunctionMeta("contains", ReturnType.BOOLEAN, BehaviorPattern.STRING_OP, Category.STRING, "CONTAINS", MappingType.DIRECT, "CONTAINS", InputType.STRING),
    "conv": FunctionMeta("conv", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.STRING),
    "corr": FunctionMeta("corr", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "CORR", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "cos": FunctionMeta("cos", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "COS", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "cosh": FunctionMeta("cosh", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "COSH", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "cot": FunctionMeta("cot", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "COT", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "count": FunctionMeta("count", ReturnType.INTEGER, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "COUNT", MappingType.DIRECT, "COUNT", InputType.NUMERIC),
    "count_distinct": FunctionMeta("count_distinct", ReturnType.INTEGER, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "COUNT(DISTINCT ...)", MappingType.DIRECT, "COUNT_DISTINCT", InputType.NUMERIC),
    "countdistinct": FunctionMeta("countDistinct", ReturnType.INTEGER, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "COUNT(DISTINCT)", MappingType.DIRECT, "COUNT_DISTINCT", InputType.NUMERIC),
    "covar_pop": FunctionMeta("covar_pop", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "COVAR_POP", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "covar_samp": FunctionMeta("covar_samp", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "COVAR_SAMP", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "crc32": FunctionMeta("crc32", ReturnType.INTEGER, BehaviorPattern.UNKNOWN, Category.HASH, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.ANY),
    "create_map": FunctionMeta("create_map", ReturnType.MAP, BehaviorPattern.COLLECTION_OP, Category.MAP, "OBJECT_CONSTRUCT", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.MAP),
    "csc": FunctionMeta("csc", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "1/SIN", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "cume_dist": FunctionMeta("cume_dist", ReturnType.NUMERIC, BehaviorPattern.WINDOW, Category.WINDOW, "CUME_DIST", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "current_date": FunctionMeta("current_date", ReturnType.DATE, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "CURRENT_DATE", MappingType.DIRECT, "CURRENT_DATE", InputType.DATE),
    "current_timestamp": FunctionMeta("current_timestamp", ReturnType.TIMESTAMP, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "CURRENT_TIMESTAMP", MappingType.DIRECT, "CURRENT_TIMESTAMP", InputType.TIMESTAMP),
    "date_add": FunctionMeta("date_add", ReturnType.DATE, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DATEADD(day)", MappingType.TRANSFORM, "DATE_ADD", InputType.DATE),
    "date_format": FunctionMeta("date_format", ReturnType.STRING, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "TO_VARCHAR", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.TIMESTAMP),
    "date_part": FunctionMeta("date_part", ReturnType.TIMESTAMP, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DATE_PART", MappingType.DIRECT, "EXTRACT", InputType.TIMESTAMP),
    "date_sub": FunctionMeta("date_sub", ReturnType.DATE, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DATEADD(day) with negative", MappingType.TRANSFORM, "DATE_ADD", InputType.DATE),
    "date_trunc": FunctionMeta("date_trunc", ReturnType.TIMESTAMP, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DATE_TRUNC", MappingType.DIRECT, "DATE_TRUNC", InputType.TIMESTAMP),
    "datediff": FunctionMeta("datediff", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DATEDIFF(day)", MappingType.TRANSFORM, "DATE_DIFF", InputType.DATE),
    "datepart": FunctionMeta("datepart", ReturnType.TIMESTAMP, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DATE_PART", MappingType.DIRECT, "EXTRACT", InputType.TIMESTAMP),
    "day": FunctionMeta("day", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DAY", MappingType.DIRECT, "EXTRACT", InputType.DATE),
    "dayofmonth": FunctionMeta("dayofmonth", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DAY", MappingType.DIRECT, "EXTRACT", InputType.DATE),
    "dayofweek": FunctionMeta("dayofweek", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DAYOFWEEK", MappingType.DIRECT, "EXTRACT", InputType.DATE),
    "dayofyear": FunctionMeta("dayofyear", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DAYOFYEAR", MappingType.DIRECT, "EXTRACT", InputType.DATE),
    "decode": FunctionMeta("decode", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.STRING),
    "degrees": FunctionMeta("degrees", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "DEGREES", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "dense_rank": FunctionMeta("dense_rank", ReturnType.INTEGER, BehaviorPattern.WINDOW, Category.WINDOW, "DENSE_RANK", MappingType.DIRECT, "DENSE_RANK", InputType.ANY),
    "element_at": FunctionMeta("element_at", ReturnType.SAME_AS_INPUT, BehaviorPattern.UNKNOWN, Category.MISC, "GET", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "encode": FunctionMeta("encode", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.STRING),
    "endswith": FunctionMeta("endswith", ReturnType.BOOLEAN, BehaviorPattern.STRING_OP, Category.STRING, "ENDSWITH", MappingType.DIRECT, "ENDS_WITH", InputType.STRING),
    "eqnullsafe": FunctionMeta("eqNullSafe", ReturnType.SAME_AS_INPUT, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "IS NOT DISTINCT FROM", MappingType.DIRECT, "EQUALS", InputType.ANY),
    "exp": FunctionMeta("exp", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "EXP", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "explode": FunctionMeta("explode", ReturnType.SAME_AS_INPUT, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "LATERAL FLATTEN", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.ARRAY),
    "explode_outer": FunctionMeta("explode_outer", ReturnType.SAME_AS_INPUT, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "LATERAL FLATTEN OUTER", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.ARRAY),
    "expm1": FunctionMeta("expm1", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "EXP(col) - 1", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.NUMERIC),
    "expr": FunctionMeta("expr", ReturnType.SAME_AS_INPUT, BehaviorPattern.UNKNOWN, Category.MISC, "SQL expression", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "extract": FunctionMeta("extract", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "EXTRACT", MappingType.DIRECT, "EXTRACT", InputType.TIMESTAMP),
    "factorial": FunctionMeta("factorial", ReturnType.INTEGER, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "FACTORIAL", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "first": FunctionMeta("first", ReturnType.SAME_AS_INPUT, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "ANY_VALUE", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.NUMERIC),
    "flatten": FunctionMeta("flatten", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "FLATTEN", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.ARRAY),
    "floor": FunctionMeta("floor", ReturnType.INTEGER, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "FLOOR", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "format_number": FunctionMeta("format_number", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "TO_VARCHAR", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.STRING),
    "format_string": FunctionMeta("format_string", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.STRING),
    "from_csv": FunctionMeta("from_csv", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.STRING),
    "from_json": FunctionMeta("from_json", ReturnType.STRUCT, BehaviorPattern.JSON_OP, Category.JSON, "PARSE_JSON", MappingType.TRANSFORM, "CAST", InputType.JSON),
    "from_unixtime": FunctionMeta("from_unixtime", ReturnType.STRING, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "TO_TIMESTAMP", MappingType.TRANSFORM, "CAST", InputType.TIMESTAMP),
    "from_utc_timestamp": FunctionMeta("from_utc_timestamp", ReturnType.TIMESTAMP, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "CONVERT_TIMEZONE", MappingType.TRANSFORM, "CAST", InputType.TIMESTAMP),
    "get_json_object": FunctionMeta("get_json_object", ReturnType.STRING, BehaviorPattern.JSON_OP, Category.JSON, "GET_PATH", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.JSON),
    "getfield": FunctionMeta("getField", ReturnType.SAME_AS_INPUT, BehaviorPattern.UNKNOWN, Category.MISC, "col:field", MappingType.DIRECT, "COLUMN_REF", InputType.ANY),
    "getitem": FunctionMeta("getItem", ReturnType.SAME_AS_INPUT, BehaviorPattern.UNKNOWN, Category.MISC, "col[key] / GET", MappingType.DIRECT, "COLUMN_REF", InputType.ANY),
    "greatest": FunctionMeta("greatest", ReturnType.SAME_AS_INPUT, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "GREATEST", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "grouping": FunctionMeta("grouping", ReturnType.INTEGER, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "GROUPING", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "grouping_id": FunctionMeta("grouping_id", ReturnType.INTEGER, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "GROUPING_ID", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "hash": FunctionMeta("hash", ReturnType.INTEGER, BehaviorPattern.UNKNOWN, Category.HASH, "HASH", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "hex": FunctionMeta("hex", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "HEX_ENCODE", MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "hour": FunctionMeta("hour", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "HOUR", MappingType.DIRECT, "EXTRACT", InputType.TIMESTAMP),
    "hypot": FunctionMeta("hypot", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "SQRT(POW(x 2)+POW(y 2))", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.NUMERIC),
    "ifnull": FunctionMeta("ifnull", ReturnType.SAME_AS_INPUT, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "IFNULL", MappingType.DIRECT, "COALESCE", InputType.ANY),
    "initcap": FunctionMeta("initcap", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "INITCAP", MappingType.DIRECT, "UPPER", InputType.STRING),
    "instr": FunctionMeta("instr", ReturnType.INTEGER, BehaviorPattern.STRING_OP, Category.STRING, "POSITION", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.STRING),
    "isin": FunctionMeta("isin", ReturnType.BOOLEAN, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "IN (...)", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "isnan": FunctionMeta("isnan", ReturnType.BOOLEAN, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "col != col", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.ANY),
    "isnotnull": FunctionMeta("isNotNull", ReturnType.BOOLEAN, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "IS NOT NULL", MappingType.DIRECT, "IS_NOT_NULL", InputType.ANY),
    "isnull": FunctionMeta("isNull", ReturnType.BOOLEAN, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "IS NULL", MappingType.DIRECT, "IS_NULL", InputType.ANY),
    "isnull": FunctionMeta("isnull", ReturnType.BOOLEAN, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "IS NULL", MappingType.DIRECT, "IS_NULL", InputType.ANY),
    "json_tuple": FunctionMeta("json_tuple", ReturnType.STRING, BehaviorPattern.JSON_OP, Category.JSON, "GET_PATH for each", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.JSON),
    "kurtosis": FunctionMeta("kurtosis", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "KURTOSIS", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "lag": FunctionMeta("lag", ReturnType.SAME_AS_INPUT, BehaviorPattern.WINDOW, Category.WINDOW, "LAG", MappingType.DIRECT, "LAG", InputType.ANY),
    "last": FunctionMeta("last", ReturnType.SAME_AS_INPUT, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "ANY_VALUE", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.NUMERIC),
    "last_day": FunctionMeta("last_day", ReturnType.DATE, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "LAST_DAY", MappingType.DIRECT, "FUNCTION_CALL", InputType.DATE),
    "lead": FunctionMeta("lead", ReturnType.SAME_AS_INPUT, BehaviorPattern.WINDOW, Category.WINDOW, "LEAD", MappingType.DIRECT, "LEAD", InputType.ANY),
    "least": FunctionMeta("least", ReturnType.SAME_AS_INPUT, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "LEAST", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "length": FunctionMeta("length", ReturnType.INTEGER, BehaviorPattern.STRING_OP, Category.STRING, "LENGTH", MappingType.DIRECT, "LENGTH", InputType.STRING),
    "levenshtein": FunctionMeta("levenshtein", ReturnType.INTEGER, BehaviorPattern.STRING_OP, Category.STRING, "EDITDISTANCE", MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "like": FunctionMeta("like", ReturnType.BOOLEAN, BehaviorPattern.STRING_OP, Category.STRING, "LIKE", MappingType.DIRECT, "LIKE", InputType.STRING),
    "lit": FunctionMeta("lit", ReturnType.SAME_AS_INPUT, BehaviorPattern.COLUMN_REF, Category.COLUMN_REFERENCE, "literal value", MappingType.DIRECT, "LITERAL", InputType.ANY),
    "locate": FunctionMeta("locate", ReturnType.INTEGER, BehaviorPattern.STRING_OP, Category.STRING, "POSITION", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.STRING),
    "log": FunctionMeta("log", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "LN / LOG", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "log10": FunctionMeta("log10", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "LOG(10)", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.NUMERIC),
    "log1p": FunctionMeta("log1p", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "LN(col + 1)", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.NUMERIC),
    "log2": FunctionMeta("log2", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "LOG(2)", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.NUMERIC),
    "lower": FunctionMeta("lower", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "LOWER", MappingType.DIRECT, "LOWER", InputType.STRING),
    "lpad": FunctionMeta("lpad", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "LPAD", MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "ltrim": FunctionMeta("ltrim", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "LTRIM", MappingType.DIRECT, "TRIM", InputType.STRING),
    "make_date": FunctionMeta("make_date", ReturnType.DATE, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DATE_FROM_PARTS", MappingType.DIRECT, "FUNCTION_CALL", InputType.DATE),
    "make_timestamp": FunctionMeta("make_timestamp", ReturnType.TIMESTAMP, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "TIMESTAMP_FROM_PARTS", MappingType.DIRECT, "FUNCTION_CALL", InputType.TIMESTAMP),
    "map_concat": FunctionMeta("map_concat", ReturnType.MAP, BehaviorPattern.COLLECTION_OP, Category.MAP, "OBJECT_INSERT", MappingType.TRANSFORM, "CONCAT", InputType.MAP),
    "map_entries": FunctionMeta("map_entries", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.MAP, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.MAP),
    "map_from_arrays": FunctionMeta("map_from_arrays", ReturnType.MAP, BehaviorPattern.COLLECTION_OP, Category.MAP, "OBJECT_CONSTRUCT", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.MAP),
    "map_from_entries": FunctionMeta("map_from_entries", ReturnType.MAP, BehaviorPattern.COLLECTION_OP, Category.MAP, "OBJECT_CONSTRUCT", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.MAP),
    "map_keys": FunctionMeta("map_keys", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.MAP, "OBJECT_KEYS", MappingType.DIRECT, "FUNCTION_CALL", InputType.MAP),
    "map_values": FunctionMeta("map_values", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.MAP, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.MAP),
    "max": FunctionMeta("max", ReturnType.SAME_AS_INPUT, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "MAX", MappingType.DIRECT, "MAX", InputType.NUMERIC),
    "md5": FunctionMeta("md5", ReturnType.STRING, BehaviorPattern.UNKNOWN, Category.HASH, "MD5", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "mean": FunctionMeta("mean", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "AVG", MappingType.DIRECT, "AVG", InputType.NUMERIC),
    "min": FunctionMeta("min", ReturnType.SAME_AS_INPUT, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "MIN", MappingType.DIRECT, "MIN", InputType.NUMERIC),
    "minute": FunctionMeta("minute", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "MINUTE", MappingType.DIRECT, "EXTRACT", InputType.TIMESTAMP),
    "monotonically_increasing_id": FunctionMeta("monotonically_increasing_id", ReturnType.INTEGER, BehaviorPattern.UNKNOWN, Category.MISC, "SEQ4() / ROW_NUMBER", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.ANY),
    "month": FunctionMeta("month", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "MONTH", MappingType.DIRECT, "EXTRACT", InputType.DATE),
    "months_between": FunctionMeta("months_between", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DATEDIFF(month)", MappingType.TRANSFORM, "DATE_DIFF", InputType.DATE),
    "nanvl": FunctionMeta("nanvl", ReturnType.SAME_AS_INPUT, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "NVL with NaN check", MappingType.TRANSFORM, "COALESCE", InputType.ANY),
    "next_day": FunctionMeta("next_day", ReturnType.DATE, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "NEXT_DAY", MappingType.DIRECT, "FUNCTION_CALL", InputType.DATE),
    "now": FunctionMeta("now", ReturnType.TIMESTAMP, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "CURRENT_TIMESTAMP", MappingType.DIRECT, "CURRENT_TIMESTAMP", InputType.TIMESTAMP),
    "nth_value": FunctionMeta("nth_value", ReturnType.SAME_AS_INPUT, BehaviorPattern.WINDOW, Category.WINDOW, "NTH_VALUE", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "ntile": FunctionMeta("ntile", ReturnType.INTEGER, BehaviorPattern.WINDOW, Category.WINDOW, "NTILE", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "nullif": FunctionMeta("nullif", ReturnType.SAME_AS_INPUT, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "NULLIF", MappingType.DIRECT, "COALESCE", InputType.ANY),
    "nvl": FunctionMeta("nvl", ReturnType.SAME_AS_INPUT, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "NVL", MappingType.DIRECT, "COALESCE", InputType.ANY),
    "nvl2": FunctionMeta("nvl2", ReturnType.SAME_AS_INPUT, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "NVL2", MappingType.DIRECT, "COALESCE", InputType.ANY),
    "octet_length": FunctionMeta("octet_length", ReturnType.INTEGER, BehaviorPattern.STRING_OP, Category.STRING, "OCTET_LENGTH", MappingType.DIRECT, "LENGTH", InputType.STRING),
    "otherwise": FunctionMeta("otherwise", ReturnType.SAME_AS_INPUT, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "ELSE value END", MappingType.DIRECT, "CASE_WHEN", InputType.ANY),
    "over": FunctionMeta("over", ReturnType.INTEGER, BehaviorPattern.WINDOW, Category.WINDOW, "OVER (window_spec)", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "overlay": FunctionMeta("overlay", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "INSERT + SUBSTR", MappingType.TRANSFORM, "REPLACE", InputType.STRING),
    "percent_rank": FunctionMeta("percent_rank", ReturnType.NUMERIC, BehaviorPattern.WINDOW, Category.WINDOW, "PERCENT_RANK", MappingType.DIRECT, "RANK", InputType.ANY),
    "percentile_approx": FunctionMeta("percentile_approx", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "APPROX_PERCENTILE", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "posexplode": FunctionMeta("posexplode", ReturnType.SAME_AS_INPUT, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "FLATTEN with INDEX", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.ARRAY),
    "posexplode_outer": FunctionMeta("posexplode_outer", ReturnType.SAME_AS_INPUT, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "FLATTEN OUTER with INDEX", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.ARRAY),
    "pow": FunctionMeta("pow", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "POWER", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "quarter": FunctionMeta("quarter", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "QUARTER", MappingType.DIRECT, "EXTRACT", InputType.DATE),
    "radians": FunctionMeta("radians", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "RADIANS", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "rand": FunctionMeta("rand", ReturnType.NUMERIC, BehaviorPattern.UNKNOWN, Category.MISC, "RANDOM", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "randn": FunctionMeta("randn", ReturnType.NUMERIC, BehaviorPattern.UNKNOWN, Category.MISC, "NORMAL(0 1)", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.NUMERIC),
    "rank": FunctionMeta("rank", ReturnType.INTEGER, BehaviorPattern.WINDOW, Category.WINDOW, "RANK", MappingType.DIRECT, "RANK", InputType.ANY),
    "regexp_extract": FunctionMeta("regexp_extract", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "REGEXP_SUBSTR", MappingType.TRANSFORM, "REGEX_EXTRACT", InputType.STRING),
    "regexp_replace": FunctionMeta("regexp_replace", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "REGEXP_REPLACE", MappingType.DIRECT, "REPLACE", InputType.STRING),
    "repeat": FunctionMeta("repeat", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "REPEAT", MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "replace": FunctionMeta("replace", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "REPLACE", MappingType.DIRECT, "REPLACE", InputType.STRING),
    "reverse": FunctionMeta("reverse", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "REVERSE", MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "rint": FunctionMeta("rint", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "ROUND", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.NUMERIC),
    "rlike": FunctionMeta("rlike", ReturnType.BOOLEAN, BehaviorPattern.STRING_OP, Category.STRING, "REGEXP", MappingType.DIRECT, "REGEX_MATCH", InputType.STRING),
    "round": FunctionMeta("round", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "ROUND", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "row_number": FunctionMeta("row_number", ReturnType.INTEGER, BehaviorPattern.WINDOW, Category.WINDOW, "ROW_NUMBER", MappingType.DIRECT, "ROW_NUMBER", InputType.ANY),
    "rpad": FunctionMeta("rpad", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "RPAD", MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "rtrim": FunctionMeta("rtrim", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "RTRIM", MappingType.DIRECT, "TRIM", InputType.STRING),
    "sec": FunctionMeta("sec", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "1/COS", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "second": FunctionMeta("second", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "SECOND", MappingType.DIRECT, "EXTRACT", InputType.TIMESTAMP),
    "sentences": FunctionMeta("sentences", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, None, MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "sequence": FunctionMeta("sequence", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.ARRAY),
    "sha1": FunctionMeta("sha1", ReturnType.STRING, BehaviorPattern.UNKNOWN, Category.HASH, "SHA1", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "sha2": FunctionMeta("sha2", ReturnType.STRING, BehaviorPattern.UNKNOWN, Category.HASH, "SHA2", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "shiftleft": FunctionMeta("shiftleft", ReturnType.INTEGER, BehaviorPattern.UNKNOWN, Category.MISC, "BITSHIFTLEFT", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "shiftright": FunctionMeta("shiftright", ReturnType.INTEGER, BehaviorPattern.UNKNOWN, Category.MISC, "BITSHIFTRIGHT", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "shuffle": FunctionMeta("shuffle", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.ARRAY),
    "sign": FunctionMeta("sign", ReturnType.INTEGER, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "SIGN", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "signum": FunctionMeta("signum", ReturnType.INTEGER, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "SIGN", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "sin": FunctionMeta("sin", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "SIN", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "sinh": FunctionMeta("sinh", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "SINH", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "size": FunctionMeta("size", ReturnType.INTEGER, BehaviorPattern.UNKNOWN, Category.MISC, "ARRAY_SIZE", MappingType.DIRECT, "LENGTH", InputType.ANY),
    "skewness": FunctionMeta("skewness", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "SKEW", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "slice": FunctionMeta("slice", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_SLICE", MappingType.DIRECT, "FUNCTION_CALL", InputType.ARRAY),
    "sort_array": FunctionMeta("sort_array", ReturnType.ARRAY, BehaviorPattern.COLLECTION_OP, Category.ARRAY, "ARRAY_SORT", MappingType.DIRECT, "FUNCTION_CALL", InputType.ARRAY),
    "soundex": FunctionMeta("soundex", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "SOUNDEX", MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "split": FunctionMeta("split", ReturnType.ARRAY, BehaviorPattern.STRING_OP, Category.STRING, "SPLIT", MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "sqrt": FunctionMeta("sqrt", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "SQRT", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "startswith": FunctionMeta("startswith", ReturnType.BOOLEAN, BehaviorPattern.STRING_OP, Category.STRING, "STARTSWITH", MappingType.DIRECT, "STARTS_WITH", InputType.STRING),
    "stddev": FunctionMeta("stddev", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "STDDEV", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "stddev_pop": FunctionMeta("stddev_pop", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "STDDEV_POP", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "stddev_samp": FunctionMeta("stddev_samp", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "STDDEV_SAMP", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "struct": FunctionMeta("struct", ReturnType.STRUCT, BehaviorPattern.UNKNOWN, Category.MISC, "OBJECT_CONSTRUCT", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.ANY),
    "substr": FunctionMeta("substr", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "SUBSTR", MappingType.DIRECT, "SUBSTRING", InputType.STRING),
    "substring": FunctionMeta("substring", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "SUBSTR", MappingType.DIRECT, "SUBSTRING", InputType.STRING),
    "substring_index": FunctionMeta("substring_index", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "SPLIT + GET", MappingType.TRANSFORM, "SUBSTRING", InputType.STRING),
    "sum": FunctionMeta("sum", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "SUM", MappingType.DIRECT, "SUM", InputType.NUMERIC),
    "sumdistinct": FunctionMeta("sumDistinct", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "SUM(DISTINCT)", MappingType.DIRECT, "SUM", InputType.NUMERIC),
    "tan": FunctionMeta("tan", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "TAN", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "tanh": FunctionMeta("tanh", ReturnType.NUMERIC, BehaviorPattern.NUMERIC_OP, Category.NUMERIC, "TANH", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "timestamp_seconds": FunctionMeta("timestamp_seconds", ReturnType.TIMESTAMP, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "TO_TIMESTAMP", MappingType.TRANSFORM, "CAST", InputType.TIMESTAMP),
    "to_csv": FunctionMeta("to_csv", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "Python UDF", MappingType.UDF_REQUIRED, "FUNCTION_CALL", InputType.STRING),
    "to_date": FunctionMeta("to_date", ReturnType.DATE, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "TO_DATE", MappingType.DIRECT, "CAST", InputType.DATE),
    "to_json": FunctionMeta("to_json", ReturnType.STRING, BehaviorPattern.JSON_OP, Category.JSON, "TO_JSON", MappingType.DIRECT, "CAST", InputType.JSON),
    "to_timestamp": FunctionMeta("to_timestamp", ReturnType.TIMESTAMP, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "TO_TIMESTAMP", MappingType.DIRECT, "CAST", InputType.TIMESTAMP),
    "to_utc_timestamp": FunctionMeta("to_utc_timestamp", ReturnType.TIMESTAMP, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "CONVERT_TIMEZONE", MappingType.TRANSFORM, "CAST", InputType.TIMESTAMP),
    "translate": FunctionMeta("translate", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "TRANSLATE", MappingType.DIRECT, "REPLACE", InputType.STRING),
    "trim": FunctionMeta("trim", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "TRIM", MappingType.DIRECT, "TRIM", InputType.STRING),
    "trunc": FunctionMeta("trunc", ReturnType.NUMERIC, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DATE_TRUNC", MappingType.TRANSFORM, "DATE_TRUNC", InputType.DATE),
    "typeof": FunctionMeta("typeof", ReturnType.STRING, BehaviorPattern.TYPE_CONVERSION, Category.TYPE_CONVERSION, "TYPEOF", MappingType.DIRECT, "FUNCTION_CALL", InputType.ANY),
    "unbase64": FunctionMeta("unbase64", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "BASE64_DECODE", MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "unhex": FunctionMeta("unhex", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "HEX_DECODE", MappingType.DIRECT, "FUNCTION_CALL", InputType.STRING),
    "unix_timestamp": FunctionMeta("unix_timestamp", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "DATE_PART(epoch_second)", MappingType.TRANSFORM, "CAST", InputType.TIMESTAMP),
    "upper": FunctionMeta("upper", ReturnType.STRING, BehaviorPattern.STRING_OP, Category.STRING, "UPPER", MappingType.DIRECT, "UPPER", InputType.STRING),
    "var_pop": FunctionMeta("var_pop", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "VAR_POP", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "var_samp": FunctionMeta("var_samp", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "VAR_SAMP", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "variance": FunctionMeta("variance", ReturnType.NUMERIC, BehaviorPattern.AGGREGATION, Category.AGGREGATION, "VARIANCE", MappingType.DIRECT, "FUNCTION_CALL", InputType.NUMERIC),
    "weekofyear": FunctionMeta("weekofyear", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "WEEKOFYEAR", MappingType.DIRECT, "EXTRACT", InputType.DATE),
    "when": FunctionMeta("when", ReturnType.SAME_AS_INPUT, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "CASE WHEN", MappingType.TRANSFORM, "CASE_WHEN", InputType.ANY),
    "when": FunctionMeta("when", ReturnType.SAME_AS_INPUT, BehaviorPattern.CONDITIONAL, Category.CONDITIONAL, "CASE WHEN", MappingType.TRANSFORM, "CASE_WHEN", InputType.ANY),
    "xxhash64": FunctionMeta("xxhash64", ReturnType.INTEGER, BehaviorPattern.UNKNOWN, Category.HASH, "HASH", MappingType.TRANSFORM, "FUNCTION_CALL", InputType.ANY),
    "year": FunctionMeta("year", ReturnType.INTEGER, BehaviorPattern.DATE_TIME_OP, Category.DATE_TIME, "YEAR", MappingType.DIRECT, "EXTRACT", InputType.DATE),
    "|": FunctionMeta("|", ReturnType.BOOLEAN, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, "OR", MappingType.DIRECT, "OR", InputType.ANY),
    "~": FunctionMeta("~", ReturnType.BOOLEAN, BehaviorPattern.COLUMN_EXPR, Category.COMPARISON, "NOT", MappingType.DIRECT, "NOT", InputType.ANY),
}

# =============================================================================
# BROAD SETS - Generated from full CSV (not filtered by data_type/status)
# =============================================================================

ALL_SPARK_NAMES: frozenset[str] = frozenset({
    "!=",
    "%",
    "&",
    "*",
    "+",
    "-",
    "/",
    "<",
    "<=",
    "==",
    ">",
    ">=",
    "abs",
    "acos",
    "acosh",
    "add_months",
    "aggregate",
    "alias",
    "approx_count_distinct",
    "array",
    "array_contains",
    "array_distinct",
    "array_except",
    "array_intersect",
    "array_join",
    "array_max",
    "array_min",
    "array_position",
    "array_remove",
    "array_repeat",
    "array_size",
    "array_sort",
    "array_union",
    "arrays_overlap",
    "arrays_zip",
    "asc",
    "asc_nulls_first",
    "asc_nulls_last",
    "ascii",
    "asin",
    "asinh",
    "assert_true",
    "astype",
    "atan",
    "atan2",
    "atanh",
    "avg",
    "base64",
    "between",
    "bin",
    "bit_length",
    "bitwiseand",
    "bitwisenot",
    "bitwiseor",
    "bitwisexor",
    "broadcast",
    "bround",
    "cast",
    "cbrt",
    "ceil",
    "ceiling",
    "coalesce",
    "col",
    "collect_list",
    "collect_set",
    "column",
    "concat",
    "concat_ws",
    "contains",
    "conv",
    "corr",
    "cos",
    "cosh",
    "cot",
    "count",
    "count_distinct",
    "countdistinct",
    "covar_pop",
    "covar_samp",
    "crc32",
    "create_map",
    "csc",
    "cume_dist",
    "current_date",
    "current_timestamp",
    "date_add",
    "date_format",
    "date_part",
    "date_sub",
    "date_trunc",
    "datediff",
    "datepart",
    "day",
    "dayofmonth",
    "dayofweek",
    "dayofyear",
    "decode",
    "degrees",
    "dense_rank",
    "desc",
    "desc_nulls_first",
    "desc_nulls_last",
    "dropfields",
    "element_at",
    "encode",
    "endswith",
    "eqnullsafe",
    "exists",
    "exp",
    "explode",
    "explode_outer",
    "expm1",
    "expr",
    "extract",
    "factorial",
    "filter",
    "first",
    "flatten",
    "floor",
    "forall",
    "format_number",
    "format_string",
    "from_csv",
    "from_json",
    "from_unixtime",
    "from_utc_timestamp",
    "get_json_object",
    "getfield",
    "getitem",
    "greatest",
    "grouping",
    "grouping_id",
    "hash",
    "hex",
    "hour",
    "hypot",
    "ifnull",
    "initcap",
    "input_file_name",
    "instr",
    "isin",
    "isnan",
    "isnotnull",
    "isnull",
    "json_tuple",
    "kurtosis",
    "lag",
    "last",
    "last_day",
    "lead",
    "least",
    "length",
    "levenshtein",
    "like",
    "lit",
    "locate",
    "log",
    "log10",
    "log1p",
    "log2",
    "lower",
    "lpad",
    "ltrim",
    "make_date",
    "make_timestamp",
    "map_concat",
    "map_entries",
    "map_filter",
    "map_from_arrays",
    "map_from_entries",
    "map_keys",
    "map_values",
    "map_zip_with",
    "max",
    "md5",
    "mean",
    "min",
    "minute",
    "monotonically_increasing_id",
    "month",
    "months_between",
    "name",
    "nanvl",
    "next_day",
    "now",
    "nth_value",
    "ntile",
    "nullif",
    "nvl",
    "nvl2",
    "octet_length",
    "otherwise",
    "over",
    "overlay",
    "percent_rank",
    "percentile_approx",
    "posexplode",
    "posexplode_outer",
    "pow",
    "quarter",
    "radians",
    "raise_error",
    "rand",
    "randn",
    "rank",
    "reduce",
    "regexp_extract",
    "regexp_replace",
    "repeat",
    "replace",
    "reverse",
    "rint",
    "rlike",
    "round",
    "row_number",
    "rpad",
    "rtrim",
    "schema_of_csv",
    "schema_of_json",
    "sec",
    "second",
    "sentences",
    "sequence",
    "sha1",
    "sha2",
    "shiftleft",
    "shiftright",
    "shuffle",
    "sign",
    "signum",
    "sin",
    "sinh",
    "size",
    "skewness",
    "slice",
    "sort_array",
    "soundex",
    "spark_partition_id",
    "split",
    "sqrt",
    "startswith",
    "stddev",
    "stddev_pop",
    "stddev_samp",
    "struct",
    "substr",
    "substring",
    "substring_index",
    "sum",
    "sumdistinct",
    "tan",
    "tanh",
    "timestamp_seconds",
    "to_csv",
    "to_date",
    "to_json",
    "to_timestamp",
    "to_utc_timestamp",
    "transform",
    "translate",
    "trim",
    "trunc",
    "typeof",
    "udf",
    "unbase64",
    "unhex",
    "unix_timestamp",
    "upper",
    "var_pop",
    "var_samp",
    "variance",
    "weekofyear",
    "when",
    "withfield",
    "xxhash64",
    "year",
    "zip_with",
    "|",
    "~",
})

DF_RETURNING_METHODS: frozenset[str] = frozenset({
    "agg",
    "applyinpandas",
    "avg",
    "cache",
    "checkpoint",
    "coalesce",
    "count",
    "createdataframe",
    "createglobaltempview",
    "createorreplacetempview",
    "createtempview",
    "crossjoin",
    "crosstab",
    "csv",
    "cube",
    "describe",
    "distinct",
    "drop",
    "drop_duplicates",
    "dropduplicates",
    "dropna",
    "exceptall",
    "fillna",
    "filter",
    "format",
    "freqitems",
    "groupby",
    "hint",
    "inputfiles",
    "intersect",
    "intersectall",
    "jdbc",
    "join",
    "json",
    "limit",
    "load",
    "localcheckpoint",
    "mapinpandas",
    "max",
    "mean",
    "min",
    "option",
    "options",
    "orc",
    "orderby",
    "parquet",
    "persist",
    "pivot",
    "range",
    "read",
    "repartition",
    "repartitionbyrange",
    "replace",
    "rollup",
    "sample",
    "schema",
    "select",
    "selectexpr",
    "sort",
    "sortwithinpartitions",
    "sql",
    "subtract",
    "sum",
    "summary",
    "table",
    "text",
    "todf",
    "topandas",
    "transform",
    "union",
    "unionall",
    "unionbyname",
    "unpersist",
    "unpivot",
    "where",
    "withcolumn",
    "withcolumnrenamed",
    "withmetadata",
})

# All DataFrame methods in original casing (for duck-typing inference)
ALL_DATAFRAME_METHODS: frozenset[str] = frozenset({
    "agg",
    "alias",
    "cache",
    "checkpoint",
    "coalesce",
    "colRegex",
    "collect",
    "count",
    "createGlobalTempView",
    "createOrReplaceTempView",
    "createTempView",
    "crossJoin",
    "crosstab",
    "cube",
    "describe",
    "distinct",
    "drop",
    "dropDuplicates",
    "drop_duplicates",
    "dropna",
    "exceptAll",
    "explain",
    "fillna",
    "filter",
    "first",
    "foreach",
    "foreachPartition",
    "freqItems",
    "groupBy",
    "groupby",
    "head",
    "hint",
    "inputFiles",
    "intersect",
    "intersectAll",
    "isEmpty",
    "isLocal",
    "join",
    "limit",
    "localCheckpoint",
    "mapInPandas",
    "orderBy",
    "persist",
    "printSchema",
    "repartition",
    "repartitionByRange",
    "replace",
    "rollup",
    "sameSemantics",
    "sample",
    "select",
    "selectExpr",
    "semanticHash",
    "show",
    "sort",
    "sortWithinPartitions",
    "subtract",
    "summary",
    "tail",
    "take",
    "toDF",
    "toLocalIterator",
    "toPandas",
    "transform",
    "union",
    "unionAll",
    "unionByName",
    "unpersist",
    "unpivot",
    "where",
    "withColumn",
    "withColumnRenamed",
    "withMetadata",
})

# SparkSession methods/attributes in original casing (for duck-typing inference)
SPARK_SESSION_METHODS: frozenset[str] = frozenset({
    "catalog",
    "createDataFrame",
    "range",
    "read",
    "sql",
    "table",
    "udf",
})


# =============================================================================
# CONVENIENCE SETS - For fast membership testing
# =============================================================================

RETURNS_INTEGER: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.return_type == ReturnType.INTEGER
)

RETURNS_NUMERIC: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.return_type in (ReturnType.NUMERIC, ReturnType.INTEGER, ReturnType.DECIMAL)
)

RETURNS_STRING: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.return_type == ReturnType.STRING
)

RETURNS_BOOLEAN: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.return_type == ReturnType.BOOLEAN
)

RETURNS_DATE: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.return_type == ReturnType.DATE
)

RETURNS_TIMESTAMP: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.return_type == ReturnType.TIMESTAMP
)

AGGREGATION_PATTERN: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.pattern == BehaviorPattern.AGGREGATION
)

WINDOW_PATTERN: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.pattern == BehaviorPattern.WINDOW
)

ALL_FUNCTION_NAMES: frozenset[str] = frozenset(ALL_FUNCTIONS.keys())

# --- Category-based sets (for detector.py and other consumers) ---

STRING_OP_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.category == Category.STRING
)

DATE_TIME_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.category == Category.DATE_TIME
)

NUMERIC_OP_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.category == Category.NUMERIC
)

CONDITIONAL_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.category == Category.CONDITIONAL
)

COLLECTION_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.category in (Category.ARRAY, Category.MAP, Category.JSON)
)

HASH_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.category == Category.HASH
)

# --- Mapping-type sets (for rules_engine.py) ---

DIRECT_MAPPED: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.mapping_type == MappingType.DIRECT
)

TRANSFORM_MAPPED: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.mapping_type == MappingType.TRANSFORM
)

UDF_REQUIRED_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.mapping_type == MappingType.UDF_REQUIRED
)

BLOCKER_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.mapping_type == MappingType.BLOCKER
)

NO_OP_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.mapping_type == MappingType.NO_OP
)

# --- Input-type sets (for schema_tracker.py type inference) ---

NUMERIC_INPUT_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.input_type == InputType.NUMERIC
)

STRING_INPUT_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.input_type == InputType.STRING
)

DATE_INPUT_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.input_type == InputType.DATE
)

TIMESTAMP_INPUT_FUNCS: frozenset[str] = frozenset(
    name for name, fn in ALL_FUNCTIONS.items()
    if fn.input_type == InputType.TIMESTAMP
)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_function_meta(func_name: str) -> FunctionMeta | None:
    """Get complete metadata for a function."""
    return ALL_FUNCTIONS.get(func_name.lower())


def get_return_type(func_name: str) -> ReturnType:
    """Get the return type of a function."""
    fn = ALL_FUNCTIONS.get(func_name.lower())
    return fn.return_type if fn else ReturnType.UNKNOWN


def get_pattern(func_name: str) -> BehaviorPattern:
    """Get the behavioral pattern of a function."""
    fn = ALL_FUNCTIONS.get(func_name.lower())
    return fn.pattern if fn else BehaviorPattern.UNKNOWN


def get_snowflake_name(func_name: str) -> str:
    """Get the Snowflake equivalent function name."""
    fn = ALL_FUNCTIONS.get(func_name.lower())
    if fn:
        return fn.snowflake_name or func_name.upper()
    return func_name.upper()


def returns_numeric(func_name: str) -> bool:
    return func_name.lower() in RETURNS_NUMERIC


def returns_integer(func_name: str) -> bool:
    return func_name.lower() in RETURNS_INTEGER


def returns_string(func_name: str) -> bool:
    return func_name.lower() in RETURNS_STRING


def returns_boolean(func_name: str) -> bool:
    return func_name.lower() in RETURNS_BOOLEAN


def returns_date(func_name: str) -> bool:
    return func_name.lower() in RETURNS_DATE


def returns_timestamp(func_name: str) -> bool:
    return func_name.lower() in RETURNS_TIMESTAMP


def is_aggregation(func_name: str) -> bool:
    return func_name.lower() in AGGREGATION_PATTERN


def is_window_function(func_name: str) -> bool:
    return func_name.lower() in WINDOW_PATTERN


def is_known_function(func_name: str) -> bool:
    return func_name.lower() in ALL_FUNCTION_NAMES


def infer_type_from_expression(expr_text: str) -> ReturnType:
    """Infer return type from expression containing function calls."""
    expr_lower = expr_text.lower()
    for name, fn in ALL_FUNCTIONS.items():
        if f"{name}(" in expr_lower or f"{name}()" in expr_lower:
            if fn.return_type != ReturnType.SAME_AS_INPUT:
                return fn.return_type
    return ReturnType.UNKNOWN


def can_infer_type(expr_text: str) -> bool:
    """Check if type can be inferred from expression."""
    return infer_type_from_expression(expr_text) != ReturnType.UNKNOWN


def print_registry_stats() -> None:
    """Print statistics about the function registry."""
    print(f"Total functions: {len(ALL_FUNCTIONS)}")
    print("\nBy Return Type:")
    type_counts: dict[ReturnType, int] = {}
    for fn in ALL_FUNCTIONS.values():
        type_counts[fn.return_type] = type_counts.get(fn.return_type, 0) + 1
    for rt, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {rt.value}: {count}")
    print("\nBy Pattern:")
    pattern_counts: dict[BehaviorPattern, int] = {}
    for fn in ALL_FUNCTIONS.values():
        pattern_counts[fn.pattern] = pattern_counts.get(fn.pattern, 0) + 1
    for pat, count in sorted(pattern_counts.items(), key=lambda x: -x[1]):
        print(f"  {pat.value}: {count}")


if __name__ == "__main__":
    print_registry_stats()
