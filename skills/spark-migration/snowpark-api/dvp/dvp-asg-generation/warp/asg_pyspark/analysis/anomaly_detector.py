"""
Anomaly Detector for ASG/ASG-Agnostic Quality Analysis.

This module scans ASG structures to detect issues that could affect
SQL generation or data lineage quality.

Detection Categories:
- Schema: Type inference failures, phantom columns
- Lineage: Broken flows, orphan nodes
- Logic: Black-box UDFs, untranslatable operations
- Reference: Missing bindings, broken references
- Join: Key mismatches, cross joins
- Control: Ambiguous branches
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from warp_core.symbol_table import SymbolTable
from asg_pyspark.parser.sql_schema_extractor import extract_sql_schema

from warp_core.ir.anomaly_models import (
    Anomaly,
    AnomalyCode,
    AnomalyContext,
    AnomalyGroup,
    AnomalyReport,
    ColumnOrigin,
    Severity,
    TransformationStep,
    create_anomaly,
    create_report,
    group_anomalies,
)

if TYPE_CHECKING:
    from warp_core.ir.agnostic_models import AgnosticASG


# =============================================================================
# Detection Context
# =============================================================================


@dataclass
class DetectionContext:
    """Context passed to each detector with pre-computed indexes."""
    
    asg: Any  # AgnosticASG or raw ASG dict
    migration_id: str
    
    # Pre-computed indexes for fast lookup
    transformations: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, Any] = field(default_factory=dict)
    sinks: dict[str, Any] = field(default_factory=dict)
    raw_asg: dict = field(default_factory=dict)  # Full ASG for cross-referencing
    functions: dict[str, Any] = field(default_factory=dict)
    
    # Lineage graph
    consumed_by: dict[str, set[str]] = field(default_factory=dict)  # node_id -> set of consumers
    produces: dict[str, set[str]] = field(default_factory=dict)     # node_id -> set of produced columns
    
    # Execution bindings
    param_bindings: dict[str, str] = field(default_factory=dict)    # param_name -> origin_node
    source_to_bindings: dict[str, set[str]] = field(default_factory=dict)  # source_id -> set of var names that reference it
    
    # Source file mapping (node_id -> source location)
    node_sources: dict[str, tuple[str, int]] = field(default_factory=dict)
    
    # Anomaly counter
    _sequence: int = 0
    
    def next_sequence(self) -> int:
        """Get next anomaly sequence number."""
        self._sequence += 1
        return self._sequence


def _safe_get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Safely get nested attribute/key."""
    for key in keys:
        if obj is None:
            return default
        if isinstance(obj, dict):
            obj = obj.get(key, default)
        else:
            obj = getattr(obj, key, default)
    return obj if obj is not None else default


def _parse_span(span: str) -> int:
    """Parse location span like '13:9-13:60' to get line number."""
    if not span:
        return 0
    try:
        return int(span.split(':')[0])
    except (ValueError, IndexError):
        return 0


def build_context(asg: Any, migration_id: str) -> DetectionContext:
    """Build detection context from ASG with pre-computed indexes."""
    ctx = DetectionContext(asg=asg, migration_id=migration_id)
    
    # Index transformations - handle both 'id' and 'transformation_id'
    transformations = _safe_get(asg, 'transformations', default=[]) or []
    for tx in transformations:
        tx_id = _safe_get(tx, 'id') or _safe_get(tx, 'transformation_id') or _safe_get(tx, 'node_id')
        if tx_id:
            ctx.transformations[tx_id] = tx
            
            # Track source location - handle nested 'location' object
            location = _safe_get(tx, 'location', default={})
            source_file = _safe_get(location, 'pathfile') or _safe_get(tx, 'source_file', default='unknown')
            span = _safe_get(location, 'span', default='')
            source_line = _parse_span(span) or _safe_get(tx, 'source_line', default=0)
            ctx.node_sources[tx_id] = (source_file, source_line)
            
            # Build consumption graph
            inputs = _safe_get(tx, 'inputs', default=[]) or []
            for inp in inputs:
                inp_id = inp if isinstance(inp, str) else _safe_get(inp, 'ref')
                if inp_id:
                    if inp_id not in ctx.consumed_by:
                        ctx.consumed_by[inp_id] = set()
                    ctx.consumed_by[inp_id].add(tx_id)
    
    # Index sources (data inputs)
    data_in = _safe_get(asg, 'data_in', default=[]) or []
    for source in data_in:
        src_id = _safe_get(source, 'id') or _safe_get(source, 'node_id')
        if src_id:
            ctx.sources[src_id] = source
            location = _safe_get(source, 'location', default={})
            ctx.node_sources[src_id] = (
                _safe_get(location, 'pathfile') or _safe_get(source, 'source_file', default='unknown'),
                _parse_span(_safe_get(location, 'span', default='')) or _safe_get(source, 'source_line', default=0),
            )
    
    # Index sinks (data outputs)
    data_out = _safe_get(asg, 'data_out', default=[]) or []
    for sink in data_out:
        sink_id = _safe_get(sink, 'id') or _safe_get(sink, 'node_id')
        if sink_id:
            ctx.sinks[sink_id] = sink
            location = _safe_get(sink, 'location', default={})
            ctx.node_sources[sink_id] = (
                _safe_get(location, 'pathfile') or _safe_get(sink, 'source_file', default='unknown'),
                _parse_span(_safe_get(location, 'span', default='')) or _safe_get(sink, 'source_line', default=0),
            )
            # Track consumption from source_id
            source_id = _safe_get(sink, 'source_id')
            if source_id:
                if source_id not in ctx.consumed_by:
                    ctx.consumed_by[source_id] = set()
                ctx.consumed_by[source_id].add(sink_id)
    
    # Index functions
    functions = _safe_get(asg, 'functions', default=[]) or []
    for func in functions:
        func_name = _safe_get(func, 'name')
        if func_name:
            ctx.functions[func_name] = func
    
    # Build param bindings from execution_instances
    # Also build reverse mapping: source_id -> set of variable names that reference it
    exec_instances = _safe_get(asg, 'execution_instances', default=[]) or []
    for inst in exec_instances:
        bindings = _safe_get(inst, 'bindings', default=[]) or []
        for binding in bindings:
            input_map = _safe_get(binding, 'input_map', default=[]) or []
            for mapping in input_map:
                param = _safe_get(mapping, 'parameter')
                origin = _safe_get(mapping, 'origin_node')
                if param and origin:
                    ctx.param_bindings[f"param_{param}"] = origin
                    # Build reverse mapping for orphan detection
                    if origin not in ctx.source_to_bindings:
                        ctx.source_to_bindings[origin] = set()
                    ctx.source_to_bindings[origin].add(param)
                    ctx.source_to_bindings[origin].add(f"param_{param}")
                    ctx.source_to_bindings[origin].add(f"df_{param.replace('df_', '')}")
    
    # Process execution_calls from ASG
    # This connects data sources passed to function calls (fixes LIN_002 false positives)
    exec_calls = _safe_get(asg, 'execution_calls', default=[]) or []
    for call in exec_calls:
        bindings = _safe_get(call, 'bindings', default={})
        inputs = _safe_get(bindings, 'inputs', default=[]) or []
        for inp in inputs:
            source_id = _safe_get(inp, 'source_id')
            source_type = _safe_get(inp, 'source_type')
            arg_name = _safe_get(inp, 'arg_name')
            inferred_origin = _safe_get(inp, 'inferred_origin', default='')
            
            # Register param_bindings from execution_calls
            if arg_name and source_id:
                param_key = f"param_{arg_name}"
                if param_key not in ctx.param_bindings:
                    ctx.param_bindings[param_key] = source_id
                if source_id not in ctx.source_to_bindings:
                    ctx.source_to_bindings[source_id] = set()
                ctx.source_to_bindings[source_id].add(arg_name)
                ctx.source_to_bindings[source_id].add(param_key)

            # If source_type is 'data_in', it's a direct DataSource reference
            if source_type == 'data_in' and source_id:
                # Mark this source as referenced
                if source_id not in ctx.consumed_by:
                    ctx.consumed_by[source_id] = set()
                ctx.consumed_by[source_id].add(_safe_get(call, 'call_id', default='unknown'))
                
                # Also add to source_to_bindings
                if source_id not in ctx.source_to_bindings:
                    ctx.source_to_bindings[source_id] = set()
                if arg_name:
                    ctx.source_to_bindings[source_id].add(arg_name)
            
            # If there's an inferred_origin pointing to data_in, mark that too
            if inferred_origin and inferred_origin.startswith('data_in.'):
                origin_id = inferred_origin.replace('data_in.', '')
                if origin_id not in ctx.consumed_by:
                    ctx.consumed_by[origin_id] = set()
                ctx.consumed_by[origin_id].add(_safe_get(call, 'call_id', default='unknown'))

    # Process call_sites with SQL queries to resolve additional bindings
    # This helps resolve REF_002 for method calls that return DataFrames
    for call_site in SymbolTable._global_call_sites:
        output_var = call_site.get('output_variable')
        literal_args = call_site.get('literal_arguments', {})
        query = literal_args.get('query')
        
        if output_var and query:
            # Extract schema from SQL query
            schema_result = extract_sql_schema(query)
            if schema_result.output_columns and schema_result.method != 'failed':
                # Create a synthetic binding for this variable
                # The "origin" is a synthetic ID based on the method call
                func_name = call_site.get('function_name', 'unknown')
                containing_class = call_site.get('containing_class', '')
                line = call_site.get('line_number', 0)
                synthetic_origin = f"call_{containing_class}_{func_name}_{line}"
                
                # Register bindings for the output variable
                param_key = f"param_{output_var}"
                if param_key not in ctx.param_bindings:
                    ctx.param_bindings[param_key] = synthetic_origin
                
                # Also register variations
                df_key = f"param_df_{output_var.replace('df_', '')}"
                if df_key not in ctx.param_bindings:
                    ctx.param_bindings[df_key] = synthetic_origin
                
                # Register in source_to_bindings for reverse lookup
                if synthetic_origin not in ctx.source_to_bindings:
                    ctx.source_to_bindings[synthetic_origin] = set()
                ctx.source_to_bindings[synthetic_origin].add(output_var)
                ctx.source_to_bindings[synthetic_origin].add(param_key)
    
    return ctx


# =============================================================================
# Individual Detectors
# =============================================================================




def _can_infer_type_from_expression(snippet: str) -> bool:
    """
    Check if the column type can be inferred from the expression.
    
    Uses the central PySpark function registry for consistent type inference,
    augmented with pandas function names (same semantic patterns).
    Returns True if the expression contains functions with known return types,
    meaning we should NOT report SCH_001 for this column.
    """
    from warp_core.spark_functions import can_infer_type
    if can_infer_type(snippet):
        return True
    from warp_core.pandas_functions import ALL_PANDAS_NAMES
    snippet_lower = snippet.lower()
    return any(f"{name}(" in snippet_lower for name in ALL_PANDAS_NAMES)


def detect_unknown_types(ctx: DetectionContext) -> list[Anomaly]:
    """
    SCH_001: Detect columns with UNKNOWN type.
    
    Scans all transformations for produced columns with unknown types.
    Checks both 'produced_columns' and 'inferred_output' fields.
    """
    anomalies = []
    seen_columns = set()  # Avoid duplicate reports for same column
    inferable_columns = set()  # Columns whose type can be inferred from expressions
    
    known_from_data_in: set[str] = set()
    raw = ctx.asg if isinstance(ctx.asg, dict) else {}
    for di in raw.get('data_in', []):
        for col in (di.get('inferred_columns') or []):
            t = (col.get('inferred_type') or 'UNKNOWN') if isinstance(col, dict) else 'UNKNOWN'
            if t != 'UNKNOWN':
                known_from_data_in.add(col.get('name', ''))
    
    # First pass: identify columns created by expressions with known types
    for tx_id, tx in ctx.transformations.items():
        spark_snippet = _safe_get(tx, 'logic', default='') or                        _safe_get(tx, 'spark_code', default='') or                        _safe_get(tx, 'expression', default='')
        if _can_infer_type_from_expression(str(spark_snippet)):
            # Extract column name from withColumn
            params = tx.get('parameters', {})
            col_name = params.get('column_name', '')
            if col_name:
                inferable_columns.add(col_name)
    
    for tx_id, tx in ctx.transformations.items():
        # Check both field names used in different ASG formats
        produced = (_safe_get(tx, 'produced_columns', default=[]) or []) + \
                   (_safe_get(tx, 'inferred_output', default=[]) or [])
        
        for col in produced:
            col_name = _safe_get(col, 'name')
            
            # Skip invalid column names (expressions, join conditions, etc.)
            if not col_name or not isinstance(col_name, str):
                continue
            # Skip obvious non-column patterns (join conditions, comparisons)
            if any(op in col_name for op in ['==', '!=', '>=', '<=', '>', '<', ' AND ', ' OR ']):
                continue
            # Skip columns that look like full expressions
            if col_name.count('.') > 1 or col_name.count(' ') > 2:
                continue
            if col_name.startswith(('F.expr(', 'F.col(', 'F.lit(', 'col(', 'expr(', 'lit(')):
                continue
            
            # Check both 'logical_type' and 'inferred_type'
            logical_type = _safe_get(col, 'logical_type', default='') or \
                          _safe_get(col, 'inferred_type', default='')
            
            # Skip if we've already reported this column or type is known
            col_key = f"{tx_id}:{col_name}"
            if col_key in seen_columns:
                continue
            
            if logical_type and 'UNKNOWN' in str(logical_type).upper():
                seen_columns.add(col_key)
                source_file, source_line = ctx.node_sources.get(tx_id, ('unknown', 0))
                spark_snippet = _safe_get(tx, 'logic', default='') or \
                               _safe_get(tx, 'spark_code', default='') or \
                               _safe_get(tx, 'expression', default='unknown')
                
                # Skip if type can be inferred from the expression
                if _can_infer_type_from_expression(str(spark_snippet)):
                    continue
                
                # Skip if this column was created by an inferable expression
                if col_name in inferable_columns:
                    continue
                
                if col_name in known_from_data_in:
                    continue
                
                anomalies.append(create_anomaly(
                    code=AnomalyCode.SCH_001,
                    target_node=tx_id,
                    target_type='transformation',
                    source_file=source_file,
                    source_line=source_line,
                    spark_snippet=str(spark_snippet)[:200],
                    migration_id=ctx.migration_id,
                    sequence=ctx.next_sequence(),
                    column=col_name,
                    severity=Severity.MEDIUM,  # UNKNOWN types are warnings, not blockers
                ))
    
    return anomalies


def detect_truncated_flows(ctx: DetectionContext) -> list[Anomaly]:
    """
    LIN_001: Detect transformations without source_id (broken lineage).
    
    A transformation with no inputs or source_id indicates a broken flow.
    """
    anomalies = []
    
    for tx_id, tx in ctx.transformations.items():
        source_id = _safe_get(tx, 'source_id')
        inputs = _safe_get(tx, 'inputs', default=[]) or []
        
        # Skip if transformation has valid source
        if source_id or inputs:
            continue
        
        # Skip certain transformation types that don't need inputs
        tx_type = _safe_get(tx, 'operation', default='') or _safe_get(tx, 'type', default='')
        if tx_type in ('literal', 'constant', 'spark_config', 'source'):
            continue
        
        # Skip projected transformations (replicas created by
        # _resolve_indirect_transformations) — their lineage gaps
        # mirror the original function and are already reported there.
        params = _safe_get(tx, 'parameters', default={}) or {}
        scope = _safe_get(tx, 'location', 'scope', default='')
        if params.get('_projected_from') or (isinstance(scope, str) and '>' in scope):
            continue
        
        source_file, source_line = ctx.node_sources.get(tx_id, ('unknown', 0))
        spark_snippet = _safe_get(tx, 'logic', default='') or \
                       _safe_get(tx, 'spark_code', default='') or \
                       _safe_get(tx, 'expression', default='unknown')
        
        # If the logic references a DataFrame-like variable (df*, *Df),
        # the source exists but is unresolvable statically (e.g. from a
        # for-comprehension); downgrade to MEDIUM.
        logic_str = str(spark_snippet).split('.')[0].strip()
        lsl = logic_str.lower()
        severity = Severity.CRITICAL
        if lsl.startswith('df') or lsl.endswith('df') or 'dataframe' in lsl or 'window' in lsl or lsl.startswith('when(') or lsl.startswith('col('):
            severity = Severity.MEDIUM
        
        anomalies.append(create_anomaly(
            code=AnomalyCode.LIN_001,
            target_node=tx_id,
            target_type='transformation',
            source_file=source_file,
            source_line=source_line,
            spark_snippet=str(spark_snippet)[:200],
            migration_id=ctx.migration_id,
            sequence=ctx.next_sequence(),
            severity=severity,
        ))
    
    return anomalies


def detect_orphan_resources(ctx: DetectionContext) -> list[Anomaly]:
    """
    LIN_002: Detect data sources/sinks not connected to any flow.
    
    Orphan sources are never consumed by transformations.
    Orphan sinks have no valid source_id.
    """
    anomalies = []
    
    # Build set of all referenced inputs (from transformations)
    referenced_inputs = set()
    for tx_id, tx in ctx.transformations.items():
        inputs = _safe_get(tx, 'inputs', default=[]) or []
        for inp in inputs:
            inp_ref = inp if isinstance(inp, str) else _safe_get(inp, 'ref')
            if inp_ref:
                referenced_inputs.add(inp_ref)
    
    # Check orphan sources (never consumed)
    for src_id, source in ctx.sources.items():
        resource_name = _safe_get(source, 'name', default=src_id)
        
        # Check if source is referenced by any transformation
        # 1. Direct reference by node_id
        is_referenced = src_id in ctx.consumed_by
        
        # 2. Reference by resource name variations
        if not is_referenced:
            name_variations = {
                resource_name,
                f"df_{resource_name}",
                f"param_df_{resource_name}",
                f"param_{resource_name}",
            }
            is_referenced = bool(name_variations & referenced_inputs)
        
        # 3. Reference through execution_instances bindings (the key fix!)
        # This catches cases like: df_sales -> in_022 where transformations use df_sales
        if not is_referenced and src_id in ctx.source_to_bindings:
            bound_vars = ctx.source_to_bindings[src_id]
            is_referenced = bool(bound_vars & referenced_inputs)
        
        # 4. Reference through call sites argument_bindings
        # This catches cases like: func(df) where df was assigned from a source
        if not is_referenced:
            for call_site in SymbolTable._global_call_sites:
                bindings = call_site.get('argument_bindings', {})
                if src_id in bindings.values():
                    is_referenced = True
                    break
        
        # 5. Reference through control flow usage (.rdd, .count, .isEmpty, etc.)
        # This catches cases like: if df.rdd.isEmpty() or df.count() > 0
        if not is_referenced:
            if src_id in SymbolTable._global_control_usages:
                is_referenced = True
        
        if not is_referenced:
            source_file, source_line = ctx.node_sources.get(src_id, ('unknown', 0))
            
            # Check if this is an empty fallback pattern (LIN_005 - INFO)
            is_empty_fallback = _safe_get(source, 'is_empty_fallback', default=False)
            
            if is_empty_fallback:
                # Empty fallback pattern: spark.createDataFrame([])
                # Report as LIN_005 with INFO severity - this is intentional, not an error
                anomalies.append(create_anomaly(
                    code=AnomalyCode.LIN_005,
                    target_node=src_id,
                    target_type='source',
                    source_file=source_file,
                    source_line=source_line,
                    spark_snippet="spark.createDataFrame([])",
                    migration_id=ctx.migration_id,
                    sequence=ctx.next_sequence(),
                    resource_name=resource_name,
                    resource_type='DataSource',
                    severity=Severity.INFO,  # Informational - intentional fallback pattern
                ))
            else:
                # True orphan - report as LIN_002
                anomalies.append(create_anomaly(
                    code=AnomalyCode.LIN_002,
                    target_node=src_id,
                    target_type='source',
                    source_file=source_file,
                    source_line=source_line,
                    spark_snippet=f"spark.read.table('{resource_name}')",
                    migration_id=ctx.migration_id,
                    sequence=ctx.next_sequence(),
                    resource_name=resource_name,
                    resource_type='DataSource',
                    severity=Severity.LOW,  # Orphan sources may be intentional
                ))
    
    # Build index of sink names that have valid lineage (source_id) somewhere.
    # A sink may appear twice: as a "template" in a utility file (no source_id)
    # and as a "promoted" copy in the caller file (with source_id).
    resolved_sink_names: set[str] = set()
    for sid, s in ctx.sinks.items():
        if _safe_get(s, 'source_id'):
            sname = _safe_get(s, 'name')
            if sname:
                resolved_sink_names.add(sname)

    # Check orphan sinks (no source_id)
    for sink_id, sink in ctx.sinks.items():
        source_id = _safe_get(sink, 'source_id')
        if not source_id:
            # DML operations (DELETE, INSERT, etc.) are side-effects with no DataFrame source
            sink_format = _safe_get(sink, 'format', default='')
            if sink_format == 'sql_dml':
                continue
            source_file, source_line = ctx.node_sources.get(sink_id, ('unknown', 0))
            resource_name = _safe_get(sink, 'name', default=sink_id)

            # If a promoted copy with valid lineage exists, mark as resolved
            has_promoted_copy = resource_name in resolved_sink_names

            anom = create_anomaly(
                code=AnomalyCode.LIN_001,
                target_node=sink_id,
                target_type='sink',
                source_file=source_file,
                source_line=source_line,
                spark_snippet=f"df.write.saveAsTable('{resource_name}')",
                migration_id=ctx.migration_id,
                sequence=ctx.next_sequence(),
                severity=Severity.CRITICAL,
            )
            if has_promoted_copy:
                anom.resolved = True
                anom.resolution = (
                    f"Lineage resolved via indirect output promotion — "
                    f"a promoted copy of '{resource_name}' with valid source_id "
                    f"exists in the caller file."
                )
                anom.resolved_by = "auto"
            anomalies.append(anom)
    
    return anomalies


def detect_blackbox_udfs(ctx: DetectionContext) -> list[Anomaly]:
    """
    LOG_001: Detect UDFs calling external libraries.
    
    Looks for UDF definitions referencing non-standard modules.
    """
    anomalies = []
    
    # Common external library patterns
    external_patterns = [
        r'from\s+(\w+)\s+import',  # From imports
        r'import\s+(\w+)',  # Direct imports
    ]
    
    # Standard libs that are translatable
    known_safe = {
        'pyspark', 'spark', 'typing', 'datetime', 'decimal', 'math', 'json',
        'collections', 'functools', 'itertools', 're', 'os', 'sys',
        'f', 'col', 'lit', 'when',  # Spark functions
    }
    
    for tx_id, tx in ctx.transformations.items():
        tx_type = _safe_get(tx, 'operation', default='') or _safe_get(tx, 'type', default='')
        logic = _safe_get(tx, 'logic', default='') or ''
        
        # Check for UDF patterns
        if 'udf' in str(tx_type).lower() or 'udf' in logic.lower():
            # Look for external library references
            for pattern in external_patterns:
                matches = re.findall(pattern, logic)
                for match in matches:
                    if match.lower() not in known_safe:
                        source_file, source_line = ctx.node_sources.get(tx_id, ('unknown', 0))
                        
                        anomalies.append(create_anomaly(
                            code=AnomalyCode.LOG_001,
                            target_node=tx_id,
                            target_type='transformation',
                            source_file=source_file,
                            source_line=source_line,
                            spark_snippet=logic[:200],
                            migration_id=ctx.migration_id,
                            sequence=ctx.next_sequence(),
                            udf_name=tx_id,
                            external_function=match,
                            library=match,
                        ))
                        break  # One anomaly per UDF
    
    # Also check explicitly marked UDFs in the ASG
    udfs = _safe_get(ctx.asg, 'udfs', default=[]) or []
    for udf in udfs:
        udf_name = _safe_get(udf, 'name', default='unknown')
        is_external = _safe_get(udf, 'is_external', default=False)
        external_lib = _safe_get(udf, 'external_library', default='')
        
        if is_external or external_lib:
            source_file = _safe_get(udf, 'source_file', default='unknown')
            source_line = _safe_get(udf, 'source_line', default=0)
            
            anomalies.append(create_anomaly(
                code=AnomalyCode.LOG_001,
                target_node=f"udf_{udf_name}",
                target_type='udf',
                source_file=source_file,
                source_line=source_line,
                spark_snippet=_safe_get(udf, 'body', default='# external function'),
                migration_id=ctx.migration_id,
                sequence=ctx.next_sequence(),
                udf_name=udf_name,
                external_function=_safe_get(udf, 'calls', default=[udf_name])[0] if _safe_get(udf, 'calls') else udf_name,
                library=external_lib or 'unknown',
            ))
    
    return anomalies


def detect_unresolved_params(ctx: DetectionContext) -> list[Anomaly]:
    """
    REF_002: Detect param_* references without bindings.
    
    Scans transformation inputs for param_ prefixed IDs that
    are not in the execution_instances bindings.
    """
    anomalies = []
    seen_params = set()  # Avoid duplicate reports
    
    for tx_id, tx in ctx.transformations.items():
        inputs = _safe_get(tx, 'inputs', default=[]) or []
        
        for inp in inputs:
            inp_id = inp if isinstance(inp, str) else _safe_get(inp, 'ref')
            
            if inp_id and inp_id.startswith('param_') and inp_id not in ctx.param_bindings:
                if inp_id in seen_params:
                    continue
                # Skip WindowSpec variables (registered in window_specs)
                param_name = inp_id.replace('param_', '', 1)
                window_spec_names = {
                    ws.get('variable_name', '')
                    for ws in _safe_get(ctx.asg, 'window_specs', default=[]) or []
                }
                if param_name in window_spec_names:
                    continue
                # Skip imported module names used as receivers (module.function() calls)
                imported_modules = set()
                for src_file in _safe_get(ctx.asg, 'source_files', default=[]) or []:
                    imports_dict = _safe_get(src_file, 'imports', default={}) or {}
                    if isinstance(imports_dict, dict):
                        for mod_key, mod_info in imports_dict.items():
                            imported_modules.add(mod_key.split('.')[-1])
                            for name in (_safe_get(mod_info, 'imported_names', default=[]) or []):
                                imported_modules.add(name)
                if param_name in imported_modules:
                    continue
                seen_params.add(inp_id)
                
                source_file, source_line = ctx.node_sources.get(tx_id, ('unknown', 0))
                spark_snippet = _safe_get(tx, 'logic', default='') or \
                               _safe_get(tx, 'spark_code', default='') or 'unknown'
                
                # Extract param name
                param_name = inp_id.replace('param_', '')
                
                # Determine severity: if the function owning this param is never
                # called in the analyzed scope, it's informational (MEDIUM), not CRITICAL
                func_scope = _safe_get(tx, 'location', 'scope', default='unknown')
                called_funcs = {
                    ec.get('callee', {}).get('function', '')
                    for ec in _safe_get(ctx.asg, 'execution_calls', default=[]) or []
                }
                # DataFrame params (named df*, *Df, *dataframe*, etc.) are inherently
                # bound by the caller in .transform() or direct invocation; treat
                # as resolved (MEDIUM) rather than CRITICAL.
                # param_name may include the function prefix (e.g.
                # "applyBV_Prestation_00021_dfPrestation"); extract the
                # actual parameter by splitting on the function scope.
                actual_param = param_name
                if func_scope and func_scope != 'unknown' and param_name.startswith(func_scope):
                    actual_param = param_name[len(func_scope):].lstrip('_')
                pn_lower = actual_param.lower()
                is_df_param = (
                    pn_lower in ('df', 'dataframe')
                    or pn_lower.startswith('df')
                    or pn_lower.endswith('df')
                    or 'dataframe' in pn_lower
                )
                if is_df_param:
                    continue
                severity = Severity.MEDIUM if func_scope not in called_funcs and func_scope != '<global>' else Severity.CRITICAL
                
                anomalies.append(create_anomaly(
                    code=AnomalyCode.REF_002,
                    target_node=tx_id,
                    target_type='transformation',
                    source_file=source_file,
                    source_line=source_line,
                    spark_snippet=str(spark_snippet)[:200],
                    migration_id=ctx.migration_id,
                    sequence=ctx.next_sequence(),
                    param_name=param_name,
                    function_name=func_scope,
                    severity=severity,
                ))
    
    return anomalies


def detect_unclear_joins(ctx: DetectionContext) -> list[Anomaly]:
    """
    JOIN_001: Detect joins without explicit join conditions.
    
    Looks for join transformations missing join_key specifications.
    """
    anomalies = []
    
    for tx_id, tx in ctx.transformations.items():
        tx_type = _safe_get(tx, 'operation', default='') or _safe_get(tx, 'type', default='')
        
        if 'join' not in str(tx_type).lower():
            continue
        
        # Check for join key in various places
        params = _safe_get(tx, 'parameters', default={}) or {}
        join_key = _safe_get(tx, 'join_key') or \
                   _safe_get(tx, 'join_condition') or \
                   _safe_get(tx, 'on') or \
                   _safe_get(params, 'on') or \
                   _safe_get(params, 'join_key')
        
        logic = _safe_get(tx, 'logic', default='') or ''
        
        # Check if the logic text itself reveals a condition (=== in Scala)
        if not join_key and ('===' in logic or 'equalTo' in logic):
            join_key = 'inferred_from_logic'
        # Also check if join_condition is already in parameters
        if not join_key and params.get('join_condition'):
            join_key = params['join_condition']
        
        # If no explicit key but logic contains join condition, it's probably OK
        if not join_key and '.join(' in logic and ', ' not in logic.split('.join(')[-1]:
            source_file, source_line = ctx.node_sources.get(tx_id, ('unknown', 0))
            
            # Try to infer join key from inputs
            inferred_key = 'id'  # Default guess
            
            anomalies.append(create_anomaly(
                code=AnomalyCode.JOIN_001,
                target_node=tx_id,
                target_type='transformation',
                source_file=source_file,
                source_line=source_line,
                spark_snippet=logic[:200],
                migration_id=ctx.migration_id,
                sequence=ctx.next_sequence(),
                inferred_key=inferred_key,
            ))
    
    return anomalies


# =============================================================================
# Context Enrichment
# =============================================================================


def _enrich_anomaly_groups(groups: list[AnomalyGroup], ctx: DetectionContext) -> None:
    """
    Enrich anomaly groups with context for debugging without source code.
    
    For each group, adds:
    - Origin: where the column/subject first appeared
    - Lineage: transformation chain affecting the subject
    - Schema: inferred types at the anomaly location
    - Related columns and input DataFrames
    """
    import re
    
    for group in groups:
        if group.subject_type != 'column':
            continue
        
        column_name = group.subject
        
        # Find origin - where the column first appears
        origin = _find_column_origin(column_name, ctx)
        
        # Build transformation chain
        chain = _build_transformation_chain(column_name, group.occurrences, ctx)
        
        # Build lineage summary
        lineage_nodes = [origin.first_seen_node] if origin and origin.first_seen_node else []
        lineage_nodes.extend(step.node_id for step in chain)
        lineage_summary = " → ".join(lineage_nodes) if lineage_nodes else None
        
        # Get schema at first occurrence
        schema = {}
        if group.occurrences:
            first_node = group.occurrences[0].node_id
            tx = ctx.transformations.get(first_node)
            if tx:
                for col in _safe_get(tx, 'inferred_input', default=[]) or []:
                    col_name = _safe_get(col, 'name')
                    col_type = _safe_get(col, 'inferred_type', default='UNKNOWN')
                    if col_name:
                        schema[col_name] = str(col_type)
        
        # Find related columns from expressions
        related = set()
        for occ in group.occurrences:
            snippet = occ.spark_snippet
            col_refs = re.findall(r"col\(['\"]([^'\"]+)['\"]\)", snippet)
            related.update(col_refs)
        related.discard(column_name)
        
        # Find input DataFrames
        input_dfs = set()
        for src_id, source in ctx.sources.items():
            name = _safe_get(source, 'name', default='')
            if name:
                input_dfs.add(name)
        
        # Set context
        group.context = AnomalyContext(
            origin=origin,
            lineage_summary=lineage_summary,
            transformation_chain=chain,
            inferred_schema=schema,
            related_columns=list(related),
            input_dataframes=list(input_dfs),
        )


def _find_column_origin(column_name: str, ctx: DetectionContext) -> ColumnOrigin | None:
    """Find where a column first appears in the pipeline."""
    # Check data inputs first
    for src_id, source in ctx.sources.items():
        schema = _safe_get(source, 'schema', default=[]) or []
        for col in schema:
            if _safe_get(col, 'name') == column_name:
                return ColumnOrigin(
                    source_dataframe=_safe_get(source, 'name'),
                    first_seen_node=src_id,
                    created_by_operation='input',
                )
    
    # Check transformations for withColumn, alias, etc.
    for tx_id, tx in ctx.transformations.items():
        params = _safe_get(tx, 'parameters', default={}) or {}
        operation = _safe_get(tx, 'operation', default='')
        
        # withColumn creates new columns
        if operation == 'withColumn' and params.get('column_name') == column_name:
            return ColumnOrigin(
                source_dataframe=None,
                first_seen_node=tx_id,
                created_by_operation='withColumn',
            )
        
        # Check inferred_input for first appearance
        for col in _safe_get(tx, 'inferred_input', default=[]) or []:
            if _safe_get(col, 'name') == column_name:
                first_seen = _safe_get(col, 'first_seen_nodes', default=[])
                if first_seen:
                    return ColumnOrigin(
                        source_dataframe=None,
                        first_seen_node=first_seen[0],
                        created_by_operation=_safe_get(col, 'source', default='unknown'),
                    )
    
    return None


def _build_transformation_chain(
    column_name: str,
    occurrences: list,
    ctx: DetectionContext,
) -> list[TransformationStep]:
    """Build the chain of transformations affecting a column."""
    chain = []
    seen_nodes = set()
    
    for occ in occurrences:
        node_id = occ.node_id
        if node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        
        tx = ctx.transformations.get(node_id)
        if not tx:
            continue
        
        operation = _safe_get(tx, 'operation', default='unknown')
        logic = _safe_get(tx, 'logic', default='')
        
        # Check if transformation directly affects the column
        affects = column_name.lower() in logic.lower()
        
        chain.append(TransformationStep(
            node_id=node_id,
            operation=operation,
            logic=str(logic)[:200],
            affects_column=affects,
        ))
    
    return chain


# =============================================================================
# Main Detector Class
# =============================================================================


def detect_regex_fallbacks(ctx: DetectionContext) -> list[Anomaly]:
    """Detect cases where regex fallback was used instead of AST/sqlglot.

    Reads W_PAR_001 warnings embedded in the ASG by parsers and converts
    them into PAR_001 anomalies with structured diagnostic hints.
    """
    anomalies: list[Anomaly] = []
    warnings = _safe_get(ctx.asg, "warnings", default=[]) or []

    for i, warning in enumerate(warnings):
        code = _safe_get(warning, "code")
        if code != "W_PAR_001":
            continue

        evidence = _safe_get(warning, "regex_evidence", default={}) or {}
        match_type = evidence.get("match_type", "UNKNOWN")
        raw_snippet = evidence.get("raw_snippet", "")
        elements = evidence.get("identified_elements", {})
        failure_reason = evidence.get("failure_reason", "parser failed")
        primary_parser = evidence.get("primary_parser", "unknown")

        source_file = _safe_get(warning, "source_file") or ""
        source_line = _safe_get(warning, "source_line") or 0

        recovered_desc = ", ".join(
            f"{k}={v}" for k, v in elements.items() if v
        ) or "none"

        anomalies.append(create_anomaly(
            code=AnomalyCode.PAR_001,
            target_node=f"parse_{match_type.lower()}_{i:03d}",
            target_type="parse_event",
            source_file=source_file,
            source_line=source_line if isinstance(source_line, int) else 0,
            spark_snippet=raw_snippet[:200],
            migration_id=ctx.migration_id,
            sequence=len(anomalies) + 1,
            severity=Severity.MEDIUM,
            parse_context=match_type,
            failure_reason=failure_reason,
            recovered_elements=recovered_desc,
            primary_parser=primary_parser,
            regex_evidence=elements,
        ))

    return anomalies


class AnomalyDetector:
    """
    Main detector that orchestrates all anomaly detection rules.
    
    Usage:
        detector = AnomalyDetector()
        report = detector.analyze(asg, migration_id="example_01")
        report_json = report.model_dump_json(indent=2)
    """
    
    def __init__(
        self,
        enabled_rules: list[AnomalyCode] | None = None,
        skip_unknown_types: bool = False,
    ):
        """
        Initialize detector with optional rule filtering.
        
        Args:
            enabled_rules: List of AnomalyCode to enable. None = all enabled.
            skip_unknown_types: If True, don't report SCH_001 (reduces noise).
        """
        self.enabled_rules = set(enabled_rules) if enabled_rules else None
        self.skip_unknown_types = skip_unknown_types
        
        # Register all detectors
        self._detectors: list[tuple[set[AnomalyCode], Callable]] = [
            ({AnomalyCode.SCH_001}, detect_unknown_types),
            ({AnomalyCode.LIN_001}, detect_truncated_flows),
            ({AnomalyCode.LIN_002, AnomalyCode.LIN_005}, detect_orphan_resources),
            ({AnomalyCode.LOG_001}, detect_blackbox_udfs),
            ({AnomalyCode.REF_002}, detect_unresolved_params),
            ({AnomalyCode.JOIN_001}, detect_unclear_joins),
            ({AnomalyCode.PAR_001}, detect_regex_fallbacks),
        ]
    
    def analyze(
        self,
        asg: Any,
        migration_id: str,
        source_asg_path: str = "",
        source_asg_agnostic_path: str | None = None,
        include_timestamp: bool = True,
    ) -> AnomalyReport:
        """
        Analyze ASG and produce anomaly report.
        
        Args:
            asg: ASG or AgnosticASG object/dict
            migration_id: Unique identifier for this migration
            source_asg_path: Path to the ASG JSON file
            source_asg_agnostic_path: Path to the ASG-A JSON file
        
        Returns:
            AnomalyReport with all detected anomalies
        """
        # Build context with indexes
        ctx = build_context(asg, migration_id)
        
        # Create report
        report = create_report(
            migration_id=migration_id,
            source_asg=source_asg_path,
            source_asg_agnostic=source_asg_agnostic_path,
            include_timestamp=include_timestamp,
        )
        
        # Collect all individual anomalies (temporary)
        all_anomalies: list[Anomaly] = []
        
        # Run all enabled detectors
        for rule_codes, detector_fn in self._detectors:
            # Skip unknown types if configured
            if self.skip_unknown_types and AnomalyCode.SCH_001 in rule_codes:
                continue
            
            # Skip if rule is not enabled
            if self.enabled_rules and not rule_codes.intersection(self.enabled_rules):
                continue
            
            try:
                anomalies = detector_fn(ctx)
                all_anomalies.extend(anomalies)
            except Exception as e:
                # Log but don't fail on individual detector errors
                print(f"Warning: Detector {detector_fn.__name__} failed: {e}")
        
        # Group anomalies by code+subject for efficiency
        report.anomalies = group_anomalies(all_anomalies)
        
        # Enrich groups with context (lineage, schema, etc.)
        _enrich_anomaly_groups(report.anomalies, ctx)
        
        report._update_summary()
        
        return report
    
    def analyze_from_file(
        self,
        asg_path: str,
        migration_id: str | None = None,
        include_timestamp: bool = True,
    ) -> AnomalyReport:
        """
        Analyze ASG from JSON file.
        
        Args:
            asg_path: Path to ASG JSON file
            migration_id: Migration ID (defaults to filename stem)
            include_timestamp: If True, include generated_at timestamp (default: True)
        
        Returns:
            AnomalyReport with all detected anomalies
        """
        import json
        from pathlib import Path
        
        path = Path(asg_path)
        if migration_id is None:
            migration_id = path.stem.replace('_asg', '').replace('_agnostic', '')
        
        with open(path) as f:
            asg = json.load(f)
        
        return self.analyze(
            asg=asg,
            migration_id=migration_id,
            source_asg_path=str(path),
            include_timestamp=include_timestamp,
        )


# =============================================================================
# Convenience Functions
# =============================================================================


def analyze_asg(
    asg: Any,
    migration_id: str = "unknown",
    source_path: str = "",
    include_timestamp: bool = True,
) -> AnomalyReport:
    """Convenience function to analyze an ASG."""
    detector = AnomalyDetector()
    return detector.analyze(asg, migration_id, source_path, include_timestamp=include_timestamp)


def analyze_asg_file(asg_path: str, include_timestamp: bool = True) -> AnomalyReport:
    """Convenience function to analyze an ASG from file."""
    detector = AnomalyDetector()
    return detector.analyze_from_file(asg_path, include_timestamp=include_timestamp)
