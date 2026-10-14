"""
Directory-level parser — parse all Scala files in a project and produce a merged ASG.

Mirrors the structure of asg_pyspark.parser.spark_ast.parse_spark_directory().
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from warp_core.ir.pyspark_models import (
    ASG,
    DataSink,
    DataSource,
    ExtractionMetadata,
    InferenceConfidence,
    InferenceSource,
    InferredColumn,
    ParsingReport,
    ParsedFileInfo,
    SourceFile,
    SourceLocation,
    SyntaxSummary,
    TransformationNode,
    UnderstandingSummary,
)

import tree_sitter_scala as _ts_scala
from tree_sitter import Language as _Language, Parser as _Parser

from asg_scala.parser.scala_spark_parser import ScalaSparkParser, SharedFunctionEntry
from asg_scala.companion_scanner import scan_companion_objects
from warp_core.schema.schema_tracker import SchemaPropagator
from warp_core.schema.naming_conventions import apply_naming_conventions


def _build_shared_function_registry(
    scala_files: list[Path],
    companion_symbols: dict[str, str],
) -> dict[str, SharedFunctionEntry]:
    """Pre-pass: parse every Scala file and collect all function bodies into a
    project-scoped registry.

    This registry is later injected into each per-file ``ScalaSparkParser`` so
    that Strategy A (data-source inlining), Strategy B (transformation
    specialisation), and Strategy C (backward string-evidence propagation) can
    follow calls across file boundaries.

    Only the *first* definition of a name is kept (order is alphabetical,
    matching the main parse loop).  Functions from test files are excluded to
    avoid polluting the registry with test helpers.
    """
    registry: dict[str, SharedFunctionEntry] = {}
    for scala_file in scala_files:
        # Skip test files — they often define helper functions with the same
        # names as production code and would shadow the real implementations.
        path_lower = str(scala_file).lower().replace("\\", "/")
        is_test = any(p in path_lower for p in ("/test/", "/tests/", "/spec/", "spec.scala"))
        if is_test:
            continue
        try:
            source_bytes = scala_file.read_bytes()
            # Lightweight parse: only extract function bodies, don't build ASG.
            prelim = ScalaSparkParser(companion_symbols=companion_symbols)
            prelim._source_bytes = source_bytes
            prelim._filepath = str(scala_file)
            # Extract only function definitions (no transformations / data sources)
            _tree = _Parser(_Language(_ts_scala.language())).parse(source_bytes)
            prelim._extract_function_bodies_only(_tree.root_node)
            for name, (body_node, params) in prelim._function_bodies.items():
                if name not in registry:
                    registry[name] = SharedFunctionEntry(
                        body_node=body_node,
                        params=params,
                        source_bytes=source_bytes,
                        file_path=str(scala_file),
                    )
        except Exception:
            continue
    return registry


def parse_scala_directory(
    dir_path: str | Path,
    *,
    include_timestamp: bool = True,
    column_types_path: str | Path | None = None,
    return_seq_vars: bool = False,
) -> "ASG | tuple[ASG, dict[str, dict[str, list[str]]]]":
    """Parse all .scala files in a directory and return a merged ASG.

    Args:
        dir_path: Path to the directory containing Scala files.
        include_timestamp: Whether to include generation timestamp.
        column_types_path: Optional path to a ``column_types.yaml`` file with
            custom naming-convention patterns.  When *None* the system
            auto-discovers ``column_types.yaml`` by walking up from *dir_path*.
        return_seq_vars: When *True*, return a tuple ``(asg, seq_string_vars)``
            where ``seq_string_vars`` is ``{filepath: {var_name: [col, ...]}}``
            — the resolved ``List[String]`` variables from all parsed files.
            Used by external enrichment steps (e.g. DDL catalog matching).

    Returns:
        Merged ASG, or ``(ASG, seq_string_vars)`` when *return_seq_vars* is True.
    """
    dir_path = Path(dir_path)
    if not dir_path.is_dir():
        raise ValueError(f"Not a directory: {dir_path}")

    scala_files = sorted(dir_path.rglob("*.scala"))
    if not scala_files:
        raise ValueError(f"No Scala files found in: {dir_path}")

    ScalaSparkParser.reset_global_counters()

    # Pre-scan all files to build a cross-file companion object symbol table.
    # This allows resolving constants like EconomicPosition.AM_ECONOMIC_POSITION
    # that are defined in one file and referenced in another.
    companion_symbols = scan_companion_objects(scala_files)

    # Pre-scan all files to build a cross-file function body registry.
    # This allows Strategy A (data-source inlining), Strategy B (transformation
    # specialisation), and Strategy C (backward string-evidence propagation) to
    # follow calls across file boundaries — e.g. when a process file calls a
    # helper defined in utils/, or when transformation chains pass through
    # multiple files.
    shared_functions = _build_shared_function_registry(scala_files, companion_symbols)

    parsing_stats = {
        "total": 0,
        "syntax_ok": 0,
        "syntax_errors": 0,
        "understanding_ok": 0,
        "understanding_errors": 0,
        "file_details": [],
    }

    source_files: list[SourceFile] = []
    all_seq_string_vars: dict[str, dict[str, list[str]]] = {}
    # Typed StructType schemas: {filepath: {var_name: [(col_name, spark_type), ...]}}
    all_struct_schema_vars: dict[str, dict[str, list[tuple[str, str]]]] = {}
    merged_data_in: list = []
    merged_data_out: list = []
    merged_transformations: list = []
    merged_functions: list = []
    merged_execution_calls: list = []
    merged_window_specs: list = []
    merged_column_constraints: list = []
    merged_column_relationships: list = []
    merged_control_nodes: list = []
    merged_warnings: list = []
    all_tail_calls: dict[str, str] = {}
    app_name: str | None = None

    for scala_file in scala_files:
        parsing_stats["total"] += 1
        relative_path = str(scala_file.relative_to(dir_path))

        try:
            source_code = scala_file.read_text(encoding="utf-8")
        except Exception as e:
            parsing_stats["syntax_errors"] += 1
            parsing_stats["file_details"].append(ParsedFileInfo(
                path=relative_path,
                file_type="scala_file",
                syntax_status="error",
                syntax_error=f"Cannot read file: {e}",
                understanding_status="skipped",
            ))
            continue

        try:
            # Note: shared_functions is intentionally NOT passed to the per-file
            # parser.  Cross-file evidence is applied as a global post-process
            # step in _apply_global_string_evidence() after all files are merged,
            # which avoids unintended Strategy A triggers from cross-file lookups.
            parser = ScalaSparkParser(companion_symbols=companion_symbols)
            file_asg = parser.parse(source_code, relative_path)
            parsing_stats["syntax_ok"] += 1
            parsing_stats["understanding_ok"] += 1
        except Exception as e:
            parsing_stats["understanding_errors"] += 1
            parsing_stats["file_details"].append(ParsedFileInfo(
                path=relative_path,
                file_type="scala_file",
                syntax_status="ok",
                understanding_status="error",
                understanding_error=f"{type(e).__name__}: {e}",
            ))
            continue

        if file_asg.source_files:
            sf = file_asg.source_files[0]
            sf.path = relative_path
            source_files.append(sf)

        for func in file_asg.functions:
            func.source_file = relative_path
        is_test = any(p in relative_path.lower() for p in ("/test/", "/tests/", "test_"))
        for node in file_asg.data_in:
            if node.location:
                node.location.pathfile = relative_path
            node.is_test_file = is_test
        for node in file_asg.data_out:
            if node.location:
                node.location.pathfile = relative_path
            node.is_test_file = is_test
        for node in file_asg.transformations:
            if node.location:
                node.location.pathfile = relative_path

        merged_data_in.extend(file_asg.data_in)
        merged_data_out.extend(file_asg.data_out)
        merged_transformations.extend(file_asg.transformations)
        merged_functions.extend(file_asg.functions)
        merged_execution_calls.extend(file_asg.execution_calls)
        merged_window_specs.extend(file_asg.window_specs)
        merged_column_constraints.extend(file_asg.column_constraints)
        merged_column_relationships.extend(file_asg.column_relationships)
        merged_control_nodes.extend(file_asg.control_nodes)
        merged_warnings.extend(file_asg.warnings)
        all_tail_calls.update(parser._function_tail_calls)
        if parser._seq_string_vars:
            all_seq_string_vars[relative_path] = dict(parser._seq_string_vars)
        if parser._struct_schema_vars:
            all_struct_schema_vars[relative_path] = dict(parser._struct_schema_vars)

        if file_asg.extraction_metadata.app_name and not app_name:
            app_name = file_asg.extraction_metadata.app_name

    parsing_report = ParsingReport(
        total_files=parsing_stats["total"],
        databricks_notebooks=0,
        python_scripts=0,
        scala_files=parsing_stats["total"],
        syntax=SyntaxSummary(
            ok=parsing_stats["syntax_ok"],
            corrected=0,
            errors=parsing_stats["syntax_errors"],
        ),
        understanding=UnderstandingSummary(
            ok=parsing_stats["understanding_ok"],
            errors=parsing_stats["understanding_errors"],
        ),
        files=parsing_stats["file_details"],
        generated_at=datetime.now() if include_timestamp else None,
    )

    asg = ASG(
        extraction_metadata=ExtractionMetadata(
            workload_root=str(dir_path.resolve()),
            source_file=str(dir_path),
            app_name=app_name,
            generated_at=datetime.now() if include_timestamp else None,
        ),
        source_files=source_files,
        functions=merged_functions,
        data_in=merged_data_in,
        data_out=merged_data_out,
        transformations=merged_transformations,
        execution_calls=merged_execution_calls,
        window_specs=merged_window_specs,
        column_constraints=merged_column_constraints,
        column_relationships=merged_column_relationships,
        control_nodes=merged_control_nodes,
        warnings=merged_warnings,
        parsing_report=parsing_report,
    )

    _run_post_merge_phases(
        asg,
        all_tail_calls,
        all_seq_string_vars,
        column_types_path=column_types_path,
        struct_schema_vars=all_struct_schema_vars,
    )

    # Global backward string-evidence propagation: scan ALL known function
    # bodies (local + cross-file) for rlike / === "" patterns and upgrade
    # matching DataSource columns to USAGE confidence.  Runs after naming
    # conventions so that pre-existing type assignments are preserved.
    _apply_global_string_evidence(asg, shared_functions)

    # Run deduplication as the absolute last step so all schema-assignment
    # passes (including _apply_global_string_evidence) have completed.  Any
    # earlier placement would cause schema-less nodes to survive that later
    # receive columns, producing apparent duplicates in the final ASG.
    _deduplicate_unresolved_sources(asg)

    # Column-signature correlation: connect in_* nodes to transformations that
    # explicitly reference their columns.  Runs after deduplication so only
    # canonical nodes are considered.
    _correlate_inputs_by_column_signature(asg)

    if return_seq_vars:
        return asg, all_seq_string_vars
    return asg


def _promote_indirect_inputs(asg: ASG) -> None:
    """Promote data_in from utility functions to caller scope using execution_calls."""
    func_data_in: dict[str, list[DataSource]] = {}
    for inp in asg.data_in:
        if inp.location and inp.location.scope:
            scope_parts = inp.location.scope.split(".")
            func_name = scope_parts[-1] if scope_parts else ""
            if func_name:
                func_data_in.setdefault(func_name, []).append(inp)

    if not func_data_in:
        return

    call_graph: dict[str, set[str]] = {}
    for call in asg.execution_calls:
        caller_func = call.caller.function if call.caller else None
        callee_func = call.callee.function if call.callee else None
        if caller_func and callee_func:
            call_graph.setdefault(caller_func, set()).add(callee_func)

    def _find_transitive_data_in(
        func_name: str, visited: set[str] | None = None
    ) -> list[DataSource] | None:
        if visited is None:
            visited = set()
        if func_name in visited:
            return None
        visited.add(func_name)
        if func_name in func_data_in:
            return func_data_in[func_name]
        for callee in call_graph.get(func_name, set()):
            result = _find_transitive_data_in(callee, visited)
            if result:
                return result
        return None

    max_id = max(
        (int(inp.id.split("_")[1]) for inp in asg.data_in if "_" in inp.id),
        default=0,
    )

    promoted: list[DataSource] = []
    for call in asg.execution_calls:
        func_name = call.callee.function if call.callee else None
        if not func_name:
            continue

        callee_inputs = _find_transitive_data_in(func_name)
        IO_READ_FUNCS: dict[str, str] = {
            "updateDataIntoRds": "csv",
            "insertIntoRds": "csv",
            "snowflakeUpdate": "csv",
            "updateData": "csv",
            "insertInto": "csv",
            "writeDataframeInSnowflake": "csv",
            "overwriteDataframeInRds": "csv",
            "writeDataframeInRds": "csv",
            "dataUpdateIntoS3": "csv",
        }
        if not callee_inputs and func_name in IO_READ_FUNCS:
            read_type = IO_READ_FUNCS[func_name]
            callee_inputs = [DataSource(
                id="tmpl_0",
                type=read_type,
                location=SourceLocation(pathfile="", scope=func_name, span="0:0-0:0"),
            )]
        if not callee_inputs:
            continue

        lit_args = call.literal_arguments or {}
        source_name = None
        for key in ("tableName", "table_name", "name", "path", "dbtable"):
            if key in lit_args:
                source_name = lit_args[key]
                break

        # Scala uses positional arg names (arg_0, arg_1, ...); try the first
        # two only.  Wrapper functions like getCurrentData(tableName, date,
        # columnNamePartition, columns, ...) place the table name at arg_0 or
        # arg_1 (when a SparkSession is at arg_0).  Scanning beyond arg_1 risks
        # picking up other string literals — e.g. a partition-column name like
        # "dt" at arg_2 — and naming the promoted DataSource after them.
        if not source_name:
            for key in ("arg_0", "arg_1"):
                val = lit_args.get(key)
                if val and isinstance(val, str) and len(val) > 1:
                    source_name = val
                    break

        if not source_name:
            # The table name is unresolvable at static-analysis time (e.g. comes
            # from a runtime config parameter).  We still want to register that
            # *some* read happens here, but only when the callee is "close to
            # data" — i.e. the function itself, or one of its direct callees,
            # directly contains an in_* node.  This prevents cascade promotion
            # through deep wrapper chains (execute → getCleanedDf → getCurrentData
            # → getDataFromSrv) which would create many duplicate SRC_* nodes.
            callee_direct = bool(func_data_in.get(func_name))
            callee_one_hop = any(
                c in func_data_in for c in call_graph.get(func_name, set())
            )
            if not callee_direct and not callee_one_hop:
                continue
            # Fall through with source_name=None; _assign_fallback_names will
            # give this node a unique SRC_* name later.

        # Extract table name from SQL queries (sqlglot AST with regex fallback)
        if source_name and any(kw in source_name.upper() for kw in ("SELECT", "FROM", "JOIN")):
            extracted = False
            import sqlglot
            try:
                parsed = sqlglot.parse_one(source_name, error_level=sqlglot.ErrorLevel.IGNORE)
                for t in parsed.find_all(sqlglot.exp.Table):
                    parts = [p for p in (t.catalog, t.db, t.name) if p]
                    if parts:
                        source_name = ".".join(parts)
                        extracted = True
                        break
            except Exception:
                pass
            if not extracted:
                import re
                sql_match = re.search(
                    r"(?:from|join)\s+([\w.]+)", source_name, re.IGNORECASE
                )
                if sql_match:
                    source_name = sql_match.group(1)

        template = next(
            (inp for inp in callee_inputs if inp.type != "other"),
            callee_inputs[0],
        )

        # Argument inlining in the parser already created a specific DataSource
        # for this call site (e.g. readTable(spark, "sales_data") → table/sales_data).
        # If a non-other callee input already has the exact same name, skip promotion
        # to avoid creating a redundant duplicate with path=source_name.
        if source_name and any(
            inp.name == source_name and inp.type != "other"
            for inp in callee_inputs
        ):
            continue

        max_id += 1
        caller_file = call.caller.file if call.caller else None
        caller_line = call.caller.line if call.caller else None
        caller_is_test = any(p in (caller_file or "").lower() for p in ("/test/", "/tests/", "test_"))

        promoted_in = DataSource(
            id=f"in_{max_id:03d}",
            type=template.type if template.type != "other" else "table",
            format=template.format,
            name=source_name,
            path=source_name,
            location=SourceLocation(
                pathfile=caller_file or "",
                scope=f"indirect:{func_name}",
                span=f"{caller_line}:0-{caller_line}:0" if caller_line else None,
            ) if caller_file else None,
            is_indirect=True,
            via_function=func_name,
            is_test_file=caller_is_test,
        )
        promoted.append(promoted_in)

    if promoted:
        asg.data_in.extend(promoted)


def _promote_indirect_outputs(asg: ASG) -> None:
    """Promote data_out from utility functions to caller scope using execution_calls."""
    func_data_out: dict[str, list[DataSink]] = {}
    for out in asg.data_out:
        if out.location and out.location.scope:
            scope_parts = out.location.scope.split(".")
            func_name = scope_parts[-1] if scope_parts else ""
            if func_name:
                func_data_out.setdefault(func_name, []).append(out)

    if not func_data_out:
        return

    call_graph: dict[str, set[str]] = {}
    for call in asg.execution_calls:
        caller_func = call.caller.function if call.caller else None
        callee_func = call.callee.function if call.callee else None
        if caller_func and callee_func:
            call_graph.setdefault(caller_func, set()).add(callee_func)

    def _find_transitive_data_out(
        func_name: str, visited: set[str] | None = None
    ) -> list[DataSink] | None:
        if visited is None:
            visited = set()
        if func_name in visited:
            return None
        visited.add(func_name)
        if func_name in func_data_out:
            return func_data_out[func_name]
        for callee in call_graph.get(func_name, set()):
            result = _find_transitive_data_out(callee, visited)
            if result:
                return result
        return None

    max_id = max(
        (int(out.id.split("_")[1]) for out in asg.data_out if "_" in out.id),
        default=0,
    )

    promoted: list[DataSink] = []
    for call in asg.execution_calls:
        func_name = call.callee.function if call.callee else None
        if not func_name:
            continue

        callee_outputs = _find_transitive_data_out(func_name)

        IO_FUNC_TYPES: dict[str, tuple[str, str]] = {
            "updateDataIntoRds": ("jdbc", "overwrite"),
            "insertIntoRds": ("jdbc", "overwrite"),
            "snowflakeUpdate": ("snowflake", "overwrite"),
            "writeDataframeInSnowflake": ("snowflake", "append"),
            "overwriteDataframeInRds": ("jdbc", "overwrite"),
            "writeDataframeInRds": ("jdbc", "append"),
        }

        if not callee_outputs and func_name in IO_FUNC_TYPES:
            io_type, io_mode = IO_FUNC_TYPES[func_name]
            callee_outputs = [DataSink(
                id="tmpl_0",
                type=io_type,
                mode=io_mode,
                location=SourceLocation(pathfile="", scope=func_name, span="0:0-0:0"),
            )]

        if not callee_outputs:
            continue

        lit_args = call.literal_arguments or {}
        dest_name = None
        for key in ("tableName", "table_name", "name", "path", "dbtable"):
            if key in lit_args:
                dest_name = lit_args[key]
                break

        if not dest_name:
            for key in sorted(k for k in lit_args if k.startswith("arg_")):
                val = lit_args[key]
                if val and isinstance(val, str) and len(val) > 1:
                    dest_name = val
                    break

        df_source_id = None
        if call.bindings and call.bindings.inputs:
            for binding in call.bindings.inputs:
                if binding.source_id:
                    df_source_id = binding.source_id
                    break

        template = callee_outputs[0]

        if not dest_name:
            continue

        max_id += 1
        caller_file = call.caller.file if call.caller else None
        caller_line = call.caller.line if call.caller else None
        caller_is_test = any(p in (caller_file or "").lower() for p in ("/test/", "/tests/", "test_"))

        promoted_out = DataSink(
            id=f"out_{max_id:03d}",
            type=template.type,
            format=template.format,
            name=dest_name,
            path=dest_name,
            mode=template.mode,
            source_id=df_source_id,
            location=SourceLocation(
                pathfile=caller_file or "",
                scope=f"indirect:{func_name}",
                span=f"{caller_line}:0-{caller_line}:0" if caller_line else None,
            ) if caller_file else None,
            is_indirect=True,
            via_function=func_name,
            is_test_file=caller_is_test,
        )
        promoted.append(promoted_out)

    if promoted:
        asg.data_out.extend(promoted)


def _derive_names_from_paths(asg: ASG) -> None:
    """Derive name from path for data_in/data_out entries that have path but no name."""
    for node in list(asg.data_in) + list(asg.data_out):
        if not node.name and getattr(node, "path", None):
            path = node.path
            segment = path.rsplit("/", 1)[-1]
            for ext in (".csv", ".parquet", ".json", ".orc", ".avro"):
                segment = segment.replace(ext, "")
            if segment:
                node.name = segment


def _assign_fallback_names(asg: ASG) -> None:
    """Assign sequential fallback names to unnamed non-test inputs."""
    PREFIX_MAP = {
        "redshift": "RDSHFT",
        "jdbc": "JDBC",
        "snowflake": "SNFLK",
    }
    counters: dict[str, int] = {}
    for inp in asg.data_in:
        if inp.name or getattr(inp, "is_test_file", False):
            continue
        src_type = inp.type or "unknown"
        if src_type == "memory":
            continue
        prefix = PREFIX_MAP.get(src_type, "SRC")
        counters[prefix] = counters.get(prefix, 0) + 1
        inp.name = f"{prefix}_{counters[prefix]:03d}"


def _propagate_columns_by_name(asg: ASG) -> None:
    """Cross-reference column propagation: share columns between same-named I/O."""
    out_columns: dict[str, list] = {}
    for out in asg.data_out:
        name = out.name
        if name and out.inferred_columns and name not in out_columns:
            out_columns[name] = out.inferred_columns

    in_columns: dict[str, list] = {}
    for inp in asg.data_in:
        name = inp.name
        if name and inp.inferred_columns:
            if name not in in_columns or len(inp.inferred_columns) > len(in_columns[name]):
                in_columns[name] = inp.inferred_columns

    for inp in asg.data_in:
        if inp.inferred_columns or not inp.name:
            continue
        source_cols = None
        xref_source = None
        if inp.name in out_columns:
            source_cols = out_columns[inp.name]
            xref_source = InferenceSource.XREF_OUTPUT
        elif inp.name in in_columns:
            source_cols = in_columns[inp.name]
            xref_source = InferenceSource.XREF_INPUT
        if source_cols and xref_source:
            inp.inferred_columns = [
                InferredColumn(
                    name=col.name,
                    inferred_type=col.inferred_type,
                    source=xref_source,
                    confidence=InferenceConfidence.LOW,
                )
                for col in source_cols
            ]



def _resolve_indirect_transformations(asg: ASG) -> None:
    """Project function-internal transformations to call sites.

    When function F contains N transformations and is called from site S,
    create N projected transformation entries at S's location. This lets
    the ASG reflect the *work* done, not just the syntactic call.

    Handles:
      - Direct calls:  val result = someFunction(df, args)
      - Curried calls: val result = someFunction(args)(df)
      - Loop calls:    items.map { x => someFunction(df, x) }
                       (foreach expansion already creates one EC per item)
    """
    ENTRY_POINT_NAMES = frozenset({"run", "main", "__main__", "init", "apply"})
    MAX_TX_PER_FUNC = 25

    func_txs_by_file: dict[tuple[str, str], list[TransformationNode]] = {}
    func_txs_global: dict[str, list[TransformationNode]] = {}
    for tx in asg.transformations:
        scope = tx.location.scope if tx.location else None
        pathfile = tx.location.pathfile if tx.location else ""
        if scope and scope != "__main__" and not scope.startswith("indirect:"):
            func_txs_by_file.setdefault((scope, pathfile), []).append(tx)
            func_txs_global.setdefault(scope, []).append(tx)

    if not func_txs_global:
        return

    projectable_names = {
        fn for fn, txs in func_txs_global.items()
        if fn not in ENTRY_POINT_NAMES
        and not fn.startswith("test_")
    }
    projectable_keys = {
        key for key in func_txs_by_file
        if key[0] in projectable_names
        and len(func_txs_by_file[key]) <= MAX_TX_PER_FUNC
    }

    if not projectable_names:
        return

    func_files: dict[str, set[str]] = {}
    for func in asg.functions:
        if func.source_file:
            func_files.setdefault(func.name, set()).add(func.source_file)

    max_id = max(
        (int(tx.id.split("_")[1]) for tx in asg.transformations if "_" in tx.id),
        default=0,
    )

    # Collect "caller>callee" scopes that were already specialized by
    # Strategy B argument inlining (identified by the "_inlined_from"
    # parameter added by _try_inline_function_with_literal_args).
    # For these call sites, _resolve_indirect_transformations must not
    # create an additional, unresolved projection.
    already_inlined_scopes: set[str] = {
        tx.parameters["_inlined_from"]
        for tx in asg.transformations
        if "_inlined_from" in (tx.parameters or {})
    }

    projected: list[TransformationNode] = []
    seen_projections: set[tuple[str, str, str]] = set()

    for call in asg.execution_calls:
        callee_name = call.callee.function if call.callee else None
        if not callee_name or callee_name not in projectable_names:
            continue

        caller_file = call.caller.file if call.caller else None
        caller_func = call.caller.function if call.caller else "__main__"
        caller_line = call.caller.line if call.caller else None

        if caller_func == callee_name:
            continue

        # Skip this call site if Strategy B already created specialized
        # transformations for it — projecting again would create duplicates.
        inlined_scope = f"{caller_func}>{callee_name}"
        if inlined_scope in already_inlined_scopes:
            continue

        dedup_key = (caller_file or "", str(caller_line or ""), callee_name)
        if dedup_key in seen_projections:
            continue
        seen_projections.add(dedup_key)

        file_key = (callee_name, caller_file or "")
        if file_key in projectable_keys:
            source_txs = func_txs_by_file[file_key]
        else:
            candidates = [k for k in projectable_keys if k[0] == callee_name]
            if len(candidates) == 1:
                source_txs = func_txs_by_file[candidates[0]]
            elif not candidates:
                continue
            else:
                source_txs = func_txs_by_file[candidates[0]]

        for orig_tx in source_txs:
            max_id += 1
            proj_tx = TransformationNode(
                id=f"tx_{max_id:03d}",
                operation=orig_tx.operation,
                inputs=orig_tx.inputs,
                logic=orig_tx.logic,
                parameters={
                    **orig_tx.parameters,
                    "_projected_from": callee_name,
                },
                inferred_input=orig_tx.inferred_input,
                inferred_output=orig_tx.inferred_output,
                location=SourceLocation(
                    pathfile=caller_file or "",
                    scope=f"{caller_func}>{callee_name}",
                    span=f"{caller_line}:0-{caller_line}:0" if caller_line else None,
                ) if caller_file else orig_tx.location,
            )
            projected.append(proj_tx)

    if projected:
        asg.transformations.extend(projected)



def _deduplicate_unresolved_sources(asg: ASG) -> None:
    """Collapse SRC_* nodes that share an identical column signature.

    When the table name is unresolvable at static-analysis time (runtime config
    parameter), ``_promote_indirect_inputs`` creates one ``SRC_*`` node per
    *call site*.  Many call sites may read from the same logical table with the
    same column set, producing dozens of redundant nodes.

    Two reduction rules are applied:

    1. **Column-set deduplication** — For SRC_* nodes *with* schema, keep only
       one representative per unique column fingerprint.  Column sets that are
       already fully covered by a named (non-SRC_*) node are dropped because the
       catalog or direct-read node is the authoritative representation.

    2. **Schema-less placeholder** — SRC_* nodes with *no* inferred columns are
       never silently discarded.  A real read was detected even if neither the
       table name nor its columns could be resolved.  We keep one placeholder per
       unique ``via_function`` so the detection surfaces in Data I/O and the
       score is not misleadingly inflated.  Discarding these entirely would hide
       genuine gaps in static-analysis coverage.
    """
    named_col_sets: list[frozenset[str]] = [
        frozenset(c.name for c in (src.inferred_columns or []))
        for src in asg.data_in
        if (src.name or "") and not (src.name or "").startswith("SRC_")
        if len(src.inferred_columns or []) >= 3
    ]

    unresolved = [s for s in asg.data_in if (s.name or "").startswith("SRC_")]
    named = [s for s in asg.data_in if not (s.name or "").startswith("SRC_")]

    seen_col_sets: set[frozenset[str]] = set()
    seen_schema_less_funcs: set[str] = set()
    kept: list[DataSource] = []

    for src in unresolved:
        cols = frozenset(c.name for c in (src.inferred_columns or []))

        if not cols:
            # No column evidence — keep one placeholder per wrapper function so
            # the detection is never silently hidden.  This is honest: we know
            # data is read via this function but cannot determine the table or
            # its schema.
            func_key = src.via_function or "unknown"
            if func_key not in seen_schema_less_funcs:
                seen_schema_less_funcs.add(func_key)
                kept.append(src)
            continue

        # Drop if columns are already fully covered by a named/catalog node.
        if any(cols <= nc for nc in named_col_sets):
            continue

        # Deduplicate by column fingerprint — same columns = same logical table.
        if cols in seen_col_sets:
            continue
        seen_col_sets.add(cols)
        kept.append(src)

    asg.data_in = named + kept


def _correlate_inputs_by_column_signature(
    asg: ASG,
    min_overlap: int = 2,
) -> int:
    """Connect in_* nodes to transformations via column-name intersection.

    When a DataSource's column names appear explicitly in a transformation's
    inferred columns (source in ``explicit``, ``select``, ``function_arg``),
    that DataSource is a structural candidate to be an input of that
    transformation — regardless of whether the parser could trace the DataFrame
    variable across function boundaries.

    Rules:
    - The DataSource must have at least ``min_overlap`` columns with the tx.
    - The intersection must cover ≥ 30 % of the tx's explicit column set.
    - Only ``in_*`` nodes with ≥ 3 inferred columns are considered (avoids
      false positives on tiny schemas).
    - The arc is added only if the ``in_*`` is not already in ``tx.inputs``.
    - Correlation evidence is recorded in ``tx.parameters['column_correlation']``
      as a list of ``{input_id, input_name, matched_columns}`` dicts.

    Returns the total number of new arcs created.
    """
    EVIDENCE_SOURCES = {"explicit", "select", "function_arg"}
    MIN_INPUT_COLS = 3

    # Only consider orphan in_* nodes — those with no existing connection to any
    # transformation.  Connected inputs already have structural lineage; adding
    # column-correlation arcs on top would create redundant and potentially
    # misleading edges in workloads where downstream txs see joined schemas
    # (columns from all joined inputs appear together, causing false matches).
    connected_in_ids: set[str] = {
        inp for tx in asg.transformations for inp in (tx.inputs or [])
        if inp.startswith("in_")
    }

    # Build index: in_* id → frozenset of uppercase column names.
    # Only consider columns whose source is ``catalog`` — i.e., derived from an
    # external authoritative schema (DDL, data catalog).  Inferred columns
    # (naming_convention, xref_input, schema_definition) are too speculative
    # to serve as correlation evidence; they are often propagated from other
    # nodes and would cause false-positive arcs in joined workloads.
    candidates: list[tuple[str, str | None, frozenset[str]]] = []  # (id, name, cols)
    for src in asg.data_in:
        if src.id in connected_in_ids:
            continue  # already connected — skip to avoid post-join false positives
        cols = frozenset(
            c.name.upper()
            for c in (src.inferred_columns or [])
            if c.name and getattr(c, "source", None) == InferenceSource.CATALOG
        )
        if len(cols) >= MIN_INPUT_COLS:
            candidates.append((src.id, src.name, cols))

    if not candidates:
        return 0

    arcs_added = 0
    for tx in asg.transformations:
        # Collect explicit column names referenced by this transformation.
        tx_explicit_cols: frozenset[str] = frozenset(
            col.name.upper()
            for col in (tx.inferred_input or []) + (tx.inferred_output or [])
            if col.name and col.source in EVIDENCE_SOURCES
        )
        if not tx_explicit_cols:
            continue

        existing_inputs = set(tx.inputs)
        correlations: list[dict] = []

        for src_id, src_name, src_cols in candidates:
            if src_id in existing_inputs:
                continue

            overlap = src_cols & tx_explicit_cols
            if len(overlap) < min_overlap:
                continue

            # Require overlap to be ≥ 30 % of the tx's explicit column set.
            if len(overlap) / len(tx_explicit_cols) < 0.30:
                continue

            tx.inputs.append(src_id)
            existing_inputs.add(src_id)
            arcs_added += 1
            correlations.append({
                "input_id": src_id,
                "input_name": src_name,
                "matched_columns": sorted(overlap),
            })

        if correlations:
            tx.parameters["column_correlation"] = correlations

    return arcs_added


_VALID_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]{1,50}$")


def _is_valid_column_identifier(s: str) -> bool:
    """Return True when *s* looks like a real column name.

    A valid identifier contains only letters, digits, and underscores, and is
    at most 50 characters long.  Strings with spaces, hyphens, dots, slashes,
    or free-form prose (e.g. ``"Cashier Station"``, ``"2021 R3"``, ``"C-103"``)
    are categorical values or labels captured from List[String] variables that
    happen to be near data-reading code, not actual column names.
    """
    return bool(_VALID_IDENTIFIER_RE.match(s))


def _build_struct_col_type_lookup(
    struct_schema_vars: dict[str, dict[str, list[tuple[str, str]]]],
) -> dict[str, str]:
    """Build a global {column_name_upper → internal_type} lookup from all StructType definitions.

    The values stored in ``struct_schema_vars`` are already internal types
    (as returned by ``ScalaSparkParser._extract_struct_field_type``), e.g.
    ``"STRING"``, ``"INT"``, ``"DOUBLE"``.  This function simply collects them
    into a flat, case-normalised dictionary.

    When a column name appears in multiple schemas with conflicting types, the
    most specific type wins: prefer any non-UNKNOWN type over UNKNOWN, and keep
    the first non-UNKNOWN seen (test schemas often have the most precise types).
    """
    lookup: dict[str, str] = {}
    for _filepath, var_dict in struct_schema_vars.items():
        for _var_name, fields in var_dict.items():
            for col_name, internal_type in fields:
                if col_name and internal_type and internal_type != "UNKNOWN":
                    key = col_name.upper()
                    if key not in lookup:
                        lookup[key] = internal_type
    return lookup


_METADATA_QUERY_RE = re.compile(
    r"^\s*SELECT\s+(?:MAX|MIN|COUNT|AVG|SUM|SHOW|DESCRIBE|EXPLAIN)\b",
    re.IGNORECASE,
)


def _infer_schema_from_downstream_transforms(asg: ASG) -> None:
    """Infer schema for naked data_in nodes from their immediate downstream transforms.

    For each production data_in without inferred_columns, collect column names
    from the ``inferred_output`` of all transforms that directly consume it.
    Assign them as ``InferenceSource.USAGE`` / ``InferenceConfidence.LOW`` columns.

    This is the "usage-sink" fallback: it fires *before* ``_propagate_columns_by_name``
    so that any schema recovered here can be further enriched by cross-referencing.

    Typical gains: utility-function reads where the immediate ``.withColumn(...)``
    reveals a column name (e.g. ``etl_updated_dt`` from ``to_timestamp(col(...))``)
    even though the full table schema is not statically resolvable.
    """
    input_to_tx: dict[str, list] = {}
    for tx in asg.transformations:
        for inp in (tx.inputs or []):
            input_to_tx.setdefault(inp, []).append(tx)

    for inp in asg.data_in:
        if inp.inferred_columns or inp.is_test_file:
            continue
        col_map: dict[str, str] = {}
        for tx in input_to_tx.get(inp.id, []):
            for col in (tx.inferred_output or []):
                if col.name and col.name not in col_map:
                    raw = col.inferred_type
                    col_map[col.name] = (
                        raw.value if hasattr(raw, "value") else str(raw)
                    ) if raw else "UNKNOWN"
        if col_map:
            inp.inferred_columns = [
                InferredColumn(
                    name=n,
                    inferred_type=t,
                    source=InferenceSource.USAGE,
                    confidence=InferenceConfidence.LOW,
                )
                for n, t in col_map.items()
            ]


def _stitch_colocated_io_arcs(asg: ASG) -> None:
    """Connect orphan data_in nodes to co-located data_out nodes via passthrough transforms.

    Some workloads use a direct read-modify-write pattern without explicit intermediate
    variable transformations tracked by the parser:

        val df = spark.read.csv(path)
        rdsUtils.update_data_into_rds(tableName = tableName, df = df)

    In these cases the ``data_in`` (csv) and ``data_out`` (jdbc) share the same
    ``name``, ``file``, and ``scope`` but are never connected through a transformation
    node — leaving the ``data_in`` as an orphan in the Connectivity Score.

    This pass creates a minimal ``passthrough`` ``TransformationNode`` for each such
    pair.  The passthrough node is only created when:

    - The ``data_in`` is a production node (not test, not metadata/fixture).
    - It has no existing connection in ``tx.inputs`` (it is genuinely orphan).
    - There is exactly one matching ``data_out`` by (name, file, scope).

    The passthrough improves the Connectivity Score without fabricating column-level
    lineage — it faithfully represents "this table was read and then written" without
    inventing transformation logic.
    """
    all_tx_inputs: set[str] = {
        inp for tx in asg.transformations for inp in (tx.inputs or [])
    }

    # Index data_out by (name, filename, scope)
    from collections import defaultdict
    out_index: dict[tuple[str, str, str], list] = defaultdict(list)
    for sink in asg.data_out:
        loc = sink.location
        fname = (loc.pathfile or "").split("/")[-1] if loc else ""
        scope = (loc.scope or "") if loc else ""
        out_index[(sink.name or "", fname, scope)].append(sink)

    passthrough_counter = 0
    for inp in asg.data_in:
        if inp.is_test_file or inp.nature != "data":
            continue
        if inp.id in all_tx_inputs:
            continue  # already connected

        loc = inp.location
        fname = (loc.pathfile or "").split("/")[-1] if loc else ""
        scope = (loc.scope or "") if loc else ""
        key = (inp.name or "", fname, scope)
        matching_sinks = out_index.get(key, [])
        if not matching_sinks:
            continue

        # Create one passthrough transformation per unique sink
        for sink in matching_sinks:
            tx_id = f"tx_passthrough_{passthrough_counter:04d}"
            passthrough_counter += 1
            tx = TransformationNode(
                id=tx_id,
                operation="passthrough",
                inputs=[inp.id],
                outputs=[sink.id],
                location=inp.location,
                logic=f"passthrough: {inp.name} → {sink.name}",
            )
            asg.transformations.append(tx)
            all_tx_inputs.add(inp.id)  # mark as connected so we don't double-add


_TEST_FILENAME_RE = re.compile(
    r"(?:^|/)(?:Test|Mock|Stub|Fixture|Spec|Fake)[A-Z].*\.scala$|.*(?:Test|Spec|Mock)\.scala$",
    re.IGNORECASE,
)


def _classify_node_nature(asg: ASG) -> None:
    """Classify each data_in node's `nature` field.

    - ``metadata``: orchestration/discovery queries (SELECT MAX, COUNT, SHOW …)
      whose result is a scalar, not a business DataFrame.  These are excluded
      from the Connectivity Score denominator.
    - ``fixture``: nodes from test files.  Also excluded from production scoring.
    - ``data`` (default): regular pipeline inputs.

    Classification is conservative: only nodes with a ``query`` that explicitly
    starts with a metadata-pattern SQL expression are marked ``metadata``.
    Test-file detection uses the existing ``is_test_file`` flag **plus** filename
    patterns (``Test*.scala``, ``*Spec.scala``, ``Mock*.scala``) so that test
    helpers that live under ``src/main`` are still classified as fixtures.
    """
    for inp in asg.data_in:
        pathfile = (inp.location.pathfile or "") if inp.location else ""
        if inp.is_test_file or _TEST_FILENAME_RE.search(pathfile):
            inp.nature = "fixture"
        elif inp.query and _METADATA_QUERY_RE.match(inp.query):
            inp.nature = "metadata"


def _run_post_merge_phases(
    asg: ASG,
    tail_calls: dict[str, str] | None = None,
    seq_string_vars: dict[str, dict[str, list[str]]] | None = None,
    column_types_path: "str | Path | None" = None,
    struct_schema_vars: dict[str, dict[str, list[tuple[str, str]]]] | None = None,
) -> None:
    """Run all post-merge processing on the merged Scala ASG."""
    _resolve_callee_files(asg)
    _resolve_cross_file_refs(asg, tail_calls or {})
    _promote_indirect_inputs(asg)
    _promote_indirect_outputs(asg)
    _resolve_indirect_transformations(asg)
    _derive_names_from_paths(asg)
    _classify_node_nature(asg)
    _stitch_colocated_io_arcs(asg)

    # Schema Enrichment: infer and propagate column schemas through the ASG.
    # Reuses the same SchemaPropagator as PySpark to populate
    # data_in.inferred_columns, tx.inferred_input/output, and data_out schemas.
    propagator = SchemaPropagator()
    propagator.process(asg)

    # Build a global col_name → Spark type lookup from all StructType definitions
    # across all parsed files. Used by _resolve_schema_from_seq_vars to convert
    # schema_definition columns from UNKNOWN to their proper Spark-mapped type.
    struct_col_types = _build_struct_col_type_lookup(struct_schema_vars or {})

    if seq_string_vars:
        _resolve_schema_from_seq_vars(asg, seq_string_vars, struct_col_types=struct_col_types)

    # Fallback: recover column names for naked inputs from their immediate
    # downstream transforms before the cross-reference propagation step.
    _infer_schema_from_downstream_transforms(asg)

    apply_naming_conventions(
        asg,
        config_path=column_types_path,
        workload_root=getattr(asg.extraction_metadata, "workload_root", None),
    )
    _propagate_columns_by_name(asg)
    _propagate_columns_by_function_usage(asg)
    _assign_fallback_names(asg)



def _find_join_co_inputs(asg: ASG) -> dict[str, set[str]]:
    """Build a map from each DataSource ID to the other DataSource IDs it is joined with.

    For a join ``A.join(B)``, returns ``{A.id: {B.id}, B.id: {A.id}}``.  When a
    chain passes through intermediate transformations (e.g. ``alias``, ``filter``),
    the traversal traces back through them to reach the originating ``in_*`` nodes.

    Used by ``_apply_global_string_evidence`` to detect genuinely ambiguous
    columns: if two co-joined DataSources are both empty, evidence columns
    assigned to them cannot be attributed to one source with certainty.
    """
    in_ids: set[str] = {d.id for d in asg.data_in}
    tx_index: dict[str, TransformationNode] = {t.id: t for t in asg.transformations}

    # Cache of tx_id → frozenset of in_* ids reachable through it.
    _cache: dict[str, frozenset[str]] = {}

    def _trace(node_id: str, visiting: frozenset[str] = frozenset()) -> frozenset[str]:
        """Return all DataSource IDs that flow into node_id."""
        if node_id in in_ids:
            return frozenset({node_id})
        if node_id in _cache:
            return _cache[node_id]
        if node_id in visiting:
            return frozenset()
        tx = tx_index.get(node_id)
        if tx is None:
            return frozenset()
        visiting = visiting | {node_id}
        result: set[str] = set()
        for inp in (tx.inputs or []):
            result |= _trace(inp, visiting)
        frozen = frozenset(result)
        _cache[node_id] = frozen
        return frozen

    co_inputs: dict[str, set[str]] = {}
    for tx in asg.transformations:
        if tx.operation != "join" or not tx.inputs or len(tx.inputs) < 2:
            continue
        # Collect all in_* sources that flow into each join input arm.
        arms: list[frozenset[str]] = [_trace(inp) for inp in tx.inputs]
        all_in: set[str] = set()
        for arm in arms:
            all_in |= arm
        for arm in arms:
            for sid in arm:
                co_inputs.setdefault(sid, set()).update(all_in - arm)

    return co_inputs


def _apply_global_string_evidence(
    asg: ASG,
    shared_functions: dict[str, SharedFunctionEntry],
) -> None:
    """Upgrade or create DataSource columns confirmed as STRING by project-wide evidence.

    After all per-file parses complete, scan EVERY known function body in the
    shared registry for ``col("X") rlike "..."`` and ``col("X") === ""``
    patterns.  Any column name found in these patterns is known to hold
    character data.

    Two behaviours:
    - **Existing column** whose name is in the evidence set: upgrade source →
      USAGE, confidence → HIGH (if the source was previously a low-confidence
      xref or unknown).
    - **Empty DataSource** (``inferred_columns`` is empty): create new
      ``InferredColumn`` entries with ``type=STRING``.  If the DataSource shares
      a join with another empty DataSource the evidence cannot be attributed to
      a single source, so the new columns receive ``source=AMBIGUOUS_JOIN`` and
      ``confidence=MEDIUM``.  Otherwise they receive ``source=USAGE`` and
      ``confidence=HIGH``.

    This post-processing pass handles the cross-file case where evidence
    functions (e.g. ``applyBV_Prestation_0027`` in ``BusinessRules.scala``)
    are defined in different files than the DataSources they operate on, making
    per-file ``df_map``–based propagation ineffective.
    """
    if not shared_functions:
        return

    # Collect all string evidence from all known function bodies.
    scanner = ScalaSparkParser(shared_functions=shared_functions)
    all_evidence: set[str] = set()
    for fn_name, entry in shared_functions.items():
        saved_bytes = scanner._source_bytes
        try:
            scanner._source_bytes = entry.source_bytes
            # Use a fresh visited set per top-level function to allow the same
            # inner functions to contribute evidence across different callers.
            evidence = scanner._collect_string_evidence(entry.body_node, set())
            all_evidence |= evidence
        finally:
            scanner._source_bytes = saved_bytes

    if not all_evidence:
        return

    # Build join co-input graph for ambiguity detection.
    co_inputs = _find_join_co_inputs(asg)
    empty_src_ids: set[str] = {
        d.id for d in asg.data_in
        if not d.is_test_file and not d.inferred_columns
    }

    _UPGRADEABLE = {"xref_input", "xref_output", "xref_function", ""}
    for src in asg.data_in:
        if src.is_test_file:
            continue
        if src.inferred_columns:
            # Upgrade existing columns that are in the evidence set.
            for col in src.inferred_columns:
                if col.name not in all_evidence:
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
            # No columns at all — create entries from evidence.
            # These DataSources (e.g. dynamic spark.sql() reads whose path
            # cannot be statically resolved) carry at least the columns that
            # downstream business-rule functions filter on.
            #
            # Join-ambiguity check: if this empty source shares a join with
            # another empty source, neither can claim the evidence columns
            # exclusively — mark them AMBIGUOUS_JOIN / MEDIUM instead.
            co_empty = co_inputs.get(src.id, set()) & empty_src_ids - {src.id}
            is_join_ambiguous = bool(co_empty)

            if src.inferred_columns is None:
                src.inferred_columns = []
            for col_name in sorted(all_evidence):
                src.inferred_columns.append(InferredColumn(
                    name=col_name,
                    inferred_type="STRING",
                    source=(
                        InferenceSource.AMBIGUOUS_JOIN
                        if is_join_ambiguous
                        else InferenceSource.USAGE
                    ),
                    confidence=(
                        InferenceConfidence.MEDIUM
                        if is_join_ambiguous
                        else InferenceConfidence.HIGH
                    ),
                ))


def _resolve_schema_from_seq_vars(
    asg: ASG,
    seq_string_vars: dict[str, dict[str, list[str]]],
    struct_col_types: dict[str, str] | None = None,
) -> None:
    """Attach inferred columns to data_in entries using resolved List[String] definitions.

    Scala projects commonly define column names as typed val constants in schema
    objects (e.g. val col: String = "name") and group them into List[String].
    These lists are passed to functions like getDataFromSrv() which build SQL
    queries.  This phase connects those resolved column lists to the
    corresponding data_in entries.

    When ``struct_col_types`` is provided (a global col_name_upper → type lookup
    built from all StructType definitions across the project), column types are
    resolved from that dictionary instead of being left as UNKNOWN.  This covers
    the common pattern where companion objects define both a ``List[String]`` of
    column names *and* a ``StructType`` schema for the same table.
    """
    col_type_lookup = struct_col_types or {}

    # Build a flat lookup: column_list_name -> [col_names]
    # across ALL files (schema objects + process files)
    all_col_lists: dict[str, list[str]] = {}
    for filepath, vars_dict in seq_string_vars.items():
        for list_name, col_names in vars_dict.items():
            if col_names and all(isinstance(c, str) for c in col_names):
                all_col_lists[list_name] = col_names

    if not all_col_lists:
        return

    # Identify data_in entries without columns that could benefit
    inputs_needing_cols = [
        d for d in asg.data_in
        if not d.inferred_columns and not d.is_test_file
    ]

    if not inputs_needing_cols:
        return

    # Strategy: for each data_in created via indirect function promotion,
    # find the process file that called the function and check if that
    # file has a seq_string_var that looks like a column list for this source.
    #
    # Heuristic: match by file — if a process file produced data_in entries
    # AND has seq_string_vars with column-like names (SrvColumns*, columns*),
    # attach those columns to the data_in from that file.

    # Group inputs by source file
    inputs_by_file: dict[str, list[DataSource]] = {}
    for inp in inputs_needing_cols:
        if inp.location and inp.location.pathfile:
            inputs_by_file.setdefault(inp.location.pathfile, []).append(inp)

    matched = 0
    for filepath, inputs in inputs_by_file.items():
        file_vars = seq_string_vars.get(filepath, {})
        if not file_vars:
            continue

        # Find column lists in this file's vars (names containing "Columns" or "columns")
        col_lists = {
            name: cols for name, cols in file_vars.items()
            if cols and len(cols) >= 2
        }

        if not col_lists:
            continue

        # For each input from this file, try to find the best matching column list.
        # Use the largest list (most complete schema) as the primary candidate.
        # If multiple inputs share the same file, distribute lists by order.
        sorted_lists = sorted(col_lists.items(), key=lambda x: len(x[1]), reverse=True)

        for inp in inputs:
            if inp.inferred_columns:
                continue

            # Try to match by examining which list is most likely for this input.
            # Use the first (largest) unmatched list from this file.
            best_cols = None
            for list_name, cols in sorted_lists:
                best_cols = cols
                break

            if not best_cols:
                continue

            columns = []
            for col_name in best_cols:
                # Skip strings that are not valid column identifiers (e.g.
                # "Cashier Station", "2021 R3", "C-103").  List[String]
                # variables sometimes hold categorical values or UI labels
                # rather than column names; admitting them inflates the
                # denominator with UNKNOWN-typed false positives.
                if not _is_valid_column_identifier(col_name):
                    continue
                # Resolve type from global StructType lookup when available,
                # so that columns defined in both a List[String] and a
                # StructType schema get the authoritative Spark type.
                resolved_type = col_type_lookup.get(col_name.upper(), "UNKNOWN")
                columns.append(InferredColumn(
                    name=col_name,
                    inferred_type=resolved_type,
                    source=InferenceSource.SCHEMA_DEFINITION,
                    confidence=InferenceConfidence.HIGH,
                ))
            if not columns:
                continue
            inp.inferred_columns = columns
            matched += 1

    if matched:
        import logging
        logging.getLogger(__name__).info(
            "Schema resolution: %d data_in entries enriched from List[String] definitions"
            " (%d unique column types resolved via StructType lookup)",
            matched,
            sum(1 for c in (c for inp in asg.data_in for c in (inp.inferred_columns or []))
                if c.source == InferenceSource.SCHEMA_DEFINITION and c.inferred_type != "UNKNOWN"),
        )


def _propagate_columns_by_function_usage(asg: ASG) -> None:
    """Propagate column schemas from test fixtures to production data sources via shared functions.

    Tests create fixtures (dfInput, dfExpected) with explicit column schemas and
    pass them to the same transformation functions that production code uses.
    Even though the names don't match (dfInput vs prod_table), we can infer
    that both must have the same schema because they feed the same function.

    Strategy:
      1. Build a mapping: function_name -> set of columns from test data_in
         entries in files that call that function.
      2. For each production data_in without columns, find which function
         produced or consumes it (via scope / via_function).
      3. If that function has a test-derived schema, apply it.
    """
    test_entries = [d for d in asg.data_in if d.is_test_file and d.inferred_columns]
    prod_entries = [d for d in asg.data_in if not d.is_test_file and not d.inferred_columns]

    if not test_entries or not prod_entries:
        return

    is_test_path = lambda p: any(m in (p or "").lower() for m in ("/test/", "/tests/", "test_"))

    # Step 1: Map test files -> columns they define
    test_file_columns: dict[str, list[InferredColumn]] = {}
    for d in test_entries:
        fp = d.location.pathfile if d.location else ""
        if not fp:
            continue
        if fp not in test_file_columns:
            test_file_columns[fp] = []
        test_file_columns[fp].extend(d.inferred_columns)

    # Step 2: Map function_name -> columns, using execution_calls to find
    # which functions each test file calls
    func_schema: dict[str, list[InferredColumn]] = {}
    for call in asg.execution_calls:
        caller_file = call.caller.file if call.caller else ""
        callee_func = call.callee.function if call.callee else ""
        if not caller_file or not callee_func:
            continue
        if not is_test_path(caller_file):
            continue
        if caller_file in test_file_columns and callee_func not in func_schema:
            func_schema[callee_func] = test_file_columns[caller_file]

    if not func_schema:
        return

    # Step 3: For each prod data_in without columns, find the function link
    matched = 0
    for d in prod_entries:
        scope = d.location.scope if d.location else ""
        via = d.via_function or ""

        # Extract function name from scope (e.g. "indirect:getCurrentData" -> "getCurrentData")
        target_func = via or (scope.split(":")[-1] if ":" in scope else scope)
        if not target_func:
            continue

        if target_func in func_schema:
            # Deduplicate columns by name, keeping the first occurrence
            seen: set[str] = set()
            columns: list[InferredColumn] = []
            for col in func_schema[target_func]:
                if col.name not in seen:
                    seen.add(col.name)
                    columns.append(InferredColumn(
                        name=col.name,
                        inferred_type=col.inferred_type,
                        source=InferenceSource.XREF_FUNCTION,
                        confidence=InferenceConfidence.MEDIUM,
                    ))
            if columns:
                d.inferred_columns = columns
                matched += 1

    if matched:
        import logging
        logging.getLogger(__name__).info(
            "Function-usage propagation: %d prod data_in enriched from test fixtures",
            matched,
        )


def _resolve_callee_files(asg: ASG) -> None:
    """Resolve callee.file from object name hint to actual source file path.

    The per-file parser stores the target object name (e.g. 'UtilsS3') in
    callee.file as a temporary hint.  This phase resolves it to the real
    file path by matching (containing_class, function_name) against the
    merged function definitions.  For ambiguous names (e.g. 'run' defined
    in multiple objects), the containing_class disambiguates.
    """
    # Build lookup: (containing_class, func_name) -> source_file
    class_func_to_file: dict[tuple[str, str], str] = {}
    # Fallback: func_name -> source_file (only when unambiguous)
    func_to_file: dict[str, str | None] = {}

    for func in asg.functions:
        cls = func.containing_class or ""
        src = func.source_file or ""
        if cls and src:
            class_func_to_file[(cls, func.name)] = src
        if func.name in func_to_file:
            if func_to_file[func.name] != src:
                func_to_file[func.name] = None  # ambiguous
        else:
            func_to_file[func.name] = src

    for ec in asg.execution_calls:
        if not ec.callee:
            continue
        caller_file = ec.caller.file if ec.caller else ""
        func_name = ec.callee.function
        object_hint = ec.callee.file  # temporary: object name from parser

        resolved_file = None

        # Try (object_hint, func_name) first for precise match
        if object_hint:
            resolved_file = class_func_to_file.get((object_hint, func_name))

        # Fallback: unambiguous func_name match
        if not resolved_file:
            candidate = func_to_file.get(func_name)
            if candidate:
                resolved_file = candidate

        # Set the real file path (or clear the hint)
        if resolved_file:
            ec.callee.file = resolved_file
        else:
            ec.callee.file = None


def _resolve_cross_file_refs(
    asg: ASG,
    tail_calls: dict[str, str] | None = None,
) -> None:
    """Resolve cross-file function outputs for execution_calls and data_out.

    Per-file parsers can't resolve function outputs defined in other files.
    This phase builds a global function-to-last-node mapping, extends it
    via tail-call chains, and patches unresolved target_nodes and
    data_out source_ids (marked as "var:XXX").
    """
    func_last_node: dict[str, str] = {}
    for func in asg.functions:
        for tx in reversed(asg.transformations):
            if tx.location and tx.location.scope == func.name:
                func_last_node[func.name] = tx.id
                break
        if func.name not in func_last_node:
            for di in reversed(asg.data_in):
                if di.location and di.location.scope == func.name:
                    func_last_node[func.name] = di.id
                    break

    if tail_calls:
        changed = True
        while changed:
            changed = False
            for fn, delegate in tail_calls.items():
                if fn not in func_last_node and delegate in func_last_node:
                    func_last_node[fn] = func_last_node[delegate]
                    changed = True

    for ec in asg.execution_calls:
        if not (ec.bindings and ec.bindings.output):
            continue
        out = ec.bindings.output
        if out.target_node:
            continue
        fn = ec.callee.function if ec.callee else None
        if fn and fn in func_last_node:
            out.target_node = func_last_node[fn]

    var_to_node: dict[str, str] = {}
    for ec in asg.execution_calls:
        if ec.bindings and ec.bindings.output:
            out = ec.bindings.output
            if out.target_node and out.variable_name:
                var_to_node[out.variable_name] = out.target_node

    for do in asg.data_out:
        if do.source_id and do.source_id.startswith("var:"):
            var_name = do.source_id[4:]
            do.source_id = var_to_node.get(var_name)
