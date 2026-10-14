"""
Agnostic Semantic Graph (ASG-A) Models.

This module defines the "Lingua Franca" for data engineering pipelines.
The Agnostic ASG is technology-independent and can represent logic from
PySpark, dbt, SSIS, Informatica, SAS, or any other data platform.

The ASG-Agnostic is:
- Self-contained: Does not require the source ASG to function
- Traceable: Maintains optional lineage to the original source
- Universal: Same structure regardless of source technology
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# =============================================================================
# Enumerations: The Universal Vocabulary
# =============================================================================


class SourceTechnology(str, Enum):
    """Supported source technologies for manifest entries."""

    PYSPARK_WORKLOAD = "PYSPARK_WORKLOAD"
    PYSPARK_MODULE = "PYSPARK_MODULE"
    DBT_MODEL = "DBT_MODEL"
    DBT_SOURCE = "DBT_SOURCE"
    SSIS_PACKAGE = "SSIS_PACKAGE"
    INFORMATICA_MAPPING = "INFORMATICA_MAPPING"
    SAS_PROGRAM = "SAS_PROGRAM"
    SQL_SCRIPT = "SQL_SCRIPT"
    SQL_PROCEDURE = "SQL_PROCEDURE"
    UNKNOWN = "UNKNOWN"


class WarningSeverity(str, Enum):
    """Severity levels for conversion warnings."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class WarningStatus(str, Enum):
    """Status of a warning."""
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class ConversionWarning(BaseModel):
    """
    A warning or issue detected during conversion.
    
    Warnings propagate from Phase 1 (ASG-Spark) through Phase 2 (ASG-A)
    to inform Phase 3 (Snowflake generator) of potential risks.
    """
    
    code: str = Field(..., description="Warning code (e.g., W_TYPE_AMBIGUITY)")
    severity: WarningSeverity = Field(..., description="Impact level")
    source_phase: str = Field(
        "PHASE_1_SPARK_EXTRACTOR", 
        description="Which phase generated this warning"
    )
    message: str = Field(..., description="Human-readable description")
    remediation_hint: str | None = Field(
        None, description="Suggested fix or action"
    )
    status: WarningStatus = Field(
        WarningStatus.OPEN, description="Current status"
    )
    acknowledged_by: str | None = Field(
        None, description="Who acknowledged this warning"
    )


class LogicalType(str, Enum):
    """
    Universal logical data types.

    These abstract away platform-specific types (Spark's LongType,
    SSIS's DT_I8, SQL's BIGINT) into a common vocabulary.
    """

    L_TEXT = "L_TEXT"  # String/VARCHAR
    L_INT = "L_INT"  # Integer/BIGINT/NUMBER(38,0)
    L_DECIMAL = "L_DECIMAL"  # Decimal/Double/FLOAT
    L_DATE = "L_DATE"  # Date without time
    L_DATETIME = "L_DATETIME"  # Timestamp/DateTime
    L_BOOL = "L_BOOL"  # Boolean
    L_OBJECT = "L_OBJECT"  # Complex/Struct/VARIANT
    L_BYTES = "L_BYTES"  # Binary data
    L_UNKNOWN = "L_UNKNOWN"  # Type not determined


class SemanticRole(str, Enum):
    """
    Semantic classification of columns for analytics.

    Used by Snowflake Cortex Intelligence and semantic layers.
    """

    KEY = "KEY"  # Primary/Natural key
    FOREIGN_KEY = "FOREIGN_KEY"  # Reference to another entity
    MEASURE = "MEASURE"  # Numeric value that can be aggregated
    DIMENSION = "DIMENSION"  # Categorical/descriptive attribute
    GEOGRAPHY = "GEOGRAPHY"  # Geographic/location columns (country, region, city)
    TIMESTAMP = "TIMESTAMP"  # Time-based column for partitioning
    METADATA = "METADATA"  # System/audit columns
    PII = "PII"  # Personally Identifiable Information (requires masking)
    MASKED = "MASKED"  # Already masked/hashed PII
    UNKNOWN = "UNKNOWN"


class PrimitiveIntent(str, Enum):
    """
    The 10 Universal Logical Primitives.

    Any command from Spark, SSIS, or dbt maps to one of these intents.
    This eliminates syntax noise and captures pure business intention.
    """

    # Data Flow Primitives
    INGEST = "INGEST"  # Entry point: read.table, OLE DB Source, FROM
    SINK = "SINK"  # Exit point: write.saveAsTable, INSERT INTO

    # Row Operations
    FILTER = "FILTER"  # Restrict rows: filter/where, Conditional Split
    SORT = "SORT"  # Order rows: orderBy, Sort Transform

    # Column Operations
    SELECT = "SELECT"  # Restrict/rename columns: select, alias
    TRANSFORM = "TRANSFORM"  # New logic or type change: withColumn, Derived Column

    # Control Flow Primitives
    ROUTE = "ROUTE"  # Conditional branching: if/else, CASE, router
    RECONCILE = "RECONCILE"  # Merge branches: union SSA versions, type coercion

    # Set Operations
    JOIN = "JOIN"  # Horizontal union: join, Merge Join, Lookup
    COMBINE = "COMBINE"  # Vertical union: union, unionByName, UNION ALL

    # Aggregation
    REDUCE = "REDUCE"  # Aggregate: groupBy + agg, Aggregate, GROUP BY

    # Escape Hatch
    OPAQUE = "OPAQUE"  # Non-analyzable logic: UDF, Script Task


class JoinType(str, Enum):
    """Join semantics."""

    INNER = "INNER"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    FULL = "FULL"
    CROSS = "CROSS"
    SEMI = "SEMI"
    ANTI = "ANTI"


class ExpressionOp(str, Enum):
    """
    Expression tree operators.

    These form the normalized representation of logic that is
    identical regardless of source syntax.
    """

    # Comparison
    EQUALS = "EQUALS"
    NOT_EQUALS = "NOT_EQUALS"
    GREATER_THAN = "GREATER_THAN"
    LESS_THAN = "LESS_THAN"
    GREATER_EQUAL = "GREATER_EQUAL"
    LESS_EQUAL = "LESS_EQUAL"

    # Logical
    AND = "AND"
    OR = "OR"
    NOT = "NOT"

    # Null checks
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"
    COALESCE = "COALESCE"

    # Arithmetic
    ADD = "ADD"
    SUBTRACT = "SUBTRACT"
    MULTIPLY = "MULTIPLY"
    DIVIDE = "DIVIDE"
    MODULO = "MODULO"
    NEGATE = "NEGATE"

    # String
    CONCAT = "CONCAT"
    SUBSTRING = "SUBSTRING"
    UPPER = "UPPER"
    LOWER = "LOWER"
    TRIM = "TRIM"
    LENGTH = "LENGTH"
    REPLACE = "REPLACE"
    REGEX_EXTRACT = "REGEX_EXTRACT"
    CONTAINS = "CONTAINS"
    STARTS_WITH = "STARTS_WITH"
    ENDS_WITH = "ENDS_WITH"
    LIKE = "LIKE"
    REGEX_MATCH = "REGEX_MATCH"

    # Date/Time
    DATE_ADD = "DATE_ADD"
    DATE_DIFF = "DATE_DIFF"
    DATE_TRUNC = "DATE_TRUNC"
    EXTRACT = "EXTRACT"  # Year, Month, Day, etc.
    CURRENT_DATE = "CURRENT_DATE"
    CURRENT_TIMESTAMP = "CURRENT_TIMESTAMP"

    # Type conversion
    CAST = "CAST"

    # Conditional
    CASE_WHEN = "CASE_WHEN"
    IF_ELSE = "IF_ELSE"

    # Aggregation
    SUM = "SUM"
    COUNT = "COUNT"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"
    COUNT_DISTINCT = "COUNT_DISTINCT"

    # Window
    ROW_NUMBER = "ROW_NUMBER"
    RANK = "RANK"
    DENSE_RANK = "DENSE_RANK"
    LAG = "LAG"
    LEAD = "LEAD"

    # Sorting (for .asc(), .desc() on columns)
    SORT_ASC = "SORT_ASC"
    SORT_DESC = "SORT_DESC"

    # Other
    LITERAL = "LITERAL"
    COLUMN_REF = "COLUMN_REF"
    FUNCTION_CALL = "FUNCTION_CALL"


# =============================================================================
# Expression Tree: Normalized Logic Representation
# =============================================================================


class ExpressionNode(BaseModel):
    """
    A node in the expression tree.

    This normalizes logic from any source syntax into a universal format.
    F.col("amount") * 0.9 in Spark becomes the same tree as
    [Amount] * 0.9 in SSIS.
    """

    op: ExpressionOp = Field(..., description="The operation type")
    args: list["ExpressionNode"] = Field(
        default_factory=list, description="Operand nodes for n-ary operations"
    )

    # For binary operations (convenience)
    left: "ExpressionNode | None" = Field(None, description="Left operand")
    right: "ExpressionNode | None" = Field(None, description="Right operand")

    # For literals and column references
    value: Any = Field(None, description="Literal value or column name")
    data_type: LogicalType | None = Field(None, description="Type for literals/casts")

    # For function calls
    function_name: str | None = Field(None, description="Name of function for FUNCTION_CALL")

    class Config:
        use_enum_values = True


class CaseWhen(BaseModel):
    """A CASE WHEN branch."""

    condition: ExpressionNode = Field(..., description="The IF condition")
    result: ExpressionNode = Field(..., description="The THEN value")


class ConditionalExpression(BaseModel):
    """A complete CASE WHEN expression."""

    cases: list[CaseWhen] = Field(..., description="List of WHEN branches")
    default: ExpressionNode | None = Field(None, description="ELSE value")


# =============================================================================
# Origin Bridge: Traceability to Source
# =============================================================================


class OriginBridge(BaseModel):
    """
    Links an agnostic element back to its source.

    This maintains audit trail without creating dependency.
    The ASG-Agnostic works without this, but it enables
    debugging and compliance reporting.
    """

    legacy_id: str | None = Field(None, description="Original ID (e.g., tx_001, in_002)")
    legacy_tech: SourceTechnology = Field(..., description="Source technology")
    source_path: str | None = Field(None, description="Original file path")
    line_number: int | None = Field(None, description="Line in source file")
    logic_ref: str | None = Field(
        None, description="Original code snippet for reference"
    )


# =============================================================================
# Resource Definitions (Data Sources and Sinks)
# =============================================================================


class ColumnConstraints(BaseModel):
    """Constraints on a column for optimization and validation."""
    
    nullable: bool = Field(True, description="Whether NULL is allowed")
    unique: bool = Field(False, description="Whether values must be unique")
    primary_key: bool = Field(False, description="Part of primary key")
    foreign_key_ref: str | None = Field(None, description="Reference to another resource.column")


class SemanticMetadata(BaseModel):
    """Business metadata for semantic layer integration."""
    
    synonyms: list[str] = Field(default_factory=list, description="Alternative names users might use")
    pii_classification: str | None = Field(None, description="PII level: NONE, SENSITIVE, RESTRICTED")
    business_definition: str | None = Field(None, description="Plain English definition")
    example_values: list[str] = Field(default_factory=list, description="Sample values for context")


class Attribute(BaseModel):
    """A column/field in a resource."""

    name: str = Field(..., description="Column name")
    logical_type: LogicalType = Field(
        LogicalType.L_UNKNOWN, description="Universal data type"
    )
    semantic_role: SemanticRole = Field(
        SemanticRole.UNKNOWN, description="Business meaning"
    )
    constraints: ColumnConstraints = Field(
        default_factory=ColumnConstraints, description="Column constraints"
    )
    semantic_metadata: SemanticMetadata | None = Field(
        None, description="Business metadata for semantic layer"
    )
    description: str | None = Field(None, description="Business description")
    requires_masking: bool = Field(
        False, 
        description="If True, Snowflake generator applies Tag-based Masking Policy"
    )


class PhysicalMapping(BaseModel):
    """How the resource maps to physical storage in the source system."""

    legacy_name: str | None = Field(None, description="Original table/file name")
    legacy_technology: str | None = Field(None, description="e.g., PYSPARK_TABLE")
    connection_string: str | None = Field(None, description="Connection reference")
    format: str | None = Field(None, description="File format (csv, parquet, etc.)")


class Resource(BaseModel):
    """
    A logical data resource (table, file, stream).

    This is the agnostic representation of data_in/data_out.
    """

    id: str = Field(..., description="Unique identifier (e.g., res_001)")
    agnostic_name: str = Field(..., description="Logical business name")
    resource_type: str = Field(
        "TABLE", description="TABLE, FILE, STREAM, API, etc."
    )
    direction: str = Field("INPUT", description="INPUT, OUTPUT, or BOTH")

    # Schema contract
    attributes: list[Attribute] = Field(
        default_factory=list, description="Column definitions"
    )
    warnings: list[ConversionWarning] = Field(
        default_factory=list,
        description="Warnings affecting this resource"
    )

    # Physical vs ephemeral
    is_ephemeral: bool = Field(
        False,
        description="If true, this is an in-memory temporary table (not a physical Data-In/Data-Out). "
        "If false, it represents a physical entity that needs to be created in Snowflake.",
    )

    # Physical mapping (optional, for documentation)
    physical_mapping: PhysicalMapping | None = Field(
        None, description="Source system details"
    )

    # Traceability
    origin_bridge: OriginBridge | None = Field(
        None, description="Link to original ASG element"
    )


# =============================================================================
# Transformation Steps: The Data Flow
# =============================================================================


class JoinKey(BaseModel):
    """A join condition between two columns."""

    left: str = Field(..., description="Column from left side")
    right: str = Field(..., description="Column from right side")


class ColumnLineage(BaseModel):
    """Precise column-level lineage with step reference."""
    
    column: str = Field(..., description="Column name")
    from_step: str | None = Field(None, description="Step ID where this column originates")
    from_resource: str | None = Field(None, description="Resource ID if from input source")
    source_ref: str | None = Field(
        None, 
        description="Original resource ID for disambiguation after JOINs. "
        "Enables SQL generation with correct table aliases."
    )


class ColumnOperation(BaseModel):
    """A single column derivation or transformation."""

    target_column: str = Field(..., description="Output column name")
    semantic_role: SemanticRole = Field(
        SemanticRole.UNKNOWN, description="Business classification"
    )
    expression: ExpressionNode | None = Field(
        None, description="Computation logic as expression tree"
    )
    conditional: ConditionalExpression | None = Field(
        None, description="CASE WHEN logic"
    )
    description: str | None = Field(None, description="Business description")
    input_sources: list[ColumnLineage] | None = Field(
        None,
        description="Precise column-level lineage with step references. "
        "Critical for back-propagation and impact analysis.",
    )


class ProducedColumn(BaseModel):
    """A column available after a step's execution."""
    
    name: str = Field(..., description="Column name")
    ssa_id: str | None = Field(
        None,
        description="SSA (Static Single Assignment) identifier for this column version. "
                    "Format: {column_name}_v{version}_{branch}. "
                    "E.g., 'local_tax_v1_LATAM', 'amount_v0'. "
                    "Enables precise lineage tracking through branches."
    )
    source_step: str | None = Field(
        None, description="Step ID that created/modified this column"
    )
    source_resource: str | None = Field(
        None, description="Original resource ID for disambiguation"
    )
    logical_type: LogicalType = Field(
        LogicalType.L_UNKNOWN, description="Data type"
    )
    derivation: str | None = Field(
        None,
        description="Expression showing how this column was derived. "
                    "Uses SSA IDs for inputs, e.g., 'amount_v0 * 0.16'"
    )


class ExternalRefMetadata(BaseModel):
    """Metadata for external functions (UDFs, stored procedures)."""
    
    function_name: str = Field(..., description="Name of the external function")
    language: str = Field("PYTHON", description="Implementation language")
    runtime_version: str | None = Field(None, description="Runtime version (e.g., 3.10)")
    dependencies: list[str] = Field(
        default_factory=list,
        description="List of external dependencies (imports, libraries) required by this function"
    )


# =============================================================================
# Router and Reconciliation Models (SSA Support)
# =============================================================================


class RouterBranch(BaseModel):
    """A branch in a routing decision."""
    
    label: str = Field(..., description="Branch identifier (e.g., 'true', 'LATAM_PATH')")
    condition_value: str | None = Field(
        None, description="Value that triggers this branch (e.g., 'true', 'LATAM')"
    )
    target_variable: str | None = Field(
        None, description="SSA variable produced by this branch (e.g., 'df_processed_LATAM')"
    )
    steps: list[str] = Field(
        default_factory=list,
        description="Step IDs executed in this branch"
    )
    terminal_step: str | None = Field(
        None, description="Last step ID in this branch (for convergence)"
    )


class RouterLogic(BaseModel):
    """
    Defines conditional routing logic for ROUTE steps.
    
    Transforms procedural if/else into declarative routing that
    Snowflake can translate to Dynamic Table filters or CASE statements.
    """
    
    expression: str = Field(
        ..., description="The routing condition expression"
    )
    expression_parsed: "ExpressionNode | None" = Field(
        None, description="Parsed AST of the expression"
    )
    branches: list[RouterBranch] = Field(
        default_factory=list, description="Available routing paths"
    )
    is_exhaustive: bool = Field(
        True, description="Whether all cases are covered (has else/default)"
    )


class ReconciliationStrategy(str, Enum):
    """How to merge SSA versions at convergence."""
    
    UNION_BY_BRANCH = "UNION_BY_BRANCH"  # UNION ALL with branch indicator
    COALESCE = "COALESCE"  # Take first non-null
    CASE_BY_CONDITION = "CASE_BY_CONDITION"  # CASE WHEN based on router condition


class TypeMismatchPolicy(str, Enum):
    """How to handle type mismatches between branches."""
    
    FORCE_CAST = "FORCE_CAST"  # Cast to target type, fail on error
    TRY_CAST = "TRY_CAST"  # Cast to target type, NULL on error
    COERCE_TO_HIGHER_PRECISION = "COERCE_TO_HIGHER_PRECISION"  # Widen type


class ReconciliationColumn(BaseModel):
    """Configuration for reconciling a single column across branches."""
    
    name: str = Field(..., description="Output column name")
    ssa_id: str = Field(
        ..., description="SSA ID for unified output (e.g., 'local_tax_v2_UNIFIED')"
    )
    logical_type: LogicalType = Field(
        ..., description="Target type after reconciliation"
    )
    input_ssa_ids: list[str] = Field(
        default_factory=list,
        description="SSA IDs from each branch being merged "
                    "(e.g., ['local_tax_v1_LATAM', 'local_tax_v1_STD'])"
    )
    derivation_strategy: ReconciliationStrategy = Field(
        ReconciliationStrategy.UNION_BY_BRANCH,
        description="How to combine the branch versions"
    )


class ReconciliationConfig(BaseModel):
    """
    Configuration for RECONCILE steps.
    
    Defines how to merge SSA versions from different branches
    into a unified output contract.
    """
    
    input_refs: list[str] = Field(
        default_factory=list,
        description="SSA variable IDs being merged (e.g., ['df_processed_LATAM', 'df_processed_STD'])"
    )
    produced_columns: list[ReconciliationColumn] = Field(
        default_factory=list,
        description="Columns produced with unified SSA IDs"
    )
    on_type_mismatch: TypeMismatchPolicy = Field(
        TypeMismatchPolicy.COERCE_TO_HIGHER_PRECISION,
        description="Policy when branch types don't match"
    )
    router_ref: str | None = Field(
        None, description="Reference to the ROUTE step that created the branches"
    )
    handler: str | None = Field(None, description="Handler path (module.function)")
    dependencies: list[str] = Field(default_factory=list, description="Required packages")
    is_deterministic: bool = Field(True, description="Whether function is deterministic")
    source_file_ref: str | None = Field(None, description="Reference to source manifest")
    return_type: str | None = Field(None, description="Return type of the function")


class FlowStep(BaseModel):
    """
    A single step in the data flow.

    This is the agnostic representation of a transformation node.
    """

    step_id: str = Field(..., description="Unique step identifier")
    intent: PrimitiveIntent = Field(..., description="What this step does")
    description: str | None = Field(None, description="Business description")

    # Input references
    input_refs: list[str] = Field(
        default_factory=list,
        description="IDs of input resources or previous steps",
    )

    # For FILTER
    filter_logic: ExpressionNode | None = Field(
        None, description="Filter condition"
    )

    # For SELECT
    columns: list[str] | None = Field(
        None, description="Columns to select/project"
    )
    column_aliases: dict[str, str] | None = Field(
        None, description="Column rename mapping"
    )

    # For TRANSFORM
    operations: list[ColumnOperation] | None = Field(
        None, description="Column transformations"
    )

    # For JOIN
    join_type: JoinType | None = Field(None, description="Type of join")
    join_keys: list[JoinKey] | None = Field(None, description="Join conditions")
    right_input_ref: str | None = Field(None, description="Right side of join")

    # For ROUTE (conditional branching)
    router_logic: RouterLogic | None = Field(
        None,
        description="Routing configuration for conditional branches. "
                    "Transforms if/else into declarative routing."
    )

    # For RECONCILE (merge branches)
    reconciliation_config: ReconciliationConfig | None = Field(
        None,
        description="Configuration for merging SSA versions from different branches. "
                    "Ensures type consistency at convergence points."
    )

    # For REDUCE (aggregation)
    group_keys: list[str] | None = Field(None, description="GROUP BY columns")
    aggregations: list[ColumnOperation] | None = Field(
        None, description="Aggregate expressions"
    )

    # For SORT
    sort_keys: list[str] | None = Field(None, description="ORDER BY columns")
    sort_ascending: list[bool] | None = Field(
        None, description="Sort direction per key"
    )

    # For SINK
    output_ref: str | None = Field(None, description="Target resource ID")
    write_mode: str | None = Field(
        None, description="OVERWRITE, APPEND, MERGE, etc."
    )

    # For OPAQUE
    external_ref: str | None = Field(None, description="UDF or external function name")
    external_ref_metadata: ExternalRefMetadata | None = Field(
        None, description="Detailed metadata for external functions/UDFs"
    )
    risk_level: str | None = Field(None, description="LOW, MEDIUM, HIGH")
    
    # Schema Propagation: Columns available after this step
    produced_columns: list[ProducedColumn] = Field(
        default_factory=list,
        description="All columns available after this step executes. "
        "Computed by inheriting from inputs, adding new columns, removing dropped."
    )
    
    # Determinism tracking (for incremental refresh optimization)
    is_deterministic: bool = Field(
        True,
        description="Whether this step produces deterministic results. "
                    "False for current_timestamp(), rand(), uuid(), etc. "
                    "Affects Dynamic Table incremental refresh strategy."
    )
    non_deterministic_reason: str | None = Field(
        None,
        description="Explanation if is_deterministic=False, e.g., 'uses current_timestamp()'"
    )
    
    # Control Flow: Link to source control structure
    control_ref: str | None = Field(
        None,
        description="Reference to ControlNode ID from ASG-Spark (e.g., ctrl_001)"
    )
    branch_label: str | None = Field(
        None,
        description="Which branch this step belongs to (e.g., 'true', 'false', 'case_1')"
    )
    branch_condition: str | None = Field(
        None,
        description="Condition expression for this branch (for SQL CASE WHEN generation)"
    )
    is_unrolled: bool = Field(
        False,
        description="True if this step was unrolled from a loop (CODE_GENERATION)"
    )
    unroll_index: int | None = Field(
        None,
        description="Loop iteration index if unrolled (e.g., 0, 1, 2, ...)"
    )
    
    # Audit: Warnings propagated from Phase 1 or generated in Phase 2
    warnings: list[ConversionWarning] = Field(
        default_factory=list,
        description="Warnings and issues affecting this step"
    )
    warning: str | None = Field(None, description="Migration warning message")

    # Traceability
    origin_bridge: OriginBridge | None = Field(
        None, description="Link to original transformation"
    )

    # Contract (for back-propagation)
    required_columns: list[str] = Field(
        default_factory=list,
        description="Columns this step requires from upstream",
    )


    class Config:
        use_enum_values = True


# =============================================================================
# Logical Unit: A Complete Data Pipeline
# =============================================================================


class AgnosticControlFlow(BaseModel):
    """
    Represents control flow structure in the Agnostic ASG.
    
    This is the agnostic representation of a ControlNode from ASG-Spark.
    It defines how FlowSteps are conditionally executed or iterated.
    """
    
    control_id: str = Field(..., description="Unique control flow identifier")
    control_type: str = Field(
        ..., 
        description="BRANCH, LOOP, PROTECTED, SCOPED"
    )
    
    # Condition or iterator expression (agnostic form)
    condition: str | None = Field(
        None, description="Condition expression for BRANCH/LOOP"
    )
    
    # Branch definitions
    branches: list[dict] = Field(
        default_factory=list,
        description="List of branches with {label, condition, step_ids}"
    )
    
    # Exit behavior
    exit_strategy: str = Field(
        "MERGE", description="MERGE, INDEPENDENT_SINK, TERMINATE"
    )
    
    # SSA Convergence (for MERGE exit strategy)
    merge_id: str | None = Field(
        None,
        description="ID of the reconciliation step that unifies SSA branches. "
                    "E.g., 'reconcile_regional_v1'"
    )
    convergence_step_id: str | None = Field(
        None,
        description="First FlowStep after branches converge (from ASG-Spark convergence_point)"
    )
    
    # Loop specifics
    loop_type: str | None = Field(
        None, description="CODE_GENERATION, DATA_ITERATION, TABLE_ITERATION"
    )
    unroll_count: int | None = Field(
        None, description="Number of iterations if unrolled"
    )
    is_unrollable: bool = Field(
        False, description="True if loop iterates over static iterable known at compile time"
    )
    static_iterable: list | None = Field(
        None, description="The static values to unroll (e.g., ['a', 'b', 'c'] or [0, 1, 2])"
    )
    
    # Translation hints
    is_translatable: bool = Field(
        True, description="Can be translated to target SQL/code"
    )
    translation_strategy: str | None = Field(
        None, 
        description="CASE_WHEN, STORED_PROCEDURE, UDF, UNROLL, BRANCH_FILTER"
    )
    opaque_code: str | None = Field(
        None,
        description="Structured opacity code: UNSUPPORTED_LIB, IO_SIDE_EFFECT, COMPLEX_RECURSION, DYNAMIC_SCHEMA, EXTERNAL_API, STATEFUL_ITERATION"
    )
    opaque_reason: str | None = Field(
        None, description="Human-readable explanation of opacity"
    )
    
    # Source reference
    source_control_ref: str | None = Field(
        None, description="Original ControlNode ID from ASG-Spark"
    )


class LogicalUnit(BaseModel):
    """
    A self-contained logical unit of work.

    This represents a complete data pipeline (function, stored procedure,
    dbt model, SSIS package, etc.) as an agnostic flow.
    """

    id: str = Field(..., description="Unique identifier (e.g., lu_001)")
    name: str = Field(..., description="Logical name")
    purpose: str | None = Field(None, description="Business purpose description")
    source_technology: SourceTechnology = Field(
        SourceTechnology.UNKNOWN, description="Original technology"
    )

    # The data flow
    steps: list[FlowStep] = Field(
        default_factory=list, description="Ordered transformation steps"
    )

    # Entry and exit points
    input_resources: list[str] = Field(
        default_factory=list, description="Resource IDs consumed"
    )
    output_resources: list[str] = Field(
        default_factory=list, description="Resource IDs produced"
    )

    # Traceability
    origin_bridge: OriginBridge | None = Field(
        None, description="Link to original function/procedure"
    )


# =============================================================================
# Manifest Source: Original File Reference
# =============================================================================


class ManifestSource(BaseModel):
    """A source file in the original codebase."""

    id: str = Field(..., description="Unique identifier")
    source_type: SourceTechnology = Field(..., description="Technology type")
    path: str = Field(..., description="File path")
    checksum: str | None = Field(None, description="File hash for versioning")


# =============================================================================
# The Agnostic Semantic Graph (Root Model)
# =============================================================================



class IntegritySummary(BaseModel):
    """Summary of warnings and issues across the entire ASG-A."""
    
    total_warnings: int = Field(0, description="Total number of warnings")
    critical_count: int = Field(0, description="Count of CRITICAL warnings")
    high_count: int = Field(0, description="Count of HIGH warnings")
    medium_count: int = Field(0, description="Count of MEDIUM warnings")
    low_count: int = Field(0, description="Count of LOW warnings")
    unacknowledged_count: int = Field(0, description="Warnings not yet reviewed")
    blocking_generation: bool = Field(
        False, description="True if any CRITICAL/HIGH warnings block Phase 3"
    )
    warnings_by_step: dict[str, int] = Field(
        default_factory=dict, description="Warning count per step"
    )


class AgnosticASG(BaseModel):
    """
    The Agnostic Semantic Graph (ASG-A).

    This is the "Lingua Franca" for data engineering. It is:
    - Self-contained: Works without the original source ASG
    - Universal: Same structure for Spark, SSIS, dbt, Informatica
    - Traceable: Optional lineage to original source code
    """

    # Version and metadata
    agnostic_version: str = Field("1.0", description="ASG-A schema version")
    generated_at: str | None = Field(None, description="When the conversion was performed (ISO format)")
    source_asg_version: str | None = Field(
        None, description="Version of source ASG used for conversion"
    )

    # Original sources (for reference)
    manifest_sources: list[ManifestSource] = Field(
        default_factory=list, description="Original source files"
    )

    # Resources (data sources and sinks)
    resources: list[Resource] = Field(
        default_factory=list, description="All data resources"
    )

    # Logical units (pipelines, functions, procedures)
    logical_units: list[LogicalUnit] = Field(
        default_factory=list, description="Transformation logic"
    )

    # Workflow DAG: Dependencies between LogicalUnits
    workflow_dag: dict[str, list[str]] = Field(
        default_factory=dict,
        description="DAG of LogicalUnit dependencies. Key depends on values. "
        "e.g., {'lu_apply_business_logic': ['lu_process_bronze_to_silver']}"
    )
    
    # Control Flow: Agnostic representation of control structures
    control_flows: list[AgnosticControlFlow] = Field(
        default_factory=list,
        description="Control flow structures (branches, loops, error handling)"
    )
    
    # Audit & Integrity
    integrity_summary: IntegritySummary | None = Field(
        None, description="Summary of all warnings for pre-generation validation"
    )
    propagated_warnings: list[ConversionWarning] = Field(
        default_factory=list,
        description="Global warnings from Phase 1 not tied to specific steps"
    )

    # Global type mappings (optional)
    type_mappings: dict[str, LogicalType] | None = Field(
        None, description="Custom type mappings from source"
    )

    class Config:
        use_enum_values = True


# Update forward references
ExpressionNode.model_rebuild()
