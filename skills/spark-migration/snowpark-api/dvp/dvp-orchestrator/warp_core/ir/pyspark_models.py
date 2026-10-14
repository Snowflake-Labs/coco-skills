"""
Pydantic models for the Intermediate Representation (IR).

These models define the "contract" for the entire analysis pipeline:
- ASG output structure
- PDG slice representation
- Feasibility IR (the main output)
"""

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

# =============================================================================
# Enums
# =============================================================================


# Constant for global scope (module level, outside any function/class)
GLOBAL_SCOPE = "<global>"
class TargetType(str, Enum):
    """Target Snowflake construct for a Spark operation."""

    SNOWFLAKE_TABLE = "SNOWFLAKE_TABLE"
    DYNAMIC_TABLE = "DYNAMIC_TABLE"
    PYTHON_UDF = "PYTHON_UDF"
    PYTHON_DYNAMIC_TABLE = "PYTHON_DYNAMIC_TABLE"
    SNOWPARK_DATAFRAME = "SNOWPARK_DATAFRAME"  # Snowpark Python migration path
    STORED_PROCEDURE = "STORED_PROCEDURE"
    VIEW = "VIEW"
    EXTERNAL_TABLE = "EXTERNAL_TABLE"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class ComplexityRisk(str, Enum):
    """Complexity/risk level for migration."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKER = "blocker"


class SparkCategory(str, Enum):
    """Categories of Spark operations for classification."""

    RELATIONAL = "relational"
    BUILTIN_FUNCTION = "builtin_function"
    COMPLEX_BUILTIN = "complex_builtin"
    PYTHON_UDF = "python_udf"
    RDD_LOW_LEVEL = "rdd_low_level"
    SYSTEM_OPS = "system_ops"
    UNKNOWN = "unknown"


class FeasibilityLevel(str, Enum):
    """Feasibility levels for migration."""

    HIGH = "high"  # Direct conversion, minimal risk
    MEDIUM = "medium"  # Conversion possible with advanced patterns
    LOW = "low"  # Requires significant refactoring
    BLOCKER = "blocker"  # Cannot be migrated automatically


class AnalysisStatus(str, Enum):
    """Overall status of the feasibility analysis."""

    FEASIBLE = "FEASIBLE"
    FEASIBLE_WITH_PREREQUISITES = "FEASIBLE_WITH_PREREQUISITES"
    PARTIALLY_FEASIBLE = "PARTIALLY_FEASIBLE"
    NOT_FEASIBLE = "NOT_FEASIBLE"


class InferredType(str, Enum):
    """Inferred column type categories."""

    UNKNOWN = "UNKNOWN"
    NUMERIC = "NUMERIC"  # sum, avg, comparisons with numbers
    STRING = "STRING"  # lower, upper, string comparisons
    DATE = "DATE"  # year, month, date_add
    TIMESTAMP = "TIMESTAMP"  # hour, minute, to_timestamp
    BOOLEAN = "BOOLEAN"  # boolean operations
    ARRAY = "ARRAY"  # explode, array functions
    STRUCT = "STRUCT"  # struct access
    MAP = "MAP"  # map/dict types (keys(), values(), explode)


class InferenceSource(str, Enum):
    """Source of column inference."""

    EXPLICIT = "explicit"  # withColumn, alias - explicitly created
    FILTER_CONDITION = "filter_condition"  # col("x") == value
    JOIN_KEY = "join_key"  # on="col" in join
    GROUP_BY = "group_by"  # groupBy("col")
    AGGREGATION = "aggregation"  # sum("col"), count("col")
    SELECT = "select"  # select("col")
    ORDER_BY = "order_by"  # orderBy("col")
    FUNCTION_ARG = "function_arg"  # lower(col("x"))
    CATALOG = "catalog"  # from external catalog
    UDF_SEMANTIC = "udf_semantic"  # Type inferred from UDF name semantics (obfuscate -> text)
    WIDGET_DEFAULT = "widget_default"  # Type inferred from dbutils.widgets default/valid values
    WIDGET_UNUSED = "widget_unused"  # Declared but never .get()-ed (excluded from metrics)
    XREF_OUTPUT = "xref_output"  # Column propagated from a data_out with the same name
    XREF_INPUT = "xref_input"  # Column propagated from another data_in with the same name
    XREF_FUNCTION = "xref_function"  # Column propagated via shared function calls between test and production
    USAGE = "usage"  # Inferred from how the column is used in operations
    NAMING_CONVENTION = "naming_convention"  # Inferred from enterprise column naming patterns
    SCHEMA_DEFINITION = "schema_definition"  # Resolved from typed val/List[String] definitions in code
    AMBIGUOUS_JOIN = "ambiguous_join"  # Column present in multiple join inputs; cannot be attributed to one source


class InferenceConfidence(str, Enum):
    """Confidence level for inferred schema."""

    HIGH = "high"  # Explicit creation or catalog
    MEDIUM = "medium"  # Type inferred from usage pattern
    LOW = "low"  # Column exists but type unknown


class ImportType(str, Enum):
    """Type of import for classification."""

    EXTERNAL_LIBRARY = "external_library"  # Known PySpark/standard library
    CUSTOM_LIBRARY = "custom_library"  # User-defined module
    LOCAL_MODULE = "local_module"  # File in same project


class WarningSeverity(str, Enum):
    """Severity level for analysis warnings."""

    INFO = "info"  # Informational, no action required
    WARNING = "warning"  # May affect migration, review recommended
    ERROR = "error"  # Blocks migration, action required


class SourceType(str, Enum):
    """Type of source file (for entrypoint detection)."""
    
    NOTEBOOK = "notebook"  # Databricks notebook (has # COMMAND, dbutils, etc.)
    MODULE = "module"       # Python module (imported by other files)
    SCRIPT = "script"       # Standalone Python script
    UNKNOWN = "unknown"


# =============================================================================
# Control Flow Modeling - Universal Python Control Structures
# =============================================================================


class ControlType(str, Enum):
    """
    Universal control flow patterns.
    
    These abstract Python's control structures into graph topology patterns:
    - BRANCH: Conditional paths (if/elif/else, match/case)
    - LOOP: Iterative execution (for, while)
    - PROTECTED: Error handling (try/except/finally)
    - SCOPED: Context management (with statement)
    """
    BRANCH = "BRANCH"
    LOOP = "LOOP"
    PROTECTED = "PROTECTED"
    SCOPED = "SCOPED"


class ExitStrategy(str, Enum):
    """
    How control flow branches reunify after the control block.
    
    - MERGE: All branches converge to a single output (common variable assignment)
    - INDEPENDENT_SINK: Each branch writes to different destinations
    - TERMINATE: Flow ends (raise, return, sys.exit)
    """
    MERGE = "MERGE"
    INDEPENDENT_SINK = "INDEPENDENT_SINK"
    TERMINATE = "TERMINATE"


class LoopType(str, Enum):
    """
    Distinguishes loop purposes for correct translation.
    
    - CODE_GENERATION: Loop creates code artifacts (e.g., 12 columns for months)
    - DATA_ITERATION: Loop processes data iteratively
    - TABLE_ITERATION: Loop processes multiple tables/files
    """
    CODE_GENERATION = "CODE_GENERATION"
    DATA_ITERATION = "DATA_ITERATION"
    TABLE_ITERATION = "TABLE_ITERATION"


class OpaqueCode(str, Enum):
    """
    Structured codes for why a control block cannot be translated to SQL.
    
    These codes enable Phase 2/3 to make automatic decisions:
    - UNSUPPORTED_LIB → Try Python UDF
    - IO_SIDE_EFFECT → Critical error, manual intervention
    - COMPLEX_RECURSION → Stored Procedure
    - DYNAMIC_SCHEMA → Warning + manual review
    - EXTERNAL_API → Stored Procedure with External Access
    - STATEFUL_ITERATION → UDF with state accumulator
    - UNKNOWN → Generic opacity, requires analysis
    """
    UNSUPPORTED_LIB = "UNSUPPORTED_LIB"
    IO_SIDE_EFFECT = "IO_SIDE_EFFECT"
    COMPLEX_RECURSION = "COMPLEX_RECURSION"
    DYNAMIC_SCHEMA = "DYNAMIC_SCHEMA"
    EXTERNAL_API = "EXTERNAL_API"
    STATEFUL_ITERATION = "STATEFUL_ITERATION"
    UNKNOWN = "UNKNOWN"


class ControlLogic(BaseModel):
    """The condition or iterator expression for a control node."""
    
    expression: str = Field(..., description="The control expression (condition, iterator, etc.)")
    expression_ast: str | None = Field(None, description="AST representation of expression")
    engine: str = Field("PYTHON_AST", description="Expression language/engine")
    resolved_expression: str | None = Field(
        None, 
        description="Fully resolved expression including variable definitions. "
                    "E.g., if expression is 'is_latam' and is_latam = config.get('region') == 'LATAM', "
                    "resolved_expression would be the full expression."
    )


class ControlBranch(BaseModel):
    """
    A single branch within a control node.
    
    For if/else: branches are [true_branch, false_branch]
    For match/case: branches are [case_1, case_2, ..., default]
    For try/except: branches are [try_block, except_block, finally_block]
    """
    
    label: str = Field(..., description="Branch identifier (true, false, case_X, try_block, etc.)")
    condition: str | None = Field(None, description="Branch condition (for elif, case patterns)")
    steps: list[str] = Field(
        default_factory=list, 
        description="List of step IDs or transformation IDs in this branch"
    )
    sub_controls: list[str] = Field(
        default_factory=list,
        description="Nested control node IDs for complex structures"
    )
    produces_dataframe: bool = Field(
        True, description="Whether this branch produces a DataFrame output"
    )
    target_variable: str | None = Field(
        None, description="Variable name assigned in this branch (for MERGE detection)"
    )
    ssa_output_id: str | None = Field(
        None, 
        description="SSA-compliant output ID for this branch. "
                    "When target_variable is shared across branches (e.g., df_processed), "
                    "each branch gets a unique ssa_output_id (e.g., tx_015_LATAM, tx_018_STD). "
                    "This enables unambiguous lineage tracking."
    )


class ControlNode(BaseModel):
    """
    Universal control flow node for the ASG-Spark.
    
    This abstracts any Python control structure into a graph topology pattern,
    enabling Phase 2/3 to understand control flow without parsing Python syntax.
    
    Examples:
    - if/elif/else → BRANCH with 2+ branches
    - match/case → BRANCH with N case branches + default
    - for/while → LOOP with body branch + condition
    - try/except/finally → PROTECTED with try/except/finally branches
    - with → SCOPED with body branch + context metadata
    """
    
    node_id: str = Field(..., description="Unique control node identifier")
    control_type: ControlType = Field(..., description="Type of control structure")
    
    # The controlling expression
    logic: ControlLogic | None = Field(None, description="Control condition/iterator")
    
    # Branches within this control block
    branches: list[ControlBranch] = Field(
        default_factory=list, description="Conditional branches"
    )
    
    # How the branches exit
    exit_strategy: ExitStrategy = Field(
        ExitStrategy.MERGE, description="How branches reunify"
    )
    
    # For LOOP type
    loop_type: LoopType | None = Field(
        None, description="Purpose of the loop (for translation decisions)"
    )
    loop_variable: str | None = Field(
        None, description="Loop variable name (for i in ...)"
    )
    loop_iterable: str | None = Field(
        None, description="What is being iterated (range, list, DataFrame)"
    )
    
    # For SCOPED type (with statement)
    context_manager: str | None = Field(
        None, description="Context manager expression"
    )
    context_variable: str | None = Field(
        None, description="Variable bound by 'as' clause"
    )
    
    # Traceability
    source_location: "SourceLocation | None" = Field(
        None, description="Where this control structure appears in source"
    )
    
    # Analysis metadata
    affects_dataframe: bool = Field(
        True, description="Whether this control affects DataFrame lineage"
    )
    is_opaque: bool = Field(
        False, description="True if logic cannot be translated to SQL"
    )
    opaque_code: "OpaqueCode | None" = Field(
        None, description="Structured code for why this block is opaque"
    )
    opaque_reason: str | None = Field(
        None, description="Human-readable explanation of opacity"
    )
    
    # Unrolling metadata (for LOOP type)
    is_unrollable: bool = Field(
        False, description="True if loop iterates over static iterable known at compile time"
    )
    static_iterable: list[Any] | None = Field(
        None, description="The static values to unroll (e.g., ['a', 'b', 'c'] or [0, 1, 2])"
    )
    
    # Convergence metadata (for MERGE exit_strategy)
    convergence_point: str | None = Field(
        None, 
        description="ID of the first node after this control block that consumes branch outputs. "
                    "Only set when exit_strategy=MERGE and there is code after the control block."
    )
    branch_outputs: list[str] | None = Field(
        None,
        description="IDs of the last transformation node in each branch. "
                    "These are the potential inputs to the convergence_point."
    )
    merge_semantic: str | None = Field(
        None,
        description="How branch outputs should be merged at convergence_point. "
                    "Values: 'UNION_ALL' (combine all rows), 'CONDITIONAL' (runtime decides), "
                    "'EXCLUSIVE' (only one branch executes). Default is 'CONDITIONAL' for if/else."
    )
    type_reconciliation_required: bool = Field(
        False,
        description="True if branches produce columns with potentially different types. "
                    "Phase 2 must force type reconciliation at convergence_point."
    )
    type_warnings: list[str] = Field(
        default_factory=list,
        description="List of type inconsistency warnings between branches, "
                    "e.g., 'local_tax: LATAM branch has CAST, STD branch uses raw amount'"
    )
    reconciliation_strategy: str | None = Field(
        None,
        description="How Phase 2 should handle type reconciliation. "
                    "Values: 'INJECT_BEFORE_CONVERGENCE' (insert virtual ReconciliationStep before convergence_point), "
                    "'INLINE_AT_CONVERGENCE' (modify convergence_point directly). "
                    "Default recommendation: INJECT_BEFORE_CONVERGENCE to maintain separation of concerns."
    )





# =============================================================================
# Analysis Warning Model
# =============================================================================


class AnalysisWarning(BaseModel):
    """
    Structured warning for migration analysis.

    Provides detailed context about issues discovered during ASG extraction
    that may affect the migration to Snowflake.

    Example:
        AnalysisWarning(
            code="W001",
            severity=WarningSeverity.WARNING,
            node_id="tx_011",
            message="Missing source logic...",
            source_module="my_security_lib",
            suggested_action="Provide source code or create equivalent UDF"
        )
    """

    code: str = Field(..., description="Warning code (e.g., 'W001', 'W_PAR_001')")
    severity: WarningSeverity = WarningSeverity.WARNING
    node_id: str | None = Field(None, description="Related ASG node ID (e.g., 'tx_011')")
    message: str = Field(..., description="Human-readable warning description")
    source_module: str | None = Field(None, description="Related module name if applicable")
    suggested_action: str | None = Field(None, description="Recommended action to resolve")
    source_file: str | None = Field(None, description="Source file where issue occurred")
    source_line: int | None = Field(None, description="Line number in source file")
    regex_evidence: dict[str, Any] | None = Field(
        None,
        description=(
            "Structured evidence from regex fallback parsing. "
            "Contains: match_type, raw_snippet, identified_elements, "
            "failure_reason, primary_parser (ast/sqlglot)"
        ),
    )


# =============================================================================
# Source Location Models
# =============================================================================


class SourceLocation(BaseModel):
    """
    Location of a code element in the source files.

    Provides precise source tracking with:
    - pathfile: Relative path from workload_root
    - scope: Containing class/function hierarchy (e.g., "MyClass.my_method")
    - span: Line and column range (e.g., "27:9-27:60")
    """

    pathfile: str = Field(..., description="Relative path from workload_root")
    scope: str | None = Field(
        None, description="Containing class.function hierarchy, null if module-level"
    )
    span: str = Field(
        ..., description="Line:col range as 'start_line:start_col-end_line:end_col'"
    )

    def __str__(self) -> str:
        """
        Format: pathfile@scope[span] or pathfile[span] if no scope.

        Examples:
            global_transactions.py@process_bronze_to_silver[27:9-27:60]
            pipeline.py@DataProcessor.transform[45:8-50:20]
            config.py[5:1-5:30]
        """
        if self.scope:
            return f"{self.pathfile}@{self.scope}[{self.span}]"
        return f"{self.pathfile}[{self.span}]"

    @classmethod
    def create(
        cls,
        pathfile: str,
        start_line: int,
        start_col: int,
        end_line: int,
        end_col: int,
        scope: str | None = None,
    ) -> "SourceLocation":
        """
        Create a SourceLocation from individual components.
        """
        span = f"{start_line}:{start_col}-{end_line}:{end_col}"
        return cls(pathfile=pathfile, scope=scope, span=span)

    @property
    def start_line(self) -> int:
        """Extract start line from span for convenience."""
        return int(self.span.split("-")[0].split(":")[0])

    @property
    def end_line(self) -> int:
        """Extract end line from span for convenience."""
        return int(self.span.split("-")[1].split(":")[0])


# =============================================================================
# Core Models
# =============================================================================


class ImportEntry(BaseModel):
    """
    Structured import entry with alias tracking.

    Enables Name Resolution: when the lifter sees `F.col('x')`,
    it can look up that `F` maps to `pyspark.sql.functions`.

    The has_source field indicates whether the source code is available
    in the workload directory. For CUSTOM_LIBRARY imports:
    - has_source=True: We can analyze the implementation
    - has_source=False: External dependency, cannot verify migration equivalence
    """

    alias: str | None = None  # The alias (e.g., "F" for `import ... as F`)
    imported_names: list[str] = Field(default_factory=list)  # Specific names imported
    type: ImportType = ImportType.EXTERNAL_LIBRARY
    has_source: bool = Field(
        default=True,
        description="Whether source code is available in the workload (relevant for custom_library)",
    )

    # Known PySpark modules for automatic type classification
    _PYSPARK_MODULES: ClassVar[set[str]] = {
        "pyspark",
        "pyspark.sql",
        "pyspark.sql.functions",
        "pyspark.sql.types",
        "pyspark.sql.window",
        "pyspark.ml",
        "pyspark.streaming",
    }

    @classmethod
    def classify_module(cls, module: str) -> ImportType:
        """Classify a module as external, custom, or local."""
        # Check if it's a known PySpark module
        for known in cls._PYSPARK_MODULES:
            if module == known or module.startswith(f"{known}."):
                return ImportType.EXTERNAL_LIBRARY

        # Standard library modules (comprehensive list)
        stdlib = {
            # Core modules
            "os", "sys", "io", "re", "math", "time", "random", "pathlib",
            "typing", "abc", "enum", "copy", "warnings",
            # Data formats
            "json", "csv", "pickle", "struct", "base64",
            # Datetime
            "datetime", "calendar", "zoneinfo",
            # Collections and data structures
            "collections", "itertools", "functools", "operator", "dataclasses",
            "heapq", "bisect",
            # Logging and debugging
            "logging", "traceback", "pdb", "inspect",
            # Security and hashing
            "hashlib", "hmac", "secrets",
            # Concurrency
            "threading", "multiprocessing", "concurrent", "asyncio", "queue",
            # Networking
            "socket", "http", "urllib", "ssl",
            # File and archive
            "shutil", "glob", "tempfile", "gzip", "zipfile", "tarfile",
            # Testing
            "unittest", "doctest",
            # Other common
            "string", "textwrap", "difflib", "decimal", "fractions",
            "statistics", "contextlib", "weakref", "types", "uuid",
            "platform", "argparse", "getpass", "configparser",
        }
        root_module = module.split(".")[0]
        if root_module in stdlib:
            return ImportType.EXTERNAL_LIBRARY

        # If it starts with a relative path indicator, it's local
        if module.startswith("."):
            return ImportType.LOCAL_MODULE

        # Default to custom library (user-defined)
        return ImportType.CUSTOM_LIBRARY


class Column(BaseModel):
    """Schema column definition."""

    name: str = Field(..., min_length=1)
    dtype: str = Field(..., description="Data type (STRING, INT, DECIMAL, etc.)")
    nullable: bool = True
    source_node: str | None = None
    description: str | None = None


class InferredColumnRef(BaseModel):
    """
    Column reference with origin tracking for data_in nodes.

    Tracks which columns are required by downstream transformations
    and why (e.g., join, filter, select).
    """

    name: str = Field(..., description="Column name")
    source: str = Field(
        ...,
        description="Why this column is needed (join_requirement, filter_condition, select, aggregation)",
    )
    origin_node: str = Field(..., description="The transformation node that requires this column (tx_004)")


class InferredColumn(BaseModel):
    """
    A column with inference metadata.

    Used by the Schema Tracker to represent columns discovered
    through code analysis rather than external catalog.
    """

    name: str = Field(..., min_length=1)
    inferred_type: str = Field(
        default="UNKNOWN",
        description="Inferred type. Can be single (STRING) or composite (STRING | INT)",
    )
    source: InferenceSource = InferenceSource.SELECT
    confidence: InferenceConfidence = InferenceConfidence.LOW

    # Optional: exact type from catalog (e.g., "DECIMAL(10,2)")
    exact_type: str | None = None
    nullable: bool | None = None  # None = unknown

    default_value: str | None = Field(
        None, description="Default value when known (e.g., widget defaults, literal assignments)"
    )

    # Tracking - arrays to support merge scenarios (e.g., join keys)
    first_seen_nodes: list[str] = Field(
        default_factory=list,
        description="Node IDs where first detected, ordered by earliest first",
    )
    usage_count: int = 1  # How many times referenced

    def to_column(self) -> Column:
        """Convert to standard Column model."""
        dtype = self.exact_type or self.inferred_type
        return Column(
            name=self.name,
            dtype=dtype,
            nullable=self.nullable if self.nullable is not None else True,
            source_node=self.first_seen_nodes[0] if self.first_seen_nodes else None,
        )

    def merge_with(self, other: "InferredColumn") -> "InferredColumn":
        """
        Merge with another inference of the same column.

        - confidence: use the LOWEST (most conservative) when different
        - inferred_type: if one is UNKNOWN use the other; if both are
          different non-UNKNOWN, combine as "TYPE1 | TYPE2"
        - first_seen_nodes: combine and sort by earliest first
        """
        # Priority: HIGH=0, MEDIUM=1, LOW=2 (lower number = higher confidence)
        priority = {
            InferenceConfidence.HIGH: 0,
            InferenceConfidence.MEDIUM: 1,
            InferenceConfidence.LOW: 2,
        }

        # Use LOWEST confidence (highest priority number) when different
        if self.confidence != other.confidence:
            confidence = (
                self.confidence
                if priority[self.confidence] > priority[other.confidence]
                else other.confidence
            )
            source = self.source if priority[self.confidence] > priority[other.confidence] else other.source
        else:
            confidence = self.confidence
            source = self.source

        # Merge inferred_type
        inferred_type = self._merge_types(self.inferred_type, other.inferred_type)

        # Exact type from catalog always wins
        exact_type = self.exact_type or other.exact_type

        # Combine and sort first_seen_nodes
        combined_nodes = self._merge_node_lists(self.first_seen_nodes, other.first_seen_nodes)

        return InferredColumn(
            name=self.name,
            inferred_type=inferred_type,
            source=source,
            confidence=confidence,
            exact_type=exact_type,
            nullable=self.nullable if self.nullable is not None else other.nullable,
            first_seen_nodes=combined_nodes,
            usage_count=self.usage_count + other.usage_count,
        )

    @staticmethod
    def _merge_types(type1: str, type2: str) -> str:
        """
        Merge two inferred types.

        - If one is UNKNOWN, use the other
        - If both are the same, return that type
        - If different non-UNKNOWN types, combine as "TYPE1 | TYPE2"
        """
        if type1 == "UNKNOWN":
            return type2
        if type2 == "UNKNOWN":
            return type1
        if type1 == type2:
            return type1

        # Different types - combine them
        # Handle already composite types
        types1 = set(type1.split(" | "))
        types2 = set(type2.split(" | "))
        combined = sorted(types1 | types2)
        return " | ".join(combined)

    @staticmethod
    def _merge_node_lists(nodes1: list[str], nodes2: list[str]) -> list[str]:
        """
        Merge two node lists, removing duplicates and sorting by earliest first.
        """
        combined = list(dict.fromkeys(nodes1 + nodes2))  # Preserve order, remove dupes
        return sorted(combined, key=InferredColumn._node_sort_key)

    @staticmethod
    def _node_sort_key(node_id: str) -> tuple[int, int]:
        """Convert node ID to sortable tuple (prefix_order, number)."""
        prefix_order = {"in_": 0, "tx_": 1, "out_": 2, "param_": 3}
        for prefix, order in prefix_order.items():
            if node_id.startswith(prefix):
                try:
                    num = int(node_id[len(prefix):])
                    return (order, num)
                except ValueError:
                    return (order, 999999)
        return (999, 999999)  # Unknown format


class InputColumn(InferredColumn):
    """
    A column in a transformation's inferred_input.

    Extends InferredColumn with a required from_inputs field that tracks
    which input nodes this column originates from. For joins, the same
    column may come from multiple inputs.
    """

    from_inputs: list[str] = Field(
        ...,
        min_length=1,
        description="Input node IDs this column comes from (required, at least one)",
    )


# =============================================================================
# Column Constraints and Relationships (for Synthetic Data Generation)
# =============================================================================


class ConstraintType(str, Enum):
    """Type of column constraint extracted from filter conditions."""
    
    EQUALS = "equals"           # col == value
    NOT_EQUALS = "not_equals"   # col != value
    GREATER_THAN = "gt"         # col > value
    LESS_THAN = "lt"            # col < value
    GREATER_EQ = "gte"          # col >= value
    LESS_EQ = "lte"             # col <= value
    IN_LIST = "in"              # col.isin([...])
    NOT_NULL = "not_null"       # col.isNotNull()
    IS_NULL = "is_null"         # col.isNull()
    BETWEEN = "between"         # col.between(a, b)
    LIKE = "like"               # col.like(pattern)
    RLIKE = "rlike"             # col.rlike(pattern)
    ENUM = "enum"               # col must be one of a fixed set of values


class ColumnConstraint(BaseModel):
    """
    A constraint on a column extracted from filter/where conditions.
    
    Useful for synthetic data generation to ensure test data satisfies
    the constraints that would pass through filters.
    """
    
    column_name: str = Field(..., description="Name of the constrained column")
    constraint_type: ConstraintType = Field(..., description="Type of constraint")
    value: Any = Field(None, description="Constraint value (literal or list)")
    value_type: str = Field("unknown", description="Inferred type of the value")
    source_transformation: str = Field(
        ..., description="ID of the transformation where constraint was found"
    )
    location: "SourceLocation | None" = Field(
        None, description="Source code location"
    )


class RelationshipType(str, Enum):
    """Type of relationship between columns."""
    
    JOIN_KEY = "join_key"       # Columns used in join conditions
    FOREIGN_KEY = "fk"          # Inferred foreign key relationship
    SAME_DOMAIN = "same_domain" # Columns likely have same value domain


class ColumnRelationship(BaseModel):
    """
    A relationship between columns across data sources.
    
    Extracted from join conditions to understand how tables/files relate.
    Useful for synthetic data generation to ensure referential integrity.
    """
    
    left_column: str = Field(..., description="Column name from left side")
    left_source: str = Field(..., description="Source ID (in_xxx) of left column")
    right_column: str = Field(..., description="Column name from right side")
    right_source: str = Field(..., description="Source ID (in_xxx) of right column")
    relationship_type: RelationshipType = Field(
        RelationshipType.JOIN_KEY, description="Type of relationship"
    )
    join_type: str = Field("inner", description="Join type (inner, left, right, etc.)")
    source_transformation: str = Field(
        ..., description="ID of the join transformation"
    )


class AnalysisMetadata(BaseModel):
    """Metadata about the analysis run."""

    source_script: str
    app_name: str | None = None
    spark_version: str | None = None
    analysis_timestamp: datetime = Field(default_factory=datetime.now)
    tool_version: str = "0.1.0"
    catalog_used: str | None = None


class CompatibilitySummary(BaseModel):
    """High-level compatibility summary."""

    total_nodes: int = Field(..., ge=0)
    sql_convertible: int = Field(..., ge=0)
    python_udf_required: int = Field(..., ge=0)
    blockers: int = Field(..., ge=0)
    readiness_score: float = Field(..., ge=0.0, le=1.0)
    status: AnalysisStatus = AnalysisStatus.FEASIBLE

    @property
    def can_auto_migrate(self) -> bool:
        """Returns True if migration can proceed without human intervention."""
        return self.blockers == 0 and self.readiness_score >= 0.5


class ExecutionStep(BaseModel):
    """A single step in the execution plan."""

    step: int = Field(..., ge=1)
    operation: str
    target: TargetType
    feasible: bool = True

    # For SQL-convertible operations
    sql_fragment: str | None = None

    # For operations requiring special handling
    description: str | None = None
    complexity_risk: ComplexityRisk | None = None
    reason: str | None = None

    # Source tracking
    source_line_start: int | None = None
    source_line_end: int | None = None
    original_code: str | None = None

    # Schema information
    input_schema: list[Column] | None = None
    output_schema: list[Column] | None = None


class Prerequisite(BaseModel):
    """A prerequisite that must be completed before migration."""

    type: Literal["STAGE_SETUP", "TABLE_CREATE", "UDF_DEPLOY", "MANUAL_STEP"]
    description: str
    blocking: bool = False
    files: list[dict[str, Any]] | None = None


class Recommendation(BaseModel):
    """A recommendation for optimization or best practices."""

    type: Literal["OPTIMIZATION", "TARGET_LAG", "CHAINING", "SECURITY", "PERFORMANCE"]
    output: str | None = None
    description: str


class OutputMapping(BaseModel):
    """Detailed mapping for a single output (sink)."""

    id: str
    source_line_start: int | None = None
    source_line_end: int | None = None
    target_type: TargetType
    strategy: Literal[
        "DIRECT_SQL", "ANSI_SQL_WINDOW", "SQL_CASE_WHEN", "PYTHON_UDF", "HYBRID_SNOWPARK", "MANUAL"
    ]
    complexity: ComplexityRisk = ComplexityRisk.LOW
    risk: str | None = None
    risk_details: dict[str, Any] | None = None
    operations_detected: list[str] = Field(default_factory=list)
    mappings: dict[str, str] = Field(default_factory=dict)
    compatibility: dict[str, str] | None = None
    optimization: dict[str, Any] | None = None
    notes: str | None = None
    generated_sql_preview: str | None = None


# =============================================================================
# Main Feasibility IR
# =============================================================================


class FeasibilityIR(BaseModel):
    """
    The complete Feasibility IR - the main output of the analysis pipeline.

    Acts as a "semaphore" for automated decision-making:
    - Green (FEASIBLE): Proceed with automated migration
    - Yellow (FEASIBLE_WITH_PREREQUISITES): Setup required first
    - Red (NOT_FEASIBLE): Requires manual intervention
    """

    metadata: AnalysisMetadata
    compatibility_summary: CompatibilitySummary
    execution_plan: list[ExecutionStep] = Field(default_factory=list)
    outputs: list[OutputMapping] = Field(default_factory=list)
    global_blockers: list[str] = Field(default_factory=list)
    prerequisites: list[Prerequisite] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)

    def get_blockers(self) -> list[ExecutionStep]:
        """Return steps that require manual review."""
        return [s for s in self.execution_plan if s.target == TargetType.MANUAL_REVIEW]

    def get_sql_steps(self) -> list[ExecutionStep]:
        """Return steps that can be converted to pure SQL."""
        return [
            s
            for s in self.execution_plan
            if s.target in (TargetType.DYNAMIC_TABLE, TargetType.SNOWFLAKE_TABLE, TargetType.VIEW)
        ]

    def get_udf_steps(self) -> list[ExecutionStep]:
        """Return steps that require Python UDFs."""
        return [
            s
            for s in self.execution_plan
            if s.target in (TargetType.PYTHON_UDF, TargetType.PYTHON_DYNAMIC_TABLE)
        ]

    def save_json(self, path: str) -> None:
        """Save the IR to a JSON file."""
        from pathlib import Path

        Path(path).write_text(self.model_dump_json(indent=2))

    def save_yaml(self, path: str) -> None:
        """Save the IR to a YAML file (for human review)."""
        import json
        from pathlib import Path

        # Convert to dict first, then to YAML-like format
        # Note: For full YAML support, add pyyaml dependency
        data = self.model_dump()
        Path(path).write_text(json.dumps(data, indent=2, default=str))


# =============================================================================
# ASG Models (Abstract Semantic Graph)
# =============================================================================


class DataSource(BaseModel):
    """A data source (input) in the pipeline."""

    id: str
    type: Literal[
        "table", "csv", "parquet", "json", "orc", "delta", "iceberg", "jdbc", "sql", "memory", 
        "snowflake", "redshift", "bigquery", "databricks",  # Cloud DWs
        "config",  # Consolidated widget parameters
        "other"
    ]
    format: str | None = Field(
        None, description="Original format/connector string (e.g., 'com.databricks.spark.redshift')"
    )
    name: str | None = None
    path: str | None = None
    query: str | None = None  # For SQL sources
    columns: list[Column] = Field(default_factory=list)  # From catalog
    inferred_columns: list[InferredColumn] = Field(
        default_factory=list,
        description="Columns inferred from code analysis",
    )

    # Required columns with origin tracking (from downstream transformations)
    required_columns: list[InferredColumnRef] = Field(
        default_factory=list,
        description="Columns required by downstream transformations with origin tracking",
    )

    # Source location (required for precise code tracking)
    location: SourceLocation | None = None

    # Test file detection
    is_test_file: bool = Field(
        False, description="True if source is from a test file (path contains /tests/ or /test/)"
    )

    # Empty fallback pattern detection
    is_empty_fallback: bool = Field(
        False, description="True if source is an empty DataFrame fallback (spark.createDataFrame([]))"
    )

    # Indirect input tracking (reads inside utility functions, promoted to call sites)
    is_indirect: bool = Field(
        False,
        description="True if this input is read via a utility function rather than a direct spark.read call"
    )
    via_function: str | None = Field(
        None,
        description="Name of the utility function that performs the actual read. Only set when is_indirect=True"
    )

    # Node intent: distinguishes pipeline data from orchestration / test nodes
    nature: Literal["data", "metadata", "fixture", "artifact"] = Field(
        "data",
        description=(
            "'data' — regular pipeline input counted in all scores; "
            "'metadata' — orchestration/discovery query (SELECT MAX, SHOW, DESCRIBE, etc.) "
            "whose result is a scalar, not a business DataFrame; "
            "'fixture' — test mock or hardcoded data, excluded from production scoring; "
            "'artifact' — parser false-positive (e.g. variable name, module import, or "
            "malformed token mistaken for a data source), excluded from all scores"
        ),
    )

    def get_all_columns(self) -> list[InferredColumn]:
        """Get merged column list (catalog + inferred)."""
        result: dict[str, InferredColumn] = {}

        # Add inferred columns first
        for inferred_col in self.inferred_columns:
            result[inferred_col.name] = inferred_col

        # Catalog columns override/enrich inferred
        for catalog_col in self.columns:
            if catalog_col.name in result:
                # Merge: catalog provides exact type
                existing = result[catalog_col.name]
                result[catalog_col.name] = InferredColumn(
                    name=catalog_col.name,
                    inferred_type=existing.inferred_type,
                    source=InferenceSource.CATALOG,
                    confidence=InferenceConfidence.HIGH,
                    exact_type=catalog_col.dtype,
                    nullable=catalog_col.nullable,
                    first_seen_nodes=existing.first_seen_nodes,
                    usage_count=existing.usage_count,
                )
            else:
                # Catalog-only column (not used in code but exists)
                result[catalog_col.name] = InferredColumn(
                    name=catalog_col.name,
                    inferred_type="UNKNOWN",
                    source=InferenceSource.CATALOG,
                    confidence=InferenceConfidence.HIGH,
                    exact_type=catalog_col.dtype,
                    nullable=catalog_col.nullable,
                )

        return list(result.values())

    def get_column_names(self) -> list[str]:
        """Get unique column names from catalog and inferred sources."""
        names = {col.name for col in self.columns}
        names.update(col.name for col in self.inferred_columns)
        names.update(col.name for col in self.required_columns)
        return sorted(names)


class DataSink(BaseModel):
    """A data sink (output) in the pipeline."""

    id: str
    type: Literal[
        "table", "parquet", "csv", "json", "orc", "delta", "iceberg", "jdbc", 
        "snowflake", "redshift", "bigquery", "databricks",  # Cloud DWs
        "other"
    ]
    format: str | None = Field(
        None, description="Original format/connector string"
    )
    name: str | None = None
    path: str | None = None
    mode: Literal["overwrite", "append", "ignore", "error"] = "overwrite"
    source_id: str | None = None  # ID of the transformation that feeds this sink
    inferred_columns: list[InferredColumn] = Field(default_factory=list)  # Output schema

    # Source location (required for precise code tracking)
    location: SourceLocation | None = None

    # Test file detection
    is_test_file: bool = Field(
        False, description="True if source is from a test file (path contains /tests/ or /test/)"
    )

    # Empty fallback pattern detection
    is_empty_fallback: bool = Field(
        False, description="True if source is an empty DataFrame fallback (spark.createDataFrame([]))"
    )

    # Indirect output tracking
    is_indirect: bool = Field(
        False,
        description="True if this output is written via a utility function (e.g., write_to_s3()) "
                    "rather than a direct df.write call"
    )
    via_function: str | None = Field(
        None,
        description="Name of the utility function that performs the actual write "
                    "(e.g., 'data_update_into_s3'). Only set when is_indirect=True"
    )

    # Node classification (mirrors DataSource.nature)
    nature: Literal["data", "metadata", "artifact"] = Field(
        "data",
        description=(
            "'data' — real pipeline output counted in all scores; "
            "'metadata' — DDL or schema-management side-effect, not a business write; "
            "'artifact' — parser false-positive (e.g. phantom indirect placeholder with "
            "no name and no columns), excluded from all scores"
        ),
    )


class NodePosition(str, Enum):
    """Position of a node in the DAG for slicing purposes."""

    ROOT = "root"  # No inputs (reads from data source)
    LEAF = "leaf"  # No outputs (writes to data sink)
    PASS = "pass"  # Has both inputs and outputs (intermediate)
    ISOLATED = "isolated"  # No inputs or outputs (orphan)


class TransformationNode(BaseModel):
    """A transformation node in the ASG."""

    id: str
    operation: str  # select, filter, join, groupBy, etc.
    inputs: list[str] = Field(default_factory=list)  # IDs of input nodes
    outputs: list[str] = Field(default_factory=list)  # IDs of output nodes

    # Details
    logic: str | None = None  # Original code snippet
    parameters: dict[str, Any] = Field(default_factory=dict)

    # Schema tracking (from catalog)
    input_schema: list[Column] = Field(default_factory=list)
    output_schema: list[Column] = Field(default_factory=list)

    # Inferred schema (from code analysis)
    inferred_input: list[InputColumn] = Field(default_factory=list)
    inferred_output: list[InferredColumn] = Field(default_factory=list)

    # Source location (required for precise code tracking)
    location: SourceLocation | None = None

    # Test file detection
    is_test_file: bool = Field(
        False, description="True if source is from a test file (path contains /tests/ or /test/)"
    )

    # Empty fallback pattern detection
    is_empty_fallback: bool = Field(
        False, description="True if source is an empty DataFrame fallback (spark.createDataFrame([]))"
    )

    # Classification (filled by Relational Lifting)
    category: SparkCategory | None = None
    feasibility: FeasibilityLevel | None = None
    
    # Determinism flag (for incremental refresh compatibility)
    is_deterministic: bool = Field(
        True,
        description="False if transformation uses non-deterministic functions like "
                    "current_timestamp(), rand(), uuid(). Affects Dynamic Table refresh strategy."
    )
    non_deterministic_reason: str | None = Field(
        None,
        description="Explanation of why this node is non-deterministic, e.g., 'uses current_timestamp()'"
    )
    is_convergence_point: bool = Field(
        False,
        description="True if this is the first node after control flow branches merge. "
                    "Phase 2 uses convergence_inputs to build UNION/CASE logic."
    )
    convergence_inputs: list[str] = Field(
        default_factory=list,
        description="SSA-compliant IDs of branch outputs converging here, "
                    "e.g., ['tx_015_TRUE', 'tx_018_FALSE']."
    )

    @property
    def node_position(self) -> NodePosition:
        """
        Determine the node's position in the DAG.

        Useful for the Slicer to identify:
        - ROOT: Entry points (no predecessors)
        - LEAF: Terminal nodes (no successors)
        - PASS: Intermediate nodes (has both)
        - ISOLATED: Dead code (no connections)
        """
        has_inputs = len(self.inputs) > 0
        has_outputs = len(self.outputs) > 0

        if has_inputs and has_outputs:
            return NodePosition.PASS
        elif has_inputs and not has_outputs:
            return NodePosition.LEAF
        elif not has_inputs and has_outputs:
            return NodePosition.ROOT
        else:
            return NodePosition.ISOLATED

    @property
    def is_terminal(self) -> bool:
        """True if this is a leaf node (no outputs)."""
        return self.node_position == NodePosition.LEAF

    @property
    def is_entry_point(self) -> bool:
        """True if this is a root node (no inputs from other transformations)."""
        return self.node_position == NodePosition.ROOT


class FunctionArgument(BaseModel):
    """
    Represents a function argument with schema tracking capabilities.

    Enables schema propagation through function calls by linking
    arguments to their data sources.
    """

    name: str
    inferred_type: str = "Unknown"  # DataFrame, SparkSession, str, etc.
    inferred_schema_origin: str | None = None  # e.g., "data_in.raw_transactions"
    is_optional: bool = False


class ReturnRefType(str, Enum):
    """Type of reference for function return values."""

    TRANSFORMATION = "transformation"  # Return from Spark chain (ref_id = "tx_007")
    VARIABLE = "variable"  # Return of a variable (ref_id = "df_final")
    EXPRESSION = "expression"  # Complex call not traceable (UDFs, lambdas)
    MULTIPLE = "multiple"  # Multiple return paths (try/except)
    LITERAL = "literal"  # Constant value (True, 1.5, "x")
    VOID = "void"  # No explicit return (action functions)
    DATA_SOURCE = "data_source"  # Return from spark.read chain (ref_id = "in_001")


class FunctionReturn(BaseModel):
    """
    Represents the return value of a function.

    Uses ref_type and ref_id to precisely identify what is returned:
    - transformation: Points to the last tx_xxx node in a Spark chain
    - variable: Points to a named variable (e.g., "df_final")
    - multiple: Function has multiple return paths
    - literal: Returns a constant value
    - void: No return statement (action/sink functions)
    """

    ref_type: ReturnRefType = ReturnRefType.VOID
    ref_id: str | None = None  # "tx_007", "df_final", "boolean", etc.
    inferred_type: str = "Unknown"


class FunctionDefinition(BaseModel):
    """
    Complete function definition with argument and return tracking.

    Enables:
    - Schema propagation through function arguments
    - Lineage tracking from data sources to function parameters
    - Return type inference for downstream consumers
    """

    name: str

    # Containing class (for methods): "UtilsS3", "DataProcessor", etc.
    # None for top-level functions
    containing_class: str | None = None

    # Source file (relative path from workload root)
    source_file: str | None = None

    # Source location (required for precise code tracking)
    location: SourceLocation | None = None

    # Test file detection
    is_test_file: bool = Field(
        False, description="True if source is from a test file (path contains /tests/ or /test/)"
    )

    # Empty fallback pattern detection
    is_empty_fallback: bool = Field(
        False, description="True if source is an empty DataFrame fallback (spark.createDataFrame([]))"
    )

    arguments: list[FunctionArgument] = Field(default_factory=list)
    returns: FunctionReturn | None = None
    
    # UDF detection
    is_udf: bool = Field(
        False, description="True if this function is registered or used as a Spark UDF"
    )
    udf_return_schema: str | None = Field(
        None, 
        description="Spark return type/schema for UDFs (e.g., 'DoubleType()', 'StructType([...])')"
    )


# =============================================================================
# Execution Call Models (Function Invocation Tracking)
# =============================================================================


class BindingSourceType(str, Enum):
    """Type of source for an input binding."""

    DATA_IN = "data_in"  # Direct data source (in_008)
    TRANSFORMATION = "transformation"  # Output of a transformation (tx_004)
    CALL_OUTPUT = "call_output"  # Output of a previous call (call_001)
    VARIABLE = "variable"  # Unresolved variable reference
    LITERAL = "literal"  # Literal value (e.g., "sales_data" table name)


class InputBinding(BaseModel):
    """
    Maps a function argument to its source at call time.

    Example: When calling `process_bronze_to_silver(df_sales, df_products)`,
    this tracks that `df_sales` comes from `in_008` (a data source).
    """

    arg_name: str = Field(..., description="Parameter name in callee function")
    source_type: BindingSourceType = Field(..., description="Type of source")
    source_id: str = Field(
        ..., description="Source identifier (in_008, tx_004, call_001, or variable name)"
    )
    inferred_origin: str | None = Field(
        None,
        description="Resolved data origin (e.g., 'data_in.sales_data') propagated through call graph",
    )


class OutputBinding(BaseModel):
    """
    Maps function return to variable and traceable node.

    Example: `silver_df = process_bronze_to_silver(...)` binds the return
    to variable `silver_df`, which resolves to transformation `tx_004`.
    """

    variable_name: str = Field(..., description="Variable name at call site")
    target_node: str | None = Field(
        None, description="Resolved node ID (tx_004) if traceable"
    )
    resolved_origin: str | None = Field(
        None,
        description="Data origin this call produces (e.g., 'data_in.sales_data' for _read_table)",
    )


class CallLocation(BaseModel):
    """Location where a function call occurs."""

    function: str = Field(..., description="Caller function name (or '__main__')")
    line: int = Field(..., description="Line number of the call")
    file: str = Field(..., description="Source file path")


class CalleeRef(BaseModel):
    """Reference to the called function."""

    function: str = Field(..., description="Name of the called function")
    file: str | None = Field(None, description="Source file (None if same file)")


class CallBindings(BaseModel):
    """Input and output bindings for a function call."""

    inputs: list[InputBinding] = Field(default_factory=list)
    output: OutputBinding | None = None


class ExecutionCall(BaseModel):
    """
    A function invocation with resolved bindings.

    Captures the call graph: which function calls which, with what arguments,
    and where the return value is stored. This enables tracing data flow
    through function boundaries.

    Example:
        silver_df = process_bronze_to_silver(df_sales, df_products)

    Becomes:
        ExecutionCall(
            call_id="call_001",
            caller=CallLocation(function="run_pipeline", line=62, file="main.py"),
            callee=CalleeRef(function="process_bronze_to_silver"),
            bindings=CallBindings(
                inputs=[
                    InputBinding(arg_name="df_sales", source_type="data_in", source_id="in_008"),
                    InputBinding(arg_name="df_products", source_type="data_in", source_id="in_009"),
                ],
                output=OutputBinding(variable_name="silver_df", target_node="tx_004")
            )
        )
    """

    call_id: str = Field(..., description="Unique identifier (call_001, call_002, ...)")
    caller: CallLocation
    callee: CalleeRef
    bindings: CallBindings = Field(default_factory=CallBindings)
    literal_arguments: dict[str, str] = Field(
        default_factory=dict, 
        description="Resolved string literals for arguments (e.g., table_name -> 'my_table')"
    )


# =============================================================================
# Execution Instance Models (Runtime Binding Resolution)
# =============================================================================


class BindingAction(str, Enum):
    """Type of action in an execution binding."""

    RESOLVE_INBOUND = "RESOLVE_INBOUND"  # Resolves a data source (table name, file path)
    EXECUTE_FLOW = "EXECUTE_FLOW"  # Executes a function with specific bindings


class ResolveDetails(BaseModel):
    """Details for RESOLVE_INBOUND action."""

    source_id: str = Field(..., description="Data source ID (in_008)")
    resolved_name: str = Field(..., description="Resolved name (sales_data)")
    output_variable: str = Field(..., description="Variable receiving the data (df_sales)")


class InputMapping(BaseModel):
    """Maps a function parameter to its origin node."""

    parameter: str = Field(..., description="Parameter name in target function")
    origin_node: str = Field(..., description="Origin data source ID (in_008)")


class ExecutionBinding(BaseModel):
    """
    A single binding action in an execution instance.

    Either RESOLVE_INBOUND (captures literal -> data source) or
    EXECUTE_FLOW (function call with parameter mappings).
    """

    call_id: str = Field(..., description="Reference to execution_call")
    action: BindingAction

    # For RESOLVE_INBOUND
    details: ResolveDetails | None = None

    # For EXECUTE_FLOW
    context: str | None = Field(None, description="Target function name")
    input_map: list[InputMapping] = Field(default_factory=list)


class ExecutionInstance(BaseModel):
    """
    Represents a single execution flow from an entry point.

    Captures how data flows from literals/sources through function calls,
    enabling late binding resolution of inferred_schema_origin.

    Example: When __main__ calls _read_table("sales_data") and then
    passes the result to run_pipeline(), this tracks the full lineage.
    """

    instance_id: str = Field(..., description="Unique identifier (main_run_001)")
    entry_point: str = Field(..., description="Entry function (__main__)")
    bindings: list[ExecutionBinding] = Field(default_factory=list)


class WidgetParameter(BaseModel):
    """A Databricks widget parameter (dbutils.widgets.*)."""
    
    name: str = Field(description="Widget name/key")
    widget_type: str = Field(
        default="text",
        description="Widget type: text, dropdown, combobox, multiselect"
    )
    default_value: str | None = Field(
        default=None,
        description="Default value (literal or runtime:<expr> if dynamic)"
    )
    valid_values: list[str] = Field(
        default_factory=list,
        description="Allowed values for dropdown/multiselect widgets"
    )
    label: str | None = Field(
        default=None,
        description="Human-readable label for the widget"
    )
    line: int | None = Field(default=None, description="Line number of definition")


class NotebookDependency(BaseModel):
    """A dependency between Databricks notebooks via %run or dbutils.notebook.run."""
    
    target: str = Field(
        description="Raw target path as written in %run (e.g., '../config', './utils')"
    )
    resolved_path: str | None = Field(
        default=None,
        description="Resolved relative path from workload root (e.g., '.databricks/notebooks/rsuccess/config.py')"
    )
    params: dict[str, str] = Field(
        default_factory=dict,
        description="Parameters passed via $key=value (e.g., {'brand': 'plk'})"
    )
    line: int | None = Field(
        default=None,
        description="Line number in source file where the dependency is declared"
    )


class SourceFile(BaseModel):
    """
    Represents a single source file with its imports.

    Enables tracking of which imports belong to which file when
    processing a multi-file workload. Functions are stored at the
    ASG level with a source_file field pointing back to their origin.
    
    Entrypoint detection fields enable downstream tools to identify
    execution starting points without re-parsing source code.
    """

    path: str  # Relative path from workload root (e.g., "obfuscate_data.py")
    imports: dict[str, ImportEntry] = Field(default_factory=dict)
    
    # Entrypoint detection fields
    source_type: SourceType = Field(
        default=SourceType.UNKNOWN,
        description="Type of source file: NOTEBOOK, MODULE, SCRIPT"
    )
    is_entry_point: bool = Field(
        default=False,
        description="True if this file is an execution entry point"
    )
    entry_point_reason: str | None = Field(
        default=None,
        description="Why this file is an entry point: notebook | main_guard | spark_session_creation | main_method"
    )
    entry_point_lineno: int | None = Field(
        default=None,
        description="Line number of the entry point construct (if __name__ guard or def main). None for notebooks (convention: always 1)."
    )
    entry_point_scope: str | None = Field(
        default=None,
        description="Scope qualifier for the entry point, e.g. 'GlobalTransactions::main' for Scala objects. None for Python scripts and notebooks."
    )
    has_spark_session: bool = Field(
        default=False,
        description="True if this file creates a SparkSession"
    )
    
    # Notebook inter-dependencies
    notebook_dependencies: list[NotebookDependency] = Field(
        default_factory=list,
        description="Other notebooks this file depends on via %run or dbutils.notebook.run"
    )
    
    # Widget parameters — consolidated into data_in type="config" entries.
    # Kept for backward compatibility; new code should use data_in instead.
    widget_parameters: list[WidgetParameter] = Field(
        default_factory=list,
        description="Deprecated: widgets are now promoted to data_in config entries",
        exclude=True,
    )
    
    # Notebook description (from MAGIC %md)
    description: str | None = Field(
        default=None,
        description="Description extracted from the first %md section of the notebook"
    )
    
    # Runtime dependencies (from MAGIC %pip)
    pip_dependencies: list[str] = Field(
        default_factory=list,
        description="Python packages installed via %pip in this notebook"
    )


    
    # Notebook display outputs (display()/show() calls)
    display_outputs: int = Field(
        default=0,
        description="Number of display()/show() calls for notebook visualization"
    )

    def resolve_alias(self, alias: str) -> str | None:
        """Resolve an alias to its full module name within this file."""
        for module, entry in self.imports.items():
            if entry.alias == alias:
                return module
        return None



class ExtractionMetadata(BaseModel):
    """
    Metadata about the ASG extraction process and source context.
    
    Groups project-level information separate from the graph structure.
    """
    
    # Project paths
    workload_root: str | None = Field(
        None,
        description="Absolute path to workload directory (base for relative paths)"
    )
    source_file: str = Field(
        "",
        description="Path to input file/directory. Deprecated: use workload_root"
    )
    
    # Application info
    app_name: str | None = Field(None, description="Spark application name if detected")
    spark_version: str | None = Field(None, description="Spark version if detected")
    
    # Extraction info
    generated_at: datetime | None = Field(
        default=None,
        description="When the extraction was performed (ISO format)"
    )


class WindowSpecDefinition(BaseModel):
    """
    Definition of a PySpark Window specification.
    
    Captures window specs assigned to variables (e.g., window = Window.partitionBy(...))
    so they can be resolved during SQL generation.
    """
    scope: str = Field(..., description="Function/scope where the window spec is defined")
    variable_name: str = Field(..., description="Name of the variable (e.g., 'window_spec')")
    pyspark_expr: str = Field(..., description="Original PySpark expression")
    sql_expr: str | None = Field(None, description="Resolved SQL expression (cached)")


class ASG(BaseModel):
    """
    Abstract Semantic Graph (ASG) — the output of the parsing phase.

    Contains the full structure of the analyzed Spark workload (PySpark, Scala, or any future language) with resolved references and propagated schemas.

    Structure:
    - workload_root: Absolute path to the workload directory (base for relative paths)
    - source_files: List of SourceFile objects with per-file imports
    - functions: Global list of functions, each with source_file field
    - source_file: Path to the input (file or directory) - deprecated, use workload_root
    """

    # Extraction metadata (grouped project-level info)
    extraction_metadata: ExtractionMetadata = Field(
        default_factory=ExtractionMetadata,
        description="Project-level metadata: paths, versions, timestamps"
    )

    # Multi-file support: list of source files with their imports
    source_files: list[SourceFile] = Field(default_factory=list)

    # Function definitions (global list, each has source_file field)
    functions: list[FunctionDefinition] = Field(default_factory=list)

    # Function call graph - tracks invocations between functions
    execution_calls: list[ExecutionCall] = Field(default_factory=list)

    # Execution instances - runtime binding resolution trace
    execution_instances: list[ExecutionInstance] = Field(default_factory=list)

    data_in: list[DataSource] = Field(default_factory=list)
    data_out: list[DataSink] = Field(default_factory=list)
    transformations: list[TransformationNode] = Field(default_factory=list)
    
    # Control flow structures (if/match/for/while/try/with)
    control_nodes: list[ControlNode] = Field(
        default_factory=list,
        description="Control flow structures extracted from Python AST"
    )

    # Warnings and issues
    warnings: list[AnalysisWarning] = Field(default_factory=list)
    
    # Window specifications (for SQL generation)
    window_specs: list[WindowSpecDefinition] = Field(
        default_factory=list,
        description="Window spec definitions (scope::var -> SQL expression)"
    )
    
    # Parsing report (optional, present when parsing a directory)
    parsing_report: "ParsingReport | None" = Field(
        default=None,
        description="Report of parsing results including file types and errors"
    )
    
    # Column constraints extracted from filter/where conditions
    column_constraints: list["ColumnConstraint"] = Field(
        default_factory=list,
        description="Constraints on columns from filter conditions (for synthetic data)"
    )
    
    # Relationships between columns across data sources
    column_relationships: list["ColumnRelationship"] = Field(
        default_factory=list,
        description="Relationships from join conditions (for synthetic data)"
    )

    # Companion configuration files detected in the workload directory.
    # These are JSON/YAML files that may contain data source paths, schemas,
    # or pipeline wiring that static Python/Scala analysis cannot see at
    # parse time (config-driven / plugin architectures).
    config_files: list[dict] = Field(
        default_factory=list,
        description=(
            "Companion config files found in the workload root "
            "(JSON/YAML). Not analysed — surfaced for user awareness. "
            "Each entry includes ai_instructions for AI-assisted enrichment."
        ),
    )

    # AI enrichment hint — present when actionable config files are detected.
    # An AI agent with file-system access can follow this structured task to
    # enrich the ASG with information extracted from the config files, without
    # any additional context beyond what is in this document.
    warp_ai_hint: dict | None = Field(
        default=None,
        description=(
            "Structured enrichment task for an AI agent. "
            "Present when companion config files were found that an AI "
            "could parse to improve Data I/O naming, schema coverage, or lineage."
        ),
    )

    def resolve_alias(self, alias: str, source_file: str | None = None) -> str | None:
        """
        Resolve an alias to its full module name.

        Args:
            alias: The alias to resolve (e.g., "F")
            source_file: Optional file path to search in first

        Returns:
            Full module name (e.g., "pyspark.sql.functions") or None

        Example: resolve_alias("F") -> "pyspark.sql.functions"
        """
        # If source_file specified, search that file first
        if source_file:
            for sf in self.source_files:
                if sf.path == source_file:
                    result = sf.resolve_alias(alias)
                    if result:
                        return result

        # Search all source files
        for sf in self.source_files:
            result = sf.resolve_alias(alias)
            if result:
                return result

        return None

    def get_import_type(self, name: str) -> ImportType | None:
        """
        Get the import type for a name (alias or function).

        Useful for detecting custom UDFs vs standard Spark functions.
        """
        for source_file in self.source_files:
            for _module, entry in source_file.imports.items():
                if entry.alias == name:
                    return entry.type
                if name in entry.imported_names:
                    return entry.type
        return None

    def get_all_imports(self) -> dict[str, ImportEntry]:
        """
        Get merged imports from all source files.

        Note: If multiple files have the same module with different aliases,
        the last one processed wins. Use source_files for precise tracking.
        """
        merged: dict[str, ImportEntry] = {}
        for source_file in self.source_files:
            merged.update(source_file.imports)
        return merged

    def get_functions_by_file(self, file_path: str) -> list[FunctionDefinition]:
        """Get all functions defined in a specific source file."""
        return [f for f in self.functions if f.source_file == file_path]


# =============================================================================
# Parsing Report Models
# =============================================================================


class SyntaxSummary(BaseModel):
    """Summary of syntax parsing results."""
    ok: int = Field(..., description="Files with valid Python syntax")
    corrected: int = Field(..., description="Files with syntax errors that were auto-corrected")
    errors: int = Field(..., description="Files with syntax errors that could not be corrected")


class UnderstandingSummary(BaseModel):
    """Summary of pipeline understanding results."""
    ok: int = Field(..., description="Files where pipeline was successfully understood")
    errors: int = Field(..., description="Files where pipeline understanding failed")


class InferenceSummary(BaseModel):
    """Summary of type inference results during transformation detection."""
    inferred: int = Field(0, description="Transformations where type was successfully inferred")
    name_match: int = Field(0, description="Transformations detected by name matching (fallback)")
    excluded: int = Field(0, description="Calls excluded because receiver is not DataFrame")


class TypeInferenceWarning(BaseModel):
    """Warning when type inference falls back to name matching or excludes a call."""
    
    # Location
    path: str = Field(..., description="Source file path")
    line: int = Field(..., description="Line number")
    column: int | None = Field(None, description="Column number")
    
    # Context
    method: str = Field(..., description="Method name detected (e.g., 'join')")
    receiver: str = Field(..., description="Code of the receiver object")
    receiver_type: str | None = Field(None, description="Inferred type if known")
    
    # Resolution
    resolution: Literal["inferred", "name_match", "excluded"] = Field(
        ..., description="How the call was resolved"
    )
    reason: str = Field(..., description="Why this resolution was applied")
    
    # For debugging without re-running
    code_snippet: str = Field(..., description="The actual code snippet")
    context_lines: list[str] = Field(
        default_factory=list,
        description="Surrounding lines for context"
    )
    
    # Actionable
    suggestion: str | None = Field(
        None, description="Suggested action to improve inference"
    )


class ParsedFileInfo(BaseModel):
    """Information about a parsed file with two-phase status."""

    path: str = Field(..., description="Relative path to the file")
    file_type: Literal["databricks_notebook", "python_script", "scala_file"] = Field(
        ..., description="Type of source file"
    )
    
    # Phase 1: Syntax Parsing (Python validity)
    syntax_status: Literal["ok", "corrected", "error"] = Field(
        ..., description="Python syntax parsing status"
    )
    syntax_correction: str | None = Field(
        None, description="Description of automatic syntax correction applied"
    )
    syntax_error: str | None = Field(
        None, description="Syntax error message if parsing failed"
    )
    
    # Phase 2: Pipeline Understanding (semantic extraction)
    understanding_status: Literal["ok", "error", "skipped"] = Field(
        ..., description="Pipeline understanding status"
    )
    understanding_error: str | None = Field(
        None, description="Understanding error message if extraction failed"
    )


class ParsingReport(BaseModel):
    """
    Report of file processing results for a directory.
    
    Three-phase processing:
    1. Syntax Parsing: Validates Python syntax, auto-corrects where possible
    2. Pipeline Understanding: Extracts semantic information about data transformations
    3. Type Inference: Determines receiver types for accurate transformation detection
    """

    # Summary counts
    total_files: int = Field(..., description="Total number of Python files found")
    databricks_notebooks: int = Field(
        ..., description="Number of files that are Databricks notebook exports"
    )
    python_scripts: int = Field(
        ..., description="Number of pure Python script files"
    )
    scala_files: int = Field(
        0, description="Number of Scala source files"
    )

    # Phase 1: Syntax parsing summary
    syntax: SyntaxSummary = Field(..., description="Syntax parsing results")
    
    # Phase 2: Pipeline understanding summary
    understanding: UnderstandingSummary = Field(..., description="Pipeline understanding results")
    
    # Phase 3: Type inference summary
    inference: InferenceSummary | None = Field(
        None, description="Type inference results for transformation detection"
    )

    # Detailed file information (only for files with issues)
    files: list[ParsedFileInfo] = Field(
        default_factory=list,
        description="Details for files with corrections or errors",
    )
    
    # Type inference warnings (calls that couldn't be fully resolved)
    inference_warnings: list["TypeInferenceWarning"] = Field(
        default_factory=list,
        description="Warnings for calls resolved by name matching or excluded",
    )

    # Metadata
    generated_at: datetime | None = Field(
        None, description="When the report was generated"
    )
