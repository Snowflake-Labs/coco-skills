"""
Rules Engine for Spark-to-Snowflake function mappings.

This module contains:
- Function mappings (Spark function → Snowflake equivalent)
- Operation classifications (what can be translated to SQL vs UDF)
- The RulesEngine class that evaluates feasibility
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel

from warp_core.ir.pyspark_models import (
    FeasibilityLevel,
    SparkCategory,
    TargetType,
)
from warp_core.spark_functions import (
    ALL_FUNCTIONS,
    MappingType as CsvMappingType,
)

# =============================================================================
# Function Mapping Models
# =============================================================================


class MappingCategory(str, Enum):
    """Category of function mapping."""

    DIRECT = "direct"  # 1:1 mapping, no changes needed
    TRANSFORM = "transform"  # Mapping exists but syntax differs
    UDF_REQUIRED = "udf_required"  # Must be wrapped in Python UDF
    BLOCKER = "blocker"  # Cannot be migrated


class FunctionMapping(BaseModel):
    """Mapping from a Spark function to its Snowflake equivalent."""

    spark_function: str
    snowflake_function: str | None  # None = requires UDF or is blocker
    category: MappingCategory
    notes: str | None = None


class OperationClassification(BaseModel):
    """Classification of a Spark operation for migration."""

    spark_category: SparkCategory
    operation_name: str
    snowflake_target: TargetType
    feasibility: FeasibilityLevel
    notes: str | None = None
    requires_refactoring: bool = False
    performance_risk: bool = False


# =============================================================================
# Spark to Snowflake Function Mappings
# =============================================================================

_CSV_TO_MAPPING_CATEGORY: dict[CsvMappingType, MappingCategory] = {
    CsvMappingType.DIRECT: MappingCategory.DIRECT,
    CsvMappingType.TRANSFORM: MappingCategory.TRANSFORM,
    CsvMappingType.UDF_REQUIRED: MappingCategory.UDF_REQUIRED,
    CsvMappingType.BLOCKER: MappingCategory.BLOCKER,
    CsvMappingType.NO_OP: MappingCategory.DIRECT,
    CsvMappingType.UNKNOWN: MappingCategory.TRANSFORM,
}


def _build_function_mappings() -> dict[str, FunctionMapping]:
    """Build function mappings from the CSV-derived registry (spark_functions.py).

    The CSV is the single source of truth for Spark→Snowflake function names
    and mapping categories.  Manual overrides below add notes for edge cases
    that the CSV columns cannot express.
    """
    mappings: dict[str, FunctionMapping] = {}

    for name, fn in ALL_FUNCTIONS.items():
        if not fn.snowflake_name:
            continue
        cat = _CSV_TO_MAPPING_CATEGORY.get(fn.mapping_type, MappingCategory.TRANSFORM)
        mappings[name] = FunctionMapping(
            spark_function=f"{fn.name}(...)",
            snowflake_function=fn.snowflake_name if fn.snowflake_name != "Python UDF" else None,
            category=MappingCategory.UDF_REQUIRED if fn.snowflake_name == "Python UDF" else cat,
        )

    # --- Manual overrides: entries that need specific notes ---
    _notes: dict[str, str] = {
        "datediff": "Argument order differs, Snowflake requires unit",
        "date_add": "Argument order differs",
        "date_sub": "Use negative value in DATEADD",
        "date_format": "Format string may need adjustment (Java → Snowflake format)",
        "regexp_extract": "Snowflake uses different syntax for group extraction",
        "array_contains": "Argument order reversed in Snowflake",
        "array_union": "ARRAY_CAT concatenates, may need ARRAY_DISTINCT after",
        "nanvl": "Snowflake NVL handles NULL, not NaN specifically",
        "from_json": "Schema validation differs",
        "when": "Maps to CASE WHEN ... THEN ... ELSE ... END",
        "trunc": "Argument order differs",
    }
    for func_name, note in _notes.items():
        if func_name in mappings:
            m = mappings[func_name]
            mappings[func_name] = FunctionMapping(
                spark_function=m.spark_function,
                snowflake_function=m.snowflake_function,
                category=m.category,
                notes=note,
            )

    # --- Entries not in the CSV registry (UDF markers, no-ops) ---
    mappings.update({
        "udf": FunctionMapping(
            spark_function="@udf decorated function",
            snowflake_function=None,
            category=MappingCategory.UDF_REQUIRED,
            notes="Must be converted to Snowpark Python UDF",
        ),
        "pandas_udf": FunctionMapping(
            spark_function="@pandas_udf decorated function",
            snowflake_function=None,
            category=MappingCategory.UDF_REQUIRED,
            notes="Must be converted to Snowpark Vectorized UDF",
        ),
        "broadcast": FunctionMapping(
            spark_function="broadcast(df)",
            snowflake_function=None,
            category=MappingCategory.DIRECT,
            notes="No-op: Snowflake handles broadcast joins automatically",
        ),
        "repartition": FunctionMapping(
            spark_function="df.repartition(n)",
            snowflake_function=None,
            category=MappingCategory.DIRECT,
            notes="No-op: Partitioning is automatic in Snowflake",
        ),
        "coalesce_partitions": FunctionMapping(
            spark_function="df.coalesce(n)",
            snowflake_function=None,
            category=MappingCategory.DIRECT,
            notes="No-op: Partitioning is automatic in Snowflake",
        ),
        "cache": FunctionMapping(
            spark_function="df.cache()",
            snowflake_function=None,
            category=MappingCategory.DIRECT,
            notes="No-op: Result caching is automatic in Snowflake",
        ),
        "persist": FunctionMapping(
            spark_function="df.persist()",
            snowflake_function=None,
            category=MappingCategory.DIRECT,
            notes="No-op: Result caching is automatic in Snowflake",
        ),
        "unpersist": FunctionMapping(
            spark_function="df.unpersist()",
            snowflake_function=None,
            category=MappingCategory.DIRECT,
            notes="No-op: No manual cache management needed",
        ),
    })

    return mappings


SPARK_TO_SNOWFLAKE_FUNCTIONS: dict[str, FunctionMapping] = _build_function_mappings()


# =============================================================================
# Operation Classifications
# =============================================================================


OPERATION_CLASSIFICATIONS: dict[str, OperationClassification] = {
    # -------------------------------------------------------------------------
    # Relational operations - High feasibility
    # -------------------------------------------------------------------------
    "select": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="select",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL SELECT projection",
    ),
    "filter": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="filter",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL WHERE clause",
    ),
    "where": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="where",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Alias for filter, direct SQL WHERE",
    ),
    "join": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="join",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL JOIN",
    ),
    "crossJoin": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="crossJoin",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL CROSS JOIN",
    ),
    "groupBy": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="groupBy",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL GROUP BY",
    ),
    "agg": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="agg",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Aggregate functions in SELECT",
    ),
    "orderBy": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="orderBy",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL ORDER BY",
    ),
    "sort": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="sort",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Alias for orderBy",
    ),
    "distinct": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="distinct",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL DISTINCT",
    ),
    "dropDuplicates": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="dropDuplicates",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Use ROW_NUMBER() with QUALIFY",
    ),
    "union": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="union",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL UNION ALL",
    ),
    "unionAll": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="unionAll",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL UNION ALL",
    ),
    "unionByName": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="unionByName",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="SQL UNION with column reordering",
    ),
    "intersect": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="intersect",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL INTERSECT",
    ),
    "except": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="except",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL EXCEPT",
    ),
    "subtract": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="subtract",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL EXCEPT",
    ),
    "limit": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="limit",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Direct SQL LIMIT",
    ),
    "drop": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="drop",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Omit columns from SELECT",
    ),
    "withColumn": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="withColumn",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Add column in SELECT clause",
    ),
    "withColumnRenamed": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="withColumnRenamed",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Use AS alias in SELECT",
    ),
    "alias": OperationClassification(
        spark_category=SparkCategory.RELATIONAL,
        operation_name="alias",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Use AS alias",
    ),
    # -------------------------------------------------------------------------
    # Complex built-in - Medium feasibility
    # -------------------------------------------------------------------------
    "explode": OperationClassification(
        spark_category=SparkCategory.COMPLEX_BUILTIN,
        operation_name="explode",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.MEDIUM,
        notes="Use LATERAL FLATTEN in Snowflake",
    ),
    "pivot": OperationClassification(
        spark_category=SparkCategory.COMPLEX_BUILTIN,
        operation_name="pivot",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.MEDIUM,
        notes="Snowflake PIVOT syntax differs slightly",
    ),
    "unpivot": OperationClassification(
        spark_category=SparkCategory.COMPLEX_BUILTIN,
        operation_name="unpivot",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.MEDIUM,
        notes="Snowflake UNPIVOT syntax",
    ),
    "rollup": OperationClassification(
        spark_category=SparkCategory.COMPLEX_BUILTIN,
        operation_name="rollup",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.MEDIUM,
        notes="Direct SQL ROLLUP",
    ),
    "cube": OperationClassification(
        spark_category=SparkCategory.COMPLEX_BUILTIN,
        operation_name="cube",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.MEDIUM,
        notes="Direct SQL CUBE",
    ),
    # -------------------------------------------------------------------------
    # Python UDF - High feasibility (encapsulation)
    # -------------------------------------------------------------------------
    "udf": OperationClassification(
        spark_category=SparkCategory.PYTHON_UDF,
        operation_name="udf",
        snowflake_target=TargetType.PYTHON_UDF,
        feasibility=FeasibilityLevel.HIGH,
        notes="Encapsulate in @udf decorator",
        performance_risk=True,
    ),
    "pandas_udf": OperationClassification(
        spark_category=SparkCategory.PYTHON_UDF,
        operation_name="pandas_udf",
        snowflake_target=TargetType.PYTHON_UDF,
        feasibility=FeasibilityLevel.HIGH,
        notes="Vectorized UDF in Snowpark",
        performance_risk=True,
    ),
    # -------------------------------------------------------------------------
    # RDD / Low Level - Low feasibility
    # -------------------------------------------------------------------------
    "map": OperationClassification(
        spark_category=SparkCategory.RDD_LOW_LEVEL,
        operation_name="map",
        snowflake_target=TargetType.PYTHON_DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.LOW,
        notes="Requires refactoring to DataFrame API",
        requires_refactoring=True,
    ),
    "flatMap": OperationClassification(
        spark_category=SparkCategory.RDD_LOW_LEVEL,
        operation_name="flatMap",
        snowflake_target=TargetType.PYTHON_DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.LOW,
        notes="Use LATERAL FLATTEN or UDF",
        requires_refactoring=True,
    ),
    "mapPartitions": OperationClassification(
        spark_category=SparkCategory.RDD_LOW_LEVEL,
        operation_name="mapPartitions",
        snowflake_target=TargetType.PYTHON_DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.LOW,
        notes="Partition-level processing not directly supported",
        requires_refactoring=True,
    ),
    "foreach": OperationClassification(
        spark_category=SparkCategory.RDD_LOW_LEVEL,
        operation_name="foreach",
        snowflake_target=TargetType.MANUAL_REVIEW,
        feasibility=FeasibilityLevel.BLOCKER,
        notes="Side-effect operations require manual review",
    ),
    "foreachPartition": OperationClassification(
        spark_category=SparkCategory.RDD_LOW_LEVEL,
        operation_name="foreachPartition",
        snowflake_target=TargetType.MANUAL_REVIEW,
        feasibility=FeasibilityLevel.BLOCKER,
        notes="Side-effect operations require manual review",
    ),
    "reduce": OperationClassification(
        spark_category=SparkCategory.RDD_LOW_LEVEL,
        operation_name="reduce",
        snowflake_target=TargetType.MANUAL_REVIEW,
        feasibility=FeasibilityLevel.BLOCKER,
        notes="RDD reduce requires manual conversion",
    ),
    # -------------------------------------------------------------------------
    # System operations - High feasibility
    # -------------------------------------------------------------------------
    "read": OperationClassification(
        spark_category=SparkCategory.SYSTEM_OPS,
        operation_name="read",
        snowflake_target=TargetType.SNOWFLAKE_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Map to Snowflake Table or External Stage",
    ),
    "write": OperationClassification(
        spark_category=SparkCategory.SYSTEM_OPS,
        operation_name="write",
        snowflake_target=TargetType.SNOWFLAKE_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="Target Snowflake Table",
    ),
    "saveAsTable": OperationClassification(
        spark_category=SparkCategory.SYSTEM_OPS,
        operation_name="saveAsTable",
        snowflake_target=TargetType.DYNAMIC_TABLE,
        feasibility=FeasibilityLevel.HIGH,
        notes="CREATE DYNAMIC TABLE",
    ),
}


# =============================================================================
# Forbidden Libraries (Block migration)
# =============================================================================


FORBIDDEN_LIBRARIES: frozenset[str] = frozenset(
    {
        # Network/IO
        "socket",
        "requests",
        "urllib",
        "urllib3",
        "httpx",
        "aiohttp",
        "websocket",
        "grpc",
        # GPU/CUDA
        "cudf",
        "cupy",
        "torch",
        "tensorflow",
        "jax",
        "cuda",
        "pycuda",
        # System/Process
        "subprocess",
        "multiprocessing",
        "threading",
        "asyncio",
        "signal",
        # Database drivers (should use Snowflake native connections)
        "psycopg2",
        "pymysql",
        "cx_Oracle",
        "pyodbc",
        "sqlalchemy",
        # File system (local access)
        "pathlib",  # Read-only may be OK, but writing is blocked
        "shutil",
        "tempfile",
        "glob",
        # OS-level
        "os.system",
        "os.popen",
        "os.spawn",
        "ctypes",
        "cffi",
    }
)


SNOWPARK_SUPPORTED_LIBRARIES: frozenset[str] = frozenset(
    {
        # Data processing
        "pandas",
        "numpy",
        "scipy",
        # ML (supported in Snowpark)
        "scikit-learn",
        "sklearn",
        "xgboost",
        "lightgbm",
        "catboost",
        # Snowflake native
        "snowflake",
        "snowflake.snowpark",
        # Standard library (safe subset)
        "json",
        "re",
        "math",
        "decimal",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "operator",
        "string",
        "typing",
        "dataclasses",
        "enum",
        "copy",
        "hashlib",
        "base64",
        "uuid",
        "statistics",
    }
)


# =============================================================================
# Rules Engine
# =============================================================================


class AnalysisResult(BaseModel):
    """Result of analyzing a single ASG node."""

    target: Literal["SQL", "Python_UDF", "Python_DT", "None"]
    status: Literal["Ready", "Hybrid", "Blocker"]
    risk: Literal["None", "Performance", "Maintenance"] | None = None
    reason: str | None = None
    sql_fragment: str | None = None


class RulesEngine:
    """
    The Rules Engine evaluates Spark operations against Snowflake capabilities.

    It determines:
    - Whether an operation can be converted to SQL
    - Whether it requires a Python UDF
    - Whether it's a blocker requiring manual review
    """

    def __init__(self) -> None:
        self.function_mappings = SPARK_TO_SNOWFLAKE_FUNCTIONS
        self.operation_classifications = OPERATION_CLASSIFICATIONS
        self.forbidden_libraries = FORBIDDEN_LIBRARIES
        self.supported_libraries = SNOWPARK_SUPPORTED_LIBRARIES

    def get_function_mapping(self, func_name: str) -> FunctionMapping | None:
        """Get the Snowflake mapping for a Spark function."""
        return self.function_mappings.get(func_name)

    def get_operation_classification(self, op_name: str) -> OperationClassification | None:
        """Get the classification for a Spark operation."""
        return self.operation_classifications.get(op_name)

    def is_library_forbidden(self, library: str) -> bool:
        """Check if a library is forbidden in Snowpark."""
        base_module = library.split(".")[0]
        return library in self.forbidden_libraries or base_module in self.forbidden_libraries

    def is_library_supported(self, library: str) -> bool:
        """Check if a library is explicitly supported in Snowpark."""
        base_module = library.split(".")[0]
        return base_module in self.supported_libraries

    def check_imports(self, imports: list[str]) -> tuple[list[str], list[str], list[str]]:
        """
        Check a list of imports and categorize them.

        Returns:
            (supported, unknown, forbidden) - Three lists of library names
        """
        supported = []
        unknown = []
        forbidden = []

        for imp in imports:
            imp.split(".")[0]
            if self.is_library_forbidden(imp):
                forbidden.append(imp)
            elif self.is_library_supported(imp):
                supported.append(imp)
            else:
                unknown.append(imp)

        return supported, unknown, forbidden

    def analyze_operation(self, operation: str) -> AnalysisResult:
        """
        Analyze a single operation and determine its migration path.

        Args:
            operation: The Spark operation name (e.g., "select", "join", "udf")

        Returns:
            AnalysisResult with target, status, and optional details
        """
        # Check operation classification first
        if operation in self.operation_classifications:
            classification = self.operation_classifications[operation]

            if classification.feasibility == FeasibilityLevel.HIGH:
                if classification.snowflake_target in (
                    TargetType.DYNAMIC_TABLE,
                    TargetType.SNOWFLAKE_TABLE,
                    TargetType.VIEW,
                ):
                    return AnalysisResult(
                        target="SQL",
                        status="Ready",
                        reason=classification.notes,
                    )
                elif classification.snowflake_target == TargetType.PYTHON_UDF:
                    return AnalysisResult(
                        target="Python_UDF",
                        status="Hybrid",
                        risk="Performance",
                        reason=classification.notes,
                    )

            elif classification.feasibility == FeasibilityLevel.MEDIUM:
                return AnalysisResult(
                    target="SQL",
                    status="Ready",
                    risk="Maintenance",
                    reason=classification.notes,
                )

            elif classification.feasibility == FeasibilityLevel.LOW:
                return AnalysisResult(
                    target="Python_DT",
                    status="Hybrid",
                    risk="Performance",
                    reason=classification.notes,
                )

            else:  # BLOCKER
                return AnalysisResult(
                    target="None",
                    status="Blocker",
                    reason=classification.notes,
                )

        # Check function mappings
        if operation in self.function_mappings:
            mapping = self.function_mappings[operation]

            if mapping.category == MappingCategory.DIRECT:
                return AnalysisResult(
                    target="SQL",
                    status="Ready",
                    sql_fragment=mapping.snowflake_function,
                )
            elif mapping.category == MappingCategory.TRANSFORM:
                return AnalysisResult(
                    target="SQL",
                    status="Ready",
                    risk="Maintenance",
                    reason=mapping.notes,
                    sql_fragment=mapping.snowflake_function,
                )
            elif mapping.category == MappingCategory.UDF_REQUIRED:
                return AnalysisResult(
                    target="Python_UDF",
                    status="Hybrid",
                    risk="Performance",
                    reason=mapping.notes,
                )
            else:  # BLOCKER
                return AnalysisResult(
                    target="None",
                    status="Blocker",
                    reason=mapping.notes,
                )

        # Unknown operation
        return AnalysisResult(
            target="None",
            status="Blocker",
            reason=f"Unknown operation: {operation}",
        )

    def analyze_function(self, func_name: str) -> AnalysisResult:
        """
        Analyze a Spark function and determine its Snowflake equivalent.

        Args:
            func_name: The Spark function name (e.g., "sum", "coalesce", "rank")

        Returns:
            AnalysisResult with target, status, and optional SQL fragment
        """
        if func_name in self.function_mappings:
            mapping = self.function_mappings[func_name]

            if mapping.category == MappingCategory.DIRECT:
                return AnalysisResult(
                    target="SQL",
                    status="Ready",
                    sql_fragment=mapping.snowflake_function,
                )
            elif mapping.category == MappingCategory.TRANSFORM:
                return AnalysisResult(
                    target="SQL",
                    status="Ready",
                    risk="Maintenance",
                    reason=mapping.notes,
                    sql_fragment=mapping.snowflake_function,
                )
            elif mapping.category == MappingCategory.UDF_REQUIRED:
                return AnalysisResult(
                    target="Python_UDF",
                    status="Hybrid",
                    risk="Performance",
                    reason=mapping.notes,
                )
            else:  # BLOCKER
                return AnalysisResult(
                    target="None",
                    status="Blocker",
                    reason=mapping.notes,
                )

        # Unknown function - might be a UDF candidate
        return AnalysisResult(
            target="Python_UDF",
            status="Hybrid",
            risk="Performance",
            reason=f"Unknown function: {func_name} - assuming custom UDF",
        )
