"""
Scala Spark ASG Parser — extracts DataFrame operations from Scala code using tree-sitter.

Traverses the tree-sitter CST to identify:
- Data sources (spark.read.csv/parquet/json/format/table/jdbc)
- Data sinks (df.write.csv/parquet/mode/save/saveAsTable/insertInto)
- Transformations (select, filter, join, groupBy, withColumn, etc.)
- Function/method definitions with parameters
- Execution calls (function invocations with argument bindings)
- Import declarations
- Variable assignments (val/var bindings)
- Column references (col(), $"", .alias())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tree_sitter_scala
from tree_sitter import Language, Node, Parser

from warp_core.ir.pyspark_models import (
    ASG,
    CallBindings,
    CalleeRef,
    CallLocation,
    DataSink,
    DataSource,
    ExecutionCall,
    ExtractionMetadata,
    FunctionArgument,
    FunctionDefinition,
    ImportEntry,
    InferenceConfidence,
    InferenceSource,
    InferredColumn,
    InputBinding,
    InputColumn,
    OutputBinding,
    SourceFile,
    SourceLocation,
    TransformationNode,
    WindowSpecDefinition,
    ColumnConstraint,
    ConstraintType,
    ColumnRelationship,
    RelationshipType,
    ControlNode,
    ControlType,
    ControlLogic,
    ControlBranch,
    ExitStrategy,
    LoopType,
    AnalysisWarning,
    WarningSeverity,
)
from asg_scala.scala_spark_functions import (
    ACTION_FUNCS,
    AGGREGATION_FUNCS as _AGG_FUNCTIONS,
    COLUMN_RETURNING_FUNCS,
    DATA_TYPE_NAMES,
    READ_FORMATS,
    SPARK_READ_ENTRY,
    SPARK_WRITE_ENTRY,
    TRANSFORM_OPS,
    WRITE_FORMATS,
    WRITE_METHODS,
)

SCALA_LANGUAGE = Language(tree_sitter_scala.language())


@dataclass
class SharedFunctionEntry:
    """A function body collected from one file that can be used by parsers of other files.

    Keeps the tree-sitter ``body_node`` alive (which in turn keeps the entire
    parse tree alive) so that ``start_byte`` / ``end_byte`` offsets remain
    valid.  ``source_bytes`` is the raw UTF-8 content of the originating file,
    required for :py:meth:`ScalaSparkParser._text` to decode node text.
    """

    body_node: object
    params: list[str]
    source_bytes: bytes
    file_path: str

_HELPER_READ_METHODS: frozenset[str] = frozenset({
    "readDfFromRds",
    "read_df_from_rds",
    "readDataframe",
    "read_dataframe",
    "readFromRds",
    "read_from_rds",
})

# Spark DataType constructor names — derived from the API inventory (element_type=DataType).
# Covers all primitive and complex types including TimestampNTZType (Spark 3.4+).
# Re-generated automatically via: python scripts/generate_scala_functions.py
_TYPE_CONSTRUCTORS: frozenset[str] = DATA_TYPE_NAMES

_HELPER_SINK_METHODS: frozenset[str] = frozenset({
    "dataUpdateIntoS3",
    "data_update_into_s3",
    "writeTable",
    "write_table",
    "truncateAndWriteTable",
    "truncate_and_write_table",
    "executeQuery",
    "execute_query",
    "writeFrumRestlist",
    "write_frum_restlist",
    "snowflakeUpdate",
    "snowflake_update",
    "writeDataframeInRds",
    "write_dataframe_in_rds",
    "overwriteDataframeInRds",
    "overwrite_dataframe_in_rds",
    "updateDataIntoRds",
    "update_data_into_rds",
    "insertIntoRds",
    "insert_into_rds",
    "updateData",
    "update_data",
    "insertInto",
    "insert_into",
    "writeDataframeInSnowflake",
    "write_dataframe_in_snowflake",
})

_HELPER_DUAL_METHODS: frozenset[str] = frozenset({
    "updateDataIntoRds",
    "update_data_into_rds",
    "insertIntoRds",
    "insert_into_rds",
    "snowflakeUpdate",
    "snowflake_update",
})



class ScalaSparkParser:
    """Extracts ASG from Scala Spark source using tree-sitter."""

    _global_id_counter: int = 0

    def __init__(
        self,
        companion_symbols: dict[str, str] | None = None,
        shared_functions: dict[str, "SharedFunctionEntry"] | None = None,
    ) -> None:
        self._parser = Parser(SCALA_LANGUAGE)
        self._data_in: list[DataSource] = []
        self._data_out: list[DataSink] = []
        self._transformations: list[TransformationNode] = []
        self._functions: list[FunctionDefinition] = []
        self._execution_calls: list[ExecutionCall] = []
        self._imports: dict[str, ImportEntry] = {}
        self._variables: dict[str, str | None] = {}
        self._function_tx_ranges: dict[str, tuple[int, int]] = {}
        self._function_di_ranges: dict[str, tuple[int, int]] = {}
        self._function_tail_calls: dict[str, str] = {}
        # Maps function name -> (body_node, params) for argument-inlining (file-local)
        self._function_bodies: dict[str, tuple[object, list[str]]] = {}
        # Scope stack for local string constants.  Each frame is a dict mapping
        # var_name -> string_value for val declarations seen in that scope.
        # Frame 0 = file-level / object-level scope; inner frames correspond to
        # function bodies.  Only ``val`` (immutable) String literals are tracked.
        # Resolution searches from the innermost frame outward (local wins).
        self._scope_stack: list[dict[str, str]] = [{}]
        self._seq_string_vars: dict[str, list[str]] = {}
        self._struct_schema_vars: dict[str, list[tuple[str, str]]] = {}
        # Cross-file companion object symbol table: "ObjName.CONST" -> "string_value"
        self._companion_symbols: dict[str, str] = companion_symbols or {}
        # Cross-file function body registry shared across all files in a project.
        # Keys are short function names (no object prefix).  Populated by the
        # directory parser's pre-pass via ``_build_shared_function_registry()``.
        self._shared_functions: dict[str, SharedFunctionEntry] = shared_functions or {}
        self._window_specs: list[WindowSpecDefinition] = []
        self._column_constraints: list[ColumnConstraint] = []
        self._column_relationships: list[ColumnRelationship] = []
        self._control_nodes: list[ControlNode] = []
        self._current_function: str = "__main__"
        self._filepath: str = ""
        self._source_bytes: bytes = b""
        self._warnings: list[AnalysisWarning] = []

    # ------------------------------------------------------------------
    # Scope-stack helpers for local string constant tracking
    # ------------------------------------------------------------------

    def _push_scope(self) -> None:
        """Push a new variable scope frame (called on entering object/function body)."""
        self._scope_stack.append({})

    def _pop_scope(self) -> None:
        """Pop the innermost variable scope frame (called on exiting object/function body)."""
        if len(self._scope_stack) > 1:
            self._scope_stack.pop()

    def _set_string_var(self, name: str, value: str) -> None:
        """Record a ``val name = "literal"`` assignment in the current scope."""
        self._scope_stack[-1][name] = value

    def _get_string_var(self, name: str) -> str | None:
        """Look up a string variable, searching from innermost scope outward."""
        for frame in reversed(self._scope_stack):
            if name in frame:
                return frame[name]
        return None

    def _is_test_file(self) -> bool:
        """Return True if the current file lives under a test directory."""
        path_lower = self._filepath.lower().replace("\\", "/")
        return (
            "/test/" in path_lower
            or "/tests/" in path_lower
            or "/spec/" in path_lower
            or "spec.scala" in path_lower
            or path_lower.startswith("test/")
            or path_lower.startswith("tests/")
        )

    @classmethod
    def reset_global_counters(cls) -> None:
        cls._global_id_counter = 0

    def _next_id(self, prefix: str) -> str:
        ScalaSparkParser._global_id_counter += 1
        return f"{prefix}_{ScalaSparkParser._global_id_counter:03d}"

    def _loc(self, node: Node) -> SourceLocation:
        return SourceLocation.create(
            pathfile=self._filepath,
            start_line=node.start_point[0] + 1,
            start_col=node.start_point[1],
            end_line=node.end_point[0] + 1,
            end_col=node.end_point[1],
        )

    def _text(self, node: Node | None) -> str:
        if node is None:
            return ""
        return node.text.decode("utf-8")

    def _string_value(self, node: Node) -> str:
        """Extract string content without quotes."""
        t = self._text(node)
        if t.startswith('s"""') and t.endswith('"""'):
            return t[4:-3]
        if t.startswith('"""') and t.endswith('"""'):
            return t[3:-3]
        if t.startswith('s"') and t.endswith('"'):
            return t[2:-1]
        if t.startswith('"') and t.endswith('"'):
            return t[1:-1]
        return t

    # ------------------------------------------------------------------
    # Chain unwinding
    # ------------------------------------------------------------------

    def _record_regex_fallback(
        self,
        context: str,
        raw_snippet: str,
        recovered: dict[str, Any],
        failure_reason: str = "sqlglot parse failed",
        line: int = 0,
    ) -> None:
        """Record a regex fallback event as an AnalysisWarning."""
        elements = ", ".join(f"{k}={v}" for k, v in recovered.items() if v)
        self._warnings.append(AnalysisWarning(
            code="W_PAR_001",
            severity=WarningSeverity.WARNING,
            message=(
                f"Regex fallback used for {context}: {failure_reason}. "
                f"Recovered: {elements or 'none'}"
            ),
            source_file=self._filepath,
            source_line=line,
            regex_evidence={
                "match_type": context,
                "raw_snippet": raw_snippet[:200],
                "identified_elements": recovered,
                "failure_reason": failure_reason,
                "primary_parser": "sqlglot",
            },
        ))

    def _unwind_chain(self, node: Node) -> list[tuple[str, Node | None, Node]]:
        """Unwind a method-call chain into [(method_name, args_node, call_node)]."""
        chain: list[tuple[str, Node | None, Node]] = []
        self._collect_chain(node, chain)
        return chain

    def _collect_chain(
        self, node: Node, chain: list[tuple[str, Node | None, Node]]
    ) -> None:
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            if fn and fn.type == "field_expression":
                obj = fn.named_children[0] if fn.named_children else None
                method_node = fn.child_by_field_name("name")
                if method_node is None:
                    for c in fn.named_children:
                        if c.type == "identifier" and c != obj:
                            method_node = c
                            break
                method_name = self._text(method_node) if method_node else ""
                chain.append((method_name, args, node))
                if obj:
                    self._collect_chain(obj, chain)
            elif fn and fn.type == "identifier":
                chain.append((self._text(fn), args, node))
            elif fn and fn.type == "call_expression":
                self._collect_chain(fn, chain)
        elif node.type == "field_expression":
            obj = node.named_children[0] if node.named_children else None
            field_node = node.child_by_field_name("name")
            if field_node is None:
                for c in node.named_children:
                    if c.type == "identifier" and c != obj:
                        field_node = c
                        break
            chain.append((self._text(field_node) if field_node else "", None, node))
            if obj:
                self._collect_chain(obj, chain)
        elif node.type == "identifier":
            chain.append((self._text(node), None, node))

    # ------------------------------------------------------------------
    # Argument extraction helpers
    # ------------------------------------------------------------------

    def _arg_strings(self, args_node: Node | None, *, resolve_vars: bool = False) -> list[str]:
        """Extract string-literal arguments from an arguments node.

        Handles .stripMargin calls on multi-line strings
        (e.g. s\"\"\"...\"\"\"\".stripMargin).
        When *resolve_vars* is True, also resolves identifiers via _string_vars.
        Also resolves companion object constant references (e.g.
        EconomicPosition.AM_ECONOMIC_POSITION) via _companion_symbols.

        Handles Scala named-argument syntax (``param=value``) by unwrapping the
        ``assignment_expression`` node and extracting the value part.  Without
        this, calls like ``update_data_into_rds(tableName=tableName, …)`` would
        produce an empty string list and fall back to a SRC_* placeholder name.
        """
        if args_node is None:
            return []
        results: list[str] = []
        for c in args_node.named_children:
            if c.type == "assignment_expression":
                # Named argument: param=value — extract the value (last named child).
                named = c.named_children
                val_node = named[-1] if named else None
                if val_node is None:
                    continue
                if val_node.type in ("string", "interpolated_string_expression"):
                    results.append(self._string_value(val_node))
                elif val_node.type == "call_expression":
                    inner = self._unwrap_strip_margin(val_node)
                    if inner is not None:
                        results.append(inner)
                elif val_node.type == "field_expression":
                    inner = self._unwrap_strip_margin_field(val_node)
                    if inner is not None:
                        results.append(inner)
                    else:
                        resolved = self._resolve_companion_field(val_node)
                        if resolved is not None:
                            results.append(resolved)
                elif resolve_vars and val_node.type == "identifier":
                    resolved = self._get_string_var(self._text(val_node))
                    if resolved is not None:
                        results.append(resolved)
            elif c.type == "string":
                results.append(self._string_value(c))
            elif c.type == "interpolated_string_expression":
                results.append(self._string_value(c))
            elif c.type == "call_expression":
                inner = self._unwrap_strip_margin(c)
                if inner is not None:
                    results.append(inner)
            elif c.type == "field_expression":
                inner = self._unwrap_strip_margin_field(c)
                if inner is not None:
                    results.append(inner)
                else:
                    resolved = self._resolve_companion_field(c)
                    if resolved is not None:
                        results.append(resolved)
            elif resolve_vars and c.type == "identifier":
                resolved = self._get_string_var(self._text(c))
                if resolved is not None:
                    results.append(resolved)
        return results

    def _resolve_companion_field(self, node: Node) -> str | None:
        """Resolve a field_expression like EconomicPosition.AM_ECONOMIC_POSITION.

        Looks up ``ObjectName.FIELD`` in the cross-file companion symbol table.
        Returns the resolved string value, or *None* if not found.
        """
        raw = self._text(node)
        # field_expression text is e.g. "EconomicPosition.AM_ECONOMIC_POSITION"
        if "." not in raw:
            return None
        return self._companion_symbols.get(raw)

    def _unwrap_strip_margin(self, node: Node) -> str | None:
        """Unwrap s\"\"\"...\"\"\"  .stripMargin() (call form) -> raw string."""
        fn = node.child_by_field_name("function")
        if fn is None or fn.type != "field_expression":
            return None
        return self._unwrap_strip_margin_field(fn)

    def _unwrap_strip_margin_field(self, node: Node) -> str | None:
        """Unwrap s\"\"\"...\"\"\"  .stripMargin (field form) -> raw string.

        Handles both `str.stripMargin` (field_expression) and
        `str.stripMargin()` (call_expression wrapping field_expression).
        """
        if node.type != "field_expression":
            return None
        method_node = node.child_by_field_name("name")
        if method_node is None:
            for c in node.named_children:
                if c.type == "identifier" and c != node.named_children[0]:
                    method_node = c
                    break
        if method_node is None or self._text(method_node) != "stripMargin":
            return None
        obj = node.named_children[0] if node.named_children else None
        if obj is None:
            return None
        if obj.type in ("string", "interpolated_string_expression"):
            raw = self._string_value(obj)
            return raw.replace("|", "").strip()
        return None

    def _first_arg_string(self, args_node: Node | None, *, resolve_vars: bool = False) -> str | None:
        strs = self._arg_strings(args_node, resolve_vars=resolve_vars)
        return strs[0] if strs else None

    def _arg_identifiers(self, args_node: Node | None) -> list[str]:
        if args_node is None:
            return []
        return [self._text(c) for c in args_node.named_children if c.type == "identifier"]

    # Maps Spark SQL type identifiers to our internal type strings
    _SPARK_TYPE_MAP: dict[str, str] = {
        "StringType": "STRING",
        "IntegerType": "INT",
        "LongType": "L_INT",
        "DoubleType": "DOUBLE",
        "FloatType": "FLOAT",
        "BooleanType": "BOOLEAN",
        "DateType": "DATE",
        "TimestampType": "TIMESTAMP",
        "TimestampNTZType": "TIMESTAMP",
        "BinaryType": "BINARY",
        "ByteType": "INT",
        "ShortType": "INT",
        "DecimalType": "NUMERIC",
        "ArrayType": "ARRAY",
        "MapType": "MAP",
        "StructType": "STRUCT",
    }

    def _extract_struct_field_type(self, args_node: Node) -> str:
        """Extract the Spark type from a StructField's argument list.

        StructField("col_name", StringType, nullable = true)
                                ^^^^^^^^^^^ second positional arg
        Returns our internal type string (e.g. "STRING"), or "UNKNOWN".
        """
        children = [c for c in args_node.named_children]
        if len(children) < 2:
            return "UNKNOWN"
        type_node = children[1]
        type_text = self._text(type_node)
        # Handle generic types like DecimalType(18, 3) or ArrayType(StringType)
        base_type = type_text.split("(")[0].strip()
        return self._SPARK_TYPE_MAP.get(base_type, "UNKNOWN")

    def _extract_struct_fields_from_node(self, node: Node) -> list[tuple[str, str]]:
        """Extract (col_name, type) pairs from a List/Seq/Array of StructField calls.

        Handles both:
          List(StructField("col", StringType), ...)
          StructType(List(StructField("col", StringType), ...))
        """
        fields: list[tuple[str, str]] = []
        for c in node.named_children:
            if c.type == "call_expression":
                fn = c.child_by_field_name("function")
                fn_name = self._text(fn) if fn else ""
                args = c.child_by_field_name("arguments")
                if fn_name == "StructField" and args:
                    col_name = self._first_arg_string(args)
                    if col_name:
                        spark_type = self._extract_struct_field_type(args)
                        fields.append((col_name, spark_type))
                elif fn_name in ("Seq", "List", "Array") and args:
                    fields.extend(self._extract_struct_fields_from_node(args))
        return fields

    # ------------------------------------------------------------------
    # Column reference extraction (GAP 4)
    # ------------------------------------------------------------------

    def _extract_col_refs_from_node(self, node: Node) -> tuple[list[str], list[str]]:
        """Extract column references by traversing the tree-sitter CST.

        Returns (input_columns, output_columns).
        - input_columns: columns read via col("x"), $"x", aggregate functions
        - output_columns: columns produced via .alias("x")
        """
        inputs: list[str] = []
        outputs: list[str] = []
        self._walk_for_col_refs(node, inputs, outputs)
        return (list(dict.fromkeys(inputs)), list(dict.fromkeys(outputs)))

    def _walk_for_col_refs(
        self, node: Node, inputs: list[str], outputs: list[str]
    ) -> None:
        """Recursively walk a CST node collecting column references."""
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            fn_name = ""

            if fn and fn.type == "identifier":
                fn_name = self._text(fn)
            elif fn and fn.type == "field_expression":
                name_node = fn.child_by_field_name("name")
                if name_node:
                    fn_name = self._text(name_node)

            if fn_name == "col" and args:
                col_name = self._first_arg_string(args)
                if col_name:
                    inputs.append(col_name)
                return

            if fn_name in _AGG_FUNCTIONS and args:
                col_name = self._first_arg_string(args)
                if col_name:
                    inputs.append(col_name)

            if fn_name == "alias" and args:
                alias_name = self._first_arg_string(args)
                if alias_name:
                    outputs.append(alias_name)

        elif node.type == "interpolated_string_expression":
            # Handle $"column_name" (Scala column shorthand)
            text = self._text(node)
            if text.startswith('$"') and text.endswith('"'):
                col_name = text[2:-1]
                if col_name and not any(c in col_name for c in "{} \n"):
                    inputs.append(col_name)
            return

        for child in node.named_children:
            self._walk_for_col_refs(child, inputs, outputs)

    # ------------------------------------------------------------------
    # Data source extraction
    # ------------------------------------------------------------------

    def _extract_data_source(
        self, chain: list[tuple[str, Node | None, Node]], assign_var: str | None
    ) -> DataSource | None:
        """Detect spark.read.format("csv").load("path") patterns."""
        methods = {name: args for name, args, _ in chain}

        if not any(m in SPARK_READ_ENTRY for m in methods):
            if not any(m in READ_FORMATS for m in methods):
                return None

        src_type = "other"
        src_format = None
        src_path = None
        src_name = None
        src_query = None
        options: dict[str, str] = {}
        first_node = chain[-1][2] if chain else chain[0][2]

        for method_name, args_node, _ in chain:
            if method_name in READ_FORMATS:
                src_type = method_name if method_name not in ("text", "textFile") else "other"
                src_path = self._first_arg_string(args_node)
                if method_name == "jdbc" and args_node:
                    strs = self._arg_strings(args_node, resolve_vars=True)
                    if len(strs) >= 2:
                        src_name = strs[1]  # table is the 2nd arg
                    elif len(strs) == 1:
                        # Try named arg: table = "..."
                        for c in args_node.named_children:
                            txt = self._text(c)
                            if txt.startswith("table"):
                                val = self._first_arg_string(c)
                                if val:
                                    src_name = val
            elif method_name == "format":
                fmt = self._first_arg_string(args_node)
                if fmt:
                    src_format = fmt
                    fmt_lower = fmt.lower()
                    if "redshift" in fmt_lower:
                        src_type = "redshift"
                    elif "jdbc" in fmt_lower or fmt == "jdbc":
                        src_type = "jdbc"
                    elif fmt_lower in ("delta", "iceberg", "snowflake", "bigquery"):
                        src_type = fmt_lower
                    elif fmt_lower in READ_FORMATS:
                        src_type = fmt_lower if fmt_lower not in ("text", "textfile") else "other"
            elif method_name == "load":
                p = self._first_arg_string(args_node)
                if p:
                    src_path = p
            elif method_name == "table":
                t = self._first_arg_string(args_node, resolve_vars=True)
                if t:
                    src_name = t
                    src_type = "table"
            elif method_name == "option":
                strs = self._arg_strings(args_node)
                if len(strs) == 1 and args_node:
                    children = [c for c in args_node.named_children if c.type not in ("comment",)]
                    if len(children) >= 2 and children[1].type == "identifier":
                        var_val = self._get_string_var(self._text(children[1]))
                        if var_val:
                            strs.append(var_val)
                if len(strs) >= 2:
                    options[strs[0]] = strs[1]
                    if strs[0].lower() in ("dbtable", "query"):
                        src_query = strs[1]
                        if strs[0].lower() == "dbtable":
                            src_name = strs[1]

        # Extract table name from SQL query when no explicit name is set
        if src_name is None and src_query:
            src_name, _used_fb = self._extract_table_from_sql(src_query)
            if _used_fb and src_name:
                self._record_regex_fallback(
                    "SQL_TABLE_EXTRACTION", src_query,
                    {"table": src_name}, line=node.start_point[0] + 1,
                )

        if src_name is None and src_path:
            src_name = (
                src_path.rsplit("/", 1)[-1]
                .replace(".csv", "").replace(".parquet", "").replace(".json", "")
            )

        src_id = self._next_id("in")
        loc = self._loc(first_node)
        if self._current_function != "__main__":
            loc.scope = self._current_function
        ds = DataSource(
            id=src_id,
            type=src_type,
            format=src_format,
            name=src_name,
            path=src_path,
            query=src_query,
            location=loc,
            is_test_file=self._is_test_file(),
        )
        # Enrich with columns from SQL query
        if src_query:
            sql_cols, _used_fb2 = self._extract_columns_from_sql(src_query)
            if _used_fb2 and sql_cols:
                self._record_regex_fallback(
                    "SQL_COLUMN_EXTRACTION", src_query,
                    {"columns": [c[0] for c in sql_cols]},
                    line=node.start_point[0] + 1,
                )
            if sql_cols:
                from warp_core.ir.pyspark_models import InferredColumn
                for col_name, _original in sql_cols:
                    ds.inferred_columns.append(InferredColumn(
                        name=col_name.lower(),
                        type="UNKNOWN",
                        source="catalog",
                    ))

        self._data_in.append(ds)

        if assign_var:
            self._variables[assign_var] = src_id

        return ds

    @staticmethod
    def _extract_table_from_sql(sql: str) -> tuple[str | None, bool]:
        """Extract the primary table name from a SQL query.

        Uses sqlglot AST as primary strategy with regex fallback for
        malformed or non-standard SQL that sqlglot cannot parse.

        Returns (table_name, used_regex_fallback).
        """
        import sqlglot
        try:
            parsed = sqlglot.parse_one(sql, error_level=sqlglot.ErrorLevel.IGNORE)
            for t in parsed.find_all(sqlglot.exp.Table):
                parts = [p for p in (t.catalog, t.db, t.name) if p]
                if parts:
                    return ".".join(parts), False
        except Exception:
            pass
        import re
        match = re.search(r"\bFROM\s+([\w.]+)", sql, re.IGNORECASE)
        return (match.group(1) if match else None), True

    @staticmethod
    def _extract_columns_from_sql(sql: str) -> tuple[list[tuple[str, str | None]], bool]:
        """Extract column names from a SQL SELECT clause.

        Uses sqlglot AST as primary strategy with regex fallback for
        malformed or non-standard SQL.

        Returns list of (final_name, original_name) where final_name is the
        alias if present.  Skips complex expressions without clear column names.
        """
        import sqlglot
        try:
            parsed = sqlglot.parse_one(sql, error_level=sqlglot.ErrorLevel.IGNORE)
            select = parsed.find(sqlglot.exp.Select)
            if select:
                columns: list[tuple[str, str | None]] = []
                for expr in select.expressions:
                    if isinstance(expr, sqlglot.exp.Star):
                        continue
                    alias = expr.alias if hasattr(expr, "alias") and expr.alias else None
                    if alias:
                        inner = expr.this if hasattr(expr, "this") else expr
                        original = inner.name if hasattr(inner, "name") and inner.name else None
                        columns.append((alias, original))
                    elif isinstance(expr, sqlglot.exp.Column):
                        columns.append((expr.name, None))
                if columns:
                    return columns, False
        except Exception:
            pass

        import re
        select_match = re.search(
            r"\bSELECT\s+(.*?)\bFROM\b", sql, re.IGNORECASE | re.DOTALL
        )
        if not select_match:
            return [], False
        select_clause = select_match.group(1).strip()
        if select_clause == "*":
            return [], False

        columns_fb: list[tuple[str, str | None]] = []
        for part in select_clause.split(","):
            part = part.strip()
            if not part:
                continue
            alias_match = re.search(r"\bAS\s+(\w+)\s*$", part, re.IGNORECASE)
            if alias_match:
                alias = alias_match.group(1)
                original = re.sub(r"\s+AS\s+\w+\s*$", "", part, flags=re.IGNORECASE).strip()
                col_match = re.match(r"^[\w.]+$", original)
                original_name = col_match.group(0).split(".")[-1] if col_match else None
                columns_fb.append((alias, original_name))
            else:
                col_match = re.match(r"^[\w.]+$", part)
                if col_match:
                    columns_fb.append((part.split(".")[-1], None))
        return columns_fb, True

    def _extract_sql_source(
        self, chain: list[tuple[str, Node | None, Node]], assign_var: str | None
    ) -> DataSource | None:
        """Detect spark.sql("SELECT ... FROM table ...") patterns."""
        methods = {name: args_node for name, args_node, _ in chain}
        if "sql" not in methods:
            return None

        # Check if the receiver is spark-related
        chain_text = " ".join(name for name, _, _ in chain)
        if not any(kw in chain_text for kw in ("spark", "sqlContext", "hiveContext")):
            return None

        sql_str = None
        sql_args_node = None
        for name, args_node, _ in chain:
            if name == "sql":
                sql_str = self._first_arg_string(args_node, resolve_vars=True)
                sql_args_node = args_node
                break

        if not sql_str:
            first_node = chain[-1][2] if chain else chain[0][2]
            src_id = self._next_id("in")
            loc = self._loc(first_node)
            if self._current_function != "__main__":
                loc.scope = self._current_function
            ds = DataSource(
                id=src_id,
                type="sql",
                name=None,
                query="runtime:spark.sql(variable)",
                location=loc,
                is_test_file=self._is_test_file(),
            )
            self._data_in.append(ds)
            if assign_var:
                self._variables[assign_var] = ds.id
            return ds

        import sqlglot
        tables: list[str] = []
        _sql_fb = False
        try:
            parsed = sqlglot.parse_one(sql_str, error_level=sqlglot.ErrorLevel.IGNORE)
            for t in parsed.find_all(sqlglot.exp.Table):
                parts = [p for p in (t.catalog, t.db, t.name) if p]
                if parts:
                    tables.append(".".join(parts))
        except Exception:
            _sql_fb = True
        if not tables:
            _sql_fb = True
        tables = list(dict.fromkeys(tables))
        if _sql_fb and tables:
            self._record_regex_fallback(
                "SQL_SOURCE_TABLES", sql_str,
                {"tables": tables},
                line=chain[0][2].start_point[0] + 1 if chain else 0,
            )

        if not tables:
            return None

        first_node = chain[-1][2] if chain else chain[0][2]
        first_ds = None
        for table_name in tables:
            src_id = self._next_id("in")
            loc = self._loc(first_node)
            if self._current_function != "__main__":
                loc.scope = self._current_function
            ds = DataSource(
                id=src_id,
                type="table",
                name=table_name,
                query=sql_str,
                location=loc,
                is_test_file=self._is_test_file(),
            )
            self._data_in.append(ds)
            if first_ds is None:
                first_ds = ds

        if assign_var and first_ds:
            self._variables[assign_var] = first_ds.id

        return first_ds

    # ------------------------------------------------------------------
    # Data sink extraction
    # ------------------------------------------------------------------

    def _extract_data_sink(
        self, chain: list[tuple[str, Node | None, Node]], source_var: str | None
    ) -> DataSink | None:
        """Detect df.write.mode("overwrite").parquet("path") patterns."""
        methods = {name: args for name, args, _ in chain}

        if not any(m in SPARK_WRITE_ENTRY for m in methods):
            return None

        sink_type = "other"
        sink_format = None
        sink_path = None
        sink_name = None
        mode = "overwrite"
        is_table_sink = False
        first_node = chain[-1][2] if chain else chain[0][2]

        for method_name, args_node, _ in chain:
            if method_name in WRITE_FORMATS:
                if not is_table_sink:
                    sink_type = method_name
                sink_path = self._first_arg_string(args_node)
                if method_name == "jdbc" and args_node:
                    strs = self._arg_strings(args_node, resolve_vars=True)
                    if len(strs) >= 2:
                        sink_name = strs[1]
                    elif args_node:
                        all_args = [c for c in args_node.named_children if c.type not in ("comment",)]
                        if len(all_args) >= 2:
                            txt = all_args[1].text.decode() if all_args[1].text else ""
                            resolved = self._get_string_var(txt)
                            if resolved:
                                sink_name = resolved
            elif method_name == "format":
                fmt = self._first_arg_string(args_node)
                if fmt:
                    sink_format = fmt
                    if not is_table_sink:
                        fmt_lower = fmt.lower()
                        if fmt_lower in WRITE_FORMATS:
                            sink_type = fmt_lower
                        elif "redshift" in fmt_lower:
                            sink_type = "redshift"
                        elif fmt_lower in ("delta", "iceberg", "snowflake", "bigquery", "jdbc"):
                            sink_type = fmt_lower
            elif method_name == "save":
                p = self._first_arg_string(args_node)
                if p:
                    sink_path = p
            elif method_name == "saveAsTable":
                t = self._first_arg_string(args_node)
                if t:
                    sink_name = t
                    sink_type = "table"
                    is_table_sink = True
            elif method_name == "insertInto":
                t = self._first_arg_string(args_node)
                if t:
                    sink_name = t
                    sink_type = "table"
                    is_table_sink = True
            elif method_name == "mode":
                m = self._first_arg_string(args_node)
                if m and m in ("overwrite", "append", "ignore", "error"):
                    mode = m
            elif method_name == "option":
                strs = self._arg_strings(args_node)
                if len(strs) == 1 and args_node:
                    children = [c for c in args_node.named_children if c.type not in ("comment",)]
                    if len(children) >= 2 and children[1].type == "identifier":
                        var_val = self._get_string_var(self._text(children[1]))
                        if var_val:
                            strs.append(var_val)
                if len(strs) >= 2 and strs[0].lower() == "dbtable":
                    sink_name = strs[1]

        if sink_name is None and sink_path:
            sink_name = sink_path.rsplit("/", 1)[-1]

        source_id = self._variables.get(source_var) if source_var else None
        if source_id is None and source_var:
            source_id = f"var:{source_var}"

        sink_id = self._next_id("out")
        loc = self._loc(first_node)
        if self._current_function != "__main__":
            loc.scope = self._current_function
        ds = DataSink(
            id=sink_id,
            type=sink_type,
            format=sink_format,
            name=sink_name,
            path=sink_path,
            mode=mode,
            source_id=source_id,
            location=loc,
            is_test_file=self._is_test_file(),
        )
        self._data_out.append(ds)
        return ds



    def _extract_helper_source(self, chain: list[tuple[str, Node | None, Node]], assign_var: str | None) -> None:
        """Detect helper function calls that read data (e.g. rdsUtils.readDfFromRds)."""
        read_method = None
        read_args = None
        first_node = chain[0][2] if chain else None

        for method_name, args_node, nd in chain:
            if method_name in _HELPER_READ_METHODS:
                read_method = method_name
                read_args = args_node
                first_node = nd
                break

        if read_method is None:
            return

        strs = self._arg_strings(read_args, resolve_vars=True) if read_args else []
        src_name = strs[1] if len(strs) > 1 else (strs[0] if strs else None)

        base = read_method.lower().replace("_", "")
        if "rds" in base or "redshift" in base:
            src_type = "jdbc"
            src_format = "jdbc"
        elif "snowflake" in base:
            src_type = "snowflake"
            src_format = "snowflake"
        else:
            src_type = "other"
            src_format = "?"

        src_id = self._next_id("in")
        loc = self._loc(first_node)
        if self._current_function != "__main__":
            loc.scope = self._current_function

        ds = DataSource(
            id=src_id,
            type=src_type,
            format=src_format,
            name=src_name,
            location=loc,
            is_test_file=self._is_test_file(),
        )
        self._data_in.append(ds)
        if assign_var:
            self._variables[assign_var] = src_id

    def _extract_helper_sink(self, chain: list[tuple[str, Node | None, Node]]) -> None:
        """Detect helper function calls that write data (e.g. rdsUtils.writeTable, myS3Utils.dataUpdateIntoS3)."""
        sink_method = None
        sink_args = None
        first_node = chain[0][2] if chain else None

        for method_name, args_node, nd in chain:
            if method_name in _HELPER_SINK_METHODS:
                sink_method = method_name
                sink_args = args_node
                first_node = nd
                break

        if sink_method is None:
            return

        base = sink_method.lower().replace("_", "")
        if "writedataframe" in base or "overwritedataframe" in base:
            strs = self._arg_strings(sink_args, resolve_vars=True) if sink_args else []
            sink_name = strs[1] if len(strs) >= 2 else (strs[0] if strs else None)
        else:
            sink_name = self._first_arg_string(sink_args, resolve_vars=True) if sink_args else None

        if "snowflake" in base:
            sink_type = "snowflake"
            sink_format = "snowflake"
        elif "execute" in base or "query" in base:
            sink_type = "jdbc"
            sink_format = "jdbc"
        elif "s3" in base:
            sink_type = "csv"
            sink_format = "csv"
        else:
            sink_type = "jdbc"
            sink_format = "jdbc"

        sink_id = self._next_id("out")
        loc = self._loc(first_node)
        if self._current_function != "__main__":
            loc.scope = self._current_function

        if sink_name:
            sink_name = self._resolve_sql_table_name(sink_name)

        ds = DataSink(
            id=sink_id,
            type=sink_type,
            format=sink_format,
            name=sink_name,
            mode="overwrite",
            location=loc,
            is_test_file=self._is_test_file(),
        )
        self._data_out.append(ds)

        if sink_method in _HELPER_DUAL_METHODS:
            src_id = self._next_id("in")
            src_loc = self._loc(first_node)
            if self._current_function != "__main__":
                src_loc.scope = self._current_function
            src = DataSource(
                id=src_id,
                type="csv",
                format="csv",
                name=sink_name,
                location=src_loc,
                is_test_file=self._is_test_file(),
            )
            self._data_in.append(src)


    def _resolve_sql_table_name(self, name: str) -> str:
        """Extract target table from SQL using sqlglot AST, with variable resolution."""
        import re
        import sqlglot
        from sqlglot import exp

        stripped = name.strip()
        if not stripped:
            return name

        var_map: dict[str, str] = {}
        def _replace_var(m: re.Match) -> str:
            var_name = m.group(1)
            placeholder = f"__var_{var_name}__"
            var_map[placeholder] = var_name
            return placeholder

        clean = re.sub(r'\$\$?\{(\w+)\}', _replace_var, stripped)

        try:
            parsed = sqlglot.parse_one(clean, error_level=sqlglot.ErrorLevel.IGNORE)
            tbl = parsed.find(exp.Table)
            if tbl and tbl.name:
                table_name = tbl.name
                if table_name in var_map:
                    var_name = var_map[table_name]
                    resolved = self._get_string_var(var_name)
                    return resolved if resolved else f"runtime:{var_name}"
                return table_name
        except Exception:
            pass
        self._record_regex_fallback(
            "SQL_TABLE_RESOLVE", name,
            {"fallback_name": name},
            failure_reason="sqlglot could not extract table from SQL",
        )
        return name


    def _extract_filter_constraints(
        self, args_node: Node | None, tx_id: str, loc: SourceLocation
    ) -> None:
        """Extract column constraints from .filter()/.where() arguments."""
        if args_node is None:
            return
        for c in args_node.named_children:
            self._walk_for_constraints(c, tx_id, loc)

    def _walk_for_constraints(
        self, node: Node, tx_id: str, loc: SourceLocation
    ) -> None:
        """Recursively walk a predicate expression to extract constraints."""
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            if fn and fn.type == "field_expression":
                method_node = fn.child_by_field_name("name")
                if method_node is None:
                    for ch in fn.named_children:
                        if ch.type == "identifier" and ch != fn.named_children[0]:
                            method_node = ch
                            break
                method_name = self._text(method_node) if method_node else ""
                obj = fn.named_children[0] if fn.named_children else None
                col_name = self._extract_col_name_from_node(obj) if obj else None

                if col_name and method_name == "isNotNull":
                    self._column_constraints.append(ColumnConstraint(
                        column_name=col_name, constraint_type=ConstraintType.NOT_NULL,
                        value_type="boolean", source_transformation=tx_id, location=loc,
                    ))
                    return
                elif col_name and method_name == "isNull":
                    self._column_constraints.append(ColumnConstraint(
                        column_name=col_name, constraint_type=ConstraintType.IS_NULL,
                        value_type="boolean", source_transformation=tx_id, location=loc,
                    ))
                    return
                elif col_name and method_name == "isin":
                    vals = self._arg_strings(args, resolve_vars=True) if args else []
                    self._column_constraints.append(ColumnConstraint(
                        column_name=col_name, constraint_type=ConstraintType.IN_LIST,
                        value=vals, value_type="list", source_transformation=tx_id, location=loc,
                    ))
                    return
                elif col_name and method_name == "like":
                    pat = self._first_arg_string(args) if args else None
                    self._column_constraints.append(ColumnConstraint(
                        column_name=col_name, constraint_type=ConstraintType.LIKE,
                        value=pat, value_type="string", source_transformation=tx_id, location=loc,
                    ))
                    return
                elif col_name and method_name == "rlike":
                    pat = self._first_arg_string(args) if args else None
                    self._column_constraints.append(ColumnConstraint(
                        column_name=col_name, constraint_type=ConstraintType.RLIKE,
                        value=pat, value_type="string", source_transformation=tx_id, location=loc,
                    ))
                    return
                elif col_name and method_name == "between":
                    vals = self._arg_strings(args, resolve_vars=True) if args else []
                    self._column_constraints.append(ColumnConstraint(
                        column_name=col_name, constraint_type=ConstraintType.BETWEEN,
                        value=vals, value_type="range", source_transformation=tx_id, location=loc,
                    ))
                    return

        if node.type == "infix_expression":
            non_op_children = [c for c in node.named_children if c.type != "operator_identifier"]
            op_node = next((c for c in node.named_children if c.type == "operator_identifier"), None)
            if op_node and len(non_op_children) >= 2:
                op = self._text(op_node)
                left = non_op_children[0]
                right = non_op_children[1]
                col_name = self._extract_col_name_from_node(left)
                if col_name and right:
                    val = self._text(right).strip('"').strip("'")
                    vtype = "string" if right.type in ("string", "interpolated_string_expression") else "integer" if right.type == "integer_literal" else "unknown"
                    ct_map = {"===": ConstraintType.EQUALS, "==": ConstraintType.EQUALS,
                              "=!=": ConstraintType.NOT_EQUALS, "!=": ConstraintType.NOT_EQUALS,
                              ">": ConstraintType.GREATER_THAN, "<": ConstraintType.LESS_THAN,
                              ">=": ConstraintType.GREATER_EQ, "<=": ConstraintType.LESS_EQ}
                    ct = ct_map.get(op)
                    if ct:
                        self._column_constraints.append(ColumnConstraint(
                            column_name=col_name, constraint_type=ct,
                            value=val, value_type=vtype, source_transformation=tx_id, location=loc,
                        ))
                        return

        for child in node.named_children:
            self._walk_for_constraints(child, tx_id, loc)

    def _extract_col_name_from_node(self, node: Node) -> str | None:
        """Extract column name from col("x"), $"x", or string literal."""
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            if fn:
                fn_name = self._text(fn)
                if fn_name in ("col", "column"):
                    return self._first_arg_string(args) if args else None
        if node.type == "string":
            return self._string_value(node)
        if node.type == "interpolated_string_expression":
            txt = self._text(node)
            if txt.startswith('$"') and txt.endswith('"'):
                return txt[2:-1]
        return None


    def _extract_control_node_if(self, node: Node) -> None:
        """Extract if/else control nodes."""
        ctrl_id = self._next_id("ctrl")
        condition_node = node.child_by_field_name("condition")
        condition_text = self._text(condition_node).strip("()") if condition_node else "?"

        branches: list[ControlBranch] = []
        body = node.child_by_field_name("consequence")
        if body:
            tx_ids = self._collect_tx_ids_in_subtree(body)
            branches.append(ControlBranch(
                label="true", condition=condition_text,
                steps=tx_ids, produces_dataframe=bool(tx_ids),
            ))

        alt = node.child_by_field_name("alternative")
        if alt:
            tx_ids = self._collect_tx_ids_in_subtree(alt)
            branches.append(ControlBranch(
                label="false", steps=tx_ids, produces_dataframe=bool(tx_ids),
            ))
        else:
            branches.append(ControlBranch(
                label="false", steps=[], produces_dataframe=False,
            ))

        loc = self._loc(node)
        if self._current_function != "__main__":
            loc.scope = self._current_function

        has_df_steps = any(b.steps for b in branches)
        if has_df_steps:
            self._control_nodes.append(ControlNode(
                node_id=ctrl_id, control_type=ControlType.BRANCH,
                logic=ControlLogic(expression=condition_text, engine="SCALA_AST"),
                branches=branches, exit_strategy=ExitStrategy.MERGE,
                source_location=loc, affects_dataframe=True,
            ))

    def _extract_control_node_match(self, node: Node) -> None:
        """Extract match/case control nodes."""
        ctrl_id = self._next_id("ctrl")
        expr_node = node.named_children[0] if node.named_children else None
        expr_text = self._text(expr_node) if expr_node else "?"

        branches: list[ControlBranch] = []
        for c in node.named_children:
            if c.type == "case_block":
                for clause in c.named_children:
                    if clause.type == "case_clause":
                        pattern = clause.child_by_field_name("pattern")
                        label = self._text(pattern) if pattern else "case"
                        body = clause.child_by_field_name("body")
                        tx_ids = self._collect_tx_ids_in_subtree(body) if body else []
                        branches.append(ControlBranch(
                            label=label, condition=label,
                            steps=tx_ids, produces_dataframe=bool(tx_ids),
                        ))

        loc = self._loc(node)
        if self._current_function != "__main__":
            loc.scope = self._current_function

        has_df_steps = any(b.steps for b in branches)
        if has_df_steps:
            self._control_nodes.append(ControlNode(
                node_id=ctrl_id, control_type=ControlType.BRANCH,
                logic=ControlLogic(expression=expr_text, engine="SCALA_AST"),
                branches=branches, exit_strategy=ExitStrategy.MERGE,
                source_location=loc, affects_dataframe=True,
            ))

    def _collect_tx_ids_in_subtree(self, node: Node) -> list[str]:
        """Collect transformation IDs whose location falls within node's line range."""
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        result = []
        for tx in self._transformations:
            if tx.location and tx.location.span:
                tx_line = tx.location.start_line
                if start_line <= tx_line <= end_line:
                    result.append(tx.id)
        return result

    def _extract_control_node_for(self, node: Node) -> None:
        """Extract for comprehension/loop as LOOP control node."""
        body = node.child_by_field_name("body")
        if not body:
            return

        tx_ids = self._collect_tx_ids_in_subtree(body)
        if not tx_ids:
            return

        enums = node.child_by_field_name("enumerators")
        iter_text = self._text(enums) if enums else "?"

        loc = self._loc(node)
        if self._current_function != "__main__":
            loc.scope = self._current_function

        self._control_nodes.append(ControlNode(
            node_id=self._next_id("ctrl"), control_type=ControlType.LOOP,
            logic=ControlLogic(expression=iter_text, engine="SCALA_AST"),
            branches=[ControlBranch(
                label="body", steps=tx_ids, produces_dataframe=bool(tx_ids),
            )],
            exit_strategy=ExitStrategy.MERGE,
            source_location=loc, affects_dataframe=True,
        ))

    def _extract_control_node_while(self, node: Node) -> None:
        """Extract while loop as LOOP control node."""
        body = node.child_by_field_name("body")
        if not body:
            return

        tx_ids = self._collect_tx_ids_in_subtree(body)
        if not tx_ids:
            return

        cond = node.child_by_field_name("condition")
        cond_text = self._text(cond).strip("()") if cond else "?"

        loc = self._loc(node)
        if self._current_function != "__main__":
            loc.scope = self._current_function

        self._control_nodes.append(ControlNode(
            node_id=self._next_id("ctrl"), control_type=ControlType.LOOP,
            logic=ControlLogic(expression=cond_text, engine="SCALA_AST"),
            branches=[ControlBranch(
                label="body", steps=tx_ids, produces_dataframe=bool(tx_ids),
            )],
            exit_strategy=ExitStrategy.MERGE,
            source_location=loc, affects_dataframe=True,
        ))

    def _extract_control_node_try(self, node: Node) -> None:
        """Extract try/catch/finally as PROTECTED control node."""
        body = node.child_by_field_name("body")
        branches: list[ControlBranch] = []

        if body:
            tx_ids = self._collect_tx_ids_in_subtree(body)
            branches.append(ControlBranch(
                label="try_block", steps=tx_ids, produces_dataframe=bool(tx_ids),
            ))

        for child in node.children:
            if child.type == "catch_clause":
                for sub in child.named_children:
                    if sub.type == "case_block":
                        for clause in sub.named_children:
                            if clause.type == "case_clause":
                                pattern = clause.child_by_field_name("pattern")
                                label = self._text(pattern) if pattern else "catch"
                                clause_body = clause.child_by_field_name("body")
                                tx_ids = self._collect_tx_ids_in_subtree(clause_body) if clause_body else []
                                branches.append(ControlBranch(
                                    label=f"catch_{label}", condition=label,
                                    steps=tx_ids, produces_dataframe=bool(tx_ids),
                                ))

            if child.type == "finally_clause":
                for sub in child.children:
                    if sub.type == "block":
                        tx_ids = self._collect_tx_ids_in_subtree(sub)
                        branches.append(ControlBranch(
                            label="finally_block", steps=tx_ids,
                            produces_dataframe=bool(tx_ids),
                        ))

        has_df_steps = any(b.steps for b in branches)
        if has_df_steps:
            loc = self._loc(node)
            if self._current_function != "__main__":
                loc.scope = self._current_function
            self._control_nodes.append(ControlNode(
                node_id=self._next_id("ctrl"), control_type=ControlType.PROTECTED,
                logic=ControlLogic(expression="try", engine="SCALA_AST"),
                branches=branches, exit_strategy=ExitStrategy.MERGE,
                source_location=loc, affects_dataframe=True,
            ))


    def _extract_window_spec(self, var_name: str, chain: list[tuple[str, Node | None, Node]]) -> bool:
        """Detect Window.partitionBy(...).orderBy(...) and record as WindowSpecDefinition."""
        method_names = [m for m, _, _ in chain]
        if "Window" not in method_names and "partitionBy" not in method_names:
            return False

        has_window_root = False
        for m, _, nd in chain:
            if m == "Window":
                has_window_root = True
                break

        if not has_window_root:
            return False

        parts: list[str] = []
        for m, args, nd in chain:
            if m in ("partitionBy", "orderBy", "rowsBetween", "rangeBetween"):
                arg_strs = self._arg_strings(args, resolve_vars=True) if args else []
                col_refs: list[str] = []
                if args:
                    for c in args.named_children:
                        if c.type == "call_expression":
                            fn = c.child_by_field_name("function")
                            if fn:
                                fn_name = self._text(fn).split(".")[-1]
                                inner_args = c.child_by_field_name("arguments")
                                if fn_name in ("col", "asc", "desc"):
                                    a = self._first_arg_string(inner_args) if inner_args else None
                                    suffix = ".desc()" if fn_name == "desc" else ""
                                    col_refs.append(f"{a}{suffix}" if a else self._text(c))
                                else:
                                    col_refs.append(self._text(c))
                        elif c.type == "field_expression":
                            txt = self._text(c)
                            if txt.endswith(".desc"):
                                col_refs.append(txt.replace(".desc", ".desc()"))
                            elif txt.endswith(".asc"):
                                col_refs.append(txt.replace(".asc", ""))
                            else:
                                col_refs.append(txt)
                all_cols = arg_strs + col_refs
                if all_cols:
                    cols_str = ", ".join(f"\'{c}\'" if not c.startswith("col(") and "." not in c and "(" not in c else c for c in all_cols)
                    parts.append(f"Window.{m}({cols_str})")

        if not parts:
            return False

        parts.reverse()
        expr = parts[0]
        for p in parts[1:]:
            expr += "." + p.replace("Window.", "")
        scope = self._current_function if self._current_function != "__main__" else "<global>"
        self._window_specs.append(WindowSpecDefinition(
            scope=scope,
            variable_name=var_name,
            pyspark_expr=expr,
        ))
        return True

    # ------------------------------------------------------------------
    # Transformation extraction
    # ------------------------------------------------------------------

    def _resolve_inline_chain(self, expr_node: Node) -> str | None:
        """Resolve an inline expression (e.g. df.select(...).filter(...)) to a node ID.

        If the expression is a simple identifier, look it up in _variables.
        If it's a call/field chain, extract its transformations and return the last ID.
        """
        if expr_node.type == "identifier":
            return self._variables.get(self._text(expr_node))

        if expr_node.type in ("call_expression", "field_expression"):
            inner_chain = self._unwind_chain(expr_node)
            if not inner_chain:
                return None
            inner_methods = {name for name, _, _ in inner_chain}
            if inner_methods & TRANSFORM_OPS:
                source_var = inner_chain[-1][0] if inner_chain else None
                txs = self._extract_transformations(inner_chain, None, source_var)
                if txs:
                    return txs[-1].id
            ident = inner_chain[-1][0] if inner_chain else None
            if ident:
                return self._variables.get(ident)
        return None

    def _extract_transformations(
        self,
        chain: list[tuple[str, Node | None, Node]],
        assign_var: str | None,
        source_var: str | None,
        inline_source_id: str | None = None,
    ) -> list[TransformationNode]:
        """Extract DataFrame transformations from a method chain.

        ``inline_source_id`` is set when the chain starts with an inline
        ``spark.read.*`` expression (no intermediate variable assignment).
        In that case the ``data_in`` node created by ``_extract_data_source``
        is passed directly as the first ``prev_id`` so that the transformation
        chain is connected to the read rather than left with ``inputs=[]``.
        """
        txs: list[TransformationNode] = []
        prev_id = inline_source_id or (self._variables.get(source_var) if source_var else None)

        # Maps alias-name → source-id for DataFrames aliased within this chain.
        # Built incrementally as we encounter alias("name") calls.  Used later
        # to resolve col("alias.*") wildcard selects.
        _alias_map: dict[str, str] = {}

        pending_groupby: dict[str, Any] | None = None
        for method_name, args_node, call_node in reversed(chain):
            if method_name not in TRANSFORM_OPS:
                continue
            if method_name in self._COLLECTION_ONLY_OPS:
                continue

            if method_name == "groupBy":
                pending_groupby = {
                    "cols": self._arg_strings(args_node) or (
                        self._extract_col_expressions(args_node) if args_node else []
                    ),
                    "args_node": args_node,
                    "call_node": call_node,
                }
                continue

            if method_name == "agg" and pending_groupby is not None:
                method_name = "groupBy_agg"
                call_node = pending_groupby["call_node"]
                pending_groupby = None

            tx_id = self._next_id("tx")
            inputs = [prev_id] if prev_id else []

            params: dict[str, Any] = {}
            logic = self._text(call_node)
            inferred_input: list[InputColumn] = []
            inferred_output: list[InferredColumn] = []

            if method_name in ("select", "drop"):
                cols = self._arg_strings(args_node) or self._arg_identifiers(args_node)
                if not cols and args_node:
                    cols = self._extract_col_expressions(args_node)
                if not cols and args_node:
                    cols = self._resolve_spread_seq(args_node)
                if not cols and args_node:
                    cols = self._resolve_prepend_wildcard_select(args_node, _alias_map)
                params["columns"] = cols
                if method_name == "select":
                    for c in cols:
                        inferred_output.append(
                            InferredColumn(name=c, inferred_type="UNKNOWN")
                        )

            elif method_name in ("filter", "where"):
                params["condition"] = self._text(args_node) if args_node else ""
                filter_loc = self._loc(call_node)
                if self._current_function != "__main__":
                    filter_loc.scope = self._current_function
                self._extract_filter_constraints(args_node, tx_id, filter_loc)

            elif method_name == "join":
                if args_node:
                    first_arg = next(
                        (c for c in args_node.named_children
                         if c.type not in ("comment",)),
                        None,
                    )
                    right_id = None
                    if first_arg:
                        right_id = self._resolve_inline_chain(first_arg)
                    if right_id:
                        inputs.append(right_id)
                    strs = self._arg_strings(args_node)
                    join_cols = self._extract_seq_args(args_node)
                    if join_cols:
                        params["join_condition"] = join_cols
                    if strs:
                        params["join_type"] = strs[-1]
                    join_type = strs[-1] if strs else "inner"
                    left_source = inputs[0] if inputs else "unknown"
                    right_source = right_id if right_id else "unknown"
                    for jc in (join_cols or []):
                        self._column_relationships.append(ColumnRelationship(
                            left_column=jc,
                            left_source=left_source,
                            right_column=jc,
                            right_source=right_source,
                            relationship_type=RelationshipType.JOIN_KEY,
                            join_type=join_type,
                            source_transformation=tx_id,
                        ))

            elif method_name in ("groupBy",):
                cols = self._arg_strings(args_node)
                if not cols and args_node:
                    cols = self._extract_col_expressions(args_node)
                params["group_columns"] = cols
                params["columns"] = cols

            elif method_name == "agg":
                params["expressions"] = self._text(args_node) if args_node else ""
                if args_node:
                    agg_cols = self._extract_col_expressions(args_node)
                    params["columns"] = agg_cols
                    for c in agg_cols:
                        inferred_output.append(
                            InferredColumn(name=c, inferred_type="UNKNOWN")
                        )

            elif method_name == "withColumn":
                strs = self._arg_strings(args_node)
                if strs:
                    params["column_name"] = strs[0]
                    params["columns"] = [strs[0]]
                    inferred_output.append(
                        InferredColumn(name=strs[0], inferred_type="UNKNOWN")
                    )
                params["expression"] = self._text(args_node) if args_node else ""

            elif method_name == "withColumnRenamed":
                strs = self._arg_strings(args_node)
                if len(strs) >= 2:
                    params["existing"] = strs[0]
                    params["new"] = strs[1]
                    params["columns"] = [strs[1]]
                    inferred_output.append(
                        InferredColumn(name=strs[1], inferred_type="UNKNOWN")
                    )

            elif method_name == "toDF":
                cols = self._arg_strings(args_node)
                if not cols and args_node:
                    cols = self._extract_col_expressions(args_node)
                params["columns"] = cols
                for c in cols:
                    inferred_output.append(
                        InferredColumn(name=c, inferred_type="UNKNOWN")
                    )

            elif method_name in ("orderBy", "sort"):
                cols = self._arg_strings(args_node)
                params["columns"] = cols

            elif method_name in ("union", "unionAll", "unionByName"):
                if args_node:
                    first_arg = next(
                        (c for c in args_node.named_children
                         if c.type not in ("comment",)),
                        None,
                    )
                    if first_arg:
                        other_id = self._resolve_inline_chain(first_arg)
                        if other_id:
                            inputs.append(other_id)

            elif method_name == "limit":
                params["n"] = self._text(args_node) if args_node else ""

            elif method_name in ("alias", "as", "toDF"):
                strs = self._arg_strings(args_node)
                if strs:
                    params["alias"] = strs[0]
                    # Track alias → source mapping for later wildcard resolution.
                    # inputs[0] is the DataSource/tx being aliased at this point.
                    if method_name in ("alias", "as") and inputs:
                        _alias_map[strs[0]] = inputs[0]

            # GAP 4: extract column references via CST traversal
            col_inputs, col_outputs = self._extract_col_refs_from_node(call_node)
            from_inputs = inputs if inputs else ["unknown"]
            for col_name in col_inputs:
                if not any(ic.name == col_name for ic in inferred_input):
                    inferred_input.append(InputColumn(
                        name=col_name, inferred_type="UNKNOWN", from_inputs=from_inputs
                    ))
            for col_name in col_outputs:
                if not any(io.name == col_name for io in inferred_output):
                    inferred_output.append(
                        InferredColumn(name=col_name, inferred_type="UNKNOWN")
                    )

            loc = self._loc(call_node)
            if self._current_function != "__main__":
                loc.scope = self._current_function
            tx = TransformationNode(
                id=tx_id,
                operation=method_name,
                inputs=inputs,
                logic=logic[:200] if logic else None,
                parameters=params,
                inferred_input=inferred_input,
                inferred_output=inferred_output,
                location=loc,
            )
            self._transformations.append(tx)
            txs.append(tx)
            prev_id = tx_id

        if pending_groupby is not None:
            tx_id = self._next_id("tx")
            inputs_gb = [prev_id] if prev_id else []
            cols = pending_groupby["cols"]
            loc_gb = self._loc(pending_groupby["call_node"])
            if self._current_function != "__main__":
                loc_gb.scope = self._current_function
            tx_gb = TransformationNode(
                id=tx_id, operation="groupBy", inputs=inputs_gb,
                logic=self._text(pending_groupby["call_node"])[:200],
                parameters={"group_columns": cols, "columns": cols},
                location=loc_gb,
            )
            self._transformations.append(tx_gb)
            txs.append(tx_gb)
            prev_id = tx_id

        if assign_var and prev_id:
            self._variables[assign_var] = prev_id

        return txs

    def _extract_memory_source(
        self,
        chain: list[tuple[str, Node | None, Node]],
        assign_var: str | None,
    ) -> DataSource | None:
        """Detect spark.createDataFrame(data, schema) as memory-type data input."""
        first_node = chain[-1][2] if chain else chain[0][2]
        src_id = self._next_id("in")
        loc = self._loc(first_node)
        if self._current_function != "__main__":
            loc.scope = self._current_function

        inferred_cols = self._resolve_create_dataframe_schema(chain)

        ds = DataSource(
            id=src_id,
            type="memory",
            format=None,
            name=assign_var,
            path=None,
            query=None,
            location=loc,
            is_test_file=self._is_test_file(),
            inferred_columns=inferred_cols,
        )
        self._data_in.append(ds)
        if assign_var:
            self._variables[assign_var] = src_id
        return ds

    def _resolve_create_dataframe_schema(
        self, chain: list[tuple[str, Node | None, Node]]
    ) -> list["InferredColumn"]:
        """Extract inferred columns from the schema argument of createDataFrame.

        Handles:
          spark.createDataFrame(data, StructType(varName))    — variable reference
          spark.createDataFrame(data, StructType(List(...)))  — inline StructType
        Returns a list of InferredColumn (may be empty if schema cannot be resolved).
        """
        for method_name, args_node, _ in chain:
            if method_name != "createDataFrame" or args_node is None:
                continue
            positional = [
                c for c in args_node.named_children
                if c.type not in ("comment",)
            ]
            # Second positional arg is the schema (StructType(...))
            if len(positional) < 2:
                continue
            schema_arg = positional[1]
            fields = self._resolve_struct_type_node(schema_arg)
            return [
                InferredColumn(
                    name=col_name,
                    inferred_type=spark_type,
                    source=InferenceSource.SELECT,
                    confidence=InferenceConfidence.HIGH,
                )
                for col_name, spark_type in fields
            ]
        return []

    def _resolve_struct_type_node(self, node: "Node") -> list[tuple[str, str]]:
        """Resolve a StructType(...) node to a list of (col_name, type) pairs.

        Handles:
          StructType(varName)              — variable looked up in _struct_schema_vars
          StructType(List(StructField(...))) — inline definition
        """
        if node.type != "call_expression":
            return []
        fn = node.child_by_field_name("function")
        if fn is None or self._text(fn) != "StructType":
            return []
        args = node.child_by_field_name("arguments")
        if args is None:
            return []

        # Try variable reference: StructType(varName)
        for c in args.named_children:
            if c.type == "identifier":
                var_name = self._text(c)
                if var_name in self._struct_schema_vars:
                    return self._struct_schema_vars[var_name]

        # Try inline: StructType(List(StructField(...), ...))
        return self._extract_struct_fields_from_node(args)

    def _extract_col_expressions(self, args_node: Node) -> list[str]:
        """Extract column names from col("name"), $"name", col("x").as("y") patterns.

        Handles:
          col("name")              -> "name"
          $"name"                  -> "name"
          col("orig").as("alias")  -> "alias"
          expr.as("alias")         -> "alias"
          lit(...).as("alias")     -> "alias"
        """
        cols: list[str] = []
        for child in args_node.named_children:
            col_name = self._resolve_col_expr(child)
            if col_name:
                cols.append(col_name)
        return cols

    def _resolve_col_expr(self, node: Node) -> str | None:
        """Resolve a single column expression to its name."""
        text = self._text(node)

        # col("name").as("alias") or expr.alias("name")
        inner_chain = self._unwind_chain(node)
        if inner_chain:
            for method_name, args, _ in inner_chain:
                if method_name in ("as", "alias", "name"):
                    alias = self._first_arg_string(args)
                    if alias:
                        return alias

            for method_name, args, _ in inner_chain:
                if method_name == "col":
                    name = self._first_arg_string(args)
                    if name:
                        return name

        # col("name") direct call
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn and self._text(fn) == "col":
                args = node.child_by_field_name("arguments")
                return self._first_arg_string(args)

        # $"name" (string interpolation prefix)
        if node.type == "interpolated_string_expression":
            return self._string_value(node)

        return None

    def _extract_seq_args(self, args_node: Node | None) -> list[str]:
        """Extract column names from Seq("col1", "col2") patterns."""
        if args_node is None:
            return []
        for child in args_node.named_children:
            if child.type == "call_expression":
                fn = child.child_by_field_name("function")
                if fn and self._text(fn) == "Seq":
                    inner_args = child.child_by_field_name("arguments")
                    return self._arg_strings(inner_args)
        return []

    def _resolve_spread_seq(self, args_node: Node) -> list[str]:
        """Resolve seqVar.map(col): _* spread patterns to column names.

        Handles patterns like .select(OutputColumns.map(col): _*) where
        OutputColumns is a Seq of strings tracked in _seq_string_vars.
        Also handles (Seq("a", "b") ++ otherSeq).map(col): _*.
        """
        for child in args_node.named_children:
            if child.type != "ascription_expression":
                continue
            for sub in child.named_children:
                if sub.type != "call_expression":
                    continue
                fn = sub.child_by_field_name("function")
                if fn is None or fn.type != "field_expression":
                    continue
                method_node = fn.child_by_field_name("name")
                if method_node is None:
                    for c in fn.named_children:
                        if c.type == "identifier" and c != fn.named_children[0]:
                            method_node = c
                            break
                if method_node and self._text(method_node) in ("map", "flatMap"):
                    obj = fn.named_children[0] if fn.named_children else None
                    if obj and obj.type == "identifier":
                        var_name = self._text(obj)
                        if var_name in self._seq_string_vars:
                            return list(self._seq_string_vars[var_name])
        return []

    def _resolve_prepend_wildcard_select(
        self,
        args_node: Node,
        alias_map: dict[str, str],
    ) -> list[str]:
        """Resolve ``col("alias.*") +: colVar: _*`` patterns in select args.

        Handles the common Scala pattern where a join output selects all
        columns from one aliased input plus specific named columns from
        another::

            df.alias("t")
              .join(other.alias("c"), col("t.id") === col("c.id"), "left")
              .select(col("t.*") +: custCols: _*)

        Two sources of column names are resolved:

        1. ``col("X.*")`` wildcards — expand to the ``inferred_columns`` of
           the DataSource/Transformation that was aliased as ``X``.  Falls
           back to an empty list when that source has no inferred schema yet.
        2. ``varName: _*`` spreads — resolved from ``_seq_string_vars`` when
           the variable is a known ``Seq[String]`` / ``List[String]``.

        At minimum, resolving the spread variable eliminates the false-positive
        join ambiguity that ``DataIODetector`` assigns to those columns when
        ``parameters["columns"]`` is empty.
        """
        import re as _re

        text = self._text(args_node)

        # col("X.*")  or  col('X.*')
        wildcard_aliases: list[str] = _re.findall(
            r'col\s*\(\s*["\'](\w+)\.\*["\']\s*\)', text
        )
        # identifier: _*   (bare spread of a Seq variable, e.g. custCols: _*)
        spread_vars: list[str] = _re.findall(r'\b(\w+)\s*:\s*_\*', text)

        cols: list[str] = []

        # 1. Expand alias wildcards using inferred_columns of the aliased source.
        for alias in wildcard_aliases:
            source_id = alias_map.get(alias)
            if not source_id:
                continue
            ds = next((d for d in self._data_in if d.id == source_id), None)
            if ds and ds.inferred_columns:
                cols.extend(c.name for c in ds.inferred_columns)
                continue
            tx = next((t for t in self._transformations if t.id == source_id), None)
            if tx:
                for ic in (tx.inferred_output or []):
                    if ic.name and ic.name not in cols:
                        cols.append(ic.name)

        # 2. Resolve Seq variable spreads from the string-sequence registry.
        for var in spread_vars:
            if var in self._seq_string_vars:
                for name in self._seq_string_vars[var]:
                    if name not in cols:
                        cols.append(name)

        return cols

    # ------------------------------------------------------------------
    # Import extraction
    # ------------------------------------------------------------------

    def _extract_import(self, node: Node) -> None:
        parts = [self._text(c) for c in node.named_children if c.type == "identifier"]
        full_path = ".".join(parts)

        wildcard = any(c.type == "namespace_wildcard" for c in node.named_children)
        if wildcard:
            full_path += "._"

        self._imports[full_path] = ImportEntry(
            imported_names=[parts[-1]] if parts and not wildcard else ["*"],
            alias=None,
        )

    # ------------------------------------------------------------------
    # Function extraction
    # ------------------------------------------------------------------

    def _extract_function(
        self, node: Node, containing_object: str | None = None
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            for c in node.named_children:
                if c.type == "identifier":
                    name_node = c
                    break
        name = self._text(name_node) if name_node else "unknown"

        params_node = node.child_by_field_name("parameters")
        args: list[FunctionArgument] = []
        if params_node:
            for clause in params_node.named_children:
                param_nodes = (
                    clause.named_children if clause.type == "parameters" else [clause]
                )
                for p in param_nodes:
                    if p.type == "parameter":
                        p_name = ""
                        p_type = "Unknown"
                        for c in p.named_children:
                            if c.type == "identifier":
                                p_name = self._text(c)
                            elif c.type in (
                                "type_identifier",
                                "generic_type",
                                "compound_type",
                            ):
                                p_type = self._text(c)
                        args.append(FunctionArgument(name=p_name, inferred_type=p_type))

        func = FunctionDefinition(
            name=name,
            source_file=self._filepath,
            scope="global",
            containing_class=containing_object,
            arguments=args,
            location=self._loc(node),
        )
        self._functions.append(func)

        body = node.child_by_field_name("body")
        if body:
            prev_function = self._current_function
            self._current_function = name
            self._push_scope()  # isolate local val definitions from sibling functions
            tx_start = len(self._transformations)
            di_start = len(self._data_in)

            df_type_names = {"DataFrame", "Dataset", "Dataset[Row]"}
            param_names: list[str] = []
            for a in args:
                param_names.append(a.name)
                if a.inferred_type in df_type_names:
                    self._variables[a.name] = f"param_{name}_{a.name}"

            # Store body node + ordered param names for later argument inlining
            self._function_bodies[name] = (body, param_names)

            self._visit(body, containing_object=containing_object)

            tx_end = len(self._transformations)
            di_end = len(self._data_in)
            self._function_tx_ranges[name] = (tx_start, tx_end)
            self._function_di_ranges[name] = (di_start, di_end)

            if tx_end == tx_start and di_end == di_start:
                last_child = None
                for c in reversed(body.named_children):
                    if c.type not in ("comment",):
                        last_child = c
                        break
                if last_child and last_child.type == "call_expression":
                    chain = self._unwind_chain(last_child)
                    if chain:
                        tail_fn = chain[0][0]
                        if tail_fn and tail_fn[0].islower():
                            self._function_tail_calls[name] = tail_fn

            self._pop_scope()
            self._current_function = prev_function

    # ------------------------------------------------------------------
    # Execution call creation (GAP 2)
    # ------------------------------------------------------------------

    def _create_execution_call(
        self,
        func_name: str,
        args_node: Node | None,
        assign_var: str | None,
        target_node: str | None,
        call_node: Node,
        target_object: str | None = None,
    ) -> ExecutionCall:
        """Create an ExecutionCall for a function invocation."""
        input_bindings: list[InputBinding] = []
        literal_args: dict[str, str] = {}

        func_def = next(
            (f for f in self._functions if f.name == func_name), None
        )
        param_names = [a.name for a in func_def.arguments] if func_def else []

        if args_node:
            for i, arg_child in enumerate(args_node.named_children):
                param_name = param_names[i] if i < len(param_names) else f"arg_{i}"

                if arg_child.type == "assignment_expression":
                    for ac in arg_child.children:
                        if ac.type == "identifier" and ac == arg_child.children[0]:
                            param_name = self._text(ac)
                        elif ac.is_named and ac.type != "identifier":
                            arg_child = ac
                            break
                        elif ac.type == "identifier" and ac != arg_child.children[0]:
                            arg_child = ac
                            break

                arg_text = self._text(arg_child)

                source_id = self._variables.get(arg_text)
                if source_id:
                    src_type = (
                        "data_in"
                        if source_id.startswith("in_")
                        else "transformation"
                    )
                    input_bindings.append(InputBinding(
                        arg_name=param_name,
                        source_type=src_type,
                        source_id=source_id,
                    ))

                if arg_child.type in ("string", "interpolated_string_expression"):
                    literal_args[param_name] = self._string_value(arg_child)
                elif arg_child.type == "field_expression":
                    unwrapped = self._unwrap_strip_margin_field(arg_child)
                    if unwrapped is not None:
                        literal_args[param_name] = unwrapped
                    else:
                        obj_id = arg_child.named_children[0] if arg_child.named_children else None
                        if obj_id and obj_id.type == "identifier":
                            first_val = self._get_string_var(self._text(obj_id) + ".__first__")
                            if first_val is not None:
                                literal_args[param_name] = first_val
                elif arg_child.type == "call_expression":
                    unwrapped = self._unwrap_strip_margin(arg_child)
                    if unwrapped is not None:
                        literal_args[param_name] = unwrapped

                # Resolve identifier args via tracked string variables
                if arg_child.type == "identifier" and param_name not in literal_args:
                    var_val = self._get_string_var(arg_text)
                    if var_val is not None:
                        literal_args[param_name] = var_val

        output_binding = None
        if assign_var:
            output_binding = OutputBinding(
                variable_name=assign_var,
                target_node=target_node,
            )

        call_id = self._next_id("call")
        ec = ExecutionCall(
            call_id=call_id,
            caller=CallLocation(
                function=self._current_function,
                line=call_node.start_point[0] + 1,
                file=self._filepath,
            ),
            callee=CalleeRef(function=func_name, file=target_object),
            bindings=CallBindings(
                inputs=input_bindings,
                output=output_binding,
            ),
            literal_arguments=literal_args,
        )
        self._execution_calls.append(ec)
        return ec

    def _resolve_function_output(
        self, func_name: str, _seen: set[str] | None = None,
    ) -> str | None:
        """Find the last transformation or data_in ID produced inside a function body.

        If the function only delegates to another function call, resolve
        recursively through the call chain (with cycle detection).
        """
        tx_range = self._function_tx_ranges.get(func_name)
        if tx_range:
            start, end = tx_range
            if end > start:
                return self._transformations[end - 1].id

        di_range = self._function_di_ranges.get(func_name)
        if di_range:
            di_start, di_end = di_range
            if di_end > di_start:
                return self._data_in[di_end - 1].id

        if _seen is None:
            _seen = set()
        if func_name in _seen:
            return None
        _seen.add(func_name)

        tail_call = self._function_tail_calls.get(func_name)
        if tail_call:
            return self._resolve_function_output(tail_call, _seen)
        return None

    # ------------------------------------------------------------------
    # Main visitor
    # ------------------------------------------------------------------

    def _visit(self, node: Node, containing_object: str | None = None) -> None:
        if node.type == "import_declaration":
            self._extract_import(node)
            return

        if node.type == "function_definition":
            self._extract_function(node, containing_object)
            return

        if node.type == "object_definition":
            obj_name = None
            for c in node.named_children:
                if c.type == "identifier":
                    obj_name = self._text(c)
                    break
            body = None
            for c in node.named_children:
                if c.type == "template_body":
                    body = c
                    break
            if body:
                for child in body.named_children:
                    self._visit(child, containing_object=obj_name)
            return

        if node.type == "class_definition":
            cls_name = None
            for c in node.named_children:
                if c.type == "identifier":
                    cls_name = self._text(c)
                    break
            body = None
            for c in node.named_children:
                if c.type == "template_body":
                    body = c
                    break
            if body:
                for child in body.named_children:
                    self._visit(child, containing_object=cls_name)
            return

        if node.type in ("val_definition", "var_definition"):
            self._handle_val_var(node)
            return

        if node.type == "call_expression":
            inner_call = None
            body_block = None
            for c in node.named_children:
                if c.type == "call_expression":
                    inner_call = c
                elif c.type in ("block", "indented_block"):
                    body_block = c
            if inner_call is not None and body_block is not None:
                fn_node = inner_call.child_by_field_name("function")
                if fn_node and fn_node.type == "identifier" and self._text(fn_node) == "test":
                    args = inner_call.child_by_field_name("arguments")
                    test_name = self._first_arg_string(args) if args else None
                    if test_name:
                        safe_name = "test_" + test_name.replace(" ", "_").replace("-", "_")[:60]
                        func = FunctionDefinition(
                            name=safe_name,
                            source_file=self._filepath,
                            scope="global",
                            containing_class=containing_object,
                            arguments=[],
                            location=self._loc(node),
                        )
                        self._functions.append(func)
                        prev_function = self._current_function
                        self._current_function = safe_name
                        self._push_scope()
                        tx_start = len(self._transformations)
                        di_start = len(self._data_in)
                        for child in body_block.named_children:
                            self._visit(child, containing_object=containing_object)
                        tx_end = len(self._transformations)
                        di_end = len(self._data_in)
                        self._function_tx_ranges[safe_name] = (tx_start, tx_end)
                        self._function_di_ranges[safe_name] = (di_start, di_end)
                        self._pop_scope()
                        self._current_function = prev_function
                        return

        if node.type in ("call_expression", "field_expression"):
            chain = self._unwind_chain(node)
            method_names = {name for name, _, _ in chain}

            if method_names & SPARK_WRITE_ENTRY:
                source_var = chain[-1][0] if chain else None
                self._extract_data_sink(chain, source_var)
            elif method_names & _HELPER_SINK_METHODS:
                self._extract_helper_sink(chain)
            elif method_names & _HELPER_READ_METHODS:
                self._extract_helper_source(chain, None)
            elif "createDataFrame" in method_names:
                self._extract_memory_source(chain, None)
            elif method_names & (TRANSFORM_OPS | SPARK_READ_ENTRY | READ_FORMATS):
                source_var = chain[-1][0] if chain else None
                inline_src_id: str | None = None
                if method_names & (SPARK_READ_ENTRY | READ_FORMATS):
                    ds = self._extract_data_source(chain, None)
                    inline_src_id = ds.id if ds else None
                if method_names & TRANSFORM_OPS:
                    # Skip if chain root is a Column-producing function called with args
                    # (not a bare DataFrame variable that happens to share a function name)
                    if not (chain and chain[-1][1] is not None
                            and chain[-1][0] in self._COLUMN_EXPR_ROOTS):
                        self._extract_transformations(chain, None, source_var,
                                                      inline_source_id=inline_src_id)
            else:
                self._try_extract_call(chain)
                if chain:
                    _, first_args, _ = chain[0]
                    self._visit_nested_call_args(first_args, containing_object)

            if method_names & self._HOF_METHODS:
                self._visit_foreach_body(node, chain, containing_object)
            return

        if node.type == "for_expression":
            self._visit_for_expression(node, containing_object)
            return

        for child in node.named_children:
            self._visit(child, containing_object)

        if node.type == "if_expression":
            self._extract_control_node_if(node)

        if node.type == "match_expression":
            self._extract_control_node_match(node)

        if node.type == "while_expression":
            self._extract_control_node_while(node)

        if node.type == "try_expression":
            self._extract_control_node_try(node)

    def _handle_val_var(self, node: Node) -> None:
        var_name = None
        for c in node.named_children:
            if c.type == "identifier":
                var_name = self._text(c)
                break

        expr_node = None
        eq_seen = False
        for c in node.children:
            if self._text(c) == "=":
                eq_seen = True
            elif eq_seen and c.is_named:
                expr_node = c
                break

        if expr_node is None or var_name is None:
            return

        # Unwrap indented_block (multiline assignments after newline)
        if expr_node.type == "indented_block" and expr_node.named_children:
            expr_node = expr_node.named_children[0]

        # Track simple string variable assignments (val tableName = "...")
        if expr_node.type in ("string", "interpolated_string_expression"):
            self._set_string_var(var_name, self._string_value(expr_node))
        elif expr_node.type == "field_expression":
            unwrapped = self._unwrap_strip_margin_field(expr_node)
            if unwrapped is not None:
                self._set_string_var(var_name, unwrapped)
        elif expr_node.type == "call_expression":
            unwrapped = self._unwrap_strip_margin(expr_node)
            if unwrapped is not None:
                self._set_string_var(var_name, unwrapped)

        if var_name and expr_node and expr_node.type == "call_expression":
            fn_node = expr_node.child_by_field_name("function")
            fn_name = None
            if fn_node and fn_node.type == "identifier":
                fn_name = self._text(fn_node)
            elif fn_node and fn_node.type == "generic_function":
                for gc in fn_node.named_children:
                    if gc.type == "identifier":
                        fn_name = self._text(gc)
                        break
            if fn_name and fn_name in ("Seq", "List", "Array"):
                args = expr_node.child_by_field_name("arguments")
                if args:
                    strings: list[str] = []
                    struct_fields: list[tuple[str, str]] = []
                    for c in args.named_children:
                        if c.type == "call_expression":
                            inner_fn = c.child_by_field_name("function")
                            inner_fn_name = (
                                self._text(inner_fn) if inner_fn else ""
                            )
                            inner_args = c.child_by_field_name("arguments")
                            if inner_args:
                                first = self._first_arg_string(inner_args)
                                if first:
                                    if inner_fn_name == "StructField":
                                        spark_type = self._extract_struct_field_type(inner_args)
                                        struct_fields.append((first, spark_type))
                                    else:
                                        strings.append(first)
                        elif c.type == "string":
                            s = self._text(c).strip('"').strip("'")
                            if s:
                                strings.append(s)
                        elif c.type == "identifier":
                            resolved = self._get_string_var(self._text(c))
                            if resolved:
                                strings.append(resolved)
                    if struct_fields:
                        self._struct_schema_vars[var_name] = struct_fields
                    elif strings:
                        self._seq_string_vars[var_name] = strings
            elif fn_name == "StructType":
                # val schema = StructType(Seq(StructField(...), ...))
                # The StructField list is nested inside StructType's argument.
                args = expr_node.child_by_field_name("arguments")
                if args and var_name:
                    struct_fields = self._extract_struct_fields_from_node(args)
                    if struct_fields:
                        self._struct_schema_vars[var_name] = struct_fields

        if var_name and expr_node and expr_node.type in ("infix_expression", "postfix_expression"):
            merged = self._collect_concat_seqs(expr_node)
            if merged and expr_node.type == "postfix_expression":
                parent = node.parent
                if parent:
                    siblings = list(parent.named_children)
                    try:
                        idx = siblings.index(node)
                        if idx + 1 < len(siblings):
                            next_sib = siblings[idx + 1]
                            if next_sib.type in ("infix_expression", "identifier"):
                                merged.extend(self._collect_concat_seqs(next_sib))
                    except ValueError:
                        pass
            if merged:
                self._seq_string_vars[var_name] = merged

        self._process_df_binding(var_name, expr_node)

    def _process_df_binding(self, var_name: str | None, expr_node: Node) -> None:
        """Unwind the chain from *expr_node* and register *var_name* in _variables.

        Shared by ``_handle_val_var`` and ``_handle_for_enumerator`` so that both
        ``val x = expr`` and for-comprehension generator bindings (``x <- expr`` /
        ``x = expr``) produce the same ASG nodes.
        """
        # For-expressions (val x = for { ... } yield body) cannot be unwound as
        # a chain; delegate to the specialised visitor so the body is traversed.
        if expr_node.type == "for_expression":
            self._visit_for_expression(expr_node, None)
            return

        chain = self._unwind_chain(expr_node)

        if var_name and chain and var_name not in self._seq_string_vars:
            for method_name, args_node_c, _ in chain:
                if method_name in ("Seq", "List", "Array") and args_node_c:
                    strings = []
                    for c in args_node_c.named_children:
                        if c.type == "call_expression":
                            inner_args = c.child_by_field_name("arguments")
                            if inner_args:
                                first = self._first_arg_string(inner_args)
                                if first:
                                    strings.append(first)
                        elif c.type == "string":
                            s = self._text(c).strip('"').strip("'")
                            if s:
                                strings.append(s)
                        elif c.type == "identifier":
                            resolved = self._get_string_var(self._text(c))
                            if resolved:
                                strings.append(resolved)
                    if strings:
                        self._seq_string_vars[var_name] = strings
                    break

        if not chain:
            return

        method_names = {name for name, _, _ in chain}

        if var_name and ("Window" in method_names or "partitionBy" in method_names):
            if self._extract_window_spec(var_name, chain):
                return  # window spec, not a DataFrame chain

        if "createDataFrame" in method_names:
            mem_ds = self._extract_memory_source(chain, var_name)
            if method_names & TRANSFORM_OPS:
                source_var = chain[-1][0] if chain else None
                self._extract_transformations(chain, var_name, source_var,
                                              inline_source_id=mem_ds.id if mem_ds else None)
        elif method_names & (SPARK_READ_ENTRY | READ_FORMATS):
            ds = self._extract_data_source(chain, var_name)
            if method_names & TRANSFORM_OPS:
                source_var = chain[-1][0] if chain else None
                self._extract_transformations(chain, var_name, source_var,
                                              inline_source_id=ds.id if ds else None)
        elif method_names & SPARK_WRITE_ENTRY:
            source_var = chain[-1][0] if chain else None
            self._extract_data_sink(chain, source_var)
        elif method_names & _HELPER_READ_METHODS:
            di_before = len(self._data_in)
            self._extract_helper_source(chain, var_name)
            if method_names & TRANSFORM_OPS:
                source_var = chain[-1][0] if chain else None
                helper_src_id = self._data_in[-1].id if len(self._data_in) > di_before else None
                self._extract_transformations(chain, var_name, source_var,
                                              inline_source_id=helper_src_id)
        elif "sql" in method_names and self._extract_sql_source(chain, var_name):
            pass
        elif method_names & TRANSFORM_OPS:
            source_var = chain[-1][0] if chain else None
            if not (chain and chain[-1][1] is not None
                    and chain[-1][0] in self._COLUMN_EXPR_ROOTS):
                self._extract_transformations(chain, var_name, source_var)
        else:
            self._handle_function_call_assignment(chain, var_name)

        if method_names & self._HOF_METHODS:
            self._visit_foreach_body(None, chain, None)

    def _handle_for_enumerator(self, enumerator: Node) -> None:
        """Process a single for-comprehension enumerator.

        Handles generator bindings (``var <- expr``) and val bindings
        (``var = expr``).  Registers the bound variable in ``_variables``
        so subsequent enumerators and the body can reference it.
        """
        named = enumerator.named_children
        if len(named) < 2:
            return

        is_binding = any(c.type in ("<-", "=") for c in enumerator.children)
        if not is_binding:
            return

        first = named[0]
        if first.type != "identifier":
            return

        var_name = self._text(first)
        expr_node = named[-1]
        self._process_df_binding(var_name, expr_node)

    def _visit_for_expression(
        self, node: Node, containing_object: str | None
    ) -> None:
        """Visit a for_expression, processing enumerators in order.

        Enumerators are processed sequentially so that each generator binding
        (``dfu001 <- ...``) is registered in ``_variables`` before the next
        enumerator or the body references the bound variable.
        """
        enums_node: Node | None = None
        for child in node.named_children:
            if child.type == "enumerators":
                enums_node = child
                break

        if enums_node:
            for enum in enums_node.named_children:
                if enum.type == "enumerator":
                    self._handle_for_enumerator(enum)

        # Visit everything except enumerators (body, yield expression, etc.)
        for child in node.named_children:
            if child.type != "enumerators":
                self._visit(child, containing_object)

        self._extract_control_node_for(node)

    def _handle_function_call_assignment(
        self, chain: list[tuple[str, Node | None, Node]], var_name: str
    ) -> None:
        """Handle val x = someFunction(...) — track variable and create ExecutionCall."""
        func_name, args_node, call_node = chain[0]

        target_object = None
        if len(chain) >= 2 and chain[-1][1] is None:
            target_object = chain[-1][0]

        func_name_lookup = func_name
        target_id = self._resolve_function_output(func_name_lookup)
        self._variables[var_name] = target_id

        # Argument inlining: when the function is defined in this file AND the
        # resolved DataSource is generic (type=other, path=None because the body
        # used a parameter as the path), re-visit the function body with the actual
        # call-site string literals substituted into _string_vars.  This recovers
        # cases like:
        #   def readTable(spark, tableName) = spark.read.table(tableName)
        #   val dfSales = readTable(spark, "sales_data")  → creates table/sales_data
        inlined_source_id = self._try_inline_function_with_literal_args(
            func_name_lookup, args_node, target_id,
        )
        if inlined_source_id:
            target_id = inlined_source_id
            self._variables[var_name] = inlined_source_id

        nested_source = self._scan_args_for_nested_reads(args_node)
        if nested_source and not target_id:
            self._variables[var_name] = nested_source

        # Last-resort: when the function output cannot be resolved, propagate the
        # first identifier argument that maps to a known variable.  This preserves
        # lineage through opaque helper calls like unionDataframe(dfA, dfB).
        if not self._variables.get(var_name) and args_node:
            for arg in args_node.named_children:
                if arg.type == "identifier":
                    resolved = self._variables.get(self._text(arg))
                    if resolved:
                        self._variables[var_name] = resolved
                        break

        if args_node is not None:
            ec = self._create_execution_call(
                func_name_lookup, args_node, var_name, target_id, call_node,
                target_object=target_object,
            )
            if nested_source and ec.bindings:
                if not ec.bindings.inputs:
                    ec.bindings.inputs = []
                ec.bindings.inputs.append(InputBinding(
                    arg_name="df",
                    source_type="data_in",
                    source_id=nested_source,
                ))
            self._visit_nested_call_args(args_node)

    def _try_inline_function_with_literal_args(
        self,
        func_name: str,
        args_node: object,
        current_target_id: str | None,
    ) -> str | None:
        """Attempt argument inlining for user-defined wrapper functions.

        When a wrapper like ``def readTable(spark, tableName) = spark.read.table(tableName)``
        is called as ``readTable(spark, "sales_data")``, the generic first-pass
        produces DataSource(type=other, path=None) because ``tableName`` was an
        unresolved parameter.  This method re-visits the function body with the
        actual call-site string literals injected into _string_vars, producing a
        specialized DataSource with the correct path/type.

        Returns the ID of the newly created DataSource, or None if inlining was
        not applicable or produced no new sources.
        """
        if args_node is None:
            return None

        # Resolve function body: prefer file-local definition, fall back to
        # cross-file shared registry.  A cross-file body requires temporarily
        # swapping ``_source_bytes`` so that all ``_text()`` calls inside the
        # recursive visit decode correctly against the originating file.
        shared_entry: SharedFunctionEntry | None = None
        if func_name in self._function_bodies:
            body, param_names = self._function_bodies[func_name]
        elif func_name in self._shared_functions:
            shared_entry = self._shared_functions[func_name]
            body, param_names = shared_entry.body_node, shared_entry.params
        else:
            return None

        # Only inline when the resolved source is still generic (unresolved param)
        if current_target_id:
            resolved_src = next(
                (s for s in self._data_in if s.id == current_target_id), None
            )
            if resolved_src and not (resolved_src.type == "other" and resolved_src.path is None):
                return None  # Already specific — no need to inline

        # Build two substitution maps from call-site args:
        #   - literal_map: param → string literal value (for _string_vars)
        #   - df_map:      param_FUNC_PARAM → actual_node_id (for transformations)
        literal_map: dict[str, str] = {}
        df_map: dict[str, str] = {}
        call_args = list(args_node.named_children)
        for i, arg in enumerate(call_args):
            if i >= len(param_names):
                break
            param = param_names[i]
            param_key = f"param_{func_name}_{param}"
            if arg.type in ("string", "interpolated_string_expression"):
                literal_map[param] = self._string_value(arg)
            elif arg.type == "identifier":
                arg_text = self._text(arg)
                str_val = self._get_string_var(arg_text)
                if str_val:
                    literal_map[param] = str_val
                else:
                    node_id = self._variables.get(arg_text)
                    if node_id and (
                        node_id.startswith("in_") or node_id.startswith("tx_")
                    ):
                        df_map[param_key] = node_id

        if not literal_map and not df_map:
            return None  # No substitutable args

        # For cross-file bodies, save/restore _source_bytes so that all
        # _text() calls inside _collect_string_evidence decode correctly.
        # Strategy A (DataSource creation) is intentionally DISABLED for
        # cross-file bodies to avoid creating DataSources with unresolvable
        # paths (e.g. a dynamic SQL tableName parameter becoming "dt").
        # Cross-file evidence is instead applied via the global backward
        # propagation pass in directory_parser.py.
        saved_src_bytes = self._source_bytes if shared_entry else None
        if shared_entry:
            self._source_bytes = shared_entry.source_bytes

        try:
            # Strategy A (DataSource re-visit): when there are string literal
            # args, temporarily inject them into _string_vars and re-visit the
            # body.  Restricted to file-local functions only — cross-file bodies
            # (shared_entry is not None) never create DataSources here to prevent
            # spurious sources with unresolvable paths from polluting the ASG.
            if literal_map and not shared_entry:
                # Push a temporary scope frame with the inlined argument values
                # so that the re-visited body resolves them without leaking into
                # sibling or parent scopes.
                self._push_scope()
                for k, v in literal_map.items():
                    self._set_string_var(k, v)

                prev_fn = self._current_function
                self._current_function = func_name
                di_before = len(self._data_in)

                self._visit(body, None)

                self._current_function = prev_fn
                self._pop_scope()

                di_after = len(self._data_in)
                if di_after > di_before:
                    return self._data_in[-1].id

            # Strategy C (backward type propagation): runs whenever there are
            # resolved DataFrame arguments.  Scans the function body (and any
            # user-defined callees) for string-operation evidence (rlike, === "")
            # and upgrades the confidence of matching source columns.
            # Must run *before* the Strategy-B early return so it is always
            # executed when df_map is non-empty.
            # _source_bytes is already set correctly for cross-file bodies.
            if df_map:
                evidence_cols = self._collect_string_evidence(body)
                if evidence_cols:
                    self._apply_backward_string_evidence(evidence_cols, df_map)

            # Strategy B (transformation specialization): when there are DataFrame args,
            # clone the function's existing transformations with param_* IDs replaced by
            # the actual call-site node IDs.  Only applies to file-local functions
            # (cross-file transformations are handled by the directory parser merger).
            if df_map and not shared_entry:
                tx_start, tx_end = self._function_tx_ranges.get(func_name, (0, 0))
                if tx_end <= tx_start:
                    return None

                orig_txs = self._transformations[tx_start:tx_end]
                # Build old-id → new-id mapping for intra-function references
                id_remap: dict[str, str] = {}
                new_txs: list = []
                caller_ctx = self._current_function

                for orig_tx in orig_txs:
                    new_id = self._next_id("tx")
                    # Substitute param_* IDs in inputs — first apply df_map substitutions,
                    # then apply the intra-function id_remap for chained transformations.
                    new_inputs = []
                    for inp in orig_tx.inputs:
                        inp = df_map.get(inp, inp)
                        inp = id_remap.get(inp, inp)
                        new_inputs.append(inp)
                    id_remap[orig_tx.id] = new_id

                    cloned = TransformationNode(
                        id=new_id,
                        operation=orig_tx.operation,
                        inputs=new_inputs,
                        logic=orig_tx.logic,
                        parameters={
                            **orig_tx.parameters,
                            "_inlined_from": f"{caller_ctx}>{func_name}",
                        },
                        inferred_input=orig_tx.inferred_input,
                        inferred_output=orig_tx.inferred_output,
                        location=SourceLocation(
                            pathfile=orig_tx.location.pathfile if orig_tx.location else "",
                            scope=f"{caller_ctx}>{func_name}",
                            span=orig_tx.location.span if orig_tx.location else None,
                        ),
                    )
                    new_txs.append(cloned)
                    self._transformations.append(cloned)

                if new_txs:
                    return new_txs[-1].id

        finally:
            if saved_src_bytes is not None:
                self._source_bytes = saved_src_bytes

        return None

    # ------------------------------------------------------------------
    # Backward type propagation helpers
    # ------------------------------------------------------------------

    def _collect_string_evidence(
        self,
        body: object,
        visited: set[str] | None = None,
    ) -> set[str]:
        """Scan an AST subtree for high-confidence STRING-type signals.

        Returns the set of column names that appear in patterns proving the
        column holds character data in the source system:
          - ``col("X") rlike "..."``      — rlike requires string operand
          - ``col("X") === ""``            — equality with empty string

        Interprocedural: when the body contains a direct call to a function
        that is defined in the same file (present in ``_function_bodies``),
        that function's body is also scanned recursively.  A *visited* set
        prevents infinite recursion on mutually recursive functions.

        The analysis is conservative: it only returns column names from
        patterns that are unambiguously string operations.  Casts alone
        (e.g. ``col("X").cast("integer")``) are intentionally excluded
        because casting is also applied to numeric columns for type coercion.
        """
        if visited is None:
            visited = set()
        result: set[str] = set()
        if body is None:
            return result

        for node in self._iter_all_nodes(body):
            # Infix expression: col("X") rlike "..."  /  col("X") === ""
            #
            # In tree-sitter-scala, the infix operator is always the second
            # named child.  Symbolic operators (===, =!=) have type
            # "operator_identifier"; alphanumeric infix methods (rlike,
            # contains) have type "identifier".
            if node.type == "infix_expression":
                named = list(node.named_children)
                if len(named) < 2:
                    continue
                op_node = named[1]
                if op_node.type not in ("operator_identifier", "identifier"):
                    continue
                op = self._text(op_node)
                left = named[0]
                col_name = self._extract_col_name_from_node(left)
                if col_name:
                    if op == "rlike":
                        result.add(col_name)
                    elif op in ("===", "==") and len(named) >= 3:
                        right = named[-1]
                        if right.type in ("string", "string_literal"):
                            if self._string_value(right) == "":
                                result.add(col_name)

            # Interprocedural: follow direct calls to known user functions.
            # Also follows HOF patterns: df.transform(funcName).
            if node.type == "call_expression":
                fn_node = node.child_by_field_name("function")
                args_node = node.child_by_field_name("arguments")
                if fn_node:
                    fname = self._text(fn_node)
                    # Strip object prefix for qualified calls: "Obj.method" → "method"
                    short_fname = fname.split(".")[-1] if "." in fname else fname
                    # Direct call: funcName(args) — local then cross-file registry
                    if fname not in visited:
                        result |= self._follow_function_for_evidence(
                            fname, short_fname, visited
                        )
                    # HOF call: obj.transform(funcName) — funcName is an arg
                    if args_node:
                        for arg in args_node.named_children:
                            if arg.type == "identifier":
                                inner_name = self._text(arg)
                                if inner_name not in visited:
                                    result |= self._follow_function_for_evidence(
                                        inner_name, inner_name, visited
                                    )
        return result

    def _follow_function_for_evidence(
        self,
        fname: str,
        short_fname: str,
        visited: set[str],
    ) -> set[str]:
        """Follow a function call into its body to collect STRING evidence.

        Checks the file-local ``_function_bodies`` first, then falls back to
        the cross-file ``_shared_functions`` registry.  When using a cross-file
        body, temporarily swaps ``_source_bytes`` so that all ``_text()`` calls
        inside the recursive ``_collect_string_evidence`` decode correctly.

        Returns an empty set if the function is unknown in both registries.
        """
        if fname in self._function_bodies:
            visited.add(fname)
            inner_body, _ = self._function_bodies[fname]
            return self._collect_string_evidence(inner_body, visited)

        # Fall back to project-level shared registry (cross-file functions)
        for key in (fname, short_fname):
            if key in self._shared_functions and key not in visited:
                visited.add(key)
                entry = self._shared_functions[key]
                saved_bytes = self._source_bytes
                try:
                    self._source_bytes = entry.source_bytes
                    return self._collect_string_evidence(entry.body_node, visited)
                finally:
                    self._source_bytes = saved_bytes

        return set()

    def _extract_function_bodies_only(self, root: object) -> None:
        """Lightweight pre-pass: collect function definitions into ``_function_bodies``.

        Unlike the full ``_visit`` → ``_extract_function`` path, this does NOT
        visit function bodies for ASG extraction.  It only records the body
        AST node and ordered parameter names so that the resulting entries can
        be shared with other parsers via :class:`SharedFunctionEntry`.

        Recursively descends into class/object/template bodies so that methods
        defined inside ``object Foo { ... }`` are collected too.
        """
        df_type_names = {"DataFrame", "Dataset", "Dataset[Row]", "Try[DataFrame]"}
        for node in self._iter_all_nodes(root):
            if node.type not in ("function_definition", "val_definition"):
                continue
            if node.type != "function_definition":
                continue

            name_node = node.child_by_field_name("name")
            if name_node is None:
                for c in getattr(node, "named_children", []):
                    if c.type == "identifier":
                        name_node = c
                        break
            if name_node is None:
                continue
            name = self._text(name_node)

            params_node = node.child_by_field_name("parameters")
            param_names: list[str] = []
            if params_node:
                for clause in params_node.named_children:
                    p_nodes = (
                        clause.named_children
                        if clause.type == "parameters"
                        else [clause]
                    )
                    for p in p_nodes:
                        if p.type != "parameter":
                            continue
                        p_name = ""
                        p_type = "Unknown"
                        for c in p.named_children:
                            if c.type == "identifier":
                                p_name = self._text(c)
                            elif c.type in (
                                "type_identifier",
                                "generic_type",
                                "compound_type",
                            ):
                                p_type = self._text(c)
                        if p_name:
                            param_names.append(p_name)

            body = node.child_by_field_name("body")
            if body and name and name not in self._function_bodies:
                self._function_bodies[name] = (body, param_names)

    def _iter_all_nodes(self, node: object):
        """Yield *node* and all its named descendants (depth-first)."""
        yield node
        for child in getattr(node, "named_children", []):
            yield from self._iter_all_nodes(child)

    def _trace_to_data_in_nodes(
        self,
        node_id: str,
        depth: int = 0,
        _visited: set[str] | None = None,
    ) -> list:
        """Walk the transformation graph backward to find originating DataSource nodes.

        Starts from *node_id* (which may be an ``in_*`` or ``tx_*`` ID) and
        follows ``tx.inputs`` edges until reaching ``DataSource`` nodes or
        hitting the depth limit.

        ``param_*`` nodes are dead ends in the post-parse graph; they are
        skipped here because the relevant ``df_map`` already resolved them to
        concrete ``in_*``/``tx_*`` IDs before this method is called.
        """
        if _visited is None:
            _visited = set()
        if depth > 10 or node_id in _visited:
            return []
        _visited.add(node_id)

        di = next((s for s in self._data_in if s.id == node_id), None)
        if di:
            return [di]

        tx = next((t for t in self._transformations if t.id == node_id), None)
        if tx:
            result = []
            for inp in tx.inputs:
                result.extend(
                    self._trace_to_data_in_nodes(inp, depth + 1, _visited)
                )
            return result

        return []

    def _apply_backward_string_evidence(
        self,
        evidence_cols: set[str],
        df_map: dict[str, str],
    ) -> None:
        """Upgrade or create data_in columns confirmed as STRING by backward evidence.

        For each DataFrame argument in *df_map* (``param_* → in_*/tx_*``),
        walk backward to the originating :class:`DataSource` nodes and:

        * **Existing columns** whose name is in *evidence_cols*: upgrade source
          to ``USAGE`` and confidence to ``HIGH`` (if currently from an
          upgradeable source such as ``xref_input``).  The type is only changed
          when it was previously ``UNKNOWN`` — business types are preserved.

        * **DataSources with no inferred_columns** (e.g. created from dynamic
          SQL where columns cannot be parsed statically): add new
          :class:`InferredColumn` entries for each evidence column with type
          ``STRING`` and source ``USAGE``.  This is safe because the evidence
          comes from ``col("X") rlike "..."`` patterns, which are unambiguous
          proof that column X exists in the DataFrame and holds string data.

        Sources not eligible for upgrade (``select``, ``naming_convention``,
        ``schema_definition``, …) are left untouched.
        """
        if not evidence_cols or not df_map:
            return

        _UPGRADEABLE = {"xref_input", "xref_output", "xref_function", ""}

        for node_id in df_map.values():
            for src in self._trace_to_data_in_nodes(node_id):
                if src.inferred_columns:
                    # Update existing columns
                    for col in src.inferred_columns:
                        if col.name not in evidence_cols:
                            continue
                        src_val = (
                            col.source.value
                            if hasattr(col.source, "value")
                            else str(col.source or "")
                        )
                        if src_val not in _UPGRADEABLE:
                            continue
                        if col.inferred_type == "UNKNOWN":
                            col.inferred_type = "STRING"
                        col.source = InferenceSource.USAGE
                        col.confidence = InferenceConfidence.HIGH
                else:
                    # DataSource has no columns yet (e.g. dynamic SQL).
                    # Create them from evidence — each is proven to be STRING
                    # by the rlike/=="" usage pattern in the code.
                    if src.inferred_columns is None:
                        src.inferred_columns = []
                    existing_names = {c.name for c in src.inferred_columns}
                    for col_name in sorted(evidence_cols):
                        if col_name not in existing_names:
                            src.inferred_columns.append(
                                InferredColumn(
                                    name=col_name,
                                    inferred_type="STRING",
                                    source=InferenceSource.USAGE,
                                    confidence=InferenceConfidence.HIGH,
                                )
                            )

    def _scan_args_for_nested_reads(
        self, args_node: Node | None
    ) -> str | None:
        """Recursively scan function arguments for spark.read chains.

        When a data source is passed directly as an argument to a function
        call (e.g. ``lowercaseColumns(spark.read.format("snowflake")...load())``),
        the chain unwinding misses it.  This method walks argument children,
        extracts any embedded data sources, and returns the last one's ID
        so the caller can bind it.
        """
        if args_node is None:
            return None
        last_source_id = None
        for arg_child in args_node.named_children:
            inner_chain = self._unwind_chain(arg_child)
            if not inner_chain:
                continue
            inner_methods = {name for name, _, _ in inner_chain}
            if inner_methods & SPARK_READ_ENTRY:
                ds = self._extract_data_source(inner_chain, None)
                if ds:
                    last_source_id = ds.id
            elif inner_methods & SPARK_WRITE_ENTRY:
                src_var = inner_chain[-1][0] if inner_chain else None
                self._extract_data_sink(inner_chain, src_var)
        return last_source_id

    # Higher-order function methods — methods that accept a lambda/function argument
    # whose body must be visited for nested Spark operations.
    # foreachPartition mirrors foreach: visits each partition with a user lambda.
    _HOF_METHODS = frozenset({
        "foreach", "foreachPartition", "map", "flatMap", "reduce", "foldLeft", "foldRight",
    })

    # Column expression roots — standalone Spark functions that produce a Column, not a DataFrame.
    # Derived from the Scala Spark API inventory (data_type=COLUMN, element_type=function).
    # When chain[-1] is one of these the chain is a Column expression and must NOT be
    # extracted as a DataFrame transformation.
    # Re-generated automatically via: python scripts/generate_scala_functions.py
    _COLUMN_EXPR_ROOTS: frozenset[str] = COLUMN_RETURNING_FUNCS

    # Terminal pipeline operations — methods that appear in TRANSFORM_OPS but signal the
    # end of the DataFrame pipeline (they do not produce another DataFrame for further use).
    # Combines hardcoded Scala collection ops with ACTION_FUNCS from the API inventory.
    # Re-generated automatically via: python scripts/generate_scala_functions.py
    _COLLECTION_ONLY_OPS: frozenset[str] = frozenset({
        "map", "flatMap", "reduce", "collect", "collectAsList",
        "head", "take", "takeAsList", "columns", "count",
        "foldLeft", "foldRight",
    }) | ACTION_FUNCS

    def _visit_foreach_body(
        self, node: Node, chain: list[tuple[str, Node | None, Node]],
        containing_object: str | None,
    ) -> None:
        """Recurse into foreach/map/flatMap/reduce lambda bodies.

        Handles three AST patterns:
        - block lambda:      .foreach { s => body }
        - case_block lambda:  .map { case (a,b) => body }
        - parenthesized:      .map(m => fn(m))
        """
        seq_var = chain[-1][0] if len(chain) >= 2 else None
        seq_strings = self._seq_string_vars.get(seq_var, []) if seq_var else []

        for method_name, args_node, call_node in chain:
            if method_name not in self._HOF_METHODS:
                continue
            if args_node is None:
                continue

            for lambda_var, body_nodes in self._find_lambda_bodies(args_node):
                apply_seq = bool(seq_strings and lambda_var and method_name != "reduce")
                if apply_seq:
                    for s in seq_strings:
                        self._set_string_var(lambda_var + ".__first__", s)
                        for child in body_nodes:
                            self._visit(child, containing_object)
                else:
                    for child in body_nodes:
                        self._visit(child, containing_object)

        self._visit_curried_block(node, containing_object)

    def _visit_curried_block(
        self, node: Node | None, containing_object: str | None,
    ) -> None:
        """Visit block argument of curried calls like foldLeft(df) { ... }."""
        if node is None or node.type != "call_expression":
            return
        outer_args = node.child_by_field_name("arguments")
        if outer_args is None or outer_args.type not in ("block", "case_block"):
            return
        for lambda_var, body_nodes in self._find_lambda_bodies(outer_args):
            if body_nodes:
                for child in body_nodes:
                    self._visit(child, containing_object)
    def _find_lambda_bodies(
        self, args_node: Node,
    ) -> list[tuple[str | None, list[Node]]]:
        """Extract (lambda_var, [body_nodes]) from the argument of a HOF call."""
        results: list[tuple[str | None, list[Node]]] = []

        if args_node.type == "block":
            for c in args_node.named_children:
                if c.type == "lambda_expression":
                    pair = self._extract_lambda_parts(c)
                    if pair[1]:
                        results.append(pair)
                    break

        elif args_node.type == "case_block":
            for c in args_node.named_children:
                if c.type == "case_clause":
                    body = self._extract_case_clause_body(c)
                    if body:
                        results.append((None, body))

        elif args_node.type == "arguments":
            for c in args_node.named_children:
                if c.type == "lambda_expression":
                    pair = self._extract_lambda_parts(c)
                    if pair[1]:
                        results.append(pair)
                    break

        return results

    def _extract_lambda_parts(
        self, lambda_node: Node,
    ) -> tuple[str | None, list[Node] | None]:
        """Extract (variable_name, [body_nodes]) from a lambda_expression."""
        var: str | None = None
        body_nodes: list[Node] | None = None

        for c in lambda_node.named_children:
            if c.type == "identifier" and var is None:
                var = self._text(c)
            elif c.type == "bindings":
                for b in c.named_children:
                    if b.type == "binding":
                        for bc in b.named_children:
                            if bc.type == "identifier":
                                var = self._text(bc)
                                break
                        break
            elif c.type in ("block", "indented_block"):
                body_nodes = list(c.named_children)
            elif c.type not in ("=>",) and c.is_named and body_nodes is None:
                body_nodes = [c]

        return var, body_nodes

    def _extract_case_clause_body(self, case_clause: Node) -> list[Node]:
        """Extract the body nodes from a case_clause (everything after =>)."""
        body: list[Node] = []
        after_arrow = False
        for c in case_clause.children:
            if not after_arrow:
                if c.type == "=>" or (not c.is_named and self._text(c) == "=>"):
                    after_arrow = True
            elif c.is_named:
                body.append(c)
        return body

    def _visit_nested_call_args(
        self, args_node: Node | None, containing_object: str | None = None,
    ) -> None:
        """Visit nested call_expressions in arguments (e.g., StructField inside StructType)."""
        if args_node is None or args_node.type != "arguments":
            return
        for arg_child in args_node.named_children:
            if arg_child.type == "call_expression":
                self._visit(arg_child, containing_object)
            elif arg_child.type == "identifier" and self._text(arg_child) in _TYPE_CONSTRUCTORS:
                self._create_execution_call(
                    self._text(arg_child), None, None, None, arg_child,
                )

    def _collect_concat_seqs(self, node: Node) -> list[str]:
        """Collect string lists from infix/postfix ++ expressions like a ++ b ++ c."""
        if node.type == "identifier":
            name = self._text(node)
            return list(self._seq_string_vars.get(name, []))
        if node.type in ("infix_expression", "postfix_expression"):
            result: list[str] = []
            for c in node.named_children:
                if c.type == "operator_identifier":
                    continue
                result.extend(self._collect_concat_seqs(c))
            return result
        return []

    def _try_extract_call(
        self, chain: list[tuple[str, Node | None, Node]]
    ) -> None:
        """Try to extract an ExecutionCall from a standalone function call."""
        if not chain:
            return

        func_name, args_node, call_node = chain[0]

        target_object = None
        if len(chain) >= 2 and chain[-1][1] is None:
            target_object = chain[-1][0]
            func_name = chain[0][0]

        if args_node is None and not target_object:
            return

        if args_node:
            self._scan_args_for_nested_reads(args_node)

        known = any(f.name == func_name for f in self._functions)
        if known or target_object or args_node:
            self._create_execution_call(
                func_name, args_node, None, None, call_node,
                target_object=target_object,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_source_type(
        filepath: str, has_main: bool, has_spark: bool, has_data_io: bool
    ) -> str:
        """Classify a Scala file as script, module, or notebook.

        Files under utils/, logger/, validators/, schemas/, exceptions/
        or whose name starts with Utils are classified as modules (libraries).
        Files with a main method or clear entry-point indicators are scripts.
        """
        path_lower = filepath.lower().replace("\\", "/")
        name = path_lower.rsplit("/", 1)[-1].replace(".scala", "")

        MODULE_DIRS = (
            "/utils/", "/logger/", "/validators/", "/schemas/",
            "/exceptions/", "/persistence/", "/metric_weight/",
            "/star_thresholds/", "/letter_grade_thresholds/",
            "/order_accuracy/", "/transformation/",
        )
        MODULE_PREFIXES = ("utils", "helper", "common", "base")

        if any(d in path_lower for d in MODULE_DIRS):
            return "module"
        if any(name.startswith(p) for p in MODULE_PREFIXES):
            return "module"
        if has_main:
            return "script"
        return "script"

    def parse(self, source_code: str, filepath: str = "<string>") -> ASG:
        """Parse Scala source code and return an ASG."""
        self._filepath = filepath
        self._source_bytes = source_code.encode("utf-8")
        self._data_in = []
        self._data_out = []
        self._transformations = []
        self._scope_stack = [{}]
        self._window_specs = []
        self._column_constraints = []
        self._column_relationships = []
        self._control_nodes = []
        self._functions = []
        self._execution_calls = []
        self._imports = {}
        self._variables = {}
        self._function_tx_ranges = {}
        self._function_di_ranges = {}
        self._function_tail_calls = {}
        self._function_bodies = {}
        self._current_function = "__main__"

        tree = self._parser.parse(self._source_bytes)
        root = tree.root_node

        self._visit(root)

        app_name = None
        for ds in self._data_in:
            if ds.name:
                continue

        spark_in_imports = any("SparkSession" in mod for mod in self._imports)
        spark_in_code = (
            b"SparkSession" in self._source_bytes if self._source_bytes else False
        )
        has_spark = spark_in_imports or spark_in_code
        has_main = any(f.name == "main" for f in self._functions)
        has_data_io = bool(self._data_in or self._data_out)

        source_type = self._classify_source_type(filepath, has_main, has_spark, has_data_io)
        is_ep = has_main or (source_type == "script" and has_spark and has_data_io)

        ep_reason: str | None = None
        ep_lineno: int | None = None
        ep_scope: str | None = None

        if is_ep:
            if has_main:
                ep_reason = "main_method"
                # Find the main FunctionDefinition to extract its line and enclosing object.
                main_fn = next((f for f in self._functions if f.name == "main"), None)
                if main_fn:
                    if main_fn.location:
                        ep_lineno = main_fn.location.start_line
                    obj_name = main_fn.containing_class or ""
                    ep_scope = f"{obj_name}::main" if obj_name else "main"
            else:
                ep_reason = "spark_session_creation"

        source_file = SourceFile(
            path=filepath,
            imports=self._imports,
            source_type=source_type,
            has_spark_session=has_spark,
            is_entry_point=is_ep,
            entry_point_reason=ep_reason,
            entry_point_lineno=ep_lineno,
            entry_point_scope=ep_scope,
        )

        # Deduplicate transformations with identical (scope, span, operation).
        # The scope is included so that argument-inlined clones (which may share
        # the same AST span as the original but have a different caller scope like
        # "main>processBronzeToSilver") are preserved alongside the originals.
        seen_spans: set[tuple[str, str, str]] = set()
        deduped: list[TransformationNode] = []
        for tx in self._transformations:
            loc = tx.location
            key = (
                loc.scope if loc else "",
                loc.span if loc else "",
                tx.operation,
            )
            if key not in seen_spans:
                seen_spans.add(key)
                deduped.append(tx)
        self._transformations = deduped

        # Remove orphan relationships referencing deduped transformations
        valid_tx_ids = {tx.id for tx in self._transformations}
        self._column_relationships = [
            r for r in self._column_relationships
            if r.source_transformation in valid_tx_ids
        ]

        return ASG(
            extraction_metadata=ExtractionMetadata(
                source_file=filepath,
                app_name=app_name,
            ),
            source_files=[source_file],
            functions=self._functions,
            data_in=self._data_in,
            data_out=self._data_out,
            transformations=self._transformations,
            execution_calls=self._execution_calls,
            window_specs=self._window_specs,
            column_constraints=self._column_constraints,
            column_relationships=self._column_relationships,
            control_nodes=self._control_nodes,
            warnings=self._warnings,
        )

    def parse_file(self, filepath: str | Path) -> ASG:
        """Parse a Scala file and return an ASG."""
        filepath = Path(filepath)
        source = filepath.read_text(encoding="utf-8")
        return self.parse(source, str(filepath))
